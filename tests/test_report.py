"""Daily vehicle report and the private 07:30 schedule."""

import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
import yaml

from core.cli import main
from core.errors import ConfigError
from core.fleet.health import skipped_report
from core.fleet.http import HttpError, UrllibClient
from core.fleet.queries import FleetReader, QueryError
from core.fleet.report import skipped_vehicle_report
from core.ops import App, format_check
from core.report_schedule import (
    DEFAULT_TIME,
    crontab_example,
    load_report_schedule,
    parse_report_time,
    set_report_time,
)

VIN = "SECRET-VIN-VALUE"
TOKEN = "access-token"
LATITUDE = "12.345678"
LONGITUDE = "98.765432"


class Control:
    def run(self, *, vin, access_token, args, timeout=90):
        raise AssertionError("report must not sign a command")


class Http:
    def __init__(self, state="online", data=None, data_status=200, boom=None, boom_on="vehicle_data", list_status=200):
        self.state = state
        self.data = data or {"response": {}}
        self.data_status = data_status
        self.boom = boom
        self.boom_on = boom_on
        self.list_status = list_status
        self.calls = []

    def request_json(self, method, url, headers, timeout=30, body=None):
        self.calls.append(url)
        if self.boom is not None and self.boom_on in url:
            raise self.boom
        if "wake_up" in url:
            return 200, {"response": {"state": "online"}}
        if "vehicle_data" in url:
            return self.data_status, self.data
        if url.rstrip("/").endswith("/vehicles"):
            if self.list_status != 200:
                return self.list_status, {}
            return 200, {"response": [{"vin": VIN, "state": self.state, "display_name": "Example"}]}
        return 500, {}


def vehicle_payload():
    return {
        "response": {
            "drive_state": {"shift_state": "P", "speed": 0, "latitude": float(LATITUDE), "longitude": float(LONGITUDE)},
            "charge_state": {
                "battery_level": 80,
                "battery_range": 100,
                "charge_limit_soc": 80,
                "charging_state": "Disconnected",
            },
            "climate_state": {"is_climate_on": False, "driver_temp_setting": 21, "inside_temp": 20, "outside_temp": 15},
            "vehicle_state": {
                "locked": True,
                "sentry_mode": True,
                "tpms_pressure_fl": 2.9,
                "tpms_pressure_fr": 2.9,
                "tpms_pressure_rl": 2.9,
                "tpms_pressure_rr": 2.9,
                "tpms_soft_warning_fl": False,
                "df": 0,
                "dr": 0,
                "pf": 0,
                "pr": 0,
                "ft": 0,
                "rt": 0,
                "fd_window": 0,
                "fp_window": 0,
                "rd_window": 0,
                "rp_window": 0,
            },
        }
    }


def make_app(http):
    from core.config import load_regions

    regions = load_regions()
    tokens = SimpleNamespace(access_token=lambda force=False: TOKEN)
    reader = FleetReader(http, regions, "na", tokens)
    return App(Path("."), reader=reader, tokens=tokens, control=Control(), region="na")


def urls(http):
    return list(http.calls)


def test_report_briefing_uses_the_parked_snapshot_and_does_not_wake():
    http = Http(data=vehicle_payload())
    report = make_app(http).report(preferred=None)
    assert report.skipped is False
    assert report.exit_code == 0
    assert report.state == "online"
    text = report.text
    assert "Vehicle report: Example" in text
    assert "Battery: 80%" in text
    assert "161 km" in text
    assert "Charge connection: Disconnected" in text
    assert "Locked: yes" in text
    assert "Gear: P (parked)" in text
    assert "Doors, windows, and trunk: closed" in text
    assert "Sentry: on" in text
    assert "Tyres (bar): FL 2.9 FR 2.9 RL 2.9 RR 2.9" in text
    assert "TPMS: ok" in text
    assert VIN not in text
    assert TOKEN not in text
    assert LATITUDE not in text
    assert LONGITUDE not in text
    assert "latitude" not in text.lower()
    data_urls = [url for url in urls(http) if "vehicle_data" in url]
    assert len(data_urls) == 1
    assert "location_data" not in data_urls[0]
    assert "charge_state" in data_urls[0]
    assert "climate_state" in data_urls[0]
    assert "vehicle_state" in data_urls[0]
    assert "drive_state" in data_urls[0]
    assert not any("wake_up" in url for url in urls(http))


