---
name: agentdrive
description: Use AgentDrive to read a personal vehicle and send parked commands through the agentdrive program. Use when the user asks about battery, climate, lock state, tyre pressure, a health check, the daily vehicle report, a coarse area, an in-car charging timetable, charging current, a departure time that preconditions the cabin, sentry mode, seat or steering heat, seat cooling, automatic seat climate, defrost, cabin preconditioning, climate keeper, bioweapon defense mode, the charge port, official charging history or an invoice, a software update, the rear trunk, the front trunk, remote start, the external speaker, or a parked command such as flash lights, lock, or charging. Also use it when the user has more than one car, a family member asks for an action the owner must approve, whether the battery is enough for a trip, a charge limit, charging habits, a home tariff, or a weekly, monthly, or yearly chart. Do not sign vehicle commands in this skill.
---

# AgentDrive

AgentDrive is unofficial. It is not affiliated with, endorsed by, or supported by Tesla.

This skill tells a shell-capable AI agent how to install and run AgentDrive. Grok Bot, Muse, and any other AI agent that can run shell commands use the same commands. Memory, schedules, confirmations, and secret files go through the host adapter. Signing stays in the shared core (`core/fleet/commands.py`). Do not call `tesla-control` from this skill, and do not reimplement request signing.

The install prompt is `prompts/install.md` in this folder. The path example is `config/runtime.example.yaml`.

## Before install

Read `DISCLAIMER.md` and `SECURITY.md` in full and show them to the user. Stop. Installation waits for a later message with this sentence:

`I have read, understood, and accept the AgentDrive disclaimer and security reminder`

## Config home

- When `AGENTDRIVE_HOME` is set, that directory is the config home on macOS, Linux, and Windows.
- When it is unset, the default is `~/.config/agentdrive`.
- On Windows the default is `C:\Users\<you>\.config\agentdrive`. `<you>` is a placeholder for the account folder.

The adapter writes `memory.json`, `schedules.json`, and `host.yaml` in the config home. It reads `client-id`, `client-secret`, `vin`, and `redirect-uri` from `secrets/` when a command needs them. It does not store those values in memory.

## Existing key and tokens

Point at files that already exist. A key that is already paired with another agent can be shared.

- `AGENTDRIVE_KEY_FILE`, or `key_file` in `$AGENTDRIVE_HOME/runtime.yaml`. Placeholder: `C:\Users\<you>\agentdrive-key\private-key.pem`.
- `AGENTDRIVE_TOKEN_FILE`, or `token_file` in the same file.

Security trade-off of a shared key: every agent that can read the private key file can sign commands, together with a valid token, inside the approved scopes. Keep one file and limit it to the user. A second copy is a second secret. The token file rotates on refresh, so keep one token file and one writer. Two refreshers can invalidate each other.

## What to run

1. `agentdrive doctor`
2. `agentdrive check` — the read-only vehicle status check. It does not wake a sleeping vehicle and it does not send a command.
3. `agentdrive where` — a coarse area: suburb or locality, state or region, and country. It does not wake a sleeping vehicle and it does not send a command. A rounded point is sent to Nominatim. `agentdrive where --precise` prints precise latitude and longitude and a map link when the car is already online. It still does not wake the car. Precise coordinates are not written to the local history. Do not copy them into memory.
4. A vehicle command only after doctor and check have finished, and only when the user asks. Park and speed 0 are required. Unlock, turning sentry off, the rear trunk, the front trunk, remote start, charging changes, setting or removing a departure schedule, climate keeper keep, dog, and camp, bioweapon defense on, the charge port, and scheduling or cancelling a software update need a fresh confirmation. A schedule cannot supply that confirmation and cannot run those commands.

`agentdrive sentry-on` and `agentdrive sentry-off` change sentry mode. Both wake a sleeping car: if the vehicle list says the car is not online, they send one wake and poll for up to 45 seconds, then send the signed command. `sentry-off` needs a fresh confirmation that names turning sentry off, for example `I confirm turn sentry off`. A schedule cannot supply that confirmation and cannot run `sentry-off`. `agentdrive check`, `agentdrive report`, `agentdrive where`, history, official charging history, software status, `agentdrive advice`, and `agentdrive tariff` do not wake a sleeping car. Advice does not change a charge limit, a timetable, or preconditioning.

