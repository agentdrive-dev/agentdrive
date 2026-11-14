"""Trip advice, charging advice, briefing summaries, and charge-energy correction."""

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from core.advice import charge_advice, parse_kilometers, trip_advice
from core.cli import main
from core.errors import ConfigError
from core.family import add_member
from core.garage import load_garage, remember_car, store_for
from core.history.charts import write_builtin_png
from core.history.crypto import database_paths
from core.history.energy import plausible_energy, resolve_charge_energy
from core.history.store import history_session
from core.history.usage import render_briefing, render_list
from core.ops import App
from core.safety.catalog import schedule_blocks
from core.tariff import Tariff, TariffWindow, load_tariff
from tests.test_cars_and_family import VIN_A, VIN_B, row
from tests.test_history import UTC, at, record

import pytest


def _trip(day, km=20.0, start=80, end=70):
    stamp = f"2026-03-{day:02d}T08:00:00Z"
    return {
        "open": False,
        "started_at": stamp,
        "ended_at": stamp,
        "distance_km": km,
        "start_battery_percent": start,
        "end_battery_percent": end,
    }


def _charge(day, hour, start, end, energy, readings=None):
    started = f"2026-03-{day:02d}T{hour:02d}:00:00Z"
    return {
        "open": False,
        "started_at": started,
        "ended_at": f"2026-03-{day:02d}T{hour + 2:02d}:00:00Z",
        "start_percent": start,
        "end_percent": end,
        "energy_added_kwh": energy,
        "readings": readings or [],
    }


def _peek(temp=18, battery=60, departures=(), schedules=(), departure_section=True, charge_section=True):
    observation = SimpleNamespace(
        battery_level=battery,
        outside_temp=temp,
        departure_schedules=departures,
        departure_schedule_section_present=departure_section,
        departure_schedule_list_present=True,
        charge_schedules=schedules,
        charge_schedule_section_present=charge_section,
        charge_schedule_list_present=True,
    )
    return SimpleNamespace(state="online", observation=observation, store=None, label="")


def _window(kind, start, end, start_minutes, end_minutes, price=None):
    return TariffWindow(kind, start, end, start_minutes, end_minutes, price)


def test_resolve_keeps_a_normal_session_and_drops_a_stale_or_cumulative_counter():
    assert resolve_charge_energy([0.2, 0.5, 5.0], 30, 40) == pytest.approx(5.0)
    assert resolve_charge_energy([54.2, 0.2, 0.8, 1.2], 70, 72) == pytest.approx(1.2)
    assert resolve_charge_energy([54.2], 70, 72) is None
    assert resolve_charge_energy([17.1, 25.0, 35.8], 61, 77) == pytest.approx(18.7)
    assert plausible_energy(17.1, 37, 51) is True
    assert plausible_energy(35.8, 61, 77) is False
    assert plausible_energy(54.2, 70, 72) is False


def test_too_few_trips_are_not_guessed():
    payload = trip_advice(
        kilometers=40,
        trips=[_trip(2), _trip(3)],
        parked={"battery_percent": 70, "outside_temp_c": 15, "last_seen": "2026-03-04T08:00:00Z"},
        peek=None,
        tariff=Tariff(),
        today=date(2026, 3, 5),
        tz=UTC,
    )
    assert payload["consumption_percent_per_km"] is None
    assert payload["enough"] is None
    assert "No guess was made" in payload["text"]
    assert "2 usable trips" in payload["text"]
    assert "% of the battery per km" not in payload["text"]
    assert "was not woken" in payload["text"]


