"""Charging current and departure preconditioning."""

import json
from pathlib import Path

import pytest

from adapters.template import TemplateAdapter
from core.cli import build_parser, main
from core.errors import ConfigError, ConfirmationRequired, NotParked, ScheduleBlocked, VerificationFailed
from core.fleet.departure import (
    format_departure_report,
    format_departure_report_json,
    parse_departure_write,
    schedules_from_vehicle_data,
)
from core.fleet.snapshot import from_vehicle_data
from core.ops import CHARGE_AMPS_MAX, CHARGE_AMPS_MIN, PUBLIC_COMMANDS, format_status, parse_amps
from core.safety.catalog import load_specs, schedule_blocks
from tests.test_refresh_and_check import VIN, Control, Http, listed, make_app, urls, vehicle_payload
from tests.test_safety import Adapter, execute, parked

ROOT = Path(__file__).resolve().parents[1]


def _row(**overrides):
    body = {
        "id": 1700000000,
        "days_of_week": 62,
        "enabled": True,
        "precondition_time": 7 * 60 + 30,
        "one_time": False,
        "latitude": 1,
        "longitude": 2,
    }
    body.update(overrides)
    return body


def _payload(*rows, section=True, as_list=True, **parts):
    payload = vehicle_payload(**parts)
    if section:
        block = {}
        if as_list:
            block["precondition_schedules"] = list(rows)
        payload["response"]["preconditioning_schedule_data"] = block
    return payload


def _with_departure(**overrides):
    seen = parked(charge_current_request=16)
    parsed = from_vehicle_data(_payload(_row(**overrides)))
    seen.departure_schedules = parsed.departure_schedules
    seen.departure_schedule_section_present = True
    seen.departure_schedule_list_present = True
    return seen


def test_amps_range_is_a_whole_number_from_1_to_80():
    assert CHARGE_AMPS_MIN == 1
    assert CHARGE_AMPS_MAX == 80
    assert parse_amps("1") == "1"
    assert parse_amps("80") == "80"
    assert parse_amps("08") == "8"
    for bad in ("0", "81", "00", "-1", "16.5", "16A", "", "  ", "+16"):
        with pytest.raises(ConfigError):
            parse_amps(bad)


def test_charge_amps_uses_the_upstream_signed_command():
    spec = load_specs()["charging_set_amps"]
    assert spec.tier == "sensitive"
    assert spec.tool_args == ("charging-set-amps",)
    assert spec.action == "charging set amps"
    assert PUBLIC_COMMANDS["charge-amps"] == "charging_set_amps"
    assert "set-charging-amps" not in spec.tool_args
    assert "charging-set-current" not in spec.tool_args


def test_charge_amps_sends_once_after_the_named_confirmation():
    http = Http(listed("online"), vehicle_payload(charge_state={"charge_current_request": 16, "charge_amps": 0}))
    control = Control()
    text = make_app(http, control).command(
        "charge-amps",
        preferred=None,
        argument="16",
        statement="I confirm charging set amps",
        sleep=lambda _seconds: None,
    )
    assert text.startswith("OK: charge-amps 16")
    assert "may clamp" in text
    assert control.calls[0]["args"] == ["charging-set-amps", "16"]
    assert len(control.calls) == 1
    assert control.calls[0]["token"] not in control.calls[0]["args"]
    assert VIN not in text


def test_charge_amps_accepts_a_charger_clamp_and_names_it():
    after = parked(charge_current_request=16, charge_current_request_max=16, charge_amps=0)
    _, runner = execute(
        "charging_set_amps",
        adapter=Adapter("I confirm charging set amps"),
        extra=["32"],
        after=after,
    )
    assert runner.calls[0]["args"] == ["charging-set-amps", "32"]
    http = Http(
        listed("online"),
        vehicle_payload(charge_state={"charge_current_request": 16, "charge_current_request_max": 16, "charge_amps": 0}),
    )
    control = Control()
    text = make_app(http, control).command(
        "charge-amps",
        preferred=None,
        argument="32",
        statement="I confirm charging set amps",
        sleep=lambda _seconds: None,
    )
    assert "The car kept 16 A" in text
    assert "clamps" in text
    assert len(control.calls) == 1


