"""
The reply choke point. Nothing reaches a user except as an `Outbound`, and
an `Outbound` can only be minted by the two functions below.

  reading()  model-generated text. Always screened by screen_output(), and
             always given the AI disclosure. No exceptions, ever.

  canned()   copy we wrote, identified by a `Msg` member. Screened in the
             test suite rather than at runtime, for the reason in
             messages_ja.py: a runtime filter over our own reviewed text
             could block the crisis message, which is the worst failure
             available to this system.

There is deliberately no `send_text(str)`. A helper taking a bare string is
one refactor away from being handed a fragment of model output, and at that
point neither guarantee holds.

A transport (`Transport` subclass) accepts `Outbound` and nothing else, so
"every reply is screened" and "every push is screened" are the same
statement. The daily reading will be a push, not a reply — that path does
not exist yet, and `test_outbound.py` is written so it fails the suite on
the day someone adds it without routing it through here.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Sequence

from bot.messages_ja import NEVER_APPEND, TEMPLATES, Msg
from bot.safety import Verdict, screen_output, with_disclosure

logger = logging.getLogger("uranai.outbound")

# LINE's per-message ceiling.
LINE_MAX_CHARS = 5000

# Held by this module alone, so an Outbound cannot be constructed elsewhere.
_MINT = object()


class UnscreenedOutput(RuntimeError):
    """Raised when text tries to reach a user without going through here."""


@dataclass(frozen=True)
class QuickAction:
    """A tappable suggestion attached to a message.

    `label` is what the user sees, so it is user-facing copy and is screened
    with everything else in messages_ja. A button is a smaller thing to
    write than a paragraph and an easier one to forget is copy at all.

    kind "message" sends `payload` as if the user typed it; kind "date"
    opens LINE's date picker and returns a postback.
    """

    label: str
    kind: str = "message"
    payload: str = ""


@dataclass(frozen=True)
class Outbound:
    """Text cleared to send: screened, and disclosed where disclosure applies."""

    text: str
    kind: str            # "reading" | "canned"
    source: Optional[Msg] = None
    blocked_reason: Optional[str] = None
    quick: tuple = ()
    _mint: object = None

    def __post_init__(self) -> None:
        if self._mint is not _MINT:
            raise UnscreenedOutput(
                "Outbound is minted by outbound.reading() or "
                "outbound.canned(). Do not construct it directly — those two "
                "functions are what run screen_output() and with_disclosure()."
            )

    def with_quick(self, actions: Sequence["QuickAction"]) -> "Outbound":
        """Attach suggestions to an already-cleared message.

        The text is untouched, so nothing needs re-screening — this only
        adds buttons whose labels are themselves reviewed copy. Rebuilding
        the message from its `Msg` instead would silently drop the
        parameters it was rendered with, which for MANUAL_REVIEW is the
        reference number the user was told to quote.
        """
        for action in actions:
            if not isinstance(action, QuickAction):
                raise UnscreenedOutput(
                    "quick actions must be QuickAction values from "
                    "messages_ja.quick(), so their labels are reviewed copy."
                )
        return Outbound(text=self.text, kind=self.kind, source=self.source,
                        blocked_reason=self.blocked_reason,
                        quick=tuple(actions), _mint=_MINT)

    def chunks(self) -> List[str]:
        """Split for transports with a per-message limit."""
        return [self.text[i:i + LINE_MAX_CHARS]
                for i in range(0, max(len(self.text), 1), LINE_MAX_CHARS)]


def canned(message: Msg, quick: Sequence["QuickAction"] = (),
           **params) -> Outbound:
    """Send reviewed copy from `messages_ja`. Accepts a Msg, never a string."""
    if not isinstance(message, Msg):
        raise UnscreenedOutput(
            f"canned() takes a Msg member, got {type(message).__name__}. "
            "Register the copy in messages_ja.TEMPLATES so it is reviewable."
        )
    for action in quick:
        if not isinstance(action, QuickAction):
            raise UnscreenedOutput(
                "quick actions must be QuickAction values from "
                "messages_ja.QUICK, so their labels are reviewed copy."
            )
    text = TEMPLATES[message]
    if params:
        text = text.format(**params)
    return Outbound(text=text, kind="canned", source=message,
                    quick=tuple(quick), _mint=_MINT)


def reading(model_text: str, *, on_block: Msg = Msg.READING_UNAVAILABLE,
            quick: Sequence["QuickAction"] = ()) -> Outbound:
    """Screen a generated reading, then disclose. Both, always.

    A block is a prompt defect, not a user problem (E5). The reading is
    dropped rather than patched, the reason is logged for the weekly
    practitioner review, and the user gets reviewed copy that neither
    explains nor blames.
    """
    verdict = screen_output(model_text)

    if verdict.verdict is not Verdict.ALLOW:
        logger.warning("outbound blocked: %s", verdict.reason)
        blocked = canned(on_block)
        return Outbound(text=blocked.text, kind="canned", source=on_block,
                        blocked_reason=verdict.reason, _mint=_MINT)

    return Outbound(text=with_disclosure(model_text), kind="reading",
                    quick=tuple(quick), _mint=_MINT)


# --- Transports ------------------------------------------------------------

class Transport(ABC):
    """Anything that can put text in front of a user.

    Every public method on a subclass must take an `Outbound`. That is
    checked in `test_outbound.py` across all subclasses, so a `push` or a
    `broadcast` added later cannot take a bare string without failing the
    suite.
    """

    @abstractmethod
    def reply(self, reply_token: str, message: Outbound) -> None:
        ...

    @abstractmethod
    def push(self, user_id: str, message: Outbound) -> None:
        ...

    @staticmethod
    def _require_outbound(message) -> Outbound:
        if not isinstance(message, Outbound):
            raise UnscreenedOutput(
                f"transports send Outbound, got {type(message).__name__}. "
                "Build it with outbound.reading() or outbound.canned()."
            )
        return message


class NullTransport(Transport):
    """Records what would have been sent. Used by the tests and the local
    runner, so the full path can run with no LINE credentials."""

    def __init__(self):
        self.sent: List[dict] = []

    def reply(self, reply_token: str, message: Outbound) -> None:
        self._require_outbound(message)
        self.sent.append({"via": "reply", "to": reply_token,
                          "message": message})

    def push(self, user_id: str, message: Outbound) -> None:
        self._require_outbound(message)
        self.sent.append({"via": "push", "to": user_id, "message": message})

    @property
    def texts(self) -> List[str]:
        return [entry["message"].text for entry in self.sent]
