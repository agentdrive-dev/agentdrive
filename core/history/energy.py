"""Session energy from charge_energy_added readings.

The car reports energy added in the current charging session. That counter
sometimes still holds the previous session, or keeps climbing across plugs
without returning to zero. Taking the maximum then stores that stale or
cumulative figure as this session's energy.

The value saved for a new session is the increase that belongs to this plug
in. A drop is a reset: energy before the drop counts only if it rose during
this session, and energy after the reset counts from the new counter.
Summaries use the same rule on samples so an old row can be left as it was
and still kept out of a total when the saved number cannot match the battery
change.
"""

from __future__ import annotations

# A full-size pack is about 1.3 kWh per percent at the battery. Wall energy
# and a few kWh of battery heating sit above that. Past this line the
# reported figure does not match the percent change.
_OVERHEAD_KWH = 6.0
_KWH_PER_PERCENT = 1.6
_ABSOLUTE_MAX_KWH = 160.0
_RESET_DROP_KWH = 1.0


def plausible_energy(kwh: float | None, start_percent, end_percent) -> bool:
    """True when kWh can belong to the observed battery change."""
    if kwh is None:
        return False
    try:
        number = float(kwh)
    except (TypeError, ValueError):
        return False
    if number != number or number < 0 or number > _ABSOLUTE_MAX_KWH:
        return False
    if start_percent is None or end_percent is None:
        return True
    try:
        gain = int(end_percent) - int(start_percent)
    except (TypeError, ValueError):
        return True
    if gain <= 0:
        return number <= _OVERHEAD_KWH
    ceiling = _OVERHEAD_KWH + gain * _KWH_PER_PERCENT
    if number <= 0.05 and gain >= 5:
        return False
    return number <= ceiling


def resolve_charge_energy(values: list[float], start_percent, end_percent) -> float | None:
    """Return kWh added during this plug-in, or None when nothing fits.

    ``values`` are raw charge_energy_added readings in time order. They are
    not summed. A session counter that starts near zero contributes its last
    reading. A counter that is already high at the first reading contributes
    only its increase. A drop starts a new counter.
    """
    cleaned = _clean(values)
    if not cleaned:
        return None
    segments = _segments(cleaned)
    if len(segments) > 1:
        total = max(0.0, segments[0][-1] - segments[0][0])
        for segment in segments[1:]:
            total += segment[-1]
        if plausible_energy(total, start_percent, end_percent):
            return round(total, 3)
        return None
    first = segments[0][0]
    last = segments[0][-1]
    delta = max(0.0, last - first)
    session_ok = plausible_energy(last, start_percent, end_percent)
    delta_ok = len(cleaned) > 1 and plausible_energy(delta, start_percent, end_percent)
    cumulative = first >= 5 and last > 0 and (first / last) >= 0.45
    if cumulative and delta_ok:
        return round(delta, 3)
    if session_ok:
        return round(last, 3)
    if delta_ok:
        return round(delta, 3)
    return None


def annotate_charge(charge: dict) -> dict:
    """Add the energy a summary should count. The stored number is not edited.

    ``energy_counted_kwh`` is what a total should add. ``energy_ignored`` is
    true when the saved value was replaced or left out.
    """
    if "energy_counted_kwh" in charge and "energy_ignored" in charge:
        return charge
    start = charge.get("start_percent")
    end = charge.get("end_percent")
    readings = charge.get("readings") or []
    raw = [item.get("energy_added_kwh") for item in readings if isinstance(item, dict)]
    corrected = resolve_charge_energy(raw, start, end) if any(item is not None for item in raw) else None
    stored = charge.get("energy_added_kwh")
    counted, ignored = _choose(corrected, stored, start, end)
    updated = dict(charge)
    updated["energy_counted_kwh"] = counted
    updated["energy_ignored"] = ignored
    return updated


def _choose(corrected, stored, start, end) -> tuple[float | None, bool]:
    if corrected is not None:
        if stored is None:
            return corrected, False
        try:
            saved = float(stored)
        except (TypeError, ValueError):
            return corrected, True
        if abs(saved - corrected) <= 0.05:
            return saved, False
        return corrected, True
    if stored is None:
        return None, False
    try:
        saved = float(stored)
    except (TypeError, ValueError):
        return None, True
    if plausible_energy(saved, start, end):
        return saved, False
    return None, True


def _clean(values: list[float]) -> list[float]:
    cleaned: list[float] = []
    for value in values:
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number != number or number < 0 or number > 1000:
            continue
        cleaned.append(number)
    return cleaned


def _segments(values: list[float]) -> list[list[float]]:
    segments = [[values[0]]]
    for value in values[1:]:
        if _reset(segments[-1][-1], value):
            segments.append([value])
        else:
            segments[-1].append(value)
    return segments


def _reset(previous: float, current: float) -> bool:
    return current + 0.3 < previous and previous - current >= _RESET_DROP_KWH
