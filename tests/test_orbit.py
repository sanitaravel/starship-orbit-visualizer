from datetime import datetime, timezone

import numpy as np
import pytest

from starship_viz.orbit import (azimuth_from_inclination, compute_trajectory,
                                inclination_from_azimuth, j2_rates,
                                orbital_period_s, propagate)
from starship_viz.sites import LAUNCH_SITES

T0 = datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize('name', list(LAUNCH_SITES))
def test_az_inc_roundtrip(name):
    lat = LAUNCH_SITES[name]['lat']
    for az in (45.0, 70.0, 95.0, 120.0, 300.0):
        inc = inclination_from_azimuth(lat, az)
        d = 'north' if np.cos(np.radians(az)) >= 0 else 'south'
        assert azimuth_from_inclination(lat, inc, d) == pytest.approx(az, abs=1e-6)


def test_infeasible_inclination():
    with pytest.raises(ValueError, match='minimum'):
        azimuth_from_inclination(28.5, 10.0)
    with pytest.raises(ValueError):
        azimuth_from_inclination(28.5, 170.0)


@pytest.mark.parametrize('name', list(LAUNCH_SITES))
@pytest.mark.parametrize('mode,val,dirn', [('azimuth', 95.0, 'north'),
                                           ('azimuth', 90.0, 'north'),
                                           ('azimuth', 270.0, 'north'),
                                           ('azimuth', 200.0, 'north'),
                                           ('inclination', 51.6, 'south'),
                                           ('inclination', 120.0, 'north')])
def test_first_point_is_site_and_max_lat(name, mode, val, dirn):
    s = LAUNCH_SITES[name]
    tr = compute_trajectory(name, mode, val, dirn, 200, 1, T0, 10)
    assert tr.lat_deg[0] == pytest.approx(s['lat'], abs=1e-6)
    dlon = (tr.lon_deg[0] - s['lon'] + 180) % 360 - 180
    assert dlon == pytest.approx(0, abs=1e-6)
    inc = tr.inc_deg
    assert np.max(np.abs(tr.lat_deg)) == pytest.approx(min(inc, 180 - inc), abs=0.5)
    assert tr.t_s[-1] == pytest.approx(tr.period_s)


def test_equatorial_no_nan():
    tr = propagate(0.0, 0.0, 0.0, True, 300, T0, 6000, 60)
    assert np.all(np.isfinite(tr.r_ecef))
    tr = propagate(0.0, 10.0, 180.0, False, 300, T0, 6000, 60)
    assert np.all(np.isfinite(tr.r_ecef))


def test_no_duplicate_final_sample():
    tr = compute_trajectory('Starbase, TX', 'azimuth', 95, 'north', 1530, 2, T0, 15)
    assert np.all(np.diff(tr.t_s) > 1e-3)
    assert np.all(np.isfinite(tr.r_ecef))
    assert tr.t_s[-1] == pytest.approx(2 * tr.period_s)


def test_sun_synchronous_raan_rate():
    deg_day = np.degrees(j2_rates(800, 98.6)[0]) * 86400
    assert deg_day == pytest.approx(0.9856, rel=0.02)


def test_period_200km():
    # J2 shifts the nodal period by -0.3%..+0.15% vs Keplerian (88.5 min);
    # at the critical inclination 63.435 deg the shift vanishes.
    assert 88.4 <= orbital_period_s(200, 63.435) / 60 <= 88.6


def test_naive_datetime_assumed_utc():
    a = compute_trajectory('Starbase, TX', 'azimuth', 95, 'north', 200, 1,
                           datetime(2025, 3, 1, 12), 60)
    b = compute_trajectory('Starbase, TX', 'azimuth', 95, 'north', 200, 1, T0, 60)
    assert np.allclose(a.r_ecef, b.r_ecef)


def test_error_message_bounds_are_accepted_values():
    import re
    for lat in (28.5721, 25.9971, 34.632, 5.2362):
        with pytest.raises(ValueError) as ei:
            azimuth_from_inclination(lat, 1.0 if lat > 10 else 90.0 + 0, 'north') \
                if lat > 10 else azimuth_from_inclination(lat, 179.99999, 'north')
        lo, hi = map(float, re.findall(r'(\d+\.\d{3}) deg', str(ei.value))[1:3])
        azimuth_from_inclination(lat, lo, 'north')
        azimuth_from_inclination(lat, hi, 'north')


def test_reachability_tolerance():
    azimuth_from_inclination(28.5721, 28.5721 - 1e-10, 'north')
