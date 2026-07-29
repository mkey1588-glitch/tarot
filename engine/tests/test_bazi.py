"""Tests for the Four Pillars engine.

The point of these is not coverage for its own sake. Each test corresponds
to a way the engine could be silently wrong in a manner a knowledgeable
user would notice and we would not.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine import compute_chart, ChartOptions
from engine.constants import STEMS, BRANCHES
from engine.solar import lichun

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "known_charts.json").read_text(encoding="utf-8")
)
JST = timezone(timedelta(hours=9))


@pytest.mark.parametrize("case", FIXTURES["charts"], ids=lambda c: c["label"])
def test_known_charts(case):
    chart = compute_chart(datetime.fromisoformat(case["birth_local"]))
    expect = case["expect"]
    got = {
        "year": chart.year.name,
        "month": chart.month.name,
        "day": chart.day.name,
        "hour": chart.hour.name,
        "solar_year": chart.solar_year,
        "month_term": chart.month_term,
    }
    for key, want in expect.items():
        assert got[key] == want, f"{key}: expected {want}, got {got[key]}"


# --- The year boundary: 立春, not January and not Lunar New Year -----------

def test_year_pillar_changes_at_lichun_not_january():
    """A January birth belongs to the PREVIOUS solar year."""
    before = compute_chart(datetime(1990, 1, 20, 12, 0))
    after = compute_chart(datetime(1990, 3, 20, 12, 0))
    assert before.solar_year == 1989
    assert after.solar_year == 1990
    assert before.year.name != after.year.name


def test_year_pillar_flips_across_lichun_instant():
    """Two births an hour either side of 立春 get different year pillars."""
    instant = lichun(1995).astimezone(JST)
    before = compute_chart(instant - timedelta(hours=1))
    after = compute_chart(instant + timedelta(hours=1))
    assert before.solar_year == after.solar_year - 1
    assert before.year.name != after.year.name


def test_lunar_new_year_is_not_the_boundary():
    """2024's Lunar New Year was 10 Feb; 立春 was 4 Feb. A 6 Feb birth is
    already in the new solar year, which a lunar-calendar implementation
    would get wrong."""
    chart = compute_chart(datetime(2024, 2, 6, 12, 0))
    assert chart.solar_year == 2024


# --- The month boundary ----------------------------------------------------

def test_month_pillar_changes_at_sectional_term_not_month_start():
    """Early April is still the 卯 month; 清明 falls around the 5th."""
    early = compute_chart(datetime(2024, 4, 2, 12, 0))
    late = compute_chart(datetime(2024, 4, 10, 12, 0))
    assert early.month.branch == "卯"
    assert late.month.branch == "辰"


def test_month_stem_follows_wuhu_rule():
    """五虎遁: a 甲 or 己 year opens its 寅 month with 丙."""
    for year_start in (datetime(1984, 2, 10, 12, 0), datetime(1989, 2, 10, 12, 0)):
        chart = compute_chart(year_start)
        assert chart.year.stem in ("甲", "己")
        assert chart.month.branch == "寅"
        assert chart.month.stem == "丙"


# --- The day pillar --------------------------------------------------------

def test_day_pillar_is_an_unbroken_60_day_cycle():
    base = datetime(2020, 6, 1, 12, 0)
    first = compute_chart(base).day.name
    assert compute_chart(base + timedelta(days=60)).day.name == first
    assert compute_chart(base + timedelta(days=59)).day.name != first


def test_day_pillar_anchor():
    """2000-01-01 was 戊午. Everything else hangs off this."""
    assert compute_chart(datetime(2000, 1, 1, 12, 0)).day.name == "戊午"


def test_day_pillar_advances_at_2300_when_configured():
    """早子時/晩子時 is a real school split, so both behaviours must work."""
    rolls = ChartOptions(day_changes_at_2300=True)
    holds = ChartOptions(day_changes_at_2300=False)
    at_2330 = datetime(2020, 6, 1, 23, 30)
    assert compute_chart(at_2330, rolls).day.name == compute_chart(datetime(2020, 6, 2, 12, 0)).day.name
    assert compute_chart(at_2330, holds).day.name == compute_chart(datetime(2020, 6, 1, 12, 0)).day.name


# --- The hour pillar -------------------------------------------------------

@pytest.mark.parametrize("hour,branch", [
    (0, "子"), (1, "丑"), (2, "丑"), (3, "寅"), (5, "卯"), (7, "辰"),
    (9, "巳"), (11, "午"), (13, "未"), (15, "申"), (17, "酉"),
    (19, "戌"), (21, "亥"), (23, "子"),
])
def test_hour_branch_boundaries(hour, branch):
    """子 spans 23:00-00:59, and every other branch a clean two hours."""
    assert compute_chart(datetime(2020, 6, 1, hour, 0)).hour.branch == branch


def test_hour_stem_follows_wushu_rule():
    """五鼠遁: a 甲 or 己 day opens its 子 hour with 甲."""
    d = datetime(2020, 6, 1, 12, 0)
    for _ in range(60):
        chart = compute_chart(d)
        if chart.day.stem in ("甲", "己"):
            midnight = compute_chart(d.replace(hour=0, minute=30))
            assert midnight.hour.branch == "子"
            assert midnight.hour.stem == "甲"
            return
        d += timedelta(days=1)
    pytest.fail("no 甲 or 己 day found in 60 days, which is impossible")


# --- Boundary honesty ------------------------------------------------------

def test_births_near_a_term_boundary_are_flagged_for_review():
    """The solar series is good to ~15 minutes. Anything closer than the
    configured window must not be answered automatically."""
    instant = lichun(2001).astimezone(JST)
    chart = compute_chart(instant + timedelta(minutes=5))
    assert chart.needs_manual_review
    assert any("立春" in w for w in chart.boundary_warnings)


def test_ordinary_births_are_not_flagged():
    assert not compute_chart(datetime(1990, 5, 15, 14, 30)).needs_manual_review


# --- Local mean time -------------------------------------------------------

def test_local_mean_time_requires_a_longitude():
    with pytest.raises(ValueError):
        compute_chart(datetime(1990, 5, 15, 14, 30),
                      ChartOptions(apply_local_mean_time=True))


def test_local_mean_time_shifts_the_hour_pillar_when_it_should():
    """Fukuoka (130.4E) runs ~18 min behind JST clock time, which moves a
    birth just after an hour boundary back into the previous branch."""
    tokyo_clock = datetime(1990, 5, 15, 13, 5)
    plain = compute_chart(tokyo_clock)
    corrected = compute_chart(
        tokyo_clock,
        ChartOptions(apply_local_mean_time=True, birth_longitude_deg=130.4),
    )
    assert plain.hour.branch == "未"
    assert corrected.hour.branch == "午"


# --- Structural invariants -------------------------------------------------

def test_element_counts_always_total_eight():
    """Four pillars, each contributing a stem and a branch."""
    d = datetime(1975, 3, 3, 9, 0)
    for i in range(0, 400, 37):
        chart = compute_chart(d + timedelta(days=i))
        assert sum(chart.element_counts.values()) == 8


def test_all_pillars_are_valid_sexagenary_combinations():
    """Stem and branch parity must match: 陽 stems only pair with 陽 branches."""
    d = datetime(1968, 1, 1, 6, 0)
    for i in range(0, 4000, 61):
        chart = compute_chart(d + timedelta(days=i))
        for p in (chart.year, chart.month, chart.day, chart.hour):
            assert STEMS.index(p.stem) % 2 == BRANCHES.index(p.branch) % 2, (
                f"impossible pillar {p.name}"
            )


def test_chart_serialises_to_json():
    """to_dict is the contract with the interpretation layer."""
    payload = compute_chart(datetime(1990, 5, 15, 14, 30)).to_dict()
    json.dumps(payload, ensure_ascii=False)
    assert set(payload["pillars"]) == {"year", "month", "day", "hour"}
    assert payload["day_master"]["stem"] == payload["pillars"]["day"]["stem"]
