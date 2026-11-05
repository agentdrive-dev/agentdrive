"""Flash the lights while the vehicle is parked, then read the state again."""

from __future__ import annotations

from core.skills.base import SkillContext, SkillResult


class FlashLightsSkill:
    name = "flash_lights"
    phase = 1

    def run(self, ctx: SkillContext) -> SkillResult:
        observed = ctx.gateway.execute(
            "flash_lights",
            shift_state=ctx.shift_state,
            speed=ctx.speed,
            adapter=ctx.adapter,
            via_schedule=ctx.via_schedule,
            vin=ctx.vin,
            access_token=ctx.access_token,
            observe=ctx.observe,
        )
        return SkillResult(spoken="Lights flashed. The vehicle is still parked.", notified=False, data={"shift_state": observed.shift_state})
