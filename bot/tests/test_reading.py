"""Tests for the reading pipeline.

The order of the stages is the product here, not an implementation detail,
so most of these assert on ordering and on what happens when a stage says no.
"""

from datetime import date, datetime, time, timezone

import pytest

from bot.config import Config
from bot.cost import BudgetGuard
from bot.llm import ModelGateway, ModelUnavailable, StubModel
from bot.messages_ja import TEMPLATES, Msg
from bot.outbound import Outbound
from bot.reading import BirthData, ReadingService, parse_birth_data
from bot.safety import AI_DISCLOSURE_SHORT
from bot.storage import JST, Storage
from engine.solar import solar_term_instant

BIRTH = BirthData(date(1990, 5, 15), time(7, 30))
BIRTH_NO_TIME = BirthData(date(1990, 5, 15))


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "data")


@pytest.fixture
def config():
    return Config.from_env({"FREE_TIER_LIMIT": "3"})


def make_service(store, config, model=None, budget=10.0):
    gateway = ModelGateway(model or StubModel(), BudgetGuard(store, budget))
    return ReadingService(store, gateway, config)


@pytest.fixture
def service(store, config):
    return make_service(store, config)


# --- Parsing birth data ----------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1990-05-15", BirthData(date(1990, 5, 15))),
    ("1990/05/15", BirthData(date(1990, 5, 15))),
    ("1990年5月15日", BirthData(date(1990, 5, 15))),
    ("1990-05-15 07:30", BirthData(date(1990, 5, 15), time(7, 30))),
    ("1990年5月15日 7時30分", BirthData(date(1990, 5, 15), time(7, 30))),
    ("誕生日は1990-05-15です", BirthData(date(1990, 5, 15))),
])
def test_birth_data_is_parsed_from_the_shapes_people_write(text, expected):
    assert parse_birth_data(text, today=date(2026, 7, 30)) == expected


def test_a_date_without_a_time_parses_successfully():
    """P6. An absent time is a successful parse, not a rejection — turning
    users away at the first question is what we are avoiding."""
    parsed = parse_birth_data("1990-05-15", today=date(2026, 7, 30))
    assert parsed is not None
    assert not parsed.hour_known


@pytest.mark.parametrize("text", [
    "", "こんにちは", "1990-13-45", "3000-01-01", "1850-01-01", "05-15",
])
def test_unusable_input_is_rejected(text):
    assert parse_birth_data(text, today=date(2026, 7, 30)) is None


def test_a_nonsense_time_falls_back_to_no_time_rather_than_failing():
    """Losing the hour is recoverable. Losing the whole registration is not."""
    parsed = parse_birth_data("1990-05-15 99:99", today=date(2026, 7, 30))
    assert parsed == BirthData(date(1990, 5, 15))


def test_birth_data_round_trips_through_storage():
    assert BirthData.from_record(BIRTH.to_record()) == BIRTH
    assert BirthData.from_record(BIRTH_NO_TIME.to_record()) == BIRTH_NO_TIME
    assert BirthData.from_record({}) is None


# --- Crisis: before everything else ----------------------------------------

def test_crisis_language_never_reaches_the_model(store, config):
    class ExplodingModel:
        def generate(self, *args, **kwargs):
            raise AssertionError("a crisis message reached the model")

    service = make_service(store, config, ExplodingModel())
    outcome = service.generate("U1", "もう死にたい", BIRTH)
    assert outcome.outcome == "crisis"
    assert outcome.message.text == TEMPLATES[Msg.CRISIS]


def test_crisis_is_checked_before_the_quota(store, config):
    """A person in distress must never meet a paywall. Quota is a billing
    concern; this is not."""
    service = make_service(store, config)
    for _ in range(3):
        service.generate("U1", "恋愛運を教えてください", BIRTH)
    assert store.free_quota_remaining("U1", 3) == 0

    outcome = service.generate("U1", "もう死にたい", BIRTH)
    assert outcome.outcome == "crisis"
    assert outcome.message.text == TEMPLATES[Msg.CRISIS]


def test_crisis_is_checked_before_birth_data_is_required(store, config):
    """Registration is not a precondition for being redirected to help."""
    outcome = make_service(store, config).generate("U1", "死にたい", birth=None)
    assert outcome.outcome == "crisis"


def test_crisis_logs_the_pattern_and_not_the_message(store, service):
    service.generate("U1", "もう死にたいです、助けてください", BIRTH)
    events = [e for e in store.iter_events() if e["type"] == "crisis_redirect"]
    assert len(events) == 1
    assert events[0]["pattern"] == "死にたい"
    assert set(events[0]) == {"ts", "type", "pattern"}

    everything = store.events_file.read_text(encoding="utf-8")
    assert "助けてください" not in everything
    assert "U1" not in everything


