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


# --- The Phase 0 funnel ----------------------------------------------------

def test_the_funnel_records_a_users_journey(client, store):
    from bot import funnel
    signed(client, {"events": [{"type": "follow", "replyToken": "t",
                                "source": {"type": "user", "userId": "U1"}}]})
    signed(client, text_event("1990-05-15 07:30"))
    signed(client, text_event("恋愛運を教えてください"))

    counted = funnel.counts(store)
    assert counted["followed"] == 1
    assert counted["registered"] == 1
    assert counted["free_reading"] == 1
    assert counted["paywall_shown"] == 0     # not permitted to sell yet


def test_asking_for_the_paid_reading_is_refused_while_gated(client, transport,
                                                            store):
    from bot import funnel
    signed(client, text_event("詳しく"))
    assert transport.texts[-1] == TEMPLATES[Msg.PAYMENT_UNAVAILABLE]
    assert funnel.counts(store)["checkout_started"] == 0


def test_the_funnel_report_needs_the_admin_token(client):
    assert client.get("/admin/funnel").status_code == 401


def test_the_funnel_report_says_it_has_no_answer_yet(client):
    """A conversion rate of 0% and 'nobody has been asked' are different
    claims. Phase 0 is at the second one and the report must say so."""
    body = client.get("/admin/funnel",
                      headers={"X-Admin-Token": "test-admin-token"}).json()
    assert body["headline"] == "paid_of_offered"
    assert body["overall"]["rates"]["paid_of_offered"] is None
    assert body["payments_enabled"] is False
    assert "prompts_written" in body["blocking_gates"]


# --- 個人情報保護法: disclosure and erasure ---------------------------------

def register(client, birth="1990-05-15 07:30"):
    signed(client, text_event(birth))


def test_a_user_can_see_what_is_held_about_them(client, transport):
    """開示 is a statutory right, so it is a command rather than something a
    user has to email us about."""
    register(client)
    signed(client, text_event("恋愛運を教えてください"))
    signed(client, text_event("データ確認"))

    summary = transport.texts[-1]
    assert "1990年5月15日 07:30" in summary
    assert "1 回" in summary          # one reading so far


def test_an_unregistered_user_is_told_exactly_what_is_held(client, transport):
    """Not "nothing". Sending us a message creates a row with a timestamp
    and a counter, so claiming we hold nothing would be a false statement
    about what we keep — which is worse than not offering disclosure."""
    signed(client, text_event("データ確認"))
    summary = transport.texts[-1]
    assert "未登録" in summary
    assert "0 回" in summary


def test_data_none_is_for_someone_we_have_never_seen(store, transport, config):
    """Reachable only before any message, which is when it is true."""
    assert store.export_user("U-never-seen")["held"] is False


def test_deletion_asks_once_before_doing_it(client, store, transport):
    """One confirmation, because erasure cannot be undone. Not two, and not
    a buried link — making deletion hard is the dark pattern Rule 4 forbids,
    and making it accidental is its own harm."""
    register(client)
    signed(client, text_event("データ削除"))
    assert "元に戻すことはできません" in transport.texts[-1]
    assert store.get_user("U1")["birth_date"] == "1990-05-15"   # not yet gone


def test_confirming_erases_the_birth_data(client, store, transport):
    register(client)
    signed(client, text_event("データ削除"))
    signed(client, text_event("削除する"))

    assert transport.texts[-1] == TEMPLATES[Msg.DATA_DELETED]
    assert store.get_user("U1") == {}
    assert "1990-05-15" not in store.users_file.read_text(encoding="utf-8")


def test_confirming_without_asking_first_does_nothing(client, store):
    """削除する out of the blue is far more likely to be a stray message
    than an intention to erase."""
    register(client)
    signed(client, text_event("削除する"))
    assert store.get_user("U1")["birth_date"] == "1990-05-15"


def test_any_other_message_cancels_a_pending_deletion(client, store):
    """Silence is the safe default for something irreversible: a user who
    changes their mind should not have to say so."""
    register(client)
    signed(client, text_event("データ削除"))
    signed(client, text_event("ヘルプ"))
    signed(client, text_event("削除する"))
    assert store.get_user("U1")["birth_date"] == "1990-05-15"


def test_erasure_keeps_the_counts_it_contributed_to(client, store):
    """Deleting the funnel rows would corrupt the one number Phase 0 exists
    to produce, and it is not what the right requires: a record whose
    identifier has been replaced by a value we never stored a mapping for
    can no longer identify anyone."""
    from bot import funnel
    register(client)
    signed(client, text_event("恋愛運を教えてください"))
    before = funnel.counts(store)

    signed(client, text_event("データ削除"))
    signed(client, text_event("削除する"))

    assert funnel.counts(store) == before
    assert "U1" not in store.events_file.read_text(encoding="utf-8")


