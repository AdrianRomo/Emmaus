#!/usr/bin/env bash
# pip-audit on the selected interpreter's installed environment: the pinned requirements AND
# everything they pulled in transitively. It always runs as `$PYTHON -m pip_audit`, so it can only
# audit the environment the gate tests against, never a pip-audit that happens to be on PATH.
# Ignored ids come from .github/pip-audit-ignore.txt, one per line, each with the issue that
# removes it. Run after scripts/agents/bootstrap.sh (CI installs the same packages).
. "$(dirname "$0")/lib.sh"
cd "$repo_root"
"$PYTHON" -c 'import pip_audit' 2>/dev/null || die "pip_audit is not installed for $PYTHON; run scripts/agents/bootstrap.sh"
"$PYTHON" -c 'import django' 2>/dev/null || die "the requirements are not installed for $PYTHON; run scripts/agents/bootstrap.sh"
args=()
while IFS= read -r line; do
  line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
  [ -n "$line" ] && args+=(--ignore-vuln "$line")
done < .github/pip-audit-ignore.txt
log "pip-audit on the environment of $PYTHON (ignores: $(( ${#args[@]} / 2 )) entries from .github/pip-audit-ignore.txt)"
exec "$PYTHON" -m pip_audit --progress-spinner off ${args[@]+"${args[@]}"}
