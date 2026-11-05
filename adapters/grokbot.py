"""File-backed adapter for Grok Bot.

Secrets stay in files. Memory and schedules are JSON. Confirmations are asked
one at a time and are not cached.
"""

from __future__ import annotations

from adapters.files import FileBackedAdapter

# Printed when this adapter is installed, and repeated in the assistant docs.
# The routine is read-only: `agentdrive check` must not wake a sleeping car.
INSTALL_NOTE = (
    "Set up a recurring health check by default. "
    "Schedule a routine that runs `agentdrive check --json` every few hours. "
    "The check is read-only and does not wake a sleeping car. "
    "Exit 0 means there is nothing to report, including when the vehicle is asleep. "
    "Exit 1 means an alert the owner should hear. Notify only when the exit code is 1."
)


class GrokBotAdapter(FileBackedAdapter):
    """File-backed adapter for Grok Bot."""
