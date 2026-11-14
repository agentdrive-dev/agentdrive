"""Small HTTP helper. Tests pass a fake with the same methods.

Every outbound request sends one shared User-Agent. CDNs often block the
default Python-urllib agent and answer 403.
"""

from __future__ import annotations

import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core import __version__
from core.errors import AgentDriveError

USER_AGENT = f"AgentDrive/{__version__} (+https://github.com/agentdrive-dev/agentdrive)"


class HttpError(AgentDriveError):
    def __init__(self, message: str, status: int | None = None, *, timed_out: bool = False):
        super().__init__(message)
        self.status = status
        self.timed_out = timed_out


class HttpClient(Protocol):
    def post_form(self, url: str, fields: dict[str, str], timeout: float = 30) -> dict:
        """POST application/x-www-form-urlencoded and return a JSON object."""

    def request_json(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float = 30,
        body: bytes | None = None,
    ) -> tuple[int, dict]:
        """Return the status code and a JSON object."""

    def get_text(self, url: str, timeout: float = 20) -> tuple[int, str]:
        """Return the status code and the response body as text."""

    def request_bytes(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float = 60,
    ) -> tuple[int, bytes]:
        """Return the status code and the raw body. Used for invoice PDFs."""


def build_request(
    url: str,
    *,
    data: bytes | None = None,
    method: str | None = None,
    headers: dict[str, str] | None = None,
) -> Request:
    """Build a urllib request that sends the AgentDrive User-Agent."""
    request = Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        if key.lower() == "user-agent":
            continue
        request.add_header(key, value)
    request.add_header("User-Agent", USER_AGENT)
    return request


def open_url(
    url: str,
    *,
    data: bytes | None = None,
    method: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
):
    """Open a URL with the shared User-Agent."""
    request = build_request(url, data=data, method=method, headers=headers)
    return urlopen(request, timeout=timeout)


class UrllibClient:
    """Stdlib client used by the real installation. Not used in unit tests.

    Form bodies are url-encoded. A client secret can contain ``&``. Sending that
    character unescaped splits the body into extra fields and the token endpoint
    answers ``unauthorized_client``.
    """

    def post_form(self, url: str, fields: dict[str, str], timeout: float = 30) -> dict:
        data = urlencode(fields).encode("utf-8")
        try:
            with open_url(
                url,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                timeout=timeout,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise HttpError("Token endpoint refused the request", status=exc.code) from None
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise HttpError("Token endpoint could not be read") from None
        if not isinstance(payload, dict):
            raise HttpError("Token endpoint returned an unexpected payload")
        return payload

    def request_json(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float = 30,
        body: bytes | None = None,
    ) -> tuple[int, dict]:
        try:
            with open_url(url, data=body, method=method, headers=headers, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                status = response.status
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except TimeoutError:
            raise HttpError("Fleet API request timed out", timed_out=True) from None
        except URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise HttpError("Fleet API request timed out", timed_out=True) from None
            raise HttpError("Fleet API request failed") from None
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw_status": status}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return status, payload

    def get_text(self, url: str, timeout: float = 20) -> tuple[int, str]:
        try:
            with open_url(url, method="GET", timeout=timeout) as response:
                return response.status, response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")
        except (URLError, TimeoutError) as exc:
            raise HttpError("Public key URL could not be reached") from None

    def request_bytes(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float = 60,
    ) -> tuple[int, bytes]:
        try:
            with open_url(url, method=method, headers=headers, timeout=timeout) as response:
                return response.status, response.read()
        except HTTPError as exc:
            return exc.code, exc.read()
        except TimeoutError:
            raise HttpError("Fleet API request timed out", timed_out=True) from None
        except URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise HttpError("Fleet API request timed out", timed_out=True) from None
            raise HttpError("Fleet API request failed") from None
