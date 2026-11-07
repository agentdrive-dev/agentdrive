"""Local encrypted history, morning briefings, tips, and the service log."""

import json
import secrets
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.cli import main
from core.config import load_regions
from core.errors import HistoryError
from core.fleet.queries import FleetReader
from core.fleet.snapshot import from_vehicle_data
from core.history.crypto import database_paths, open_sealed
from core.history.store import history_session, isoformat_utc, record_parked_snapshot, save_notice
from core.history.tips import CATALOG, add_calendar_months, is_tip_day, on_cooldown
from core.history.usage import covered, due_period, render_briefing, render_list, render_tips
from core.ops import App, format_check

VIN = "SECRET-VIN-VALUE"
LAT = "12.345678"
LON = "98.765432"
TOKEN = "access-token"
UTC = timezone.utc
NY = ZoneInfo("America/New_York")
REQUIRED_TIP_IDS = {
    "precondition-plugged-in",
    "daily-charge-limit",
    "avoid-sitting-at-100",
    "avoid-low-soc",
    "sentry-drain",
    "tire-rotation",
    "seasonal-tire-pressure",
    "software-update-plugged",
    "climate-modes-drain",
    "scheduled-charge-departure",
    "off-peak-charging",
    "navigate-to-supercharger",
    "home-charge-over-supercharge",
    "parked-in-heat",
    "winter-plug-in",
    "long-sit-plug-in",
    "check-does-not-wake",
    "chat-plain-words",
    "morning-report-time",
    "tell-service-log",
    "cameras-clean",
    "smooth-driving",
    "tokens-and-keys",
    "washer-and-filters",
}


class Control:
    def run(self, **kwargs):
        raise AssertionError("history must not sign a command")


class Http:
    def __init__(self, state="online", data=None):
        self.state = state
        self.data = data or {"response": {}}
        self.calls = []

    def request_json(self, method, url, headers, timeout=30, body=None):
        self.calls.append(url)
        if "wake_up" in url:
            return 200, {"response": {"state": "online"}}
        if "vehicle_data" in url:
            return 200, self.data
        if url.rstrip("/").endswith("/vehicles"):
            return 200, {"response": [{"vin": VIN, "state": self.state, "display_name": "Example"}]}
        return 500, {}


