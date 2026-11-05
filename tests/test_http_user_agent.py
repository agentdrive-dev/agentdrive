"""Outbound HTTP sends one shared User-Agent."""

from core import __version__
from core.fleet.http import USER_AGENT, UrllibClient, build_request
from core.update import _fetch_bytes


def test_outbound_requests_send_agentdrive_user_agent(monkeypatch):
    assert USER_AGENT == f"AgentDrive/{__version__} (+https://github.com/agentdrive-dev/agentdrive)"
    direct = build_request(
        "https://example.invalid/key.pem",
        method="GET",
        headers={"User-Agent": "Python-urllib/3.11", "Accept": "text/plain"},
    )
    assert direct.get_header("User-agent") == USER_AGENT
    assert direct.get_header("Accept") == "text/plain"
    assert "Python-urllib" not in direct.get_header("User-agent")

    seen = []

    class Response:
        status = 200

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=30):
        seen.append(request.get_header("User-agent"))
        return Response()

    monkeypatch.setattr("core.fleet.http.urlopen", fake_urlopen)
    client = UrllibClient()
    status, body = client.get_text("https://example.invalid/.well-known/appspecific/com.tesla.3p.public-key.pem")
    assert (status, body) == (200, '{"ok": true}')
    client.post_form("https://example.invalid/token", {"grant_type": "client_credentials"})
    client.request_json(
        "GET",
        "https://example.invalid/api",
        {"Authorization": "Bearer token", "User-Agent": "Python-urllib/3.11"},
    )
    assert _fetch_bytes("https://example.invalid/archive.tar.gz") == b'{"ok": true}'
    assert seen == [USER_AGENT, USER_AGENT, USER_AGENT, USER_AGENT]
