"""Tests for the webhook.

Routing and transport only. The pipeline underneath has its own tests; what
matters here is that an unsigned request cannot spend money, that every
reply goes out as a screened Outbound, and that the endpoint holding birth
data is not public.
"""

import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from bot.app import create_app
from bot.config import Config
from bot.cost import BudgetGuard
from bot.llm import ModelGateway, StubModel
from bot.messages_ja import TEMPLATES, Msg
from bot.outbound import NullTransport, Outbound
from bot.reading import ReadingService
from bot.safety import AI_DISCLOSURE_SHORT
from bot.storage import Storage

SECRET = "test-channel-secret"


@pytest.fixture
def store(tmp_path):
    return Storage(tmp_path / "data")


@pytest.fixture
def transport():
    return NullTransport()


@pytest.fixture
def config():
    return Config.from_env({
        "LINE_CHANNEL_SECRET": SECRET,
        "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
        "FREE_TIER_LIMIT": "3",
        "ADMIN_TOKEN": "test-admin-token",
    })


@pytest.fixture
def client(store, transport, config):
    gateway = ModelGateway(StubModel(), BudgetGuard(store, 10.0))
    app = create_app(config=config, storage=store, transport=transport,
                     service=ReadingService(store, gateway, config))
    return TestClient(app)


def signed(client, body: dict, secret: str = SECRET):
    raw = json.dumps(body).encode("utf-8")
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).digest()
    ).decode("utf-8")
    return client.post("/webhook", content=raw,
                       headers={"X-Line-Signature": signature})


def text_event(text: str, user_id: str = "U1") -> dict:
    return {"events": [{
        "type": "message",
        "replyToken": "reply-token",
        "source": {"type": "user", "userId": user_id},
        "message": {"type": "text", "text": text},
    }]}


# --- Signature verification ------------------------------------------------

def test_a_correctly_signed_request_is_accepted(client):
    assert signed(client, text_event("ヘルプ")).status_code == 200


def test_an_unsigned_request_is_rejected(client):
    response = client.post("/webhook", content=b'{"events":[]}')
    assert response.status_code == 400


def test_a_wrongly_signed_request_is_rejected(client):
    assert signed(client, text_event("ヘルプ"), secret="wrong").status_code == 400


def test_an_unsigned_request_reaches_neither_the_model_nor_the_user(
        client, store, transport):
    """An open webhook generates readings, and therefore spend, for anyone
    who finds the URL."""
    client.post("/webhook", content=json.dumps(text_event("恋愛運")).encode(),
                headers={"X-Line-Signature": "not-a-signature"})
    assert transport.sent == []
    assert list(store.iter_llm_usage()) == []


def test_a_tampered_body_is_rejected(client):
    """The signature covers the body, so an edited event fails."""
    raw = json.dumps(text_event("ヘルプ")).encode("utf-8")
    signature = base64.b64encode(
        hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()).decode()
    tampered = raw.replace(b"U1", b"U2")
    response = client.post("/webhook", content=tampered,
                           headers={"X-Line-Signature": signature})
    assert response.status_code == 400


# --- Everything that goes out is an Outbound -------------------------------

def test_every_reply_is_a_screened_outbound(client, transport):
    for text in ["ヘルプ", "1990-05-15", "恋愛運を教えてください",
                 "もう死にたい", "癌は治りますか", "今日の運勢"]:
        signed(client, text_event(text))
    assert transport.sent
    assert all(isinstance(entry["message"], Outbound)
               for entry in transport.sent)


def test_a_generated_reading_carries_the_disclosure(client, transport):
    signed(client, text_event("1990-05-15 07:30"))
    signed(client, text_event("恋愛運を教えてください"))
    reading = [entry["message"] for entry in transport.sent
               if entry["message"].kind == "reading"]
    assert reading and reading[-1].text.endswith(AI_DISCLOSURE_SHORT)


# --- Routing ---------------------------------------------------------------

def test_a_follow_sends_the_welcome(client, transport):
    signed(client, {"events": [{
        "type": "follow", "replyToken": "reply-token",
        "source": {"type": "user", "userId": "U1"},
    }]})
    assert "はじめまして" in transport.texts[0]
    assert "AI" in transport.texts[0]


def test_help_is_answered_without_a_model_call(client, store, transport):
    signed(client, text_event("ヘルプ"))
    assert transport.texts[0] == TEMPLATES[Msg.HELP].format(limit=3)
    assert list(store.iter_llm_usage()) == []


def test_a_bare_date_registers_birth_data(client, store, transport):
    signed(client, text_event("1990-05-15"))
    assert store.get_user("U1")["birth_date"] == "1990-05-15"
    assert store.get_user("U1")["birth_time"] is None
    assert "1990年5月15日" in transport.texts[0]


