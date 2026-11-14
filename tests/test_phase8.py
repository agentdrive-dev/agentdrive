"""Phase 8 parked controls, official records, presence, and energy-mode alerts."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.cli import main
from core.errors import CommandFailed, ConfigError, ConfirmationRequired, NotParked, ScheduleBlocked, VerificationFailed
from core.family import classify
from core.fleet.charging import format_history, history_rows, parse_invoice_id
from core.fleet.check_state import CHECK_ALERTS_NAME, apply_rising_edge
from core.fleet.health import active_energy_modes, evaluate_alerts, report_from_observation
from core.fleet.snapshot import cable_plugged, climate_keeper_name, driver_seat_text, from_vehicle_data
from core.ops import parse_seat_climate, parse_seat_cool, parse_update_delay
from core.safety.catalog import load_specs, schedule_blocks
from tests.test_cars_and_family import VIN_A, Http as CarsHttp
from tests.test_cars_and_family import make_app as cars_app
from tests.test_cars_and_family import payload as cars_payload
from tests.test_cars_and_family import row
from tests.test_refresh_and_check import VIN, Control, Http, listed, make_app, urls, vehicle_payload
from tests.test_safety import Adapter, Runner, execute, parked

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc)


def _patch_module():
    spec = importlib.util.spec_from_file_location(
        "patch_tesla_control_phase8",
        ROOT / "setup" / "patch_tesla_control_phase8.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _report(observation):
    return report_from_observation(observation, tyre_bar=2.5, low_battery_percent=30)


def test_new_commands_are_mapped_and_confirmation_follows_energy_use():
    specs = load_specs()
    assert specs["climate_keeper_off"].tier == "standard"
    assert specs["climate_keeper_off"].tool_args == ("climate-keeper", "off")
    for mode in ("keep", "dog", "camp"):
        spec = specs[f"climate_keeper_{mode}"]
        assert spec.tier == "sensitive"
        assert spec.tool_args == ("climate-keeper", mode)
        assert spec.action == f"climate keeper {mode}"
    assert specs["charge_port_open"].tier == "sensitive"
    assert specs["charge_port_open"].tool_args == ("charge-port-open",)
    assert specs["charge_port_open"].action == "open charge port"
    assert specs["charge_port_close"].tool_args == ("charge-port-close",)
    assert specs["charge_port_close"].action == "close charge port"
    assert specs["seat_cooler"].tier == "standard"
    assert specs["seat_cooler"].tool_args == ("seat-cooler",)
    assert specs["auto_seat_climate"].tool_args == ("auto-seat-and-climate",)
    assert specs["bioweapon_on"].tier == "sensitive"
    assert specs["bioweapon_on"].action == "turn bioweapon defense on"
    assert specs["bioweapon_on"].tool_args == ("bioweapon-mode", "on")
    assert specs["bioweapon_off"].tier == "standard"
    assert specs["software_schedule"].tier == "sensitive"
    assert specs["software_schedule"].tool_args == ("software-update-start",)
    assert specs["software_schedule"].action == "schedule software update"
    assert specs["software_cancel"].action == "cancel software update"
    assert specs["software_cancel"].tool_args == ("software-update-cancel",)


def test_each_command_succeeds_and_a_mismatch_is_not_sent_again():
    cases = (
        ("climate_keeper_off", None, parked(climate_keeper="off"), ["climate-keeper", "off"]),
        ("climate_keeper_keep", None, parked(climate_keeper="keep"), ["climate-keeper", "keep"]),
        ("climate_keeper_dog", None, parked(climate_keeper="dog"), ["climate-keeper", "dog"]),
        ("climate_keeper_camp", None, parked(climate_keeper="camp"), ["climate-keeper", "camp"]),
        ("charge_port_open", None, parked(charge_port_open=True), ["charge-port-open"]),
        ("charge_port_close", None, parked(charge_port_open=False), ["charge-port-close"]),
        ("seat_cooler", ["front-left", "low"], parked(seat_cool={"front-left": 1}), ["seat-cooler", "front-left", "low"]),
        ("auto_seat_climate", ["L", "on"], parked(auto_seat_left=True), ["auto-seat-and-climate", "L", "on"]),
        ("auto_seat_climate", ["LR", "off"], parked(auto_seat_left=False, auto_seat_right=False), ["auto-seat-and-climate", "LR", "off"]),
        ("bioweapon_on", None, parked(bioweapon_on=True), ["bioweapon-mode", "on"]),
        ("bioweapon_off", None, parked(bioweapon_on=False), ["bioweapon-mode", "off"]),
        ("software_schedule", ["2h"], parked(software_update_status="scheduled"), ["software-update-start", "2h"]),
        ("software_cancel", None, parked(software_update_status="available"), ["software-update-cancel"]),
    )
    phrases = {
        "climate_keeper_keep": "I confirm climate keeper keep",
        "climate_keeper_dog": "I confirm climate keeper dog",
        "climate_keeper_camp": "I confirm climate keeper camp",
        "charge_port_open": "I confirm open charge port",
        "charge_port_close": "I confirm close charge port",
        "bioweapon_on": "I confirm turn bioweapon defense on",
        "software_schedule": "I confirm schedule software update",
        "software_cancel": "I confirm cancel software update",
    }
    for command, extra, after, args in cases:
        statement = phrases.get(command)
        adapter = Adapter(statement) if statement else Adapter()
        _, runner = execute(command, extra=extra, after=after, adapter=adapter)
        assert runner.calls[0]["args"] == args
        if statement:
            assert adapter.calls == 1
        else:
            assert adapter.calls == 0

    runner = Runner()
    with pytest.raises(VerificationFailed):
        execute("seat_cooler", extra=["front-right", "high"], after=parked(seat_cool={"front-right": 0}), runner=runner)
    with pytest.raises(VerificationFailed):
        execute("climate_keeper_dog", adapter=Adapter("I confirm climate keeper dog"), after=parked(climate_keeper="off"), runner=runner)
    with pytest.raises(VerificationFailed):
        execute("bioweapon_on", adapter=Adapter("I confirm turn bioweapon defense on"), after=parked(bioweapon_on=False), runner=runner)
    assert len(runner.calls) == 3


def test_confirmation_is_required_for_energy_and_sensitive_commands_only():
    runner = Runner()
    for command in (
        "climate_keeper_keep",
        "climate_keeper_dog",
        "climate_keeper_camp",
        "charge_port_open",
        "charge_port_close",
        "bioweapon_on",
        "software_schedule",
        "software_cancel",
    ):
        with pytest.raises(ConfirmationRequired):
            execute(command, adapter=Adapter("yes"), extra=["2h"] if command == "software_schedule" else None, runner=runner)
    assert runner.calls == []
    _, runner = execute("climate_keeper_off", after=parked(climate_keeper="off"))
    assert runner.calls[0]["args"] == ["climate-keeper", "off"]
    _, runner = execute("bioweapon_off", after=parked(bioweapon_on=False))
    assert runner.calls[0]["args"][0] == "bioweapon-mode"


def test_new_commands_require_park_and_a_schedule_cannot_confirm_them():
    runner = Runner()
    blocked = (
        "climate_keeper_dog",
        "charge_port_open",
        "bioweapon_on",
        "software_schedule",
        "seat_cooler",
    )
    for command in blocked:
        with pytest.raises(NotParked):
            execute(
                command,
                shift="D",
                speed=5,
                extra=["front-left", "low"] if command == "seat_cooler" else (["2h"] if command == "software_schedule" else None),
                adapter=Adapter("I confirm climate keeper dog"),
                runner=runner,
            )
    assert runner.calls == []
    for text in (
        "climate-keeper dog",
        "climate-keeper keep",
        "climate-keeper camp",
        "bioweapon on",
        "charge-port open",
        "charge-port close",
        "software schedule 2h",
        "software cancel",
    ):
        assert schedule_blocks(text) is True
    assert schedule_blocks("climate-keeper off") is False
    assert schedule_blocks("bioweapon off") is False
    assert schedule_blocks("software") is False
    assert schedule_blocks("software show") is False
    assert schedule_blocks("charging-history") is False
    with pytest.raises(ScheduleBlocked):
        execute(
            "climate_keeper_dog",
            via_schedule=True,
            adapter=Adapter("I confirm climate keeper dog"),
            after=parked(climate_keeper="dog"),
            runner=Runner(),
        )
    _, runner = execute("climate_keeper_off", via_schedule=True, after=parked(climate_keeper="off"))
    assert runner.calls[0]["args"] == ["climate-keeper", "off"]


def test_app_reports_unsupported_hardware_climate_off_and_a_plugged_cable(monkeypatch):
    monkeypatch.setattr("core.ops.sys.stdin.isatty", lambda: False)
    cooling = vehicle_payload(
        climate_state={"is_climate_on": True},
        vehicle_config={"has_seat_cooling": False},
    )
    http = Http(listed("online"), cooling)
    control = Control()
    with pytest.raises(ConfigError) as caught:
        make_app(http, control).command("seat-cool", preferred=None, argument=None, statement=None, extra=["front-left", "low"], sleep=lambda _s: None)
    assert "does not have seat cooling" in str(caught.value)
    assert control.calls == []

    climate_off = vehicle_payload(
        climate_state={"is_climate_on": False},
        vehicle_config={"has_seat_cooling": True},
    )
    control = Control()
    with pytest.raises(ConfigError) as caught:
        make_app(Http(listed("online"), climate_off), control).command(
            "seat-cool", preferred=None, argument=None, statement=None, extra=["front-left", "off"], sleep=lambda _s: None
        )
    assert "Climate is not on" in str(caught.value)
    assert control.calls == []

    control = Control()
    with pytest.raises(ConfigError) as caught:
        make_app(Http(listed("online"), vehicle_payload(climate_state={"is_climate_on": False})), control).command(
            "seat-climate", preferred=None, argument=None, statement=None, extra=["L", "on"], sleep=lambda _s: None
        )
    assert "Climate is not on" in str(caught.value)
    assert control.calls == []

    class Unsupported:
        def __init__(self):
            self.calls = []

        def run(self, *, vin, access_token, args, timeout=90):
            self.calls.append(args)
            raise CommandFailed("tesla-control failed (exit 1). The command was not sent again. not_supported")

    control = Unsupported()
    with pytest.raises(CommandFailed) as caught:
        make_app(Http(listed("online"), vehicle_payload()), control).command(
            "bioweapon-on",
            preferred=None,
            argument=None,
            statement="I confirm turn bioweapon defense on",
            sleep=lambda _s: None,
        )
    assert "does not support bioweapon defense mode" in str(caught.value)
    assert len(control.calls) == 1

    plugged = vehicle_payload(charge_state={"charging_state": "Charging", "charge_port_door_open": True, "charge_port_latch": "Engaged"})
    control = Control()
    with pytest.raises(ConfigError) as caught:
        make_app(Http(listed("online"), plugged), control).command(
            "charge-port-close",
            preferred=None,
            argument=None,
            statement="I confirm close charge port",
            sleep=lambda _s: None,
        )
    assert "cable is plugged in" in str(caught.value)
    assert control.calls == []


def test_app_sends_the_new_commands_and_names_them(monkeypatch):
    monkeypatch.setattr("core.ops.sys.stdin.isatty", lambda: False)
    body = vehicle_payload(
        climate_state={
            "is_climate_on": True,
            "climate_keeper_mode": "dog",
            "bioweapon_mode": False,
            "seat_fan_front_left": 1,
            "auto_seat_climate_left": True,
        },
        charge_state={"charging_state": "Disconnected", "charge_port_door_open": True, "charge_port_latch": "Disengaged"},
        vehicle_config={"has_seat_cooling": True},
        vehicle_state={"software_update": {"status": "scheduled", "version": "2026.8"}, "car_version": "2026.4"},
    )
    control = Control()
    app = make_app(Http(listed("online"), body), control)
    assert "climate-keeper dog" in app.command(
        "climate-keeper-dog",
        preferred=None,
        argument=None,
        statement="I confirm climate keeper dog",
        sleep=lambda _s: None,
    )
    assert control.calls[-1]["args"] == ["climate-keeper", "dog"]
    text = app.command(
        "charge-port-open",
        preferred=None,
        argument=None,
        statement="I confirm open charge port",
        sleep=lambda _s: None,
    )
    assert text == "OK: charge-port open"
    assert control.calls[-1]["args"] == ["charge-port-open"]
    assert "seat-cool front-left low" in app.command(
        "seat-cool", preferred=None, argument=None, statement=None, extra=["front-left", "low"], sleep=lambda _s: None
    )
    assert control.calls[-1]["args"] == ["seat-cooler", "front-left", "low"]
    assert "seat-climate front-left on" in app.command(
        "seat-climate", preferred=None, argument=None, statement=None, extra=["L", "on"], sleep=lambda _s: None
    )
    closed = vehicle_payload(charge_state={"charging_state": "Disconnected", "charge_port_door_open": False})
    app = make_app(Http(listed("online"), closed), control)
    assert app.command(
        "charge-port-close",
        preferred=None,
        argument=None,
        statement="I confirm close charge port",
        sleep=lambda _s: None,
    ) == "OK: charge-port close"
    off = vehicle_payload(climate_state={"bioweapon_mode": False})
    app = make_app(Http(listed("online"), off), control)
    assert app.command("bioweapon-off", preferred=None, argument=None, statement=None, sleep=lambda _s: None) == "OK: bioweapon off"
    on = vehicle_payload(climate_state={"bioweapon_mode": True})
    app = make_app(Http(listed("online"), on), control)
    assert "bioweapon on" in app.command(
        "bioweapon-on",
        preferred=None,
        argument=None,
        statement="I confirm turn bioweapon defense on",
        sleep=lambda _s: None,
    )
    scheduled = vehicle_payload(vehicle_state={"software_update": {"status": "scheduled", "version": "2026.8"}})
    app = make_app(Http(listed("online"), scheduled), control)
    assert app.command(
        "software-schedule",
        preferred=None,
        argument=None,
        statement="I confirm schedule software update",
        extra=["2h"],
        sleep=lambda _s: None,
    ) == "OK: software schedule 2h"
    assert control.calls[-1]["args"] == ["software-update-start", "2h"]
    cancelled = vehicle_payload(vehicle_state={"software_update": {"status": "available", "version": "2026.8"}})
    app = make_app(Http(listed("online"), cancelled), control)
    assert app.command(
        "software-cancel",
        preferred=None,
        argument=None,
        statement="I confirm cancel software update",
        sleep=lambda _s: None,
    ) == "OK: software cancel"


def test_presence_parsing_and_check_report_lines():
    present = from_vehicle_data({"response": {"vehicle_state": {"is_user_present": True}}})
    empty = from_vehicle_data({"response": {"vehicle_state": {"is_user_present": False}}})
    missing = from_vehicle_data({"response": {"vehicle_state": {"locked": True}}})
    assert present.driver_present is True
    assert empty.driver_present is False
    assert missing.driver_present is None
    assert driver_seat_text(True) == "someone appears to be in the driver seat"
    assert driver_seat_text(False) == "no one detected in the driver seat"
    assert climate_keeper_name("on") == "keep"
    assert climate_keeper_name(2) == "dog"
    assert climate_keeper_name("camp") == "camp"

    http = Http(listed("online"), vehicle_payload(vehicle_state={"is_user_present": True, "locked": False}))
    report = make_app(http).check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
    from core.ops import format_check
    from core.fleet.report import vehicle_report

    text = format_check(report)
    assert "someone appears to be in the driver seat" in text
    assert "unlocked" not in text
    briefing = vehicle_report("Example", from_vehicle_data(vehicle_payload(vehicle_state={"is_user_present": False})))
    assert "no one detected in the driver seat" in briefing.text
    assert briefing.fields["driver_present"] is False


def test_unlocked_alert_is_suppressed_when_the_driver_seat_is_occupied():
    occupied = parked(locked=False, driver_present=True)
    empty = parked(locked=False, driver_present=False)
    unknown = parked(locked=False, driver_present=None)
    assert "unlocked" not in [item.code for item in evaluate_alerts(occupied)]
    assert "unlocked" in [item.code for item in evaluate_alerts(empty)]
    assert "unlocked" in [item.code for item in evaluate_alerts(unknown)]


def test_energy_mode_alerts_are_hourly_unless_the_driver_seat_is_occupied(tmp_path):
    occupied = parked(driver_present=True, climate_on=True, climate_keeper="dog", bioweapon_on=True, locked=False)
    assert evaluate_alerts(occupied) == []
    assert active_energy_modes(occupied) == ["energy:keeper:dog", "energy:bioweapon", "energy:climate"]

    home = tmp_path / "daily"
    first = apply_rising_edge(home, _report(parked(driver_present=False, climate_on=True, climate_keeper="off")), now=T0)
    assert [item.code for item in first.alerts] == ["energy:climate"]
    assert first.exit_code == 1
    quiet = apply_rising_edge(home, _report(parked(driver_present=False, climate_on=True)), now=T0 + timedelta(minutes=30))
    assert quiet.alerts == []
    assert quiet.exit_code == 0
    seated = apply_rising_edge(
        home,
        _report(parked(driver_present=True, climate_on=True, locked=False)),
        now=T0 + timedelta(minutes=40),
    )
    assert seated.alerts == []
    still = apply_rising_edge(home, _report(parked(driver_present=False, climate_on=True)), now=T0 + timedelta(minutes=50))
    assert still.alerts == []
    again = apply_rising_edge(home, _report(parked(driver_present=False, climate_on=True)), now=T0 + timedelta(hours=1))
    assert [item.code for item in again.alerts] == ["energy:climate"]
    cleared = apply_rising_edge(home, _report(parked(driver_present=False, climate_on=False)), now=T0 + timedelta(hours=1, minutes=5))
    assert cleared.alerts == []
    saved = json.loads((home / CHECK_ALERTS_NAME).read_text(encoding="utf-8"))
    assert "energy:climate" not in saved.get("energy_notified", {})
    returned = apply_rising_edge(home, _report(parked(driver_present=False, climate_on=True)), now=T0 + timedelta(hours=1, minutes=6))
    assert [item.code for item in returned.alerts] == ["energy:climate"]

    other = tmp_path / "weekend"
    dog = apply_rising_edge(other, _report(parked(driver_present=False, climate_keeper="dog", climate_on=True, bioweapon_on=True)), now=T0)
    assert [item.code for item in dog.alerts] == ["energy:keeper:dog", "energy:bioweapon", "energy:climate"]
    for mode in ("keep", "camp"):
        codes = [item.code for item in evaluate_alerts(parked(driver_present=False, climate_keeper=mode))]
        assert f"energy:keeper:{mode}" in codes
    low = apply_rising_edge(tmp_path / "plain", _report(parked(battery_level=10, charging_state="Disconnected", sentry_on=True)), now=T0)
    assert [item.code for item in low.alerts] == ["low_battery"]
    again_low = apply_rising_edge(
        tmp_path / "plain",
        _report(parked(battery_level=10, charging_state="Disconnected", sentry_on=True)),
        now=T0 + timedelta(hours=2),
    )
    assert again_low.alerts == []


def test_official_records_do_not_wake_or_keep_coordinates(tmp_path):
    class Records:
        def __init__(self):
            self.calls = []
            self.pdf = b"%PDF-1.4 sample"

        def request_json(self, method, url, headers, timeout=30, body=None):
            self.calls.append(url)
            if "charging/history" in url:
                return 200, {
                    "response": {
                        "data": [
                            {
                                "vin": VIN,
                                "siteLocationName": "Sample Supercharger",
                                "chargeStartDateTime": "2026-07-27T11:43:45-07:00",
                                "siteEntryLocation": {"latitude": 12.345678, "longitude": 98.765432},
                                "fees": [
                                    {
                                        "feeType": "CHARGING",
                                        "currencyCode": "USD",
                                        "usageBase": 40,
                                        "uom": "kwh",
                                        "totalDue": 18.4,
                                    }
                                ],
                                "invoices": [{"contentId": "abc-123-efg", "fileName": "secret.pdf"}],
                            }
                        ]
                    }
                }
            if url.endswith("/wake_up"):
                raise AssertionError("charging history woke the car")
            return 500, {}

        def request_bytes(self, method, url, headers, timeout=60):
            self.calls.append(url)
            assert "charging/invoice/abc-123-efg" in url
            return 200, self.pdf

    http = Records()
    app = make_app(http, home=tmp_path)
    text = app.charging_history()
    assert "Home charging is not included." in text
    assert "2026-07-27T11:43:45-07:00" in text
    assert "Sample Supercharger" in text
    assert "40 kwh" in text
    assert "USD 18.4" in text
    assert "abc-123-efg" in text
    assert VIN not in text
    assert "12.345678" not in text
    assert "latitude" not in text
    assert not any("wake_up" in url or url.rstrip("/").endswith("/vehicles") for url in http.calls)
    rendered = json.loads(app.charging_history(as_json=True))
    assert rendered["records"][0]["site"] == "Sample Supercharger"
    assert "vin" not in json.dumps(rendered)

    skipped = app.charging_invoice("abc-123-efg", save=False)
    assert "was not saved" in skipped
    assert not (tmp_path / "invoices").exists()
    saved = app.charging_invoice("abc-123-efg", save=True)
    path = tmp_path / "invoices" / "abc-123-efg.pdf"
    assert path.is_file()
    assert path.read_bytes().startswith(b"%PDF")
    assert str(path) in saved
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ConfigError):
        parse_invoice_id("TESTVN00000000001")

    asleep = Http(listed("asleep"))
    software = make_app(asleep, home=tmp_path).software(preferred=None)
    assert "asleep" in software
    assert "not woken" in software
    assert not any("wake_up" in url or "vehicle_data" in url or "release_notes" in url for url in urls(asleep))


def test_software_read_uses_release_notes_only_when_online():
    class Notes:
        def __init__(self):
            self.calls = []

        def request_json(self, method, url, headers, timeout=30, body=None):
            self.calls.append(url)
            if url.rstrip("/").endswith("/vehicles"):
                return 200, listed("online")
            if "vehicle_data" in url:
                return 200, vehicle_payload(
                    vehicle_state={
                        "car_version": "2026.4.1",
                        "software_update": {"status": "available", "version": "2026.8.2"},
                    }
                )
            if "release_notes" in url:
                assert "staged=true" in url
                return 200, {
                    "response": {
                        "release_notes": [
                            {
                                "title": "Minor fixes",
                                "description": "This release contains minor fixes and improvements.",
                                "customer_version": "2026.8.2",
                                "image_url": "https://vehicle-files.example/secret",
                            }
                        ]
                    }
                }
            if url.endswith("/wake_up"):
                raise AssertionError("software read woke the car")
            return 500, {}

    http = Notes()
    text = make_app(http).software(preferred=None)
    assert "2026.4.1" in text
    assert "available" in text
    assert "2026.8.2" in text
    assert "Minor fixes" in text
    assert "minor fixes and improvements" in text
    assert "image_url" not in text
    assert "vehicle-files" not in text
    assert not any(url.endswith("/wake_up") for url in http.calls)


def test_family_sensitive_commands_wait_for_the_owner(tmp_path, monkeypatch, capsys):
    from core.family import expand_action
    from core.garage import remember_car

    http = CarsHttp([row(VIN_A)], cars_payload())
    control = Control()
    app = cars_app(tmp_path, http, control)
    remember_car(tmp_path, http.rows, "0001", "daily")
    monkeypatch.setattr("core.cli.build_app", lambda home: app)
    assert main(["--home", str(tmp_path), "family", "add", "rider"]) == 0
    assert main(["--home", str(tmp_path), "family", "allow", "rider", "climate-keeper-dog"]) == 0
    assert classify(tmp_path, "rider", "climate-keeper-dog") == "request"
    assert classify(tmp_path, "rider", "bioweapon-on") == "deny"
    capsys.readouterr()
    assert main(
        [
            "--home",
            str(tmp_path),
            "--as",
            "rider",
            "--car",
            "daily",
            "climate-keeper",
            "dog",
            "--confirm",
            "I confirm climate keeper dog on daily",
        ]
    ) == 0
    relay = capsys.readouterr().out
    assert "Pending request" in relay
    assert "climate-keeper-dog" in relay
    assert "This action was not run." in relay
    assert control.calls == []
    for action, kind in (
        ("charge-port-open", "request"),
        ("charge-port-close", "request"),
        ("software-schedule", "request"),
        ("software-cancel", "request"),
        ("bioweapon-on", "request"),
        ("climate-keeper-off", "direct"),
        ("seat-cool", "direct"),
        ("charging-history", "direct"),
        ("software", "direct"),
    ):
        assert expand_action(action) == [(action, kind)]


def test_parsers_reject_unsupported_seat_and_delay_values():
    assert parse_seat_cool("Front-Left", "HIGH") == ("front-left", "high")
    assert parse_seat_climate("both", "OFF") == ("LR", "off")
    assert parse_update_delay("2h") == "2h"
    assert parse_update_delay("now") == "0s"
    assert parse_update_delay("90") == "90s"
    with pytest.raises(ConfigError):
        parse_seat_cool("2nd-row-left", "low")
    with pytest.raises(ConfigError):
        parse_seat_climate("front-center", "on")
    with pytest.raises(ConfigError):
        parse_update_delay("later")


def test_phase8_patch_inserts_the_sdk_commands_once(tmp_path):
    ensure = _patch_module().ensure_phase8_commands
    target = tmp_path / "commands.go"
    target.write_text('package main\nvar commands = map[string]*Command{\n\t"product-info": {\n\t},\n}\n', encoding="utf-8")
    assert ensure(target) is True
    text = target.read_text(encoding="utf-8")
    assert text.count('"climate-keeper":') == 1
    assert text.count('"seat-cooler":') == 1
    assert text.count('"bioweapon-mode":') == 1
    assert "SetClimateKeeperMode(ctx, mode, false)" in text
    assert "SetSeatCooler(ctx, level, position)" in text
    assert "SetBioweaponDefenseMode(ctx, state, true)" in text
    assert ensure(target) is False
    install = (ROOT / "setup" / "install.sh").read_text(encoding="utf-8")
    assert "patch_tesla_control_phase8.py" in install
    rows = history_rows(
        {"response": {"data": [{"vin": VIN, "siteLocationName": "Sample Supercharger", "chargeStartDateTime": "2026-01-01T00:00:00Z"}]}}
    )
    rendered = format_history(rows, as_json=False)
    assert VIN not in rendered
    assert cable_plugged(parked(charging_state="Charging")) is True
    assert cable_plugged(parked(charging_state="Disconnected", charge_port_latch="Disengaged")) is False


def test_docs_and_skill_describe_the_new_commands_and_the_driver_seat_limit():
    queries = (ROOT / "docs" / "06-queries-and-commands.md").read_text(encoding="utf-8")
    skill = (ROOT / ".agents" / "skills" / "agentdrive" / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for text in (queries, skill):
        assert "climate-keeper" in text
        assert "I confirm climate keeper dog" in text
        assert "I confirm climate keeper keep" in text
        assert "I confirm open charge port" in text
        assert "I confirm close charge port" in text
        assert "I confirm schedule software update" in text
        assert "I confirm cancel software update" in text
        assert "I confirm turn bioweapon defense on" in text
        assert "Home charging is not included" in text
        assert "once an hour" in text or "once per hour" in text
        assert "driver seat" in text.lower()
        assert "not a guarantee" in text.lower()
        assert "pet" in text.lower()
    assert "climate keeper" in readme.lower()
    assert "charge port" in readme.lower()
    assert "## Next" not in readme
    assert "may wake" not in readme.lower()
    assert "cloudflare" not in readme.lower()
    assert "cloudflare" not in queries.lower()
