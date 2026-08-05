"""
LINE transport and webhook signature verification.

Signature verification is done here rather than through the SDK's
`WebhookParser`. It is six lines of HMAC, and doing it ourselves means the
webhook can be unit-tested without constructing an SDK object, without
credentials and without network — which is what makes the Sprint 01
requirement true of `app.py` and not only of the pipeline underneath it.

Sending still goes through the SDK, since that is a real HTTP client with
retries and error types we should not reimplement. The SDK is imported
lazily so that importing this module needs neither the package nor a token.

The prototype used the v2 API (`from linebot import LineBotApi`), which is
deprecated in the installed line-bot-sdk 3.x. This is the v3 equivalent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bot.outbound import Outbound, Transport

logger = logging.getLogger("uranai.line")

JST = timezone(timedelta(hours=9))


def verify_signature(channel_secret: str, body: bytes,
                     signature_header: Optional[str]) -> bool:
    """Constant-time check of LINE's X-Line-Signature header.

    An unverified webhook is an open endpoint that will generate readings,
    and therefore spend, for anyone who finds the URL.
    """
    if not signature_header or not channel_secret:
        return False
    digest = hmac.new(channel_secret.encode("utf-8"), body,
                      hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature_header)


def parse_events(body: bytes) -> List[Dict[str, Any]]:
    """Pull the event list out of a verified webhook body.

    Only called after `verify_signature` has passed.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.error("webhook body was not valid JSON")
        return []
    events = payload.get("events")
    return events if isinstance(events, list) else []


class LineTransport(Transport):
    """Sends via the LINE Messaging API.

    Both methods take an `Outbound` and check it at runtime. That is what
    makes "every reply is screened" and "every push is screened" the same
    statement — see bot/outbound.py.
    """

    def __init__(self, channel_access_token: str):
        self._token = channel_access_token
        self._api = None

    def _messaging_api(self):
        if self._api is None:
            from linebot.v3.messaging import (
                ApiClient, Configuration, MessagingApi,
            )
            self._api = MessagingApi(
                ApiClient(Configuration(access_token=self._token)))
        return self._api

    @staticmethod
    def _text_messages(message: Outbound):
        from linebot.v3.messaging import (
            DatetimePickerAction, MessageAction, QuickReply, QuickReplyItem,
            TextMessage,
        )

        def to_item(action):
            if action.kind == "date":
                return QuickReplyItem(action=DatetimePickerAction(
                    label=action.label, data="birth_date", mode="date",
                    # Nobody in the target cohort was born outside this
                    # range, and an open-ended picker makes a 1990 birthday
                    # a long scroll.
                    initial="1990-01-01", min="1900-01-01",
                    max=datetime.now(JST).strftime("%Y-%m-%d"),
                ))
            return QuickReplyItem(action=MessageAction(
                label=action.label, text=action.payload))

        chunks = message.chunks()
        messages = [TextMessage(text=chunk) for chunk in chunks]
        if message.quick and messages:
            # LINE shows quick replies on the last message of a reply only.
            messages[-1].quick_reply = QuickReply(
                items=[to_item(a) for a in message.quick])
        return messages

    def reply(self, reply_token: str, message: Outbound) -> None:
        self._require_outbound(message)
        from linebot.v3.messaging import ReplyMessageRequest
        try:
            self._messaging_api().reply_message(ReplyMessageRequest(
                reply_token=reply_token,
                messages=self._text_messages(message),
            ))
        except Exception as exc:
            # Never log the message: it may be a reading, and a reading is
            # derived from personal information.
            logger.error("LINE reply failed: %s", type(exc).__name__)

    def push(self, user_id: str, message: Outbound) -> None:
        self._require_outbound(message)
        from linebot.v3.messaging import PushMessageRequest
        try:
            self._messaging_api().push_message(PushMessageRequest(
                to=user_id,
                messages=self._text_messages(message),
            ))
        except Exception as exc:
            logger.error("LINE push failed: %s", type(exc).__name__)
