# Troubleshooting

[Back to the checklist](00-before-you-start.md)

Doctor checks four things, then the adapter:

```bash
python -m core doctor --secrets-dir ~/.config/agentdrive --public-key-url "https://<project>.pages.dev/.well-known/appspecific/com.tesla.3p.public-key.pem"
```

| Check | Pass means | If it fails |
| --- | --- | --- |
| config | `config/regions.yaml` and `config/vehicles.yaml` load, with North America and Europe only | Fix the YAML. Do not add a China host. |
| private_key | `private-key.pem` exists and is not group-readable | Move it to the home directory and `chmod 600` it. |
| public_key | The HTTPS URL returns a public key | Re-run the publish script. A 404 means the well-known path is wrong. |
| token | The access token is still in date, or the refresh token is | Sign in again only when the refresh token is dead. |
| adapter | A single host was detected or you set an override | If it says ambiguous, re-run with `--adapter`. |

Other stops:

- Installer exit 2: the disclaimer was not accepted in a separate turn.
- Installer exit 3: Grok Bot and Muse signals are both set.
- A command exits non-zero: do not send it again until a read shows the car's state.
- Partner registration says the domain is invalid: Tesla has not documented `pages.dev` as accepted. Keep the error. This guide has no second host.
- The car is not in Park or the speed is missing: the command is refused before `tesla-control` runs.
- `agentdrive check` exits 0 when the car is asleep. That is not a failure. It did not wake the car. Exit 1 means an alert. Exit 2 means the read itself failed.
- `unauthorized_client` from the token endpoint, after a hand-written `curl -d`, often means the body was not encoded. A client secret may contain `&`. `curl -d` sends the body as-is, so that character starts another field and the token endpoint rejects the client. Encode every field (`curl --data-urlencode` once per field). AgentDrive's client does this. Do not put the secret in the command line of a process list, and do not paste it into chat.
- The browser says Not found on the redirect page after you approve scopes. That is expected. The authorization code is in the address bar. Copy the whole address into a mode 600 file. Do not paste it into chat.
- The pairing link `https://tesla.com/_ak/agentdrive.example.com` (use the hostname you registered) must be opened in the phone's browser, not an in-app browser, with the Tesla app installed, while you are near the car. An in-app browser often fails to hand the link to the app.
- To see whether the key is paired, send `POST /api/1/vehicles/fleet_status` with a JSON body `{"vins":["<vin>"]}`. Read the access token from the mode 600 token file inside the program. Do not put the token or the VIN on a command line or in chat. A VIN listed under `unpaired_vins` still needs the phone step. A VIN listed under `key_paired_vins` has the key. Mask the VIN in anything you print.

## Expected result

Doctor prints `pass` for config, the private key, the public key, and the token, plus one adapter line that names `grokbot`, `muse`, `template`, or `unknown`.
