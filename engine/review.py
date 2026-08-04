"""
The practitioner review harness — gate 1.

CLAUDE.md, "Before anything reaches a real user", opens with:

    1. Engine reviewed by the practitioner against their own charts

This module is what makes that an afternoon rather than a fortnight. The
practitioner writes out charts they have computed by hand; this compares
them with ours and, crucially, **diagnoses each disagreement** instead of
just listing it.

WHY DIAGNOSIS RATHER THAN A DIFF
--------------------------------
A bare list of mismatches would hand the practitioner an engineering problem
and hand us a divination problem, and neither of us can act on the other's.
Most disagreements will not be bugs. They will be one of:

  * **P1** — 早子時/晩子時. We roll the day pillar forward at 23:00 by
    placeholder default. A school that holds until midnight disagrees on
    every birth in that hour, and the chart is evidence for the ruling
    rather than a defect.
  * **P2** — 地方時修正. We use clock time as reported. A school that
    corrects to local mean time disagrees near an hour boundary, by an
    amount that implies a specific birth longitude — which this reports, so
    the practitioner can confirm it matches where the person was born.
  * **precision** — the birth sits close enough to a solar term that our
    computed boundary and theirs can differ within the ±15 minutes
    engine/solar.py documents. Not a rule difference and not a bug.
  * **unexplained** — everything else. This is the short list an engineer
    should actually look at, and the reason the other three categories exist
    is to keep it short.

Pure standard library, like the rest of the engine, so the practitioner can
be walked through it on any machine.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .bazi import ChartOptions, compute_chart

PILLARS = ("year", "month", "day", "hour")

# Japan spans roughly 127°E (Yonaguni) to 146°E (eastern Hokkaido). Scanned
# when testing whether local mean time reconciles a disagreement.
LONGITUDE_MIN, LONGITUDE_MAX = 127.0, 146.0

# Named so a reconciling longitude range can be reported as places rather
# than as degrees, which is what the practitioner can actually check against
# a birthplace.
REFERENCE_LONGITUDES = {
    "那覇": 127.7, "福岡": 130.4, "広島": 132.5, "大阪": 135.5,
    "名古屋": 136.9, "東京": 139.7, "仙台": 140.9, "札幌": 141.3,
    "釧路": 144.4,
}

# How close to a solar term a birth must be for our documented precision to
# be a plausible explanation. engine/solar.py measures a worst case of 9.2
# minutes on sectional terms against published NAOJ values; 20 gives room
# for the practitioner's own source differing from NAOJ too.
PRECISION_WINDOW_MINUTES = 20


@dataclass(frozen=True)
class Expectation:
    """One chart, as the practitioner computed it."""

    label: str
    birth_local: datetime
    hour_known: bool = True
    expected: Dict[str, str] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class Finding:
    expectation: Expectation
    computed: Dict[str, Optional[str]]
    mismatched: List[str]
    diagnosis: str
    detail: str = ""

    @property
    def agrees(self) -> bool:
        return not self.mismatched


def _pillars(chart) -> Dict[str, Optional[str]]:
    return {
        "year": chart.year.name,
        "month": chart.month.name,
        "day": chart.day.name,
        "hour": chart.hour.name if chart.hour else None,
    }


def _compare(expected: Dict[str, str],
             computed: Dict[str, Optional[str]]) -> List[str]:
    return [name for name in PILLARS
            if expected.get(name) and expected[name] != computed.get(name)]


def _diagnose(expectation: Expectation, base_options: ChartOptions):
    """Try to reconcile a disagreement with an open ruling.

    Returns (diagnosis, detail). Order matters, and it is ordered by how
    much each explanation has to assume:

      P1        applies only to births in the 23:00 hour and only moves the
                day and hour pillars. Very specific, so almost no risk of
                absorbing an unrelated disagreement.
      precision applies only near a solar term, and assumes nothing at all
                about the person.
      P2        can shift a birth by up to ±32 minutes, so it reconciles
                almost any near-boundary disagreement — but only by
                assuming a birthplace we were not told. It is the most
                promiscuous explanation and therefore goes last.
      unexplained is what survives.

    Tried in the other order, a birth three minutes from 立春 is reported as
    evidence for the P2 ruling, when the parsimonious reading is that our
    computed boundary and the practitioner's differ by three minutes.
    """
    # P1 — the other 早子時/晩子時 convention.
    flipped = ChartOptions(
        day_changes_at_2300=not base_options.day_changes_at_2300,
        apply_local_mean_time=base_options.apply_local_mean_time,
        birth_longitude_deg=base_options.birth_longitude_deg,
        boundary_warning_minutes=base_options.boundary_warning_minutes,
    )
    chart = compute_chart(expectation.birth_local, flipped,
                          hour_known=expectation.hour_known)
    if not _compare(expectation.expected, _pillars(chart)):
        held = "holds until midnight" if base_options.day_changes_at_2300 \
               else "rolls forward at 23:00"
        return "P1", (
            f"reconciled if the day pillar {held} (早子時/晩子時). "
            "This chart is evidence for the P1 ruling, not a defect."
        )

    # precision — near enough to a term that either side could be right.
    chart = compute_chart(expectation.birth_local, base_options,
                          hour_known=expectation.hour_known)
    if chart.boundary_warnings:
        return "precision", (
            "the birth sits near a solar-term boundary, where our computed "
            "time and the practitioner's source can differ within the ±15 "
            "minutes engine/solar.py documents. "
            + chart.boundary_warnings[0]
        )
    nearest = _minutes_to_nearest_term(expectation.birth_local, base_options)
    if nearest is not None and nearest <= PRECISION_WINDOW_MINUTES:
        return "precision", (
            f"the birth is about {nearest:.0f} min from a solar term, within "
            "our documented precision."
        )

    # P2 — local mean time, and which birth longitudes it would imply.
    #
    # A range is reported, not a single value. The hour branch is a two-hour
    # bucket, so many longitudes reconcile the same chart, and naming only
    # the first would invite the practitioner to reject a correct hypothesis
    # — "127°E is Yonaguni, but she was born in Fukuoka" — when Fukuoka was
    # in the reconciling range all along.
    if expectation.hour_known:
        reconciling = []
        longitude = LONGITUDE_MIN
        while longitude <= LONGITUDE_MAX:
            corrected = ChartOptions(
                day_changes_at_2300=base_options.day_changes_at_2300,
                apply_local_mean_time=True,
                birth_longitude_deg=longitude,
                boundary_warning_minutes=base_options.boundary_warning_minutes,
            )
            chart = compute_chart(expectation.birth_local, corrected,
                                  hour_known=True)
            if not _compare(expectation.expected, _pillars(chart)):
                reconciling.append(longitude)
            longitude += 0.5

        if reconciling:
            low, high = min(reconciling), max(reconciling)
            places = ", ".join(
                name for name, degrees in REFERENCE_LONGITUDES.items()
                if low <= degrees <= high
            )
            return "P2", (
                f"reconciled by 地方時修正 for a birth longitude between "
                f"{low:.1f}°E and {high:.1f}°E "
                f"({(low - 135.0) * 4:+.0f} to {(high - 135.0) * 4:+.0f} min)"
                + (f" — e.g. {places}" if places else "")
                + ". If the birthplace falls in that range, this chart is "
                "evidence for the P2 ruling rather than a defect."
            )

    return "unexplained", (
        "not reconciled by any open ruling or by our documented precision. "
        "This is either a bug in engine/ or a convention we have not "
        "modelled — an engineer should look at it."
    )


def _minutes_to_nearest_term(birth_local: datetime,
                             options: ChartOptions) -> Optional[float]:
    """Distance to the nearest sectional term, in minutes.

    Read out of the engine's own boundary machinery by widening the warning
    window until something is reported, so this cannot drift away from what
    compute_chart actually considers a boundary.
    """
    for window in (30, 60, 120, 240):
        widened = ChartOptions(
            day_changes_at_2300=options.day_changes_at_2300,
            apply_local_mean_time=options.apply_local_mean_time,
            birth_longitude_deg=options.birth_longitude_deg,
            boundary_warning_minutes=window,
        )
        chart = compute_chart(birth_local, widened)
        if chart.boundary_warnings:
            found = [int(n) for n in
                     "".join(c if c.isdigit() else " "
                             for c in chart.boundary_warnings[0]).split()]
            return float(found[0]) if found else float(window)
    return None


def review(expectations: List[Expectation],
           options: Optional[ChartOptions] = None) -> List[Finding]:
    options = options or ChartOptions()
    findings = []
    for expectation in expectations:
        chart = compute_chart(expectation.birth_local, options,
                              hour_known=expectation.hour_known)
        computed = _pillars(chart)
        mismatched = _compare(expectation.expected, computed)

        if mismatched:
            diagnosis, detail = _diagnose(expectation, options)
        else:
            diagnosis, detail = "agrees", ""

        findings.append(Finding(expectation, computed, mismatched,
                                diagnosis, detail))
    return findings


def summarise(findings: List[Finding]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for finding in findings:
        counts[finding.diagnosis] = counts.get(finding.diagnosis, 0) + 1
    return counts


# --- Reading what the practitioner writes ----------------------------------

def load_csv(path: Path) -> List[Expectation]:
    """Read the practitioner's charts.

    Deliberately forgiving: blank expected pillars are skipped rather than
    treated as a disagreement, so a practitioner can fill in only the
    pillars they are sure of. Rows beginning with # are comments.
    """
    expectations = []
    with open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(
                line for line in handle if not line.lstrip().startswith("#")):
            raw_date = (row.get("birth_date") or "").strip()
            if not raw_date:
                continue
            raw_time = (row.get("birth_time") or "").strip()
            hour_known = bool(raw_time)
            stamp = datetime.fromisoformat(
                f"{raw_date}T{raw_time or '00:00'}")
            expectations.append(Expectation(
                label=(row.get("label") or raw_date).strip(),
                birth_local=stamp,
                hour_known=hour_known,
                expected={name: (row.get(name) or "").strip()
                          for name in PILLARS
                          if (row.get(name) or "").strip()},
                note=(row.get("note") or "").strip(),
            ))
    return expectations
