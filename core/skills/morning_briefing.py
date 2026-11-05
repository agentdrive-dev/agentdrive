"""One parked readout. Speak only when the owner needs to do something."""

from __future__ import annotations

from core.fleet.snapshot import Observation
from core.profile.owner import OwnerProfile
from core.skills.base import SkillContext, SkillResult
from core.skills.notify import should_speak


class MorningBriefingSkill:
    name = "morning_briefing"
    phase = 1

    def run(self, ctx: SkillContext) -> SkillResult:
        requires_action, spoken = briefing(ctx.observation, ctx.profile)
        if not should_speak(requires_action=requires_action):
            return SkillResult(spoken="", notified=False, data={"requires_action": False})
        notified = bool(ctx.adapter.notify(spoken, requires_action=True, kind="alert"))
        return SkillResult(spoken=spoken, notified=notified, data={"requires_action": True})


def briefing(observation: Observation, profile: OwnerProfile | None) -> tuple[bool, str]:
    threshold = profile.habit_int("low_battery_percent", 30) if profile else 30
    reasons: list[str] = []
    if observation.locked is False:
        reasons.append("The vehicle is unlocked.")
    if observation.sentry_on is False:
        reasons.append("Sentry mode is off.")
    if observation.battery_level is not None and observation.battery_level < threshold:
        reasons.append(f"Battery is at {observation.battery_level} percent, under your {threshold} percent line.")
    if observation.tpms_soft_warning is True:
        reasons.append("A tire-pressure warning is present.")
    if not reasons:
        return False, ""
    return True, " ".join(reasons) + " This is worth doing before you leave."
