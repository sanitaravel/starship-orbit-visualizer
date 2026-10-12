"""Sun position, GMST, day/night geometry. numpy only.

Datetimes: timezone-aware datetimes are converted to UTC; naive datetimes are
ASSUMED to already be UTC. Angles are degrees at the public API unless the
function name ends in ``_rad``.
"""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

_J2000_JD = 2451545.0
_J2000_DT = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def ensure_utc(dt: datetime) -> datetime:
    """Return an aware UTC datetime (naive input is assumed to be UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def julian_date(dt_utc: datetime) -> float:
    """Julian date (days) of a datetime, treating UTC as UT1 (no leap/dUT1 correction)."""
    return _J2000_JD + (ensure_utc(dt_utc) - _J2000_DT).total_seconds() / 86400.0


def gmst_rad(t):
    """Greenwich mean sidereal time in radians, in [0, 2*pi).

    ``t`` is a datetime or a Julian date (float or numpy array). IAU-82 /
    Vallado polynomial.
    """
    if isinstance(t, datetime):
        jd = julian_date(t)
    else:
        jd = np.asarray(t, dtype=float)
    T = (jd - _J2000_JD) / 36525.0
    sec = (67310.54841 + (876600.0 * 3600.0 + 8640184.812866) * T
           + 0.093104 * T ** 2 - 6.2e-6 * T ** 3)
    return np.mod(sec, 86400.0) * (2.0 * np.pi / 86400.0)


def _sun_ra_dec(dt_utc: datetime):
    """Apparent RA, Dec (rad) of the Sun, Astronomical Almanac low precision."""
    n = julian_date(dt_utc) - _J2000_JD
    L = np.radians(280.460 + 0.9856474 * n)
    g = np.radians(357.528 + 0.9856003 * n)
    lam = L + np.radians(1.915) * np.sin(g) + np.radians(0.020) * np.sin(2 * g)
    eps = np.radians(23.439 - 0.0000004 * n)
    ra = np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam))
    dec = np.arcsin(np.sin(eps) * np.sin(lam))
    return float(ra), float(dec)


def subsolar_point(dt_utc: datetime):
    """Return (lat_deg, lon_deg) of the subsolar point (Sun at zenith).

    Latitude is the Sun's declination, deg; longitude is east-positive in the
    Earth-fixed frame, in [-180, 180). Low-precision solar position
    (Astronomical Almanac approximation), adequate for display only.
    """
    ra, dec = _sun_ra_dec(dt_utc)
    lon = np.degrees(ra - float(gmst_rad(dt_utc)))
    lon = (lon + 180.0) % 360.0 - 180.0
    return float(np.degrees(dec)), float(lon)


def solar_elevation_grid(dt_utc: datetime, lats_deg, lons_deg) -> np.ndarray:
    """sin(solar elevation) on a lat/lon grid; shape (len(lats), len(lons)).

    ``lats_deg`` / ``lons_deg``: 1-D, degrees (east positive, Earth-fixed).
    Values are dimensionless in [-1, 1]; positive means the Sun is up.
    No atmospheric refraction.
    """
    lat0, lon0 = subsolar_point(dt_utc)
    lat = np.radians(np.asarray(lats_deg, dtype=float))[:, None]
    dlon = np.radians(np.asarray(lons_deg, dtype=float) - lon0)[None, :]
    d = np.radians(lat0)
    return np.sin(lat) * np.sin(d) + np.cos(lat) * np.cos(d) * np.cos(dlon)


def terminator_line(dt_utc: datetime, n: int = 361):
    """Day/night boundary (solar elevation 0) as (lons_deg, lats_deg).

    ``n`` points, lons evenly from -180 to 180 deg; lats in degrees. The
    subsolar declination is clamped to |dec| >= 1e-3 rad to avoid the
    equinox singularity.
    """
    lat0, lon0 = subsolar_point(dt_utc)
    d = np.radians(lat0)
    if abs(d) < 1e-3:
        d = 1e-3 if d >= 0 else -1e-3
    lons = np.linspace(-180.0, 180.0, n)
    lats = np.degrees(np.arctan(-np.cos(np.radians(lons - lon0)) / np.tan(d)))
    return lons, lats


def night_polygon(dt_utc: datetime, n: int = 181):
    """Night-side polygon ring for the equirectangular map, as (lons, lats) arrays.

    Terminator from -180 to 180, then back along the dark pole in 90 degree
    steps (so no consecutive points jump by more than 180 degrees in longitude)
    and closed at the start point. The ring winds clockwise on the map for
    either dark pole. The dark pole is the south pole if the subsolar latitude
    is >= 0 (same convention as ``terminator_line``), else the north pole.
    """
    lat_s, _ = subsolar_point(dt_utc)
    lons, lats = terminator_line(dt_utc, n)
    pole = -90.0 if lat_s >= 0 else 90.0
    ring_lon = np.concatenate([lons, [180.0, 90.0, 0.0, -90.0, -180.0, lons[0]]])
    ring_lat = np.concatenate([lats, [pole] * 5 + [lats[0]]])
    if pole > 0:
        # Keep a clockwise (on the map) winding for both poles: Scattergeo
        # fills are spherical polygons and an anticlockwise ring is inverted.
        ring_lon, ring_lat = ring_lon[::-1], ring_lat[::-1]
    return ring_lon, ring_lat
