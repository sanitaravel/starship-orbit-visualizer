"""Dash app: launch-site orbit visualizer. Run with ``python app.py``.

Serves on http://127.0.0.1:8050. Debug mode is off unless ``--debug`` is passed
on the command line. Launch time is entered in UTC. Angles in degrees,
altitude in km, time step in seconds.
"""
from __future__ import annotations

import math
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import numpy as np
from dash import Dash, Input, Output, State, dcc, html, no_update

from starship_viz import theme
from starship_viz.figures import build_figure
from starship_viz.orbit import (azimuth_from_inclination, compute_trajectory,
                                inclination_from_azimuth, orbital_period_s)
from starship_viz.sites import LAUNCH_SITES

DEFAULT_SITE = 'Starbase, TX'
DEFAULT_AZIMUTH = 95.0
ALT_RANGE = (100.0, 2000.0)
ORBIT_RANGE = (0.1, 16)   # decimals allowed; value must be > 0 (UI minimum 0.1)
ORBIT_MAX = ORBIT_RANGE[1]
STEP_RANGE = (1.0, 600.0)
MAX_SAMPLES = 50000

PANEL_STYLE = {'width': '320px', 'padding': '12px', 'overflowY': 'auto',
               'background': theme.SIDEBAR_BG, 'color': theme.TEXT,
               'borderRight': f'1px solid {theme.BORDER}', 'height': '100vh',
               'boxSizing': 'border-box', 'fontFamily': theme.FONT_FAMILY, 'fontSize': '14px'}
BOX_STYLE = {'flex': '1', 'minWidth': 0}
FIELD = {'marginBottom': '12px'}
INPUT = {'width': '100%', 'boxSizing': 'border-box', 'color': theme.TEXT, 'background': theme.FIELD_BG,
         'border': f'1px solid {theme.BORDER}', 'borderRadius': '4px', 'padding': '4px 6px'}
RADIO_LABEL = {'color': theme.TEXT, 'marginRight': '12px'}
# No data may leave the browser: hide every chart-upload / cloud button.
GRAPH_CONFIG = {'displaylogo': False, 'displayModeBar': True, 'showSendToCloud': False,
                'showEditInChartStudio': False, 'plotlyServerURL': '',
                'modeBarButtonsToRemove': ['sendChartToCloud', 'sendDataToCloud',
                                           'editInChartStudio']}


