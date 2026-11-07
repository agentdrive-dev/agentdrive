"""Coarse area command. Mocked HTTP must not leak coordinates into output."""

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

from core import __version__
from core.cli import main
from core.config import load_regions
from core.fleet.area import SCOPE_HINT, WhereReport, area_from_vehicle_payload, format_where, format_where_json
from core.fleet.auth import build_authorize_url, default_scopes, request_partner_token
from core.fleet.health import skipped_report
from core.fleet.http import USER_AGENT, UrllibClient
from core.fleet.queries import FleetReader, QueryError
from core.fleet.snapshot import Observation, from_vehicle_data
from core.ops import DATA_ENDPOINTS, LOCATION_ENDPOINTS, PUBLIC_COMMANDS, App, format_check, format_check_json, format_status

RAW_LAT = "12.345678"
RAW_LON = "98.765432"
ROUNDED_LAT = "12.35"
ROUNDED_LON = "98.77"
TOKEN = "access-token"
VIN = "VEHICLE-1"
SECRETS = (
    RAW_LAT,
    RAW_LON,
    ROUNDED_LAT,
    ROUNDED_LON,
    "12.340",
    "98.760",
    "99991",
    "Secret Road",
    "should-not-be-used",
    TOKEN,
    VIN,
)


class Http:
    def __init__(self, state="online", data=None, data_status=200, geocode=None, geocode_status=200, boom=None):
        self.state = state
        self.data = data if data is not None else location_payload()
        self.data_status = data_status
        self.geocode = geocode if geocode is not None else nominatim_payload()
        self.geocode_status = geocode_status
        self.boom = boom
        self.calls = []

    def request_json(self, method, url, headers, timeout=30, body=None):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if self.boom and self.boom in url:
            raise RuntimeError(f"failed {url} {RAW_LAT},{RAW_LON}")
        if "nominatim.openstreetmap.org" in url:
            return self.geocode_status, self.geocode
        if "vehicle_data" in url:
            return self.data_status, self.data
        if url.endswith("/wake_up"):
            return 200, {"response": {"state": "online"}}
        if url.rstrip("/").endswith("/vehicles"):
            return 200, {"response": [{"vin": VIN, "id": "veh-1", "state": self.state, "display_name": "Example"}]}
        return 500, {"error": f"unexpected {RAW_LAT}"}


def location_payload(latitude=12.345678, longitude=98.765432, error=None):
    payload = {
        "response": {
            "drive_state": {
                "shift_state": "P",
                "speed": 0,
                "latitude": latitude,
                "longitude": longitude,
                "native_latitude": latitude,
                "native_longitude": longitude,
                "heading": 180,
            }
        }
    }
    if error:
        payload["error"] = error
        payload["error_description"] = error
    return payload


def nominatim_payload():
    return {
        "lat": ROUNDED_LAT,
        "lon": ROUNDED_LON,
        "display_name": f"should-not-be-used {RAW_LAT} {RAW_LON}",
        "boundingbox": ["12.340", "12.350", "98.760", "98.770"],
        "address": {
            "suburb": "Northside",
            "state": "Example State",
            "country": "Exampleland",
            "postcode": "99991",
            "road": "Secret Road",
        },
    }


def make_app(http, home=None):
    regions = load_regions()
    tokens = SimpleNamespace(access_token=lambda force=False: TOKEN)
    reader = FleetReader(http, regions, "na", tokens)
    return App(
        Path(home) if home is not None else Path("."),
        reader=reader,
        tokens=tokens,
        control=SimpleNamespace(run=lambda **kwargs: None),
        region="na",
    )


def rendered(report):
    return "\n".join((format_where(report), format_where_json(report), repr(report)))


def assert_secret_free(text):
    for secret in SECRETS:
        assert secret not in text, secret


def nominatim_calls(http):
    return [call for call in http.calls if "nominatim.openstreetmap.org" in call["url"]]


