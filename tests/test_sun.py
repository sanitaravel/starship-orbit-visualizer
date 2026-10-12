from datetime import datetime, timezone

import numpy as np
import pytest

from starship_viz.sun import (gmst_rad, julian_date, night_polygon,
                              solar_elevation_grid, subsolar_point, terminator_line)


def test_gmst_j2000():
    dt = datetime(2000, 1, 1, 12, tzinfo=timezone.utc)
    assert julian_date(dt) == 2451545.0
    assert np.degrees(gmst_rad(dt)) == pytest.approx(280.46, abs=0.01)


def test_subsolar_solstice():
    lat, lon = subsolar_point(datetime(2024, 6, 20, 20, 51, tzinfo=timezone.utc))
    assert lat == pytest.approx(23.44, abs=0.05)
    assert -180 <= lon < 180


def test_subsolar_noon_lon_near_zero():
    # Equation of time is within ~16 min => lon within ~4 deg of 0 at 12:00 UT
    _, lon = subsolar_point(datetime(2024, 3, 20, 12, tzinfo=timezone.utc))
    assert abs(lon) < 4.5


def test_elevation_max_at_subsolar():
    dt = datetime(2024, 9, 1, 6, tzinfo=timezone.utc)
    lat, lon = subsolar_point(dt)
    g = solar_elevation_grid(dt, [lat, lat + 30], [lon, lon + 90, lon + 180])
    assert g.shape == (2, 3)
    assert g[0, 0] == pytest.approx(1.0, abs=1e-9)
    assert g[0, 2] < 0


def test_terminator_and_polygon():
    dt = datetime(2024, 12, 21, 0, tzinfo=timezone.utc)
    lons, lats = terminator_line(dt, 361)
    assert lons.shape == lats.shape == (361,) and np.all(np.isfinite(lats))
    rl, rt = night_polygon(dt)
    assert (rl[0], rt[0]) == (rl[-1], rt[-1])
    assert 90.0 in rt  # dec < 0 in December -> north pole dark
    equinox = datetime(2024, 3, 20, 3, 6, tzinfo=timezone.utc)
    assert np.all(np.isfinite(terminator_line(equinox)[1]))


def test_naive_assumed_utc():
    assert julian_date(datetime(2000, 1, 1, 12)) == 2451545.0
