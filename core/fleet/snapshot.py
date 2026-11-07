"""Normalize a vehicle_data payload into the fields the safety checks need.

Drive-state latitude and longitude are ignored on purpose so a readout cannot
be copied into assistant memory by accident. Charging-timetable coordinates
stay on the schedule rows, which only the charge-schedule command prints.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.fleet.charge_schedule import ChargeScheduleRow, schedules_from_vehicle_data


@dataclass
class Observation:
    shift_state: str | None = None
    speed: float | None = None
    locked: bool | None = None
    climate_on: bool | None = None
    frunk_open: bool | None = None
    trunk_open: bool | None = None
    sentry_on: bool | None = None
    battery_level: int | None = None
    charging_state: str | None = None
    charge_limit_soc: int | None = None
    charge_amps: int | None = None
    tpms_soft_warning: bool | None = None
    tpms_hard_warning: bool | None = None
    tyre_fl: float | None = None
    tyre_fr: float | None = None
    tyre_rl: float | None = None
    tyre_rr: float | None = None
    driver_temp: float | None = None
    inside_temp: float | None = None
    outside_temp: float | None = None
    seat_heat: dict[str, int] | None = None
    steering_heater: bool | None = None
    defrost_mode: int | str | None = None
    front_defroster_on: bool | None = None
    rear_defroster_on: bool | None = None
    battery_range_miles: float | None = None
    open_closure: bool | None = None
    charge_schedules: tuple[ChargeScheduleRow, ...] = ()
    charge_schedule_section_present: bool = False
    charge_schedule_list_present: bool = False


def _section(payload: dict, name: str) -> dict:
    body = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    if not isinstance(body, dict):
        return {}
    section = body.get(name)
    return section if isinstance(section, dict) else {}


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _open_closure(value) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return None


_SOFT_WARNINGS = (
    "tpms_soft_warning_fl",
    "tpms_soft_warning_fr",
    "tpms_soft_warning_rl",
    "tpms_soft_warning_rr",
)
_HARD_WARNINGS = (
    "tpms_hard_warning_fl",
    "tpms_hard_warning_fr",
    "tpms_hard_warning_rl",
    "tpms_hard_warning_rr",
)
_CLOSURE_KEYS = (
    "df",
    "dr",
    "pf",
    "pr",
    "ft",
    "rt",
    "fd_window",
    "fp_window",
    "rd_window",
    "rp_window",
)


def _flag_group(vehicle: dict, keys: tuple[str, ...]) -> bool | None:
    values = [vehicle.get(key) for key in keys if key in vehicle]
    if not values:
        return None
    if any(item is True for item in values):
        return True
    if any(item is False for item in values):
        return False
    return None


def _any_open(vehicle: dict) -> bool | None:
    present = [key for key in _CLOSURE_KEYS if key in vehicle]
    if not present:
        return None
    opened = [_open_closure(vehicle.get(key)) for key in present]
    if any(item is True for item in opened):
        return True
    if all(item is False for item in opened):
        return False
    return None


# tesla-control `seat-heater` names. Backrest heaters are not in that command.
SEAT_API_FIELDS = {
    "seat_heater_left": "front-left",
    "seat_heater_right": "front-right",
    "seat_heater_rear_left": "2nd-row-left",
    "seat_heater_rear_center": "2nd-row-center",
    "seat_heater_rear_right": "2nd-row-right",
    "seat_heater_third_row_left": "3rd-row-left",
    "seat_heater_third_row_right": "3rd-row-right",
}
SEAT_HEATER_LEVELS = {"off": 0, "low": 1, "medium": 2, "high": 3}


def seat_heater_names() -> tuple[str, ...]:
    return tuple(SEAT_API_FIELDS.values())


def _heater_level(value) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 0 or number > 3:
        return None
    return number


def _seat_heat(climate: dict) -> dict[str, int]:
    heaters: dict[str, int] = {}
    for field, name in SEAT_API_FIELDS.items():
        level = _heater_level(climate.get(field))
        if level is not None:
            heaters[name] = level
    return heaters


def _optional_mode(value) -> int | str | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def defrost_state(mode, front: bool | None, rear: bool | None) -> str | None:
    """Return ``max``, ``off``, or None when the climate reading does not say.

    Max defrost is ``defrost_mode`` 2 in the Fleet API climate state, and 3 in
    the command protocol's Max enum. Mode 0 is off. Front or rear defroster
    flags are the same reading when the mode number is absent.
    """
    mode_max = False
    mode_off = False
    if isinstance(mode, str):
        text = mode.strip().lower()
        mode_max = text in {"max", "2", "3"}
        mode_off = text in {"off", "0"}
    elif isinstance(mode, (int, float)) and not isinstance(mode, bool):
        number = int(mode)
        mode_max = number in {2, 3}
        mode_off = number == 0
    if mode_max or front is True or rear is True:
        return "max"
    if mode_off or (front is False and rear is False):
        return "off"
    return None


def from_vehicle_data(payload: dict) -> Observation:
    drive = _section(payload, "drive_state")
    vehicle = _section(payload, "vehicle_state")
    climate = _section(payload, "climate_state")
    charge = _section(payload, "charge_state")
    amps = charge.get("charge_amps", charge.get("charge_current_request"))
    shift = drive.get("shift_state")
    section_present, list_present, schedules = schedules_from_vehicle_data(payload)
    return Observation(
        shift_state=str(shift) if shift not in (None, "") else None,
        speed=_optional_float(drive.get("speed")),
        locked=vehicle.get("locked") if isinstance(vehicle.get("locked"), bool) else None,
        climate_on=climate.get("is_climate_on") if isinstance(climate.get("is_climate_on"), bool) else None,
        frunk_open=_open_closure(vehicle.get("ft")),
        trunk_open=_open_closure(vehicle.get("rt")),
        sentry_on=vehicle.get("sentry_mode") if isinstance(vehicle.get("sentry_mode"), bool) else None,
        battery_level=_optional_int(charge.get("battery_level")),
        charging_state=str(charge["charging_state"]) if charge.get("charging_state") else None,
        charge_limit_soc=_optional_int(charge.get("charge_limit_soc")),
        charge_amps=_optional_int(amps),
        tpms_soft_warning=_flag_group(vehicle, _SOFT_WARNINGS),
        tpms_hard_warning=_flag_group(vehicle, _HARD_WARNINGS),
        tyre_fl=_optional_float(vehicle.get("tpms_pressure_fl")),
        tyre_fr=_optional_float(vehicle.get("tpms_pressure_fr")),
        tyre_rl=_optional_float(vehicle.get("tpms_pressure_rl")),
        tyre_rr=_optional_float(vehicle.get("tpms_pressure_rr")),
        driver_temp=_optional_float(climate.get("driver_temp_setting")),
        inside_temp=_optional_float(climate.get("inside_temp")),
        outside_temp=_optional_float(climate.get("outside_temp")),
        seat_heat=_seat_heat(climate),
        steering_heater=climate.get("steering_wheel_heater") if isinstance(climate.get("steering_wheel_heater"), bool) else None,
        defrost_mode=_optional_mode(climate.get("defrost_mode")),
        front_defroster_on=climate.get("is_front_defroster_on") if isinstance(climate.get("is_front_defroster_on"), bool) else None,
        rear_defroster_on=climate.get("is_rear_defroster_on") if isinstance(climate.get("is_rear_defroster_on"), bool) else None,
        battery_range_miles=_optional_float(charge.get("battery_range")),
        open_closure=_any_open(vehicle),
        charge_schedules=schedules,
        charge_schedule_section_present=section_present,
        charge_schedule_list_present=list_present,
    )
