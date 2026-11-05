"""Small HTTP helper. Tests pass a fake with the same methods."""

from __future__ import annotations

import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.errors import AgentDriveError


class HttpError(AgentDriveError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


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


class UrllibClient:
    """Stdlib client used by the real installation. Not used in unit tests.

    Form bodies are url-encoded. A client secret can contain ``&``. Sending that
    character unescaped splits the body into extra fields and the token endpoint
    answers ``unauthorized_client``.
    """

    def post_form(self, url: str, fields: dict[str, str], timeout: float = 30) -> dict:
        data = urlencode(fields).encode("utf-8")
        request = Request(url, data=data, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("Accept", "application/json")
        try:
            with urlopen(request, timeout=timeout) as response:
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
        request = Request(url, data=body, method=method)
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                status = response.status
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except (URLError, TimeoutError) as exc:
            raise HttpError("Fleet API request failed") from None
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw_status": status}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return status, payload

    def get_text(self, url: str, timeout: float = 20) -> tuple[int, str]:
        request = Request(url, method="GET")
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")
        except (URLError, TimeoutError) as exc:
            raise HttpError("Public key URL could not be reached") from None