def test_a_lower_reading_without_the_charger_max_is_not_sent_again():
    runner_calls = []

    class Runner:
        def run(self, *, vin, access_token, args, timeout=90):
            runner_calls.append(args)
            from core.fleet.commands import CommandResult

            return CommandResult(0, "")

    with pytest.raises(VerificationFailed):
        execute(
            "charging_set_amps",
            adapter=Adapter("I confirm charging set amps"),
            extra=["32"],
            after=parked(charge_current_request=16),
            runner=Runner(),
        )
    assert len(runner_calls) == 1


def test_charge_amps_requires_confirmation_and_park_before_the_tool_runs(monkeypatch):
    monkeypatch.setattr("core.ops.sys.stdin.isatty", lambda: False)
    http = Http(listed("online"), vehicle_payload(charge_state={"charge_current_request": 16}))
    control = Control()
    with pytest.raises(ConfirmationRequired) as caught:
        make_app(http, control).command(
            "charge-amps",
            preferred=None,
            argument="16",
            statement=None,
            sleep=lambda _seconds: None,
        )
    assert "charging set amps" in str(caught.value)
    assert control.calls == []

    driving = Http(listed("online"), vehicle_payload(drive_state={"shift_state": "D", "speed": 10}))
    with pytest.raises(NotParked):
        make_app(driving, Control()).command(
            "charge-amps",
            preferred=None,
            argument="16",
            statement="I confirm charging set amps",
            sleep=lambda _seconds: None,
        )
    with pytest.raises(ConfirmationRequired):
        execute(
            "charging_set_amps",
            adapter=Adapter("I confirm unlock"),
            extra=["16"],
            after=parked(charge_current_request=16),
        )
    with pytest.raises(ScheduleBlocked):
        execute(
            "charging_set_amps",
            adapter=Adapter("I confirm charging set amps"),
            via_schedule=True,
            extra=["16"],
            after=parked(charge_current_request=16),
        )


def test_departure_tool_args_match_precondition_schedule_add():
    spec = load_specs()["departure_schedule_set"]
    assert spec.tier == "sensitive"
    assert spec.tool_args == ("precondition-schedule-add",)
    assert spec.action == "departure schedule set"
    assert load_specs()["departure_schedule_remove"].tool_args == ("precondition-schedule-remove",)
    assert load_specs()["departure_schedule_remove"].action == "departure schedule remove"
    weekly = parse_departure_write("Monday, Wednesday", "7:30", "1.0", "-2.50", once=False, enabled=True)
    assert weekly.days == "Mon,Wed"
    assert weekly.ready_time == "07:30"
    assert weekly.ready_minutes == 450
    assert weekly.tool_args() == ["precondition-schedule-add", "Mon,Wed", "07:30", "1", "-2.5"]
    once = parse_departure_write("all", "08:00", "0", "0", once=True, enabled=True)
    assert once.tool_args() == ["precondition-schedule-add", "all", "08:00", "0", "0", "once"]
    changed = parse_departure_write("weekdays", "07:30", "1", "2", once=False, enabled=True, schedule_id="1700000000")
    assert changed.tool_args() == ["precondition-schedule-add", "weekdays", "07:30", "1", "2", "weekly", "1700000000"]
    off = parse_departure_write("Sun,Sat", "20:32", "1", "2", once=True, enabled=False, schedule_id="1700000000")
    assert off.tool_args() == [
        "precondition-schedule-add",
        "Sun,Sat",
        "20:32",
        "1",
        "2",
        "once",
        "1700000000",
        "false",
    ]
    assert "set_scheduled_departure" not in off.tool_args()
    assert "scheduled-departure" not in off.tool_args()
    assert "off-peak" not in " ".join(off.tool_args())
    with pytest.raises(ConfigError):
        parse_departure_write("weekdays", "07:30", "1", "2", once=False, enabled=False)


