"""File-backed adapter for Grok Bot.

Secrets stay in files. Memory and schedules are JSON. Confirmations are asked
one at a time and are not cached.
"""

from __future__ import annotations

from adapters.files import FileBackedAdapter
from core.fleet.check_state import HEALTH_CHECK_NOTE
from core.report_schedule import DAILY_REPORT_NOTE

# Printed when this adapter is installed, and repeated in the assistant docs.
# The routine is read-only: `agentdrive check` must not wake a sleeping car.
INSTALL_NOTE = HEALTH_CHECK_NOTE + DAILY_REPORT_NOTE


class GrokBotAdapter(FileBackedAdapter):
    """File-backed adapter for Grok Bot."""
