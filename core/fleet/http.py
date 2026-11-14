"""Small HTTP helper. Tests pass a fake with the same methods.

Every outbound request sends one shared User-Agent. CDNs often block the
default Python-urllib agent and answer 403.

Idempotent reads are retried when the connection drops or the server returns
502, 503, or 504. Vehicle commands are not retried: the car may already
have carried one out.
"""

from __future__ import annotations

import errno
import http.client
import json
import time
from contextvars import ContextVar
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from core import __version__
from core.errors import AgentDriveError

USER_AGENT = f"AgentDrive/{__version__} (+https://github.com/agentdrive-dev/agentdrive)"

READ_ATTEMPTS = 3
READ_BACKOFF_SECONDS = (1.0, 3.0)
TRANSIENT_STATUS = frozenset({502, 503, 504})


def _errnos(*names: str) -> frozenset[int]:
    found: list[int] = []
    for name in names:
        value = getattr(errno, name, None)
        if isinstance(value, int):
            found.append(value)
    return frozenset(found)


_RESET_ERRNOS = _errnos("ECONNRESET", "EPIPE", "ECONNABORTED", "ETIMEDOUT")
_UNREACHABLE_ERRNOS = _errnos("ECONNREFUSED", "EHOSTUNREACH", "ENETUNREACH", "ENETDOWN", "EHOSTDOWN")

CLOSED_NOTHING_SENT = "Tesla's server closed the connection. Nothing was sent to the car. Try again in a moment."

_command_attempted: ContextVar[bool] = ContextVar("agentdrive_command_attempted", default=False)


class HttpError(AgentDriveError):
    def __init__(self, message: str, status: int | None = None, *, timed_out: bool = False):
        super().__init__(message)
        self.status = status
        self.timed_out = timed_out


class _Transient(Exception):
    """A read failed in a way that is safe to try again."""

    def __init__(self, kind: str, status: int | None = None, body: bytes = b""):
        super().__init__(kind)
        self.kind = kind
        self.status = status
        self.body = body


def note_command_attempted() -> None:
    """Remember that a vehicle command was handed off in this process."""
    _command_attempted.set(True)


def reset_command_attempted() -> None:
    """Clear the flag. Each CLI invocation starts with no command sent."""
    _command_attempted.set(False)


def command_was_sent() -> bool:
    return bool(_command_attempted.get())


def _idempotent_read(method: str, url: str) -> bool:
    """GET, the vehicle list, and wake are safe to send again.

    A wake that the car already accepted only asks it to stay awake.
    """
    verb = (method or "").upper()
    path = urlsplit(url).path.rstrip("/")
    if verb == "GET":
        return True
    if verb != "POST":
        return False
    if path == "/api/1/vehicles":
        return True
    return path.endswith("/wake_up")


def _wake(method: str, url: str) -> bool:
    return (method or "").upper() == "POST" and urlsplit(url).path.rstrip("/").endswith("/wake_up")


def _vehicle_command(method: str, url: str) -> bool:
    """A state-changing POST. Wake, the vehicle list, and fleet status are reads."""
    if (method or "").upper() != "POST":
        return False
    if _idempotent_read(method, url):
        return False
    path = urlsplit(url).path.rstrip("/")
    return not path.endswith("/fleet_status")


