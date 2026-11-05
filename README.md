# AgentDrive

Turn your AI assistant into your car concierge.

AgentDrive lets you connect a car you own to an assistant you already use. You register your own developer application, publish your own public key, and keep every secret on your own machine. The assistant can then report status, flash the lights, turn climate on or off, and send a morning note only when you need to do something.

AgentDrive is unofficial. It is not affiliated with, endorsed by, or supported by Tesla.

Docs are published at <https://agentdrive-dev.github.io/agentdrive>.

## Quick start: install with your AI agent

AgentDrive is installed by your assistant (Grok Bot, or any agent that can run shell commands on a computer). Paste this message to it:

```text
Install AgentDrive from https://github.com/agentdrive-dev/agentdrive.
Clone it to ~/agentdrive and read README.md, DISCLAIMER.md and SECURITY.md.
Show me the disclaimer and the security reminder in full, then stop and wait.
Do not install anything until I reply with the acceptance sentence.
After that, follow "What you will do" in the README one step at a time,
tell me whenever a step needs me (GitHub login, forms, billing, browser sign-in, key pairing),
and keep every secret in ~/.config/agentdrive (or $AGENTDRIVE_HOME), never in chat or in the repo.
```

Muse users: paste the prompt on [Install in Muse](docs/10-install-in-muse.md). It points Muse at an existing private key and existing tokens, then runs doctor and a read-only check before any command.

