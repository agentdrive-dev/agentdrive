"""External speaker: confirmation, park, quiet hours, override, and family requests."""

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

from core.boombox import (
    OVERRIDE_PHRASE,
    active_window,
    format_quiet,
    load_quiet,
    set_quiet,
)
from core.cli import _normalize_global, build_parser, main
from core.config import load_regions
from core.errors import ConfigError, ConfirmationRequired, NotParked
from core.family import classify, expand_action
from core.fleet.queries import FleetReader
from core.garage import remember_car
from core.ops import App
from core.requests import REQUESTS_NAME
from core.safety.catalog import load_specs, schedule_blocks


def _patch_module():
    path = ROOT / "setup" / "patch_tesla_control_boombox.py"
    spec = importlib.util.spec_from_file_location("patch_tesla_control_boombox", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

ROOT = Path(__file__).resolve().parents[1]
VIN = "TESTVN00000000001"
TOKEN = "access-token"
NIGHT = datetime(2026, 11, 14, 3, 15)
DAY = datetime(2026, 11, 14, 8, 5)
EDGE = datetime(2026, 11, 14, 6, 0)


class Control:
    def __init__(self):
        self.calls = []

    def run(self, *, vin, access_token, args, timeout=90):
        self.calls.append({"vin": vin, "args": list(args)})


class Http:
    def __init__(self, state="online", shift="P", speed=0):
        self.state = state
        self.shift = shift
        self.speed = speed
        self.urls = []

    def request_json(self, method, url, headers, timeout=30, body=None):
        self.urls.append(url)
        if url.rstrip("/").endswith("/vehicles"):
            return 200, {"response": [{"vin": VIN, "state": self.state, "display_name": "Sunny", "id": "veh-0001"}]}
        if url.endswith("/wake_up"):
            return 200, {"response": {"state": "online", "id": "veh-0001", "vin": VIN}}
        if "vehicle_data" in url:
            return 200, {
                "response": {
                    "drive_state": {"shift_state": self.shift, "speed": self.speed},
                    "vehicle_state": {"locked": True},
                    "charge_state": {},
                }
            }
        return 200, {"response": {"state": "online", "id": "veh-0001", "vin": VIN}}


def make_app(home, http=None, control=None):
    transport = http or Http()
    runner = control or Control()
    tokens = type("Tokens", (), {"access_token": staticmethod(lambda force=False: TOKEN)})()
    reader = FleetReader(transport, load_regions(), "na", tokens)
    return App(home, reader=reader, tokens=tokens, control=runner, region="na"), transport, runner


def play(home, sound="fart", *, statement=None, override=False, now=DAY, http=None, control=None, car=""):
    app, transport, runner = make_app(home, http, control)
    if car:
        app.car_name = car
    text = app.command(
        f"boombox-{sound}",
        preferred=None,
        argument=None,
        statement=statement,
        override=override,
        now=now,
        sleep=lambda _seconds: None,
    )
    return text, transport, runner


def test_car_flag_can_follow_the_boombox_command():
    args = build_parser().parse_args(_normalize_global(["boombox", "fart", "--car", "Sunny", "--override"]))
    assert args.command == "boombox"
    assert args.sound == "fart"
    assert args.car == "Sunny"
    assert args.override is True
    after = build_parser().parse_args(_normalize_global(["--car", "Sunny", "boombox", "ping"]))
    assert after.car == "Sunny"
    assert after.sound == "ping"


def test_signed_mapping_is_one_shot_sound_ids():
    specs = load_specs()
    assert specs["boombox_fart"].tier == "sensitive"
    assert specs["boombox_fart"].tool_args == ("boombox", "0")
    assert specs["boombox_fart"].action == "boombox fart"
    assert specs["boombox_fart"].verify == "parked_only"
    assert specs["boombox_ping"].tool_args == ("boombox", "2000")
    assert specs["boombox_ping"].action == "boombox ping"


def test_confirmation_is_required_and_names_the_sound(tmp_path):
    control = Control()
    with pytest.raises(ConfirmationRequired):
        play(tmp_path, statement=None, now=DAY, control=control)
    assert control.calls == []
    with pytest.raises(ConfirmationRequired):
        play(tmp_path, statement="yes", now=DAY, control=control)
    with pytest.raises(ConfirmationRequired):
        play(tmp_path, statement="I confirm boombox ping", sound="fart", now=DAY, control=control)
    text, _, runner = play(tmp_path, statement="I confirm boombox fart", now=DAY, control=control)
    assert text == "OK: boombox fart"
    assert runner.calls[0]["args"] == ["boombox", "0"]
    text, _, runner = play(tmp_path, sound="ping", statement="I confirm boombox ping", now=DAY)
    assert text == "OK: boombox ping"
    assert runner.calls[0]["args"] == ["boombox", "2000"]


def test_nickname_confirmation_names_sunny(tmp_path):
    http = Http()
    remember_car(tmp_path, [{"vin": VIN, "id": "veh-0001"}], "0001", "Sunny")
    with pytest.raises(ConfirmationRequired):
        play(tmp_path, statement="I confirm boombox fart", now=DAY, http=http, car="Sunny")
    text, _, runner = play(
        tmp_path,
        statement="I confirm boombox fart on Sunny",
        now=DAY,
        http=http,
        car="Sunny",
    )
    assert text == "OK: boombox fart on sunny"
    assert runner.calls[0]["args"] == ["boombox", "0"]
    assert runner.calls[0]["vin"] == VIN


def test_driving_is_refused_before_the_tool_runs(tmp_path):
    control = Control()
    http = Http(shift="D", speed=10)
    with pytest.raises(NotParked):
        play(tmp_path, statement="I confirm boombox fart", now=DAY, http=http, control=control)
    assert control.calls == []


def test_quiet_hours_default_to_midnight_until_six(tmp_path):
    window = load_quiet(tmp_path)
    assert window.label() == "00:00-06:00"
    assert active_window(tmp_path, NIGHT) is not None
    assert active_window(tmp_path, EDGE) is None
    assert active_window(tmp_path, DAY) is None
    assert "00:00-06:00" in format_quiet(window)
    assert "one sound and stops" in format_quiet(window)


def test_quiet_hours_refuse_without_a_wake_and_name_the_override(tmp_path):
    control = Control()
    http = Http(state="asleep")
    with pytest.raises(ConfigError) as caught:
        play(
            tmp_path,
            statement="I confirm boombox fart despite quiet hours",
            now=NIGHT,
            http=http,
            control=control,
        )
    assert "quiet hours (00:00-06:00" in str(caught.value)
    assert "--override" in str(caught.value)
    assert "despite quiet hours" in str(caught.value)
    assert control.calls == []
    assert not any(url.endswith("/wake_up") for url in http.urls)


def test_override_needs_the_stronger_phrase_and_then_sends_once(tmp_path):
    control = Control()
    with pytest.raises(ConfirmationRequired) as caught:
        play(tmp_path, statement="I confirm boombox fart", override=True, now=NIGHT, control=control)
    assert "despite quiet hours" in str(caught.value)
    assert control.calls == []
    text, http, runner = play(
        tmp_path,
        statement="I confirm boombox fart despite quiet hours",
        override=True,
        now=NIGHT,
    )
    assert text == "OK: boombox fart"
    assert runner.calls == [{"vin": VIN, "args": ["boombox", "0"]}]
    assert any(url.endswith("/wake_up") for url in http.urls) is False


def test_override_outside_quiet_hours_still_uses_the_normal_confirmation(tmp_path):
    text, _, runner = play(tmp_path, sound="ping", statement="I confirm boombox ping", override=True, now=DAY)
    assert text == "OK: boombox ping"
    assert runner.calls[0]["args"] == ["boombox", "2000"]


def test_there_is_no_rate_limit(tmp_path):
    first, _, runner = play(tmp_path, statement="I confirm boombox fart", now=DAY)
    second, _, runner = play(tmp_path, statement="I confirm boombox fart", now=DAY, control=runner)
    assert first == second == "OK: boombox fart"
    assert len(runner.calls) == 2
    assert not (tmp_path / "boombox-rate.json").exists()


def test_quiet_hours_setting_is_stored_and_can_wrap_or_turn_off(tmp_path):
    set_quiet(tmp_path, "22:00-07:00")
    saved = (tmp_path / "boombox.yaml").read_text(encoding="utf-8")
    assert "22:00-07:00" in saved
    assert active_window(tmp_path, datetime(2026, 11, 14, 23, 0)) is not None
    assert active_window(tmp_path, datetime(2026, 11, 14, 6, 30)) is not None
    assert active_window(tmp_path, datetime(2026, 11, 14, 7, 0)) is None
    assert active_window(tmp_path, datetime(2026, 11, 14, 12, 0)) is None
    set_quiet(tmp_path, "off")
    assert load_quiet(tmp_path).disabled is True
    assert active_window(tmp_path, NIGHT) is None
    text, _, runner = play(tmp_path, statement="I confirm boombox fart", now=NIGHT)
    assert text == "OK: boombox fart"
    assert runner.calls[0]["args"] == ["boombox", "0"]
    with pytest.raises(ConfigError):
        set_quiet(tmp_path, "00:00-00:00")
    with pytest.raises(ConfigError):
        set_quiet(tmp_path, "morning")


def test_cli_quiet_setting_and_daytime_play(tmp_path, monkeypatch, capsys):
    http = Http()
    control = Control()
    app, _, _ = make_app(tmp_path, http, control)
    monkeypatch.setattr("core.cli.build_app", lambda home: app)
    monkeypatch.setattr("core.boombox.local_minutes", lambda now=None: 8 * 60)
    assert main(["--home", str(tmp_path), "boombox", "quiet"]) == 0
    assert "00:00-06:00" in capsys.readouterr().out
    assert main(["--home", str(tmp_path), "boombox", "quiet", "21:00-05:00"]) == 0
    saved = capsys.readouterr().out
    assert "21:00-05:00" in saved
    assert "does not change the car" in saved
    assert main(["--home", str(tmp_path), "--as", "rider", "boombox", "quiet", "off"]) == 2
    assert "Only the owner" in capsys.readouterr().err
    assert main(
        ["--home", str(tmp_path), "boombox", "ping", "--confirm", "I confirm boombox ping"]
    ) == 0
    assert control.calls[0]["args"] == ["boombox", "2000"]
    monkeypatch.setattr("core.boombox.local_minutes", lambda now=None: 2 * 60)
    assert main(
        ["--home", str(tmp_path), "boombox", "fart", "--confirm", "I confirm boombox fart"]
    ) == 2
    assert "quiet hours" in capsys.readouterr().err
    assert len(control.calls) == 1
    assert main(
        [
            "--home",
            str(tmp_path),
            "boombox",
            "fart",
            "--override",
            "--confirm",
            "I confirm boombox fart despite quiet hours",
        ]
    ) == 0
    assert control.calls[-1]["args"] == ["boombox", "0"]


def test_asleep_car_wakes_once_like_honk(tmp_path):
    http = Http(state="asleep")
    text, transport, runner = play(tmp_path, statement="I confirm boombox fart", now=DAY, http=http)
    assert text == "OK: boombox fart"
    assert sum(1 for url in transport.urls if url.endswith("/wake_up")) == 1
    assert runner.calls[0]["args"] == ["boombox", "0"]


def test_family_request_cannot_override_and_approval_obeys_quiet_hours(tmp_path, monkeypatch, capsys):
    http = Http()
    control = Control()
    app, _, _ = make_app(tmp_path, http, control)
    remember_car(tmp_path, [{"vin": VIN, "id": "veh-0001"}], "0001", "Sunny")
    app.car_name = "Sunny"
    monkeypatch.setattr("core.cli.build_app", lambda home: app)
    monkeypatch.setattr("core.boombox.local_minutes", lambda now=None: 1 * 60)
    assert main(["--home", str(tmp_path), "family", "add", "rider"]) == 0
    assert main(["--home", str(tmp_path), "family", "allow", "rider", "boombox"]) == 0
    assert classify(tmp_path, "rider", "boombox-fart") == "request"
    assert expand_action("boombox") == [("boombox-fart", "request"), ("boombox-ping", "request")]
    capsys.readouterr()
    assert main(
        [
            "--home",
            str(tmp_path),
            "--as",
            "rider",
            "--car",
            "Sunny",
            "boombox",
            "fart",
            "--override",
            "--confirm",
            f"I confirm boombox fart on Sunny {OVERRIDE_PHRASE}",
        ]
    ) == 0
    relay = capsys.readouterr().out
    assert "Pending request" in relay
    assert "boombox fart" in relay
    assert "This action was not run." in relay
    assert control.calls == []
    saved = json.loads((tmp_path / REQUESTS_NAME).read_text(encoding="utf-8"))
    record = saved["requests"][-1]
    assert record["command"] == "boombox-fart"
    assert record.get("detail") in (None, {})
    request_id = record["id"]
    assert main(["--home", str(tmp_path), "request", "approve", request_id]) == 2
    assert "quiet hours" in capsys.readouterr().err
    assert control.calls == []
    still = json.loads((tmp_path / REQUESTS_NAME).read_text(encoding="utf-8"))["requests"][-1]
    assert still["status"] == "pending"
    assert main(["--home", str(tmp_path), "--as", "rider", "request", "approve", request_id, "--override"]) == 2
    assert control.calls == []
    assert main(
        ["--home", str(tmp_path), "request", "approve", request_id, "--override"]
    ) == 0
    approved = capsys.readouterr().out
    assert "Approved" in approved
    assert "OK: boombox fart on sunny" in approved
    assert control.calls[0]["args"] == ["boombox", "0"]


def test_schedule_cannot_store_a_boombox_play():
    assert schedule_blocks("boombox fart")
    assert schedule_blocks("boombox-ping")
    assert schedule_blocks("boombox quiet off") is False
    assert schedule_blocks("check") is False


def test_patch_adds_the_signed_boombox_helper(tmp_path):
    commands = tmp_path / "cmd" / "tesla-control" / "commands.go"
    vehicle = tmp_path / "pkg" / "vehicle"
    commands.parent.mkdir(parents=True)
    vehicle.mkdir(parents=True)
    commands.write_text('package main\nvar commands = map[string]*Command{\n\t"product-info": {\n\t},\n}\n', encoding="utf-8")
    module = _patch_module()
    ensure_boombox_command = module.ensure_boombox_command
    assert ensure_boombox_command(commands) is True
    text = commands.read_text(encoding="utf-8")
    assert text.count('"boombox":') == 1
    assert "car.Boombox(ctx, uint32(sound))" in text
    helper = (vehicle / "boombox.go").read_text(encoding="utf-8")
    assert helper == module.BOOMBOX_GO
    assert "protowire.AppendTag(nil, 64, protowire.BytesType)" in helper
    assert "protowire.AppendTag(nil, 1, protowire.VarintType)" in helper
    assert "DOMAIN_INFOTAINMENT" in helper
    assert "func (v *Vehicle) Boombox" in helper
    assert ensure_boombox_command(commands) is False
    assert (vehicle / "boombox_test.go").is_file()
    missing = tmp_path / "loose.go"
    missing.write_text("package main\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        ensure_boombox_command(missing)


def test_docs_say_one_sound_and_the_homepage_stays_a_story():
    queries = (ROOT / "docs" / "06-queries-and-commands.md").read_text(encoding="utf-8")
    skill = (ROOT / ".agents" / "skills" / "agentdrive" / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    install = (ROOT / "setup" / "install.sh").read_text(encoding="utf-8")
    assert "plays one sound" in queries.lower() or "play one sound" in queries.lower()
    assert "I confirm boombox fart on Sunny" in queries
    assert "despite quiet hours" in queries
    assert "00:00-06:00" in queries
    assert "field 64" in queries
    assert "boombox fart" in skill
    assert "Sunny" in skill
    assert "despite quiet hours" in skill
    assert "setup/patch_tesla_control_boombox.py" in install
    can_do = readme[readme.index("## What you can do"):readme.index("## A conversation")]
    assert "external speaker" in can_do.lower()
    assert "then it stops" in can_do.lower()
    assert "may wake" not in readme.lower()
    assert "## Next" not in readme
    assert "cloudflare" not in readme.lower()
    assert "cloudflare" not in queries.lower()