def test_a_date_with_a_time_registers_both(client, store):
    signed(client, text_event("1990-05-15 07:30"))
    user = store.get_user("U1")
    assert user["birth_date"] == "1990-05-15"
    assert user["birth_time"] == "07:30"


def test_registration_without_a_time_says_three_pillars(client, transport):
    signed(client, text_event("1990-05-15"))
    assert "三柱" in transport.texts[0]


def test_a_question_mentioning_a_year_is_not_read_as_registration(
        client, store, transport):
    """"1990年に別れた人のことが忘れられません" is a question that happens to
    contain a date, not a registration."""
    signed(client, text_event("1990年5月15日に別れた人のことが忘れられません"))
    assert "birth_date" not in store.get_user("U1")


def test_a_question_before_registration_asks_for_birth_data(client, transport):
    signed(client, text_event("恋愛運を教えてください"))
    assert transport.texts[0] == TEMPLATES[Msg.NEED_BIRTH_DATA_FIRST]


def test_a_question_after_registration_produces_a_reading(client, transport):
    signed(client, text_event("1990-05-15 07:30"))
    signed(client, text_event("恋愛運を教えてください"))
    assert transport.sent[-1]["message"].kind == "reading"


def test_the_daily_command_is_routed_as_a_daily_reading(client, store,
                                                        transport):
    signed(client, text_event("1990-05-15 07:30"))
    signed(client, text_event("今日の運勢"))
    delivered = [e for e in store.iter_events()
                 if e["type"] == "reading_delivered"]
    assert delivered[-1]["kind"] == "daily"


def test_crisis_language_is_redirected_even_before_registration(client,
                                                               transport):
    signed(client, text_event("もう死にたい"))
    assert transport.texts[0] == TEMPLATES[Msg.CRISIS]


def test_a_non_text_message_asks_for_birth_data(client, transport):
    signed(client, {"events": [{
        "type": "message", "replyToken": "reply-token",
        "source": {"type": "user", "userId": "U1"},
        "message": {"type": "sticker", "stickerId": "1"},
    }]})
    assert transport.texts[0] == TEMPLATES[Msg.ASK_BIRTH_DATA]


def test_an_event_without_a_user_is_ignored(client, transport):
    signed(client, {"events": [{"type": "message", "replyToken": "t",
                                "source": {"type": "room"},
                                "message": {"type": "text", "text": "hi"}}]})
    assert transport.sent == []


def test_one_bad_event_does_not_drop_the_rest_of_the_batch(client, transport):
    """LINE retries a non-200, which would re-deliver the good events too."""
    batch = {"events": [
        {"type": "message"},  # no source, no reply token
        text_event("ヘルプ")["events"][0],
    ]}
    assert signed(client, batch).status_code == 200
    assert len(transport.sent) == 1


# --- Ops endpoints ---------------------------------------------------------

def test_health_needs_no_token_and_reports_the_placeholder_state(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["prompts_are_placeholders"] is True


def test_the_review_queue_is_not_public(client):
    """It contains birth data. The prototype served its stats endpoint to
    anyone who found it."""
    assert client.get("/admin/review-queue").status_code == 401
    assert client.get("/admin/stats").status_code == 401


def test_a_wrong_admin_token_is_rejected(client):
    assert client.get("/admin/stats",
                      headers={"X-Admin-Token": "guess"}).status_code == 401


def test_admin_endpoints_are_disabled_when_no_token_is_configured(
        store, transport):
    """Unset means off, not open."""
    config = Config.from_env({"LINE_CHANNEL_SECRET": SECRET,
                              "LINE_CHANNEL_ACCESS_TOKEN": "t"})
    gateway = ModelGateway(StubModel(), BudgetGuard(store, 10.0))
    app = create_app(config=config, storage=store, transport=transport,
                     service=ReadingService(store, gateway, config))
    assert TestClient(app).get("/admin/stats").status_code == 503


def test_stats_report_spend_and_the_missing_hour_rate(client):
    signed(client, text_event("1990-05-15"))
    signed(client, text_event("恋愛運を教えてください"))
    stats = client.get("/admin/stats",
                       headers={"X-Admin-Token": "test-admin-token"}).json()
    assert stats["llm_spend_month_to_date_usd"] > 0
    assert stats["missing_birth_time_rate"] == 1.0
    assert stats["prompts_are_placeholders"] is True


def test_the_review_queue_is_served_with_a_token(client):
    response = client.get("/admin/review-queue",
                          headers={"X-Admin-Token": "test-admin-token"})
    assert response.status_code == 200
    assert response.json() == {"open": []}
