"""Command line entry for the install gate, vehicle commands, and doctor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from adapters.detect import VALID_ADAPTERS, resolve_adapter
from adapters.grokbot import INSTALL_NOTE as GROK_INSTALL_NOTE
from adapters.muse import INSTALL_NOTE as MUSE_INSTALL_NOTE
from core.config import repo_root
from core.doctor import run_doctor
from core.errors import AgentDriveError, AmbiguousAdapter, ConfigError
from core.fleet.http import UrllibClient
from core.ops import PUBLIC_COMMANDS, build_app, format_check, format_check_json, parse_limit, parse_temp, preferred_vin, resolve_thresholds
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
    for name in ("wake", "flash", "honk", "lock", "unlock", "climate-on", "climate-off", "charge-start", "charge-stop"):
        sub.add_parser(name, parents=[confirmed])
    set_temp = sub.add_parser("set-temp", parents=[confirmed])
    set_temp.add_argument("celsius")
    charge_limit = sub.add_parser("charge-limit", parents=[confirmed])
    charge_limit.add_argument("percent")

    check = sub.add_parser("check")
    check.add_argument("--json", action="store_true")
    check.add_argument("--tyre-threshold", type=float, default=None)
    check.add_argument("--battery-threshold", type=int, default=None)
    check.add_argument("--profile", default="")

    sub.add_parser("update")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
    if args.action == "install" and choice.adapter == "muse":
        print(MUSE_INSTALL_NOTE)
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
    report = build_app(home).check(preferred=preferred_vin(), tyre_bar=tyre, low_battery_percent=battery)
    if args.json:
        print(scrub(format_check_json(report)))
    else:
        print(scrub(format_check(report)))
    return report.exit_code


def _vehicle(args, home: Path) -> int:
    argument = None
    if args.command == "set-temp":
        argument = args.celsius
        parse_temp(argument)
    elif args.command == "charge-limit":
        argument = args.percent
        parse_limit(argument)
    app = build_app(home)
    if args.command == "refresh":
        print(scrub(app.refresh()))
        return 0
    if args.command == "status":
        print(scrub(app.status(preferred=preferred_vin())))
        return 0
    statement = getattr(args, "confirm", None)
    print(scrub(app.command(args.command, preferred=preferred_vin(), argument=argument, statement=statement)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
