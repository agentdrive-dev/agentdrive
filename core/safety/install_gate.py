"""The disclaimer is shown first. Acceptance has to arrive in a later turn."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.atomic import write_atomic
from core.config import repo_root
from core.errors import InstallBlocked

PHRASE = "I have read, understood, and accept the AgentDrive disclaimer and security reminder"


def phrase_digest() -> str:
    return hashlib.sha256(PHRASE.encode("utf-8")).hexdigest()


def present(home: Path, turn_id: str) -> str:
    if not turn_id.strip():
        raise InstallBlocked("A turn id is required.")
    text = _documents()
    home.mkdir(parents=True, exist_ok=True)
    write_atomic(home / "disclaimer-presented.json", json.dumps({"presented_turn": turn_id.strip()}))
    accepted = home / "disclaimer-accepted.json"
    if accepted.exists():
        accepted.unlink()
    return text


def accept(home: Path, turn_id: str, statement: str) -> None:
    presented_path = home / "disclaimer-presented.json"
    if not presented_path.is_file():
        raise InstallBlocked("Present the disclaimer and the security reminder before asking for acceptance.")
    presented = json.loads(presented_path.read_text(encoding="utf-8"))
    if turn_id.strip() == str(presented.get("presented_turn", "")).strip() or not turn_id.strip():
        raise InstallBlocked("Acceptance has to be a later turn than the one that presented the documents.")
    if statement.strip() != PHRASE:
        raise InstallBlocked("The confirmation has to be the explicit acceptance sentence. A vague reply does not count.")
    write_atomic(
        home / "disclaimer-accepted.json",
        json.dumps(
            {
                "accepted": True,
                "presented_turn": presented["presented_turn"],
                "accepted_turn": turn_id.strip(),
                "phrase_sha256": phrase_digest(),
            }
        ),
    )


def assert_install_allowed(home: Path) -> None:
    path = home / "disclaimer-accepted.json"
    if not path.is_file():
        raise InstallBlocked("Installation is blocked until the disclaimer has been accepted in a separate step.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallBlocked("The acceptance stamp is unreadable.") from exc
    if payload.get("accepted") is not True or payload.get("phrase_sha256") != phrase_digest():
        raise InstallBlocked("The acceptance stamp does not match the disclaimer sentence.")
    if payload.get("accepted_turn") == payload.get("presented_turn"):
        raise InstallBlocked("Acceptance has to be a later turn than the presentation.")


def _documents() -> str:
    root = repo_root()
    disclaimer = (root / "DISCLAIMER.md").read_text(encoding="utf-8").strip()
    security = (root / "SECURITY.md").read_text(encoding="utf-8").strip()
    return f"{disclaimer}\n\n---\n\n{security}\n"
