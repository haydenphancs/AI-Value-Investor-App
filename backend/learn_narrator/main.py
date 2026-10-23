#!/usr/bin/env python3
"""
learn_narrator/main.py — the Learn narrator worker (plan "Wiser weekly content engine" §1.8).
Run as `python -m learn_narrator.main` from the image's WORKDIR (/app). Railway runs it on a cron
(`railway.toml`, every 20 minutes, restart Never); each tick claims at most ONE job and exits.

One tick:
  1. preflight — ffmpeg present; which engines have every weight BAKED (`runtime.missing_weights`).
     Only those are declared on the claim: a worker never claims what it would have to download.
  2. `POST /jobs/claim` → 204: exit 0. 200: present the claim on every later call (`hold`).
  3. render in a CHILD process (`runtime.run_child`; torch never enters this process), heartbeating
     every 60 s. Chatterbox: then align the final m4a in a second child (WAV2VEC2, MIT).
  4. build the read-along table (`timings`) and check it the way the server will; measure the file
     (`verify`) against the engine's bands. Any miss → `/fail` with a typed code, never an upload.
  5. `POST /upload` → PUT the bytes to the signed URL → `POST /complete`.

⚠️ STANDALONE BY DESIGN. Nothing under `backend/learn_narrator/` imports `app.*` (a test scans every
file and imports the package in a tree without `app/`): the container holds NO Supabase key, only
the narrator token, and reaches the database through the token-gated internal API. Signed upload
URLs are write credentials: the httpx / httpcore loggers are held at WARNING (they log every
request line, URL and token included, at INFO) and nothing here logs a URL.

Stopping rules:
  * a heartbeat answered 409 (cancelled or re-claimed) or 404 → kill the child, log a WARNING, exit 0
    — the job is no longer ours, so nothing is reported;
  * 401 / 403 on any call → ERROR, exit 2 (a credential problem no retry fixes);
  * a render/alignment/check failure → `/fail` once, exit 1. The SERVER counts worker failures
    (≤ 2 per job, then the round fails) and lease re-claims; this process keeps no count.

Environment:
  LEARN_NARRATOR_API_BASE      https://caydexinvest.com (the web service's public URL)
  LEARN_NARRATOR_API_TOKEN     same value as the web service's LEARN_NARRATOR_TOKEN
  LEARN_NARRATOR_WORKER_VERSION  free-form tag recorded on the claim (default learn-narrator/1)
  LEARN_NARRATOR_ENGINES       optional comma list restricting what this worker declares
  LEARN_NARRATOR_THREADS       optional torch thread count (default: the cgroup cpu.max quota)
  LEARN_NARRATOR_RENDER_TIMEOUT_MINUTES  optional override of the render child's budget
  LEARN_NARRATOR_UPLOAD_APIKEY optional Supabase PUBLISHABLE key sent on the signed PUT (never a
                               secret key — the image holds none)
  LEARN_NARRATOR_LOG_LEVEL     default INFO

Exit codes: 0 = done / nothing to do / job no longer ours; 1 = the job failed (reported) or the
API failed; 2 = configuration or credentials.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logging.basicConfig(
    level=(os.environ.get("LEARN_NARRATOR_LOG_LEVEL") or "INFO").strip().upper(),
    format="%(asctime)s %(levelname)s learn_narrator %(message)s",
)
# httpx logs every request line at INFO — including a signed upload URL WITH its token, which is a
# live write credential. It must never land in Railway logs.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger("learn_narrator")

from learn_narrator import align as al  # noqa: E402 — after the logging setup, on purpose
from learn_narrator import api, runtime, verify  # noqa: E402
from learn_narrator import timings as tm  # noqa: E402
from learn_narrator.engines import ENGINES  # noqa: E402
from learn_narrator.engines import chatterbox as cb  # noqa: E402
from learn_narrator.engines import kokoro as kk  # noqa: E402

WORKER_VERSION = "learn-narrator/1"
EXIT_OK, EXIT_JOB_FAILED, EXIT_CONFIG = 0, 1, 2

#: Wall-clock budget of each child. Chatterbox's benchmark bar (A1) is 150 min for 10.5 min of
#: audio on 4 vCPU; a 12-minute Deep Dive at that rate is ~170 min — 4 h leaves room, and a render
#: that needs more is a runaway (the lease is kept alive by the heartbeat meanwhile).
RENDER_TIMEOUT_SECONDS = {"chatterbox": 4 * 3600, "kokoro": 45 * 60}
ALIGN_TIMEOUT_SECONDS = 45 * 60
HEARTBEAT_SECONDS = runtime.HEARTBEAT_SECONDS

ROLES = frozenset({"context", "body", "item"})
MAX_BLOCKS = 2000
MAX_BLOCK_CHARS = 4000
#: The server stores `checks` as ≤ 64,000 characters of JSON.
MAX_CHECKS_CHARS = 60_000
_FAIL_CODE = re.compile(r"[a-z0-9][a-z0-9_.\-]{0,63}")
_URL = re.compile(r"https?://\S+")


class JobFailure(Exception):
    """A failure of THIS job, reported with `/fail`."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class JobLost(Exception):
    """The job is no longer ours (409 / 404 / 422 on a held call): stop, report nothing."""


