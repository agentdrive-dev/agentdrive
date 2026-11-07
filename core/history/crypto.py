"""Encrypt the history database at rest.

The key is 32 random bytes in the private config home. The database file is
AES-256-GCM ciphertext. SQLCipher is not required. The key must not be copied
into the git repo or into chat.
"""

from __future__ import annotations

import os
import secrets
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.errors import HistoryError

MAGIC = b"ADH1"
KEY_NAME = "history.key"
DB_NAME = "history.db"
LOCK_NAME = ".history.lock"
_NONCE_LEN = 12
_KEY_LEN = 32


def database_paths(home: Path) -> tuple[Path, Path]:
    root = Path(home)
    return root / KEY_NAME, root / DB_NAME


def seal(key: bytes, plaintext: bytes) -> bytes:
    if len(key) != _KEY_LEN:
        raise HistoryError("The history key could not be read.")
    nonce = secrets.token_bytes(_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, MAGIC)
    return MAGIC + nonce + ciphertext


def open_sealed(key: bytes, blob: bytes) -> bytes:
    if len(key) != _KEY_LEN or len(blob) < len(MAGIC) + _NONCE_LEN + 16 or not blob.startswith(MAGIC):
        raise HistoryError("The saved history could not be unlocked.")
    nonce = blob[len(MAGIC) : len(MAGIC) + _NONCE_LEN]
    try:
        return AESGCM(key).decrypt(nonce, blob[len(MAGIC) + _NONCE_LEN :], MAGIC)
    except Exception as exc:
        raise HistoryError("The saved history could not be unlocked.") from exc


def write_private_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Atomically replace a file and keep it private on POSIX."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        if os.name != "nt" and hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, mode)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_or_create_key(path: Path, *, create: bool) -> bytes:
    if path.is_file():
        data = path.read_bytes()
        if len(data) != _KEY_LEN:
            raise HistoryError("The history key could not be read.")
        return data
    if path.exists():
        raise HistoryError("The history key could not be read.")
    if not create:
        raise HistoryError("No vehicle history has been saved on this computer yet.")
    key = secrets.token_bytes(_KEY_LEN)
    write_private_bytes(path, key)
    return key
