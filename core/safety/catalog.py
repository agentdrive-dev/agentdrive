"""Command tiers loaded from config/vehicles.yaml."""

from __future__ import annotations

from dataclasses import dataclass

from core.config import load_vehicles
from core.errors import ConfigError

# Arguments passed to tesla-control.
# Remote start is that tool's `drive` command (keyless-driving enablement). It takes no password.
# The rear trunk can open (`trunk-open`) and, on vehicles that can close it, `trunk-close`.
# The front trunk can open. The signed tool has no command that closes it.
# Cabin preconditioning now is `climate-on` (auto conditioning start).
# A later departure is `precondition-schedule-add` / `precondition-schedule-remove`.
# Those subcommands are upstream. They are not the older scheduled-departure command.
# Charging current is upstream `charging-set-amps` (`set_charging_amps`).
# Max defrost is `preconditioning-max`, added to the local tesla-control build because
# upstream does not register SetPreconditioningMax. The manual override is not sent.
# Climate keeper, seat cooling, and bioweapon defense are added the same way.
# Charge port open/close and software schedule/cancel are already upstream commands.
# Automatic seat climate is upstream `auto-seat-and-climate`.
TOOL_ARGS: dict[str, tuple[str, ...]] = {
    "flash_lights": ("flash-lights",),
    "climate_on": ("climate-on",),
    "climate_off": ("climate-off",),
    "climate_set_temp": ("climate-set-temp",),
    "lock": ("lock",),
    "honk": ("honk",),
    "unlock": ("unlock",),
    "remote_start": ("drive",),
    "frunk_open": ("frunk-open",),
    "trunk_open": ("trunk-open",),
    "trunk_close": ("trunk-close",),
    "sentry_on": ("sentry-mode", "on"),
    "sentry_off": ("sentry-mode", "off"),
    "seat_heater": ("seat-heater",),
    "steering_wheel_heater": ("steering-wheel-heater",),
    "defrost_on": ("preconditioning-max", "on"),
    "defrost_off": ("preconditioning-max", "off"),
    "cabin_precondition": ("climate-on",),
    "charging_start": ("charging-start",),
    "charging_stop": ("charging-stop",),
    "charging_set_limit": ("charging-set-limit",),
    "charging_set_amps": ("charging-set-amps",),
    "charging_schedule_set": ("charging-schedule-add",),
    "charging_schedule_remove": ("charging-schedule-remove",),
    "departure_schedule_set": ("precondition-schedule-add",),
    "departure_schedule_remove": ("precondition-schedule-remove",),
    "climate_keeper_off": ("climate-keeper", "off"),
    "climate_keeper_keep": ("climate-keeper", "keep"),
    "climate_keeper_dog": ("climate-keeper", "dog"),
    "climate_keeper_camp": ("climate-keeper", "camp"),
    "charge_port_open": ("charge-port-open",),
    "charge_port_close": ("charge-port-close",),
    "seat_cooler": ("seat-cooler",),
    "auto_seat_climate": ("auto-seat-and-climate",),
    "bioweapon_on": ("bioweapon-mode", "on"),
    "bioweapon_off": ("bioweapon-mode", "off"),
    "software_schedule": ("software-update-start",),
    "software_cancel": ("software-update-cancel",),
}

VERIFY_KIND = {
    "flash_lights": "parked_only",
    "honk": "parked_only",
    "remote_start": "parked_only",
    "climate_on": "climate_on",
    "climate_off": "climate_off",
    "climate_set_temp": "driver_temp",
    "lock": "locked",
    "unlock": "unlocked",
    "frunk_open": "frunk_open",
    "trunk_open": "trunk_open",
    "trunk_close": "trunk_closed",
    "sentry_on": "sentry_on",
    "sentry_off": "sentry_off",
    "seat_heater": "seat_heater",
    "steering_wheel_heater": "steering_heater",
    "defrost_on": "defrost_on",
    "defrost_off": "defrost_off",
    "cabin_precondition": "climate_on",
    "charging_start": "charging",
    "charging_stop": "not_charging",
    "charging_set_limit": "charge_limit",
    "charging_set_amps": "charge_amps",
    "charging_schedule_set": "charge_schedule_set",
    "charging_schedule_remove": "charge_schedule_remove",
    "departure_schedule_set": "departure_schedule_set",
    "departure_schedule_remove": "departure_schedule_remove",
    "climate_keeper_off": "climate_keeper_off",
    "climate_keeper_keep": "climate_keeper_keep",
    "climate_keeper_dog": "climate_keeper_dog",
    "climate_keeper_camp": "climate_keeper_camp",
    "charge_port_open": "charge_port_open",
    "charge_port_close": "charge_port_closed",
    "seat_cooler": "seat_cooler",
    "auto_seat_climate": "auto_seat_climate",
    "bioweapon_on": "bioweapon_on",
    "bioweapon_off": "bioweapon_off",
    "software_schedule": "software_scheduled",
    "software_cancel": "software_cancelled",
}


