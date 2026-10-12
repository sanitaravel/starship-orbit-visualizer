import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from starship_viz.figures import build_figure
from starship_viz.orbit import R_E, compute_trajectory
from starship_viz.sites import LAUNCH_SITES
from starship_viz.sun import night_polygon, subsolar_point

T0 = datetime(2025, 3, 20, 12, tzinfo=timezone.utc)


def _traj(site='Starbase, TX', n_orbits=2, step=60):
    return compute_trajectory(site, 'azimuth', 95, 'north', 200, n_orbits, T0, step)


def _anim(fig):
    return fig.layout.meta['anim']


def test_no_frames_and_no_slider():
    traj = _traj()
    for view in ('2d', '3d'):
        fig = build_figure(traj, view)
        assert len(fig.frames) == 0
        assert not fig.layout.sliders
        assert not fig.layout.updatemenus  # playback controls live in the Dash layout


def test_trace_layout_2d():
    fig = build_figure(_traj(), '2d')
    names = [t.name for t in fig.data]
    assert names == ['Day', 'Borders', 'Terminator', 'Ground track', 'Launch site', 'Ship']
    assert all(t.type == 'scattergeo' for t in fig.data)
    shown = [t.name for t in fig.data if t.showlegend is not False]
    assert shown == ['Terminator', 'Ground track', 'Launch site', 'Ship']
    # The dynamic layers are drawn on the canvas overlay: their Plotly traces are empty legend dummies.
    for t in fig.data[2:4] + fig.data[5:]:
        assert list(t.lon) == [None] and list(t.lat) == [None]
    site = fig.data[4]
    lon, lat = LAUNCH_SITES['Starbase, TX']['lon'], LAUNCH_SITES['Starbase, TX']['lat']
    assert (site.lon[0], site.lat[0]) == (lon, lat) and site.text[0] == 'Starbase, TX'
    assert None in list(fig.data[1].lon) and fig.data[1].showlegend is False
    assert fig.layout.margin.b >= 40  # room for the HTML playback bar


def test_trace_layout_3d():
    fig = build_figure(_traj(), '3d')
    a = _anim(fig)
    assert [t.type for t in fig.data] == ['surface', 'scatter3d', 'scatter3d', 'scatter3d',
                                          'scatter3d', 'scatter3d']
    assert fig.data[a['surface_trace']].type == 'surface'
    assert fig.data[a['ship_trace']].name == 'Ship'
    assert fig.data[0].showlegend is False
    assert fig.data[5].name == 'Borders' and fig.data[5].showlegend is False
    assert fig.data[5].line.width < fig.data[1].line.width
    # Day/night is lighting: ambient-only night side, so no per-frame surface re-colouring.
    lighting = fig.data[0].lighting
    assert lighting.ambient + lighting.diffuse == pytest.approx(1.0)
    assert 0 < lighting.ambient < 0.5 and lighting.specular == 0
    assert fig.layout.scene.aspectmode == 'data'


def test_payload_2d_content_and_shape():
    traj = _traj(n_orbits=1.5, step=45)
    fig = build_figure(traj, '2d', step_s=45)
    a = _anim(fig)
    n = len(traj.t_s)
    assert a['view'] == '2d' and a['step_s'] == 45.0
    assert len(a['t_s']) == len(a['lon']) == len(a['lat']) == n
    assert a['t_s'][0] == 0 and a['t_s'][-1] == pytest.approx(traj.t_s[-1], abs=1e-3)
    assert a['t0_ms'] == int(T0.timestamp() * 1000)
    assert -180 <= min(a['lon']) and max(a['lon']) < 180 and max(np.abs(a['lat'])) <= 90
    assert np.allclose(a['lon'], traj.lon_deg, atol=1e-3)
    assert np.allclose(a['lat'], traj.lat_deg, atol=1e-3)
    assert a['site'] == dict(lon=LAUNCH_SITES['Starbase, TX']['lon'], lat=LAUNCH_SITES['Starbase, TX']['lat'])
    assert set(a['colors']) >= {'track', 'ship', 'ship_edge', 'site', 'night', 'terminator'}


def test_payload_3d_content_and_shape():
    traj = _traj(step=30)
    a = _anim(build_figure(traj, '3d'))
    n = len(traj.t_s)
    assert a['view'] == '3d'
    assert len(a['x']) == len(a['y']) == len(a['z']) == n == len(a['t_s'])
    r = np.sqrt(np.array(a['x']) ** 2 + np.array(a['y']) ** 2 + np.array(a['z']) ** 2)
    assert r.min() == pytest.approx(R_E + 200, abs=1.0) and r.max() == pytest.approx(R_E + 200, abs=1.0)
    assert np.allclose(np.array([a['x'], a['y'], a['z']]).T, traj.r_ecef, atol=0.1)
    fig = build_figure(traj, '3d')
    assert (a['shade']['n_lat'], a['shade']['n_lon']) == np.array(fig.data[0].z).shape


def test_step_default_is_first_sample_interval():
    assert _anim(build_figure(_traj(step=60), '2d'))['step_s'] == 60.0


