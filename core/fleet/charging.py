"""Personal-account charging reads.

Field names below are candidates. They are marked unconfirmed until an owner
compares them with a live response. Nearby sites expose location and stall
counts only. This module does not request live Supercharger pricing, and it
does not call the business-only sessions route.
"""

from __future__ import annotations

from typing import Any

from core.config import assert_path_allowed, endpoint, fill_path
from core.errors import EndpointNotAllowed

FIELD_STATUS = "unconfirmed_against_a_live_response"

# Candidate keys only. A live payload decides which one is real.
_SITE_LIST_KEYS = ("superchargers", "charging_sites", "sites", "destination_charging")
_STALL_AVAILABLE = ("available_stalls", "availableStalls", "stalls_available")
_STALL_TOTAL = ("total_stalls", "totalStalls", "stall_count")
_NAME_KEYS = ("name", "title", "site_name")
_PRICE_HINTS = ("price", "pricing", "cost", "fee", "rate", "currency")


def nearby_path(regions: dict, vehicle_id: str) -> str:
    path = fill_path(endpoint(regions, "nearby_charging_sites"), vehicle_id=vehicle_id)
    assert_path_allowed(regions, path)
    return path


def history_path(regions: dict) -> str:
    path = endpoint(regions, "charging_history")
    assert_path_allowed(regions, path)
    return path


def invoice_path(regions: dict, invoice_id: str) -> str:
    if not invoice_id or "/" in invoice_id or ".." in invoice_id:
        raise EndpointNotAllowed("Invoice id is not usable")
    path = fill_path(endpoint(regions, "charging_invoice"), invoice_id=invoice_id)
    assert_path_allowed(regions, path)
    return path


def sessions_are_blocked(regions: dict) -> bool:
    blocked = regions.get("blocked_endpoints") or []
    return any("charging/sessions" in str(item) for item in blocked)


def _price_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            full = f"{prefix}.{key}" if prefix else str(key)
            if any(hint in str(key).lower() for hint in _PRICE_HINTS):
                found.append(full)
            found.extend(_price_keys(inner, full))
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            found.extend(_price_keys(inner, f"{prefix}[{index}]"))
    return found


def _first(mapping: dict, keys: tuple[str, ...]) -> tuple[Any, str | None]:
    for key in keys:
        if key in mapping:
            return mapping[key], key
    return None, None


def _coordinates(site: dict) -> tuple[dict | None, str | None]:
    location = site.get("location")
    if isinstance(location, dict):
        lat = location.get("lat", location.get("latitude"))
        lon = location.get("long", location.get("lng", location.get("longitude")))
        if lat is not None or lon is not None:
            return {"lat": lat, "lon": lon}, "location"
    lat = site.get("latitude", site.get("lat"))
    lon = site.get("longitude", site.get("long", site.get("lng")))
    if lat is not None or lon is not None:
        return {"lat": lat, "lon": lon}, "latitude/longitude"
    return None, None


def _site_lists(payload: dict) -> list[tuple[str, list]]:
    body = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    if not isinstance(body, dict):
        return []
    lists: list[tuple[str, list]] = []
    for key in _SITE_LIST_KEYS:
        value = body.get(key)
        if isinstance(value, list):
            lists.append((key, value))
    return lists


def normalize_nearby(payload: dict) -> dict:
    """Location and stall counts only. Pricing values are discarded."""
    sites: list[dict] = []
    matched: list[str] = []
    for list_key, rows in _site_lists(payload):
        matched.append(list_key)
        for row in rows:
            if not isinstance(row, dict):
                continue
            name, name_key = _first(row, _NAME_KEYS)
            available, available_key = _first(row, _STALL_AVAILABLE)
            total, total_key = _first(row, _STALL_TOTAL)
            coords, coord_key = _coordinates(row)
            sites.append(
                {
                    "name": name,
                    "available_stalls": available,
                    "total_stalls": total,
                    "location": coords,
                    "matched_keys": {
                        "list": list_key,
                        "name": name_key,
                        "available_stalls": available_key,
                        "total_stalls": total_key,
                        "location": coord_key,
                    },
                }
            )
    return {
        "field_status": FIELD_STATUS,
        "sites": sites,
        "pricing": None,
        "ignored_pricing_fields": _price_keys(payload),
        "note": (
            "Nearby charging results are limited to location and stall counts. "
            "No live Supercharger pricing is available. Field names must be confirmed against a real call."
        ),
        "matched_list_keys": matched,
    }


def normalize_history(payload: dict) -> dict:
    body = payload.get("response", payload)
    rows = []
    container_key = None
    if isinstance(body, dict):
        for key in ("data", "history", "charging_history", "results"):
            if isinstance(body.get(key), list):
                rows = body[key]
                container_key = key
                break
    elif isinstance(body, list):
        rows = body
        container_key = "root"
    preview = []
    for row in rows:
        if isinstance(row, dict):
            preview.append(sorted(row.keys()))
    return {
        "field_status": FIELD_STATUS,
        "container_key": container_key,
        "count": len(rows),
        "row_field_names": preview,
        "note": "Charging history field names are unconfirmed until checked against a real call.",
    }


def normalize_invoice(payload: dict) -> dict:
    body = payload.get("response", payload)
    keys = sorted(body.keys()) if isinstance(body, dict) else []
    return {
        "field_status": FIELD_STATUS,
        "field_names": keys,
        "note": "Invoice field names are unconfirmed until checked against a real call.",
    }
