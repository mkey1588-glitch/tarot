"""
Bridge between the deterministic engine and the interpretation layer.

The model receives `build_payload(...)` output and nothing else. It cannot
see the birth datetime arithmetic, it cannot recompute a pillar, and it has
no path to invent one -- which is exactly the intent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from engine import ChartOptions, compute_chart


class ManualReviewRequired(Exception):
    """Raised when a chart sits too close to a solar-term boundary.

    Callers must route these to the retained practitioner rather than
    answering automatically. See engine/solar.py on why the boundary is
    uncertain at this precision.
    """

    def __init__(self, warnings: list[str]):
        self.warnings = warnings
        super().__init__("; ".join(warnings))


def build_payload(birth_local: datetime,
                  options: ChartOptions | None = None,
                  allow_uncertain: bool = False,
                  hour_known: bool = True) -> dict[str, Any]:
    """Compute a chart and shape it for the prompt.

    `hour_known=False` yields a three-pillar chart. The payload says so in
    `hour_known`, and the prompt must instruct the model to stay quiet on
    what the missing pillar carries. See P6 in docs/DECISIONS.md.
    """
    chart = compute_chart(birth_local, options, hour_known=hour_known)

    if chart.needs_manual_review and not allow_uncertain:
        raise ManualReviewRequired(chart.boundary_warnings)

    return chart.to_dict()


def format_for_prompt(payload: dict[str, Any]) -> str:
    """Render the chart as compact text for the prompt.

    Kept human-readable on purpose: the practitioner grading output weekly
    needs to check the chart the model was given, not just what it said.

    The birth datetime is deliberately NOT included, though `build_payload`
    returns it. Two reasons, and they point the same way:

      * A model handed a birth date has the raw material to recompute a
        pillar, and "check my work" is exactly the behaviour E1 exists to
        prevent. The pillars fully determine the reading; the date adds
        nothing to interpret and one thing to get wrong.
      * It is personal information under 個人情報保護法 going to a third
        party for no benefit. The chart is not identifying in the same way.

    Auditability is not lost: the prompt still carries every computed value,
    and the birth data it came from is in our own store, which is where a
    reviewer already has to look to check that we recorded it correctly.
    """
    p = payload["pillars"]
    lines = [
        "【命式】",
        f"  年柱 {p['year']['pillar']}（{p['year']['stem_element']}/{p['year']['branch_element']}）",
        f"  月柱 {p['month']['pillar']}（{p['month']['stem_element']}/{p['month']['branch_element']}） 節気: {payload['month_term']}",
        f"  日柱 {p['day']['pillar']}（{p['day']['stem_element']}/{p['day']['branch_element']}）",
    ]
    if p["hour"]:
        lines.append(
            f"  時柱 {p['hour']['pillar']}（{p['hour']['stem_element']}/{p['hour']['branch_element']}）"
        )
    else:
        # Stated rather than omitted: the practitioner reviewing this needs
        # to see that the hour was absent, not wonder whether it was lost
        # somewhere between the engine and the prompt.
        lines.append("  時柱 不明（出生時刻の申告なし・三柱で鑑定）")
    dm = payload["day_master"]
    lines.append(f"【日主】{dm['stem']}（{dm['element']}・{dm['polarity']}）")
    counts = "　".join(f"{k}{v}" for k, v in payload["element_counts"].items())
    lines.append(f"【五行】{counts}")
    return "\n".join(lines)
