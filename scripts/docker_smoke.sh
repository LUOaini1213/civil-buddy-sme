#!/usr/bin/env bash
# Smoke test for the gateway Docker image: it must refuse to start open without CIVIL_TOKEN,
# serve with it, parse the synthetic facade ITT, run the tender <-> packing link from an upload and
# from the /demo page's route, keep its SQLite database across a container re-create (the database
# lives in the /app/output volume), and keep what an agent turn wrote (the linked run's
# tender-packing-link.json in /app/demo/out, a second volume) across the re-create too.
# The image must not carry a working copy's demo/.env, demo/out or demo/data (.dockerignore).
#
#   docker build -t civil-buddy-gateway:smoke .
#   bash scripts/docker_smoke.sh [image]
#
# Exits non-zero on the first mismatch and prints the container logs.
set -euo pipefail

IMAGE="${1:-civil-buddy-gateway:smoke}"
PORT="${SMOKE_PORT:-18000}"
NAME="cb-smoke-$$"
VOL="cb-smoke-out-$$"
AGENT_VOL="cb-smoke-agent-$$"
BASE="http://127.0.0.1:${PORT}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ITT="${ROOT}/examples/facade-demo/facade_itt_doc.md"
TMP="$(mktemp -d)"
TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
SESSION="docker-smoke-$$"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "=== docker smoke FAILED (exit $status); container logs:" >&2
    docker logs "$NAME" 2>&1 | tail -n 200 >&2 || true
  fi
  docker rm -f "$NAME" "$NAME-open" >/dev/null 2>&1 || true
  docker volume rm "$VOL" "$AGENT_VOL" >/dev/null 2>&1 || true
  rm -rf "$TMP"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
code() { curl -s -o "${2:-/dev/null}" -w '%{http_code}' "${@:3}" "$BASE$1"; }
expect() {  # expect <label> <want> <got>
  if [ "$2" != "$3" ]; then fail "$1: want HTTP $2, got $3"; fi
  echo "ok   $1 -> $3"
}

start() {  # start the gateway with the token on the shared volume; wait for /api/health
  local t0 t1
  t0=$(date +%s%N)
  docker run -d --name "$NAME" -e CIVIL_TOKEN="$TOKEN" -e CIVIL_JOB_ROOT=/app/output/job -p "127.0.0.1:${PORT}:8000" \
    -v "$VOL:/app/output" -v "$AGENT_VOL:/app/demo/out" "$IMAGE" >/dev/null
  for _ in $(seq 1 300); do
    if curl -fs -o /dev/null "$BASE/api/health"; then
      t1=$(date +%s%N)
      echo "ok   cold start (docker run -> /api/health 200): $(( (t1 - t0) / 1000000 )) ms"
      return 0
    fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" != "true" ]; then
      fail "container exited during startup"
    fi
    sleep 0.2
  done
  fail "/api/health not up after 60 s"
}

# 0. Nothing of a working copy's local state is baked in (sme-3): no demo/.env, demo/out, demo/data.
for p in /app/demo/.env /app/demo/out /app/demo/data /app/.env /app/output/web-link; do
  if docker run --rm --entrypoint sh "$IMAGE" -c "test -e $p"; then fail "$p is in the image"; fi
done
echo "ok   image carries no demo/.env, demo/out, demo/data, .env or output/web-link"

# 1. #61 baseline: listening on 0.0.0.0 without CIVIL_TOKEN must refuse to start.
set +e
timeout 60 docker run --rm --name "$NAME-open" "$IMAGE" >"$TMP/notoken.log" 2>&1
rc=$?
set -e
[ "$rc" -ne 0 ] && [ "$rc" -ne 124 ] || { cat "$TMP/notoken.log" >&2; fail "container without CIVIL_TOKEN did not refuse (exit $rc)"; }
grep -q "CIVIL_TOKEN" "$TMP/notoken.log" || { cat "$TMP/notoken.log" >&2; fail "refusal does not mention CIVIL_TOKEN"; }
echo "ok   no CIVIL_TOKEN -> refused to start (exit $rc)"

# 2. Serve with a random token.
start
AUTH=(-H "Authorization: Bearer $TOKEN")
expect "GET /api/health (public liveness)" 200 "$(code /api/health)"
expect "GET /api/health with token" 200 "$(code /api/health /dev/null "${AUTH[@]}")"
expect "GET /api/tools without token" 401 "$(code /api/tools)"
expect "GET /api/tools with wrong token" 401 "$(code /api/tools /dev/null -H 'Authorization: Bearer wrong')"
expect "GET /api/tools with token" 200 "$(code /api/tools /dev/null "${AUTH[@]}")"
expect "POST /api/tender/parse without token" 401 "$(code /api/tender/parse /dev/null -H 'content-type: application/json' --data '{}')"

# 3. Parse the synthetic facade ITT.
python3 -c 'import json,sys; print(json.dumps({"text": open(sys.argv[1], encoding="utf-8").read()}))' "$ITT" >"$TMP/itt.json"
expect "POST /api/tender/parse facade_itt_doc.md" 200 \
  "$(code /api/tender/parse "$TMP/parse.json" "${AUTH[@]}" -H 'content-type: application/json' --data @"$TMP/itt.json")"
python3 - "$TMP/parse.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
if d.get("ok") is not True:
    sys.exit(f"FAIL: tender parse ok={d.get('ok')!r} error_code={d.get('error_code')!r}")
