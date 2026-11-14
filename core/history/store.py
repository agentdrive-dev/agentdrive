"""Encrypted SQLite history for one car.

Rows are compact columns taken from parked snapshots. A stable parked period
stays one row, with the latest battery and tyre readings written in place.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from core.errors import HistoryError
from core.fleet.health import evaluate_alerts
from core.fleet.report import vehicle_report
from core.fleet.snapshot import Observation
from core.history.crypto import (
    LOCK_NAME,
    database_paths,
    load_or_create_key,
    open_sealed,
    seal,
    write_private_bytes,
)

MILES_TO_KM = 1.609344
MIN_TRIP_KM = 0.1
CURVE_SECONDS = 60
_PLUGGED = {"charging", "starting", "connected", "stopped", "complete", "nopower"}
_CHARGING = {"charging", "starting"}
_ISO = "%Y-%m-%dT%H:%M:%SZ"
_COORD_TEXT = re.compile(r"-?\d{1,3}\.\d{4,}")
_VIN_TEXT = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY,
  observed_at TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  sample_count INTEGER NOT NULL DEFAULT 1,
  battery_percent INTEGER,
  range_km INTEGER,
  charge_limit_percent INTEGER,
  charging_state TEXT,
  plugged_in INTEGER,
  locked INTEGER,
  shift_state TEXT,
  speed REAL,
  parked INTEGER,
  closures TEXT,
  sentry INTEGER,
  climate_on INTEGER,
  driver_temp_c REAL,
  inside_temp_c REAL,
  outside_temp_c REAL,
  tyre_fl REAL,
  tyre_fr REAL,
  tyre_rl REAL,
  tyre_rr REAL,
  tpms TEXT,
  odometer_km REAL
);
CREATE TABLE IF NOT EXISTS trips (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  distance_km REAL,
  energy_used_kwh REAL,
  start_battery_percent INTEGER,
  end_battery_percent INTEGER,
  start_locked INTEGER,
  end_locked INTEGER,
  start_shift TEXT,
  end_shift TEXT,
  start_sentry INTEGER,
  end_sentry INTEGER,
  open INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS charges (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  start_percent INTEGER,
  end_percent INTEGER,
  energy_added_kwh REAL,
  plugged_in INTEGER,
  charging INTEGER,
  open INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS charge_samples (
  id INTEGER PRIMARY KEY,
  charge_id INTEGER NOT NULL,
  observed_at TEXT NOT NULL,
  battery_percent INTEGER,
  energy_added_kwh REAL,
  charger_power_kw REAL
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY,
  observed_at TEXT NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS config_changes (
  id INTEGER PRIMARY KEY,
  observed_at TEXT NOT NULL,
  software_version TEXT,
  distance_units TEXT,
  temperature_units TEXT,
  charge_rate_units TEXT,
  vehicle_config TEXT
);
CREATE TABLE IF NOT EXISTS service_events (
  id INTEGER PRIMARY KEY,
  logged_at TEXT NOT NULL,
  kind TEXT NOT NULL,
  note TEXT,
  odometer_km REAL NOT NULL,
  interval_km REAL,
  basis TEXT
);
CREATE TABLE IF NOT EXISTS shown_notices (
  notice_id TEXT PRIMARY KEY,
  last_shown_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_at ON snapshots(observed_at);
CREATE INDEX IF NOT EXISTS trips_started ON trips(started_at);
CREATE INDEX IF NOT EXISTS charges_started ON charges(started_at);
CREATE INDEX IF NOT EXISTS alerts_at ON alerts(observed_at);
CREATE INDEX IF NOT EXISTS samples_charge ON charge_samples(charge_id, observed_at);
"""


@dataclass
class Reading:
    observed_at: str
    battery_percent: int | None
    range_km: int | None
    charge_limit_percent: int | None
    charging_state: str | None
    plugged: bool | None
    charging: bool | None
    energy_added_kwh: float | None
    charger_power_kw: float | None
    locked: bool | None
    shift_state: str | None
    speed: float | None
    parked: bool | None
    closures: str
    sentry: bool | None
    climate_on: bool | None
    driver_temp_c: float | None
    inside_temp_c: float | None
    outside_temp_c: float | None
    tyre_fl: float | None
    tyre_fr: float | None
    tyre_rl: float | None
    tyre_rr: float | None
    tpms: str
    odometer_km: float | None
    motion: str
    config_canonical: str | None
    config_body: dict


