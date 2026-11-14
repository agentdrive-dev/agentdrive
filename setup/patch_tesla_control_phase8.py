#!/usr/bin/env python3
"""Add climate keeper, seat cooling, and bioweapon defense to tesla-control.

Upstream tesla-control does not register SetClimateKeeperMode, SetSeatCooler,
or SetBioweaponDefenseMode. This inserts those three subcommands and leaves
the rest of the file alone. It does not add a second signer.

Climate keeper passes manual override false, matching a Fleet API body that
only sends the mode. Bioweapon defense passes manual override true because
the owner asked for that mode.
"""

from __future__ import annotations

import sys
from pathlib import Path

ANCHOR = '\t"product-info": {'
MARKERS = ('"climate-keeper":', '"seat-cooler":', '"bioweapon-mode":')

SNIPPET = """\t"climate-keeper": {
\t\thelp:             "Set climate keeper mode to MODE ('off', 'keep', 'dog', or 'camp').",
\t\trequiresAuth:     true,
\t\trequiresFleetAPI: false,
\t\targs: []Argument{
\t\t\t{name: "MODE", help: "off, keep, dog, or camp"},
\t\t},
\t\thandler: func(ctx context.Context, _ *account.Account, car *vehicle.Vehicle, args map[string]string) error {
\t\t\tmodes := map[string]vehicle.ClimateKeeperMode{
\t\t\t\t"off":  vehicle.ClimateKeeperModeOff,
\t\t\t\t"keep": vehicle.ClimateKeeperModeOn,
\t\t\t\t"dog":  vehicle.ClimateKeeperModeDog,
\t\t\t\t"camp": vehicle.ClimateKeeperModeCamp,
\t\t\t}
\t\t\tmode, ok := modes[args["MODE"]]
\t\t\tif !ok {
\t\t\t\treturn fmt.Errorf("climate keeper mode must be off, keep, dog, or camp")
\t\t\t}
\t\t\treturn car.SetClimateKeeperMode(ctx, mode, false)
\t\t},
\t},
\t"seat-cooler": {
\t\thelp:             "Set seat cooling at SEAT to LEVEL. Front seats only.",
\t\trequiresAuth:     true,
\t\trequiresFleetAPI: false,
\t\targs: []Argument{
\t\t\t{name: "SEAT", help: "front-left or front-right"},
\t\t\t{name: "LEVEL", help: "off, low, medium, or high"},
\t\t},
\t\thandler: func(ctx context.Context, _ *account.Account, car *vehicle.Vehicle, args map[string]string) error {
\t\t\tseats := map[string]vehicle.SeatPosition{
\t\t\t\t"front-left":  vehicle.SeatFrontLeft,
\t\t\t\t"front-right": vehicle.SeatFrontRight,
\t\t\t}
\t\t\tposition, ok := seats[args["SEAT"]]
\t\t\tif !ok {
\t\t\t\treturn fmt.Errorf("seat cooling supports front-left and front-right only")
\t\t\t}
\t\t\tlevels := map[string]vehicle.Level{
\t\t\t\t"off":    vehicle.LevelOff,
\t\t\t\t"low":    vehicle.LevelLow,
\t\t\t\t"medium": vehicle.LevelMed,
\t\t\t\t"high":   vehicle.LevelHigh,
\t\t\t}
\t\t\tlevel, ok := levels[args["LEVEL"]]
\t\t\tif !ok {
\t\t\t\treturn fmt.Errorf("seat cooling level must be off, low, medium, or high")
\t\t\t}
\t\t\treturn car.SetSeatCooler(ctx, level, position)
\t\t},
\t},
\t"bioweapon-mode": {
\t\thelp:             "Set bioweapon defense mode to STATE ('on' or 'off').",
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
\t\t\t\treturn fmt.Errorf("bioweapon defense mode must be 'on' or 'off'")
\t\t\t}
\t\t\treturn car.SetBioweaponDefenseMode(ctx, state, true)
\t\t},
\t},
"""


def ensure_phase8_commands(path: Path) -> bool:
    """Insert the subcommands when they are missing. Return True when the file changes."""
    text = path.read_text(encoding="utf-8")
    present = [marker for marker in MARKERS if marker in text]
    if len(present) == len(MARKERS):
        return False
    if present:
        raise SystemExit("tesla-control commands.go has only some of the phase 8 commands, so none were added")
    if ANCHOR not in text:
        raise SystemExit("tesla-control commands.go has no product-info command, so the phase 8 commands were not added")
    path.write_text(text.replace(ANCHOR, SNIPPET + ANCHOR, 1), encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: patch_tesla_control_phase8.py path/to/commands.go", file=sys.stderr)
        return 2
    target = Path(argv[1])
    if not target.is_file():
        print("tesla-control commands.go was not found", file=sys.stderr)
        return 1
    changed = ensure_phase8_commands(target)
    print("phase 8 commands added" if changed else "phase 8 commands already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
