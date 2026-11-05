import os
import subprocess
from pathlib import Path

from core.errors import CommandFailed
from core.fleet.commands import TeslaControl


def test_token_is_passed_by_environment_and_the_temp_file_is_removed(tmp_path):
    seen = {}

    def runner(argv, timeout, env):
        token_path = env["TESLA_TOKEN_FILE"]
        seen["token"] = Path(token_path).read_text(encoding="utf-8")
        seen["mode"] = Path(token_path).stat().st_mode & 0o777
        seen["argv"] = argv
        seen["env_vin"] = env["TESLA_VIN"]
        seen["env_key"] = env["TESLA_KEY_FILE"]
        return 0, "stdout secret-token and VIN-TEST"

    tool = TeslaControl("tesla-control", tmp_path / "private-key.pem", runner=runner, token_dir=tmp_path)
    result = tool.run(vin="VIN-TEST", access_token="secret-token", args=["flash-lights"])
    assert seen["token"] == "secret-token"
    assert seen["mode"] == 0o600
    assert seen["env_vin"] == "VIN-TEST"
    assert seen["env_key"].endswith("private-key.pem")
    assert "secret-token" not in seen["argv"]
    assert "VIN-TEST" not in seen["argv"]
    assert "-token-file" not in seen["argv"]
    assert seen["argv"][-1] == "flash-lights"
    assert "secret-token" not in result.stderr_redacted
    assert "VIN-TEST" not in result.stderr_redacted
    assert list(tmp_path.iterdir()) == []


def test_nonzero_exit_is_not_retried(tmp_path):
    calls = {"n": 0}

    def runner(argv, timeout, env):
        calls["n"] += 1
        calls["path"] = env["TESLA_TOKEN_FILE"]
        return 3, "nope secret-token"

    tool = TeslaControl("tesla-control", tmp_path / "key.pem", runner=runner, token_dir=tmp_path)
    try:
        tool.run(vin="VIN-TEST", access_token="secret-token", args=["lock"])
    except CommandFailed as exc:
        assert "secret-token" not in str(exc)
        assert "VIN-TEST" not in str(exc)
    else:
        raise AssertionError("expected failure")
    assert calls["n"] == 1
    assert not Path(calls["path"]).exists()


def test_token_file_is_removed_when_the_tool_times_out(tmp_path):
    seen = {}

    def runner(argv, timeout, env):
        seen["path"] = env["TESLA_TOKEN_FILE"]
        raise subprocess.TimeoutExpired(argv, timeout)

    tool = TeslaControl("tesla-control", tmp_path / "key.pem", runner=runner, token_dir=tmp_path)
    try:
        tool.run(vin="VIN-TEST", access_token="secret-token", args=["honk"])
    except CommandFailed as exc:
        assert "secret-token" not in str(exc)
    else:
        raise AssertionError("expected timeout")
    assert not os.path.exists(seen["path"])
