"""Occasional usage tips.

A tip is shown on some mornings only: Monday, Wednesday, Friday, and the 1st
of a month. At most one tip is shown. After a tip id is shown, it is not
shown again for two calendar months. If every tip is still in that window,
the tip is omitted.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

_SERVICE_PREFIX = "service:"


@dataclass(frozen=True)
class Tip:
    id: str
    text: str


# Stable ids. The sentences are the user-facing text.
CATALOG: tuple[Tip, ...] = (
    Tip(
        "precondition-plugged-in",
        "Precondition the cabin, and the battery in cold weather, while the car is still plugged in. "
        "That uses wall power instead of driving range.",
    ),
    Tip(
        "daily-charge-limit",
        "For daily driving, a charge limit around 80% is easier on nickel-based packs. "
        "LFP packs can regularly charge to 100%, and that helps the gauge stay accurate.",
    ),
    Tip(
        "avoid-sitting-at-100",
        "Try not to leave a nickel-based pack sitting at 100% for a long time. Charge full when you need the range, then drive.",
    ),
    Tip(
        "avoid-low-soc",
        "Avoid leaving the car at a very low state of charge for days. A buffer is kinder to the high-voltage pack and the low-voltage system.",
    ),
    Tip(
        "sentry-drain",
        "Sentry mode uses energy. If the car is somewhere you trust, turning it off can save several percent overnight.",
    ),
    Tip(
        "tire-rotation",
        "Tesla suggests a tyre rotation or service check about every 10,000 km (6,250 miles). "
        "Even wear helps range and handling. Replacing tyres depends on tread, not a fixed distance.",
    ),
    Tip(
        "seasonal-tire-pressure",
        "Tyre pressure changes with outdoor temperature. When the weather shifts, glance at the pressures so a slow leak is not mistaken for the weather.",
    ),
    Tip(
        "software-update-plugged",
        "Install software updates when the car is parked and, if you can, plugged in. Updates can take a while and use energy.",
    ),
    Tip(
        "climate-modes-drain",
        "Cabin Overheat Protection, Dog mode, and Camp mode use a noticeable amount of energy. Turn them off when you do not need them.",
    ),
    Tip(
        "scheduled-charge-departure",
        "Scheduled charging or scheduled departure can finish charging near the time you leave, so the battery does not sit full all night.",
    ),
    Tip(
        "off-peak-charging",
        "If your electricity is cheaper overnight, an in-car charge schedule can shift most charging into those hours.",
    ),
    Tip(
        "navigate-to-supercharger",
        "On a trip, navigating to a Supercharger lets the car precondition the battery so the session can start faster.",
    ),
    Tip(
        "home-charge-over-supercharge",
        "Use home or destination charging for everyday top-ups when you can. Superchargers are most useful on trips.",
    ),
    Tip(
        "parked-in-heat",
        "In hot weather, parking in the shade and preconditioning before you get in is easier on you and the cabin.",
    ),
    Tip(
        "winter-plug-in",
        "In cold weather, leaving the car plugged in overnight helps preconditioning and the next morning's charge.",
    ),
    Tip(
        "long-sit-plug-in",
        "If the car will sit unused for a while, leave it at a moderate charge and plugged in if you can.",
    ),
    Tip(
        "check-does-not-wake",
        "The health check and the morning history do not wake a sleeping car. That saves energy compared with opening the car app just to look.",
    ),
    Tip(
        "chat-plain-words",
        "You do not need to memorize commands. Say what you want in plain words. The AI agent turns that into AgentDrive.",
    ),
    Tip(
        "morning-report-time",
        "The morning briefing defaults to 07:30 local time. You can ask the AI agent to change that time or turn the briefing off.",
    ),
    Tip(
        "tell-service-log",
        "If you say the tyres were changed or the car was serviced, AgentDrive can record the odometer it already saved and remind you when the next interval is due. It does not wake the car to do that.",
    ),
    Tip(
        "cameras-clean",
        "Keep the cameras and the front glass reasonably clean. Driver assistance works better when it can see.",
    ),
    Tip(
        "smooth-driving",
        "Smoother acceleration and lifting off earlier let regenerative braking do more of the work, which usually means better efficiency.",
    ),
    Tip(
        "tokens-and-keys",
        "Keep tokens, the virtual key, and the VIN in the private config home. Do not paste them into chat, a git repo, or a screenshot.",
    ),
    Tip(
        "washer-and-filters",
        "The cabin filter and washer fluid are easy to forget. A periodic check keeps the ventilation and the view in good shape.",
    ),
    Tip(
        "fresh-confirmation",
        "Unlocking and charging changes need a fresh yes each time. An earlier yes does not carry over, and a scheduled job cannot confirm them.",
    ),
    Tip(
        "one-token-writer",
        "Keep one token file and one program refreshing it. Two refreshers can invalidate each other.",
    ),
)

COOLDOWN_MONTHS = 2


def is_tip_day(day: date) -> bool:
    """Monday, Wednesday, Friday, and the first morning of a month."""
    return day.day == 1 or day.weekday() in (0, 2, 4)


def add_calendar_months(day: date, months: int) -> date:
    """Return the same day-of-month, clamped to the target month."""
    index = day.month - 1 + months
    year = day.year + index // 12
    month = index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last))


def on_cooldown(shown: date, today: date, *, months: int = COOLDOWN_MONTHS) -> bool:
    """True until the calendar-month anniversary. That anniversary is eligible."""
    return today < add_calendar_months(shown, months)


def choose_tip(conn, today: date) -> tuple[dict | None, str]:
    """Pick at most one eligible tip and record that it was shown.

    Returns `(tip, reason)`. `tip` is None unless `reason` is `shown`.
    """
    from core.history.store import load_notices, recent_snapshots, save_notice

    if not is_tip_day(today):
        return None, "not_today"
    notices = load_notices(conn)
    if any(not key.startswith(_SERVICE_PREFIX) and shown == today for key, shown in notices.items()):
        return None, "already_shown"
    preferred = _preferred(recent_snapshots(conn))
    eligible = [tip for tip in CATALOG if tip.id not in notices or not on_cooldown(notices[tip.id], today)]
    if not eligible:
        return None, "none_eligible"
    eligible.sort(key=lambda tip: (0 if tip.id in preferred else 1, CATALOG.index(tip)))
    chosen = eligible[0]
    save_notice(conn, chosen.id, today)
    return {"id": chosen.id, "text": chosen.text}, "shown"


def _preferred(snapshots: list[dict]) -> set[str]:
    """Light hints from stored snapshots. Missing data adds nothing."""
    if not snapshots:
        return set()
    latest = snapshots[0]
    chosen: set[str] = set()
    if latest.get("sentry") in (1, True):
        chosen.add("sentry-drain")
    battery = latest.get("battery_percent")
    charging = str(latest.get("charging_state") or "").strip().lower()
    if isinstance(battery, int) and battery >= 95 and charging in {"", "disconnected", "complete", "stopped"}:
        chosen.add("avoid-sitting-at-100")
    if isinstance(battery, int) and battery <= 20:
        chosen.add("avoid-low-soc")
    if latest.get("charge_limit_percent") == 100:
        chosen.add("daily-charge-limit")
    outside = latest.get("outside_temp_c")
    if isinstance(outside, (int, float)) and outside <= 5:
        chosen.add("winter-plug-in")
        chosen.add("precondition-plugged-in")
    if isinstance(outside, (int, float)) and outside >= 30:
        chosen.add("parked-in-heat")
    return chosen
