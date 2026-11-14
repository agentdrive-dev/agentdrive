"""Transient Fleet reads are retried. Commands are not, and the CLI stays quiet."""

import http.client
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from core.cli import main
from core.errors import CommandFailed
from core.fleet.commands import TeslaControl
from core.fleet.http import (
    CLOSED_NOTHING_SENT,
    READ_BACKOFF_SECONDS,
    HttpError,
    UrllibClient,
    note_command_attempted,
    reset_command_attempted,
)


class _Response:
    def __init__(self, status: int, payload: bytes):
        self.status = status
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _clear_command_flag():
    reset_command_attempted()
    yield
    reset_command_attempted()


def _install(monkeypatch, events):
    calls = []
    slept = []

    def urlopen(request, timeout=30):
        calls.append((request.get_method(), request.full_url))
        event = events[len(calls) - 1]
        if isinstance(event, BaseException):
            raise event
        return event

    monkeypatch.setattr("core.fleet.http.urlopen", urlopen)
    monkeypatch.setattr("core.fleet.http.time.sleep", slept.append)
    return calls, slept


def _http_error(url: str, status: int) -> HTTPError:
    return HTTPError(url, status, "error", hdrs=None, fp=BytesIO(b"{}"))


def _closed() -> http.client.RemoteDisconnected:
    return http.client.RemoteDisconnected("Remote end closed connection without response")


def test_get_retries_then_returns_the_vehicle_list(monkeypatch):
    url = "https://example.invalid/api/1/vehicles"
    calls, slept = _install(
        monkeypatch,
        [_closed(), _Response(200, b'{"response":[]}')],
    )
    status, payload = UrllibClient().request_json("GET", url, {})
    assert status == 200
    assert payload == {"response": []}
    assert calls == [("GET", url), ("GET", url)]
    assert slept == [READ_BACKOFF_SECONDS[0]]


def test_wake_and_status_codes_retry_and_a_plain_get_can_succeed(monkeypatch):
    wake = "https://example.invalid/api/1/vehicles/1/wake_up"
    calls, slept = _install(
        monkeypatch,
        [_http_error(wake, 502), _http_error(wake, 503), _Response(200, b'{"response":{"state":"online"}}')],
    )
    status, payload = UrllibClient().request_json("POST", wake, {}, body=b"{}")
    assert status == 200
    assert payload["response"]["state"] == "online"
    assert [method for method, _url in calls] == ["POST", "POST", "POST"]
    assert slept == list(READ_BACKOFF_SECONDS)


def test_read_exhaustion_says_nothing_was_sent(monkeypatch):
    url = "https://example.invalid/api/1/vehicles"
    calls, slept = _install(monkeypatch, [_closed(), _closed(), ConnectionResetError("Connection reset by peer")])
    with pytest.raises(HttpError) as caught:
        UrllibClient().request_json("GET", url, {})
    assert str(caught.value) == CLOSED_NOTHING_SENT
    assert "Nothing was sent to the car." in str(caught.value)
    assert "Traceback" not in str(caught.value)
    assert "RemoteDisconnected" not in str(caught.value)
    assert len(calls) == 3
    assert slept == list(READ_BACKOFF_SECONDS)


def test_urlerror_reset_retries_and_timeout_stays_a_timeout(monkeypatch):
    url = "https://example.invalid/api/1/vehicles/1"
    calls, slept = _install(
        monkeypatch,
        [URLError(ConnectionResetError("Connection reset by peer")), _Response(200, b'{"response":{"state":"online"}}')],
    )
    status, _payload = UrllibClient().request_json("GET", url, {})
    assert status == 200
    assert len(calls) == 2
    assert slept == [1.0]

    events = [URLError(TimeoutError("timed out"))] * 3
    recorded, pauses = _install(monkeypatch, events)
    with pytest.raises(HttpError) as caught:
        UrllibClient().request_json("GET", url, {})
    assert caught.value.timed_out is True
    assert str(caught.value) == "Fleet API request timed out"
    assert len(recorded) == 3
    assert pauses == list(READ_BACKOFF_SECONDS)


def test_server_error_on_a_read_retries_then_says_to_try_again(monkeypatch):
    url = "https://example.invalid/api/1/vehicles"
    calls, _slept = _install(monkeypatch, [_http_error(url, 504), _http_error(url, 504), _http_error(url, 504)])
    with pytest.raises(HttpError) as caught:
        UrllibClient().request_json("GET", url, {})
    text = str(caught.value)
    assert "HTTP 504" in text
    assert "Nothing was sent to the car." in text
    assert "Try again in a moment." in text
    assert len(calls) == 3


