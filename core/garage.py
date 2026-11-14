"""Nicknames for the cars on one account.

The file is ``$AGENTDRIVE_HOME/cars.yaml``. It is private. A nickname is
chosen by the owner. The VIN stays in that file so a later command can
pick the same car, and it is never printed.

With one car, history and alert files stay in the config home. The first
car that is named keeps those files. Another car uses
``$AGENTDRIVE_HOME/cars/<id>/``.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.atomic import write_atomic
from core.errors import ConfigError
from core.fleet.queries import QueryError
from core.redact import mask_vin

CARS_NAME = "cars.yaml"
_NICK = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_RESERVED = {
    "approve",
    "car",
    "confirm",
    "default",
    "deny",
    "off",
    "on",
    "owner",
    "request",
}


@dataclass(frozen=True)
class Car:
    id: str
    nickname: str
    vin: str


@dataclass(frozen=True)
class Garage:
    cars: tuple[Car, ...] = ()
    default: str = ""
    legacy_id: str = ""

    def by_nickname(self, name: str) -> Car | None:
        key = str(name or "").strip().lower()
        for car in self.cars:
            if car.nickname == key:
                return car
        return None

    def by_vin(self, vin: str) -> Car | None:
        key = str(vin or "").strip().upper()
        for car in self.cars:
            if car.vin == key:
                return car
        return None

    def by_id(self, car_id: str) -> Car | None:
        for car in self.cars:
            if car.id == str(car_id or ""):
                return car
        return None


@dataclass(frozen=True)
class Selection:
    """The car a command will use, and where its local data lives."""

    row: dict
    car: Car | None
    store: Path
    label: str


def normalize_nickname(value: str) -> str:
    """Return a stored nickname, or refuse a VIN-shaped or reserved word."""
    text = " ".join(str(value or "").split()).lower()
    if not _NICK.fullmatch(text) or text in _RESERVED or _VIN.fullmatch(text.upper()):
        raise ConfigError("A car nickname must be 2 to 32 letters, digits, or hyphens.")
    return text


def load_garage(home: Path) -> Garage:
    path = Path(home) / CARS_NAME
    if not path.is_file():
        return Garage()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError("cars.yaml could not be read.") from exc
    if data is None:
        return Garage()
    if not isinstance(data, dict):
        raise ConfigError("cars.yaml must be a mapping.")
    unknown = set(data) - {"version", "default", "legacy_id", "cars"}
    if unknown:
        raise ConfigError("cars.yaml contains a field this toolkit will not store.")
    cars: list[Car] = []
    seen_names: set[str] = set()
    seen_vins: set[str] = set()
    for item in data.get("cars") or []:
        car = _car(item)
        if car.nickname in seen_names or car.vin in seen_vins:
            raise ConfigError("cars.yaml has two cars with the same nickname or VIN.")
        seen_names.add(car.nickname)
        seen_vins.add(car.vin)
        cars.append(car)
    default = str(data.get("default") or "").strip().lower()
    if default and default not in seen_names:
        raise ConfigError("The default car is not in cars.yaml.")
    legacy = str(data.get("legacy_id") or "").strip()
    if legacy and all(car.id != legacy for car in cars):
        raise ConfigError("The saved history car is not in cars.yaml.")
    return Garage(cars=tuple(cars), default=default, legacy_id=legacy)


def save_garage(home: Path, garage: Garage) -> None:
    payload = {
        "version": 1,
        "default": garage.default,
        "legacy_id": garage.legacy_id,
        "cars": [{"id": car.id, "nickname": car.nickname, "vin": car.vin} for car in garage.cars],
    }
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
    write_atomic(Path(home) / CARS_NAME, text)


def store_for(home: Path, garage: Garage, car: Car | None) -> Path:
    """Directory that holds this car's history and alert file.

    One car, or the car that already had history, keeps the config home.
    """
    root = Path(home)
    if car is None or len(garage.cars) <= 1:
        return root
    if garage.legacy_id and car.id == garage.legacy_id:
        return root
    return root / "cars" / car.id


def store_for_saved(home: Path, car_name: str | None) -> Path:
    """Pick a history directory without contacting the car.

    Zero or one saved car keeps the config home, which is where an older
    install already wrote ``history.db``.
    """
    garage = load_garage(home)
    if len(garage.cars) <= 1:
        if car_name:
            nickname = normalize_nickname(car_name)
            if garage.cars and garage.by_nickname(nickname) is None:
                raise ConfigError("No car is saved under that nickname.")
            if not garage.cars:
                raise ConfigError("No car is saved under that nickname.")
        return Path(home)
    if car_name:
        car = garage.by_nickname(normalize_nickname(car_name))
        if car is None:
            raise ConfigError("No car is saved under that nickname.")
        return store_for(home, garage, car)
    if garage.default:
        car = garage.by_nickname(garage.default)
        if car is None:
            raise ConfigError("The default car is not in cars.yaml.")
        return store_for(home, garage, car)
    raise ConfigError(
        "Several cars are saved and no default is set. "
        "Pass --car <nickname>, or set one with `agentdrive cars default <nickname>`."
    )


def resolve_vehicle(home: Path, payload: dict, *, car_name: str | None, preferred: str | None) -> Selection:
    """Choose one car. Several cars and no explicit choice is a refusal."""
    rows = vehicle_rows(payload)
    if not rows:
        raise QueryError("no vehicles")
    garage = load_garage(home)
    explicit = str(car_name or "").strip()
    if explicit:
        car = garage.by_nickname(normalize_nickname(explicit))
        if car is None:
            raise ConfigError("No car is saved under that nickname. Run `agentdrive cars` and `agentdrive cars name`.")
        row = _row_for_vin(rows, car.vin)
        return Selection(row=row, car=car, store=store_for(home, garage, car), label=car.nickname)
    if len(rows) == 1:
        row = rows[0]
        vin = str(row.get("vin") or "")
        if preferred and vin.upper() != str(preferred).upper():
            raise QueryError("The requested vehicle is not on this account")
        car = garage.by_vin(vin)
        return Selection(row=row, car=car, store=store_for(home, garage, car), label="")
    if preferred:
        car = garage.by_vin(preferred)
        if car is None:
            raise ConfigError(
                "Several cars are on this account. "
                "Name this car with `agentdrive cars name <tail> <nickname>` and pass `--car <nickname>`."
            )
        row = _row_for_vin(rows, car.vin)
        return Selection(row=row, car=car, store=store_for(home, garage, car), label=car.nickname)
    if garage.default:
        car = garage.by_nickname(garage.default)
        if car is None:
            raise ConfigError("The default car is not in cars.yaml.")
        row = _row_for_vin(rows, car.vin)
        return Selection(row=row, car=car, store=store_for(home, garage, car), label=car.nickname)
    raise ConfigError(
        "Several cars are on this account and no default is set. "
        "Pass --car <nickname>, or set one with `agentdrive cars default <nickname>`. "
        "Commands will not guess a car."
    )


def remember_car(home: Path, rows: list[dict], tail: str, nickname: str) -> Car:
    """Attach a nickname to the car whose VIN ends with ``tail``."""
    row = match_tail(rows, tail)
    vin = str(row.get("vin") or "").strip().upper()
    if not _VIN.fullmatch(vin):
        raise ConfigError("That vehicle has no usable VIN.")
    name = normalize_nickname(nickname)
    garage = load_garage(home)
    current = garage.by_vin(vin)
    others = [car for car in garage.cars if car.vin != vin]
    if any(car.nickname == name for car in others):
        raise ConfigError("That nickname is already used by another car.")
    if current is None:
        car = Car(id=secrets.token_hex(4), nickname=name, vin=vin)
        legacy = garage.legacy_id or car.id
    else:
        car = Car(id=current.id, nickname=name, vin=vin)
        legacy = garage.legacy_id or current.id
    default = garage.default
    if current is not None and default == current.nickname:
        default = name
    save_garage(home, Garage(cars=tuple(others + [car]) if current is None else _replace(garage.cars, car), default=default, legacy_id=legacy))
    return car


def set_default(home: Path, nickname: str) -> Garage:
    name = normalize_nickname(nickname)
    garage = load_garage(home)
    if garage.by_nickname(name) is None:
        raise ConfigError("No car is saved under that nickname.")
    updated = Garage(cars=garage.cars, default=name, legacy_id=garage.legacy_id)
    save_garage(home, updated)
    return updated


def clear_default(home: Path) -> Garage:
    garage = load_garage(home)
    updated = Garage(cars=garage.cars, default="", legacy_id=garage.legacy_id)
    save_garage(home, updated)
    return updated


def match_tail(rows: list[dict], token: str) -> dict:
    """Match a VIN tail or a full VIN. The token is never copied into an error."""
    text = "".join(str(token or "").split()).upper()
    if len(text) == 4:
        matches = [row for row in rows if str(row.get("vin") or "").upper().endswith(text)]
    elif _VIN.fullmatch(text):
        matches = [row for row in rows if str(row.get("vin") or "").upper() == text]
    else:
        raise ConfigError("Name a car by the last four characters shown in `agentdrive cars`.")
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ConfigError("No car on this account has that VIN tail.")
    raise ConfigError("That VIN tail matches more than one car.")


def format_cars(rows: list[dict], garage: Garage) -> str:
    """List cars from the vehicle list. The full VIN is not included."""
    lines = ["Cars on this account. This list does not wake them."]
    if not rows:
        lines.append("No cars on this account.")
        return "\n".join(lines)
    for row in rows:
        vin = str(row.get("vin") or "")
        car = garage.by_vin(vin)
        nickname = car.nickname if car else "(unnamed)"
        default = "  default" if car and garage.default == car.nickname else ""
        state = str(row.get("state") or "unknown").strip().lower() or "unknown"
        lines.append(f"{nickname}  {_public_name(row, vin)}  {mask_vin(vin)}{default}  {state}")
    if len(rows) > 1 and not garage.default:
        lines.append(
            "No default is set. A command will not guess a car until you pass --car or set a default."
        )
    return "\n".join(lines)


def vehicle_rows(payload: dict) -> list[dict]:
    body = payload.get("response", payload) if isinstance(payload, dict) else payload
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if isinstance(body, dict):
        return [body]
    return []


def _replace(cars: tuple[Car, ...], updated: Car) -> tuple[Car, ...]:
    return tuple(updated if car.id == updated.id else car for car in cars)


def _car(item) -> Car:
    if not isinstance(item, dict):
        raise ConfigError("A car in cars.yaml must be a mapping.")
    unknown = set(item) - {"id", "nickname", "vin"}
    if unknown:
        raise ConfigError("A car in cars.yaml has a field this toolkit will not store.")
    nickname = normalize_nickname(str(item.get("nickname") or ""))
    vin = str(item.get("vin") or "").strip().upper()
    car_id = str(item.get("id") or "").strip().lower()
    if not _VIN.fullmatch(vin):
        raise ConfigError("A car in cars.yaml needs a VIN.")
    if not re.fullmatch(r"[0-9a-f]{8}", car_id):
        raise ConfigError("A car in cars.yaml needs a local id.")
    return Car(id=car_id, nickname=nickname, vin=vin)


def _row_for_vin(rows: list[dict], vin: str) -> dict:
    for row in rows:
        if str(row.get("vin") or "").upper() == vin.upper():
            return row
    raise QueryError("The requested vehicle is not on this account")


def _public_name(row: dict, vin: str) -> str:
    name = " ".join(str(row.get("display_name") or "").split())
    if vin and name:
        name = name.replace(vin, mask_vin(vin))
    return name or "Vehicle"
