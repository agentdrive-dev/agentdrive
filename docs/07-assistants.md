# Assistants

[Back to the checklist](00-before-you-start.md)

![You choose an assistant that can run commands, or the installer detects the host](images/assistant-1.svg)

Phase 1 has three adapter names:

| Name | When it is used | What it does now |
| --- | --- | --- |
| `grokbot` | Detected from `GROK_BOT`, `GROKBOT`, `GROK_BOT_HOME`, `GROK_BOT_SESSION`, or `CURSOR_AGENT` | Supported — tested end-to-end on a real vehicle (v0.1, internet commands). Working file-backed adapter. |
| `muse` | Detected from `MUSE_BOT`, `MUSE_HOME`, `MUSE_SESSION`, or `MUSE_AGENT` | Supported — tested end-to-end on a real vehicle (v0.1, internet commands). Working file-backed adapter. |
| `unknown` | No host signal | The template adapter runs |

Detection runs from `setup/install.sh` and from `python -m core adapter`. Prefer `$AGENTDRIVE_HOME/host.yaml` (written by the installer). Only copy `config/host.example.yaml` to `config/host.yaml` for a repo-local override you do not commit. A conflict does not guess: the command prints both signal lists and exits until you pass `--adapter`. `AGENTDRIVE_ADAPTER` and a `source: override` file also win. `--redetect` drops a saved override and looks again.

```bash
python -m core adapter show
python -m core adapter --adapter template show
bash setup/install.sh
```

Copy `adapters/template.py` for a host that is not listed. The same five operations apply: schedule, memory, confirm, secrets, and notify. Notifications stay silent unless you need to act. A schedule cannot store a confirmation.

## Health check by default

Installing an assistant includes this instruction: set up a recurring health check by default. The routine runs `agentdrive check --json` every few hours. It is read-only. It reads the vehicle list first and does not wake a sleeping car. Exit 0 means there is nothing to do, including when the car is asleep, so the assistant stays quiet. Exit 1 means an alert (tyre pressure, TPMS warning, low battery while not charging, unlocked or open while parked, or a charge error). Notify only on exit 1. `agentdrive adapter install` prints the same note. The config home and the private-key path work on Windows as well as macOS and Linux.

```text
Schedule a routine named health-check that runs `agentdrive check --json` every few hours. Notify me only when the exit code is 1. Do not wake the car from that routine. Do not schedule unlock or a charging change.
```

## Using more than one assistant

Most people run one assistant. Two assistants can share one private key that is already paired with the car. Do not enroll a second key for that case.

Each agent has its own OAuth token. The token file rotates when it is refreshed, so a second copy goes stale, and two writers can invalidate each other. Keep one token file and one writer for each agent.

The key file is readable by both. Every agent that can read the private key can sign commands, together with a valid token, inside the scopes you approved. Keep one copy, limited to your user. A second copy is a second secret.

Point `AGENTDRIVE_KEY_FILE`, or `key_file` in `$AGENTDRIVE_HOME/runtime.yaml`, at that key. Point `AGENTDRIVE_TOKEN_FILE`, or `token_file` in the same file, at that agent's own token file. See `config/runtime.example.yaml`.

## Prompt you can give the bot

```text
Run python -m core adapter show and tell me the adapter name, whether it was detected or overridden, and which environment variables it used. If it says ambiguous, stop and ask me which adapter to store. Do not print secrets.
```
