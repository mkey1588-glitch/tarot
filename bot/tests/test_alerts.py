"""Tests for operator alerting on the manual-review queue.

The user has been told a person will look at their chart. Whether that
happens is the subject here.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot import alerts
from bot.config import Config
from bot.cost import BudgetGuard
from bot.llm import ModelGateway, StubModel
from bot.messages_ja import Msg
from bot.outbound import NullTransport, Outbound
from bot.reading import BirthData, ReadingService
from bot.storage import JST, Storage
from engine.solar import solar_term_instant


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "data")


def boundary_birth():
    instant = solar_term_instant(135.0, datetime(2020, 8, 7, tzinfo=timezone.utc))
    return BirthData(instant.astimezone(JST).date())


# --- Choosing an alerter ---------------------------------------------------

def test_without_an_operator_it_is_log_only_and_says_so():
    """A WARNING in a log file is fine while an engineer watches a terminal
    and useless the moment nobody does. It reports itself as unconfigured
    rather than letting a missing alert look like a working one."""
    chosen = alerts.alerts_for(Config.from_env({}), NullTransport())
    assert isinstance(chosen, alerts.LogOnlyAlerts)
    assert chosen.configured is False


def test_with_an_operator_it_pushes():
    config = Config.from_env({"OPERATOR_LINE_USER_ID": "Uoperator"})
    chosen = alerts.alerts_for(config, NullTransport())
    assert isinstance(chosen, alerts.LineOperatorAlerts)
    assert chosen.configured is True


def test_an_operator_without_a_transport_falls_back_to_logging():
    config = Config.from_env({"OPERATOR_LINE_USER_ID": "Uoperator"})
    assert alerts.alerts_for(config, None).configured is False


# --- What the alert contains ----------------------------------------------

def test_the_alert_carries_the_review_id():
    transport = NullTransport()
    alerts.LineOperatorAlerts(transport, "Uop").review_queued("rev123", "boundary")
    assert transport.sent[0]["via"] == "push"
    assert transport.sent[0]["to"] == "Uop"
    assert "rev123" in transport.texts[0]


def test_the_alert_carries_no_birth_data(store):
    """The queue entry holds it because the reviewer needs it. A phone
    notification travelling through LINE's servers is not where personal
    information belongs — the same rule the log line already follows."""
    transport = NullTransport()
    config = Config.from_env({"FREE_TIER_LIMIT": "3",
                              "OPERATOR_LINE_USER_ID": "Uop"})
    service = ReadingService(
        store, ModelGateway(StubModel(), BudgetGuard(store, 10.0)), config,
        alerts=alerts.LineOperatorAlerts(transport, "Uop"))

    service.generate("U1", "恋愛運を教えてください", boundary_birth())

    alert = transport.texts[0]
    assert "2020" not in alert
    assert "08-07" not in alert
    assert "U1" not in alert
    # …while the queue entry, which a person opens deliberately, does have it.
    assert store.open_reviews()[0]["birth_date"]


def test_the_alert_goes_through_the_outbound_funnel():
    """Making an exception for internal messages would put a second,
    unscreened path into the transport — what the funnel exists to prevent."""
    transport = NullTransport()
    alerts.LineOperatorAlerts(transport, "Uop").review_queued("r1", "boundary")
    assert isinstance(transport.sent[0]["message"], Outbound)


# --- Failure modes ---------------------------------------------------------

def test_a_failed_alert_never_breaks_the_users_reply(store):
    """The user has already been told a person will look. Losing that reply
    because a notification failed would be the worse outcome."""
    class BrokenTransport(NullTransport):
        def push(self, user_id, message):
            self._require_outbound(message)
            raise RuntimeError("LINE is down")

    config = Config.from_env({"FREE_TIER_LIMIT": "3"})
    service = ReadingService(
        store, ModelGateway(StubModel(), BudgetGuard(store, 10.0)), config,
        alerts=alerts.LineOperatorAlerts(BrokenTransport(), "Uop"))

    outcome = service.generate("U1", "恋愛運を教えてください", boundary_birth())
    assert outcome.outcome == "manual_review"
    assert outcome.review_id
    assert "人が確かめて" in outcome.message.text


def test_a_failed_alert_still_leaves_the_queue_entry(store):
    class BrokenTransport(NullTransport):
        def push(self, user_id, message):
            raise RuntimeError("LINE is down")

    config = Config.from_env({"FREE_TIER_LIMIT": "3"})
    ReadingService(
        store, ModelGateway(StubModel(), BudgetGuard(store, 10.0)), config,
        alerts=alerts.LineOperatorAlerts(BrokenTransport(), "Uop")
    ).generate("U1", "恋愛運を教えて", boundary_birth())
    assert len(store.open_reviews()) == 1


# --- Overdue reviews -------------------------------------------------------

def test_a_fresh_review_is_not_overdue(store):
    store.enqueue_manual_review("U1", "boundary", {})
    assert alerts.overdue_reviews(store) == []


def test_an_old_review_is_reported_overdue(store):
    """An alert that fires and is then ignored is the same as no alert."""
    import json
    store.enqueue_manual_review("U1", "boundary", {})
    record = json.loads(store.review_queue_file.read_text(encoding="utf-8").strip())
    record["ts"] = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    store.review_queue_file.write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert len(alerts.overdue_reviews(store)) == 1
    assert alerts.overdue_reviews(store, hours=72) == []


def test_a_malformed_timestamp_does_not_crash_the_check(store):
    import json
    store.enqueue_manual_review("U1", "boundary", {})
    record = json.loads(store.review_queue_file.read_text(encoding="utf-8").strip())
    record["ts"] = "not a timestamp"
    store.review_queue_file.write_text(json.dumps(record) + "\n", encoding="utf-8")
    assert alerts.overdue_reviews(store) == []
