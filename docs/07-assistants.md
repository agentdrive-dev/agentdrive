# AI agents

[Back to the checklist](00-before-you-start.md)

![You choose an AI agent that can run commands, or the installer detects the host](images/assistant-1.svg)

## Host environment variables

The installer and `python -m core adapter` look at the process environment and pick the host. If both hosts match, nothing is written and the command asks you to choose. Set one yourself with `--adapter grokbot`, `--adapter muse`, `--adapter template`, or `--adapter unknown`, or with `AGENTDRIVE_ADAPTER`. A saved file with `source: override` stays in place until you pass `--redetect`.

| Name | When it is used | What it does now |
| --- | --- | --- |
| `grokbot` | Detected from `GROK_BOT`, `GROKBOT`, `GROK_BOT_HOME`, `GROK_BOT_SESSION`, or `CURSOR_AGENT` | Supported — basic real-car commands tried on a vehicle. Working file-backed adapter. |
| `muse` | Detected from `MUSE_BOT`, `MUSE_HOME`, `MUSE_SESSION`, or `MUSE_AGENT` | Supported — basic real-car commands tried on a vehicle. Working file-backed adapter. |
| `unknown` | No host signal | The template adapter runs |

Grok Bot and Muse were used for basic real-car commands. That trial does not cover every command in this repository.

Detection runs from `setup/install.sh` and from `python -m core adapter`. Prefer `$AGENTDRIVE_HOME/host.yaml` (written by the installer). Only copy `config/host.example.yaml` to `config/host.yaml` for a repo-local override you do not commit. A conflict does not guess: the command prints both signal lists and exits until you pass `--adapter`. `AGENTDRIVE_ADAPTER` and a `source: override` file also win. `--redetect` drops a saved override and looks again.

```bash
python -m core adapter show
python -m core adapter --adapter template show
bash setup/install.sh
```

Copy `adapters/template.py` for a host that is not listed. The same five operations apply: schedule, memory, confirm, secrets, and notify. Notifications stay silent unless you need to act. A schedule cannot store a confirmation.

## Health check by default

Setup for an AI agent includes this instruction: set up a recurring health check by default. The routine runs `agentdrive check --json` every 30 minutes. It is read-only. It reads the vehicle list first and does not wake a sleeping car. Exit 0 means there is nothing new to report, including when the car is asleep and when the same alerts are still active, so the AI agent stays quiet. Exit 1 means a new alert (tyre pressure, TPMS warning, low battery while not charging, sentry off while the battery is low and the vehicle is not charging, unlocked or open while parked, or a charge error). Notify only on exit 1. The same alert stays quiet until it clears and later returns. `agentdrive adapter install` prints the same note. AgentDrive does not run that job and does not install cron. The morning usage briefing stays the separate daily job. The config home and the private-key path work on Windows as well as macOS and Linux.

```text
Schedule a routine named health-check that runs `agentdrive check --json` every 30 minutes. Notify me only when the exit code is 1. Exit 0 means nothing new, including when the car is asleep and when the same alerts are still active. The same alert stays quiet until it clears and later returns. Do not wake the car from that routine. Do not schedule unlock, turning sentry off, the trunk, the front trunk, remote start, a charging change, or a departure schedule change.
```

## Daily vehicle report by default

The daily report is a second job, separate from the health check. The health check stays quiet unless it exits 1. The daily report is sent every time.

The default is 07:30 in the local timezone, and the report is enabled. `agentdrive report-schedule` prints that. The time is stored in `$AGENTDRIVE_HOME/runtime.yaml` under `report_schedule` when you change it. With no `report_schedule` key, the default above is what the command shows.

After install, the AI agent creates one daily job at the configured time. The job runs `agentdrive history due`, and the AI agent sends that usage briefing. On Monday it covers the previous week, on the 1st the previous month, and on 1 January the previous year. Every other morning it covers the previous day. The command reads saved history only. It does not wake the car. `agentdrive report` remains the live parked snapshot when the user wants the car's current status, including the skip line when the vehicle is not online.

When you ask for a different time, the AI agent runs `agentdrive report-schedule set HH:MM` and moves the job to that time. When you ask to turn the report off, the AI agent runs `agentdrive report-schedule disable` and removes the job. `agentdrive report-schedule enable` turns it back on. AgentDrive does not update the job itself. The same steps apply to Grok Bot, Muse, and any shell-capable AI agent.

A host that uses cron can copy the example line printed by `agentdrive report-schedule`. AgentDrive does not install that line or a systemd timer. This is the recommended setup. It is not zero-setup: something outside AgentDrive has to run the command.

```text
Read `agentdrive report-schedule`. If it is enabled, create a daily job at that local time that runs `agentdrive history due` and send me that briefing every day. On Monday send the previous week, on the 1st the previous month, and on 1 January the previous year. If I change the time or turn the briefing off, update the job to match the command. Do not wake the car from that job.
```

## Using more than one AI agent

Most people run one AI agent. Two AI agents can share one private key that is already paired with the car. Do not enroll a second key for that case.

Each agent has its own OAuth token. The token file rotates when it is refreshed, so a second copy goes stale, and two writers can invalidate each other. Keep one token file and one writer for each agent.

The key file is readable by both. Every agent that can read the private key can sign commands, together with a valid token, inside the scopes you approved. Keep one copy, limited to your user. A second copy is a second secret.

Point `AGENTDRIVE_KEY_FILE`, or `key_file` in `$AGENTDRIVE_HOME/runtime.yaml`, at that key. Point `AGENTDRIVE_TOKEN_FILE`, or `token_file` in the same file, at that agent's own token file. See `config/runtime.example.yaml`.

## Prompt you can give the AI agent

```text
Run python -m core adapter show and tell me the adapter name, whether it was detected or overridden, and which environment variables it used. If it says ambiguous, stop and ask me which adapter to store. Do not print secrets.
```
