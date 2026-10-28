#!/usr/bin/env bash
# The deterministic gate: the same steps CI runs (docs/agents/quality-gates.md), each under a
# timeout, with a summary block to paste into the PR.
# Usage: scripts/agents/quality-gate.sh [--postgres] [--e2e] [--skip-tests] [--out DIR]
#   --postgres   also run the suite on a throwaway postgres:16 container (or $DATABASE_URL if set)
#   --e2e        also run scripts/agents/e2e-gate.sh (needs e2e/node_modules + chromium)
#   --skip-tests only the fast steps (lint, checks, migrations, deploy check, pip-audit)
. "$(dirname "$0")/lib.sh"
postgres=false; e2e=false; skip_tests=false; out=""
while [ $# -gt 0 ]; do case "$1" in
  --postgres) postgres=true;; --e2e) e2e=true;; --skip-tests) skip_tests=true;;
  --out) out="$2"; shift;; -h|--help) sed -n '2,8p' "$0"; exit 0;; *) die "unknown option $1";;
esac; shift; done
cd "$repo_root"
out="${out:-$EMMAUS_AGENT_HOME/gates/$(date -u +%Y%m%dT%H%M%SZ)}"; mkdir -p "$out"
results=()

run_step() { # <name> <timeout-seconds> <command...>
  local name="$1" to="$2"; shift 2
  local logf="$out/$name.log" start end status rc
  start=$(date +%s)
  if timeout "$to" "$@" >"$logf" 2>&1; then status=PASS; else
    rc=$?; status=FAIL; [ "$rc" -eq 124 ] && status="FAIL(timeout)"
  fi
  end=$(date +%s)
  results+=("$name|$status|$((end - start))|$logf")
  log "$name: $status ($((end - start))s)"
}
skip_step() { results+=("$1|SKIP|0|$2"); log "$1: SKIP ($2)"; }

ruff_cmd=("$PYTHON" -m ruff)
"${ruff_cmd[@]}" --version >/dev/null 2>&1 || { command -v ruff >/dev/null 2>&1 && ruff_cmd=(ruff); }
run_step lint 120 "${ruff_cmd[@]}" check .
run_step check 180 env DEBUG=true "$PYTHON" manage.py check
run_step migrations 180 env DEBUG=true "$PYTHON" manage.py makemigrations --check --dry-run
run_step deploy-check 180 "$agents_dir/check-deploy.sh"
if command -v "${PIP_AUDIT:-pip-audit}" >/dev/null 2>&1; then
  run_step pip-audit 300 "$agents_dir/pip-audit.sh"
else
  skip_step pip-audit "pip-audit not installed (pip install pip-audit==2.9.0)"
fi

if $skip_tests; then
  skip_step tests-sqlite "--skip-tests"; skip_step tests-postgres "--skip-tests"
else
  # The label names the backend: SQLite ignores any inherited DATABASE_URL; PostgreSQL needs a postgres:// URL.
  run_step tests-sqlite 900 env -u DATABASE_URL DEBUG=true "$PYTHON" -m pytest -q -p no:cacheprovider --ignore=e2e
  if ! $postgres; then
    skip_step tests-postgres "--postgres not given"
  elif [ -n "${DATABASE_URL:-}" ]; then
    case "$DATABASE_URL" in
      postgres://*|postgresql://*) run_step tests-postgres 1200 env DEBUG=true "$PYTHON" -m pytest -q -p no:cacheprovider --ignore=e2e;;
      *) die "--postgres needs a postgres:// DATABASE_URL (got scheme ${DATABASE_URL%%:*}); unset it to use a throwaway container";;
    esac
  else
    need docker
    pg_port="$("$PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
    pg_name="emmaus-gate-pg-$$"
    docker run -d --rm --name "$pg_name" --tmpfs /var/lib/postgresql/data \
      -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_USER=emmaus -e POSTGRES_DB=emmaus \
      -p "127.0.0.1:$pg_port:5432" postgres:16 >/dev/null
    trap 'docker stop "$pg_name" >/dev/null 2>&1 || true' EXIT
    for i in $(seq 1 60); do docker exec "$pg_name" pg_isready -U emmaus -d emmaus >/dev/null 2>&1 && break; sleep 1; done
    run_step tests-postgres 1200 env DEBUG=true DATABASE_URL="postgres://emmaus@127.0.0.1:$pg_port/emmaus" \
      "$PYTHON" -m pytest -q -p no:cacheprovider --ignore=e2e
    docker stop "$pg_name" >/dev/null 2>&1 || true; trap - EXIT
  fi
fi
if $e2e; then run_step e2e 1500 env E2E_OUT="$out/e2e" "$agents_dir/e2e-gate.sh"; else skip_step e2e "--e2e not given"; fi

# The verdict is computed before the summary is printed, and nothing inside the summary block can
# abort it (a grep with no match must not hide a FAIL line under `set -e`).
overall=PASS
for r in "${results[@]}"; do
  IFS='|' read -r _ status _ _ <<<"$r"
  case "$status" in FAIL*) overall=FAIL;; esac
done
{
  echo "GATE SUMMARY  $(git branch --show-current 2>/dev/null || echo detached) @ $(git rev-parse --short HEAD)  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '%-15s %-14s %6s  %s\n' step status secs detail
  for r in "${results[@]}"; do
    IFS='|' read -r name status secs detail <<<"$r"
    tail_line=""
    if [ -f "$detail" ]; then
      tail_line="$(grep -iE "passed|failed|error|ok|found|all checks|no changes|identified|vulnerab" "$detail" | tail -n 1 | cut -c1-90 || true)"
    fi
    printf '%-15s %-14s %6s  %s\n' "$name" "$status" "$secs" "${tail_line:-$detail}"
  done
  echo "overall: $overall   logs: $out"
} | tee "$out/summary.txt"
[ "$overall" = PASS ]
