# Queries and commands

[Back to the checklist](00-before-you-start.md)

![Park check, one-time confirmation for sensitive actions, then one send and a fresh read](images/command-safety.svg)

Reads use the Fleet API: the vehicle list, `vehicle_data`, and wake. Location on firmware 2023.38 and later needs the `location_data` endpoint and the `vehicle_location` scope. `status` and `check` do not request that endpoint. `where` and `where --precise` request it only when the vehicle list already says the car is online. Precise coordinates are used only when you ask for them (`where --precise`, or a departure place taken from the car) and are not written to the local history.

`agentdrive status` reads charge, climate, and vehicle state, including tyre pressures and TPMS warnings. If the vehicle list says the car is asleep, status sends one wake and then polls for up to 45 seconds. The VIN is masked. Tokens are not printed.

`agentdrive cars` lists the cars on the account from the vehicle list. It does not wake a car. Each line shows the nickname, the name on the account, a masked VIN tail, and whether the car is the default. `agentdrive cars name <tail> <nickname>` saves a nickname you choose. The tail is the last four characters from that list. `agentdrive cars default <nickname>` sets the car used when a command does not pass `--car`. `agentdrive cars clear-default` clears it. With one car, leave the nickname unset and the commands stay as they are. With several cars and no default, a command does not guess: pass `--car <nickname>` or set a default. `AGENTDRIVE_VIN` still selects a car when that car already has a nickname. The full VIN is not printed.

Health checks, alert memory, the morning briefing, and the encrypted history are kept per car. The first car you name keeps the history and alert file that were already in the config home. Another car gets its own files under the config home. An alert that is already active on one car is still new on another car. `agentdrive history`, `agentdrive tips`, and `agentdrive service` read the selected car and do not contact it. With several cars and no default, pass `--car`. The morning job runs `agentdrive history due` once per car when you have several. The health check runs `agentdrive check --json --car <nickname>` once per car. With one car, both commands stay as they are.

`agentdrive check` is the read-only health check for a schedule. It reads the vehicle list first and does not wake a sleeping car. If the car is not online, the command skips, prints that it skipped, and exits 0. That skip does not change which alerts are remembered. If the car is online it looks for a TPMS warning, a tyre below the configured bar threshold, a low battery that is not charging, sentry off while that same low battery is not charging, the car unlocked or open while parked, or a charge error. It exits 1 only when an alert is new. The printed alerts are those new ones. The same alert exits 0, and is not printed again, until it clears and later returns. A different new alert still exits 1 while an older one continues. `--json` prints a short object for the AI agent. On install, schedule `agentdrive check --json` every 30 minutes and notify only when the exit code is 1. AgentDrive does not run that job and does not install cron. The morning usage briefing stays the separate daily job at the report-schedule time. Thresholds come from `--tyre-threshold`, `--battery-threshold`, or the `tyre_pressure_bar` and `low_battery_percent` habits in the profile. The default tyre line is 2.5 bar and the default battery line is 30 percent. Sentry off uses that same battery line. The check does not request location. Remembered alert codes are stored in `$AGENTDRIVE_HOME/check-alerts.json` (or `~/.config/agentdrive/check-alerts.json`). That file is alert codes only. Do not copy it into the git repo.

`agentdrive where` is a read-only coarse area. It reads the vehicle list first. If the car is asleep or offline, it skips with the same kind of message as `check`, does not wake the car, and exits 0. If the car is already online, it calls `vehicle_data` with `endpoints=location_data` only.

Before that point leaves the process, latitude and longitude are rounded to two decimal places, about 1 km. The command sends that rounded point to OpenStreetMap Nominatim for one reverse lookup at zoom 14, with the `AgentDrive/<version>` User-Agent. It does not retry, and it does not send the precise point. Nominatim's usage policy allows this kind of light use and asks callers not to bulk-geocode. Place names are OpenStreetMap data.

