import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.doctor import run_doctor
from core.errors import InstallBlocked
from core.fleet.tokens import AtomicTokenStore, TokenBundle
from core.safety.install_gate import PHRASE, accept, assert_install_allowed, present


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_install_gate_requires_a_later_turn(tmp_path):
    text = present(tmp_path, "turn-1")
    assert "Experimental and testing purposes only." in text
    with pytest.raises(InstallBlocked):
        accept(tmp_path, "turn-1", PHRASE)
    with pytest.raises(InstallBlocked):
        accept(tmp_path, "turn-2", "ok")
    accept(tmp_path, "turn-2", PHRASE)
    assert_install_allowed(tmp_path)
    present(tmp_path, "turn-3")
    with pytest.raises(InstallBlocked):
        assert_install_allowed(tmp_path)


def test_install_script_refuses_without_acceptance(tmp_path):
    completed = subprocess.run(
        ["bash", "setup/install.sh"],
        cwd=ROOT,
        env={"AGENTDRIVE_HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert not (ROOT / ".venv").exists()


def test_publish_script_rejects_a_bad_project_name(tmp_path):
    key = tmp_path / "public-key.pem"
    key.write_text("-----BEGIN PUBLIC KEY-----\nMFkw\n-----END PUBLIC KEY-----\n", encoding="utf-8")
    bad = subprocess.run(
        ["bash", "setup/publish-public-key.sh", "--project", "tesla-keys", "--public-key", str(key)],
        cwd=ROOT,
        env={"CHECK_ONLY": "1", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 2
    good = subprocess.run(
        ["bash", "setup/publish-public-key.sh", "--project", "agentdrive-keys", "--public-key", str(key)],
        cwd=ROOT,
        env={"CHECK_ONLY": "1", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert good.returncode == 0, good.stderr


def test_doctor_reports_each_check(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    key = secrets / "private-key.pem"
    key.write_text("-----BEGIN " + "PRIVATE KEY-----\nsecret\n-----END " + "PRIVATE KEY-----\n", encoding="utf-8")
    key.chmod(0o600)
    token = tmp_path / "tokens.json"
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
        secrets_dir=secrets,
        public_key_url=url,
        token_file=token,
        get_text=get_text,
        now=NOW,
    )
    assert {item.name: item.ok for item in checks} == {
        "config": True,
        "private_key": True,
        "public_key": True,
        "token": True,
    }
    loose = secrets / "private-key.pem"
    loose.chmod(0o644)
    failed = run_doctor(
        config_dir=ROOT / "config",
        secrets_dir=secrets,
        public_key_url=url,
        token_file=token,
        get_text=get_text,
        now=NOW,
    )
    assert any(item.name == "private_key" and not item.ok for item in failed)
    assert "secret" not in " ".join(item.detail for item in failed)


def test_doctor_public_key_403_hints_at_cdn_blocking():
    url = "https://your-project.pages.dev/.well-known/appspecific/com.tesla.3p.public-key.pem"

    def run(status):
        checks = run_doctor(
            config_dir=ROOT / "config",
            secrets_dir=None,
            public_key_url=url,
            token_file=None,
            get_text=lambda requested: (status, "blocked"),
            now=NOW,
        )
        return next(item for item in checks if item.name == "public_key")

    blocked = run(403)
    assert blocked.ok is False
    assert blocked.detail == (
        "The public key URL returned HTTP 403. "
        "A CDN or bot-protection rule may be blocking non-browser clients."
    )
    missing = run(404)
    assert missing.detail == "The public key URL returned HTTP 404."


def test_cli_accepts_only_the_explicit_sentence(tmp_path):
    from core.cli import main

    assert main(["--home", str(tmp_path), "gate-present", "--turn-id", "one"]) == 0
    assert main(["--home", str(tmp_path), "gate-accept", "--turn-id", "two", "--statement", PHRASE]) == 0
    stamp = json.loads((tmp_path / "disclaimer-accepted.json").read_text())
    assert stamp["accepted"] is True