def test_where_rounds_before_the_request_and_hides_coordinates(caplog):
    caplog.set_level(logging.DEBUG)
    http = Http()
    report = make_app(http).where(preferred=None)
    assert report == WhereReport(kind="area", suburb="Northside", region="Example State", country="Exampleland")
    text = rendered(report)
    assert "Northside" in text
    assert "Example State" in text
    assert "Exampleland" in text
    assert_secret_free(text)
    assert set(json.loads(format_where_json(report))) == {"suburb", "region", "country"}
    lookups = nominatim_calls(http)
    assert len(lookups) == 1
    url = lookups[0]["url"]
    assert "zoom=14" in url
    assert f"lat={ROUNDED_LAT}" in url
    assert f"lon={ROUNDED_LON}" in url
    assert RAW_LAT not in url
    assert RAW_LON not in url
    assert "Authorization" not in lookups[0]["headers"]
    assert TOKEN not in url
    data_calls = [call for call in http.calls if "vehicle_data" in call["url"]]
    assert len(data_calls) == 1
    assert "endpoints=location_data" in data_calls[0]["url"]
    assert "charge_state" not in data_calls[0]["url"]
    assert not any(call["url"].endswith("/wake_up") for call in http.calls)
    assert_secret_free(caplog.text)
    assert RAW_LAT not in caplog.text
    assert ROUNDED_LAT not in caplog.text


