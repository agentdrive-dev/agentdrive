# AgentDrive

Turn your AI assistant into your car concierge.

AgentDrive lets you connect a car you own to an assistant you already use. You register your own developer application, publish your own public key, and keep every secret on your own machine. The assistant can then report status, flash the lights, turn climate on or off, and send a morning note only when you need to do something.

AgentDrive is unofficial. It is not affiliated with, endorsed by, or supported by Tesla.

Docs are published at <https://agentdrive-dev.github.io/agentdrive>.

## Disclaimer

Experimental and testing purposes only.

AgentDrive is an unofficial personal toolkit. It is not affiliated with, endorsed by, or supported by Tesla. It is not a Tesla product, and Tesla does not provide support for it.

Use it only on a vehicle you own, or a vehicle you are explicitly allowed to control, with your own account, for your own personal use. You bear all consequences of installing, configuring, and using this project. The author is not responsible for vehicle damage, injury, property loss, theft, fines, charges, account limits, data loss, or any other outcome.

To the maximum extent permitted by law, the software and the documentation are provided as is, without warranty of any kind, and the author's liability is excluded. Nothing in this disclaimer excludes, restricts, or modifies any consumer guarantee, right, or remedy under the Australian Consumer Law that cannot be excluded. Where the Australian Consumer Law allows liability for a failure to comply with a consumer guarantee to be limited, the author's liability is limited to the maximum extent permitted by that law.

A command that changes the vehicle is sent only when the shift state is Park and the speed is zero. After every command, AgentDrive reads the vehicle again. Unlock, remote start, opening the front trunk, opening the rear trunk, turning sentry mode off, and any charging change each need a fresh explicit confirmation. That confirmation covers that single action and then expires. It is never reused, and a schedule cannot supply it or skip it.

Follow Tesla's current safety guidance, developer terms, and Fleet API terms. Where those terms and this project differ, the terms win. Obey the road rules that apply where you are, and do not interact with the assistant while you are driving.

A private key together with an access token can operate the vehicle inside the scopes you approved. Keep both private. Reported problems are reviewed and fixed promptly through GitHub Issues and GitHub private vulnerability reporting. See [SECURITY.md](SECURITY.md). The same text is in [DISCLAIMER.md](DISCLAIMER.md).

## Before you start

Do not install anything until every row below is true, or you know which row you still have to finish. Each item links to its own step-by-step page. Checked against Tesla's developer docs on the day this guide was written: the getting-started page, the billing page, the virtual-key guide, the partner endpoints, and the vehicle endpoints.

| Item | Why it is needed | Cost | Who |
| --- | --- | --- | --- |
| [Vehicle account](docs/prerequisites/vehicle-account.md) | Tesla's getting-started page requires an account with a verified email and multi-factor authentication. Only an account that can see the car can approve access and enroll a key. | No fee from this project. Use the account that already owns the car. | You |
| [A car that can take a virtual key](docs/prerequisites/vehicle-compatibility.md) | Signed commands need the command protocol and an enrolled key. Tesla's fleet status marks some older Model S and Model X hardware as unsupported, and it reports firmware floors. | No extra fee. | You confirm the car. The bot reads fleet status later. |
| [A computer the assistant can use](docs/prerequisites/computer.md) | Keys, token refresh, doctor, and `tesla-control` run on a machine. A phone alone cannot hold the private key. | Your computer. The tools are free. | You provide it. The bot runs the checks. |
| [An assistant](docs/prerequisites/assistant.md) | Something has to follow these docs, run commands, and wait for a separate answer. Phase 1 includes a Grok Bot adapter and a template. The Muse adapter is not built yet. | Whatever that assistant already costs. AgentDrive adds none. | You choose it. |
| [A developer application](docs/prerequisites/developer-app.md) | Tesla requires an application before any API call. It holds the client id, the secret, the allowed origin, and the redirect URL. | Creating it is not an AgentDrive charge. API use is billed separately. | You submit the form. |
| [A payment method and a billing limit](docs/prerequisites/billing.md) | Tesla's billing page says an application with no payment method, or one that passes its billing limit, is disabled. The limit starts at 0. Usage is pay-per-use. Status codes 500 and above are not billed. A monthly discount of $10 is published for individual developers. Set a limit. | Not free. The card is yours. The credit is Tesla's, and the amount can change. | You, in the portal. Never give the bot the card. |
| [A free Cloudflare account](docs/prerequisites/public-key-host.md) | The public key must be on HTTPS before Tesla will register the domain. One login lets the bot publish it. | Cloudflare's free plan. You do not buy a domain. | You sign up and approve login once. The bot publishes. |
| [Key pairing in the official app](docs/prerequisites/virtual-key.md) | A personal vehicle rejects signed commands until someone with access adds the key. Tesla does not add that key for you. | Free. | You, on your phone, after scopes are granted. |
| [Time](docs/prerequisites/time.md) | The forms, the card, the browser approvals, and the pairing are human steps. | About an hour of clicking if Tesla accepts the application immediately. Their review time is not published here. | You for approvals. The bot for the commands in between. |

### The path, in order

