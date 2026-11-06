# Troubleshooting

[Back to the checklist](00-before-you-start.md)

`agentdrive doctor` (the same command as `python -m core doctor`) checks four things, then the adapter:

```bash
python -m core doctor --secrets-dir ~/.config/agentdrive --public-key-url "https://<username>.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem"
```

| Check | Pass means | If it fails |
| --- | --- | --- |
| config | `config/regions.yaml` and `config/vehicles.yaml` load, with North America and Europe only | Fix the YAML. Do not add a China host. |
| private_key | The private key file exists and, on macOS and Linux, is not group-readable | Set `AGENTDRIVE_KEY_FILE` or `key_file` in `$AGENTDRIVE_HOME/runtime.yaml`. A placeholder Windows path is `C:\Users\<you>\agentdrive-key\private-key.pem`. On macOS or Linux, `chmod 600` the file. On Windows, limit the file ACL to your user. |
| public_key | The HTTPS URL returns a public key | Re-run the publish script. A 404 means the well-known path is wrong. HTTP 403 can mean a CDN or bot-protection rule is blocking non-browser clients. |
| token | The access token is still in date, or the refresh token is | Sign in again only when the refresh token is dead. |
| adapter | A single host was detected or you set an override | If it says ambiguous, re-run with `--adapter`. |

Other stops:

- The public key check returns HTTP 403 while curl, with its default user agent, returns 200. A CDN or bot-protection rule may be blocking non-browser clients. The default Python user agent `Python-urllib` is often blocked. AgentDrive sends `AgentDrive/<version> (+https://github.com/agentdrive-dev/agentdrive)` on every HTTP request it makes. If 403 remains, allow that user agent or exempt the well-known path. Hosts in this guide are placeholders, such as `https://<username>.github.io`.
- Installer exit 2: the disclaimer was not accepted in a separate turn.
- Installer exit 3: Grok Bot and Muse signals are both set.
- A command exits non-zero: do not send it again until a read shows the car's state.
- Partner registration says the domain is invalid: keep the error. The key has to be at the domain root (`https://<username>.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem`). A project subpath does not work. Any other HTTPS host at a domain root, for example a custom domain, can serve the same file.
- The car is not in Park or the speed is missing: the command is refused before `tesla-control` runs.
- `agentdrive check` exits 0 when the car is asleep. That is not a failure. It did not wake the car. Exit 1 means an alert. Exit 2 means the read itself failed.
- `agentdrive where` exits 0 when the car is asleep or offline. It did not wake the car. `area unavailable` means the coarse area could not be resolved, and that line never includes coordinates. A line that says to re-authorize with the `vehicle_location` scope means the token, or the firmware, refused the location read. Sign in again and include that scope. Do not paste a coordinate into the issue.
- `unauthorized_client` from the token endpoint, after a hand-written `curl -d`, often means the body was not encoded. A client secret may contain `&`. `curl -d` sends the body as-is, so that character starts another field and the token endpoint rejects the client. Encode every field (`curl --data-urlencode` once per field). AgentDrive's client does this. Do not put the secret in the command line of a process list, and do not paste it into chat.
- The browser says Not found on the redirect page after you approve scopes. That is expected. The authorization code is in the address bar. Copy the whole address into a mode 600 file. Do not paste it into chat.
- The pairing link `https://tesla.com/_ak/your-username.github.io` (the hostname you published) must be opened in the phone's browser, not an in-app browser, with the Tesla app installed, while you are near the car. An in-app browser often fails to hand the link to the app.
- To see whether the key is paired, send `POST /api/1/vehicles/fleet_status` with a JSON body `{"vins":["<vin>"]}`. Read the access token from the mode 600 token file inside the program. Do not put the token or the VIN on a command line or in chat. A VIN listed under `unpaired_vins` still needs the phone step. A VIN listed under `key_paired_vins` has the key. Mask the VIN in anything you print.

## Expected result

Doctor prints `pass` for config, the private key, the public key, and the token, plus one adapter line that names `grokbot`, `muse`, `template`, or `unknown`.
