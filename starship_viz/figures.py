"""Plotly figures (2D map and 3D globe) for the client-side player.

There are no Plotly frames and no Plotly slider. The figure holds the static
scene; the animation data (sample times, ship positions, subsolar track) is
sent once in ``layout.meta['anim']`` and played by ``assets/playback.js``
(requestAnimationFrame, continuous sim time). Lengths in km, angles in
degrees. 2D uses geodetic lon/lat; 3D uses the Earth-fixed (ECEF) frame
(x to lon 0 / lat 0, z to the north pole), so the orbit path drawn in 3D is the
path relative to the rotating Earth, matching the 2D ground track.
"""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import plotly.graph_objects as go

from .coastlines import load_borders, load_coastlines
from .orbit import R_E, Trajectory
from . import theme
from .sites import LAUNCH_SITES
from .sun import subsolar_point

SHIP_COLOR = theme.ACCENT
TRACK_COLOR = theme.ACCENT
SITE_COLOR = theme.ACCENT_2
_SUN_DT = 60.0        # s, cadence of the subsolar-point samples sent to the player
_BG = theme.BACKGROUND
_N_SHADES = 16          # fallback only: quantisation levels of the re-coloured 3D surface
_EYE = 1.25            # 3D camera distance (scene units), globe + orbit fill the plot
_EYE_LIFT = 0.15       # slight upward tilt of the camera
_TWILIGHT = 0.12        # fallback only: sin(elevation) range of the twilight gradient
_AMBIENT = int(theme.EARTH[1:3], 16) / int(theme.DAY_3D[1:3], 16)   # night brightness / day brightness
_DIFFUSE = 1.0 - _AMBIENT
_ROUGHNESS = 0.5


def _to_datetime(t64):
    """numpy datetime64 -> naive datetime (interpreted as UTC by sun.py)."""
    return t64.astype('datetime64[ms]').astype(object)


def _site_lonlat(traj: Trajectory):
    site = LAUNCH_SITES.get(traj.site_name)
    if site is None:
        return float(traj.lon_deg[0]), float(traj.lat_deg[0])
    return site['lon'], site['lat']


def _step_of(traj: Trajectory, step_s) -> float:
    """The user's sample step: ``step_s`` if given, else the first sample interval."""
    if step_s is not None:
        return float(step_s)
    return float(traj.t_s[1] - traj.t_s[0]) if len(traj.t_s) > 1 else 1.0


def _sun_track(traj: Trajectory):
    """Subsolar point every ``_SUN_DT`` s of sim time, covering the whole run.

    Returns ``(lat_deg, lon_deg)`` lists rounded to 4 decimals; the longitude is
    unwrapped (continuous) so the player can interpolate it linearly. The player
    derives the terminator and the 3D shading from these.
    """
    dur = float(traj.t_s[-1])
    ts = np.arange(0.0, dur + _SUN_DT, _SUN_DT)
    t0 = _to_datetime(traj.utc[0])
    lat, lon = [], []
    for t in ts:
        la, lo = subsolar_point(t0 + timedelta(seconds=float(t)))
        lat.append(la)
        lon.append(lo)
    lon = np.degrees(np.unwrap(np.radians(lon)))
    return np.round(lat, 4).tolist(), np.round(lon, 4).tolist()


_N_LAT, _N_LON = 61, 121
_SURFACE_3D, _SHIP_3D = 0, 4


def _anim_payload(traj: Trajectory, view: str, step_s) -> dict:
    """Everything the JS player needs, kept small (rounded floats).

    ``t_s`` sample times (s since launch), ``t0_ms`` launch epoch (ms, UTC),
    ``step_s`` the user's time step, ``sun_dt``/``sun_lat``/``sun_lon`` the
    subsolar track, plus ``lon``/``lat`` (2D, deg) or ``x``/``y``/``z`` (3D,
    ECEF km) of the ship.
    """
    sun_lat, sun_lon = _sun_track(traj)
    t0_ms = int(traj.utc[0].astype('datetime64[ms]').astype('int64'))
    p = dict(view=view, t0_ms=t0_ms, step_s=_step_of(traj, step_s),
             t_s=np.round(traj.t_s, 3).tolist(), sun_dt=_SUN_DT,
             sun_lat=sun_lat, sun_lon=sun_lon,
             site=dict(zip(('lon', 'lat'), _site_lonlat(traj))))
    if view == '2d':
        p.update(lon=np.round(traj.lon_deg, 3).tolist(), lat=np.round(traj.lat_deg, 3).tolist(),
                 colors=dict(track=TRACK_COLOR, ship=SHIP_COLOR, ship_edge=theme.TEXT,
                             site=SITE_COLOR, night=theme.NIGHT_TINT,
                             terminator=theme.TERMINATOR))
    else:
        r = traj.r_ecef
        p.update(x=np.round(r[:, 0], 1).tolist(), y=np.round(r[:, 1], 1).tolist(),
                 z=np.round(r[:, 2], 1).tolist(),
                 shade=dict(n_lat=_N_LAT, n_lon=_N_LON, levels=_N_SHADES, twilight=_TWILIGHT,
                            earth=theme.EARTH, day=theme.DAY_3D),
                 ship_trace=_SHIP_3D, surface_trace=_SURFACE_3D)
    return p


