#!/usr/bin/env bash
# Install AgentDrive after a separate disclaimer acceptance.
# This script does not present the disclaimer and does not accept it.
# `go install ...@latest` cannot build tesla-control: the upstream module
# uses replace directives. Clone the repository and `go build ./cmd/tesla-control`.
# --update and --rebuild-tool share install_vehicle_command: fetch or
# fast-forward vehicle-command, reapply the local patches, and go build.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export AGENTDRIVE_HOME="${AGENTDRIVE_HOME:-$HOME/.config/agentdrive}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

ADAPTER_ARGS=()
UPDATE=0
REBUILD_TOOL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --adapter)
      ADAPTER_ARGS=(--adapter "${2:-}")
      shift 2
      ;;
    --update)
      # Re-run tooling only. Do not rewrite host.yaml, tokens, keys, or client files.
      UPDATE=1
      shift
      ;;
    --rebuild-tool)
      # Vehicle-command half of --update. Do not touch the virtualenv or private config.
      REBUILD_TOOL=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: setup/install.sh [--adapter grokbot|muse|template|unknown] [--update|--rebuild-tool]" >&2
      exit 2
      ;;
  esac
done

require_go() {
  if ! command -v go >/dev/null 2>&1; then
    echo "Go 1.23 or newer is required to build the car command tool. Install it, then run the command again." >&2
    exit 1
  fi
  local raw major minor
  raw="$(go version 2>/dev/null || true)"
  if [[ "$raw" =~ go([0-9]+)\.([0-9]+) ]]; then
    major="${BASH_REMATCH[1]}"
    minor="${BASH_REMATCH[2]}"
    if (( major > 1 || (major == 1 && minor >= 23) )); then
      return 0
    fi
  fi
  echo "Go 1.23 or newer is required to build the car command tool. Install it, then run the command again." >&2
  exit 1
}

install_vehicle_command() {
  require_go
  if ! command -v git >/dev/null 2>&1; then
    echo "git is required to clone the signed-command tool." >&2
    exit 1
  fi
  local src="${AGENTDRIVE_HOME}/src/vehicle-command"
  local bindir="${AGENTDRIVE_HOME}/bin"
  case "$src" in
    */src/vehicle-command) ;;
    *)
      echo "Refusing to clone outside the source directory." >&2
      exit 1
      ;;
  esac
  local pin=""
  if [[ -f "${ROOT}/setup/vehicle-command.rev" && ! -L "${ROOT}/setup/vehicle-command.rev" ]]; then
    pin="$(awk '{ sub(/#.*/, "") } NF { print $1; exit }' "${ROOT}/setup/vehicle-command.rev")"
  fi
  mkdir -p "$bindir"
  if [[ ! -d "${src}/.git" ]]; then
    rm -rf "$src"
    git clone --depth 1 https://github.com/teslamotors/vehicle-command.git "$src"
  elif [[ -z "$pin" ]]; then
    # Drop previous local command insertions so pull can fast-forward.
    git -C "$src" checkout -- cmd/tesla-control/commands.go
    git -C "$src" pull --ff-only
  fi
  if [[ -n "$pin" ]]; then
    git -C "$src" fetch --depth 1 origin "$pin"
    git -C "$src" checkout --detach --force FETCH_HEAD
  fi
  python3 "${ROOT}/setup/patch_tesla_control_defrost.py" "${src}/cmd/tesla-control/commands.go"
  python3 "${ROOT}/setup/patch_tesla_control_phase8.py" "${src}/cmd/tesla-control/commands.go"
  python3 "${ROOT}/setup/patch_tesla_control_boombox.py" "${src}/cmd/tesla-control/commands.go"
  (
    cd "$src"
    go build -o "${bindir}/tesla-control" ./cmd/tesla-control
  )
  chmod 755 "${bindir}/tesla-control"
  python3 -m core.command_tool write-stamp
  echo "tesla-control installed at ${bindir}/tesla-control"
}

install_python() {
  python3 -m venv "$ROOT/.venv"
  "$ROOT/.venv/bin/pip" install -e "$ROOT"
  install_vehicle_command
  echo "AgentDrive is installed in $ROOT/.venv"
  echo "Activate it with: source $ROOT/.venv/bin/activate"
  echo "Then run: python -m core doctor"
}

if [[ "$REBUILD_TOOL" == "1" ]]; then
  install_vehicle_command
  exit 0
fi

if [[ "$UPDATE" == "1" ]]; then
  install_python
  exit 0
fi

python3 -m core gate-check
python3 -m core adapter install "${ADAPTER_ARGS[@]}"
install_python
