# Assistants

[Back to the checklist](00-before-you-start.md)

![You choose an assistant that can run commands, or the installer detects the host](images/assistant-1.svg)

Phase 1 has three adapter names:

| Name | When it is used | What it does now |
| --- | --- | --- |
| `grokbot` | Detected from `GROK_BOT`, `GROKBOT`, `GROK_BOT_HOME`, `GROK_BOT_SESSION`, or `CURSOR_AGENT` | Working file-backed adapter |
| `muse` | Detected from `MUSE_BOT`, `MUSE_HOME`, `MUSE_SESSION`, or `MUSE_AGENT` | Stub in this version: every call fails; use Grok Bot or the template adapter |
| `unknown` | No host signal | The template adapter runs |

Detection runs from `setup/install.sh` and from `python -m core adapter`. Prefer `$AGENTDRIVE_HOME/host.yaml` (written by the installer). Only copy `config/host.example.yaml` to `config/host.yaml` for a repo-local override you do not commit. A conflict does not guess: the command prints both signal lists and exits until you pass `--adapter`. `AGENTDRIVE_ADAPTER` and a `source: override` file also win. `--redetect` drops a saved override and looks again.

```bash
python -m core adapter show
python -m core adapter --adapter template show
bash setup/install.sh --adapter grokbot
```

Copy `adapters/template.py` for a host that is not listed. The same five operations apply: schedule, memory, confirm, secrets, and notify. Notifications stay silent unless you need to act. A schedule cannot store a confirmation.

## Health check by default

Installing the Grok Bot adapter includes this instruction for the bot: set up a recurring health check by default. The routine runs `agentdrive check --json` every few hours. It is read-only. It reads the vehicle list first and does not wake a sleeping car. Exit 0 means there is nothing to do, including when the car is asleep, so the bot stays quiet. Exit 1 means an alert (tyre pressure, TPMS warning, low battery while not charging, unlocked or open while parked, or a charge error). Notify only on exit 1. The same note is printed by `agentdrive adapter install` when the adapter is `grokbot`.

```text
Schedule a routine named health-check that runs `agentdrive check --json` every few hours. Notify me only when the exit code is 1. Do not wake the car from that routine. Do not schedule unlock or a charging change.
```

## Prompt you can give the bot

```text
Run python -m core adapter show and tell me the adapter name, whether it was detected or overridden, and which environment variables it used. If it says ambiguous, stop and ask me which adapter to store. Do not print secrets.
```
