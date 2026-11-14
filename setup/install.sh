#!/usr/bin/env bash
# Install AgentDrive after a separate disclaimer acceptance.
# This script does not present the disclaimer and does not accept it.
# `go install ...@latest` cannot build tesla-control: the upstream module
# uses replace directives. Clone the repository and `go build ./cmd/tesla-control`.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export AGENTDRIVE_HOME="${AGENTDRIVE_HOME:-$HOME/.config/agentdrive}"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

ADAPTER_ARGS=()
UPDATE=0
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
    *)
      echo "Unknown argument: $1" >&2
      echo "Usage: setup/install.sh [--adapter grokbot|muse|template|unknown] [--update]" >&2
      exit 2
      ;;
  esac
done

install_vehicle_command() {
  if ! command -v go >/dev/null 2>&1; then
    echo "Go is required to build tesla-control. The upstream module uses replace directives, so this script clones the repository and builds ./cmd/tesla-control. Install Go, then re-run setup/install.sh." >&2
    exit 1
  fi
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
  mkdir -p "$bindir"
  if [[ ! -d "${src}/.git" ]]; then
    rm -rf "$src"
    git clone --depth 1 https://github.com/teslamotors/vehicle-command.git "$src"
  else
    # Drop previous local command insertions so pull can fast-forward.
    git -C "$src" checkout -- cmd/tesla-control/commands.go
    git -C "$src" pull --ff-only
  fi
  python3 "${ROOT}/setup/patch_tesla_control_defrost.py" "${src}/cmd/tesla-control/commands.go"
  python3 "${ROOT}/setup/patch_tesla_control_phase8.py" "${src}/cmd/tesla-control/commands.go"
  (
    cd "$src"
    go build -o "${bindir}/tesla-control" ./cmd/tesla-control
  )
  chmod 755 "${bindir}/tesla-control"
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

if [[ "$UPDATE" == "1" ]]; then
  install_python
  exit 0
fi

python3 -m core gate-check
python3 -m core adapter install "${ADAPTER_ARGS[@]}"
install_python
