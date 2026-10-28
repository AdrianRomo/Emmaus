#!/usr/bin/env bash
# Regression tests for the agent tooling's failure boundaries (docs/agents/quality-gates.md).
# Mocked commands only; no network, no Codex, no browser. Needs the application requirements
# (Django) and ruff for the real fast gate steps. Usage: scripts/agents/tests/test-tooling.sh
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"; agents="$(dirname "$here")"; root="$(cd "$agents/../.." && pwd)"
. "$agents/lib.sh"
set +e   # lib.sh enables errexit; a test harness must observe failures, not die on them
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
export EMMAUS_AGENT_HOME="$tmp/home"
pass=0; fail=0
ok()   { pass=$((pass + 1)); echo "ok   $1"; }
bad()  { fail=$((fail + 1)); echo "FAIL $1"; [ -n "${2:-}" ] && sed 's/^/     | /' <<<"$2"; }
fake() { # <name> <script body>
  mkdir -p "$tmp/bin"; printf '#!/bin/sh\n%s\n' "$2" >"$tmp/bin/$1"; chmod +x "$tmp/bin/$1"
}

# 1. A failing step gives overall FAIL and exit 1, and the summary lists every step.
fake pip-audit 'echo "No known vulnerabilities found"; exit 1'
out="$(cd "$root" && PIP_AUDIT="$tmp/bin/pip-audit" "$agents/quality-gate.sh" --skip-tests 2>/dev/null)"; rc=$?
if [ "$rc" -eq 1 ] && grep -q "^overall: FAIL" <<<"$out" && [ "$(grep -cE '^(lint|check|migrations|deploy-check|pip-audit|tests-sqlite|tests-postgres|e2e) ' <<<"$out")" -eq 8 ]; then
  ok "gate: failing step -> overall FAIL, exit 1, 8 step lines"; else bad "gate: failing step" "rc=$rc"$'\n'"$out"; fi

# 2. A passing step whose log matches none of the summary patterns does not truncate the summary.
fake pip-audit 'echo "nothing here matches the summary grep"; exit 0'
out="$(cd "$root" && PIP_AUDIT="$tmp/bin/pip-audit" "$agents/quality-gate.sh" --skip-tests 2>/dev/null)"; rc=$?
if [ "$rc" -eq 0 ] && grep -q "^overall: PASS" <<<"$out" && grep -q "^e2e " <<<"$out"; then
  ok "gate: unmatched log text keeps the summary complete (PASS, exit 0)"; else bad "gate: unmatched log text" "rc=$rc"$'\n'"$out"; fi

# 3. codex-review.sh: a pre-existing approval must not survive a failing Codex run.
# The script reviews the checkout it lives in, so the test repo gets its own copy of scripts/agents.
git -C "$tmp" init -q repo && cd "$tmp/repo" && mkdir -p scripts && cp -r "$agents" scripts/agents && rm -rf scripts/agents/tests \
  && git add scripts && git -c user.name=t -c user.email=t@t commit -q -m base && git branch -q base \
  && echo x >f && git add f && git -c user.name=t -c user.email=t@t commit -q -m change
review="$tmp/repo/scripts/agents/codex-review.sh"
fake codex 'echo "ERROR: boom" >&2; exit 1'
printf '{"verdict":"approve","summary":"old","checked":[],"findings":[]}' >"$tmp/stale.json"
out="$(PATH="$tmp/bin:$PATH" PYTHON="$PYTHON" "$review" --base base --out "$tmp/stale.json" 2>&1)"; rc=$?
if [ "$rc" -eq 1 ] && [ ! -e "$tmp/stale.json" ]; then ok "codex-review: failing codex removes the stale verdict, exit 1"; else bad "codex-review: stale verdict" "rc=$rc exists=$([ -e "$tmp/stale.json" ] && echo yes || echo no)"$'\n'"$out"; fi