def test_departure_coordinates_are_optional_and_checked():
    omitted = parse_departure_write("weekdays", "07:30", once=False, enabled=True)
    assert omitted.latitude is None
    assert omitted.longitude is None
    with pytest.raises(ConfigError):
        omitted.tool_args()
    filled = omitted.with_location(12.25, -3.5)
    assert filled.tool_args()[:5] == ["precondition-schedule-add", "weekdays", "07:30", "12.25", "-3.5"]
    assert parse_departure_write("weekdays", "07:30", "90", "180", once=False, enabled=True).latitude == "90"
    for days, ready, lat, lon in (
        ("weekdays", "07:30", "91", "2"),
        ("weekdays", "07:30", "-90.1", "2"),
        ("weekdays", "07:30", "1", "181"),
        ("weekdays", "07:30", "1", None),
        ("weekdays", "07:30", None, "2"),
    ):
        with pytest.raises(ConfigError):
            parse_departure_write(days, ready, lat, lon, once=False, enabled=True)


def test_omitted_coordinates_use_the_car_after_the_write_wake():
    row = _row(latitude=12.25, longitude=-3.5)
    payload = _payload(row, drive_state={"latitude": 12.25, "longitude": -3.5})
    http = Http(listed("online"), payload)
    control = Control()
    spec = parse_departure_write("weekdays", "07:30", once=False, enabled=True)
    text = make_app(http, control).departure_set(
        spec,
        preferred=None,
        statement="I confirm departure schedule set",
        sleep=lambda _seconds: None,
    )
    assert text == "OK: departure set id 1700000000"
    assert control.calls[0]["args"][:5] == ["precondition-schedule-add", "weekdays", "07:30", "12.25", "-3.5"]
    data_urls = [url for url in urls(http) if "vehicle_data" in url]
    assert sum("location_data" in url for url in data_urls) == 1
    assert not any(url.endswith("/wake_up") for url in urls(http))

    asleep = Http(listed("asleep"), payload, polls=[{"response": {"state": "online"}}])
    control = Control()
    text = make_app(asleep, control).departure_set(
        spec,
        preferred=None,
        statement="I confirm departure schedule set",
        sleep=lambda _seconds: None,
    )
    assert text == "OK: departure set id 1700000000"
    assert sum(1 for url in urls(asleep) if url.endswith("/wake_up")) == 1
    assert control.calls[0]["args"][3:5] == ["12.25", "-3.5"]


def test_a_location_read_that_says_asleep_is_not_woken_again():
    class LocationAsleep(Http):
        def request_json(self, method, url, headers, timeout=30, body=None):
            if "vehicle_data" in url and "location_data" in url:
                self.calls.append((method, url, body))
                return 408, {"error": "asleep"}
            return super().request_json(method, url, headers, timeout=timeout, body=body)

    online = LocationAsleep(listed("online"), vehicle_payload())
    spec = parse_departure_write("weekdays", "07:30", once=False, enabled=True)
    with pytest.raises(ConfigError) as caught:
        make_app(online, Control()).departure_set(
            spec,
            preferred=None,
            statement="I confirm departure schedule set",
            sleep=lambda _seconds: None,
        )
    assert "must be online" in str(caught.value)
    assert not any(url.endswith("/wake_up") for url in urls(online))

    asleep = LocationAsleep(listed("asleep"), vehicle_payload(), polls=[{"response": {"state": "online"}}])
    with pytest.raises(ConfigError) as caught:
        make_app(asleep, Control()).departure_set(
            spec,
            preferred=None,
            statement="I confirm departure schedule set",
            sleep=lambda _seconds: None,
        )
    assert "must be online" in str(caught.value)
    assert sum(1 for url in urls(asleep) if url.endswith("/wake_up")) == 1

    missing = Http(listed("online"), vehicle_payload())
    with pytest.raises(ConfigError) as caught:
        make_app(missing, Control()).departure_set(
            spec,
            preferred=None,
            statement="I confirm departure schedule set",
            sleep=lambda _seconds: None,
        )
    assert "did not return a location" in str(caught.value)
    assert not any(url.endswith("/wake_up") for url in urls(missing))