`agentdrive seat-heat`, `agentdrive steering-heat`, `agentdrive defrost-on`, `agentdrive defrost-off`, and `agentdrive precondition` are parked climate commands. They use the same confirmation pattern as climate on, climate off, and set temperature. `seat-heat` takes a seat the signed tool accepts (`front-left`, `front-right`, `2nd-row-left`, `2nd-row-center`, `2nd-row-right`, `3rd-row-left`, `3rd-row-right`) and a level (`off`, `low`, `medium`, or `high`). `steering-heat` is `on` or `off`. Defrost is max defrost, on or off, for the front and rear together. `precondition` starts cabin preconditioning now. It does not add a departure schedule. A departure time is `agentdrive departure`.

`agentdrive charge-amps <amps>` sets the charging current. It is a charging command. It needs a fresh confirmation that names the action, for example `I confirm charging set amps`. The value is a whole number from 1 to 80. The car may use less, down to what the charger allows. A scheduled job cannot supply that confirmation and cannot run `charge-amps`.

`agentdrive charge-schedule show` reads the in-car charging timetable and does not wake a sleeping vehicle. `agentdrive charge-schedule set` and `agentdrive charge-schedule remove` change that timetable. They are charging commands. Each one needs a fresh confirmation. A scheduled job cannot supply that confirmation.

`agentdrive departure show` reads the departure schedules and does not wake a sleeping vehicle. `agentdrive departure set` and `agentdrive departure remove` change them. Each change needs a fresh confirmation, for example `I confirm departure schedule set` or `I confirm departure schedule remove`. A scheduled job cannot supply that confirmation and cannot run those two commands. `departure show` is a read. The schedule preconditions the cabin by a time, on selected days, at a place. Latitude and longitude are optional. Omit both and the command uses the car's current location after the same wake other parked changes use. If that location read says the car is asleep, it is not woken again: say the car must be online, or pass coordinates. It does not set a battery switch or an off-peak charging time. `precondition` is still the command that starts the cabin now. Precise coordinates are not written to the local history.

`agentdrive climate-keeper off` turns climate keeper off and does not need a confirmation. `agentdrive climate-keeper keep`, `dog`, and `camp` need a fresh confirmation because they use energy, for example `I confirm climate keeper keep`, `I confirm climate keeper dog`, or `I confirm climate keeper camp`. `agentdrive charge-port open` and `agentdrive charge-port close` need a fresh confirmation, for example `I confirm open charge port` or `I confirm close charge port`. If close fails because a cable is plugged in, say that and do not send it again. `agentdrive seat-cool <seat> <level>` sets front-seat cooling (`front-left` or `front-right`, level `off`, `low`, `medium`, or `high`). `agentdrive seat-climate <seat> on` or `off` sets automatic seat climate (`front-left`, `front-right`, or `both`). If the car has no seat cooling, or the climate is not on, say so and do not keep retrying. `agentdrive bioweapon off` does not need a confirmation. `agentdrive bioweapon on` needs one, for example `I confirm turn bioweapon defense on`. If the car does not support bioweapon defense mode, say so. `agentdrive software` shows the installed version, the update status, and official release notes. It does not wake a sleeping car: say the car is asleep. `agentdrive software schedule <delay>` and `agentdrive software cancel` need a fresh confirmation, for example `I confirm schedule software update` or `I confirm cancel software update`. A delay is `2h`, `10m`, `30s`, a whole number of seconds, or `now`.

`agentdrive charging-history` is the official account charging history. It does not wake a car. Show the date, site name, energy, and cost as Tesla returns them, and include the sentence `Home charging is not included`. Do not store site coordinates or a VIN. `agentdrive charging-invoice <id> --save` writes that PDF under the config home. Without `--save`, do not write the file.

`agentdrive boombox fart` plays one random fart on the external speaker and stops. `agentdrive boombox ping` plays one locate ping and stops. Both are parked commands and both need a fresh confirmation, for example `I confirm boombox fart` or, when the car has a nickname, `I confirm boombox fart on Sunny`. If the car is asleep, boombox wakes it the same way honk does. Quiet hours are 00:00-06:00 local time unless the owner changes them with `agentdrive boombox quiet HH:MM-HH:MM` or `agentdrive boombox quiet off`. The setting is stored in the config home. During quiet hours the command refuses and says the owner can override. `--override` plays during quiet hours only with a confirmation that includes `despite quiet hours`, for example `I confirm boombox fart on Sunny despite quiet hours`. A family member cannot override. Their boombox command is a pending owner request, the same as unlock. Approving that request still obeys quiet hours unless the owner approves with `--override`. A schedule cannot supply the confirmation and cannot run boombox.

