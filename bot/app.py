"""
The webhook. Transport and routing only.

Nothing in this module decides anything about a reading. It verifies a
signature, works out which user said what, hands it to `ReadingService`, and
sends back whatever `Outbound` comes out. Every compliance guarantee lives
below this layer, on purpose: a routing file is where "just this once"
changes get made.

Ported from reference/phase0_prototype/app.py. The differences:

  * The prototype validated all credentials at import, which makes the
    credential-free paths Sprint 01 requires impossible. `create_app` takes
    its collaborators, so tests build one with a stub model and a null
    transport, and `python -m bot.app` builds the real thing.
  * Signature verification is ours (see line_api.py), so the webhook is
    testable without an SDK object.
  * The admin endpoints need a token. The review queue contains birth data,
    which is personal information under 個人情報保護法; the prototype served
    its stats endpoint to anyone who found it.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse

from bot import alerts, funnel, payments, prompts_ja
from bot.config import Config, load_env
from bot.cost import BudgetGuard
from bot.line_api import LineTransport, parse_events, verify_signature
from bot.llm import ModelGateway, OpenAIModel
from bot.messages_ja import Msg, quick
from bot.outbound import Transport, canned
from bot.reading import BirthData, ReadingService, parse_birth_data
from bot.storage import JST, Storage

logger = logging.getLogger("uranai.app")

HELP_COMMANDS = {"ヘルプ", "help", "使い方"}
DAILY_COMMANDS = {"今日の運勢", "今日", "運勢"}
DEEP_COMMANDS = {"詳しく", "詳細"}

# 個人情報保護法 開示・削除. Statutory rights, so they are commands rather
# than something a user has to email us about.
DATA_COMMANDS = {"データ確認", "登録内容", "個人情報"}
DELETE_COMMANDS = {"データ削除", "削除"}
DELETE_CONFIRM = {"削除する", "はい削除"}


def create_app(config: Optional[Config] = None,
               storage: Optional[Storage] = None,
               transport: Optional[Transport] = None,
               service: Optional[ReadingService] = None) -> FastAPI:
    """Build the app. Everything is injectable so the tests need no secrets."""
    if config is None:
        load_env()
        config = Config.from_env()
        config.require_all()

    storage = storage or Storage(config.data_dir)

    if transport is None:
        config.require_line()
        transport = LineTransport(config.line_channel_access_token)

    if service is None:
        config.require_llm()
        gateway = ModelGateway(OpenAIModel(config.openai_api_key),
                               BudgetGuard(storage, config.monthly_llm_budget_usd))
        service = ReadingService(storage, gateway, config,
                                 alerts=alerts.alerts_for(config, transport))

    app = FastAPI(title="AI Uranai (Phase 0)")
    app.state.config = config
    app.state.storage = storage
    app.state.transport = transport
    app.state.service = service

    warning = prompts_ja.startup_warning()
    if warning:
        logger.warning(warning)

    def require_admin(x_admin_token: Optional[str] = Header(default=None)):
        """The review queue holds birth data. It is not a public endpoint."""
        expected = config.admin_token
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="admin endpoints are disabled: ADMIN_TOKEN is not set",
            )
        if not x_admin_token or x_admin_token != expected:
            raise HTTPException(status_code=401, detail="bad admin token")

    # --- Webhook ----------------------------------------------------------

    @app.post("/webhook")
    async def webhook(request: Request):
        body = await request.body()
        signature = request.headers.get("X-Line-Signature")

        if not verify_signature(config.line_channel_secret, body, signature):
            # An open webhook generates readings, and therefore spend, for
            # anyone who finds the URL.
            logger.warning("rejected a webhook with a bad signature")
            raise HTTPException(status_code=400, detail="invalid signature")

        for event in parse_events(body):
            try:
                _handle_event(app, event)
            except Exception:
                # One malformed event must not drop the rest of the batch,
                # and LINE retries a non-200.
                logger.exception("event handling failed")

        return PlainTextResponse("OK")

    @app.post("/stripe/webhook")
    async def stripe_webhook(request: Request):
        """Stripe telling us a payment completed.

        Signature-verified. Unverified, anyone who finds this endpoint can
        claim a payment and be handed a paid reading — the mirror of the
        LINE webhook, and the same reasoning.

        The reply is a push, not a reply: there is no reply token, because
        the user is on Stripe's page rather than in the conversation. That
        is the path `Transport.push` was declared abstract for while it was
        still unused.
        """
        if not payments.enabled_for(config):
            raise HTTPException(status_code=503, detail="payments disabled")

        provider = payments.StripeProvider(config)
        event = provider.verify_webhook(
            await request.body(), request.headers.get("Stripe-Signature", ""))
        if event is None:
            raise HTTPException(status_code=400, detail="bad signature")

        if event.get("type") != "checkout.session.completed":
            return PlainTextResponse("ignored")

        session = event["data"]["object"]
        user_id = session.get("client_reference_id")
        if not user_id:
            logger.error("completed checkout with no client_reference_id")
            return PlainTextResponse("no user")

        # Idempotent: Stripe retries, and will deliver the same completion
        # more than once given the chance.
        if storage.grant_paid_credit(user_id, session.get("id", "")):
            funnel.record(storage, funnel.Stage.PAID, user_id,
                          amount_jpy=session.get("amount_total"))
            storage.log_event("payment_completed",
                              {"amount_jpy": session.get("amount_total")})
            transport.push(user_id, canned(Msg.PAYMENT_RECEIVED))
        return PlainTextResponse("OK")

    # --- Ops --------------------------------------------------------------

    @app.get("/health")
    def health():
        return {"status": "ok", "ts": datetime.now(JST).isoformat(),
                "prompts_are_placeholders": prompts_ja.PROMPTS_ARE_PLACEHOLDERS}

    @app.get("/admin/stats", dependencies=[Depends(require_admin)])
    def admin_stats():
        guard = BudgetGuard(storage, config.monthly_llm_budget_usd)
        return {
            **storage.get_stats(),
            "llm_spend_month_to_date_usd": round(guard.month_to_date_usd(), 4),
            "llm_budget_usd": config.monthly_llm_budget_usd,
            "prompts_are_placeholders": prompts_ja.PROMPTS_ARE_PLACEHOLDERS,
            "operator_alerts_configured":
                alerts.alerts_for(config, transport).configured,
            "overdue_manual_reviews": len(alerts.overdue_reviews(storage)),
        }

    @app.get("/admin/funnel", dependencies=[Depends(require_admin)])
    def admin_funnel():
        """The Phase 0 number, or an honest statement that we do not have it.

        Behind the admin token because it is commercially sensitive and
        because it identifies users by id.
        """
        return {
            **funnel.report(storage),
            "payments_enabled": payments.enabled_for(config),
            "blocking_gates": payments.blocking_gates(config),
        }

    @app.get("/admin/review-queue", dependencies=[Depends(require_admin)])
    def admin_review_queue():
        """Boundary charts waiting for a person. Contains birth data."""
        return {"open": storage.open_reviews()}

    return app


# --- Routing ---------------------------------------------------------------

def _handle_event(app: FastAPI, event: dict) -> None:
    storage: Storage = app.state.storage
    transport: Transport = app.state.transport
    config: Config = app.state.config

    event_type = event.get("type")
    user_id = (event.get("source") or {}).get("userId")
    reply_token = event.get("replyToken")

    if not user_id or not reply_token:
        return

    if event_type == "follow":
        storage.upsert_user(user_id, {"followed_at": datetime.now(JST).isoformat()})
        storage.log_event("follow")
        funnel.record(storage, funnel.Stage.FOLLOWED, user_id)
        transport.reply(reply_token,
                        canned(Msg.WELCOME, quick("start"),
                               limit=config.free_tier_limit))
        return

    if event_type == "unfollow":
        storage.log_event("unfollow")
        return

    if event_type == "postback":
        # LINE's date picker returns here rather than as a message. It is
        # the one input path where a user cannot get the format wrong,
        # which for a birth date is most of the difficulty.
        params = (event.get("postback") or {}).get("params") or {}
        picked = params.get("date")
        if picked:
            _register_birth(app, user_id, reply_token,
                            parse_birth_data(picked))
        return

    if event_type != "message":
        return

    message = event.get("message") or {}
    if message.get("type") != "text":
        transport.reply(reply_token, canned(Msg.ASK_BIRTH_DATA))
        return

    _handle_text(app, user_id, reply_token, (message.get("text") or "").strip())


def _handle_text(app: FastAPI, user_id: str, reply_token: str,
                 text: str) -> None:
    storage: Storage = app.state.storage
    transport: Transport = app.state.transport
    service: ReadingService = app.state.service
    config: Config = app.state.config

    storage.increment_message_count(user_id)

    # A pending deletion is cancelled by anything that is not the
    # confirmation. Computed here, before the command dispatch, because
    # several commands return early — with this at the bottom, saying
    # 「ヘルプ」 left the deletion armed and a later stray 「削除する」 would
    # have erased their data.
    #
    # Silence is the safe default for something irreversible: a user who
    # changes their mind should not have to say so.
    awaiting_erasure = bool(storage.get_user(user_id).get("awaiting_erasure"))
    if awaiting_erasure and text not in DELETE_CONFIRM \
            and text not in DELETE_COMMANDS:
        storage.upsert_user(user_id, {"awaiting_erasure": False})
        awaiting_erasure = False

    if text in HELP_COMMANDS:
        transport.reply(reply_token,
                        canned(Msg.HELP, limit=config.free_tier_limit))
        return

    if text in DEEP_COMMANDS:
        _handle_checkout(app, user_id, reply_token)
        return

    if text in DATA_COMMANDS:
        _handle_data_request(app, user_id, reply_token)
        return

    if text in DELETE_COMMANDS:
        storage.upsert_user(user_id, {"awaiting_erasure": True})
        transport.reply(reply_token, canned(Msg.DATA_DELETE_CONFIRM))
        return

    if text in DELETE_CONFIRM and awaiting_erasure:
        summary = storage.erase_user(user_id)
        logger.info("erasure completed: profile_deleted=%s events=%d",
                    summary["profile_deleted"], summary["events_anonymised"])
        transport.reply(reply_token, canned(Msg.DATA_DELETED))
        return

    # A message that is only birth data is registration, not a question.
    # Anything else is passed through to the pipeline, which screens it —
    # so a crisis message inside a birthday-shaped string is still screened,
    # because parse_birth_data cannot match one.
    birth = parse_birth_data(text)
    if birth is not None and _is_registration(text):
        _register_birth(app, user_id, reply_token, birth)
        return

    # She tried to give us a date and we could not read it. Before this,
    # that fell through to "please give me your birth date" — the same
    # request again, with no sign that the format was the problem. The
    # message for it existed and was wired to nothing.
    if birth is None and _looks_like_a_date_attempt(text):
        transport.reply(reply_token, canned(Msg.BIRTH_DATA_UNPARSEABLE,
                                            quick("start")))
        return

    stored = BirthData.from_record(storage.get_user(user_id))
    kind = "daily" if text in DAILY_COMMANDS else "question"

    outcome = service.generate(user_id, text, birth=stored, kind=kind)

    message = outcome.message
    # No buttons on a crisis reply or a professional referral. Those are not
    # moments to offer someone a menu, and a row of cheerful suggestions
    # under a helpline would undo the tone the message is carrying.
    if outcome.outcome in ("delivered", "quota_exhausted", "paywall_shown"):
        message = message.with_quick(quick("after_reading"))
    transport.reply(reply_token, message)


def _handle_data_request(app: FastAPI, user_id: str, reply_token: str) -> None:
    """開示 — what we hold about this person, in their own words."""
    storage: Storage = app.state.storage
    transport: Transport = app.state.transport

    export = storage.export_user(user_id)
    if not export["held"]:
        transport.reply(reply_token, canned(Msg.DATA_NONE))
        return

    birth = BirthData.from_record(export["profile"])
    created = (export["profile"].get("created_at") or "")[:10]
    transport.reply(reply_token, canned(
        Msg.DATA_SUMMARY,
        birth=birth.summary() if birth else "未登録",
        readings=export["readings"],
        created=created or "不明",
    ))


def _handle_checkout(app: FastAPI, user_id: str, reply_token: str) -> None:
    """The user asked for the paid reading.

    Refuses unless every launch gate is met. Taking ¥300 for a reading
    written by an engineer, with no 特商法 notice and no legal review, is not
    a pricing experiment — see bot/payments.py.
    """
    storage: Storage = app.state.storage
    transport: Transport = app.state.transport
    config: Config = app.state.config

    if not payments.enabled_for(config):
        logger.info("checkout requested but gates not met: %s",
                    ", ".join(payments.blocking_gates(config)))
        transport.reply(reply_token, canned(Msg.PAYMENT_UNAVAILABLE))
        return

    provider = payments.provider_for(config)
    checkout = provider.create_checkout(
        user_id, config.deep_reading_price_jpy, "深層鑑定 1回")
    storage.log_event("checkout_created", {"provider": provider.name})
    funnel.record(storage, funnel.Stage.CHECKOUT_STARTED, user_id,
                  provider=provider.name)
    transport.reply(reply_token, canned(
        Msg.CHECKOUT_HANDOFF, url=checkout.url,
        price=config.deep_reading_price_jpy))


def _register_birth(app: FastAPI, user_id: str, reply_token: str,
                    birth) -> None:
    """Shared by typed dates and the date picker."""
    storage: Storage = app.state.storage
    transport: Transport = app.state.transport

    if birth is None:
        transport.reply(reply_token, canned(Msg.BIRTH_DATA_UNPARSEABLE,
                                            quick("start")))
        return

    storage.upsert_user(user_id, birth.to_record())
    storage.log_event("birth_data_registered", {"hour_known": birth.hour_known})
    funnel.record(storage, funnel.Stage.REGISTERED, user_id,
                  hour_known=birth.hour_known)
    transport.reply(reply_token, canned(
        Msg.BIRTH_DATA_SAVED, quick("after_register"),
        birth_summary=birth.summary(), time_note=_time_note(birth),
    ))


_DATE_ATTEMPT = re.compile(r"\d{4}|[0-9]{1,2}\s*[/／年月日]|生年月日|誕生日")


def _looks_like_a_date_attempt(text: str) -> bool:
    """Did she mean to give us a date and get the shape wrong?

    Deliberately loose. A false positive costs one extra "here is the
    format" message; a false negative sends her round the same loop with no
    hint of what went wrong, which is where people give up.
    """
    return bool(_DATE_ATTEMPT.search(text)) and len(text) <= 40


def _is_registration(text: str) -> bool:
    """True when the message is a birth date and little else.

    Guards against reading "1990年に別れた人のことが忘れられません" as a
    registration. A date plus a sentence is a question that happens to
    mention a date.
    """
    without_date = re.sub(r"[0-9\-/.:年月日時分\s]", "", text)
    return len(without_date) <= 6


def _time_note(birth: BirthData) -> str:
    # PLACEHOLDER — practitioner to rewrite. Do not ship.
    if birth.hour_known:
        return "出生時刻もお預かりしました。四柱で拝見します。"
    return ("出生時刻がわかる場合は、あとからお送りいただいても構いません。"
            "いまは三柱で拝見します。")


app = None  # built in __main__ so importing this module needs no credentials


def main() -> None:  # pragma: no cover - entry point
    import uvicorn

    load_env()
    config = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    application = create_app(config)
    uvicorn.run(application, host="0.0.0.0", port=config.port)


if __name__ == "__main__":  # pragma: no cover
    main()
