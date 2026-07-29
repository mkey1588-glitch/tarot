"""Tests for solar position and term boundaries."""

from datetime import datetime, timedelta, timezone

import pytest

from engine.solar import (
    julian_day, julian_day_number, sun_apparent_longitude,
    solar_term_instant, lichun, delta_t_seconds,
)

JST = timezone(timedelta(hours=9))

# Published 立春 times from the National Astronomical Observatory of Japan,
# in JST. The engine is expected to land within TOLERANCE_MINUTES of these.
# TODO(team): extend this table from the NAOJ 暦要項 before Phase 0 launch.
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
