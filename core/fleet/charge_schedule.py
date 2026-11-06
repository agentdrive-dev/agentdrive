"""In-car charging timetable.

The read is Fleet API ``vehicle_data`` with ``charge_schedule_data``.
Writes go through tesla-control, which signs ``add_charge_schedule`` and
``remove_charge_schedule``. Field names and the command shape come from
that tool and from the ChargeSchedule message in vehicle-command.

``set_scheduled_charging`` is a different, older command. Tesla's command
docs say it is not recommended beginning with firmware 2024.26 and that
``add_charge_schedule`` should be used instead. This module does not call it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from core.errors import ConfigError

# tesla-control dayNamesBitMask. Sunday is the low bit.
_DAY_BITS = {
    "SUN": 1,
    "SUNDAY": 1,
    "MON": 2,
    "MONDAY": 2,
    "TUES": 4,
    "TUESDAY": 4,
    "WED": 8,
    "WEDNESDAY": 8,
    "THURS": 16,
    "THURSDAY": 16,
    "FRI": 32,
    "FRIDAY": 32,
    "SAT": 64,
    "SATURDAY": 64,
    "ALL": 127,
    "WEEKDAYS": 62,
}
_DAY_LABELS = (
    (1, "Sun"),
    (2, "Mon"),
    (4, "Tues"),
    (8, "Wed"),
    (16, "Thurs"),
    (32, "Fri"),
    (64, "Sat"),
)
_ALL_DAYS = 127
_WEEKDAYS = 62
_UINT64_MAX = 18446744073709551615

# charging-schedule-add optionals are positional: REPEAT, ID, ENABLED.
# The handler applies REPEAT when it is "once" and applies ENABLED.
# It does not copy ID; it stores the current unix time and prints that id.
# "weekly" is only a placeholder so ENABLED can be reached. It is not a
# protocol field. Any REPEAT other than "once" leaves one_time false.
# "0" fills the ignored ID slot. The proxy treats id 0 as "assign one".
_REPEAT_WEEKLY = "weekly"
_IGNORED_ID_SLOT = "0"


@dataclass(frozen=True)
class ScheduleWrite:
    days: str
    time_range: str
    latitude: str
    longitude: str
    once: bool
    enabled: bool
    days_mask: int
    start_enabled: bool
    start_minutes: int | None
    end_enabled: bool
    end_minutes: int | None

    def tool_args(self) -> list[str]:
        args = ["charging-schedule-add", self.days, self.time_range, self.latitude, self.longitude]
        if self.once and self.enabled:
            args.append("once")
        elif not self.enabled:
            args.append("once" if self.once else _REPEAT_WEEKLY)
            args.extend([_IGNORED_ID_SLOT, "false"])
        return args


@dataclass(frozen=True)
class ChargeScheduleRow:
    id: int | None
    name: str | None
    days_of_week: int | None
    start_enabled: bool
    start_time: int | None
    end_enabled: bool
    end_time: int | None
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
            "start_enabled": self.start_enabled,
            "start_time": self.start_time,
            "end_enabled": self.end_enabled,
            "end_time": self.end_time,
            "one_time": self.one_time,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }
        if self.name:
            body["name"] = self.name
        return body


@dataclass(frozen=True)
class ScheduleReport:
    state: str
    skipped: bool
    section_present: bool
    list_present: bool
    schedules: tuple[ChargeScheduleRow, ...]

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "skipped": self.skipped,
            "charge_schedule_data": self.section_present,
            "charge_schedules": self.list_present,
            "schedules": [row.as_dict() for row in self.schedules],
        }


def parse_days(value: str) -> tuple[str, int]:
    text = " ".join(str(value).split())
    if not text:
        raise ConfigError(_DAYS_ERROR)
    mask = 0
    for part in text.split(","):
        token = part.strip().upper()
        if token not in _DAY_BITS:
            raise ConfigError(_DAYS_ERROR)
        mask |= _DAY_BITS[token]
    if mask == 0:
        raise ConfigError(_DAYS_ERROR)
    return format_days(mask), mask


def parse_time_window(value: str) -> tuple[str, bool, int | None, bool, int | None]:
    text = str(value).strip()
    parts = text.split("-")
    if len(parts) != 2:
        raise ConfigError(_TIME_ERROR)
    start_enabled, start_minutes = _clock(parts[0])
    end_enabled, end_minutes = _clock(parts[1])
    if not start_enabled and not end_enabled:
        raise ConfigError(_TIME_ERROR)
    window = f"{_format_clock(start_enabled, start_minutes)}-{_format_clock(end_enabled, end_minutes)}"
    return window, start_enabled, start_minutes, end_enabled, end_minutes


def parse_degree(value: str) -> str:
    text = str(value).strip()
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise ConfigError("latitude and longitude must both be in the range -180 to 180") from exc
    if not math.isfinite(number) or number < -180 or number > 180:
        raise ConfigError("latitude and longitude must both be in the range -180 to 180")
    rendered = f"{number:.6f}".rstrip("0").rstrip(".")
    if rendered in {"", "-", "-0"}:
        return "0"
    return rendered


def parse_schedule_id(value: str) -> str:
    text = str(value).strip()
    if not text.isdigit():
        raise ConfigError("schedule id must be a positive whole number")
    number = int(text)
    if number <= 0 or number > _UINT64_MAX:
        raise ConfigError("schedule id must be a positive whole number")
    return str(number)


def parse_schedule_write(days: str, time_range: str, latitude: str, longitude: str, *, once: bool, enabled: bool) -> ScheduleWrite:
    day_text, day_mask = parse_days(days)
    window, start_enabled, start_minutes, end_enabled, end_minutes = parse_time_window(time_range)
    return ScheduleWrite(
        days=day_text,
        time_range=window,
        latitude=parse_degree(latitude),
        longitude=parse_degree(longitude),
        once=bool(once),
        enabled=bool(enabled),
        days_mask=day_mask,
        start_enabled=start_enabled,
        start_minutes=start_minutes,
        end_enabled=end_enabled,
        end_minutes=end_minutes,
    )


def schedules_from_vehicle_data(payload: dict) -> tuple[bool, bool, tuple[ChargeScheduleRow, ...]]:
    """Return section presence, list presence, and rows.

    The documented container is ``charge_schedule_data``. Each row uses the
    ChargeSchedule fields. Latitude and longitude are also accepted as
    ``lat`` and ``lon``, which is what the signed command calls them.
    """
    body = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    if not isinstance(body, dict):
        return False, False, ()
    section = body.get("charge_schedule_data")
    if not isinstance(section, dict):
        return False, False, ()
    rows = section.get("charge_schedules")
    if not isinstance(rows, list):
        return True, False, ()
    parsed = tuple(_row(item) for item in rows if isinstance(item, dict))
    return True, True, parsed


def charge_schedule_satisfied(kind: str, observed, extra_args: list[str]) -> bool:
    if not getattr(observed, "charge_schedule_list_present", False):
        return False
    rows = getattr(observed, "charge_schedules", ()) or ()
    if kind == "charge_schedule_set":
        return any(_row_matches_add(row, extra_args) for row in rows)
    if kind == "charge_schedule_remove":
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


def format_days(mask: int | None) -> str | None:
    if mask is None:
        return None
    if mask == _ALL_DAYS:
        return "all"
    if mask == _WEEKDAYS:
        return "weekdays"
    if mask <= 0 or mask & ~_ALL_DAYS:
        return str(mask)
    names = [label for bit, label in _DAY_LABELS if mask & bit]
    if not names:
        return str(mask)
    return ",".join(names)


def format_schedule_report(report: ScheduleReport) -> str:
    if report.skipped:
        return f"state: {report.state}\nskipped: the vehicle is not online, so it was not woken"
    if not report.section_present:
        return "state: online\ncharge_schedule_data was not in the response"
    if not report.list_present:
        return "state: online\ncharge_schedule_data did not include charge_schedules"
    if not report.schedules:
        return "state: online\nno charge schedules"
    lines = ["state: online"]
    lines.extend(_format_row(row) for row in report.schedules)
    return "\n".join(lines)


def format_schedule_report_json(report: ScheduleReport) -> str:
    return json.dumps(report.as_dict(), indent=2)


def _format_row(row: ChargeScheduleRow) -> str:
    identity = f"id {row.id}" if row.id is not None else "id ?"
    if row.name:
        identity = f"{identity} {row.name}"
    state = "enabled" if row.enabled else "disabled"
    repeat = "once" if row.one_time else "weekly"
    days = format_days(row.days_of_week) or "?"
    window = f"{_format_clock(row.start_enabled, row.start_time)}-{_format_clock(row.end_enabled, row.end_time)}"
    place = ""
    if row.latitude is not None or row.longitude is not None:
        place = f", lat {_num(row.latitude)} lon {_num(row.longitude)}"
    return f"{identity}: {state}, {days}, {window}, {repeat}{place}"


def _format_clock(enabled: bool, minutes: int | None) -> str:
    if not enabled:
        return ""
    if minutes is None:
        return "?"
    return _minutes_clock(minutes)


def _minutes_clock(minutes: int) -> str:
    if minutes < 0 or minutes > 1439:
        return str(minutes)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _clock(part: str) -> tuple[bool, int | None]:
    text = part.strip()
    if text == "":
        return False, None
    pieces = text.split(":")
    if len(pieces) != 2 or pieces[0] == "" or pieces[1] == "":
        raise ConfigError(_TIME_ERROR)
    if not pieces[0].isdigit() or not pieces[1].isdigit():
        raise ConfigError(_TIME_ERROR)
    hours = int(pieces[0])
    minutes = int(pieces[1])
    if hours > 23 or minutes > 59:
        raise ConfigError(_TIME_ERROR)
    return True, hours * 60 + minutes


def _row(raw: dict) -> ChargeScheduleRow:
    return ChargeScheduleRow(
        id=_whole(raw.get("id")),
        name=_name(raw.get("name")),
        days_of_week=_days_value(raw.get("days_of_week")),
        start_enabled=_flag(raw, "start_enabled"),
        start_time=_minutes_value(raw.get("start_time")) if "start_time" in raw else None,
        end_enabled=_flag(raw, "end_enabled"),
        end_time=_minutes_value(raw.get("end_time")) if "end_time" in raw else None,
        one_time=_flag(raw, "one_time"),
        enabled=_flag(raw, "enabled"),
        latitude=_coord(raw, "latitude", "lat"),
        longitude=_coord(raw, "longitude", "lon"),
    )


def _row_matches_add(row: ChargeScheduleRow, extra_args: list[str]) -> bool:
    try:
        expected = _expected_add(extra_args)
    except ConfigError:
        return False
    if expected is None or row.days_of_week != expected.days_mask:
        return False
    if row.enabled != expected.enabled or row.one_time != expected.once:
        return False
    if row.start_enabled != expected.start_enabled or row.end_enabled != expected.end_enabled:
        return False
    if expected.start_enabled and row.start_time != expected.start_minutes:
        return False
    if expected.end_enabled and row.end_time != expected.end_minutes:
        return False
    if not _close(row.latitude, expected.latitude) or not _close(row.longitude, expected.longitude):
        return False
    return True


def _expected_add(extra_args: list[str]) -> ScheduleWrite | None:
    if len(extra_args) < 4:
        return None
    once = False
    enabled = True
    if len(extra_args) >= 5:
        once = extra_args[4] == "once"
    if len(extra_args) >= 7:
        enabled = extra_args[6] == "true"
    return parse_schedule_write(
        extra_args[0],
        extra_args[1],
        extra_args[2],
        extra_args[3],
        once=once,
        enabled=enabled,
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


_DAYS_ERROR = (
    "days must be all, weekdays, or a comma-separated list of "
    "Sun, Mon, Tues, Wed, Thurs, Fri, Sat"
)
_TIME_ERROR = "time must look like 22:00-06:00, 22:00-, or -06:00"
