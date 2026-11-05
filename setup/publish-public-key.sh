#!/usr/bin/env bash
# Publish the public key site after `npx wrangler login` has already succeeded.
# The private key is never copied. Staging happens outside the git checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$ROOT/setup/public-key-site"
PROJECT=""
PUBLIC_KEY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      PROJECT="${2:-}"
      shift 2
      ;;
    --public-key)
      PUBLIC_KEY="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$PROJECT" || -z "$PUBLIC_KEY" ]]; then
  echo "Usage: setup/publish-public-key.sh --project <name> --public-key <public-key.pem>" >&2
  exit 2
fi

if [[ "$PROJECT" == *tesla* || "$PROJECT" == *TESLA* ]]; then
  echo "Choose a project name that does not contain tesla." >&2
  exit 2
fi

if [[ ! "$PROJECT" =~ ^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$ ]]; then
  echo "Project name must be lowercase letters, numbers, and hyphens." >&2
  exit 2
fi

if [[ ! -f "$PUBLIC_KEY" ]]; then
  echo "Public key file not found." >&2
  exit 2
fi

if grep -q "PRIVATE KEY" "$PUBLIC_KEY"; then
  echo "Refusing to publish a private key." >&2
  exit 2
fi

if ! grep -q "BEGIN PUBLIC KEY" "$PUBLIC_KEY"; then
  echo "The file does not look like a public key." >&2
  exit 2
fi

if [[ "${CHECK_ONLY:-}" == "1" ]]; then
  echo "checks passed"
  exit 0
fi

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
cp -R "$TEMPLATE"/. "$STAGING"/
rm -f "$STAGING/.well-known/appspecific/README.md"
cp "$PUBLIC_KEY" "$STAGING/.well-known/appspecific/com.tesla.3p.public-key.pem"

npx --yes wrangler pages deploy "$STAGING" --project-name "$PROJECT"

URL="https://${PROJECT}.pages.dev/.well-known/appspecific/com.tesla.3p.public-key.pem"
BODY="$(mktemp)"
STATUS="$(curl -sS -o "$BODY" -w "%{http_code}" "$URL")"
if [[ "$STATUS" != "200" ]]; then
  echo "Public key URL returned HTTP $STATUS" >&2
  exit 1
fi
if ! grep -q "BEGIN PUBLIC KEY" "$BODY"; then
  echo "Public key URL did not return a public key." >&2
  exit 1
fi
if grep -q "PRIVATE KEY" "$BODY"; then
  echo "Public key URL returned private key material." >&2
  exit 1
fi

HOME_DIR="${AGENTDRIVE_HOME:-$HOME/.config/agentdrive}"
mkdir -p "$HOME_DIR"
printf "%s\n" "$URL" > "$HOME_DIR/public-key-url"
echo "$URL"
