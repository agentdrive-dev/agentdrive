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
| `charge-schedule show` | read `charge_schedule_data` | no, and it does not wake |
| `charge-schedule set <days> <time> <latitude> <longitude>` | signed `charging-schedule-add` | yes, one time |
| `charge-schedule remove <id>` | signed `charging-schedule-remove` | yes, one time |

This version has no trunk, frunk, or remote-start command. Unlock and every charging change go through the existing confirmation layer. A schedule cannot confirm them. State-changing commands are sent only in Park at speed 0, then read back once.

## In-car charging timetable

`charge-schedule` reads and changes the timetable stored on the car. It is not a reminder on this computer, and it does not call `charge-start` later.

```bash
agentdrive charge-schedule show
agentdrive charge-schedule show --json
agentdrive charge-schedule set weekdays 22:00-06:00 1 2 --confirm "I confirm charging schedule set"
agentdrive charge-schedule set all 23:30- --once --confirm "I confirm charging schedule set"
agentdrive charge-schedule set Mon,Wed -06:00 1 2 --enabled false --confirm "I confirm charging schedule set"
agentdrive charge-schedule remove 1700000000 --confirm "I confirm charging schedule remove"
```

`show` calls `vehicle_data` with the endpoint `charge_schedule_data`. If the vehicle list says the car is asleep, offline, or otherwise not online, the command skips, prints that it skipped, and exits 0. It does not send a wake. `--json` prints `state`, `skipped`, and `schedules`.

`set` and `remove` are charging changes. Each one needs a confirmation sentence that includes `confirm` and the action name (`charging schedule set` or `charging schedule remove`). An assistant schedule cannot supply that sentence. If the vehicle list says the car is not online, these two commands wake it, the same way the other parked commands do. The car must then be in Park at speed 0. The command is sent once, then the timetable is read again.

| Argument | Value |
| --- | --- |
| `days` | `all`, `weekdays`, or a comma-separated list of `Sun`, `Mon`, `Tues`, `Wed`, `Thurs`, `Fri`, `Sat`. `Sunday`, `Monday`, `Tuesday`, `Wednesday`, `Thursday`, `Friday`, and `Saturday` are accepted. |
| `time` | 24-hour local time. `22:00-06:00` sets a start and an end. `22:00-` sets a start. `-06:00` sets an end. These are minutes after midnight. The end may fall on the next day. |
| `latitude` `longitude` | Charging-site coordinates in decimal degrees, from -180 to 180. The signed command requires both. |
| `--once` | The row runs once. Omit it and the row repeats every week. |
| `--enabled` | `true` (the default) or `false`. `false` adds the row turned off. |
| `ID` | The id printed by `show`. `remove` deletes that row. |

`show` prints each row's id, enabled flag, days, start, end, weekly or once, charging-site coordinates, and name when the response includes one. The read asks only for `charge_schedule_data`. It does not ask for `location_data`.

`set` signs `charging-schedule-add`, which is `add_charge_schedule`. `remove` signs `charging-schedule-remove` with type `id`, which is `remove_charge_schedule`. Tesla's command reference says `set_scheduled_charging` is not recommended beginning with firmware version 2024.26, and that `add_charge_schedule` should be used instead. The older command takes one `time` in minutes after midnight. This command family sends `add_charge_schedule`, which carries the days, a start, an end, or both, the charging-site coordinates, `one_time`, and `enabled`.

`set` adds a row. In the current `tesla-control` `charging-schedule-add` command, the optional id argument is not copied into the signed message. The tool stores the current unix time as the id. There is no update-by-id flag here. `--enabled false` adds a disabled row. It leaves rows that are already on the car as they are. `remove` is how you drop an id. One `set` is one window. Different hours on different days are separate schedules. Preconditioning and scheduled departure are separate commands and are not part of `charge-schedule`.

The sample coordinates and the sample id above are placeholders.

Signed commands run `tesla-control` with `TESLA_TOKEN_FILE`, `TESLA_KEY_FILE`, and `TESLA_VIN` set in the environment. Those names are what the tool requires. The access token is written to a mode 0600 file and deleted in a `finally` block. It is never placed on the argument list. The tool is invoked once. If it fails, it is not launched again until a read shows whether the car already changed.

`agentdrive update` fast-forwards the checkout from `main`, or downloads that branch when the tree is not a git checkout, then re-runs the install steps. It does not modify tokens, keys, client credentials, `host.yaml`, or vehicle files under `$AGENTDRIVE_HOME`.

```bash
agentdrive check
agentdrive update
```

The command line does not include charging history, charging invoices, a telemetry stream, or a where command. `agentdrive status` does not request location. The sessions route is refused. There is no live Supercharger pricing.

## Expected result

A status question returns a short readout with tyre pressures, no coordinates, and a masked VIN. A parked lights flash runs once and is followed by another read. A sleeping car makes `agentdrive check` exit 0 without a wake.