`agentdrive trunk-open` and `agentdrive trunk-close` open and close the rear trunk. Closing works when the vehicle can close the rear trunk. `agentdrive frunk-open` opens the front trunk. The front trunk cannot be closed from AgentDrive. `agentdrive remote-start` enables keyless driving so the car can be driven without the phone key for a short time. It does not drive the car, it does not start an engine, and it does not ask for a password. Each command needs a fresh confirmation that names the action: open rear trunk, close rear trunk, open frunk, or enable remote start. Do not call `tesla-control` for these. The shared core signs them.

`agentdrive status` answers a direct question and may wake a sleeping vehicle. It is still not a lock, unlock, climate, or charging command. Use `agentdrive check` for the check that must happen first.

## Which car

`agentdrive cars` lists the cars on the account. It does not wake a car and it does not print a full VIN. A masked tail is enough to tell them apart.

With one car, do not ask for a nickname and do not pass `--car`. The commands stay as they are.

With several cars, ask the owner for a nickname for each car, then run `agentdrive cars name <tail> <nickname>`. The tail is the last four characters from `agentdrive cars`. Set a default only when the owner chooses one: `agentdrive cars default <nickname>`. `agentdrive cars clear-default` clears it.

Pass `--car <nickname>` on a command for one car. `--car` can sit before or after the command name. If several cars are saved and there is no default and no `--car`, a command that would act on one car refuses. Do not guess.

When a nickname is in use, a confirmation-required command names that car. The sentence has to include `confirm`, the action, and the nickname. Example: `I confirm unlock on daily`. `I confirm unlock` is not enough for that car, and a sentence that names a different nickname does not apply. The result line names the car too, for example `OK: unlock on daily`. With one car and no `--car`, keep the existing sentence, such as `I confirm unlock`.

Health checks, alert memory, briefings, and the local history are per car. The first car that is named keeps history that was already saved. With several cars, schedule `agentdrive check --json --car <nickname>` once per car. The same alert on two cars is two alerts. The morning job runs `agentdrive history due --car <nickname>` once per car and you send each briefing on its own. With one car, `agentdrive check --json` and `agentdrive history due` stay as they are. Those commands still do not wake the car.

## Family members

The owner registers a handle and the actions that handle may ask for.

```bash
agentdrive family add rider
agentdrive family allow rider lock
agentdrive family allow rider status
agentdrive family allow rider unlock
agentdrive family allow rider charging
agentdrive family list
agentdrive family revoke rider lock
agentdrive family remove rider
```

`charging` means the charging changes: start, stop, charge limit, charging current, and the in-car timetable changes. Low-risk actions the owner allowed, such as status or lock, run directly. Run them with `--as <handle>`.

Unlock and charging changes are not run for a family member, even if the owner allowed the request and even if a confirmation sentence is on the command. Turning sentry off, the trunk, the front trunk, remote start, departure changes, climate keeper keep, dog, or camp, bioweapon defense on, a charge-port open or close, scheduling or cancelling a software update, and the external speaker are the same. A family member cannot pass `--override` for boombox. The command writes a pending request and prints relay text. Read that text to the owner. Do not send it yourself, and do not approve it yourself.

```bash
agentdrive --as rider --car daily unlock
```

The owner approves or denies with the id from that text:

```bash
agentdrive request list
agentdrive request approve <id>
agentdrive request deny <id>
```

Approve runs the action once. The car must still be in Park at speed 0. A request expires 10 minutes after it is created. Expired, denied, and already used requests do not run. A family member cannot approve or deny their own request. Do not pass `--as` on `request approve` or `request deny`.

## Health check

On install, schedule a routine that runs `agentdrive check --json` every 30 minutes. Exit 0 means there is nothing new to report, including when the vehicle is asleep and when the same alerts are still active, so stay quiet. Exit 1 means a new alert the user should hear. Notify only when the exit code is 1. The same alert stays quiet until it clears and later returns. An energy-mode alert is the exception: climate left on, climate keeper keep, dog, or camp, or bioweapon defense mode, while the car is parked and no one is in the driver seat, repeats at most once an hour while that mode stays on and stops when the mode turns off. If someone is in the driver seat, that alert is not sent, and the parked-and-unlocked alert is not sent. The driver-seat reading covers only the driver seat. It is not a guarantee that nobody is inside. There is no rear-seat, passenger, child, or pet detection. Do not schedule unlock, turning sentry off, the trunk, the front trunk, remote start, a charging change, a departure schedule change, climate keeper keep, dog, or camp, bioweapon defense on, the charge port, a software update, or the external speaker. `charge-amps`, `charge-schedule set`, and `charge-schedule remove` are charging changes. `departure set` and `departure remove` are departure schedule changes. `sentry-off` is a sensitive command. Do not put `agentdrive report` or `agentdrive history due` on that health-check loop. When the car is already online, `check` also saves local history, including when there is nothing new to notify. It does not wake the car to do that. AgentDrive does not run that job and does not install cron.

