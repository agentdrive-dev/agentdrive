import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from core.errors import ReauthenticationRequired, TokenError
from core.fleet.tokens import ACCESS_TTL, REFRESH_TTL, AtomicTokenStore, TokenBundle, TokenManager


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bundle(access="access-old", refresh="refresh-old", access_in=timedelta(hours=8), refresh_in=timedelta(days=90)):
    return TokenBundle(
        access_token=access,
        refresh_token=refresh,
        access_expires_at=(NOW + access_in).isoformat(),
        refresh_expires_at=(NOW + refresh_in).isoformat(),
        region="na",
    )


class Refresher:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {
            "access_token": "access-new",
            "refresh_token": "refresh-new",
            "expires_in": 28800,
            "token_type": "Bearer",
        }
        self.error = error
        self.calls = []

    def refresh(self, refresh_token, *, client_id, audience):
        self.calls.append(refresh_token)
        if self.error:
            raise self.error
        return self.payload


def test_valid_access_token_skips_the_network(tmp_path):
    path = tmp_path / "tokens.json"
    store = AtomicTokenStore(path)
    store.save_atomic(bundle())
    refresher = Refresher()
    manager = TokenManager(store, refresher, client_id="client", audience="https://example.invalid", clock=lambda: NOW)
    assert manager.access_token() == "access-old"
    assert refresher.calls == []


def test_refresh_persists_the_new_token_before_dropping_the_old_one(tmp_path):
    path = tmp_path / "tokens.json"
    AtomicTokenStore(path).save_atomic(bundle(access_in=timedelta(minutes=-1)))
    seen = {}

    class Recording(AtomicTokenStore):
        def save_atomic(self, new_bundle):
            seen["held"] = manager._held.refresh_token
            seen["on_disk_before"] = json.loads(path.read_text())["refresh_token"]
            super().save_atomic(new_bundle)

    refresher = Refresher()
    manager = TokenManager(Recording(path), refresher, client_id="client", audience="aud", clock=lambda: NOW)
    assert manager.access_token() == "access-new"
    assert seen == {"held": "refresh-old", "on_disk_before": "refresh-old"}
    saved = json.loads(path.read_text())
    assert saved["refresh_token"] == "refresh-new"
    assert saved["access_token"] == "access-new"
    expires = datetime.fromisoformat(saved["access_expires_at"])
    refresh_expires = datetime.fromisoformat(saved["refresh_expires_at"])
    assert expires - NOW == timedelta(seconds=28800)
    assert refresh_expires - NOW == REFRESH_TTL
    assert ACCESS_TTL == timedelta(hours=8)
    assert (path.stat().st_mode & 0o777) == 0o600


def test_missing_refresh_token_keeps_the_previous_refresh_token(tmp_path):
    path = tmp_path / "tokens.json"
    original = bundle(access_in=timedelta(minutes=-1))
    AtomicTokenStore(path).save_atomic(original)
    refresher = Refresher({"access_token": "access-new", "expires_in": 28800})
    manager = TokenManager(AtomicTokenStore(path), refresher, client_id="client", audience="aud", clock=lambda: NOW)
    assert manager.access_token() == "access-new"
    saved = json.loads(path.read_text())
    assert saved["refresh_token"] == "refresh-old"
    assert saved["access_token"] == "access-new"
    assert saved["refresh_expires_at"] == original.refresh_expires_at
    assert (path.stat().st_mode & 0o777) == 0o600


def test_failed_write_keeps_the_old_refresh_token(tmp_path, monkeypatch):
    path = tmp_path / "tokens.json"
    AtomicTokenStore(path).save_atomic(bundle(access_in=timedelta(minutes=-1)))

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    manager = TokenManager(
        AtomicTokenStore(path),
        Refresher(),
        client_id="client",
        audience="aud",
        clock=lambda: NOW,
    )
    with pytest.raises(OSError):
        manager.access_token()
    assert json.loads(path.read_text())["refresh_token"] == "refresh-old"


def test_expired_refresh_token_does_not_call_the_network(tmp_path):
    path = tmp_path / "tokens.json"
    AtomicTokenStore(path).save_atomic(bundle(access_in=timedelta(hours=-1), refresh_in=timedelta(days=-1)))
    refresher = Refresher()
    manager = TokenManager(AtomicTokenStore(path), refresher, client_id="client", audience="aud", clock=lambda: NOW)
    with pytest.raises(ReauthenticationRequired):
        manager.access_token()
    assert refresher.calls == []
