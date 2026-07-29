"""Tests for the JSON storage layer.

Most of this is a straight port and needs little defending. The tests that
matter are the three defects fixed on the way in, and the retention rule on
crisis events.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from bot.storage import JST, Storage, jst_today


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "data")


# --- Users -----------------------------------------------------------------

def test_a_new_user_is_created_with_defaults(store):
    store.upsert_user("U1", {"birth_date": "1990-05-15"})
    user = store.get_user("U1")
    assert user["user_id"] == "U1"
    assert user["free_quota_used"] == 0
    assert user["birth_date"] == "1990-05-15"


def test_upsert_merges_rather_than_replaces(store):
    store.upsert_user("U1", {"birth_date": "1990-05-15"})
    store.upsert_user("U1", {"birth_time": "07:30"})
    user = store.get_user("U1")
    assert user["birth_date"] == "1990-05-15"
    assert user["birth_time"] == "07:30"


def test_unknown_user_reads_as_empty(store):
    assert store.get_user("nobody") == {}


def test_writes_survive_a_restart(store, tmp_path):
    store.upsert_user("U1", {"birth_date": "1990-05-15"})
    assert Storage(tmp_path / "data").get_user("U1")["birth_date"] == "1990-05-15"


def test_a_corrupt_users_file_does_not_take_the_bot_down(store):
    store.users_file.write_text("{ not json", encoding="utf-8")
    assert store.get_user("U1") == {}


# --- Quota: the concurrency defect ----------------------------------------

def test_quota_is_spent_up_to_the_limit_and_no_further(store):
    assert [store.consume_free_quota("U1", 3) for _ in range(5)] == \
           [True, True, True, False, False]


def test_concurrent_consumers_cannot_exceed_the_limit(store):
    """The prototype released the lock between read, check and write, so two
    messages arriving together both saw the same remaining count."""
    import threading

    granted = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        granted.append(store.consume_free_quota("U1", 3))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(granted) == 3, f"granted {sum(granted)} of a 3-reading limit"


def test_quota_resets_on_the_japanese_day_not_the_servers(store):
    """A user in Japan gets a new allowance at JST midnight wherever the
    host happens to be."""
    store.consume_free_quota("U1", 3)
    store.upsert_user("U1", {"quota_reset_date": "2020-01-01"})
    assert store.consume_free_quota("U1", 3)
    assert store.get_user("U1")["quota_reset_date"] == jst_today()
    assert store.get_user("U1")["free_quota_used"] == 1


def test_jst_today_matches_japan_not_utc():
    assert jst_today() == datetime.now(JST).strftime("%Y-%m-%d")


def test_remaining_reports_a_full_allowance_on_a_new_day(store):
    store.consume_free_quota("U1", 3)
    assert store.free_quota_remaining("U1", 3) == 2
    store.upsert_user("U1", {"quota_reset_date": "2020-01-01"})
    assert store.free_quota_remaining("U1", 3) == 3


def test_remaining_is_a_full_allowance_for_an_unknown_user(store):
    assert store.free_quota_remaining("nobody", 3) == 3


# --- Timestamps ------------------------------------------------------------

def test_timestamps_are_timezone_aware_utc(store):
    """utcnow() returned a naive datetime that silently compared wrong
    against aware ones, and is deprecated besides."""
    store.upsert_user("U1", {})
    parsed = datetime.fromisoformat(store.get_user("U1")["created_at"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


# --- Crisis events: what must not be stored -------------------------------

def test_crisis_event_records_the_pattern_and_the_time(store):
    store.log_crisis_event("死にたい")
    events = list(store.iter_events())
    assert len(events) == 1
    assert events[0]["type"] == "crisis_redirect"
    assert events[0]["pattern"] == "死にたい"
    assert events[0]["ts"]


def test_crisis_event_stores_no_user_id_and_no_message_text(store):
    """Mental-health information is likely 要配慮個人情報. We need the rate,
    not the words, and not who said them."""
    store.log_crisis_event("死にたい")
    record = list(store.iter_events())[0]
    assert set(record) == {"ts", "type", "pattern"}


def test_log_crisis_event_has_no_parameter_for_text_or_identity(store):
    """Structural, not conventional: there is no argument through which a
    caller could pass the message even by mistake."""
    import inspect
    params = list(inspect.signature(store.log_crisis_event).parameters)
    assert params == ["pattern"]


# --- Append-only logs ------------------------------------------------------

def test_llm_usage_is_appended_and_read_back(store):
    store.log_llm_usage("U1", {"model": "gpt-4o-mini", "prompt_tokens": 100,
                               "completion_tokens": 50})
    records = list(store.iter_llm_usage())
    assert len(records) == 1
    assert records[0]["prompt_tokens"] == 100
    assert records[0]["user_id"] == "U1"


def test_a_truncated_log_line_does_not_stop_the_rest_being_read(store):
    store.log_llm_usage("U1", {"prompt_tokens": 1})
    with open(store.llm_log_file, "a", encoding="utf-8") as handle:
        handle.write('{"partial": \n')
    store.log_llm_usage("U2", {"prompt_tokens": 2})
    assert [r["prompt_tokens"] for r in store.iter_llm_usage()] == [1, 2]


def test_logs_are_valid_jsonl(store):
    store.log_event("chart_computed", {"hour_known": True})
    store.log_event("reading_delivered", {"tier": "free"})
    lines = store.events_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        json.loads(line)


# --- Manual review queue ---------------------------------------------------

def test_a_queued_review_is_readable_and_has_an_id(store):
    review_id = store.enqueue_manual_review(
        "U1", "boundary", {"warnings": ["立春 within 5 min"]})
    assert review_id
    open_reviews = store.open_reviews()
    assert len(open_reviews) == 1
    assert open_reviews[0]["review_id"] == review_id
    assert open_reviews[0]["status"] == "open"


def test_review_ids_are_distinct(store):
    ids = {store.enqueue_manual_review("U1", "boundary", {}) for _ in range(20)}
    assert len(ids) == 20


# --- Stats -----------------------------------------------------------------

def test_stats_report_the_missing_birth_time_rate(store):
    """P6's instrumentation, and the evidence P2 should be ruled on."""
    for hour_known in (True, False, False, False):
        store.log_event("chart_computed", {"hour_known": hour_known})
    stats = store.get_stats()
    assert stats["charts_computed"] == 4
    assert stats["charts_without_birth_time"] == 3
    assert stats["missing_birth_time_rate"] == 0.75


def test_stats_do_not_divide_by_zero_before_any_chart(store):
    assert store.get_stats()["missing_birth_time_rate"] is None


def test_stats_count_users_and_open_reviews(store):
    store.upsert_user("U1", {"birth_date": "1990-05-15"})
    store.upsert_user("U2", {})
    store.enqueue_manual_review("U1", "boundary", {})
    stats = store.get_stats()
    assert stats["total_users"] == 2
    assert stats["users_with_birth_data"] == 1
    assert stats["open_manual_reviews"] == 1


def test_stats_carry_no_spend_figure(store):
    """Pricing belongs to the cost guard. Storage does not know what a model
    costs and must not grow an opinion about it."""
    assert not any("cost" in key or "usd" in key
                   for key in store.get_stats())