1. Read [DISCLAIMER.md](DISCLAIMER.md) and [SECURITY.md](SECURITY.md), then stop. Outcome: the risks are in front of you and nothing is installed. Guide: [Before you start](docs/00-before-you-start.md).
2. In a later message, accept with the sentence in the security file. Outcome: the install gate opens. Guide: [Install](docs/01-install-and-safety.md).
3. Finish any open row in the checklist. Outcome: you know nothing required is missing. Guide: [Before you start](docs/00-before-you-start.md).
4. Run the installer. Outcome: a local environment can run AgentDrive. Guide: [Install](docs/01-install-and-safety.md).
5. Create the Cloudflare account and approve `npx wrangler login` once. Outcome: the bot is allowed to publish. Guide: [Public key host](docs/prerequisites/public-key-host.md).
6. Let the bot publish the key. Outcome: `https://<project>.pages.dev/.well-known/appspecific/com.tesla.3p.public-key.pem` returns the public key. Guide: [Host the public key](docs/02-host-the-public-key.md).
7. Create the developer application and store the secret in a file, not in chat. Outcome: the hostname is an allowed origin. Guide: [Developer application](docs/03-developer-app.md).
8. Add the payment method and raise the billing limit above 0. Outcome: calls are allowed up to the cap you set. Guide: [Billing](docs/prerequisites/billing.md).
9. Let the bot register the partner domain and hand you a sign-in link. Outcome: Tesla has been asked to fetch the key, and your browser is ready. Guide: [Sign-in and pairing](docs/04-sign-in-and-pairing.md).
10. Finish sign-in in your own browser. Outcome: tokens are stored locally, and a new refresh token is saved before the old one is dropped. Guide: [Tokens](docs/05-tokens.md).
11. Pair the key in the official app. Outcome: the car lists the key. Guide: [Virtual key](docs/prerequisites/virtual-key.md).
12. Run doctor. Outcome: config, private key, public URL, and token each pass or name what is still missing. Guide: [Troubleshooting](docs/09-troubleshooting.md).
13. Ask for status, then flash the lights while the car is parked. Outcome: one read and one signed command, each followed by a fresh read. Guides: [Queries and commands](docs/06-queries-and-commands.md), [Skills](docs/08-skills.md).
14. Connect the assistant. Outcome: it stays quiet unless you need to act. Guides: [Assistants](docs/07-assistants.md), [Skills](docs/08-skills.md).

## Host adapter

The installer and `python -m core adapter` look at the process environment and pick the host bot:

- Grok Bot when `GROK_BOT`, `GROKBOT`, `GROK_BOT_HOME`, `GROK_BOT_SESSION`, or `CURSOR_AGENT` is set.
- Muse when `MUSE_BOT`, `MUSE_HOME`, `MUSE_SESSION`, or `MUSE_AGENT` is set.
- `unknown` when none of those are set. Runtime then uses the template adapter.

The choice is written to `$AGENTDRIVE_HOME/host.yaml`. If both hosts match, nothing is written and the command asks you to choose. Set one yourself with `--adapter grokbot`, `--adapter muse`, `--adapter template`, or `--adapter unknown`, with `AGENTDRIVE_ADAPTER`, or with `source: override` in `config/host.yaml` (see `config/host.example.yaml`). `--redetect` ignores a saved override and detects again.

## Install

The assistant must show [DISCLAIMER.md](DISCLAIMER.md) and [SECURITY.md](SECURITY.md) in full, then wait. It must not install in that same turn.

After you reply with the acceptance sentence:

```bash
PYTHONPATH=. python3 -m core gate-present --turn-id 1
PYTHONPATH=. python3 -m core gate-accept --turn-id 2 --statement "I have read, understood, and accept the AgentDrive disclaimer and security reminder"
bash setup/install.sh
```

`gate-present` and `gate-accept` have to use different turn ids. The installer checks the stamp and will not create it.

## What version 0.1 actually does

Working in this version: owner and partner sign-in, rotating token storage (refresh when the access token has under 30 minutes left, with a lock around the rotation), vehicle reads, wake, status with tyre pressures, a read-only `agentdrive check` that does not wake a sleeping car, nearby charging sites (location and stalls only), charging history, charging invoices, signed commands through `tesla-control` (flash, honk, lock, unlock, climate, set temperature, charge start, charge stop, charge limit), `agentdrive update`, the safety gate, doctor, the Grok Bot adapter, the template adapter, and four skills (status, flash lights, climate on or off, morning briefing). There is no trunk or frunk command. Unlock and charging changes still need a one-time confirmation.

Not built yet, and present only as stubs: onboarding, trip advisor, charging tips, news watch, and the Muse adapter. Field names on the charging reads are marked unconfirmed until they are compared with a live response. There is no live Supercharger pricing. The business-only charging sessions route is refused.

## Layout

`config/` holds hosts, scopes, and generic vehicle capabilities. `core/fleet` talks to the API. `core/safety` enforces park, confirmation, and the install gate. `core/profile` stores suburb-level habits. `adapters/` swaps the assistant. `setup/` installs, publishes the public key, and runs doctor.

## Contact

agentdrivedev@gmail.com

## Reporting

Ordinary problems go to [GitHub Issues](https://github.com/agentdrive-dev/agentdrive/issues). Vulnerabilities go through [private vulnerability reporting](https://github.com/agentdrive-dev/agentdrive/security/advisories/new). Reports are reviewed and fixed promptly. Do not include secrets, a VIN, or a live location.

## License

MIT License, Copyright (c) 2026 AgentDrive contributors. See [LICENSE](LICENSE). The MuseDrive MIT notice is kept, unchanged, in [NOTICE](NOTICE). MuseDrive is at <https://github.com/ukhhh/musedrive>.
