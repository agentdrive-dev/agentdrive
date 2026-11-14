"""Owner and partner OAuth against the hosts listed in regions.yaml."""

from __future__ import annotations

from urllib.parse import urlencode

from core.config import fleet_host, load_scopes
from core.errors import ConfigError, TokenError
from core.fleet.http import HttpClient


def default_scopes(scopes: dict | None = None, *, include_charging: bool = False) -> list[str]:
    document = scopes or load_scopes()
    chosen = list(document["default"])
    if include_charging:
        charging = (document.get("optional") or {}).get("charging")
        if not charging:
            raise ConfigError("Charging scope is missing from scopes.yaml")
        if str(charging) not in chosen:
            chosen.append(str(charging))
    return chosen


def build_authorize_url(
    regions: dict,
    *,
    region: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: list[str] | None = None,
) -> str:
    """Build the interactive sign-in URL. The audience is the regional Fleet API host."""
    if not client_id or not redirect_uri or not state:
        raise ConfigError("client id, redirect URI, and state are required")
    shared = regions["shared"]
    audience = fleet_host(regions, region)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes or default_scopes()),
            "state": state,
            "audience": audience,
        }
    )
    return f"{shared['authorization_host'].rstrip('/')}{shared['authorize_path']}?{query}"


def _token_url(regions: dict) -> str:
    shared = regions["shared"]
    return f"{shared['token_host'].rstrip('/')}{shared['token_path']}"


def _require_token_fields(payload: dict, *, expect_refresh: bool) -> dict:
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise TokenError("Token response did not include an access token")
    if expect_refresh:
        refresh = payload.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            raise TokenError("Token response did not include a refresh token")
    return payload


def exchange_authorization_code(
    http: HttpClient,
    regions: dict,
    *,
    region: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict:
    """Exchange a one-time code. The caller must store the result and must not log it."""
    if not code:
        raise TokenError("Authorization code is empty")
    payload = http.post_form(
        _token_url(regions),
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "audience": fleet_host(regions, region),
        },
    )
    return _require_token_fields(payload, expect_refresh=True)


def request_partner_token(
    http: HttpClient,
    regions: dict,
    *,
    region: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Client-credentials token used to register the owner's domain."""
    payload = http.post_form(
        _token_url(regions),
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "audience": fleet_host(regions, region),
            "scope": "openid vehicle_device_data vehicle_cmds vehicle_location",
        },
    )
    return _require_token_fields(payload, expect_refresh=False)


def partner_registration_url(regions: dict, region: str) -> str:
    return fleet_host(regions, region) + regions["shared"]["partner_accounts_path"]


def register_partner(http: HttpClient, regions: dict, *, region: str, partner_token: str, domain: str) -> tuple[int, dict]:
    """Register the hostname. The public key must already be reachable on HTTPS."""
    bare = domain.strip().lower()
    if bare != domain.strip() or "/" in bare or not bare:
        raise ConfigError("Partner domain must be a lowercase hostname")
    url = partner_registration_url(regions, region)
    return http.request_json(
        "POST",
        url,
        {
            "Authorization": f"Bearer {partner_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        body=json_bytes({"domain": bare}),
    )


def json_bytes(payload: dict) -> bytes:
    import json

    return json.dumps(payload).encode("utf-8")


def pairing_link(regions: dict, domain: str) -> str:
    template = regions["shared"]["pairing_link_template"]
    if "{domain}" not in template:
        raise ConfigError("pairing link template is invalid")
    bare = domain.strip().lower().strip(".")
    if not bare or "/" in bare or " " in bare:
        raise ConfigError("Domain must be a bare hostname")
    return template.format(domain=bare)
