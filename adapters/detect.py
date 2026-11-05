"""Choose grokbot, muse, or unknown from the process environment.

A single matching host is stored and used. Conflicting hosts are not guessed.
A manual override on the command line, in AGENTDRIVE_ADAPTER, or in a host
file with source override wins over detection.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from adapters.grokbot import GrokBotAdapter
from adapters.muse import MuseAdapter
from adapters.template import TemplateAdapter
from core.atomic import write_atomic
from core.config import repo_root
from core.errors import AmbiguousAdapter, ConfigError

VALID_ADAPTERS = ("grokbot", "muse", "template", "unknown")

# Names that mean this process is hosted by Grok Bot.
GROK_SIGNALS = (
    "GROK_BOT",
    "GROKBOT",
    "GROK_BOT_HOME",
    "GROK_BOT_SESSION",
    # Cursor-hosted Grok Bot sets this. Another adapter can still be forced.
    "CURSOR_AGENT",
)

# Names that mean this process is hosted by Muse.
MUSE_SIGNALS = (
    "MUSE_BOT",
    "MUSE_HOME",
    "MUSE_SESSION",
    "MUSE_AGENT",
)

_OFF = {"", "0", "false", "no"}


@dataclass(frozen=True)
class HostChoice:
    adapter: str
    source: str
    signals: tuple[str, ...]

    def summary(self) -> str:
        if self.signals:
            listed = ", ".join(self.signals)
            return f"{self.adapter} ({self.source}: {listed})"
        return f"{self.adapter} ({self.source})"


def _on(env: Mapping[str, str], name: str) -> bool:
    value = env.get(name)
    if value is None:
        return False
    return value.strip().lower() not in _OFF


def detect_host(env: Mapping[str, str] | None = None) -> HostChoice:
    """Return grokbot, muse, or unknown. Raise if both hosts match."""
    current = os.environ if env is None else env
    grok = tuple(name for name in GROK_SIGNALS if _on(current, name))
    muse = tuple(name for name in MUSE_SIGNALS if _on(current, name))
    if grok and muse:
        raise AmbiguousAdapter(
            "Host detection is ambiguous. "
            f"Grok Bot signals: {', '.join(grok)}. "
            f"Muse signals: {', '.join(muse)}. "
            "Choose one with --adapter grokbot, --adapter muse, or --adapter template."
        )
    if grok:
        return HostChoice("grokbot", "detected", grok)
    if muse:
        return HostChoice("muse", "detected", muse)
    return HostChoice("unknown", "detected", ())


def _read_choice(path: Path) -> HostChoice | None:
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "adapter" not in data:
        return None
    adapter = str(data.get("adapter", "")).strip().lower()
    source = str(data.get("source", "override")).strip().lower()
    if adapter not in VALID_ADAPTERS:
        raise ConfigError("host.yaml adapter must be grokbot, muse, template, or unknown")
    raw_signals = data.get("signals") or []
    if not isinstance(raw_signals, list):
        raw_signals = []
    return HostChoice(adapter, source, tuple(str(item) for item in raw_signals))


def write_choice(path: Path, choice: HostChoice) -> None:
    payload = yaml.safe_dump(
        {"adapter": choice.adapter, "source": choice.source, "signals": list(choice.signals)},
        sort_keys=False,
    )
    write_atomic(path, payload)


def resolve_adapter(
    *,
    home: Path,
    env: Mapping[str, str] | None = None,
    cli_adapter: str | None = None,
    redetect: bool = False,
    project_config: Path | None = None,
) -> HostChoice:
    """Resolve the adapter and store a detected or override choice under home."""
    current = os.environ if env is None else env
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    stored_path = home / "host.yaml"
    if cli_adapter:
        choice = _manual(cli_adapter)
        write_choice(stored_path, choice)
        return choice
    env_override = current.get("AGENTDRIVE_ADAPTER", "").strip().lower()
    if env_override:
        choice = _manual(env_override)
        write_choice(stored_path, choice)
        return choice
    if not redetect:
        for path in (stored_path, project_config or (repo_root() / "config" / "host.yaml")):
            saved = _read_choice(path)
            if saved and saved.source == "override":
                return saved
    try:
        detected = detect_host(current)
    except AmbiguousAdapter:
        if not redetect:
            saved = _read_choice(stored_path)
            if saved and saved.source == "override":
                return saved
        raise
    write_choice(stored_path, detected)
    return detected


def _manual(name: str) -> HostChoice:
    adapter = name.strip().lower()
    if adapter not in VALID_ADAPTERS:
        raise ConfigError("Adapter must be grokbot, muse, template, or unknown")
    return HostChoice(adapter, "override", ())


def load_adapter(name: str, *, root: Path, prompter=None):
    """Build the adapter class for a resolved name. Unknown uses the template."""
    if prompter is None:
        def prompter(challenge):  # noqa: ARG001
            return ""

    if name == "grokbot":
        return GrokBotAdapter(root, prompter)
    if name == "muse":
        return MuseAdapter(root, prompter)
    if name in {"unknown", "template"}:
        return TemplateAdapter(prompter)
    raise ConfigError("Adapter must be grokbot, muse, template, or unknown")
