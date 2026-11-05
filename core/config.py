"""Load host and vehicle data from YAML. Hosts are not embedded in code."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[1]


def repo_root() -> Path:
    return REPO_ROOT


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"Missing configuration file: {path.name}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} must be a mapping")
    return data


def load_regions(path: Path | None = None) -> dict[str, Any]:
    """Return the global region table and reject unsupported hosts."""
    document = _load_yaml(path or REPO_ROOT / "config" / "regions.yaml")
    markers = document.get("rejected_host_markers") or []
    if not isinstance(markers, list) or not markers:
        raise ConfigError("rejected_host_markers must list the hosts this toolkit refuses")
    regions = document.get("regions") or {}
    shared = document.get("shared") or {}
    if "na" not in regions or "eu" not in regions:
        raise ConfigError("regions.yaml must define the na and eu Fleet API hosts")
    active_hosts = [str(shared.get("developer_portal", "")), str(shared.get("authorization_host", "")), str(shared.get("token_host", ""))]
    for region in regions.values():
        if isinstance(region, dict):
            active_hosts.append(str(region.get("fleet_api_host", "")))
    for host in active_hosts:
        lowered = host.lower()
        for marker in markers:
            if str(marker).lower() in lowered:
                raise ConfigError("A configured host is outside the regions this toolkit supports")
    for key in ("developer_portal", "authorization_host", "token_host", "authorize_path", "token_path"):
        if not shared.get(key):
            raise ConfigError(f"shared.{key} is missing from regions.yaml")
    return document


def fleet_host(regions: dict[str, Any], region_id: str) -> str:
    try:
        host = regions["regions"][region_id]["fleet_api_host"]
    except KeyError as exc:
        raise ConfigError(f"Unknown region '{region_id}'. Choose na or eu.") from exc
    if not isinstance(host, str) or not host.startswith("https://"):
        raise ConfigError("Fleet API host must be an https URL from regions.yaml")
    return host.rstrip("/")


def endpoint(regions: dict[str, Any], name: str) -> str:
    try:
        path = regions["personal_endpoints"][name]
    except KeyError as exc:
        raise ConfigError(f"No personal endpoint named {name}") from exc
    if not isinstance(path, str) or not path.startswith("/"):
        raise ConfigError(f"Endpoint {name} is not a path")
    blocked = regions.get("blocked_endpoints") or []
    if path in blocked or path.rstrip("/") in blocked:
        raise ConfigError(f"Endpoint {name} is blocked")
    return path


def assert_path_allowed(regions: dict[str, Any], path: str) -> None:
    """Refuse business-only routes, including charging sessions."""
    bare = path.split("?", 1)[0]
    for blocked in regions.get("blocked_endpoints") or []:
        if bare == blocked or bare.startswith(str(blocked).rstrip("/") + "/"):
            from core.errors import EndpointNotAllowed

            raise EndpointNotAllowed("That route is not available to a personal account")
    if "charging/sessions" in bare:
        from core.errors import EndpointNotAllowed

        raise EndpointNotAllowed("Charging sessions are not available to a personal account")


def load_scopes(path: Path | None = None) -> dict[str, Any]:
    document = _load_yaml(path or REPO_ROOT / "config" / "scopes.yaml")
    default = document.get("default")
    if not isinstance(default, list) or "offline_access" not in default or "vehicle_cmds" not in default:
        raise ConfigError("scopes.yaml is missing the default owner scopes")
    return document


def load_vehicles(path: Path | None = None) -> dict[str, Any]:
    document = _load_yaml(path or REPO_ROOT / "config" / "vehicles.yaml")
    caps = document.get("capabilities") or {}
    for key in ("phase_1_commands", "sensitive_commands", "standard_commands"):
        if not isinstance(caps.get(key), list):
            raise ConfigError(f"vehicles.yaml capabilities.{key} must be a list")
    return document


def fill_path(template: str, **values: str) -> str:
    try:
        return template.format(**values)
    except KeyError as exc:
        raise ConfigError("Endpoint template is missing a value") from exc
