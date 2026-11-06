"""File-backed adapter for a Muse agent.

Memory and schedules are JSON in the config home. Secrets stay in files.
Vehicle commands are not signed here. The shared core signs them.
"""

from __future__ import annotations

from pathlib import Path

from adapters.files import FileBackedAdapter
from core.config import repo_root
from core.report_schedule import DAILY_REPORT_NOTE

# Printed when this adapter is installed. The health check matches the Grok
# Bot note: `agentdrive check` is read-only and must not wake a sleeping car.
INSTALL_NOTE = (
    "Muse adapter is installed. "
    "Follow .agents/skills/agentdrive/SKILL.md and the prompt in "
    ".agents/skills/agentdrive/prompts/install.md. "
    "Set up a recurring health check by default. "
    "Schedule a routine that runs `agentdrive check --json` every few hours. "
    "The check is read-only and does not wake a sleeping car. "
    "Exit 0 means there is nothing to report, including when the vehicle is asleep. "
    "Exit 1 means an alert the owner should hear. Notify only when the exit code is 1. "
    "The config home is $AGENTDRIVE_HOME, or ~/.config/agentdrive when that variable is unset "
    "(on Windows, C:\\Users\\<you>\\.config\\agentdrive). "
    "Point AGENTDRIVE_KEY_FILE or runtime.yaml key_file at the existing private key, "
    "for example C:\\Users\\<you>\\agentdrive-key\\private-key.pem. "
    "Point AGENTDRIVE_TOKEN_FILE or runtime.yaml token_file at the existing token file. "
    "Do not sign commands in this adapter. Use the agentdrive command, which calls the shared core. "
    + DAILY_REPORT_NOTE
)


class MuseAdapter(FileBackedAdapter):
    """Working file-backed adapter for Muse. Constructing it does not sign a command."""

    @staticmethod
    def bundle_dir() -> Path:
        return repo_root() / ".agents" / "skills" / "agentdrive"

    @classmethod
    def bundle_files(cls) -> dict[str, Path]:
        root = cls.bundle_dir()
        return {
            "skill": root / "SKILL.md",
            "prompt": root / "prompts" / "install.md",
            "config": root / "config" / "runtime.example.yaml",
        }
