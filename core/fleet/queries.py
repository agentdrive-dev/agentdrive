"""Read-only Fleet API calls: vehicle list, vehicle data, wake, and fleet status."""

from __future__ import annotations

import json
import time
from urllib.parse import quote

from core.config import assert_path_allowed, endpoint, fill_path, fleet_host
from core.errors import AgentDriveError, EndpointNotAllowed
from core.fleet import charging
from core.fleet.http import HttpClient
from core.fleet.tokens import TokenManager

WAKE_ATTEMPTS = 15
WAKE_PAUSE_SECONDS = 3.0


class VehicleAsleep(AgentDriveError):
    """vehicle_data returned 408. Do not loop on it, and do not wake from a where read."""


class QueryError(AgentDriveError):
    def __init__(self, message: str, status: int | None = None, *, location_scope: bool = False):
        super().__init__(message)
        self.status = status
        self.location_scope = location_scope


def location_scope_denied(payload: object) -> bool:
    """True when an error string says location needs the vehicle_location scope.

    Only error text is read. The payload is not copied into a message, so a
    body that also contains coordinates cannot leak through this check.
    """
    texts: list[str] = []
    _collect_error_text(payload, texts)
    if isinstance(payload, dict):
        _collect_error_text(payload.get("response"), texts)
    blob = " ".join(texts).casefold()
    if "vehicle_location" in blob:
        return True
    if "location" in blob and "scope" in blob:
        return True
    if "location_data" in blob and any(word in blob for word in ("required", "must", "firmware", "scope", "endpoint")):
        return True
    return False


def _collect_error_text(payload: object, texts: list[str]) -> None:
    if not isinstance(payload, dict):
        return
    for key in ("error", "error_description", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            texts.append(value)


def _check_vehicle_id(vehicle_id: str) -> str:
    if not vehicle_id or any(char in vehicle_id for char in "/\\?&#"):
        raise EndpointNotAllowed("Vehicle id is not usable")
    return vehicle_id


class FleetReader:
    def __init__(self, http: HttpClient, regions: dict, region: str, tokens: TokenManager):
        self.http = http
        self.regions = regions
        self.region = region
        self.tokens = tokens

    def _url(self, path: str) -> str:
        assert_path_allowed(self.regions, path)
        return fleet_host(self.regions, self.region) + path

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.tokens.access_token()}",
            "Accept": "application/json",
        }

    def _get(self, path: str) -> dict:
        status, payload = self.http.request_json("GET", self._url(path), self._headers())
        if status == 408:
            raise VehicleAsleep("The vehicle is asleep")
        if status >= 400:
            raise QueryError(
                "Fleet API read failed",
                status=status,
                location_scope=location_scope_denied(payload),
            )
        return payload

    def _post(self, path: str) -> dict:
        status, payload = self.http.request_json("POST", self._url(path), self._headers(), body=b"{}")
        if status >= 400:
            raise QueryError("Fleet API wake failed", status=status)
        return payload

    def list_vehicles(self) -> dict:
        return self._get(endpoint(self.regions, "vehicles"))

    def vehicle_data(self, vehicle_id: str, endpoints: list[str]) -> dict:
        if not endpoints:
            raise QueryError("Choose at least one vehicle_data endpoint")
        joined = quote(";".join(endpoints), safe="")
        path = fill_path(endpoint(self.regions, "vehicle_data"), vehicle_id=_check_vehicle_id(vehicle_id))
        return self._get(f"{path}?endpoints={joined}")

    def vehicle(self, vehicle_id: str) -> dict:
        path = fill_path(endpoint(self.regions, "vehicle"), vehicle_id=_check_vehicle_id(vehicle_id))
        return self._get(path)

    def wake(self, vehicle_id: str) -> dict:
        path = fill_path(endpoint(self.regions, "wake_up"), vehicle_id=_check_vehicle_id(vehicle_id))
        return self._post(path)

    def wake_until_online(
        self,
        vehicle_id: str,
        *,
        attempts: int = WAKE_ATTEMPTS,
        pause: float = WAKE_PAUSE_SECONDS,
        sleep=time.sleep,
    ) -> dict:
        """Wake once, then poll until online. The budget is 15 pauses of 3 seconds."""
        self.wake(vehicle_id)
        last: dict = {}
        for _ in range(attempts):
            payload = self.vehicle(vehicle_id)
            body = payload.get("response") if isinstance(payload.get("response"), dict) else payload
            last = body if isinstance(body, dict) else {}
            if str(last.get("state") or "").lower() == "online":
                return last
            sleep(pause)
        raise QueryError("vehicle did not wake within 45s")

    def fleet_status(self, vins: list[str]) -> dict:
        """POST /api/1/vehicles/fleet_status. Used to see whether the key is paired."""
        if not vins:
            raise QueryError("A pairing check needs a vehicle id")
        for vin in vins:
            _check_vehicle_id(vin)
        path = endpoint(self.regions, "fleet_status")
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        status, payload = self.http.request_json(
            "POST",
            self._url(path),
            headers,
            body=json.dumps({"vins": vins}).encode("utf-8"),
        )
        if status >= 400:
            raise QueryError("Fleet status read failed", status=status)
        return payload

    def nearby_charging_sites(self, vehicle_id: str) -> dict:
        path = charging.nearby_path(self.regions, _check_vehicle_id(vehicle_id))
        return charging.normalize_nearby(self._get(path))

    def charging_history(self) -> list[dict]:
        """Account charging history. This does not call the vehicle and does not wake it."""
        path = charging.history_path(self.regions)
        records: list[dict] = []
        seen: set[tuple] = set()
        for page in range(1, 11):
            try:
                payload = self._get(f"{path}?pageNo={page}&pageSize=50")
            except QueryError as exc:
                if exc.status in (401, 403):
                    raise QueryError(
                        "Charging history needs the vehicle_charging_cmds scope. Sign in again and include it.",
                        status=exc.status,
                    ) from exc
                raise
            batch = charging.history_rows(payload)
            if not batch:
                break
            fresh = []
            for row in batch:
                key = (row["date"], row["site"], row["energy"], row["cost"], row["invoice_id"])
                if key in seen:
                    continue
                seen.add(key)
                fresh.append(row)
            if not fresh:
                break
            records.extend(fresh)
            if len(batch) < 50:
                break
        return records

    def charging_invoice_pdf(self, invoice_id: str) -> bytes:
        """Download one invoice PDF. The caller writes it only when asked."""
        path = charging.invoice_path(self.regions, invoice_id)
        headers = self._headers()
        headers["Accept"] = "application/pdf"
        status, payload = self.http.request_bytes("GET", self._url(path), headers)
        if status in (401, 403):
            raise QueryError(
                "Charging invoices need the vehicle_charging_cmds scope. Sign in again and include it.",
                status=status,
            )
        if status == 404:
            raise QueryError("That invoice was not found.", status=status)
        if status >= 400 or not payload.startswith(b"%PDF"):
            raise QueryError("The invoice was not a PDF.", status=status)
        return payload

    def release_notes(self, vehicle_id: str, *, staged: bool = False) -> dict:
        """Official release notes. Call this only when the vehicle list already says online."""
        path = fill_path(endpoint(self.regions, "release_notes"), vehicle_id=_check_vehicle_id(vehicle_id))
        if staged:
            path = f"{path}?staged=true"
        return self._get(path)
