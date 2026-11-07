"""Park check, confirmation, signed command, then a fresh read."""

from __future__ import annotations

from core.errors import ConfigError, ScheduleBlocked, VerificationFailed
from core.fleet.commands import TeslaControl
from core.fleet.charge_schedule import charge_schedule_satisfied
from core.fleet.snapshot import SEAT_HEATER_LEVELS, Observation, defrost_state
from core.safety.catalog import CommandSpec, load_specs
from core.safety.confirm import Confirmation, ConfirmationBroker, new_challenge
from core.safety.parked import require_parked


class Gateway:
    def __init__(self, runner: TeslaControl, specs: dict[str, CommandSpec] | None = None, broker: ConfirmationBroker | None = None):
        self.runner = runner
        self.specs = specs or load_specs()
        self.broker = broker or ConfirmationBroker()

    def execute(
        self,
        command: str,
        *,
        shift_state: str | None,
        speed,
        adapter,
        via_schedule: bool,
        vin: str,
        access_token: str,
        observe,
        extra_args: list[str] | None = None,
    ) -> Observation:
        spec = self.specs.get(command)
        if spec is None:
            raise ConfigError("That command is not available.")
        require_parked(shift_state, speed)
        response: Confirmation | None = None
        if spec.tier == "sensitive":
            if via_schedule:
                raise ScheduleBlocked("A schedule cannot run a sensitive command.")
            challenge = new_challenge(command, spec.action)
            response = adapter.confirm(challenge)
            self.broker.authorize(spec, challenge, response, via_schedule=False)
        args = list(spec.tool_args)
        if extra_args:
            args.extend(extra_args)
        self.runner.run(vin=vin, access_token=access_token, args=args)
        try:
            observed = observe()
        except Exception as exc:
            raise VerificationFailed("The follow-up read failed. The command was not sent again.") from exc
        if not isinstance(observed, Observation):
            raise VerificationFailed("The follow-up read did not return vehicle state.")
        require_parked(observed.shift_state, observed.speed)
        _verify(spec.verify, observed, extra_args or [])
        return observed


def _verify(kind: str, observed: Observation, extra_args: list[str]) -> None:
    if kind == "parked_only":
        return
    if kind == "climate_on" and observed.climate_on is True:
        return
    if kind == "climate_off" and observed.climate_on is False:
        return
    if kind == "driver_temp" and extra_args and observed.driver_temp is not None:
        try:
            if abs(float(observed.driver_temp) - float(extra_args[0])) < 0.2:
                return
        except (TypeError, ValueError):
            pass
    if kind == "locked" and observed.locked is True:
        return
    if kind == "unlocked" and observed.locked is False:
        return
    if kind == "frunk_open" and observed.frunk_open is True:
        return
    if kind == "trunk_open" and observed.trunk_open is True:
        return
    if kind == "trunk_closed" and observed.trunk_open is False:
        return
    if kind == "sentry_on" and observed.sentry_on is True:
        return
    if kind == "sentry_off" and observed.sentry_on is False:
        return
    if kind == "seat_heater" and _seat_matches(observed, extra_args):
        return
    if kind == "steering_heater" and _steering_matches(observed, extra_args):
        return
    if kind == "defrost_on" and defrost_state(observed.defrost_mode, observed.front_defroster_on, observed.rear_defroster_on) == "max":
        return
    if kind == "defrost_off" and defrost_state(observed.defrost_mode, observed.front_defroster_on, observed.rear_defroster_on) == "off":
        return
    if kind == "charging" and observed.charging_state == "Charging":
        return
    if kind == "not_charging" and observed.charging_state not in (None, "Charging"):
        return
    if kind == "charge_limit" and extra_args and observed.charge_limit_soc is not None:
        if str(observed.charge_limit_soc) == str(extra_args[0]):
            return
    if kind == "charge_amps" and extra_args and observed.charge_amps is not None:
        if str(observed.charge_amps) == str(extra_args[0]):
            return
    if kind in {"charge_schedule_set", "charge_schedule_remove"} and charge_schedule_satisfied(kind, observed, extra_args):
        return
    raise VerificationFailed("The vehicle state after the command did not match the request. The command was not sent again.")


def _seat_matches(observed: Observation, extra_args: list[str]) -> bool:
    if len(extra_args) < 2:
        return False
    expected = SEAT_HEATER_LEVELS.get(extra_args[1])
    if expected is None:
        return False
    heaters = observed.seat_heat or {}
    return heaters.get(extra_args[0]) == expected


def _steering_matches(observed: Observation, extra_args: list[str]) -> bool:
    if not extra_args or extra_args[0] not in {"on", "off"}:
        return False
    if observed.steering_heater is None:
        return False
    return observed.steering_heater is (extra_args[0] == "on")
