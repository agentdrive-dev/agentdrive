"""Unsolicited messages stay quiet unless the owner needs to act."""


def should_speak(*, requires_action: bool) -> bool:
    return bool(requires_action)
