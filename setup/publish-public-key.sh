#!/usr/bin/env bash
# Publish the public key to a GitHub Pages user or organization site.
# The reader approves `gh auth login` once before this script runs.
# The repository must be named <owner>.github.io so the key is served at the
# domain root. A project site subpath is refused: Tesla fetches the key from
# https://<domain>/.well-known/appspecific/com.tesla.3p.public-key.pem.
# An existing site is left in place. This script adds the public key and, if
# it is missing, an empty .nojekyll file. It does not replace other files.
# The private key is never copied. Work happens outside this git checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$ROOT/setup/public-key-site"
OWNER=""
PUBLIC_KEY=""

usage() {
  echo "Usage: setup/publish-public-key.sh --owner <username> --public-key <public-key.pem>" >&2
  echo "The site repository is <username>.github.io. A project subpath does not work." >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --owner)
      OWNER="${2:-}"
      shift 2
      ;;
    --public-key)
      PUBLIC_KEY="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$OWNER" || -z "$PUBLIC_KEY" ]]; then
  usage
  exit 2
fi

OWNER="${OWNER#https://}"
OWNER="${OWNER#http://}"
OWNER="${OWNER%/}"
if [[ "$OWNER" == */* ]]; then
  echo "A project subpath does not work. Tesla reads the key at the domain root. Use a repository named <username>.github.io." >&2
  exit 2
fi
if [[ "$OWNER" == *.github.io ]]; then
  OWNER="${OWNER%.github.io}"
fi
LOWER="$(printf '%s' "$OWNER" | tr '[:upper:]' '[:lower:]')"
if [[ "$OWNER" != "$LOWER" ]]; then
  echo "Use the lowercase GitHub login. Tesla requires a lowercase domain." >&2
  exit 2
fi
if [[ "$OWNER" == *tesla* ]]; then
  echo "Choose an account name that does not contain tesla." >&2
  exit 2
fi
if [[ ! "$OWNER" =~ ^[a-z0-9]([a-z0-9-]{0,37}[a-z0-9])?$ ]]; then
  echo "Account name must be lowercase letters, numbers, and hyphens." >&2
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

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required. Install it, run gh auth login, and approve it once." >&2
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI is not logged in. Run gh auth login and approve it once, then re-run this script." >&2
  exit 1
fi

REPO="${OWNER}.github.io"
URL="https://${OWNER}.github.io/.well-known/appspecific/com.tesla.3p.public-key.pem"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
SITE="$WORKDIR/site"

clone_or_create() {
  local view_err clone_err
  view_err="$(mktemp -p "$WORKDIR")"
  if gh repo view "${OWNER}/${REPO}" >/dev/null 2>"$view_err"; then
    clone_err="$(mktemp -p "$WORKDIR")"
    if gh repo clone "${OWNER}/${REPO}" "$SITE" -- --depth 1 2>"$clone_err"; then
      return 0
    fi
    if grep -qi "empty" "$clone_err"; then
      mkdir -p "$SITE"
      git -C "$SITE" init -b main
      git -C "$SITE" remote add origin "https://github.com/${OWNER}/${REPO}.git"
      return 0
    fi
    cat "$clone_err" >&2
    exit 1
  fi
  if ! gh repo create "${OWNER}/${REPO}" --public --description "Public key host for a personal vehicle command setup." >/dev/null; then
    cat "$view_err" >&2
    echo "Could not create ${OWNER}/${REPO}." >&2
    exit 1
  fi
  mkdir -p "$SITE"
  git -C "$SITE" init -b main
  git -C "$SITE" remote add origin "https://github.com/${OWNER}/${REPO}.git"
}

clone_or_create

configure_git() {
  local login id email
  login="$(gh api user --jq .login)"
  id="$(gh api user --jq .id)"
  email="$(printf '%s+%s@%s' "$id" "$login" "users.noreply.github.com")"
  git config user.name "$login"
  git config user.email "$email"
  git config credential.helper ""
  git config --add credential.helper '!gh auth git-credential'
}

cd "$SITE"
configure_git

HAS_SITE=0
if git rev-parse --verify HEAD >/dev/null 2>&1; then
  HAS_SITE=1
fi

pages_branch="$(gh api "repos/${OWNER}/${REPO}/pages" --jq '.source.branch // empty' 2>/dev/null || true)"
if [[ "$HAS_SITE" == "1" && -n "$pages_branch" && "$pages_branch" != "null" ]]; then
  current="$(git rev-parse --abbrev-ref HEAD)"
  if [[ "$pages_branch" != "$current" ]]; then
    git fetch origin "$pages_branch"
    git checkout "$pages_branch"
  fi
fi

mkdir -p .well-known/appspecific
cp "$PUBLIC_KEY" .well-known/appspecific/com.tesla.3p.public-key.pem
if [[ ! -e .nojekyll ]]; then
  cp "$TEMPLATE/.nojekyll" .nojekyll
fi
if [[ "$HAS_SITE" == "0" ]]; then
  cp "$TEMPLATE/index.html" index.html
  mkdir -p callback
  cp "$TEMPLATE/callback/index.html" callback/index.html
fi

git add -- .nojekyll .well-known/appspecific/com.tesla.3p.public-key.pem
if [[ "$HAS_SITE" == "0" ]]; then
  git add -- index.html callback/index.html
fi

if git diff --cached --quiet; then
  echo "Public key file is already in ${REPO}."
else
  git commit -m "Add the vehicle public key."
  git push -u origin HEAD
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if ! gh api "repos/${OWNER}/${REPO}/pages" >/dev/null 2>&1; then
  if ! gh api --method POST "repos/${OWNER}/${REPO}/pages" \
    -f build_type=legacy \
    -f "source[branch]=${BRANCH}" \
    -f "source[path]=/" >/dev/null; then
    echo "GitHub Pages is not enabled for ${REPO}. Turn it on for the ${BRANCH} branch, folder / (root)." >&2
    exit 1
  fi
fi

BODY="$WORKDIR/curl-body"
STATUS="000"
ATTEMPT=1
while [[ "$ATTEMPT" -le 12 ]]; do
  STATUS="$(curl -sS -o "$BODY" -w "%{http_code}" -L --max-redirs 5 "$URL" || true)"
  if [[ "$STATUS" == "200" ]] && grep -q "BEGIN PUBLIC KEY" "$BODY" && ! grep -q "PRIVATE KEY" "$BODY"; then
    break
  fi
  sleep 5
  ATTEMPT=$((ATTEMPT + 1))
done

if [[ "$STATUS" != "200" ]]; then
  echo "Public key URL returned HTTP ${STATUS}. GitHub Pages can take a few minutes. URL: ${URL}" >&2
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
