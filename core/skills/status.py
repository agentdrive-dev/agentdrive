"""Read-only status for a question the owner just asked."""

from __future__ import annotations

from core.fleet.snapshot import Observation
from core.skills.base import SkillContext, SkillResult


class StatusSkill:
    """Summarize the latest vehicle read. This does not send a command."""

    name = "status"
    phase = 1

    def run(self, ctx: SkillContext) -> SkillResult:
        spoken = summarize(ctx.observation)
        return SkillResult(spoken=spoken, notified=False, data={"command": None})


def summarize(observation: Observation) -> str:
    parts: list[str] = []
    if observation.battery_level is not None:
        parts.append(f"Battery {observation.battery_level} percent.")
    if observation.charging_state:
        parts.append(f"Charging state {observation.charging_state}.")
    if observation.shift_state == "P" and observation.speed == 0:
        parts.append("Parked.")
    elif observation.shift_state:
        parts.append(f"Shift {observation.shift_state}.")
    if observation.locked is True:
        parts.append("Locked.")
    elif observation.locked is False:
        parts.append("Unlocked.")
    if observation.climate_on is True:
        parts.append("Climate on.")
    elif observation.climate_on is False:
        parts.append("Climate off.")
    if observation.sentry_on is True:
        parts.append("Sentry on.")
    elif observation.sentry_on is False:
        parts.append("Sentry off.")
    if observation.tpms_soft_warning is True:
        parts.append("A tire warning is present.")
    if not parts:
        return "No vehicle state was returned."
    return " ".join(parts)
