# An assistant

[Back to the checklist](../00-before-you-start.md)

AgentDrive is a toolkit an assistant drives. Phase 1 includes a working Grok Bot adapter and a template adapter you can copy. The Muse adapter is a stub and will refuse every call.

The assistant has to read these docs, run shell commands, store files on the machine, and wait when a step needs you. You choose the assistant. AgentDrive does not add a fee on top of whatever that assistant already costs.

## Step 1 — Choose an assistant that can run commands

![Diagram: you choose Grok Bot or another assistant that can run commands](../images/assistant-1.svg)

Grok Bot is the adapter that is implemented. Another assistant can follow `adapters/template.py` if it can call the same five operations: schedule, memory, confirm, secrets, and notify. If the assistant can only chat, it can still walk you through the pages, but it cannot publish the key or refresh tokens by itself.

When the Grok Bot adapter is installed, set up a recurring health check by default. Schedule a routine that runs `agentdrive check --json` every few hours. The check is read-only and does not wake a sleeping car. Exit 0 means there is nothing to report, including when the vehicle is asleep. Exit 1 means an alert the owner should hear. Notify only when the exit code is 1. Do not put that check on a schedule that wakes the car, and do not schedule unlock or a charging change.

## Step 2 — Use two turns for the disclaimer

![Diagram: you read the disclaimer in one turn and accept it in a later turn, so install stays blocked](../images/assistant-2.svg)

The first message from the assistant must be the full [disclaimer](../../DISCLAIMER.md) and the full [security file](../../SECURITY.md), then a stop. Your acceptance has to be a later message, in these words:

`I have read, understood, and accept the AgentDrive disclaimer and security reminder`

A vague reply does not count. The assistant must not type that sentence for you in the same turn it showed the documents.

## Step 3 — Secrets go through the adapter, not through chat

![Diagram: the bot reads secret files and keeps assistant memory free of them](../images/assistant-3.svg)

The adapter reads `client-id`, `client-secret`, `vin`, and `redirect-uri` from files when a command needs them. It will not store those values in memory. A schedule cannot carry a confirmation, and a notification is sent only when you need to act.

## Step 4 — Let the installer detect the host

![Diagram: one host signal is stored, both hosts stop and ask, and --adapter overrides detection](../images/adapter-detect.svg)

You do not pick an adapter by hand when the process already shows which host it is. `setup/install.sh` and `python -m core adapter` check environment variables and write `$AGENTDRIVE_HOME/host.yaml`.

Grok Bot matches `GROK_BOT`, `GROKBOT`, `GROK_BOT_HOME`, `GROK_BOT_SESSION`, or `CURSOR_AGENT`. Muse matches `MUSE_BOT`, `MUSE_HOME`, `MUSE_SESSION`, or `MUSE_AGENT`. If none match, the stored choice is `unknown` and runtime uses the template adapter. If both hosts match, the installer stops and asks you to pass `--adapter`. A value of `0`, `false`, or `no` does not count as a match.

Force a choice with `setup/install.sh --adapter template`, `python -m core adapter --adapter muse show`, or `AGENTDRIVE_ADAPTER`. `source: override` in `config/host.yaml` or in the home file is kept until you pass `--redetect`. See `config/host.example.yaml`.

## Expected result

You know which assistant will run the toolkit, it has shown you the disclaimer without installing anything, and you know the acceptance sentence is yours to send later.

## Common errors

The assistant starts installing in the same message as the disclaimer. Stop it. Acceptance has not happened.

The assistant asks you to paste a token or a private key into chat. Refuse. Point it at a mode 600 file instead.

You wanted Muse. That adapter is not implemented in this version. Use Grok Bot or the template adapter.

## Bot prompt

Copy this only when you want the bot to do the part below. It must not invent approvals.

```text
Read DISCLAIMER.md and SECURITY.md and present both to me in full. Then stop. Do not install anything, do not create keys, and do not ask me for secrets in this message. Wait until I reply in a later message with the acceptance sentence.
```