def test_explicit_coordinates_do_not_read_location_data():
    http = Http(listed("online"), _payload(_row()))
    control = Control()
    spec = parse_departure_write("weekdays", "07:30", "1", "2", once=False, enabled=True)
    text = make_app(http, control).departure_set(
        spec,
        preferred=None,
        statement="I confirm departure schedule set",
        sleep=lambda _seconds: None,
    )
    assert text == "OK: departure set id 1700000000"
    assert "location_data" not in "".join(urls(http))


def test_rejected_departure_shapes():
    for days, ready, lat, lon in (
        ("marketday", "07:30", "1", "2"),
        ("weekdays", "7", "1", "2"),
        ("weekdays", "24:00", "1", "2"),
        ("weekdays", "07:60", "1", "2"),
        ("weekdays", "07:30-08:00", "1", "2"),
        ("weekdays", "07:30", "181", "2"),
    ):
        with pytest.raises(ConfigError):
            parse_departure_write(days, ready, lat, lon, once=False, enabled=True)


def test_show_skips_a_sleeping_car_without_a_wake_or_a_data_read():
    for state in ("asleep", "offline", ""):
        http = Http(listed(state))
        control = Control()
        report = make_app(http, control).departure_show(preferred=None)
        text = format_departure_report(report)
        assert "skipped: the vehicle is not online, so it was not woken" in text
        assert report.skipped is True
        assert control.calls == []
        assert not any("vehicle_data" in url or "wake_up" in url for url in urls(http))
        body = json.loads(format_departure_report_json(report))
        assert body["schedules"] == []
        assert VIN not in text
        assert VIN not in json.dumps(body)


def test_show_reads_only_preconditioning_schedule_data_when_the_car_is_online():
    http = Http(listed("online"), _payload(_row(name="Home")))
    report = make_app(http, Control()).departure_show(preferred=None)
    text = format_departure_report(report)
    assert "id 1700000000 Home: enabled, weekdays, ready by 07:30, weekly, lat 1 lon 2" in text
    data_urls = [url for url in urls(http) if "vehicle_data" in url]
    assert len(data_urls) == 1
    assert "endpoints=preconditioning_schedule_data" in data_urls[0]
    assert "location_data" not in data_urls[0]
    assert "charge_state" not in data_urls[0]
    assert "wake_up" not in "".join(urls(http))
    _, _, alias = schedules_from_vehicle_data(
        {
            "response": {
                "preconditioning_schedule_data": {
                    "precondition_schedules": [
                        {
                            "id": "5",
                            "days_of_week": "weekdays",
                            "lat": 3,
                            "lon": 4,
                            "enabled": True,
                            "precondition_time": "450",
                        }
                    ]
                }
            }
        }
    )
    assert alias[0].id == 5
    assert alias[0].days_of_week == 62
    assert alias[0].latitude == 3
    assert alias[0].longitude == 4
    assert alias[0].precondition_time == 450


def test_show_reports_a_missing_section_and_an_empty_list():
    missing = make_app(Http(listed("online"), vehicle_payload()), Control()).departure_show(preferred=None)
    assert format_departure_report(missing) == "state: online\npreconditioning_schedule_data was not in the response"
    empty = make_app(Http(listed("online"), _payload()), Control()).departure_show(preferred=None)
    assert format_departure_report(empty) == "state: online\nno departure schedules"
    bare = make_app(Http(listed("online"), _payload(as_list=False)), Control()).departure_show(preferred=None)
    assert "did not include precondition_schedules" in format_departure_report(bare)


