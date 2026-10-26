#!/usr/bin/env bash
# Dump the Emmaus PostgreSQL database (custom format, pg_dump -Fc) from the compose `db`
# container and keep only the newest N dumps. Meant for an hourly cron during a camp.
#
# Usage: scripts/backup_db.sh [OUT_DIR] [KEEP]
#
#   OUT_DIR  where dumps go (or BACKUP_DIR; default: ./backups)
#   KEEP     how many dumps to keep, newest first (or BACKUP_KEEP; default: 48)
#
# Which container to dump:
#   EMMAUS_DB_CONTAINER   container name or id; when set, the compose lookup is skipped
#   COMPOSE_PROJECT_NAME  compose project (default: the checkout's directory name, as compose does)
#   COMPOSE_DB_SERVICE    compose service (default: db)
#
# No credentials here: pg_dump runs inside the container as $POSTGRES_USER on $POSTGRES_DB,
# read from the container's own environment, over its local socket.
# Exit status: 0 with the dump written and verified, non-zero otherwise (nothing partial is left).
set -euo pipefail

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }
die() { echo "backup_db: $*" >&2; exit 1; }

case "${1:-}" in
  -h | --help) usage; exit 0 ;;
esac
[ "$#" -le 2 ] || { usage >&2; exit 2; }

out_dir="${1:-${BACKUP_DIR:-./backups}}"
keep="${2:-${BACKUP_KEEP:-48}}"
service="${COMPOSE_DB_SERVICE:-db}"
prefix="emmaus-db"

# A leading zero would make bash arithmetic read the number as octal (or fail): accept 1-99999 only.
[[ "$keep" =~ ^[1-9][0-9]{0,4}$ ]] || die "KEEP must be a whole number from 1 to 99999, got '$keep'"

command -v docker >/dev/null || die "docker is not on PATH ($PATH)"

container="${EMMAUS_DB_CONTAINER:-}"
if [ -z "$container" ]; then
  repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  # Compose's default project name: the directory name, lowercased, other characters dropped,
  # leading '-' and '_' trimmed.
  default_project="$(basename "$repo_dir" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-' | sed 's/^[-_]*//')"
  project="${COMPOSE_PROJECT_NAME:-$default_project}"
  listing="$(docker ps \
    --filter "label=com.docker.compose.project=$project" \
    --filter "label=com.docker.compose.service=$service" \
    --format '{{.Names}}')" || die "docker ps failed (is this user allowed to use docker?)"
  found=()
  [ -z "$listing" ] || mapfile -t found <<<"$listing"
  case "${#found[@]}" in
    1) container="${found[0]}" ;;
    0) die "no running container for compose project '$project', service '$service' (set COMPOSE_PROJECT_NAME or EMMAUS_DB_CONTAINER)" ;;
    *) die "more than one running container for project '$project', service '$service': ${found[*]} (set EMMAUS_DB_CONTAINER)" ;;
  esac
fi

umask 077  # dumps hold every answer: readable by the backup user only
mkdir -p -- "$out_dir"
out_dir="$(cd -- "$out_dir" && pwd)"

shopt -s nullglob
if command -v flock >/dev/null; then
  exec 9>"$out_dir/.backup_db.lock"
  flock -n 9 || die "another backup is still running in $out_dir"
  # Holding the lock, any partial file is a leftover of a run that was killed.
  stale=("$out_dir/.$prefix"-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.dump.partial)
  if [ "${#stale[@]}" -gt 0 ]; then
    rm -f -- "${stale[@]}"
    echo "backup_db: removed ${#stale[@]} partial file(s) left by an interrupted run"
  fi
else
  echo "backup_db: flock not found; not cleaning up partial files of earlier runs" >&2
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
final="$out_dir/$prefix-$stamp.dump"
partial="$out_dir/.$prefix-$stamp.dump.partial"
[ ! -e "$final" ] || die "$final already exists"
trap 'rm -f -- "$partial"' EXIT

# Single quotes on purpose: $POSTGRES_USER and $POSTGRES_DB expand inside the container.
docker exec "$container" sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' >"$partial" \
  || die "pg_dump in container '$container' failed; no dump written"
[ -s "$partial" ] || die "pg_dump in container '$container' produced an empty file"
# Restore the whole archive into /dev/null: every data block is read, so a truncated or corrupt
# dump fails here (pg_restore --list would only read the table of contents).
docker exec -i "$container" pg_restore -f /dev/null <"$partial" \
  || die "the dump from '$container' does not restore (pg_restore -f /dev/null failed); not kept"
mv -- "$partial" "$final"
trap - EXIT

bytes="$(wc -c <"$final" | tr -d ' ')"
echo "backup_db: wrote $final ($(du -h -- "$final" | cut -f1), $bytes bytes) from container $container"

# Only names this script writes (emmaus-db-20261026T050000Z.dump) count against KEEP; a dump named
# by hand (emmaus-db-before-upgrade.dump) is never rotated away. Names sort by UTC time, so the
# glob (sorted) lists the oldest first.
dumps=("$out_dir/$prefix"-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.dump)
excess=$((${#dumps[@]} - keep))
removed=0
if [ "$excess" -gt 0 ]; then
  for old in "${dumps[@]:0:excess}"; do
    rm -f -- "$old"
    removed=$((removed + 1))
  done
fi
echo "backup_db: keeping $((${#dumps[@]} - removed)) dump(s) in $out_dir (limit $keep), removed $removed"
