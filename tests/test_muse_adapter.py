"""Muse adapter, config home, and private-key path."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from adapters.muse import INSTALL_NOTE, MuseAdapter
from core.cli import main
from core.doctor import run_doctor
from core.errors import MemoryRejected, ScheduleBlocked
from core.fleet.tokens import AtomicTokenStore, TokenBundle
from core.paths import WINDOWS_KEY_EXAMPLE, resolve_home, resolve_key, resolve_token_file
from core.safety.confirm import Challenge


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def prompter(challenge):
    return f"I confirm {challenge.command.replace('_', ' ')}"


def test_muse_adapter_persists_memory_and_refuses_secrets(tmp_path):
    muse = MuseAdapter(tmp_path, prompter)
    muse.remember("suburb", "Example Suburb")
    muse.schedule("health-check", "every 6 hours", {"command": "check", "json": True})
    with pytest.raises(ScheduleBlocked):
        muse.schedule("night", "22:00", {"command": "unlock", "preconfirmed": True})
    assert muse.notify("all quiet", requires_action=False) is False
    assert muse.notify("tyre pressure is low", requires_action=True) is True
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "client-secret").write_text("top-secret\n", encoding="utf-8")
    assert muse.get_secret("client_secret") == "top-secret"
    reloaded = MuseAdapter(tmp_path, prompter)
    assert reloaded.recall("suburb") == "Example Suburb"
    assert reloaded.schedules[0]["name"] == "health-check"
    assert "top-secret" not in (tmp_path / "memory.json").read_text(encoding="utf-8")
    challenge = Challenge("1", "unlock", "Confirm unlock")
    assert muse.confirm(challenge).accepted is True
    muse._in_schedule = True
    with pytest.raises(ScheduleBlocked):
        muse.confirm(challenge)


def test_muse_memory_rejects_a_vin(tmp_path):
    muse = MuseAdapter(tmp_path, prompter)
    with pytest.raises(MemoryRejected):
        muse.remember("vin", "5YJ00000000000000")


def test_install_note_and_bundle(tmp_path, capsys):
    code = main(["--home", str(tmp_path), "adapter", "install", "--adapter", "muse"])
    assert code == 0
    output = capsys.readouterr().out
    assert "Phase 2 stub" not in output
    assert INSTALL_NOTE in output
    assert "agentdrive check --json" in output
    assert WINDOWS_KEY_EXAMPLE in output
    files = MuseAdapter.bundle_files()
    assert set(files) == {"skill", "prompt", "config"}
    for path in files.values():
        assert path.is_file(), path
    skill = files["skill"].read_text(encoding="utf-8")
    assert skill.startswith("---\n")
    assert "name: agentdrive" in skill
    assert "tesla-control" in skill
    source = (ROOT / "adapters" / "muse.py").read_text(encoding="utf-8")
    assert "tesla-control" not in source
    assert "NotImplementedError" not in source
    prompt = files["prompt"].read_text(encoding="utf-8").strip()
    page = (ROOT / "docs" / "10-install-in-muse.md").read_text(encoding="utf-8")
    assert prompt in page
    assert "https://github.com/agentdrive-dev/agentdrive" in prompt
    assert "agentdrive doctor" in prompt
    assert "read-only vehicle status check" in prompt
    assert "already paired with another agent" in prompt
    assert "security trade-off" in prompt
    example = files["config"].read_text(encoding="utf-8")
    assert example == (ROOT / "config" / "runtime.example.yaml").read_text(encoding="utf-8")
    loaded = yaml.safe_load(example)
    assert loaded["key_file"] == WINDOWS_KEY_EXAMPLE
    assert loaded["token_file"].endswith("tokens.json")


VEHICLE_TESTED = "Supported — tested end-to-end on a real vehicle (v0.1, internet commands)"


def test_supported_agents_list_marks_muse_supported():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assistants = (ROOT / "docs" / "07-assistants.md").read_text(encoding="utf-8")
    muse_page = (ROOT / "docs" / "10-install-in-muse.md").read_text(encoding="utf-8")
    assistant_page = (ROOT / "docs" / "prerequisites" / "assistant.md").read_text(encoding="utf-8")
    assert "## Supported agents" in readme
    assert f"| Muse | {VEHICLE_TESTED} |" in readme
    assert f"| [Grok Bot](https://grokbot.ai) | {VEHICLE_TESTED} |" in readme
    assert "Tested end to end on a real car" not in readme
    assert "The Muse adapter is not built yet" not in readme
    assert "stub and not built yet" not in readme
    assert "| `muse` |" in assistants
    assert "Phase 2 stub" not in assistants
    for text in (readme, assistants, muse_page, assistant_page):
        lowered = text.lower()
        assert "unverified" not in lowered
        assert "untested" not in lowered
        assert VEHICLE_TESTED.lower() in lowered
    muse_row = assistants.split("| `muse` |", 1)[1].split("\n", 1)[0]
    grok_row = assistants.split("| `grokbot` |", 1)[1].split("\n", 1)[0]
    assert VEHICLE_TESTED in muse_row
    assert VEHICLE_TESTED in grok_row


def test_config_home_and_key_path(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTDRIVE_HOME", str(tmp_path / "custom"))
    assert resolve_home() == tmp_path / "custom"
    monkeypatch.delenv("AGENTDRIVE_HOME", raising=False)
    assert resolve_home() == Path.home() / ".config" / "agentdrive"

    home = tmp_path / "home"
    (home / "keys").mkdir(parents=True)
    local = home / "keys" / "private-key.pem"
    local.write_text("local\n", encoding="utf-8")
    external = tmp_path / "agentdrive-key" / "private-key.pem"
    external.parent.mkdir()
    external.write_text("shared\n", encoding="utf-8")
    assert resolve_key(home, env={}) == local
    assert resolve_key(home, env={"AGENTDRIVE_KEY_FILE": str(external)}) == external
    assert resolve_key(home, env={"TESLA_KEY_FILE": str(external)}) == external
    assert resolve_key(home, env={"AGENTDRIVE_KEY_FILE": str(local), "TESLA_KEY_FILE": str(external)}) == local

    (home / "runtime.yaml").write_text(
        f"key_file: '{WINDOWS_KEY_EXAMPLE}'\ntoken_file: '{tmp_path / 'oauth.json'}'\n",
        encoding="utf-8",
    )
    assert resolve_key(home, env={}) == Path(WINDOWS_KEY_EXAMPLE)
    assert resolve_token_file(home, env={}) == tmp_path / "oauth.json"
    other = tmp_path / "other-tokens.json"
    assert resolve_token_file(home, env={"AGENTDRIVE_TOKEN_FILE": str(other), "TESLA_TOKEN_FILE": str(external)}) == other
    assert resolve_token_file(tmp_path / "empty-home", env={"TESLA_TOKEN_FILE": str(other)}) == tmp_path / "empty-home" / "tokens.json"


def test_doctor_checks_a_key_outside_the_config_home(tmp_path, monkeypatch, capsys):
    key = tmp_path / "agentdrive-key" / "private-key.pem"
    key.parent.mkdir()
    key.write_text("-----BEGIN " + "PRIVATE KEY-----\nabc\n-----END " + "PRIVATE KEY-----\n", encoding="utf-8")
    key.chmod(0o600)
    token = tmp_path / "oauth.json"
    AtomicTokenStore(token).save_atomic(
        TokenBundle(
            access_token="access",
            refresh_token="refresh",
            access_expires_at=(NOW + timedelta(hours=2)).isoformat(),
            refresh_expires_at=(NOW + timedelta(days=10)).isoformat(),
        )
    )
    url = "https://your-project.pages.dev/.well-known/appspecific/com.tesla.3p.public-key.pem"

    def get_text(requested):
        assert requested == url
        return 200, "-----BEGIN PUBLIC KEY-----\nMFkw\n-----END PUBLIC KEY-----\n"

    checks = run_doctor(
        config_dir=ROOT / "config",
        secrets_dir=tmp_path / "missing",
        public_key_url=url,
        token_file=token,
        get_text=get_text,
        now=NOW,
        key_file=key,
    )
    assert {item.name: item.ok for item in checks}["private_key"] is True

    home = tmp_path / "home"
    monkeypatch.setenv("AGENTDRIVE_KEY_FILE", str(key))
    monkeypatch.setenv("AGENTDRIVE_TOKEN_FILE", str(token))
    seen = {}

    def fake_run_doctor(**kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr("core.cli.run_doctor", fake_run_doctor)
    assert main(["--home", str(home), "doctor", "--adapter", "muse"]) == 0
    assert seen["key_file"] == key
    assert seen["token_file"] == token
    assert "muse (override)" in capsys.readouterr().out


def test_saved_schedule_json_has_no_confirmation(tmp_path):
    muse = MuseAdapter(tmp_path, prompter)
    muse.schedule("health-check", "every 6 hours", {"command": "check"})
    saved = json.loads((tmp_path / "schedules.json").read_text(encoding="utf-8"))
    assert "confirmation" not in json.dumps(saved)
