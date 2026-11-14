"""Home electricity windows stored in the private config home.

The file is ``$AGENTDRIVE_HOME/tariff.yaml``. It is advice input only.
Nothing here talks to the car or changes a charging timetable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from core.atomic import write_atomic
from core.errors import ConfigError

TARIFF_NAME = "tariff.yaml"
_KINDS = {"off-peak", "peak"}


@dataclass(frozen=True)
class TariffWindow:
    kind: str
    start: str
    end: str
    start_minutes: int
    end_minutes: int
    price_per_kwh: float | None = None

    def as_dict(self) -> dict:
        body = {"kind": self.kind, "start": self.start, "end": self.end}
        if self.price_per_kwh is not None:
            body["price_per_kwh"] = self.price_per_kwh
        return body


@dataclass(frozen=True)
class Tariff:
    windows: tuple[TariffWindow, ...] = ()

    def of_kind(self, kind: str) -> tuple[TariffWindow, ...]:
        return tuple(item for item in self.windows if item.kind == kind)


def tariff_path(home: Path) -> Path:
    return Path(home) / TARIFF_NAME


def load_tariff(home: Path) -> Tariff:
    path = tariff_path(home)
    if not path.is_file():
        return Tariff()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError("tariff.yaml could not be read.") from exc
    if data is None:
        return Tariff()
    if not isinstance(data, dict):
        raise ConfigError("tariff.yaml must be a mapping.")
    unknown = set(data) - {"version", "windows"}
    if unknown:
        raise ConfigError("tariff.yaml contains a field this toolkit will not store.")
    windows = tuple(_window(item) for item in data.get("windows") or [])
    return Tariff(windows=windows)


def save_tariff(home: Path, tariff: Tariff) -> None:
    payload = {"version": 1, "windows": [item.as_dict() for item in tariff.windows]}
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
    write_atomic(tariff_path(home), text)


def set_window(home: Path, kind: str, window: str, price: float | None) -> Tariff:
    parsed = _parse_window(kind, window, price)
    current = load_tariff(home)
    kept = [
        item
        for item in current.windows
        if not (item.kind == parsed.kind and item.start == parsed.start and item.end == parsed.end)
    ]
    updated = Tariff(windows=tuple(kept + [parsed]))
    save_tariff(home, updated)
    return updated


def clear_windows(home: Path, kind: str | None) -> Tariff:
    current = load_tariff(home)
    if kind is None:
        updated = Tariff()
    else:
        name = _kind(kind)
        updated = Tariff(windows=tuple(item for item in current.windows if item.kind != name))
    save_tariff(home, updated)
    return updated


def format_tariff(tariff: Tariff) -> str:
    if not tariff.windows:
        return (
            "No home tariff is saved.\n"
            "Set one with `agentdrive tariff set off-peak 22:00-06:00` or "
            "`agentdrive tariff set peak 16:00-21:00`. "
            "An optional `--price` is per kWh. This does not change the car."
        )
    lines = ["Home tariff windows saved on this computer. This does not change the car."]
    for item in tariff.windows:
        price = "" if item.price_per_kwh is None else f"  {item.price_per_kwh:.4g} per kWh"
        lines.append(f"{item.kind}  {item.start}-{item.end}{price}")
    return "\n".join(lines)


def in_window(minute: int, window: TariffWindow) -> bool:
    start = window.start_minutes
    end = window.end_minutes
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def clock_text(minutes: int) -> str:
    hour, minute = divmod(int(minutes), 60)
    return f"{hour:02d}:{minute:02d}"


def _window(item) -> TariffWindow:
    if not isinstance(item, dict):
        raise ConfigError("A tariff window must be a mapping.")
    unknown = set(item) - {"kind", "start", "end", "price_per_kwh"}
    if unknown:
        raise ConfigError("A tariff window has a field this toolkit will not store.")
    kind = _kind(str(item.get("kind") or ""))
    start = _clock(str(item.get("start") or ""))
    end = _clock(str(item.get("end") or ""))
    if start == end:
        raise ConfigError("A tariff window needs a start and a different end.")
    price = _price(item.get("price_per_kwh"))
    return TariffWindow(
        kind=kind,
        start=_format_clock(start),
        end=_format_clock(end),
        start_minutes=start,
        end_minutes=end,
        price_per_kwh=price,
    )


def _parse_window(kind: str, window: str, price: float | None) -> TariffWindow:
    text = str(window or "").strip()
    if "-" not in text:
        raise ConfigError("A tariff window is HH:MM-HH:MM.")
    start_text, end_text = text.split("-", 1)
    start = _clock(start_text)
    end = _clock(end_text)
    if start == end:
        raise ConfigError("A tariff window needs a start and a different end.")
    return TariffWindow(
        kind=_kind(kind),
        start=_format_clock(start),
        end=_format_clock(end),
        start_minutes=start,
        end_minutes=end,
        price_per_kwh=_price(price),
    )


def _kind(value: str) -> str:
    text = str(value or "").strip().lower()
    if text not in _KINDS:
        raise ConfigError("A tariff window is off-peak or peak.")
    return text


def _clock(value: str) -> int:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ConfigError("A tariff time is HH:MM in 24-hour time.")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour > 23 or minute > 59 or len(parts[0]) > 2 or len(parts[1]) != 2:
        raise ConfigError("A tariff time is HH:MM in 24-hour time.")
    return hour * 60 + minute


def _format_clock(minutes: int) -> str:
    return clock_text(minutes)


def _price(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("A tariff price must be a number of currency units per kWh.") from exc
    if number != number or number < 0 or number > 100:
        raise ConfigError("A tariff price must be from 0 to 100 per kWh.")
    return round(number, 6)