@contextmanager
def _lock(home: Path):
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(home / LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.name != "nt" and hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        if os.name != "nt":
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name != "nt":
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def history_session(home: Path, *, write: bool):
    """Yield a connection, or None when a read finds no database.

    A write creates the key and database when they are missing. The database
    is decrypted only in memory and written back as ciphertext.
    """
    home = Path(home)
    key_path, db_path = database_paths(home)
    if not write and not db_path.is_file():
        yield None
        return
    with _lock(home):
        if db_path.is_file() and not key_path.is_file():
            raise HistoryError("The history key is missing, so the saved history cannot be read.")
        if not db_path.is_file() and not write:
            yield None
            return
        key = load_or_create_key(key_path, create=write)
        conn = sqlite3.connect(":memory:")
        try:
            if db_path.is_file():
                conn.deserialize(open_sealed(key, db_path.read_bytes()))
            conn.row_factory = sqlite3.Row
            conn.executescript(SCHEMA)
            yield conn
            if write:
                conn.commit()
                write_private_bytes(db_path, seal(key, conn.serialize()))
        finally:
            conn.close()


def record_parked_snapshot(
    home: Path,
    payload: dict,
    observation: Observation,
    *,
    tyre_bar: float,
    low_battery_percent: int,
    now: datetime | None = None,
) -> None:
    """Record one parked snapshot and update trip, charge, alert, and config rows."""
    reading = _reading(payload if isinstance(payload, dict) else {}, observation, now or _utc_now())
    with history_session(home, write=True) as conn:
        _ingest(conn, reading, observation, tyre_bar=tyre_bar, low_battery_percent=low_battery_percent)


def database_exists(home: Path) -> bool:
    return database_paths(home)[1].is_file()


def history_root(home: Path, store: Path | None = None) -> Path:
    """Directory that holds history.db. ``store`` overrides the config home."""
    return Path(home if store is None else store)


def latest_odometer(conn) -> float | None:
    row = conn.execute(
        "SELECT odometer_km FROM snapshots WHERE odometer_km IS NOT NULL ORDER BY last_seen DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def recent_snapshots(conn, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        """
        SELECT observed_at, battery_percent, charge_limit_percent, charging_state, sentry, outside_temp_c
        FROM snapshots
        ORDER BY last_seen DESC, id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    return [dict(row) for row in rows]


def load_notices(conn) -> dict[str, date]:
    found: dict[str, date] = {}
    for row in conn.execute("SELECT notice_id, last_shown_at FROM shown_notices"):
        try:
            found[str(row[0])] = date.fromisoformat(str(row[1])[:10])
        except ValueError:
            continue
    return found


def save_notice(conn, notice_id: str, shown_on: date) -> None:
    conn.execute(
        """
        INSERT INTO shown_notices(notice_id, last_shown_at) VALUES(?, ?)
        ON CONFLICT(notice_id) DO UPDATE SET last_shown_at = excluded.last_shown_at
        """,
        (notice_id, shown_on.isoformat()),
    )


def list_service_events(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, logged_at, kind, note, odometer_km, interval_km, basis
        FROM service_events
        ORDER BY logged_at DESC, id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def insert_service_event(
    conn,
    *,
    logged_at: str,
    kind: str,
    note: str | None,
    odometer_km: float,
    interval_km: float | None,
    basis: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO service_events(logged_at, kind, note, odometer_km, interval_km, basis)
        VALUES(?, ?, ?, ?, ?, ?)
        """,
        (logged_at, kind, note, odometer_km, interval_km, basis),
    )


def list_named(conn, kind: str, limit: int) -> tuple[list[dict], int]:
    if kind == "trips":
        total = conn.execute("SELECT COUNT(*) FROM trips").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM trips ORDER BY started_at DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [_trip_dict(row) for row in rows], int(total)
    if kind == "charges":
        total = conn.execute("SELECT COUNT(*) FROM charges").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM charges ORDER BY started_at DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [_charge_dict(conn, row, window=None) for row in rows], int(total)
    if kind == "snapshots":
        total = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM snapshots ORDER BY last_seen DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [_snapshot_dict(row) for row in rows], int(total)
    if kind == "alerts":
        total = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY observed_at DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [_alert_dict(row) for row in rows], int(total)
    if kind == "config":
        total = conn.execute("SELECT COUNT(*) FROM config_changes").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM config_changes ORDER BY observed_at DESC, id DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
        return [_config_dict(row) for row in rows], int(total)
    raise HistoryError("Unknown history list.")


def range_summary(conn, start_iso: str, end_iso: str) -> dict:
    trips = [
        _trip_dict(row)
        for row in conn.execute(
            """
            SELECT * FROM trips
            WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)
            ORDER BY started_at DESC, id DESC
            """,
            (end_iso, start_iso),
        )
    ]
    charges = [
        _charge_dict(conn, row, window=(start_iso, end_iso))
        for row in conn.execute(
            """
            SELECT * FROM charges
            WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)
            ORDER BY started_at DESC, id DESC
            """,
            (end_iso, start_iso),
        )
    ]
    snapshots = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM snapshots
            WHERE observed_at < ? AND last_seen >= ?
            """,
            (end_iso, start_iso),
        ).fetchone()[0]
    )
    alerts = [
        _alert_dict(row)
        for row in conn.execute(
            """
            SELECT * FROM alerts
            WHERE observed_at >= ? AND observed_at < ?
            ORDER BY observed_at DESC, id DESC
            """,
            (start_iso, end_iso),
        )
    ]
    return {"trips": trips, "charges": charges, "snapshots": snapshots, "alerts": alerts}


