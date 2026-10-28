#!/usr/bin/env bash
# One-time setup of a checkout for the gate: a virtualenv with the application requirements plus
# the pinned gate tools (ruff, pip-audit). Routines, fresh clones and humans run this first.
# Usage: scripts/agents/bootstrap.sh [venv-dir]   (default .venv; prints the interpreter to use)
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
venv="${1:-$root/.venv}"
[ -x "$venv/bin/python" ] || python3 -m venv "$venv"
"$venv/bin/pip" install --quiet --disable-pip-version-check -r "$root/requirements.txt" ruff==0.17.2 pip-audit==2.9.0
echo "ready: PYTHON=$venv/bin/python (ruff $("$venv/bin/ruff" --version | cut -d' ' -f2), pip-audit $("$venv/bin/pip-audit" --version | cut -d' ' -f2))"