The default command prints a suburb or locality, a state or region, and a country. The text labels are `suburb`, `region`, and `country`. `suburb` is the suburb when Nominatim has one, and otherwise a neighbourhood, locality, village, town, or city. `--json` prints those three fields. That default text, JSON, and the errors do not contain coordinates. If the lookup fails, the command prints `area unavailable` and exits 1.

`agentdrive where --precise` prints the precise latitude and longitude and an OpenStreetMap link when the car is already online. It uses the same non-waking read. It does not call Nominatim. If the car is asleep or offline, it skips and does not wake the car. `--json` prints `latitude`, `longitude`, and `map`.

```bash
agentdrive where
agentdrive where --precise
agentdrive where --precise --json
```

A location read needs the official `vehicle_location` scope. The owner sign-in scope list is `openid`, `offline_access`, `user_data`, `vehicle_device_data`, `vehicle_location`, and `vehicle_cmds`. If the token lacks `vehicle_location`, or the firmware refuses the read until that scope is granted, the command tells you to re-authorize and include `vehicle_location`, and it exits 1. Sign in again so the new token carries that scope. `status` and `check` stay free of location.

`agentdrive report` is the daily briefing. It uses that same parked snapshot (`charge_state`, `climate_state`, `vehicle_state`, and `drive_state`). It does not request `location_data` and it does not print coordinates. It does not call wake. If the vehicle list says the car is asleep, offline, or otherwise not online, it prints the same skip line as `check` and exits 0. A timeout or HTTP 408 is the same skip. When the car is online the briefing includes battery, charge connection, lock, gear, doors, windows, trunk, sentry, climate, tyres, and TPMS. `--json` prints the same briefing as an object. The AI agent sends that output every time. Exit 0 here is not a reason to stay quiet.

`agentdrive report-schedule` shows the daily time. The default is 07:30 local time and enabled, even when `runtime.yaml` has no `report_schedule` key. `agentdrive report-schedule set HH:MM` stores a 24-hour time. `disable` and `enable` store the on/off state. The file is `$AGENTDRIVE_HOME/runtime.yaml` (or `~/.config/agentdrive/runtime.yaml`). Writing it does not change the AI agent's job. After a change, the AI agent updates the job. The command also prints a crontab example, such as `30 7 * * * agentdrive history due`. AgentDrive does not install that line and does not install a systemd timer.

`agentdrive history` reads the encrypted history file in the config home. `history due` is the morning usage briefing. On 1 January it covers the previous calendar year. On any other 1st it covers the previous calendar month. On Monday it covers the previous seven days, Monday through Sunday. Every other morning it covers the previous local day. Only one of those is sent. `history daily`, `weekly`, `monthly`, and `yearly` print that period when you ask. `history trips`, `history charges`, and `history snapshots` list the saved rows. `--json` prints the same facts as an object. These commands do not call the car.

A weekly, monthly, or yearly briefing adds a summary: kilometres driven, number of trips, average kilometres per day, number of charges, total kWh added, average per charge, and a comparison with the previous period. It says when coverage is incomplete. History is saved only while the car was already online, so it is opportunistic, not a continuous record. The briefing writes a PNG chart under the car's directory in `$AGENTDRIVE_HOME` and prints that path. It also writes `briefings/dashboard.html`, a single file with week, month, and year views that opens offline. If matplotlib is installed it draws the PNG. Otherwise AgentDrive draws the PNG itself. No extra package is required. Attach the PNG when you send the briefing. Do not upload the history database.

`agentdrive advice trip <km>` says whether the battery covers that distance. It uses a non-waking reading when the car is already online, and otherwise the last saved battery. It does not wake the car. Consumption is the battery percent per kilometre from the car's own saved trips. Fewer than three usable trips is reported as too little data, and no guess is filled in. A cold or hot saved temperature adjusts the estimate. The same command suggests an everyday charge limit, usually 80%, and a higher limit before a long trip. It does not change the limit. When a departure schedule is set and the reading is cold or hot, it suggests preconditioning and does not start it.