def test_erasure_removes_birth_data_from_the_review_queue(client, store):
    """Those entries carry a birth date. Only the review id and timestamp
    survive, which is what an operator needs to know a case was closed."""
    signed(client, text_event("2020-08-07"))
    signed(client, text_event("恋愛運を教えてください"))
    assert any(r.get("birth_date") for r in store.open_reviews())

    signed(client, text_event("データ削除"))
    signed(client, text_event("削除する"))

    remaining = store.open_reviews()
    assert remaining and not any(r.get("birth_date") for r in remaining)
    assert remaining[0]["review_id"]


def test_the_help_text_mentions_both_rights(client, transport):
    signed(client, text_event("ヘルプ"))
    assert "データ確認" in transport.texts[-1]
    assert "データ削除" in transport.texts[-1]


# --- B1/B3/B4: buttons, the date picker, and getting the format wrong ------

def test_a_reading_offers_the_next_step(client, transport):
    register(client)
    signed(client, text_event("恋愛運を教えてください"))
    labels = [a.label for a in transport.sent[-1]["message"].quick]
    assert "今日の運勢" in labels


def test_a_crisis_reply_carries_no_buttons(client, transport):
    """A row of cheerful suggestions under a helpline would undo the tone
    the message is carrying."""
    register(client)
    signed(client, text_event("もう死にたい"))
    assert transport.sent[-1]["message"].quick == ()


def test_a_professional_referral_carries_no_buttons(client, transport):
    register(client)
    signed(client, text_event("癌は治りますか"))
    assert transport.sent[-1]["message"].quick == ()


def test_the_welcome_offers_the_date_picker(client, transport):
    signed(client, {"events": [{"type": "follow", "replyToken": "t",
                                "source": {"type": "user", "userId": "U1"}}]})
    kinds = {a.kind for a in transport.sent[-1]["message"].quick}
    assert "date" in kinds


def test_the_date_picker_registers_a_birth_date(client, store, transport):
    """The one input path where the format cannot be got wrong, which for a
    birth date is most of the difficulty."""
    signed(client, {"events": [{
        "type": "postback", "replyToken": "t",
        "source": {"type": "user", "userId": "U1"},
        "postback": {"data": "birth_date", "params": {"date": "1985-03-10"}},
    }]})
    assert store.get_user("U1")["birth_date"] == "1985-03-10"
    assert "1985年3月10日" in transport.texts[-1]


def test_a_postback_without_a_date_is_ignored(client, store):
    signed(client, {"events": [{
        "type": "postback", "replyToken": "t",
        "source": {"type": "user", "userId": "U1"},
        "postback": {"data": "something-else", "params": {}},
    }]})
    assert "birth_date" not in store.get_user("U1")


@pytest.mark.parametrize("attempt", [
    "1990年13月45日", "1990/5", "誕生日は忘れました", "19900515",
])
def test_a_failed_date_gets_the_format_rather_than_the_same_request(
        client, transport, attempt):
    """Before this it fell through to "please give me your birth date" — the
    same request again, with no sign the format was the problem. The message
    for it existed and was wired to nothing."""
    signed(client, text_event(attempt))
    assert transport.texts[-1] == TEMPLATES[Msg.BIRTH_DATA_UNPARSEABLE]


def test_a_real_question_is_not_mistaken_for_a_failed_date(client, transport):
    """The detector is loose on purpose, but it must not swallow questions."""
    register(client)
    signed(client, text_event("彼と別れるべきか迷っています"))
    assert transport.texts[-1] != TEMPLATES[Msg.BIRTH_DATA_UNPARSEABLE]


def test_quick_reply_labels_are_screened_copy():
    """A button is smaller than a paragraph and correspondingly easier to
    forget is user-facing copy at all."""
    from bot.messages_ja import QUICK_LABELS
    from bot.safety import Verdict, screen_output
    for label in QUICK_LABELS:
        assert screen_output(label).verdict is Verdict.ALLOW, label


def test_attaching_buttons_keeps_the_rendered_parameters(client, transport):
    """Rebuilding from the Msg would drop them — for MANUAL_REVIEW that is
    the reference number the user was told to quote."""
    from bot.messages_ja import quick as quick_set
    from bot.outbound import canned as canned_msg
    message = canned_msg(Msg.MANUAL_REVIEW, review_id="abc123")
    with_buttons = message.with_quick(quick_set("after_reading"))
    assert "abc123" in with_buttons.text
    assert with_buttons.quick
