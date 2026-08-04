"""Tests for solar position and term boundaries."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from engine.constants import SECTIONAL_TERMS

from engine.solar import (
    julian_day, julian_day_number, sun_apparent_longitude,
    solar_term_instant, lichun, delta_t_seconds,
)

JST = timezone(timedelta(hours=9))

# Published 立春 times from the National Astronomical Observatory of Japan,
# in JST. The engine is expected to land within TOLERANCE_MINUTES of these.
# Kept as a readable anchor. The broad check now lives at the bottom of this
# file, against 312 NAOJ values spanning 1970-2025.
PUBLISHED_LICHUN_JST = {
    2024: datetime(2024, 2, 4, 17, 27, tzinfo=JST),
    2025: datetime(2025, 2, 3, 23, 10, tzinfo=JST),
}
TOLERANCE_MINUTES = 15


def test_julian_day_at_j2000_epoch():
    assert julian_day(datetime(2000, 1, 1, 12, 0)) == pytest.approx(2451545.0, abs=1e-6)


def test_julian_day_number_at_j2000_epoch():
    assert julian_day_number(datetime(2000, 1, 1)) == 2451545


def test_sun_longitude_is_zero_at_the_march_equinox():
    """The vernal equinox is by definition 0 degrees of ecliptic longitude."""
    equinox = solar_term_instant(0.0, datetime(2024, 3, 20, tzinfo=timezone.utc))
    assert equinox.month == 3
    assert 19 <= equinox.day <= 21
    assert sun_apparent_longitude(julian_day(equinox)) == pytest.approx(0.0, abs=0.01)


def test_sun_longitude_advances_about_one_degree_per_day():
    jd = julian_day(datetime(2024, 6, 1, tzinfo=timezone.utc))
    step = (sun_apparent_longitude(jd + 1) - sun_apparent_longitude(jd)) % 360
    assert 0.95 <= step <= 1.02


@pytest.mark.parametrize("year,published", sorted(PUBLISHED_LICHUN_JST.items()))
def test_lichun_matches_published_values(year, published):
    """Guards the accuracy claim in the module docstring. If this fails, the
    docstring is now lying and both must be fixed together."""
    computed = lichun(year).astimezone(JST)
    drift = abs((computed - published).total_seconds()) / 60.0
    assert drift <= TOLERANCE_MINUTES, (
        f"{year} 立春 computed {computed:%Y-%m-%d %H:%M} vs published "
        f"{published:%Y-%m-%d %H:%M} -- {drift:.1f} min adrift"
    )


def test_lichun_always_falls_in_early_february():
    for year in range(1950, 2051, 7):
        d = lichun(year).astimezone(JST)
        assert d.month == 2 and 3 <= d.day <= 5


def test_delta_t_is_continuous_across_fit_boundaries():
    for boundary in (1961, 1986, 2005):
        below = delta_t_seconds(boundary - 0.01)
        above = delta_t_seconds(boundary + 0.01)
        assert abs(above - below) < 2.0, f"discontinuity at {boundary}"


# --- Independent verification against published NAOJ values ----------------
#
# The two-entry PUBLISHED_LICHUN_JST table above checked one term in one
# delta-T branch. The target user is a woman aged 30-50, so born roughly
# 1976-1996, and delta_t_seconds switches polynomial at 1986 and 2005 — so
# the engine was verified in a decade in which no user was born, on the one
# term out of twelve that happens to open the year.
#
# The fixture below is 312 terms across 13 years, computed by NAOJ and not by
# us. Regenerate with `python3 scripts/fetch_naoj_terms.py`; the tests
# themselves never touch the network.

NAOJ = json.loads(
    (Path(__file__).parent / "fixtures" / "naoj_solar_terms.json")
    .read_text(encoding="utf-8")
)
SECTIONAL_DEGREES = {deg for _name, deg, _branch in SECTIONAL_TERMS}


def _drifts(sectional_only: bool = False):
    for year, payload in NAOJ["years"].items():
        for term in payload["terms"]:
            if sectional_only and term["longitude_deg"] not in SECTIONAL_DEGREES:
                continue
            published = datetime.strptime(term["jst"], "%Y-%m-%dT%H:%M") \
                                .replace(tzinfo=JST)
            computed = solar_term_instant(
                float(term["longitude_deg"]),
                published.astimezone(timezone.utc)).astimezone(JST)
            yield (int(year), term["term"],
                   abs((computed - published).total_seconds()) / 60.0)


def test_the_fixture_covers_the_years_users_were_actually_born():
    """Guards the gap this section was written to close. If the fixture is
    ever trimmed back to recent years, the accuracy claim stops being
    evidence about anyone we serve."""
    years = {int(y) for y in NAOJ["years"]}
    assert any(1961 <= y <= 1985 for y in years), "no delta-T 1961-1985 sample"
    assert any(1986 <= y <= 2004 for y in years), "no delta-T 1986-2004 sample"
    assert any(y >= 2005 for y in years), "no delta-T >=2005 sample"
    # A user aged 30-50 in 2026 was born 1976-1996.
    assert sum(1 for y in years if 1976 <= y <= 1996) >= 4


def test_the_fixture_is_not_our_own_output_played_back():
    """The distinction CLAUDE.md draws: a fixture recorded from the engine
    tests nothing. This one records where it came from."""
    assert "National Astronomical Observatory" in NAOJ["_source"]
    assert NAOJ["_url"].startswith("https://eco.mtk.nao.ac.jp/")
    assert NAOJ["_retrieved"]


def test_every_published_term_is_within_the_documented_tolerance():
    """The claim in solar.py's docstring, checked against all 24 terms rather
    than 立春 alone."""
    breaches = [(y, t, d) for y, t, d in _drifts() if d > TOLERANCE_MINUTES]
    assert not breaches, (
        "terms outside the documented ±%d min: %s"
        % (TOLERANCE_MINUTES, breaches[:5])
    )


def test_accuracy_does_not_degrade_in_the_years_users_were_born():
    """The point of the exercise. If the pre-2005 branches were materially
    worse, every chart we produce would be worse for our actual users than
    the test suite suggested."""
    def worst(lo, hi):
        return max(d for y, _t, d in _drifts() if lo <= y <= hi)

    older, recent = worst(1961, 2004), worst(2005, 2100)
    assert older <= TOLERANCE_MINUTES
    # Allow real variation, but catch a branch that is quietly much worse.
    assert older <= recent + 5.0, (
        f"pre-2005 worst {older:.1f} min vs post-2005 {recent:.1f} min"
    )


def test_the_terms_that_set_the_month_pillar_are_the_most_accurate_group():
    """Only the twelve sectional terms move a pillar. The other twelve are
    computed by the same code but nothing depends on them, so this is the
    number that matters for a chart."""
    sectional = [d for _y, _t, d in _drifts(sectional_only=True)]
    assert len(sectional) >= 120
    assert max(sectional) <= 10.0, f"worst sectional drift {max(sectional):.2f} min"
    assert sum(sectional) / len(sectional) <= 6.0


def test_the_boundary_warning_window_is_wider_than_the_measured_error():
    """ChartOptions.boundary_warning_minutes defaults to 30. That is only a
    safety factor if it exceeds the error we actually observe, rather than
    the error we assumed. P4 remains the practitioner's ruling; this is the
    evidence it should be made on."""
    worst_sectional = max(d for _y, _t, d in _drifts(sectional_only=True))
    assert worst_sectional * 2 <= 30


def test_delta_t_matches_naoj_where_our_users_were_born():
    """delta_t_seconds had only a continuity test. NAOJ publishes the value
    it used, so the fit can be checked rather than assumed."""
    for year, payload in NAOJ["years"].items():
        if int(year) > 2005 or payload["delta_t_seconds"] is None:
            continue
        ours = delta_t_seconds(int(year))
        assert abs(ours - payload["delta_t_seconds"]) <= 2.0, (
            f"{year}: ours {ours:.2f}s vs NAOJ {payload['delta_t_seconds']}s"
        )


def test_delta_t_extrapolation_is_drifting_but_still_immaterial():
    """The >=2005 fit is an extrapolation and is now ~5s adrift. That is
    0.08 minutes against a 15-minute tolerance, so it does not matter yet —
    but it grows, and this test is where it will be noticed when it does."""
    recent = [(int(y), p) for y, p in NAOJ["years"].items()
              if int(y) >= 2020 and p["delta_t_seconds"]]
    assert recent, "no recent year in the fixture to check drift against"
    worst = max(abs(delta_t_seconds(y) - p["delta_t_seconds"]) for y, p in recent)
    assert worst < 30.0, (
        f"delta-T extrapolation is {worst:.1f}s adrift. Still small against a "
        "15-minute tolerance, but the Espenak & Meeus fit is being asked to "
        "predict further than it was made for. Refit or tabulate."
    )
