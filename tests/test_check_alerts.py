"""Sentry-off low-battery alerts, and rising-edge notification dedupe."""

import json
from pathlib import Path
from types import SimpleNamespace

from core.config import load_regions
from core.fleet.check_state import CHECK_ALERTS_NAME
from core.fleet.health import evaluate_alerts
from core.fleet.queries import FleetReader
from core.fleet.snapshot import Observation
from core.history.usage import render_list
from core.ops import App

VIN = "SECRET-VIN-VALUE"
TOKEN = "access-token"
ROOT = Path(__file__).resolve().parents[1]

THIRTY_MINUTES = (
    "docs/00-before-you-start.md",
    "docs/01-install-and-safety.md",
    "docs/06-queries-and-commands.md",
    "docs/07-assistants.md",
    "docs/08-skills.md",
    "docs/prerequisites/assistant.md",
    "docs/prerequisites/billing.md",
    ".agents/skills/agentdrive/SKILL.md",
    ".agents/skills/agentdrive/prompts/install.md",
)


class Http:
    def __init__(self, state, data):
        self.state = state
        self.data = data
        self.calls = []

    def request_json(self, method, url, headers, timeout=30, body=None):
        self.calls.append(url)
        if "vehicle_data" in url:
            return 200, self.data
        if url.endswith("/wake_up"):
            return 200, {"response": {"state": "online"}}
        if url.rstrip("/").endswith("/vehicles"):
            return 200, {"response": [{"vin": VIN, "state": self.state, "display_name": "Example"}]}
        return 500, {}


def payload(*, battery=80, charging="Disconnected", sentry=True, locked=True):
    return {
        "response": {
            "drive_state": {"shift_state": "P", "speed": 0},
            "charge_state": {"battery_level": battery, "charging_state": charging},
            "vehicle_state": {
                "locked": locked,
                "sentry_mode": sentry,
                "tpms_pressure_fl": 2.9,
                "tpms_pressure_fr": 2.9,
                "tpms_pressure_rl": 2.9,
                "tpms_pressure_rr": 2.9,
            },
        }
    }


def make_app(home, http, *, history=False):
    regions = load_regions()
    tokens = SimpleNamespace(access_token=lambda force=False: TOKEN)
    reader = FleetReader(http, regions, "na", tokens)
    return App(home, reader=reader, tokens=tokens, control=SimpleNamespace(run=lambda **kwargs: None), region="na", history=history)


def codes(report):
    return [item.code for item in report.alerts]


def check(app):
    return app.check(preferred=None, tyre_bar=2.5, low_battery_percent=30)


def test_sentry_off_alerts_only_when_the_battery_is_already_low():
    low_and_off = evaluate_alerts(
        Observation(battery_level=12, charging_state="Disconnected", sentry_on=False, shift_state="P", speed=0, locked=True),
        tyre_bar=2.5,
        low_battery_percent=30,
    )
    low_codes = codes_of(low_and_off)
    assert "low_battery" in low_codes
    assert "sentry-off-due-to-low-battery" in low_codes
    sentry = next(item for item in low_and_off if item.code == "sentry-off-due-to-low-battery")
    assert "Sentry is off" in sentry.message
    assert "12 percent" in sentry.message

    battery_ok = evaluate_alerts(
        Observation(battery_level=80, charging_state="Disconnected", sentry_on=False, shift_state="P", speed=0, locked=True),
        tyre_bar=2.5,
        low_battery_percent=30,
    )
    assert codes_of(battery_ok) == []
    at_threshold = evaluate_alerts(
        Observation(battery_level=30, charging_state="Disconnected", sentry_on=False, shift_state="P", speed=0, locked=True),
        tyre_bar=2.5,
        low_battery_percent=30,
    )
    assert codes_of(at_threshold) == []

    sentry_on = evaluate_alerts(
        Observation(battery_level=12, charging_state="Disconnected", sentry_on=True, shift_state="P", speed=0, locked=True),
        tyre_bar=2.5,
        low_battery_percent=30,
    )
    assert codes_of(sentry_on) == ["low_battery"]

    for charging in ("Charging", "Starting"):
        charging_now = evaluate_alerts(
            Observation(battery_level=12, charging_state=charging, sentry_on=False, shift_state="P", speed=0, locked=True),
            tyre_bar=2.5,
            low_battery_percent=30,
        )
        assert codes_of(charging_now) == []


