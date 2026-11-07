# Before you start

[Back to the README](../README.md)

This page is the complete install. The short prompt on the [README](../README.md#how-to-start) sends the AI agent here. Follow this page exactly. Do not invent a second installer or extra steps. Only ask the person for the approvals in [What you will do](#what-you-will-do).

## How to start

The prompt below is the full install brief. It lives on this page. Follow it.

**You just chat in plain words. The AI agent translates that into AgentDrive commands. You do not need to learn, memorize, or type the CLI commands.**

Grok Bot, Muse, or any shell-capable AI agent uses this same install.

```text
Install AgentDrive from https://github.com/agentdrive-dev/agentdrive.
Clone it to ~/agentdrive and read README.md, DISCLAIMER.md, and SECURITY.md.
Show me the disclaimer and the security reminder in full, then stop and wait.
Do not install anything until I reply in a later message with this sentence:
I have read, understood, and accept the AgentDrive disclaimer and security reminder
After that, follow "What the AI agent runs" on this page (docs/00-before-you-start.md) one step at a time,
tell me whenever a step needs me (GitHub login, the Tesla developer app and billing, browser Tesla sign-in, the virtual key in the Tesla phone app),
and keep every secret in ~/.config/agentdrive (or $AGENTDRIVE_HOME), never in chat or in the repo.
The same install is used for every shell-capable AI agent. Do not invent a second installer.
After install, run agentdrive report-schedule. The default is 07:30 local time and enabled.
Create one daily job at that time that runs agentdrive history due and send me that usage briefing.
On Monday send the previous week, on the 1st the previous month, and on 1 January the previous year.
Every other morning send the previous day. That one job is the whole morning briefing:
the usage report, a short tip on some mornings, a service reminder when one is due, and
on a Monday weekly briefing one official Tesla note only when the local Tesla site has
something new. If it does not, leave that part out. Do not add a second morning job.
Also schedule a health check that runs agentdrive check --json every 30 minutes.
Notify me only when it exits 1. Exit 0 means nothing new, including when the car
is asleep and when the same alert is still active. That alert stays quiet until
it clears and later returns. Do not wake the car from that check, and do not
schedule unlock or a charging change. This check is separate from the one daily
morning job above. AgentDrive does not run it itself.
agentdrive report is the live snapshot when I want the car's current status, including
the skip line when the car is asleep. If I later ask for a different time or to turn the
briefing off, update the stored schedule and update that job.
AgentDrive does not run it itself.
```

After the prompt is in use, the AI agent shows [DISCLAIMER.md](../DISCLAIMER.md) and [SECURITY.md](../SECURITY.md) in full, then stops. Reply in a later message with the acceptance sentence below. Same-turn acceptance is rejected. The installer checks the stamp and will not create it. The AI agent then installs one step at a time and interrupts only for the approvals in [What you will do](#what-you-will-do).

## What you need

- A [Tesla account that owns the car](prerequisites/vehicle-account.md).
- A [Tesla that supports virtual keys](prerequisites/vehicle-compatibility.md) (most cars from 2021 on do).
- An [AI agent](prerequisites/assistant.md) that can run shell commands on a [computer](prerequisites/computer.md), for example Grok Bot (brings its own computer), Muse, or any shell-capable AI agent.
- A [Tesla developer account](prerequisites/developer-app.md) with a [payment method and a billing limit above 0](prerequisites/billing.md). It uses the same Tesla login, not a new account. Tesla bills API use. A $10 monthly credit is published for individual developers.
- A [GitHub account](prerequisites/public-key-host.md), used to host the public key on GitHub Pages. You do not buy a domain.

## What you will do

[About an hour, mostly approvals.](prerequisites/time.md)

1. Paste the short prompt in [How to start](../README.md#how-to-start) to your AI agent. The full install prompt is on this page. Your AI agent gets the code and follows [What the AI agent runs](#what-the-ai-agent-runs).
2. Read the [disclaimer](../DISCLAIMER.md) and the [security reminder](../SECURITY.md) when the AI agent shows them, then reply in a separate message with: `I have read, understood, and accept the AgentDrive disclaimer and security reminder`
3. Approve GitHub login once when asked.
4. Create the Tesla developer app and add billing when asked. The AI agent tells you what to fill in. Put the secret in a file, never in chat.
5. Sign in to Tesla in your browser when the AI agent gives you a link.
6. Approve the key in the Tesla app on your phone. See [Virtual key](prerequisites/virtual-key.md).

Everything else (installing, publishing the key, registering, tokens, doctor, the daily report, test command) your AI agent does and reports back.

## What the AI agent runs

Your AI agent runs this install after the prompt in [How to start](#how-to-start) and the acceptance sentence in a later message. Grok Bot, Muse, and any other AI agent that can run shell commands use this same install.

1. Get the code.

```bash
git clone https://github.com/agentdrive-dev/agentdrive.git ~/agentdrive
cd ~/agentdrive
```

2. Read [DISCLAIMER.md](../DISCLAIMER.md) and [SECURITY.md](../SECURITY.md) in full. The AI agent must show both documents and then stop. In a later message, reply with this sentence:

`I have read, understood, and accept the AgentDrive disclaimer and security reminder`

3. From the repository root, after that separate acceptance, record it and install. Use a different `--turn-id` for each chat message. Same-turn acceptance is rejected. The installer checks the stamp and will not create it.

```bash
PYTHONPATH=. python3 -m core gate-present --turn-id 1
PYTHONPATH=. python3 -m core gate-accept --turn-id 2 --statement "I have read, understood, and accept the AgentDrive disclaimer and security reminder"
bash setup/install.sh
```

`gate-present` prints the disclaimer and the security file in full.

After install, the AI agent schedules `agentdrive check --json` every 30 minutes. Notify only when that command exits 1. Exit 0 means nothing new, including when the car is asleep and when the same alerts are still active. The same alert stays quiet until it clears and later returns. The check does not wake the car. Do not schedule unlock or a charging change on it. AgentDrive does not run that job. It is separate from the daily morning briefing.

After install, the AI agent reads `agentdrive report-schedule`. The default is 07:30 local time and enabled. It creates one daily job at that time that runs `agentdrive history due` and sends that usage briefing. On Monday it sends the previous week, on the 1st the previous month, and on 1 January the previous year. Every other morning it sends the previous day. `agentdrive report` remains available when you want the car's current status, including the skip line when the car is asleep. A later change of time, or turning the briefing off, is `agentdrive report-schedule set HH:MM`, `disable`, or `enable`. The AI agent then updates its own job. AgentDrive does not install that job.

## Full step-by-step guide

These pages are the longer version. Your AI agent follows them. You only do the six steps above.

1. [Install and safety](01-install-and-safety.md)
2. [Host the public key](02-host-the-public-key.md)
3. [Developer application](03-developer-app.md)
4. [Sign-in and pairing](04-sign-in-and-pairing.md)
5. [Tokens](05-tokens.md)
6. [Queries and commands](06-queries-and-commands.md)
7. [AI agents](07-assistants.md)
8. [Skills](08-skills.md)
9. [Troubleshooting](09-troubleshooting.md)
