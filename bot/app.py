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

from bot import funnel, payments, prompts_ja
from bot.config import Config, load_env
from bot.cost import BudgetGuard
from bot.line_api import LineTransport, parse_events, verify_signature
from bot.llm import ModelGateway, OpenAIModel
from bot.messages_ja import Msg
from bot.outbound import Transport, canned
from bot.reading import BirthData, ReadingService, parse_birth_data
from bot.storage import JST, Storage

logger = logging.getLogger("uranai.app")

HELP_COMMANDS = {"ヘルプ", "help", "使い方"}
DAILY_COMMANDS = {"今日の運勢", "今日", "運勢"}
DEEP_COMMANDS = {"詳しく", "詳細"}


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
        service = ReadingService(storage, gateway, config)

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
                        canned(Msg.WELCOME, limit=config.free_tier_limit))
        return

    if event_type == "unfollow":
        storage.log_event("unfollow")
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

    if text in HELP_COMMANDS:
        transport.reply(reply_token,
                        canned(Msg.HELP, limit=config.free_tier_limit))
        return

    if text in DEEP_COMMANDS:
        _handle_checkout(app, user_id, reply_token)
        return

    # A message that is only birth data is registration, not a question.
    # Anything else is passed through to the pipeline, which screens it —
    # so a crisis message inside a birthday-shaped string is still screened,
    # because parse_birth_data cannot match one.
    birth = parse_birth_data(text)
    if birth is not None and _is_registration(text):
        storage.upsert_user(user_id, birth.to_record())
        storage.log_event("birth_data_registered",
                          {"hour_known": birth.hour_known})
        funnel.record(storage, funnel.Stage.REGISTERED, user_id,
                      hour_known=birth.hour_known)
        transport.reply(reply_token, canned(
            Msg.BIRTH_DATA_SAVED,
            birth_summary=birth.summary(),
            time_note=_time_note(birth),
        ))
        return

    stored = BirthData.from_record(storage.get_user(user_id))
    kind = "daily" if text in DAILY_COMMANDS else "question"

    outcome = service.generate(user_id, text, birth=stored, kind=kind)
    transport.reply(reply_token, outcome.message)


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
