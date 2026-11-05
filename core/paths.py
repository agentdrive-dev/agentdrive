"""Config home, private key, and token file locations.

`$AGENTDRIVE_HOME` overrides the default directory on macOS, Linux, and
Windows. The default is `~/.config/agentdrive` (on Windows,
`C:\\Users\\<you>\\.config\\agentdrive`).

A key or token file may live outside that directory. The environment
variable wins, then `runtime.yaml` in the config home, then the default
file inside the config home. This module does not sign commands.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

import yaml

from core.errors import ConfigError

RUNTIME_NAME = "runtime.yaml"

# Placeholder only. Docs and runtime.example.yaml use this shape.
WINDOWS_KEY_EXAMPLE = r"C:\Users\<you>\agentdrive-key\private-key.pem"
WINDOWS_TOKEN_EXAMPLE = r"C:\Users\<you>\agentdrive-key\tokens.json"


def resolve_home(env: Mapping[str, str] | None = None) -> Path:
    """Return the config home. `$AGENTDRIVE_HOME` wins over the default."""
    raw = _value(env, "AGENTDRIVE_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".config" / "agentdrive"


def load_runtime(home: Path) -> dict:
    """Return `$AGENTDRIVE_HOME/runtime.yaml`, or an empty mapping."""
    path = Path(home) / RUNTIME_NAME
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError("runtime.yaml must be a mapping")
    return data


def resolve_key(home: Path, env: Mapping[str, str] | None = None) -> Path:
    """Return the private key path.

    Order: `AGENTDRIVE_KEY_FILE`, `TESLA_KEY_FILE`, `runtime.yaml` `key_file`,
    an existing key inside the config home, then
    `$AGENTDRIVE_HOME/keys/private-key.pem`.
    """
    explicit = _value(env, "AGENTDRIVE_KEY_FILE") or _value(env, "TESLA_KEY_FILE")
    if explicit:
        return _as_path(explicit)
    configured = str(load_runtime(home).get("key_file") or "").strip()
    if configured:
        return _as_path(configured)
    home = Path(home)
    for candidate in (home / "keys" / "private-key.pem", home / "private-key.pem"):
        if candidate.is_file():
            return candidate
    return home / "keys" / "private-key.pem"


def resolve_token_file(home: Path, env: Mapping[str, str] | None = None) -> Path:
    """Return the OAuth token file.

    Order: `AGENTDRIVE_TOKEN_FILE`, `runtime.yaml` `token_file`, then
    `$AGENTDRIVE_HOME/tokens.json`. `TESLA_TOKEN_FILE` is not read here:
    the signed-command tool sets that name to a short-lived file.
    """
    explicit = _value(env, "AGENTDRIVE_TOKEN_FILE")
    if explicit:
        return _as_path(explicit)
    configured = str(load_runtime(home).get("token_file") or "").strip()
    if configured:
        return _as_path(configured)
    return Path(home) / "tokens.json"


def _value(env: Mapping[str, str] | None, name: str) -> str:
    current = os.environ if env is None else env
    return str(current.get(name, "") or "").strip()


def _as_path(value: str) -> Path:
    return Path(value).expanduser()
