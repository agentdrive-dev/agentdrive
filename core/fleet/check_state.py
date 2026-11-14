"""Remember which health alerts are already active.

The file lives next to that car's history. With one car it stays
``$AGENTDRIVE_HOME/check-alerts.json``. Another car uses
``$AGENTDRIVE_HOME/cars/<id>/check-alerts.json``. It stores alert codes
and, for energy-mode alerts, the last time that mode was announced.
No VIN, no location, and no alert text. A skipped check does not
read or write it. AgentDrive does not run the health check itself.

Energy-mode alerts (climate on, climate keeper keep/dog/camp, bioweapon
defense) are announced at most once an hour while the mode stays on.
Every other alert stays quiet until it clears.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.atomic import write_atomic
from core.fleet.health import CheckReport

CHECK_ALERTS_NAME = "check-alerts.json"
ENERGY_INTERVAL_SECONDS = 3600

# Printed on adapter install. The morning briefing is a separate daily job.
HEALTH_CHECK_NOTE = (
    "Set up a recurring health check by default. "
    "Schedule a routine that runs `agentdrive check --json` every 30 minutes. "
    "The check is read-only and does not wake a sleeping car. "
    "Exit 0 means there is nothing new to report, including when the vehicle is asleep "
    "and when the same alerts are still active. "
    "Exit 1 means a new alert the owner should hear. Notify only when the exit code is 1. "
    "The same alert stays quiet until it clears and later returns. "
    "An energy-mode alert is the exception: climate left on, climate keeper keep, dog, or camp, "
    "or bioweapon defense mode, while parked with no one in the driver seat, repeats at most once "
    "an hour while that mode stays on and stops when the mode turns off. "
    "If someone is in the driver seat, that alert is not sent, and the parked-and-unlocked alert "
    "is not sent. The driver-seat reading covers only the driver seat and is not a guarantee that "
    "nobody is inside. "
)


def load_codes(home: Path) -> set[str]:
    """Return the alert codes saved after the last online check.

    A missing or unreadable file means nothing is remembered, so the next
    online check treats every current alert as new.
    """
    return set(_load_state(home)["codes"])


def save_codes(home: Path, codes: list[str]) -> None:
    """Replace the remembered codes with the alerts active on this online check."""
    _save_state(home, _unique(codes), {})


def apply_rising_edge(home: Path, report: CheckReport, now: datetime | None = None) -> CheckReport:
    """Exit 1 only for alerts the owner has not already been told about.

    A normal alert stays quiet until it clears. An energy-mode alert is
    repeated at most once an hour while that mode stays on, including when
    the driver seat was occupied in between, and the timer clears when the
    mode turns off. A skipped report is returned unchanged.
    """
    if report.skipped:
        return report
    moment = _clock(now)
    state = _load_state(home)
    previous = set(state["codes"])
    notified = {
        code: stamp
        for code, stamp in state["energy_notified"].items()
        if code in set(report.energy_modes)
    }
    fresh = []
    for alert in report.alerts:
        if alert.code.startswith("energy:"):
            last = notified.get(alert.code)
            if last is None or (moment - last).total_seconds() >= ENERGY_INTERVAL_SECONDS:
                fresh.append(alert)
                notified[alert.code] = moment
            continue
        if alert.code not in previous:
            fresh.append(alert)
    current = [alert.code for alert in report.alerts]
    _save_state(home, current, notified)
    if len(fresh) == len(report.alerts):
        return report
    return CheckReport(
        ok=not fresh,
        state=report.state,
        skipped=False,
        alerts=fresh,
        tyre_bar=report.tyre_bar,
        low_battery_percent=report.low_battery_percent,
        driver_present=report.driver_present,
        energy_modes=list(report.energy_modes),
    )


def _load_state(home: Path) -> dict:
    path = Path(home) / CHECK_ALERTS_NAME
    empty = {"codes": [], "energy_notified": {}}
    if not path.is_file():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    codes = data.get("codes")
    raw_times = data.get("energy_notified")
    parsed: dict[str, datetime] = {}
    if isinstance(raw_times, dict):
        for code, stamp in raw_times.items():
            moment = _parse_time(stamp)
            if isinstance(code, str) and code.startswith("energy:") and moment is not None:
                parsed[code] = moment
    return {"codes": _unique(codes if isinstance(codes, list) else []), "energy_notified": parsed}


def _save_state(home: Path, codes: list[str], notified: dict[str, datetime]) -> None:
    unique = _unique(codes)
    payload: dict = {"codes": unique}
    if notified:
        payload["energy_notified"] = {code: _iso(notified[code]) for code in sorted(notified)}
    text = json.dumps(payload, indent=2) + "\n"
    write_atomic(Path(home) / CHECK_ALERTS_NAME, text)


def _unique(codes) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for code in codes:
        if not isinstance(code, str) or not code or code in seen:
            continue
        seen.add(code)
        unique.append(code)
    return unique


def _clock(now: datetime | None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return _clock(moment).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
