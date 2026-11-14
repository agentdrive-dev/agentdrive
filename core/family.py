"""Family members the owner allows to ask for parked actions.

The file is ``$AGENTDRIVE_HOME/family.yaml``. A member has a handle, the
low-risk actions they may run, and the sensitive actions they may request.
Unlock and every charging change stay requests: the command records them
and the owner approves them. A family member cannot approve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from core.atomic import write_atomic
from core.errors import ConfigError
from core.safety.catalog import load_specs

FAMILY_NAME = "family.yaml"
_HANDLE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

# Low-risk reads and parked actions. The owner opts in per member.
DIRECT_ACTIONS = {
    "advice",
    "cars",
    "charge-schedule-show",
    "check",
    "climate-off",
    "climate-on",
    "defrost-off",
    "defrost-on",
    "departure-show",
    "flash",
    "history",
    "honk",
    "lock",
    "news",
    "precondition",
    "report",
    "seat-heat",
    "sentry-on",
    "service",
    "set-temp",
    "status",
    "steering-heat",
    "tips",
    "wake",
    "where",
}

# Sensitive actions. These are requests even if the owner lists them.
CHARGING_ACTIONS = (
    "charge-amps",
    "charge-limit",
    "charge-schedule-remove",
    "charge-schedule-set",
    "charge-start",
    "charge-stop",
)
REQUEST_ACTIONS = {
    *CHARGING_ACTIONS,
    "departure-remove",
    "departure-set",
    "frunk-open",
    "remote-start",
    "sentry-off",
    "trunk-close",
    "trunk-open",
    "unlock",
}


@dataclass
class Member:
    handle: str
    direct: list[str] = field(default_factory=list)
    request: list[str] = field(default_factory=list)


@dataclass
class Family:
    members: list[Member] = field(default_factory=list)

    def by_handle(self, handle: str) -> Member | None:
        key = str(handle or "").strip().lower()
        for member in self.members:
            if member.handle == key:
                return member
        return None


def normalize_handle(value: str) -> str:
    text = " ".join(str(value or "").split()).lower()
    if not _HANDLE.fullmatch(text) or _VIN.fullmatch(text.upper()):
        raise ConfigError("A family handle must be 2 to 32 letters, digits, or hyphens.")
    return text


def load_family(home) -> Family:
    from pathlib import Path

    file_path = Path(home) / FAMILY_NAME
    if not file_path.is_file():
        return Family()
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ConfigError("family.yaml could not be read.") from exc
    if data is None:
        return Family()
    if not isinstance(data, dict):
        raise ConfigError("family.yaml must be a mapping.")
    if set(data) - {"version", "members"}:
        raise ConfigError("family.yaml contains a field this toolkit will not store.")
    members: list[Member] = []
    seen: set[str] = set()
    for item in data.get("members") or []:
        member = _member(item)
        if member.handle in seen:
            raise ConfigError("family.yaml has two members with the same handle.")
        seen.add(member.handle)
        members.append(member)
    return Family(members=members)


def save_family(home, family: Family) -> None:
    from pathlib import Path

    payload = {
        "version": 1,
        "members": [
            {"handle": member.handle, "direct": list(member.direct), "request": list(member.request)}
            for member in family.members
        ],
    }
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)
    write_atomic(Path(home) / FAMILY_NAME, text)


def add_member(home, handle: str) -> Member:
    name = normalize_handle(handle)
    family = load_family(home)
    if family.by_handle(name) is not None:
        raise ConfigError("That family member is already registered.")
    member = Member(handle=name)
    family.members.append(member)
    save_family(home, family)
    return member


def remove_member(home, handle: str) -> None:
    name = normalize_handle(handle)
    family = load_family(home)
    if family.by_handle(name) is None:
        raise ConfigError("No family member is registered with that handle.")
    family.members = [member for member in family.members if member.handle != name]
    save_family(home, family)


def allow_actions(home, handle: str, token: str) -> list[tuple[str, str]]:
    """Grant a handle an action. Sensitive names are stored as requests."""
    name = normalize_handle(handle)
    family = load_family(home)
    member = family.by_handle(name)
    if member is None:
        raise ConfigError("No family member is registered with that handle.")
    granted = expand_action(token)
    for action, kind in granted:
        if kind == "request":
            if action not in member.request:
                member.request.append(action)
            if action in member.direct:
                member.direct.remove(action)
        elif action not in member.direct:
            member.direct.append(action)
    member.direct.sort()
    member.request.sort()
    save_family(home, family)
    return granted


def revoke_action(home, handle: str, token: str) -> list[str]:
    name = normalize_handle(handle)
    family = load_family(home)
    member = family.by_handle(name)
    if member is None:
        raise ConfigError("No family member is registered with that handle.")
    names = [action for action, _kind in expand_action(token)]
    for action in names:
        if action in member.direct:
            member.direct.remove(action)
        if action in member.request:
            member.request.remove(action)
    save_family(home, family)
    return names


def classify(home, actor: str, action: str) -> str:
    """Return ``direct``, ``request``, or ``deny``."""
    name = normalize_handle(actor)
    member = load_family(home).by_handle(name)
    if member is None:
        raise ConfigError("No family member is registered with that handle.")
    if action in REQUEST_ACTIONS:
        if action in member.request or action in member.direct:
            return "request"
        return "deny"
    if action in member.direct:
        return "direct"
    return "deny"


def expand_action(token: str) -> list[tuple[str, str]]:
    text = " ".join(str(token or "").split()).lower()
    if text == "charging":
        return [(name, "request") for name in CHARGING_ACTIONS]
    if text in REQUEST_ACTIONS:
        return [(text, "request")]
    if text in DIRECT_ACTIONS:
        return [(text, "direct")]
    raise ConfigError("That action is not one a family member can be given.")


def format_family(family: Family) -> str:
    if not family.members:
        return "No family members are registered."
    lines = ["Family members."]
    for member in family.members:
        direct = ", ".join(member.direct) or "none"
        request = ", ".join(member.request) or "none"
        lines.append(f"{member.handle}")
        lines.append(f"  direct: {direct}")
        lines.append(f"  request: {request}")
    return "\n".join(lines)


def allow_text(handle: str, granted: list[tuple[str, str]]) -> str:
    lines = []
    for action, kind in granted:
        if kind == "request":
            lines.append(f"{handle} may request {action}. It is not run until the owner approves it.")
        else:
            lines.append(f"{handle} may run {action} directly.")
    return "\n".join(lines)


def action_phrase(command: str) -> str:
    """Confirmation phrase for a saved request. Matches the safety catalog."""
    from core.ops import PUBLIC_COMMANDS

    spec_name = {
        "charge-schedule-remove": "charging_schedule_remove",
        "charge-schedule-set": "charging_schedule_set",
        "departure-remove": "departure_schedule_remove",
        "departure-set": "departure_schedule_set",
    }.get(command, PUBLIC_COMMANDS.get(command, ""))
    spec = load_specs().get(spec_name)
    if spec is None:
        raise ConfigError("That request cannot be run.")
    return spec.action


def owner_confirmation(command: str, label: str) -> str:
    phrase = action_phrase(command)
    if label:
        return f"I confirm {phrase} on {label}"
    return f"I confirm {phrase}"


def _member(item) -> Member:
    if not isinstance(item, dict):
        raise ConfigError("A family member must be a mapping.")
    if set(item) - {"handle", "direct", "request"}:
        raise ConfigError("A family member has a field this toolkit will not store.")
    handle = normalize_handle(str(item.get("handle") or ""))
    direct = _names(item.get("direct") or [], DIRECT_ACTIONS, "direct")
    request = _names(item.get("request") or [], REQUEST_ACTIONS, "request")
    return Member(handle=handle, direct=direct, request=request)


def _names(value, allowed: set[str], label: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"A family member {label} list must be a list.")
    names: list[str] = []
    for item in value:
        text = str(item or "").strip().lower()
        if text not in allowed:
            raise ConfigError(f"A family member {label} list has an unknown action.")
        if text not in names:
            names.append(text)
    names.sort()
    return names
