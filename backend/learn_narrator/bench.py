"""
learn_narrator/bench.py — the Phase-0 narrator benchmark (plan "Wiser weekly content engine",
§Phase 0): can the catalog voice (Chatterbox) run on CPU inside the worker image?

    docker run --rm --cpus=4 --memory=8g -v /tmp/bench:/tmp/bench <image> \
        python -m learn_narrator.bench --out /tmp/bench/run1

It renders `bench_fixture.json` (~1,600 words ≈ 10.5 min: how-amazon-built-its-moat in full, the
start of costcos-membership-magic, and the worst cases — a 75-word block dense with figures, a
3-word block, acronyms and em-dashes) through EXACTLY the worker's path (the same children, the
same gate, assembly, alignment, table check and voice bands) and grades it:

  A1  wall time (render + align)                       ≤ 150 min
  A2  peak RSS of the largest child                    ≤ 6 GB
  A3  no block ≥ 39.5 s raw; every block 1.8–3.6 spoken words/s (after the one re-render)
  A4  verify_learn_voice bands (F0, wpm, B/s, LUFS)    all in band
  A5  owner blind A/B against the catalog clips        PENDING — the kit is written for the owner
  A6  read-along: ≥ 98 % of sentences aligned, the table passes the server's checks (monotonic,
      bands), block-start error ≤ 0.15 s
  + the prepared conditioning's emotion_adv equals the requested exaggeration (0.65).

Safety: it REFUSES to start when any SUPABASE_* or LEARN_NARRATOR_API_* variable is present (it
must never be able to reach production), never calls the API, and writes only under /tmp.
Exit 0 = every automated check passed (A5 still needs the owner), 1 = a check failed, 2 = refused.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import resource
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from learn_narrator import runtime, verify
from learn_narrator import timings as tm
from learn_narrator.engines import chatterbox as cb

FIXTURE = Path(__file__).resolve().with_name("bench_fixture.json")
TMP_ROOT = Path("/tmp")
FORBIDDEN_PREFIXES = ("SUPABASE_", "LEARN_NARRATOR_API_")

A1_WALL_SECONDS = 150 * 60
A2_PEAK_RSS_BYTES = 6 * 1024 ** 3
A6_SENTENCE_COVERAGE = 0.98
A6_START_ERROR_SECONDS = 0.15
#: The bench's own child budgets: generous (a slow box should FAIL A1 with a number, not time out).
RENDER_TIMEOUT_SECONDS = 2 * A1_WALL_SECONDS
ALIGN_TIMEOUT_SECONDS = 60 * 60


def forbidden_env(env: Mapping[str, str]) -> List[str]:
    """Names of variables that could point this process at production (present at all = refused)."""
    return sorted(k for k in env if k.startswith(FORBIDDEN_PREFIXES))


def under_tmp(path: Path) -> bool:
    try:
        path.resolve().relative_to(TMP_ROOT.resolve())
        return True
    except ValueError:
        return False


def default_out() -> Path:
    return TMP_ROOT / "learn_narrator_bench" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_fixture(path: Path = FIXTURE) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_job(fixture: Mapping[str, Any], engine: str, voice: Optional[str]):
    from learn_narrator import main as m

    raw = {"id": "bench", "round": 0, "engine": engine, "attempts": 0, "claim_nonce": "bench",
           "pipeline_version": "bench",
           "voice": dict(cb_voice()) if engine == "chatterbox" else {"voice": voice or "am_michael"},
           "blocks": [{"i": b["i"], "text": b["text"], "role": b["role"]} for b in fixture["blocks"]]}
    return m.parse_job(raw)


def cb_voice() -> Dict[str, Any]:
    return {"exaggeration": cb.EXAG, "cfg_weight": cb.CFG, "target_wpm": cb.TARGET_WPM, "ref": cb.REF_NAME}


def peak_child_rss_bytes() -> int:
    """Max RSS of the largest finished child (Linux reports KiB, macOS bytes)."""
    raw = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return int(raw if sys.platform == "darwin" else raw * 1024)


def cgroup_peak_bytes() -> Optional[int]:
    try:
        return int(Path("/sys/fs/cgroup/memory.peak").read_text().strip())
    except (OSError, ValueError):
        return None


def grade(*, engine: str, render: Mapping[str, Any], table: List[dict], block_checks: List[dict],
          table_problems: List[str], file_m: Mapping[str, Any], wall_seconds: float,
          peak_rss: int) -> Dict[str, Any]:
    """The A1–A6 verdicts from what the run measured (pure, so the thresholds are tested)."""
    rblocks = render["blocks"]

    def calls(b: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        # The gate is per Chatterbox CALL (a split block has several); a block without a call list
        # is one call.
        return b.get("calls") or [{"raw_seconds": b["raw_seconds"], "words_per_sec": b["words_per_sec"],
                                   "spoken_words": b.get("spoken_words")}]

    def in_band(c: Mapping[str, Any]) -> bool:
        lo_, hi_ = cb.band_for(int(c.get("spoken_words") or cb.SHORT_CALL_WORDS))
        return lo_ <= float(c["words_per_sec"]) <= hi_

    truncated = [b["i"] for b in rblocks if any(float(c["raw_seconds"]) >= cb.TRUNCATION_SECONDS for c in calls(b))]
    off_band = [b["i"] for b in rblocks if any(not in_band(c) for c in calls(b))]
    first_failures = [{"i": b["i"], "reason": b.get("first_reason")} for b in rblocks if b.get("first_reason")]
    total_sents = sum(len(e["sentences"]) for e in table)
    covered = sum(round(c["align_coverage"] * len(e["sentences"])) for c, e in zip(block_checks, table))
    coverage = covered / total_sents if total_sents else 0.0
    errors = [c["start_error"] for c in block_checks if isinstance(c.get("start_error"), (int, float))]
    max_err = max(errors) if errors else None
    bands = verify.band_problems(engine, dict(file_m))
    out: Dict[str, Any] = {
        "A1": {"pass": wall_seconds <= A1_WALL_SECONDS, "wall_minutes": round(wall_seconds / 60, 1),
               "bar_minutes": A1_WALL_SECONDS / 60},
        "A2": {"pass": peak_rss <= A2_PEAK_RSS_BYTES, "peak_rss_gb": round(peak_rss / 1024 ** 3, 2),
               "bar_gb": A2_PEAK_RSS_BYTES / 1024 ** 3},
        "A3": {"pass": not truncated and not off_band, "truncated": truncated, "off_band": off_band,
               "rerendered": sum(1 for b in rblocks if b.get("rerendered")), "first_attempt_failures": first_failures,
               "split_blocks": [b["i"] for b in rblocks if len(calls(b)) > 1],
               "max_call_seconds": max(float(c["raw_seconds"]) for b in rblocks for c in calls(b))},
        "A4": {"pass": not bands, "problems": bands, **{k: file_m.get(k) for k in ("f0_hz", "wpm", "bytes_per_sec", "lufs")}},
        "A5": {"pass": None, "status": "PENDING — the owner's blind A/B (ab_kit/)"},
        "A6": {"pass": (coverage >= A6_SENTENCE_COVERAGE and not table_problems
                        and (engine != "chatterbox" or (max_err is not None and max_err <= A6_START_ERROR_SECONDS))),
               "sentence_coverage": round(coverage, 4), "max_start_error": max_err,
               "table_problems": table_problems[:10]},
    }
    if engine == "chatterbox":
        emo = render.get("emotion_adv")
        out["exaggeration"] = {"pass": emo is not None and abs(float(emo) - cb.EXAG) < 1e-6, "emotion_adv": emo}
    return out


def write_ab_kit(out_dir: Path, audio: Path, fixture: Mapping[str, Any], table: List[dict],
                 catalog_dir: Optional[Path], seed: int = 0) -> Dict[str, Any]:
    """Cut the render into its A/B segments; with `catalog_dir` (holding the catalog's <slug>.m4a),
    pair each with the catalog clip under random A/B labels and write the key separately."""
    kit = out_dir / "ab_kit"
    kit.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    key: Dict[str, Any] = {}
    for slug, (a, b) in fixture["ab_segments"].items():
        start = max(0.0, table[a]["start"] - 0.2)
        end = table[b - 1]["end"] + 0.4
        new = kit / f"{slug}.new.m4a"
        subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                        "-i", str(audio), "-c:a", "aac", "-b:a", "96k", str(new)], check=True, timeout=300)
        if catalog_dir is not None and (catalog_dir / f"{slug}.m4a").exists():
            pair = [("new", new), ("catalog", catalog_dir / f"{slug}.m4a")]
            rng.shuffle(pair)
            for label, (kind, src) in zip(("A", "B"), pair):
                shutil.copyfile(src, kit / f"{slug}.{label}.m4a")
                key[f"{slug}.{label}"] = kind
            new.unlink()
    if key:
        (out_dir / "ab_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    return key


def run(args: argparse.Namespace, *, env: Mapping[str, str],
        runner: Callable[..., dict] = runtime.run_child,
        measure: Callable[..., Dict[str, Any]] = verify.measure,
        clock: Callable[[], float] = time.monotonic) -> int:
    from learn_narrator import main as m

    out_dir = Path(args.out)
    if not under_tmp(out_dir):
        print(f"refused: --out {out_dir} is not under {TMP_ROOT}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    fixture = load_fixture()
    job = fixture_job(fixture, args.engine, args.voice)
    try:
        m.check_voice(job, env)
    except m.JobFailure as f:
        print(f"bench: cannot run {args.engine}: {f.code}: {f.detail}", file=sys.stderr)
        return 1
    threads = args.threads or runtime.torch_threads(env)
    job_path = out_dir / "job.json"
    job_path.write_text(json.dumps(m.child_job(job, out_dir, threads), ensure_ascii=False), encoding="utf-8")
    print(f"bench: engine={job.engine} blocks={len(job.blocks)} words={fixture['words']} threads={threads} out={out_dir}",
          flush=True)
    t0 = clock()
    render = runner("render", job_path, out_dir, timeout=RENDER_TIMEOUT_SECONDS, threads=threads, heartbeat=None)
    render_s = clock() - t0
    aligned = None
    if job.engine == "chatterbox":
        aligned = runner("align", job_path, out_dir, timeout=ALIGN_TIMEOUT_SECONDS, threads=threads, heartbeat=None)
    wall = clock() - t0
    peak = peak_child_rss_bytes()
    table, block_checks = m.build_timings(job, render, aligned)
    audio = out_dir / render["audio"]
    file_m = measure(audio, sum(len(b["text"].split()) for b in job.blocks))
    problems = tm.check_table(job.blocks, table, file_m.get("duration"))
    report = {"engine": job.engine, "threads": threads, "render_seconds": round(render_s, 1),
              "wall_seconds": round(wall, 1), "cgroup_memory_peak": cgroup_peak_bytes(),
              "audio": str(audio), "file": file_m, "summary": m.summary(block_checks),
              "grades": grade(engine=job.engine, render=render, table=table, block_checks=block_checks,
                              table_problems=problems, file_m=file_m, wall_seconds=wall, peak_rss=peak),
              "blocks": block_checks}
    if not args.no_ab_kit:
        report["ab_key_written"] = bool(write_ab_kit(out_dir, audio, fixture, table,
                                                     Path(args.catalog_dir) if args.catalog_dir else None))
    (out_dir / "timings.json").write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"{k}: {'PASS' if v['pass'] else ('PENDING' if v['pass'] is None else 'FAIL')}  "
             + json.dumps({kk: vv for kk, vv in v.items() if kk != 'pass'}, ensure_ascii=False)
             for k, v in report["grades"].items()]
    text = "\n".join(lines)
    (out_dir / "report.txt").write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    automated = [v["pass"] for v in report["grades"].values() if v["pass"] is not None]
    return 0 if all(automated) else 1


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m learn_narrator.bench", description=__doc__.split("\n\n")[0])
    p.add_argument("--engine", choices=("chatterbox", "kokoro"), default="chatterbox")
    p.add_argument("--voice", default=None, help="Kokoro voice id (default am_michael)")
    p.add_argument("--out", default=str(default_out()), help="output directory (must be under /tmp)")
    p.add_argument("--catalog-dir", default=None, help="folder holding the catalog's <slug>.m4a for the blind A/B")
    p.add_argument("--threads", type=int, default=0)
    p.add_argument("--no-ab-kit", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None, env: Optional[Mapping[str, str]] = None) -> int:
    env = os.environ if env is None else env
    bad = forbidden_env(env)
    if bad:
        print(f"refused: {', '.join(bad)} set — the bench must not be able to reach production. "
              "Unset them (or run in a clean container).", file=sys.stderr)
        return 2
    return run(parse_args(argv), env=env)


if __name__ == "__main__":
    sys.exit(main())
