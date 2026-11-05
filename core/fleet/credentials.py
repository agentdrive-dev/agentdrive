"""Client id and secret from the environment or a mode 0600 file.

The token endpoint needs these values. They are never printed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from core.errors import ConfigError


def load_client_credentials(home: Path, env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Return (client_id, client_secret). The secret may be empty.

    Order: AGENTDRIVE_CLIENT_ID / AGENTDRIVE_CLIENT_SECRET, then the TESLA_CLIENT_ID /
    TESLA_CLIENT_SECRET names used by the signed-command tool's surroundings, then
    $AGENTDRIVE_HOME/client.json, then the adapter secret files.
    """
    current = os.environ if env is None else env
    client_id = _first(current, "AGENTDRIVE_CLIENT_ID", "TESLA_CLIENT_ID")
    client_secret = _first(current, "AGENTDRIVE_CLIENT_SECRET", "TESLA_CLIENT_SECRET")
    if client_id and client_secret:
        return client_id, client_secret
    file_id, file_secret = _from_files(Path(home))
    client_id = client_id or file_id
    client_secret = client_secret or file_secret
    if not client_id:
        raise ConfigError(
            "No client id. Set AGENTDRIVE_CLIENT_ID or store client.json mode 0600 in the home directory."
        )
    return client_id, client_secret


def _first(env: Mapping[str, str], *names: str) -> str:
    for name in names:
        value = env.get(name, "")
        if value.strip():
            return value.strip()
    return ""


def _read_private_json(path: Path) -> dict:
    _require_private(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("client.json cannot be read") from exc
    if not isinstance(payload, dict):
        raise ConfigError("client.json must be a JSON object")
    return payload


def _require_private(path: Path) -> None:
    if not path.is_file():
        raise ConfigError("client.json is missing")
    if path.stat().st_mode & 0o077:
        raise ConfigError("client.json must be mode 0600")


def _from_files(home: Path) -> tuple[str, str]:
    client_json = home / "client.json"
    if client_json.is_file():
        payload = _read_private_json(client_json)
        client_id = str(payload.get("client_id") or "").strip()
        client_secret = str(payload.get("client_secret") or "").strip()
        return client_id, client_secret
    secrets = home / "secrets"
    id_path = secrets / "client-id"
    secret_path = secrets / "client-secret"
    if id_path.is_file():
        if id_path.stat().st_mode & 0o077 or (secret_path.is_file() and secret_path.stat().st_mode & 0o077):
            raise ConfigError("Client credential files must be mode 0600")
        client_id = id_path.read_text(encoding="utf-8").strip()
        client_secret = secret_path.read_text(encoding="utf-8").strip() if secret_path.is_file() else ""
        return client_id, client_secret
    return "", ""