def test_report_json_briefing_has_no_coordinates_or_vin():
    report = make_app(Http(data=vehicle_payload())).report(preferred=None)
    from core.fleet.report import format_report_json

    rendered = format_report_json(report)
    payload = json.loads(rendered)
    assert payload["skipped"] is False
    assert payload["battery_percent"] == 80
    assert payload["charging_state"] == "Disconnected"
    assert payload["locked"] is True
    assert payload["shift_state"] == "P"
    assert payload["parked"] is True
    assert payload["closures"] == "closed"
    assert payload["sentry"] is True
    assert payload["tyres_bar"]["fl"] == 2.9
    assert payload["tpms"] == "ok"
    assert payload["text"].startswith("Vehicle report: Example")
    assert VIN not in rendered
    assert LATITUDE not in rendered
    assert LONGITUDE not in rendered
    assert "latitude" not in rendered
    assert "longitude" not in rendered


def test_report_skip_matches_check_for_asleep_and_offline():
    expected = "state: asleep\nskipped: the vehicle is not online, so it was not woken"
    assert format_check(skipped_report("asleep", tyre_bar=2.5, low_battery_percent=30)) == expected
    asleep = make_app(Http(state="asleep")).report(preferred=None)
    assert asleep.skipped is True
    assert asleep.exit_code == 0
    assert asleep.state == "asleep"
    assert asleep.text == expected
    assert format_check(skipped_report("asleep", tyre_bar=2.5, low_battery_percent=30)) == asleep.text
    http = Http(state="asleep")
    make_app(http).report(preferred=None)
    assert not any("vehicle_data" in url or "wake_up" in url for url in urls(http))

    offline = make_app(Http(state="offline")).report(preferred=None)
    assert offline.text == "state: offline\nskipped: the vehicle is not online, so it was not woken"
    assert offline.exit_code == 0
    assert skipped_vehicle_report("offline").text == offline.text


def test_report_skips_a_408_without_waking():
    http = Http(data_status=408)
    report = make_app(http).report(preferred=None)
    assert report.skipped is True
    assert report.state == "asleep"
    assert report.exit_code == 0
    assert "was not woken" in report.text
    assert any("vehicle_data" in url for url in urls(http))
    assert not any("wake_up" in url for url in urls(http))


def test_report_skips_a_timeout_without_waking():
    http = Http(boom=TimeoutError("timed out"))
    report = make_app(http).report(preferred=None)
    assert report.skipped is True
    assert report.state == "timeout"
    assert report.exit_code == 0
    assert "was not woken" in report.text
    assert not any("wake_up" in url for url in urls(http))

    listed = Http(boom=HttpError("Fleet API request timed out", timed_out=True), boom_on="/vehicles")
    listed_report = make_app(listed).report(preferred=None)
    assert listed_report.state == "timeout"
    assert not any("vehicle_data" in url or "wake_up" in url for url in urls(listed))


def test_report_still_fails_on_a_real_read_error():
    http = Http(list_status=500)
    with pytest.raises(QueryError):
        make_app(http).report(preferred=None)
    assert not any("wake_up" in url for url in urls(http))


def test_urllib_timeout_and_408_are_skips(monkeypatch):
    from core.config import load_regions

    calls = []

    def timeout(request, timeout=30):
        calls.append(request.full_url)
        raise TimeoutError("timed out")

    monkeypatch.setattr("core.fleet.http.urlopen", timeout)
    regions = load_regions()
    tokens = SimpleNamespace(access_token=lambda force=False: TOKEN)
    reader = FleetReader(UrllibClient(), regions, "na", tokens)
    app = App(Path("."), reader=reader, tokens=tokens, control=Control(), region="na")
    report = app.report(preferred=None)
    assert report.skipped is True
    assert report.state == "timeout"
    assert report.exit_code == 0
    assert calls and "wake_up" not in calls[0]

    calls.clear()

    def asleep(request, timeout=30):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 408, "Request Timeout", hdrs=None, fp=BytesIO(b"{}"))

    monkeypatch.setattr("core.fleet.http.urlopen", asleep)
    report = app.report(preferred=None)
    assert report.state == "asleep"
    assert report.exit_code == 0
    assert calls and "wake_up" not in calls[0]

    client = UrllibClient()
    monkeypatch.setattr("core.fleet.http.urlopen", timeout)
    with pytest.raises(HttpError) as caught:
        client.request_json("GET", "https://example.invalid/api", {})
    assert caught.value.timed_out is True

    def wrapped(request, timeout=30):
        raise URLError(TimeoutError("timed out"))

    monkeypatch.setattr("core.fleet.http.urlopen", wrapped)
    with pytest.raises(HttpError) as wrapped_error:
        client.request_json("GET", "https://example.invalid/api", {})
    assert wrapped_error.value.timed_out is True

    def other(request, timeout=30):
        raise URLError(OSError("refused"))

    monkeypatch.setattr("core.fleet.http.urlopen", other)
    with pytest.raises(HttpError) as other_error:
        client.request_json("GET", "https://example.invalid/api", {})
    assert other_error.value.timed_out is False


