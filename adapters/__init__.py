"""Swappable assistant adapters."""

from adapters.detect import load_adapter, resolve_adapter
from adapters.grokbot import GrokBotAdapter
from adapters.muse import MuseAdapter
from adapters.template import TemplateAdapter

__all__ = [
    "GrokBotAdapter",
    "MuseAdapter",
    "TemplateAdapter",
    "load_adapter",
    "resolve_adapter",
]
