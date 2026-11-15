"""The car command tool rebuilds itself when this repository's build inputs change."""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.command_tool as command_tool
from core.cli import main
from core.command_tool import FAIL_LINE, UPDATE_LINE, ensure_command_tool, expected_fingerprint, write_stamp
from core.errors import ConfigError
from core.fleet.area import WhereReport
from core.fleet.charge_schedule import ScheduleReport
from core.fleet.commands import TeslaControl
from core.fleet.departure import DepartureReport
from core.fleet.health import skipped_report
from core.fleet.report import skipped_vehicle_report

ROOT = Path(__file__).resolve().parents[1]
SENTENCE = (
    "Update with `agentdrive update`. If you only pull the code, "
    "AgentDrive rebuilds its car command tool by itself the next time it needs it."
)


def _use_real_check(monkeypatch):
    monkeypatch.setattr(command_tool, "ensure_command_tool", ensure_command_tool)


def _binary(home: Path) -> None:
    directory = home / "bin"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tesla-control"
    path.write_bytes(b"tool\n")
    path.chmod(0o755)


def _private(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "tokens.json").write_text('{"access_token":"owner"}\n', encoding="utf-8")
    (home / "host.yaml").write_text("adapter: template\n", encoding="utf-8")
    (home / "client.json").write_text('{"client_id":"cid"}\n', encoding="utf-8")
    (home / "boombox.yaml").write_text("version: 1\nquiet: 00:00-06:00\n", encoding="utf-8")
    keys = home / "keys"
    keys.mkdir()
    (keys / "private-key.pem").write_text("private\n", encoding="utf-8")


def _outside_bin(home: Path) -> dict[str, bytes]:
    found = {}
    if not home.exists():
        return found
    for path in home.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(home).as_posix()
        if relative.startswith("bin/"):
            continue
        found[relative] = path.read_bytes()
    return found


class Fake:
    def __init__(self):
        self.calls = []

    def command(self, name, **kwargs):
        self.calls.append(name)
        return "OK: " + name

    def check(self, **kwargs):
        self.calls.append("check")
        return skipped_report("asleep", tyre_bar=kwargs["tyre_bar"], low_battery_percent=kwargs["low_battery_percent"])

    def report(self, **kwargs):
        self.calls.append("report")
        return skipped_vehicle_report("asleep")

    def status(self, **kwargs):
        self.calls.append("status")
        return "status"

    def refresh(self):
        self.calls.append("refresh")
        return "refreshed"

    def where(self, **kwargs):
        self.calls.append("where")
        return WhereReport(kind="skipped", state="asleep")

    def departure_show(self, **kwargs):
        self.calls.append("departure-show")
        return DepartureReport("asleep", True, False, False, ())

    def charge_schedule_show(self, **kwargs):
        self.calls.append("charge-schedule-show")
        return ScheduleReport("asleep", True, False, False, ())

    def software(self, **kwargs):
        self.calls.append("software")
        return "asleep"

    def charging_history(self, **kwargs):
        self.calls.append("charging-history")
        return "Home charging is not included"

    def peek(self, **kwargs):
        self.calls.append("peek")
        return None


