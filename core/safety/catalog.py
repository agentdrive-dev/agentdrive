"""Command tiers loaded from config/vehicles.yaml."""

from __future__ import annotations

from dataclasses import dataclass

from core.config import load_vehicles
from core.errors import ConfigError

# Arguments passed to tesla-control. Remote start is that tool's `drive` command.
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
    "sentry_off": ("sentry-mode", "off"),
    "charging_start": ("charging-start",),
    "charging_stop": ("charging-stop",),
    "charging_set_limit": ("charging-set-limit",),
    "charging_set_amps": ("charging-set-amps",),
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
    "sentry_off": "sentry_off",
    "charging_start": "charging",
    "charging_stop": "not_charging",
    "charging_set_limit": "charge_limit",
    "charging_set_amps": "charge_amps",
}


@dataclass(frozen=True)
class CommandSpec:
    name: str
    tier: str
    tool_args: tuple[str, ...]
    verify: str


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
        specs[name] = CommandSpec(name=name, tier=tier, tool_args=TOOL_ARGS[name], verify=VERIFY_KIND[name])
    return specs


def sensitive_names(document: dict | None = None) -> set[str]:
    return {name for name, spec in load_specs(document).items() if spec.tier == "sensitive"}
