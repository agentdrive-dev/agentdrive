"""Vehicle reads and signed commands used by the command line.

Status wakes a sleeping vehicle and polls for up to 45 seconds. The health
check, the daily report, where, charge-schedule show, departure show,
official charging history, and a software-status read never wake. Unlock
and other sensitive commands go through the safety gateway, which asks
for a one-time confirmation.

Check and report also save local history when the car is already online.
That write does not wake the car, and a history problem does not change the
check result or the report text.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from adapters.template import TemplateAdapter
from core.config import load_regions
from core.errors import CommandFailed, ConfigError, ConfirmationRequired, TokenError
from core.fleet.area import WhereReport, area_from_vehicle_payload, precise_from_vehicle_payload, precise_point
from core.fleet.charge_schedule import (
    ScheduleReport,
    ScheduleWrite,
    matching_ids,
    parse_schedule_id,
    schedules_from_vehicle_data,
)
from core.fleet.departure import (
    DepartureReport,
    DepartureWrite,
    matching_ids as matching_departure_ids,
    schedules_from_vehicle_data as departure_schedules_from_vehicle_data,
)
from core.fleet.charging import format_history, format_invoice_saved, format_invoice_skipped, parse_invoice_id
from core.fleet.commands import TeslaControl
from core.fleet.failures import clarify_command_failure
from core.fleet.credentials import load_client_credentials
from core.fleet.check_state import apply_rising_edge
from core.fleet.health import (
    DEFAULT_LOW_BATTERY_PERCENT,
    DEFAULT_TYRE_BAR,
    CheckReport,
    report_from_observation,
    skipped_report,
)
from core.fleet.http import HttpError, UrllibClient
from core.fleet.queries import FleetReader, QueryError, VehicleAsleep, location_scope_denied
from core.fleet.report import VehicleReport, skipped_vehicle_report, skip_text, vehicle_report
from core.fleet.snapshot import (
    SEAT_API_FIELDS,
    SEAT_COOLER_LEVELS,
    SEAT_COOLER_NAMES,
    SEAT_HEATER_LEVELS,
    Observation,
    cable_plugged,
    driver_seat_text,
    from_vehicle_data,
    seat_heater_names,
)
from core.fleet.software import format_software, format_software_json, notes_from
from core.fleet.tokens import AtomicTokenStore, FleetTokenRefresher, TokenManager, audience_for
from core.garage import resolve_vehicle
from core.paths import resolve_key, resolve_token_file
from core.profile.owner import load_profile
from core.redact import mask_vin, redact
from core.safety.gateway import Gateway

MILES_TO_KM = 1.609344
DATA_ENDPOINTS = ["charge_state", "climate_state", "vehicle_state", "drive_state"]
CHARGE_SCHEDULE_ENDPOINT = "charge_schedule_data"
DEPARTURE_ENDPOINT = "preconditioning_schedule_data"
CHARGE_AMPS_MIN = 1
CHARGE_AMPS_MAX = 80
LOCATION_ENDPOINTS = ["location_data"]

PUBLIC_COMMANDS = {
    "wake": "wake",
    "flash": "flash_lights",
    "honk": "honk",
    "lock": "lock",
    "unlock": "unlock",
    "climate-on": "climate_on",
    "climate-off": "climate_off",
    "set-temp": "climate_set_temp",
    "charge-start": "charging_start",
    "charge-stop": "charging_stop",
    "charge-limit": "charging_set_limit",
    "charge-amps": "charging_set_amps",
    "sentry-on": "sentry_on",
    "sentry-off": "sentry_off",
    "seat-heat": "seat_heater",
    "steering-heat": "steering_wheel_heater",
    "defrost-on": "defrost_on",
    "defrost-off": "defrost_off",
    "precondition": "cabin_precondition",
    "trunk-open": "trunk_open",
    "trunk-close": "trunk_close",
    "frunk-open": "frunk_open",
    "remote-start": "remote_start",
    "climate-keeper-off": "climate_keeper_off",
    "climate-keeper-keep": "climate_keeper_keep",
    "climate-keeper-dog": "climate_keeper_dog",
    "climate-keeper-camp": "climate_keeper_camp",
    "charge-port-open": "charge_port_open",
    "charge-port-close": "charge_port_close",
    "seat-cool": "seat_cooler",
    "seat-climate": "auto_seat_climate",
    "bioweapon-on": "bioweapon_on",
    "bioweapon-off": "bioweapon_off",
    "software-schedule": "software_schedule",
    "software-cancel": "software_cancel",
}


def parse_temp(value: str) -> str:
    text = str(value).strip()
    try:
        number = float(text)
    except (TypeError, ValueError) as exc:
        raise ConfigError("temp must be 15-28 C") from exc
    if number < 15 or number > 28:
        raise ConfigError("temp must be 15-28 C")
    if number == int(number):
        return str(int(number))
    return str(number)


def parse_seat_heat(seat: str, level: str) -> tuple[str, str]:
    """Accept only the seat names and levels tesla-control `seat-heater` accepts."""
    seat_name = " ".join(str(seat).strip().lower().split())
    level_name = str(level).strip().lower()
    allowed = ", ".join(seat_heater_names())
    if seat_name not in SEAT_API_FIELDS.values():
        raise ConfigError(f"seat must be one the signed tool accepts: {allowed}")
    if level_name not in SEAT_HEATER_LEVELS:
        raise ConfigError("seat heater level must be off, low, medium, or high")
    return seat_name, level_name


def parse_seat_cool(seat: str, level: str) -> tuple[str, str]:
    """Front seats only, at the levels the signed seat-cooler command accepts."""
    seat_name = " ".join(str(seat).strip().lower().split())
    level_name = str(level).strip().lower()
    allowed = ", ".join(SEAT_COOLER_NAMES)
    if seat_name not in SEAT_COOLER_NAMES:
        raise ConfigError(f"seat cooling supports {allowed}")
    if level_name not in SEAT_COOLER_LEVELS:
        raise ConfigError("seat cooling level must be off, low, medium, or high")
    return seat_name, level_name


def parse_seat_climate(seat: str, state: str) -> tuple[str, str]:
    """Map a front seat to the signed auto-seat-and-climate position token."""
    seat_name = " ".join(str(seat).strip().lower().split())
    state_name = str(state).strip().lower()
    tokens = {"front-left": "L", "front-right": "R", "both": "LR"}
    if seat_name not in tokens:
        raise ConfigError("automatic seat climate supports front-left, front-right, or both")
    if state_name not in {"on", "off"}:
        raise ConfigError("automatic seat climate must be on or off")
    return tokens[seat_name], state_name


_DELAY = re.compile(r"^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?$")
_DELAY_LIMIT = 7 * 24 * 3600


def parse_update_delay(value: str) -> str:
    """A tesla-control duration: 2h, 10m, 30s, 1h30m, a whole number of seconds, or now."""
    text = " ".join(str(value).strip().lower().split())
    if text in {"now", "0"}:
        return "0s"
    if text.isdigit():
        seconds = int(text)
        if seconds > _DELAY_LIMIT:
            raise ConfigError("delay must be at most 7 days")
        return f"{seconds}s"
    if not _DELAY.fullmatch(text) or not any(char.isdigit() for char in text):
        raise ConfigError("delay must be a duration such as 2h, 10m, 30s, or a whole number of seconds")
    return text


def parse_on_off(value: str, label: str) -> str:
    text = str(value).strip().lower()
    if text not in {"on", "off"}:
        raise ConfigError(f"{label} must be on or off")
    return text


def parse_limit(value: str) -> str:
    text = str(value).strip()
    if not text.isdigit():
        raise ConfigError("limit must be 50-100")
    number = int(text)
    if number < 50 or number > 100:
        raise ConfigError("limit must be 50-100")
    return str(number)


def parse_amps(value: str) -> str:
    """Whole amps from 1 to 80. The car may still use less."""
    text = str(value).strip()
    if not text.isdigit():
        raise ConfigError(f"amps must be a whole number from {CHARGE_AMPS_MIN} to {CHARGE_AMPS_MAX}")
    number = int(text)
    if number < CHARGE_AMPS_MIN or number > CHARGE_AMPS_MAX:
        raise ConfigError(f"amps must be a whole number from {CHARGE_AMPS_MIN} to {CHARGE_AMPS_MAX}")
    return str(number)


def charge_amps_result(requested: str, observed: Observation) -> str:
    """Say what was asked, and when the car clamped it to the charger."""
    setting = observed.charge_current_request
    if setting is None:
        setting = observed.charge_amps
    if setting is not None and str(setting) != str(requested):
        return (
            f"OK: charge-amps {requested}\n"
            f"The car kept {setting} A. It clamps to what the charger allows."
        )
    return f"OK: charge-amps {requested}\nThe car may clamp this to what the charger allows."


def vehicle_rows(payload: dict) -> list[dict]:
    body = payload.get("response", payload)
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if isinstance(body, dict):
        return [body]
    return []


def select_vehicle(payload: dict, preferred: str | None) -> dict:
    rows = vehicle_rows(payload)
    if not rows:
        raise QueryError("no vehicles")
    if preferred:
        for row in rows:
            if str(row.get("vin") or "") == preferred:
                return row
        raise QueryError("The requested vehicle is not on this account")
    return rows[0]


def vehicle_key(row: dict) -> str:
    vin = str(row.get("vin") or "").strip()
    if vin:
        return vin
    identifier = row.get("id", row.get("vehicle_id"))
    if identifier is None or str(identifier).strip() == "":
        raise QueryError("The vehicle record has no id")
    return str(identifier).strip()


def vehicle_state_name(row: dict) -> str:
    return str(row.get("state") or "").strip().lower()


def preferred_vin(env: dict[str, str] | None = None) -> str | None:
    import os

    current = os.environ if env is None else env
    for name in ("AGENTDRIVE_VIN", "TESLA_VIN"):
        value = str(current.get(name) or "").strip()
        if value:
            return value
    return None


def resolve_binary(home: Path) -> str:
    candidate = home / "bin" / "tesla-control"
    if candidate.is_file():
        return str(candidate)
    return "tesla-control"


def resolve_thresholds(
    home: Path,
    *,
    profile_path: Path | None = None,
    tyre_bar: float | None = None,
    low_battery_percent: int | None = None,
) -> tuple[float, int]:
    path = profile_path if profile_path is not None else home / "profile.yaml"
    profile = load_profile(path) if path.is_file() else None
    tyre = tyre_bar
    if tyre is None:
        tyre = profile.habit_float("tyre_pressure_bar", DEFAULT_TYRE_BAR) if profile else DEFAULT_TYRE_BAR
    battery = low_battery_percent
    if battery is None:
        battery = profile.habit_int("low_battery_percent", DEFAULT_LOW_BATTERY_PERCENT) if profile else DEFAULT_LOW_BATTERY_PERCENT
    return float(tyre), int(battery)


@dataclass(frozen=True)
class ParkedPeek:
    """A read that did not wake the car. ``observation`` is set only when it is already online."""

    state: str
    observation: Observation | None
    store: Path | None
    label: str


class App:
    """Wires the fleet reader, token manager, and signed-command runner."""

    def __init__(
        self,
        home: Path,
        *,
        reader: FleetReader,
        tokens: TokenManager,
        control: TeslaControl,
        region: str,
        history: bool = False,
    ):
        self.home = Path(home)
        self.reader = reader
        self.tokens = tokens
        self.control = control
        self.region = region
        self.history = history
        self.gateway = Gateway(control)
        self.car_name = ""

    def refresh(self) -> str:
        self.tokens.access_token(force=True)
        return "refreshed; tokens saved"

    def prepare(self, preferred: str | None):
        """Resolve the car from the vehicle list. This does not wake the car."""
        return self._bind(preferred)

    def status(self, *, preferred: str | None, sleep=time.sleep) -> str:
        _selection, chosen, key, observation = self._online(preferred, sleep=sleep)
        text = format_status(chosen, observation, key)
        return redact(text, [key, preferred or ""])

    def check(self, *, preferred: str | None, tyre_bar: float, low_battery_percent: int, now=None) -> CheckReport:
        selection = self._bind(preferred)
        chosen = selection.row
        state = vehicle_state_name(chosen) or "unknown"
        if state != "online":
            return skipped_report(state, tyre_bar=tyre_bar, low_battery_percent=low_battery_percent)
        payload = self.reader.vehicle_data(vehicle_key(chosen), DATA_ENDPOINTS)
        observation = from_vehicle_data(payload)
        report = report_from_observation(observation, tyre_bar=tyre_bar, low_battery_percent=low_battery_percent)
        # History records the full online reading. Notification dedupe is separate,
        # and a skipped check never reaches this write.
        self._remember(payload, observation, tyre_bar=tyre_bar, low_battery_percent=low_battery_percent, store=selection.store)
        return apply_rising_edge(selection.store, report, now=now)

    def peek(self, *, preferred: str | None) -> ParkedPeek:
        """Read battery, climate, and schedules when the car is already online.

        A car that is not online is left asleep. This never calls wake.
        """
        try:
            selection = self._bind(preferred)
        except ConfigError:
            raise
        except (VehicleAsleep, TimeoutError, HttpError, QueryError, TokenError):
            return ParkedPeek("unavailable", None, None, "")
        state = vehicle_state_name(selection.row) or "unknown"
        if state != "online":
            return ParkedPeek(state, None, selection.store, selection.label)
        try:
            payload = self.reader.vehicle_data(
                vehicle_key(selection.row),
                [*DATA_ENDPOINTS, CHARGE_SCHEDULE_ENDPOINT, DEPARTURE_ENDPOINT],
            )
        except (VehicleAsleep, TimeoutError, HttpError, QueryError, TokenError):
            return ParkedPeek("unavailable", None, selection.store, selection.label)
        observation = from_vehicle_data(payload)
        self._remember(payload, observation, store=selection.store)
        return ParkedPeek("online", observation, selection.store, selection.label)

    def report(self, *, preferred: str | None) -> VehicleReport:
        """Read-only briefing. A car that is not online, a timeout, or HTTP 408 is a skip.

        This never calls wake.
        """
        try:
            selection = self._bind(preferred)
            chosen = selection.row
        except (VehicleAsleep, TimeoutError, HttpError, QueryError) as exc:
            state = _report_skip_state(exc)
            if state is None:
                raise
            return skipped_vehicle_report(state)
        state = vehicle_state_name(chosen) or "unknown"
        if state != "online":
            return skipped_vehicle_report(state)
        try:
            payload = self.reader.vehicle_data(vehicle_key(chosen), DATA_ENDPOINTS)
        except (VehicleAsleep, TimeoutError, HttpError, QueryError) as exc:
            skipped = _report_skip_state(exc)
            if skipped is None:
                raise
            return skipped_vehicle_report(skipped)
        observation = from_vehicle_data(payload)
        name = str(chosen.get("display_name") or "Vehicle")
        briefing = vehicle_report(name, observation)
        self._remember(payload, observation, store=selection.store)
        secrets = [item for item in (str(chosen.get("vin") or ""), preferred or "") if item]
        return _redact_report(briefing, secrets)

    def where(self, *, preferred: str | None, precise: bool = False) -> WhereReport:
        """Area or a precise point. A sleeping or offline vehicle is not woken."""
        try:
            chosen = self._bind(preferred).row
        except VehicleAsleep:
            return WhereReport(kind="skipped", state="asleep")
        state = vehicle_state_name(chosen) or "unknown"
        if state != "online":
            return WhereReport(kind="skipped", state=state)
        try:
            payload = self.reader.vehicle_data(vehicle_key(chosen), LOCATION_ENDPOINTS)
        except VehicleAsleep:
            return WhereReport(kind="skipped", state="asleep")
        except QueryError as exc:
            if exc.status in (401, 403) or exc.location_scope:
                return WhereReport(kind="scope")
            return WhereReport(kind="unavailable")
        except Exception:
            return WhereReport(kind="unavailable")
        if precise:
            return precise_from_vehicle_payload(payload)
        return area_from_vehicle_payload(self.reader.http, payload)

    def charge_schedule_show(self, *, preferred: str | None) -> ScheduleReport:
        """Read the in-car timetable. A car that is not online is left asleep."""
        chosen = self._bind(preferred).row
        state = vehicle_state_name(chosen) or "unknown"
        if state != "online":
            return ScheduleReport(
                state=state,
                skipped=True,
                section_present=False,
                list_present=False,
                schedules=(),
            )
        payload = self.reader.vehicle_data(vehicle_key(chosen), [CHARGE_SCHEDULE_ENDPOINT])
        section_present, list_present, schedules = schedules_from_vehicle_data(payload)
        return ScheduleReport(
            state="online",
            skipped=False,
            section_present=section_present,
            list_present=list_present,
            schedules=schedules,
        )

    def charge_schedule_set(
        self,
        spec: ScheduleWrite,
        *,
        preferred: str | None,
        statement: str | None,
        sleep=time.sleep,
    ) -> str:
        return self._charge_schedule_command(
            "charging_schedule_set",
            spec.tool_args(),
            preferred=preferred,
            statement=statement,
            sleep=sleep,
        )

    def charge_schedule_remove(
        self,
        schedule_id: str,
        *,
        preferred: str | None,
        statement: str | None,
        sleep=time.sleep,
    ) -> str:
        identified = parse_schedule_id(schedule_id)
        return self._charge_schedule_command(
            "charging_schedule_remove",
            ["charging-schedule-remove", "id", identified],
            preferred=preferred,
            statement=statement,
            sleep=sleep,
        )

    def command(
        self,
        name: str,
        *,
        preferred: str | None,
        argument: str | None,
        statement: str | None,
        extra: list[str] | None = None,
        sleep=time.sleep,
    ) -> str:
        spec = PUBLIC_COMMANDS.get(name)
        if spec is None:
            raise ConfigError("unknown command")
        extra_args = list(extra) if extra is not None else _extra(name, argument)
        if name == "wake":
            selection = self._bind(preferred)
            key = vehicle_key(selection.row)
            token = self.tokens.access_token()
            self.control.run(vin=key, access_token=token, args=["wake"])
            return _tagged("OK: wake", selection.label)
        _key, observation, label = self._read_online(preferred, sleep=sleep)
        self._reject_unsupported(name, _key, observation)
        token = self.tokens.access_token()

        def observe() -> Observation:
            payload = self.reader.vehicle_data(_key, DATA_ENDPOINTS)
            return from_vehicle_data(payload)

        try:
            observed = self.gateway.execute(
                spec,
                shift_state=observation.shift_state,
                speed=observation.speed,
                adapter=_cli_adapter(statement),
                via_schedule=False,
                vin=_key,
                access_token=token,
                observe=observe,
                extra_args=extra_args,
                car=label,
            )
        except CommandFailed as exc:
            clarified = clarify_command_failure(name, str(exc))
            if clarified:
                raise CommandFailed(clarified) from exc
            raise
        if name == "charge-amps" and extra_args:
            return _tagged(charge_amps_result(extra_args[0], observed), label)
        return _tagged(f"OK: {_result_name(name, extra_args)}", label)

    def _reject_unsupported(self, name: str, key: str, observation: Observation) -> None:
        """Stop before a signed command when the reading already explains the failure."""
        if name == "seat-cool":
            supported = observation.seat_cooling
            if supported is None:
                payload = self.reader.vehicle_data(key, ["vehicle_config"])
                supported = from_vehicle_data(payload).seat_cooling
            if supported is False:
                raise ConfigError("This car does not have seat cooling.")
            if observation.climate_on is not True:
                raise ConfigError("Climate is not on, so seat cooling was not changed.")
        if name == "seat-climate" and observation.climate_on is not True:
            raise ConfigError("Climate is not on, so automatic seat climate was not changed.")
        if name == "charge-port-close" and cable_plugged(observation):
            raise ConfigError("The charge port did not close because a cable is plugged in.")

    def charging_history(self, *, as_json: bool = False) -> str:
        """Official account charging history. This does not list vehicles and does not wake a car."""
        return format_history(self.reader.charging_history(), as_json=as_json)

    def charging_invoice(self, invoice_id: str, *, save: bool) -> str:
        """Save the PDF under the config home only when save is true."""
        identified = parse_invoice_id(invoice_id)
        if not save:
            return format_invoice_skipped(identified)
        data = self.reader.charging_invoice_pdf(identified)
        directory = self.home / "invoices"
        directory.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(directory, 0o700)
        path = directory / f"{identified}.pdf"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        if os.name != "nt":
            os.chmod(path, 0o600)
        return format_invoice_saved(str(path))

    def software(self, *, preferred: str | None, as_json: bool = False) -> str:
        """Status and release notes. A sleeping car is not woken."""
        try:
            selection = self._bind(preferred)
        except VehicleAsleep:
            return _software_text("asleep", None, [], skipped=True, as_json=as_json)
        chosen = selection.row
        state = vehicle_state_name(chosen) or "unknown"
        if state != "online":
            return _software_text(state, None, [], skipped=True, as_json=as_json)
        key = vehicle_key(chosen)
        try:
            payload = self.reader.vehicle_data(key, DATA_ENDPOINTS)
        except VehicleAsleep:
            return _software_text("asleep", None, [], skipped=True, as_json=as_json)
        observation = from_vehicle_data(payload)
        notes: list[dict] = []
        try:
            notes = notes_from(
                self.reader.release_notes(key, staged=observation.software_update_status is not None)
            )
        except VehicleAsleep:
            return _software_text("asleep", None, [], skipped=True, as_json=as_json)
        except (TimeoutError, HttpError, QueryError):
            notes = []
        self._remember(payload, observation, store=selection.store)
        return _software_text("online", observation, notes, skipped=False, as_json=as_json)

    def _bind(self, preferred: str | None):
        return resolve_vehicle(
            self.home,
            self.reader.list_vehicles(),
            car_name=self.car_name or None,
            preferred=preferred,
        )

    def _remember(self, payload, observation, *, tyre_bar=None, low_battery_percent=None, store=None) -> None:
        """Save a parked snapshot. A failure is printed and does not change the command."""
        if not self.history:
            return
        try:
            if tyre_bar is None or low_battery_percent is None:
                tyre_bar, low_battery_percent = resolve_thresholds(self.home)
            from core.history.store import record_parked_snapshot

            record_parked_snapshot(
                Path(store) if store is not None else self.home,
                payload if isinstance(payload, dict) else {},
                observation,
                tyre_bar=float(tyre_bar),
                low_battery_percent=int(low_battery_percent),
            )
        except Exception as exc:
            _remember_failure(exc)

    def _online(self, preferred: str | None, *, sleep):
        selection = self._bind(preferred)
        chosen = selection.row
        key = vehicle_key(chosen)
        if vehicle_state_name(chosen) != "online":
            woken = self.reader.wake_until_online(key, sleep=sleep)
            chosen = {**chosen, **woken, "state": woken.get("state") or "online"}
        payload = self.reader.vehicle_data(key, DATA_ENDPOINTS)
        return selection, chosen, key, from_vehicle_data(payload)

    def _read_online(self, preferred: str | None, *, sleep) -> tuple[str, Observation, str]:
        selection, _chosen, key, observation = self._online(preferred, sleep=sleep)
        return key, observation, selection.label

    def _charge_schedule_command(
        self,
        spec_name: str,
        tool_args: list[str],
        *,
        preferred: str | None,
        statement: str | None,
        sleep,
    ) -> str:
        key, _observation, label = self._read_online(preferred, sleep=sleep)
        token = self.tokens.access_token()

        def observe() -> Observation:
            payload = self.reader.vehicle_data(key, [*DATA_ENDPOINTS, CHARGE_SCHEDULE_ENDPOINT])
            return from_vehicle_data(payload)

        observed = self.gateway.execute(
            spec_name,
            shift_state=_observation.shift_state,
            speed=_observation.speed,
            adapter=_cli_adapter(statement),
            via_schedule=False,
            vin=key,
            access_token=token,
            observe=observe,
            extra_args=tool_args[1:],
            car=label,
        )
        if spec_name == "charging_schedule_set":
            found = matching_ids(observed.charge_schedules, tool_args[1:])
            if found:
                return _tagged("OK: charge-schedule set id " + " ".join(found), label)
            return _tagged("OK: charge-schedule set", label)
        return _tagged(f"OK: charge-schedule remove {tool_args[-1]}", label)

    def departure_show(self, *, preferred: str | None) -> DepartureReport:
        """Read departure schedules. A car that is not online is left asleep."""
        chosen = self._bind(preferred).row
        state = vehicle_state_name(chosen) or "unknown"
        if state != "online":
            return DepartureReport(
                state=state,
                skipped=True,
                section_present=False,
                list_present=False,
                schedules=(),
            )
        payload = self.reader.vehicle_data(vehicle_key(chosen), [DEPARTURE_ENDPOINT])
        section_present, list_present, schedules = departure_schedules_from_vehicle_data(payload)
        return DepartureReport(
            state="online",
            skipped=False,
            section_present=section_present,
            list_present=list_present,
            schedules=schedules,
        )

    def departure_set(
        self,
        spec: DepartureWrite,
        *,
        preferred: str | None,
        statement: str | None,
        sleep=time.sleep,
    ) -> str:
        return self._departure_command(
            "departure_schedule_set",
            None if spec.latitude is None or spec.longitude is None else spec.tool_args(),
            spec=spec,
            preferred=preferred,
            statement=statement,
            sleep=sleep,
        )

    def departure_remove(
        self,
        schedule_id: str,
        *,
        preferred: str | None,
        statement: str | None,
        sleep=time.sleep,
    ) -> str:
        identified = parse_schedule_id(schedule_id)
        return self._departure_command(
            "departure_schedule_remove",
            ["precondition-schedule-remove", "id", identified],
            preferred=preferred,
            statement=statement,
            sleep=sleep,
        )

    def _read_precise_location(self, key: str) -> tuple[str, str]:
        """Read location_data. A 408 is not followed by another wake."""
        try:
            payload = self.reader.vehicle_data(key, LOCATION_ENDPOINTS)
        except VehicleAsleep as exc:
            raise ConfigError(
                "The car must be online to read its location. Wake it, or pass latitude and longitude."
            ) from exc
        if location_scope_denied(payload):
            raise ConfigError(
                "The token cannot read vehicle location. "
                "Re-authorize and include the vehicle_location scope, or pass latitude and longitude."
            )
        point = precise_point(payload)
        if point is None:
            raise ConfigError("The car did not return a location. Pass latitude and longitude.")
        return point

    def _departure_command(
        self,
        spec_name: str,
        tool_args: list[str] | None,
        *,
        preferred: str | None,
        statement: str | None,
        sleep,
        spec: DepartureWrite | None = None,
    ) -> str:
        key, _observation, label = self._read_online(preferred, sleep=sleep)
        if spec is not None and (spec.latitude is None or spec.longitude is None):
            latitude, longitude = self._read_precise_location(key)
            spec = spec.with_location(latitude, longitude)
            tool_args = spec.tool_args()
        if not tool_args:
            raise ConfigError("latitude and longitude are required before the command is sent")
        token = self.tokens.access_token()

        def observe() -> Observation:
            payload = self.reader.vehicle_data(key, [*DATA_ENDPOINTS, DEPARTURE_ENDPOINT])
            return from_vehicle_data(payload)

        observed = self.gateway.execute(
            spec_name,
            shift_state=_observation.shift_state,
            speed=_observation.speed,
            adapter=_cli_adapter(statement),
            via_schedule=False,
            vin=key,
            access_token=token,
            observe=observe,
            extra_args=tool_args[1:],
            car=label,
        )
        if spec_name == "departure_schedule_set":
            found = matching_departure_ids(observed.departure_schedules, tool_args[1:])
            if found:
                return _tagged("OK: departure set id " + " ".join(found), label)
            return _tagged("OK: departure set", label)
        return _tagged(f"OK: departure remove {tool_args[-1]}", label)


def _tagged(text: str, label: str) -> str:
    """Name the car on the first line when a confirmation has to name it."""
    if not label:
        return text
    head, sep, tail = text.partition("\n")
    named = f"{head} on {label}"
    return named + sep + tail if sep else named


def _extra(name: str, argument: str | None) -> list[str] | None:
    if name == "seat-heat":
        raise ConfigError("seat and level are required")
    if name == "seat-cool":
        raise ConfigError("seat and level are required")
    if name == "seat-climate":
        raise ConfigError("seat and state are required")
    if name == "software-schedule":
        raise ConfigError("delay must be a duration such as 2h, 10m, 30s, or a whole number of seconds")
    if name == "steering-heat":
        raise ConfigError("steering heat must be on or off")
    if name == "set-temp":
        if argument is None:
            raise ConfigError("temp must be 15-28 C")
        return [parse_temp(argument)]
    if name == "charge-limit":
        if argument is None:
            raise ConfigError("limit must be 50-100")
        return [parse_limit(argument)]
    if name == "charge-amps":
        if argument is None:
            raise ConfigError(f"amps must be a whole number from {CHARGE_AMPS_MIN} to {CHARGE_AMPS_MAX}")
        return [parse_amps(argument)]
    if argument is not None:
        raise ConfigError("unknown command")
    return None


def _action_phrase(challenge) -> str:
    summary = str(getattr(challenge, "summary", "") or "").strip()
    if summary:
        return summary
    return str(challenge.command).replace("_", " ")


def _cli_adapter(statement: str | None):
    def prompter(challenge) -> str:
        phrase = _action_phrase(challenge)
        if statement is not None:
            return statement
        car = str(getattr(challenge, "car", "") or "").strip()
        if not sys.stdin.isatty():
            if car:
                raise ConfirmationRequired(
                    "This action needs an explicit confirmation. "
                    f"Pass --confirm with a sentence that includes confirm, the car, and {phrase}."
                )
            raise ConfirmationRequired(
                f"This action needs an explicit confirmation. Pass --confirm with a sentence that includes confirm and {phrase}."
            )
        if car:
            return input(f"Confirm {phrase}. Type a sentence that names the action and the car and includes confirm: ")
        return input(f"Confirm {phrase}. Type a sentence that names the action and includes confirm: ")

    return TemplateAdapter(prompter)


def format_status(vehicle: dict, observation: Observation, vin: str) -> str:
    name = " ".join(str(vehicle.get("display_name") or "Vehicle").split()) or "Vehicle"
    state = str(vehicle.get("state") or "online")
    lines = [
        f"Vehicle: {name} (VIN {mask_vin(vin)}) - {state}",
        (
            f"Battery: {_pct(observation.battery_level)} | Range: {_km(observation.battery_range_miles)} | "
            f"Limit: {_pct(observation.charge_limit_soc)}"
        ),
        f"Charging: {observation.charging_state or '?'}",
        f"Locked: {_yes_no(observation.locked)}",
        f"Climate: {_climate(observation)}",
        f"Inside: {_num(observation.inside_temp)}°C | Outside: {_num(observation.outside_temp)}°C",
        (
            f"Tyres (bar): FL {_num(observation.tyre_fl)} FR {_num(observation.tyre_fr)} "
            f"RL {_num(observation.tyre_rl)} RR {_num(observation.tyre_rr)}"
        ),
        f"TPMS: {_tpms(observation)}",
    ]
    return "\n".join(lines)


def _report_skip_state(exc: BaseException) -> str | None:
    """Map a timeout or HTTP 408 to a skip. Other failures stay failures."""
    if isinstance(exc, VehicleAsleep):
        return "asleep"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, HttpError) and exc.timed_out:
        return "timeout"
    if isinstance(exc, QueryError) and exc.status == 408:
        return "asleep"
    return None


def _redact_report(report: VehicleReport, secrets: list[str]) -> VehicleReport:
    if not secrets:
        return report
    fields = dict(report.fields)
    if "name" in fields:
        fields["name"] = redact(str(fields["name"]), secrets)
    return VehicleReport(skipped=report.skipped, state=report.state, text=redact(report.text, secrets), fields=fields)


def _result_name(name: str, extra_args: list[str] | None) -> str:
    if name.startswith("climate-keeper-"):
        return "climate-keeper " + name.removeprefix("climate-keeper-")
    if name.startswith("charge-port-"):
        return "charge-port " + name.removeprefix("charge-port-")
    if name.startswith("bioweapon-"):
        return "bioweapon " + name.removeprefix("bioweapon-")
    if name == "software-schedule" and extra_args:
        return "software schedule " + extra_args[0]
    if name == "software-cancel":
        return "software cancel"
    if name == "seat-climate" and extra_args and len(extra_args) >= 2:
        seats = {"L": "front-left", "R": "front-right", "LR": "both"}
        return f"seat-climate {seats.get(extra_args[0], extra_args[0])} {extra_args[1]}"
    if extra_args:
        return name + " " + " ".join(extra_args)
    return name


def _software_text(state, observation, notes, *, skipped: bool, as_json: bool) -> str:
    if as_json:
        return format_software_json(state, observation, notes, skipped=skipped)
    return format_software(state, observation, notes, skipped=skipped)


def format_check(report: CheckReport) -> str:
    if report.skipped:
        return skip_text(report.state)
    lines = ["state: online", f"Driver seat: {driver_seat_text(report.driver_present)}"]
    if not report.alerts:
        lines.append("no alerts")
    else:
        lines.extend(f"alert: {item.message}" for item in report.alerts)
    return "\n".join(lines)


def format_check_json(report: CheckReport) -> str:
    return json.dumps(report.as_dict(), indent=2)


def build_app(home: Path, *, region: str | None = None) -> App:
    """Real network clients. Tests build App directly and do not call this."""
    home = Path(home)
    regions = load_regions()
    client_id, client_secret = load_client_credentials(home)
    store = AtomicTokenStore(resolve_token_file(home))
    try:
        saved_region = store.load().region
    except TokenError:
        saved_region = None
    chosen = region or saved_region or "na"
    http = UrllibClient()
    tokens = TokenManager(
        store,
        FleetTokenRefresher(http, regions, client_secret),
        client_id=client_id,
        audience=audience_for(regions, chosen),
    )
    reader = FleetReader(http, regions, chosen, tokens)
    control = TeslaControl(
        resolve_binary(home),
        resolve_key(home),
        session_cache=home / ".session-cache.json",
        token_dir=home,
    )
    return App(home, reader=reader, tokens=tokens, control=control, region=chosen, history=True)


def _remember_failure(exc: BaseException) -> None:
    """A history problem must not change check or report. The line has no secrets."""
    print(
        f"history: the local history could not be saved ({type(exc).__name__})",
        file=sys.stderr,
    )


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


def _km(miles) -> str:
    if miles is None:
        return "?"
    try:
        return f"{float(miles) * MILES_TO_KM:.0f} km"
    except (TypeError, ValueError):
        return "?"


def _yes_no(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
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
