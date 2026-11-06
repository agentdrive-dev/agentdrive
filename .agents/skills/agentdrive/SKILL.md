---
name: agentdrive
description: Use AgentDrive to read a personal vehicle and send parked commands through the agentdrive program. Use when the user asks about battery, climate, lock state, tyre pressure, a health check, the daily vehicle report, or a parked command such as flash lights, lock, or charging. Do not sign vehicle commands in this skill.
---

# AgentDrive

AgentDrive is unofficial. It is not affiliated with, endorsed by, or supported by Tesla.

This skill tells a shell-capable assistant how to install and run AgentDrive. Grok Bot, Muse, and any other agent that can run shell commands use the same commands. Memory, schedules, confirmations, and secret files go through the host adapter. Signing stays in the shared core (`core/fleet/commands.py`). Do not call `tesla-control` from this skill, and do not reimplement request signing.

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
3. A vehicle command only after those two have finished, and only when the user asks. Park and speed 0 are required. Unlock and charging changes need a fresh confirmation. A schedule cannot supply that confirmation.

`agentdrive status` answers a direct question and may wake a sleeping vehicle. It is still not a lock, unlock, climate, or charging command. Use `agentdrive check` for the check that must happen first.

## Health check

On install, schedule a routine that runs `agentdrive check --json` every few hours. Exit 0 means there is nothing to report, including when the vehicle is asleep, so stay quiet. Exit 1 means an alert the user should hear. Notify only when the exit code is 1. Do not schedule unlock or a charging change. Do not put `agentdrive report` on that health-check loop.

## Daily vehicle report

On install, also create one daily job. Run `agentdrive report-schedule`. The default is 07:30 local time and enabled, until the user changes it. The job runs `agentdrive report` and you send the printed result to the user every time. Send the skip line too when the vehicle is asleep, offline, or the read times out. The command is read-only and does not wake the vehicle.

If the user asks for a different time, run `agentdrive report-schedule set HH:MM` and move the job to that time. If the user asks to turn the report off, run `agentdrive report-schedule disable` and remove the job. `agentdrive report-schedule enable` turns it back on. AgentDrive only stores the time in the private config home. It does not run in the background and it does not update the job for you. A host cron can use the example line the schedule command prints. Do not install a crontab unless the user asked for cron.

The same job is used for every shell-capable assistant. Do not invent a second report command.

## Secrets

Never put a private key, token, client secret, authorization code, or VIN in this skill, in assistant memory, or in chat.