def validate_inputs(site, mode, value, direction, alt_km, n_orbits, date_str,
                    time_str, step_s, view='2d'):
    """Validate raw UI values.

    Args:
        site: key of ``LAUNCH_SITES``.
        mode: 'azimuth' or 'inclination'.
        value: azimuth in [0, 360] deg (inertial, clockwise from north) or
            inclination in [0, 180] deg, depending on ``mode``.
        direction: 'north' or 'south'; only checked in inclination mode.
        alt_km: altitude above the R_E sphere, km, within ``ALT_RANGE``.
        n_orbits: number of nodal periods (decimals allowed, e.g. 2.5), > 0 and
            at most 16.
        date_str: ISO date 'YYYY-MM-DD' (first 10 characters are used), UTC.
        time_str: 'HH:MM:SS' or 'HH:MM', UTC.
        step_s: sample interval, s, within ``STEP_RANGE``.
        view: '2d' or '3d'.

    Returns ``(params, error)``: ``params`` is the kwargs dict for
    ``compute_trajectory`` (or None) and ``error`` a message (or None). Also
    rejects inclinations unreachable from the site latitude and runs that
    would exceed ``MAX_SAMPLES`` samples (duration / step_s).
    """
    if site not in LAUNCH_SITES:
        return None, 'Select a launch site.'
    if mode not in ('azimuth', 'inclination'):
        return None, 'Select azimuth or inclination.'
    if view not in ('2d', '3d'):
        return None, 'Select 2D or 3D view.'
    label = 'Azimuth' if mode == 'azimuth' else 'Inclination'
    if value is None:
        return None, f'{label} is required.'
    if mode == 'azimuth' and not 0 <= value <= 360:
        return None, 'Azimuth must be between 0 and 360 degrees.'
    if mode == 'inclination' and not 0 <= value <= 180:
        return None, 'Inclination must be between 0 and 180 degrees.'
    lat = LAUNCH_SITES[site]['lat']
    if mode == 'inclination':
        if direction not in ('north', 'south'):
            return None, 'Select northbound or southbound.'
        try:
            azimuth_from_inclination(lat, value, direction)
        except ValueError as exc:
            return None, str(exc)
    if alt_km is None or not ALT_RANGE[0] <= alt_km <= ALT_RANGE[1]:
        return None, f'Altitude must be between {ALT_RANGE[0]:g} and {ALT_RANGE[1]:g} km.'
    if n_orbits is None:
        return None, 'Number of orbits is required.'
    if (not isinstance(n_orbits, (int, float)) or isinstance(n_orbits, bool)
            or not math.isfinite(n_orbits)):
        return None, 'Number of orbits must be a number.'
    if n_orbits <= 0:
        return None, 'Number of orbits must be greater than 0.'
    if n_orbits > ORBIT_MAX:
        return None, f'Number of orbits must be at most {ORBIT_MAX:g}.'
    if step_s is None or not STEP_RANGE[0] <= step_s <= STEP_RANGE[1]:
        return None, f'Time step must be between {STEP_RANGE[0]:g} and {STEP_RANGE[1]:g} s.'
    try:
        day = date.fromisoformat((date_str or '')[:10])
    except ValueError:
        return None, 'Pick a valid launch date.'
    t_text = (time_str or '').strip()
    for fmt in ('%H:%M:%S', '%H:%M'):
        try:
            clock = datetime.strptime(t_text, fmt).time()
            break
        except ValueError:
            clock = None
    if clock is None:
        return None, 'Launch time must be HH:MM:SS (UTC).'
    t0 = datetime.combine(day, clock, tzinfo=timezone.utc)
    inc = (inclination_from_azimuth(lat, value) if mode == 'azimuth' else value)
    duration = n_orbits * orbital_period_s(alt_km, inc)
    if duration / step_s > MAX_SAMPLES:
        return None, 'Too many samples: increase the time step or reduce the orbits.'
    return dict(site_name=site, mode=mode, value_deg=float(value), direction=direction,
                alt_km=float(alt_km), n_orbits=float(n_orbits), t0_utc=t0,
                step_s=float(step_s)), None


def _labeled(text, component):
    """Wrap a Dash input ``component`` in a Div with a label ``text`` above it."""
    return html.Div([html.Label(text, style={'display': 'block', 'marginBottom': '4px'}),
                     component], style=FIELD)


