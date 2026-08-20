#!/usr/bin/env bash
#
# measure_whale_latency.sh — whale profile latency, measured DURING the nightly
# hydration window.
#
# Why this exists: the daily full hydration (02:00 UTC) writes to Supabase through a
# SYNCHRONOUS client on the same event loop that serves the API, so its writes block
# live requests. Before the write-bulking change, API requests landing in that window
# stalled 40-49s. Latency measured at any other hour cannot see this at all — the
# window is the only time the regression is observable.
#
# Usage:
#   scripts/measure_whale_latency.sh [minutes]      # default 22
#
# Run it from backend/. Read-only: GETs the public roster + profile endpoints and reads
# the DB to confirm hydration was actually running. Writes only its own report file.
set -uo pipefail

DURATION_MIN="${1:-22}"
BASE="${WHALE_API_BASE:-https://ai-value-investor-app-production.up.railway.app}"
OUT="/tmp/whale_latency_$(date -u +%Y%m%dT%H%M%SZ).txt"

DB=$(grep -m1 '^DATABASE_URL=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | sed 's#+asyncpg##')

log() { echo "$@" | tee -a "$OUT"; }

hydration_count() {   # whales hydrated in the last N minutes
  [ -z "$DB" ] && { echo "?"; return; }
  PGCONNECT_TIMEOUT=15 psql "$DB" -X -At -c \
    "select count(*) from whales where last_hydrated_at > now() - interval '$1 minutes';" 2>/dev/null || echo "?"
}

log "=== whale profile latency — hydration window ==="
log "started : $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
log "base    : $BASE"
log "duration: ${DURATION_MIN} min"
log ""

ROSTER=$(mktemp)
if ! curl -s -m 30 "$BASE/api/v1/whales" -o /tmp/_roster.json; then
  log "FATAL: roster fetch failed"; exit 1
fi
python3 -c "
import json,sys
d=json.load(open('/tmp/_roster.json'))
open('$ROSTER','w').write('\n'.join(f\"{w['name']}\t{w['id']}\" for w in d))
print(len(d))
" > /tmp/_n || { log "FATAL: roster parse failed"; exit 1; }
log "roster  : $(cat /tmp/_n) whales"
log ""

ALL=$(mktemp)
END=$(( $(date +%s) + DURATION_MIN * 60 ))
PASS=0

# do-while: ALWAYS complete at least one pass. A duration that has already elapsed (or a
# job that fires late) must still produce a measurement rather than an empty report.
while : ; do
  PASS=$((PASS+1))
  HYD_BEFORE=$(hydration_count 3)
  PASSFILE=$(mktemp)
  while IFS=$'\t' read -r name id; do
    t=$(curl -s -m 150 -o /dev/null -w "%{time_total}" "$BASE/api/v1/whales/$id/profile" 2>/dev/null)
    [ -n "$t" ] && { echo -e "$t\t$name" >> "$PASSFILE"; echo -e "$t\t$name" >> "$ALL"; }
  done < "$ROSTER"
  HYD_AFTER=$(hydration_count 3)

  python3 - "$PASSFILE" "$PASS" "$HYD_BEFORE" "$HYD_AFTER" <<'PY' | tee -a "$OUT"
import sys
rows=[]
for line in open(sys.argv[1]):
    t,n=line.rstrip('\n').split('\t',1)
    try: rows.append((float(t),n))
    except: pass
if rows:
    ts=sorted(t for t,_ in rows); n=len(ts); p=lambda q: ts[min(n-1,int(n*q))]
    worst=max(rows)
    print(f"pass {sys.argv[2]:>2}  hyd(before/after)={sys.argv[3]}/{sys.argv[4]}  "
          f"n={n} median={ts[n//2]:.2f} p95={p(0.95):.2f} max={ts[-1]:.2f} "
          f"over3s={sum(1 for t in ts if t>3)}  worst={worst[1]}")
PY
  [ "$(date +%s)" -lt "$END" ] || break
done

log ""
log "=== OVERALL (all passes combined) ==="
python3 - "$ALL" <<'PY' | tee -a "$OUT"
import sys, collections
rows=[]
for line in open(sys.argv[1]):
    t,n=line.rstrip('\n').split('\t',1)
    try: rows.append((float(t),n))
    except: pass
if not rows:
    print("NO SAMPLES — every request failed. Check the API base URL and connectivity;")
    print("this run proves nothing about latency.")
    raise SystemExit(0)
ts=sorted(t for t,_ in rows); n=len(ts); p=lambda q: ts[min(n-1,int(n*q))]
print(f"samples={n} min={ts[0]:.2f} median={ts[n//2]:.2f} p95={p(0.95):.2f} p99={p(0.99):.2f} max={ts[-1]:.2f}")
print(f"over 3s = {sum(1 for t in ts if t>3)}   over 10s = {sum(1 for t in ts if t>10)}")
print("\nslowest 8:")
for t,name in sorted(rows, reverse=True)[:8]:
    print(f"  {t:7.2f}s  {name}")
print("""
REFERENCE (measured 2026-08-20):
  baseline, no hydration, OLD code    median 0.88  p95 1.40  max  1.74   over3s 0
  OLD code, hydration RUNNING         median 0.22  p95 2.15  max 48.90   over3s 2
  OLD code, hydration RUNNING         median 0.21  p95 0.49  max 40.96   over3s 2
  NEW code, politician sweep RUNNING  median 0.21  p95 0.59  max  7.79   over3s 1
  NEW code, idle                      median 0.21  p95 0.31  max  0.43   over3s 0

READ IT LIKE THIS
  - A max at or under ~8s during a confirmed-active window = the bulking held for the
    FULL 56-whale run, not just the 11-whale politician sweep.
  - A max back in the 20-50s range = blocking work remains; the next lever is moving
    the synchronous postgrest calls onto asyncio.to_thread.
  - hyd(before/after)=0/0 on EVERY pass means the hydration never ran and the
    measurement proves nothing. Check the job actually fired before drawing a conclusion.""")
PY
log ""
log "finished: $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
log "report  : $OUT"