def test_status_does_not_print_departure_coordinates():
    payload = _payload(_row())
    payload["response"]["drive_state"]["latitude"] = 9.5
    payload["response"]["drive_state"]["longitude"] = 8.5
    observation = from_vehicle_data(payload)
    assert not hasattr(observation, "latitude")
    text = format_status({"display_name": "Example", "state": "online"}, observation, VIN)
    assert "9.5" not in text
    assert "8.5" not in text
    assert "07:30" not in text
    assert "1 lon 2" not in text


def test_set_sends_one_signed_add_and_reads_the_schedules_back():
    http = Http(listed("online"), _payload(_row()))
    control = Control()
    spec = parse_departure_write("weekdays", "07:30", "1", "2", once=False, enabled=True)
    text = make_app(http, control).departure_set(
        spec,
        preferred=None,
        statement="I confirm departure schedule set",
        sleep=lambda _seconds: None,
    )
    assert text == "OK: departure set id 1700000000"
    assert control.calls[0]["args"] == ["precondition-schedule-add", "weekdays", "07:30", "1", "2"]
    assert len(control.calls) == 1
    data_urls = [url for url in urls(http) if "vehicle_data" in url]
    assert "preconditioning_schedule_data" not in data_urls[0]
    assert "preconditioning_schedule_data" in data_urls[-1]
    assert not any(url.endswith("/wake_up") for url in urls(http))


def test_set_wakes_a_sleeping_car_once_and_show_does_not():
    http = Http(
        listed("asleep"),
        _payload(_row()),
        polls=[{"response": {"state": "online"}}],
    )
    control = Control()
    spec = parse_departure_write("weekdays", "07:30", "1", "2", once=False, enabled=True)
    text = make_app(http, control).departure_set(
        spec,
        preferred=None,
        statement="I confirm departure schedule set",
        sleep=lambda _seconds: None,
    )
    assert text == "OK: departure set id 1700000000"
    assert sum(1 for url in urls(http) if url.endswith("/wake_up")) == 1
    assert control.calls[0]["args"][0] == "precondition-schedule-add"

    asleep = Http(listed("asleep"))
    show = make_app(asleep, Control()).departure_show(preferred=None)
    assert show.skipped is True
    assert not any("wake_up" in url or "vehicle_data" in url for url in urls(asleep))


def test_set_and_remove_require_confirmation_and_park(monkeypatch):
    monkeypatch.setattr("core.ops.sys.stdin.isatty", lambda: False)
    spec = parse_departure_write("weekdays", "07:30", "1", "2", once=False, enabled=True)
    http = Http(listed("online"), _payload(_row()))
    control = Control()
    with pytest.raises(ConfirmationRequired) as caught:
        make_app(http, control).departure_set(spec, preferred=None, statement=None, sleep=lambda _seconds: None)
    assert "departure schedule set" in str(caught.value)
    assert control.calls == []

    driving = Http(listed("online"), vehicle_payload(drive_state={"shift_state": "D", "speed": 4}))
    with pytest.raises(NotParked):
        make_app(driving, Control()).departure_set(
            spec,
            preferred=None,
            statement="I confirm departure schedule set",
            sleep=lambda _seconds: None,
        )
    with pytest.raises(ConfirmationRequired):
        execute(
            "departure_schedule_set",
            adapter=Adapter("I confirm unlock"),
            extra=spec.tool_args()[1:],
            after=_with_departure(),
        )
    with pytest.raises(NotParked):
        execute(
            "departure_schedule_remove",
            shift="D",
            speed=0,
            adapter=Adapter("I confirm departure schedule remove"),
            extra=["id", "1700000000"],
            after=parked(),
        )


