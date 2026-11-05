"""Turn climate on or off while the vehicle is parked."""

from __future__ import annotations

from core.skills.base import SkillContext, SkillResult


class ClimateSkill:
    name = "climate"
    phase = 1

    def run(self, ctx: SkillContext, *, on: bool) -> SkillResult:
        command = "climate_on" if on else "climate_off"
        ctx.gateway.execute(
            command,
            shift_state=ctx.shift_state,
            speed=ctx.speed,
            adapter=ctx.adapter,
            via_schedule=ctx.via_schedule,
            vin=ctx.vin,
            access_token=ctx.access_token,
            observe=ctx.observe,
        )
        spoken = "Climate is on." if on else "Climate is off."
        return SkillResult(spoken=spoken, notified=False, data={"command": command})
