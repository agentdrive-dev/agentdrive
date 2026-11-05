"""Refresh window, form encoding, and the read-only health check."""

import base64
import fcntl
import io
import json
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from adapters.grokbot import GrokBotAdapter, INSTALL_NOTE
from core.cli import build_parser, main
from core.config import load_regions
from core.errors import ConfigError, ConfirmationRequired, NotParked, ScheduleBlocked, TokenError
from core.fleet.commands import CommandResult
from core.fleet.credentials import load_client_credentials
from core.fleet.health import evaluate_alerts
from core.fleet.http import UrllibClient
from core.fleet.queries import FleetReader
from core.fleet.snapshot import Observation
from core.fleet.tokens import (
    REFRESH_WITHIN,
    AtomicTokenStore,
    FleetTokenRefresher,
    TokenBundle,
    TokenManager,
    bundle_from_token_response,
)
from core.ops import PUBLIC_COMMANDS, App, format_check_json, format_status, parse_limit, parse_temp
from core.update import fingerprint, update_install
from tests.test_safety import execute, parked

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
VIN = "SECRET-VIN-VALUE"
TOKEN = "access-token"


def make_jwt(when: datetime) -> str:
    body = json.dumps({"exp": int(when.timestamp())}).encode()
    payload = base64.urlsafe_b64encode(body).decode().rstrip("=")
    return f"header.{payload}.sig"


def bundle(access="access-old", refresh="refresh-old", access_in=timedelta(hours=8), refresh_in=timedelta(days=90)):
    return TokenBundle(
        access_token=access,
        refresh_token=refresh,
        access_expires_at=(NOW + access_in).isoformat(),
        refresh_expires_at=(NOW + refresh_in).isoformat(),
        region="na",
    )


class Refresher:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def refresh(self, refresh_token, *, client_id, audience):
        self.calls.append(refresh_token)
        return self.payload


class Control:
    def __init__(self):
        self.calls = []

    def run(self, *, vin, access_token, args, timeout=90):
        self.calls.append({"vin": vin, "token": access_token, "args": list(args)})
        return CommandResult(0, "")


class Http:
    def __init__(self, listed, data=None, polls=None):
        self.listed = listed
        self.data = data or {"response": {}}
        self.polls = list(polls or [])
        self.calls = []

    def request_json(self, method, url, headers, timeout=30, body=None):
        self.calls.append((method, url, body))
        if "vehicle_data" in url:
            return 200, self.data
        if url.endswith("/wake_up"):
            return 200, {"response": {"state": "online"}}
        if url.endswith("/fleet_status"):
            return 200, {"response": {"unpaired_vins": [], "key_paired_vins": []}}
        if url.rstrip("/").endswith("/vehicles"):
            return 200, self.listed
        if method == "GET" and "/vehicles/" in url:
            if self.polls:
                return 200, self.polls.pop(0)
            return 200, {"response": {"state": "asleep"}}
        return 500, {}

    def post_form(self, url, fields, timeout=30):
        return {}


def listed(state, **extra):
    row = {"vin": VIN, "state": state, "display_name": "Example"}
    row.update(extra)
    return {"response": [row]}


def vehicle_payload(**parts):
    response = {
        "drive_state": {"shift_state": "P", "speed": 0},
        "charge_state": {"battery_level": 80, "battery_range": 100, "charge_limit_soc": 80, "charging_state": "Disconnected"},
        "climate_state": {"is_climate_on": False, "driver_temp_setting": 21, "inside_temp": 20, "outside_temp": 15},
        "vehicle_state": {
            "locked": True,
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
        },
    }
    for name, value in parts.items():
        response.setdefault(name, {}).update(value)
    return {"response": response}


def make_app(http, control=None):
    regions = load_regions()
    tokens = SimpleNamespace(access_token=lambda force=False: TOKEN)
    reader = FleetReader(http, regions, "na", tokens)
    return App(Path("."), reader=reader, tokens=tokens, control=control or Control(), region="na")


def urls(http):
    return [url for _method, url, _body in http.calls]


def test_jwt_inside_thirty_minutes_refreshes_even_if_the_stored_expiry_is_later(tmp_path):
    path = tmp_path / "tokens.json"
    AtomicTokenStore(path).save_atomic(bundle(access=make_jwt(NOW + timedelta(minutes=10)), access_in=timedelta(hours=8)))
    refresher = Refresher({"access_token": "access-new", "refresh_token": "refresh-new", "expires_in": 28800})
    manager = TokenManager(AtomicTokenStore(path), refresher, client_id="client", audience="aud", clock=lambda: NOW)
    assert manager.access_token() == "access-new"
    assert refresher.calls == ["refresh-old"]
    assert REFRESH_WITHIN == timedelta(minutes=30)


