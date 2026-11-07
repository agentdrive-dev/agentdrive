"""Vehicle reads and signed commands used by the command line.

Status wakes a sleeping vehicle and polls for up to 45 seconds. The health
check, the daily report, where, and charge-schedule show never wake. Unlock
and other sensitive commands go through the safety gateway, which asks for a
one-time confirmation.

Check and report also save local history when the car is already online.
That write does not wake the car, and a history problem does not change the
check result or the report text.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from adapters.template import TemplateAdapter
from core.config import load_regions
from core.errors import ConfigError, ConfirmationRequired, TokenError
from core.fleet.area import WhereReport, area_from_vehicle_payload
from core.fleet.charge_schedule import (
    ScheduleReport,
    ScheduleWrite,
    matching_ids,
    parse_schedule_id,
    schedules_from_vehicle_data,
)
from core.fleet.commands import TeslaControl
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
from core.fleet.queries import FleetReader, QueryError, VehicleAsleep
from core.fleet.report import VehicleReport, skipped_vehicle_report, skip_text, vehicle_report
from core.fleet.snapshot import SEAT_API_FIELDS, SEAT_HEATER_LEVELS, Observation, from_vehicle_data, seat_heater_names
from core.fleet.tokens import AtomicTokenStore, FleetTokenRefresher, TokenManager, audience_for
from core.paths import resolve_key, resolve_token_file
from core.profile.owner import load_profile
from core.redact import mask_vin, redact
from core.safety.gateway import Gateway

MILES_TO_KM = 1.609344
DATA_ENDPOINTS = ["charge_state", "climate_state", "vehicle_state", "drive_state"]
CHARGE_SCHEDULE_ENDPOINT = "charge_schedule_data"
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
    "sentry-on": "sentry_on",
    "sentry-off": "sentry_off",
    "seat-heat": "seat_heater",
    "steering-heat": "steering_wheel_heater",
    "defrost-on": "defrost_on",
    "defrost-off": "defrost_off",
    "precondition": "cabin_precondition",
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

    def refresh(self) -> str:
        self.tokens.access_token(force=True)
        return "refreshed; tokens saved"

    def status(self, *, preferred: str | None, sleep=time.sleep) -> str:
        chosen, key, observation = self._online(preferred, sleep=sleep)
        text = format_status(chosen, observation, key)
        return redact(text, [key, preferred or ""])

    def check(self, *, preferred: str | None, tyre_bar: float, low_battery_percent: int) -> CheckReport:
        chosen = select_vehicle(self.reader.list_vehicles(), preferred)
        state = vehicle_state_name(chosen) or "unknown"
        if state != "online":
            return skipped_report(state, tyre_bar=tyre_bar, low_battery_percent=low_battery_percent)
        payload = self.reader.vehicle_data(vehicle_key(chosen), DATA_ENDPOINTS)
        observation = from_vehicle_data(payload)
        report = report_from_observation(observation, tyre_bar=tyre_bar, low_battery_percent=low_battery_percent)
        # History records the full online reading. Notification dedupe is separate,
        # and a skipped check never reaches this write.
        self._remember(payload, observation, tyre_bar=tyre_bar, low_battery_percent=low_battery_percent)
        return apply_rising_edge(self.home, report)

    def report(self, *, preferred: str | None) -> VehicleReport:
        """Read-only briefing. A car that is not online, a timeout, or HTTP 408 is a skip.

        This never calls wake.
        """
        try:
            chosen = select_vehicle(self.reader.list_vehicles(), preferred)
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
        self._remember(payload, observation)
        secrets = [item for item in (str(chosen.get("vin") or ""), preferred or "") if item]
        return _redact_report(briefing, secrets)

    def where(self, *, preferred: str | None) -> WhereReport:
        """Coarse area. A sleeping or offline vehicle is skipped and is not woken."""
        try:
            chosen = select_vehicle(self.reader.list_vehicles(), preferred)
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
        return area_from_vehicle_payload(self.reader.http, payload)

    def charge_schedule_show(self, *, preferred: str | None) -> ScheduleReport:
        """Read the in-car timetable. A car that is not online is left asleep."""
        chosen = select_vehicle(self.reader.list_vehicles(), preferred)
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
            chosen = select_vehicle(self.reader.list_vehicles(), preferred)
            key = vehicle_key(chosen)
            token = self.tokens.access_token()
            self.control.run(vin=key, access_token=token, args=["wake"])
            return "OK: wake"
        _key, observation = self._read_online(preferred, sleep=sleep)
        token = self.tokens.access_token()

        def observe() -> Observation:
            payload = self.reader.vehicle_data(_key, DATA_ENDPOINTS)
            return from_vehicle_data(payload)

        self.gateway.execute(
            spec,
            shift_state=observation.shift_state,
            speed=observation.speed,
            adapter=_cli_adapter(statement),
            via_schedule=False,
            vin=_key,
            access_token=token,
            observe=observe,
            extra_args=extra_args,
        )
        if extra_args:
            return f"OK: {name} {' '.join(extra_args)}"
        return f"OK: {name}"

    def _remember(self, payload, observation, *, tyre_bar=None, low_battery_percent=None) -> None:
        """Save a parked snapshot. A failure is printed and does not change the command."""
        if not self.history:
            return
        try:
            if tyre_bar is None or low_battery_percent is None:
                tyre_bar, low_battery_percent = resolve_thresholds(self.home)
            from core.history.store import record_parked_snapshot

            record_parked_snapshot(
                self.home,
                payload if isinstance(payload, dict) else {},
                observation,
                tyre_bar=float(tyre_bar),
                low_battery_percent=int(low_battery_percent),
            )
        except Exception as exc:
            _remember_failure(exc)

    def _online(self, preferred: str | None, *, sleep) -> tuple[dict, str, Observation]:
        chosen = select_vehicle(self.reader.list_vehicles(), preferred)
        key = vehicle_key(chosen)
        if vehicle_state_name(chosen) != "online":
            woken = self.reader.wake_until_online(key, sleep=sleep)
            chosen = {**chosen, **woken, "state": woken.get("state") or "online"}
        payload = self.reader.vehicle_data(key, DATA_ENDPOINTS)
        return chosen, key, from_vehicle_data(payload)

    def _read_online(self, preferred: str | None, *, sleep) -> tuple[str, Observation]:
        _chosen, key, observation = self._online(preferred, sleep=sleep)
        return key, observation

    def _charge_schedule_command(
        self,
        spec_name: str,
        tool_args: list[str],
        *,
        preferred: str | None,
        statement: str | None,
        sleep,
    ) -> str:
        key, _observation = self._read_online(preferred, sleep=sleep)
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
        )
        if spec_name == "charging_schedule_set":
            found = matching_ids(observed.charge_schedules, tool_args[1:])
            if found:
                return "OK: charge-schedule set id " + " ".join(found)
            return "OK: charge-schedule set"
        return f"OK: charge-schedule remove {tool_args[-1]}"


def _extra(name: str, argument: str | None) -> list[str] | None:
    if name == "seat-heat":
        raise ConfigError("seat and level are required")
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
        if not sys.stdin.isatty():
            raise ConfirmationRequired(
                f"This action needs an explicit confirmation. Pass --confirm with a sentence that includes confirm and {phrase}."
            )
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


def format_check(report: CheckReport) -> str:
    if report.skipped:
        return skip_text(report.state)
    if not report.alerts:
        return "state: online\nno alerts"
    lines = ["state: online"]
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
