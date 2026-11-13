"""Departure preconditioning stored on the car.

The read is Fleet API ``vehicle_data`` with ``preconditioning_schedule_data``.
Writes go through tesla-control ``precondition-schedule-add`` and
``precondition-schedule-remove``, which sign ``add_precondition_schedule``
and ``remove_precondition_schedule``.

The signed schedule is a cabin-ready time, on selected days, at a place.
``PreconditionSchedule`` has no battery switch and no off-peak time.
Tesla's command reference says ``set_scheduled_departure`` is not
recommended beginning with firmware 2024.26, and that
``add_precondition_schedule`` should be used instead. tesla-control does
not register the older command. This module does not send it, and it does
not send off-peak charging.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from core.errors import ConfigError
from core.fleet.charge_schedule import format_days, parse_days, parse_degree, parse_schedule_id

# precondition-schedule-add optionals are positional: REPEAT, ID, ENABLED.
# REPEAT other than "once" leaves one_time false. "weekly" is only a
# placeholder so ID and ENABLED can be reached. It is not a protocol field.
# The tool copies ID when it is present. Omit it and the tool stores the
# current unix time. ENABLED defaults to true inside the tool.
_REPEAT_WEEKLY = "weekly"


@dataclass(frozen=True)
class DepartureWrite:
    days: str
    ready_time: str
    latitude: str
    longitude: str
    once: bool
    enabled: bool
    days_mask: int
    ready_minutes: int
    schedule_id: str | None

    def tool_args(self) -> list[str]:
        args = ["precondition-schedule-add", self.days, self.ready_time, self.latitude, self.longitude]
        if self.schedule_id is None and self.enabled:
            if self.once:
                args.append("once")
            return args
        args.append("once" if self.once else _REPEAT_WEEKLY)
        args.append(self.schedule_id or "")
        if not self.enabled:
            args.append("false")
        return args


@dataclass(frozen=True)
class DepartureRow:
    id: int | None
    name: str | None
    days_of_week: int | None
    precondition_time: int | None
    one_time: bool
    enabled: bool
    latitude: float | None
    longitude: float | None

    def as_dict(self) -> dict:
        body = {
            "id": self.id,
            "days_of_week": self.days_of_week,
            "days": format_days(self.days_of_week),
            "enabled": self.enabled,
            "precondition_time": self.precondition_time,
            "ready_by": _format_clock(self.precondition_time),
            "one_time": self.one_time,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }
        if self.name:
            body["name"] = self.name
        return body


@dataclass(frozen=True)
class DepartureReport:
    state: str
    skipped: bool
    section_present: bool
    list_present: bool
    schedules: tuple[DepartureRow, ...]

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "skipped": self.skipped,
            "preconditioning_schedule_data": self.section_present,
            "precondition_schedules": self.list_present,
            "schedules": [row.as_dict() for row in self.schedules],
        }


def parse_ready_time(value: str) -> tuple[str, int]:
    text = str(value).strip()
    pieces = text.split(":")
    if len(pieces) != 2 or pieces[0] == "" or pieces[1] == "":
        raise ConfigError(_TIME_ERROR)
    if not pieces[0].isdigit() or not pieces[1].isdigit():
        raise ConfigError(_TIME_ERROR)
    hours = int(pieces[0])
    minutes = int(pieces[1])
    if hours > 23 or minutes > 59:
        raise ConfigError(_TIME_ERROR)
    total = hours * 60 + minutes
    return f"{hours:02d}:{minutes:02d}", total


def parse_departure_write(
    days: str,
    ready_time: str,
    latitude: str,
    longitude: str,
    *,
    once: bool,
    enabled: bool,
    schedule_id: str | None = None,
) -> DepartureWrite:
    day_text, day_mask = parse_days(days)
    clock, minutes = parse_ready_time(ready_time)
    identified = None
    if schedule_id is not None and str(schedule_id).strip() != "":
        identified = parse_schedule_id(schedule_id)
    if not enabled and identified is None:
        raise ConfigError("a disabled departure schedule needs an id")
    return DepartureWrite(
        days=day_text,
        ready_time=clock,
        latitude=parse_degree(latitude),
        longitude=parse_degree(longitude),
        once=bool(once),
        enabled=bool(enabled),
        days_mask=day_mask,
        ready_minutes=minutes,
        schedule_id=identified,
    )


def schedules_from_vehicle_data(payload: dict) -> tuple[bool, bool, tuple[DepartureRow, ...]]:
    """Return section presence, list presence, and rows.

    The documented container is ``preconditioning_schedule_data``. Each row
    uses the PreconditionSchedule fields. Latitude and longitude are also
    accepted as ``lat`` and ``lon``.
    """
    body = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    if not isinstance(body, dict):
        return False, False, ()
    section = body.get("preconditioning_schedule_data")
    if not isinstance(section, dict):
        return False, False, ()
    rows = section.get("precondition_schedules")
    if not isinstance(rows, list):
        return True, False, ()
    parsed = tuple(_row(item) for item in rows if isinstance(item, dict))
    return True, True, parsed


def departure_satisfied(kind: str, observed, extra_args: list[str]) -> bool:
    if not getattr(observed, "departure_schedule_list_present", False):
        return False
    rows = getattr(observed, "departure_schedules", ()) or ()
    if kind == "departure_schedule_set":
        return any(_row_matches_add(row, extra_args) for row in rows)
    if kind == "departure_schedule_remove":
        if len(extra_args) < 2 or extra_args[0] != "id":
            return False
        target = extra_args[-1]
        return all(str(row.id) != target for row in rows)
    return False


def matching_ids(rows, extra_args: list[str]) -> list[str]:
    found = []
    for row in rows:
        if row.id is None:
            continue
        if _row_matches_add(row, extra_args):
            found.append(str(row.id))
    return found


def format_departure_report(report: DepartureReport) -> str:
    if report.skipped:
        return f"state: {report.state}\nskipped: the vehicle is not online, so it was not woken"
    if not report.section_present:
        return "state: online\npreconditioning_schedule_data was not in the response"
    if not report.list_present:
        return "state: online\npreconditioning_schedule_data did not include precondition_schedules"
    if not report.schedules:
        return "state: online\nno departure schedules"
    lines = ["state: online"]
    lines.extend(_format_row(row) for row in report.schedules)
    return "\n".join(lines)


def format_departure_report_json(report: DepartureReport) -> str:
    return json.dumps(report.as_dict(), indent=2)


def _format_row(row: DepartureRow) -> str:
    identity = f"id {row.id}" if row.id is not None else "id ?"
    if row.name:
        identity = f"{identity} {row.name}"
    state = "enabled" if row.enabled else "disabled"
    repeat = "once" if row.one_time else "weekly"
    days = format_days(row.days_of_week) or "?"
    place = ""
    if row.latitude is not None or row.longitude is not None:
        place = f", lat {_num(row.latitude)} lon {_num(row.longitude)}"
    return f"{identity}: {state}, {days}, ready by {_format_clock(row.precondition_time)}, {repeat}{place}"


def _format_clock(minutes: int | None) -> str:
    if minutes is None:
        return "?"
    if minutes < 0 or minutes > 1439:
        return str(minutes)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _row(raw: dict) -> DepartureRow:
    return DepartureRow(
        id=_whole(raw.get("id")),
        name=_name(raw.get("name")),
        days_of_week=_days_value(raw.get("days_of_week")),
        precondition_time=_minutes_value(raw.get("precondition_time")) if "precondition_time" in raw else None,
        one_time=_flag(raw, "one_time"),
        enabled=_flag(raw, "enabled"),
        latitude=_coord(raw, "latitude", "lat"),
        longitude=_coord(raw, "longitude", "lon"),
    )


def _row_matches_add(row: DepartureRow, extra_args: list[str]) -> bool:
    try:
        expected = _expected_add(extra_args)
    except ConfigError:
        return False
    if expected is None or row.days_of_week != expected.days_mask:
        return False
    if row.enabled != expected.enabled or row.one_time != expected.once:
        return False
    if row.precondition_time != expected.ready_minutes:
        return False
    if not _close(row.latitude, expected.latitude) or not _close(row.longitude, expected.longitude):
        return False
    return True


def _expected_add(extra_args: list[str]) -> DepartureWrite | None:
    if len(extra_args) < 4:
        return None
    once = False
    enabled = True
    schedule_id = None
    if len(extra_args) >= 5:
        once = extra_args[4] == "once"
    if len(extra_args) >= 6 and extra_args[5] != "":
        schedule_id = extra_args[5]
    if len(extra_args) >= 7:
        enabled = extra_args[6] == "true"
    return parse_departure_write(
        extra_args[0],
        extra_args[1],
        extra_args[2],
        extra_args[3],
        once=once,
        enabled=enabled,
        schedule_id=schedule_id,
    )


def _close(actual: float | None, expected: str) -> bool:
    if actual is None:
        return False
    try:
        return abs(float(actual) - float(expected)) <= 0.0001
    except (TypeError, ValueError):
        return False


def _flag(raw: dict, key: str) -> bool:
    value = raw.get(key)
    return value if isinstance(value, bool) else False


def _whole(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _minutes_value(value) -> int | None:
    number = _whole(value)
    if number is None:
        return None
    return number


def _days_value(value) -> int | None:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return parse_days(text)[1]
        except ConfigError:
            return _whole(text)
    return _whole(value)


def _coord(raw: dict, *keys: str) -> float | None:
    for key in keys:
        if key not in raw:
            continue
        value = raw.get(key)
        if isinstance(value, bool) or value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if math.isfinite(number):
            return number
        return None
    return None


def _name(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    return text or None


def _num(value: float | None) -> str:
    if value is None:
        return "?"
    try:
        return parse_degree(str(value))
    except ConfigError:
        return str(value)


_TIME_ERROR = "time must look like 07:30"