def at(year, month, day, hour=9, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def miles(km):
    return km / 1.609344


def payload(**changes):
    body = {
        "drive_state": {
            "shift_state": "P",
            "speed": 0,
            "latitude": float(LAT),
            "longitude": float(LON),
            "heading": 90,
            "native_latitude": float(LAT),
            "native_longitude": float(LON),
            "route": f"{LAT},{LON}",
        },
        "charge_state": {
            "battery_level": 80,
            "battery_range": 100,
            "charge_limit_soc": 80,
            "charging_state": "Disconnected",
        },
        "climate_state": {
            "is_climate_on": False,
            "driver_temp_setting": 21,
            "inside_temp": 20,
            "outside_temp": 15,
        },
        "vehicle_state": {
            "locked": True,
            "sentry_mode": True,
            "odometer": 100,
            "car_version": "2026.4.1",
            "vin": VIN,
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
        "vehicle_config": {"car_type": "example", "vin": VIN, "latitude": float(LAT)},
        "gui_settings": {
            "gui_distance_units": "km/hr",
            "gui_temperature_units": "C",
            "gui_charge_rate_units": "km/hr",
        },
    }
    for name, values in changes.items():
        body.setdefault(name, {}).update(values)
    return {"response": body}


def make_app(home, http, *, history):
    regions = load_regions()
    tokens = SimpleNamespace(access_token=lambda force=False: TOKEN)
    reader = FleetReader(http, regions, "na", tokens)
    return App(home, reader=reader, tokens=tokens, control=Control(), region="na", history=history)


def record(home, when, **changes):
    body = payload(**changes)
    record_parked_snapshot(
        home,
        body,
        from_vehicle_data(body),
        tyre_bar=2.5,
        low_battery_percent=30,
        now=when,
    )
    return body


def plaintext(home):
    key_path, db_path = database_paths(home)
    return open_sealed(key_path.read_bytes(), db_path.read_bytes())


def test_calendar_precedence_and_windows():
    new_year = date(2024, 1, 1)
    assert new_year.weekday() == 0
    assert due_period(new_year) == "yearly"
    monthly_monday = date(2026, 6, 1)
    assert monthly_monday.weekday() == 0
    assert due_period(monthly_monday) == "monthly"
    monday = date(2026, 3, 2)
    assert monday.weekday() == 0 and due_period(monday) == "weekly"
    tuesday = date(2026, 3, 3)
    assert due_period(tuesday) == "daily"
    assert covered("daily", tuesday) == (date(2026, 3, 2), date(2026, 3, 3))
    assert covered("weekly", monday) == (date(2026, 2, 23), date(2026, 3, 2))
    assert covered("monthly", date(2026, 4, 1)) == (date(2026, 3, 1), date(2026, 4, 1))
    assert covered("yearly", date(2026, 1, 1)) == (date(2025, 1, 1), date(2026, 1, 1))
    assert add_calendar_months(date(2026, 1, 31), 2) == date(2026, 3, 31)
    assert add_calendar_months(date(2025, 12, 31), 2) == date(2026, 2, 28)
    assert on_cooldown(date(2026, 3, 2), date(2026, 5, 1))
    assert not on_cooldown(date(2026, 3, 2), date(2026, 5, 2))


def test_asleep_check_and_report_do_not_save_or_wake(tmp_path):
    for state in ("asleep", "offline"):
        home = tmp_path / state
        http = Http(state=state, data=payload())
        app = make_app(home, http, history=True)
        report = app.check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
        assert report.skipped is True
        assert report.exit_code == 0
        briefing = app.report(preferred=None)
        assert briefing.skipped is True
        assert briefing.exit_code == 0
        assert not database_paths(home)[1].exists()
        assert not database_paths(home)[0].exists()
        assert not any("wake_up" in url or "vehicle_data" in url for url in http.calls)


def test_online_check_saves_history_without_changing_the_result(tmp_path, capsys):
    home = tmp_path / "home"
    body = payload()
    quiet = make_app(home, Http(data=body), history=False).check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
    http = Http(data=body)
    loud = make_app(home, http, history=True).check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
    assert format_check(quiet) == format_check(loud)
    assert quiet.exit_code == loud.exit_code == 0
    assert capsys.readouterr().err == ""
    assert database_paths(home)[1].is_file()
    data_urls = [url for url in http.calls if "vehicle_data" in url]
    assert len(data_urls) == 1
    assert "location_data" not in data_urls[0]
    assert "gui_settings" not in data_urls[0]
    assert "vehicle_config" not in data_urls[0]
    assert not any("wake_up" in url for url in http.calls)
    listed = json.loads(render_list(home, "snapshots", limit=5, as_json=True))
    assert listed["total"] == 1
    assert listed["snapshots"][0]["battery_percent"] == 80
    assert listed["snapshots"][0]["locked"] is True
    assert "latitude" not in json.dumps(listed)


def test_report_also_saves_and_keeps_its_text(tmp_path):
    home = tmp_path / "home"
    body = payload()
    quiet = make_app(home, Http(data=body), history=False).report(preferred=None)
    loud = make_app(home, Http(data=body), history=True).report(preferred=None)
    assert quiet.text == loud.text
    assert loud.exit_code == 0
    assert VIN not in loud.text
    assert LAT not in loud.text
    assert json.loads(render_list(home, "snapshots", limit=5, as_json=True))["total"] == 1


def test_coordinates_and_vin_are_absent_from_the_database(tmp_path):
    home = tmp_path / "home"
    record(home, at(2026, 3, 2))
    raw = database_paths(home)[1].read_bytes()
    plain = plaintext(home)
    blob = plain.decode("latin1")
    assert raw.startswith(b"ADH1")
    assert not raw.startswith(b"SQLite format 3")
    for secret in (VIN, LAT, LON, "latitude", "longitude", "native_latitude", "polyline", "heading"):
        assert secret not in blob
        assert secret.encode() not in raw
    assert b"SQLite format 3" not in raw
    wrong = secrets.token_bytes(32)
    with pytest.raises(HistoryError):
        open_sealed(wrong, raw)
    key_path, db_path = database_paths(home)
    assert key_path.stat().st_mode & 0o777 == 0o600
    assert db_path.stat().st_mode & 0o777 == 0o600
    key_path.write_bytes(secrets.token_bytes(32))
    with pytest.raises(HistoryError, match="could not be unlocked"):
        render_list(home, "trips", limit=5, as_json=False)


def test_parked_odometer_delta_becomes_one_trip(tmp_path):
    home = tmp_path / "home"
    record(home, at(2026, 3, 2, 8), vehicle_state={"odometer": 100, "locked": True, "sentry_mode": True}, charge_state={"battery_level": 80, "charging_state": "Disconnected"})
    record(home, at(2026, 3, 2, 10), vehicle_state={"odometer": 110, "locked": True, "sentry_mode": False}, charge_state={"battery_level": 70, "charging_state": "Disconnected"})
    trips = json.loads(render_list(home, "trips", limit=10, as_json=True))["trips"]
    assert len(trips) == 1
    assert trips[0]["open"] is False
    assert trips[0]["started_at"] == "2026-03-02T08:00:00Z"
    assert trips[0]["ended_at"] == "2026-03-02T10:00:00Z"
    assert trips[0]["distance_km"] == pytest.approx(10 * 1.609344)
    assert trips[0]["start_battery_percent"] == 80
    assert trips[0]["end_battery_percent"] == 70
    assert trips[0]["start_shift"] == "P"
    assert trips[0]["end_sentry"] is False
    assert trips[0]["energy_used_kwh"] is None
    text = render_list(home, "trips", limit=10, as_json=False)
    assert "16.1 km" in text
    assert LAT not in text


def test_an_observed_drive_is_one_trip(tmp_path):
    home = tmp_path / "home"
    record(home, at(2026, 3, 2, 8), vehicle_state={"odometer": 100})
    record(
        home,
        at(2026, 3, 2, 9),
        drive_state={"shift_state": "D", "speed": 40, "latitude": float(LAT), "longitude": float(LON)},
        vehicle_state={"odometer": 105},
        charge_state={"battery_level": 76, "charging_state": "Disconnected"},
    )
    record(home, at(2026, 3, 2, 10), vehicle_state={"odometer": 112}, charge_state={"battery_level": 70, "charging_state": "Disconnected"})
    trips = json.loads(render_list(home, "trips", limit=10, as_json=True))["trips"]
    assert len(trips) == 1
    assert trips[0]["open"] is False
    assert trips[0]["started_at"] == "2026-03-02T09:00:00Z"
    assert trips[0]["ended_at"] == "2026-03-02T10:00:00Z"
    assert trips[0]["distance_km"] == pytest.approx(12 * 1.609344)
    assert trips[0]["start_shift"] == "D"
    assert trips[0]["end_shift"] == "P"
    assert LAT not in json.dumps(trips)


def test_charge_session_energy_and_curve_are_downsampled(tmp_path):
    home = tmp_path / "home"
    start = at(2026, 3, 2, 22)
    samples = [
        (start, 30, 0.2, 11),
        (start + timedelta(seconds=30), 31, 0.3, 11),
        (start + timedelta(seconds=60), 32, 0.5, 11),
        (start + timedelta(seconds=90), 33, 0.6, 11),
        (start + timedelta(minutes=5), 40, 5.0, 7),
    ]
    for moment, battery, energy, power in samples:
        record(
            home,
            moment,
            charge_state={
                "battery_level": battery,
                "charging_state": "Charging",
                "charge_energy_added": energy,
                "charger_power": power,
            },
            vehicle_state={"odometer": 100},
        )
    record(
        home,
        start + timedelta(minutes=5, seconds=10),
        charge_state={"battery_level": 40, "charging_state": "Disconnected", "charge_energy_added": 0},
        vehicle_state={"odometer": 100},
    )
    charges = json.loads(render_list(home, "charges", limit=5, as_json=True))["charges"]
    assert len(charges) == 1
    charge = charges[0]
    assert charge["open"] is False
    assert charge["start_percent"] == 30
    assert charge["end_percent"] == 40
    assert charge["energy_added_kwh"] == pytest.approx(5.0)
    assert charge["plugged_in"] is True
    assert charge["charging"] is True
    assert [item["battery_percent"] for item in charge["readings"]] == [30, 32, 40]
    assert 31 not in [item["battery_percent"] for item in charge["readings"]]


def test_unchanged_parked_checks_share_one_snapshot(tmp_path):
    home = tmp_path / "home"
    start = at(2026, 3, 2, 0)
    for step in range(48):
        record(
            home,
            start + timedelta(minutes=30 * step),
            vehicle_state={"odometer": 100, "tpms_pressure_fl": 2.9 + (step % 2) * 0.01},
            charge_state={"battery_level": 80 - step // 12, "charging_state": "Disconnected"},
        )
    rows = json.loads(render_list(home, "snapshots", limit=10, as_json=True))
    assert rows["total"] == 1
    snap = rows["snapshots"][0]
    assert snap["first_seen"] == "2026-03-02T00:00:00Z"
    assert snap["last_seen"] == "2026-03-02T23:30:00Z"
    assert snap["sample_count"] == 48
    assert snap["battery_percent"] == 80 - 47 // 12
    assert snap["tyres_bar"]["fl"] == pytest.approx(2.91)
    record(
        home,
        start + timedelta(hours=24),
        drive_state={"shift_state": "D", "speed": 10},
        vehicle_state={"odometer": 100},
    )
    record(
        home,
        start + timedelta(hours=24, minutes=30),
        vehicle_state={"odometer": 101, "locked": False},
        charge_state={"battery_level": 70, "charging_state": "Disconnected"},
    )
    assert json.loads(render_list(home, "snapshots", limit=10, as_json=True))["total"] == 3


def test_charge_curve_does_not_multiply_parked_snapshots(tmp_path):
    home = tmp_path / "home"
    start = at(2026, 3, 2, 22)
    for step, battery in enumerate((30, 31, 34, 40)):
        record(
            home,
            start + timedelta(minutes=2 * step),
            charge_state={
                "battery_level": battery,
                "charging_state": "Charging",
                "charge_energy_added": battery / 10,
                "charger_power": 11,
            },
            vehicle_state={"odometer": 80},
        )
    record(
        home,
        start + timedelta(minutes=10),
        charge_state={"battery_level": 40, "charging_state": "Disconnected", "charge_energy_added": 0},
        vehicle_state={"odometer": 80},
    )
    snapshots = json.loads(render_list(home, "snapshots", limit=10, as_json=True))
    assert snapshots["total"] == 2
    charging = next(item for item in snapshots["snapshots"] if item["charging_state"] == "Charging")
    assert charging["sample_count"] == 4
    assert charging["battery_percent"] == 40
    readings = json.loads(render_list(home, "charges", limit=5, as_json=True))["charges"][0]["readings"]
    assert len(readings) == 4


def test_alerts_and_config_are_recorded_only_on_change(tmp_path):
    home = tmp_path / "home"
    unlocked = {"locked": False, "sentry_mode": True, "odometer": 50, "car_version": "2026.4.1", "vin": VIN}
    record(home, at(2026, 3, 2, 8), vehicle_state=unlocked, charge_state={"battery_level": 80, "charging_state": "Disconnected"})
    record(home, at(2026, 3, 2, 9), vehicle_state=unlocked, charge_state={"battery_level": 80, "charging_state": "Disconnected"})
    record(home, at(2026, 3, 2, 10), vehicle_state={"locked": True, "odometer": 50, "car_version": "2026.4.1", "vin": VIN})
    record(home, at(2026, 3, 2, 11), vehicle_state={"locked": False, "odometer": 50, "car_version": "2026.4.2", "vin": VIN})
    alerts = json.loads(render_list(home, "alerts", limit=10, as_json=True))["alerts"]
    assert [item["code"] for item in alerts] == ["unlocked", "unlocked"]
    config = json.loads(render_list(home, "config", limit=10, as_json=True))
    assert config["total"] == 2
    versions = {item["software_version"] for item in config["config"]}
    assert versions == {"2026.4.1", "2026.4.2"}
    rendered = json.dumps(config)
    assert VIN not in rendered
    assert "latitude" not in rendered
    assert LAT not in rendered
    assert config["config"][0]["vehicle_config"]["car_type"] == "example"
    assert "vin" not in config["config"][0]["vehicle_config"]


def test_a_history_failure_does_not_change_check(tmp_path, monkeypatch, capsys):
    def boom(*args, **kwargs):
        raise OSError("disk full at " + str(tmp_path))

    monkeypatch.setattr("core.history.store.record_parked_snapshot", boom)
    http = Http(data=payload(vehicle_state={"locked": False, "odometer": 10}))
    report = make_app(tmp_path, http, history=True).check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
    captured = capsys.readouterr()
    assert report.exit_code == 1
    assert "unlocked" in format_check(report)
    assert "history: the local history could not be saved (OSError)" in captured.err
    assert "disk full" not in captured.err
    assert VIN not in captured.err
    assert LAT not in captured.err
    assert not any(url.endswith("/wake_up") for url in http.calls)


def test_history_queries_do_not_contact_the_car_or_create_a_database(tmp_path, monkeypatch, capsys):
    def explode(home):
        raise AssertionError("history must not contact the car")

    monkeypatch.setattr("core.cli.build_app", explode)
    monkeypatch.setattr("core.history.usage.local_today", lambda tz: date(2026, 3, 3))
    monkeypatch.delenv("AGENTDRIVE_VIN", raising=False)
    assert main(["--home", str(tmp_path), "history", "trips", "--json"]) == 0
    payload_out = json.loads(capsys.readouterr().out)
    assert payload_out["trips"] == []
    assert main(["--home", str(tmp_path), "history", "due"]) == 0
    text = capsys.readouterr().out
    assert "This morning's report is the daily vehicle history." in text
    assert "does not contact the car" in text
    assert not database_paths(tmp_path)[0].exists()
    assert main(["--home", str(tmp_path), "history", "trips", "--limit", "0"]) == 2
    assert "1 to 200" in capsys.readouterr().err


def test_usage_window_uses_the_profile_timezone(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "profile.yaml").write_text(
        "country: US\ntimezone: America/New_York\nplaces: []\nhabits: []\n",
        encoding="utf-8",
    )
    record(home, datetime(2026, 3, 3, 3, 30, tzinfo=UTC), vehicle_state={"odometer": 10}, charge_state={"battery_level": 55, "charging_state": "Disconnected"})
    local = json.loads(render_briefing(home, "daily", as_json=True, on=date(2026, 3, 3), tz=NY))
    utc = json.loads(render_briefing(home, "daily", as_json=True, on=date(2026, 3, 3), tz=UTC))
    assert local["snapshots"]["count"] == 1
    assert utc["snapshots"]["count"] == 0
    assert local["covered_start"] == "2026-03-02"
    assert "latitude" not in json.dumps(local)


def test_empty_period_says_so(tmp_path):
    text = render_briefing(tmp_path, "daily", as_json=False, on=date(2026, 3, 3), tz=UTC)
    assert "Daily vehicle history for 2026-03-02" in text
    assert "No trips, charges, snapshots, or alerts were saved for this period." in text
    assert "Tip:" not in text


def test_tip_catalog_cooldown_and_sparse_days(tmp_path):
    ids = [tip.id for tip in CATALOG]
    assert REQUIRED_TIP_IDS <= set(ids)
    assert len(ids) == len(set(ids))
    for tip in CATALOG:
        assert tip.text
        assert "latitude" not in tip.text.lower()
        assert "longitude" not in tip.text.lower()
        assert VIN not in tip.text
    assert is_tip_day(date(2026, 3, 2))
    assert not is_tip_day(date(2026, 3, 3))
    assert is_tip_day(date(2026, 3, 4))
    assert is_tip_day(date(2026, 3, 1))
    home = tmp_path / "home"
    record(home, at(2026, 3, 2, 7), vehicle_state={"sentry_mode": True, "odometer": 20}, climate_state={"outside_temp": 15})
    first = json.loads(render_tips(home, as_json=True, on=date(2026, 3, 2), tz=UTC))
    assert first["due"] is True
    assert first["id"] == "sentry-drain"
    assert "Sentry" in first["tip"]
    second = json.loads(render_tips(home, as_json=True, on=date(2026, 3, 2), tz=UTC))
    assert second["due"] is False
    assert second["reason"] == "already_shown"
    assert "Tip:" not in render_briefing(home, "due", as_json=False, on=date(2026, 3, 2), tz=UTC)
    wednesday = json.loads(render_tips(home, as_json=True, on=date(2026, 3, 4), tz=UTC))
    assert wednesday["due"] is True
    assert wednesday["id"] == "precondition-plugged-in"
    tuesday = render_tips(home, as_json=False, on=date(2026, 3, 3), tz=UTC)
    assert tuesday == "No tip this morning."
    early = json.loads(render_tips(home, as_json=True, on=date(2026, 5, 1), tz=UTC))
    assert early["id"] not in {"sentry-drain", "precondition-plugged-in"}
    again = json.loads(render_tips(home, as_json=True, on=date(2026, 5, 4), tz=UTC))
    assert again["id"] == "sentry-drain"
    blocked = tmp_path / "blocked"
    with history_session(blocked, write=True) as conn:
        for tip in CATALOG:
            save_notice(conn, tip.id, date(2026, 3, 2))
    none = json.loads(render_tips(blocked, as_json=True, on=date(2026, 3, 4), tz=UTC))
    assert none["due"] is False
    assert none["reason"] == "none_eligible"
    briefing = render_briefing(blocked, "daily", as_json=False, on=date(2026, 3, 4), tz=UTC)
    assert "Tip:" not in briefing


def test_service_log_uses_saved_odometer_and_does_not_wake(tmp_path, monkeypatch, capsys):
    from core.history.service import perform_log

    home = tmp_path / "empty"
    with pytest.raises(HistoryError, match="was not logged"):
        perform_log(home, "rotation")
    assert not database_paths(home)[0].exists()

    def explode(path):
        raise AssertionError("service must not contact the car")

    monkeypatch.setattr("core.cli.build_app", explode)
    assert main(["--home", str(home), "service", "log", "rotation"]) == 2
    assert "does not wake" in capsys.readouterr().err

    ready = tmp_path / "ready"
    record(ready, at(2026, 3, 1, 8), vehicle_state={"odometer": miles(1000)})
    logged = perform_log(ready, "rotation", now=at(2026, 3, 1, 9))
    assert "Logged tyre rotation" in logged
    assert "1000 km" in logged
    record(ready, at(2026, 3, 3, 8), vehicle_state={"odometer": miles(11500)})
    briefing = render_briefing(ready, "daily", as_json=False, on=date(2026, 3, 3), tz=UTC)
    assert "Service reminders:" in briefing
    assert "Tyre service is due" in briefing
    assert "10,000 km" in briefing
    assert "6,250 miles" in briefing
    assert "tread" in briefing
    assert "Tip:" not in briefing
    again = render_briefing(ready, "daily", as_json=False, on=date(2026, 3, 3), tz=UTC)
    assert "Service reminders:" not in again
    next_day = render_briefing(ready, "daily", as_json=False, on=date(2026, 3, 4), tz=UTC)
    assert "Service reminders:" not in next_day
    later = render_briefing(ready, "daily", as_json=False, on=date(2026, 5, 3), tz=UTC)
    assert "Service reminders:" in later
    status = json.loads(
        __import__("core.history.service", fromlist=["render_service_status"]).render_service_status(
            ready, as_json=True, on=date(2026, 3, 4), tz=UTC
        )
    )
    assert status["events"][0]["since_km"] == pytest.approx(10500)
    assert status["events"][0]["interval_reached"] is True
    assert status["events"][0]["on_cooldown"] is True
    assert LAT not in json.dumps(status)
    assert VIN not in json.dumps(status)


def test_replacement_and_maintenance_intervals(tmp_path):
    from core.history.service import perform_log

    home = tmp_path / "home"
    record(home, at(2026, 4, 1, 8), vehicle_state={"odometer": miles(2000)})
    text = perform_log(home, "replacement", now=at(2026, 4, 1, 9))
    assert "tread" in text
    assert "10000 km" in text
    record(home, at(2026, 4, 2, 8), vehicle_state={"odometer": miles(2000 + 10000)})
    briefing = render_briefing(home, "daily", as_json=False, on=date(2026, 4, 2), tz=UTC)
    assert "tread" in briefing
    assert "10,000 km" in briefing

    custom = tmp_path / "custom"
    record(custom, at(2026, 4, 1, 8), vehicle_state={"odometer": miles(0)})
    perform_log(custom, "replacement", interval_km=20000, now=at(2026, 4, 1, 9))
    record(custom, at(2026, 4, 2, 8), vehicle_state={"odometer": miles(10000)})
    early = render_briefing(custom, "daily", as_json=False, on=date(2026, 4, 2), tz=UTC)
    assert "Service reminders:" not in early
    record(custom, at(2026, 4, 3, 8), vehicle_state={"odometer": miles(20000)})
    due = render_briefing(custom, "daily", as_json=False, on=date(2026, 4, 3), tz=UTC)
    assert "20000 km" in due
    assert "tread" in due

    plain = tmp_path / "plain"
    record(plain, at(2026, 4, 1, 8), vehicle_state={"odometer": miles(500)})
    assert "No reminder interval" in perform_log(plain, "maintenance", note="filters", now=at(2026, 4, 1, 9))
    record(plain, at(2026, 6, 1, 8), vehicle_state={"odometer": miles(500 + 40000)})
    quiet = render_briefing(plain, "monthly", as_json=False, on=date(2026, 6, 1), tz=UTC)
    assert "Service reminders:" not in quiet
    from core.history.service import render_service_status

    shown = render_service_status(plain, as_json=False, on=date(2026, 6, 1), tz=UTC)
    assert "filters" in shown
    assert "No reminder interval was set." in shown


def test_maintenance_interval_reminds_once(tmp_path):
    from core.history.service import perform_log

    home = tmp_path / "home"
    record(home, at(2026, 4, 1, 8), vehicle_state={"odometer": miles(100)})
    perform_log(home, "maintenance", interval_km=5000, note="cabin filter", now=at(2026, 4, 1, 9))
    record(home, at(2026, 4, 2, 8), vehicle_state={"odometer": miles(5100)})
    first = render_briefing(home, "due", as_json=True, on=date(2026, 4, 2), tz=UTC)
    body = json.loads(first)
    assert body["selected"] == "daily"
    assert body["service_reminders"]
    assert "cabin filter" in body["service_reminders"][0]["text"]
    second = json.loads(render_briefing(home, "due", as_json=True, on=date(2026, 4, 2), tz=UTC))
    assert second["service_reminders"] == []


def test_note_rejects_coordinates_and_commands_do_not_print_them(tmp_path, monkeypatch, capsys):
    from core.errors import ConfigError
    from core.history.service import perform_log

    home = tmp_path / "home"
    record(home, at(2026, 3, 2), vehicle_state={"odometer": 10})
    with pytest.raises(ConfigError):
        perform_log(home, "maintenance", note=f"near {LAT},{LON}")
    monkeypatch.setattr("core.cli.build_app", lambda path: (_ for _ in ()).throw(AssertionError("no network")))
    monkeypatch.setattr("core.history.usage.local_today", lambda tz: date(2026, 3, 4))
    assert main(["--home", str(home), "history", "snapshots"]) == 0
    output = capsys.readouterr().out
    assert LAT not in output
    assert VIN not in output
    assert "latitude" not in output.lower()
    plain = plaintext(home).decode("latin1")
    assert LAT not in plain
    assert VIN not in plain
