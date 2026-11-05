"""Country, timezone, suburb-level places, and habits.

Tokens, keys, a VIN, a street address, and live coordinates are rejected.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from core.atomic import write_atomic
from core.errors import MemoryRejected, ProfileError

FORBIDDEN_KEYS = {
    "access_token",
    "refresh_token",
    "token",
    "client_secret",
    "private_key",
    "public_key",
    "vin",
    "latitude",
    "longitude",
    "lat",
    "lon",
    "lng",
    "coordinates",
    "authorization_code",
    "password",
    "street",
    "street_number",
    "address",
    "address_line",
    "postal_code",
    "postcode",
}

_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")


@dataclass
class Place:
    label: str
    suburb: str
    region: str
    country: str


@dataclass
class Habit:
    name: str
    detail: str


@dataclass
class OwnerProfile:
    country: str
    timezone: str
    places: list[Place]
    habits: list[Habit]

    def habit(self, name: str, default: str | None = None) -> str | None:
        for item in self.habits:
            if item.name == name:
                return item.detail
        return default

    def habit_int(self, name: str, default: int) -> int:
        raw = self.habit(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def habit_float(self, name: str, default: float) -> float:
        raw = self.habit(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            return default


def assert_memory_safe(key: str, value) -> None:
    if str(key).lower() in FORBIDDEN_KEYS:
        raise MemoryRejected("That value cannot be stored in assistant memory.")
    _walk(value)


def _walk(value) -> None:
    if isinstance(value, dict):
        for inner_key, inner in value.items():
            if str(inner_key).lower() in FORBIDDEN_KEYS:
                raise MemoryRejected("That value cannot be stored in assistant memory.")
            _walk(inner)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            _walk(inner)
    elif isinstance(value, str):
        stripped = value.strip()
        if "BEGIN PRIVATE" in stripped or "BEGIN PUBLIC" in stripped or stripped.startswith("eyJ"):
            raise MemoryRejected("That value cannot be stored in assistant memory.")
        if _VIN.match(stripped):
            raise MemoryRejected("A VIN cannot be stored in assistant memory.")


def load_profile(path: Path) -> OwnerProfile:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProfileError("The profile file is not a mapping.")
    unknown = set(data) - {"country", "timezone", "places", "habits"}
    if unknown:
        raise ProfileError("The profile contains a field this toolkit will not store.")
    country = str(data.get("country", ""))
    if not _COUNTRY.match(country):
        raise ProfileError("Country must be a two-letter code.")
    timezone = str(data.get("timezone", ""))
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ProfileError("Timezone is not a known zone name.") from exc
    places = [_place(item) for item in data.get("places") or []]
    habits = [_habit(item) for item in data.get("habits") or []]
    profile = OwnerProfile(country=country, timezone=timezone, places=places, habits=habits)
    assert_memory_safe("profile", asdict(profile))
    return profile


def save_profile(path: Path, profile: OwnerProfile) -> None:
    assert_memory_safe("profile", asdict(profile))
    payload = yaml.safe_dump(asdict(profile), sort_keys=False)
    write_atomic(Path(path), payload)


def _place(item) -> Place:
    if not isinstance(item, dict):
        raise ProfileError("A place must be a mapping.")
    if set(item) - {"label", "suburb", "region", "country"}:
        raise ProfileError("A place can only name a suburb, a region, and a country.")
    place = Place(
        label=str(item.get("label", "")).strip(),
        suburb=str(item.get("suburb", "")).strip(),
        region=str(item.get("region", "")).strip(),
        country=str(item.get("country", "")).strip(),
    )
    if not place.label or not place.suburb or not place.region or not _COUNTRY.match(place.country):
        raise ProfileError("A place needs a label, a suburb, a region, and a country code.")
    if any(char.isdigit() for char in place.suburb):
        raise ProfileError("Use a suburb name, not a street number.")
    return place


def _habit(item) -> Habit:
    if not isinstance(item, dict) or set(item) - {"name", "detail"}:
        raise ProfileError("A habit needs a name and a detail.")
    habit = Habit(name=str(item.get("name", "")).strip(), detail=str(item.get("detail", "")).strip())
    if not habit.name or not habit.detail:
        raise ProfileError("A habit needs a name and a detail.")
    assert_memory_safe(habit.name, habit.detail)
    return habit