Prefer to do it yourself? Get the code, then follow [Install](#install):

```bash
git clone https://github.com/agentdrive-dev/agentdrive.git ~/agentdrive
cd ~/agentdrive
```

To upgrade later, run `agentdrive update`. It updates this checkout from the latest `main` and leaves your private config in `~/.config/agentdrive` (or `$AGENTDRIVE_HOME`).

## Disclaimer

Experimental and testing purposes only.

AgentDrive is an unofficial personal toolkit. It is not affiliated with, endorsed by, or supported by Tesla. It is not a Tesla product, and Tesla does not provide support for it.

Use it only on a vehicle you own, or a vehicle you are explicitly allowed to control, with your own account, for your own personal use. You bear all consequences of installing, configuring, and using this project. The author is not responsible for vehicle damage, injury, property loss, theft, fines, charges, account limits, data loss, or any other outcome.

To the maximum extent permitted by law, the software and the documentation are provided as is, without warranty of any kind, and the author's liability is excluded. Nothing in this disclaimer excludes, restricts, or modifies any consumer guarantee, right, or remedy under the Australian Consumer Law that cannot be excluded. Where the Australian Consumer Law allows liability for a failure to comply with a consumer guarantee to be limited, the author's liability is limited to the maximum extent permitted by that law.

A command that changes the vehicle is sent only when the shift state is Park and the speed is zero. After every command, AgentDrive reads the vehicle again. Unlock, remote start, opening the front trunk, opening the rear trunk, turning sentry mode off, and any charging change each need a fresh explicit confirmation. That confirmation covers that single action and then expires. It is never reused, and a schedule cannot supply it or skip it. These checks also apply if such commands are added later; this version has no trunk, frunk, or remote-start command.

Follow Tesla's current safety guidance, developer terms, and Fleet API terms. Where those terms and this project differ, the terms win. Obey the road rules that apply where you are, and do not interact with the assistant while you are driving.

A private key together with an access token can operate the vehicle inside the scopes you approved. Keep both private. Reported problems are reviewed and fixed promptly through GitHub Issues and GitHub private vulnerability reporting. See [SECURITY.md](SECURITY.md). The same text is in [DISCLAIMER.md](DISCLAIMER.md).

## Before you start

### What you need

- A [Tesla account that owns the car](docs/prerequisites/vehicle-account.md).
- A [car that supports virtual keys](docs/prerequisites/vehicle-compatibility.md).
- A [computer your assistant can run commands on](docs/prerequisites/computer.md).
- A supported [assistant](docs/prerequisites/assistant.md) (Grok Bot or Muse, or any shell agent via the template).
- A [Tesla developer account](docs/prerequisites/developer-app.md) with a [payment method and a billing limit above 0](docs/prerequisites/billing.md). Tesla bills API use. A $10 monthly credit is published for individual developers.
- A [GitHub account](docs/prerequisites/public-key-host.md). You do not buy a domain.
- [About an hour](docs/prerequisites/time.md).

### What you will do

1. Paste the Quick start prompt to your assistant.
2. Read the [disclaimer](DISCLAIMER.md) and the [security reminder](SECURITY.md), then reply in a separate message with: `I have read, understood, and accept the AgentDrive disclaimer and security reminder`
3. Approve GitHub login once when asked.
4. Create the Tesla developer app and add billing when asked. The assistant tells you what to fill in. Put the secret in a file, never in chat.
5. Sign in to Tesla in your browser when the assistant gives you a link.
6. Approve the key in the Tesla app on your phone. See [Virtual key](docs/prerequisites/virtual-key.md).

Everything else (installing, publishing the key, registering, tokens, doctor, test command) your assistant does and reports back.

[Full step-by-step guide](docs/00-before-you-start.md#full-step-by-step-guide)

## Supported agents

| Agent | Status | Notes |
| --- | --- | --- |
| [Grok Bot](https://grokbot.ai) | Supported — tested end-to-end on a real vehicle (v0.1, internet commands) | Adapter `grokbot`. |
| Any agent that can run shell commands | Supported through the template | Adapter `template`. Works with assistants such as coding agents or self-hosted bots that can run `python3` and `bash` on your computer. |
| Muse | Supported — tested end-to-end on a real vehicle (v0.1, internet commands) | Adapter `muse`. Install steps: [Install in Muse](docs/10-install-in-muse.md). |

Want another agent? Copy `adapters/template.py`, then open an issue or a pull request.

## Host adapter

The installer and `python -m core adapter` look at the process environment and pick the host bot:

- Grok Bot when `GROK_BOT`, `GROKBOT`, `GROK_BOT_HOME`, `GROK_BOT_SESSION`, or `CURSOR_AGENT` is set. Supported — tested end-to-end on a real vehicle (v0.1, internet commands).
- Muse when `MUSE_BOT`, `MUSE_HOME`, `MUSE_SESSION`, or `MUSE_AGENT` is set. Supported — tested end-to-end on a real vehicle (v0.1, internet commands). See [Install in Muse](docs/10-install-in-muse.md).
- `unknown` when none of those are set. Runtime then uses the template adapter.

Prefer `$AGENTDRIVE_HOME/host.yaml` (written by the installer). Only copy `config/host.example.yaml` to `config/host.yaml` for a repo-local override you do not commit. If both hosts match, nothing is written and the command asks you to choose. Set one yourself with `--adapter grokbot`, `--adapter muse`, `--adapter template`, or `--adapter unknown`, or with `AGENTDRIVE_ADAPTER`. A saved file with `source: override` stays in place until you pass `--redetect`.

## Install

The assistant must show [DISCLAIMER.md](DISCLAIMER.md) and [SECURITY.md](SECURITY.md) in full, then wait. It must not install in that same turn.

After you reply with the acceptance sentence:

From the repository root (so Python can find the core package):

```bash
PYTHONPATH=. python3 -m core gate-present --turn-id 1
PYTHONPATH=. python3 -m core gate-accept --turn-id 2 --statement "I have read, understood, and accept the AgentDrive disclaimer and security reminder"
bash setup/install.sh
```

Use a different `--turn-id` for each chat message (for example 1 when showing the docs, 2 when recording acceptance). Same-turn acceptance is rejected. The installer checks the stamp and will not create it.

## What version 0.1 actually does

**Reads**

- Owner and partner sign-in.
- Rotating token storage. The access token is refreshed when fewer than 30 minutes remain, and a lock stops two refreshes at once.
- Vehicle reads, wake, and status with tyre pressures.
- A read-only `agentdrive check` that does not wake a sleeping car.
- Nearby charging sites (location and stalls only), charging history, and charging invoices.

**Commands**

- Signed commands through `tesla-control`: flash, honk, lock, unlock, climate, set temperature, charge start, charge stop, and charge limit.
- `agentdrive update`, which updates this checkout from the latest `main`.
- `agentdrive doctor`.

**Safety**

- The safety gate. A state-changing command is sent only in Park at speed 0, then the car is read again.
- Unlock and charging changes need a one-time confirmation.
- This version has no trunk, frunk, or remote-start command.

**Adapters**

- The Grok Bot adapter. Supported — tested end-to-end on a real vehicle (v0.1, internet commands).
- The Muse adapter. Supported — tested end-to-end on a real vehicle (v0.1, internet commands). Install steps are in [Install in Muse](docs/10-install-in-muse.md). Config home is `~/.config/agentdrive` or `$AGENTDRIVE_HOME`, on macOS, Linux, and Windows. The private-key path can point at an existing file.
- The template adapter.

**Skills**

- Status.
- Flash lights.
- Climate on or off.
- Morning briefing.

**Not in this version**

- Onboarding, trip advisor, charging tips, and news watch. These are stubs.
- Live Supercharger pricing.
- The business-only charging sessions route. The client refuses it.
- Confirmed field names on the charging reads. They stay marked unconfirmed until they are compared with a live response.
- China-region Tesla accounts are not supported.

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