def test_sun_track_matches_subsolar_point_and_is_continuous():
    traj = _traj(n_orbits=3, step=30)
    a = _anim(build_figure(traj, '2d'))
    dt = a['sun_dt']
    assert dt > 0 and len(a['sun_lat']) == len(a['sun_lon'])
    assert (len(a['sun_lat']) - 1) * dt >= traj.t_s[-1]          # covers the whole run
    assert np.abs(np.diff(a['sun_lon'])).max() < 1.0              # unwrapped: no 360 jumps
    for i in (0, 7, len(a['sun_lat']) - 1):
        lat, lon = subsolar_point(T0 + timedelta(seconds=i * dt))
        assert a['sun_lat'][i] == pytest.approx(lat, abs=1e-3)
        assert (a['sun_lon'][i] - lon + 180) % 360 - 180 == pytest.approx(0, abs=1e-3)
    # linear interpolation between samples (what the player does) is accurate to well below a pixel
    mid = 3.5 * dt
    lat, lon = subsolar_point(T0 + timedelta(seconds=mid))
    assert 0.5 * (a['sun_lat'][3] + a['sun_lat'][4]) == pytest.approx(lat, abs=2e-3)
    assert (0.5 * (a['sun_lon'][3] + a['sun_lon'][4]) - lon + 180) % 360 - 180 == pytest.approx(0, abs=2e-3)


def test_payload_crosses_antimeridian_with_wrapped_longitudes():
    a = _anim(build_figure(_traj(n_orbits=3), '2d'))
    jumps = np.abs(np.diff(a['lon'])) > 180
    assert jumps.any()  # the player must split the track there (it does, see playback.js)


def test_payload_is_json_and_small():
    traj = compute_trajectory('Starbase, TX', 'azimuth', 95.0, 'north', 200, 3, T0, 30)
    f2, f3 = build_figure(traj, '2d'), build_figure(traj, '3d')
    s2, s3 = len(f2.to_json()), len(f3.to_json())
    assert s2 < 250_000, s2   # was ~3.5 MB with frames
    assert s3 < 1_000_000, s3  # was ~2.5 MB with frames
    json.dumps(_anim(f2))
    # The anim payload is a few tens of kB, rounded floats only.
    assert len(json.dumps(_anim(f2))) < 60_000
    assert all(len(repr(v).split('.')[-1]) <= 3 for v in _anim(f2)['lon'][:200])


@pytest.mark.parametrize('when', [datetime(2025, 6, 21, 3, tzinfo=timezone.utc),
                                  datetime(2025, 12, 21, 18, tzinfo=timezone.utc),
                                  datetime(2025, 3, 20, 12, tzinfo=timezone.utc)])
def test_night_polygon_has_no_wraparound_jumps(when):
    lon, lat = night_polygon(when)
    lon, lat = np.array(lon), np.array(lat)
    assert np.abs(np.diff(lon)).max() <= 180
    assert (lon[0], lat[0]) == (lon[-1], lat[-1])
    assert lon.min() == -180 and lon.max() == 180
    assert np.abs(lat).max() <= 90


def test_night_polygon_winding_clockwise_for_both_poles():
    def area(when):
        lon, lat = night_polygon(when)
        return 0.5 * np.sum(np.array(lon[:-1]) * np.array(lat[1:])
                            - np.array(lon[1:]) * np.array(lat[:-1]))
    assert area(datetime(2025, 6, 21, 3, tzinfo=timezone.utc)) < 0
    assert area(datetime(2025, 12, 21, 18, tzinfo=timezone.utc)) < 0


@pytest.mark.parametrize('view', ['2d', '3d'])
@pytest.mark.parametrize('site', list(LAUNCH_SITES))
def test_builds_for_every_site_and_view(site, view):
    traj = _traj(site=site, n_orbits=1, step=120)
    fig = build_figure(traj, view)
    assert len(_anim(fig)['t_s']) == len(traj.t_s)
    assert fig.layout.title.text is None


def test_invalid_view():
    with pytest.raises(ValueError):
        build_figure(_traj(), '4d')


def test_builds_for_a_tiny_run():
    traj = _traj(n_orbits=0.1, step=600)
    assert len(traj.t_s) >= 2
    for view in ('2d', '3d'):
        assert len(_anim(build_figure(traj, view))['t_s']) == len(traj.t_s)


def test_font_margin_and_no_title():
    for view in ('2d', '3d'):
        fig = build_figure(_traj(), view)
        assert fig.layout.title.text is None
        assert fig.layout.font.family == 'JetBrains Mono, monospace'
        assert fig.layout.margin.t <= 10


def test_load_borders():
    from starship_viz.coastlines import load_borders
    lines = load_borders()
    assert len(lines) > 100 and lines[0].shape[1] == 2


def test_player_script_has_no_plotly_frames_api():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / 'assets' / 'playback.js').read_text(encoding='utf-8')
    assert 'Plotly.animate' not in js and 'sliders' not in js
    assert 'requestAnimationFrame' in js
