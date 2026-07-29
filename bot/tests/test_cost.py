"""Tests for the monthly budget guard.

Phase 0's whole budget is US$1-3K. The scenario these exist for is a retry
loop at 3am, so the cases that matter are the ones where the guard has to
say no.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.cost import (
    BudgetExceeded, BudgetGuard, cost_usd, estimate_prompt_tokens,
    worst_case_cost_usd,
)
from bot.storage import JST, Storage


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "data")


# --- Pricing arithmetic ----------------------------------------------------

def test_cost_is_computed_per_million_tokens():
    # gpt-4o-mini: $0.15 in, $0.60 out per 1M.
    assert cost_usd("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)
    assert cost_usd("gpt-4o-mini", 0, 1_000_000) == pytest.approx(0.60)
    assert cost_usd("gpt-4o-mini", 1000, 500) == pytest.approx(0.00045)


def test_worst_case_assumes_the_full_output_allowance():
    assert worst_case_cost_usd("gpt-4o-mini", 1000, 1000) == \
           cost_usd("gpt-4o-mini", 1000, 1000)


def test_token_estimate_errs_high_for_japanese():
    """An overestimate refuses slightly early; an underestimate lets through
    a call that should have been refused."""
    assert estimate_prompt_tokens("今日の運勢を教えてください") >= 13


# --- The cap ---------------------------------------------------------------

def test_a_call_within_budget_is_allowed(store):
    BudgetGuard(store, budget_usd=1.0).check("gpt-4o-mini", 1000, 1000)


def test_a_call_is_refused_once_the_cap_is_reached(store):
    """Simulated exhaustion: spend the budget, then ask for one more."""
    guard = BudgetGuard(store, budget_usd=0.10)
    for _ in range(200):
        guard.record("U1", "gpt-4o-mini", 1000, 1000)

    assert guard.month_to_date_usd() > 0.10
    with pytest.raises(BudgetExceeded):
        guard.check("gpt-4o-mini", 1000, 1000)


def test_the_refusal_is_before_the_call_not_after(store):
    """The last call that would breach never happens. A cap that notices
    afterwards is a report, not a cap."""
    guard = BudgetGuard(store, budget_usd=0.001)
    # Nothing spent at all, but this one call could cost more than the cap.
    assert guard.month_to_date_usd() == 0.0
    with pytest.raises(BudgetExceeded):
        guard.check("gpt-4o", 10_000, 10_000)


def test_a_refusal_is_logged_as_an_event(store):
    guard = BudgetGuard(store, budget_usd=0.0)
    with pytest.raises(BudgetExceeded):
        guard.check("gpt-4o-mini", 100, 100)
    events = [e for e in store.iter_events() if e["type"] == "budget_refused"]
    assert len(events) == 1
    assert events[0]["model"] == "gpt-4o-mini"
    assert events[0]["budget_usd"] == 0.0


def test_a_runaway_loop_stops_within_one_call_of_the_cap(store):
    """The scenario the cap exists for."""
    guard = BudgetGuard(store, budget_usd=0.05)
    calls = refusals = 0
    for _ in range(10_000):
        try:
            guard.check("gpt-4o-mini", 2000, 1000)
        except BudgetExceeded:
            refusals += 1
            break
        guard.record("U1", "gpt-4o-mini", 2000, 1000)
        calls += 1

    assert refusals == 1, "the loop was never stopped"
    assert guard.month_to_date_usd() <= 0.05
    assert calls < 10_000


def test_remaining_never_goes_negative(store):
    guard = BudgetGuard(store, budget_usd=0.01)
    for _ in range(100):
        guard.record("U1", "gpt-4o-mini", 5000, 5000)
    assert guard.remaining_usd() == 0.0


# --- Month boundaries ------------------------------------------------------

def _rewrite_single_usage_timestamp(store, when: datetime) -> None:
    import json
    record = json.loads(store.llm_log_file.read_text(encoding="utf-8").strip())
    record["ts"] = when.isoformat()
    store.llm_log_file.write_text(json.dumps(record) + "\n", encoding="utf-8")


def test_only_this_months_spend_counts(store):
    now = datetime.now(timezone.utc)
    store.log_llm_usage("U1", {"model": "gpt-4o-mini", "prompt_tokens": 0,
                               "completion_tokens": 0, "cost_usd": 999.0})
    _rewrite_single_usage_timestamp(store, now.replace(day=1) - timedelta(days=2))

    guard = BudgetGuard(store, budget_usd=1.0)
    assert guard.month_to_date_usd(now) == 0.0
    guard.check("gpt-4o-mini", 100, 100)


def test_the_billing_month_is_japanese_not_utc(store):
    """These two instants are an hour apart and in the same UTC month, but
    in different Japanese ones. The convention has to match the free-tier
    reset or the two disagree about what month it is."""
    guard = BudgetGuard(store, budget_usd=1.0)
    spent_at = datetime(2026, 7, 31, 23, 30, tzinfo=JST)   # 31 Jul 14:30 UTC
    asked_at = datetime(2026, 8, 1, 0, 30, tzinfo=JST)     # 31 Jul 15:30 UTC

    store.log_llm_usage("U1", {"model": "gpt-4o-mini", "prompt_tokens": 0,
                               "completion_tokens": 0, "cost_usd": 0.5})
    _rewrite_single_usage_timestamp(store, spent_at)

    assert guard.month_to_date_usd(spent_at) == pytest.approx(0.5)
    assert guard.month_to_date_usd(asked_at) == 0.0


# --- Recording -------------------------------------------------------------

def test_recording_stores_the_cost_at_the_time(store):
    """Prices change. What a historical call cost is what it cost."""
    guard = BudgetGuard(store, budget_usd=1.0)
    spent = guard.record("U1", "gpt-4o-mini", 1000, 500, tier="free")
    record = list(store.iter_llm_usage())[0]
    assert record["cost_usd"] == pytest.approx(spent)
    assert record["tier"] == "free"
    assert record["user_id"] == "U1"


def test_a_stored_cost_is_trusted_over_recomputing_it(store):
    guard = BudgetGuard(store, budget_usd=10.0)
    store.log_llm_usage("U1", {"model": "gpt-4o-mini", "prompt_tokens": 1,
                               "completion_tokens": 1, "cost_usd": 2.0})
    assert guard.month_to_date_usd() == pytest.approx(2.0)


def test_a_usage_record_for_an_unpriced_model_does_not_crash_the_guard(store):
    """An old record from a model since removed from the price table must
    not take the guard down — that would fail open."""
    guard = BudgetGuard(store, budget_usd=1.0)
    store.log_llm_usage("U1", {"model": "gpt-9-imaginary",
                               "prompt_tokens": 100, "completion_tokens": 100})
    assert guard.month_to_date_usd() == 0.0
    guard.check("gpt-4o-mini", 100, 100)
