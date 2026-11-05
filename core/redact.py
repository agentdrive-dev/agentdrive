"""Mask secrets in text that a person or a bot might read."""

from __future__ import annotations

import re

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


def mask_vin(vin: str) -> str:
    """Show only the last four characters. A short value is hidden entirely."""
    cleaned = "".join(str(vin).split())
    if len(cleaned) < 4:
        return "****"
    return "****" + cleaned[-4:]


def redact(text: str, secrets: list[str]) -> str:
    """Replace each secret with a fixed marker. Longer secrets are replaced first."""
    cleaned = text
    ordered = sorted({item for item in secrets if item}, key=len, reverse=True)
    for secret in ordered:
        cleaned = cleaned.replace(secret, "[redacted]")
    return scrub(cleaned)


def scrub(text: str) -> str:
    """Hide JWT-shaped strings that a caller did not list explicitly."""
    return _JWT.sub("[redacted]", text)