def test_ordinary_http_500_is_not_retried(monkeypatch):
    url = "https://example.invalid/api/1/vehicles"
    calls, slept = _install(monkeypatch, [_http_error(url, 500)])
    status, payload = UrllibClient().request_json("GET", url, {})
    assert status == 500
    assert payload == {}
    assert len(calls) == 1
    assert slept == []


def test_a_read_after_wake_does_not_claim_nothing_was_sent(monkeypatch):
    wake = "https://example.invalid/api/1/vehicles/1/wake_up"
    data = "https://example.invalid/api/1/vehicles/1/vehicle_data"
    calls, _slept = _install(
        monkeypatch,
        [
            _Response(200, b'{"response":{"state":"online"}}'),
            _closed(),
            _closed(),
            _closed(),
        ],
    )
    client = UrllibClient()
    assert client.request_json("POST", wake, {}, body=b"{}")[0] == 200
    with pytest.raises(HttpError) as caught:
        client.request_json("GET", data, {})
    assert "Nothing was sent" not in str(caught.value)
    assert "Try again in a moment." in str(caught.value)
    assert calls[0] == ("POST", wake)
    assert len(calls) == 4


def test_command_post_is_not_retried(monkeypatch):
    url = "https://example.invalid/api/1/vehicles/1/command/door_lock"
    calls, slept = _install(monkeypatch, [_closed(), _Response(200, b"{}")])
    with pytest.raises(HttpError) as caught:
        UrllibClient().request_json("POST", url, {}, body=b"{}")
    text = str(caught.value)
    assert len(calls) == 1
    assert slept == []
    assert "Nothing was sent" not in text
    assert "not sent again" in text
    assert "may or may not have received it" in text

    calls.clear()
    status_url = "https://example.invalid/api/1/vehicles/1/command/door_unlock"
    recorded, pauses = _install(monkeypatch, [_http_error(status_url, 503)])
    status, _payload = UrllibClient().request_json("POST", status_url, {}, body=b"{}")
    assert status == 503
    assert len(recorded) == 1
    assert pauses == []


def test_context_deadline_is_not_retried(tmp_path):
    calls = {"n": 0}

    def runner(argv, timeout, env):
        calls["n"] += 1
        return 1, "Post command: context deadline exceeded secret-token"

    tool = TeslaControl("tesla-control", tmp_path / "key.pem", runner=runner, token_dir=tmp_path)
    with pytest.raises(CommandFailed) as caught:
        tool.run(vin="VIN-TEST", access_token="secret-token", args=["lock"])
    text = str(caught.value)
    assert calls["n"] == 1
    assert "not sent again" in text
    assert "may or may not have received it" in text
    assert "context deadline exceeded" in text
    assert "secret-token" not in text
    assert "VIN-TEST" not in text
    assert list(tmp_path.iterdir()) == []


def test_cli_prints_one_line_when_the_connection_closes(tmp_path, monkeypatch, capsys):
    def explode(args, home):
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr("core.cli._cars", explode)
    code = main(["--home", str(tmp_path), "cars"])
    captured = capsys.readouterr()
    assert code == 2
    assert captured.err.strip() == CLOSED_NOTHING_SENT
    assert "Traceback" not in captured.err
    assert "RemoteDisconnected" not in captured.err
    assert captured.out == ""


def test_cli_does_not_say_nothing_was_sent_after_a_command(tmp_path, monkeypatch, capsys):
    def explode(args, home):
        note_command_attempted()
        raise http.client.RemoteDisconnected("Remote end closed connection without response")

    monkeypatch.setattr("core.cli._check", explode)
    code = main(["--home", str(tmp_path), "check"])
    err = capsys.readouterr().err.strip()
    assert code == 2
    assert "Nothing was sent" not in err
    assert "may or may not have received it" in err
    assert "Traceback" not in err


def test_cli_still_shows_a_programming_error(tmp_path, monkeypatch):
    def explode(args, home):
        raise RuntimeError("bug")

    monkeypatch.setattr("core.cli._cars", explode)
    with pytest.raises(RuntimeError, match="bug"):
        main(["--home", str(tmp_path), "cars"])
