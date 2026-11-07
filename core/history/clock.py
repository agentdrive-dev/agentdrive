"""Local calendar for history briefings.

The profile timezone is used when `$AGENTDRIVE_HOME/profile.yaml` is present
and valid. Otherwise the computer's local timezone is used.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.profile.owner import load_profile


def resolve_timezone(home: Path):
    path = Path(home) / "profile.yaml"
    if path.is_file():
        try:
            return ZoneInfo(load_profile(path).timezone)
        except Exception:
            pass
    current = datetime.now().astimezone().tzinfo
    return current or ZoneInfo("UTC")


def local_today(tz) -> date:
    return datetime.now(tz).date()


def timezone_name(tz) -> str:
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        return key
    return "local"
