import importlib.util
from pathlib import Path

import pytest

from adapters.template import TemplateAdapter
from core.cli import main
from core.errors import ConfigError, ConfirmationRequired, NotParked, ScheduleBlocked, VerificationFailed
from core.fleet.snapshot import defrost_state, from_vehicle_data
from core.ops import PUBLIC_COMMANDS, parse_on_off, parse_seat_heat
from core.safety.catalog import load_specs
from tests.test_refresh_and_check import VIN, Control, Http, listed, make_app, urls, vehicle_payload
from tests.test_safety import Adapter, Runner, execute, parked

ROOT = Path(__file__).resolve().parents[1]


def _patch_module():
    spec = importlib.util.spec_from_file_location(
        "patch_tesla_control_defrost",
        ROOT / "setup" / "patch_tesla_control_defrost.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sentry_on_is_standard_and_sentry_off_stays_sensitive():
    specs = load_specs()
    assert specs["sentry_on"].tier == "standard"
    assert specs["sentry_on"].tool_args == ("sentry-mode", "on")
    assert specs["sentry_off"].tier == "sensitive"
    assert specs["sentry_off"].tool_args == ("sentry-mode", "off")
    assert specs["sentry_off"].action == "turn sentry off"
    for name in ("seat_heater", "steering_wheel_heater", "defrost_on", "defrost_off", "cabin_precondition"):
        assert specs[name].tier == "standard"
    assert specs["cabin_precondition"].tool_args == ("climate-on",)
    assert specs["defrost_on"].tool_args == ("preconditioning-max", "on")
    assert specs["defrost_off"].tool_args == ("preconditioning-max", "off")
    assert "trunk" not in PUBLIC_COMMANDS
    assert "frunk" not in PUBLIC_COMMANDS
    assert "precondition-schedule" not in PUBLIC_COMMANDS


def test_seat_and_steering_reject_combinations_the_signed_tool_rejects():
    assert parse_seat_heat("Front-Left", "HIGH") == ("front-left", "high")
    assert parse_on_off("OFF", "steering heat") == "off"
    for seat, level in (
        ("2nd-row-left-back", "low"),
        ("front-center", "high"),
        ("rear-left", "off"),
        ("3rd-row-center", "low"),
        ("0", "high"),
    ):
        with pytest.raises(ConfigError):
            parse_seat_heat(seat, level)
    for level in ("med", "3", "on", "max"):
        with pytest.raises(ConfigError):
            parse_seat_heat("front-left", level)
    for state in ("low", "1", "auto", "medium"):
        with pytest.raises(ConfigError):
            parse_on_off(state, "steering heat")


def test_cli_refuses_unsupported_seat_and_steering_before_a_network_call(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "seat-heat", "2nd-row-left-back", "high"]) == 2
    assert "signed tool" in capsys.readouterr().err
    assert main(["--home", str(tmp_path), "seat-heat", "front-right", "med"]) == 2
    assert "off, low, medium, or high" in capsys.readouterr().err
    assert main(["--home", str(tmp_path), "steering-heat", "low"]) == 2
    assert "on or off" in capsys.readouterr().err


def test_sentry_off_confirmation_names_the_action_and_is_one_time():
    runner = Runner()
    with pytest.raises(ConfirmationRequired):
        execute("sentry_off", adapter=Adapter("yes"), after=parked(sentry_on=False), runner=runner)
    with pytest.raises(ConfirmationRequired):
        execute("sentry_off", adapter=Adapter("I confirm sentry on"), after=parked(sentry_on=False), runner=runner)
    with pytest.raises(ConfirmationRequired):
        execute("sentry_off", adapter=Adapter("I confirm turn sentry on"), after=parked(sentry_on=False), runner=runner)
    assert runner.calls == []

    _, runner = execute(
        "sentry_off",
        adapter=Adapter("I confirm turn sentry off"),
        after=parked(sentry_on=False),
    )
    assert runner.calls[0]["args"] == ["sentry-mode", "off"]

    _, runner = execute("sentry_off", adapter=Adapter("I confirm sentry_off"), after=parked(sentry_on=False))
    assert runner.calls[0]["args"] == ["sentry-mode", "off"]


def test_sentry_on_does_not_ask_and_a_mismatch_is_not_retried():
    adapter = Adapter()
    _, runner = execute("sentry_on", adapter=adapter, after=parked(sentry_on=True))
    assert adapter.calls == 0
    assert runner.calls[0]["args"] == ["sentry-mode", "on"]

    runner = Runner()
    with pytest.raises(VerificationFailed):
        execute("sentry_on", after=parked(sentry_on=False), runner=runner)
    assert len(runner.calls) == 1


