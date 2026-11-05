# Queries and commands

[Back to the checklist](00-before-you-start.md)

![Park check, one-time confirmation for sensitive actions, then one send and a fresh read](images/command-safety.svg)

Reads use the Fleet API: the vehicle list, `vehicle_data`, and wake. Location on firmware 2023.38 and later needs the `location_data` endpoint. Coordinates from that read are not copied into assistant memory, and `status` does not request that endpoint.

`agentdrive status` reads charge, climate, and vehicle state, including tyre pressures and TPMS warnings. If the vehicle list says the car is asleep, status sends one wake and then polls for up to 45 seconds. The VIN is masked. Tokens are not printed. When the account has more than one vehicle, set `AGENTDRIVE_VIN` to choose one. Otherwise the first vehicle on the account is used.

`agentdrive check` is the read-only health check for a schedule. It reads the vehicle list first and does not wake a sleeping car. If the car is not online, the command skips, prints that it skipped, and exits 0. If the car is online it reports actionable alerts and exits 1: a TPMS warning, a tyre below the configured bar threshold, a low battery that is not charging, the car unlocked or open while parked, or a charge error. `--json` prints a short object for a bot. Thresholds come from `--tyre-threshold`, `--battery-threshold`, or the `tyre_pressure_bar` and `low_battery_percent` habits in the profile. The default tyre line is 2.5 bar and the default battery line is 30 percent.

Commands:

| Command | What it sends | Confirmation |
| --- | --- | --- |
| `wake` | signed `wake` | no |
| `flash` | signed `flash-lights` | no |
| `honk` | signed `honk` | no |
| `lock` | signed `lock` | no |
| `unlock` | signed `unlock` | yes, one time |
| `climate-on`, `climate-off` | signed climate command | no |
| `set-temp <C>` | signed `climate-set-temp` | no, and only 15 to 28 C |
| `charge-start`, `charge-stop` | signed charging command | yes, one time |
| `charge-limit <percent>` | signed `charging-set-limit` | yes, one time, and only 50 to 100 |

This version has no trunk, frunk, or remote-start command. Unlock and every charging change go through the existing confirmation layer. A schedule cannot confirm them. State-changing commands are sent only in Park at speed 0, then read back once.

Signed commands run `tesla-control` with `TESLA_TOKEN_FILE`, `TESLA_KEY_FILE`, and `TESLA_VIN` set in the environment. Those names are what the tool requires. The access token is written to a mode 0600 file and deleted in a `finally` block. It is never placed on the argument list. The tool is invoked once. If it fails, it is not launched again until a read shows whether the car already changed.

`agentdrive update` fast-forwards the checkout from `main`, or downloads that branch when the tree is not a git checkout, then re-runs the install steps. It does not modify tokens, keys, client credentials, `host.yaml`, or vehicle files under `$AGENTDRIVE_HOME`.

```bash
agentdrive check
agentdrive update
```

The command line does not include scheduled charging, charging history, charging invoices, a telemetry stream, or a where command. `agentdrive status` does not request location. The sessions route is refused. There is no live Supercharger pricing.

## Expected result

A status question returns a short readout with tyre pressures, no coordinates, and a masked VIN. A parked lights flash runs once and is followed by another read. A sleeping car makes `agentdrive check` exit 0 without a wake.
