"""Command line entry for the install gate, vehicle commands, and doctor."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from adapters.detect import VALID_ADAPTERS, resolve_adapter
from adapters.grokbot import INSTALL_NOTE as GROK_INSTALL_NOTE
from adapters.muse import INSTALL_NOTE as MUSE_INSTALL_NOTE
from core.fleet.check_state import HEALTH_CHECK_NOTE
from core.config import repo_root
from core.doctor import run_doctor
from core.errors import AgentDriveError, AmbiguousAdapter, ConfigError
from core.family import (
    add_member,
    allow_actions,
    allow_text,
    classify,
    format_family,
    load_family,
    normalize_handle,
    owner_confirmation,
    remove_member,
    revoke_action,
)
from core.fleet.area import format_where, format_where_json
from core.fleet.charge_schedule import format_schedule_report, format_schedule_report_json, parse_schedule_id, parse_schedule_write
from core.fleet.departure import format_departure_report, format_departure_report_json, parse_departure_write
from core.garage import (
    clear_default,
    format_cars,
    load_garage,
    remember_car,
    set_default,
    store_for_saved,
    vehicle_rows,
)
from core.redact import mask_vin
from core.requests import approve_request, create_request, deny_request, list_requests
from core.fleet.http import UrllibClient
from core.fleet.report import format_report, format_report_json
from core.history.official import render_news
from core.history.service import perform_log, render_service_status
from core.history.usage import render_briefing, render_list, render_tips
from core.ops import (
    PUBLIC_COMMANDS,
    build_app,
    format_check,
    format_check_json,
    parse_amps,
    parse_limit,
    parse_on_off,
    parse_seat_heat,
    parse_temp,
    preferred_vin,
    resolve_thresholds,
)
from core.report_schedule import DAILY_REPORT_NOTE, format_schedule, load_report_schedule, set_report_enabled, set_report_time
from core.paths import resolve_home, resolve_key, resolve_token_file
from core.redact import scrub
from core.safety.install_gate import accept, assert_install_allowed, present
from core.update import update_install


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--adapter", choices=VALID_ADAPTERS)
    common.add_argument("--redetect", action="store_true")

    confirmed = argparse.ArgumentParser(add_help=False)
    confirmed.add_argument("--confirm", default=None, help="Confirmation sentence for a sensitive command")

    parser = argparse.ArgumentParser(prog="agentdrive")
    parser.add_argument("--home", default="", help="Config home. Defaults to $AGENTDRIVE_HOME or ~/.config/agentdrive")
    parser.add_argument("--car", default="", help="Nickname of the car this command uses")
    parser.add_argument("--as", dest="actor", default="", help="Family member handle. The owner leaves this unset.")
    sub = parser.add_subparsers(dest="command", required=True)

    present_cmd = sub.add_parser("gate-present")
    present_cmd.add_argument("--turn-id", required=True)

    accept_cmd = sub.add_parser("gate-accept")
    accept_cmd.add_argument("--turn-id", required=True)
    accept_cmd.add_argument("--statement", required=True)

    sub.add_parser("gate-check")

    doctor = sub.add_parser("doctor", parents=[common])
    doctor.add_argument("--config-dir", default=str(repo_root() / "config"))
    doctor.add_argument("--secrets-dir", default="")
    doctor.add_argument("--public-key-url", default="")
    doctor.add_argument("--token-file", default="")

    adapter = sub.add_parser("adapter", parents=[common])
    adapter.add_argument("action", nargs="?", choices=["show", "install"], default="show")

    sub.add_parser("refresh")
    sub.add_parser("status")
    for name in (
        "wake",
        "flash",
        "honk",
        "lock",
        "unlock",
        "climate-on",
        "climate-off",
        "charge-start",
        "charge-stop",
        "sentry-on",
        "sentry-off",
        "defrost-on",
        "defrost-off",
        "precondition",
    ):
        sub.add_parser(name, parents=[confirmed])
    sub.add_parser("trunk-open", parents=[confirmed], help="Open the rear trunk. Needs a confirmation.")
    sub.add_parser("trunk-close", parents=[confirmed], help="Close the rear trunk when the vehicle can. Needs a confirmation.")
    sub.add_parser("frunk-open", parents=[confirmed], help="Open the front trunk. Needs a confirmation.")
    sub.add_parser(
        "remote-start",
        parents=[confirmed],
        help="Enable keyless driving. Needs a confirmation. Does not drive the car.",
    )
    set_temp = sub.add_parser("set-temp", parents=[confirmed])
    set_temp.add_argument("celsius")
    seat_heat = sub.add_parser("seat-heat", parents=[confirmed])
    seat_heat.add_argument("seat")
    seat_heat.add_argument("level")
    steering_heat = sub.add_parser("steering-heat", parents=[confirmed])
    steering_heat.add_argument("state")
    charge_limit = sub.add_parser("charge-limit", parents=[confirmed])
    charge_limit.add_argument("percent")
    charge_amps = sub.add_parser(
        "charge-amps",
        parents=[confirmed],
        help="Set the charging current in amps. Needs a confirmation. The car may use less.",
    )
    charge_amps.add_argument("amps")

    schedule = sub.add_parser("charge-schedule", help="Read or change the in-car charging timetable")
    schedule_sub = schedule.add_subparsers(dest="schedule_action", required=True)
    show = schedule_sub.add_parser("show", help="Read the in-car charging timetable. Does not wake the car.")
    show.add_argument("--json", action="store_true")
    set_cmd = schedule_sub.add_parser("set", parents=[confirmed], help="Add an in-car charging schedule")
    set_cmd.add_argument("days", help="all, weekdays, or comma-separated Sun, Mon, Tues, Wed, Thurs, Fri, Sat")
    set_cmd.add_argument("time", help="Local window HH:MM-HH:MM, HH:MM-, or -HH:MM")
    set_cmd.add_argument("latitude")
    set_cmd.add_argument("longitude")
    set_cmd.add_argument("--once", action="store_true", help="Run once. Omit to repeat every week.")
    set_cmd.add_argument(
        "--enabled",
        choices=("true", "false"),
        default="true",
        help="true adds an enabled schedule. false adds a disabled schedule.",
    )
    remove = schedule_sub.add_parser("remove", parents=[confirmed], help="Remove one in-car charging schedule by id")
    remove.add_argument("schedule_id", metavar="ID")

    departure = sub.add_parser("departure", help="Read or change a departure time that preconditions the cabin")
    departure_sub = departure.add_subparsers(dest="departure_action", required=True)
    departure_show = departure_sub.add_parser("show", help="Read departure schedules. Does not wake the car.")
    departure_show.add_argument("--json", action="store_true")
    departure_set = departure_sub.add_parser("set", parents=[confirmed], help="Add or change a departure precondition schedule")
    departure_set.add_argument("days", help="all, weekdays, or comma-separated Sun, Mon, Tues, Wed, Thurs, Fri, Sat")
    departure_set.add_argument("time", help="Cabin ready by this local time, HH:MM")
    departure_set.add_argument("latitude", nargs="?", default=None, help="Optional. Omit both coordinates to use the car's current location.")
    departure_set.add_argument("longitude", nargs="?", default=None)
    departure_set.add_argument("--once", action="store_true", help="Run once. Omit to repeat every week.")
    departure_set.add_argument(
        "--enabled",
        choices=("true", "false"),
        default="true",
        help="true adds an enabled schedule. false adds a disabled schedule.",
    )
    departure_set.add_argument("--id", default=None, help="Change this schedule id. Omit to add a schedule.")
    departure_remove = departure_sub.add_parser("remove", parents=[confirmed], help="Remove one departure schedule by id")
    departure_remove.add_argument("schedule_id", metavar="ID")

    check = sub.add_parser("check")
    check.add_argument("--json", action="store_true")
    check.add_argument("--tyre-threshold", type=float, default=None)
    check.add_argument("--battery-threshold", type=int, default=None)
    check.add_argument("--profile", default="")

    report = sub.add_parser("report")
    report.add_argument("--json", action="store_true")

    report_schedule = sub.add_parser("report-schedule")
    report_action = report_schedule.add_subparsers(dest="report_action")
    set_time = report_action.add_parser("set")
    set_time.add_argument("time")
    report_action.add_parser("enable")
    report_action.add_parser("disable")

    where = sub.add_parser("where", help="Print a coarse area. Does not wake the vehicle.")
    where.add_argument("--json", action="store_true")
    where.add_argument(
        "--precise",
        action="store_true",
        help="Print latitude, longitude, and a map link when the car is already online. Does not wake the car.",
    )

    history = sub.add_parser("history", help="Read saved vehicle history on this computer. Does not contact the car.")
    history_sub = history.add_subparsers(dest="history_action", required=True)
    for name in ("trips", "charges", "snapshots", "alerts", "config"):
        listed = history_sub.add_parser(name, help="Read saved history. Does not contact the car.")
        listed.add_argument("--json", action="store_true")
        listed.add_argument("--limit", type=int, default=20)
    for name in ("due", "daily", "weekly", "monthly", "yearly"):
        period = history_sub.add_parser(name, help="Print a usage briefing from saved history. Does not contact the car.")
        period.add_argument("--json", action="store_true")

    tips = sub.add_parser("tips", help="Say whether a usage tip is due this morning. Does not contact the car.")
    tips.add_argument("--json", action="store_true")

    news = sub.add_parser(
        "news",
        help="Print one new official Tesla item for this locale, when there is one. Does not wake the car.",
    )
    news.add_argument("--json", action="store_true")

    service = sub.add_parser("service", help="Log or read service events from the saved odometer. Does not wake the car.")
    service.add_argument("--json", action="store_true")
    service_sub = service.add_subparsers(dest="service_action")
    service_log = service_sub.add_parser("log", help="Record a tyre or service event against the saved odometer.")
    service_log.add_argument("kind", choices=("rotation", "replacement", "maintenance"))
    service_log.add_argument("--note", default="")
    service_log.add_argument("--interval-km", type=float, default=None)
    service_status = service_sub.add_parser("status", help="Show kilometres since logged service. Does not wake the car.")
    service_status.add_argument("--json", action="store_true")

    sub.add_parser("update")

    cars = sub.add_parser("cars", help="List cars on this account, or save a nickname. Listing does not wake a car.")
    cars_action = cars.add_subparsers(dest="cars_action")
    cars_action.add_parser("list", help="List cars from the vehicle list. Does not wake a car.")
    cars_name = cars_action.add_parser("name", help="Save a nickname for the car with this VIN tail.")
    cars_name.add_argument("tail", help="Last four characters from `agentdrive cars`.")
    cars_name.add_argument("nickname")
    cars_default = cars_action.add_parser("default", help="Use this nickname when a command does not pass --car.")
    cars_default.add_argument("nickname")
    cars_action.add_parser("clear-default", help="Clear the default car. Several cars then need --car.")

    family = sub.add_parser("family", help="Register a family member and the actions they may request.")
    family_action = family.add_subparsers(dest="family_action")
    family_action.add_parser("list")
    family_add = family_action.add_parser("add")
    family_add.add_argument("handle")
    family_remove = family_action.add_parser("remove")
    family_remove.add_argument("handle")
    family_allow = family_action.add_parser("allow")
    family_allow.add_argument("handle")
    family_allow.add_argument("action")
    family_revoke = family_action.add_parser("revoke")
    family_revoke.add_argument("handle")
    family_revoke.add_argument("action")

    request = sub.add_parser("request", help="List, approve, or deny a family member's pending request.")
    request_action = request.add_subparsers(dest="request_action")
    request_action.add_parser("list")
    request_approve = request_action.add_parser("approve")
    request_approve.add_argument("request_id")
    request_deny = request_action.add_parser("deny")
    request_deny.add_argument("request_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize_global(argv))
    home = Path(args.home) if args.home else resolve_home()
    try:
        if args.command == "gate-present":
            sys.stdout.write(present(home, args.turn_id))
            return 0
        if args.command == "gate-accept":
            accept(home, args.turn_id, args.statement)
            print("Acceptance recorded.")
            return 0
        if args.command == "gate-check":
            assert_install_allowed(home)
            print("Install is allowed.")
            return 0
        if args.command == "adapter":
            return _adapter(args, home)
        if args.command == "doctor":
            return _doctor(args, home)
        if args.command == "update":
            return _update(home)
        if args.command == "check":
            return _check(args, home)
        if args.command == "report":
            return _report(args, home)
        if args.command == "report-schedule":
            return _report_schedule(args, home)
        if args.command == "where":
            return _where(args, home)
        if args.command == "history":
            return _history(args, home)
        if args.command == "tips":
            return _tips(args, home)
        if args.command == "news":
            return _news(args, home)
        if args.command == "service":
            return _service(args, home)
        if args.command == "cars":
            return _cars(args, home)
        if args.command == "family":
            return _family(args, home)
        if args.command == "request":
            return _request(args, home)
        if args.command == "charge-schedule":
            return _charge_schedule(args, home)
        if args.command == "departure":
            return _departure(args, home)
        if args.command in {"refresh", "status", *PUBLIC_COMMANDS}:
            return _vehicle(args, home)
    except AmbiguousAdapter as exc:
        print(scrub(str(exc)), file=sys.stderr)
        return 3
    except AgentDriveError as exc:
        print(scrub(str(exc)), file=sys.stderr)
        return 2
    return 2


def _adapter(args, home: Path) -> int:
    choice = resolve_adapter(
        home=home,
        cli_adapter=args.adapter,
        redetect=args.redetect,
    )
    print(f"Adapter {choice.summary()}.")
    if choice.adapter == "unknown":
        print("No host bot was detected. Runtime will use the template adapter.")
    if args.action == "install" and choice.adapter == "grokbot":
        print(GROK_INSTALL_NOTE)
    elif args.action == "install" and choice.adapter == "muse":
        print(MUSE_INSTALL_NOTE)
    elif args.action == "install":
        print(HEALTH_CHECK_NOTE + DAILY_REPORT_NOTE)
    return 0


def _doctor(args, home: Path) -> int:
    if args.secrets_dir:
        secrets = Path(args.secrets_dir)
        key = secrets / "private-key.pem"
    else:
        secrets = home
        key = resolve_key(home)
    url = args.public_key_url
    url_file = home / "public-key-url"
    if not url and url_file.is_file():
        url = url_file.read_text(encoding="utf-8").strip()
    token = Path(args.token_file) if args.token_file else resolve_token_file(home)
    client = UrllibClient()
    checks = run_doctor(
        config_dir=Path(args.config_dir),
        secrets_dir=secrets,
        public_key_url=url or None,
        token_file=token,
        get_text=client.get_text,
        key_file=key,
    )
    failed = False
    for check in checks:
        state = "pass" if check.ok else "fail"
        print(f"{state:4} {check.name}: {check.detail}")
        failed = failed or not check.ok
    try:
        choice = resolve_adapter(home=home, cli_adapter=args.adapter, redetect=args.redetect)
        print(f"pass adapter: {choice.summary()}.")
    except AmbiguousAdapter as exc:
        print(f"fail adapter: {exc}")
        failed = True
    return 1 if failed else 0


def _update(home: Path) -> int:
    source = update_install(repo_root(), home)
    if source == "git":
        print("Updated from git. Private configuration was left in place.")
    else:
        print("Updated from the latest main archive. Private configuration was left in place.")
    return 0


def _check(args, home: Path) -> int:
    if args.tyre_threshold is not None and args.tyre_threshold <= 0:
        raise ConfigError("The tyre threshold must be greater than 0.")
    if args.battery_threshold is not None and not 0 <= args.battery_threshold <= 100:
        raise ConfigError("The battery threshold must be from 0 to 100.")
    profile = Path(args.profile) if args.profile else None
    tyre, battery = resolve_thresholds(
        home,
        profile_path=profile,
        tyre_bar=args.tyre_threshold,
        low_battery_percent=args.battery_threshold,
    )
    _guard_read(args, home, "check")
    report = _open_app(home, args).check(preferred=preferred_vin(), tyre_bar=tyre, low_battery_percent=battery)
    if args.json:
        print(scrub(format_check_json(report)))
    else:
        print(scrub(format_check(report)))
    return report.exit_code


def _report(args, home: Path) -> int:
    _guard_read(args, home, "report")
    briefing = _open_app(home, args).report(preferred=preferred_vin())
    if args.json:
        print(scrub(format_report_json(briefing)))
    else:
        print(scrub(format_report(briefing)))
    return briefing.exit_code


def _report_schedule(args, home: Path) -> int:
    action = args.report_action
    if action == "set":
        schedule = set_report_time(home, args.time)
    elif action == "enable":
        schedule = set_report_enabled(home, True)
    elif action == "disable":
        schedule = set_report_enabled(home, False)
    else:
        schedule = load_report_schedule(home)
        action = None
    print(format_schedule(schedule, action=action))
    return 0


def _where(args, home: Path) -> int:
    _guard_read(args, home, "where")
    report = _open_app(home, args).where(preferred=preferred_vin(), precise=bool(args.precise))
    rendered = format_where_json(report) if args.json else format_where(report)
    print(scrub(rendered))
    return report.exit_code


def _history(args, home: Path) -> int:
    _guard_read(args, home, "history")
    store = store_for_saved(home, args.car or None)
    action = args.history_action
    if action in {"trips", "charges", "snapshots", "alerts", "config"}:
        if args.limit < 1 or args.limit > 200:
            raise ConfigError("The history limit must be from 1 to 200.")
        print(scrub(render_list(home, action, limit=args.limit, as_json=args.json, store=store)))
        return 0
    print(scrub(render_briefing(home, action, as_json=args.json, store=store)))
    return 0


def _tips(args, home: Path) -> int:
    _guard_read(args, home, "tips")
    print(scrub(render_tips(home, as_json=args.json, store=store_for_saved(home, args.car or None))))
    return 0


def _news(args, home: Path) -> int:
    _guard_read(args, home, "news")
    text = render_news(home, as_json=args.json, store=store_for_saved(home, args.car or None))
    if text:
        print(scrub(text))
    return 0


def _service(args, home: Path) -> int:
    _guard_read(args, home, "service")
    store = store_for_saved(home, args.car or None)
    if args.service_action == "log":
        print(scrub(perform_log(home, args.kind, note=args.note, interval_km=args.interval_km, store=store)))
        return 0
    as_json = bool(args.json)
    print(scrub(render_service_status(home, as_json=as_json, store=store)))
    return 0


def _charge_schedule(args, home: Path) -> int:
    if args.schedule_action == "show":
        _guard_read(args, home, "charge-schedule-show")
        report = _open_app(home, args).charge_schedule_show(preferred=preferred_vin())
        text = format_schedule_report_json(report) if args.json else format_schedule_report(report)
        print(scrub(text))
        return 0
    statement = getattr(args, "confirm", None)
    action = f"charge-schedule-{args.schedule_action}"
    if args.schedule_action == "set":
        spec = parse_schedule_write(
            args.days,
            args.time,
            args.latitude,
            args.longitude,
            once=args.once,
            enabled=args.enabled == "true",
        )
        app = _open_app(home, args)
        held = _family_hold(
            args,
            home,
            app,
            action,
            detail={
                "days": args.days,
                "time": args.time,
                "latitude": args.latitude,
                "longitude": args.longitude,
                "once": bool(args.once),
                "enabled": args.enabled == "true",
            },
        )
        if held is not None:
            print(scrub(held))
            return 0
        print(
            scrub(
                app.charge_schedule_set(
                    spec,
                    preferred=preferred_vin(),
                    statement=statement,
                )
            )
        )
        return 0
    if args.schedule_action == "remove":
        schedule_id = parse_schedule_id(args.schedule_id)
        app = _open_app(home, args)
        held = _family_hold(args, home, app, action, detail={"schedule_id": schedule_id})
        if held is not None:
            print(scrub(held))
            return 0
        print(
            scrub(
                app.charge_schedule_remove(
                    schedule_id,
                    preferred=preferred_vin(),
                    statement=statement,
                )
            )
        )
        return 0
    raise ConfigError("unknown command")


def _departure(args, home: Path) -> int:
    if args.departure_action == "show":
        _guard_read(args, home, "departure-show")
        report = _open_app(home, args).departure_show(preferred=preferred_vin())
        text = format_departure_report_json(report) if args.json else format_departure_report(report)
        print(scrub(text))
        return 0
    statement = getattr(args, "confirm", None)
    action = f"departure-{args.departure_action}"
    if args.departure_action == "set":
        schedule_id = args.id
        if args.enabled == "false" and not schedule_id:
            schedule_id = str(int(time.time()))
        spec = parse_departure_write(
            args.days,
            args.time,
            args.latitude,
            args.longitude,
            once=args.once,
            enabled=args.enabled == "true",
            schedule_id=schedule_id,
        )
        app = _open_app(home, args)
        held = _family_hold(
            args,
            home,
            app,
            action,
            detail={
                "days": args.days,
                "time": args.time,
                "latitude": args.latitude,
                "longitude": args.longitude,
                "once": bool(args.once),
                "enabled": args.enabled == "true",
                "schedule_id": schedule_id,
            },
        )
        if held is not None:
            print(scrub(held))
            return 0
        print(
            scrub(
                app.departure_set(
                    spec,
                    preferred=preferred_vin(),
                    statement=statement,
                )
            )
        )
        return 0
    if args.departure_action == "remove":
        schedule_id = parse_schedule_id(args.schedule_id)
        app = _open_app(home, args)
        held = _family_hold(args, home, app, action, detail={"schedule_id": schedule_id})
        if held is not None:
            print(scrub(held))
            return 0
        print(
            scrub(
                app.departure_remove(
                    schedule_id,
                    preferred=preferred_vin(),
                    statement=statement,
                )
            )
        )
        return 0
    raise ConfigError("unknown command")


def _vehicle(args, home: Path) -> int:
    argument = None
    extra = None
    if args.command == "set-temp":
        argument = args.celsius
        parse_temp(argument)
    elif args.command == "charge-limit":
        argument = args.percent
        parse_limit(argument)
    elif args.command == "charge-amps":
        argument = args.amps
        parse_amps(argument)
    elif args.command == "seat-heat":
        extra = list(parse_seat_heat(args.seat, args.level))
    elif args.command == "steering-heat":
        extra = [parse_on_off(args.state, "steering heat")]
    app = _open_app(home, args)
    if args.command == "refresh":
        _guard_read(args, home, "refresh")
        print(scrub(app.refresh()))
        return 0
    if args.command == "status":
        _guard_read(args, home, "status")
        print(scrub(app.status(preferred=preferred_vin())))
        return 0
    held = _family_hold(args, home, app, args.command, argument=argument, extra=extra)
    if held is not None:
        print(scrub(held))
        return 0
    statement = getattr(args, "confirm", None)
    print(
        scrub(
            app.command(
                args.command,
                preferred=preferred_vin(),
                argument=argument,
                statement=statement,
                extra=extra,
            )
        )
    )
    return 0


_GLOBAL = {"--home", "--car", "--as"}


def _normalize_global(argv: list[str] | None) -> list[str] | None:
    """Move --home, --car, and --as in front of the subcommand."""
    if argv is None:
        return None
    pulled: list[str] = []
    rest: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        name, sep, value = token.partition("=")
        if sep and name in _GLOBAL:
            pulled.extend([name, value])
            index += 1
            continue
        if token in _GLOBAL:
            if index + 1 >= len(argv) or str(argv[index + 1]).startswith("-"):
                pulled.append(token)
                index += 1
                continue
            pulled.extend([token, argv[index + 1]])
            index += 2
            continue
        rest.append(token)
        index += 1
    return pulled + rest


def _open_app(home: Path, args):
    app = build_app(home)
    car = getattr(args, "car", "") or ""
    if car:
        app.car_name = car
    return app


def _owner_only(args) -> None:
    if getattr(args, "actor", ""):
        raise ConfigError("Only the owner can change cars, family members, or requests.")


def _guard_read(args, home: Path, action: str) -> None:
    actor = getattr(args, "actor", "") or ""
    if not actor:
        return
    if classify(home, actor, action) != "direct":
        raise ConfigError(f"{normalize_handle(actor)} cannot request {action}.")


def _family_hold(args, home: Path, app, action: str, *, argument=None, extra=None, detail=None):
    """Return relay text when a family member must wait for the owner."""
    actor = getattr(args, "actor", "") or ""
    if not actor:
        return None
    decision = classify(home, actor, action)
    handle = normalize_handle(actor)
    if decision == "deny":
        raise ConfigError(f"{handle} cannot request {action}.")
    if decision == "direct":
        return None
    selection = app.prepare(preferred_vin())
    car = selection.car
    return create_request(
        home,
        member=handle,
        car=car.nickname if car else "",
        car_id=car.id if car else "",
        label=selection.label,
        command=action,
        argument=argument,
        extra=list(extra) if extra else None,
        detail=detail,
    )


def _cars(args, home: Path) -> int:
    action = args.cars_action or "list"
    if action == "list":
        _guard_read_if_actor(args, home, "cars")
        app = _open_app(home, args)
        rows = vehicle_rows(app.reader.list_vehicles())
        print(scrub(format_cars(rows, load_garage(home))))
        return 0
    _owner_only(args)
    if action == "name":
        app = _open_app(home, args)
        rows = vehicle_rows(app.reader.list_vehicles())
        car = remember_car(home, rows, args.tail, args.nickname)
        print(f"Saved {car.nickname} as {mask_vin(car.vin)}.")
        return 0
    if action == "default":
        garage = set_default(home, args.nickname)
        print(f"Default car is {garage.default}.")
        return 0
    if action == "clear-default":
        clear_default(home)
        print("No default car is set.")
        return 0
    raise ConfigError("unknown command")


def _guard_read_if_actor(args, home: Path, action: str) -> None:
    """Family members need a direct grant. The owner does not."""
    if getattr(args, "actor", ""):
        _guard_read(args, home, action)


def _family(args, home: Path) -> int:
    _owner_only(args)
    action = args.family_action or "list"
    if action == "list":
        print(format_family(load_family(home)))
        return 0
    if action == "add":
        member = add_member(home, args.handle)
        print(f"Added family member {member.handle}.")
        return 0
    if action == "remove":
        remove_member(home, args.handle)
        print(f"Removed family member {normalize_handle(args.handle)}.")
        return 0
    if action == "allow":
        granted = allow_actions(home, args.handle, args.action)
        print(allow_text(normalize_handle(args.handle), granted))
        return 0
    if action == "revoke":
        names = revoke_action(home, args.handle, args.action)
        handle = normalize_handle(args.handle)
        print("\n".join(f"{handle} can no longer request {name}." for name in names))
        return 0
    raise ConfigError("unknown command")


def _request(args, home: Path) -> int:
    action = args.request_action or "list"
    if action == "list":
        _owner_only(args)
        print(scrub(list_requests(home)))
        return 0
    if action == "approve":
        print(
            scrub(
                approve_request(
                    home,
                    args.request_id,
                    actor=getattr(args, "actor", "") or "",
                    execute=lambda record: _execute_saved(home, record),
                    ready=lambda record: _ready_saved(home, record),
                )
            )
        )
        return 0
    if action == "deny":
        print(scrub(deny_request(home, args.request_id, actor=getattr(args, "actor", "") or "")))
        return 0
    raise ConfigError("unknown command")


def _ready_saved(home: Path, record: dict) -> None:
    """Refuse a request whose car disappeared before it is spent."""
    car_id = str(record.get("car_id") or "")
    if not car_id:
        return
    if load_garage(home).by_id(car_id) is None:
        raise ConfigError("That car is no longer in the private config.")


def _execute_saved(home: Path, record: dict) -> str:
    app = build_app(home)
    garage = load_garage(home)
    car = garage.by_id(str(record.get("car_id") or "")) if record.get("car_id") else None
    preferred = car.vin if car is not None else None
    label = str(record.get("label") or "")
    if car is not None and (label or len(garage.cars) > 1):
        label = car.nickname
        app.car_name = car.nickname
    else:
        label = ""
    statement = owner_confirmation(str(record.get("command") or ""), label)
    command = str(record.get("command") or "")
    detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
    if command == "charge-schedule-set":
        spec = parse_schedule_write(
            str(detail.get("days") or ""),
            str(detail.get("time") or ""),
            str(detail.get("latitude") or ""),
            str(detail.get("longitude") or ""),
            once=bool(detail.get("once")),
            enabled=bool(detail.get("enabled", True)),
        )
        return app.charge_schedule_set(spec, preferred=preferred, statement=statement)
    if command == "charge-schedule-remove":
        return app.charge_schedule_remove(str(detail.get("schedule_id") or ""), preferred=preferred, statement=statement)
    if command == "departure-set":
        spec = parse_departure_write(
            str(detail.get("days") or ""),
            str(detail.get("time") or ""),
            detail.get("latitude"),
            detail.get("longitude"),
            once=bool(detail.get("once")),
            enabled=bool(detail.get("enabled", True)),
            schedule_id=detail.get("schedule_id"),
        )
        return app.departure_set(spec, preferred=preferred, statement=statement)
    if command == "departure-remove":
        return app.departure_remove(str(detail.get("schedule_id") or ""), preferred=preferred, statement=statement)
    return app.command(
        command,
        preferred=preferred,
        argument=record.get("argument"),
        statement=statement,
        extra=record.get("extra"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
