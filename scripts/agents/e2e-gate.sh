#!/usr/bin/env bash
# Browser gate on synthetic data only: migrate and seed a scratch SQLite database, serve it with
# runserver on a free port, run e2e/station-flow.mjs (shared-computer interview regression) and
# e2e/review-all.mjs (every screen per role at three widths: axe, overflow, h1, stylesheet, JS errors).
# Last, with the community modules turned on by seed_community_demo, e2e/community-flow.mjs (the camp-to-group journey
# and every community screen at phone and desktop widths). It runs last because the review must see the modules off.
# Usage: scripts/agents/e2e-gate.sh [--skip-flow] [--skip-review] [--skip-community]
# Env: E2E_OUT (results dir), E2E_PORT (default: a free port), PYTHON.
. "$(dirname "$0")/lib.sh"
need node; need curl
skip_flow=false; skip_review=false; skip_community=false
for a in "$@"; do case "$a" in
  --skip-flow) skip_flow=true;; --skip-review) skip_review=true;; --skip-community) skip_community=true;;
  -h|--help) sed -n '2,9p' "$0"; exit 0;; *) die "unknown option $a";;
esac; done
cd "$repo_root"
[ -d e2e/node_modules/playwright ] && [ -f e2e/node_modules/axe-core/axe.min.js ] \
  || die "e2e/node_modules missing: cd e2e && npm ci && npx playwright install chromium"

out="${E2E_OUT:-$EMMAUS_AGENT_HOME/e2e/$(date -u +%Y%m%dT%H%M%SZ)}"; mkdir -p "$out"
scratch="$(mktemp -d)"
port="${E2E_PORT:-$("$PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')}"
pw="DemoCamp-2026"   # the password e2e/review-all.mjs expects; synthetic @demo.invalid accounts only
export DEBUG=true DATABASE_URL="sqlite:///$scratch/e2e.sqlite3" MEDIA_ROOT="$scratch/media"

server=""
cleanup() {
  if [ -n "$server" ]; then kill "$server" 2>/dev/null || true; wait "$server" 2>/dev/null || true; fi
  rm -rf "$scratch"
}
trap cleanup EXIT

log "scratch database: migrate + seed_camp_demo + seed_dev_data"
"$PYTHON" manage.py migrate -v 0 >"$out/seed.log" 2>&1
"$PYTHON" manage.py seed_camp_demo --password "$pw" -v 0 >>"$out/seed.log" 2>&1
"$PYTHON" manage.py seed_dev_data --password "$pw" -v 0 >>"$out/seed.log" 2>&1
"$PYTHON" manage.py shell < e2e/all_urls.py | tail -1 >"$out/urls.json"
[ -s "$out/urls.json" ] || die "all_urls.py produced no URL list (see $out/seed.log)"

"$PYTHON" manage.py runserver "127.0.0.1:$port" --noreload >"$out/server.log" 2>&1 &
server=$!
for i in $(seq 1 60); do
  curl -fsS -o /dev/null "http://127.0.0.1:$port/health/" 2>/dev/null && break
  [ "$i" -eq 60 ] && die "server did not answer on :$port (see $out/server.log)"
  sleep 1
done
log "server up on :$port; results in $out"

status=0
cd e2e
if ! $skip_flow; then
  log "station-flow.mjs"
  if EMMAUS_URL="http://127.0.0.1:$port" EMMAUS_PASSWORD="$pw" timeout 600 node station-flow.mjs >"$out/station-flow.log" 2>&1; then
    tail -n 1 "$out/station-flow.log"
  else
    status=1; log "station-flow FAILED (see $out/station-flow.log)"; tail -n 5 "$out/station-flow.log"
  fi
fi
if ! $skip_review; then
  log "review-all.mjs (axe, overflow, h1, stylesheet, JS errors at 3 widths)"
  if AXE=node_modules/axe-core/axe.min.js EMMAUS_URL="http://127.0.0.1:$port" timeout 1200 node review-all.mjs "$out/urls.json" "$out/review" >"$out/review-all.log" 2>&1; then
    grep -E "page/width checks" "$out/review-all.log" || tail -n 2 "$out/review-all.log"
  else
    status=1; log "review-all FAILED (see $out/review-all.log and $out/review/results.json)"; tail -n 5 "$out/review-all.log"
  fi
fi
if ! $skip_community; then
  log "community-flow.mjs (modules on: camp-to-group journey, axe, overflow, targets)"
  # --panorama gives the pastor who approves content and --duplicados the pair of records to join (wave 9's steps).
  # Wave 10's steps: --dos-iglesias (the account of two churches), --rotas (the team's roles, dates and two more people)
  # and --oracion (prayer requests switched on by the pastor). --formularios is NOT here on purpose: today the seed
  # answers the church's form as the 17-year-old demo campera and formularios.services.submit refuses a known minor
  # (FormChanged), so the flag aborts the whole seed. Step 13 creates its own form and does not need it; add the flag
  # back when the seed is fixed.
  (cd .. && "$PYTHON" manage.py seed_community_demo --password "$pw" --panorama --duplicados --dos-iglesias --rotas --oracion >>"$out/seed.log" 2>&1) || { status=1; log "seed_community_demo FAILED (see $out/seed.log)"; }
  if AXE=node_modules/axe-core/axe.min.js EMMAUS_URL="http://127.0.0.1:$port" EMMAUS_PASSWORD="$pw" timeout 600 node community-flow.mjs "$out/community" >"$out/community-flow.log" 2>&1; then
    tail -n 1 "$out/community-flow.log"
  else
    status=1; log "community-flow FAILED (see $out/community-flow.log)"; tail -n 8 "$out/community-flow.log"
  fi
fi
if ! $skip_review && ! $skip_community; then
  # The same review again, now that the church of the camp uses the new modules: its church-role accounts get the
  # redesigned shell and stylesheet on every screen, the camp's included.
  log "review-all.mjs again, modules on (the redesigned shell over every screen)"
  if AXE=node_modules/axe-core/axe.min.js EMMAUS_URL="http://127.0.0.1:$port" timeout 1200 node review-all.mjs "$out/urls.json" "$out/review-on" >"$out/review-all-on.log" 2>&1; then
    grep -E "page/width checks" "$out/review-all-on.log" || tail -n 2 "$out/review-all-on.log"
  else
    status=1; log "review-all (modules on) FAILED (see $out/review-all-on.log and $out/review-on/results.json)"; tail -n 8 "$out/review-all-on.log"
  fi
fi
[ "$status" -eq 0 ] && log "browser gate OK" || log "browser gate FAILED"
exit "$status"