`agentdrive advice charge` flags three habits when the saved charges show them: often charging to 100% and leaving it, often starting a charge very low, and often charging in a peak window you saved. `agentdrive tariff set off-peak 22:00-06:00` and `agentdrive tariff set peak 16:00-21:00` store those hours in `$AGENTDRIVE_HOME/tariff.yaml`. `--price` is optional, per kWh. The advice can suggest moving the in-car timetable into the cheap window. It does not change the timetable. Any change still needs the usual confirmation.

With more than one saved car, pass `--car <nickname>`. Advice, the chart, and the summary use that car's history.

A parked snapshot is saved when `check` or `report` already has a reading and the car is online. A check that finds nothing new to notify still saves that snapshot. Repeated checks that find the same parked condition update that snapshot's last time, sample count, battery, and tyre readings. A new snapshot starts when the car leaves Park, charging changes, the lock, sentry, doors, windows, or trunk change, a new alert appears, or the odometer moves. Charge readings are kept with the charge session, about once a minute when a reading was seen. The database is encrypted with AES-256-GCM. The key is `$AGENTDRIVE_HOME/history.key`, created on first save, mode 600 on macOS and Linux. The Python dependency is `cryptography`. SQLCipher is not required. The key is not in the git repo and must not be pasted into chat.

What is saved: trip summaries (time, distance from the odometer change, battery, lock, gear, and sentry), charge sessions (time, start and end percent, energy added during that plug-in, and whether it was plugged in), those charge readings, parked snapshots, alerts when they appear, and software or unit settings when they change. Energy added is the increase during the plug-in. A counter that still holds the previous session, or that keeps climbing across plugs, is not stored as this session's total. Older rows are left as they were. A summary ignores a saved energy figure that cannot match the battery change. A service you mention, such as a tyre rotation, is stored against the last saved odometer. How full the charge curve looks depends on how often the usual check runs while the car is already online or charging. AgentDrive does not run in the background to fill the gaps.

`agentdrive news` prints one new official item from `https://www.tesla.com` for the profile country, or for the computer's timezone when the profile has no country. The Monday weekly briefing includes that same item: a short summary and the official link. A safety notice is preferred. The same item is not shown again for two calendar months. When there is nothing new, the command prints nothing and the briefing leaves that part out. It does not wake the car.

`agentdrive tips` says whether one short tip is included this morning. Tips are shown on Monday, Wednesday, Friday, and the 1st. The same tip is not shown again for two calendar months. A usage tip is not a stand-in for an empty official check. `agentdrive service` shows kilometres since a logged tyre rotation, tyre replacement, or other service. `service log rotation`, `service log replacement`, and `service log maintenance` record that event against the latest saved odometer. Tyre service uses 10,000 km (6,250 miles) unless you set `--interval-km`. Replacing tyres depends on tread. A `--note` can name the service, such as a cabin filter. A due reminder is added to the morning briefing, and that same reminder waits two calendar months before it is sent again. `tips`, `news`, and `service` do not wake the car.

Commands:

| Command | What it sends | Confirmation |
| --- | --- | --- |
| `where` | no signed command; one `location_data` read when the car is already online | no |
| `where --precise` | no signed command; the same non-waking `location_data` read, then latitude, longitude, and a map link | no |
| `wake` | signed `wake` | no, and it does not use the park check |
| `flash` | signed `flash-lights` | no |
| `honk` | signed `honk` | no |
| `lock` | signed `lock` | no |
| `unlock` | signed `unlock` | yes, one time |
| `climate-on`, `climate-off` | signed climate command | no |
| `set-temp <C>` | signed `climate-set-temp` | no, and only 15 to 28 C |
| `sentry-on` | signed `sentry-mode on` | no |
| `sentry-off` | signed `sentry-mode off` | yes, one time |
| `seat-heat <seat> <level>` | signed `seat-heater` | no |
| `steering-heat on` or `off` | signed `steering-wheel-heater` | no |
| `defrost-on`, `defrost-off` | signed `preconditioning-max` | no |
| `precondition` | signed `climate-on` | no |
| `departure show` | read `preconditioning_schedule_data` | no, and it does not wake |
| `departure set <days> <time> [latitude longitude]` | signed `precondition-schedule-add` | yes, one time |
| `departure remove <id>` | signed `precondition-schedule-remove` | yes, one time |
| `charge-start`, `charge-stop` | signed charging command | yes, one time |
| `charge-limit <percent>` | signed `charging-set-limit` | yes, one time, and only 50 to 100 |
| `charge-amps <amps>` | signed `charging-set-amps` | yes, one time, and only 1 to 80 |
| `charge-schedule show` | read `charge_schedule_data` | no, and it does not wake |
| `charge-schedule set <days> <time> <latitude> <longitude>` | signed `charging-schedule-add` | yes, one time |
| `charge-schedule remove <id>` | signed `charging-schedule-remove` | yes, one time |
| `trunk-open` | signed `trunk-open` | yes, one time. The sentence names open rear trunk |
| `trunk-close` | signed `trunk-close` | yes, one time. The sentence names close rear trunk |
| `frunk-open` | signed `frunk-open` | yes, one time. The sentence names open frunk |
| `remote-start` | signed `drive` | yes, one time. The sentence names enable remote start |
| `history trips`, `history charges`, `history snapshots` | no signed command; saved history only | no, and it does not contact the car |
| `history due`, `history daily`, `history weekly`, `history monthly`, `history yearly` | no signed command; saved history only. Week, month, and year also write a local chart | no |
| `advice trip <km>`, `advice charge` | no signed command; saved history, plus a non-waking read when the car is already online | no, and it does not wake the car or change a setting |
| `tariff show`, `tariff set`, `tariff clear` | no signed command; writes `$AGENTDRIVE_HOME/tariff.yaml` | no, and it does not change the car |
| `tips` | no signed command | no, and it does not contact the car |
| `news` | no signed command; reads the official Tesla site for the owner's country | no, and it does not wake the car |
| `service`, `service log rotation`, `service log replacement`, `service log maintenance` | no signed command; uses the saved odometer | no, and it does not wake the car |

Unlock, turning sentry off, every charging change, setting or removing a departure schedule, the rear trunk, the front trunk, and remote start go through the existing confirmation layer. A schedule cannot confirm them and cannot run sentry off, the trunk, the front trunk, remote start, or a departure schedule change. When a nickname is in use for that command, the confirmation also names the car. `I confirm unlock on daily` approves unlock for the car named daily. `I confirm unlock` does not. With one car and no `--car`, the sentence stays `I confirm unlock`. The prompt is `Confirm unlock on daily. Type a sentence that names the action and the car and includes confirm:`. The result line names the car too: `OK: unlock on daily`. The health check stays `agentdrive check`. The morning job stays `agentdrive history due`. State-changing commands are sent only in Park at speed 0, then read back once.

## Several cars

```bash
agentdrive cars
agentdrive cars name 0001 daily
agentdrive cars name 0002 weekend
agentdrive cars default daily
agentdrive --car weekend lock
agentdrive unlock --car weekend --confirm "I confirm unlock on weekend"
```

`0001` and `0002` stand in for the last four characters printed by `cars`. The nickname is yours. `daily` and `weekend` are examples.

## Family members

You can register a handle for someone in the household and choose what they may ask for. Low-risk reads and parked actions that you allow run directly. Unlock, charging changes, turning sentry off, the trunk, the front trunk, remote start, and departure changes do not. Those become a pending request. The command prints text for the assistant to relay to you. AgentDrive does not send that message itself.

```bash
agentdrive family add rider
agentdrive family allow rider lock
agentdrive family allow rider status
agentdrive family allow rider unlock
agentdrive family allow rider charging
agentdrive family list
```

