"""Rules shared by working adapters.

A schedule cannot carry a confirmation. Memory cannot hold secrets.
A notification is sent only when the owner needs to act.
"""

from __future__ import annotations

from core.errors import MemoryRejected, ScheduleBlocked
from core.profile.owner import assert_memory_safe
from core.safety.catalog import schedule_blocks, sensitive_names
from core.safety.confirm import Challenge, Confirmation


class AdapterBase:
    """In-memory behavior. File-backed adapters persist after each change."""

    def __init__(self, prompter, secrets: dict[str, str] | None = None):
        self._prompter = prompter
        self._secrets = dict(secrets or {})
        self.memory: dict = {}
        self.schedules: list[dict] = []
        self.outbox: list[str] = []
        self._in_schedule = False
        self._sensitive = sensitive_names()

    def schedule(self, name: str, when: str, task: dict) -> str:
        if not isinstance(task, dict):
            raise ScheduleBlocked("A schedule needs a task mapping.")
        command = task.get("command")
        if _schedule_command_is_sensitive(command, self._sensitive):
            raise ScheduleBlocked("A sensitive command cannot be put on a schedule.")
        if task.get("preconfirmed") or "confirmation" in task:
            raise ScheduleBlocked("A schedule cannot carry a confirmation.")
        item = {"name": name, "when": when, "task": dict(task)}
        self.schedules.append(item)
        self._persist()
        return name

    def remember(self, key: str, value) -> None:
        assert_memory_safe(key, value)
        self.memory[key] = value
        self._persist()

    def recall(self, key: str):
        return self.memory.get(key)

    def confirm(self, challenge: Challenge) -> Confirmation:
        if self._in_schedule:
            raise ScheduleBlocked("A schedule cannot ask for confirmation.")
        statement = str(self._prompter(challenge)).strip()
        accepted = "confirm" in statement.lower().split()
        return Confirmation(
            action_id=challenge.action_id,
            command=challenge.command,
            accepted=accepted,
            source="owner",
            statement=statement,
        )

    def get_secret(self, name: str) -> str:
        if name not in self._secrets:
            raise MemoryRejected("That secret is not available to the adapter.")
        return self._secrets[name]

    def notify(self, message: str, *, requires_action: bool, kind: str = "alert") -> bool:
        del kind
        if not requires_action:
            return False
        self.outbox.append(message)
        self._persist()
        return True

    def run_due(self, handler) -> None:
        self._in_schedule = True
        try:
            for item in list(self.schedules):
                handler(item)
        finally:
            self._in_schedule = False

    def _persist(self) -> None:
        return None


def _schedule_command_is_sensitive(command, sensitive: set[str]) -> bool:
    # Sensitive spec names, the charge-schedule family, sentry off, and trunk, frunk, and remote start.
    # `check` and `history` stay allowed for the health check and the morning report.
    return schedule_blocks(command, sensitive)