def test_trip_sufficiency_charge_limit_and_cold_preconditioning():
    trips = [_trip(2), _trip(3), _trip(4)]
    mild = trip_advice(
        kilometers=20,
        trips=trips,
        parked=None,
        peek=_peek(temp=18, battery=50),
        tariff=Tariff(),
        today=date(2026, 3, 6),
        tz=UTC,
    )
    assert mild["consumption_percent_per_km"] == pytest.approx(0.5)
    assert mild["enough"] is True
    assert mild["needed_percent"] == pytest.approx(20.0)
    assert mild["charge_limit_percent"] == 80
    assert mild["weather_factor"] == pytest.approx(1.0)
    assert "is enough" in mild["text"]
    assert "does not change the charge limit" in mild["text"]
    assert "non-waking reading" in mild["text"]

    cold = trip_advice(
        kilometers=20,
        trips=trips,
        parked=None,
        peek=_peek(temp=-2, battery=50, departures=(SimpleNamespace(enabled=True),)),
        tariff=Tariff(),
        today=date(2026, 3, 6),
        tz=UTC,
    )
    assert cold["weather_factor"] == pytest.approx(1.2)
    assert cold["needed_percent"] == pytest.approx(22.0)
    assert cold["precondition"] is True
    assert "cold" in cold["text"]
    assert "advice only" in cold["text"]
    assert "does not start preconditioning" in cold["text"]

    short = trip_advice(
        kilometers=20,
        trips=trips,
        parked=None,
        peek=_peek(temp=18, battery=15),
        tariff=Tariff(),
        today=date(2026, 3, 6),
        tz=UTC,
    )
    assert short["enough"] is False
    assert "not enough" in short["text"]

    long_trip = trip_advice(
        kilometers=300,
        trips=trips,
        parked=None,
        peek=_peek(temp=18, battery=90, departures=()),
        tariff=Tariff(),
        today=date(2026, 3, 6),
        tz=UTC,
    )
    assert long_trip["trip_limit_percent"] == 100
    assert "100%" in long_trip["text"]
    assert "No departure schedule is set" in long_trip["text"]


def test_charge_habits_tariff_and_schedule_suggestion_do_not_print_coordinates():
    charges = []
    for day, hour, start in ((2, 18, 10), (3, 18, 12), (4, 18, 8), (5, 7, 40)):
        charges.append(_charge(day, hour, start, 100, 30))
    marks = [
        {"last_seen": "2026-03-02T22:00:00Z", "battery_percent": 100, "observed_at": "2026-03-02T20:00:00Z"},
        {"last_seen": "2026-03-03T22:00:00Z", "battery_percent": 99, "observed_at": "2026-03-03T20:00:00Z"},
        {"last_seen": "2026-03-04T22:00:00Z", "battery_percent": 98, "observed_at": "2026-03-04T20:00:00Z"},
    ]
    tariff = Tariff(
        windows=(
            _window("off-peak", "22:00", "06:00", 22 * 60, 6 * 60, 0.18),
            _window("peak", "16:00", "21:00", 16 * 60, 21 * 60, 0.42),
        )
    )
    schedule = SimpleNamespace(
        enabled=True,
        start_enabled=True,
        start_time=18 * 60,
        latitude=55.1234,
        longitude=12.5678,
    )
    payload = charge_advice(
        trips=[_trip(2), _trip(3), _trip(4)],
        charges=charges,
        marks=marks,
        parked=None,
        peek=_peek(temp=18, battery=60, schedules=(schedule,)),
        tariff=tariff,
        today=date(2026, 3, 6),
        tz=UTC,
    )
    assert "full_and_left" in payload["habits"]
    assert "low_before_charge" in payload["habits"]
    assert "peak_window" in payload["habits"]
    assert "Often charging to 100% and leaving it" in payload["text"]
    assert "Often dropping very low before charging" in payload["text"]
    assert "Often charging in the peak window" in payload["text"]
    assert "22:00-06:00" in payload["text"]
    assert "outside the cheap window" in payload["text"]
    assert "needs a confirmation" in payload["text"]
    assert "does not change" in payload["text"]
    assert "55.1234" not in payload["text"]
    assert "12.5678" not in payload["text"]
    assert payload["advice_only"] is True
    assert payload["wakes_car"] is False


