"""Tests for the practitioner review harness.

The diagnosis is the part that has to be right. A disagreement labelled P1
tells the practitioner "this is your ruling to make"; labelled unexplained
it tells an engineer "you have a bug". Getting that backwards wastes the
scarcest time in the project, so each category is tested against a chart
constructed to fall in it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from engine import ChartOptions, compute_chart
from engine.review import (
    Expectation, Finding, load_csv, review, summarise,
)
from engine.solar import lichun

JST = timezone(timedelta(hours=9))


def expectation_from(birth, options=None, hour_known=True, label="case",
                     pillars=("year", "month", "day", "hour")):
    """Build an expectation from what the engine produces under `options`.

    Used to construct a practitioner who follows a *different convention*
    from our default — not to assert that the engine agrees with itself.
    """
    chart = compute_chart(birth, options, hour_known=hour_known)
    computed = {"year": chart.year.name, "month": chart.month.name,
                "day": chart.day.name,
                "hour": chart.hour.name if chart.hour else None}
    return Expectation(label, birth, hour_known,
                       {k: v for k, v in computed.items()
                        if k in pillars and v})


# --- Agreement -------------------------------------------------------------

def test_a_matching_chart_agrees():
    birth = datetime(1990, 5, 15, 7, 30)
    finding = review([expectation_from(birth)])[0]
    assert finding.agrees
    assert finding.diagnosis == "agrees"
    assert finding.mismatched == []


def test_blank_pillars_are_not_treated_as_disagreement():
    """A practitioner may be sure of the day pillar and not the hour."""
    birth = datetime(1990, 5, 15, 7, 30)
    finding = review([expectation_from(birth, pillars=("day",))])[0]
    assert finding.agrees


def test_a_three_pillar_expectation_is_compared_as_three():
    birth = datetime(1990, 5, 15)
    finding = review([expectation_from(birth, hour_known=False)])[0]
    assert finding.agrees
    assert finding.computed["hour"] is None


# --- P1: 早子時 / 晩子時 ----------------------------------------------------

def test_a_2330_birth_read_the_other_way_is_diagnosed_as_P1():
    """The practitioner holds the day pillar until midnight; we roll it
    forward at 23:00. Every birth in that hour disagrees, and none of them
    is a bug."""
    birth = datetime(1985, 3, 10, 23, 30)
    theirs = expectation_from(birth, ChartOptions(day_changes_at_2300=False))

    finding = review([theirs])[0]
    assert not finding.agrees
    assert finding.diagnosis == "P1"
    assert "早子時" in finding.detail
    assert "day" in finding.mismatched


def test_the_P1_diagnosis_names_the_convention_that_would_reconcile():
    birth = datetime(1985, 3, 10, 23, 30)
    theirs = expectation_from(birth, ChartOptions(day_changes_at_2300=False))
    detail = review([theirs])[0].detail
    assert "holds until midnight" in detail
    assert "evidence for the P1 ruling" in detail


def test_P1_is_not_invoked_for_a_birth_outside_that_hour():
    """The diagnosis must not become a catch-all that hides real bugs."""
    wrong = Expectation("wrong", datetime(1975, 6, 6, 10, 0), True,
                        {"year": "甲子", "day": "乙丑"})
    assert review([wrong])[0].diagnosis != "P1"


# --- P2: 地方時修正 ---------------------------------------------------------

def test_a_local_mean_time_chart_is_diagnosed_as_P2():
    birth = datetime(1988, 9, 3, 13, 5)
    theirs = expectation_from(
        birth, ChartOptions(apply_local_mean_time=True,
                            birth_longitude_deg=130.4))
    finding = review([theirs])[0]
    assert finding.diagnosis == "P2"
    assert "地方時修正" in finding.detail


def test_the_P2_diagnosis_reports_a_longitude_range_not_a_point():
    """The hour branch is a two-hour bucket, so many longitudes reconcile
    the same chart. Naming only the first would invite the practitioner to
    reject a correct hypothesis — '127°E is Yonaguni, but she was born in
    Fukuoka' — when Fukuoka was in the range all along."""
    birth = datetime(1988, 9, 3, 13, 5)
    theirs = expectation_from(
        birth, ChartOptions(apply_local_mean_time=True,
                            birth_longitude_deg=130.4))
    detail = review([theirs])[0].detail
    assert "between" in detail and "°E" in detail
    assert "福岡" in detail, "the true birthplace must appear in the range"


