#!/usr/bin/env python3
"""Add a signed external-speaker command to a local tesla-control build.

Upstream vehicle-command does not register boombox. ``pkg/proxy`` returns
"command not implemented" for ``remote_boombox``, and ``car_server.proto``
has no boombox arm on ``VehicleAction``. The car still accepts the signed
infotainment message used by other command-protocol clients: VehicleAction
field 64 (BoomboxAction) with a uint32 sound in field 1. Sound 0 is a random
fart. Sound 2000 is the locate ping. The sound plays once and stops.

This writes ``pkg/vehicle/boombox.go`` and inserts a ``boombox`` subcommand.
It uses the existing signed ``Vehicle.Send`` path. It does not add a second
signer and it does not call the Fleet REST command.
"""

from __future__ import annotations

import sys
from pathlib import Path

ANCHOR = '\t"product-info": {'
MARKER = "car.Boombox(ctx, uint32(sound))"

SNIPPET = """\t"boombox": {
\t\thelp:             "Play one external-speaker sound and stop. SOUND is a sound id (0 fart, 2000 locate ping).",
\t\trequiresAuth:     true,
\t\trequiresFleetAPI: false,
\t\targs: []Argument{
\t\t\t{name: "SOUND", help: "sound id, such as 0 or 2000"},
\t\t},
\t\thandler: func(ctx context.Context, _ *account.Account, car *vehicle.Vehicle, args map[string]string) error {
\t\t\tsound, err := strconv.ParseUint(args["SOUND"], 10, 32)
\t\t\tif err != nil {
\t\t\t\treturn fmt.Errorf("boombox sound must be a whole number")
\t\t\t}
\t\t\treturn car.Boombox(ctx, uint32(sound))
\t\t},
\t},
"""

BOOMBOX_GO = r'''package vehicle

import (
	"context"
	"fmt"

	"google.golang.org/protobuf/encoding/protowire"
	"google.golang.org/protobuf/proto"

	"github.com/teslamotors/vehicle-command/pkg/protocol"
	carserver "github.com/teslamotors/vehicle-command/pkg/protocol/protobuf/carserver"
	universal "github.com/teslamotors/vehicle-command/pkg/protocol/protobuf/universalmessage"
)

// Boombox plays one external-speaker sound and stops.
//
// Upstream car_server.proto omits this action, and the proxy reports it as
// not implemented. The signed payload is VehicleAction field 64 (BoomboxAction)
// with uint32 sound in field 1, sent on the infotainment domain. 0 is a random
// fart. 2000 is the locate ping.
func (v *Vehicle) Boombox(ctx context.Context, sound uint32) error {
	responsePayload, err := v.Send(ctx, universal.Domain_DOMAIN_INFOTAINMENT, boomboxPayload(sound), v.authMethod)
	if err != nil {
		return err
	}
	var response carserver.Response
	if err := proto.Unmarshal(responsePayload, &response); err != nil {
		return &protocol.CommandError{Err: fmt.Errorf("unable to parse vehicle response: %w", err), PossibleSuccess: true, PossibleTemporary: false}
	}
	if response.GetActionStatus().GetResult() == carserver.OperationStatus_E_OPERATIONSTATUS_ERROR {
		description := response.GetActionStatus().GetResultReason().GetPlainText()
		if description == "" {
			description = "unspecified error"
		}
		return &protocol.NominalError{Details: protocol.NewError("car could not execute command: "+description, false, false)}
	}
	return nil
}

func boomboxPayload(sound uint32) []byte {
	inner := protowire.AppendVarint(protowire.AppendTag(nil, 1, protowire.VarintType), uint64(sound))
	vehicleAction := protowire.AppendBytes(protowire.AppendTag(nil, 64, protowire.BytesType), inner)
	return protowire.AppendBytes(protowire.AppendTag(nil, 2, protowire.BytesType), vehicleAction)
}
'''

BOOMBOX_TEST_GO = r'''package vehicle

import (
	"bytes"
	"testing"
)

func TestBoomboxPayloadFartAndLocate(t *testing.T) {
	fart := []byte{0x12, 0x05, 0x82, 0x04, 0x02, 0x08, 0x00}
	if got := boomboxPayload(0); !bytes.Equal(got, fart) {
		t.Fatalf("fart payload %x", got)
	}
	locate := []byte{0x12, 0x06, 0x82, 0x04, 0x03, 0x08, 0xd0, 0x0f}
	if got := boomboxPayload(2000); !bytes.Equal(got, locate) {
		t.Fatalf("locate payload %x", got)
	}
}
'''


def ensure_boombox_command(commands_path: Path) -> bool:
    """Insert the subcommand and vehicle helper. Return True when something changes."""
    commands_path = Path(commands_path)
    repo = commands_path.resolve().parents[2]
    vehicle_dir = repo / "pkg" / "vehicle"
    if not vehicle_dir.is_dir():
        raise SystemExit("tesla-control vehicle package was not found, so boombox was not added")
    changed = _ensure_commands(commands_path)
    changed = _ensure_file(vehicle_dir / "boombox.go", BOOMBOX_GO) or changed
    changed = _ensure_file(vehicle_dir / "boombox_test.go", BOOMBOX_TEST_GO) or changed
    return changed


def _ensure_commands(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return False
    if ANCHOR not in text:
        raise SystemExit("tesla-control commands.go has no product-info command, so boombox was not added")
    path.write_text(text.replace(ANCHOR, SNIPPET + ANCHOR, 1), encoding="utf-8")
    return True


def _ensure_file(path: Path, contents: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == contents:
        return False
    path.write_text(contents, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: patch_tesla_control_boombox.py path/to/commands.go", file=sys.stderr)
        return 2
    target = Path(argv[1])
    if not target.is_file():
        print("tesla-control commands.go was not found", file=sys.stderr)
        return 1
    changed = ensure_boombox_command(target)
    print("boombox added" if changed else "boombox already present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
