from datetime import datetime, timedelta, timezone

import pytest

from core.errors import CommandFailed, ConfirmationRequired, NotParked, ScheduleBlocked, VerificationFailed
from core.fleet.commands import CommandResult
from core.fleet.snapshot import Observation
from core.safety.confirm import Confirmation
from core.safety.gateway import Gateway
from core.safety.parked import require_parked


class Runner:
    def __init__(self):
        self.calls = []

    def run(self, *, vin, access_token, args, timeout=45):
        self.calls.append({"vin": vin, "args": args})
        return CommandResult(0, "")


class Adapter:
    def __init__(self, statement=None, source="owner"):
        self.statement = statement
        self.source = source
        self.calls = 0
        self.last = None

    def confirm(self, challenge):
        self.calls += 1
        self.last = challenge
        return Confirmation(
            action_id=challenge.action_id,
            command=challenge.command,
            accepted=True,
            source=self.source,
            statement=self.statement or "",
        )


def parked(**overrides):
    values = dict(shift_state="P", speed=0, locked=True, climate_on=False, sentry_on=True)
    values.update(overrides)
    return Observation(**values)


def execute(command, *, adapter=None, via_schedule=False, shift="P", speed=0, after=None, extra=None, runner=None):
    runner = runner or Runner()
    seen = after or parked()
    gateway = Gateway(runner)
    result = gateway.execute(
        command,
        shift_state=shift,
        speed=speed,
        adapter=adapter or Adapter(),
        via_schedule=via_schedule,
        vin="VIN-TEST",
        access_token="token",
        observe=lambda: seen,
        extra_args=extra,
    )
    return result, runner


def test_flash_when_parked_does_not_ask_for_confirmation():
    adapter = Adapter()
    _, runner = execute("flash_lights", adapter=adapter)
    assert runner.calls[0]["args"] == ["flash-lights"]
    assert adapter.calls == 0


def test_drive_and_unknown_speed_are_refused_before_the_tool_runs():
    runner = Runner()
    with pytest.raises(NotParked):
        execute("climate_on", shift="D", speed=0, runner=runner)
    with pytest.raises(NotParked):
        execute("climate_on", speed=None, runner=runner)
    with pytest.raises(NotParked):
        execute("climate_on", speed=5, runner=runner)
    assert runner.calls == []


def test_zero_speed_float_is_parked():
    require_parked("P", 0.0)


def test_unlock_requires_a_matching_one_time_confirmation():
    runner = Runner()
    with pytest.raises(ConfirmationRequired):
        execute("unlock", adapter=Adapter("yes"), after=parked(locked=False), runner=runner)
    assert runner.calls == []

    adapter = Adapter("I confirm unlock")
    runner = Runner()
    gateway = Gateway(runner)
    gateway.execute(
        "unlock",
        shift_state="P",
        speed=0,
        adapter=adapter,
        via_schedule=False,
        vin="VIN-TEST",
        access_token="token",
        observe=lambda: parked(locked=False),
    )
    assert runner.calls[0]["args"] == ["unlock"]
    spent = Confirmation(
        action_id=adapter.last.action_id,
        command="unlock",
        accepted=True,
        source="owner",
        statement="I confirm unlock",
    )
    with pytest.raises(ConfirmationRequired):
        gateway.broker.authorize(gateway.specs["unlock"], adapter.last, spent, via_schedule=False)


def test_unlock_confirmation_does_not_authorize_lock_or_trunk():
    runner = Runner()
    with pytest.raises(ConfirmationRequired):
        execute("frunk_open", adapter=Adapter("I confirm unlock"), after=parked(frunk_open=True), runner=runner)
    with pytest.raises(ConfirmationRequired):
        execute("trunk_open", adapter=Adapter("I confirm unlock"), after=parked(trunk_open=True), runner=runner)
    assert runner.calls == []


def test_schedule_cannot_run_or_confirm_a_sensitive_command():
    adapter = Adapter("I confirm unlock")
    runner = Runner()
    with pytest.raises(ScheduleBlocked):
        execute("unlock", adapter=adapter, via_schedule=True, after=parked(locked=False), runner=runner)
    assert adapter.calls == 0
    assert runner.calls == []


def test_schedule_can_run_climate_without_a_confirmation():
    adapter = Adapter()
    _, runner = execute("climate_on", adapter=adapter, via_schedule=True, after=parked(climate_on=True))
    assert adapter.calls == 0
    assert runner.calls[0]["args"] == ["climate-on"]


def test_mismatched_follow_up_state_does_not_retry():
    runner = Runner()
    with pytest.raises(VerificationFailed):
        execute("climate_off", after=parked(climate_on=True), runner=runner)
    assert len(runner.calls) == 1


def test_cached_confirmation_is_rejected():
    runner = Runner()
    with pytest.raises(ConfirmationRequired):
        execute("sentry_off", adapter=Adapter("I confirm sentry_off", source="cached"), after=parked(sentry_on=False), runner=runner)
    assert runner.calls == []


def test_command_failure_is_not_retried(monkeypatch):
    class Boom:
        def run(self, **kwargs):
            raise CommandFailed("failed once")

    gateway = Gateway(Boom())
    with pytest.raises(CommandFailed):
        gateway.execute(
            "flash_lights",
            shift_state="P",
            speed=0,
            adapter=Adapter(),
            via_schedule=False,
            vin="VIN-TEST",
            access_token="token",
            observe=lambda: parked(),
        )
