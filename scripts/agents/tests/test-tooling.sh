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

# A fake interpreter that behaves like $PYTHON except for `-m pip_audit`, whose outcome the test sets
# (the gate must run pip-audit through the selected interpreter, never through PATH).
fake_python() { # <name> <pip_audit body>
  mkdir -p "$tmp/bin"
  printf '#!/bin/sh\nif [ "$1" = "-m" ] && [ "$2" = "pip_audit" ]; then %s; fi\nexec "%s" "$@"\n' "$2" "$PYTHON" >"$tmp/bin/$1"
  chmod +x "$tmp/bin/$1"
}

# 1. A failing step gives overall FAIL and exit 1, and the summary lists every step.
fake_python python-audit-fails 'echo "No known vulnerabilities found"; exit 1'
out="$(cd "$root" && ALLOW_CAMP_MIGRATIONS=true PYTHON="$tmp/bin/python-audit-fails" "$agents/quality-gate.sh" --skip-tests 2>/dev/null)"; rc=$?
if [ "$rc" -eq 1 ] && grep -q "^overall: FAIL" <<<"$out" && [ "$(grep -cE '^(lint|check|migrations|deploy-check|migration-scope|pip-audit|tests-sqlite|tests-postgres|e2e) ' <<<"$out")" -eq 9 ]; then
  ok "gate: failing step -> overall FAIL, exit 1, 9 step lines"; else bad "gate: failing step" "rc=$rc"$'\n'"$out"; fi

# 2. A passing step whose log matches none of the summary patterns does not truncate the summary.
fake_python python-audit-quiet 'echo "nothing here matches the summary grep"; exit 0'
out="$(cd "$root" && ALLOW_CAMP_MIGRATIONS=true PYTHON="$tmp/bin/python-audit-quiet" "$agents/quality-gate.sh" --skip-tests 2>/dev/null)"; rc=$?
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

# 2b. pip-audit.sh audits the selected interpreter: a pip-audit on PATH must not stand in for a
#     missing pip_audit module, and the script fails clearly instead.
fake pip-audit 'echo "WRONG ENVIRONMENT AUDITED"; exit 0'
mkdir -p "$tmp/bin"
printf '#!/bin/sh\ncase "$1 $2" in "-c import pip_audit"|"-m pip_audit") echo "No module named pip_audit" >&2; exit 1;; esac\nexec "%s" "$@"\n' "$PYTHON" >"$tmp/bin/python-no-audit"
chmod +x "$tmp/bin/python-no-audit"
out="$(PATH="$tmp/bin:$PATH" PYTHON="$tmp/bin/python-no-audit" "$agents/pip-audit.sh" 2>&1)"; rc=$?
if [ "$rc" -eq 1 ] && grep -q "pip_audit is not installed" <<<"$out" && ! grep -q "WRONG ENVIRONMENT" <<<"$out"; then
  ok "pip-audit: missing module fails clearly; PATH pip-audit is never used"; else bad "pip-audit: environment selection" "rc=$rc"$'\n'"$out"; fi

# 4b. check-migration-scope.sh: a branch that adds a core/ or events/ migration fails; a new app passes;
#     ALLOW_CAMP_MIGRATIONS=true skips; on the base branch itself it is not applicable.
git -C "$tmp" init -q scope && cd "$tmp/scope" && mkdir -p scripts && cp -r "$agents" scripts/agents && rm -rf scripts/agents/tests \
  && mkdir -p core/migrations events/migrations && touch core/migrations/__init__.py events/migrations/__init__.py \
  && git add -A && git -c user.name=t -c user.email=t@t commit -q -m base && git branch -q v2/foundation \
  && git checkout -q -b v2/issue-1-x && mkdir -p people/migrations && echo "# ok" > people/migrations/0001_initial.py \
  && git add -A && git -c user.name=t -c user.email=t@t commit -q -m "new app"