# 4. codex-review.sh: a valid verdict is accepted with the exit code of its verdict; invalid JSON is rejected.
fake codex 'o=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && o="$2"; shift; done; printf "{\"verdict\":\"request_changes\",\"summary\":\"s\",\"checked\":[\"c\"],\"findings\":[{\"severity\":\"low\",\"file\":\"f\",\"line\":1,\"title\":\"t\",\"evidence\":\"e\",\"suggestion\":\"g\"}]}" >"$o"'
out="$(PATH="$tmp/bin:$PATH" PYTHON="$PYTHON" "$review" --base base --out "$tmp/v.json" 2>&1)"; rc=$?
if [ "$rc" -eq 2 ] && grep -q "verdict=request_changes" <<<"$out"; then ok "codex-review: valid request_changes verdict -> exit 2"; else bad "codex-review: valid verdict" "rc=$rc"$'\n'"$out"; fi
fake codex 'o=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && o="$2"; shift; done; echo "not json" >"$o"'
out="$(PATH="$tmp/bin:$PATH" PYTHON="$PYTHON" "$review" --base base --out "$tmp/bad.json" 2>&1)"; rc=$?
if [ "$rc" -eq 1 ] && [ ! -e "$tmp/bad.json" ]; then ok "codex-review: invalid JSON rejected, exit 1"; else bad "codex-review: invalid JSON" "rc=$rc"; fi
cd "$root"

# 5. run-tests.sh: backend guards, and sqlite ignores an inherited PostgreSQL URL.
"$agents/run-tests.sh" postgres >/dev/null 2>&1; [ $? -eq 1 ] && ok "run-tests: postgres without DATABASE_URL refused" || bad "run-tests: postgres without URL"
DATABASE_URL=sqlite:///x "$agents/run-tests.sh" postgres >/dev/null 2>&1; [ $? -eq 1 ] && ok "run-tests: postgres with a sqlite URL refused" || bad "run-tests: postgres with sqlite URL"
if DATABASE_URL=postgres://nobody@127.0.0.1:1/nowhere "$agents/run-tests.sh" sqlite core/test_locale.py -q -k form_field >"$tmp/sqlite.log" 2>&1; then
  ok "run-tests: sqlite ignores an inherited postgres:// URL"; else bad "run-tests: sqlite inherited URL" "$(tail -n 3 "$tmp/sqlite.log")"; fi

# 6. check-deploy.sh accepts only the documented warnings on this tree.
out="$("$agents/check-deploy.sh" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && grep -q "only accepted warnings" <<<"$out"; then ok "check-deploy: documented warnings only"; else bad "check-deploy" "rc=$rc"$'\n'"$out"; fi

# 7. audit-log.sh records the branch of the directory it is called from, not the script's checkout.
git -C "$tmp" init -q other && (cd "$tmp/other" && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m x && git checkout -q -b v2/issue-999-probe)
line="$(cd "$tmp/other" && "$agents/audit-log.sh" --issue 999 --action note --detail probe)"
if grep -q '"branch": "v2/issue-999-probe"' <<<"$line"; then ok "audit-log: branch of the caller's checkout"; else bad "audit-log: branch" "$line"; fi

# 8. status-report.py: CI state classification.
out="$("$PYTHON" - "$agents/status-report.py" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("sr", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
cases = [("success", [{"conclusion": "SUCCESS"}, {"state": "SUCCESS"}, {"conclusion": "SKIPPED"}]),
         ("failure", [{"conclusion": "SUCCESS"}, {"conclusion": "STARTUP_FAILURE"}]),
         ("failure", [{"conclusion": "STALE"}]), ("failure", [{"conclusion": "TIMED_OUT"}]),
         ("failure", [{"conclusion": "WHATEVER_NEW"}]),
         ("pending", [{"conclusion": "SUCCESS"}, {"status": "IN_PROGRESS"}]), ("none", [])]
bad = [(w, c, m.ci_state({"statusCheckRollup": c})) for w, c in cases if m.ci_state({"statusCheckRollup": c}) != w]
print("bad:", bad) if bad else print("all ok")
PY
)"
[ "$out" = "all ok" ] && ok "status-report: ci_state table (unknown conclusion = failure)" || bad "status-report: ci_state" "$out"

echo "tooling tests: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
