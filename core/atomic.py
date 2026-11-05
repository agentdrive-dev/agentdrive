"""Small atomic file replace used for stamps and profiles."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_atomic(path: Path, text: str, mode: int = 0o600) -> None:
    """Write text to a temp file in the same directory, then rename it into place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        _private_fd(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _private_path(path, mode)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _private_fd(fd: int, mode: int) -> None:
    """Restrict a descriptor on POSIX. Windows uses an ACL, not these bits."""
    if os.name == "nt" or not hasattr(os, "fchmod"):
        return
    os.fchmod(fd, mode)


def _private_path(path: Path, mode: int) -> None:
    """Restrict a path on POSIX. Windows uses an ACL, not these bits."""
    if os.name == "nt":
        return
    os.chmod(path, mode)
