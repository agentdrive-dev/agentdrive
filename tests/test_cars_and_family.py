"""Several cars on one account, and family requests the owner approves."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.cli import main
from core.config import load_regions
from core.errors import ConfigError, ConfirmationRequired
from core.family import allow_actions, classify
from core.fleet.check_state import CHECK_ALERTS_NAME, load_codes
from core.fleet.queries import FleetReader
from core.fleet.snapshot import Observation
from core.garage import load_garage, remember_car, store_for
from core.history.store import record_parked_snapshot
from core.history.usage import render_list
from core.ops import App
from core.requests import REQUESTS_NAME, approve_request, create_request
from core.safety.catalog import schedule_blocks
from core.safety.confirm import Confirmation
from core.safety.gateway import Gateway

VIN_A = "TESTVN00000000001"
VIN_B = "TESTVN00000000002"
TOKEN = "access-token"
NOW = datetime(2026, 11, 14, 1, 0, tzinfo=timezone.utc)


class Control:
    def __init__(self):
        self.calls = []

    def run(self, *, vin, access_token, args, timeout=90):
        self.calls.append({"vin": vin, "args": list(args)})


class Http:
    def __init__(self, rows, data):
        self.rows = rows
        self.data = data
        self.calls = []

    def request_json(self, method, url, headers, timeout=30, body=None):
        self.calls.append(url)
        if url.rstrip("/").endswith("/vehicles"):
            return 200, {"response": self.rows}
        if "vehicle_data" in url:
            return 200, self.data
        if url.endswith("/wake_up"):
            return 200, {"response": {"state": "online"}}
        return 500, {}


def row(vin, state="online", name="One"):
    return {"vin": vin, "state": state, "display_name": name, "id": "veh-" + vin[-4:]}


def payload(**vehicle_state):
    state = {"locked": True, "sentry_mode": True, "odometer": 10}
    state.update(vehicle_state)
    return {
        "response": {
            "drive_state": {"shift_state": "P", "speed": 0},
            "charge_state": {"battery_level": 80, "charging_state": "Disconnected"},
            "vehicle_state": state,
        }
    }


def make_app(home, http, control=None, *, history=False):
    tokens = type("Tokens", (), {"access_token": staticmethod(lambda force=False: TOKEN)})()
    reader = FleetReader(http, load_regions(), "na", tokens)
    return App(
        home,
        reader=reader,
        tokens=tokens,
        control=control or Control(),
        region="na",
        history=history,
    )


def name_both(home, http):
    remember_car(home, http.rows, "0001", "daily")
    remember_car(home, http.rows, "0002", "weekend")


def test_vin_fixtures_are_seventeen_characters():
    assert len(VIN_A) == 17
    assert len(VIN_B) == 17
    assert VIN_A != VIN_B


def test_one_car_keeps_the_old_confirmation_and_ok_line(tmp_path):
    http = Http([row(VIN_A)], payload(locked=False))
    control = Control()
    app = make_app(tmp_path, http, control)
    text = app.command("unlock", preferred=None, argument=None, statement="I confirm unlock", sleep=lambda _s: None)
    assert text == "OK: unlock"
    assert " on " not in text
    assert control.calls[0]["vin"] == VIN_A
    assert control.calls[0]["args"] == ["unlock"]


def test_several_cars_refuse_without_a_default_or_nickname(tmp_path):
    http = Http([row(VIN_A, name="One"), row(VIN_B, name="Two")], payload(locked=False))
    control = Control()
    app = make_app(tmp_path, http, control)
    name_both(tmp_path, http)
    with pytest.raises(ConfigError) as caught:
        app.command("unlock", preferred=None, argument=None, statement="I confirm unlock", sleep=lambda _s: None)
    assert "no default" in str(caught.value)
    assert "will not guess" in str(caught.value)
    assert control.calls == []
    assert VIN_A not in str(caught.value)
    assert VIN_B not in str(caught.value)


def test_car_flag_selects_one_car_and_the_confirmation_names_it(tmp_path, monkeypatch, capsys):
    http = Http([row(VIN_A, name=VIN_A), row(VIN_B, name="Two")], payload(locked=False))
    control = Control()
    app = make_app(tmp_path, http, control)
    name_both(tmp_path, http)
    monkeypatch.setattr("core.cli.build_app", lambda home: app)
    monkeypatch.delenv("AGENTDRIVE_VIN", raising=False)
    monkeypatch.delenv("TESLA_VIN", raising=False)

    missing = main(["--home", str(tmp_path), "--car", "daily", "unlock", "--confirm", "I confirm unlock"])
    assert missing == 2
    assert control.calls == []
    assert "Name the car" in capsys.readouterr().err

    ok = main(
        [
            "unlock",
            "--car",
            "daily",
            "--home",
            str(tmp_path),
            "--confirm",
            "I confirm unlock on daily",
        ]
    )
    assert ok == 0
    output = capsys.readouterr().out
    assert output.strip() == "OK: unlock on daily"
    assert control.calls[0]["vin"] == VIN_A
    assert VIN_A not in output
    assert VIN_B not in output


def test_confirmation_for_the_other_car_does_not_run(tmp_path):
    http = Http([row(VIN_A), row(VIN_B)], payload(locked=False))
    control = Control()
    app = make_app(tmp_path, http, control)
    name_both(tmp_path, http)
    app.car_name = "weekend"
    with pytest.raises(ConfirmationRequired):
        app.command(
            "unlock",
            preferred=None,
            argument=None,
            statement="I confirm unlock on daily",
            sleep=lambda _s: None,
        )
    assert control.calls == []


def test_default_car_is_used_when_the_flag_is_omitted(tmp_path):
    http = Http([row(VIN_A), row(VIN_B)], payload(locked=False))
    control = Control()
    app = make_app(tmp_path, http, control)
    name_both(tmp_path, http)
    from core.garage import set_default

    set_default(tmp_path, "weekend")
    text = app.command("unlock", preferred=None, argument=None, statement="I confirm unlock on weekend", sleep=lambda _s: None)
    assert text == "OK: unlock on weekend"
    assert control.calls[0]["vin"] == VIN_B


def test_car_list_does_not_wake_and_masks_the_vin(tmp_path, monkeypatch, capsys):
    http = Http([row(VIN_A, state="asleep", name=VIN_A), row(VIN_B, state="online", name="Two")], payload())
    app = make_app(tmp_path, http)
    name_both(tmp_path, http)
    monkeypatch.setattr("core.cli.build_app", lambda home: app)
    assert main(["--home", str(tmp_path), "cars"]) == 0
    output = capsys.readouterr().out
    assert "does not wake" in output
    assert "daily" in output
    assert "weekend" in output
    assert "****0001" in output
    assert "****0002" in output
    assert VIN_A not in output
    assert VIN_B not in output
    assert "asleep" in output
    assert not any(url.endswith("/wake_up") for url in http.calls)
    assert not any("vehicle_data" in url for url in http.calls)


def test_history_and_alerts_stay_with_the_car_they_were_saved_for(tmp_path):
    record_parked_snapshot(
        tmp_path,
        {"response": {"vehicle_state": {"odometer": 12}, "drive_state": {"shift_state": "P", "speed": 0}}},
        Observation(shift_state="P", speed=0, battery_level=55, locked=True),
        tyre_bar=2.5,
        low_battery_percent=30,
        now=NOW,
    )
    from core.fleet.check_state import save_codes

    save_codes(tmp_path, ["unlocked"])
    rows = [row(VIN_A), row(VIN_B)]
    remember_car(tmp_path, rows, "0001", "daily")
    remember_car(tmp_path, rows, "0002", "weekend")
    garage = load_garage(tmp_path)
    daily = garage.by_nickname("daily")
    weekend = garage.by_nickname("weekend")
    daily_store = store_for(tmp_path, garage, daily)
    weekend_store = store_for(tmp_path, garage, weekend)
    assert daily_store == tmp_path
    assert weekend_store != tmp_path

    daily_rows = json.loads(render_list(tmp_path, "snapshots", limit=5, as_json=True, store=daily_store))
    weekend_rows = json.loads(render_list(tmp_path, "snapshots", limit=5, as_json=True, store=weekend_store))
    assert daily_rows["total"] == 1
    assert weekend_rows["total"] == 0
    assert load_codes(tmp_path) == {"unlocked"}
    assert load_codes(weekend_store) == set()
    assert VIN_A not in json.dumps(daily_rows)

    record_parked_snapshot(
        weekend_store,
        {"response": {"vehicle_state": {"odometer": 40}, "drive_state": {"shift_state": "P", "speed": 0}}},
        Observation(shift_state="P", speed=0, battery_level=70, locked=True),
        tyre_bar=2.5,
        low_battery_percent=30,
        now=NOW + timedelta(hours=1),
    )
    assert json.loads(render_list(tmp_path, "snapshots", limit=5, as_json=True, store=daily_store))["total"] == 1
    assert json.loads(render_list(tmp_path, "snapshots", limit=5, as_json=True, store=weekend_store))["total"] == 1


def test_alert_dedupe_is_per_car(tmp_path):
    http = Http([row(VIN_A), row(VIN_B)], payload(locked=False))
    name_both(tmp_path, http)
    app = make_app(tmp_path, http, history=False)
    app.car_name = "daily"
    first = app.check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
    assert first.exit_code == 1
    assert [item.code for item in first.alerts] == ["unlocked"]
    again = app.check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
    assert again.exit_code == 0
    app.car_name = "weekend"
    other = app.check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
    assert other.exit_code == 1
    garage = load_garage(tmp_path)
    weekend_store = store_for(tmp_path, garage, garage.by_nickname("weekend"))
    assert (tmp_path / CHECK_ALERTS_NAME).is_file()
    assert (weekend_store / CHECK_ALERTS_NAME).is_file()
    assert load_codes(tmp_path) == {"unlocked"}
    assert load_codes(weekend_store) == {"unlocked"}
    assert not any(url.endswith("/wake_up") for url in http.calls)


def test_history_selection_does_not_contact_the_car(tmp_path, monkeypatch, capsys):
    rows = [row(VIN_A), row(VIN_B)]
    remember_car(tmp_path, rows, "0001", "daily")
    remember_car(tmp_path, rows, "0002", "weekend")

    def explode(home):
        raise AssertionError("history must not contact the car")

    monkeypatch.setattr("core.cli.build_app", explode)
    assert main(["--home", str(tmp_path), "history", "snapshots", "--json"]) == 2
    assert "no default" in capsys.readouterr().err
    assert main(["--home", str(tmp_path), "history", "--car", "weekend", "snapshots", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["total"] == 0


def test_family_request_is_not_run_until_the_owner_approves_it_once(tmp_path, monkeypatch, capsys):
    http = Http([row(VIN_A), row(VIN_B)], payload(locked=False))
    control = Control()
    app = make_app(tmp_path, http, control)
    name_both(tmp_path, http)
    monkeypatch.setattr("core.cli.build_app", lambda home: app)
    assert main(["--home", str(tmp_path), "family", "add", "rider"]) == 0
    assert main(["--home", str(tmp_path), "family", "allow", "rider", "unlock"]) == 0
    assert main(["--home", str(tmp_path), "family", "allow", "rider", "charging"]) == 0
    allowed = capsys.readouterr().out
    assert "may request unlock" in allowed
    assert "not run until the owner approves" in allowed
    assert "may request charge-amps" in allowed
    assert classify(tmp_path, "rider", "unlock") == "request"

    created = main(
        [
            "--home",
            str(tmp_path),
            "--as",
            "rider",
            "--car",
            "daily",
            "unlock",
            "--confirm",
            "I confirm unlock on daily",
        ]
    )
    assert created == 0
    relay = capsys.readouterr().out
    assert "Pending request" in relay
    assert "from rider on daily: unlock" in relay
    assert "This action was not run." in relay
    assert "Tell the owner:" in relay
    assert "request approve" in relay
    assert "request deny" in relay
    assert VIN_A not in relay
    assert control.calls == []
    saved = json.loads((tmp_path / REQUESTS_NAME).read_text(encoding="utf-8"))
    assert VIN_A not in json.dumps(saved)
    assert "vin" not in json.dumps(saved)
    request_id = saved["requests"][0]["id"]

    listed = main(["--home", str(tmp_path), "request", "list"])
    assert listed == 0
    listing = capsys.readouterr().out
    assert request_id in listing
    assert "pending" in listing
    assert "rider" in listing
    assert VIN_A not in listing

    approved = main(["--home", str(tmp_path), "request", "approve", request_id])
    assert approved == 0
    assert "Approved" in capsys.readouterr().out
    assert control.calls == [{"vin": VIN_A, "args": ["unlock"]}]

    control.calls.clear()
    again = main(["--home", str(tmp_path), "request", "approve", request_id])
    assert again == 2
    assert "already used" in capsys.readouterr().err
    assert control.calls == []


def test_deny_expiry_and_self_approval_never_run_the_command(tmp_path, monkeypatch, capsys):
    http = Http([row(VIN_A)], payload(locked=False))
    control = Control()
    app = make_app(tmp_path, http, control)
    remember_car(tmp_path, http.rows, "0001", "daily")
    monkeypatch.setattr("core.cli.build_app", lambda home: app)
    assert main(["--home", str(tmp_path), "family", "add", "rider"]) == 0
    assert main(["--home", str(tmp_path), "family", "allow", "rider", "unlock"]) == 0
    capsys.readouterr()

    assert main(["--home", str(tmp_path), "--as", "rider", "unlock"]) == 0
    denied_id = json.loads((tmp_path / REQUESTS_NAME).read_text(encoding="utf-8"))["requests"][0]["id"]
    assert main(["--home", str(tmp_path), "request", "deny", denied_id]) == 0
    assert "Denied" in capsys.readouterr().out
    assert main(["--home", str(tmp_path), "request", "approve", denied_id]) == 2
    assert "denied" in capsys.readouterr().err
    assert control.calls == []

    car_id = load_garage(tmp_path).by_nickname("daily").id
    expired = create_request(
        tmp_path,
        member="rider",
        car="daily",
        car_id=car_id,
        label="daily",
        command="unlock",
        argument=None,
        extra=None,
        detail=None,
        now=NOW,
    )
    expired_id = expired.split()[2]
    with pytest.raises(ConfigError) as caught:
        approve_request(
            tmp_path,
            expired_id,
            actor="",
            execute=lambda record: (_ for _ in ()).throw(AssertionError("expired request ran")),
            now=NOW + timedelta(minutes=10),
        )
    assert "expired" in str(caught.value)

    fresh = create_request(
        tmp_path,
        member="rider",
        car="daily",
        car_id=car_id,
        label="daily",
        command="unlock",
        argument=None,
        extra=None,
        detail=None,
        now=NOW,
    )
    fresh_id = fresh.split()[2]
    with pytest.raises(ConfigError) as own:
        approve_request(tmp_path, fresh_id, actor="rider", execute=lambda record: "ran", now=NOW)
    assert "cannot approve their own request" in str(own.value)
    with pytest.raises(ConfigError) as other:
        approve_request(tmp_path, fresh_id, actor="guest", execute=lambda record: "ran", now=NOW)
    assert "Only the owner" in str(other.value)

    assert main(["--home", str(tmp_path), "--as", "rider", "unlock"]) == 0
    own_id = json.loads((tmp_path / REQUESTS_NAME).read_text(encoding="utf-8"))["requests"][-1]["id"]
    assert main(["--as", "rider", "--home", str(tmp_path), "request", "approve", own_id]) == 2
    assert "cannot approve their own request" in capsys.readouterr().err
    assert control.calls == []


def test_a_low_risk_grant_runs_and_a_missing_grant_does_not(tmp_path, monkeypatch):
    http = Http([row(VIN_A)], payload())
    control = Control()
    app = make_app(tmp_path, http, control)
    monkeypatch.setattr("core.cli.build_app", lambda home: app)
    assert main(["--home", str(tmp_path), "family", "add", "rider"]) == 0
    assert main(["--home", str(tmp_path), "--as", "rider", "lock"]) == 2
    assert control.calls == []
    allow_actions(tmp_path, "rider", "lock")
    assert main(["--home", str(tmp_path), "--as", "rider", "lock"]) == 0
    assert control.calls[0]["args"] == ["lock"]
    assert not (tmp_path / REQUESTS_NAME).exists()


def test_approval_still_refuses_a_car_that_is_not_parked(tmp_path):
    http = Http(
        [row(VIN_A)],
        {"response": {"drive_state": {"shift_state": "D", "speed": 4}, "vehicle_state": {"locked": True}}},
    )
    control = Control()
    app = make_app(tmp_path, http, control)
    remember_car(tmp_path, http.rows, "0001", "daily")
    car = load_garage(tmp_path).by_nickname("daily")
    text = create_request(
        tmp_path,
        member="rider",
        car="daily",
        car_id=car.id,
        label="daily",
        command="unlock",
        argument=None,
        extra=None,
        detail=None,
        now=NOW,
    )
    request_id = text.split()[2]

    def execute(record):
        app.car_name = "daily"
        return app.command("unlock", preferred=car.vin, argument=None, statement="I confirm unlock on daily", sleep=lambda _s: None)

    with pytest.raises(Exception):
        approve_request(tmp_path, request_id, actor="", execute=execute, now=NOW)
    assert control.calls == []
    with pytest.raises(ConfigError) as caught:
        approve_request(tmp_path, request_id, actor="", execute=execute, now=NOW)
    assert "already used" in str(caught.value)
    assert control.calls == []


def test_a_schedule_cannot_approve_a_request():
    assert schedule_blocks("request approve ab12cd34")
    assert schedule_blocks("request deny ab12cd34")
    assert schedule_blocks("check") is False


def test_gateway_challenge_names_the_car():
    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, *, vin, access_token, args, timeout=45):
            self.calls.append(args)

    class Adapter:
        def __init__(self, statement):
            self.statement = statement
            self.last = None

        def confirm(self, challenge):
            self.last = challenge
            return Confirmation(
                action_id=challenge.action_id,
                command=challenge.command,
                accepted=True,
                source="owner",
                statement=self.statement,
            )

    runner = Runner()
    adapter = Adapter("I confirm unlock")
    with pytest.raises(ConfirmationRequired):
        Gateway(runner).execute(
            "unlock",
            shift_state="P",
            speed=0,
            adapter=adapter,
            via_schedule=False,
            vin="VIN",
            access_token="token",
            observe=lambda: Observation(shift_state="P", speed=0, locked=False),
            car="daily",
        )
    assert adapter.last.summary == "unlock on daily"
    assert adapter.last.car == "daily"
    assert runner.calls == []