def test_jwt_beyond_thirty_minutes_skips_refresh_even_if_the_stored_expiry_is_past(tmp_path):
    path = tmp_path / "tokens.json"
    access = make_jwt(NOW + timedelta(hours=2))
    AtomicTokenStore(path).save_atomic(bundle(access=access, access_in=timedelta(minutes=-5)))
    refresher = Refresher({"access_token": "access-new", "refresh_token": "refresh-new"})
    manager = TokenManager(AtomicTokenStore(path), refresher, client_id="client", audience="aud", clock=lambda: NOW)
    assert manager.access_token() == access
    assert refresher.calls == []


def test_a_second_caller_reloads_the_saved_token(tmp_path):
    path = tmp_path / "tokens.json"
    AtomicTokenStore(path).save_atomic(bundle(access=make_jwt(NOW - timedelta(seconds=5)), access_in=timedelta(minutes=-1)))
    fresh = make_jwt(NOW + timedelta(hours=3))
    first = Refresher({"access_token": fresh, "refresh_token": "refresh-new", "expires_in": 10800})
    TokenManager(AtomicTokenStore(path), first, client_id="c", audience="a", clock=lambda: NOW).access_token()
    second = Refresher({"access_token": "should-not-be-used", "refresh_token": "other"})
    got = TokenManager(AtomicTokenStore(path), second, client_id="c", audience="a", clock=lambda: NOW).access_token()
    assert first.calls == ["refresh-old"]
    assert second.calls == []
    assert got == fresh


def test_refresh_holds_an_exclusive_lock(tmp_path):
    path = tmp_path / "tokens.json"
    AtomicTokenStore(path).save_atomic(bundle(access_in=timedelta(minutes=-1)))
    lock_path = tmp_path / ".refresh.lock"
    held = {}

    class Locked(Refresher):
        def refresh(self, refresh_token, *, client_id, audience):
            handle = open(lock_path, encoding="utf-8")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                held["exclusive"] = True
            else:
                held["exclusive"] = False
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
            return super().refresh(refresh_token, client_id=client_id, audience=audience)

    refresher = Locked({"access_token": "access-new", "refresh_token": "refresh-new", "expires_in": 28800})
    TokenManager(AtomicTokenStore(path), refresher, client_id="c", audience="a", clock=lambda: NOW).access_token()
    assert held["exclusive"] is True
    assert (lock_path.stat().st_mode & 0o777) == 0o600


def test_refresh_without_a_new_refresh_token_raises_when_there_is_no_previous_one():
    with pytest.raises(TokenError):
        bundle_from_token_response({"access_token": "access-new"}, region="na", now=NOW, previous=None)


