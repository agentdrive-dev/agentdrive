"""Shared skill types."""

from __future__ import annotations

from dataclasses import dataclass

from core.fleet.snapshot import Observation
from core.profile.owner import OwnerProfile


@dataclass
class SkillResult:
    spoken: str
    notified: bool
    data: dict


@dataclass
class SkillContext:
    observation: Observation
    adapter: object
    profile: OwnerProfile | None = None
    gateway: object | None = None
    via_schedule: bool = False
    vin: str = ""
    access_token: str = ""
    shift_state: str | None = None
    speed: float | None = None
    observe: object | None = None