reqs = (d.get("parse") or {}).get("requirements") or []
rows = (d.get("matrix") or {}).get("rows") or []
if not reqs or not rows:
    sys.exit(f"FAIL: tender parse returned {len(reqs)} requirements, {len(rows)} matrix rows")
print(f"ok   tender parse: {len(reqs)} requirements, {len(rows)} response-matrix rows")
PY

# 3b. The tender <-> packing link from a browser upload, and the one-click demo route (both token-gated).
EX="${ROOT}/examples/facade-demo"
expect "POST /api/tender/link without token" 401 "$(code /api/tender/link /dev/null -F "tender=@$EX/facade_itt_doc.md" -F "panel_list=@$EX/facade_panels.xlsx")"
expect "GET /demo without token" 401 "$(code /demo)"
expect "GET /demo with token" 200 "$(code /demo /dev/null "${AUTH[@]}")"
expect "POST /api/tender/link facade_itt_doc.md + facade_panels.xlsx" 200 \
  "$(code /api/tender/link "$TMP/link.json" "${AUTH[@]}" -F "tender=@$EX/facade_itt_doc.md" -F "panel_list=@$EX/facade_panels.xlsx" -F session_id=smoke)"
expect "POST /api/tender/link same session, rev B" 200 \
  "$(code /api/tender/link "$TMP/link_b.json" "${AUTH[@]}" -F "tender=@$EX/facade_itt_doc.md" -F "panel_list=@$EX/facade_panels_rev_b.xlsx" -F session_id=smoke)"
expect "POST /api/tender/link/demo" 200 "$(code /api/tender/link/demo "$TMP/demo.json" "${AUTH[@]}" -X POST)"
python3 - "$TMP/link.json" "$TMP/link_b.json" "$TMP/demo.json" <<'PY'
import json, sys
a, b, demo = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:4])
ids = [s["id"] for s in a["statements"]]
if ids != [f"S{i}" for i in range(1, 8)] or a["plan"]["containers_used"] != 6 or a["submit_blocked"] is not True:
    sys.exit(f"FAIL: upload link gave {ids}, plan {a.get('plan')}, submit_blocked {a.get('submit_blocked')}")
if b["previous_job_id"] != a["job_id"] or b["stale_statements"] != ["S2", "S3", "S6", "S7"]:
    sys.exit(f"FAIL: rev B upload: previous {b.get('previous_job_id')}, stale {b.get('stale_statements')}")
if demo["stale_statements"] != ["S2", "S3", "S6", "S7"] or demo["rev_b"]["plan"]["containers_used"] != 8:
    sys.exit(f"FAIL: demo route: stale {demo.get('stale_statements')}")
print(f"ok   linked upload: {len(ids)} statements, 6 x 40HQ; rev B stale {', '.join(b['stale_statements'])}; demo route the same")
PY

# 3c. The typed agent request on the seeded job folder writes tender-packing-link.json under /app/demo/out.
docker exec "$NAME" sh -c 'mkdir -p /app/output/job && cp examples/facade-demo/facade_itt_doc.md examples/facade-demo/facade_panels.xlsx /app/output/job/'
python3 -c 'import json; print(json.dumps({"text": "Link the tender facade_itt_doc.md to the packing list facade_panels.xlsx and write the logistics response", "session_id": "smoke-agent"}))' >"$TMP/agent.json"
expect "POST /api/agent (linked request)" 200 \
  "$(code /api/agent "$TMP/agent_out.json" "${AUTH[@]}" -H 'content-type: application/json' --data @"$TMP/agent.json")"
agent_record() { docker exec "$NAME" sh -c 'ls /app/demo/out/smoke-agent/*/tender-packing-link.json' >/dev/null 2>&1; }
agent_record || fail "the linked agent turn wrote no tender-packing-link.json under /app/demo/out"
echo "ok   linked agent turn wrote /app/demo/out/smoke-agent/.../tender-packing-link.json"

# 4. Write a session to SQLite, re-create the container on the same volume, read it back.
expect "POST /api/demo (writes a session)" 200 \
  "$(code /api/demo /dev/null "${AUTH[@]}" -H 'content-type: application/json' --data "{\"session_id\":\"$SESSION\"}")"
has_session() {
  code /api/audit "$TMP/audit.json" "${AUTH[@]}" >/dev/null
  python3 - "$TMP/audit.json" "$SESSION" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
ids = [s.get("session_id") for s in d.get("sessions") or []]
sys.exit(0 if sys.argv[2] in ids else 1)
PY
}
in_db() {  # the session row must be in the SQLite file inside the volume, not only in JSON
  docker exec "$NAME" python -c "import sqlite3,sys; c=sqlite3.connect('file:/app/output/db/civilbuddy.db?mode=ro', uri=True); sys.exit(0 if c.execute('select count(*) from sessions where session_id=?', (sys.argv[1],)).fetchone()[0] else 1)" "$SESSION"
}
has_session || fail "session $SESSION not listed by /api/audit before re-create"
in_db || fail "session $SESSION not in /app/output/db/civilbuddy.db"
echo "ok   session row in /app/output/db/civilbuddy.db (sessions table) and listed by /api/audit"
docker rm -f "$NAME" >/dev/null
start
in_db || fail "session $SESSION missing from the database after the container was re-created"
agent_record || fail "the agent turn's tender-packing-link.json was lost when the container was re-created"
echo "ok   agent output survived container re-create (volume $AGENT_VOL at /app/demo/out)"
has_session || fail "session $SESSION lost after the container was re-created"
echo "ok   session survived container re-create (volume $VOL)"
echo "docker smoke: all checks passed"
