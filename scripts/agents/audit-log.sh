#!/usr/bin/env bash
# Append one line to the local audit log ($EMMAUS_AGENT_HOME/audit.jsonl) and, with --comment,
# post the same record as a comment on the issue (the durable log; docs/agents/policies.md).
# Usage: scripts/agents/audit-log.sh --issue N --action <claimed|gate|retry|pr|blocked|escalated|done|note> [--detail "text"] [--comment]
. "$(dirname "$0")/lib.sh"
issue=""; action=""; detail=""; comment=false
while [ $# -gt 0 ]; do case "$1" in
  --issue) issue="$2"; shift;; --action) action="$2"; shift;; --detail) detail="$2"; shift;;
  --comment) comment=true;; -h|--help) sed -n '2,5p' "$0"; exit 0;; *) die "unknown option $1";;
esac; shift; done
[ -n "$issue" ] && [ -n "$action" ] || die "--issue and --action are required"
case "$action" in claimed|gate|retry|pr|blocked|escalated|done|note) ;; *) die "unknown action $action";; esac

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
actor="${EMMAUS_AGENT_ACTOR:-${USER:-unknown}}"
host="$(hostname)"
branch="$(git -C "$repo_root" branch --show-current 2>/dev/null || echo '-')"
sha="$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo '-')"
line="$("$PYTHON" -c 'import json,sys; k=["ts","actor","host","issue","branch","sha","action","detail"]; print(json.dumps(dict(zip(k, sys.argv[1:]))))' \
  "$ts" "$actor" "$host" "$issue" "$branch" "$sha" "$action" "$detail")"
printf '%s\n' "$line" >>"$EMMAUS_AGENT_HOME/audit.jsonl"
printf '%s\n' "$line"
if $comment; then
  need gh
  gh issue comment "$issue" --body "**agent:${action}** · ${ts} · ${actor}@${host} · \`${branch}\` @ ${sha}

${detail:-(no detail)}" >/dev/null
  log "comment posted on #$issue"
fi
