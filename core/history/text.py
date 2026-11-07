"""Shared formatting for history text. Coordinates and a VIN are not fields."""

from __future__ import annotations

import json

_BANNED = {
    "latitude",
    "longitude",
    "lat",
    "lon",
    "lng",
    "vin",
    "heading",
    "polyline",
    "native_latitude",
    "native_longitude",
    "gps",
    "coordinate",
    "coordinates",
}


def _banned(key: str) -> bool:
    folded = str(key).lower()
    if folded in _BANNED:
        return True
    return any(part in folded for part in ("latitude", "longitude", "polyline", "breadcrumb", "coordinate"))


def _strip(value):
    if isinstance(value, dict):
        return {str(key): _strip(item) for key, item in value.items() if not _banned(str(key))}
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def dumps(payload) -> str:
    return json.dumps(_strip(payload), indent=2)


def fmt_km(value) -> str:
    if value is None:
        return "?"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    if abs(number - round(number)) < 0.05:
        return f"{round(number):.0f} km"
    return f"{number:.1f} km"


def fmt_kwh(value) -> str:
    if value is None:
        return "?"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    return f"{number:.1f} kWh"


def fmt_pct(value) -> str:
    if value is None:
        return "?"
    return f"{value}%"


def yes_no(value) -> str:
    if value is True or value == 1:
        return "yes"
    if value is False or value == 0:
        return "no"
    return "?"
