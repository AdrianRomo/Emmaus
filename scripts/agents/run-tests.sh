#!/usr/bin/env bash
# The test suite on one backend, the way CI runs it.
#   run-tests.sh sqlite              ignores any inherited DATABASE_URL (SQLite is the default backend)
#   run-tests.sh postgres            needs a postgres:// DATABASE_URL; applies scripts/agents/postgres-deselect.txt
# Extra arguments go to pytest.
. "$(dirname "$0")/lib.sh"
backend="${1:-}"; shift || true
cd "$repo_root"
case "$backend" in
  sqlite)
    exec env -u DATABASE_URL DEBUG=true "$PYTHON" -m pytest -q -p no:cacheprovider --ignore=e2e "$@"
    ;;
  postgres)
    case "${DATABASE_URL:-}" in
      postgres://*|postgresql://*) ;;
      *) die "run-tests.sh postgres needs a postgres:// DATABASE_URL (got '${DATABASE_URL:-unset}')";;
    esac
    args=()
    while IFS= read -r line; do
      line="${line%%#*}"; line="$(printf '%s' "$line" | tr -d '[:space:]')"
      [ -n "$line" ] && args+=(--deselect "$line")
    done < "$agents_dir/postgres-deselect.txt"
    [ ${#args[@]} -gt 0 ] && log "postgres: deselecting $(( ${#args[@]} / 2 )) node id(s) from scripts/agents/postgres-deselect.txt"
    exec env DEBUG=true "$PYTHON" -m pytest -q -p no:cacheprovider --ignore=e2e ${args[@]+"${args[@]}"} "$@"
    ;;
  *) die "usage: $0 sqlite|postgres [pytest args]";;
esac