@dataclass
class Job:
    id: str
    round: int
    engine: str
    voice: Dict[str, Any]
    pipeline_version: str
    attempts: int
    claim_nonce: str
    blocks: List[Dict[str, Any]]


# ── pure helpers (unit-tested) ────────────────────────────────────────────────


def one_line(text: Any, limit: int = 500) -> str:
    """One line for `/fail`'s detail and the logs: whitespace folded, any URL replaced (a signed
    URL is a credential), capped."""
    return _URL.sub("<url>", " ".join(str(text).split()))[:limit]


def fail_code(code: str) -> str:
    code = (code or "").strip().lower()
    return code if _FAIL_CODE.fullmatch(code) else "worker_error"


def claim_of(raw: Mapping[str, Any]) -> Tuple[str, int, str]:
    """(job id, attempts, claim nonce) — the minimum needed to report on a job. ValueError when the
    claim itself is unreadable (then nothing can be reported: the lease expires)."""
    job_id, attempts, nonce = raw.get("id"), raw.get("attempts"), raw.get("claim_nonce")
    if not (isinstance(job_id, str) and re.fullmatch(r"[0-9A-Za-z\-]{1,64}", job_id)):
        raise ValueError(f"job id {job_id!r}")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError(f"attempts {attempts!r}")
    if not (isinstance(nonce, str) and re.fullmatch(r"[0-9A-Za-z_\-]{1,64}", nonce)):
        raise ValueError("claim_nonce is missing or malformed")
    return job_id, attempts, nonce


def parse_job(raw: Mapping[str, Any]) -> Job:
    """The claimed job, validated. JobFailure("job_invalid") names the first problem. Block texts
    are normalised with `strip_markup` (the server sends them stripped; doing it again is a no-op
    that keeps the spoken words, the aligner's words and the sentence split on one basis)."""
    job_id, attempts, nonce = claim_of(raw)

    def bad(why: str) -> JobFailure:
        return JobFailure("job_invalid", why)

    engine = raw.get("engine")
    if engine not in ENGINES:
        raise JobFailure("engine_unsupported", f"engine {engine!r} (this worker renders {list(ENGINES)})")
    voice = raw.get("voice")
    if not isinstance(voice, dict):
        raise bad("voice is not an object")
    pipeline = raw.get("pipeline_version")
    if not (isinstance(pipeline, str) and 0 < len(pipeline) <= 80):
        raise bad("pipeline_version is missing")
    rnd = raw.get("round")
    if isinstance(rnd, bool) or not isinstance(rnd, int):
        raise bad("round is not an integer")
    blocks = raw.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        raise bad("no blocks")
    if len(blocks) > MAX_BLOCKS:
        raise bad(f"{len(blocks)} blocks (cap {MAX_BLOCKS})")
    out = []
    for pos, b in enumerate(blocks):
        if not isinstance(b, dict):
            raise bad(f"block {pos} is not an object")
        i, text, role = b.get("i"), b.get("text"), b.get("role")
        if isinstance(i, bool) or i != pos:
            raise bad(f"block {pos} has i={i!r} — blocks must be 0..n-1 in order")
        if not isinstance(text, str) or not tm.strip_markup(text).strip():
            raise bad(f"block {pos} has no text")
        if len(text) > MAX_BLOCK_CHARS:
            raise bad(f"block {pos} is {len(text)} characters (cap {MAX_BLOCK_CHARS})")
        if role not in ROLES:
            raise bad(f"block {pos} has role {role!r}")
        out.append({"i": pos, "text": tm.strip_markup(text).strip(), "role": role})
    return Job(id=job_id, round=rnd, engine=engine, voice=dict(voice), pipeline_version=pipeline,
               attempts=attempts, claim_nonce=nonce, blocks=out)


