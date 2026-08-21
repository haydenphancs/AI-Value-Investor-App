#!/usr/bin/env bash
#
# measure_whale_latency.sh — whale profile latency, measured DURING a hydration window.
#
# Why this exists: whale hydration writes to Supabase through a SYNCHRONOUS client on the
# same event loop that serves the API, so its writes block live requests. Before the
# write-bulking change, API requests landing in that window stalled 40-49s. Latency
# measured at any other hour cannot see this at all.
#
# ─────────────────────────────────────────────────────────────────────────────────────
# READ THIS BEFORE TRUSTING A NUMBER FROM THIS SCRIPT
#
# A fast `max` only means something if writes were ACTUALLY HAPPENING. The 02:00 UTC
# sweep skips a whale entirely when its payload is unchanged — `_hydrate_one` returns
# BEFORE any write (hydrate_whales.py, "Skipping … data unchanged"). 13F payloads change
# only during filing season (~45 days after each quarter end), so on a typical night all
# 45 13F filers are hash-stable and the sweep writes ESSENTIALLY NOTHING. Measured then,
# `max` looks great and proves only that no writes occurred.
#
# So this script refuses to render a verdict without a WRITE-VOLUME signal. It gets one
# from the hydrator's own summary line:
#
#     Hydration complete. processed=N  skipped=N  no_data=N  errors=N
#
# `processed` is precisely the number of whales that took the write path. It is captured
# live from `railway logs` (see below), and/or read from the durable job marker.
#
# ⚠️ `whales.last_hydrated_at` is NOT a liveness signal. It is written only when a
#    whale's data CHANGED, so it means "last CHANGED", not "last checked".
# ⚠️ `whale_filing_snapshots.processed_at` is NOT one either. Its trigger is
#    BEFORE UPDATE, so a fresh snapshot INSERT never stamps it.
# ⚠️ Railway log retention is only ~20 minutes, so the capture must run LIVE alongside
#    the measurement. Grepping logs afterwards finds nothing.
#
# AUTH POSTURE: this script runs UNAUTHENTICATED. 55 of 56 profiles come back
# `is_locked: true` / `tier_required: "pro"` with `current_holdings` and `recent_trades`
# stripped to []. Event-loop stall detection is still valid (a blocked loop delays any
# request), but the absolute figures are the LOCKED STUB cost (~1.4 KB), not what a
# signed-in Pro subscriber pays.
# ─────────────────────────────────────────────────────────────────────────────────────
#
# Usage:
#   scripts/measure_whale_latency.sh [minutes]      # default 22
#
# Run it from backend/. Read-only: GETs the public roster + profile endpoints, reads the
# DB, and streams logs. Writes only its own report file.
set -uo pipefail

DURATION_MIN="${1:-22}"
BASE="${WHALE_API_BASE:-https://ai-value-investor-app-production.up.railway.app}"
OUT="/tmp/whale_latency_$(date -u +%Y%m%dT%H%M%SZ).txt"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

