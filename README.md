# Starship trajectories

An interactive Dash/Plotly web app that visualizes the ground track of an idealized circular
orbit launched from one of four Starship-related launch sites, on a 2D map or a 3D globe, with
an animated day/night terminator.

This is a visualization tool, not a mission-design tool: see "Physics model and assumptions".

## Features

- Four launch sites: Starbase, TX; Cape Canaveral, FL; Starbase, LA; Vandenberg, CA.
- Orbit specified by either launch azimuth or inclination. For inclination you also choose
  northbound or southbound. Switching between the two modes converts the entered value.
- Altitude (100 to 2000 km), number of orbits (decimals allowed, greater than 0 and at most 16, e.g. 0.25 or 2.5), launch date and time in UTC,
  and sample time step (1 to 600 s).
- 2D equirectangular map or 3D globe view.
- Playback bar along the bottom of the plot: step back, play/pause, step forward, a time slider with
  a `UTC YYYY-MM-DD HH:MM:SS` label, and a speed selector (30x, 60x, 120x, 300x, 600x, 1200x;
  default 300x, i.e. 300 simulated seconds per real second).
  - The play button shows ▶ when paused and ⏸ while playing. Playback runs at the display
    refresh rate (60 FPS) in continuous simulation time; the ship is interpolated between samples.
  - It switches back to ▶ on reaching the end, and pressing it at the end restarts from the beginning.
  - Dragging the slider sets the time and pauses. The step buttons move by the "Time step (s)"
    value (clamped at both ends) and pause playback.
  - A newly built graph (any parameter change) starts paused at the beginning. The chosen speed is kept.
  - The ship, the day/night shading and terminator, and the UTC label always show the same instant.
- Derived values shown in the side panel: inclination, azimuth, nodal period, total duration.
- Invalid input is reported inline; the previous valid figure stays on screen, dimmed.
- Runs entirely locally (127.0.0.1); the Plotly cloud/upload buttons are removed.

## Setup (Windows)

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

Requirements: numpy, dash, plotly, pytest (see `requirements.txt`; minimum versions are those the project was tested with: numpy 2.5.4, dash 4.4.1, plotly 7.1.0, pytest 9.1.1).

## Run

```
.venv\Scripts\python app.py
```

Then open http://127.0.0.1:8050. Add `--debug` (`.venv\Scripts\python app.py --debug`) to run
Dash in debug mode; it is off by default.

## Tests

```
.venv\Scripts\python -m pytest -q
```

## Physics model and assumptions

Units: distances km, times s, angles in degrees at the public API (radians internally),
rates rad/s. Constants: mu = 398600.4418 km^3/s^2, R_E = 6378.137 km (Earth treated as a
sphere of this radius), J2 = 1.08262668e-3.

Reference frames:
- ECI: inertial equatorial frame, z to the north pole, x to the vernal equinox (no precession
  or nutation modelling).
- ECEF: Earth-fixed, x to longitude 0 / latitude 0, z to the north pole; ECEF is obtained from
  ECI by rotating about z by the Greenwich mean sidereal time (GMST).
- Longitude is east-positive, wrapped to [-180, 180). Azimuth is clockwise from north.

Model:
- Circular orbit at constant altitude above the R_E sphere.
- Secular J2 rates only (no short-period terms, no drag, no other perturbations). The RAAN rate,
  argument-of-perigee rate and mean-anomaly rate are computed for a circular orbit; their sum is
  the argument-of-latitude rate u_dot.
- One "orbit" is one nodal (draconitic) period, 2*pi / u_dot. A run lasts
  (number of orbits) x (nodal period).
- Launch azimuth is the inertial azimuth: cos i = cos(lat) sin(Az). The Earth-rotation
  correction to launch azimuth is ignored.
- No ascent phase: the ship starts at t = 0 exactly at the launch site's latitude/longitude, at
  the chosen altitude, already in orbit. The initial RAAN is set so the orbit passes over the
  site at the launch time.
- GMST uses the IAU-82 / Vallado polynomial; UTC is treated as UT1.
- Sun position uses the low-precision Astronomical Almanac formulae. The terminator is where
  the solar elevation is zero (no refraction). It is for display only.
