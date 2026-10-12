"""Natural Earth 110m coastline and country-border loaders.

Coordinates are geodetic (longitude, latitude) in degrees, lon in [-180, 180].
"""
from __future__ import annotations

import json
from functools import cache
from pathlib import Path

import numpy as np

_DATA_DIR = Path(__file__).parent / 'data'
_DATA_FILE = _DATA_DIR / 'coastlines_110m.json'
_BORDERS_FILE = _DATA_DIR / 'borders_110m.json'


def _load_lines(path: Path) -> tuple[np.ndarray, ...]:
    """Read ``{"source": ..., "lines": [[[lon, lat], ...], ...]}`` from ``path``."""
    with open(path, encoding='utf-8') as fh:
        lines = json.load(fh)['lines']
    return tuple(np.asarray(line, dtype=float) for line in lines)


@cache
def load_coastlines() -> tuple[np.ndarray, ...]:
    """Return coastline polylines as a tuple of (n, 2) arrays of [lon, lat] degrees."""
    return _load_lines(_DATA_FILE)


@cache
def load_borders() -> tuple[np.ndarray, ...]:
    """Return country-border polylines as a tuple of (n, 2) arrays of [lon, lat] degrees."""
    return _load_lines(_BORDERS_FILE)
