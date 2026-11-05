"""Normalize a vehicle_data payload into the fields the safety checks need.

Latitude and longitude are ignored on purpose so a readout cannot be copied
into assistant memory by accident.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    battery_range_miles: float | None = None
    open_closure: bool | None = None


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


def from_vehicle_data(payload: dict) -> Observation:
    drive = _section(payload, "drive_state")
    vehicle = _section(payload, "vehicle_state")
    climate = _section(payload, "climate_state")
    charge = _section(payload, "charge_state")
    amps = charge.get("charge_amps", charge.get("charge_current_request"))
    shift = drive.get("shift_state")
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
        battery_range_miles=_optional_float(charge.get("battery_range")),
        open_closure=_any_open(vehicle),
    )
