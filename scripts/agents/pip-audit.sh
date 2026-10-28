#!/usr/bin/env bash
# pip-audit on the interpreter's installed environment: the pinned requirements AND everything they
# pulled in transitively, which a `-r requirements.txt --no-deps` audit would miss. Ignored ids come
# from .github/pip-audit-ignore.txt, one per line, each with the issue that removes it.
# Run after the requirements are installed (scripts/agents/bootstrap.sh locally; CI installs them).
. "$(dirname "$0")/lib.sh"
cd "$repo_root"
bin="${PIP_AUDIT:-}"
[ -n "$bin" ] && command -v "$bin" >/dev/null 2>&1 || die "pip-audit not found (scripts/agents/bootstrap.sh installs it)"
"$PYTHON" -c 'import django' 2>/dev/null || die "the requirements are not installed for $PYTHON; run scripts/agents/bootstrap.sh"
args=()
while IFS= read -r line; do
  line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
  [ -n "$line" ] && args+=(--ignore-vuln "$line")
done < .github/pip-audit-ignore.txt
log "pip-audit on the environment of $PYTHON (ignores: ${#args[@]} entries from .github/pip-audit-ignore.txt)"
# pip-audit audits the environment of the interpreter it runs under; the one next to $PYTHON is
# that environment. A pip-audit elsewhere on PATH would audit its own.
exec "$bin" --progress-spinner off ${args[@]+"${args[@]}"}
