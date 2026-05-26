"""
IATA code -> (lat, lon, country) lookup + haversine distance.

Limited to ~35 major airports covering most business-travel routes for the
demo. Source coordinates: openflights.org airport database (CC-BY-SA 3.0).

Production would import the full ~7000-airport dataset (also openflights, or
OAG, or Cirium). Documented as a tradeoff in SOURCES.md.
"""
from __future__ import annotations

from math import atan2, cos, radians, sin, sqrt


# IATA -> (latitude, longitude, country_iso2)
AIRPORTS: dict[str, tuple[float, float, str]] = {
    # Germany
    "FRA": (50.0379, 8.5622, "DE"),
    "MUC": (48.3537, 11.7750, "DE"),
    "BER": (52.3667, 13.5033, "DE"),
    "HAM": (53.6304, 9.9882, "DE"),
    # UK + Ireland
    "LHR": (51.4700, -0.4543, "GB"),
    "LGW": (51.1481, -0.1903, "GB"),
    "DUB": (53.4213, -6.2701, "IE"),
    # Rest of Europe
    "CDG": (49.0097, 2.5479, "FR"),
    "AMS": (52.3105, 4.7683, "NL"),
    "MAD": (40.4936, -3.5668, "ES"),
    "BCN": (41.2974, 2.0833, "ES"),
    "FCO": (41.8003, 12.2389, "IT"),
    "MXP": (45.6306, 8.7281, "IT"),
    "ZRH": (47.4647, 8.5492, "CH"),
    "VIE": (48.1103, 16.5697, "AT"),
    "BRU": (50.9014, 4.4844, "BE"),
    "IST": (41.2753, 28.7519, "TR"),
    # Middle East
    "DXB": (25.2532, 55.3657, "AE"),
    # North America
    "JFK": (40.6413, -73.7781, "US"),
    "EWR": (40.6925, -74.1687, "US"),
    "LGA": (40.7769, -73.8740, "US"),
    "ORD": (41.9742, -87.9073, "US"),
    "LAX": (33.9416, -118.4085, "US"),
    "SFO": (37.6213, -122.3790, "US"),
    "DFW": (32.8998, -97.0403, "US"),
    "ATL": (33.6407, -84.4277, "US"),
    "BOS": (42.3656, -71.0096, "US"),
    "IAD": (38.9531, -77.4565, "US"),
    "SEA": (47.4502, -122.3088, "US"),
    "YYZ": (43.6777, -79.6248, "CA"),
    "MEX": (19.4361, -99.0719, "MX"),
    # Asia + Pacific
    "GRU": (-23.4356, -46.4731, "BR"),
    "NRT": (35.7720, 140.3929, "JP"),
    "HND": (35.5494, 139.7798, "JP"),
    "HKG": (22.3080, 113.9185, "HK"),
    "SIN": (1.3644, 103.9915, "SG"),
    "BKK": (13.6900, 100.7501, "TH"),
    "SYD": (-33.9461, 151.1772, "AU"),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km. Earth radius 6371 km (mean)."""
    R = 6371.0
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def distance_between_iata(origin: str, destination: str) -> float | None:
    """Returns great-circle km, or None if either IATA code is unknown to us."""
    a = AIRPORTS.get(origin.upper())
    b = AIRPORTS.get(destination.upper())
    if a is None or b is None:
        return None
    return haversine_km(a[0], a[1], b[0], b[1])