def _build_2d(traj: Trajectory) -> go.Figure:
    """Static base map. Night, terminator, ground track, site and ship are drawn
    on a canvas overlay by the player; the empty dummy traces only carry the
    legend entries (and legend toggling)."""
    site_lon, site_lat = _site_lonlat(traj)
    b_lon, b_lat = [], []
    for line in load_borders():
        b_lon += line[:, 0].tolist() + [None]
        b_lat += line[:, 1].tolist() + [None]
    none = dict(lon=[None], lat=[None])
    traces = [
        go.Scattergeo(lon=[-180, -180, 180, 180, -180], lat=[-89.9, 89.9, 89.9, -89.9, -89.9],
                      mode='lines', fill='toself', fillcolor=theme.DAY_TINT,
                      line=dict(width=0), hoverinfo='skip', name='Day',
                      showlegend=False),
        go.Scattergeo(lon=b_lon, lat=b_lat, mode='lines',
                      line=dict(color=theme.BORDER_LINE, width=0.7),
                      hoverinfo='skip', name='Borders', showlegend=False),
        go.Scattergeo(**none, mode='lines',
                      line=dict(color=theme.TERMINATOR, width=1.2, dash='dash'),
                      hoverinfo='skip', name='Terminator'),
        go.Scattergeo(**none, mode='lines', line=dict(color=TRACK_COLOR, width=2),
                      name='Ground track', hoverinfo='skip'),
        go.Scattergeo(lon=[site_lon], lat=[site_lat], mode='markers',
                      marker=dict(color=SITE_COLOR, size=9, symbol='diamond'),
                      name='Launch site', text=[traj.site_name], hoverinfo='text'),
        go.Scattergeo(**none, mode='markers',
                      marker=dict(color=SHIP_COLOR, size=13, line=dict(color=theme.TEXT, width=2)),
                      name='Ship', hoverinfo='skip'),
    ]
    fig = go.Figure(data=traces)
    fig.update_geos(projection_type='equirectangular', showland=True, landcolor=theme.LAND_2D,
                    showlakes=True, lakecolor=theme.OCEAN, showocean=True, oceancolor=theme.OCEAN, showcoastlines=True,
                    coastlinecolor=theme.COASTLINE, coastlinewidth=0.8, showframe=False,
                    lonaxis=dict(range=[-180, 180], showgrid=True, gridcolor=theme.GRID),
                    lataxis=dict(range=[-90, 90], showgrid=True, gridcolor=theme.GRID),
                    bgcolor=_BG)
    fig.update_layout(
        paper_bgcolor=_BG, plot_bgcolor=_BG, font=dict(family=theme.FONT_FAMILY, color=theme.TEXT),
        margin=dict(l=10, r=10, t=10, b=44),
        legend=dict(bgcolor=theme.LEGEND_BG, font=dict(family=theme.FONT_FAMILY, color=theme.TEXT), x=0.01, y=0.98),
        uirevision='2d')
    return fig


def _sphere(n_lat: int = _N_LAT, n_lon: int = _N_LON):
    """Earth sphere grid (lats, lons in degrees; x, y, z in km, ECEF)."""
    lats = np.linspace(-90.0, 90.0, n_lat)
    lons = np.linspace(-180.0, 180.0, n_lon)
    la, lo = np.radians(lats)[:, None], np.radians(lons)[None, :]
    x = R_E * np.cos(la) * np.cos(lo)
    y = R_E * np.cos(la) * np.sin(lo)
    z = R_E * np.sin(la) * np.ones_like(lo)
    return lats, lons, np.round(x, 1), np.round(y, 1), np.round(z, 1)


def _lonlat_to_xyz(lon, lat, radius):
    lo, la = np.radians(lon), np.radians(lat)
    return (radius * np.cos(la) * np.cos(lo), radius * np.cos(la) * np.sin(lo),
            radius * np.sin(la))