DB=$(grep -m1 '^DATABASE_URL=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | sed 's#+asyncpg##')

log() { echo "$@" | tee -a "$OUT"; }

# ── Liveness capture ─────────────────────────────────────────────────────────────────
# ⚠️ `railway logs` STREAMS ONLY ON A TTY. Redirected to a file or a pipe it dumps the
#    backlog (500 lines by default) and EXITS IMMEDIATELY — measured: line count frozen
#    at 500 and the process already dead at t=5s. A background "streaming" capture
#    therefore records only what happened BEFORE the window opened, which is exactly the
#    opposite of what this script needs, and it fails SILENTLY.
#    So: POLL `--lines N` repeatedly and dedupe. At ~325 lines/min under this script's
#    own load, 1000 lines covers ~3 min — ample headroom for a poll every pass (~15-20s).
LOGRAW=$(mktemp)
LOGCAP=$(mktemp)
LOG_POLLS=0
poll_logs() {
  command -v railway >/dev/null 2>&1 || return 0
  ( cd "$REPO_ROOT" && railway logs --lines 1000 2>/dev/null ) >> "$LOGRAW"
  LOG_POLLS=$((LOG_POLLS+1))
}
# Dedupe by whole line. Log lines carry millisecond timestamps, so distinct events stay
# distinct while the overlap between consecutive polls collapses — without this, summing
# `processed=N` across polls would multiply the write count by the number of polls.
finalize_logs() { sort -u "$LOGRAW" > "$LOGCAP"; }

# Whales whose data CHANGED in the last N minutes. Supplementary only — see the warning
# above: 0 here does NOT mean the sweep did not run.
changed_count() {
  [ -z "$DB" ] && { echo "?"; return; }
  PGCONNECT_TIMEOUT=15 psql "$DB" -X -At -c \
    "select count(*) from whales where last_hydrated_at > now() - interval '$1 minutes';" 2>/dev/null || echo "?"
}

# Durable job marker (migration 147+). Prints "ran<TAB>items" or nothing when absent.
marker_probe() {
  [ -z "$DB" ] && return
  PGCONNECT_TIMEOUT=15 psql "$DB" -X -At -F $'\t' -c \
    "select last_run_at, coalesce(items_written,0)
       from notification_job_state
      where job='whale_hydration_full'
        and last_run_at > now() - interval '${DURATION_MIN} minutes' + interval '-10 minutes';" \
    2>/dev/null || true
}

log "=== whale profile latency — hydration window ==="
log "started : $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
log "base    : $BASE"
log "duration: ${DURATION_MIN} min"
log "auth    : ANONYMOUS — 55/56 profiles serve the locked stub (holdings/trades stripped)"
log ""
poll_logs
log "  railway log polling enabled (baseline poll taken)"
log ""

ROSTER=$(mktemp)
if ! curl -s -m 30 "$BASE/api/v1/whales" -o /tmp/_roster.json; then
  log "FATAL: roster fetch failed"; exit 1
fi
# NOTE the trailing newline. Without it bash `while read` silently DROPS the final entry
# — this measured only 55 of 56 whales for its whole life, and Nelson Peltz (last in the
# roster) was never sampled once.
python3 -c "
import json
d=json.load(open('/tmp/_roster.json'))
with open('$ROSTER','w') as f:
    for w in d:
        f.write(f\"{w['name']}\t{w['id']}\n\")
print(len(d))
" > /tmp/_n || { log "FATAL: roster parse failed"; exit 1; }
ROSTER_N=$(cat /tmp/_n)
log "roster  : $ROSTER_N whales"
log ""

ALL=$(mktemp)
END=$(( $(date +%s) + DURATION_MIN * 60 ))
PASS=0

# do-while: ALWAYS complete at least one pass. A duration that has already elapsed (or a
# job that fires late) must still produce a measurement rather than an empty report.
while : ; do
  PASS=$((PASS+1))
  CHG_BEFORE=$(changed_count 3)
  PASSFILE=$(mktemp)
  # `|| [ -n "$name" ]` so a final line without a trailing newline is still processed.
  while IFS=$'\t' read -r name id || [ -n "$name" ]; do
    [ -z "$id" ] && continue
    t=$(curl -s -m 150 -o /dev/null -w "%{time_total}" "$BASE/api/v1/whales/$id/profile" 2>/dev/null)
    [ -n "$t" ] && { echo -e "$t\t$name" >> "$PASSFILE"; echo -e "$t\t$name\t$PASS" >> "$ALL"; }
  done < "$ROSTER"
  CHG_AFTER=$(changed_count 3)

  python3 - "$PASSFILE" "$PASS" "$CHG_BEFORE" "$CHG_AFTER" "$ROSTER_N" <<'PY' | tee -a "$OUT"
import sys
rows=[]
for line in open(sys.argv[1]):
    t,n=line.rstrip('\n').split('\t',1)
    try: rows.append((float(t),n))
    except: pass
expected=int(sys.argv[5])
if rows:
    ts=sorted(t for t,_ in rows); n=len(ts); p=lambda q: ts[min(n-1,int(n*q))]
    worst=max(rows)
    line=(f"pass {sys.argv[2]:>2}  changed(before/after)={sys.argv[3]}/{sys.argv[4]}  "
          f"n={n} median={ts[n//2]:.2f} p95={p(0.95):.2f} max={ts[-1]:.2f} "
          f"over3s={sum(1 for t in ts if t>3)}  worst={worst[1]}")
    if sys.argv[2] == "1":
        line += "   <- WARM-UP (cold builds; excluded from steady state)"
    print(line)
    if n != expected:
        print(f"  ⚠️  SAMPLED {n} of {expected} WHALES — {expected-n} missing from this pass.")
PY
  poll_logs
  [ "$(date +%s)" -lt "$END" ] || break
done

poll_logs
finalize_logs

# ── Write-volume signal ──────────────────────────────────────────────────────────────
# `processed=N` in the hydrator's summary is the count of whales that took the WRITE
# path. Comments are irrelevant here (this greps runtime log output, not source).
HYD_LINES=$(grep -c "Hydration complete\." "$LOGCAP" 2>/dev/null || true)
PROCESSED=$(grep -o "Hydration complete\. processed=[0-9]*" "$LOGCAP" 2>/dev/null \
            | grep -o "[0-9]*$" | awk '{s+=$1} END {print s+0}')
SKIPPED=$(grep -o "skipped=[0-9]*" "$LOGCAP" 2>/dev/null | grep -o "[0-9]*$" | awk '{s+=$1} END {print s+0}')
FULL_DONE=$(grep -c "Full whale hydration completed" "$LOGCAP" 2>/dev/null || true)
POLI_DONE=$(grep -c "Politician whale hydration completed" "$LOGCAP" 2>/dev/null || true)
UNCHANGED=$(grep -c "data unchanged" "$LOGCAP" 2>/dev/null || true)
PREWARM=$(grep -c "Whale profile pre-warm" "$LOGCAP" 2>/dev/null || true)
MARKER=$(marker_probe)

log ""
log "=== WRITE-VOLUME SIGNAL (did anything actually write?) ==="
log "  railway log lines captured      : $(wc -l < "$LOGCAP" | tr -d ' ') (from $LOG_POLLS polls)"
log "  'Hydration complete.' summaries : $HYD_LINES"
log "  whales PROCESSED (wrote)        : $PROCESSED"
log "  whales SKIPPED (hash unchanged) : $SKIPPED"
log "  'data unchanged' lines          : $UNCHANGED"
log "  full sweep completed            : $FULL_DONE"
log "  politician sweep completed      : $POLI_DONE"
log "  WHALE pre-warm lines            : $PREWARM  (a whale pre-warm sweep is a
                                     write window, but it is NOT the hydration)"
log "  durable marker row              : ${MARKER:-<none — migration 147 not applied/deployed>}"

log ""
log "=== OVERALL (all passes combined) ==="
python3 - "$ALL" "$ROSTER_N" "$PROCESSED" "$HYD_LINES" "$FULL_DONE" "$POLI_DONE" "$PREWARM" <<'PY' | tee -a "$OUT"
import sys
rows=[]
for line in open(sys.argv[1]):
    parts=line.rstrip('\n').split('\t')
    if len(parts) < 3: continue
    try: rows.append((float(parts[0]), parts[1], int(parts[2])))
    except: pass
expected   = int(sys.argv[2])
processed  = int(sys.argv[3])
hyd_lines  = int(sys.argv[4])
full_done  = int(sys.argv[5])
poli_done  = int(sys.argv[6])
prewarm    = int(sys.argv[7])

if not rows:
    print("NO SAMPLES — every request failed. Check the API base URL and connectivity;")
    print("this run proves nothing about latency.")
    raise SystemExit(0)

def summarize(sel, label):
    ts=sorted(t for t,_,_ in sel)
    if not ts:
        print(label); print("  no samples"); return None
    n=len(ts); p=lambda q: ts[min(n-1,int(n*q))]
    print(label)
    print(f"  samples={n} min={ts[0]:.2f} median={ts[n//2]:.2f} p95={p(0.95):.2f} "
          f"p99={p(0.99):.2f} max={ts[-1]:.2f}")
    print(f"  over 3s = {sum(1 for t in ts if t>3)}   over 10s = {sum(1 for t in ts if t>10)}")
    return ts[-1]

# ⚠️ Pass 1 is a WARM-UP, and separating it is load-bearing on this schedule.
# `whale_profile_cache` has a 24h TTL and its rows are typically written by the PREVIOUS
# night's activity, so they routinely expire minutes before this run starts — measured on
# 2026-08-20, all 51 rows expired 14-25 minutes before the 01:57 UTC start. Every profile
# in pass 1 is then a cold rebuild, and folding that into the headline figure would
# misattribute cold-build cost to hydration contention. BOTH are printed so nothing is
# hidden: a genuine cold-build regression still shows up in the warm-up line.
summarize(rows, "ALL PASSES (includes the cold warm-up pass)")
print()
steady_max = summarize([r for r in rows if r[2] > 1],
                       "STEADY STATE (pass 2+) — compare THIS to the reference below")

per_pass = {}
for _,_,ps in rows: per_pass[ps] = per_pass.get(ps,0)+1
short = {p:c for p,c in per_pass.items() if c != expected}
if short:
    print(f"\n⚠️  COVERAGE GAP — passes not sampling all {expected} whales: {short}")

print("\nslowest 8 overall:")
for t,name,ps in sorted(rows, reverse=True)[:8]:
    print(f"  {t:7.2f}s  {name}  (pass {ps})")

print("""
REFERENCE (measured 2026-08-20, anonymous/locked-stub responses):
  baseline, no hydration, OLD code    median 0.88  p95 1.40  max  1.74   over3s 0
  OLD code, hydration RUNNING         median 0.22  p95 2.15  max 48.90   over3s 2
  OLD code, hydration RUNNING         median 0.21  p95 0.49  max 40.96   over3s 2
  NEW code, politician sweep RUNNING  median 0.21  p95 0.59  max  7.79   over3s 1
  NEW code, idle                      median 0.21  p95 0.31  max  0.43   over3s 0""")

# ── Three-way verdict ────────────────────────────────────────────────────────────────
print("\n" + "="*78)
ran = (hyd_lines > 0) or (full_done > 0) or (poli_done > 0)
if prewarm and not ran:
    print(f"NOTE: {prewarm} whale pre-warm line(s) seen but NO hydration lifecycle lines.")
    print("      The pre-warmer is a different job — it does not evidence the sweep.")

if not ran:
    print("VERDICT: NO EVIDENCE THE HYDRATION RAN.")
    print("  No hydrator lifecycle lines were captured, so this run says NOTHING about")
    print("  write contention. Either the job did not fire in this window, or the log")
    print("  capture failed (railway CLI missing / not linked / stream died).")
    print("  Do NOT record this as a pass. Re-run with the capture confirmed working.")
elif processed == 0:
    print("VERDICT: INCONCLUSIVE — THE SWEEP RAN BUT WROTE NOTHING.")
    print(f"  processed=0 (whales that took the write path), against the roster of {expected}.")
    print("  Every whale was hash-stable, so `_hydrate_one` returned before any write.")
    print(f"  The measured max of {steady_max:.2f}s therefore reflects an IDLE event loop,")
    print("  NOT bulked writes. It is not evidence that bulking held.")
    print("  13F payloads only move during filing season (~45d after quarter end). To get")
    print("  a real write window, redeploy and measure the pre-warmer sweep instead.")
elif processed < 5:
    print(f"VERDICT: WEAK — only {processed} whale(s) wrote.")
    print(f"  max={steady_max:.2f}s. Too little write volume to generalise to a full sweep;")
    print("  treat as a smoke test, not proof.")
else:
    print(f"VERDICT: CONCLUSIVE — {processed} whales took the write path.")
    if steady_max is not None and steady_max <= 8:
        print(f"  max={steady_max:.2f}s (≤ 8s) — the bulking HELD under real write load.")
    elif steady_max is not None and steady_max <= 20:
        print(f"  max={steady_max:.2f}s — degraded but not the old failure mode. Investigate.")
    else:
        print(f"  max={steady_max:.2f}s — BLOCKING WORK REMAINS, back in the old 20-50s range.")
        print("  Next lever: move the synchronous postgrest calls onto asyncio.to_thread.")
print("="*78)
PY

log ""
log "finished: $(date -u '+%Y-%m-%d %H:%M:%S') UTC"
log "report  : $OUT"
log "logs    : $LOGCAP"
