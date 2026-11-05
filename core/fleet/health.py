"""Read-only health alerts. This module does not wake a vehicle or call the network."""

from __future__ import annotations

from dataclasses import dataclass, field

from core.fleet.snapshot import Observation

DEFAULT_TYRE_BAR = 2.5
DEFAULT_LOW_BATTERY_PERCENT = 30

_CHARGING_NOW = {"charging", "starting"}
_TYRE_POSITIONS = (
    ("front left", "tyre_fl"),
    ("front right", "tyre_fr"),
    ("rear left", "tyre_rl"),
    ("rear right", "tyre_rr"),
)


@dataclass(frozen=True)
class Alert:
    code: str
    message: str


@dataclass
class CheckReport:
    ok: bool
    state: str
    skipped: bool
    alerts: list[Alert] = field(default_factory=list)
    tyre_bar: float = DEFAULT_TYRE_BAR
    low_battery_percent: int = DEFAULT_LOW_BATTERY_PERCENT

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "skipped": self.skipped,
            "state": self.state,
            "tyre_bar": self.tyre_bar,
            "low_battery_percent": self.low_battery_percent,
            "alerts": [{"code": item.code, "message": item.message} for item in self.alerts],
        }


def skipped_report(state: str, *, tyre_bar: float, low_battery_percent: int) -> CheckReport:
    label = state or "unknown"
    return CheckReport(
        ok=True,
        state=label,
        skipped=True,
        alerts=[],
        tyre_bar=tyre_bar,
        low_battery_percent=low_battery_percent,
    )


def evaluate_alerts(
    observation: Observation,
    *,
    tyre_bar: float = DEFAULT_TYRE_BAR,
    low_battery_percent: int = DEFAULT_LOW_BATTERY_PERCENT,
) -> list[Alert]:
    """Actionable alerts only. Coordinates and the VIN are not part of an alert."""
    alerts: list[Alert] = []
    if observation.tpms_soft_warning is True or observation.tpms_hard_warning is True:
        alerts.append(Alert("tpms_warning", "A tyre pressure warning is present. Check the tyres before driving."))
    low = _low_tyres(observation, tyre_bar)
    if low:
        listed = ", ".join(low)
        alerts.append(
            Alert(
                "tyre_pressure",
                f"Tyre pressure is below {tyre_bar:g} bar ({listed}). Inflate the tyres before driving.",
            )
        )
    if _low_battery(observation, low_battery_percent):
        alerts.append(
            Alert(
                "low_battery",
                f"Battery is at {observation.battery_level} percent and the vehicle is not charging. Plug it in or start charging.",
            )
        )
    if _charge_error(observation):
        state = observation.charging_state or "error"
        alerts.append(Alert("charge_error", f"Charging reported an error ({state}). Check the cable and the charge port."))
    if _parked(observation):
        if observation.locked is False:
            alerts.append(Alert("unlocked", "The vehicle is unlocked while parked. Lock it if nobody is with it."))
        if observation.open_closure is True:
            alerts.append(Alert("open", "A door, trunk, frunk, or window is open while the vehicle is parked. Close it."))
    return alerts


def report_from_observation(
    observation: Observation,
    *,
    tyre_bar: float,
    low_battery_percent: int,
) -> CheckReport:
    alerts = evaluate_alerts(observation, tyre_bar=tyre_bar, low_battery_percent=low_battery_percent)
    return CheckReport(
        ok=not alerts,
        state="online",
        skipped=False,
        alerts=alerts,
        tyre_bar=tyre_bar,
        low_battery_percent=low_battery_percent,
    )


def _parked(observation: Observation) -> bool:
    if observation.shift_state != "P":
        return False
    if observation.speed is None:
        return False
    try:
        return float(observation.speed) == 0
    except (TypeError, ValueError):
        return False


def _low_battery(observation: Observation, threshold: int) -> bool:
    if observation.battery_level is None or observation.battery_level >= threshold:
        return False
    charging = (observation.charging_state or "").strip().lower()
    return charging not in _CHARGING_NOW


def _charge_error(observation: Observation) -> bool:
    charging = (observation.charging_state or "").strip().lower()
    if not charging:
        return False
    return "error" in charging or charging == "nopower"


def _low_tyres(observation: Observation, threshold: float) -> list[str]:
    found: list[str] = []
    for label, name in _TYRE_POSITIONS:
        value = getattr(observation, name)
        if value is None:
            continue
        try:
            pressure = float(value)
        except (TypeError, ValueError):
            continue
        # A zero reading usually means the sensor has not reported, not a flat tyre.
        if pressure > 0 and pressure < threshold:
            found.append(label)
    return found
