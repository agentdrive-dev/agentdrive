"""In-car charging timetable: mocked Fleet API reads and signed command arguments."""

import json

import pytest

from adapters.template import TemplateAdapter
from core.cli import build_parser, main
from core.errors import ConfigError, ConfirmationRequired, NotParked, ScheduleBlocked, VerificationFailed
from core.fleet.charge_schedule import (
    format_schedule_report,
    format_schedule_report_json,
    parse_schedule_id,
    parse_schedule_write,
    schedules_from_vehicle_data,
)
from core.fleet.snapshot import from_vehicle_data
from core.ops import format_status
from core.safety.catalog import load_specs
from tests.test_refresh_and_check import VIN, Control, Http, listed, make_app, urls, vehicle_payload
from tests.test_safety import Adapter, execute, parked


def _row(**overrides):
    body = {
        "id": 1700000000,
        "days_of_week": 62,
        "enabled": True,
        "start_enabled": True,
        "start_time": 22 * 60,
        "end_enabled": True,
        "end_time": 6 * 60,
        "one_time": False,
        "latitude": 1,
        "longitude": 2,
    }
    body.update(overrides)
    return body


def _payload(*rows, section=True, as_list=True):
    payload = vehicle_payload()
    if section:
        listed_rows = list(rows) if as_list else None
        block = {}
        if as_list:
            block["charge_schedules"] = listed_rows
        payload["response"]["charge_schedule_data"] = block
    return payload


def _with_schedule(**overrides):
    seen = parked()
    payload = _payload(_row(**overrides))
    parsed = from_vehicle_data(payload)
    seen.charge_schedules = parsed.charge_schedules
    seen.charge_schedule_section_present = True
    seen.charge_schedule_list_present = True
    return seen


def test_days_time_and_degrees_match_the_signed_command_shape():
    spec = parse_schedule_write("Monday, Wednesday", "22:00-6:00", "1.0", "-2.50", once=False, enabled=True)
    assert spec.days == "Mon,Wed"
    assert spec.time_range == "22:00-06:00"
    assert spec.latitude == "1"
    assert spec.longitude == "-2.5"
    assert spec.tool_args() == ["charging-schedule-add", "Mon,Wed", "22:00-06:00", "1", "-2.5"]

    once = parse_schedule_write("all", "23:30-", "0", "0", once=True, enabled=True)
    assert once.tool_args() == ["charging-schedule-add", "all", "23:30-", "0", "0", "once"]

    off = parse_schedule_write("weekdays", "-06:00", "1", "2", once=False, enabled=False)
    assert off.days_mask == 62
    assert off.tool_args() == ["charging-schedule-add", "weekdays", "-06:00", "1", "2", "weekly", "0", "false"]
    assert off.tool_args()[0] != "charging-schedule"
    assert "set_scheduled_charging" not in off.tool_args()


def test_rejected_schedule_shapes():
    with pytest.raises(ConfigError):
        parse_schedule_write("marketday", "22:00-06:00", "1", "2", once=False, enabled=True)
    with pytest.raises(ConfigError):
        parse_schedule_write("sun mon", "22:00-06:00", "1", "2", once=False, enabled=True)
    with pytest.raises(ConfigError):
        parse_schedule_write("weekdays", "22:00", "1", "2", once=False, enabled=True)
    with pytest.raises(ConfigError):
        parse_schedule_write("weekdays", "24:00-01:00", "1", "2", once=False, enabled=True)
    with pytest.raises(ConfigError):
        parse_schedule_write("weekdays", "-", "1", "2", once=False, enabled=True)
    with pytest.raises(ConfigError):
        parse_schedule_write("weekdays", "22:00-06:00", "181", "2", once=False, enabled=True)
    with pytest.raises(ConfigError):
        parse_schedule_id("0")
    with pytest.raises(ConfigError):
        parse_schedule_id("nope")


def test_show_skips_a_sleeping_or_offline_car_without_a_wake_or_a_data_read():
    for state in ("asleep", "offline", ""):
        http = Http(listed(state))
        control = Control()
        report = make_app(http, control).charge_schedule_show(preferred=None)
        text = format_schedule_report(report)
        assert "skipped: the vehicle is not online, so it was not woken" in text
        assert report.state == (state or "unknown")
        assert control.calls == []
        assert not any("vehicle_data" in url or "wake_up" in url for url in urls(http))
        body = json.loads(format_schedule_report_json(report))
        assert body["skipped"] is True
        assert body["schedules"] == []
        assert VIN not in text
        assert VIN not in json.dumps(body)


