"""Location reads for a vehicle that is already online.

The default area rounds latitude and longitude to two decimal places before
that point is sent to Nominatim. The rounded point is not printed. A precise
point is formatted and returned only when the caller asks for it, and that
point is not sent to Nominatim. History does not store either point.

Nominatim's usage policy allows light use: at most one request per second,
a User-Agent that identifies the application, and no bulk geocoding. This
module sends one request per call and does not retry. See
https://operations.osmfoundation.org/policies/nominatim/
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlencode

from core.errors import ConfigError
from core.fleet.http import HttpClient
from core.fleet.queries import location_scope_denied

NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
ZOOM = 14
AREA_UNAVAILABLE = "area unavailable"
SCOPE_HINT = (
    "The token cannot read vehicle location. "
    "Re-authorize and include the vehicle_location scope."
)

_ROUNDED = re.compile(r"^-?\d+\.\d{2}$")
_PRECISE = re.compile(r"\d+\.\d{3,}")
_LOCALITY = (
    "suburb",
    "neighbourhood",
    "locality",
    "village",
    "hamlet",
    "town",
    "city_district",
    "municipality",
    "city",
)
_REGION = ("state", "region", "state_district")


class AreaUnavailable(Exception):
    """The area could not be resolved. The message is a fixed public phrase."""

    def __init__(self) -> None:
        super().__init__(AREA_UNAVAILABLE)


@dataclass(frozen=True)
class WhereReport:
    """Public result. A coarse area has no coordinates. A precise result does."""

    kind: str
    state: str = ""
    suburb: str = ""
    region: str = ""
    country: str = ""
    latitude: str = ""
    longitude: str = ""
    map_url: str = ""

    @property
    def exit_code(self) -> int:
        if self.kind in {"area", "precise", "skipped"}:
            return 0
        return 1


def area_from_vehicle_payload(http: HttpClient, payload: object) -> WhereReport:
    """Resolve a coarse area, or a public failure phrase. Never raises a coordinate."""
    try:
        if not isinstance(payload, dict):
            return WhereReport(kind="unavailable")
        pair = _coordinates(payload)
        if pair is None:
            if location_scope_denied(payload):
                return WhereReport(kind="scope")
            return WhereReport(kind="unavailable")
        suburb, region, country = _coarse_area(http, pair[0], pair[1])
        return WhereReport(kind="area", suburb=suburb, region=region, country=country)
    except Exception:
        return WhereReport(kind="unavailable")


def format_where(report: WhereReport) -> str:
    if report.kind == "skipped":
        state = report.state or "unknown"
        return f"state: {state}\nskipped: the vehicle is not online, so it was not woken"
    if report.kind == "scope":
        return SCOPE_HINT
    if report.kind == "precise":
        if not report.latitude or not report.longitude or not report.map_url:
            return AREA_UNAVAILABLE
        return f"latitude: {report.latitude}\nlongitude: {report.longitude}\nmap: {report.map_url}"
    if report.kind != "area":
        return AREA_UNAVAILABLE
    lines = []
    if report.suburb:
        lines.append(f"suburb: {report.suburb}")
    if report.region:
        lines.append(f"region: {report.region}")
    if report.country:
        lines.append(f"country: {report.country}")
    text = "\n".join(lines)
    if not text or _PRECISE.search(text):
        return AREA_UNAVAILABLE
    return text


def format_where_json(report: WhereReport) -> str:
    if report.kind == "skipped":
        payload: dict = {"skipped": True, "state": report.state or "unknown"}
    elif report.kind == "scope":
        payload = {"error": SCOPE_HINT}
    elif report.kind == "precise":
        if not report.latitude or not report.longitude or not report.map_url:
            payload = {"error": AREA_UNAVAILABLE}
        else:
            payload = {"latitude": report.latitude, "longitude": report.longitude, "map": report.map_url}
        return json.dumps(payload, indent=2)
    elif report.kind != "area":
        payload = {"error": AREA_UNAVAILABLE}
    else:
        payload = {"suburb": report.suburb, "region": report.region, "country": report.country}
    rendered = json.dumps(payload, indent=2)
    if _PRECISE.search(rendered):
        return json.dumps({"error": AREA_UNAVAILABLE}, indent=2)
    return rendered


def precise_point(payload: object) -> tuple[str, str] | None:
    """Formatted latitude and longitude, or None when the payload has no usable point."""
    if not isinstance(payload, dict):
        return None
    pair = _coordinates(payload)
    if pair is None:
        return None
    try:
        return format_latitude(pair[0]), format_longitude(pair[1])
    except ConfigError:
        return None


def precise_from_vehicle_payload(payload: object) -> WhereReport:
    """Precise point and a map link. This does not call Nominatim."""
    if isinstance(payload, dict) and location_scope_denied(payload):
        return WhereReport(kind="scope")
    point = precise_point(payload)
    if point is None:
        return WhereReport(kind="unavailable")
    latitude, longitude = point
    return WhereReport(kind="precise", latitude=latitude, longitude=longitude, map_url=map_link(latitude, longitude))


def map_link(latitude: str, longitude: str) -> str:
    return f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}#map=16/{latitude}/{longitude}"


def format_latitude(value) -> str:
    return _format_axis(value, 90, "latitude must be from -90 to 90")


def format_longitude(value) -> str:
    return _format_axis(value, 180, "longitude must be from -180 to 180")


def _format_axis(value, limit: int, message: str) -> str:
    if isinstance(value, bool) or value is None:
        raise ConfigError(message)
    text = str(value).strip()
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise ConfigError(message) from exc
    if number != number or number in (float("inf"), float("-inf")) or number < -limit or number > limit:
        raise ConfigError(message)
    rendered = f"{number:.6f}".rstrip("0").rstrip(".")
    if rendered in {"", "-", "-0"}:
        return "0"
    return rendered


def _coordinates(payload: dict) -> tuple[object, object] | None:
    body = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    if not isinstance(body, dict):
        return None
    drive = body.get("drive_state") if isinstance(body.get("drive_state"), dict) else {}
    latitude = drive.get("latitude")
    longitude = drive.get("longitude")
    if latitude is None or longitude is None:
        extra = body.get("location_data") if isinstance(body.get("location_data"), dict) else {}
        if latitude is None:
            latitude = extra.get("latitude", extra.get("lat"))
        if longitude is None:
            longitude = extra.get("longitude", extra.get("lon", extra.get("lng")))
    if latitude is None or longitude is None:
        return None
    return latitude, longitude


def _coarse_area(http: HttpClient, latitude: object, longitude: object) -> tuple[str, str, str]:
    """Round first, then reverse-geocode. The original values are not sent."""
    try:
        lat_s = _round_coord(latitude, 90)
        lon_s = _round_coord(longitude, 180)
    except Exception:
        raise AreaUnavailable from None
    try:
        suburb, region, country = _reverse(http, lat_s, lon_s)
    except AreaUnavailable:
        raise
    except Exception:
        raise AreaUnavailable from None
    blob = "\n".join((suburb, region, country))
    for secret in (lat_s, lon_s, lat_s.lstrip("+-"), lon_s.lstrip("+-")):
        if secret and secret in blob:
            raise AreaUnavailable
    if not any((suburb, region, country)):
        raise AreaUnavailable
    return suburb, region, country


def _round_coord(value: object, limit: int) -> str:
    if isinstance(value, bool) or value is None:
        raise AreaUnavailable
    try:
        number = Decimal(str(value).strip())
    except Exception:
        raise AreaUnavailable from None
    if not number.is_finite() or abs(number) > limit:
        raise AreaUnavailable
    quantized = number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if abs(quantized) > limit:
        raise AreaUnavailable
    text = format(quantized, "f")
    if not _ROUNDED.fullmatch(text):
        raise AreaUnavailable
    return text


def _reverse(http: HttpClient, latitude: str, longitude: str) -> tuple[str, str, str]:
    if not _ROUNDED.fullmatch(latitude) or not _ROUNDED.fullmatch(longitude):
        raise AreaUnavailable
    query = urlencode(
        {
            "format": "jsonv2",
            "lat": latitude,
            "lon": longitude,
            "zoom": str(ZOOM),
            "addressdetails": "1",
            "accept-language": "en",
        }
    )
    status, payload = http.request_json(
        "GET",
        f"{NOMINATIM_REVERSE}?{query}",
        {"Accept": "application/json", "Accept-Language": "en"},
        timeout=20,
    )
    if status != 200 or not isinstance(payload, dict):
        raise AreaUnavailable
    address = payload.get("address")
    if not isinstance(address, dict):
        raise AreaUnavailable
    suburb = _place(address, _LOCALITY)
    region = _place(address, _REGION)
    country = _place(address, ("country",))
    return suburb, region, country


def _place(address: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = address.get(key)
        if not isinstance(value, str):
            continue
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 80 or _PRECISE.search(cleaned):
            continue
        return cleaned
    return ""