def test_climate_extras_send_the_signed_commands_and_read_them_back():
    _, runner = execute(
        "seat_heater",
        extra=["front-left", "medium"],
        after=parked(seat_heat={"front-left": 2}),
    )
    assert runner.calls[0]["args"] == ["seat-heater", "front-left", "medium"]

    _, runner = execute("steering_wheel_heater", extra=["on"], after=parked(steering_heater=True))
    assert runner.calls[0]["args"] == ["steering-wheel-heater", "on"]
    _, runner = execute("steering_wheel_heater", extra=["off"], after=parked(steering_heater=False))
    assert runner.calls[0]["args"] == ["steering-wheel-heater", "off"]

    _, runner = execute("defrost_on", after=parked(defrost_mode=2, front_defroster_on=True, rear_defroster_on=True))
    assert runner.calls[0]["args"] == ["preconditioning-max", "on"]
    _, runner = execute("defrost_off", after=parked(defrost_mode=0, front_defroster_on=False, rear_defroster_on=False))
    assert runner.calls[0]["args"] == ["preconditioning-max", "off"]

    _, runner = execute("cabin_precondition", after=parked(climate_on=True))
    assert runner.calls[0]["args"] == ["climate-on"]
    assert "precondition-schedule" not in runner.calls[0]["args"]


def test_climate_extra_mismatch_is_not_sent_again():
    runner = Runner()
    with pytest.raises(VerificationFailed):
        execute("seat_heater", extra=["2nd-row-right", "high"], after=parked(seat_heat={"2nd-row-right": 0}), runner=runner)
    with pytest.raises(VerificationFailed):
        execute("steering_wheel_heater", extra=["on"], after=parked(steering_heater=False), runner=runner)
    with pytest.raises(VerificationFailed):
        execute("defrost_off", after=parked(defrost_mode=2), runner=runner)
    assert len(runner.calls) == 3


def test_new_commands_are_refused_when_the_car_is_not_parked():
    runner = Runner()
    for command, extra in (
        ("sentry_on", None),
        ("sentry_off", None),
        ("seat_heater", ["front-left", "low"]),
        ("steering_wheel_heater", ["off"]),
        ("defrost_on", None),
        ("cabin_precondition", None),
    ):
        with pytest.raises(NotParked):
            execute(
                command,
                shift="D",
                speed=5,
                extra=extra,
                adapter=Adapter("I confirm turn sentry off"),
                runner=runner,
            )
    assert runner.calls == []


def test_a_schedule_cannot_run_sentry_off_and_can_still_run_check_and_climate():
    adapter = TemplateAdapter(lambda challenge: "I confirm turn sentry off")
    for command in ("sentry-off", "sentry_off", "unlock", "charging_start", "charge-schedule set"):
        with pytest.raises(ScheduleBlocked):
            adapter.schedule("blocked", "22:00", {"command": command})
    adapter.schedule("health-check", "every 30 minutes", {"command": "check", "json": True})
    adapter.schedule("morning", "07:30", {"command": "history due"})
    adapter.schedule("heat", "07:00", {"command": "seat-heat"})
    adapter.schedule("cabin", "07:00", {"command": "precondition"})
    adapter.schedule("sentry", "07:00", {"command": "sentry-on"})
    stored = {item["task"]["command"] for item in adapter.schedules}
    assert "check" in stored
    assert "history due" in stored
    assert "sentry-off" not in stored

    runner = Runner()
    with pytest.raises(ScheduleBlocked):
        execute(
            "sentry_off",
            via_schedule=True,
            adapter=Adapter("I confirm turn sentry off"),
            after=parked(sentry_on=False),
            runner=runner,
        )
    assert runner.calls == []
    _, runner = execute("cabin_precondition", via_schedule=True, after=parked(climate_on=True))
    assert runner.calls[0]["args"] == ["climate-on"]


def test_sentry_commands_wake_a_sleeping_car_and_check_does_not(monkeypatch):
    monkeypatch.setattr("core.ops.sys.stdin.isatty", lambda: False)
    http = Http(
        listed("asleep"),
        vehicle_payload(vehicle_state={"sentry_mode": True, "locked": True}),
        polls=[{"response": {"state": "online"}}],
    )
    control = Control()
    text = make_app(http, control).command(
        "sentry-on",
        preferred=None,
        argument=None,
        statement=None,
        sleep=lambda _seconds: None,
    )
    assert text == "OK: sentry-on"
    assert sum(1 for url in urls(http) if url.endswith("/wake_up")) == 1
    assert control.calls[0]["args"] == ["sentry-mode", "on"]
    assert VIN not in text

    asleep = Http(listed("asleep"))
    report = make_app(asleep).check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
    assert report.skipped is True
    assert not any("wake_up" in url or "vehicle_data" in url for url in urls(asleep))


def test_sentry_off_from_the_app_requires_the_clear_sentence(monkeypatch):
    monkeypatch.setattr("core.ops.sys.stdin.isatty", lambda: False)
    http = Http(listed("online"), vehicle_payload(vehicle_state={"sentry_mode": False, "locked": True}))
    control = Control()
    app = make_app(http, control)
    with pytest.raises(ConfirmationRequired) as caught:
        app.command("sentry-off", preferred=None, argument=None, statement=None, sleep=lambda _seconds: None)
    assert "turn sentry off" in str(caught.value)
    assert control.calls == []

    http = Http(listed("online"), vehicle_payload(drive_state={"shift_state": "D", "speed": 4}, vehicle_state={"sentry_mode": False}))
    control = Control()
    with pytest.raises(NotParked):
        make_app(http, control).command(
            "sentry-off",
            preferred=None,
            argument=None,
            statement="I confirm turn sentry off",
            sleep=lambda _seconds: None,
        )
    assert control.calls == []

    http = Http(listed("online"), vehicle_payload(vehicle_state={"sentry_mode": False, "locked": True}))
    control = Control()
    text = make_app(http, control).command(
        "sentry-off",
        preferred=None,
        argument=None,
        statement="I confirm turn sentry off",
        sleep=lambda _seconds: None,
    )
    assert text == "OK: sentry-off"
    assert control.calls[0]["args"] == ["sentry-mode", "off"]