def test_the_crisis_reply_has_no_disclosure_appended(service):
    text = service.generate("U1", "死にたい", BIRTH).message.text
    assert AI_DISCLOSURE_SHORT not in text
    assert "0570-064-556" in text


# --- Professional domains --------------------------------------------------

@pytest.mark.parametrize("text,message", [
    ("癌が治るか占ってください", Msg.PROFESSIONAL_MEDICAL),
    ("裁判に勝てますか", Msg.PROFESSIONAL_LEGAL),
    ("この株を買うべきでしょうか", Msg.PROFESSIONAL_FINANCIAL),
])
def test_professional_questions_are_referred_not_read(service, text, message):
    outcome = service.generate("U1", text, BIRTH)
    assert outcome.outcome.startswith("professional:")
    assert outcome.message.text == TEMPLATES[message]


def test_a_professional_referral_does_not_spend_quota(store, service):
    service.generate("U1", "癌が治るか占ってください", BIRTH)
    assert store.free_quota_remaining("U1", 3) == 3


# --- The happy path --------------------------------------------------------

def test_a_reading_is_produced_screened_and_disclosed(service):
    outcome = service.generate("U1", "恋愛運を教えてください", BIRTH)
    assert outcome.outcome == "delivered"
    assert outcome.message.kind == "reading"
    assert outcome.message.text.endswith(AI_DISCLOSURE_SHORT)
    assert outcome.cost_usd > 0


def test_the_model_receives_the_chart_and_never_a_birth_date(store, config):
    """E1, end to end. The prompt must contain computed pillars and no raw
    birth data for the model to work from."""
    class CapturingModel(StubModel):
        seen = None

        def generate(self, prompt, **kwargs):
            CapturingModel.seen = prompt
            return super().generate(prompt, **kwargs)

    make_service(store, config, CapturingModel()).generate(
        "U1", "恋愛運を教えてください", BIRTH)

    prompt = CapturingModel.seen
    assert "【命式】" in prompt.user
    assert "庚午" in prompt.user          # the computed year pillar
    assert "1990-05-15" not in prompt.user
    assert "1990年5月15日" not in prompt.user


def test_a_three_pillar_chart_tells_the_model_to_stay_quiet(store, config):
    class CapturingModel(StubModel):
        seen = None

        def generate(self, prompt, **kwargs):
            CapturingModel.seen = prompt
            return super().generate(prompt, **kwargs)

    make_service(store, config, CapturingModel()).generate(
        "U1", "恋愛運を教えてください", BIRTH_NO_TIME)

    prompt = CapturingModel.seen
    assert "時柱 不明" in prompt.user
    assert "時柱はありません" in prompt.user


def test_the_missing_hour_rate_is_recorded(store, service):
    service.generate("U1", "恋愛運を教えて", BIRTH)
    service.generate("U2", "恋愛運を教えて", BIRTH_NO_TIME)
    stats = store.get_stats()
    assert stats["charts_computed"] == 2
    assert stats["charts_without_birth_time"] == 1
    assert stats["missing_birth_time_rate"] == 0.5


def test_the_paid_tier_uses_the_stronger_model(store, config):
    service = make_service(store, config)
    service.generate("U1", "恋愛運を教えて", BIRTH, tier="paid")
    record = list(store.iter_llm_usage())[0]
    assert record["model"] == config.model_paid
    assert record["tier"] == "paid"


def test_the_paid_tier_does_not_spend_free_quota(store, config):
    service = make_service(store, config)
    service.generate("U1", "恋愛運を教えて", BIRTH, tier="paid")
    assert store.free_quota_remaining("U1", 3) == 3


# --- No birth data ---------------------------------------------------------

def test_without_birth_data_the_user_is_asked_for_it(service):
    outcome = service.generate("U1", "恋愛運を教えてください", birth=None)
    assert outcome.outcome == "no_birth_data"
    assert outcome.message.text == TEMPLATES[Msg.NEED_BIRTH_DATA_FIRST]


def test_asking_for_birth_data_costs_nothing(store, service):
    service.generate("U1", "恋愛運を教えてください", birth=None)
    assert list(store.iter_llm_usage()) == []
    assert store.free_quota_remaining("U1", 3) == 3


# --- Quota -----------------------------------------------------------------

def test_the_free_tier_runs_out(service):
    for _ in range(3):
        assert service.generate("U1", "恋愛運を教えて", BIRTH).outcome == "delivered"
    outcome = service.generate("U1", "恋愛運を教えて", BIRTH)
    assert outcome.outcome == "quota_exhausted"
    assert outcome.message.text == TEMPLATES[Msg.QUOTA_EXHAUSTED]


