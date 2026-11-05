from urllib.parse import unquote

import pytest

from core.config import assert_path_allowed, load_regions
from core.errors import EndpointNotAllowed
from core.fleet.auth import build_authorize_url, pairing_link, register_partner
from core.fleet.charging import normalize_nearby
from core.fleet.queries import FleetReader
from core.fleet.snapshot import Observation, from_vehicle_data
from core.profile.owner import load_profile
from core.skills.base import SkillContext
from core.skills.climate import ClimateSkill
from core.skills.morning_briefing import MorningBriefingSkill
from core.skills.news_watch import NewsWatchSkill
from core.skills.onboarding import OnboardingSkill
from core.skills.status import StatusSkill
from tests.test_safety import Adapter, Runner, execute, parked


class Tokens:
    def access_token(self):
        return "access-token"


class Http:
    def __init__(self):
        self.urls = []

    def request_json(self, method, url, headers, timeout=30, body=None):
        self.urls.append((method, url, body))
        return 200, {"response": []}

    def post_form(self, url, fields, timeout=30):
        return {}


def test_status_hides_coordinates_and_briefing_stays_quiet():
    payload = {
        "response": {
            "drive_state": {"shift_state": "P", "speed": 0, "latitude": 1.2, "longitude": 3.4},
            "charge_state": {"battery_level": 80, "charging_state": "Disconnected"},
            "vehicle_state": {"locked": True, "sentry_mode": True},
            "climate_state": {"is_climate_on": False},
        }
    }
    observation = from_vehicle_data(payload)
    assert not hasattr(observation, "latitude") or "latitude" not in observation.__dict__
    spoken = StatusSkill().run(SkillContext(observation=observation, adapter=Adapter())).spoken
    assert "1.2" not in spoken
    assert "Battery 80 percent." in spoken
    result = MorningBriefingSkill().run(SkillContext(observation=observation, adapter=Adapter(), profile=load_profile("config/profile.example.yaml")))
    assert result.notified is False
    assert result.spoken == ""


def test_briefing_speaks_when_the_owner_needs_to_act():
    class Box:
        def __init__(self):
            self.messages = []

        def notify(self, message, *, requires_action, kind="alert"):
            if requires_action:
                self.messages.append(message)
                return True
            return False

    box = Box()
    observation = Observation(shift_state="P", speed=0, locked=False, battery_level=90, sentry_on=True)
    result = MorningBriefingSkill().run(SkillContext(observation=observation, adapter=box))
    assert result.notified is True
    assert "unlocked" in result.spoken


def test_climate_skill_uses_the_gateway():
    runner = Runner()
    ctx = SkillContext(
        observation=parked(),
        adapter=Adapter(),
        gateway=__import__("core.safety.gateway", fromlist=["Gateway"]).Gateway(runner),
        shift_state="P",
        speed=0,
        vin="VIN-TEST",
        access_token="token",
        observe=lambda: parked(climate_on=False),
    )
    result = ClimateSkill().run(ctx, on=False)
    assert result.spoken == "Climate is off."
    assert runner.calls[0]["args"] == ["climate-off"]


def test_phase_2_skills_are_stubs():
    with pytest.raises(NotImplementedError):
        OnboardingSkill().run(None)
    with pytest.raises(NotImplementedError):
        NewsWatchSkill().run(None)


def test_personal_charging_routes_and_nearby_normalization():
    regions = load_regions()
    with pytest.raises(EndpointNotAllowed):
        assert_path_allowed(regions, "/api/1/dx/charging/sessions")
    normalized = normalize_nearby(
        {
            "response": {
                "superchargers": [
                    {
                        "name": "Example Site",
                        "available_stalls": 2,
                        "total_stalls": 8,
                        "location": {"lat": 10, "long": 20},
                        "price_per_kwh": 0.42,
                    }
                ]
            }
        }
    )
    assert normalized["pricing"] is None
    assert normalized["field_status"] == "unconfirmed_against_a_live_response"
    assert normalized["sites"][0]["available_stalls"] == 2
    assert "price_per_kwh" in normalized["ignored_pricing_fields"][0]
    http = Http()
    reader = FleetReader(http, regions, "eu", Tokens())
    reader.vehicle_data("123", ["charge_state", "drive_state"])
    assert "%3B" in http.urls[0][1]
    assert "sessions" not in http.urls[0][1]


def test_authorize_url_uses_configured_hosts():
    regions = load_regions()
    url = build_authorize_url(
        regions,
        region="na",
        client_id="client",
        redirect_uri="https://your-username.github.io/callback",
        state="state-1",
    )
    assert url.startswith(regions["shared"]["authorization_host"])
    assert regions["regions"]["na"]["fleet_api_host"] in unquote(url)

    class PartnerHttp(Http):
        def request_json(self, method, url, headers, timeout=30, body=None):
            self.urls.append((method, url, body))
            return 200, {"response": {"ok": True}}

    http = PartnerHttp()
    status, _payload = register_partner(http, regions, region="eu", partner_token="partner", domain="your-username.github.io")
    assert status == 200
    assert http.urls[0][1].startswith(regions["regions"]["eu"]["fleet_api_host"])
    assert b"your-username.github.io" in http.urls[0][2]
    assert pairing_link(regions, "your-username.github.io").endswith("/_ak/your-username.github.io")


def test_execute_import_used_by_climate_matches_safety_runner():
    _, runner = execute("honk")
    assert runner.calls[0]["args"] == ["honk"]
