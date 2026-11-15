"""Keep the patched tesla-control binary in step with this repository.

The stamp beside the binary is the fingerprint of the vehicle-command commit
and the local patch and build scripts. A command that needs the tool rebuilds
it when that stamp is missing or different. Reads do not rebuild it, and the
rebuild writes only under bin/ and src/vehicle-command.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

from core.atomic import write_atomic
from core.config import repo_root
from core.errors import ConfigError
from core.paths import resolve_home

STAMP_NAME = "tesla-control.stamp"
LOCK_NAME = ".tesla-control.lock"
PIN_NAME = "vehicle-command.rev"
MIN_GO = (1, 23)
UPDATE_LINE = "Updating the car command tool after an AgentDrive update (one time, about a minute)..."
FAIL_LINE = "The car command tool could not be updated, so the command was not sent."

_GO_VERSION = re.compile(r"go(\d+)\.(\d+)")
_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")


def expected_fingerprint(root: Path | None = None, home: Path | None = None) -> str:
    """Fingerprint of every input that changes the tesla-control build.

    The commit is the one pinned in the repository, or the commit checked
    out under the config home when the repository does not pin one. The
    rest is a hash of ``setup/patch_tesla_control_*.py`` and the build script.
    """
    root = Path(root) if root is not None else repo_root()
    commit = _commit(root, home)
    patches = _digest(_patch_files(root))
    script = _digest(_build_scripts(root))
    return f"commit {commit}\npatches {patches}\nscript {script}\n"


def write_stamp(home: Path, root: Path | None = None) -> None:
    """Write the stamp for the binary that was just built."""
    home = Path(home)
    text = expected_fingerprint(root, home)
    write_atomic(home / "bin" / STAMP_NAME, text, mode=0o644)


def ensure_command_tool(
    home: Path,
    *,
    root: Path | None = None,
    runner=None,
    go_version=None,
    announce=None,
) -> None:
    """Rebuild when the stamp does not match. Return without building when it does.

    A failure raises before the caller can send a vehicle command. Two
    runs share one lock, and the waiter uses the binary the first run built.
    """
    home = Path(home)
    root = Path(root) if root is not None else repo_root()
    if _current(home, root):
        return
    run = runner or _default_runner
    probe = go_version or _go_version
    say = announce or _announce
    with _build_lock(home):
        if _current(home, root):
            return
        version = probe()
        if version is None or version < MIN_GO:
            raise ConfigError(_go_message(version))
        say(UPDATE_LINE)
        env = os.environ.copy()
        env["AGENTDRIVE_HOME"] = str(home)
        env["PYTHONPATH"] = _prepend(str(root), env.get("PYTHONPATH", ""))
        argv = ["bash", str(root / "setup" / "install.sh"), "--rebuild-tool"]
        try:
            completed = run(argv, env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ConfigError(FAIL_LINE) from exc
        code = completed.returncode if hasattr(completed, "returncode") else int(completed)
        if code != 0 or not _current(home, root):
            raise ConfigError(FAIL_LINE)


def _current(home: Path, root: Path) -> bool:
    binary = home / "bin" / "tesla-control"
    if not binary.is_file():
        return False
    return _stamp_text(home) == expected_fingerprint(root, home)


def _stamp_text(home: Path) -> str:
    path = home / "bin" / STAMP_NAME
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _commit(root: Path, home: Path | None) -> str:
    pinned = _pinned_commit(root)
    if pinned:
        return pinned
    if home is None:
        return "unpinned"
    src = Path(home) / "src" / "vehicle-command"
    if not (src / ".git").exists():
        return "unpinned"
    return _rev_parse(src) or "unpinned"


def _pinned_commit(root: Path) -> str:
    path = root / "setup" / PIN_NAME
    if not path.is_file() or path.is_symlink():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        cleaned = line.split("#", 1)[0].strip()
        if cleaned:
            return cleaned.split()[0]
    return ""


def _rev_parse(src: Path) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        completed = subprocess.run(
            ["git", "-C", str(src), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    rev = completed.stdout.strip().lower()
    if _COMMIT.fullmatch(rev):
        return rev
    return ""


def _patch_files(root: Path) -> list[Path]:
    setup = root / "setup"
    return sorted(path for path in setup.glob("patch_tesla_control_*.py") if path.is_file())


def _build_scripts(root: Path) -> list[Path]:
    setup = root / "setup"
    found: list[Path] = []
    install = setup / "install.sh"
    if install.is_file():
        found.append(install)
    for path in sorted(setup.glob("build*.sh")):
        if path.is_file() and path not in found:
            found.append(path)
    return found


def _digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        data = path.read_bytes()
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _go_version() -> tuple[int, int] | None:
    try:
        completed = subprocess.run(
            ["go", "version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    match = _GO_VERSION.search(completed.stdout)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _go_message(version: tuple[int, int] | None) -> str:
    if version is None:
        found = "Go is not installed"
    else:
        found = f"this machine has Go {version[0]}.{version[1]}"
    return (
        "Go 1.23 or newer is required to update the car command tool "
        f"({found}), so the command was not sent."
    )


def _announce(text: str) -> None:
    print(text, file=sys.stderr)


def _default_runner(argv: list[str], env: dict[str, str]):
    return subprocess.run(argv, env=env, check=False, capture_output=True, text=True)


def _prepend(entry: str, current: str) -> str:
    parts = [part for part in current.split(os.pathsep) if part and part != entry]
    return os.pathsep.join([entry, *parts])


def _lock_handle(handle, *, exclusive: bool) -> None:
    if fcntl is not None:
        flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_UN
        fcntl.flock(handle.fileno(), flag)
        return
    import msvcrt

    if exclusive and handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    if exclusive:
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
    else:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


@contextmanager
def _build_lock(home: Path):
    directory = home / "bin"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / LOCK_NAME
    if path.is_symlink():
        raise ConfigError(FAIL_LINE)
    mode = "a+b" if fcntl is None else "a+"
    handle = open(path, mode)
    try:
        if os.name != "nt":
            os.chmod(path, 0o600)
        _lock_handle(handle, exclusive=True)
        try:
            yield
        finally:
            _lock_handle(handle, exclusive=False)
    finally:
        handle.close()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args != ["write-stamp"]:
        print("Usage: python -m core.command_tool write-stamp", file=sys.stderr)
        return 2
    write_stamp(resolve_home(), repo_root())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
