# Host the public key

[Back to the checklist](00-before-you-start.md)

You do not buy a domain. After one Cloudflare login, the bot publishes the key. The click-by-click version is [Free public-key host](prerequisites/public-key-host.md).

![The bot deploys the site folder and returns a pages.dev URL](images/public-key-host-2.svg)

## What you do

Create a free Cloudflare account and run `npx --yes wrangler login` once. Approve the browser prompt. Then stop.

## What the bot does

```text
1. I have already created a Cloudflare account and finished npx wrangler login.
2. Read docs/prerequisites/public-key-host.md.
3. Generate a P-256 (prime256v1) key pair, the curve Tesla requires, in ~/.config/agentdrive (or $AGENTDRIVE_HOME). Set mode 600 on the private key (readable only by your user; run chmod 600 on the file). Do not print the private key and do not put it in the git repository.
4. Publish only the public key with setup/publish-public-key.sh. Use a lowercase project name that does not contain tesla.
5. Curl the well-known URL. Run python -m core doctor for that URL. Reply with the pages.dev URL and the HTTP status.
6. Stop. Do not create a developer application in this turn.
```

The published URL looks like `https://<project>.pages.dev/.well-known/appspecific/com.tesla.3p.public-key.pem`.

## Whether Tesla accepts that hostname

Tesla's partner docs require a lowercase domain that shares a root with an allowed origin. Their example is `123.abc.com` alongside an origin of `www.abc.com`. The getting-started page lists "Domain name is invalid" as an automatic rejection. Tesla does not say that a `pages.dev` hostname is accepted or rejected. Use the hostname you actually published, and treat the portal's answer as the verdict. This guide does not offer a second host.

## Expected result

`curl` prints a public key and doctor passes the public-key check. The token check stays failed until sign-in.
