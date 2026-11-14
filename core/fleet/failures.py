"""Turn a signed-command failure into a sentence the owner can act on."""

from __future__ import annotations


def clarify_command_failure(command: str, message: str) -> str | None:
    """Return a clearer message, or None to keep the original failure."""
    text = " ".join(str(message or "").lower().split())
    if command == "charge-port-close" and _cable(text):
        return "The charge port did not close because a cable is plugged in. The command was not sent again."
    if command in {"seat-cool", "seat-climate"} and _climate_off(text):
        return "Climate is not on, so the seat was not changed. The command was not sent again."
    if command == "seat-cool" and _unsupported(text):
        return "This car does not have seat cooling. The command was not sent again."
    if command == "seat-climate" and _unsupported(text):
        return "This car does not support automatic seat climate. The command was not sent again."
    if command in {"bioweapon-on", "bioweapon-off"} and _unsupported(text):
        return "This car does not support bioweapon defense mode. The command was not sent again."
    return None


def _cable(text: str) -> bool:
    return any(phrase in text for phrase in ("cable", "plugged", "connector"))


def _climate_off(text: str) -> bool:
    if "climate is not on" in text or "climate is off" in text or "climate_off" in text:
        return True
    if "hvac" in text and "off" in text:
        return True
    return "climate" in text and "not on" in text


def _unsupported(text: str) -> bool:
    return any(
        phrase in text
        for phrase in (
            "not supported",
            "not_supported",
            "unsupported",
            "does not support",
            "doesn't support",
            "not available",
            "unavailable",
        )
    )
