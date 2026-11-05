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
    """vehicle_data returned 408. Wake once, then read again. Do not loop."""


class QueryError(AgentDriveError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


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
            raise QueryError("Fleet API read failed", status=status)
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

    def charging_history(self) -> dict:
        return charging.normalize_history(self._get(charging.history_path(self.regions)))

    def charging_invoice(self, invoice_id: str) -> dict:
        return charging.normalize_invoice(self._get(charging.invoice_path(self.regions, invoice_id)))
