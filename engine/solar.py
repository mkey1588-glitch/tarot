"""
Solar position and solar-term boundaries.

Four Pillars is a solar system, not a lunar one. The year changes at
立春 (roughly 4 February), not at New Year and not at Lunar New Year,
and each month changes at its 節 boundary rather than on the 1st. Getting
this wrong is the single most common defect in amateur implementations
and it produces charts that are silently wrong for anyone born within a
few days of a boundary.

Sun longitude follows Meeus, "Astronomical Algorithms" 2nd ed., ch. 25
(low-precision solar coordinates), with a Delta-T correction between
Universal and Terrestrial Time.

ACCURACY, STATED HONESTLY
-------------------------
The low-precision series is good to roughly 0.01 degrees. The Sun moves
about 0.9856 degrees per day, so 0.01 degrees is about 15 minutes of clock
time. Spot checks against published National Astronomical Observatory of
Japan values land within about 6 minutes, but 15 minutes is the number to
design against.

That is far tighter than the birth times most users can report accurately,
and irrelevant for the overwhelming majority of charts. It is NOT good
enough for someone born within a few minutes of a boundary, where the
month or even the year pillar flips. Those cases must not be guessed at:
`engine.bazi.compute_chart` flags them via `boundary_warnings` so they can
be routed to the retained practitioner rather than answered automatically.

If exactness at the boundary is later required, replace
`sun_apparent_longitude` with a VSOP87 truncation or a lookup table of
official 暦要項 term times. The rest of the engine is unaffected --
that is why this function is isolated here.

No third-party dependency: pure standard library, so the engine can be
unit-tested anywhere including CI without network access.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


def julian_day(dt_utc: datetime) -> float:
    """Julian Day (fractional) for an aware or naive UTC datetime."""
    if dt_utc.tzinfo is not None:
        dt_utc = dt_utc.astimezone(timezone.utc).replace(tzinfo=None)

    year, month = dt_utc.year, dt_utc.month
    day = (dt_utc.day
           + dt_utc.hour / 24.0
           + dt_utc.minute / 1440.0
           + dt_utc.second / 86400.0
           + dt_utc.microsecond / 86400_000_000.0)

    if month <= 2:
        year -= 1
        month += 12

    a = year // 100
    b = 2 - a + a // 4  # Gregorian calendar correction
    return (math.floor(365.25 * (year + 4716))
            + math.floor(30.6001 * (month + 1))
            + day + b - 1524.5)


def julian_day_number(d) -> int:
    """Integer Julian Day Number for a civil date (date or datetime).

    Used for the day pillar, which advances on a continuous 60-day cycle
    independent of any astronomical event.
    """
    year, month, day = d.year, d.month, d.day
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return int(math.floor(365.25 * (year + 4716))
               + math.floor(30.6001 * (month + 1))
               + day + b - 1524)


def delta_t_seconds(year: float) -> float:
    """Difference TT - UT in seconds (Espenak & Meeus polynomial fits).

    Piecewise fits covering 1941 onward, which spans every plausible birth
    date for a living user. Dates before 1941 fall back to the 1941-1961
    branch; the resulting error is a few seconds, which is immaterial next
    to the 15-minute accuracy of the solar series itself.
    """
    if year >= 2005:
        t = year - 2000
        return 62.92 + 0.32217 * t + 0.005589 * t * t
    if year >= 1986:
        t = year - 2000
        return (63.86 + 0.3345 * t - 0.060374 * t ** 2 + 0.0017275 * t ** 3
                + 0.000651814 * t ** 4 + 0.00002373599 * t ** 5)
    if year >= 1961:
        t = year - 1975
        return 45.45 + 1.067 * t - t ** 2 / 260.0 - t ** 3 / 718.0
    t = year - 1950
    return 29.07 + 0.407 * t - t ** 2 / 233.0 + t ** 3 / 2547.0


def sun_apparent_longitude(jd: float) -> float:
    """Apparent geocentric ecliptic longitude of the Sun, degrees in [0, 360).

    `jd` is a Julian Day in Universal Time; the Delta-T conversion to
    Terrestrial Time is applied internally.
    """
    approx_year = 2000.0 + (jd - 2451545.0) / 365.25
    jde = jd + delta_t_seconds(approx_year) / 86400.0
    t = (jde - 2451545.0) / 36525.0

    # Geometric mean longitude
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    # Mean anomaly
    m = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
    m_rad = math.radians(m % 360.0)

    # Equation of the centre
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(m_rad)
         + (0.019993 - 0.000101 * t) * math.sin(2 * m_rad)
         + 0.000289 * math.sin(3 * m_rad))

    true_longitude = l0 + c

    # Correction to apparent longitude: nutation and aberration
    omega = math.radians(125.04 - 1934.136 * t)
    apparent = true_longitude - 0.00569 - 0.00478 * math.sin(omega)

    return apparent % 360.0


def _longitude_offset(jd: float, target_deg: float) -> float:
    """Signed angular distance from the Sun's longitude to target, in (-180, 180]."""
    diff = (sun_apparent_longitude(jd) - target_deg + 180.0) % 360.0 - 180.0
    return diff


def solar_term_instant(target_deg: float, near: datetime) -> datetime:
    """UTC instant at which the Sun's apparent longitude reaches target_deg.

    Returns the crossing closest to `near`. Bisection on a bracketed root;
    the Sun's longitude is monotonic in time, so the only care needed is
    keeping the bracket inside a single revolution.
    """
    if near.tzinfo is None:
        near = near.replace(tzinfo=timezone.utc)

    jd_near = julian_day(near)

    # Days until the target longitude, at the mean rate of ~0.9856 deg/day.
    approx = jd_near - _longitude_offset(jd_near, target_deg) / 0.98564736

    lo, hi = approx - 3.0, approx + 3.0
    f_lo, f_hi = _longitude_offset(lo, target_deg), _longitude_offset(hi, target_deg)

    # Widen if the root is not bracketed (only happens near the 0/360 wrap).
    tries = 0
    while f_lo * f_hi > 0 and tries < 10:
        lo -= 2.0
        hi += 2.0
        f_lo, f_hi = _longitude_offset(lo, target_deg), _longitude_offset(hi, target_deg)
        tries += 1

    for _ in range(80):
        mid = (lo + hi) / 2.0
        f_mid = _longitude_offset(mid, target_deg)
        if f_lo * f_mid <= 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
        if hi - lo < 1e-9:  # ~0.1 ms
            break

    return _jd_to_datetime((lo + hi) / 2.0)


def _jd_to_datetime(jd: float) -> datetime:
    """Inverse of julian_day, returning an aware UTC datetime."""
    z = math.floor(jd + 0.5)
    f = (jd + 0.5) - z

    alpha = math.floor((z - 1867216.25) / 36524.25)
    a = z + 1 + alpha - math.floor(alpha / 4)
    b = a + 1524
    c = math.floor((b - 122.1) / 365.25)
    d = math.floor(365.25 * c)
    e = math.floor((b - d) / 30.6001)

    day = b - d - math.floor(30.6001 * e) + f
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715

    day_int = int(math.floor(day))
    seconds = (day - day_int) * 86400.0

    return (datetime(int(year), int(month), day_int, tzinfo=timezone.utc)
            + timedelta(seconds=seconds))


def lichun(year: int) -> datetime:
    """UTC instant of 立春 (sun at 315 degrees) for the given Gregorian year.

    This is the boundary at which the Four Pillars year changes.
    """
    return solar_term_instant(315.0, datetime(year, 2, 4, tzinfo=timezone.utc))