def test_report_cli_json_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENTDRIVE_VIN", raising=False)
    monkeypatch.delenv("TESLA_VIN", raising=False)

    class Fake:
        def report(self, **kwargs):
            assert kwargs == {"preferred": None}
            return make_app(Http(data=vehicle_payload())).report(preferred=None)

    monkeypatch.setattr("core.cli.build_app", lambda home: Fake())
    code = main(["--home", str(tmp_path), "report", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["text"].startswith("Vehicle report: Example")
    assert VIN not in json.dumps(payload)


def test_schedule_defaults_to_0730_and_can_be_changed(tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    assert load_report_schedule(home).time == DEFAULT_TIME
    assert load_report_schedule(home).enabled is True
    assert DEFAULT_TIME == "07:30"
    assert main(["--home", str(home), "report-schedule"]) == 0
    shown = capsys.readouterr().out
    assert "report schedule: 07:30 local time" in shown
    assert "enabled: yes" in shown
    assert "crontab example: 30 7 * * * agentdrive history due" in shown
    assert "does not install the job" in shown
    assert not (home / "runtime.yaml").exists()

    assert main(["--home", str(home), "report-schedule", "set", "08:15"]) == 0
    updated = capsys.readouterr().out
    assert "report schedule: 08:15 local time" in updated
    assert "enabled: yes" in updated
    assert "crontab example: 15 8 * * * agentdrive history due" in updated
    assert "update its daily job" in updated
    saved = yaml.safe_load((home / "runtime.yaml").read_text(encoding="utf-8"))
    assert saved["report_schedule"] == {"enabled": True, "time": "08:15"}
    assert '"08:15"' in (home / "runtime.yaml").read_text(encoding="utf-8")
    assert (home / "runtime.yaml").stat().st_mode & 0o777 == 0o600

    assert main(["--home", str(home), "report-schedule", "disable"]) == 0
    disabled = capsys.readouterr().out
    assert "enabled: no" in disabled
    assert "crontab example" not in disabled
    assert "remove its scheduled job" in disabled
    assert load_report_schedule(home).enabled is False
    assert load_report_schedule(home).time == "08:15"

    assert main(["--home", str(home), "report-schedule", "set", "06:05"]) == 0
    assert load_report_schedule(home).time == "06:05"
    assert load_report_schedule(home).enabled is False
    assert "crontab example" not in capsys.readouterr().out

    assert main(["--home", str(home), "report-schedule", "enable"]) == 0
    enabled = capsys.readouterr().out
    assert "enabled: yes" in enabled
    assert "crontab example: 5 6 * * * agentdrive history due" in enabled
    assert load_report_schedule(home).enabled is True


def test_schedule_rejects_bad_times_and_keeps_other_runtime_keys(tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    (home / "runtime.yaml").write_text(
        "key_file: /tmp/example-key.pem\ntoken_file: /tmp/example-tokens.json\n",
        encoding="utf-8",
    )
    before = (home / "runtime.yaml").read_text(encoding="utf-8")
    for bad in ("7:30", "24:00", "23:60", "07:3", "0730", "07:30:00", "am", ""):
        assert main(["--home", str(home), "report-schedule", "set", bad]) == 2
        assert "HH:MM" in capsys.readouterr().err
        assert (home / "runtime.yaml").read_text(encoding="utf-8") == before
    with pytest.raises(ConfigError):
        parse_report_time("7:30")
    assert set_report_time(home, " 09:05 ").time == "09:05"
    loaded = yaml.safe_load((home / "runtime.yaml").read_text(encoding="utf-8"))
    assert loaded["key_file"] == "/tmp/example-key.pem"
    assert loaded["token_file"] == "/tmp/example-tokens.json"
    assert loaded["report_schedule"]["time"] == "09:05"
    assert loaded["report_schedule"]["enabled"] is True
    assert crontab_example(load_report_schedule(home)) == "5 9 * * * agentdrive history due"
    assert crontab_example(load_report_schedule(tmp_path / "empty")) == "30 7 * * * agentdrive history due"


def test_unquoted_clock_time_in_runtime_yaml_is_rejected(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # PyYAML reads an unquoted 23:59 as a number, not as a clock time.
    (home / "runtime.yaml").write_text("report_schedule:\n  enabled: true\n  time: 23:59\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_report_schedule(home)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert set_report_time(fresh, "23:59").time == "23:59"
    assert '"23:59"' in (fresh / "runtime.yaml").read_text(encoding="utf-8")
    assert load_report_schedule(fresh).time == "23:59"
    assert crontab_example(load_report_schedule(fresh)) == "59 23 * * * agentdrive history due"


def test_template_install_prints_the_daily_report_note(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "adapter", "install", "--adapter", "template"]) == 0
    output = capsys.readouterr().out
    assert "07:30 local time" in output
    assert "every 30 minutes" in output
    assert "agentdrive report" in output
    assert "does not run the job itself" in output