def test_P2_is_not_offered_when_the_hour_is_unknown():
    """Correcting an unknown clock time is meaningless, so it cannot be the
    explanation for anything."""
    birth = datetime(1990, 5, 15)
    theirs = Expectation("no hour", birth, False, {"day": "甲子"})
    assert review([theirs])[0].diagnosis != "P2"


# --- Precision -------------------------------------------------------------

def test_a_disagreement_at_a_term_boundary_is_diagnosed_as_precision():
    """Not a rule difference and not a bug: our computed boundary and the
    practitioner's source can differ within the documented ±15 minutes."""
    instant = lichun(1993).astimezone(JST)
    ours = compute_chart(instant + timedelta(minutes=3))
    # The practitioner puts this birth on the other side of 立春.
    theirs = Expectation("near 立春", instant + timedelta(minutes=3), True,
                         {"year": compute_chart(
                             instant - timedelta(hours=2)).year.name})
    finding = review([theirs])[0]
    assert finding.diagnosis == "precision"
    assert ours.year.name != theirs.expected["year"]


# --- Unexplained -----------------------------------------------------------

def test_a_genuine_disagreement_is_left_unexplained():
    """The short list an engineer should actually look at. The other
    categories exist to keep it short, not to empty it."""
    wrong = Expectation("nonsense", datetime(1975, 6, 6, 10, 0), True,
                        {"year": "甲子", "day": "乙丑"})
    finding = review([wrong])[0]
    assert finding.diagnosis == "unexplained"
    assert "engineer" in finding.detail


def test_the_summary_counts_each_category():
    cases = [
        expectation_from(datetime(1990, 5, 15, 7, 30), label="ok"),
        expectation_from(datetime(1985, 3, 10, 23, 30),
                         ChartOptions(day_changes_at_2300=False), label="p1"),
        Expectation("bad", datetime(1975, 6, 6, 10, 0), True,
                    {"year": "甲子"}),
    ]
    counts = summarise(review(cases))
    assert counts["agrees"] == 1
    assert counts["P1"] == 1
    assert counts["unexplained"] == 1


# --- Reading the practitioner's file ---------------------------------------

def test_the_csv_template_loads(tmp_path):
    from pathlib import Path
    template = (Path(__file__).resolve().parent.parent.parent
                / "docs" / "practitioner" / "charts_template.csv")
    expectations = load_csv(template)
    assert len(expectations) == 2
    assert expectations[0].expected["year"] == "庚午"
    assert expectations[1].hour_known is False


def test_a_blank_birth_time_means_the_hour_is_unknown(tmp_path):
    path = tmp_path / "charts.csv"
    path.write_text(
        "label,birth_date,birth_time,year,month,day,hour,note\n"
        "a,1990-05-15,,庚午,,,,\n", encoding="utf-8")
    loaded = load_csv(path)[0]
    assert loaded.hour_known is False
    assert loaded.expected == {"year": "庚午"}


def test_comment_lines_are_ignored(tmp_path):
    path = tmp_path / "charts.csv"
    path.write_text(
        "# a note from the practitioner\n"
        "label,birth_date,birth_time,year,month,day,hour,note\n"
        "# another note\n"
        "a,1990-05-15,07:30,庚午,,,,\n", encoding="utf-8")
    assert len(load_csv(path)) == 1


def test_rows_without_a_date_are_skipped(tmp_path):
    path = tmp_path / "charts.csv"
    path.write_text(
        "label,birth_date,birth_time,year,month,day,hour,note\n"
        ",,,,,,,\n"
        "a,1990-05-15,07:30,庚午,,,,\n", encoding="utf-8")
    assert len(load_csv(path)) == 1


def test_review_does_not_mutate_the_engine_defaults():
    """The diagnosis explores alternative ChartOptions. None of that may
    leak into the options a real reading is computed with."""
    default = ChartOptions()
    review([Expectation("x", datetime(1985, 3, 10, 23, 30), True,
                        {"day": "甲子"})])
    assert ChartOptions() == default
    assert ChartOptions().day_changes_at_2300 is True
