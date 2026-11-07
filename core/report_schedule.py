"""Daily vehicle-report schedule stored in the private config home.

The default is 07:30 local time, enabled. Changing it writes
``$AGENTDRIVE_HOME/runtime.yaml`` and nothing else. AgentDrive does not
install a crontab, a systemd timer, or an assistant routine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.atomic import write_atomic
from core.errors import ConfigError
from core.paths import RUNTIME_NAME, load_runtime

DEFAULT_TIME = "07:30"
_TIME = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")

# Printed on adapter install. The same instruction is for every shell assistant.
DAILY_REPORT_NOTE = (
    "Also set up the morning vehicle history by default. "
    "Run `agentdrive report-schedule`. The default is 07:30 local time and enabled. "
    "Create one daily job at that time. The job runs `agentdrive history due` and you send that usage briefing. "
    "On Monday it is the previous week, on the 1st it is the previous month, and on 1 January it is the previous year. "
    "Every other morning it is the previous day. "
    "That one job is the whole morning. A short tip is included only on some mornings, and a service reminder "
    "is included only when one is due and was not sent in the last two months. "
    "On a Monday weekly briefing, one new official Tesla note is included when the local Tesla site has one. "
    "When it does not, that part is left out. Do not add a second morning job. "
    "`agentdrive report` is the live parked snapshot and can still be run when the user wants the car's current status. "
    "If the user changes the time or turns the report off, update that job to match `agentdrive report-schedule`. "
    "AgentDrive stores the schedule and does not run the job itself."
)


@dataclass(frozen=True)
class ReportSchedule:
    time: str = DEFAULT_TIME
    enabled: bool = True


class _Quoted(str):
    """A string PyYAML must quote so ``07:30`` is not read as a number."""


class _RuntimeDumper(yaml.SafeDumper):
    pass


def _represent_quoted(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(value), style='"')


_RuntimeDumper.add_representer(_Quoted, _represent_quoted)


def parse_report_time(value: str) -> str:
    """Accept HH:MM in 24-hour form, for example 07:30."""
    text = str(value).strip()
    if not _TIME.fullmatch(text):
        raise ConfigError('Time must be HH:MM in 24-hour form, for example 07:30.')
    return text


def load_report_schedule(home: Path) -> ReportSchedule:
    """Return the stored schedule, or 07:30 enabled when it has not been set."""
    raw = load_runtime(home).get("report_schedule")
    if raw is None:
        return ReportSchedule()
    if not isinstance(raw, dict):
        raise ConfigError("report_schedule in runtime.yaml must be a mapping.")
    unknown = set(raw) - {"enabled", "time"}
    if unknown:
        raise ConfigError("report_schedule only accepts enabled and time.")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("report_schedule.enabled must be true or false.")
    time_value = raw.get("time", DEFAULT_TIME)
    if not isinstance(time_value, str):
        raise ConfigError('report_schedule.time must be a quoted HH:MM string, for example "07:30".')
    return ReportSchedule(time=parse_report_time(time_value), enabled=enabled)


def save_report_schedule(home: Path, schedule: ReportSchedule) -> None:
    """Merge the schedule into runtime.yaml and leave key and token paths in place."""
    home = Path(home)
    data = load_runtime(home)
    data["report_schedule"] = {"enabled": bool(schedule.enabled), "time": _Quoted(schedule.time)}
    payload = yaml.dump(data, Dumper=_RuntimeDumper, sort_keys=False, allow_unicode=False)
    write_atomic(home / RUNTIME_NAME, payload)


def set_report_time(home: Path, value: str) -> ReportSchedule:
    current = load_report_schedule(home)
    updated = ReportSchedule(time=parse_report_time(value), enabled=current.enabled)
    save_report_schedule(home, updated)
    return updated


def set_report_enabled(home: Path, enabled: bool) -> ReportSchedule:
    current = load_report_schedule(home)
    updated = ReportSchedule(time=current.time, enabled=enabled)
    save_report_schedule(home, updated)
    return updated


def crontab_example(schedule: ReportSchedule) -> str:
    """A cron line for the configured local time. This is not installed."""
    hour = int(schedule.time[:2])
    minute = int(schedule.time[3:])
    return f"{minute} {hour} * * * agentdrive history due"


def format_schedule(schedule: ReportSchedule, *, action: str | None = None) -> str:
    """Human-readable schedule. ``action`` is set, enable, or disable after a change."""
    lines: list[str] = [
        f"report schedule: {schedule.time} local time",
        f"enabled: {'yes' if schedule.enabled else 'no'}",
    ]
    if action == "set":
        lines.append("The time is stored in the config home. AgentDrive does not change the assistant job.")
        if schedule.enabled:
            lines.append(
                "The assistant should update its daily job to this time, run `agentdrive history due`, and send the result."
            )
        else:
            lines.append("The morning history is off, so the assistant should leave its job removed until the report is enabled.")
    elif action == "enable":
        lines.append("The morning history is on. The assistant should create a daily job at this local time.")
        lines.append("The job runs `agentdrive history due` and the assistant sends the result. AgentDrive does not install the job.")
    elif action == "disable":
        lines.append("The morning history is off. The assistant should remove its scheduled job.")
        lines.append("AgentDrive does not change that job by itself.")
    elif schedule.enabled:
        lines.append("The assistant should run `agentdrive history due` every day at this local time and send the result.")
        lines.append("`agentdrive report` remains the live parked snapshot when the current status is wanted.")
        lines.append("AgentDrive does not run in the background and does not install the job.")
    else:
        lines.append("The morning history is off. The assistant should not create a job, and should remove one that already exists.")
        lines.append("AgentDrive does not change that job by itself.")
    if schedule.enabled:
        lines.append(f"crontab example: {crontab_example(schedule)}")
    return "\n".join(lines)
