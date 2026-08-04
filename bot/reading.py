"""
The reading pipeline. One function, one order, no variations.

    screen_input()      crisis and professional routing, before any model
    free-tier quota     after screening, never before — see below
    build_payload()     the deterministic chart; may raise ManualReviewRequired
    format_for_prompt() the chart as text a practitioner can audit
    model call          interpretation only, through the single choke point
    screen_output()     景品表示法 / 霊感商法
    with_disclosure()   Rule 2
    reply

`generate` returns an `Outbound`, which is the only thing a transport will
send. Every branch below returns one, so there is no path out of this
function that produces raw text.

WHY SCREENING COMES BEFORE THE QUOTA CHECK
------------------------------------------
Someone who has typed 死にたい must not be told they have run out of free
readings. Quota is a billing concern and crisis is not, and a person in
distress meeting a paywall is the single worst thing this pipeline could
do. The order is asserted in the tests rather than left to be re-derived.

WHAT ManualReviewRequired MEANS HERE
------------------------------------
It is not an error to apologise for. The chart genuinely has two possible
readings and the engine will not pick one. The user is told a person will
look, a queue entry is written with what that person needs, and the log
line carries only the review id — never the birth details.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from bot import funnel, payments, prompts_ja
from bot.chart_service import ManualReviewRequired, build_payload, format_for_prompt
from bot.cost import BudgetExceeded
from bot.llm import ModelGateway, ModelUnavailable, ScreenedPrompt
from bot.messages_ja import PROFESSIONAL_MESSAGE, Msg
from bot.outbound import Outbound, canned, reading as outbound_reading
from bot.safety import Verdict, screen_input
from bot.storage import JST, Storage

logger = logging.getLogger("uranai.reading")


# --- Birth data ------------------------------------------------------------

@dataclass(frozen=True)
class BirthData:
    """What we ask for. The time is optional by product decision (P6)."""

    birth_date: date
    birth_time: Optional[time] = None

    @property
    def hour_known(self) -> bool:
        return self.birth_time is not None

    def as_datetime(self) -> datetime:
        return datetime.combine(self.birth_date, self.birth_time or time(0, 0))

    def summary(self) -> str:
        """For display back to the user. Never for a log."""
        if self.birth_time is None:
            return self.birth_date.strftime("%Y年%-m月%-d日")
        return (self.birth_date.strftime("%Y年%-m月%-d日")
                + self.birth_time.strftime(" %H:%M"))

    def to_record(self) -> dict:
        return {
            "birth_date": self.birth_date.isoformat(),
            "birth_time": (self.birth_time.strftime("%H:%M")
                           if self.birth_time else None),
        }

    @classmethod
    def from_record(cls, record: dict) -> Optional["BirthData"]:
        raw_date = record.get("birth_date")
        if not raw_date:
            return None
        raw_time = record.get("birth_time")
        return cls(
            birth_date=date.fromisoformat(raw_date),
            birth_time=(time.fromisoformat(raw_time) if raw_time else None),
        )


_DATE_PATTERN = re.compile(
    r"(?P<year>\d{4})\s*[-/.年]\s*(?P<month>\d{1,2})\s*[-/.月]\s*(?P<day>\d{1,2})\s*日?"
)
_TIME_PATTERN = re.compile(
    r"(?P<hour>\d{1,2})\s*[:時]\s*(?P<minute>\d{1,2})\s*分?"
)

EARLIEST_BIRTH_YEAR = 1900


def parse_birth_data(text: str, today: Optional[date] = None) -> Optional[BirthData]:
    """Read a birth date, and a birth time if one was given.

    Returns None when nothing usable is present. An absent time is a
    successful parse, not a failure: the whole point of P6 is that we do not
    turn users away at the first question.
    """
    match = _DATE_PATTERN.search(text)
    if not match:
        return None

    try:
        parsed_date = date(int(match.group("year")), int(match.group("month")),
                           int(match.group("day")))
    except ValueError:
        return None

    today = today or datetime.now(JST).date()
    if parsed_date > today or parsed_date.year < EARLIEST_BIRTH_YEAR:
        return None

    parsed_time = None
    remainder = text[match.end():]
    time_match = _TIME_PATTERN.search(remainder)
    if time_match:
        try:
            parsed_time = time(int(time_match.group("hour")),
                               int(time_match.group("minute")))
        except ValueError:
            parsed_time = None

    return BirthData(parsed_date, parsed_time)


# --- The pipeline ----------------------------------------------------------

@dataclass(frozen=True)
class ReadingOutcome:
    """What happened, for the caller to log and for tests to assert on.

    `message` is the only thing that goes to the user.
    """

    message: Outbound
    outcome: str
    cost_usd: float = 0.0
    review_id: Optional[str] = None


@dataclass
class ReadingTrace:
    """What each stage decided, for an operator looking over the pipeline.

    Opt-in: `generate` fills one only when the caller passes one in, so the
    webhook path is unaffected. Held in memory and handed back to that
    caller — never logged, never stored. It contains the chart and the
    prompt, which are derived from personal information.

    It exists because the practitioner grading output weekly needs the chart
    the model was given, not just what it said, and re-deriving that outside
    the pipeline would show what we think happened rather than what did.
    """

    input_verdict: Optional[str] = None
    input_reason: Optional[str] = None
    quota_remaining: Optional[int] = None
    chart: Optional[dict] = None
    chart_text: Optional[str] = None
    prompt_system: Optional[str] = None
    prompt_user: Optional[str] = None
    model: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    model_text: Optional[str] = None
    output_verdict: Optional[str] = None
    output_reason: Optional[str] = None
    disclosure_appended: bool = False


class ReadingService:
    def __init__(self, storage: Storage, gateway: ModelGateway, config,
                 cohort: str = "line"):
        self.storage = storage
        self.gateway = gateway
        self.config = config
        # Board traffic and seed traffic are different populations; averaging
        # them produces a conversion number that describes nobody.
        self.cohort = cohort

    def generate(self, user_id: str, text: str,
                 birth: Optional[BirthData] = None,
                 tier: str = "free",
                 kind: str = "question",
                 today: Optional[date] = None,
                 trace: Optional[ReadingTrace] = None) -> ReadingOutcome:
        # 1. Screening. Before the quota check, before the chart, before
        #    anything that could cost money or take time.
        screened = screen_input(text)

        if trace is not None:
            trace.input_verdict = screened.verdict.value
            trace.input_reason = screened.reason

        if screened.verdict is Verdict.REDIRECT_CRISIS:
            # Pattern and timestamp only. Not the message, not the user id.
            self.storage.log_crisis_event(screened.pattern or "unknown")
            logger.warning("crisis redirect (pattern=%s)", screened.pattern)
            return ReadingOutcome(canned(Msg.CRISIS), "crisis")

        if screened.verdict is Verdict.REDIRECT_PROFESSIONAL:
            domain = screened.domain or "medical"
            self.storage.log_event("professional_redirect", {"domain": domain})
            return ReadingOutcome(canned(PROFESSIONAL_MESSAGE[domain]),
                                  f"professional:{domain}")

        if not screened.allowed:  # pragma: no cover - defensive
            return ReadingOutcome(canned(Msg.READING_UNAVAILABLE), "blocked")

        # 2. We need a chart to read.
        if birth is None:
            return ReadingOutcome(canned(Msg.NEED_BIRTH_DATA_FIRST),
                                  "no_birth_data")

        # 3. Free-tier quota. Running out is where the Phase 0 question gets
        #    asked: the user is offered the paid reading rather than simply
        #    turned away. Rule 1 constrains that offer — see messages_ja.
        if tier == "free" and not self.storage.consume_free_quota(
                user_id, self.config.free_tier_limit):
            if not payments.enabled_for(self.config):
                # Offering a paid reading we are not permitted to sell would
                # be advertising a product that does not exist, and it would
                # put PAYWALL_SHOWN in the funnel for people who were never
                # really asked — making the one number Phase 0 exists to
                # produce a fiction. Say the free tier is done, and nothing
                # more, until the gates are met.
                return ReadingOutcome(canned(Msg.QUOTA_EXHAUSTED),
                                      "quota_exhausted")
            funnel.record(self.storage, funnel.Stage.PAYWALL_SHOWN, user_id,
                          self.cohort)
            return ReadingOutcome(
                canned(Msg.PAYWALL_OFFER,
                       price=self.config.deep_reading_price_jpy),
                "paywall_shown")

        if trace is not None:
            trace.quota_remaining = self.storage.free_quota_remaining(
                user_id, self.config.free_tier_limit)

        # 4. The chart. Computed here, never by the model.
        try:
            payload = build_payload(birth.as_datetime(),
                                    hour_known=birth.hour_known)
        except ManualReviewRequired as needs_human:
            return self._route_to_human(user_id, birth, needs_human)

        self.storage.log_event("chart_computed",
                               {"hour_known": birth.hour_known})

        chart_text = format_for_prompt(payload)

        if trace is not None:
            trace.chart = payload
            trace.chart_text = chart_text

        # 5. The prompt. Built from the chart, never from a birth date.
        if kind == "daily":
            user_prompt = prompts_ja.build_daily_prompt(
                chart_text,
                (today or datetime.now(JST).date()).isoformat(),
                hour_known=birth.hour_known,
            )
        else:
            user_prompt = prompts_ja.build_reading_prompt(
                chart_text, text, hour_known=birth.hour_known)

        # The token is what makes this a prompt the gateway will accept.
        prompt = ScreenedPrompt(system=prompts_ja.SYSTEM_PROMPT,
                                user=user_prompt, token=screened.token)

        model = (self.config.model_paid if tier == "paid"
                 else self.config.model_free)

        if trace is not None:
            trace.prompt_system = prompt.system
            trace.prompt_user = prompt.user
            trace.model = model

        # 6. The model call.
        try:
            completion = self.gateway.complete(prompt, user_id=user_id,
                                               model=model, tier=tier)
        except BudgetExceeded:
            # Already logged by the guard, with the figures.
            return ReadingOutcome(canned(Msg.SERVICE_PAUSED), "budget_exceeded")
        except ModelUnavailable as exc:
            logger.error("model unavailable: %s", exc)
            return ReadingOutcome(canned(Msg.READING_UNAVAILABLE),
                                  "model_unavailable")

        # 7. Outbound screening and disclosure, both, in that order.
        message = outbound_reading(completion.text)

        if trace is not None:
            trace.prompt_tokens = completion.prompt_tokens
            trace.completion_tokens = completion.completion_tokens
            trace.model_text = completion.text
            trace.output_verdict = ("block" if message.blocked_reason
                                    else "allow")
            trace.output_reason = message.blocked_reason
            trace.disclosure_appended = message.kind == "reading"

        if message.blocked_reason:
            # E5: a block is a prompt defect. It goes to the weekly
            # practitioner review, not to the user.
            self.storage.log_event("reading_blocked",
                                   {"reason": message.blocked_reason,
                                    "tier": tier, "model": completion.model})
            return ReadingOutcome(message, "output_blocked",
                                  cost_usd=completion.cost_usd)

        self.storage.log_event("reading_delivered",
                               {"tier": tier, "kind": kind,
                                "hour_known": birth.hour_known})
        funnel.record(self.storage,
                      funnel.Stage.PAID if tier == "paid"
                      else funnel.Stage.FREE_READING,
                      user_id, self.cohort)
        return ReadingOutcome(message, "delivered",
                              cost_usd=completion.cost_usd)

    def _route_to_human(self, user_id: str, birth: BirthData,
                        needs_human: ManualReviewRequired) -> ReadingOutcome:
        """A boundary chart. A person looks at it; the user is told so.

        The queue entry carries the birth data because the reviewer needs it.
        The log line carries the review id and nothing else, because
        application logs are not where personal information belongs.
        """
        review_id = self.storage.enqueue_manual_review(
            user_id, "solar_term_boundary",
            {"warnings": list(needs_human.warnings), **birth.to_record()},
        )
        logger.warning(
            "chart needs manual review: review_id=%s (details in the queue, "
            "not in this log)", review_id)
        self.storage.log_event("manual_review_queued",
                               {"review_id": review_id,
                                "hour_known": birth.hour_known})
        return ReadingOutcome(canned(Msg.MANUAL_REVIEW, review_id=review_id),
                              "manual_review", review_id=review_id)
