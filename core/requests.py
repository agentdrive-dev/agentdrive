"""Pending family requests stored in the private config home.

A request records who asked, which car, the action, and its parameters.
It expires after 10 minutes. Approve runs it once. Deny, expiry, and a
second approve never run it. This module does not send a message.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.atomic import write_atomic
from core.errors import ConfigError

REQUESTS_NAME = "requests.json"
REQUEST_MINUTES = 10

_OPEN = "pending"
_APPROVED = "approved"
_DENIED = "denied"
_EXPIRED = "expired"


def create_request(
    home: Path,
    *,
    member: str,
    car: str,
    car_id: str,
    label: str,
    command: str,
    argument: str | None,
    extra: list[str] | None,
    detail: dict | None,
    now: datetime | None = None,
) -> str:
    """Save one pending request and return the text an assistant should relay."""
    moment = _clock(now)
    record = {
        "id": _new_id(home),
        "member": member,
        "car": car,
        "car_id": car_id,
        "label": label,
        "command": command,
        "argument": argument,
        "extra": list(extra) if extra else None,
        "detail": detail,
        "summary": summary_for(command, argument, extra, detail),
        "created": _iso(moment),
        "expires": _iso(moment + timedelta(minutes=REQUEST_MINUTES)),
        "status": _OPEN,
    }
    _reject_vin(record)
    records = _load(home)
    records.append(record)
    _save(home, records)
    return relay_text(record)


def list_requests(home: Path, *, now: datetime | None = None) -> str:
    records = _refresh(home, _clock(now))
    if not records:
        return "No requests."
    lines = ["Requests saved on this computer."]
    for record in records:
        car = record.get("car") or "-"
        lines.append(
            f"{record['id']}  {record['status']}  {record['member']}  {car}  {record['summary']}  "
            f"created {record['created']}  expires {record['expires']}"
        )
    return "\n".join(lines)


def approve_request(home: Path, request_id: str, *, actor: str, execute, now: datetime | None = None, ready=None) -> str:
    """Mark the request used, then run it once. A failure still spends it."""
    record = _take(home, request_id, actor=actor, now=now, verb="approve")
    if ready is not None:
        ready(record)
    _set_status(home, record["id"], _APPROVED)
    record["status"] = _APPROVED
    try:
        result = execute(record)
    except Exception:
        raise
    text = str(result or "").rstrip()
    if text:
        return f"Approved {record['id']}.\n{text}"
    return f"Approved {record['id']}."


def deny_request(home: Path, request_id: str, *, actor: str, now: datetime | None = None) -> str:
    record = _take(home, request_id, actor=actor, now=now, verb="deny")
    _set_status(home, record["id"], _DENIED)
    return f"Denied {record['id']}. It will not run."


def relay_text(record: dict) -> str:
    car_bit = f" on {record['car']}" if record.get("car") else ""
    summary = record.get("summary") or record.get("command") or "the action"
    return (
        f"Pending request {record['id']} from {record['member']}{car_bit}: {summary}.\n"
        f"Created {record['created']}. Expires {record['expires']} ({REQUEST_MINUTES} minutes).\n"
        "This action was not run.\n"
        f"Tell the owner: {record['member']} asked to {summary}{car_bit}. "
        f"Approve with `agentdrive request approve {record['id']}` "
        f"or deny with `agentdrive request deny {record['id']}`."
    )


def summary_for(command: str, argument: str | None, extra: list[str] | None, detail: dict | None) -> str:
    if command == "charge-schedule-set" and isinstance(detail, dict):
        return f"charge-schedule set {detail.get('days', '')} {detail.get('time', '')}".strip()
    if command == "charge-schedule-remove" and isinstance(detail, dict):
        return f"charge-schedule remove {detail.get('schedule_id', '')}".strip()
    if command == "departure-set" and isinstance(detail, dict):
        return f"departure set {detail.get('days', '')} {detail.get('time', '')}".strip()
    if command == "departure-remove" and isinstance(detail, dict):
        return f"departure remove {detail.get('schedule_id', '')}".strip()
    if command == "boombox-fart":
        return "boombox fart"
    if command == "boombox-ping":
        return "boombox ping"
    if extra:
        return command + " " + " ".join(str(item) for item in extra)
    if argument:
        return f"{command} {argument}"
    return command


def _take(home: Path, request_id: str, *, actor: str, now: datetime, verb: str) -> dict:
    moment = _clock(now)
    records = _refresh(home, moment)
    record = _find(records, request_id)
    _reject_actor(actor, record, verb)
    status = record["status"]
    if status == _EXPIRED:
        raise ConfigError("That request has expired and will not run.")
    if status == _DENIED:
        raise ConfigError("That request was denied and will not run.")
    if status == _APPROVED:
        raise ConfigError("That request was already used and will not run.")
    if status != _OPEN:
        raise ConfigError("That request cannot be run.")
    return record


def _reject_actor(actor: str, record: dict, verb: str) -> None:
    handle = str(actor or "").strip().lower()
    if not handle:
        return
    if handle == str(record.get("member") or ""):
        if verb == "deny":
            raise ConfigError("A family member cannot deny their own request.")
        raise ConfigError("A family member cannot approve their own request.")
    raise ConfigError("Only the owner can approve a request." if verb == "approve" else "Only the owner can deny a request.")


def _refresh(home: Path, moment: datetime) -> list[dict]:
    records = _load(home)
    changed = False
    for record in records:
        if record.get("status") == _OPEN and _expired(record, moment):
            record["status"] = _EXPIRED
            changed = True
    if changed:
        _save(home, records)
    return records


def _expired(record: dict, moment: datetime) -> bool:
    try:
        expires = datetime.strptime(str(record.get("expires") or ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return moment >= expires


def _find(records: list[dict], request_id: str) -> dict:
    key = str(request_id or "").strip().lower()
    if not key:
        raise ConfigError("Name the request id.")
    for record in records:
        if str(record.get("id") or "") == key:
            return record
    raise ConfigError("No request has that id.")


def _set_status(home: Path, request_id: str, status: str) -> None:
    records = _load(home)
    for record in records:
        if record.get("id") == request_id:
            record["status"] = status
            break
    _save(home, records)


def _load(home: Path) -> list[dict]:
    path = Path(home) / REQUESTS_NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("requests.json could not be read.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("requests"), list):
        raise ConfigError("requests.json must contain a requests list.")
    records = []
    for item in data["requests"]:
        if isinstance(item, dict):
            records.append(item)
    return records


def _save(home: Path, records: list[dict]) -> None:
    for record in records:
        _reject_vin(record)
    payload = json.dumps({"requests": records}, indent=2) + "\n"
    write_atomic(Path(home) / REQUESTS_NAME, payload)


def _new_id(home: Path) -> str:
    used = {str(item.get("id") or "") for item in _load(home)}
    for _ in range(8):
        candidate = secrets.token_hex(4)
        if candidate not in used:
            return candidate
    raise ConfigError("A request id could not be chosen.")


def _reject_vin(record: dict) -> None:
    """Requests keep a car id, never a VIN."""
    if "vin" in record:
        raise ConfigError("A request cannot store a VIN.")


def _clock(now: datetime | None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
