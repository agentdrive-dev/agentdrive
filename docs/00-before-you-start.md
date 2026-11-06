# Before you start

[Back to the README](../README.md)

## What you need

- A [Tesla account that owns the car](prerequisites/vehicle-account.md).
- A [Tesla that supports virtual keys](prerequisites/vehicle-compatibility.md) (most cars from 2021 on do).
- An [assistant](prerequisites/assistant.md) that can run shell commands on a [computer](prerequisites/computer.md), for example Grok Bot (brings its own computer), Muse, or any shell-capable agent.
- A [Tesla developer account](prerequisites/developer-app.md) with a [payment method and a billing limit above 0](prerequisites/billing.md). It uses the same Tesla login, not a new account. Tesla bills API use. A $10 monthly credit is published for individual developers.
- A [GitHub account](prerequisites/public-key-host.md), used to host the public key on GitHub Pages. You do not buy a domain.

## What you will do

[About an hour, mostly approvals.](prerequisites/time.md)

1. Paste the prompt in [How to start](../README.md#how-to-start) to your assistant. The prompt is on that page in full. Your assistant gets the code and follows [What the assistant runs](../README.md#what-the-assistant-runs).
2. Read the [disclaimer](../DISCLAIMER.md) and the [security reminder](../SECURITY.md) when the assistant shows them, then reply in a separate message with: `I have read, understood, and accept the AgentDrive disclaimer and security reminder`
3. Approve GitHub login once when asked.
4. Create the Tesla developer app and add billing when asked. The assistant tells you what to fill in. Put the secret in a file, never in chat.
5. Sign in to Tesla in your browser when the assistant gives you a link.
6. Approve the key in the Tesla app on your phone. See [Virtual key](prerequisites/virtual-key.md).

Everything else (installing, publishing the key, registering, tokens, doctor, test command) your assistant does and reports back.

## Full step-by-step guide

These pages are the longer version. Your assistant follows them. You only do the six steps above.

1. [Install and safety](01-install-and-safety.md)
2. [Host the public key](02-host-the-public-key.md)
3. [Developer application](03-developer-app.md)
4. [Sign-in and pairing](04-sign-in-and-pairing.md)
5. [Tokens](05-tokens.md)
6. [Queries and commands](06-queries-and-commands.md)
7. [Assistants](07-assistants.md)
8. [Skills](08-skills.md)
9. [Troubleshooting](09-troubleshooting.md)