def test_app_sends_seat_steering_defrost_and_precondition(monkeypatch):
    monkeypatch.setattr("core.ops.sys.stdin.isatty", lambda: False)
    http = Http(
        listed("online"),
        vehicle_payload(
            climate_state={
                "is_climate_on": True,
                "seat_heater_left": 1,
                "steering_wheel_heater": False,
                "defrost_mode": 0,
                "is_front_defroster_on": False,
                "is_rear_defroster_on": False,
            }
        ),
    )
    control = Control()
    app = make_app(http, control)
    assert app.command("seat-heat", preferred=None, argument=None, statement=None, extra=["front-left", "low"], sleep=lambda _s: None) == "OK: seat-heat front-left low"
    assert control.calls[-1]["args"] == ["seat-heater", "front-left", "low"]
    assert app.command("steering-heat", preferred=None, argument=None, statement=None, extra=["off"], sleep=lambda _s: None) == "OK: steering-heat off"
    assert control.calls[-1]["args"] == ["steering-wheel-heater", "off"]
    assert app.command("defrost-off", preferred=None, argument=None, statement=None, sleep=lambda _s: None) == "OK: defrost-off"
    assert control.calls[-1]["args"] == ["preconditioning-max", "off"]
    assert app.command("precondition", preferred=None, argument=None, statement=None, sleep=lambda _s: None) == "OK: precondition"
    assert control.calls[-1]["args"] == ["climate-on"]


def test_climate_readings_map_seat_heat_and_defrost():
    observation = from_vehicle_data(
        {
            "response": {
                "climate_state": {
                    "seat_heater_left": 3,
                    "seat_heater_rear_center": 0,
                    "seat_heater_third_row_right": 2,
                    "steering_wheel_heater": True,
                    "defrost_mode": 2,
                    "is_front_defroster_on": True,
                    "is_rear_defroster_on": True,
                }
            }
        }
    )
    assert observation.seat_heat == {"front-left": 3, "2nd-row-center": 0, "3rd-row-right": 2}
    assert observation.steering_heater is True
    assert defrost_state(observation.defrost_mode, observation.front_defroster_on, observation.rear_defroster_on) == "max"
    assert defrost_state(0, False, False) == "off"
    assert defrost_state(None, None, None) is None


def test_queries_page_and_skill_document_sentry_wake_behavior():
    queries = (ROOT / "docs" / "06-queries-and-commands.md").read_text(encoding="utf-8")
    skill = (ROOT / ".agents" / "skills" / "agentdrive" / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "`sentry-on` and `sentry-off` wake a sleeping car." in queries
    assert "`check`, `report`, `where`, and history do not wake a sleeping car." in queries
    assert "Both wake a sleeping car" in skill
    assert "do not wake a sleeping car" in skill
    assert "heat a seat" in readme
    assert "heat the steering wheel" in readme
    assert "turn defrost on or off" in readme
    assert "precondition the cabin" in readme
    assert "Turn sentry mode on or off" in readme
    assert "may wake a sleeping car" not in readme
    assert "## Next" not in readme
    assert "| Command |" not in readme
    for page in (ROOT / "README.md", ROOT / "DISCLAIMER.md", ROOT / "SECURITY.md"):
        assert "when those commands are added" in page.read_text(encoding="utf-8") or page.name == "README.md"


def test_local_tesla_control_build_gains_max_defrost_without_a_manual_override(tmp_path):
    ensure_defrost_command = _patch_module().ensure_defrost_command
    target = tmp_path / "commands.go"
    target.write_text('package main\nvar commands = map[string]*Command{\n\t"product-info": {\n\t},\n}\n', encoding="utf-8")
    assert ensure_defrost_command(target) is True
    text = target.read_text(encoding="utf-8")
    assert text.count('"preconditioning-max":') == 1
    assert "SetPreconditioningMax(ctx, state, false)" in text
    assert "SetPreconditioningMax(ctx, state, true)" not in text
    assert ensure_defrost_command(target) is False
    assert target.read_text(encoding="utf-8").count('"preconditioning-max":') == 1
    missing = tmp_path / "other.go"
    missing.write_text("package main\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        ensure_defrost_command(missing)


def test_patch_module_is_the_installer_script():
    module = _patch_module()
    assert module.__file__.endswith("setup/patch_tesla_control_defrost.py")
    assert "SetPreconditioningMax(ctx, state, false)" in module.SNIPPET
