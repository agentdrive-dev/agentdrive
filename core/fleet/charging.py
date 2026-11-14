"""Personal-account charging reads.

Nearby sites expose location and stall counts. Official charging history is
account-level. The public record is the date, site name, energy, and cost
Tesla returns. Coordinates, a street address, and the VIN are not copied
into that record. Home charging is not in this history.
"""

from __future__ import annotations

import re
from typing import Any

from core.config import assert_path_allowed, endpoint, fill_path
from core.errors import ConfigError, EndpointNotAllowed

HOME_CHARGING_NOTE = "Home charging is not included."
_INVOICE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,79}$")
_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_CHARGING_FEE = "CHARGING"

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


def parse_invoice_id(value: str) -> str:
    """Accept an invoice content id. A VIN-shaped value is refused."""
    text = str(value or "").strip()
    if not _INVOICE_ID.fullmatch(text) or ".." in text or _VIN.fullmatch(text.upper()):
        raise ConfigError("Invoice id is not usable.")
    return text


def invoice_path(regions: dict, invoice_id: str) -> str:
    identified = parse_invoice_id(invoice_id)
    path = fill_path(endpoint(regions, "charging_invoice"), invoice_id=identified)
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
            "Nearby charging results are location and stall counts. "
            "Field names must be confirmed against a real call."
        ),
        "matched_list_keys": matched,
    }


def history_rows(payload: dict) -> list[dict]:
    """Public charging rows. Coordinates, addresses, and the VIN are dropped."""
    records = []
    for row in _raw_rows(payload):
        if not isinstance(row, dict):
            continue
        records.append(
            {
                "date": _as_text(row.get("chargeStartDateTime")),
                "site": _as_text(row.get("siteLocationName")),
                "energy": _energy(row),
                "cost": _cost(row),
                "invoice_id": _invoice_id(row),
            }
        )
    return records


def format_history(records: list[dict], *, as_json: bool) -> str:
    """Date, site name, energy, and cost as Tesla returned them."""
    import json

    public = [_public_row(row) for row in records]
    if as_json:
        return json.dumps({"note": HOME_CHARGING_NOTE, "records": public}, indent=2) + "\n"
    lines = [f"Official Tesla charging history. {HOME_CHARGING_NOTE}"]
    if not public:
        lines.append("No charging sessions were returned.")
        return "\n".join(lines)
    for row in public:
        invoice = f" | invoice {row['invoice_id']}" if row["invoice_id"] else ""
        lines.append(f"{row['date']} | {row['site']} | {row['energy']} | {row['cost']}{invoice}")
    return "\n".join(lines)


def format_invoice_skipped(invoice_id: str) -> str:
    return (
        f"Invoice {invoice_id} was not saved.\n"
        f"{HOME_CHARGING_NOTE}\n"
        "Ask to save it and the PDF is written under the config home."
    )


def format_invoice_saved(path: str) -> str:
    return f"Saved invoice to {path}\n{HOME_CHARGING_NOTE}"


def _public_row(row: dict) -> dict:
    return {
        "date": row.get("date") or "",
        "site": row.get("site") or "",
        "energy": row.get("energy") or "",
        "cost": row.get("cost") or "",
        "invoice_id": row.get("invoice_id") or "",
    }


def _raw_rows(payload: dict) -> list:
    body = payload.get("response", payload) if isinstance(payload, dict) else None
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ("data", "history", "charging_history", "results"):
            if isinstance(body.get(key), list):
                return body[key]
    return []


def _as_text(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return str(value).strip()


def _number_text(value) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).strip()


def _fees(row: dict, kind: str | None = None) -> list[dict]:
    fees = row.get("fees")
    if not isinstance(fees, list):
        return []
    chosen = []
    for fee in fees:
        if not isinstance(fee, dict):
            continue
        if kind is not None and str(fee.get("feeType") or "").upper() != kind:
            continue
        chosen.append(fee)
    return chosen


def _energy(row: dict) -> str:
    """Charging usageBase and uom, as returned. Tiers are not added in."""
    parts = []
    for fee in _fees(row, _CHARGING_FEE):
        usage = _number_text(fee.get("usageBase"))
        unit = _as_text(fee.get("uom"))
        if usage and unit:
            parts.append(f"{usage} {unit}")
        elif usage:
            parts.append(usage)
    return "; ".join(parts)


def _cost(row: dict) -> str:
    """Currency and totalDue for each fee Tesla billed, charging first."""
    ordered = _fees(row, _CHARGING_FEE) + [
        fee for fee in _fees(row) if str(fee.get("feeType") or "").upper() != _CHARGING_FEE
    ]
    parts = []
    for fee in ordered:
        amount = _number_text(fee.get("totalDue"))
        if amount == "":
            continue
        kind = _as_text(fee.get("feeType")).lower()
        if kind and kind != "charging":
            try:
                if float(amount) == 0:
                    continue
            except ValueError:
                pass
        currency = _as_text(fee.get("currencyCode"))
        label = f"{currency} {amount}".strip()
        if kind and kind != "charging":
            label = f"{label} {kind}".strip()
        parts.append(label)
    return "; ".join(parts)


def _invoice_id(row: dict) -> str:
    invoices = row.get("invoices")
    if isinstance(invoices, dict):
        invoices = [invoices]
    if not isinstance(invoices, list):
        return ""
    for item in invoices:
        if not isinstance(item, dict):
            continue
        content = _as_text(item.get("contentId"))
        if content and _INVOICE_ID.fullmatch(content) and not _VIN.fullmatch(content.upper()):
            return content
    return ""