def codes_of(alerts):
    return [item.code for item in alerts]


def test_check_exits_1_only_when_an_alert_newly_appears(tmp_path):
    home = tmp_path / "home"
    http = Http("online", payload(battery=12, sentry=True, locked=True))
    app = make_app(home, http)
    first = check(app)
    assert first.exit_code == 1
    assert codes(first) == ["low_battery"]
    state_path = home / CHECK_ALERTS_NAME
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"codes": ["low_battery"]}
    assert VIN not in state_path.read_text(encoding="utf-8")
    assert "latitude" not in state_path.read_text(encoding="utf-8")
    assert state_path.stat().st_mode & 0o777 == 0o600

    second = check(app)
    assert second.exit_code == 0
    assert second.alerts == []
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"codes": ["low_battery"]}

    http.data = payload(battery=12, sentry=False, locked=True)
    sentry = check(app)
    assert sentry.exit_code == 1
    assert codes(sentry) == ["sentry-off-due-to-low-battery"]
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "codes": ["low_battery", "sentry-off-due-to-low-battery"]
    }

    http.data = payload(battery=80, sentry=True, locked=True)
    cleared = check(app)
    assert cleared.exit_code == 0
    assert cleared.alerts == []
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"codes": []}

    http.data = payload(battery=12, sentry=True, locked=True)
    returned = check(app)
    assert returned.exit_code == 1
    assert codes(returned) == ["low_battery"]

    http.data = payload(battery=12, sentry=True, locked=False)
    added = check(app)
    assert added.exit_code == 1
    assert codes(added) == ["unlocked"]
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"codes": ["low_battery", "unlocked"]}

    quiet = check(app)
    assert quiet.exit_code == 0
    assert quiet.alerts == []


def test_skipped_check_does_not_change_remembered_alerts(tmp_path):
    home = tmp_path / "home"
    http = Http("online", payload(battery=12, sentry=False, locked=True))
    app = make_app(home, http)
    first = check(app)
    assert first.exit_code == 1
    assert "sentry-off-due-to-low-battery" in codes(first)
    state_path = home / CHECK_ALERTS_NAME
    remembered = state_path.read_bytes()
    stamp = state_path.stat().st_mtime_ns

    for state in ("asleep", "offline"):
        http.state = state
        http.calls.clear()
        skipped = check(app)
        assert skipped.skipped is True
        assert skipped.exit_code == 0
        assert skipped.state == state
        assert state_path.read_bytes() == remembered
        assert state_path.stat().st_mtime_ns == stamp
        assert not any("wake_up" in url or "vehicle_data" in url for url in http.calls)

    http.state = "online"
    again = check(app)
    assert again.exit_code == 0
    assert again.alerts == []

    fresh = tmp_path / "fresh"
    skipped_first = check(make_app(fresh, Http("asleep", payload())))
    assert skipped_first.exit_code == 0
    assert not (fresh / CHECK_ALERTS_NAME).exists()
    offline_first = check(make_app(tmp_path / "offline", Http("offline", payload())))
    assert offline_first.exit_code == 0
    assert not (tmp_path / "offline" / CHECK_ALERTS_NAME).exists()


def test_a_quiet_repeat_still_saves_history(tmp_path):
    home = tmp_path / "home"
    http = Http("online", payload(battery=12, sentry=True, locked=True))
    app = make_app(home, http, history=True)
    assert check(app).exit_code == 1
    assert check(app).exit_code == 0
    listed = json.loads(render_list(home, "snapshots", limit=5, as_json=True))
    assert listed["total"] == 1
    assert listed["snapshots"][0]["sample_count"] == 2
    alerts = json.loads(render_list(home, "alerts", limit=10, as_json=True))
    assert [item["code"] for item in alerts["alerts"]] == ["low_battery"]


def test_documented_health_check_default_is_thirty_minutes():
    for relative in THIRTY_MINUTES:
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "every few hours" not in text, relative
        assert "every 30 minutes" in text, relative