def test_tariff_commands_stay_in_the_config_home(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "tariff", "set", "off-peak", "22:00-06:00", "--price", "0.18"]) == 0
    assert main(["--home", str(tmp_path), "tariff", "set", "peak", "16:00-21:00", "--price", "0.42"]) == 0
    saved = load_tariff(tmp_path)
    assert [item.kind for item in saved.windows] == ["off-peak", "peak"]
    assert (tmp_path / "tariff.yaml").is_file()
    capsys.readouterr()
    assert main(["--home", str(tmp_path), "tariff", "show", "--json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["windows"][0]["price_per_kwh"] == pytest.approx(0.18)
    assert main(["--home", str(tmp_path), "tariff", "clear", "peak"]) == 0
    assert [item.kind for item in load_tariff(tmp_path).windows] == ["off-peak"]
    with pytest.raises(ConfigError):
        parse_kilometers("0")
    add_member(tmp_path, "rider")
    assert main(["--home", str(tmp_path), "--as", "rider", "tariff", "show"]) == 2
    assert "owner" in capsys.readouterr().err


def test_advice_does_not_wake_and_uses_the_selected_car(tmp_path, monkeypatch, capsys):
    from core.config import load_regions
    from core.fleet.queries import FleetReader

    class Http:
        def __init__(self, state):
            self.state = state
            self.calls = []

        def request_json(self, method, url, headers, timeout=30, body=None):
            self.calls.append(url)
            if url.rstrip("/").endswith("/vehicles"):
                return 200, {"response": [{"vin": VIN_A, "state": self.state, "display_name": "Example"}]}
            if "wake_up" in url:
                raise AssertionError("advice must not wake the car")
            return 200, {"response": {}}

    http = Http("asleep")
    regions = load_regions()
    tokens = SimpleNamespace(access_token=lambda force=False: "token")
    app = App(
        tmp_path,
        reader=FleetReader(http, regions, "na", tokens),
        tokens=tokens,
        control=SimpleNamespace(run=lambda **kwargs: (_ for _ in ()).throw(AssertionError("no command"))),
        region="na",
        history=True,
    )
    peek = app.peek(preferred=None)
    assert peek.state == "asleep"
    assert peek.observation is None
    assert not any("wake_up" in url for url in http.calls)
    assert not any("vehicle_data" in url for url in http.calls)

    rows = [row(VIN_A), row(VIN_B)]
    remember_car(tmp_path, rows, "0001", "daily")
    remember_car(tmp_path, rows, "0002", "weekend")
    garage = load_garage(tmp_path)
    weekend = store_for(tmp_path, garage, garage.by_nickname("weekend"))
    assert weekend != tmp_path
    for day in (2, 3, 4):
        record(
            weekend,
            at(2026, 3, day, 8),
            vehicle_state={"odometer": 100 + day},
            charge_state={"battery_level": 80, "charging_state": "Disconnected"},
        )
        record(
            weekend,
            at(2026, 3, day, 9),
            vehicle_state={"odometer": 110 + day},
            charge_state={"battery_level": 70, "charging_state": "Disconnected"},
        )
    monkeypatch.setattr("core.cli._peek_parked", lambda home, args: None)
    monkeypatch.setattr("core.history.usage.local_today", lambda tz: date(2026, 3, 6))
    assert main(["--home", str(tmp_path), "advice", "--car", "weekend", "trip", "20"]) == 0
    text = capsys.readouterr().out
    assert "is enough" in text or "% of the battery per km" in text
    assert main(["--home", str(tmp_path), "advice", "--car", "daily", "trip", "20"]) == 0
    other = capsys.readouterr().out
    assert "No guess was made" in other
    assert schedule_blocks("advice trip 20") is False
    assert schedule_blocks("advice charge") is False
    assert schedule_blocks("tariff set off-peak 22:00-06:00") is False
    assert schedule_blocks("charge-schedule set weekdays 22:00-06:00 1 2") is True


def test_recorded_charge_energy_uses_the_session_increase(tmp_path):
    home = tmp_path / "home"
    start = at(2026, 3, 2, 22)
    series = [
        (start, 70, 54.2),
        (start + timedelta(minutes=2), 70, 0.2),
        (start + timedelta(minutes=4), 71, 0.8),
        (start + timedelta(minutes=6), 72, 1.2),
    ]
    for moment, battery, energy in series:
        record(
            home,
            moment,
            charge_state={
                "battery_level": battery,
                "charging_state": "Charging",
                "charge_energy_added": energy,
                "charger_power": 7,
            },
            vehicle_state={"odometer": 100},
        )
    record(
        home,
        start + timedelta(minutes=8),
        charge_state={"battery_level": 72, "charging_state": "Disconnected", "charge_energy_added": 0},
        vehicle_state={"odometer": 100},
    )
    charge = json.loads(render_list(home, "charges", limit=5, as_json=True))["charges"][0]
    assert charge["energy_added_kwh"] == pytest.approx(1.2)
    assert charge["start_percent"] == 70
    assert charge["end_percent"] == 72

    home_b = tmp_path / "cumulative"
    begin = at(2026, 3, 4, 1)
    for offset, battery, energy in ((0, 61, 17.1), (2, 68, 25.0), (4, 77, 35.8)):
        record(
            home_b,
            begin + timedelta(minutes=offset),
            charge_state={
                "battery_level": battery,
                "charging_state": "Charging",
                "charge_energy_added": energy,
                "charger_power": 7,
            },
            vehicle_state={"odometer": 80},
        )
    record(
        home_b,
        begin + timedelta(minutes=6),
        charge_state={"battery_level": 77, "charging_state": "Disconnected", "charge_energy_added": 0},
        vehicle_state={"odometer": 80},
    )
    cumulative = json.loads(render_list(home_b, "charges", limit=5, as_json=True))["charges"][0]
    assert cumulative["energy_added_kwh"] == pytest.approx(18.7)


def test_legacy_bad_energy_is_left_in_place_and_kept_out_of_the_summary(tmp_path):
    home = tmp_path / "home"
    record(home, at(2026, 3, 2, 8), vehicle_state={"odometer": 10})
    with history_session(home, write=True) as conn:
        conn.execute(
            """
            INSERT INTO charges(
                started_at, ended_at, start_percent, end_percent, energy_added_kwh, plugged_in, charging, open
            ) VALUES
            ('2026-03-03T01:00:00Z', '2026-03-03T02:00:00Z', 70, 72, 54.2, 1, 1, 0),
            ('2026-03-03T03:00:00Z', '2026-03-03T04:00:00Z', 37, 51, 17.1, 1, 1, 0),
            ('2026-03-03T05:00:00Z', '2026-03-03T06:00:00Z', 61, 77, 35.8, 1, 1, 0)
            """
        )
        conn.execute(
            """
            INSERT INTO charge_samples(charge_id, observed_at, battery_percent, energy_added_kwh, charger_power_kw)
            VALUES
            (3, '2026-03-03T05:00:00Z', 61, 17.1, 7),
            (3, '2026-03-03T06:00:00Z', 77, 35.8, 7)
            """
        )
    briefing = json.loads(render_briefing(home, "weekly", as_json=True, on=date(2026, 3, 9), tz=UTC))
    with history_session(home, write=False) as conn:
        stored = [row[0] for row in conn.execute("SELECT energy_added_kwh FROM charges ORDER BY id")]
    assert stored == pytest.approx([54.2, 17.1, 35.8])
    assert briefing["summary"]["energy_added_kwh"] == pytest.approx(35.8)
    assert briefing["summary"]["energy_omitted"] == 1
    assert briefing["summary"]["energy_replaced"] == 1
    assert "does not match the battery change" in briefing["text"]
    assert "Those saved rows were not changed" in briefing["text"]
    listed = render_list(home, "charges", limit=5, as_json=False)
    assert "not counted" in listed
    assert "18.7 kWh counted" in listed


def test_weekly_summary_compares_the_previous_period_and_writes_a_chart(tmp_path):
    home = tmp_path / "home"
    record(
        home,
        at(2026, 3, 3, 8),
        vehicle_state={"odometer": 100},
        charge_state={"battery_level": 80, "charging_state": "Disconnected"},
    )
    record(
        home,
        at(2026, 3, 3, 10),
        vehicle_state={"odometer": 120},
        charge_state={"battery_level": 60, "charging_state": "Disconnected"},
    )
    record(
        home,
        at(2026, 2, 24, 8),
        vehicle_state={"odometer": 50},
        charge_state={"battery_level": 70, "charging_state": "Disconnected"},
    )
    record(
        home,
        at(2026, 2, 24, 9),
        vehicle_state={"odometer": 60},
        charge_state={"battery_level": 60, "charging_state": "Disconnected"},
    )
    text = render_briefing(home, "weekly", as_json=False, on=date(2026, 3, 9), tz=UTC)
    body = json.loads(render_briefing(home, "weekly", as_json=True, on=date(2026, 3, 9), tz=UTC))
    assert "Summary:" in text
    assert "Compared with the previous week" in text
    assert "Coverage is incomplete" in text
    assert "History is opportunistic, not a continuous record." in text
    assert body["summary"]["trip_count"] == 1
    assert body["summary"]["previous"]["trip_count"] == 1
    assert body["summary"]["coverage"]["incomplete"] is True
    chart = body["summary"]["chart"]
    dashboard = body["summary"]["dashboard"]
    assert chart.endswith(".png")
    assert "Chart:" in text
    assert "Dashboard:" in text
    png = Path(chart).read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    page = Path(dashboard).read_text(encoding="utf-8")
    assert "Week" in page and "Month" in page and "Year" in page
    assert "http://" not in page and "https://" not in page
    assert body["summary"]["chart_renderer"] == "builtin"
    fallback = tmp_path / "fallback.png"
    write_builtin_png(fallback, ["01"], [12.0], ["01"], [4.0])
    assert fallback.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    yearly = render_briefing(home, "yearly", as_json=False, on=date(2026, 1, 1), tz=UTC)
    assert "Compared with the previous year" in yearly
    monthly = render_briefing(home, "monthly", as_json=False, on=date(2026, 3, 1), tz=UTC)
    assert "Compared with the previous month" in monthly
