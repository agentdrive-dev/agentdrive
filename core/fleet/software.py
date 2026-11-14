"""Official software status and release notes.

Reading uses the vehicle list first. A car that is not online is left asleep.
Notes are the title and description Tesla returns. Image URLs are not copied.
"""

from __future__ import annotations

import json

from core.fleet.report import skip_text
from core.fleet.snapshot import Observation


def notes_from(payload: dict) -> list[dict]:
    """Title, subtitle, description, and customer version. No image URLs."""
    notes = _note_list(payload)
    public = []
    for item in notes:
        if not isinstance(item, dict):
            continue
        public.append(
            {
                "title": _text(item.get("title")),
                "subtitle": _text(item.get("subtitle")),
                "description": _text(item.get("description")),
                "customer_version": _text(item.get("customer_version")),
            }
        )
    return public


def format_software(state: str, observation: Observation | None, notes: list[dict], *, skipped: bool) -> str:
    if skipped or observation is None:
        label = state or "unknown"
        return f"{skip_text(label)}\nSoftware status was not read."
    lines = [
        "state: online",
        f"Installed: {observation.car_version or '?'}",
        f"Update status: {observation.software_update_status or 'none'}",
        f"Update version: {observation.software_update_version or 'none'}",
        "Release notes:",
    ]
    if not notes:
        lines.append("No release notes were returned.")
    for note in notes:
        title = note.get("title") or "Note"
        version = note.get("customer_version")
        heading = f"{title} ({version})" if version else title
        lines.append(heading)
        if note.get("subtitle"):
            lines.append(note["subtitle"])
        if note.get("description"):
            lines.append(note["description"])
    return "\n".join(lines)


def format_software_json(state: str, observation: Observation | None, notes: list[dict], *, skipped: bool) -> str:
    payload = {
        "skipped": skipped,
        "state": state or "unknown",
        "installed": None if observation is None else observation.car_version,
        "update_status": None if observation is None else observation.software_update_status,
        "update_version": None if observation is None else observation.software_update_version,
        "release_notes": notes if not skipped else [],
        "text": format_software(state, observation, notes, skipped=skipped),
    }
    return json.dumps(payload, indent=2) + "\n"


def _note_list(payload: dict) -> list:
    body = payload.get("response", payload) if isinstance(payload, dict) else None
    if isinstance(body, dict) and isinstance(body.get("response"), dict):
        nested = body["response"]
        if "release_notes" in nested:
            body = nested
    if isinstance(body, dict) and isinstance(body.get("release_notes"), list):
        return body["release_notes"]
    if isinstance(body, list):
        return body
    return []


def _text(value) -> str:
    if value is None or isinstance(value, bool):
        return ""
    return " ".join(str(value).split())
