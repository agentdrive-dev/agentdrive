"""Run signed commands through the official tesla-control binary.

The binary reads TESLA_TOKEN_FILE, TESLA_KEY_FILE, and TESLA_VIN from the
environment. Those names are required by the tool. The access token is written
to a mode 0600 file and removed in a finally block. It is never placed on the
argument list or in returned error text.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.errors import CommandFailed
from core.redact import redact


@dataclass
class CommandResult:
    returncode: int
    stderr_redacted: str


class TeslaControl:
    def __init__(self, binary: str, key_file: Path, runner=None, *, session_cache: Path | None = None, token_dir: Path | None = None):
        self.binary = binary
        self.key_file = Path(key_file)
        self.runner = runner or _subprocess_runner
        self.session_cache = Path(session_cache) if session_cache else None
        self.token_dir = Path(token_dir) if token_dir else None

    def run(self, *, vin: str, access_token: str, args: list[str], timeout: float = 90) -> CommandResult:
        if not vin or not access_token or not args:
            raise CommandFailed("A signed command needs a vehicle id, an access token, and a command")
        directory = self.token_dir
        created_dir = None
        if directory is None:
            created_dir = Path(tempfile.mkdtemp(prefix="agentdrive-cmd-"))
            if os.name != "nt":
                os.chmod(created_dir, 0o700)
            directory = created_dir
        else:
            directory.mkdir(parents=True, exist_ok=True)
        fd, token_name = tempfile.mkstemp(prefix=".token-", dir=str(directory))
        token_path = Path(token_name)
        try:
            if os.name != "nt" and hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(access_token)
                handle.flush()
                os.fsync(handle.fileno())
            argv = [self.binary]
            if self.session_cache is not None:
                argv.extend(["-session-cache", str(self.session_cache)])
            argv.extend(args)
            env = os.environ.copy()
            env["TESLA_TOKEN_FILE"] = str(token_path)
            env["TESLA_KEY_FILE"] = str(self.key_file)
            env["TESLA_VIN"] = vin
            try:
                returncode, output = self.runner(argv, timeout, env)
            except FileNotFoundError as exc:
                raise CommandFailed("tesla-control is not installed on this machine") from exc
            except subprocess.TimeoutExpired as exc:
                raise CommandFailed("tesla-control timed out. The command was not sent again.") from exc
            redacted = redact(output or "", [access_token, vin, str(token_path)])
            if returncode != 0:
                raise CommandFailed(
                    f"tesla-control failed (exit {returncode}). The command was not sent again. {redacted}".strip()
                )
            return CommandResult(returncode=returncode, stderr_redacted=redacted)
        finally:
            try:
                token_path.unlink()
            except OSError:
                pass
            if created_dir is not None:
                try:
                    created_dir.rmdir()
                except OSError:
                    pass


def _subprocess_runner(argv: list[str], timeout: float, env: dict) -> tuple[int, str]:
    completed = subprocess.run(
        argv,
        capture_output=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    return completed.returncode, stdout + stderr
