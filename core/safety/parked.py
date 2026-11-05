"""A state-changing command is sent only in Park at speed zero."""

from __future__ import annotations

from core.errors import NotParked


def require_parked(shift_state: str | None, speed) -> None:
    if shift_state != "P":
        raise NotParked("The vehicle is not in Park. No state-changing command was sent.")
    if speed is None:
        raise NotParked("The vehicle speed is unknown. No state-changing command was sent.")
    try:
        speed_value = float(speed)
    except (TypeError, ValueError) as exc:
        raise NotParked("The vehicle speed is unknown. No state-changing command was sent.") from exc
    if speed_value != 0:
        raise NotParked("The vehicle speed is not zero. No state-changing command was sent.")