def check_voice(job: Job, env: Optional[Mapping[str, str]] = None) -> None:
    """The job's frozen voice must be one this image can render, with every file baked."""
    if job.engine == "chatterbox":
        try:
            cb.voice_params(job.voice)
        except cb.VoiceUnsupported as e:
            raise JobFailure("voice_unsupported", str(e)) from None
        if cb.ref_sha256() != cb.REF_SHA256:
            raise JobFailure("voice_unsupported", f"{cb.REF.name} does not match its pinned sha256")
        missing = runtime.missing_weights("chatterbox", env=env)
    else:
        try:
            voice = kk.voice_id(job.voice)
        except kk.VoiceUnsupported as e:
            raise JobFailure("voice_unsupported", str(e)) from None
        missing = runtime.missing_weights("kokoro", voice=voice, env=env)
    if missing:
        raise JobFailure("weights_missing", f"not baked into the image: {', '.join(missing)[:400]}")


def available_engines(env: Mapping[str, str]) -> Tuple[List[str], Dict[str, List[str]]]:
    """(engines this image can render, {engine: missing files}). LEARN_NARRATOR_ENGINES narrows it."""
    wanted = [e.strip() for e in (env.get("LEARN_NARRATOR_ENGINES") or "").split(",") if e.strip()]
    for e in wanted:
        if e not in ENGINES:
            logger.error("LEARN_NARRATOR_ENGINES names unknown engine %r — ignored", e)
    candidates = [e for e in ENGINES if not wanted or e in wanted]
    missing = {e: runtime.missing_weights(e, env=env) for e in candidates}
    if "kokoro" in missing and not missing["kokoro"] and not runtime.baked_kokoro_voices(env):
        missing["kokoro"] = ["no Kokoro voice is baked"]
    return [e for e in candidates if not missing[e]], missing


def block_spans(job: Job, render: Mapping[str, Any], aligned: Optional[Mapping[str, Any]]):
    """Per block: (word spans, window, extra checks) from the children's outputs."""
    sr = int(render["sample_rate"])
    atempo = float(render.get("atempo") or 1.0)
    rblocks = {int(b["i"]): b for b in render["blocks"]}
    ablocks = {int(b["i"]): b for b in (aligned or {}).get("blocks", [])}
    out = []
    for b in job.blocks:
        rb = rblocks.get(b["i"])
        if rb is None:
            raise JobFailure("render_failed", f"the render has no block {b['i']}")
        core_lo = int(rb["raw_offset"]) / sr / atempo
        core_hi = (int(rb["raw_offset"]) + int(rb["raw_samples"])) / sr / atempo
        extra: Dict[str, Any] = {}
        if job.engine == "chatterbox":
            ab = ablocks.get(b["i"])
            if ab is None:
                raise JobFailure("align_failed", f"the alignment has no block {b['i']}")
            spans = [None if w is None else (float(w[0]), float(w[1])) for w in ab["words"]]
            first = next((s[0] for s in spans if s is not None), None)
            extra = {"confidence": ab.get("confidence"), "word_coverage": ab.get("word_coverage"),
                     "start_error": al.start_error(first, core_lo, float(rb.get("lead_silence") or 0.0), atempo)}
        else:
            spans = [(float(w[0]), float(w[1])) for w in rb["word_spans"]]
            extra = {"engine_coverage": rb.get("align_coverage"), "exact": rb.get("exact")}
        out.append((spans, (core_lo, core_hi), extra))
    return out


def build_timings(job: Job, render: Mapping[str, Any], aligned: Optional[Mapping[str, Any]]):
    """(timing table, per-block checks)."""
    rblocks = {int(b["i"]): b for b in render["blocks"]}
    table, checks = [], []
    for b, (spans, window, extra) in zip(job.blocks, block_spans(job, render, aligned)):
        try:
            entry, stats = tm.build_block(b["i"], b["text"], spans, window=window)
        except ValueError as e:
            raise JobFailure("timings_invalid", str(e)) from None
        rb = rblocks[b["i"]]
        table.append(entry)
        if len(rb.get("calls") or []) > 1:          # a block split to stay under Chatterbox's 40 s cap
            extra = {**extra, "calls": len(rb["calls"]), "max_call_seconds": rb.get("max_call_seconds")}
        checks.append({"i": b["i"], "raw_seconds": rb.get("raw_seconds"), "words_per_sec": rb.get("words_per_sec"),
                       "align_coverage": stats["align_coverage"], "rerendered": bool(rb.get("rerendered")),
                       **{k: v for k, v in extra.items() if v is not None}})
    return tm.enforce_table_order(table), checks