scope="$tmp/scope/scripts/agents/check-migration-scope.sh"
out="$(BASE=v2/foundation "$scope" 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && grep -q "migration scope OK" <<<"$out" && ok "migration-scope: a new app's migration passes" || bad "migration-scope: new app" "rc=$rc"$'\n'"$out"
echo "# camp" > events/migrations/0013_x.py && git add -A && git -c user.name=t -c user.email=t@t commit -q -m "camp migration"
out="$(BASE=v2/foundation "$scope" 2>&1)"; rc=$?
[ "$rc" -eq 1 ] && grep -q "events/migrations/0013_x.py" <<<"$out" && ok "migration-scope: an events/ migration on a v2 branch fails" || bad "migration-scope: events migration" "rc=$rc"$'\n'"$out"
git checkout -q -b v2/issue-2-rename v2/foundation && mkdir -p people/migrations && echo "# moved" > people/migrations/0007_moved.py \
  && git add -A && git -c user.name=t -c user.email=t@t commit -q -m "new app file" \
  && git mv people/migrations/0007_moved.py events/migrations/0007_moved.py && git -c user.name=t -c user.email=t@t commit -q -m "moved into events"
out="$(BASE=v2/foundation "$scope" 2>&1)"; rc=$?
[ "$rc" -eq 1 ] && grep -q "events/migrations/0007_moved.py" <<<"$out" && ok "migration-scope: a file renamed into events/migrations/ is caught" || bad "migration-scope: rename" "rc=$rc"$'\n'"$out"
git checkout -q v2/issue-1-x
out="$(ALLOW_CAMP_MIGRATIONS=true BASE=v2/foundation "$scope" 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && grep -q "skipped" <<<"$out" && ok "migration-scope: ALLOW_CAMP_MIGRATIONS=true skips with a notice" || bad "migration-scope: allow" "rc=$rc"
out="$(BASE=origin/missing-base "$scope" 2>&1)"; rc=$?
[ "$rc" -eq 1 ] && grep -q "not found; fetch it first" <<<"$out" && ok "migration-scope: a missing base (shallow checkout) fails clearly" || bad "migration-scope: missing base" "rc=$rc"$'\n'"$out"
git checkout -q v2/foundation
out="$(BASE=v2/foundation "$scope" 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && grep -q "not applicable" <<<"$out" && ok "migration-scope: not applicable on the base branch" || bad "migration-scope: base branch" "rc=$rc"
cd "$root"

# 5. run-tests.sh: backend guards, and sqlite ignores an inherited PostgreSQL URL.
"$agents/run-tests.sh" postgres >/dev/null 2>&1; [ $? -eq 1 ] && ok "run-tests: postgres without DATABASE_URL refused" || bad "run-tests: postgres without URL"
DATABASE_URL=sqlite:///x "$agents/run-tests.sh" postgres >/dev/null 2>&1; [ $? -eq 1 ] && ok "run-tests: postgres with a sqlite URL refused" || bad "run-tests: postgres with sqlite URL"
if DATABASE_URL=postgres://nobody@127.0.0.1:1/nowhere "$agents/run-tests.sh" sqlite core/test_locale.py -q -k form_field >"$tmp/sqlite.log" 2>&1; then
  ok "run-tests: sqlite ignores an inherited postgres:// URL"; else bad "run-tests: sqlite inherited URL" "$(tail -n 3 "$tmp/sqlite.log")"; fi

# 6. check-deploy.sh accepts only the documented warnings on this tree, passes a check with no
#    warnings at all, and fails on an undocumented id (fake interpreters stand in for manage.py).
out="$("$agents/check-deploy.sh" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && grep -q "only accepted warnings" <<<"$out"; then ok "check-deploy: documented warnings only"; else bad "check-deploy" "rc=$rc"$'\n'"$out"; fi
fake python-clean 'echo "System check identified no issues (0 silenced)."; exit 0'
out="$(PYTHON="$tmp/bin/python-clean" "$agents/check-deploy.sh" 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && grep -q "no warnings" <<<"$out"; then ok "check-deploy: a check with no warnings passes"; else bad "check-deploy: no warnings" "rc=$rc"$'\n'"$out"; fi
fake python-noisy 'echo "?: (security.W999) something new"; echo "?: (security.W005) hsts"; exit 0'
out="$(PYTHON="$tmp/bin/python-noisy" "$agents/check-deploy.sh" 2>&1)"; rc=$?
if [ "$rc" -eq 1 ] && grep -q "unexpected=\[security.W999\]" <<<"$out"; then ok "check-deploy: an undocumented warning fails"; else bad "check-deploy: undocumented warning" "rc=$rc"$'\n'"$out"; fi

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

