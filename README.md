# AgentDrive

Turn your AI assistant into your car concierge.

[![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)
[![Vehicle trial](https://img.shields.io/badge/vehicle%20trial-basic%20commands%2C%20Grok%20Bot%20and%20Muse-lightgrey)](#supported-agents)

AgentDrive is unofficial. It is not affiliated with, endorsed by, or supported by Tesla.

You register your own developer application, publish your own public key, and keep every secret on your own machine. The public key is hosted on GitHub Pages (`<username>.github.io`). You do not buy a domain. Docs are published at <https://agentdrive-dev.github.io/agentdrive>.

## How it works

![How it works. You ask in plain words. The assistant invokes AgentDrive. AgentDrive reads the Tesla Fleet API and signs parked commands for the vehicle.](docs/images/how-it-works.svg)

The assistant invokes AgentDrive on your computer. Reads use the Tesla Fleet API. Parked commands are signed with your key. Those commands act while the vehicle is parked. AgentDrive does not drive the vehicle.

## What this version does

![What it does. Reads, parked commands, and the safety checks in the agentdrive command line.](docs/images/what-it-does.svg)

These are the commands in `agentdrive` today. Grok Bot and Muse have been used for basic real-car commands. That trial does not cover every command in this repository.

`agentdrive status` may wake a sleeping car. `agentdrive check` and `agentdrive report` never wake. `agentdrive wake` is a separate command. A parked command is sent only in Park at speed 0, then the car is read once. `wake` does not use that park check. The VIN in a status reading is masked. Status and the daily report do not request location.

`agentdrive doctor` checks the local setup. `agentdrive refresh` refreshes the access token. A later command refreshes on its own when fewer than 30 minutes remain.

| Command | Confirmation |
| --- | --- |
| `wake` | no, and it does not use the park check |
| `flash`, `honk`, `lock` | no |
| `climate-on`, `climate-off` | no |
| `set-temp <C>` | no, and only 15 to 28 C |
| `unlock` | yes, one time |
| `charge-start`, `charge-stop` | yes, one time |
| `charge-limit <percent>` | yes, one time, and only 50 to 100 |

Unlock and charging changes are immediate commands. A schedule cannot run them or confirm them. This version has no trunk, frunk, or remote-start command.

### Check, daily report, and update

```bash
agentdrive check
agentdrive report
agentdrive report-schedule
agentdrive report-schedule set 07:30
agentdrive report-schedule disable
agentdrive report-schedule enable
agentdrive update
```

`agentdrive check` prints that it skipped, and exits 0, when the car is not online. When the car is online it exits 1 for a TPMS warning, a tyre below the threshold, a low battery that is not charging, the car unlocked or open while parked, or a charge error. `agentdrive check --json` prints a short object. On install, the assistant is asked to run that check every few hours and to notify only when the exit code is 1.

`agentdrive report` prints a short briefing the assistant should send to you: battery, charge connection, lock, gear, doors, windows, trunk, sentry, climate, tyres, and TPMS. It reads the same parked snapshot as `check` and does not request location. If the vehicle list says the car is asleep, offline, or otherwise not online, it prints the same skip line as `check` and exits 0. A timeout or HTTP 408 is the same skip. It never wakes the car. `--json` prints that briefing as an object. This is a report to send every time, including the skip line. It is not an alerts-only health check.

The default schedule is 07:30 in your local timezone, and it is enabled. `agentdrive report-schedule` shows that time. The setting is stored in the private config home (`$AGENTDRIVE_HOME/runtime.yaml`, or `~/.config/agentdrive` when the variable is unset) only after you change it. After install, the assistant creates a daily job at the configured time, runs `agentdrive report`, and sends you the result. Say when you want a different time, or when you want the report off. The assistant then runs `report-schedule set HH:MM`, `disable`, or `enable`, and updates its own job. AgentDrive does not run in the background, and it does not rewrite that job, a crontab, or a systemd timer. The schedule command prints a crontab example for a host that prefers cron. The recommended setup is this 07:30 local daily report. It still needs the assistant, or a cron line you add yourself, to start it.

`agentdrive update` updates this checkout from the latest `main` and leaves private config in `~/.config/agentdrive` (or `$AGENTDRIVE_HOME`).

### Not in this version

- No scheduled charging.
- No charging history, charging invoices, or telemetry stream.
- No where command and no location readout.
- No trunk, frunk, or remote-start command.
- No China-region account.
- Onboarding, trip advisor, charging tips, and news watch are stubs.
- No live Supercharger pricing. The business-only charging sessions route is refused.

## Illustrative example

![Illustrative example — sample data. A sample chat about battery and a lights flash. The figures are sample data, not a vehicle reading or a transcript.](docs/images/ask-in-plain-words.svg)

## Disclaimer

Experimental and testing purposes only. AgentDrive is unofficial and is not a Tesla product. Use it only on a vehicle you own, or a vehicle you are explicitly allowed to control.

To the maximum extent permitted by law the software is provided as is, and the author's liability is excluded. Nothing here excludes a right under the Australian Consumer Law that cannot be excluded. The full text is in [DISCLAIMER.md](DISCLAIMER.md). Read [SECURITY.md](SECURITY.md) before you install.

## Quick start

Grok Bot, Muse, and any other agent that can run shell commands use this same install.

1. Get the code.

```bash
git clone https://github.com/agentdrive-dev/agentdrive.git ~/agentdrive
cd ~/agentdrive
```

2. Read [DISCLAIMER.md](DISCLAIMER.md) and [SECURITY.md](SECURITY.md) in full. The assistant must show both documents and then stop. In a later message, reply with this sentence:

`I have read, understood, and accept the AgentDrive disclaimer and security reminder`

3. From the repository root, after that separate acceptance, record it and install. Use a different `--turn-id` for each chat message. Same-turn acceptance is rejected. The installer checks the stamp and will not create it.

```bash
PYTHONPATH=. python3 -m core gate-present --turn-id 1
PYTHONPATH=. python3 -m core gate-accept --turn-id 2 --statement "I have read, understood, and accept the AgentDrive disclaimer and security reminder"
bash setup/install.sh
```

`gate-present` prints the disclaimer and the security file in full.

<details>
<summary>Prompt you can paste to a shell agent</summary>

```text
Install AgentDrive from https://github.com/agentdrive-dev/agentdrive.
Clone it to ~/agentdrive and read README.md, DISCLAIMER.md, and SECURITY.md.
Show me the disclaimer and the security reminder in full, then stop and wait.
Do not install anything until I reply in a later message with this sentence:
I have read, understood, and accept the AgentDrive disclaimer and security reminder
After that, follow the Quick start in the README one step at a time,
tell me whenever a step needs me (GitHub login, forms, billing, browser sign-in, key pairing),
and keep every secret in ~/.config/agentdrive (or $AGENTDRIVE_HOME), never in chat or in the repo.
The same install is used for every shell agent. Do not invent a second installer.
After install, run agentdrive report-schedule. The default is 07:30 local time and enabled.
Create a daily job at that time that runs agentdrive report and send me the result every day,
including the skip line when the car is asleep. If I later ask for a different time or to turn
the report off, update the stored schedule and update that job. AgentDrive does not run it itself.
```

</details>

## Before you start

### What you need

- A [Tesla account that owns the car](docs/prerequisites/vehicle-account.md).
- A [Tesla that supports virtual keys](docs/prerequisites/vehicle-compatibility.md) (most cars from 2021 on do).
- An [assistant](docs/prerequisites/assistant.md) that can run shell commands on a [computer](docs/prerequisites/computer.md), for example Grok Bot (brings its own computer), Muse, or any shell-capable agent.
- A [Tesla developer account](docs/prerequisites/developer-app.md) with a [payment method and a billing limit above 0](docs/prerequisites/billing.md). It uses the same Tesla login, not a new account. Tesla bills API use. A $10 monthly credit is published for individual developers.
- A [GitHub account](docs/prerequisites/public-key-host.md), used to host the public key on GitHub Pages. You do not buy a domain.

### What you will do

[About an hour, mostly approvals.](docs/prerequisites/time.md)

1. Follow the [Quick start](#quick-start).
2. Read the [disclaimer](DISCLAIMER.md) and the [security reminder](SECURITY.md), then reply in a separate message with: `I have read, understood, and accept the AgentDrive disclaimer and security reminder`
3. Approve GitHub login once when asked.
4. Create the Tesla developer app and add billing when asked. The assistant tells you what to fill in. Put the secret in a file, never in chat.
5. Sign in to Tesla in your browser when the assistant gives you a link.
6. Approve the key in the Tesla app on your phone. See [Virtual key](docs/prerequisites/virtual-key.md).

Everything else (installing, publishing the key, registering, tokens, doctor, the daily report, test command) your assistant does and reports back.

[Full step-by-step guide](docs/00-before-you-start.md#full-step-by-step-guide)

## Supported agents

| Agent | Status | Notes |
| --- | --- | --- |
| [Grok Bot](https://grokbot.ai) | Supported — basic real-car commands tried on a vehicle | Adapter `grokbot`. |
| Any agent that can run shell commands | Supported through the template | Adapter `template`. Works with assistants such as coding agents or self-hosted bots that can run `python3` and `bash` on your computer. |
| Muse | Supported — basic real-car commands tried on a vehicle | Adapter `muse`. |

Basic real-car commands were tried with Grok Bot and with Muse. That trial does not cover every command in this repository.

Host detection, including the environment variables the installer reads, is documented in [Assistants](docs/07-assistants.md#host-environment-variables).

Want another agent? Copy `adapters/template.py`, then open an issue or a pull request.

## Layout

`config/` holds hosts, scopes, and generic vehicle capabilities. `core/fleet` talks to the API. `core/safety` enforces park, confirmation, and the install gate. `core/profile` stores suburb-level habits. `adapters/` swaps the assistant. `setup/` installs, publishes the public key, and runs doctor.

## Contact

agentdrivedev@gmail.com

## Reporting

Ordinary problems go to [GitHub Issues](https://github.com/agentdrive-dev/agentdrive/issues). Vulnerabilities go through [private vulnerability reporting](https://github.com/agentdrive-dev/agentdrive/security/advisories/new). Reports are reviewed and fixed promptly. Do not include secrets, a VIN, or a live location.

## Acknowledgements

Thanks to [MuseDrive](https://github.com/ukhhh/musedrive) for the original idea and working approach that inspired AgentDrive. MuseDrive is MIT-licensed; its notice is preserved in [NOTICE](NOTICE).

## License

MIT License, Copyright (c) 2026 AgentDrive contributors. See [LICENSE](LICENSE). The MuseDrive MIT notice is kept, unchanged, in [NOTICE](NOTICE). MuseDrive is at <https://github.com/ukhhh/musedrive>.
