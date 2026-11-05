"""Copy this adapter when wiring a new assistant.

It implements schedule, memory, confirm, secrets, and notify in memory.
Replace the prompter with the assistant's question to the owner.
Do not cache a confirmation, and do not let a schedule answer one.
"""

from __future__ import annotations

from adapters.base import AdapterBase


class TemplateAdapter(AdapterBase):
    """Working in-memory adapter used as the starting point for a new bot."""
