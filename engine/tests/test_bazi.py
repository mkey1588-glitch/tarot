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


# --- Unknown birth time: three pillars, not four (P6) ----------------------

def test_unknown_hour_produces_no_hour_pillar():
    """The failure this guards: before P6, a date-only birth silently got a
    時柱 computed from midnight. A fabricated pillar is the one error a
    knowledgeable user spots instantly."""
    chart = compute_chart(datetime(1990, 5, 15), hour_known=False)
    assert chart.hour is None
    assert not chart.hour_known
    assert chart.to_dict()["pillars"]["hour"] is None
    assert chart.to_dict()["hour_known"] is False


def test_unknown_hour_keeps_the_other_three_pillars_intact():
    known = compute_chart(datetime(1990, 5, 15, 7, 30))
    unknown = compute_chart(datetime(1990, 5, 15), hour_known=False)
    assert (unknown.year.name, unknown.month.name, unknown.day.name) == \
           (known.year.name, known.month.name, known.day.name)
    assert unknown.day_master == known.day_master


def test_unknown_hour_ignores_the_reported_clock_time():
    """A caller who has no hour may still pass a datetime. Whatever is in
    the time component must not reach the chart."""
    midnight = compute_chart(datetime(1990, 5, 15, 0, 0), hour_known=False)
    for hour in (3, 11, 19, 23):
        other = compute_chart(datetime(1990, 5, 15, hour, 45), hour_known=False)
        assert other.to_dict() == midnight.to_dict(), f"hour {hour} leaked"


def test_unknown_hour_tallies_six_element_positions():
    chart = compute_chart(datetime(1990, 5, 15), hour_known=False)
    assert sum(chart.element_counts.values()) == 6


def test_unknown_hour_skips_local_mean_time_correction():
    """Correcting an unknown clock time is meaningless, and from midnight a
    negative correction crosses into the previous day and moves the DAY
    pillar — the exact silent error this whole module exists to prevent."""
    fukuoka = ChartOptions(apply_local_mean_time=True, birth_longitude_deg=130.4)
    corrected = compute_chart(datetime(1990, 5, 15), fukuoka, hour_known=False)
    plain = compute_chart(datetime(1990, 5, 15), hour_known=False)
    assert corrected.day.name == plain.day.name


def test_unknown_hour_does_not_require_a_longitude():
    """The known-hour path raises without one. The unknown-hour path never
    uses it, so it must not raise."""
    compute_chart(datetime(1990, 5, 15),
                  ChartOptions(apply_local_mean_time=True),
                  hour_known=False)


def test_unknown_hour_widens_the_boundary_window_to_the_whole_day():
    """Born on the day 立春 falls, with no clock time, the YEAR pillar is
    genuinely ambiguous rather than merely imprecise. 30 minutes is the
    wrong question to ask of a 24-hour window."""
    on_the_day = lichun(2001).astimezone(JST).replace(hour=0, minute=0)
    chart = compute_chart(on_the_day, hour_known=False)
    assert chart.needs_manual_review
    assert any("立春" in w and "unknown" in w for w in chart.boundary_warnings)

    # The same date with a clock time well clear of the instant is fine.
    clear = compute_chart(on_the_day.replace(hour=23, minute=59))
    assert not clear.needs_manual_review


def test_unknown_hour_away_from_any_term_is_not_flagged():
    assert not compute_chart(datetime(1990, 5, 15), hour_known=False).needs_manual_review


def test_unknown_hour_flags_a_month_term_falling_on_the_birth_date():
    from engine.solar import solar_term_instant

    risshuu = solar_term_instant(135.0, datetime(2020, 8, 7, tzinfo=timezone.utc))
    chart = compute_chart(risshuu.astimezone(JST).replace(hour=0, minute=0),
                          hour_known=False)
    assert chart.needs_manual_review
    assert any("MONTH" in w for w in chart.boundary_warnings)


def test_day_change_at_2300_does_not_apply_without_an_hour():
    """early/late 子時 is a question about a clock time we do not have."""
    rolls = ChartOptions(day_changes_at_2300=True)
    holds = ChartOptions(day_changes_at_2300=False)
    d = datetime(2020, 6, 1)
    assert compute_chart(d, rolls, hour_known=False).day.name == \
           compute_chart(d, holds, hour_known=False).day.name


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
