#!/usr/bin/env bash
# pip-audit on the pinned requirements (direct pins, no resolution). Ignored ids come from
# .github/pip-audit-ignore.txt, one per line, each with the issue that removes it.
. "$(dirname "$0")/lib.sh"
cd "$repo_root"
bin="${PIP_AUDIT:-}"
[ -n "$bin" ] && command -v "$bin" >/dev/null 2>&1 || die "pip-audit not found (scripts/agents/bootstrap.sh installs it)"
args=()
while IFS= read -r line; do
  line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
  [ -n "$line" ] && args+=(--ignore-vuln "$line")
done < .github/pip-audit-ignore.txt
log "pip-audit on requirements.txt (ignores: ${#args[@]} entries from .github/pip-audit-ignore.txt)"
exec "$bin" -r requirements.txt --no-deps --disable-pip --progress-spinner off ${args[@]+"${args[@]}"}
