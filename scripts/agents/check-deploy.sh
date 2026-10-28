#!/usr/bin/env bash
# `manage.py check --deploy` with production-like settings. Only the two HSTS warnings that
# docs/deployment.md documents as intentional (security.W005, security.W021) are accepted;
# anything else, or a non-zero exit, fails the gate. Runs against an empty scratch SQLite
# database so a seeded local db.sqlite3 cannot add warnings (set CHECK_DEPLOY_USE_DB=1 to use yours).
. "$(dirname "$0")/lib.sh"
cd "$repo_root"
accepted="security.W005 security.W021"

scratch="$(mktemp -d)"; trap 'rm -rf "$scratch"' EXIT
db_url="${DATABASE_URL:-}"
[ "${CHECK_DEPLOY_USE_DB:-0}" = "1" ] || db_url="sqlite:///$scratch/empty.sqlite3"

set +e
out="$(DEBUG=false \
  SECRET_KEY="${CHECK_DEPLOY_SECRET_KEY:-ci-only-not-a-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz-ABCDEF}" \
  ALLOWED_HOSTS=ci.emmaus.invalid CSRF_TRUSTED_ORIGINS=https://ci.emmaus.invalid \
  SECURE_SSL_REDIRECT=true SECURE_HSTS_SECONDS=3600 DATABASE_URL="$db_url" \
  "$PYTHON" manage.py check --deploy 2>&1)"
rc=$?
set -e
printf '%s\n' "$out"

ids="$(printf '%s\n' "$out" | grep -oE '\(([a-z_]+\.[EWIC][0-9]+)\)' | tr -d '()' | sort -u | tr '\n' ' ')"
unexpected=""
for id in $ids; do
  case " $accepted " in *" $id "*) ;; *) unexpected="$unexpected $id";; esac
done
if [ "$rc" -ne 0 ] || [ -n "$unexpected" ]; then
  echo "check --deploy FAILED: rc=$rc unexpected=[${unexpected# }] accepted=[$accepted]"
  exit 1
fi
echo "check --deploy OK: only accepted warnings [${ids% }]"