- Launch-site latitude/longitude are used as spherical coordinates (no ellipsoid or
  geodetic-to-geocentric conversion). Naive datetimes are assumed to be UTC.

Reachable inclinations: from a site at latitude phi, an inclination i is reachable only if
|phi| <= i <= 180 - |phi| (degrees). Outside this range the app shows an error giving the
minimum and maximum. In azimuth mode, due east (90 deg) gives i = |phi|.

## Animation architecture

Plotly frames and the Plotly slider are not used (each frame re-rendered the whole geo/3D scene
and put megabytes into the figure JSON). Instead:

- `figures.build_figure` builds the static scene and puts the animation data once into
  `fig.layout.meta['anim']`: sample times `t_s`, the launch epoch `t0_ms`, the time step, the ship
  track (`lon`/`lat` in 2D, ECEF `x`/`y`/`z` in 3D, rounded) and the subsolar point every 60 s of
  simulated time (`sun_lat`/`sun_lon`, longitude unwrapped). The 3 orbit default figure is about 65 kB (2D)
  and 0.54 MB (3D) instead of 3.5 MB and 2.5 MB.
- `assets/playback.js` is the player: a `requestAnimationFrame` loop advancing the simulation time by
  real dt x speed. The terminator and the 3D sun are derived in the browser from the interpolated
  subsolar point with the same formulas as `starship_viz/sun.py`.
- 2D: Plotly draws only the base map (land, ocean, coastlines, borders) and the legend (empty dummy
  traces carry the legend entries, and toggling them hides the matching layer). A canvas overlay
  (`pointer-events: none`) placed exactly over the geo subplot draws the night shading, terminator,
  ground track (flown part bright), launch site and ship. It re-projects after zoom/pan, resize and
  `devicePixelRatio` changes and splits paths at the cut meridian.
- 3D: only the ship trace is updated, directly on its gl object (`Plotly.restyle` would re-convert and
  re-upload every trace, about 100 ms; the player falls back to it if the Plotly internals change).
  Day/night is done with lighting instead of re-colouring the surface (about 60 ms+ per update): the
  night side is the ambient colour, the sun is a light fixed in the Earth frame, and moving it costs
  nothing. The globe renders at the device pixel ratio rather than Plotly's default 2x, and the
  per-frame picking pass is skipped for ship updates. Without the needed Plotly internals the player
  falls back to re-colouring the surface at a throttled rate.
- Physics is unchanged: `orbit.py` and `sun.py` are not touched; the player only interpolates their output.

## Project layout

```
app.py                       Dash app: layout, input validation, callbacks
assets/style.css             Dash page styling, @font-face rules, playback bar, Plotly modebar theming
assets/playback.js           Client-side player: rAF loop, 2D canvas overlay, 3D ship/sun updates, controls
assets/fonts/                Bundled JetBrains Mono woff2 files and OFL licence text
requirements.txt
starship_viz/
  sites.py                   Launch site coordinates
  orbit.py                   J2 rates, azimuth/inclination conversion, propagation, Trajectory
  sun.py                     Julian date, GMST, subsolar point, terminator, night polygon (the browser
                             player mirrors the terminator formulas)
  coastlines.py              Loads the bundled coastline and country-border data
  figures.py                 Static Plotly 2D map / 3D globe figures plus the player payload (layout.meta)
  data/coastlines_110m.json  Natural Earth 110m coastlines
  data/borders_110m.json     Natural Earth 110m country borders
tests/                       pytest tests (orbit, sun, figures, app)
```

## Data attribution

Coastlines and country borders come from [Natural Earth](https://www.naturalearthdata.com/)
110m data, which is in the public domain.

The UI font is [JetBrains Mono](https://www.jetbrains.com/lp/mono/), bundled in `assets/fonts/`
(served locally, no CDN) under the SIL Open Font License 1.1; see
`assets/fonts/JetBrainsMono-OFL.txt`.

## Contributing

Ideas and bug reports are welcome via [GitHub Issues](https://github.com/sanitaravel/starship-orbit-visualizer/issues/new/choose).
See [CONTRIBUTING.md](CONTRIBUTING.md) for details.
