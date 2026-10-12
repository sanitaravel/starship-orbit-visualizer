"""Circular-orbit physics with J2 secular rates and Earth rotation. numpy only.

Conventions: angles in degrees at the public API (radians internally);
azimuth is clockwise from north. Azimuth is the INERTIAL (non-rotating Earth)
azimuth: the Earth-rotation correction to launch azimuth is ignored.
Datetimes: aware datetimes are converted to UTC; naive ones are assumed UTC.
Distances km, times s.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .sites import LAUNCH_SITES
from .sun import ensure_utc, gmst_rad, julian_date

MU = 398600.4418          # km^3/s^2
R_E = 6378.137            # km
J2 = 1.08262668e-3
OMEGA_E = 7.2921150e-5    # rad/s
_EPS = 1e-12
_REACH_TOL = 1e-9         # tolerance of the inclination reachability check


def inclination_from_azimuth(lat_deg: float, az_deg: float) -> float:
    """Inclination for a launch azimuth: cos i = cos(lat) sin(Az).

    ``lat_deg``: site latitude, deg. ``az_deg``: inertial launch azimuth, deg,
    clockwise from north. Returns inclination in [0, 180] deg (equatorial
    plane of the inertial frame).
    """
    c = np.cos(np.radians(lat_deg)) * np.sin(np.radians(az_deg))
    return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))


def azimuth_from_inclination(lat_deg: float, inc_deg: float,
                             direction: str = 'north') -> float:
    """Inertial azimuth in [0, 360) for a given inclination.

    sin Az = cos i / cos lat. ``lat_deg`` and ``inc_deg`` in degrees; azimuth
    is clockwise from north. ``direction`` 'north' gives NE (prograde) or NW
    (retrograde); 'south' gives SE / SW. Raises ValueError if unreachable
    (|lat| <= inc <= 180 - |lat| is required) or if ``direction`` is invalid.
    """
    if direction not in ('north', 'south'):
        raise ValueError("direction must be 'north' or 'south'")
    cl = np.cos(np.radians(lat_deg))
    ci = np.cos(np.radians(inc_deg))
    if abs(ci) > cl + _REACH_TOL:
        lo = abs(lat_deg)
        # Round the suggested bounds inward so the suggested values are accepted.
        lo_txt = math.ceil(lo * 1000.0 - 1e-6) / 1000.0
        hi_txt = math.floor((180.0 - lo) * 1000.0 + 1e-6) / 1000.0
        raise ValueError(
            f"Inclination {inc_deg:g} deg is not reachable from latitude "
            f"{lat_deg:.3f} deg: minimum achievable is {lo_txt:.3f} deg, "
            f"maximum is {hi_txt:.3f} deg.")
    s = np.clip(ci / cl, -1.0, 1.0) if cl > _EPS else 0.0
    a = np.degrees(np.arcsin(s))
    az = a if direction == 'north' else 180.0 - a
    return float(az % 360.0)


def j2_rates(alt_km: float, inc_deg: float):
    """Secular J2 rates for a circular orbit, rad/s.

    ``alt_km``: altitude above the R_E sphere, km. ``inc_deg``: inclination, deg.
    Returns (raan_dot, argp_dot, mean_anom_dot, u_dot) in rad/s with u_dot =
    argp_dot + mean_anom_dot (argument-of-latitude rate). RAAN is measured in
    the inertial (ECI) equatorial frame.
    """
    a = R_E + alt_km
    n = np.sqrt(MU / a ** 3)
    k = J2 * (R_E / a) ** 2
    ci = np.cos(np.radians(inc_deg))
    c2 = ci ** 2
    raan_dot = -1.5 * n * k * ci
    argp_dot = 0.75 * n * k * (5 * c2 - 1)
    m_dot = n * (1 + 0.75 * k * (3 * c2 - 1))
    return float(raan_dot), float(argp_dot), float(m_dot), float(argp_dot + m_dot)


def orbital_period_s(alt_km: float, inc_deg: float) -> float:
    """Nodal (draconitic) period 2*pi/u_dot, seconds.

    Time between successive equal values of the argument of latitude, i.e.
    what the app calls one "orbit". ``alt_km`` in km, ``inc_deg`` in degrees.
    """
    return float(2 * np.pi / j2_rates(alt_km, inc_deg)[3])


@dataclass
class Trajectory:
    """Propagated orbit samples and summary values.

    Position arrays: ``r_eci`` is the inertial equatorial frame (z to the
    north pole, x to the vernal equinox, treated as equal to the frame of
    date), ``r_ecef`` the Earth-fixed frame (x to lon 0 / lat 0, z north); both
    in km, centred on the Earth's centre.
    """
    t_s: np.ndarray        # (N,) seconds since t0
    utc: np.ndarray        # (N,) datetime64[ms], UTC
    r_eci: np.ndarray      # (N,3) km, ECI
    r_ecef: np.ndarray     # (N,3) km, ECEF
    lat_deg: np.ndarray    # (N,) deg, spherical latitude of the sub-satellite point
    lon_deg: np.ndarray    # (N,) deg east, ECEF, wrapped to [-180, 180)
    alt_km: np.ndarray     # (N,) km, altitude above the R_E sphere
    period_s: float        # s, nodal period
    inc_deg: float         # deg
    az_deg: float          # deg, inertial launch azimuth, clockwise from north
    t0_utc: datetime = None
    site_name: str = ''


def propagate(site_lat, site_lon, inc_deg, northbound, alt_km,
              t0_utc: datetime, duration_s, step_s) -> Trajectory:
    """Propagate a circular orbit launched from (site_lat, site_lon).

    ``site_lat`` / ``site_lon``: deg (east positive). ``inc_deg``: deg.
    ``alt_km``: km above the R_E sphere. ``t0_utc``: launch instant (naive =
    UTC). ``duration_s`` and ``step_s``: s.
    ``northbound``: ship is moving north at the site (latitude increasing).
    The ship starts exactly at the site, at altitude ``alt_km`` (no ascent
    phase). Samples at t = 0, step_s, ... and always includes t = duration_s.
    Raises ValueError for step_s <= 0, duration_s < 0 or an inclination that
    cannot reach the site latitude.
    """
    t0 = ensure_utc(t0_utc)
    if step_s <= 0 or duration_s < 0:
        raise ValueError("step_s must be > 0 and duration_s >= 0")
    phi = np.radians(site_lat)
    inc = np.radians(inc_deg)
    si, ci = np.sin(inc), np.cos(inc)
    if abs(np.sin(phi)) > si + 1e-9:
        raise ValueError(
            f"Inclination {inc_deg:g} deg cannot reach latitude {site_lat:.3f} deg.")
    s = np.clip(np.sin(phi) / si, -1.0, 1.0) if si > _EPS else 0.0
    u0 = np.arcsin(s) if northbound else np.pi - np.arcsin(s)
    alpha = float(gmst_rad(t0)) + np.radians(site_lon)
    raan0 = alpha - np.arctan2(ci * np.sin(u0), np.cos(u0))

    raan_dot, _, _, u_dot = j2_rates(alt_km, inc_deg)
    period = float(2 * np.pi / u_dot)

    t = np.arange(0.0, duration_s, step_s)
    if t.size == 0 or t[-1] < duration_s - 1e-9:
        t = np.append(t, float(duration_s))
    if t.size > 1 and t[-1] - t[-2] < 1e-3:   # float-noise duplicate of the last sample
        t = np.delete(t, -2)
    u = u0 + u_dot * t
    raan = raan0 + raan_dot * t
    a = R_E + alt_km
    cu, su, cO, sO = np.cos(u), np.sin(u), np.cos(raan), np.sin(raan)
    r_eci = a * np.column_stack([cO * cu - sO * ci * su,
                                 sO * cu + cO * ci * su,
                                 si * su])
    th = gmst_rad(julian_date(t0) + t / 86400.0)
    c, s_ = np.cos(th), np.sin(th)
    x = c * r_eci[:, 0] + s_ * r_eci[:, 1]      # rotate about z by -GMST
    y = -s_ * r_eci[:, 0] + c * r_eci[:, 1]
    r_ecef = np.column_stack([x, y, r_eci[:, 2]])
    rn = np.linalg.norm(r_ecef, axis=1)
    lat = np.degrees(np.arcsin(np.clip(r_ecef[:, 2] / rn, -1, 1)))
    lon = (np.degrees(np.arctan2(y, x)) + 180.0) % 360.0 - 180.0

    cl = np.cos(phi)
    sa = np.clip(ci / cl, -1.0, 1.0) if cl > _EPS else 0.0
    a0 = np.degrees(np.arcsin(sa))
    az = float((a0 if northbound else 180.0 - a0) % 360.0)

    t0_naive = t0.replace(tzinfo=None)
    utc = np.datetime64(t0_naive, 'ms') + np.round(t * 1000).astype('timedelta64[ms]')
    return Trajectory(t_s=t, utc=utc, r_eci=r_eci, r_ecef=r_ecef, lat_deg=lat,
                      lon_deg=lon, alt_km=rn - R_E, period_s=period,
                      inc_deg=float(inc_deg), az_deg=az, t0_utc=t0)


def compute_trajectory(site_name, mode, value_deg, direction, alt_km,
                       n_orbits, t0_utc, step_s) -> Trajectory:
    """High-level entry point.

    ``site_name``: key of ``LAUNCH_SITES``. ``value_deg``: deg. ``alt_km``: km.
    ``n_orbits``: number of nodal periods (duration = n_orbits * period).
    ``t0_utc``: launch time (naive = UTC). ``step_s``: s. Returns a
    ``Trajectory``; raises ValueError for unknown site/mode or unreachable
    inclination.

    mode 'azimuth': value_deg is the inertial azimuth (direction is derived
    from it; the ``direction`` argument is ignored). mode 'inclination':
    value_deg is the inclination and ``direction`` ('north'/'south') is used.
    """
    if site_name not in LAUNCH_SITES:
        raise ValueError(f"Unknown launch site: {site_name!r}")
    site = LAUNCH_SITES[site_name]
    lat, lon = site['lat'], site['lon']
    if mode == 'azimuth':
        inc = inclination_from_azimuth(lat, value_deg)
        northbound = bool(np.cos(np.radians(value_deg)) >= -1e-9)
    elif mode == 'inclination':
        inc = float(value_deg)
        azimuth_from_inclination(lat, inc, direction)  # validates
        northbound = direction == 'north'
    else:
        raise ValueError("mode must be 'azimuth' or 'inclination'")
    period = orbital_period_s(alt_km, inc)
    traj = propagate(lat, lon, inc, northbound, alt_km, t0_utc,
                     n_orbits * period, step_s)
    if mode == 'azimuth':
        traj.az_deg = float(value_deg % 360.0)
    traj.site_name = site_name
    return traj
