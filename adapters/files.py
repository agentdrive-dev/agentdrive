"""File-backed memory, schedules, and secrets shared by host adapters.

Confirmations are asked one at a time and are not cached. Secrets stay in
files. This module does not sign vehicle commands.
"""

from __future__ import annotations

import json
from pathlib import Path

from adapters.base import AdapterBase
from core.atomic import write_atomic
from core.errors import MemoryRejected
from core.profile.owner import assert_memory_safe

SECRET_FILES = {
    "client_id": "client-id",
    "client_secret": "client-secret",
    "vin": "vin",
    "redirect_uri": "redirect-uri",
}


class FileBackedAdapter(AdapterBase):
    """Persist memory and schedules as JSON under a config directory."""

    def __init__(self, root: Path, prompter):
        super().__init__(prompter)
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory_path = self.root / "memory.json"
        self.schedule_path = self.root / "schedules.json"
        self.secrets_dir = self.root / "secrets"
        if self.memory_path.is_file():
            loaded = json.loads(self.memory_path.read_text(encoding="utf-8"))
            for key, value in loaded.items():
                assert_memory_safe(key, value)
            self.memory = loaded
        if self.schedule_path.is_file():
            self.schedules = json.loads(self.schedule_path.read_text(encoding="utf-8"))

    def get_secret(self, name: str) -> str:
        filename = SECRET_FILES.get(name)
        if filename is None:
            raise MemoryRejected("That secret is not available to the adapter.")
        path = self.secrets_dir / filename
        if not path.is_file():
            raise MemoryRejected("That secret file is not present.")
        return path.read_text(encoding="utf-8").strip()

    def _persist(self) -> None:
        write_atomic(self.memory_path, json.dumps(self.memory))
        write_atomic(self.schedule_path, json.dumps(self.schedules))