def test_an_exhausted_quota_makes_no_model_call(store, config):
    service = make_service(store, config)
    for _ in range(3):
        service.generate("U1", "恋愛運を教えて", BIRTH)
    before = len(list(store.iter_llm_usage()))
    service.generate("U1", "恋愛運を教えて", BIRTH)
    assert len(list(store.iter_llm_usage())) == before


# --- Boundary charts reach a human -----------------------------------------

def _boundary_birth():
    """A birth with no reported time, on a day a solar term falls: the month
    pillar is genuinely ambiguous across that 24 hours."""
    instant = solar_term_instant(135.0, datetime(2020, 8, 7, tzinfo=timezone.utc))
    return BirthData(instant.astimezone(JST).date())


def test_a_boundary_chart_is_queued_for_a_human(store, service):
    outcome = service.generate("U1", "恋愛運を教えてください", _boundary_birth())
    assert outcome.outcome == "manual_review"
    assert outcome.review_id
    reviews = store.open_reviews()
    assert len(reviews) == 1
    assert reviews[0]["review_id"] == outcome.review_id
    assert reviews[0]["warnings"]


def test_the_user_is_told_a_person_will_look_not_given_an_apology(store, service):
    outcome = service.generate("U1", "恋愛運を教えてください", _boundary_birth())
    assert outcome.review_id in outcome.message.text
    assert "担当者が確認" in outcome.message.text


def test_a_boundary_chart_never_reaches_the_model(store, config):
    class ExplodingModel:
        def generate(self, *args, **kwargs):
            raise AssertionError("an unresolved chart reached the model")

    service = make_service(store, config, ExplodingModel())
    assert service.generate("U1", "恋愛運を教えて",
                            _boundary_birth()).outcome == "manual_review"


def test_the_review_queue_holds_what_the_reviewer_needs(store, service):
    service.generate("U1", "恋愛運を教えてください", _boundary_birth())
    review = store.open_reviews()[0]
    assert review["birth_date"]
    assert review["user_id"] == "U1"
    assert review["reason"] == "solar_term_boundary"


def test_the_event_log_carries_the_review_id_and_not_the_birth_date(store,
                                                                   service):
    """Personal information belongs in the queue file the reviewer opens,
    not in the general event log."""
    outcome = service.generate("U1", "恋愛運を教えてください", _boundary_birth())
    events = [e for e in store.iter_events()
              if e["type"] == "manual_review_queued"]
    assert events[0]["review_id"] == outcome.review_id
    assert "birth_date" not in events[0]


# --- Failure modes ---------------------------------------------------------

def test_an_exhausted_budget_pauses_rather_than_erroring(store, config):
    service = make_service(store, config, budget=0.0)
    outcome = service.generate("U1", "恋愛運を教えてください", BIRTH)
    assert outcome.outcome == "budget_exceeded"
    assert outcome.message.text == TEMPLATES[Msg.SERVICE_PAUSED]


def test_a_transport_failure_does_not_leak_details_to_the_user(store, config):
    class BrokenModel:
        def generate(self, *args, **kwargs):
            raise ModelUnavailable("RateLimitError")

    outcome = make_service(store, config, BrokenModel()).generate(
        "U1", "恋愛運を教えてください", BIRTH)
    assert outcome.outcome == "model_unavailable"
    assert "RateLimitError" not in outcome.message.text


def test_a_blocked_reading_is_withheld_and_logged(store, config):
    """E5: a block is a prompt defect. It goes to the practitioner review,
    and the user gets reviewed copy instead."""
    service = make_service(store, config,
                           StubModel("必ず良い方向に向かいます"))
    outcome = service.generate("U1", "恋愛運を教えてください", BIRTH)
    assert outcome.outcome == "output_blocked"
    assert "必ず良い方向に向かいます" not in outcome.message.text
    blocked = [e for e in store.iter_events() if e["type"] == "reading_blocked"]
    assert len(blocked) == 1
    assert "景品表示法" in blocked[0]["reason"]


# --- Every path returns something sendable ---------------------------------

@pytest.mark.parametrize("text,birth", [
    ("恋愛運を教えてください", BIRTH),
    ("恋愛運を教えてください", BIRTH_NO_TIME),
    ("恋愛運を教えてください", None),
    ("もう死にたい", BIRTH),
    ("癌は治りますか", BIRTH),
])
def test_every_branch_returns_an_outbound(service, text, birth):
    """There is no path out of generate() that produces raw text."""
    outcome = service.generate("U1", text, birth)
    assert isinstance(outcome.message, Outbound)
    assert outcome.message.text