# 9. status-report.py: the Codex CI verdict (latest review comment for the PR's current head).
out="$("$PYTHON" - "$agents/status-report.py" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("sr", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
H = "a" * 40
def review(sha, verdict, user="github-actions[bot]"):
    return {"user": {"login": user}, "body": f"### Independent review (Codex CLI, read-only) of `{sha}`\n\n```\nCODEX REVIEW  verdict={verdict}  findings: high=0 medium=0 low=0\n```"}
agent = {"body": "**agent:note** · fixed the findings"}
other = {"body": "looks fine to me"}
cases = [
    ("approve", [review(H, "approve")], ("approve", False)),
    ("request_changes", [other, review(H, "request_changes")], ("request_changes", False)),
    ("request_changes answered", [review(H, "request_changes"), agent], ("request_changes", True)),
    ("needs_human", [review(H, "needs_human")], ("needs_human", False)),
    ("stale", [review("b" * 40, "needs_human")], ("stale", False)),
    ("latest wins", [review("b" * 40, "needs_human"), review(H, "approve")], ("approve", False)),
    ("none", [other], ("none", False)),
    ("untrusted author cannot override", [review(H, "needs_human"), review(H, "approve", user="someone")], ("needs_human", False)),
    ("untrusted author alone is none", [review(H, "approve", user="someone")], ("none", False)),
]
bad = [(n, w, m.codex_review(c, H)) for n, c, w in cases if m.codex_review(c, H) != w]
print("bad:", bad) if bad else print("all ok")
PY
)"
[ "$out" = "all ok" ] && ok "status-report: codex_review verdicts (approve, request_changes, needs_human, stale)" || bad "status-report: codex_review" "$out"

# 9b. status-report.py: pagination must work on gh 2.46 (no --slurp) and decode concatenated pages.
grep -q -- "--slurp" "$agents/status-report.py" && bad "status-report: uses gh --slurp (not in gh 2.46)" || ok "status-report: no gh --slurp"
out="$("$PYTHON" - "$agents/status-report.py" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("sr", sys.argv[1]); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
class R: stdout = '[{"id": 1}, {"id": 2}]\n[{"id": 3}]\n'
m.subprocess.run = lambda *a, **k: R()
print([c["id"] for c in m.gh_paginated("x")])
PY
)"
[ "$out" = "[1, 2, 3]" ] && ok "status-report: concatenated pages decoded" || bad "status-report: pagination" "$out"

# 10. status-report.py: a failed comment fetch aborts the report instead of reading as "no review".
fake gh 'case "$1" in api) echo "boom" >&2; exit 1;; pr) case "$*" in *"--state open"*) echo "[{\"number\":5,\"title\":\"t\",\"isDraft\":true,\"headRefName\":\"v2/issue-5-x\",\"headRefOid\":\"abc\",\"labels\":[],\"url\":\"u\"}]";; *) echo "[]";; esac;; *) echo "[]";; esac'
out="$(PATH="$tmp/bin:$PATH" "$PYTHON" "$agents/status-report.py" --repo o/r 2>&1)"; rc=$?
if [ "$rc" -eq 1 ] && grep -q "gh failed" <<<"$out"; then ok "status-report: comment-fetch failure aborts (exit 1), not codex=none"; else bad "status-report: fetch failure" "rc=$rc"$'\n'"$out"; fi

echo "tooling tests: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
