"""Central colour palette shared by the Dash app and the Plotly figures.

``assets/style.css`` mirrors these values as CSS custom properties.
"""
ACCENT = '#FF8014'          # ship, ground track, buttons, slider, highlights
ACCENT_2 = '#CF0000'        # launch site, errors, warnings
TEXT = '#FEFEFE'
BACKGROUND = '#262626'
EARTH = '#090909'

SIDEBAR_BG = '#202020'      # slightly darker than BACKGROUND
BORDER = '#4a4a4a'          # subtle borders
FIELD_BG = '#2e2e2e'        # inputs, dropdown, date picker
OCEAN = '#1e1e1e'           # 2D sea (day look), darker than LAND_2D
LAND_2D = '#3c3c3c'         # 2D land (day look); the night overlay darkens it
COASTLINE = '#c8c8c8'
COASTLINE_3D = '#8a8a8a'    # brighter so it reads on the DAY_3D side
BORDER_LINE = '#686868'     # 2D country borders, dimmer than coastlines
BORDER_LINE_3D = '#5e5e5e'  # 3D country borders
FONT_FAMILY = 'JetBrains Mono, monospace'
GRID = '#3a3a3a'
DAY_TINT = 'rgba(254,254,254,0.3)'      # 2D day-side overlay colour
NIGHT_TINT = 'rgba(0,0,0,0.45)'          # 2D night-side overlay colour
TERMINATOR = 'rgba(254,254,254,0.75)'
DAY_3D = '#3a3a3a'          # 3D globe day colour (night is EARTH)
LEGEND_BG = 'rgba(38,38,38,0.8)'
CONTROL_BG = '#3a3a3a'      # Plotly Play/Pause and slider track
CONTROL_BORDER = BORDER  # Plotly Play/Pause button border
MODEBAR_BG = 'rgba(32,32,32,0.85)'
