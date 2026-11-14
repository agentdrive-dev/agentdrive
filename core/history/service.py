"""Service and tyre log against the last saved odometer.

Logging does not wake the car. If no odometer has been saved, the event is
not recorded. The same due reminder is not repeated for two calendar months.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from core.errors import ConfigError, HistoryError
from core.history.clock import local_today, resolve_timezone
from core.history.store import (
    history_root,
    history_session,
    insert_service_event,
    isoformat_utc,
    latest_odometer,
    list_service_events,
    load_notices,
    save_notice,
)
from core.history.text import dumps, fmt_km
from core.history.tips import on_cooldown

TYRE_SERVICE_KM = 10000.0
NO_ODOMETER = (
    "No odometer is saved yet, so this service was not logged. "
    "When the car is next online, run a check or a report, then log the service again. "
    "That does not wake the car."
)
_KINDS = {"rotation", "replacement", "maintenance"}
_VIN = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_COORD = re.compile(r"-?\d{1,3}\.\d{4,}")


def clean_note(note: str) -> str | None:
    text = " ".join(str(note or "").split())
    if not text:
        return None
    if len(text) > 160:
        raise ConfigError("The service note must be 160 characters or fewer.")
    lowered = text.lower()
    if _VIN.search(text) or _COORD.search(text) or "latitude" in lowered or "longitude" in lowered:
        raise ConfigError("The service note cannot include a VIN or coordinates.")
    return text


def clean_interval(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError("The service interval must be from 1 to 500000 km.") from exc
    if number < 1 or number > 500_000:
        raise ConfigError("The service interval must be from 1 to 500000 km.")
    return number


def perform_log(home, kind: str, *, note: str = "", interval_km: float | None = None, now: datetime | None = None, store=None) -> str:
    """Record one service event. Raises when the odometer has not been saved."""
    if kind not in _KINDS:
        raise ConfigError("Service kind must be rotation, replacement, or maintenance.")
    cleaned = clean_note(note)
    requested = clean_interval(interval_km)
    basis, stored_interval = _resolve_interval(kind, requested)
    logged_at = isoformat_utc(now or datetime.now(timezone.utc))
    root = history_root(home, store)
    with history_session(root, write=False) as existing:
        if existing is None or latest_odometer(existing) is None:
            raise HistoryError(NO_ODOMETER)
    with history_session(root, write=True) as conn:
        odometer = latest_odometer(conn)
        if odometer is None:
            raise HistoryError(NO_ODOMETER)
        insert_service_event(
            conn,
            logged_at=logged_at,
            kind=kind,
            note=cleaned,
            odometer_km=odometer,
            interval_km=stored_interval,
            basis=basis,
        )
    return _logged_text(kind, odometer, basis, stored_interval)


def render_service_status(home, *, as_json: bool, on=None, tz=None, store=None) -> str:
    zone = tz or resolve_timezone(home)
    today = on or local_today(zone)
    with history_session(history_root(home, store), write=False) as conn:
        if conn is None:
            view = service_view([], None, {}, today)
        else:
            view = service_view(list_service_events(conn), latest_odometer(conn), load_notices(conn), today)
    if as_json:
        return dumps(view)
    return format_status_text(view)


def take_service_reminders(conn, today) -> list[dict]:
    """Return due reminders that are outside the cooldown, and record that they were shown."""
    view = service_view(list_service_events(conn), latest_odometer(conn), load_notices(conn), today)
    shown: list[dict] = []
    for event in view["events"]:
        if not event["remind"]:
            continue
        save_notice(conn, event["notice_id"], today)
        shown.append({"id": event["notice_id"], "text": event["reminder_text"]})
    return shown


def service_view(events: list[dict], odometer, notices: dict, today) -> dict:
    latest_tyre = _latest_tyre(events)
    described = []
    for event in events:
        described.append(_describe(event, odometer, notices, today, latest_tyre))
    return {"odometer_km": odometer, "events": described}


def format_status_text(view: dict) -> str:
    if not view["events"] and view["odometer_km"] is None:
        return (
            "No service has been logged, and no odometer is saved yet. "
            "When the car is next online, a check or a report can save the odometer. That does not wake the car."
        )
    lines = []
    if view["odometer_km"] is None:
        lines.append("No odometer is saved yet, so distance since service cannot be calculated.")
    else:
        lines.append(f"Latest saved odometer: {fmt_km(view['odometer_km'])}.")
    if not view["events"]:
        lines.append("No service has been logged.")
    for event in view["events"]:
        lines.append(_event_line(event))
    return "\n".join(lines)


def _resolve_interval(kind: str, requested: float | None) -> tuple[str | None, float | None]:
    if kind == "rotation":
        if requested is None:
            return "rotation", TYRE_SERVICE_KM
        return "user", requested
    if kind == "replacement":
        if requested is None:
            return "rotation", TYRE_SERVICE_KM
        return "user", requested
    if requested is None:
        return None, None
    return "user", requested


def _logged_text(kind: str, odometer: float, basis: str | None, interval: float | None) -> str:
    label = {"rotation": "tyre rotation", "replacement": "tyre replacement", "maintenance": "service"}[kind]
    line = f"Logged {label} at {fmt_km(odometer)}."
    if kind == "maintenance" and interval is None:
        return line + " No reminder interval was set."
    if kind == "replacement" and basis == "rotation":
        return (
            line
            + " The next reminder is the 10000 km tyre-service interval. "
            + "Replacing tyres depends on tread, not a fixed distance."
        )
    if kind == "replacement" and basis == "user":
        return (
            line
            + f" Reminder after {fmt_km(interval)}, the interval you set. "
            + "Replacing tyres depends on tread, not a fixed distance."
        )
    if basis == "rotation":
        return line + " Tyre service reminder after 10000 km (6,250 miles)."
    return line + f" Reminder after {fmt_km(interval)}."


def _latest_tyre(events: list[dict]) -> dict | None:
    tyre = [event for event in events if event.get("kind") in {"rotation", "replacement"}]
    if not tyre:
        return None
    return max(tyre, key=lambda event: (str(event.get("logged_at") or ""), int(event.get("id") or 0)))


def _describe(event: dict, odometer, notices: dict, today, latest_tyre: dict | None) -> dict:
    kind = str(event.get("kind") or "")
    event_id = int(event["id"])
    event_odo = float(event["odometer_km"])
    since = None if odometer is None else float(odometer) - event_odo
    interval = None if event.get("interval_km") is None else float(event["interval_km"])
    basis = event.get("basis")
    active = _is_active(event, latest_tyre)
    reached = active and interval is not None and since is not None and since + 1e-6 >= interval
    notice_id = _notice_id(event) if active and interval is not None else None
    shown = notices.get(notice_id) if notice_id else None
    cooling = bool(shown and on_cooldown(shown, today))
    remind = bool(reached and notice_id and not cooling)
    text = _reminder_text(event, since) if reached and notice_id else None
    return {
        "id": event_id,
        "logged_at": event.get("logged_at"),
        "kind": kind,
        "note": event.get("note"),
        "odometer_km": event_odo,
        "since_km": since,
        "interval_km": interval,
        "basis": basis,
        "active": active,
        "interval_reached": reached,
        "notice_id": notice_id,
        "on_cooldown": cooling,
        "cooldown_until": None if shown is None else _cooldown_until(shown),
        "remind": remind,
        "reminder_text": text,
    }


def _cooldown_until(shown):
    from core.history.tips import add_calendar_months

    return add_calendar_months(shown, 2).isoformat()


def _is_active(event: dict, latest_tyre: dict | None) -> bool:
    if event.get("kind") in {"rotation", "replacement"}:
        return latest_tyre is not None and int(latest_tyre["id"]) == int(event["id"])
    return True


def _notice_id(event: dict) -> str:
    if event.get("kind") == "maintenance":
        return f"service:maintenance:{int(event['id'])}"
    if event.get("kind") == "replacement" and event.get("basis") == "user":
        return "service:tyre-replacement-due"
    return "service:tyre-rotation-due"


def _reminder_text(event: dict, since) -> str:
    logged = str(event.get("logged_at") or "")[:10]
    distance = fmt_km(since)
    if event.get("kind") == "maintenance":
        note = event.get("note")
        extra = f" Note: {note}." if note else ""
        return (
            f"{distance} since the service logged on {logged}. "
            f"The {fmt_km(event.get('interval_km'))} interval you set has been reached.{extra}"
        )
    if event.get("basis") == "user":
        if event.get("kind") == "replacement":
            when = f"the tyres were replaced on {logged}"
        else:
            when = f"the tyre service logged on {logged}"
        return (
            f"{distance} since {when}. "
            f"The {fmt_km(event.get('interval_km'))} interval you set has been reached. "
            "Replacing tyres depends on tread, not a fixed distance."
        )
    return (
        f"{distance} since the tyre service logged on {logged}. "
        "Tyre service is due. Tesla suggests a rotation or service check about every 10,000 km (6,250 miles). "
        "Replacing tyres depends on tread, not a fixed distance."
    )


def _event_line(event: dict) -> str:
    kind = {"rotation": "Tyre rotation", "replacement": "Tyre replacement", "maintenance": "Service"}.get(
        event["kind"], "Service"
    )
    logged = str(event.get("logged_at") or "")[:10]
    since = "distance not available" if event["since_km"] is None else f"{fmt_km(event['since_km'])} since then"
    note = f" Note: {event['note']}." if event.get("note") else ""
    if not event["active"]:
        return f"{kind} logged {logged} at {fmt_km(event['odometer_km'])}. {since}. Superseded by a later tyre service.{note}"
    if event["interval_km"] is None:
        return f"{kind} logged {logged} at {fmt_km(event['odometer_km'])}. {since}. No reminder interval was set.{note}"
    if not event["interval_reached"]:
        return (
            f"{kind} logged {logged} at {fmt_km(event['odometer_km'])}. {since}. "
            f"Reminder at {fmt_km(event['interval_km'])}. Not due.{note}"
        )
    if event["on_cooldown"]:
        return (
            f"{kind} logged {logged} at {fmt_km(event['odometer_km'])}. {since}. "
            f"The {fmt_km(event['interval_km'])} interval has been reached. "
            f"The morning reminder will not repeat until {event['cooldown_until']}.{note}"
        )
    return (
        f"{kind} logged {logged} at {fmt_km(event['odometer_km'])}. {since}. "
        f"The {fmt_km(event['interval_km'])} interval has been reached.{note}"
    )