def test_refresh_form_is_url_encoded_and_keeps_the_old_refresh_token(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    AtomicTokenStore(path).save_atomic(bundle(access_in=timedelta(minutes=-1)))
    captured = {}

    class Response:
        def read(self):
            return json.dumps({"access_token": "access-new", "expires_in": 28800, "token_type": "Bearer"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=30):
        captured["url"] = request.full_url
        captured["body"] = request.data.decode()
        captured["type"] = request.get_header("Content-type")
        return Response()

    monkeypatch.setattr("core.fleet.http.urlopen", fake_urlopen)
    regions = load_regions()
    http = UrllibClient()
    manager = TokenManager(
        AtomicTokenStore(path),
        FleetTokenRefresher(http, regions, client_secret="a&b=c"),
        client_id="client",
        audience="https://example.invalid/audience",
        clock=lambda: NOW,
    )
    assert manager.access_token() == "access-new"
    assert captured["url"].startswith(regions["shared"]["token_host"])
    assert captured["url"].endswith(regions["shared"]["token_path"])
    assert captured["type"] == "application/x-www-form-urlencoded"
    assert "client_secret=a%26b%3Dc" in captured["body"]
    assert "a&b=c" not in captured["body"]
    assert "grant_type=refresh_token" in captured["body"]
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "refresh-old"
    assert saved["access_token"] == "access-new"


def test_legacy_token_file_without_expiry_fields_loads(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps(
            {
                "access_token": make_jwt(NOW + timedelta(hours=2)),
                "refresh_token": "refresh-legacy",
                "expires_in": 28800,
                "obtained_at": int(NOW.timestamp()),
            }
        ),
        encoding="utf-8",
    )
    loaded = AtomicTokenStore(path).load()
    assert loaded.refresh_token == "refresh-legacy"
    assert loaded.access_token.startswith("header.")
    assert loaded.refresh_expires_at


def test_client_credentials_come_from_env_or_a_private_file(tmp_path):
    loose = tmp_path / "client.json"
    loose.write_text(json.dumps({"client_id": "from-file", "client_secret": "sec&ret"}), encoding="utf-8")
    loose.chmod(0o644)
    with pytest.raises(ConfigError):
        load_client_credentials(tmp_path, env={})
    loose.chmod(0o600)
    assert load_client_credentials(tmp_path, env={}) == ("from-file", "sec&ret")
    assert load_client_credentials(tmp_path, env={"AGENTDRIVE_CLIENT_ID": "from-env", "AGENTDRIVE_CLIENT_SECRET": "env-secret"}) == (
        "from-env",
        "env-secret",
    )


def test_check_does_not_wake_a_sleeping_vehicle():
    http = Http(listed("asleep"))
    report = make_app(http).check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
    assert report.skipped is True
    assert report.exit_code == 0
    assert report.state == "asleep"
    rendered = format_check_json(report)
    assert VIN not in rendered
    assert TOKEN not in rendered
    assert not any("wake_up" in url or "vehicle_data" in url for url in urls(http))


def test_check_reports_actionable_alerts_from_vehicle_data():
    http = Http(
        listed("online"),
        vehicle_payload(
            charge_state={"battery_level": 12, "charging_state": "Disconnected"},
            vehicle_state={
                "locked": False,
                "df": 1,
                "tpms_pressure_fl": 2.1,
                "tpms_soft_warning_fr": True,
            },
        ),
    )
    report = make_app(http).check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
    codes = [item.code for item in report.alerts]
    assert report.exit_code == 1
    assert "tpms_warning" in codes
    assert "tyre_pressure" in codes
    assert "low_battery" in codes
    assert "unlocked" in codes
    assert "open" in codes
    rendered = format_check_json(report)
    assert VIN not in rendered
    assert TOKEN not in rendered
    assert not any(url.endswith("/wake_up") for url in urls(http))


def test_check_ignores_a_zero_pressure_a_charging_battery_and_an_open_door_while_driving():
    quiet = evaluate_alerts(
        Observation(
            shift_state="D",
            speed=5,
            locked=False,
            open_closure=True,
            battery_level=10,
            charging_state="Charging",
            tyre_fl=0,
            tyre_fr=2.9,
        ),
        tyre_bar=2.5,
        low_battery_percent=30,
    )
    assert quiet == []
    starting = evaluate_alerts(
        Observation(battery_level=10, charging_state="Starting", shift_state="P", speed=0, locked=True),
        tyre_bar=2.5,
        low_battery_percent=30,
    )
    assert starting == []
    fault = evaluate_alerts(
        Observation(battery_level=80, charging_state="NoPower", shift_state="P", speed=0, locked=True),
        tyre_bar=2.5,
        low_battery_percent=30,
    )
    assert [item.code for item in fault] == ["charge_error"]


def test_status_wakes_and_polls_then_masks_the_vin_and_prints_tyres():
    http = Http(
        listed("asleep"),
        vehicle_payload(),
        polls=[{"response": {"state": "asleep"}}, {"response": {"state": "online"}}],
    )
    sleeps = []
    text = make_app(http).status(preferred=None, sleep=lambda seconds: sleeps.append(seconds))
    assert sleeps == [3]
    assert sum(1 for url in urls(http) if url.endswith("/wake_up")) == 1
    assert VIN not in text
    assert TOKEN not in text
    assert "****ALUE" in text
    assert "Tyres (bar): FL 2.9 FR 2.9 RL 2.9 RR 2.9" in text
    assert "TPMS: ok" in text
    assert "161 km" in text
    assert "Example" in text


def test_status_gives_up_after_forty_five_seconds():
    http = Http(listed("asleep"), polls=[{"response": {"state": "asleep"}}] * 20)
    sleeps = []
    with pytest.raises(Exception) as caught:
        make_app(http).status(preferred=None, sleep=lambda seconds: sleeps.append(seconds))
    assert "45s" in str(caught.value)
    assert sleeps == [3] * 15
    assert sum(1 for url in urls(http) if url.endswith("/wake_up")) == 1
    assert VIN not in str(caught.value)


def test_ranges_and_signed_commands(monkeypatch):
    monkeypatch.setattr("core.ops.sys.stdin.isatty", lambda: False)
    assert parse_temp("15") == "15"
    assert parse_temp("28") == "28"
    assert parse_temp("21.5") == "21.5"
    with pytest.raises(ConfigError):
        parse_temp("14.9")
    with pytest.raises(ConfigError):
        parse_temp("28.1")
    assert parse_limit("50") == "50"
    assert parse_limit("100") == "100"
    with pytest.raises(ConfigError):
        parse_limit("49")
    with pytest.raises(ConfigError):
        parse_limit("101")
    assert "trunk" not in PUBLIC_COMMANDS
    assert "frunk" not in PUBLIC_COMMANDS

    http = Http(listed("online"), vehicle_payload())
    control = Control()
    app = make_app(http, control)
    with pytest.raises(ConfigError):
        app.command("set-temp", preferred=None, argument="10", statement=None)
    assert control.calls == []
    assert http.calls == []

    text = app.command("flash", preferred=None, argument=None, statement=None, sleep=lambda _seconds: None)
    assert text == "OK: flash"
    assert control.calls[0]["args"] == ["flash-lights"]
    assert control.calls[0]["vin"] == VIN
    assert TOKEN not in text

    control.calls.clear()
    with pytest.raises(ConfirmationRequired):
        app.command("unlock", preferred=None, argument=None, statement=None, sleep=lambda _seconds: None)
    assert control.calls == []

    http.data = vehicle_payload(vehicle_state={"locked": False})
    unlocked = app.command("unlock", preferred=None, argument=None, statement="I confirm unlock", sleep=lambda _seconds: None)
    assert unlocked == "OK: unlock"
    assert control.calls[-1]["args"] == ["unlock"]

    control.calls.clear()
    app.command("wake", preferred=None, argument=None, statement=None)
    assert control.calls[0]["args"] == ["wake"]
    assert not any("vehicle_data" in url or url.endswith("/wake_up") for url in urls(http)[-1:])


def test_set_temp_uses_the_signed_command_arguments():
    _result, runner = execute("climate_set_temp", extra=["21"], after=parked(driver_temp=21))
    assert runner.calls[0]["args"] == ["climate-set-temp", "21"]


def test_driving_refuses_a_command_before_the_tool_runs():
    http = Http(listed("online"), vehicle_payload(drive_state={"shift_state": "D", "speed": 10}))
    control = Control()
    with pytest.raises(NotParked):
        make_app(http, control).command("honk", preferred=None, argument=None, statement=None, sleep=lambda _seconds: None)
    assert control.calls == []


def test_fleet_status_posts_the_vehicle_list():
    http = Http(listed("online"))
    regions = load_regions()
    reader = FleetReader(http, regions, "na", SimpleNamespace(access_token=lambda force=False: TOKEN))
    reader.fleet_status([VIN])
    method, url, body = http.calls[-1]
    assert method == "POST"
    assert url.endswith("/fleet_status")
    assert json.loads(body.decode()) == {"vins": [VIN]}


def test_cli_rejects_trunk_and_bad_ranges(tmp_path, capsys):
    choices = set(build_parser()._subparsers._group_actions[0].choices)
    for name in (
        "status",
        "wake",
        "flash",
        "honk",
        "lock",
        "unlock",
        "climate-on",
        "climate-off",
        "set-temp",
        "charge-start",
        "charge-stop",
        "charge-limit",
        "check",
        "update",
        "refresh",
    ):
        assert name in choices
    assert "trunk" not in choices
    assert "frunk" not in choices
    assert main(["--home", str(tmp_path), "set-temp", "10"]) == 2
    assert "15-28" in capsys.readouterr().err
    assert main(["--home", str(tmp_path), "charge-limit", "40"]) == 2


def test_check_cli_json_uses_thresholds_and_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENTDRIVE_VIN", raising=False)
    monkeypatch.delenv("TESLA_VIN", raising=False)
    seen = {}

    class Fake:
        def check(self, **kwargs):
            seen.update(kwargs)
            from core.fleet.health import Alert, CheckReport

            return CheckReport(
                ok=False,
                state="online",
                skipped=False,
                alerts=[Alert("low_battery", "Battery is at 10 percent and the vehicle is not charging.")],
                tyre_bar=kwargs["tyre_bar"],
                low_battery_percent=kwargs["low_battery_percent"],
            )

    monkeypatch.setattr("core.cli.build_app", lambda home: Fake())
    code = main(["--home", str(tmp_path), "check", "--json", "--tyre-threshold", "3", "--battery-threshold", "40"])
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["alerts"][0]["code"] == "low_battery"
    assert seen == {"preferred": None, "tyre_bar": 3.0, "low_battery_percent": 40}
    assert VIN not in json.dumps(payload)


def test_status_line_masks_a_vin():
    text = format_status(
        {"display_name": "Example", "state": "online"},
        Observation(battery_level=70, tyre_fl=2.4, tpms_soft_warning=True),
        VIN,
    )
    assert VIN not in text
    assert "****ALUE" in text
    assert "TPMS: warning" in text


def test_grokbot_install_tells_the_bot_to_schedule_a_health_check(tmp_path, capsys):
    assert main(["--home", str(tmp_path), "adapter", "install", "--adapter", "grokbot"]) == 0
    output = capsys.readouterr().out
    assert "agentdrive check --json" in output
    assert INSTALL_NOTE in output
    grok = GrokBotAdapter(tmp_path, lambda challenge: f"I confirm {challenge.command}")
    grok.schedule("health-check", "every 6 hours", {"command": "check", "json": True})
    with pytest.raises(ScheduleBlocked):
        grok.schedule("nope", "22:00", {"command": "unlock"})


def test_install_script_builds_the_signed_command_tool_from_source():
    text = Path("setup/install.sh").read_text(encoding="utf-8")
    assert "go build ./cmd/tesla-control" in text
    assert "github.com/teslamotors/vehicle-command" in text
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        assert "go install" not in line
        assert "@latest" not in line


def _archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        def add(name, data):
            payload = data.encode()
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

        add("agentdrive-main/README.md", "updated\n")
        add("agentdrive-main/tokens.json", "not-the-owner\n")
        add("agentdrive-main/../../escaped.txt", "nope\n")
    return buffer.getvalue()


def test_update_from_an_archive_leaves_private_config_alone(tmp_path):
    root = tmp_path / "repo"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    (home / "tokens.json").write_text('{"access_token":"owner"}\n', encoding="utf-8")
    (home / "client.json").write_text('{"client_id":"cid"}\n', encoding="utf-8")
    (home / "host.yaml").write_text("adapter: grokbot\n", encoding="utf-8")
    keys = home / "keys"
    keys.mkdir()
    (keys / "private-key.pem").write_text("private\n", encoding="utf-8")
    (home / "vehicles.yaml").write_text("owner: true\n", encoding="utf-8")
    before = fingerprint(home)
    calls = []

    def runner(argv):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    source = update_install(root, home, runner=runner, fetch=lambda url: _archive())
    assert source == "download"
    assert calls == [["bash", str(root.resolve() / "setup" / "install.sh"), "--update"]]
    assert fingerprint(home) == before
    assert (home / "tokens.json").read_text(encoding="utf-8").startswith('{"access_token":"owner"}')
    assert (root / "README.md").read_text(encoding="utf-8") == "updated\n"
    assert (root / "tokens.json").read_text(encoding="utf-8") == "not-the-owner\n"
    assert not (tmp_path / "escaped.txt").exists()
    assert not (root / "escaped.txt").exists()


def test_update_from_git_does_not_download_and_stops_if_private_config_changes(tmp_path):
    root = tmp_path / "repo"
    home = tmp_path / "home"
    root.mkdir()
    (root / ".git").mkdir()
    home.mkdir()
    (home / "host.yaml").write_text("adapter: template\n", encoding="utf-8")
    calls = []

    def runner(argv):
        calls.append(list(argv))
        if argv[0] == "git":
            (home / "host.yaml").write_text("adapter: changed\n", encoding="utf-8")
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=0)

    with pytest.raises(ConfigError):
        update_install(root, home, runner=runner, fetch=lambda url: (_ for _ in ()).throw(AssertionError("no download")))
    assert calls[0][:3] == ["git", "-C", str(root.resolve())]
    assert "pull" in calls[0]
