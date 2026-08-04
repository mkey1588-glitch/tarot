"""Tests for the Phase 0 funnel and the payment gate.

Phase 0 exists to produce one ratio. These check that the ratio counts
people rather than events, that board traffic does not contaminate seed
traffic, and that we cannot report a conversion rate for a question nobody
has been asked.
"""

import pytest

from bot import funnel, payments
from bot.config import Config
from bot.funnel import Stage
from bot.storage import Storage


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "data")


def walk(store, user_id, upto: Stage, cohort="seed"):
    for stage in funnel.ORDER:
        funnel.record(store, stage, user_id, cohort)
        if stage is upto:
            return


# --- Counting people, not events -------------------------------------------

def test_the_funnel_counts_distinct_users(store):
    """The gap this module was written to close: the old event log could say
    fourteen readings were delivered and not whether that was fourteen
    people once or one person fourteen times."""
    for _ in range(14):
        funnel.record(store, Stage.FREE_READING, "U1", "seed")
    assert funnel.counts(store)["free_reading"] == 1


def test_reaching_a_stage_counts_you_at_every_earlier_one(store):
    """A dropped event would otherwise show as negative conversion."""
    funnel.record(store, Stage.PAID, "U1", "seed")   # no earlier events
    counted = funnel.counts(store)
    assert counted["followed"] == 1
    assert counted["registered"] == 1
    assert counted["paid"] == 1


def test_the_rates_are_computed_from_the_counts(store):
    for user in ("a", "b", "c", "d"):
        walk(store, user, Stage.FREE_READING)
    walk(store, "a", Stage.PAYWALL_SHOWN)
    walk(store, "b", Stage.PAYWALL_SHOWN)
    walk(store, "a", Stage.PAID)

    rates = funnel.rates(funnel.counts(store))
    assert rates["offered_of_read"] == 0.5      # 2 of 4
    assert rates["paid_of_offered"] == 0.5      # 1 of 2
    assert rates["paid_of_followed"] == 0.25    # 1 of 4


def test_a_stage_must_be_a_stage(store):
    with pytest.raises(TypeError):
        funnel.record(store, "paid", "U1", "seed")


# --- Not inventing an answer -----------------------------------------------

def test_an_empty_denominator_reports_none_not_zero(store):
    """"0% converted" and "nobody has been asked" are different claims, and
    the second is where Phase 0 actually is. Reporting the first would be a
    fabricated answer to the question the phase exists for."""
    for user in ("a", "b"):
        walk(store, user, Stage.FREE_READING)
    rates = funnel.rates(funnel.counts(store))
    assert rates["paid_of_offered"] is None
    assert rates["offered_of_read"] == 0.0      # asked and nobody advanced


def test_the_headline_is_the_phase_0_question(store):
    report = funnel.report(store)
    assert report["headline"] == "paid_of_offered"
    assert "¥200-500" in report["question"]


# --- Cohorts ---------------------------------------------------------------

def test_cohorts_are_reported_apart(store):
    """Board traffic and seed traffic are different populations. Their
    average describes nobody."""
    for user in ("s1", "s2"):
        walk(store, user, Stage.PAYWALL_SHOWN, cohort="seed")
    walk(store, "s1", Stage.PAID, cohort="seed")
    for user in ("b1", "b2", "b3"):
        walk(store, user, Stage.PAYWALL_SHOWN, cohort="board")

    report = funnel.report(store)
    assert report["by_cohort"]["seed"]["rates"]["paid_of_offered"] == 0.5
    assert report["by_cohort"]["board"]["rates"]["paid_of_offered"] == 0.0
    # And the blended figure, which is the one not to quote.
    assert report["overall"]["rates"]["paid_of_offered"] == 0.2


def test_a_cohort_with_no_traffic_does_not_appear(store):
    walk(store, "s1", Stage.FOLLOWED, cohort="seed")
    assert list(funnel.report(store)["by_cohort"]) == ["seed"]


# --- What the funnel does not record ---------------------------------------

def test_funnel_events_carry_a_user_id_and_crisis_events_do_not(store):
    """The distinction is the point. Knowing that someone reached the
    paywall is an operational metric; knowing who typed 死にたい is not."""
    funnel.record(store, Stage.PAYWALL_SHOWN, "U1", "seed")
    store.log_crisis_event("死にたい")

    events = {e["type"]: e for e in store.iter_events()}
    assert events["funnel"]["user_id"] == "U1"
    assert "user_id" not in events["crisis_redirect"]
    assert set(events["crisis_redirect"]) == {"ts", "type", "pattern"}


def test_log_funnel_event_cannot_be_handed_message_text(store):
    """It takes named fields. There is no free-text parameter to misuse."""
    import inspect
    params = list(inspect.signature(store.log_funnel_event).parameters)
    assert params[:3] == ["stage", "user_id", "cohort"]


# --- The payment gate ------------------------------------------------------

def test_payment_is_not_permitted_today():
    """Four of six launch gates are open. If this starts passing, either the
    practitioner and counsel have delivered, or a check has been weakened."""
    assert payments.enabled_for(Config.from_env({})) is False
    assert "prompts_written" in payments.blocking_gates(Config.from_env({}))


def test_the_real_provider_refuses_before_the_gates_are_met():
    """Taking ¥300 for a reading written by an engineer, with no 特商法
    notice and no legal review, is not a pricing experiment."""
    provider = payments.StripeProvider(Config.from_env({}))
    with pytest.raises(payments.PaymentsNotPermitted, match="launch gates"):
        provider.create_checkout("U1", 300, "深層鑑定")


def test_asking_for_the_real_provider_returns_the_stub_while_gated():
    """The caller cannot get a charging provider by wanting one."""
    provider = payments.provider_for(Config.from_env({}), force_stub=False)
    assert isinstance(provider, payments.StubProvider)


def test_the_stub_moves_no_money_but_completes_the_flow(store):
    """Enough to exercise the funnel and rehearse with friends and family."""
    provider = payments.StubProvider(Config.from_env({}))
    checkout = provider.create_checkout("U1", 300, "深層鑑定")
    assert checkout.amount_jpy == 300
    assert checkout.provider == "stub"
    assert "example.invalid" in checkout.url


def test_the_stripe_provider_is_deliberately_unwritten():
    """It is last because it is the only part that cannot be tested without
    a real customer and real money."""
    import inspect
    source = inspect.getsource(payments.StripeProvider.create_checkout)
    assert "_require_permission" in source
    assert "NotImplementedError" in source
