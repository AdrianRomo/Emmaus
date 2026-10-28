#!/usr/bin/env bash
# Independent, read-only Codex CLI review of HEAD against a base branch, with a structured verdict.
# Usage: scripts/agents/codex-review.sh [--base origin/v2/foundation] [--out FILE]
# Exit: 0 approve, 2 request_changes, 3 needs_human, 1 error. Needs `codex login` on this machine.
. "$(dirname "$0")/lib.sh"
need codex; need git
base="${BASE:-origin/v2/foundation}"; out=""
while [ $# -gt 0 ]; do case "$1" in
  --base) base="$2"; shift;; --out) out="$2"; shift;; -h|--help) sed -n '2,4p' "$0"; exit 0;; *) die "unknown option $1";;
esac; shift; done
cd "$repo_root"
git fetch --quiet origin "${base#origin/}" 2>/dev/null || true
merge_base="$(git merge-base "$base" HEAD)"
files="$(git diff --name-only "$merge_base" HEAD)"
[ -n "$files" ] || die "no changes between $base and HEAD"
mkdir -p "$EMMAUS_AGENT_HOME/reviews"
out="${out:-$EMMAUS_AGENT_HOME/reviews/$(date -u +%Y%m%dT%H%M%SZ)-$(git rev-parse --short HEAD).json}"

prompt="You are an independent reviewer for the Emmaus repository (Django, Spanish UI; a real camp runs on the main branch in production).
Review the changes between ${merge_base} (the merge base with ${base}) and HEAD. Run \`git diff ${merge_base} HEAD\` and \`git log --oneline ${merge_base}..HEAD\` yourself, read any file you need, and run tests if that settles a doubt. You cannot write files.
Read AGENTS.md first; its invariants are the standard: people exist independently of events; church and event roles have distinct scopes; church data is tenant-isolated; sensitive participant data needs explicit audited authorization; existing camp functionality must not regress; all UI text is natural Mexican Spanish and accessible; answers are never scored, judged, sent to an AI service or e-mailed.
Check, in this order: (1) correctness against the stated intent of the commits; (2) security: authorization, object-level access by id, tenant leaks, injection, secrets or data in the tree; (3) migrations: additive and reversible?; (4) tests that prove the change, missing or weak tests are findings; (5) Spanish copy and template accessibility; (6) CI, scripts or tooling changes that could hide failures, skip gates or fabricate results.
Changed files:
${files}
Answer in the required JSON schema. Use verdict needs_human when a finding touches an invariant or needs a product decision, request_changes for defects you are confident about, approve otherwise. Every finding needs file, line (0 if not applicable), evidence quoted from the diff, and a concrete suggestion. Do not invent findings to look thorough; an empty findings list with approve is a valid answer."

# Model: $CODEX_MODEL, else the one in ~/.codex/config.toml; when that model is at capacity the
# fallbacks are tried in order (override with CODEX_REVIEW_FALLBACK_MODELS, space-separated).
# Each attempt writes to a fresh temporary file; the verdict is accepted only when Codex exited 0
# and the file parses against the schema's required keys, and only then does it replace $out.
# A stale $out from an earlier run can therefore never pass as this run's verdict.
models="${CODEX_MODEL:-}"
fallbacks="${CODEX_REVIEW_FALLBACK_MODELS:-gpt-5.6-sol gpt-6-astra gpt-5.5}"
rm -f "$out"; : >"$out.log"
rc=1; verdict_ok=false
for m in ${models:-default} $fallbacks; do
  margs=(); [ "$m" != default ] && margs=(-m "$m")
  attempt="$(mktemp "${out}.attempt.XXXXXX")"
  log "codex exec (read-only, ephemeral, model ${m}) on $(git rev-parse --short HEAD) vs $base ($(printf '%s\n' "$files" | wc -l) files)"
  set +e
  codex exec -s read-only --ephemeral -C "$repo_root" --color never ${margs[@]+"${margs[@]}"} \
    --output-schema "$agents_dir/codex-review.schema.json" -o "$attempt" "$prompt" >>"$out.log" 2>&1
  rc=$?
  set -e
  if [ "$rc" -eq 0 ] && [ -s "$attempt" ] && "$PYTHON" -c '
import json, sys
d = json.load(open(sys.argv[1]))
assert d["verdict"] in ("approve", "request_changes", "needs_human")
assert isinstance(d["findings"], list) and isinstance(d["summary"], str)
' "$attempt" 2>>"$out.log"; then
    mv "$attempt" "$out"; verdict_ok=true; break
  fi
  rm -f "$attempt"
  if grep -q "at capacity" "$out.log"; then log "model $m is at capacity; trying the next one"; continue; fi
  break
done
$verdict_ok || die "codex produced no valid verdict (rc=$rc); see $out.log"
"$PYTHON" - "$out" "$base" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
sev = {"high": 0, "medium": 0, "low": 0}
for f in d.get("findings", []):
    sev[f.get("severity", "low")] = sev.get(f.get("severity", "low"), 0) + 1
print(f"CODEX REVIEW  verdict={d['verdict']}  findings: high={sev['high']} medium={sev['medium']} low={sev['low']}  base={sys.argv[2]}")
print(f"summary: {d.get('summary','').strip()}")
for f in d.get("findings", []):
    print(f"- [{f['severity']}] {f['file']}:{f['line']} {f['title']}\n    evidence: {f['evidence'].strip()[:300]}\n    suggestion: {f['suggestion'].strip()[:300]}")
print(f"checked: {', '.join(d.get('checked', []))[:400]}")
print(f"json: {sys.argv[1]}")
sys.exit({"approve": 0, "request_changes": 2, "needs_human": 3}.get(d["verdict"], 1))
PY