def _build_3d(traj: Trajectory) -> go.Figure:
    """Static globe scene. The player moves trace ``_SHIP_3D`` and moves the light
    that shades trace ``_SURFACE_3D`` (day/night) as the subsolar point moves; only
    without the lighting hook does it recolour that surface instead."""
    site_lon, site_lat = _site_lonlat(traj)
    _, _, sx, sy, sz = _sphere()

    cx, cy, cz = [], [], []
    for line in load_coastlines():
        x, y, z = _lonlat_to_xyz(line[:, 0], line[:, 1], R_E * 1.002)
        cx += np.round(x, 1).tolist() + [None]
        cy += np.round(y, 1).tolist() + [None]
        cz += np.round(z, 1).tolist() + [None]
    bx, by, bz = [], [], []
    for line in load_borders():
        x, y, z = _lonlat_to_xyz(line[:, 0], line[:, 1], R_E * 1.001)
        bx += np.round(x, 1).tolist() + [None]
        by += np.round(y, 1).tolist() + [None]
        bz += np.round(z, 1).tolist() + [None]
    r = traj.r_ecef
    kx, ky, kz = _lonlat_to_xyz(site_lon, site_lat, R_E * 1.003)

    traces = [
        # Day/night comes from the lighting (see assets/playback.js), not from surfacecolor:
        # the player only moves the light, which costs nothing, while re-colouring the
        # surface takes ~100 ms. Night = ambient only (EARTH), subsolar point = DAY_3D.
        go.Surface(x=sx, y=sy, z=sz, surfacecolor=np.zeros_like(sx),
                   colorscale=[[0.0, theme.DAY_3D], [1.0, theme.DAY_3D]], cmin=0, cmax=1,
                   showscale=False, hoverinfo='skip', name='Earth', showlegend=False,
                   contours=dict(**{ax: dict(show=False, highlight=False) for ax in 'xyz'}),
                   lighting=dict(ambient=_AMBIENT, diffuse=_DIFFUSE, specular=0.0,
                                 roughness=_ROUGHNESS, fresnel=0.0)),
        go.Scatter3d(x=cx, y=cy, z=cz, mode='lines', line=dict(color=theme.COASTLINE_3D, width=2),
                     hoverinfo='skip', name='Coastlines', showlegend=False),
        go.Scatter3d(x=np.round(r[:, 0], 1).tolist(), y=np.round(r[:, 1], 1).tolist(),
                     z=np.round(r[:, 2], 1).tolist(), mode='lines',
                     line=dict(color=TRACK_COLOR, width=4), name='Orbit (Earth-fixed)',
                     hoverinfo='skip'),
        go.Scatter3d(x=[float(kx)], y=[float(ky)], z=[float(kz)], mode='markers',
                     marker=dict(color=SITE_COLOR, size=5, symbol='diamond'),
                     name='Launch site', text=[traj.site_name], hoverinfo='text'),
        go.Scatter3d(x=[round(float(r[0, 0]), 1)], y=[round(float(r[0, 1]), 1)],
                     z=[round(float(r[0, 2]), 1)], mode='markers',
                     marker=dict(color=SHIP_COLOR, size=6, line=dict(color=theme.TEXT, width=2)),
                     name='Ship', hoverinfo='skip'),
        go.Scatter3d(x=bx, y=by, z=bz, mode='lines',
                     line=dict(color=theme.BORDER_LINE_3D, width=1),
                     hoverinfo='skip', name='Borders', showlegend=False),
    ]
    fig = go.Figure(data=traces)
    ux, uy, uz = _lonlat_to_xyz(site_lon, site_lat, 1.0)
    axis = dict(visible=False, showspikes=False)
    fig.update_layout(
        paper_bgcolor=_BG, plot_bgcolor=_BG, font=dict(family=theme.FONT_FAMILY, color=theme.TEXT),
        margin=dict(l=0, r=0, t=0, b=0),
        scene=dict(aspectmode='data', domain=dict(x=[0, 1], y=[0, 1]), xaxis=axis, yaxis=axis, zaxis=axis, bgcolor=_BG,
                   camera=dict(eye=dict(x=_EYE * ux, y=_EYE * uy, z=_EYE * uz + _EYE_LIFT),
                               up=dict(x=0, y=0, z=1), center=dict(x=0, y=0, z=0))),
        legend=dict(bgcolor=theme.LEGEND_BG, font=dict(family=theme.FONT_FAMILY, color=theme.TEXT), x=0.01, y=0.98),
        uirevision='3d')
    return fig


def build_figure(traj: Trajectory, view: str = '2d', step_s: float | None = None) -> go.Figure:
    """Build the static figure for ``traj`` plus the player payload.

    ``traj`` is a ``Trajectory`` (see orbit.py). ``view`` is '2d'
    (equirectangular map, lon/lat in degrees) or '3d' (globe in the ECEF
    frame, km). ``step_s`` is the user's "Time step (s)" (the step buttons move
    by it; default: the first sample interval). The figure has no frames and no
    slider; ``fig.layout.meta['anim']`` holds the animation data (see
    ``_anim_payload``). Raises ValueError for any other ``view``.
    """
    view = view.lower()
    if view not in ('2d', '3d'):
        raise ValueError("view must be '2d' or '3d'")
    fig = _build_2d(traj) if view == '2d' else _build_3d(traj)
    fig.update_layout(meta=dict(anim=_anim_payload(traj, view, step_s)))
    return fig
