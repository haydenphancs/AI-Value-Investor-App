#!/usr/bin/env python3
"""
Reusable cost + time meter. Wraps ANY command, prints a live elapsed/cost line every
15s, prints a final summary, and appends one record to
backend/data/book_audio/runpod_cost_log.jsonl.

    python scripts/runpod/time_and_cost.py --label "clone book 2" --rate 0.34 -- \
        ./venv_clone/bin/python scripts/generate_book_audio_clone.py 2

Rate comes from --rate or $RUNPOD_RATE_USD_PER_HR ($/hr of the GPU box).

NOTE: this meters the WRAPPED COMMAND's wall time. RunPod bills TOTAL POD UPTIME
(boot -> terminate, incl. setup, model download, idle), which is larger — so treat
this number as a lower bound and reconcile against the RunPod console's billed minutes.
Discipline: spin up -> run -> sync down -> TERMINATE promptly.

Summarize the running ledger any time:
    python scripts/runpod/time_and_cost.py --summary
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

LOG = Path(__file__).resolve().parents[2] / "data/book_audio/runpod_cost_log.jsonl"


def _fmt(elapsed: float, rate: float) -> str:
    cost = elapsed / 3600.0 * rate
    m, s = divmod(int(elapsed), 60)
    return f"elapsed {m}m{s:02d}s, ${cost:.2f} at ${rate:.2f}/hr"


def _summary() -> int:
    if not LOG.exists():
        print(f"no ledger yet at {LOG}")
        return 0
    total_s = total_c = 0.0
    n = 0
    for line in LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        n += 1
        total_s += float(r.get("elapsed_seconds", 0) or 0)
        total_c += float(r.get("cost_usd", 0) or 0)
        print(f"  {r.get('ts','?'):26} {r.get('label','?'):24} "
              f"{float(r.get('elapsed_seconds',0))/60:6.1f}m  ${float(r.get('cost_usd',0)):.2f}")
    print(f"  {'-'*60}")
    print(f"  {n} job(s)  ·  {total_s/60:.1f}m total  ·  ${total_c:.2f} total")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="job")
    ap.add_argument("--rate", type=float,
                    default=float(os.environ.get("RUNPOD_RATE_USD_PER_HR", "0")))
    ap.add_argument("--summary", action="store_true", help="print the cost ledger and exit")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()

    if a.summary:
        sys.exit(_summary())

    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        sys.exit("usage: time_and_cost.py --label L --rate R -- <command...>   (or --summary)")
    if a.rate <= 0:
        print("WARNING: rate is 0 — set --rate or RUNPOD_RATE_USD_PER_HR for a real cost.",
              file=sys.stderr, flush=True)

    start = time.time()
    stop = threading.Event()

    def ticker() -> None:
        while not stop.wait(15):
            print(f"  [cost] {a.label}: {_fmt(time.time() - start, a.rate)}", flush=True)

    threading.Thread(target=ticker, daemon=True).start()
    print(f"[cost] START {a.label} @ {datetime.now(timezone.utc).isoformat()} "
          f"rate=${a.rate:.2f}/hr  (REMINDER: pod bills boot->terminate, not just this command)",
          flush=True)

    rc = subprocess.call(cmd)
    elapsed = time.time() - start
    stop.set()
    print(f"[cost] DONE {a.label}: {_fmt(elapsed, a.rate)} (exit {rc})", flush=True)

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "label": a.label,
            "cmd": cmd,
            "elapsed_seconds": round(elapsed, 1),
            "rate_usd_per_hr": a.rate,
            "cost_usd": round(elapsed / 3600.0 * a.rate, 4),
            "exit_code": rc,
        }) + "\n")
    sys.exit(rc)


if __name__ == "__main__":
    main()
