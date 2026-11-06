"""Read-only daily vehicle briefing.

This module does not call the network and it does not wake a vehicle.
The command line reads the same parked snapshot as ``check`` and formats it here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from core.fleet.snapshot import Observation

MILES_TO_KM = 1.609344

# Shared with ``check`` so a skipped report and a skipped health check match.
SKIP_DETAIL = "skipped: the vehicle is not online, so it was not woken"


def skip_text(state: str) -> str:
    """The skip line ``check`` prints when it does not wake the vehicle."""
    return f"state: {state}\n{SKIP_DETAIL}"


@dataclass
class VehicleReport:
    """A briefing the assistant can forward, or the shared skip line."""

    skipped: bool
    state: str
    text: str
    fields: dict = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return 0

    def as_dict(self) -> dict:
        payload = {"skipped": self.skipped, "state": self.state, "text": self.text}
        payload.update(self.fields)
        return payload


def skipped_vehicle_report(state: str) -> VehicleReport:
    label = state or "unknown"
    return VehicleReport(skipped=True, state=label, text=skip_text(label))


def vehicle_report(name: str, observation: Observation) -> VehicleReport:
    """Build a full briefing from a parked snapshot. Coordinates are not read."""
    label = " ".join(name.split()) or "Vehicle"
    charging = observation.charging_state or "?"
    lines = [
        f"Vehicle report: {label}",
        (
            f"Battery: {_pct(observation.battery_level)} | Range: {_km(observation.battery_range_miles)} | "
            f"Limit: {_pct(observation.charge_limit_soc)}"
        ),
        f"Charge connection: {charging}",
        f"Locked: {_yes_no(observation.locked)}",
        f"Gear: {_gear(observation)}",
        f"Doors, windows, and trunk: {_closures(observation)}",
        f"Sentry: {_on_off(observation.sentry_on)}",
        f"Climate: {_climate(observation)}",
        f"Inside: {_num(observation.inside_temp)}°C | Outside: {_num(observation.outside_temp)}°C",
        (
            f"Tyres (bar): FL {_num(observation.tyre_fl)} FR {_num(observation.tyre_fr)} "
            f"RL {_num(observation.tyre_rl)} RR {_num(observation.tyre_rr)}"
        ),
        f"TPMS: {_tpms(observation)}",
    ]
    text = "\n".join(lines)
    fields = {
        "name": label,
        "battery_percent": observation.battery_level,
        "range_km": _range_km(observation.battery_range_miles),
        "charge_limit_percent": observation.charge_limit_soc,
        "charging_state": observation.charging_state,
        "locked": observation.locked,
        "shift_state": observation.shift_state,
        "speed": observation.speed,
        "parked": _parked(observation),
        "closures": _closures(observation),
        "sentry": observation.sentry_on,
        "climate_on": observation.climate_on,
        "driver_temp_c": observation.driver_temp,
        "inside_temp_c": observation.inside_temp,
        "outside_temp_c": observation.outside_temp,
        "tyres_bar": {
            "fl": observation.tyre_fl,
            "fr": observation.tyre_fr,
            "rl": observation.tyre_rl,
            "rr": observation.tyre_rr,
        },
        "tpms": _tpms(observation),
    }
    return VehicleReport(skipped=False, state="online", text=text, fields=fields)


def format_report(report: VehicleReport) -> str:
    return report.text


def format_report_json(report: VehicleReport) -> str:
    return json.dumps(report.as_dict(), indent=2)


def _parked(observation: Observation) -> bool:
    if observation.shift_state != "P" or observation.speed is None:
        return False
    try:
        return float(observation.speed) == 0
    except (TypeError, ValueError):
        return False


def _gear(observation: Observation) -> str:
    shift = observation.shift_state or "?"
    if _parked(observation):
        return "P (parked)"
    if observation.speed is None:
        return shift
    try:
        speed = f"{float(observation.speed):g}"
    except (TypeError, ValueError):
        return shift
    return f"{shift} (speed {speed})"


def _closures(observation: Observation) -> str:
    if observation.open_closure is True:
        details: list[str] = []
        if observation.frunk_open is True:
            details.append("front trunk")
        if observation.trunk_open is True:
            details.append("trunk")
        if details:
            return "open (" + ", ".join(details) + ")"
        return "open"
    if observation.open_closure is False:
        return "closed"
    return "?"


def _pct(value) -> str:
    if value is None:
        return "?"
    return f"{value}%"


def _num(value) -> str:
    if value is None:
        return "?"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "?"


def _range_km(miles) -> int | None:
    if miles is None:
        return None
    try:
        return int(round(float(miles) * MILES_TO_KM))
    except (TypeError, ValueError):
        return None


def _km(miles) -> str:
    kilometers = _range_km(miles)
    if kilometers is None:
        return "?"
    return f"{kilometers} km"


def _yes_no(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "?"


def _on_off(value: bool | None) -> str:
    if value is True:
        return "on"
    if value is False:
        return "off"
    return "?"


def _climate(observation: Observation) -> str:
    if observation.climate_on is True:
        state = "on"
    elif observation.climate_on is False:
        state = "off"
    else:
        state = "?"
    return f"{state} (set {_num(observation.driver_temp)}°C)"


def _tpms(observation: Observation) -> str:
    if observation.tpms_soft_warning is True or observation.tpms_hard_warning is True:
        return "warning"
    if observation.tpms_soft_warning is False or observation.tpms_hard_warning is False:
        return "ok"
    return "?"
