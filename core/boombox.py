"""Quiet hours for the external speaker.

The file is ``$AGENTDRIVE_HOME/boombox.yaml``. It does not talk to the car.
A missing file means 00:00-06:00 local time. ``off`` means no quiet hours.
The speaker plays one sound and stops. There is no rate limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from core.atomic import write_atomic
from core.errors import ConfigError

BOOMBOX_NAME = "boombox.yaml"
OVERRIDE_PHRASE = "despite quiet hours"
DEFAULT_START = 0
DEFAULT_END = 6 * 60
SOUNDS = {"fart": "0", "ping": "2000"}
_PHRASE = re.compile(rf"(?<![a-z0-9]){re.escape(OVERRIDE_PHRASE)}(?![a-z0-9])")


@dataclass(frozen=True)
class QuietWindow:
    start: str
    end: str
    start_minutes: int
    end_minutes: int
    disabled: bool = False

    def label(self) -> str:
        if self.disabled:
            return "off"
        return f"{self.start}-{self.end}"


def boombox_path(home: Path) -> Path:
    return Path(home) / BOOMBOX_NAME


def load_quiet(home: Path) -> QuietWindow:
    path = boombox_path(home)
    if not path.is_file():
        return _default_window()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError("boombox.yaml could not be read.") from exc
    if data is None:
        return _default_window()
    if not isinstance(data, dict):
        raise ConfigError("boombox.yaml must be a mapping.")
    if set(data) - {"version", "quiet"}:
        raise ConfigError("boombox.yaml contains a field this toolkit will not store.")
    return _parse_stored(data.get("quiet"))


def save_quiet(home: Path, window: QuietWindow) -> None:
    payload = {"version": 1, "quiet": "off" if window.disabled else window.label()}
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
    write_atomic(boombox_path(home), text)


def set_quiet(home: Path, text: str) -> QuietWindow:
    window = _parse_setting(text)
    save_quiet(home, window)
    return window


def format_quiet(window: QuietWindow) -> str:
    if window.disabled:
        return "Boombox quiet hours are off. The external speaker plays one sound and stops."
    return (
        f"Boombox quiet hours are {window.start}-{window.end} local time. "
        "The external speaker plays one sound and stops. "
        "During quiet hours it stays off unless you pass --override "
        "and confirm with despite quiet hours."
    )


def active_window(home: Path, now: datetime | None = None) -> QuietWindow | None:
    """The window when the clock is inside it. None when the speaker may play."""
    window = load_quiet(home)
    if window.disabled:
        return None
    if in_window(local_minutes(now), window):
        return window
    return None


def quiet_refusal(window: QuietWindow) -> str:
    return (
        f"Boombox is off during quiet hours ({window.start}-{window.end} local time). "
        "It was not played. The owner can override with --override and a confirmation "
        "that includes despite quiet hours."
    )


def override_phrase_missing(statement: str | None) -> bool:
    text = " ".join(str(statement or "").lower().split())
    return _PHRASE.search(text) is None


def local_minutes(now: datetime | None = None) -> int:
    """Minutes after midnight. A provided clock is used as its wall time."""
    moment = datetime.now().astimezone() if now is None else now
    return moment.hour * 60 + moment.minute


def in_window(minute: int, window: QuietWindow) -> bool:
    start = window.start_minutes
    end = window.end_minutes
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _default_window() -> QuietWindow:
    return QuietWindow(start="00:00", end="06:00", start_minutes=DEFAULT_START, end_minutes=DEFAULT_END)


def _parse_stored(value) -> QuietWindow:
    if value is None:
        return _default_window()
    return _parse_setting(str(value))


def _parse_setting(value: str) -> QuietWindow:
    text = " ".join(str(value or "").split()).lower()
    if text == "off":
        return QuietWindow(start="", end="", start_minutes=0, end_minutes=0, disabled=True)
    if "-" not in text:
        raise ConfigError("Boombox quiet hours are HH:MM-HH:MM, or off.")
    start_text, end_text = text.split("-", 1)
    start = _clock(start_text)
    end = _clock(end_text)
    if start == end:
        raise ConfigError("Boombox quiet hours need a start and a different end.")
    return QuietWindow(
        start=_format_clock(start),
        end=_format_clock(end),
        start_minutes=start,
        end_minutes=end,
    )


def _clock(value: str) -> int:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ConfigError("A quiet-hours time is HH:MM in 24-hour time.")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour > 23 or minute > 59 or len(parts[0]) > 2 or len(parts[1]) != 2:
        raise ConfigError("A quiet-hours time is HH:MM in 24-hour time.")
    return hour * 60 + minute


def _format_clock(minutes: int) -> str:
    hour, minute = divmod(int(minutes), 60)
    return f"{hour:02d}:{minute:02d}"
