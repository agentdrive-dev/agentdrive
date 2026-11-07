"""Remember which health alerts are already active.

The file lives in the private config home, next to ``runtime.yaml``. It stores
alert codes only: no VIN, no location, and no alert text. A skipped check does
not read or write it. AgentDrive does not run the health check itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.atomic import write_atomic
from core.fleet.health import CheckReport

CHECK_ALERTS_NAME = "check-alerts.json"

# Printed on adapter install. The morning briefing is a separate daily job.
HEALTH_CHECK_NOTE = (
    "Set up a recurring health check by default. "
    "Schedule a routine that runs `agentdrive check --json` every 30 minutes. "
    "The check is read-only and does not wake a sleeping car. "
    "Exit 0 means there is nothing new to report, including when the vehicle is asleep "
    "and when the same alerts are still active. "
    "Exit 1 means a new alert the owner should hear. Notify only when the exit code is 1. "
    "The same alert stays quiet until it clears and later returns. "
)


def load_codes(home: Path) -> set[str]:
    """Return the alert codes saved after the last online check.

    A missing or unreadable file means nothing is remembered, so the next
    online check treats every current alert as new.
    """
    path = Path(home) / CHECK_ALERTS_NAME
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return set()
    codes = data.get("codes") if isinstance(data, dict) else None
    if not isinstance(codes, list):
        return set()
    return {item for item in codes if isinstance(item, str) and item}


def save_codes(home: Path, codes: list[str]) -> None:
    """Replace the remembered codes with the alerts active on this online check."""
    unique: list[str] = []
    seen: set[str] = set()
    for code in codes:
        if not isinstance(code, str) or not code or code in seen:
            continue
        seen.add(code)
        unique.append(code)
    payload = json.dumps({"codes": unique}, indent=2) + "\n"
    write_atomic(Path(home) / CHECK_ALERTS_NAME, payload)


def apply_rising_edge(home: Path, report: CheckReport) -> CheckReport:
    """Exit 1 only for alert codes that were not active on the last online check.

    A skipped report is returned unchanged. The remembered file is left as it
    was, including when that file does not exist yet.
    """
    if report.skipped:
        return report
    previous = load_codes(home)
    current = [alert.code for alert in report.alerts]
    save_codes(home, current)
    fresh = [alert for alert in report.alerts if alert.code not in previous]
    if len(fresh) == len(report.alerts):
        return report
    return CheckReport(
        ok=not fresh,
        state=report.state,
        skipped=False,
        alerts=fresh,
        tyre_bar=report.tyre_bar,
        low_battery_percent=report.low_battery_percent,
    )