# Phrases the owner must say. Other commands use the spec name with spaces.
ACTION_NAMES = {
    "trunk_open": "open rear trunk",
    "trunk_close": "close rear trunk",
    "frunk_open": "open frunk",
    "remote_start": "enable remote start",
    "sentry_off": "turn sentry off",
    "climate_keeper_keep": "climate keeper keep",
    "climate_keeper_dog": "climate keeper dog",
    "climate_keeper_camp": "climate keeper camp",
    "charge_port_open": "open charge port",
    "charge_port_close": "close charge port",
    "software_schedule": "schedule software update",
    "software_cancel": "cancel software update",
    "bioweapon_on": "turn bioweapon defense on",
}

# Normalized command strings a schedule must not store. `drive` is the signed remote-start command.
_SCHEDULE_BLOCKED = {
    "trunk open",
    "trunk close",
    "trunk move",
    "frunk open",
    "frunk close",
    "close frunk",
    "remote start",
    "enable remote start",
    "open rear trunk",
    "close rear trunk",
    "open frunk",
    "drive",
}


@dataclass(frozen=True)
class CommandSpec:
    name: str
    tier: str
    tool_args: tuple[str, ...]
    verify: str
    action: str


def load_specs(document: dict | None = None) -> dict[str, CommandSpec]:
    catalog = document or load_vehicles()
    capabilities = catalog["capabilities"]
    sensitive = set(capabilities["sensitive_commands"])
    standard = set(capabilities["standard_commands"]) | set(capabilities["phase_1_commands"])
    overlap = sensitive & standard
    if overlap:
        raise ConfigError("A command cannot be both standard and sensitive")
    specs: dict[str, CommandSpec] = {}
    for name in sorted(sensitive | standard):
        if name not in TOOL_ARGS:
            raise ConfigError(f"No signed-command mapping for {name}")
        tier = "sensitive" if name in sensitive else "standard"
        action = ACTION_NAMES.get(name, name.replace("_", " "))
        specs[name] = CommandSpec(
            name=name,
            tier=tier,
            tool_args=TOOL_ARGS[name],
            verify=VERIFY_KIND[name],
            action=action,
        )
    return specs


def sensitive_names(document: dict | None = None) -> set[str]:
    return {name for name, spec in load_specs(document).items() if spec.tier == "sensitive"}


def _schedule_text(command: str) -> str:
    return " ".join(command.strip().lower().replace("_", " ").replace("-", " ").split())


def schedule_blocks(command, sensitive: set[str] | None = None) -> bool:
    """True when a host schedule must not store this command.

    Health check (`check`) and the morning history (`history`) stay allowed.
    Trunk, frunk, and remote start are interactive owner commands.
    Charging changes, including charge current and the charging timetable,
    are blocked. Setting or removing a departure schedule is blocked.
    `departure show` is a read and stays allowed.
    """
    if not isinstance(command, str):
        return False
    raw = command.strip()
    if not raw:
        return False
    names = sensitive if sensitive is not None else sensitive_names()
    if raw in names:
        return True
    text = _schedule_text(raw)
    if text in _SCHEDULE_BLOCKED or text.replace(" ", "_") in names:
        return True
    if _blocks_charge_schedule(raw, text) or _blocks_charge_amps(text) or _blocks_departure_change(text):
        return True
    if _blocks_phase8(text):
        return True
    if _blocks_request_approval(text):
        return True
    head = _schedule_text(raw.split()[0])
    return head in _SCHEDULE_BLOCKED or head.replace(" ", "_") in names


def _blocks_charge_schedule(raw: str, text: str) -> bool:
    if raw == "charge-schedule" or raw.startswith("charge-schedule "):
        return True
    return text == "charge schedule" or text.startswith("charge schedule ")


def _blocks_charge_amps(text: str) -> bool:
    if text in {"charge amps", "charging set amps"}:
        return True
    return text.startswith("charge amps ") or text.startswith("charging set amps ")


def _blocks_request_approval(text: str) -> bool:
    """A schedule must not approve or deny a family request."""
    if text in {"request approve", "request deny"}:
        return True
    return text.startswith("request approve ") or text.startswith("request deny ")


def _blocks_phase8(text: str) -> bool:
    """Block confirmation-required phase 8 commands. Reads and off states stay allowed."""
    parts = text.split()
    if parts[:2] == ["climate", "keeper"] and len(parts) >= 3 and parts[2] in {"keep", "dog", "camp"}:
        return True
    if text.startswith("climate-keeper keep") or text.startswith("climate-keeper dog") or text.startswith("climate-keeper camp"):
        return True
    if parts[:2] == ["bioweapon", "on"] or text.startswith("bioweapon-on") or text.startswith("bioweapon on"):
        return True
    if parts[:2] == ["charge", "port"] or text.startswith("charge-port"):
        return True
    if parts[:2] in (["software", "schedule"], ["software", "cancel"]):
        return True
    if text.startswith("software-schedule") or text.startswith("software-cancel"):
        return True
    if text.startswith("software-update-start") or text.startswith("software-update-cancel"):
        return True
    return False


def _blocks_departure_change(text: str) -> bool:
    """Block departure writes. A show read stays allowed."""
    parts = text.split()
    if parts[:2] == ["departure", "show"] or parts[:3] == ["departure", "schedule", "show"]:
        return False
    if parts[:1] == ["departure"]:
        return True
    if text in {"precondition schedule add", "precondition schedule remove"}:
        return True
    return text.startswith("precondition schedule add ") or text.startswith("precondition schedule remove ")
