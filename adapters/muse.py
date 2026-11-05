"""TODO: Phase 2 — connect a Muse-hosted assistant.

Scheduling, memory, and prompts for Muse are not wired in this version.
"""


class MuseAdapter:
    """Phase 2 stub. Constructing it does not connect to Muse."""

    def schedule(self, name: str, when: str, task: dict) -> str:
        """TODO: Phase 2 — register a Muse schedule without a stored confirmation."""
        raise NotImplementedError("The Muse adapter is a Phase 2 stub")

    def remember(self, key: str, value) -> None:
        """TODO: Phase 2 — write Muse memory after the secret filter."""
        raise NotImplementedError("The Muse adapter is a Phase 2 stub")

    def recall(self, key: str):
        """TODO: Phase 2 — read Muse memory."""
        raise NotImplementedError("The Muse adapter is a Phase 2 stub")

    def confirm(self, challenge) -> object:
        """TODO: Phase 2 — ask the owner in Muse, once per action."""
        raise NotImplementedError("The Muse adapter is a Phase 2 stub")

    def get_secret(self, name: str) -> str:
        """TODO: Phase 2 — read a local secret without copying it into Muse memory."""
        raise NotImplementedError("The Muse adapter is a Phase 2 stub")

    def notify(self, message: str, *, requires_action: bool, kind: str = "alert") -> bool:
        """TODO: Phase 2 — deliver a Muse message only when the owner needs to act."""
        raise NotImplementedError("The Muse adapter is a Phase 2 stub")
