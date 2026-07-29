"""
Four Pillars (四柱推命) chart computation.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
The language model never calculates. Everything here is deterministic and
unit-tested; the model receives the finished chart as structured data and
does nothing but interpret it in the practitioner's voice.

A hallucinated birth chart is the one failure a knowledgeable Japanese user
spots instantly and does not forgive. A chart bug, by contrast, is a failing
test. That asymmetry is the whole reason for this separation.

SCHOOL-DEPENDENT CHOICES
------------------------
Four Pillars is not a single algorithm; several conventions are genuinely
contested between schools. Where that is the case this module exposes an
explicit option rather than silently picking one, and `ChartOptions`
documents each. THESE ARE NOT ENGINEERING DECISIONS. They belong to the
retained practitioner, and the defaults below are placeholders until that
person rules on them. See docs/DECISIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from .constants import (
    STEMS, BRANCHES, STEMS_KANA, BRANCHES_KANA,
    STEM_ELEMENT, STEM_IS_YANG, BRANCH_ELEMENT, BRANCH_IS_YANG,
    BRANCH_HIDDEN_STEMS, SECTIONAL_TERMS, MONTH_BRANCH_SEQUENCE,
    JST_MERIDIAN_DEG, JST_UTC_OFFSET_HOURS,
)
from .solar import julian_day_number, solar_term_instant, lichun

JST = timezone(timedelta(hours=JST_UTC_OFFSET_HOURS))

# The day pillar runs on an unbroken 60-day cycle. Anchor: 1 January 2000
# was 戊午 (index 54). Verified as (JDN - 11) mod 60 with JDN = 2451545.
_DAY_PILLAR_EPOCH_OFFSET = 11

# 1984 was 甲子年, the start of a sexagenary cycle.
_YEAR_PILLAR_EPOCH = 1984


@dataclass(frozen=True)
class ChartOptions:
    """Conventions the practitioner must rule on. Defaults are placeholders."""

    # 早子時 / 晩子時. Most modern Japanese practice rolls the day pillar
    # forward at 23:00, treating 23:00-23:59 as the next day's 子 hour.
    # Some schools keep the day pillar until midnight. PRACTITIONER DECISION.
    day_changes_at_2300: bool = True

    # 地方時修正. Japan Standard Time is fixed to 135 deg E, so a birth in
    # Fukuoka (130.4 deg E) is about 18 minutes ahead of local solar time.
    # Some Japanese schools correct for this, others use clock time as given.
    # Off by default because it needs a birth longitude we do not collect
    # in Phase 0. PRACTITIONER DECISION.
    apply_local_mean_time: bool = False
    birth_longitude_deg: Optional[float] = None

    # How close to a solar-term boundary a birth must be before the chart is
    # flagged for manual review. The solar series is accurate to about 15
    # minutes; 30 minutes gives a safety factor of two.
    boundary_warning_minutes: int = 30


@dataclass(frozen=True)
class Pillar:
    stem: str
    branch: str

    @property
    def name(self) -> str:
        return f"{self.stem}{self.branch}"

    @property
    def stem_element(self) -> str:
        return STEM_ELEMENT[STEMS.index(self.stem)]

    @property
    def branch_element(self) -> str:
        return BRANCH_ELEMENT[BRANCHES.index(self.branch)]

    @property
    def stem_is_yang(self) -> bool:
        return STEM_IS_YANG[STEMS.index(self.stem)]

    @property
    def hidden_stems(self) -> list[str]:
        return list(BRANCH_HIDDEN_STEMS[self.branch])

    @property
    def reading(self) -> str:
        s = STEMS_KANA[STEMS.index(self.stem)]
        b = BRANCHES_KANA[BRANCHES.index(self.branch)]
        return f"{s}{b}"

    def to_dict(self) -> dict:
        return {
            "pillar": self.name,
            "stem": self.stem,
            "branch": self.branch,
            "reading": self.reading,
            "stem_element": self.stem_element,
            "branch_element": self.branch_element,
            "polarity": "陽" if self.stem_is_yang else "陰",
            "hidden_stems": self.hidden_stems,
        }


@dataclass
class Chart:
    """A computed Four Pillars chart, ready to hand to the interpretation layer."""

    birth_local: datetime
    year: Pillar
    month: Pillar
    day: Pillar
    hour: Optional[Pillar]

    solar_year: int
    month_term: str
    element_counts: dict
    day_master: str
    day_master_element: str
    boundary_warnings: list = field(default_factory=list)
    options_used: dict = field(default_factory=dict)

    @property
    def needs_manual_review(self) -> bool:
        """True when the birth time sits too close to a boundary to trust."""
        return bool(self.boundary_warnings)

    @property
    def hour_known(self) -> bool:
        """False for a three-pillar chart. See P6 in docs/DECISIONS.md.

        The interpretation layer must go quiet on anything the hour pillar
        carries when this is False, rather than reading the remaining three
        pillars as though they were the whole chart.
        """
        return self.hour is not None

    def to_dict(self) -> dict:
        """The exact structure handed to the language model. Nothing else."""
        return {
            # Date only when the hour is unknown. A payload that carried
            # "1990-05-15T03:45" beside "hour_known": false would be handing
            # the model a birth time we do not have.
            "birth_local": (self.birth_local.isoformat() if self.hour_known
                            else self.birth_local.date().isoformat()),
            "solar_year": self.solar_year,
            "month_term": self.month_term,
            "pillars": {
                "year": self.year.to_dict(),
                "month": self.month.to_dict(),
                "day": self.day.to_dict(),
                "hour": self.hour.to_dict() if self.hour else None,
            },
            "day_master": {
                "stem": self.day_master,
                "element": self.day_master_element,
                "polarity": "陽" if STEM_IS_YANG[STEMS.index(self.day_master)] else "陰",
            },
            "hour_known": self.hour_known,
            "element_counts": self.element_counts,
            "needs_manual_review": self.needs_manual_review,
            "boundary_warnings": self.boundary_warnings,
            "options_used": self.options_used,
        }


def _sexagenary(index: int) -> Pillar:
    index %= 60
    return Pillar(STEMS[index % 10], BRANCHES[index % 12])


def _month_boundaries(gregorian_year: int) -> list[tuple[str, datetime, str]]:
    """The twelve sectional-term boundaries bracketing the given solar year.

    Returned as (term name, UTC instant, month branch), ascending, starting
    from 立春 of `gregorian_year` and ending with 小寒 of the year after.
    """
    out = []
    for name, deg, branch in SECTIONAL_TERMS:
        # 立春 through 大雪 fall in `gregorian_year`; 小寒 falls in the next.
        year = gregorian_year + 1 if name == "小寒" else gregorian_year
        # Seed the search near the term's usual calendar position.
        seed_month = {315: 2, 345: 3, 15: 4, 45: 5, 75: 6, 105: 7,
                      135: 8, 165: 9, 195: 10, 225: 11, 255: 12, 285: 1}[deg]
        seed = datetime(year, seed_month, 6, tzinfo=timezone.utc)
        out.append((name, solar_term_instant(float(deg), seed), branch))
    out.sort(key=lambda x: x[1])
    return out


def compute_chart(birth_local: datetime,
                  options: ChartOptions | None = None,
                  hour_known: bool = True) -> Chart:
    """Compute a Four Pillars chart from a local birth datetime.

    `birth_local` is naive-local or JST-aware. Times are interpreted as
    Japan Standard Time; this engine is Japan-only in Phase 0.

    `hour_known=False` produces a three-pillar chart: no 時柱, elements
    tallied over six positions instead of eight, and the time component of
    `birth_local` ignored. Many Japanese adults do not know their birth time
    and refusing them costs us the only number Phase 0 exists to produce.
    Two consequences follow and both are handled here rather than left to
    the caller:

      * 地方時修正 is skipped. Correcting an unknown clock time by 18
        minutes is meaningless, and from the 00:00 we substitute it can
        cross midnight and silently move the DAY pillar.
      * The boundary check widens from minutes to the whole civil day. The
        birth is somewhere in a 24-hour window, so any solar term falling
        inside that window leaves the month — or at 立春 the year — pillar
        genuinely ambiguous, not merely imprecise.

    What may honestly be said from three pillars is a question for the
    practitioner, recorded as P6 in docs/DECISIONS.md. The engine's job is
    only to avoid inventing the fourth.
    """
    options = options or ChartOptions()

    if birth_local.tzinfo is None:
        birth_local = birth_local.replace(tzinfo=JST)
    birth_local = birth_local.astimezone(JST)

    effective = birth_local
    if not hour_known:
        effective = birth_local.replace(hour=0, minute=0, second=0, microsecond=0)
    elif options.apply_local_mean_time:
        if options.birth_longitude_deg is None:
            raise ValueError(
                "apply_local_mean_time requires birth_longitude_deg"
            )
        delta_minutes = (options.birth_longitude_deg - JST_MERIDIAN_DEG) * 4.0
        effective = birth_local + timedelta(minutes=delta_minutes)

    birth_utc = effective.astimezone(timezone.utc)
    warnings: list[str] = []

    # The window the birth is known to lie in: one instant when the clock
    # time was reported, the whole civil day when it was not. `effective` is
    # already JST midnight in the latter case, so it opens the window.
    day_end_utc = birth_utc + timedelta(days=1)

    def _uncertain(instant: datetime) -> bool:
        if not hour_known:
            return birth_utc < instant < day_end_utc
        gap_minutes = abs((birth_utc - instant).total_seconds()) / 60.0
        return gap_minutes <= options.boundary_warning_minutes

    def _warn(name: str, instant: datetime, pillar: str) -> None:
        when = f"{instant.astimezone(JST):%Y-%m-%d %H:%M} JST"
        if hour_known:
            gap_minutes = abs((birth_utc - instant).total_seconds()) / 60.0
            detail = f"Born within {gap_minutes:.0f} min of {name} {when}"
        else:
            detail = (f"Birth time unknown and {name} {when} falls on the "
                      "birth date")
        warnings.append(
            f"{detail}; the {pillar} pillar is uncertain at this precision. "
            "Route to a practitioner."
        )

    # --- Year pillar: changes at 立春, not 1 January and not Lunar New Year ---
    candidate_year = effective.year
    lichun_this = lichun(candidate_year)
    solar_year = candidate_year if birth_utc >= lichun_this else candidate_year - 1

    if _uncertain(lichun_this):
        _warn("立春", lichun_this, "YEAR")

    year_pillar = _sexagenary((solar_year - _YEAR_PILLAR_EPOCH) % 60)

    # --- Month pillar: bounded by sectional terms ---
    boundaries = _month_boundaries(solar_year)
    month_term, month_branch = boundaries[0][0], boundaries[0][2]
    for name, instant, branch in boundaries:
        if birth_utc >= instant:
            month_term, month_branch = name, branch
        if name != "立春" and _uncertain(instant):
            _warn(name, instant, "MONTH")

    # 五虎遁: the year stem fixes which stem opens the 寅 month.
    seq = MONTH_BRANCH_SEQUENCE[month_branch]
    month_stem = STEMS[(STEMS.index(year_pillar.stem) * 2 + seq + 1) % 10]
    month_pillar = Pillar(month_stem, month_branch)

    # --- Day pillar: unbroken 60-day cycle ---
    day_date = effective.date()
    if hour_known and options.day_changes_at_2300 and effective.hour == 23:
        day_date = (effective + timedelta(hours=1)).date()

    day_index = (julian_day_number(day_date) - _DAY_PILLAR_EPOCH_OFFSET) % 60
    day_pillar = _sexagenary(day_index)

    # --- Hour pillar: 五鼠遁 from the day stem ---
    hour_pillar: Optional[Pillar] = None
    if hour_known:
        hour_branch_index = ((effective.hour + 1) // 2) % 12
        hour_stem_index = (STEMS.index(day_pillar.stem) * 2 + hour_branch_index) % 10
        hour_pillar = Pillar(STEMS[hour_stem_index], BRANCHES[hour_branch_index])

    # --- Element tally across stems and branches ---
    counts = {"木": 0, "火": 0, "土": 0, "金": 0, "水": 0}
    for p in (year_pillar, month_pillar, day_pillar, hour_pillar):
        if p is None:
            continue
        counts[p.stem_element] += 1
        counts[p.branch_element] += 1

    return Chart(
        # `effective` rather than `birth_local` when the hour is unknown:
        # it is midnight, and nothing downstream should see a clock time
        # that was never reported.
        birth_local=birth_local if hour_known else effective,
        year=year_pillar,
        month=month_pillar,
        day=day_pillar,
        hour=hour_pillar,
        solar_year=solar_year,
        month_term=month_term,
        element_counts=counts,
        day_master=day_pillar.stem,
        day_master_element=day_pillar.stem_element,
        boundary_warnings=warnings,
        options_used=asdict(options),
    )
