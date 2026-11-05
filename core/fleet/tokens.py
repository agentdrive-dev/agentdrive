"""Access and refresh tokens.

Tesla rotates the refresh token. The replacement is written with os.replace
before the previous value is dropped. If a response has no new refresh token,
the previous refresh token is kept and the new access token is still saved.
A lock stops two processes from refreshing the same token at once.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

from core.config import fleet_host
from core.errors import ReauthenticationRequired, TokenError

ACCESS_TTL = timedelta(hours=8)
REFRESH_TTL = timedelta(days=90)
ACCESS_SKEW = timedelta(minutes=2)
# Refresh while more than a few minutes of network delay remain.
REFRESH_WITHIN = timedelta(minutes=30)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def jwt_expiry(access_token: str) -> datetime | None:
    """Return the exp claim, or None when the token is not a readable JWT."""
    try:
        parts = access_token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload + padding))
        exp = data.get("exp")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        return datetime.fromtimestamp(float(exp), tz=timezone.utc)
    except (ValueError, json.JSONDecodeError, TypeError, UnicodeError):
        return None


def access_is_fresh(bundle: TokenBundle, now: datetime, *, within: timedelta = REFRESH_WITHIN) -> bool:
    """True when the JWT exp (or the stored expiry) is at least `within` away."""
    exp = jwt_expiry(bundle.access_token)
    if exp is None:
        exp = _parse(bundle.access_expires_at)
    return exp - now >= within


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str
    access_expires_at: str
    refresh_expires_at: str
    token_type: str = "Bearer"
    region: str = "na"
    scope: str = ""

    def access_valid(self, now: datetime, skew: timedelta = ACCESS_SKEW) -> bool:
        return now + skew < _parse(self.access_expires_at)

    def refresh_valid(self, now: datetime) -> bool:
        return now < _parse(self.refresh_expires_at)


class TokenRefresher(Protocol):
    def refresh(self, refresh_token: str, *, client_id: str, audience: str) -> dict:
        """Return the token endpoint JSON."""


class _RefreshLock:
    """Exclusive lock file, mode 0600, next to the token file."""

    def __init__(self, directory: Path):
        self.path = directory / ".refresh.lock"
        self._handle = None

    def __enter__(self) -> _RefreshLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self.path, "a+", encoding="utf-8")
        os.chmod(self.path, 0o600)
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class AtomicTokenStore:
    """JSON file replaced with os.replace. Mode 0600. The previous file remains if the write fails."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> TokenBundle:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TokenError("Saved token file cannot be read") from exc
        if not isinstance(raw, dict):
            raise TokenError("Saved token file is not a token record")
        try:
            access = str(raw["access_token"])
            refresh = str(raw["refresh_token"])
        except KeyError as exc:
            raise TokenError("Saved token file is missing a field") from exc
        if not access or not refresh:
            raise TokenError("Saved token file is empty")
        access_expires, refresh_expires = _expiry_fields(raw, access)
        return TokenBundle(
            access_token=access,
            refresh_token=refresh,
            access_expires_at=access_expires,
            refresh_expires_at=refresh_expires,
            token_type=str(raw.get("token_type") or "Bearer"),
            region=str(raw.get("region") or "na"),
            scope=str(raw.get("scope") or ""),
        )

    def save_atomic(self, bundle: TokenBundle) -> None:
        """Write the whole record, fsync it, then rename it over the previous file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(bundle), indent=2)
        fd, temporary = tempfile.mkstemp(dir=str(self.path.parent), prefix=".token-", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise


def _expiry_fields(raw: dict, access: str) -> tuple[str, str]:
    if raw.get("access_expires_at") and raw.get("refresh_expires_at"):
        return str(raw["access_expires_at"]), str(raw["refresh_expires_at"])
    now = utcnow()
    obtained = raw.get("obtained_at")
    try:
        base = datetime.fromtimestamp(int(obtained), tz=timezone.utc) if obtained is not None else now
    except (TypeError, ValueError, OSError):
        base = now
    try:
        expires_in = int(raw.get("expires_in", int(ACCESS_TTL.total_seconds())))
    except (TypeError, ValueError):
        expires_in = int(ACCESS_TTL.total_seconds())
    jwt_exp = jwt_expiry(access)
    access_expires = _iso(jwt_exp or (base + timedelta(seconds=expires_in)))
    return access_expires, _iso(base + REFRESH_TTL)


def bundle_from_token_response(
    payload: dict,
    *,
    region: str,
    now: datetime,
    previous: TokenBundle | None = None,
) -> TokenBundle:
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise TokenError("Refresh response did not include an access token")
    reused = False
    if not isinstance(refresh, str) or not refresh:
        if previous is not None and previous.refresh_token:
            refresh = previous.refresh_token
            reused = True
        else:
            raise TokenError("Refresh response did not include a refresh token")
    expires_in = payload.get("expires_in", int(ACCESS_TTL.total_seconds()))
    try:
        expires_in = int(expires_in)
    except (TypeError, ValueError) as exc:
        raise TokenError("Refresh response has an invalid expires_in") from exc
    scope = payload.get("scope")
    if scope is None and previous is not None:
        scope = previous.scope
    jwt_exp = jwt_expiry(access)
    access_expires = jwt_exp or (now + timedelta(seconds=expires_in))
    if reused and previous is not None:
        refresh_expires = previous.refresh_expires_at
    else:
        refresh_expires = _iso(now + REFRESH_TTL)
    return TokenBundle(
        access_token=access,
        refresh_token=refresh,
        access_expires_at=_iso(access_expires),
        refresh_expires_at=refresh_expires,
        token_type=str(payload.get("token_type") or "Bearer"),
        region=region,
        scope=str(scope or ""),
    )


class TokenManager:
    """Return a usable access token, refreshing it when JWT exp is under 30 minutes.

    The lock is held for the whole check. After it is acquired the file is read
    again, so a waiter sees a token another process just saved. The replacement
    refresh token is written before the in-memory copy of the previous one is
    released. A failed write leaves the old file in place.
    """

    def __init__(
        self,
        store: AtomicTokenStore,
        refresher: TokenRefresher,
        *,
        client_id: str,
        audience: str,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.refresher = refresher
        self.client_id = client_id
        self.audience = audience
        self.clock = clock or utcnow
        self._held: TokenBundle | None = None

    def access_token(self, *, force: bool = False) -> str:
        with _RefreshLock(self.store.path.parent):
            current = self.store.load()
            self._held = current
            now = self.clock()
            if not force and access_is_fresh(current, now):
                return current.access_token
            if not current.refresh_valid(now):
                raise ReauthenticationRequired("The refresh token has expired. Sign in again.")
            try:
                payload = self.refresher.refresh(
                    current.refresh_token,
                    client_id=self.client_id,
                    audience=self.audience,
                )
            except TokenError:
                raise
            except Exception as exc:
                raise TokenError("Token refresh failed. The previous refresh token was kept.") from exc
            replacement = bundle_from_token_response(payload, region=current.region, now=now, previous=current)
            # Persist before dropping the in-memory copy of the previous refresh token.
            self.store.save_atomic(replacement)
            self._discard_old()
            self._held = replacement
            return replacement.access_token

    def _discard_old(self) -> None:
        """Drop the previous bundle only after save_atomic has returned."""
        self._held = None


class FleetTokenRefresher:
    """POST grant_type=refresh_token. Fields are form-encoded by the HTTP client."""

    def __init__(self, http, regions: dict, client_secret: str = ""):
        self.http = http
        self.regions = regions
        self.client_secret = client_secret

    def refresh(self, refresh_token: str, *, client_id: str, audience: str) -> dict:
        shared = self.regions["shared"]
        url = f"{shared['token_host'].rstrip('/')}{shared['token_path']}"
        fields = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "audience": audience,
        }
        if self.client_secret:
            fields["client_secret"] = self.client_secret
        return self.http.post_form(url, fields)


def audience_for(regions: dict, region: str) -> str:
    return fleet_host(regions, region)
