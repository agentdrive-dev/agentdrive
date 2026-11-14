"""Trip and charging advice from local history.

A live battery or timetable is used only when the car is already online.
Nothing here wakes the car, and nothing changes a setting. A later change
still goes through the existing confirmation.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from core.errors import ConfigError
from core.history.clock import resolve_timezone
from core.history.energy import annotate_charge
from core.history.store import history_session, latest_parked, list_named, parked_marks
from core.history.text import dumps, fmt_km, fmt_pct
from core.tariff import Tariff, TariffWindow, in_window, load_tariff

_MIN_TRIPS = 3
_MIN_DAYS = 3
_MIN_RATE = 0.04
_MAX_RATE = 1.2
_RESERVE_PERCENT = 10
_DAILY_BUFFER = 15
_FULL_PERCENT = 99
_LOW_PERCENT = 20
_MIN_HABIT = 3
_OFTEN = 0.5
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def parse_kilometers(value: str) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ConfigError("Trip distance must be a number of kilometres.") from exc
    if number != number or not 0 < number <= 2000:
        raise ConfigError("Trip distance must be greater than 0 and at most 2000 km.")
    return number


def render_trip_advice(home, kilometers: float, *, as_json: bool, store, peek, on=None) -> str:
    zone = resolve_timezone(home)
    today = on or datetime.now(zone).date()
    bundle = _history(store)
    tariff = load_tariff(home)
    payload = trip_advice(
        kilometers=kilometers,
        trips=bundle["trips"],
        parked=bundle["parked"],
        peek=peek,
        tariff=tariff,
        today=today,
        tz=zone,
    )
    if as_json:
        return dumps(payload)
    return payload["text"]


def render_charge_advice(home, *, as_json: bool, store, peek, on=None) -> str:
    zone = resolve_timezone(home)
    today = on or datetime.now(zone).date()
    bundle = _history(store)
    tariff = load_tariff(home)
    payload = charge_advice(
        trips=bundle["trips"],
        charges=bundle["charges"],
        marks=bundle["marks"],
        parked=bundle["parked"],
        peek=peek,
        tariff=tariff,
        today=today,
        tz=zone,
    )
    if as_json:
        return dumps(payload)
    return payload["text"]


def trip_advice(*, kilometers, trips, parked, peek, tariff, today, tz) -> dict:
    battery, battery_source = _battery(peek, parked)
    temperature, temp_source = _temperature(peek, parked)
    factor, weather = _weather(temperature, temp_source)
    rate, used = _consumption(trips)
    departure = _departure(peek)
    lines = [
        "Trip advice from saved history on this computer. This does not change the car and does not wake it."
    ]
    enough = None
    needed = None
    if battery is None:
        lines.append("No battery reading is available, so this cannot say whether the trip will fit.")
    else:
        lines.append(f"Battery is {fmt_pct(battery)} {battery_source}.")
    if rate is None:
        lines.append(
            f"Not enough saved trips to estimate consumption: {used} usable "
            f"{'trip' if used == 1 else 'trips'}. At least {_MIN_TRIPS} are needed. No guess was made."
        )
    else:
        lines.append(
            f"Recent driving averages {rate:.2f}% of the battery per km, from {used} saved trips."
        )
        if weather:
            lines.append(weather)
        needed = kilometers * rate * factor
        required = needed + _RESERVE_PERCENT
        lines.append(
            f"A trip of {fmt_km(kilometers)} needs about {required:.0f}% "
            f"({needed:.0f}% to drive and {_RESERVE_PERCENT}% in reserve)."
        )
        if battery is not None:
            enough = battery + 1e-9 >= required
            if enough:
                lines.append(f"{fmt_pct(battery)} is enough for this trip.")
            else:
                short = required - battery
                lines.append(f"{fmt_pct(battery)} is not enough for this trip. It is short by about {short:.0f}%.")
    limit = _charge_limit(trips, rate, factor, kilometers, today, tz)
    lines.extend(limit["lines"])
    lines.extend(departure["lines"])
    return {
        "text": "\n".join(lines),
        "kilometers": kilometers,
        "battery_percent": battery,
        "battery_source": battery_source,
        "consumption_percent_per_km": rate,
        "trips_used": used,
        "enough": enough,
        "needed_percent": None if needed is None else round(needed + _RESERVE_PERCENT, 1),
        "weather_factor": factor,
        "charge_limit_percent": limit["daily_limit"],
        "trip_limit_percent": limit["trip_limit"],
        "precondition": departure["suggest"],
        "advice_only": True,
        "wakes_car": False,
    }


def charge_advice(*, trips, charges, marks, parked, peek, tariff, today, tz) -> dict:
    closed = [annotate_charge(item) for item in charges if not item.get("open")]
    flags = _habits(closed, trips, marks, tariff, tz)
    limit = _charge_limit(trips, *_rate_factor(trips, peek, parked), kilometers=None, today=today, tz=tz)
    schedule = _schedule_suggestion(peek, tariff)
    lines = [
        "Charging advice from saved history on this computer. This does not change the car and does not wake it."
    ]
    if flags["lines"]:
        lines.extend(flags["lines"])
    else:
        lines.append("No charging habit stands out in the saved charges.")
    lines.extend(limit["lines"])
    lines.extend(_tariff_lines(tariff))
    lines.extend(schedule["lines"])
    return {
        "text": "\n".join(lines),
        "habits": flags["habits"],
        "charge_limit_percent": limit["daily_limit"],
        "tariff": [item.as_dict() for item in tariff.windows],
        "schedule_suggestion": schedule["suggest"],
        "advice_only": True,
        "wakes_car": False,
    }


def _history(store) -> dict:
    with history_session(store, write=False) as conn:
        if conn is None:
            return {"trips": [], "charges": [], "parked": None, "marks": []}
        trips, _total = list_named(conn, "trips", 80)
        charges, _charge_total = list_named(conn, "charges", 80)
        return {
            "trips": trips,
            "charges": charges,
            "parked": latest_parked(conn),
            "marks": parked_marks(conn),
        }


def _battery(peek, parked) -> tuple[int | None, str]:
    if peek is not None and getattr(peek, "state", "") == "online":
        observation = getattr(peek, "observation", None)
        level = getattr(observation, "battery_level", None)
        if level is not None:
            return int(level), "from a non-waking reading"
    if parked and parked.get("battery_percent") is not None:
        when = parked.get("last_seen") or "an earlier check"
        return int(parked["battery_percent"]), (
            f"from the last saved snapshot at {when}. A live reading was not available, and the car was not woken"
        )
    return None, ""


def _temperature(peek, parked) -> tuple[float | None, str]:
    if peek is not None and getattr(peek, "state", "") == "online":
        observation = getattr(peek, "observation", None)
        temp = getattr(observation, "outside_temp", None)
        if temp is not None:
            return float(temp), "non-waking reading"
    if parked and parked.get("outside_temp_c") is not None:
        return float(parked["outside_temp_c"]), "last saved snapshot"
    return None, ""


def _weather(temperature: float | None, source: str) -> tuple[float, str | None]:
    if temperature is None:
        return 1.0, "No outside temperature was saved, so no weather adjustment was applied."
    shown = f"{temperature:.0f} C"
    if temperature <= 0:
        return 1.2, f"Outside temperature is {shown} ({source}), so the estimate adds 20 percent for cold weather."
    if temperature <= 10:
        return 1.1, f"Outside temperature is {shown} ({source}), so the estimate adds 10 percent for cool weather."
    if temperature >= 35:
        return 1.1, f"Outside temperature is {shown} ({source}), so the estimate adds 10 percent for hot weather."
    return 1.0, f"Outside temperature is {shown} ({source}). No weather adjustment."


def _consumption(trips: list[dict]) -> tuple[float | None, int]:
    rates: list[float] = []
    ordered = sorted(trips, key=lambda item: item.get("started_at") or "", reverse=True)
    for trip in ordered:
        if trip.get("open"):
            continue
        distance = trip.get("distance_km")
        start = trip.get("start_battery_percent")
        end = trip.get("end_battery_percent")
        if distance is None or start is None or end is None:
            continue
        if float(distance) < 1 or int(end) >= int(start):
            continue
        rate = (int(start) - int(end)) / float(distance)
        if _MIN_RATE <= rate <= _MAX_RATE:
            rates.append(rate)
        if len(rates) >= 30:
            break
    if len(rates) < _MIN_TRIPS:
        return None, len(rates)
    return sum(rates) / len(rates), len(rates)


def _rate_factor(trips, peek, parked) -> tuple[float | None, float]:
    rate, _used = _consumption(trips)
    temperature, source = _temperature(peek, parked)
    factor, _text = _weather(temperature, source)
    return rate, factor


def _charge_limit(trips, rate, factor, kilometers, today, tz) -> dict:
    lines = []
    daily_km, days = _typical_day(trips, today, tz)
    daily_limit = None
    trip_limit = None
    if rate is None or days < _MIN_DAYS or daily_km is None:
        lines.append(
            "Not enough saved days to learn a daily charge limit from your own driving. "
            "80% is the usual everyday limit, and a long trip can justify a higher one. "
            "This does not change the charge limit."
        )
        if rate is not None and kilometers is not None:
            needed = kilometers * rate * factor + _RESERVE_PERCENT
            trip_limit = 80 if needed <= 80 else _round_limit(needed)
            if trip_limit > 80:
                lines.append(
                    f"For this trip, about {trip_limit}% would cover the estimate. "
                    "This does not change the charge limit."
                )
        return {"lines": lines, "daily_limit": None, "trip_limit": trip_limit}
    daily_need = daily_km * rate * factor + _DAILY_BUFFER
    daily_limit = 80 if daily_need <= 80 else _round_limit(daily_need)
    lines.append(
        f"A normal day in the saved history is about {fmt_km(daily_km)}. "
        f"A charge limit of {daily_limit}% covers that. This does not change the charge limit."
    )
    if kilometers is not None:
        needed = kilometers * rate * factor + _RESERVE_PERCENT
        trip_limit = daily_limit if needed <= daily_limit else _round_limit(needed)
        if trip_limit > daily_limit:
            lines.append(
                f"Before this trip, about {trip_limit}% would cover it. "
                "This does not change the charge limit."
            )
        else:
            lines.append(
                f"The everyday limit of {daily_limit}% already covers this trip. "
                "This does not change the charge limit."
            )
    return {"lines": lines, "daily_limit": daily_limit, "trip_limit": trip_limit}


def _typical_day(trips, today, tz) -> tuple[float | None, int]:
    window_start = today - timedelta(days=28)
    totals: dict = {}
    for trip in trips:
        if trip.get("open") or trip.get("distance_km") is None:
            continue
        moment = _parse(trip.get("started_at"))
        if moment is None:
            continue
        day = moment.astimezone(tz).date()
        if day < window_start or day >= today:
            continue
        totals[day] = totals.get(day, 0.0) + float(trip["distance_km"])
    if not totals:
        return None, 0
    ordered = sorted(totals.values())
    middle = ordered[len(ordered) // 2]
    return middle, len(ordered)


def _round_limit(percent: float) -> int:
    stepped = int(math.ceil(percent / 5.0) * 5)
    return max(50, min(100, stepped))


def _departure(peek) -> dict:
    if peek is None or getattr(peek, "state", "") != "online":
        return {
            "suggest": False,
            "lines": [
                "Departure schedules were not read because the car is not online. The car was not woken."
            ],
        }
    observation = getattr(peek, "observation", None)
    if observation is None or not getattr(observation, "departure_schedule_section_present", False):
        return {
            "suggest": False,
            "lines": ["Departure schedules were not in the non-waking reading."],
        }
    enabled = [item for item in getattr(observation, "departure_schedules", ()) or () if getattr(item, "enabled", False)]
    if not enabled:
        return {"suggest": False, "lines": ["No departure schedule is set, so there is no preconditioning suggestion."]}
    temperature = getattr(observation, "outside_temp", None)
    if temperature is None:
        return {
            "suggest": False,
            "lines": ["A departure schedule is set. Outside temperature was not in the reading, so preconditioning was not suggested."],
        }
    if temperature <= 5 or temperature >= 30:
        word = "cold" if temperature <= 5 else "hot"
        return {
            "suggest": True,
            "lines": [
                (
                    f"A departure schedule is set and it is {word} ({temperature:.0f} C). "
                    "Preconditioning the cabin before you leave is worth doing. "
                    "This is advice only. It does not start preconditioning and it does not change the departure schedule."
                )
            ],
        }
    return {
        "suggest": False,
        "lines": [
            f"A departure schedule is set. Outside temperature is {temperature:.0f} C, so there is no extra preconditioning suggestion."
        ],
    }


def _habits(charges, trips, marks, tariff: Tariff, tz) -> dict:
    known = [item for item in charges if item.get("start_percent") is not None or item.get("end_percent") is not None]
    habits = []
    lines = []
    full = [item for item in known if item.get("end_percent") is not None and int(item["end_percent"]) >= _FULL_PERCENT]
    left = [item for item in full if _left_full(item, trips, marks)]
    if len(known) >= _MIN_HABIT and len(left) >= _MIN_HABIT and len(left) / len(known) >= _OFTEN:
        habits.append("full_and_left")
        lines.append(
            f"Often charging to 100% and leaving it: {len(left)} of {len(known)} saved charges "
            "ended at 99% or more and were still high hours later. "
            "A lower everyday limit is easier on the battery. This does not change the charge limit."
        )
    low = [item for item in known if item.get("start_percent") is not None and int(item["start_percent"]) <= _LOW_PERCENT]
    if len(known) >= _MIN_HABIT and len(low) >= _MIN_HABIT and len(low) / len(known) >= _OFTEN:
        habits.append("low_before_charge")
        lines.append(
            f"Often dropping very low before charging: {len(low)} of {len(known)} saved charges "
            f"started at {_LOW_PERCENT}% or below. Charging sooner avoids that. This does not change the car."
        )
    peaks = tariff.of_kind("peak")
    if peaks:
        in_peak = [item for item in known if _in_any(item, peaks, tz)]
        if len(known) >= _MIN_HABIT and len(in_peak) >= _MIN_HABIT and len(in_peak) / len(known) >= _OFTEN:
            habits.append("peak_window")
            window = _window_phrase(peaks)
            lines.append(
                f"Often charging in the peak window ({window}): {len(in_peak)} of {len(known)} saved charges started then. "
                "This does not change the car."
            )
    return {"habits": habits, "lines": lines}


def _left_full(charge, trips, marks) -> bool:
    end_at = _parse(charge.get("ended_at"))
    if end_at is None:
        return False
    next_drive = None
    next_battery = None
    for trip in trips:
        started = _parse(trip.get("started_at"))
        if started is None or started <= end_at:
            continue
        if next_drive is None or started < next_drive:
            next_drive = started
            next_battery = trip.get("start_battery_percent")
    if next_drive is not None and next_drive - end_at < timedelta(hours=1):
        return False
    if next_drive is not None and next_drive - end_at >= timedelta(hours=3):
        if next_battery is None or int(next_battery) >= 90:
            return True
    for mark in marks:
        seen = _parse(mark.get("last_seen"))
        if seen is None or seen <= end_at:
            continue
        if next_drive is not None and seen > next_drive:
            continue
        battery = mark.get("battery_percent")
        if battery is not None and int(battery) >= 95 and seen - end_at >= timedelta(hours=2):
            return True
    return False


def _tariff_lines(tariff: Tariff) -> list[str]:
    if not tariff.windows:
        return [
            "No home tariff is saved. Add off-peak and peak hours with `agentdrive tariff set` if you want this compared with your cheap window. This does not change the car."
        ]
    lines = ["Home tariff windows:"]
    for item in tariff.windows:
        price = "" if item.price_per_kwh is None else f", {item.price_per_kwh:.4g} per kWh"
        lines.append(f"- {item.kind} {item.start}-{item.end}{price}")
    cheap = tariff.of_kind("off-peak")
    if cheap:
        lines.append(
            f"The cheap window is {_window_phrase(cheap)}. "
            "Moving scheduled charging there is advice only. Any change still needs a confirmation."
        )
    return lines


def _schedule_suggestion(peek, tariff: Tariff) -> dict:
    cheap = tariff.of_kind("off-peak")
    if not cheap:
        return {"suggest": False, "lines": []}
    phrase = _window_phrase(cheap)
    if peek is None or getattr(peek, "state", "") != "online":
        return {
            "suggest": True,
            "lines": [
                (
                    f"The in-car charging timetable was not read because the car is not online. The car was not woken. "
                    f"When you want charging in the cheap window ({phrase}), changing the timetable still needs a confirmation."
                )
            ],
        }
    observation = getattr(peek, "observation", None)
    if observation is None or not getattr(observation, "charge_schedule_section_present", False):
        return {
            "suggest": False,
            "lines": ["The in-car charging timetable was not in the non-waking reading."],
        }
    enabled = [item for item in getattr(observation, "charge_schedules", ()) or () if getattr(item, "enabled", False)]
    if not enabled:
        return {
            "suggest": True,
            "lines": [
                (
                    f"No in-car charging timetable is set. The cheap window is {phrase}. "
                    "Adding one is a charging change and still needs a confirmation. This advice does not change the car."
                )
            ],
        }
    outside = []
    for item in enabled:
        if not getattr(item, "start_enabled", False) or getattr(item, "start_time", None) is None:
            continue
        if not any(in_window(int(item.start_time), window) for window in cheap):
            outside.append(item)
    if not outside:
        return {
            "suggest": False,
            "lines": [f"The in-car charging timetable already starts in the cheap window ({phrase})."],
        }
    starts = ", ".join(_minutes(item.start_time) for item in outside)
    return {
        "suggest": True,
        "lines": [
            (
                f"The in-car timetable starts at {starts}, outside the cheap window ({phrase}). "
                "Moving it is a charging change and still needs a confirmation. This advice does not change the timetable."
            )
        ],
    }


def _in_any(charge, windows: tuple[TariffWindow, ...], tz) -> bool:
    moment = _parse(charge.get("started_at"))
    if moment is None:
        return False
    local = moment.astimezone(tz)
    minute = local.hour * 60 + local.minute
    return any(in_window(minute, window) for window in windows)


def _window_phrase(windows: tuple[TariffWindow, ...]) -> str:
    return ", ".join(f"{item.start}-{item.end}" for item in windows)


def _minutes(value) -> str:
    hour, minute = divmod(int(value), 60)
    return f"{hour:02d}:{minute:02d}"


def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), _ISO).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