def bounded_checks(checks: Dict[str, Any]) -> Dict[str, Any]:
    """`checks` within the server's size cap: drop the optional per-block keys first, then the
    per-block list (the summary stays)."""
    if len(json.dumps(checks)) <= MAX_CHECKS_CHARS:
        return checks
    slim = dict(checks)
    slim["blocks"] = [{k: b[k] for k in ("i", "raw_seconds", "words_per_sec", "align_coverage", "rerendered")
                       if k in b} for b in checks.get("blocks", [])]
    if len(json.dumps(slim)) <= MAX_CHECKS_CHARS:
        return slim
    slim["blocks"] = []
    slim["blocks_dropped"] = len(checks.get("blocks", []))
    return slim


def summary(block_checks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    def worst(key: str, fn=min):
        vals = [b[key] for b in block_checks if isinstance(b.get(key), (int, float))]
        return fn(vals) if vals else None
    return {"blocks": len(block_checks), "rerendered": sum(1 for b in block_checks if b.get("rerendered")),
            "min_align_coverage": worst("align_coverage"), "min_confidence": worst("confidence"),
            "max_start_error": worst("start_error", max), "max_raw_seconds": worst("raw_seconds", max),
            "min_words_per_sec": worst("words_per_sec"), "max_words_per_sec": worst("words_per_sec", max)}


# ── the job ──────────────────────────────────────────────────────────────────


class Heartbeat:
    """Keeps the lease alive. 404 / 409 / 422 → JobLost (cancelled, re-claimed or gone);
    401 / 403 propagate (credentials); a transient failure is logged and the work goes on — the
    lease is 10 minutes, a missed beat is not fatal."""

    def __init__(self, client: api.NarratorClient, job_id: str, *, every: float = HEARTBEAT_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.client, self.job_id, self.every, self.clock = client, job_id, every, clock
        self.last = clock()

    def __call__(self) -> None:
        self.last = self.clock()
        try:
            self.client.heartbeat(self.job_id)
        except api.NarratorAuthError:
            raise
        except api.NarratorTerminalError as e:
            raise JobLost(f"heartbeat {e}") from None
        except api.NarratorAPIError as e:
            logger.warning("heartbeat failed job=%s: %s (the lease outlives one missed beat)", self.job_id, e)

    def maybe(self) -> None:
        if self.clock() - self.last >= self.every:
            self()


def child_job(job: Job, out_dir: Path, threads: int) -> Dict[str, Any]:
    return {"id": job.id, "engine": job.engine, "voice": job.voice, "blocks": job.blocks,
            "seed": cb.SEED, "threads": threads, "out_dir": str(out_dir),
            "pipeline_version": job.pipeline_version}


def render_timeout(engine: str, env: Mapping[str, str]) -> float:
    raw = (env.get("LEARN_NARRATOR_RENDER_TIMEOUT_MINUTES") or "").strip()
    if raw:
        try:
            minutes = float(raw)
            if minutes > 0:
                return minutes * 60
        except ValueError:
            pass
        logger.warning("LEARN_NARRATOR_RENDER_TIMEOUT_MINUTES=%r is not a positive number — ignored", raw)
    return float(RENDER_TIMEOUT_SECONDS[engine])


def report_failure(client: api.NarratorClient, job_id: str, code: str, detail: str) -> None:
    code, detail = fail_code(code), one_line(detail)
    logger.error("narration FAILED job=%s code=%s: %s", job_id, code, detail)
    try:
        resp = client.fail(job_id, code, detail)
        logger.info("failure recorded job=%s status=%s worker_failures=%s", job_id, resp.get("status"),
                    resp.get("worker_failures"))
    except api.NarratorAuthError:
        raise
    except api.NarratorTerminalError as e:
        logger.warning("fail not recorded job=%s (the job is no longer ours): %s", job_id, e)
    except api.NarratorAPIError as e:
        logger.error("fail not recorded job=%s: %s — the lease will expire and the server re-claims", job_id, e)


def process(client: api.NarratorClient, job: Job, workdir: Path, *, threads: int,
            env: Mapping[str, str], runner: Callable[..., dict] = runtime.run_child,
            measure: Callable[..., Dict[str, Any]] = verify.measure,
            heartbeat: Optional[Heartbeat] = None) -> int:
    """Render, check, upload and complete one claimed job. Returns the exit code."""
    hb = heartbeat or Heartbeat(client, job.id)
    t0 = time.monotonic()
    try:
        check_voice(job, env)
        job_path = workdir / "job.json"
        job_path.write_text(json.dumps(child_job(job, workdir, threads), ensure_ascii=False), encoding="utf-8")
        logger.info("rendering job=%s round=%s engine=%s blocks=%d words=%d threads=%d", job.id, job.round,
                    job.engine, len(job.blocks), sum(len(b["text"].split()) for b in job.blocks), threads)
        render = runner("render", job_path, workdir, timeout=render_timeout(job.engine, env), threads=threads,
                        heartbeat=hb)
        aligned = None
        if job.engine == "chatterbox":
            hb.maybe()
            aligned = runner("align", job_path, workdir, timeout=ALIGN_TIMEOUT_SECONDS, threads=threads, heartbeat=hb)
        audio = workdir / str(render.get("audio") or "narration.m4a")
        if not audio.is_file() or audio.stat().st_size == 0:
            raise JobFailure("render_failed", "the render produced no audio file")
        table, block_checks = build_timings(job, render, aligned)
        words = sum(len(b["text"].split()) for b in job.blocks)
        file_m = measure(audio, words)
        duration = file_m.get("duration")
        if not duration:
            raise JobFailure("encode_failed", "ffprobe could not read the encoded file's duration")
        problems = tm.check_table(job.blocks, table, duration)
        if problems:
            raise JobFailure("timings_invalid", f"{len(problems)} problem(s): " + "; ".join(problems[:3]))
        bands = verify.band_problems(job.engine, file_m)
        if bands:
            raise JobFailure("voice_out_of_band", "; ".join(bands))
        data = audio.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        hb.maybe()
        target = client.upload_target(job.id, size_bytes=len(data), sha256=sha)
        if target.get("upload_url") and not target.get("already_uploaded"):
            try:
                client.put_signed(str(target["upload_url"]), data, path=str(target["path"]),
                                  apikey=(env.get("LEARN_NARRATOR_UPLOAD_APIKEY") or "").strip() or None)
            except api.NarratorUnavailable:
                raise
            except api.NarratorAPIError as e:      # Storage refused the PUT (not our API token)
                raise JobFailure("upload_failed", str(e)) from None
        else:
            logger.info("upload skipped job=%s: %s is already in the bucket", job.id, target["path"])
        file_checks = {k: file_m.get(k) for k in ("f0_hz", "wpm", "bytes_per_sec", "lufs", "duration")}
        checks = bounded_checks({
            "blocks": block_checks, "file": file_checks, "summary": summary(block_checks),
            "worker": {"worker_version": (env.get("LEARN_NARRATOR_WORKER_VERSION") or WORKER_VERSION)[:80],
                       "engine_pipeline": render.get("pipeline"), "atempo": render.get("atempo"),
                       "threads": threads, "render_seconds": render.get("wall_seconds"),
                       "align_seconds": (aligned or {}).get("wall_seconds"),
                       "emotion_adv": render.get("emotion_adv"), "voice": render.get("voice"),
                       "aligner": (aligned or {}).get("model")},
        })
        payload = {"path": target["path"], "bytes": len(data), "duration_seconds": round(float(duration), 3),
                   "engine": job.engine, "pipeline_version": job.pipeline_version, "blocks": table, "checks": checks}
        resp = client.complete(job.id, payload)
        logger.info("narration COMPLETE job=%s path=%s bytes=%d duration=%.1fs wall=%.0fs status=%s replay=%s",
                    job.id, target["path"], len(data), duration, time.monotonic() - t0, resp.get("status"),
                    resp.get("replay"))
        return EXIT_OK
    except JobLost as e:
        logger.warning("job=%s is no longer ours — stopping without reporting: %s", job.id, e)
        return EXIT_OK
    except api.NarratorAuthError:
        raise
    except api.NarratorTerminalError as e:
        if e.status == 422:
            report_failure(client, job.id, "request_refused", f"the server refused a call: {e}")
            return EXIT_JOB_FAILED
        logger.warning("job=%s is no longer ours (%s) — stopping", job.id, e)
        return EXIT_OK
    except JobFailure as f:
        report_failure(client, job.id, f.code, f.detail)
        return EXIT_JOB_FAILED
    except runtime.ChildFailed as f:
        report_failure(client, job.id, f.code, f.detail)
        return EXIT_JOB_FAILED
    except api.NarratorUnavailable as e:
        # The API itself is down (every retry failed): a /fail would fail the same way, and the render
        # was not at fault — leave the job to its lease (the server re-claims it, bounded by its
        # re-claim cap) rather than spending one of its worker failures.
        logger.error("job=%s: the API is unavailable (%s) — leaving the job to its lease", job.id, e)
        return EXIT_JOB_FAILED
    except api.NarratorAPIError as e:
        report_failure(client, job.id, "api_error", str(e))
        return EXIT_JOB_FAILED
    except Exception as e:  # noqa: BLE001 — a bug must still reach /fail and the log, loudly
        logger.exception("unexpected error job=%s", job.id)
        report_failure(client, job.id, "worker_error", f"{type(e).__name__}: {e}")
        return EXIT_JOB_FAILED


def preflight(env: Mapping[str, str]) -> Dict[str, Any]:
    engines, missing = available_engines(env)
    return {"engines": engines, "missing": {k: v for k, v in missing.items() if v},
            "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe")),
            "threads": runtime.torch_threads(env), "kokoro_voices": runtime.baked_kokoro_voices(env),
            "reference_ok": cb.ref_sha256() == cb.REF_SHA256}