`charging` covers start, stop, the charge limit, the charging current, and the in-car timetable changes. A family member's assistant runs the command with `--as`:

```bash
agentdrive --as rider --car daily unlock
```

That prints a pending request and does not unlock the car, even if a confirmation sentence is passed. You approve or deny it:

```bash
agentdrive request list
agentdrive request approve ab12cd34
agentdrive request deny ab12cd34
```

The id is the short id in the relay text. A request lasts 10 minutes. Approve runs it once, and the car must still be in Park at speed 0. A second approve, a deny, an expired request, or an approve from the same family member does not run it. Only you can approve or deny. The relay text looks like this:

```text
Pending request ab12cd34 from rider on daily: unlock.
Created 2026-11-14T01:00:00Z. Expires 2026-11-14T01:10:00Z (10 minutes).
This action was not run.
Tell the owner: rider asked to unlock on daily. Approve with `agentdrive request approve ab12cd34` or deny with `agentdrive request deny ab12cd34`.
```

## Sentry and cabin climate

`sentry-on` and `sentry-off` wake a sleeping car. If the vehicle list says the car is not online, they send one wake and poll for up to 45 seconds, then send the signed `sentry-mode` command. `check`, `report`, `where`, and history do not wake a sleeping car. Seat heat, steering-wheel heat, defrost, and precondition wake a sleeping car the same way the other parked commands do.

`sentry-off` needs a confirmation sentence that includes `confirm` and the action, for example `I confirm turn sentry off`. A scheduled job cannot supply that sentence and cannot run `sentry-off`. `sentry-on` does not add a confirmation.

```bash
agentdrive sentry-on
agentdrive sentry-off --confirm "I confirm turn sentry off"
agentdrive seat-heat front-left medium
agentdrive steering-heat on
agentdrive defrost-on
agentdrive defrost-off
agentdrive precondition
```

`seat-heat` sends `seat-heater`. The seat is `front-left`, `front-right`, `2nd-row-left`, `2nd-row-center`, `2nd-row-right`, `3rd-row-left`, or `3rd-row-right`. The level is `off`, `low`, `medium`, or `high`. A backrest heater, a center seat the tool does not name, and a numeric level are refused before the command is sent.

`steering-heat` sends `steering-wheel-heater` with `on` or `off`. A heat level is refused, because that signed command has no level.

`defrost-on` and `defrost-off` send `preconditioning-max`. That is max defrost for the front and rear together. There is no separate front-only or rear-only command, and the manual override is not sent. Upstream `tesla-control` does not include that subcommand, so the installer adds it to the local build. `agentdrive update` rebuilds that binary.

`precondition` sends `climate-on`, which starts cabin preconditioning now. It does not add a precondition schedule or a scheduled departure. A later departure time is `agentdrive departure`. `climate-off` stops the cabin climate.

## Rear trunk, front trunk, and remote start

```bash
agentdrive trunk-open --confirm "I confirm open rear trunk"
agentdrive trunk-close --confirm "I confirm close rear trunk"
agentdrive frunk-open --confirm "I confirm open frunk"
agentdrive remote-start --confirm "I confirm enable remote start"
```

`trunk-open` opens the rear trunk. `trunk-close` closes it when the vehicle can close the rear trunk electrically. If the car cannot, the signed command fails once and is not sent again. After a successful open, the follow-up read must show the rear trunk open. After a successful close, it must show the rear trunk closed.

`frunk-open` opens the front trunk. The front trunk cannot be closed from AgentDrive. The signed command tool has no close for it.

`remote-start` enables keyless driving for a short time, so the car can be driven without the phone key. It does not drive the car, it does not start an engine, and it does not ask for an account password. The signed command is `drive`. The follow-up read checks that the car is still in Park at speed 0.

These four commands may wake a sleeping car, the same way lock, climate, and charging commands do. `agentdrive check`, `agentdrive report`, `agentdrive where`, and `agentdrive history` still never wake a sleeping car.

## Charging current

