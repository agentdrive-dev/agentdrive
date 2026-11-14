"""One confirmation authorizes one sensitive action and is then spent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

from core.errors import ConfirmationRequired, ScheduleBlocked
from core.safety.catalog import CommandSpec


@dataclass(frozen=True)
class Challenge:
    action_id: str
    command: str
    summary: str
    car: str = ""


@dataclass(frozen=True)
class Confirmation:
    action_id: str
    command: str
    accepted: bool
    source: str
    statement: str


def new_challenge(command: str, summary: str, car: str = "") -> Challenge:
    return Challenge(action_id=uuid4().hex, command=command, summary=summary, car=car)


class ConfirmationBroker:
    def __init__(self) -> None:
        self.used: set[str] = set()

    def authorize(
        self,
        spec: CommandSpec,
        challenge: Challenge,
        response: Confirmation | None,
        *,
        via_schedule: bool,
    ) -> None:
        if spec.tier != "sensitive":
            return
        if via_schedule:
            raise ScheduleBlocked("A schedule cannot confirm or skip a sensitive command.")
        if response is None or response.source != "owner" or not response.accepted:
            raise ConfirmationRequired("This action needs an explicit confirmation from the owner.")
        if response.action_id != challenge.action_id or response.command != spec.name:
            raise ConfirmationRequired("That confirmation belongs to a different action.")
        if response.action_id in self.used:
            raise ConfirmationRequired("That confirmation was already used.")
        statement = " ".join(response.statement.lower().split())
        if not _names_command(statement, spec):
            raise ConfirmationRequired("Name the action and confirm it. A plain yes is not enough.")
        if challenge.car and not _has_phrase(statement, challenge.car.lower()):
            raise ConfirmationRequired(
                "Name the car in the confirmation. A confirmation for one car cannot be used on another."
            )
        self.used.add(response.action_id)


def _names_command(statement: str, spec: CommandSpec) -> bool:
    if "confirm" not in statement.split():
        return False
    phrases = {spec.name, spec.name.replace("_", " "), spec.action}
    return any(phrase and _has_phrase(statement, phrase) for phrase in phrases)


def _has_phrase(statement: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", statement) is not None