def test_remove_sends_the_id_and_checks_it_is_gone():
    http = Http(listed("online"), _payload())
    control = Control()
    text = make_app(http, control).departure_remove(
        "1700000000",
        preferred=None,
        statement="I confirm departure schedule remove",
        sleep=lambda _seconds: None,
    )
    assert text == "OK: departure remove 1700000000"
    assert control.calls[0]["args"] == ["precondition-schedule-remove", "id", "1700000000"]
    assert len(control.calls) == 1

    still = Http(listed("online"), _payload(_row()))
    control = Control()
    with pytest.raises(VerificationFailed):
        make_app(still, control).departure_remove(
            "1700000000",
            preferred=None,
            statement="I confirm departure schedule remove",
            sleep=lambda _seconds: None,
        )
    assert len(control.calls) == 1


def test_a_mismatched_departure_is_not_sent_again():
    http = Http(listed("online"), _payload(_row(precondition_time=60)))
    control = Control()
    spec = parse_departure_write("weekdays", "07:30", "1", "2", once=False, enabled=True)
    with pytest.raises(VerificationFailed):
        make_app(http, control).departure_set(
            spec,
            preferred=None,
            statement="I confirm departure schedule set",
            sleep=lambda _seconds: None,
        )
    assert len(control.calls) == 1


def test_a_schedule_cannot_store_charge_amps_or_a_departure_change():
    blocked = (
        "charge-amps",
        "charge-amps 16",
        "charge amps",
        "charging_set_amps",
        "charging-set-amps",
        "charging-set-amps 16",
        "departure",
        "departure set",
        "departure set weekdays 07:30 1 2",
        "departure remove",
        "departure remove 1700000000",
        "departure_schedule_set",
        "departure_schedule_remove",
        "departure-schedule set",
        "precondition-schedule-add",
        "precondition-schedule-remove",
        "precondition schedule add",
    )
    for command in blocked:
        assert schedule_blocks(command) is True
        with pytest.raises(ScheduleBlocked):
            TemplateAdapter(lambda challenge: "").schedule(command, "07:00", {"command": command})
    adapter = TemplateAdapter(lambda challenge: "")
    adapter.schedule("read", "07:00", {"command": "departure show"})
    adapter.schedule("cabin", "07:00", {"command": "precondition"})
    adapter.schedule("health-check", "every 30 minutes", {"command": "check", "json": True})
    adapter.schedule("morning", "07:30", {"command": "history due"})
    stored = [item["task"]["command"] for item in adapter.schedules]
    assert stored == ["departure show", "precondition", "check", "history due"]
    with pytest.raises(ScheduleBlocked):
        execute(
            "departure_schedule_set",
            adapter=Adapter("I confirm departure schedule set"),
            via_schedule=True,
            extra=["weekdays", "07:30", "1", "2"],
            after=_with_departure(),
        )


def test_check_report_where_and_history_still_do_not_wake(tmp_path, monkeypatch, capsys):
    asleep = Http(listed("asleep"))
    app = make_app(asleep)
    assert app.check(preferred=None, tyre_bar=2.5, low_battery_percent=30).skipped is True
    assert app.report(preferred=None).skipped is True
    assert app.where(preferred=None).kind == "skipped"
    assert app.departure_show(preferred=None).skipped is True
    assert not any("wake_up" in url or "vehicle_data" in url for url in urls(asleep))

    def explode(home):
        raise AssertionError("history must not contact the car")

    monkeypatch.setattr("core.cli.build_app", explode)
    assert main(["--home", str(tmp_path), "history", "due"]) == 0
    assert "does not contact the car" in capsys.readouterr().out


