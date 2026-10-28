#!/usr/bin/env bash
# Create or update the workflow labels from .github/labels.json (docs/agents/workflow.md).
# Usage: scripts/agents/sync-labels.sh [--dry-run]
. "$(dirname "$0")/lib.sh"
need gh
dry=false; [ "${1:-}" = "--dry-run" ] && dry=true
repo="${GH_REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
"$PYTHON" -c 'import json, sys
for l in json.load(open(sys.argv[1])):
    print("\t".join([l["name"], l["color"], l["description"]]))' "$repo_root/.github/labels.json" |
while IFS=$'\t' read -r name color desc; do
  if $dry; then echo "would create/update: $name  #$color  $desc"
  else gh label create "$name" -R "$repo" --color "$color" --description "$desc" --force >/dev/null && echo "ok: $name"; fi
done