def make_layout():
    """Build the page layout (called per page load, so the default launch date
    and time are the current UTC time, truncated to the minute)."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return html.Div(style={'display': 'flex', 'background': theme.BACKGROUND}, children=[
        html.Div(style=PANEL_STYLE, children=[
            html.H3('Orbit visualizer', style={'marginTop': 0}),
            _labeled('Launch site', dcc.Dropdown(
                id='site', options=list(LAUNCH_SITES), value=DEFAULT_SITE, clearable=False,
                className='themed-dropdown')),
            _labeled('Specify', dcc.RadioItems(
                id='mode', options=[{'label': ' Azimuth', 'value': 'azimuth'},
                                    {'label': ' Inclination', 'value': 'inclination'}],
                value='azimuth', inline=True, inputStyle={'marginLeft': '10px'}, labelStyle=RADIO_LABEL)),
            _labeled('Value (deg)', dcc.Input(
                id='value', type='number', value=DEFAULT_AZIMUTH, debounce=True, style=INPUT)),
            html.Div(id='direction-box', style={'display': 'none'}, children=_labeled(
                'Direction', dcc.RadioItems(
                    id='direction', options=[{'label': ' Northbound', 'value': 'north'},
                                             {'label': ' Southbound', 'value': 'south'}],
                    value='north', inline=True, inputStyle={'marginLeft': '10px'}, labelStyle=RADIO_LABEL))),
            _labeled('Altitude (km)', dcc.Input(
                id='alt', type='number', value=200, min=ALT_RANGE[0], max=ALT_RANGE[1],
                debounce=True, style=INPUT)),
            _labeled('Number of orbits', dcc.Input(
                id='orbits', type='number', value=3, min=ORBIT_RANGE[0], max=ORBIT_MAX,
                step='any', debounce=True, style=INPUT)),
            _labeled('Launch date (UTC)', dcc.DatePickerSingle(
                id='date', date=now.date().isoformat(), display_format='YYYY-MM-DD')),
            _labeled('Launch time (UTC, HH:MM:SS)', dcc.Input(
                id='time', type='text', value=now.strftime('%H:%M:%S'), debounce=True,
                style=INPUT)),
            _labeled('View', dcc.RadioItems(
                id='view', options=[{'label': ' 2D', 'value': '2d'},
                                    {'label': ' 3D', 'value': '3d'}],
                value='2d', inline=True, inputStyle={'marginLeft': '10px'}, labelStyle=RADIO_LABEL)),
            _labeled('Time step (s)', dcc.Input(
                id='step', type='number', value=30, min=STEP_RANGE[0], max=STEP_RANGE[1],
                debounce=True, style=INPUT)),
            html.Div(id='error', style={'color': theme.ACCENT_2, 'marginBottom': '12px'}),
            html.Div(id='derived'),
        ]),
        html.Div(style={'flex': '1', 'minWidth': 0, 'position': 'relative'}, children=[
            # A brand-new dcc.Graph (unique id) is rendered into this container on
            # every update; see ``_new_graph``.
            html.Div(id='graph-box', style=BOX_STYLE),
            # Playback bar, driven by assets/playback.js (event delegation): step back,
            # play/pause, step forward, time scrubber, UTC label, speed selector.
            html.Div(className='playback', children=[
                html.Button('⏮︎', id='pb-back', className='pb-btn', n_clicks=0,
                            title='Step back', **{'aria-label': 'Step back'}),
                html.Button('▶︎', id='pb-toggle', className='pb-btn', n_clicks=0,
                            title='Play', **{'aria-label': 'Play'}),
                html.Button('⏭︎', id='pb-fwd', className='pb-btn', n_clicks=0,
                            title='Step forward', **{'aria-label': 'Step forward'}),
                # Filled in by playback.js (a React-controlled select/range without a
                # callback would be reverted on every user change).
                html.Div(id='pb-speed-box', className='pb-speed'),
                html.Div(id='pb-range-box', className='pb-range-box'),
                html.Div(id='pb-utc', className='pb-utc'),
            ]),
        ]),
    ])


dash_app = Dash(__name__, title='Starship trajectories')
dash_app.layout = make_layout
# Flask WSGI server: the module-level `app` that Vercel (and any WSGI host) loads.
app = dash_app.server


@dash_app.callback(Output('direction-box', 'style'), Input('mode', 'value'))
def toggle_direction(mode):
    """Show the direction control only in inclination mode."""
    return {'display': 'block'} if mode == 'inclination' else {'display': 'none'}


def convert_mode_value(mode, value, site, direction):
    """Convert the entered value to the quantity of the newly selected mode.

    ``mode`` is the NEW mode; ``value`` (deg) is the number currently entered,
    interpreted in the old mode: an inertial azimuth in [0, 360] when the new
    mode is 'inclination', an inclination in [0, 180] when it is 'azimuth'.
    ``site`` is a ``LAUNCH_SITES`` key and ``direction`` the current
    'north'/'south' selection (used for inclination -> azimuth). Results are in
    degrees.

    Returns ``(new_value, new_direction)``; ``no_update`` entries mean "keep".
    A failed inclination->azimuth conversion yields ``None`` (cleared value) so
    the old number is never kept under the new meaning. The inclination
    produced from an azimuth is rounded to 2 decimals and clamped into the
    reachable interval [|lat|, 180 - |lat|] (rounded inward).
    """
    if value is None or site not in LAUNCH_SITES:
        return no_update, no_update
    lat = LAUNCH_SITES[site]['lat']
    if mode == 'inclination':
        if not 0 <= value <= 360:
            return no_update, no_update
        north = bool(np.cos(np.radians(value)) >= -1e-9)
        inc = round(inclination_from_azimuth(lat, value), 2)
        lo = math.ceil(abs(lat) * 100.0 - 1e-9) / 100.0
        hi = math.floor((180.0 - abs(lat)) * 100.0 + 1e-9) / 100.0
        return min(max(inc, lo), hi), 'north' if north else 'south'
    try:
        return round(azimuth_from_inclination(lat, value, direction or 'north'), 2), no_update
    except ValueError:
        return None, no_update


@dash_app.callback(Output('value', 'value'), Output('direction', 'value'),
                   Input('mode', 'value'), State('value', 'value'), State('site', 'value'),
                   State('direction', 'value'), prevent_initial_call=True)
def convert_value(mode, value, site, direction):
    """On a mode switch, convert the entered value to the new quantity."""
    return convert_mode_value(mode, value, site, direction)


def _new_graph(fig):
    """A fresh dcc.Graph with a unique id.

    Replacing the component (instead of updating ``figure`` of a long-lived
    one) unmounts the old graph, so its player (assets/playback.js) is dropped
    and the new graph always starts paused at t = 0.
    """
    return dcc.Graph(id={'type': 'graph', 'uid': uuid.uuid4().hex}, figure=fig,
                     style={'height': '100vh'}, config=GRAPH_CONFIG)


@dash_app.callback(Output('graph-box', 'children'), Output('graph-box', 'style'),
                   Output('error', 'children'), Output('derived', 'children'),
                   Input('site', 'value'), Input('mode', 'value'), Input('value', 'value'),
                   Input('direction', 'value'), Input('alt', 'value'), Input('orbits', 'value'),
                   Input('date', 'date'), Input('time', 'value'), Input('view', 'value'),
                   Input('step', 'value'), State('graph-box', 'children'))
def update(site, mode, value, direction, alt_km, n_orbits, date_str, time_str, view, step_s,
           previous):
    """Rebuild the figure on any parameter change; show errors inline."""
    params, error = validate_inputs(site, mode, value, direction, alt_km, n_orbits,
                                    date_str, time_str, step_s, view)
    fig = None
    if not error:
        try:
            traj = compute_trajectory(**params)
            fig = build_figure(traj, view, params["step_s"])
        except ValueError as exc:
            error = str(exc)
    if error:
        # Keep the previous figure (if any) but dim it and clear stale values.
        if previous:
            return no_update, {**BOX_STYLE, 'opacity': 0.35},                 f'{error} (showing previous valid result)', []
        return no_update, BOX_STYLE, error, []
    hl = {'color': theme.ACCENT, 'fontWeight': 'bold'}
    derived = html.Table([
        html.Tr([html.Td('Inclination'), html.Td(f'{traj.inc_deg:.2f} deg', style=hl)]),
        html.Tr([html.Td('Azimuth'), html.Td(f'{traj.az_deg:.2f} deg', style=hl)]),
        html.Tr([html.Td('Nodal period'), html.Td(f'{traj.period_s / 60:.2f} min', style=hl)]),
        html.Tr([html.Td('Duration'),
                 html.Td(str(timedelta(seconds=round(float(traj.t_s[-1])))), style=hl)]),
        html.Tr([html.Td('Orbits'), html.Td(f'{params["n_orbits"]:g}', style=hl)]),
    ], style={'borderSpacing': '8px 2px'})
    return _new_graph(fig), BOX_STYLE, '', derived


if __name__ == '__main__':
    dash_app.run(host='127.0.0.1', port=8050, debug='--debug' in sys.argv)
