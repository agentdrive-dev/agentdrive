"""Read local history and build the morning usage briefing.

These reads do not contact the car. Yearly wins over monthly, monthly over
weekly, and weekly over daily. A tip is added only when one is eligible.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from core.errors import ConfigError
from core.history.clock import local_today, resolve_timezone, timezone_name
from core.history.service import take_service_reminders
from core.history.store import database_exists, history_session, isoformat_utc, list_named, range_summary
from core.history.text import dumps, fmt_km, fmt_kwh, fmt_pct, yes_no
from core.history.tips import choose_tip, is_tip_day

_LISTS = ("trips", "charges", "snapshots", "alerts", "config")
_EMPTY = {
    "trips": "No trips saved yet.",
    "charges": "No charges saved yet.",
    "snapshots": "No snapshots saved yet.",
    "alerts": "No alerts saved yet.",
    "config": "No config changes saved yet.",
}
_TITLES = {
    "trips": "Trips",
    "charges": "Charges",
    "snapshots": "Snapshots",
    "alerts": "Alerts",
    "config": "Config changes",
}
_ROW_CAP = 100


def due_period(day: date) -> str:
    """Which morning report covers this local date. Yearly beats the others."""
    if day.month == 1 and day.day == 1:
        return "yearly"
    if day.day == 1:
        return "monthly"
    if day.weekday() == 0:
        return "weekly"
    return "daily"


def covered(period: str, day: date) -> tuple[date, date]:
    """Return the half-open local date window the report on `day` covers."""
    if period == "daily":
        return day - timedelta(days=1), day
    if period == "weekly":
        return day - timedelta(days=7), day
    if period == "monthly":
        first = day.replace(day=1)
        previous = first - timedelta(days=1)
        return previous.replace(day=1), first
    if period == "yearly":
        return date(day.year - 1, 1, 1), date(day.year, 1, 1)
    raise ConfigError("Unknown history period.")


def render_list(home, kind: str, *, limit: int, as_json: bool) -> str:
    if kind not in _LISTS:
        raise ConfigError("Unknown history list.")
    with history_session(home, write=False) as conn:
        if conn is None:
            rows, total = [], 0
        else:
            rows, total = list_named(conn, kind, limit)
    if as_json:
        return dumps({kind: rows, "returned": len(rows), "total": total})
    if total == 0:
        return _EMPTY[kind]
    lines = [f"{_TITLES[kind]} saved on this computer: {total}"]
    if len(rows) < total:
        lines.append(f"Showing {len(rows)} of {total}.")
    lines.extend(_list_line(kind, row) for row in rows)
    return "\n".join(lines)


def render_tips(home, *, as_json: bool, on: date | None = None, tz=None) -> str:
    zone = tz or resolve_timezone(home)
    today = on or local_today(zone)
    if not is_tip_day(today):
        tip, reason = None, "not_today"
    else:
        with history_session(home, write=True) as conn:
            tip, reason = choose_tip(conn, today)
    if as_json:
        payload = {"due": tip is not None, "reason": reason, "tip": None if tip is None else tip["text"]}
        if tip is not None:
            payload["id"] = tip["id"]
        return dumps(payload)
    if tip is None:
        return "No tip this morning."
    return f"Tip: {tip['text']}"


def render_briefing(home, period: str, *, as_json: bool, on: date | None = None, tz=None) -> str:
    zone = tz or resolve_timezone(home)
    today = on or local_today(zone)
    selected = due_period(today) if period == "due" else period
    if selected not in {"daily", "weekly", "monthly", "yearly"}:
        raise ConfigError("Unknown history period.")
    start, end = covered(selected, today)
    start_iso, end_iso = _bounds(start, end, zone)
    if not database_exists(home) and not is_tip_day(today):
        summary = {"trips": [], "charges": [], "snapshots": 0, "alerts": []}
        tip = None
        reminders: list[dict] = []
    else:
        with history_session(home, write=True) as conn:
            summary = range_summary(conn, start_iso, end_iso)
            tip, _reason = choose_tip(conn, today)
            reminders = take_service_reminders(conn, today)
    payload = _payload(
        selected=selected,
        explicit=period,
        start=start,
        end=end,
        zone_name=timezone_name(zone),
        summary=summary,
        tip=tip,
        reminders=reminders,
    )
    if as_json:
        return dumps(payload)
    return payload["text"]


def _payload(*, selected, explicit, start, end, zone_name, summary, tip, reminders) -> dict:
    inclusive_end = end - timedelta(days=1)
    trips = summary["trips"]
    charges = summary["charges"]
    alerts = summary["alerts"]
    distance, distance_complete = _distance(trips)
    energy = _sum(charges, "energy_added_kwh")
    readings = sum(len(item["readings"]) for item in charges)
    trip_rows, trips_truncated = _cap(trips)
    charge_rows, charges_truncated = _cap(charges)
    alert_rows, alerts_truncated = _cap(alerts)
    empty = not trips and not charges and summary["snapshots"] == 0 and not alerts
    text = _text(
        selected=selected,
        explicit=explicit,
        start=start,
        inclusive_end=inclusive_end,
        trips=trips,
        distance=distance,
        distance_complete=distance_complete,
        charges=charges,
        energy=energy,
        readings=readings,
        snapshots=summary["snapshots"],
        alerts=alerts,
        empty=empty,
        tip=tip,
        reminders=reminders,
    )
    return {
        "selected": selected,
        "period": selected,
        "covered_start": start.isoformat(),
        "covered_end": inclusive_end.isoformat(),
        "timezone": zone_name,
        "empty": empty,
        "text": text,
        "trips": {
            "count": len(trips),
            "distance_km": distance,
            "energy_used_kwh": _sum(trips, "energy_used_kwh"),
            "rows": trip_rows,
            "rows_truncated": trips_truncated,
        },
        "charges": {
            "count": len(charges),
            "energy_added_kwh": energy,
            "readings": readings,
            "rows": charge_rows,
            "rows_truncated": charges_truncated,
        },
        "snapshots": {"count": summary["snapshots"]},
        "alerts": alert_rows,
        "alerts_truncated": alerts_truncated,
        "tip": None if tip is None else {"id": tip["id"], "text": tip["text"]},
        "service_reminders": reminders,
    }


def _text(
    *,
    selected,
    explicit,
    start,
    inclusive_end,
    trips,
    distance,
    distance_complete,
    charges,
    energy,
    readings,
    snapshots,
    alerts,
    empty,
    tip,
    reminders,
) -> str:
    lines = []
    if explicit == "due":
        lines.append(f"This morning's report is the {selected} vehicle history.")
    lines.append(_heading(selected, start, inclusive_end))
    lines.append("Saved on this computer. This does not contact the car.")
    if empty:
        lines.append("No trips, charges, snapshots, or alerts were saved for this period.")
    else:
        lines.append(_trip_summary(trips, distance, distance_complete))
        lines.append(_charge_summary(charges, energy, readings))
        lines.append(f"Snapshots: {snapshots}")
        if alerts:
            lines.append(f"Alerts: {len(alerts)}")
            for alert in alerts[:8]:
                lines.append(f"- {alert['observed_at']} {alert['code']}: {alert['message']}")
            if len(alerts) > 8:
                lines.append(f"- and {len(alerts) - 8} more")
        else:
            lines.append("Alerts: none")
    if reminders:
        lines.append("Service reminders:")
        for item in reminders:
            lines.append(f"- {item['text']}")
    if tip is not None:
        lines.append(f"Tip: {tip['text']}")
    return "\n".join(lines)


def _heading(period: str, start: date, inclusive_end: date) -> str:
    if period == "daily":
        return f"Daily vehicle history for {start.isoformat()}"
    if period == "weekly":
        return f"Weekly vehicle history for {start.isoformat()} to {inclusive_end.isoformat()}"
    if period == "monthly":
        return f"Monthly vehicle history for {start.strftime('%Y-%m')}"
    return f"Yearly vehicle history for {start.year}"


def _trip_summary(trips, distance, distance_complete) -> str:
    energy = _sum(trips, "energy_used_kwh")
    if not trips:
        return "Trips: none"
    if distance is None:
        distance_text = "Distance was not in the saved readings."
    elif distance_complete:
        distance_text = f"{fmt_km(distance)}."
    else:
        distance_text = f"{fmt_km(distance)} where distance was recorded."
    if energy is None:
        energy_text = "Energy used was not in the saved readings."
    else:
        energy_text = f"Energy used {fmt_kwh(energy)}."
    return f"Trips: {len(trips)}, {distance_text} {energy_text}"


def _charge_summary(charges, energy, readings) -> str:
    if not charges:
        return "Charges: none"
    if energy is None:
        energy_text = "energy added was not in the saved readings"
    else:
        energy_text = f"energy added {fmt_kwh(energy)}"
    return f"Charges: {len(charges)}, {energy_text}, {readings} charge readings."


def _list_line(kind: str, row: dict) -> str:
    if kind == "trips":
        end = row["ended_at"] or "still in progress"
        distance = fmt_km(row["distance_km"]) if row["distance_km"] is not None else "distance not recorded"
        energy = f" | energy {fmt_kwh(row['energy_used_kwh'])}" if row["energy_used_kwh"] is not None else ""
        return (
            f"{row['started_at']} to {end} | {distance} | "
            f"battery {fmt_pct(row['start_battery_percent'])} to {fmt_pct(row['end_battery_percent'])} | "
            f"gear {row['start_shift'] or '?'} to {row['end_shift'] or '?'} | "
            f"locked {yes_no(row['start_locked'])} to {yes_no(row['end_locked'])} | "
            f"sentry {yes_no(row['start_sentry'])} to {yes_no(row['end_sentry'])}{energy}"
        )
    if kind == "charges":
        end = row["ended_at"] or "still plugged in"
        energy = fmt_kwh(row["energy_added_kwh"]) if row["energy_added_kwh"] is not None else "energy not recorded"
        return (
            f"{row['started_at']} to {end} | battery {fmt_pct(row['start_percent'])} to {fmt_pct(row['end_percent'])} | "
            f"{energy} | {len(row['readings'])} charge readings"
        )
    if kind == "snapshots":
        tyres = row["tyres_bar"]
        span = row["first_seen"] if row["first_seen"] == row["last_seen"] else f"{row['first_seen']} to {row['last_seen']}"
        return (
            f"{span} | {row['sample_count']} readings | battery {fmt_pct(row['battery_percent'])} | "
            f"{row['charging_state'] or '?'} | locked {yes_no(row['locked'])} | gear {row['shift_state'] or '?'} | "
            f"{row['closures']} | sentry {yes_no(row['sentry'])} | climate {yes_no(row['climate_on'])} | "
            f"tyres FL {tyres['fl']} FR {tyres['fr']} RL {tyres['rl']} RR {tyres['rr']} | TPMS {row['tpms']}"
        )
    if kind == "alerts":
        return f"{row['observed_at']} | {row['code']} | {row['message']}"
    config = row.get("vehicle_config") or {}
    version = row.get("software_version") or "?"
    units = row.get("distance_units") or "?"
    return f"{row['observed_at']} | software {version} | distance {units} | config fields {len(config)}"


def _bounds(start: date, end: date, tz) -> tuple[str, str]:
    start_dt = datetime(start.year, start.month, start.day, tzinfo=tz)
    end_dt = datetime(end.year, end.month, end.day, tzinfo=tz)
    return isoformat_utc(start_dt), isoformat_utc(end_dt)


def _distance(trips: list[dict]) -> tuple[float | None, bool]:
    if not trips:
        return 0.0, True
    known = [float(item["distance_km"]) for item in trips if item.get("distance_km") is not None]
    if not known:
        return None, False
    return float(sum(known)), len(known) == len(trips)


def _sum(rows: list[dict], key: str) -> float | None:
    values = [float(item[key]) for item in rows if item.get(key) is not None]
    if not values:
        return None
    return float(sum(values))


def _cap(rows: list[dict]) -> tuple[list[dict], bool]:
    if len(rows) <= _ROW_CAP:
        return rows, False
    return rows[:_ROW_CAP], True