def _ingest(conn, reading: Reading, observation: Observation, *, tyre_bar: float, low_battery_percent: int) -> None:
    alerts = evaluate_alerts(observation, tyre_bar=tyre_bar, low_battery_percent=low_battery_percent)
    _store_snapshot(conn, reading, alerts)
    _apply_alerts(conn, reading, alerts)
    _apply_config(conn, reading)
    _apply_charge(conn, reading)
    _apply_trip(conn, reading)
    _meta_set(conn, "last_observed_at", reading.observed_at)
    _meta_set(conn, "last_odometer_km", _num(reading.odometer_km))
    _meta_set(conn, "last_battery", _num(reading.battery_percent))
    _meta_set(conn, "last_locked", _bit_text(reading.locked))
    _meta_set(conn, "last_shift", reading.shift_state)
    _meta_set(conn, "last_sentry", _bit_text(reading.sentry))
    _meta_set(conn, "last_motion", reading.motion)


def _store_snapshot(conn, reading: Reading, alerts) -> None:
    """Keep one row for a stable parked period. A meaningful change starts another."""
    previous = _alert_codes(conn)
    current = {alert.code for alert in alerts}
    row = conn.execute("SELECT * FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if row is not None and _same_parked_period(row, reading, previous, current):
        _update_snapshot(conn, row, reading)
        return
    _insert_snapshot(conn, reading)


def _same_parked_period(row, reading: Reading, previous_alerts: set[str], current_alerts: set[str]) -> bool:
    """True when this check is the same parked condition as the latest stored row.

    Battery and tyre readings may drift. That drift updates the row in place.
    """
    if reading.motion != "parked" or current_alerts != previous_alerts:
        return False
    if row["shift_state"] != "P":
        return False
    if _text(row["shift_state"]) != _text(reading.shift_state):
        return False
    if _text(row["charging_state"]) != _text(reading.charging_state):
        return False
    if _text(row["closures"]) != _text(reading.closures):
        return False
    if _bool(row["locked"]) != reading.locked:
        return False
    if _bool(row["sentry"]) != reading.sentry:
        return False
    if _bool(row["climate_on"]) != reading.climate_on:
        return False
    if _bool(row["plugged_in"]) != reading.plugged:
        return False
    if row["charge_limit_percent"] != reading.charge_limit_percent:
        return False
    return not _odometer_changed(row["odometer_km"], reading.odometer_km)


def _update_snapshot(conn, row, reading: Reading) -> None:
    if reading.observed_at < str(row["last_seen"]):
        conn.execute("UPDATE snapshots SET sample_count = sample_count + 1 WHERE id = ?", (row["id"],))
        return
    conn.execute(
        """
        UPDATE snapshots SET
            last_seen = ?, sample_count = sample_count + 1,
            battery_percent = COALESCE(?, battery_percent),
            range_km = COALESCE(?, range_km),
            speed = COALESCE(?, speed),
            driver_temp_c = COALESCE(?, driver_temp_c),
            inside_temp_c = COALESCE(?, inside_temp_c),
            outside_temp_c = COALESCE(?, outside_temp_c),
            tyre_fl = COALESCE(?, tyre_fl),
            tyre_fr = COALESCE(?, tyre_fr),
            tyre_rl = COALESCE(?, tyre_rl),
            tyre_rr = COALESCE(?, tyre_rr),
            tpms = COALESCE(?, tpms),
            odometer_km = COALESCE(?, odometer_km)
        WHERE id = ?
        """,
        (
            reading.observed_at,
            reading.battery_percent,
            reading.range_km,
            reading.speed,
            reading.driver_temp_c,
            reading.inside_temp_c,
            reading.outside_temp_c,
            reading.tyre_fl,
            reading.tyre_fr,
            reading.tyre_rl,
            reading.tyre_rr,
            reading.tpms,
            reading.odometer_km,
            row["id"],
        ),
    )


def _insert_snapshot(conn, reading: Reading) -> None:
    conn.execute(
        """
        INSERT INTO snapshots (
            observed_at, last_seen, sample_count, battery_percent, range_km, charge_limit_percent,
            charging_state, plugged_in, locked, shift_state, speed, parked, closures, sentry, climate_on,
            driver_temp_c, inside_temp_c, outside_temp_c, tyre_fl, tyre_fr, tyre_rl, tyre_rr, tpms, odometer_km
        ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            reading.observed_at,
            reading.observed_at,
            reading.battery_percent,
            reading.range_km,
            reading.charge_limit_percent,
            reading.charging_state,
            _bit(reading.plugged),
            _bit(reading.locked),
            reading.shift_state,
            reading.speed,
            _bit(reading.parked),
            reading.closures,
            _bit(reading.sentry),
            _bit(reading.climate_on),
            reading.driver_temp_c,
            reading.inside_temp_c,
            reading.outside_temp_c,
            reading.tyre_fl,
            reading.tyre_fr,
            reading.tyre_rl,
            reading.tyre_rr,
            reading.tpms,
            reading.odometer_km,
        ),
    )


def _apply_alerts(conn, reading: Reading, alerts) -> None:
    previous = _alert_codes(conn)
    current: list[str] = []
    for alert in alerts:
        if alert.code not in previous:
            conn.execute(
                "INSERT INTO alerts(observed_at, code, message) VALUES(?, ?, ?)",
                (reading.observed_at, alert.code, alert.message),
            )
        current.append(alert.code)
    _meta_set(conn, "alert_codes", ",".join(sorted(set(current))))


def _apply_config(conn, reading: Reading) -> None:
    if not reading.config_canonical:
        return
    fingerprint = hashlib.sha256(reading.config_canonical.encode("utf-8")).hexdigest()
    if fingerprint == _meta_get(conn, "config_fingerprint"):
        return
    body = reading.config_body
    conn.execute(
        """
        INSERT INTO config_changes(
            observed_at, software_version, distance_units, temperature_units, charge_rate_units, vehicle_config
        ) VALUES(?, ?, ?, ?, ?, ?)
        """,
        (
            reading.observed_at,
            body.get("software_version"),
            body.get("distance_units"),
            body.get("temperature_units"),
            body.get("charge_rate_units"),
            json.dumps(body.get("vehicle_config"), sort_keys=True, separators=(",", ":"))
            if body.get("vehicle_config")
            else None,
        ),
    )
    _meta_set(conn, "config_fingerprint", fingerprint)


def _apply_charge(conn, reading: Reading) -> None:
    open_id = _meta_get(conn, "open_charge_id")
    if reading.plugged is True:
        if open_id is None:
            cursor = conn.execute(
                """
                INSERT INTO charges(
                    started_at, ended_at, start_percent, end_percent, energy_added_kwh, plugged_in, charging, open
                ) VALUES(?, NULL, ?, ?, ?, 1, ?, 1)
                """,
                (
                    reading.observed_at,
                    reading.battery_percent,
                    reading.battery_percent,
                    reading.energy_added_kwh,
                    1 if reading.charging else 0,
                ),
            )
            open_id = str(cursor.lastrowid)
            _meta_set(conn, "open_charge_id", open_id)
        else:
            _touch_charge(conn, int(open_id), reading)
        _maybe_sample(conn, int(open_id), reading)
        return
    if reading.plugged is False and open_id is not None:
        _close_charge(conn, int(open_id), reading)
        _meta_set(conn, "open_charge_id", None)


def _touch_charge(conn, charge_id: int, reading: Reading) -> None:
    row = conn.execute("SELECT energy_added_kwh, charging FROM charges WHERE id = ?", (charge_id,)).fetchone()
    if row is None:
        return
    energy = row[0]
    if reading.energy_added_kwh is not None:
        energy = reading.energy_added_kwh if energy is None else max(float(energy), reading.energy_added_kwh)
    charging = 1 if row[1] or reading.charging else 0
    conn.execute(
        "UPDATE charges SET end_percent = ?, energy_added_kwh = ?, charging = ?, plugged_in = 1 WHERE id = ?",
        (reading.battery_percent, energy, charging, charge_id),
    )


def _close_charge(conn, charge_id: int, reading: Reading) -> None:
    row = conn.execute("SELECT energy_added_kwh, charging FROM charges WHERE id = ?", (charge_id,)).fetchone()
    energy = None if row is None else row[0]
    charging = 0 if row is None else row[1]
    conn.execute(
        """
        UPDATE charges
        SET ended_at = ?, end_percent = ?, energy_added_kwh = ?, charging = ?, plugged_in = 1, open = 0
        WHERE id = ?
        """,
        (reading.observed_at, reading.battery_percent, energy, charging, charge_id),
    )


def _maybe_sample(conn, charge_id: int, reading: Reading) -> None:
    last = conn.execute(
        "SELECT observed_at FROM charge_samples WHERE charge_id = ? ORDER BY observed_at DESC, id DESC LIMIT 1",
        (charge_id,),
    ).fetchone()
    if last is not None and _seconds_between(str(last[0]), reading.observed_at) < CURVE_SECONDS:
        return
    conn.execute(
        """
        INSERT INTO charge_samples(charge_id, observed_at, battery_percent, energy_added_kwh, charger_power_kw)
        VALUES(?, ?, ?, ?, ?)
        """,
        (charge_id, reading.observed_at, reading.battery_percent, reading.energy_added_kwh, reading.charger_power_kw),
    )


def _apply_trip(conn, reading: Reading) -> None:
    open_id = _meta_get(conn, "open_trip_id")
    last_motion = _meta_get(conn, "last_motion")
    last_at = _meta_get(conn, "last_observed_at")
    last_odo = _meta_float(conn, "last_odometer_km")
    if reading.motion == "driving":
        if open_id is None:
            start_odo = last_odo if last_motion == "parked" and last_odo is not None else reading.odometer_km
            cursor = conn.execute(
                """
                INSERT INTO trips(
                    started_at, ended_at, distance_km, energy_used_kwh,
                    start_battery_percent, end_battery_percent, start_locked, end_locked,
                    start_shift, end_shift, start_sentry, end_sentry, open
                ) VALUES(?, NULL, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    reading.observed_at,
                    _delta(start_odo, reading.odometer_km),
                    reading.battery_percent,
                    reading.battery_percent,
                    _bit(reading.locked),
                    _bit(reading.locked),
                    reading.shift_state,
                    reading.shift_state,
                    _bit(reading.sentry),
                    _bit(reading.sentry),
                ),
            )
            _meta_set(conn, "open_trip_id", str(cursor.lastrowid))
            _meta_set(conn, "trip_start_odometer_km", _num(start_odo))
        else:
            start_odo = _meta_float(conn, "trip_start_odometer_km")
            conn.execute(
                """
                UPDATE trips
                SET end_battery_percent = ?, end_locked = ?, end_shift = ?, end_sentry = ?, distance_km = ?
                WHERE id = ?
                """,
                (
                    reading.battery_percent,
                    _bit(reading.locked),
                    reading.shift_state,
                    _bit(reading.sentry),
                    _delta(start_odo, reading.odometer_km),
                    int(open_id),
                ),
            )
        return
    if open_id is not None and reading.motion == "parked":
        start_odo = _meta_float(conn, "trip_start_odometer_km")
        conn.execute(
            """
            UPDATE trips
            SET ended_at = ?, end_battery_percent = ?, end_locked = ?, end_shift = ?, end_sentry = ?,
                distance_km = ?, open = 0
            WHERE id = ?
            """,
            (
                reading.observed_at,
                reading.battery_percent,
                _bit(reading.locked),
                reading.shift_state,
                _bit(reading.sentry),
                _delta(start_odo, reading.odometer_km),
                int(open_id),
            ),
        )
        _meta_set(conn, "open_trip_id", None)
        _meta_set(conn, "trip_start_odometer_km", None)
        return
    if open_id is None and last_at and last_odo is not None and reading.odometer_km is not None:
        delta = reading.odometer_km - last_odo
        if delta >= MIN_TRIP_KM:
            conn.execute(
                """
                INSERT INTO trips(
                    started_at, ended_at, distance_km, energy_used_kwh,
                    start_battery_percent, end_battery_percent, start_locked, end_locked,
                    start_shift, end_shift, start_sentry, end_sentry, open
                ) VALUES(?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    last_at,
                    reading.observed_at,
                    delta,
                    _meta_int(conn, "last_battery"),
                    reading.battery_percent,
                    _bit(_meta_bool(conn, "last_locked")),
                    _bit(reading.locked),
                    _meta_get(conn, "last_shift"),
                    reading.shift_state,
                    _bit(_meta_bool(conn, "last_sentry")),
                    _bit(reading.sentry),
                ),
            )


def _reading(payload: dict, observation: Observation, now: datetime) -> Reading:
    fields = vehicle_report("Vehicle", observation).fields
    tyres = fields.get("tyres_bar") if isinstance(fields.get("tyres_bar"), dict) else {}
    charge = _section(payload, "charge_state")
    vehicle = _section(payload, "vehicle_state")
    state = fields.get("charging_state")
    canonical, body = _config_blob(payload)
    shift = fields.get("shift_state")
    speed = fields.get("speed")
    return Reading(
        observed_at=isoformat_utc(_observed_at(payload, now)),
        battery_percent=fields.get("battery_percent"),
        range_km=fields.get("range_km"),
        charge_limit_percent=fields.get("charge_limit_percent"),
        charging_state=state,
        plugged=_plugged(state),
        charging=_charging_now(state),
        energy_added_kwh=_positive_float(charge.get("charge_energy_added")),
        charger_power_kw=_optional_float(charge.get("charger_power")),
        locked=fields.get("locked") if isinstance(fields.get("locked"), bool) else None,
        shift_state=shift,
        speed=speed if isinstance(speed, (int, float)) else None,
        parked=fields.get("parked") if isinstance(fields.get("parked"), bool) else None,
        closures=str(fields.get("closures") or "?"),
        sentry=fields.get("sentry") if isinstance(fields.get("sentry"), bool) else None,
        climate_on=fields.get("climate_on") if isinstance(fields.get("climate_on"), bool) else None,
        driver_temp_c=fields.get("driver_temp_c"),
        inside_temp_c=fields.get("inside_temp_c"),
        outside_temp_c=fields.get("outside_temp_c"),
        tyre_fl=tyres.get("fl"),
        tyre_fr=tyres.get("fr"),
        tyre_rl=tyres.get("rl"),
        tyre_rr=tyres.get("rr"),
        tpms=str(fields.get("tpms") or "?"),
        odometer_km=_odometer_km(vehicle.get("odometer")),
        motion=_motion(shift, speed),
        config_canonical=canonical,
        config_body=body,
    )


def _config_blob(payload: dict) -> tuple[str | None, dict]:
    vehicle = _section(payload, "vehicle_state")
    gui = _section(payload, "gui_settings")
    body = {
        "software_version": _short_text(vehicle.get("car_version"), 80),
        "distance_units": _short_text(gui.get("gui_distance_units"), 40),
        "temperature_units": _short_text(gui.get("gui_temperature_units"), 40),
        "charge_rate_units": _short_text(gui.get("gui_charge_rate_units"), 40),
        "vehicle_config": _vehicle_config(payload),
    }
    if not any(body.values()):
        return None, {}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True), body


def _vehicle_config(payload: dict) -> dict | None:
    section = _section(payload, "vehicle_config")
    kept: dict = {}
    for key, value in section.items():
        if _banned_key(str(key)):
            continue
        if isinstance(value, bool):
            kept[str(key)] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            if number == number and number not in (float("inf"), float("-inf")):
                kept[str(key)] = value
        elif isinstance(value, str):
            text = _short_text(value, 120)
            if text is not None:
                kept[str(key)] = text
    return kept or None


def _snapshot_dict(row) -> dict:
    tyres = {"fl": row["tyre_fl"], "fr": row["tyre_fr"], "rl": row["tyre_rl"], "rr": row["tyre_rr"]}
    return {
        "observed_at": row["observed_at"],
        "first_seen": row["observed_at"],
        "last_seen": row["last_seen"],
        "sample_count": row["sample_count"],
        "battery_percent": row["battery_percent"],
        "range_km": row["range_km"],
        "charge_limit_percent": row["charge_limit_percent"],
        "charging_state": row["charging_state"],
        "plugged_in": _bool(row["plugged_in"]),
        "locked": _bool(row["locked"]),
        "shift_state": row["shift_state"],
        "speed": row["speed"],
        "parked": _bool(row["parked"]),
        "closures": row["closures"],
        "sentry": _bool(row["sentry"]),
        "climate_on": _bool(row["climate_on"]),
        "driver_temp_c": row["driver_temp_c"],
        "inside_temp_c": row["inside_temp_c"],
        "outside_temp_c": row["outside_temp_c"],
        "tyres_bar": tyres,
        "tpms": row["tpms"],
        "odometer_km": row["odometer_km"],
    }


def _trip_dict(row) -> dict:
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "distance_km": row["distance_km"],
        "energy_used_kwh": row["energy_used_kwh"],
        "start_battery_percent": row["start_battery_percent"],
        "end_battery_percent": row["end_battery_percent"],
        "start_locked": _bool(row["start_locked"]),
        "end_locked": _bool(row["end_locked"]),
        "start_shift": row["start_shift"],
        "end_shift": row["end_shift"],
        "start_sentry": _bool(row["start_sentry"]),
        "end_sentry": _bool(row["end_sentry"]),
        "open": bool(row["open"]),
    }


def _charge_dict(conn, row, window: tuple[str, str] | None) -> dict:
    if window is None:
        samples = conn.execute(
            """
            SELECT observed_at, battery_percent, energy_added_kwh, charger_power_kw
            FROM charge_samples WHERE charge_id = ? ORDER BY observed_at ASC, id ASC
            """,
            (row["id"],),
        ).fetchall()
    else:
        samples = conn.execute(
            """
            SELECT observed_at, battery_percent, energy_added_kwh, charger_power_kw
            FROM charge_samples
            WHERE charge_id = ? AND observed_at >= ? AND observed_at < ?
            ORDER BY observed_at ASC, id ASC
            """,
            (row["id"], window[0], window[1]),
        ).fetchall()
    readings = [
        {
            "observed_at": item["observed_at"],
            "battery_percent": item["battery_percent"],
            "energy_added_kwh": item["energy_added_kwh"],
            "charger_power_kw": item["charger_power_kw"],
        }
        for item in samples
    ]
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "start_percent": row["start_percent"],
        "end_percent": row["end_percent"],
        "energy_added_kwh": row["energy_added_kwh"],
        "plugged_in": _bool(row["plugged_in"]),
        "charging": _bool(row["charging"]),
        "open": bool(row["open"]),
        "readings": readings,
    }


def _alert_dict(row) -> dict:
    return {"observed_at": row["observed_at"], "code": row["code"], "message": row["message"]}


def _config_dict(row) -> dict:
    config = None
    if row["vehicle_config"]:
        try:
            parsed = json.loads(row["vehicle_config"])
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            config = {key: value for key, value in parsed.items() if not _banned_key(str(key))}
    return {
        "observed_at": row["observed_at"],
        "software_version": row["software_version"],
        "distance_units": row["distance_units"],
        "temperature_units": row["temperature_units"],
        "charge_rate_units": row["charge_rate_units"],
        "vehicle_config": config,
    }


def _section(payload: dict, name: str) -> dict:
    body = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    if not isinstance(body, dict):
        return {}
    section = body.get(name)
    return section if isinstance(section, dict) else {}


def _observed_at(payload: dict, now: datetime) -> datetime:
    found: list[datetime] = []
    for name in ("drive_state", "charge_state", "climate_state", "vehicle_state", "gui_settings", "vehicle_config"):
        parsed = _epoch(_section(payload, name).get("timestamp"))
        if parsed is not None:
            found.append(parsed)
    if found:
        return max(found)
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


def _epoch(value) -> datetime | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 10_000_000_000:
        number /= 1000.0
    if number < 1_000_000_000 or number > 4_000_000_000:
        return None
    return datetime.fromtimestamp(number, timezone.utc)


def _alert_codes(conn) -> set[str]:
    return set(filter(None, (_meta_get(conn, "alert_codes") or "").split(",")))


def _odometer_changed(previous, current) -> bool:
    if previous is None or current is None:
        return False
    return abs(float(current) - float(previous)) >= MIN_TRIP_KM


def _text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _odometer_km(value) -> float | None:
    number = _optional_float(value)
    if number is None or number < 0 or number > 2_000_000:
        return None
    return number * MILES_TO_KM


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _positive_float(value) -> float | None:
    number = _optional_float(value)
    if number is None or number < 0:
        return None
    return number


def _plugged(state) -> bool | None:
    if not state:
        return None
    folded = str(state).strip().lower()
    if folded in _PLUGGED:
        return True
    if folded == "disconnected":
        return False
    return None


def _charging_now(state) -> bool | None:
    if not state:
        return None
    return str(state).strip().lower() in _CHARGING


def _motion(shift, speed) -> str:
    shift_text = str(shift or "").strip()
    if shift_text and shift_text != "P":
        return "driving"
    if isinstance(speed, (int, float)):
        if float(speed) > 0:
            return "driving"
        if float(speed) == 0 and shift_text in {"", "P"}:
            return "parked"
    if shift_text == "P":
        return "parked"
    return "unknown"


def _banned_key(key: str) -> bool:
    folded = key.lower()
    if folded in {"lat", "lon", "lng", "vin", "heading"}:
        return True
    needles = (
        "latitude",
        "longitude",
        "polyline",
        "breadcrumb",
        "coordinate",
        "native_location",
        "gps",
        "vin",
        "plate",
        "token",
        "secret",
        "password",
    )
    return any(needle in folded for needle in needles)


def _short_text(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > limit:
        return None
    if _banned_key(text) or _COORD_TEXT.search(text) or _VIN_TEXT.match(text):
        return None
    return text


def _seconds_between(earlier: str, later: str) -> float:
    start = datetime.strptime(earlier, _ISO).replace(tzinfo=timezone.utc)
    end = datetime.strptime(later, _ISO).replace(tzinfo=timezone.utc)
    return (end - start).total_seconds()


def _delta(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    delta = end - start
    if delta < 0:
        return None
    return delta


def isoformat_utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).strftime(_ISO)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _bit(value: bool | None) -> int | None:
    if value is True:
        return 1
    if value is False:
        return 0
    return None


def _bool(value) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _bit_text(value: bool | None) -> str | None:
    bit = _bit(value)
    if bit is None:
        return None
    return str(bit)


def _num(value) -> str | None:
    if value is None:
        return None
    return str(value)


def _meta_get(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def _meta_set(conn, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM meta WHERE key = ?", (key,))
        return
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _meta_float(conn, key: str) -> float | None:
    return _optional_float(_meta_get(conn, key))


def _meta_int(conn, key: str) -> int | None:
    number = _meta_float(conn, key)
    if number is None:
        return None
    return int(number)


def _meta_bool(conn, key: str) -> bool | None:
    raw = _meta_get(conn, key)
    if raw == "1":
        return True
    if raw == "0":
        return False
    return None
