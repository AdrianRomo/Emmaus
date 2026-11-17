#!/usr/bin/env bash
# Migration governance on the v2 branches (docs/decisions/0013, which replaces 0005 now that the
# camp ended): a branch aimed at v2/foundation may add migrations in any app, core/ and events/
# included, as long as they are additive. A migration that removes, renames or runs raw SQL can lose
# the first camp's data, so it fails here until a human labels the PR `risk:high`.
#   Usage: scripts/agents/check-migration-scope.sh [--base origin/v2/foundation]
#   Exit 0 when every added migration is additive, 1 otherwise, 0 with a notice when the current
#   branch IS the base or main. ALLOW_DESTRUCTIVE_MIGRATIONS=true skips the check (CI sets it from
#   the PR label risk:high; locally only for a reviewed, dumped-first change).
. "$(dirname "$0")/lib.sh"
base="${BASE:-origin/v2/foundation}"
while [ $# -gt 0 ]; do case "$1" in
  --base) base="$2"; shift;; -h|--help) sed -n '2,9p' "$0"; exit 0;; *) die "unknown option $1";;
esac; shift; done
cd "$repo_root"
if [ "${ALLOW_DESTRUCTIVE_MIGRATIONS:-false}" = "true" ]; then
  echo "migration scope: skipped (ALLOW_DESTRUCTIVE_MIGRATIONS=true: a reviewed change carrying risk:high)"; exit 0
fi
head="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
case "$head" in main|v2/foundation) echo "migration scope: not applicable on $head"; exit 0;; esac
if ! git rev-parse --verify --quiet "$base" >/dev/null; then
  echo "migration scope: base $base not found; fetch it first"; exit 1
fi
merge_base="$(git merge-base "$base" HEAD)"
# --no-renames: a file moved or copied into a migrations directory counts as added, not as a rename.
added="$(git diff --name-only --no-renames --diff-filter=ACM "$merge_base" HEAD -- '*/migrations/*.py' | grep -v '__init__.py' || true)"
bad=""
for file in $added; do
  [ -f "$file" ] || continue
  if grep -Eq 'migrations\.(RemoveField|DeleteModel|RenameField|RenameModel|AlterModelTable|RunSQL)\b' "$file"; then bad="$bad $file"; fi
done
if [ -n "$bad" ]; then
  echo "migration scope FAILED: these migrations remove, rename or run raw SQL, which can lose existing data:"
  printf '  %s\n' $bad
  echo "Make the change additive (add, backfill, stop reading; drop in a later release), or have a human label the PR risk:high after a dump (docs/decisions/0013)."
  exit 1
fi
count="$(printf '%s\n' $added | grep -c . || true)"
echo "migration scope OK: $count migration(s) added or changed since $(git rev-parse --short "$merge_base"), all additive"
