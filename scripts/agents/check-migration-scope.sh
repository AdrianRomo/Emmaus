#!/usr/bin/env bash
# Migration governance while the camp runs on main (docs/decisions/0005, roadmap rule 2):
# a change aimed at v2/foundation must not add migrations under core/migrations/ or
# events/migrations/, because main (the camp) owns those numbers and the next main -> v2 merge
# would produce two leaves per app. New data lives in new apps.
#   Usage: scripts/agents/check-migration-scope.sh [--base origin/v2/foundation]
#   Exit 0 when no guarded migration is added, 1 otherwise, 0 with a notice when the current
#   branch IS the base or main. ALLOW_CAMP_MIGRATIONS=true skips the check (CI sets it from the
#   PR label area:camp; locally only for a reviewed camp fix).
. "$(dirname "$0")/lib.sh"
base="${BASE:-origin/v2/foundation}"
while [ $# -gt 0 ]; do case "$1" in
  --base) base="$2"; shift;; -h|--help) sed -n '2,9p' "$0"; exit 0;; *) die "unknown option $1";;
esac; shift; done
cd "$repo_root"
if [ "${ALLOW_CAMP_MIGRATIONS:-false}" = "true" ]; then
  echo "migration scope: skipped (ALLOW_CAMP_MIGRATIONS=true: a reviewed camp fix carrying area:camp)"; exit 0
fi
head="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
case "$head" in main|v2/foundation) echo "migration scope: not applicable on $head"; exit 0;; esac
if ! git rev-parse --verify --quiet "$base" >/dev/null; then
  echo "migration scope: base $base not found; fetch it first"; exit 1
fi
merge_base="$(git merge-base "$base" HEAD)"
added="$(git diff --name-only --diff-filter=A "$merge_base" HEAD -- 'core/migrations/*.py' 'events/migrations/*.py' | grep -v '__init__.py' || true)"
if [ -n "$added" ]; then
  echo "migration scope FAILED: this branch adds migrations under core/ or events/, which main (the camp) owns while the event runs:"
  printf '  %s\n' $added
  echo "Put new tables in a new app (people, access, groups, ...), or label the PR area:camp for a reviewed camp fix (docs/decisions/0005)."
  exit 1
fi
echo "migration scope OK: no core/ or events/ migration added since $(git rev-parse --short "$merge_base")"
