# Shared helpers for scripts/agents/*. Source it; do not run it.
set -euo pipefail

agents_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$agents_dir/../.." && pwd)"
export EMMAUS_AGENT_HOME="${EMMAUS_AGENT_HOME:-$HOME/.emmaus-agent}"
mkdir -p "$EMMAUS_AGENT_HOME"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing command: $1"; }

# The checkout that owns .git (a worktree's main checkout, or this directory).
main_checkout() {
  local common
  common="$(git -C "$repo_root" rev-parse --git-common-dir 2>/dev/null || echo .git)"
  common="$(cd "$repo_root" && cd "$common" && pwd)"
  dirname "$common"
}

# Interpreter: $PYTHON, else this checkout's .venv, else the main checkout's .venv, else python3.
find_python() {
  if [ -n "${PYTHON:-}" ]; then printf '%s' "$PYTHON"; return; fi
  if [ -x "$repo_root/.venv/bin/python" ]; then printf '%s' "$repo_root/.venv/bin/python"; return; fi
  local mc; mc="$(main_checkout)"
  if [ -x "$mc/.venv/bin/python" ]; then printf '%s' "$mc/.venv/bin/python"; return; fi
  command -v python3
}
PYTHON="$(find_python)"
export PYTHON
