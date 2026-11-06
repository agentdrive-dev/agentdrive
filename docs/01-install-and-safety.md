# Install and safety

[Back to the checklist](00-before-you-start.md)

A beginner pastes the prompt in [How to start](../README.md#how-to-start). This page is what the assistant runs after that person accepts in a later message.

Installation is two turns. The first turn shows the documents. A later turn records your acceptance. The installer will not create that acceptance for you.

![Every state-changing command is parked, confirmed when sensitive, sent once, and read back](images/command-safety.svg)

## Step 1 — Read both documents

Read [DISCLAIMER.md](../DISCLAIMER.md) and [SECURITY.md](../SECURITY.md) in full. They say the project is experimental, that you bear the consequences, and that sensitive commands need a fresh confirmation that a schedule cannot skip.

## Step 2 — Accept later

From the repository root (so Python can find the core package):

```bash
PYTHONPATH=. python3 -m core gate-present --turn-id 1
PYTHONPATH=. python3 -m core gate-accept --turn-id 2 --statement "I have read, understood, and accept the AgentDrive disclaimer and security reminder"
bash setup/install.sh
```

Use a different `--turn-id` for each chat message (for example 1 when showing the docs, 2 when recording acceptance). Same-turn acceptance is rejected. `setup/install.sh` checks the stamp, detects the host adapter, then creates `.venv`.

Override a wrong detection with `bash setup/install.sh --adapter template`.

## Step 3 — What the safety gate will refuse

A state-changing command is sent only when the shift state is Park and the speed is 0. Unlock, remote start, the front trunk, the rear trunk, sentry off, and charging changes each need an explicit confirmation for that action. The next action needs a new one. A schedule cannot supply it. Trunk, frunk, and remote start, when added, use this same gate. See [Next](../README.md#next).

## Update an existing install

`agentdrive update` brings the checkout in line with the latest `main` and re-runs the install steps, including the `tesla-control` build. It does not modify private configuration under `$AGENTDRIVE_HOME`: tokens, keys, client credentials, `host.yaml`, or vehicle files. `setup/install.sh --update` is the same tooling step without the adapter detection that writes `host.yaml`.

## Expected result

`python -m core gate-check` prints that install is allowed, and `setup/install.sh` finishes with a virtual environment. `$AGENTDRIVE_HOME/host.yaml` names the adapter.
