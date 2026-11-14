"""Period totals for weekly, monthly, and yearly briefings.

Energy uses the counted figure from each charge. A saved value that cannot
match the battery change is left in the database and left out of the total.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from core.history.energy import annotate_charge
from core.history.text import fmt_km, fmt_kwh

_ISO = "%Y-%m-%dT%H:%M:%SZ"
_LONG = {"weekly", "monthly", "yearly"}


def is_long_period(period: str) -> bool:
    return period in _LONG


def previous_window(period: str, start: date, end: date) -> tuple[date, date]:
    if period == "monthly":
        previous_end = start
        previous_start = (start - timedelta(days=1)).replace(day=1)
        return previous_start, previous_end
    if period == "yearly":
        return date(start.year - 1, 1, 1), start
    length = end - start
    return start - length, start


def prepare_summary(summary: dict) -> dict:
    """Return the same shape with each charge annotated. Rows are copies."""
    charges = [annotate_charge(item) for item in summary.get("charges") or []]
    return {
        "trips": list(summary.get("trips") or []),
        "charges": charges,
        "snapshots": summary.get("snapshots") or 0,
        "snapshot_rows": list(summary.get("snapshot_rows") or []),
        "alerts": list(summary.get("alerts") or []),
    }


def period_stats(summary: dict, start: date, end: date, tz) -> dict:
    trips = summary["trips"]
    charges = summary["charges"]
    distance, distance_complete = _distance(trips)
    counted = [item.get("energy_counted_kwh") for item in charges]
    energies = [float(item) for item in counted if item is not None]
    ignored = sum(1 for item in charges if item.get("energy_ignored"))
    replaced = sum(1 for item in charges if item.get("energy_ignored") and item.get("energy_counted_kwh") is not None)
    omitted = ignored - replaced
    days = max((end - start).days, 1)
    energy = float(sum(energies)) if energies else None
    average_charge = (energy / len(energies)) if energies else None
    average_day = (distance / days) if distance is not None else None
    covered = _covered_days(summary, start, end, tz)
    return {
        "distance_km": distance,
        "distance_complete": distance_complete,
        "trip_count": len(trips),
        "average_km_per_day": average_day,
        "charge_count": len(charges),
        "energy_added_kwh": energy,
        "average_kwh_per_charge": average_charge,
        "charges_with_energy": len(energies),
        "energy_replaced": replaced,
        "energy_omitted": omitted,
        "days": days,
        "days_with_readings": covered,
        "coverage_incomplete": covered < days,
    }


def comparison_text(period: str, current: dict, previous: dict, prev_start: date, prev_end: date) -> str:
    label = {"weekly": "week", "monthly": "month", "yearly": "year"}.get(period, "period")
    inclusive = prev_end - timedelta(days=1)
    window = f"{prev_start.isoformat()} to {inclusive.isoformat()}"
    return (
        f"Compared with the previous {label} ({window}): "
        f"{_km(current)} versus {_km(previous)}, "
        f"{current['trip_count']} trips versus {previous['trip_count']}, "
        f"{_kwh(current)} versus {_kwh(previous)}."
    )


def coverage_text(stats: dict) -> str:
    seen = stats["days_with_readings"]
    days = stats["days"]
    if stats["coverage_incomplete"]:
        return (
            f"Coverage is incomplete: saved readings on {seen} of {days} days. "
            "History is opportunistic, not a continuous record."
        )
    return (
        f"Saved readings fall on each day of this period. "
        "History is opportunistic, not a continuous record."
    )


def energy_note(stats: dict) -> str | None:
    replaced = stats["energy_replaced"]
    omitted = stats["energy_omitted"]
    if not replaced and not omitted:
        return None
    parts = []
    if omitted:
        noun = "charge" if omitted == 1 else "charges"
        parts.append(f"{omitted} {noun} left out")
    if replaced:
        noun = "charge" if replaced == 1 else "charges"
        parts.append(f"{replaced} {noun} counted from the increase during the plug-in")
    detail = " and ".join(parts)
    return (
        f"Saved energy that does not match the battery change: {detail}. "
        "Those saved rows were not changed."
    )


def summary_lines(period: str, stats: dict, previous: dict, prev_start: date, prev_end: date) -> list[str]:
    distance = "distance was not recorded" if stats["distance_km"] is None else fmt_km(stats["distance_km"])
    average = "not available" if stats["average_km_per_day"] is None else f"{fmt_km(stats['average_km_per_day'])} per day"
    if stats["energy_added_kwh"] is None:
        energy = "energy added was not available"
        per_charge = "average per charge was not available"
    else:
        energy = f"energy added {fmt_kwh(stats['energy_added_kwh'])}"
        per_charge = f"average {fmt_kwh(stats['average_kwh_per_charge'])} per charge"
    lines = [
        (
            f"Summary: {distance} driven, {stats['trip_count']} trips, {average}. "
            f"Charges: {stats['charge_count']}, {energy}, {per_charge}."
        ),
        comparison_text(period, stats, previous, prev_start, prev_end),
        coverage_text(stats),
    ]
    note = energy_note(stats)
    if note:
        lines.append(note)
    return lines


def km_series(trips: list[dict], start: date, end: date, tz, *, monthly: bool) -> tuple[list[str], list[float]]:
    if monthly:
        totals: dict[tuple[int, int], float] = {}
        cursor = date(start.year, start.month, 1)
        last = end - timedelta(days=1)
        while cursor <= last:
            totals[(cursor.year, cursor.month)] = 0.0
            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)
        for trip in trips:
            moment = _local_day(trip.get("started_at"), tz)
            if moment is None or not start <= moment < end or trip.get("distance_km") is None:
                continue
            key = (moment.year, moment.month)
            if key in totals:
                totals[key] += float(trip["distance_km"])
        labels = [f"{month:02d}" for (_year, month) in totals]
        return labels, [round(totals[key], 3) for key in totals]
    totals_by_day: dict[date, float] = {}
    cursor = start
    while cursor < end:
        totals_by_day[cursor] = 0.0
        cursor += timedelta(days=1)
    for trip in trips:
        moment = _local_day(trip.get("started_at"), tz)
        if moment in totals_by_day and trip.get("distance_km") is not None:
            totals_by_day[moment] += float(trip["distance_km"])
    labels = [day.strftime("%d") for day in totals_by_day]
    return labels, [round(totals_by_day[day], 3) for day in totals_by_day]


def charge_series(charges: list[dict], tz, *, limit: int = 80) -> tuple[list[str], list[float]]:
    rows = []
    for charge in charges:
        counted = charge.get("energy_counted_kwh")
        if counted is None:
            continue
        moment = _local_day(charge.get("started_at"), tz)
        label = moment.strftime("%d") if moment else ""
        rows.append((charge.get("started_at") or "", label, float(counted)))
    rows.sort()
    if len(rows) > limit:
        rows = rows[-limit:]
    return [item[1] for item in rows], [round(item[2], 3) for item in rows]


def _distance(trips: list[dict]) -> tuple[float | None, bool]:
    if not trips:
        return 0.0, True
    known = [float(item["distance_km"]) for item in trips if item.get("distance_km") is not None]
    if not known:
        return None, False
    return float(sum(known)), len(known) == len(trips)


def _covered_days(summary: dict, start: date, end: date, tz) -> int:
    found: set[date] = set()
    for row in summary.get("snapshot_rows") or []:
        first = _local_day(row.get("observed_at"), tz)
        last = _local_day(row.get("last_seen"), tz)
        if first is None or last is None:
            continue
        if last < first:
            first, last = last, first
        day = max(first, start)
        stop = min(last, end - timedelta(days=1))
        while day <= stop:
            found.add(day)
            day += timedelta(days=1)
    for trip in summary["trips"]:
        moment = _local_day(trip.get("started_at"), tz)
        if moment is not None and start <= moment < end:
            found.add(moment)
    for charge in summary["charges"]:
        moment = _local_day(charge.get("started_at"), tz)
        if moment is not None and start <= moment < end:
            found.add(moment)
    return len(found)


def _local_day(value, tz) -> date | None:
    if not value:
        return None
    try:
        moment = datetime.strptime(str(value), _ISO).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return moment.astimezone(tz).date()


def _km(stats: dict) -> str:
    if stats["distance_km"] is None:
        return "distance not recorded"
    return fmt_km(stats["distance_km"])


def _kwh(stats: dict) -> str:
    if stats["energy_added_kwh"] is None:
        return "energy not recorded"
    return fmt_kwh(stats["energy_added_kwh"])