def test_cli_rejects_bad_arguments_before_the_app_and_accepts_the_phrases(tmp_path, monkeypatch, capsys):
    choices = set(build_parser()._subparsers._group_actions[0].choices)
    assert "charge-amps" in choices
    assert "departure" in choices
    assert "precondition-schedule" not in choices
    assert "precondition" in choices

    def explode(home):
        raise AssertionError("the app should not be built")

    monkeypatch.setattr("core.cli.build_app", explode)
    assert main(["--home", str(tmp_path), "charge-amps", "0"]) == 2
    assert "1 to 80" in capsys.readouterr().err
    assert main(["--home", str(tmp_path), "charge-amps", "81"]) == 2
    assert main(["--home", str(tmp_path), "departure", "set", "weekdays", "25:00", "1", "2"]) == 2
    assert "07:30" in capsys.readouterr().err
    assert main(["--home", str(tmp_path), "departure", "set", "weekdays", "07:30", "1"]) == 2
    assert "both" in capsys.readouterr().err
    assert main(["--home", str(tmp_path), "departure", "set", "weekdays", "07:30", "91", "2"]) == 2
    assert main(["--home", str(tmp_path), "departure", "remove", "0"]) == 2

    seen = {}

    class Fake:
        def command(self, name, **kwargs):
            seen["amps"] = (name, kwargs)
            return "OK: charge-amps 16\nThe car may clamp this to what the charger allows."

        def departure_show(self, **kwargs):
            from core.fleet.departure import DepartureReport

            seen["show"] = kwargs
            return DepartureReport("asleep", True, False, False, ())

        def departure_set(self, spec, **kwargs):
            seen["set"] = (spec, kwargs)
            return "OK: departure set"

        def departure_remove(self, schedule_id, **kwargs):
            seen["remove"] = (schedule_id, kwargs)
            return "OK: departure remove"

    monkeypatch.setattr("core.cli.build_app", lambda home: Fake())
    monkeypatch.setattr("core.cli.time.time", lambda: 1700000000)
    assert main(["--home", str(tmp_path), "departure", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] is True
    assert main(["--home", str(tmp_path), "charge-amps", "16", "--confirm", "I confirm charging set amps"]) == 0
    assert seen["amps"][0] == "charge-amps"
    assert seen["amps"][1]["argument"] == "16"
    assert seen["amps"][1]["statement"] == "I confirm charging set amps"
    assert main(
        [
            "--home",
            str(tmp_path),
            "departure",
            "set",
            "weekdays",
            "07:30",
            "1",
            "2",
            "--enabled",
            "false",
            "--confirm",
            "I confirm departure schedule set",
        ]
    ) == 0
    assert seen["set"][0].tool_args() == ["precondition-schedule-add", "weekdays", "07:30", "1", "2", "weekly", "1700000000", "false"]
    assert seen["set"][1]["statement"] == "I confirm departure schedule set"
    assert main(
        [
            "--home",
            str(tmp_path),
            "departure",
            "set",
            "weekdays",
            "07:30",
            "--confirm",
            "I confirm departure schedule set",
        ]
    ) == 0
    assert seen["set"][0].latitude is None
    assert seen["set"][0].longitude is None
    assert seen["set"][1]["statement"] == "I confirm departure schedule set"
    assert main(
        [
            "--home",
            str(tmp_path),
            "departure",
            "remove",
            "1700000000",
            "--confirm",
            "I confirm departure schedule remove",
        ]
    ) == 0
    assert seen["remove"] == ("1700000000", {"preferred": None, "statement": "I confirm departure schedule remove"})


def test_installer_does_not_add_a_tesla_control_patch_for_these_commands():
    text = (ROOT / "setup" / "install.sh").read_text(encoding="utf-8")
    assert "setup/patch_tesla_control_defrost.py" in text
    assert "setup/patch_tesla_control_phase8.py" in text
    assert "charging-set-amps" not in text
    assert "precondition-schedule" not in text
    assert "set_scheduled_departure" not in text
    names = {path.name for path in (ROOT / "setup").glob("patch_tesla_control*.py")}
    assert names == {"patch_tesla_control_defrost.py", "patch_tesla_control_phase8.py"}


def test_readme_mentions_both_and_does_not_add_a_wake_caveat():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    can_do = readme[readme.index("## What you can do"):readme.index("## A conversation")]
    assert "set the charging current, and schedule cabin warm-up before you leave" in can_do
    assert "may wake" not in readme.lower()
    assert "## Next" not in readme
