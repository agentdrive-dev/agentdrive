#!/usr/bin/env python3
"""Add max defrost to a local tesla-control build.

Upstream tesla-control does not register SetPreconditioningMax. That signed
call is max defrost, on or off. This inserts one subcommand, preconditioning-max,
and leaves the manual override off. It does not add a second signer.
"""

from __future__ import annotations

import sys
from pathlib import Path

ANCHOR = '\t"product-info": {'
MARKER = '"preconditioning-max":'

SNIPPET = """\t"preconditioning-max": {
\t\thelp:             "Set max defrost to STATE ('on' or 'off'). Does not force the manual override.",
\t\trequiresAuth:     true,
\t\trequiresFleetAPI: false,
\t\targs: []Argument{
\t\t\t{name: "STATE", help: "'on' or 'off'"},
\t\t},
\t\thandler: func(ctx context.Context, _ *account.Account, car *vehicle.Vehicle, args map[string]string) error {
\t\t\tvar state bool
\t\t\tswitch args["STATE"] {
\t\t\tcase "on":
\t\t\t\tstate = true
\t\t\tcase "off":
\t\t\t\tstate = false
\t\t\tdefault:
\t\t\t\treturn fmt.Errorf("max defrost state must be 'on' or 'off'")
\t\t\t}
\t\t\treturn car.SetPreconditioningMax(ctx, state, false)
\t\t},
\t},
"""


def ensure_defrost_command(path: Path) -> bool:
    """Insert the subcommand when it is missing. Return True when the file changes."""
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return False
    if ANCHOR not in text:
        raise SystemExit("tesla-control commands.go has no product-info command, so max defrost was not added")
    path.write_text(text.replace(ANCHOR, SNIPPET + ANCHOR, 1), encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: patch_tesla_control_defrost.py path/to/commands.go", file=sys.stderr)
        return 2
    target = Path(argv[1])
    if not target.is_file():
        print("tesla-control commands.go was not found", file=sys.stderr)
        return 1
    changed = ensure_defrost_command(target)
    print("preconditioning-max added" if changed else "preconditioning-max already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
