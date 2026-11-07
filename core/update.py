"""Upgrade a local checkout from the latest main without touching private config.

Private files under the home directory stay as they are: tokens, keys, client
credentials, host.yaml, and vehicles. The update pulls main, or downloads it
when this tree is not a git checkout, then re-runs the install steps.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import Callable

from core.errors import ConfigError
from core.fleet.http import open_url

ARCHIVE_URL = "https://github.com/agentdrive-dev/agentdrive/archive/refs/heads/main.tar.gz"

PRIVATE_NAMES = (
    "tokens.json",
    "client.json",
    "host.yaml",
    "vehicles.yaml",
    "vehicles",
    "private-key.pem",
    "keys",
    "secrets",
    ".refresh.lock",
    ".session-cache.json",
    "history.key",
    "history.db",
    ".history.lock",
)


def fingerprint(home: Path) -> dict[str, str]:
    """Hash the private files. Missing files are recorded as missing."""
    home = Path(home)
    found: dict[str, str] = {}
    for name in PRIVATE_NAMES:
        found[name] = _digest(home / name)
    return found


def update_install(
    root: Path,
    home: Path,
    *,
    runner: Callable | None = None,
    fetch: Callable[[str], bytes] | None = None,
) -> str:
    """Return ``git`` or ``download``. Raise if a private file changes."""
    root = Path(root).resolve()
    home = Path(home).resolve()
    if root == home or _inside(root, home) or _inside(home, root):
        raise ConfigError("The home directory and the repository must be different directories.")
    before = fingerprint(home)
    run = runner or _default_runner
    source = "git"
    try:
        if (root / ".git").exists():
            _check(run(["git", "-C", str(root), "pull", "--ff-only", "origin", "main"]))
            source = "git"
        else:
            blob = (fetch or _fetch_bytes)(ARCHIVE_URL)
            apply_archive(root, blob, home)
            source = "download"
        _check(run(["bash", str(root / "setup" / "install.sh"), "--update"]))
    except Exception:
        if fingerprint(home) != before:
            raise ConfigError("Update touched private configuration and stopped.")
        raise
    if fingerprint(home) != before:
        raise ConfigError("Update touched private configuration and stopped.")
    return source


def apply_archive(root: Path, blob: bytes, home: Path) -> None:
    """Copy an upstream archive into the repo. Paths that escape the repo are skipped."""
    root = Path(root).resolve()
    home = Path(home).resolve()
    try:
        archive = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
    except (tarfile.TarError, OSError) as exc:
        raise ConfigError("The downloaded archive could not be read.") from exc
    with archive:
        members = [item for item in archive.getmembers() if item.isfile()]
        prefix = _shared_prefix(members)
        for member in members:
            relative = _relative(member.name, prefix)
            if relative is None:
                continue
            dest = (root / relative).resolve()
            if dest.is_symlink() or not _inside(dest, root) or _inside(dest, home):
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            _write_bytes(dest, extracted.read())


def _shared_prefix(members: list[tarfile.TarInfo]) -> str | None:
    prefixes = []
    for member in members:
        parts = PurePosixPath(member.name).parts
        if len(parts) < 2:
            return None
        prefixes.append(parts[0])
    if prefixes and len(set(prefixes)) == 1:
        return prefixes[0]
    return None


def _relative(name: str, prefix: str | None) -> Path | None:
    if name.startswith("/") or "\\" in name:
        return None
    parts = PurePosixPath(name).parts
    if not parts or any(part in {"..", ""} for part in parts):
        return None
    if prefix and parts[0] == prefix:
        parts = parts[1:]
    if not parts:
        return None
    return Path(*parts)


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _digest(path: Path) -> str:
    if not path.exists():
        return "missing"
    if path.is_symlink():
        return "symlink:" + str(path.readlink())
    if path.is_dir():
        parts: list[str] = []
        for child in sorted(item for item in path.rglob("*") if item.is_file() and not item.is_symlink()):
            relative = child.relative_to(path).as_posix()
            parts.append(relative + ":" + hashlib.sha256(child.read_bytes()).hexdigest())
        return "|".join(parts)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _check(completed) -> None:
    code = completed.returncode if hasattr(completed, "returncode") else int(completed)
    if code != 0:
        raise ConfigError("Could not update the checkout. Private configuration was left unchanged.")


def _default_runner(argv: list[str]):
    return subprocess.run(argv, check=False, capture_output=True, text=True)


def _fetch_bytes(url: str) -> bytes:
    with open_url(url, method="GET", timeout=60) as response:
        return response.read()
