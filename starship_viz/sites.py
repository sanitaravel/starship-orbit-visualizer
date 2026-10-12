"""Launch sites: name -> {'lat': latitude, 'lon': east longitude}, degrees.

Values are as listed (geodetic, Earth-fixed); the orbit model treats them as
spherical latitude/longitude on a sphere of radius R_E (no ellipsoid).
"""

LAUNCH_SITES = {
    'Starbase, TX': {'lat': 25.99677200181146, 'lon': -97.15807367983918},
    'Cape Canaveral, FL': {'lat': 28.5721, 'lon': -80.5828},
    'Starbase, LA': {'lat': 29.549142673771335, 'lon': -92.31086843688368},
    'Vandenberg, CA': {'lat': 34.70607684027935, 'lon': -120.58887525432736},
}