def test_show_reads_only_charge_schedule_data_when_the_car_is_online():
    http = Http(listed("online"), _payload(_row(name="Depot", lat=1, lon=2)))
    report = make_app(http, Control()).charge_schedule_show(preferred=None)
    text = format_schedule_report(report)
    assert "id 1700000000 Depot: enabled, weekdays, 22:00-06:00, weekly, lat 1 lon 2" in text
    data_urls = [url for url in urls(http) if "vehicle_data" in url]
    assert data_urls == [url for url in data_urls if "endpoints=charge_schedule_data" in url]
    assert len(data_urls) == 1
    assert "location_data" not in data_urls[0]
    assert "wake_up" not in "".join(urls(http))
    body = json.loads(format_schedule_report_json(report))
    assert body["schedules"][0]["days_of_week"] == 62
    assert body["schedules"][0]["start_time"] == 1320
    assert body["schedules"][0]["end_time"] == 360
    assert body["schedules"][0]["latitude"] == 1
    assert body["schedules"][0]["longitude"] == 2
    assert VIN not in text
    _, _, alias = schedules_from_vehicle_data(
        {
            "response": {
                "charge_schedule_data": {
                    "charge_schedules": [
                        {
                            "id": "5",
                            "days_of_week": "weekdays",
                            "lat": 3,
                            "lon": 4,
                            "enabled": True,
                            "start_enabled": True,
                            "start_time": "60",
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
    assert alias[0].start_time == 60


def test_show_reports_a_missing_section_and_an_empty_list():
    missing = make_app(Http(listed("online"), vehicle_payload()), Control()).charge_schedule_show(preferred=None)
    assert format_schedule_report(missing) == "state: online\ncharge_schedule_data was not in the response"
    empty = make_app(Http(listed("online"), _payload()), Control()).charge_schedule_show(preferred=None)
    assert format_schedule_report(empty) == "state: online\nno charge schedules"
    bare = make_app(Http(listed("online"), _payload(as_list=False)), Control()).charge_schedule_show(preferred=None)
    assert "did not include charge_schedules" in format_schedule_report(bare)


def test_status_does_not_print_timetable_coordinates():
    payload = _payload(_row())
    payload["response"]["drive_state"]["latitude"] = 9.5
    payload["response"]["drive_state"]["longitude"] = 8.5
    observation = from_vehicle_data(payload)
    assert not hasattr(observation, "latitude")
    text = format_status({"display_name": "Example", "state": "online"}, observation, VIN)
    assert "9.5" not in text
    assert "8.5" not in text
    assert "22:00" not in text


def test_set_sends_one_signed_add_and_reads_the_timetable_back():
    http = Http(listed("online"), _payload(_row()))
    control = Control()
    spec = parse_schedule_write("weekdays", "22:00-06:00", "1", "2", once=False, enabled=True)
    text = make_app(http, control).charge_schedule_set(
        spec,
        preferred=None,
        statement="I confirm charging schedule set",
        sleep=lambda _seconds: None,
    )
    assert text == "OK: charge-schedule set id 1700000000"
    assert control.calls[0]["args"] == ["charging-schedule-add", "weekdays", "22:00-06:00", "1", "2"]
    assert control.calls[0]["token"] not in control.calls[0]["args"]
    assert len(control.calls) == 1
    data_urls = [url for url in urls(http) if "vehicle_data" in url]
    assert "charge_schedule_data" not in data_urls[0]
    assert "charge_schedule_data" in data_urls[-1]
    assert not any(url.endswith("/wake_up") for url in urls(http))


def test_set_disabled_and_once_use_the_documented_optional_slots():
    off = parse_schedule_write("Sun,Sat", "20:32-", "1", "2", once=True, enabled=False)
    after = _with_schedule(days_of_week=65, start_time=20 * 60 + 32, end_enabled=False, end_time=None, one_time=True, enabled=False)
    _, runner = execute(
        "charging_schedule_set",
        adapter=Adapter("I confirm charging schedule set"),
        extra=off.tool_args()[1:],
        after=after,
    )
    assert runner.calls[0]["args"] == [
        "charging-schedule-add",
        "Sun,Sat",
        "20:32-",
        "1",
        "2",
        "once",
        "0",
        "false",
    ]


def test_set_requires_confirmation_and_park_before_the_tool_runs():
    http = Http(listed("online"), _payload(_row()))
    control = Control()
    spec = parse_schedule_write("weekdays", "22:00-06:00", "1", "2", once=False, enabled=True)
    app = make_app(http, control)
    with pytest.raises(ConfirmationRequired):
        app.charge_schedule_set(spec, preferred=None, statement=None, sleep=lambda _seconds: None)
    assert control.calls == []

    control.calls.clear()
    driving = Http(listed("online"), vehicle_payload(drive_state={"shift_state": "D", "speed": 10}))
    with pytest.raises(NotParked):
        make_app(driving, control).charge_schedule_set(
            spec,
            preferred=None,
            statement="I confirm charging schedule set",
            sleep=lambda _seconds: None,
        )
    assert control.calls == []


def test_a_schedule_cannot_run_or_store_a_timetable_change():
    runner_box = []

    class Runner:
        def run(self, *, vin, access_token, args, timeout=90):
            runner_box.append(args)
            from core.fleet.commands import CommandResult

            return CommandResult(0, "")

    with pytest.raises(ScheduleBlocked):
        execute(
            "charging_schedule_set",
            adapter=Adapter("I confirm charging schedule set"),
            via_schedule=True,
            extra=["weekdays", "22:00-06:00", "1", "2"],
            after=_with_schedule(),
            runner=Runner(),
        )
    with pytest.raises(ScheduleBlocked):
        execute(
            "charging_schedule_remove",
            adapter=Adapter("I confirm charging schedule remove"),
            via_schedule=True,
            extra=["id", "1700000000"],
            after=parked(),
            runner=Runner(),
        )
    assert runner_box == []
    adapter = TemplateAdapter(lambda challenge: "")
    with pytest.raises(ScheduleBlocked):
        adapter.schedule("night", "22:00", {"command": "charge-schedule", "action": "set", "preconfirmed": True})
    with pytest.raises(ScheduleBlocked):
        adapter.schedule("night", "22:00", {"command": "charging_schedule_remove"})
    adapter.schedule("health-check", "every 6 hours", {"command": "check", "json": True})


def test_remove_sends_the_id_and_checks_it_is_gone():
    http = Http(listed("online"), _payload())
    control = Control()
    text = make_app(http, control).charge_schedule_remove(
        "1700000000",
        preferred=None,
        statement="I confirm charging schedule remove",
        sleep=lambda _seconds: None,
    )
    assert text == "OK: charge-schedule remove 1700000000"
    assert control.calls[0]["args"] == ["charging-schedule-remove", "id", "1700000000"]
    assert len(control.calls) == 1

    still = Http(listed("online"), _payload(_row()))
    control = Control()
    with pytest.raises(VerificationFailed):
        make_app(still, control).charge_schedule_remove(
            "1700000000",
            preferred=None,
            statement="I confirm charging schedule remove",
            sleep=lambda _seconds: None,
        )
    assert len(control.calls) == 1


def test_a_mismatched_follow_up_is_not_sent_again():
    http = Http(listed("online"), _payload(_row(start_time=60)))
    control = Control()
    spec = parse_schedule_write("weekdays", "22:00-06:00", "1", "2", once=False, enabled=True)
    with pytest.raises(VerificationFailed):
        make_app(http, control).charge_schedule_set(
            spec,
            preferred=None,
            statement="I confirm charging schedule set",
            sleep=lambda _seconds: None,
        )
    assert len(control.calls) == 1


def test_unlock_confirmation_does_not_authorize_a_timetable_change():
    with pytest.raises(ConfirmationRequired):
        execute(
            "charging_schedule_set",
            adapter=Adapter("I confirm unlock"),
            extra=["weekdays", "22:00-06:00", "1", "2"],
            after=_with_schedule(),
        )


def test_cli_parses_the_family_and_rejects_a_bad_window_before_the_app(tmp_path, monkeypatch, capsys):
    choices = set(build_parser()._subparsers._group_actions[0].choices)
    assert "charge-schedule" in choices
    assert "charging_schedule_set" in load_specs()
    assert load_specs()["charging_schedule_set"].tier == "sensitive"

    def explode(home):
        raise AssertionError("the app should not be built")

    monkeypatch.setattr("core.cli.build_app", explode)
    assert main(["--home", str(tmp_path), "charge-schedule", "set", "weekdays", "25:00-01:00", "1", "2"]) == 2
    assert "22:00-06:00" in capsys.readouterr().err

    seen = {}

    class Fake:
        def charge_schedule_show(self, **kwargs):
            seen["show"] = kwargs
            from core.fleet.charge_schedule import ScheduleReport

            return ScheduleReport("asleep", True, False, False, ())

        def charge_schedule_set(self, spec, **kwargs):
            seen["set"] = (spec.tool_args(), kwargs)
            return "OK: charge-schedule set"

        def charge_schedule_remove(self, schedule_id, **kwargs):
            seen["remove"] = (schedule_id, kwargs)
            return "OK: charge-schedule remove"

    monkeypatch.setattr("core.cli.build_app", lambda home: Fake())
    assert main(["--home", str(tmp_path), "charge-schedule", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] is True
    assert main(
        [
            "--home",
            str(tmp_path),
            "charge-schedule",
            "set",
            "weekdays",
            "22:00-06:00",
            "1",
            "2",
            "--confirm",
            "I confirm charging schedule set",
        ]
    ) == 0
    assert seen["set"][0] == ["charging-schedule-add", "weekdays", "22:00-06:00", "1", "2"]
    assert seen["set"][1]["statement"] == "I confirm charging schedule set"
    assert main(
        [
            "--home",
            str(tmp_path),
            "charge-schedule",
            "remove",
            "1700000000",
            "--confirm",
            "I confirm charging schedule remove",
        ]
    ) == 0
    assert seen["remove"][0] == "1700000000"