def _git(src: Path, *args: str) -> str:
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "AgentDrive"
    env["GIT_AUTHOR_EMAIL"] = "agentdrivedev@gmail.com"
    env["GIT_COMMITTER_NAME"] = "AgentDrive"
    env["GIT_COMMITTER_EMAIL"] = "agentdrivedev@gmail.com"
    completed = subprocess.run(
        ["git", "-C", str(src), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def test_fingerprint_tracks_the_commit_the_patches_and_the_build_script(tmp_path):
    root = tmp_path / "repo"
    setup = root / "setup"
    setup.mkdir(parents=True)
    (setup / "install.sh").write_text("build\n", encoding="utf-8")
    (setup / "patch_tesla_control_defrost.py").write_text("defrost\n", encoding="utf-8")
    home = tmp_path / "home"
    src = home / "src" / "vehicle-command"
    src.mkdir(parents=True)
    _git(src, "init", "-q")
    (src / "README").write_text("one\n", encoding="utf-8")
    _git(src, "add", "README")
    _git(src, "commit", "-q", "-m", "one")
    first_rev = _git(src, "rev-parse", "HEAD")
    first = expected_fingerprint(root, home)
    assert first.startswith(f"commit {first_rev}\n")
    assert "\npatches " in first
    assert first.endswith("\n")

    (src / "README").write_text("two\n", encoding="utf-8")
    _git(src, "add", "README")
    _git(src, "commit", "-q", "-m", "two")
    second_rev = _git(src, "rev-parse", "HEAD")
    second = expected_fingerprint(root, home)
    assert second.startswith(f"commit {second_rev}\n")
    assert second != first

    (setup / "vehicle-command.rev").write_text("# pinned\nabcdef1234567890\n", encoding="utf-8")
    pinned = expected_fingerprint(root, home)
    assert pinned.startswith("commit abcdef1234567890\n")
    assert pinned != second

    (setup / "patch_tesla_control_phase8.py").write_text("phase\n", encoding="utf-8")
    patched = expected_fingerprint(root, home)
    assert patched != pinned
    (setup / "install.sh").write_text("build\nextra\n", encoding="utf-8")
    assert expected_fingerprint(root, home) != patched
    assert expected_fingerprint(root, home) == expected_fingerprint(root, home)
    assert expected_fingerprint(ROOT) == expected_fingerprint(ROOT)
    assert expected_fingerprint(ROOT).startswith("commit ")


def test_go_older_than_1_23_does_not_build(tmp_path):
    calls = []
    with pytest.raises(ConfigError) as caught:
        ensure_command_tool(
            tmp_path,
            root=ROOT,
            runner=lambda *args: calls.append(args),
            go_version=lambda: (1, 22),
            announce=lambda text: calls.append(text),
        )
    assert calls == []
    message = str(caught.value)
    assert "Go 1.23 or newer" in message
    assert "Go 1.22" in message
    assert "not sent" in message

    with pytest.raises(ConfigError) as missing:
        ensure_command_tool(
            tmp_path,
            root=ROOT,
            runner=lambda *args: calls.append("ran"),
            go_version=lambda: None,
            announce=lambda text: calls.append(text),
        )
    assert calls == []
    assert "Go is not installed" in str(missing.value)
    assert "not sent" in str(missing.value)


def test_a_missing_binary_rebuilds_once_and_then_matches(tmp_path):
    write_stamp(tmp_path, ROOT)
    calls = []

    def runner(argv, env):
        calls.append(argv)
        _binary(tmp_path)
        write_stamp(tmp_path, ROOT)
        return SimpleNamespace(returncode=0)

    ensure_command_tool(tmp_path, root=ROOT, runner=runner, go_version=lambda: (1, 23), announce=lambda _text: None)
    ensure_command_tool(tmp_path, root=ROOT, runner=runner, go_version=lambda: (1, 23), announce=lambda _text: None)
    assert len(calls) == 1
    assert calls[0][-1] == "--rebuild-tool"


def test_stamp_match_runs_the_command_without_a_rebuild(tmp_path, monkeypatch, capsys):
    _use_real_check(monkeypatch)
    _private(tmp_path)
    _binary(tmp_path)
    write_stamp(tmp_path, ROOT)
    before = _outside_bin(tmp_path)
    fake = Fake()
    monkeypatch.setattr("core.cli.build_app", lambda home: fake)

    def fail(argv, env):
        raise AssertionError("rebuild")

    monkeypatch.setattr(command_tool, "_default_runner", fail)
    assert main(["--home", str(tmp_path), "defrost-on"]) == 0
    assert fake.calls == ["defrost-on"]
    assert UPDATE_LINE not in capsys.readouterr().err
    assert not (tmp_path / "bin" / ".tesla-control.lock").exists()
    assert _outside_bin(tmp_path) == before
    assert not (tmp_path / "src").exists()


def test_stamp_mismatch_rebuilds_then_runs_the_command(tmp_path, monkeypatch, capsys):
    _use_real_check(monkeypatch)
    _private(tmp_path)
    _binary(tmp_path)
    (tmp_path / "bin" / "tesla-control.stamp").write_text("stale\n", encoding="utf-8")
    before = _outside_bin(tmp_path)
    fake = Fake()
    calls = []

    def runner(argv, env):
        calls.append((list(argv), env.get("AGENTDRIVE_HOME")))
        _binary(Path(env["AGENTDRIVE_HOME"]))
        write_stamp(Path(env["AGENTDRIVE_HOME"]), ROOT)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("core.cli.build_app", lambda home: fake)
    monkeypatch.setattr(command_tool, "_default_runner", runner)
    monkeypatch.setattr(command_tool, "_go_version", lambda: (1, 23))
    assert main(["--home", str(tmp_path), "defrost-on"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "OK: defrost-on"
    assert UPDATE_LINE in captured.err
    assert UPDATE_LINE not in captured.out
    assert fake.calls == ["defrost-on"]
    assert len(calls) == 1
    assert calls[0][0][0] == "bash"
    assert calls[0][0][1].endswith("setup/install.sh")
    assert calls[0][0][2] == "--rebuild-tool"
    assert calls[0][1] == str(tmp_path)
    assert _outside_bin(tmp_path) == before
    assert not (tmp_path / "src").exists()
    assert (tmp_path / "bin" / "tesla-control.stamp").read_text(encoding="utf-8") == expected_fingerprint(ROOT, tmp_path)

    assert main(["--home", str(tmp_path), "defrost-on"]) == 0
    assert len(calls) == 1
    assert fake.calls == ["defrost-on", "defrost-on"]


def test_a_failed_rebuild_does_not_send_the_command(tmp_path, monkeypatch, capsys):
    _use_real_check(monkeypatch)
    _private(tmp_path)
    _binary(tmp_path)
    (tmp_path / "bin" / "tesla-control.stamp").write_text("stale\n", encoding="utf-8")
    before = _outside_bin(tmp_path)
    fake = Fake()
    monkeypatch.setattr("core.cli.build_app", lambda home: fake)
    monkeypatch.setattr(command_tool, "_go_version", lambda: None)
    monkeypatch.setattr(command_tool, "_default_runner", lambda *args: (_ for _ in ()).throw(AssertionError("built")))
    assert main(["--home", str(tmp_path), "defrost-on"]) == 2
    err = capsys.readouterr().err
    assert "Go 1.23 or newer" in err
    assert "not sent" in err
    assert UPDATE_LINE not in err
    assert fake.calls == []
    assert _outside_bin(tmp_path) == before

    monkeypatch.setattr(command_tool, "_go_version", lambda: (1, 23))

    def fail(argv, env):
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(command_tool, "_default_runner", fail)
    assert main(["--home", str(tmp_path), "lock"]) == 2
    err = capsys.readouterr().err
    assert FAIL_LINE in err
    assert UPDATE_LINE in err
    assert fake.calls == []
    assert _outside_bin(tmp_path) == before
    assert (tmp_path / "bin" / "tesla-control.stamp").read_text(encoding="utf-8") == "stale\n"


def test_read_only_commands_do_not_rebuild(tmp_path, monkeypatch, capsys):
    _use_real_check(monkeypatch)
    _private(tmp_path)
    before = _outside_bin(tmp_path)
    fake = Fake()
    calls = []
    monkeypatch.setattr("core.cli.build_app", lambda home: fake)
    monkeypatch.setattr(command_tool, "_default_runner", lambda *args: calls.append(args))
    monkeypatch.setattr(command_tool, "_go_version", lambda: (1, 23))
    commands = [
        ["check"],
        ["report"],
        ["status"],
        ["refresh"],
        ["where"],
        ["history", "due"],
        ["report-schedule"],
        ["departure", "show"],
        ["charge-schedule", "show"],
        ["software"],
        ["charging-history"],
        ["tariff", "show"],
        ["advice", "trip", "20"],
        ["boombox", "quiet"],
        ["service"],
    ]
    for command in commands:
        assert main(["--home", str(tmp_path), *command]) == 0, command
    captured = capsys.readouterr()
    assert calls == []
    assert UPDATE_LINE not in captured.err
    assert "defrost-on" not in fake.calls
    assert _outside_bin(tmp_path) == before
    assert not (tmp_path / "bin").exists()


def test_doctor_rebuilds_a_stale_tool_and_stops_when_it_cannot(tmp_path, monkeypatch, capsys):
    _use_real_check(monkeypatch)
    _private(tmp_path)
    calls = []

    def runner(argv, env):
        calls.append(argv)
        _binary(Path(env["AGENTDRIVE_HOME"]))
        write_stamp(Path(env["AGENTDRIVE_HOME"]), ROOT)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(command_tool, "_default_runner", runner)
    monkeypatch.setattr(command_tool, "_go_version", lambda: (1, 23))
    monkeypatch.setattr("core.cli.run_doctor", lambda **kwargs: [])
    assert main(["--home", str(tmp_path), "doctor", "--adapter", "template"]) == 0
    assert UPDATE_LINE in capsys.readouterr().err
    assert len(calls) == 1
    assert main(["--home", str(tmp_path), "doctor", "--adapter", "template"]) == 0
    assert len(calls) == 1

    stale = tmp_path / "again"
    _private(stale)
    monkeypatch.setattr(command_tool, "_go_version", lambda: (1, 21))
    monkeypatch.setattr("core.cli.run_doctor", lambda **kwargs: (_ for _ in ()).throw(AssertionError("continued")))
    assert main(["--home", str(stale), "doctor", "--adapter", "template"]) == 2
    assert "Go 1.21" in capsys.readouterr().err
    assert len(calls) == 1


def test_the_signed_tool_is_not_started_when_the_rebuild_fails(tmp_path, monkeypatch):
    _use_real_check(monkeypatch)
    monkeypatch.setattr(command_tool, "_go_version", lambda: (1, 23))
    monkeypatch.setattr(command_tool, "_default_runner", lambda argv, env: SimpleNamespace(returncode=1))
    started = []
    control = TeslaControl(
        "stale-tool",
        tmp_path / "key.pem",
        runner=lambda argv, timeout, env: started.append(argv) or (0, ""),
        home=tmp_path,
        token_dir=tmp_path,
    )
    with pytest.raises(ConfigError) as caught:
        control.run(vin="VIN-TEST", access_token="secret-token", args=["preconditioning-max", "on"])
    assert "not sent" in str(caught.value)
    assert started == []
    assert not any(path.name.startswith(".token-") for path in tmp_path.iterdir())

    def runner(argv, env):
        _binary(tmp_path)
        write_stamp(tmp_path, ROOT)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(command_tool, "_default_runner", runner)
    control.run(vin="VIN-TEST", access_token="secret-token", args=["preconditioning-max", "on"])
    assert started[0][0].endswith("tesla-control")
    assert "secret-token" not in started[0]


def test_concurrent_rebuilds_run_the_build_once(tmp_path):
    root = tmp_path / "repo"
    setup = root / "setup"
    setup.mkdir(parents=True)
    (setup / "install.sh").write_text("build\n", encoding="utf-8")
    (setup / "patch_tesla_control_defrost.py").write_text("patch\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    ready = tmp_path / "ready"
    ready.mkdir()
    gate = tmp_path / "gate"
    counter = tmp_path / "counter"
    child = tmp_path / "child.py"
    child.write_text(
        textwrap.dedent(
            """
            import os
            import time
            from pathlib import Path
            from types import SimpleNamespace

            from core.command_tool import ensure_command_tool, write_stamp

            home = Path(os.environ["HOME_DIR"])
            root = Path(os.environ["ROOT_DIR"])
            ready = Path(os.environ["READY"])
            gate = Path(os.environ["GATE"])
            counter = Path(os.environ["COUNTER"])
            (ready / str(os.getpid())).write_text("ready", encoding="utf-8")
            deadline = time.time() + 10
            while not gate.exists():
                if time.time() > deadline:
                    raise SystemExit(3)
                time.sleep(0.01)

            def runner(argv, env):
                fd = os.open(counter, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)
                os.write(fd, b"build\\n")
                os.close(fd)
                time.sleep(0.6)
                directory = home / "bin"
                directory.mkdir(parents=True, exist_ok=True)
                binary = directory / "tesla-control"
                binary.write_bytes(b"tool\\n")
                binary.chmod(0o755)
                write_stamp(home, root)
                return SimpleNamespace(returncode=0)

            ensure_command_tool(
                home,
                root=root,
                runner=runner,
                go_version=lambda: (1, 23),
                announce=lambda _text: None,
            )
            """
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["HOME_DIR"] = str(home)
    env["ROOT_DIR"] = str(root)
    env["READY"] = str(ready)
    env["GATE"] = str(gate)
    env["COUNTER"] = str(counter)
    procs = []
    try:
        procs = [
            subprocess.Popen(
                [sys.executable, str(child)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(2)
        ]
        deadline = time.time() + 10
        while len(list(ready.iterdir())) < 2:
            if time.time() > deadline:
                raise AssertionError("the two rebuilds did not start")
            time.sleep(0.02)
        gate.write_text("go", encoding="utf-8")
        results = [proc.communicate(timeout=15) for proc in procs]
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
    for proc, (out, err) in zip(procs, results):
        assert proc.returncode == 0, err
    assert counter.read_text(encoding="utf-8").count("build\n") == 1


def test_install_script_refuses_an_old_go_and_accepts_1_23(tmp_path):
    text = (ROOT / "setup" / "install.sh").read_text(encoding="utf-8")
    assert "python3 -m core.command_tool write-stamp" in text
    assert "--rebuild-tool" in text
    assert "vehicle-command.rev" in text
    assert "go build ./cmd/tesla-control" in text
    assert "setup/patch_tesla_control_boombox.py" in text
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        assert "go install" not in line
        assert "@latest" not in line

    old = _run_rebuild(tmp_path / "old", "go1.21.0")
    assert old.returncode == 1
    assert "Go 1.23 or newer" in old.stderr
    assert "git should not run" not in old.stderr
    assert "go build should not run" not in old.stderr

    missing = _run_rebuild(tmp_path / "missing", None)
    assert missing.returncode == 1
    assert "Go 1.23 or newer" in missing.stderr
    assert "git should not run" not in missing.stderr

    current = _run_rebuild(tmp_path / "current", "go1.23.0")
    assert current.returncode == 9
    assert "git should not run" in current.stderr
    assert "required to build" not in current.stderr
    assert "go build should not run" not in current.stderr


def _go_script(version: str | None) -> str:
    if version is None:
        return "#!/bin/sh\nexit 127\n"
    return (
        "#!/bin/sh\n"
        'if [ "$1" = "version" ]; then\n'
        f"  echo 'go version {version} linux/amd64'\n"
        "  exit 0\n"
        "fi\n"
        "echo 'go build should not run' >&2\n"
        "exit 8\n"
    )


def _run_rebuild(directory: Path, version: str | None) -> subprocess.CompletedProcess:
    directory.mkdir()
    bindir = directory / "bin"
    bindir.mkdir()
    if version is not None:
        go = bindir / "go"
        go.write_text(_go_script(version), encoding="utf-8")
        go.chmod(0o755)
    git = bindir / "git"
    git.write_text("#!/bin/sh\necho 'git should not run' >&2\nexit 9\n", encoding="utf-8")
    git.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = str(bindir) + os.pathsep + "/usr/bin:/bin"
    env["AGENTDRIVE_HOME"] = str(directory / "home")
    return subprocess.run(
        ["/usr/bin/bash", "setup/install.sh", "--rebuild-tool"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_write_stamp_cli_matches_the_fingerprint(tmp_path):
    env = os.environ.copy()
    env["AGENTDRIVE_HOME"] = str(tmp_path)
    env["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "core.command_tool", "write-stamp"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "bin" / "tesla-control.stamp").read_text(encoding="utf-8") == expected_fingerprint(ROOT, tmp_path)


def test_update_docs_tell_a_pull_to_rebuild_the_tool():
    for name in ("01-install-and-safety.md", "06-queries-and-commands.md", "prerequisites/computer.md"):
        text = (ROOT / "docs" / name).read_text(encoding="utf-8")
        assert SENTENCE in text
        assert "cloudflare" not in text.lower()