def main(argv: Optional[List[str]] = None, *, env: Optional[Mapping[str, str]] = None,
         client_factory: Optional[Callable[[str, str], api.NarratorClient]] = None,
         runner: Callable[..., dict] = runtime.run_child) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    report = preflight(env)
    if "--preflight" in argv:
        print(json.dumps(report, indent=2))
        return EXIT_OK if report["engines"] and report["ffmpeg"] and report["ffprobe"] else EXIT_CONFIG
    base = (env.get("LEARN_NARRATOR_API_BASE") or "").strip()
    token = (env.get("LEARN_NARRATOR_API_TOKEN") or "").strip()
    if not base or not token:
        logger.error("LEARN_NARRATOR_API_BASE and LEARN_NARRATOR_API_TOKEN must both be set")
        return EXIT_CONFIG
    if not (report["ffmpeg"] and report["ffprobe"]):
        logger.error("ffmpeg/ffprobe not found in the image")
        return EXIT_CONFIG
    if not report["engines"]:
        logger.error("no engine is ready — missing baked weights: %s", report["missing"])
        return EXIT_CONFIG
    if report["missing"]:
        logger.warning("engines not declared (weights missing): %s", report["missing"])
    worker_version = (env.get("LEARN_NARRATOR_WORKER_VERSION") or WORKER_VERSION).strip()[:80] or WORKER_VERSION
    client = (client_factory or (lambda b, t: api.NarratorClient(b, t)))(base, token)
    try:
        try:
            raw = client.claim(report["engines"], worker_version)
        except api.NarratorAuthError as e:
            logger.error("claim refused — the narrator token is wrong or unset on the web: %s", e)
            return EXIT_CONFIG
        except api.NarratorAPIError as e:
            logger.error("claim failed: %s", e)
            return EXIT_JOB_FAILED
        if raw is None:
            logger.info("no narration job (engines=%s)", report["engines"])
            return EXIT_OK
        try:
            job_id, attempts, nonce = claim_of(raw)
        except ValueError as e:
            logger.error("unreadable claim (%s) — nothing can be reported; the lease will expire", e)
            return EXIT_JOB_FAILED
        client.hold(attempts, nonce)
        logger.info("claimed job=%s attempts=%d engine=%s", job_id, attempts, raw.get("engine"))
        try:
            job = parse_job(raw)
        except JobFailure as f:
            report_failure(client, job_id, f.code, f.detail)
            return EXIT_JOB_FAILED
        with tempfile.TemporaryDirectory(prefix="learn-narrator-") as tmp:
            return process(client, job, Path(tmp), threads=report["threads"], env=env, runner=runner)
    except api.NarratorAuthError as e:
        logger.error("the API refused the narrator token mid-job: %s", e)
        return EXIT_CONFIG
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
