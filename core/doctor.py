"""Check configuration, the private key, the public key URL, and the token."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.config import load_regions, load_vehicles
from core.errors import ConfigError
from core.fleet.tokens import AtomicTokenStore, utcnow


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def run_doctor(
    *,
    config_dir: Path,
    secrets_dir: Path | None,
    public_key_url: str | None,
    token_file: Path | None,
    get_text,
    now: datetime | None = None,
    key_file: Path | None = None,
) -> list[Check]:
    moment = now or utcnow()
    checks = [
        _check_config(config_dir),
        _check_private_key(secrets_dir, key_file=key_file),
        _check_public_key(config_dir, public_key_url, get_text),
        _check_token(token_file, moment),
    ]
    return checks


def _check_config(config_dir: Path) -> Check:
    try:
        regions = load_regions(config_dir / "regions.yaml")
        vehicles = load_vehicles(config_dir / "vehicles.yaml")
    except (ConfigError, OSError) as exc:
        return Check("config", False, str(exc))
    phase_1 = vehicles["capabilities"]["phase_1_commands"]
    hosts = sorted(regions["regions"])
    return Check("config", True, f"Regions {', '.join(hosts)} and phase 1 commands {', '.join(phase_1)} loaded.")


def _check_private_key(secrets_dir: Path | None, key_file: Path | None = None) -> Check:
    if key_file is not None:
        path = Path(key_file)
    elif secrets_dir is None:
        return Check("private_key", False, "No secrets directory was provided.")
    else:
        path = Path(secrets_dir) / "private-key.pem"
    if not path.is_file():
        return Check("private_key", False, f"The private key file is missing ({path.name}).")
    text = path.read_text(encoding="utf-8", errors="replace")
    if "PRIVATE KEY" not in text:
        return Check("private_key", False, "The key file does not look like a private key.")
    if "BEGIN PUBLIC KEY" in text and "PRIVATE KEY" not in text:
        return Check("private_key", False, "A public key was found where the private key should be.")
    if path.stat().st_size == 0:
        return Check("private_key", False, "The private key file is empty.")
    # Unix mode bits do not describe a Windows ACL. On Windows the owner
    # limits the file to their user; doctor still requires the key material.
    if os.name != "nt" and path.stat().st_mode & 0o077:
        return Check("private_key", False, "The private key is readable by someone other than the owner.")
    if os.name == "nt":
        return Check("private_key", True, "Private key file is present. On Windows, limit the file ACL to your user.")
    return Check("private_key", True, "Private key file is present and not group-readable.")


def _check_public_key(config_dir: Path, url: str | None, get_text) -> Check:
    if not url:
        return Check("public_key", False, "No public key URL was provided.")
    try:
        regions = load_regions(config_dir / "regions.yaml")
        suffix = regions["shared"]["public_key_well_known_path"]
    except (ConfigError, OSError) as exc:
        return Check("public_key", False, str(exc))
    if suffix not in url:
        return Check("public_key", False, "The URL is not the public-key path from regions.yaml.")
    if not url.startswith("https://"):
        return Check("public_key", False, "The public key URL must use HTTPS.")
    try:
        status, body = get_text(url)
    except Exception:
        return Check("public_key", False, "The public key URL could not be reached.")
    if status != 200:
        detail = f"The public key URL returned HTTP {status}."
        if status == 403:
            detail += " A CDN or bot-protection rule may be blocking non-browser clients."
        return Check("public_key", False, detail)
    if "BEGIN PUBLIC KEY" not in body or "PRIVATE KEY" in body:
        return Check("public_key", False, "The URL did not return a public key.")
    return Check("public_key", True, "Public key URL returned a public key.")


def _check_token(token_file: Path | None, now: datetime) -> Check:
    if token_file is None or not token_file.is_file():
        return Check("token", False, "No saved token file yet.")
    try:
        bundle = AtomicTokenStore(token_file).load()
    except Exception:
        return Check("token", False, "The token file cannot be read.")
    if bundle.access_valid(now):
        return Check("token", True, "The access token is still inside its lifetime.")
    if bundle.refresh_valid(now):
        return Check("token", True, "The access token needs a refresh. The refresh token is still in date.")
    return Check("token", False, "The refresh token is no longer in date. Sign in again.")
