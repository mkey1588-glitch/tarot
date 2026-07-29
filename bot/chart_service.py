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
                  allow_uncertain: bool = False) -> dict[str, Any]:
    """Compute a chart and shape it for the prompt."""
    chart = compute_chart(birth_local, options)

    if chart.needs_manual_review and not allow_uncertain:
        raise ManualReviewRequired(chart.boundary_warnings)

    return chart.to_dict()


def format_for_prompt(payload: dict[str, Any]) -> str:
    """Render the chart as compact text for the prompt.

    Kept human-readable on purpose: the practitioner grading output weekly
    needs to check the chart the model was given, not just what it said.
    """
    p = payload["pillars"]
    lines = [
        f"【命式】{payload['birth_local']}",
        f"  年柱 {p['year']['pillar']}（{p['year']['stem_element']}/{p['year']['branch_element']}）",
        f"  月柱 {p['month']['pillar']}（{p['month']['stem_element']}/{p['month']['branch_element']}） 節気: {payload['month_term']}",
        f"  日柱 {p['day']['pillar']}（{p['day']['stem_element']}/{p['day']['branch_element']}）",
    ]
    if p["hour"]:
        lines.append(
            f"  時柱 {p['hour']['pillar']}（{p['hour']['stem_element']}/{p['hour']['branch_element']}）"
        )
    dm = payload["day_master"]
    lines.append(f"【日主】{dm['stem']}（{dm['element']}・{dm['polarity']}）")
    counts = "　".join(f"{k}{v}" for k, v in payload["element_counts"].items())
    lines.append(f"【五行】{counts}")
    return "\n".join(lines)
