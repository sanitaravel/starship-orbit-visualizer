from datetime import datetime, timezone

import pytest
from dash import no_update

from app import convert_mode_value, validate_inputs
from starship_viz.orbit import azimuth_from_inclination, inclination_from_azimuth
from starship_viz.sites import LAUNCH_SITES

CAPE = 'Cape Canaveral, FL'


def _site():
    name = next((n for n in LAUNCH_SITES if 'Cape' in n), None)
    assert name, 'Cape Canaveral missing from LAUNCH_SITES'
    return name


def _ok_args(**kw):
    a = dict(site='Starbase, TX', mode='azimuth', value=95.0, direction='north',
             alt_km=200, n_orbits=2, date_str='2025-01-01', time_str='12:00:00',
             step_s=30, view='2d')
    a.update(kw)
    return a


def test_validate_ok_and_view():
    params, err = validate_inputs(**_ok_args())
    assert err is None and params['step_s'] == 30.0
    for bad in (None, '4d', ''):
        params, err = validate_inputs(**_ok_args(view=bad))
        assert params is None and 'view' in err.lower()


def test_az_to_inc_is_clamped_into_reachable_range():
    site = _site()
    lat = LAUNCH_SITES[site]['lat']
    inc, direction = convert_mode_value('inclination', 90.0, site, 'north')
    assert inc >= abs(lat) and direction == 'north'
    # The converted value must be accepted by the reachability check.
    azimuth_from_inclination(lat, inc, direction)
    inc, direction = convert_mode_value('inclination', 270.0, site, 'north')
    assert inc <= 180 - abs(lat) and direction == 'north'
    azimuth_from_inclination(lat, inc, direction)
    inc, direction = convert_mode_value('inclination', 180.0, site, 'north')
    azimuth_from_inclination(lat, inc, direction)


def test_inc_to_az_failure_clears_value():
    site = _site()
    assert convert_mode_value('azimuth', 5.0, site, 'north') == (None, no_update)
    val, _ = convert_mode_value('azimuth', 51.6, site, 'south')
    assert val == pytest.approx(180 - 90 + 0, abs=90)  # a valid azimuth number


def test_conversion_noop_cases():
    assert convert_mode_value('azimuth', None, 'Starbase, TX', 'north') == (no_update,) * 2
    assert convert_mode_value('inclination', 400, 'Starbase, TX', 'north') == (no_update,) * 2


def test_orbits_decimal_accepted():
    for n in (0.25, 2.5, 16, 16.0, 0.1):
        params, err = validate_inputs(**_ok_args(n_orbits=n, step_s=60))
        assert err is None and params['n_orbits'] == float(n)


def test_orbits_rejected():
    for bad, text in ((0, 'greater than 0'), (-1, 'greater than 0'), (-0.5, 'greater than 0'),
                      (None, 'required'), (16.5, 'at most'), (float('nan'), 'number')):
        params, err = validate_inputs(**_ok_args(n_orbits=bad))
        assert params is None and text in err


def test_fractional_orbits_duration_and_payload():
    from starship_viz.figures import build_figure
    from starship_viz.orbit import compute_trajectory
    for n in (0.25, 2.5):
        params, err = validate_inputs(**_ok_args(n_orbits=n, step_s=30))
        assert err is None
        traj = compute_trajectory(**params)
        assert abs(traj.t_s[-1] - n * traj.period_s) < 1e-6
        assert traj.t_s[-1] - traj.t_s[-2] <= 30 + 1e-9
        anim = build_figure(traj, '2d', params['step_s']).layout.meta['anim']
        assert len(anim['t_s']) == len(traj.t_s) >= 2
        assert anim['step_s'] == 30.0 and anim['t_s'][-1] == pytest.approx(n * traj.period_s, abs=1e-3)


def test_layout_has_playback_bar():
    from app import make_layout

    ids = []

    def walk(c):
        if hasattr(c, 'id') and c.id:
            ids.append(c.id)
        kids = getattr(c, 'children', None)
        for k in (kids if isinstance(kids, (list, tuple)) else [kids]):
            if k is not None and not isinstance(k, str):
                walk(k)
    walk(make_layout())
    for needed in ('pb-back', 'pb-toggle', 'pb-fwd', 'pb-speed-box', 'pb-range-box', 'pb-utc'):
        assert needed in ids
