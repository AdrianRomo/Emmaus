#!/usr/bin/env bash
# One task per session: an isolated worktree and branch for one GitHub issue.
# Usage: scripts/agents/new-task-worktree.sh <issue> [--claim] [--base origin/v2/foundation] [--force] [--dry-run]
#   --claim    move the issue from agent:ready to agent:in-progress and post the claim comment
#   --force    skip the agent:ready check (human-supervised work)
#   --dry-run  print the plan and change nothing
. "$(dirname "$0")/lib.sh"
need gh; need git
issue=""; claim=false; base="origin/v2/foundation"; force=false; dry=false
while [ $# -gt 0 ]; do case "$1" in
  --claim) claim=true;; --base) base="$2"; shift;; --force) force=true;; --dry-run) dry=true;;
  -h|--help) sed -n '2,6p' "$0"; exit 0;;
  *) [[ "$1" =~ ^[0-9]+$ ]] && issue="$1" || die "unknown argument $1";;
esac; shift; done
[ -n "$issue" ] || die "usage: $0 <issue> [--claim] [--base REF] [--force] [--dry-run]"

repo="${GH_REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
json="$(gh issue view "$issue" -R "$repo" --json number,title,state,labels)"
IFS=$'\t' read -r state labels title < <(printf '%s' "$json" | "$PYTHON" -c '
import json, sys
d = json.load(sys.stdin)
print(d["state"], ",".join(l["name"] for l in d["labels"]), d["title"], sep="\t")')
[ "$state" = "OPEN" ] || die "#$issue is $state"
if ! $force; then
  case ",$labels," in
    *,agent:in-progress,*) die "#$issue is already agent:in-progress (someone else owns it)";;
    *,agent:ready,*) ;;
    *) die "#$issue is not agent:ready (labels: ${labels:-none}); use --force for supervised work";;
  esac
fi
slug="$(printf '%s' "$title" | "$PYTHON" -c '
import re, sys, unicodedata
t = unicodedata.normalize("NFKD", sys.stdin.read()).encode("ascii", "ignore").decode().lower()
t = re.sub(r"^\[[^\]]*\]\s*", "", t)
print(re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:40].rstrip("-") or "task")')"
branch="v2/issue-${issue}-${slug}"
mc="$(main_checkout)"
wt_root="${EMMAUS_WORKTREES:-${mc}.worktrees}"
dir="$wt_root/issue-${issue}-${slug}"

echo "issue:    #$issue  $title"
echo "labels:   ${labels:-none}"
echo "base:     $base"
echo "branch:   $branch"
echo "worktree: $dir"
if git -C "$mc" show-ref --quiet "refs/heads/$branch" || git -C "$mc" ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
  die "branch $branch already exists; resume it with: git -C '$mc' worktree add '$dir' '$branch'"
fi
[ -e "$dir" ] && die "worktree path exists: $dir"
if $dry; then echo "(dry run: nothing changed)"; exit 0; fi

git -C "$mc" fetch --quiet origin "${base#origin/}"
git -C "$mc" worktree add "$dir" -b "$branch" "$base" >/dev/null
echo "created $dir on $branch from $base ($(git -C "$dir" rev-parse --short HEAD))"
if $claim; then
  gh issue edit "$issue" -R "$repo" --remove-label agent:ready --add-label agent:in-progress >/dev/null
  (cd "$dir" && "$agents_dir/audit-log.sh" --issue "$issue" --action claimed \
     --detail "branch \`$branch\` from \`$base\`; worktree \`$dir\`" --comment)
fi
cat <<NEXT

Next, in the worktree:
  cd "$dir"
  scripts/agents/quality-gate.sh [--postgres] [--e2e]     # paste the summary into the PR
  scripts/agents/codex-review.sh                           # independent review
  gh pr create --draft --base ${base#origin/} --title "<title> (#$issue)" --body-file .github/PULL_REQUEST_TEMPLATE.md
  gh issue edit $issue --remove-label agent:in-progress --add-label agent:review
  scripts/agents/audit-log.sh --issue $issue --action pr --detail "<PR url>" --comment
NEXT