## Morning history

On install, create one daily job. Run `agentdrive report-schedule`. The default is 07:30 local time and enabled, until the user changes it. The job runs `agentdrive history due` and you send that usage briefing. On Monday it is the previous week, on the 1st it is the previous month, and on 1 January it is the previous year. Every other morning it is the previous day. The command reads saved history only. It does not wake the car. This one morning job is enough. A short tip is included only on some mornings, and the same tip is not repeated for two calendar months. A service reminder is included when one is due and was not sent in the last two months. On a Monday weekly briefing, include one new official item from the Tesla site for the profile country when `history due` prints one. The item is a short summary plus the official link. When that part is absent, send the briefing without it. Do not add a second morning job, and do not fill the gap with a usage tip. `agentdrive news` prints the same official item on demand and prints nothing when there is nothing new. `agentdrive report` is the live parked snapshot. Run it when the user wants the car's current status, and send the skip line when the car is asleep.

A weekly, monthly, or yearly briefing prints a summary and a `Chart:` path. Attach that PNG to the message. The `Dashboard:` path is one local HTML file with week, month, and year views. It works offline. Do not upload the history database or the history key. The summary names kilometres, trips, average kilometres per day, charges, kWh added, the previous period, and coverage. History is opportunistic, not a continuous record. Say so when the briefing says coverage is incomplete.

## Trip and charging advice

`agentdrive advice trip <km>` answers whether the battery is enough for that many kilometres. It uses a non-waking reading when the car is already online, and otherwise the last saved battery. It learns battery percent per kilometre from saved trips. If fewer than three usable trips are saved, say that plainly and do not invent a consumption figure. It also suggests an everyday charge limit, usually 80%, and a higher limit only before a long trip. When a departure schedule exists and it is cold or hot, it suggests preconditioning. Advice only. Do not change the charge limit, start preconditioning, or edit a departure schedule unless the user asks, and then use the existing command and confirmation.

`agentdrive advice charge` flags often charging to 100% and leaving it, often dropping very low before charging, and charging in a peak window. Store the owner's home hours with `agentdrive tariff set off-peak HH:MM-HH:MM` and `agentdrive tariff set peak HH:MM-HH:MM`. `--price` is optional, per kWh. `agentdrive tariff show` prints them. The file is `$AGENTDRIVE_HOME/tariff.yaml`. Suggest moving scheduled charging into the cheap window when the advice says so. Do not run `charge-schedule set` unless the user asks, and then it still needs a fresh confirmation. A schedule cannot supply that confirmation.

Pass `--car <nickname>` when several cars are saved. Advice and charts stay with that car. With one car, do not pass `--car`.

`agentdrive history trips`, `history charges`, and `history snapshots` read the same saved history. `agentdrive tips` says whether a tip is due. `agentdrive service` shows kilometres since a tyre change or a service the user told you about. Log that with `agentdrive service log rotation`, `replacement`, or `maintenance`. If no odometer is saved yet, wait for the next online check or report, then log it. That does not wake the car.

If the user asks for a different time, run `agentdrive report-schedule set HH:MM` and move the job to that time. If the user asks to turn the briefing off, run `agentdrive report-schedule disable` and remove the job. `agentdrive report-schedule enable` turns it back on. AgentDrive only stores the time in the private config home. It does not run in the background and it does not update the job for you. A host cron can use the example line the schedule command prints. Do not install a crontab unless the user asked for cron.

The same job is used for every shell-capable AI agent. Do not invent a second morning command. The history key stays in the config home. Do not paste it into chat.

## Secrets

Never put a private key, token, client secret, authorization code, or VIN in this skill, in the AI agent's memory, or in chat. Car nicknames and family handles live in the config home. Do not copy `cars.yaml`, `family.yaml`, or `requests.json` into chat or into the git repo.