def _os_kind(exc: OSError) -> str | None:
    timed_out = getattr(errno, "ETIMEDOUT", None)
    if isinstance(exc, TimeoutError) or (timed_out is not None and exc.errno == timed_out):
        return "timeout"
    if isinstance(exc, (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return "closed"
    if exc.errno in _errnos("ECONNRESET", "EPIPE", "ECONNABORTED"):
        return "closed"
    text = str(exc).lower()
    if "remote end closed" in text or "connection reset" in text:
        return "closed"
    if "timed out" in text:
        return "timeout"
    return None


def _url_kind(exc: URLError) -> str | None:
    reason = getattr(exc, "reason", None)
    if isinstance(reason, OSError):
        kind = _os_kind(reason)
        if kind:
            return kind
    text = str(reason).lower()
    if "remote end closed" in text or "connection reset" in text:
        return "closed"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return None


def _exception_kind(exc: BaseException) -> str | None:
    if isinstance(exc, HTTPError):
        if exc.code in TRANSIENT_STATUS:
            return "status"
        return None
    if isinstance(exc, URLError):
        return _url_kind(exc)
    if isinstance(exc, OSError):
        return _os_kind(exc)
    return None


def is_network_error(exc: BaseException) -> bool:
    """True for a dropped connection, a timeout, or an HTTP transport error."""
    if isinstance(exc, (URLError, http.client.HTTPException, ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, OSError) and (_os_kind(exc) or exc.errno in _UNREACHABLE_ERRNOS or exc.errno in _RESET_ERRNOS):
        return True
    return False


def _nothing_sent(method: str, *, read: bool) -> bool:
    return read and (method or "").upper() == "GET" and not command_was_sent()


def _closed_message(method: str, *, read: bool) -> str:
    if _nothing_sent(method, read=read):
        return CLOSED_NOTHING_SENT
    if read:
        return "Tesla's server closed the connection. Try again in a moment."
    return (
        "Tesla's server closed the connection. The command was not sent again. "
        "The car may or may not have received it."
    )


def _status_message(status: int, method: str, *, read: bool) -> str:
    if _nothing_sent(method, read=read):
        return (
            f"Tesla's server is unavailable (HTTP {status}). "
            "Nothing was sent to the car. Try again in a moment."
        )
    if read:
        return f"Tesla's server is unavailable (HTTP {status}). Try again in a moment."
    return (
        f"Tesla's server is unavailable (HTTP {status}). "
        "The command was not sent again. The car may or may not have received it."
    )


def _fleet_error(exc: _Transient, method: str, *, read: bool) -> HttpError:
    if exc.kind == "timeout":
        return HttpError("Fleet API request timed out", timed_out=True)
    if exc.kind == "status":
        status = int(exc.status or 0)
        return HttpError(_status_message(status, method, read=read), status=status)
    return HttpError(_closed_message(method, read=read))


def plain_network_line(exc: BaseException) -> str:
    """One sentence for a network error that escaped the HTTP client."""
    kind = _exception_kind(exc)
    method = "GET"
    read = not command_was_sent()
    if kind == "timeout":
        if read:
            return "The request timed out. Nothing was sent to the car. Try again in a moment."
        return "The request timed out. The car may or may not have received it."
    if kind == "status" and isinstance(exc, HTTPError):
        return _status_message(exc.code, method, read=read)
    if kind == "closed" or isinstance(exc, (http.client.RemoteDisconnected, ConnectionResetError)):
        return _closed_message(method, read=read)
    if read:
        return "The network request failed. Nothing was sent to the car. Try again in a moment."
    return "The network request failed. The car may or may not have received it."


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


def _json_object(raw: bytes, status: int) -> dict:
    text = raw.decode("utf-8", errors="replace" if status >= 400 else "strict")
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        payload = {"raw_status": status}
    if not isinstance(payload, dict):
        return {"value": payload}
    return payload


class UrllibClient:
    """Stdlib client used by the real installation. Not used in unit tests.

    Form bodies are url-encoded. A client secret can contain ``&``. Sending that
    character unescaped splits the body into extra fields and the token endpoint
    answers ``unauthorized_client``.

    GET, the vehicle list, and wake are tried up to three times. A signed
    command, or any other POST that changes the car, is sent once.
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
        except (URLError, TimeoutError, ConnectionError, http.client.HTTPException, json.JSONDecodeError):
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
        def once() -> tuple[int, dict]:
            status, raw = self._exchange(method, url, headers, timeout, body, "Fleet API request failed")
            return status, _json_object(raw, status)

        return self._with_retry(method, url, once, surface="fleet")

    def get_text(self, url: str, timeout: float = 20) -> tuple[int, str]:
        def once() -> tuple[int, str]:
            status, raw = self._exchange("GET", url, None, timeout, None, "Public key URL could not be reached")
            return status, raw.decode("utf-8", errors="replace")

        return self._with_retry("GET", url, once, surface="text")

    def request_bytes(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        timeout: float = 60,
    ) -> tuple[int, bytes]:
        def once() -> tuple[int, bytes]:
            return self._exchange(method, url, headers, timeout, None, "Fleet API request failed")

        return self._with_retry(method, url, once, surface="fleet")

    def _exchange(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        timeout: float,
        body: bytes | None,
        network_message: str,
    ) -> tuple[int, bytes]:
        try:
            with open_url(url, data=body, method=method, headers=headers, timeout=timeout) as response:
                return int(response.status), response.read()
        except HTTPError as exc:
            status = int(exc.code)
            raw = b""
            problem: BaseException | None = None
            try:
                raw = exc.read()
            except Exception as read_exc:
                problem = read_exc
            finally:
                try:
                    exc.close()
                except Exception:
                    pass
            if problem is not None:
                kind = _exception_kind(problem)
                if kind == "timeout":
                    raise _Transient("timeout") from None
                if kind == "closed":
                    raise _Transient("closed") from None
                raise problem
            if status in TRANSIENT_STATUS and _idempotent_read(method, url):
                raise _Transient("status", status, raw)
            return status, raw
        except URLError as exc:
            kind = _url_kind(exc)
            if kind:
                raise _Transient(kind) from None
            raise HttpError(network_message) from None
        except OSError as exc:
            kind = _os_kind(exc)
            if kind:
                raise _Transient(kind) from None
            if isinstance(exc, ConnectionError):
                raise HttpError(network_message) from None
            raise

    def _with_retry(self, method: str, url: str, once, *, surface: str):
        retry = _idempotent_read(method, url)
        attempts = READ_ATTEMPTS if retry else 1
        # Wake is safe to retry, but it does reach the car, so a later failure
        # must not say that nothing was sent.
        if _vehicle_command(method, url) or _wake(method, url):
            note_command_attempted()
        read = not _vehicle_command(method, url)
        last: _Transient | None = None
        for attempt in range(attempts):
            try:
                return once()
            except _Transient as exc:
                last = exc
                if attempt + 1 == attempts:
                    break
                time.sleep(READ_BACKOFF_SECONDS[min(attempt, len(READ_BACKOFF_SECONDS) - 1)])
        if last is None:
            raise HttpError("Fleet API request failed")
        if surface == "text" and last.kind == "status":
            return int(last.status or 0), last.body.decode("utf-8", errors="replace")
        if surface == "text":
            raise HttpError("Public key URL could not be reached") from None
        raise _fleet_error(last, method, read=read) from None
