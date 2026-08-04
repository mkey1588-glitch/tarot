"""
Operator alerting for the manual-review queue.

A chart on a solar-term boundary is not answered automatically: the user is
told a person will look, and a queue entry is written. Until now that was
the whole mechanism, and it relied on someone thinking to open
`/admin/review-queue`. A promise to a user that depends on an operator's
memory is not a mechanism, it is a hope.

WHAT AN ALERT MAY CONTAIN
-------------------------
The review id and nothing else. The queue entry holds the birth data because
the reviewer needs it, and they will see it when they open the queue — but
the alert itself travels through LINE's servers and lands in a notification
on a phone, and that is not somewhere personal information belongs. This is
the same rule the log line already follows.

WHY THE ALERT GOES THROUGH THE OUTBOUND FUNNEL
----------------------------------------------
It is a push, and `bot/outbound.py` requires everything a transport sends to
be an `Outbound`. Making an exception for internal messages would put a
second, unscreened path into the transport — exactly what the funnel exists
to prevent — so the alert is registered canned copy like everything else.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Optional

from bot.messages_ja import Msg
from bot.outbound import Transport, canned

logger = logging.getLogger("uranai.alerts")


class OperatorAlerts(ABC):
    @abstractmethod
    def review_queued(self, review_id: str, reason: str) -> None:
        ...

    @property
    @abstractmethod
    def configured(self) -> bool:
        """False when nothing will actually reach a person."""


class LogOnlyAlerts(OperatorAlerts):
    """The default, and deliberately honest about being insufficient.

    A WARNING in a log file is what we had. It is fine while an engineer is
    watching a terminal and useless the moment nobody is, which is why
    `configured` is False: `/admin/stats` and `bot/readiness.py` report that
    rather than letting it look solved.
    """

    def review_queued(self, review_id: str, reason: str) -> None:
        logger.warning(
            "manual review queued: review_id=%s reason=%s — NO OPERATOR "
            "ALERT CONFIGURED, this will only be seen by someone reading "
            "logs or /admin/review-queue", review_id, reason)

    @property
    def configured(self) -> bool:
        return False


class LineOperatorAlerts(OperatorAlerts):
    """Pushes to the operator's own LINE account.

    Chosen because it reaches a phone, needs no new dependency, and reuses
    the transport that already exists. A failure to alert is logged loudly
    and never raised: the user has already been told a person will look, and
    losing the reply to them because a notification failed would be a worse
    outcome than a missed notification.
    """

    def __init__(self, transport: Transport, operator_user_id: str):
        self.transport = transport
        self.operator_user_id = operator_user_id

    def review_queued(self, review_id: str, reason: str) -> None:
        logger.warning("manual review queued: review_id=%s reason=%s",
                       review_id, reason)
        try:
            self.transport.push(
                self.operator_user_id,
                canned(Msg.OPERATOR_REVIEW_ALERT, review_id=review_id),
            )
        except Exception as exc:
            logger.error("operator alert failed (%s) for review_id=%s — the "
                         "queue entry is still there",
                         type(exc).__name__, review_id)

    @property
    def configured(self) -> bool:
        return True


def alerts_for(config, transport: Optional[Transport]) -> OperatorAlerts:
    operator = getattr(config, "operator_line_user_id", None)
    if operator and transport is not None:
        return LineOperatorAlerts(transport, operator)
    return LogOnlyAlerts()


def overdue_reviews(storage, hours: int = 24) -> List[dict]:
    """Open reviews older than `hours`.

    An alert that fires and is then ignored is the same as no alert, so the
    queue's age is reported alongside its length.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    overdue = []
    for review in storage.open_reviews():
        try:
            raised = datetime.fromisoformat(review["ts"])
        except (KeyError, ValueError):
            continue
        if raised.tzinfo is None:
            raised = raised.replace(tzinfo=timezone.utc)
        if raised < cutoff:
            overdue.append(review)
    return overdue
