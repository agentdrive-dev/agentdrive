# Security

Read this file, and `DISCLAIMER.md`, before you install AgentDrive. An assistant must show both documents in full and then wait. Installation starts only after you confirm them in a later message. A vague reply does not count.

AgentDrive is unofficial. It is not affiliated with, endorsed by, or supported by Tesla.

## Your own vehicle

Use this toolkit on a vehicle you own, or one you are explicitly allowed to control, with your own developer application, your own domain, and your own key pair. Do not point it at someone else's vehicle, do not collect other people's tokens or keys, and do not run it as a service for other people.

Turn on multi-factor authentication for the vehicle account, the code host that publishes your public key, and the assistant account. Review third-party access and the keys enrolled on the vehicle, and remove anything you do not recognise.

## Secrets stay off this repository

Never commit or paste a private key, client secret, access token, refresh token, authorization code, VIN, license plate, or live location. Each owner generates a new key pair and registers a new application. Whoever holds the private key and a valid token can act on the vehicle within the approved scopes.

Local secret files should be readable only by your user. The doctor command checks that the private-key file is present and, on macOS and Linux, not group- or world-readable. On Windows, limit the file ACL to your user. Unix mode bits are not how Windows records that restriction. The token store writes the replacement refresh token with an atomic rename before the previous value is dropped from memory.

A private key that is already paired with another agent you trust can be the key AgentDrive uses. That is one signing key for every program that can read the file. A second copy is a second secret. The token file rotates: keep one file and one writer, because a refresh replaces the refresh token and two writers can invalidate each other.

## Commands

AgentDrive refuses a state-changing command unless the vehicle reports shift state Park and speed 0. It then reads the vehicle again. If that read does not match the command you asked for, the result is a failure, and the command is not sent a second time.

These actions are sensitive: unlock, remote start, front trunk, rear trunk, sentry mode off, and any charging change. Each one needs an explicit confirmation for that action. A previous "yes" does not carry over. A schedule cannot confirm them and cannot skip the check. These checks also apply if such commands are added later; this version has no trunk, frunk, or remote-start command.

Remote commands that can unlock a vehicle create a real theft risk if the credentials leak. Do not leave valuables in the vehicle. After an unlock, confirm that the vehicle is locked again. Prefer leaving sentry mode on when the vehicle is parked somewhere unfamiliar.

## If something looks wrong

Stop sending commands. Revoke the application's access on the vehicle account, delete the enrolled key on the vehicle, rotate the developer credentials, and delete the local secret files. Treat an unlock, key, or application you do not recognise as a reason to check the vehicle before you use AgentDrive again.

## Reporting

Reported violations and other problems are reviewed and fixed promptly.

- Ordinary bugs and safety problems: open a GitHub Issue on this repository. Do not include secrets, a VIN, a license plate, or a live location.
- Vulnerabilities: use GitHub private vulnerability reporting at <https://github.com/agentdrive-dev/agentdrive/security/advisories/new>.
- Email: agentdrivedev@gmail.com

Do not send credentials to GitHub Issues, private vulnerability reporting, or email.

## Install gate

The assistant presents `DISCLAIMER.md` and this file, then stops. In a later turn you confirm with this sentence:

`I have read, understood, and accept the AgentDrive disclaimer and security reminder`

Only after that separate confirmation may the assistant record acceptance and run `setup/install.sh`. The installer checks the acceptance stamp and will not create it for you.
