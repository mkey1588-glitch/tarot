"""
The Phase 0 funnel.

Phase 0 exists to answer one question — will a Japanese user pay ¥200-500
for an AI-generated reading — and that answer is a ratio, not a count. This
module is the instrument that produces it.

WHY THIS IS A MODULE AND NOT A COUPLE OF log_event CALLS
--------------------------------------------------------
Before this, no event carried a user id. The event log could say that
fourteen readings were delivered; it could not say whether that was fourteen
people once or one person fourteen times, and a conversion rate needs
people. So funnel events deliberately carry an identifier, and they are the
only ones that do.

`storage.log_crisis_event` is the counter-example and the reason this is
worth stating: it takes a pattern and a timestamp, and there is no parameter
through which a user id could be passed. Mental-health information is likely
要配慮個人情報. Knowing *that* someone reached the paywall is an operational
metric. Knowing *who* typed 死にたい is not one, and we do not keep it.

COHORTS
-------
The board clicking through a demo and a seed user meeting the paywall are
different populations, and averaging them produces a number that describes
nobody. Every event carries the cohort it came from, so they can be reported
apart. This is why `DEMO_ACCESS_CODES` is written `board:code,seed:code`.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

EVENT_TYPE = "funnel"


class Stage(Enum):
    """Ordered. A user who reaches a stage is counted at every earlier one,
    whether or not the earlier event was recorded — otherwise a dropped
    event would show up as negative conversion, which is not a thing."""

    FOLLOWED = "followed"
    REGISTERED = "registered"
    FREE_READING = "free_reading"
    PAYWALL_SHOWN = "paywall_shown"
    CHECKOUT_STARTED = "checkout_started"
    PAID = "paid"


ORDER: List[Stage] = list(Stage)

# The ratios worth reporting, as (label, numerator, denominator).
RATES = [
    ("registered_of_followed", Stage.REGISTERED, Stage.FOLLOWED),
    ("read_of_registered", Stage.FREE_READING, Stage.REGISTERED),
    ("offered_of_read", Stage.PAYWALL_SHOWN, Stage.FREE_READING),
    ("started_of_offered", Stage.CHECKOUT_STARTED, Stage.PAYWALL_SHOWN),
    # The Phase 0 number: of the people we actually asked, how many paid.
    ("paid_of_offered", Stage.PAID, Stage.PAYWALL_SHOWN),
    ("paid_of_followed", Stage.PAID, Stage.FOLLOWED),
]

HEADLINE = "paid_of_offered"


def record(storage, stage: Stage, user_id: str, cohort: str = "line",
           **extra) -> None:
    """Mark that a user reached a stage. Idempotence is not required: the
    report counts distinct users, so recording twice changes nothing."""
    if not isinstance(stage, Stage):
        raise TypeError(f"stage must be a Stage, got {type(stage).__name__}")
    storage.log_funnel_event(stage.value, user_id, cohort, **extra)


def _furthest_stage_per_user(storage, cohort: Optional[str] = None):
    reached: Dict[str, int] = {}
    cohorts: Dict[str, str] = {}
    for event in storage.iter_events():
        if event.get("type") != EVENT_TYPE:
            continue
        user_id = event.get("user_id")
        try:
            index = ORDER.index(Stage(event.get("stage")))
        except (ValueError, KeyError):
            continue
        if not user_id:
            continue
        if cohort is not None and event.get("cohort") != cohort:
            continue
        cohorts[user_id] = event.get("cohort", "unknown")
        reached[user_id] = max(reached.get(user_id, -1), index)
    return reached, cohorts


def counts(storage, cohort: Optional[str] = None) -> Dict[str, int]:
    """Distinct users who reached each stage or went past it."""
    reached, _ = _furthest_stage_per_user(storage, cohort)
    return {
        stage.value: sum(1 for furthest in reached.values() if furthest >= index)
        for index, stage in enumerate(ORDER)
    }


def rates(stage_counts: Dict[str, int]) -> Dict[str, Optional[float]]:
    """None rather than 0.0 when the denominator is empty.

    A conversion rate of "0%" and "nobody has been asked yet" are different
    claims, and the second one is where Phase 0 currently is. Reporting the
    first would be a made-up answer to the question the phase exists for.
    """
    out = {}
    for label, numerator, denominator in RATES:
        bottom = stage_counts.get(denominator.value, 0)
        out[label] = (round(stage_counts.get(numerator.value, 0) / bottom, 4)
                      if bottom else None)
    return out


def report(storage) -> dict:
    _, cohorts = _furthest_stage_per_user(storage)
    seen = sorted(set(cohorts.values()))

    per_cohort = {}
    for name in seen:
        stage_counts = counts(storage, cohort=name)
        per_cohort[name] = {"counts": stage_counts, "rates": rates(stage_counts)}

    overall = counts(storage)
    return {
        "question": "Will a Japanese user pay ¥200-500 for an AI reading?",
        "headline": HEADLINE,
        "headline_note": (
            "Of the users actually shown the paywall, the share who paid. "
            "Read it per cohort: board traffic is not seed traffic, and the "
            "average of the two describes nobody."
        ),
        "overall": {"counts": overall, "rates": rates(overall)},
        "by_cohort": per_cohort,
    }