def test_where_cli_stdout_and_stderr_omit_coordinates(tmp_path, monkeypatch, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.delenv("AGENTDRIVE_VIN", raising=False)
    monkeypatch.delenv("TESLA_VIN", raising=False)
    monkeypatch.setattr("core.cli.build_app", lambda home: make_app(Http()))
    assert main(["--home", str(tmp_path), "where"]) == 0
    plain = capsys.readouterr()
    assert "suburb: Northside" in plain.out
    assert "region: Example State" in plain.out
    assert "country: Exampleland" in plain.out
    assert_secret_free(plain.out)
    assert_secret_free(plain.err)
    assert main(["--home", str(tmp_path), "where", "--json"]) == 0
    as_json = capsys.readouterr()
    payload = json.loads(as_json.out)
    assert payload == {"suburb": "Northside", "region": "Example State", "country": "Exampleland"}
    assert_secret_free(as_json.out)
    assert_secret_free(as_json.err)
    assert_secret_free(caplog.text)


def test_asleep_and_offline_skip_without_waking_or_geocoding():
    for state in ("asleep", "offline"):
        http = Http(state=state)
        report = make_app(http).where(preferred=None)
        assert report.kind == "skipped"
        assert report.state == state
        assert report.exit_code == 0
        text = rendered(report)
        assert "skipped: the vehicle is not online, so it was not woken" in text
        assert_secret_free(text)
        assert nominatim_calls(http) == []
        assert not any("vehicle_data" in call["url"] or call["url"].endswith("/wake_up") for call in http.calls)
    check_skip = format_check(skipped_report("asleep", tyre_bar=2.5, low_battery_percent=30))
    where_skip = format_where(WhereReport(kind="skipped", state="asleep"))
    assert check_skip.splitlines()[1] == where_skip.splitlines()[1]


def test_asleep_response_does_not_wake():
    http = Http(data_status=408, data={"error": f"asleep near {RAW_LAT},{RAW_LON}"})
    report = make_app(http).where(preferred=None)
    assert report.kind == "skipped"
    assert report.state == "asleep"
    assert_secret_free(rendered(report))
    assert nominatim_calls(http) == []
    assert not any(call["url"].endswith("/wake_up") for call in http.calls)


def test_missing_scope_or_firmware_asks_for_reauthorization():
    cases = [
        Http(data_status=403, data={"error": "Unauthorized", "latitude": 12.345678, "longitude": 98.765432}),
        Http(data_status=401, data={"error": "expired"}),
        Http(
            data_status=412,
            data={"error_description": "location_data endpoint requires vehicle_location scope", "latitude": RAW_LAT},
        ),
        Http(data=location_payload(latitude=None, longitude=None, error="firmware requires the location_data endpoint")),
    ]
    for http in cases:
        report = make_app(http).where(preferred=None)
        assert report.kind == "scope"
        assert report.exit_code == 1
        text = rendered(report)
        assert SCOPE_HINT in text
        assert "vehicle_location" in text
        assert "Re-authorize" in text
        assert "Unauthorized" not in text
        assert "Precondition" not in text
        assert_secret_free(text)
        assert nominatim_calls(http) == []


def test_failures_print_area_unavailable_without_coordinates(caplog):
    caplog.set_level(logging.DEBUG)
    cases = [
        Http(geocode_status=500, geocode={"error": f"Unable to geocode {RAW_LAT}"}),
        Http(geocode={"error": "Unable to geocode"}),
        Http(data=location_payload(latitude=None, longitude=None)),
        Http(boom="nominatim.openstreetmap.org"),
        Http(data_status=500, data={"response": {"latitude": 12.345678, "longitude": 98.765432}, "error": "nope"}),
        Http(boom="vehicle_data"),
    ]
    for http in cases:
        report = make_app(http).where(preferred=None)
        assert report.kind == "unavailable"
        assert report.exit_code == 1
        assert format_where(report) == "area unavailable"
        assert json.loads(format_where_json(report)) == {"error": "area unavailable"}
        assert_secret_free(rendered(report))
    assert_secret_free(caplog.text)


def test_query_error_does_not_include_coordinates():
    http = Http(data_status=500, data=location_payload())
    reader = FleetReader(http, load_regions(), "na", SimpleNamespace(access_token=lambda force=False: TOKEN))
    try:
        reader.vehicle_data("veh-1", ["location_data"])
    except QueryError as exc:
        assert str(exc) == "Fleet API read failed"
        assert exc.location_scope is False
        assert_secret_free(str(exc))
    else:
        raise AssertionError("expected QueryError")


def test_status_and_check_still_strip_location(tmp_path):
    assert "location_data" not in DATA_ENDPOINTS
    assert LOCATION_ENDPOINTS == ["location_data"]
    assert "where" not in PUBLIC_COMMANDS
    assert "latitude" not in Observation.__dataclass_fields__
    http = Http(data=location_payload())
    app = make_app(http, home=tmp_path)
    status = app.status(preferred=None, sleep=lambda _seconds: None)
    assert_secret_free(status)
    assert "Tyres" in status or "Battery" in status or "Vehicle" in status
    assert all("location_data" not in call["url"] for call in http.calls)
    assert not any("nominatim" in call["url"] for call in http.calls)
    observation = from_vehicle_data(location_payload())
    assert observation.speed == 0
    assert "latitude" not in observation.__dict__
    line = format_status({"display_name": "Example", "state": "online"}, observation, VIN)
    assert_secret_free(line)
    http.calls.clear()
    report = app.check(preferred=None, tyre_bar=2.5, low_battery_percent=30)
    body = format_check(report) + format_check_json(report)
    assert_secret_free(body)
    assert all("location_data" not in call["url"] for call in http.calls)
    assert not any(call["url"].endswith("/wake_up") for call in http.calls)


def test_nominatim_user_agent_and_half_up_rounding(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    seen = {}

    class Response:
        status = 200

        def read(self):
            return json.dumps(nominatim_payload()).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=30):
        seen["url"] = request.full_url
        seen["ua"] = request.get_header("User-agent")
        return Response()

    monkeypatch.setattr("core.fleet.http.urlopen", fake_urlopen)
    report = area_from_vehicle_payload(
        UrllibClient(),
        location_payload(latitude=-45.675, longitude=1.005),
    )
    assert seen["ua"] == USER_AGENT
    assert seen["ua"].startswith(f"AgentDrive/{__version__}")
    assert "zoom=14" in seen["url"]
    assert "lat=-45.68" in seen["url"]
    assert "lon=1.01" in seen["url"]
    assert "-45.675" not in seen["url"]
    assert "1.005" not in seen["url"]
    assert report.suburb == "Northside"
    assert_secret_free(rendered(report))
    assert "-45.68" not in rendered(report)
    assert "1.01" not in rendered(report)
    assert_secret_free(caplog.text)

    called = {"count": 0}

    def refuse(request, timeout=30):
        called["count"] += 1
        raise AssertionError("nominatim must not be called")

    monkeypatch.setattr("core.fleet.http.urlopen", refuse)
    rejected = area_from_vehicle_payload(UrllibClient(), location_payload(latitude=90.006, longitude=0))
    assert rejected.kind == "unavailable"
    assert called["count"] == 0


def test_scope_list_includes_vehicle_location():
    scopes = default_scopes()
    assert "vehicle_location" in scopes
    regions = load_regions()
    url = build_authorize_url(
        regions,
        region="na",
        client_id="client",
        redirect_uri="https://your-username.github.io/callback",
        state="state-1",
    )
    assert "vehicle_location" in unquote(url)

    class Partner:
        def post_form(self, url, fields, timeout=30):
            self.fields = fields
            return {"access_token": "partner-token"}

    http = Partner()
    request_partner_token(http, regions, region="na", client_id="client", client_secret="secret")
    assert "vehicle_location" in http.fields["scope"].split()
    readme = Path("README.md").read_text(encoding="utf-8")
    guide = Path("docs/06-queries-and-commands.md").read_text(encoding="utf-8")
    security = Path("SECURITY.md").read_text(encoding="utf-8")
    for text in (readme, guide, security):
        assert "Nominatim" in text
        assert "vehicle_location" in text
    assert "rounded" in guide.lower()
    assert "`where`" in guide
    assert "agentdrive where" in Path(".agents/skills/agentdrive/SKILL.md").read_text(encoding="utf-8")