`charge-amps` sets the charging current. It is a charging change.

```bash
agentdrive charge-amps 16 --confirm "I confirm charging set amps"
```

The confirmation sentence has to include `confirm` and `charging set amps`. A scheduled job cannot supply that sentence and cannot run `charge-amps`. The car must be in Park at speed 0. If the vehicle list says the car is not online, this command wakes it, the same way the other parked commands do. The signed command is `charging-set-amps`, which is `set_charging_amps`. Upstream `tesla-control` already has that subcommand. The installer does not patch it.

The amps value is a whole number from 1 to 80. 80 is the highest AC current a Tesla has used. `tesla-control` parses the number and does not apply a range of its own. The car may use less, down to what that charger and the car allow. The follow-up read uses `charge_current_request` when the charge state includes it, and otherwise `charge_amps`. If that value equals `charge_current_request_max` and the max is lower than the request, the command is kept and the output says the car clamped it. The command is not sent again.

## Departure preconditioning

`departure` reads and changes a time when the cabin should be ready. It is not `precondition`, and it is not the in-car charging timetable.

```bash
agentdrive departure show
agentdrive departure show --json
agentdrive departure set weekdays 07:30 --confirm "I confirm departure schedule set"
agentdrive departure set weekdays 07:30 1 2 --confirm "I confirm departure schedule set"
agentdrive departure set all 08:00 1 2 --once --confirm "I confirm departure schedule set"
agentdrive departure set Mon,Wed 07:30 1 2 --id 1700000000 --confirm "I confirm departure schedule set"
agentdrive departure remove 1700000000 --confirm "I confirm departure schedule remove"
```

`show` calls `vehicle_data` with the endpoint `preconditioning_schedule_data`. If the vehicle list says the car is asleep, offline, or otherwise not online, the command skips, prints that it skipped, and exits 0. It does not send a wake. `--json` prints `state`, `skipped`, and `schedules`.

`set` and `remove` need a confirmation sentence that includes `confirm` and the action name (`departure schedule set` or `departure schedule remove`). A scheduled job cannot supply that sentence and cannot run those two commands. If the vehicle list says the car is not online, these two commands wake it. The car must then be in Park at speed 0. The command is sent once, then the schedules are read again.

| Argument | Value |
| --- | --- |
| `days` | `all`, `weekdays`, or a comma-separated list of `Sun`, `Mon`, `Tues`, `Wed`, `Thurs`, `Fri`, `Sat`. Full day names are accepted. |
| `time` | 24-hour local time, `HH:MM`. This is when the cabin should be ready, as minutes after midnight. It is not a start-and-end window. |
| `latitude` `longitude` | Optional. Decimal degrees. Latitude is from -90 to 90 and longitude is from -180 to 180. Pass both, or omit both. Omit both and the command reads the car's current location with `location_data` after the same wake other parked changes use. If that read says the car is asleep, it is not woken again. The command then says the car must be online, or you can pass coordinates. The schedule applies at that place. |
| `--once` | The row runs once. Omit it and the row repeats every week. |
| `--enabled` | `true` (the default) or `false`. |
| `--id` | Change the row with this id. Omit it to add a row. |
| `ID` | The id printed by `show`. `remove` deletes that row. |

`show` prints each row's id, enabled flag, days, ready-by time, weekly or once, place, and name when the response includes one. The read asks only for `preconditioning_schedule_data`. It does not ask for `location_data`.

`set` signs `precondition-schedule-add`, which is `add_precondition_schedule`. `remove` signs `precondition-schedule-remove` with type `id`, which is `remove_precondition_schedule`. The signed schedule fields are the id, days, precondition time, one-time flag, enabled flag, and the place. There is no battery switch and no off-peak charging time on this message.

