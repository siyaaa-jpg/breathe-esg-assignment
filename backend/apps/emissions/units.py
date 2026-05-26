"""
Tiny unit-conversion table. Just enough for v1 sources.

Adding a new unit:
  1. Add its case-normalized aliases to UNIT_ALIASES (lowercase keys).
  2. Add a (from_canonical, to_canonical) -> multiplier row to CONVERSIONS.

Why not a real units library (Pint, etc.): a real library is overkill for ~10
units and adds a dependency that's hard to defend in a 4-day prototype. The
cost: we miss exotic conversions (therms to kWh, kJ to kWh) — those become
`unit_unknown` flags the analyst handles manually. Documented in TRADEOFFS.md.
"""
from __future__ import annotations

from decimal import Decimal


# Various source-system spellings -> internal canonical token (lowercase).
UNIT_ALIASES: dict[str, str] = {
    # electricity
    "kwh": "kwh", "kw-h": "kwh", "kw/h": "kwh",
    "mwh": "mwh", "mw-h": "mwh",
    "wh":  "wh",
    # liquid volume
    "l": "liter", "ltr": "liter", "liter": "liter", "litre": "liter",
    "m3": "m3", "m^3": "m3", "cubic_meter": "m3",
    "us_gal": "us_gal", "usgal": "us_gal", "usg": "us_gal", "gal": "us_gal", "us_gallon": "us_gal",
    # distance
    "km": "km", "kilometer": "km", "kilometre": "km",
    "mi": "mi", "mile": "mi",
    # pass-through types (already canonical, but listed so the alias lookup succeeds)
    "night": "night", "nights": "night",
    "passenger_km": "passenger_km", "pkm": "passenger_km",
}


# (from_canonical, to_canonical) -> multiplier
CONVERSIONS: dict[tuple[str, str], Decimal] = {
    # electricity -> kwh
    ("kwh", "kwh"): Decimal("1"),
    ("mwh", "kwh"): Decimal("1000"),
    ("wh", "kwh"): Decimal("0.001"),
    # volume -> liter
    ("liter", "liter"): Decimal("1"),
    ("m3", "liter"): Decimal("1000"),
    ("us_gal", "liter"): Decimal("3.785411784"),
    # gas (often reported in m3 directly, no conversion)
    ("m3", "m3"): Decimal("1"),
    # distance -> km
    ("km", "km"): Decimal("1"),
    ("mi", "km"): Decimal("1.609344"),
    # pass-through types
    ("night", "night"): Decimal("1"),
    ("passenger_km", "passenger_km"): Decimal("1"),
}


def normalize(value: Decimal, from_unit_raw: str, to_unit: str) -> tuple[Decimal | None, str]:
    """
    Convert `value` from `from_unit_raw` (any alias) to `to_unit` (canonical).
    Both inputs are lowercased defensively. Returns (converted_value, to_unit)
    or (None, to_unit) if conversion is impossible.
    """
    to_unit_norm = to_unit.strip().lower()
    from_canonical = UNIT_ALIASES.get(from_unit_raw.strip().lower())
    if from_canonical is None:
        return None, to_unit_norm
    factor = CONVERSIONS.get((from_canonical, to_unit_norm))
    if factor is None:
        return None, to_unit_norm
    return value * factor, to_unit_norm