Tesla's command reference says `set_scheduled_departure` is not recommended beginning with firmware 2024.26, and that `add_precondition_schedule` should be used instead. The older command carried a departure time, an off-peak end time, and preconditioning and off-peak policies. `tesla-control` does not register that older command. AgentDrive does not send it, and it does not send off-peak charging. `home`, `work`, and `other` batch removal exist on the signed tool and are not used here. `remove` deletes one id.

In `precondition-schedule-add`, the optional arguments are positional: repeat, id, then enabled. Omit the id and the tool stores the current unix time and prints that id. `--id` copies that id into the signed message, which is how an existing row is changed. `--enabled false` has to pass an id because enabled sits after it. If you omit `--id` on a disabled row, AgentDrive fills the current unix time. `weekly` is only a placeholder so those later arguments can be reached. Any repeat value other than `once` leaves the row weekly.

One `set` is one ready-by time. Different times are separate schedules. The sample coordinates and the sample id above are placeholders. Immediate cabin preconditioning stays `agentdrive precondition`.

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

`set` and `remove` are charging changes. Each one needs a confirmation sentence that includes `confirm` and the action name (`charging schedule set` or `charging schedule remove`). A scheduled job cannot supply that sentence. If the vehicle list says the car is not online, these two commands wake it, the same way the other parked commands do. The car must then be in Park at speed 0. The command is sent once, then the timetable is read again.

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

`set` adds a row. In the current `tesla-control` `charging-schedule-add` command, the optional id argument is not copied into the signed message. The tool stores the current unix time as the id. There is no update-by-id flag here. `--enabled false` adds a disabled row. It leaves rows that are already on the car as they are. `remove` is how you drop an id. One `set` is one window. Different hours on different days are separate schedules. Departure preconditioning is `agentdrive departure`. Immediate cabin preconditioning is `agentdrive precondition`. Neither is part of `charge-schedule`.

The sample coordinates and the sample id above are placeholders.

Signed commands run `tesla-control` with `TESLA_TOKEN_FILE`, `TESLA_KEY_FILE`, and `TESLA_VIN` set in the environment. Those names are what the tool requires. The access token is written to a mode 0600 file and deleted in a `finally` block. It is never placed on the argument list. The tool is invoked once. If it fails, it is not launched again until a read shows whether the car already changed.

`agentdrive update` fast-forwards the checkout from `main`, or downloads that branch when the tree is not a git checkout, then re-runs the install steps. It does not modify tokens, keys, client credentials, `host.yaml`, or vehicle files under `$AGENTDRIVE_HOME`.

```bash
agentdrive check
agentdrive report
agentdrive report-schedule
agentdrive report-schedule set 07:30
agentdrive report-schedule disable
agentdrive report-schedule enable
agentdrive where
agentdrive charge-schedule show
agentdrive charge-amps 16 --confirm "I confirm charging set amps"
agentdrive departure show
agentdrive update
```

`agentdrive status` does not request location. The client refuses the business-only charging sessions route. Nearby charging reads keep location and stall counts.

## Expected result

A status question returns a short readout with tyre pressures and a masked VIN. `status` and `check` do not request location. `agentdrive where` returns a suburb, region, and country, or says that it skipped when the car is asleep. `agentdrive where --precise` prints latitude, longitude, and a map link when the car is already online, and it still skips without a wake when the car is asleep. A parked lights flash runs once and is followed by another read. A sleeping car makes `agentdrive check`, `agentdrive report`, `agentdrive where`, `agentdrive where --precise`, `agentdrive charge-schedule show`, and `agentdrive departure show` exit 0 without a wake. The AI agent still sends the report, including that skip line.

Illustrative sample. These figures are sample data, not a vehicle reading:

```text
Vehicle report: Example
Battery: 80% | Range: 161 km | Limit: 80%
Charge connection: Disconnected
Locked: yes
Gear: P (parked)
Doors, windows, and trunk: closed
Sentry: on
Climate: off (set 21.0°C)
Inside: 20.0°C | Outside: 15.0°C
Tyres (bar): FL 2.9 FR 2.9 RL 2.9 RR 2.9
TPMS: ok
```
