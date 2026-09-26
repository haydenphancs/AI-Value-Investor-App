"""
The `voiced` stage: narrate the accepted script with Kokoro-82M and publish it as the run's
canonical `audio` asset, its word timings in the asset's metadata (SYSTEM_DESIGN_GUIDELINES §12.7).

Why each piece is shaped the way it is:

* **Kokoro runs in a CHILD process** (`python -m marketing.voice child <job.json>`) under a
  timeout. torch holds 1.5-3.5 GB; if the container's memory limit kills it, only the child dies
  (exit -9 → `VoiceOOM`, recorded, the run retried on the next tick), and a wedged model call
  cannot hold the Railway cron slot forever (Railway skips every later tick while one lives).
  The parent sends a heartbeat PATCH while it waits, so the claim stays live.
* **Seeded synthesis** (`SEED`): Kokoro's vocoder draws random noise, so two unseeded runs of
  the same line differ (measured 2026-09-26); seeded, the same image produces the same bytes,
  and the AAC encode is `bitexact` — so a re-claimed attempt re-registers the SAME
  content-addressed object instead of minting a second public file.
* **Reuse before synthesis**: a `ready` audio asset of this run whose metadata carries the same
  `script_sha256`, `pipeline_version` and voice IS the narration; nothing is synthesised again.
* **The checkpoint carries the pointer**: `metadata.voice_asset_id` goes in the SAME PATCH as
  `stage=voiced` (`run_pipeline` merges the dict this stage returns), so a resume can never see
  the stage without the asset — the render stage reads it back (`GET /runs/{id}/assets`).
* **Duration gate**: the narration must fit MARKETING_MAX_VIDEO_SECONDS minus the disclaimer
  card. Over it, the script is re-synthesised ONCE at the speed that fits (≤ MAX_SPEED); still
  over, the day is skipped (`narration_too_long`) rather than published clipped.

Heavy imports (torch, kokoro, numpy) live inside the child functions only. Nothing here imports
app.* (tests/test_marketing_worker.py scans it).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from marketing import timings as tm

logger = logging.getLogger("marketing.voice")

#: Bump whenever synthesis, grouping or encoding changes: it is part of the reuse key and is
#: written into the m4a, so a new pipeline produces new bytes (a new object), never a silent
#: mismatch between an old file and new timings.
PIPELINE_VERSION = "kokoro-0.9.4+misaki-0.9.4/aac160k-48k/v1"
KOKORO_REPO = "hexgrad/Kokoro-82M"
DEFAULT_VOICE = "af_heart"
SEED = 20260926
MAX_SPEED = 1.15
#: The closing disclaimer card's screen time (the Phase 4 render shows it after the narration).
DISCLAIMER_CARD_SECONDS = 4.0
DEFAULT_MAX_VIDEO_SECONDS = 75
#: The child's whole budget (model load + synthesis). Railway CPUs are slower than a laptop's;
#: measured 0.28× realtime on an M1 with 2 threads, so a 70 s narration is well inside.
VOICE_TIMEOUT_SECONDS = 8 * 60
HEARTBEAT_SECONDS = 60
ENCODE_TIMEOUT_SECONDS = 120
OUTPUT_SAMPLE_RATE = 48000


class VoiceOOM(RuntimeError):
    """The synthesis child was killed (SIGKILL / exit -9): almost always the memory limit."""


class VoiceFailed(RuntimeError):
    """The synthesis child failed, timed out, or produced unusable output."""


class NarrationTooLong(Exception):
    """Even at MAX_SPEED the narration does not fit the video budget."""


@dataclass
class Narration:
    wav_path: Path
    words: List[dict]          # the metadata.words table
    duration: float            # seconds, from the WAV
    speed: float
    exact_lines: int           # lines whose engine tokens aligned exactly


# ── configuration from the worker's environment ──────────────────────────────


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        logger.warning("%s=%r is not a number — using %s", name, raw, default)
        return default


def video_budget_seconds() -> float:
    """Narration budget: MARKETING_MAX_VIDEO_SECONDS (mirrors the web setting of the same name —
    tests/test_marketing_worker.py pins the defaults equal) minus the disclaimer card."""
    return env_float("MARKETING_MAX_VIDEO_SECONDS", DEFAULT_MAX_VIDEO_SECONDS) - DISCLAIMER_CARD_SECONDS


def torch_threads() -> int:
    """The container's CPU quota, not the host's core count (`os.cpu_count()` sees the host, so
    torch would oversubscribe a 2-vCPU slice). MARKETING_TTS_THREADS overrides; else cgroup v2
    `cpu.max`; else 2."""
    raw = os.environ.get("MARKETING_TTS_THREADS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            return max(1, math.ceil(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    return 2


def script_sha256(script: Dict[str, Any]) -> str:
    """Identity of WHAT is narrated (hook + lines), for reuse."""
    lines = tm.narrated_lines(script)
    return hashlib.sha256(json.dumps(lines, ensure_ascii=False).encode("utf-8")).hexdigest()


# ── the child: Kokoro synthesis ───────────────────────────────────────────────


def _child(job_path: str) -> int:
    """Entry point of the synthesis child. Writes narration.wav (24 kHz mono int16) and
    lines.json ([[timed words, duration, exact], …]) into the job's out_dir."""
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    import numpy as np
    import torch
    from kokoro import KPipeline

    torch.set_num_threads(int(job["threads"]))
    pipe = KPipeline(lang_code="a", repo_id=KOKORO_REPO)
    pause = np.zeros(int(tm.LINE_PAUSE_SECONDS * tm.SAMPLE_RATE), dtype=np.float32)
    chunks: List[Any] = []
    per_line: List[list] = []
    for i, line in enumerate(job["lines"]):
        torch.manual_seed(int(job["seed"]) + i)
        samples = 0
        tokens: List[tm.Token] = []
        parts: List[Any] = []
        for r in pipe(line, voice=job["voice"], speed=float(job["speed"])):
            audio = r.audio.numpy() if hasattr(r.audio, "numpy") else np.asarray(r.audio)
            audio = audio.astype(np.float32).reshape(-1)
            tokens += tm.tokens_from_engine(r.tokens, offset=samples / tm.SAMPLE_RATE)
            samples += len(audio)
            parts.append(audio)
        audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        duration = len(audio) / tm.SAMPLE_RATE
        timed, exact = tm.align_line(line, tokens, duration)
        per_line.append([[list(t) for t in timed], duration, exact])
        chunks.append(audio)
        if i + 1 < len(job["lines"]):
            chunks.append(pause)
    full = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
    pcm = (np.clip(full, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    out = Path(job["out_dir"])
    with wave.open(str(out / "narration.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tm.SAMPLE_RATE)
        w.writeframes(pcm)
    (out / "lines.json").write_text(json.dumps(per_line), encoding="utf-8")
    return 0


def run_child(lines: List[str], *, voice: str, speed: float, out_dir: Path,
              heartbeat: Optional[Callable[[], None]] = None,
              timeout: float = VOICE_TIMEOUT_SECONDS) -> Narration:
    """Synthesise `lines` in a child process; heartbeat while waiting; typed failures."""
    job = {"lines": lines, "voice": voice, "speed": speed, "seed": SEED, "threads": torch_threads(),
           "out_dir": str(out_dir)}
    job_path = out_dir / "job.json"
    job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    t0 = time.monotonic()
    proc = subprocess.Popen([sys.executable, "-m", "marketing.voice", "child", str(job_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    last_beat = t0
    while True:
        try:
            proc.wait(timeout=5)
            break
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            if now - t0 > timeout:
                proc.kill()
                proc.wait()
                raise VoiceFailed(f"synthesis timed out after {timeout:.0f}s")
            if heartbeat is not None and now - last_beat >= HEARTBEAT_SECONDS:
                last_beat = now
                heartbeat()
    output = (proc.stdout.read() if proc.stdout else "")[-2000:]
    if proc.returncode in (-9, 137):
        raise VoiceOOM(f"synthesis child killed (exit {proc.returncode}) — the memory limit? {output[-300:]}")
    if proc.returncode != 0:
        raise VoiceFailed(f"synthesis child exited {proc.returncode}: {output[-600:]}")
    per_line = json.loads((out_dir / "lines.json").read_text(encoding="utf-8"))
    wav_path = out_dir / "narration.wav"
    with wave.open(str(wav_path), "rb") as w:
        duration = w.getnframes() / float(w.getframerate())
    words = tm.as_table(tm.assemble(lines, [([tuple(t) for t in timed], d) for timed, d, _e in per_line]))
    exact = sum(1 for _t, _d, e in per_line if e)
    logger.info("narration synthesised lines=%d exact=%d duration=%.1fs wall=%.1fs speed=%.2f",
                len(lines), exact, duration, time.monotonic() - t0, speed)
    return Narration(wav_path, words, duration, speed, exact)


# ── encoding ─────────────────────────────────────────────────────────────────


def encode_m4a(wav_path: Path, out_path: Path) -> Tuple[bytes, float]:
    """WAV → AAC 160k, 48 kHz stereo, faststart, bit-exact (same input → same bytes), with the
    pipeline version in the file. Returns (bytes, duration from ffprobe)."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise VoiceFailed("ffmpeg/ffprobe not found in the image")
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav_path),
           "-map_metadata", "-1", "-ac", "2", "-ar", str(OUTPUT_SAMPLE_RATE), "-c:a", "aac",
           "-b:a", "160k", "-threads", "2", "-fflags", "+bitexact", "-flags:a", "+bitexact",
           "-metadata", f"comment={PIPELINE_VERSION}", "-movflags", "+faststart", str(out_path)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=ENCODE_TIMEOUT_SECONDS)
    if res.returncode != 0:
        raise VoiceFailed(f"ffmpeg encode failed ({res.returncode}): {res.stderr[-600:]}")
    probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of",
                            "csv=p=0", str(out_path)], capture_output=True, text=True, timeout=30)
    try:
        duration = float(probe.stdout.strip())
    except ValueError:
        raise VoiceFailed(f"ffprobe could not read {out_path.name}: {probe.stderr[-300:]}") from None
    return out_path.read_bytes(), duration


# ── the stage ────────────────────────────────────────────────────────────────


def clamp_to_duration(words: List[dict], duration: float) -> List[dict]:
    """The encoded file is the timeline's authority: a caption must not outlast the audio (the
    server tolerates a small tail past it, `validate_audio_words`, but no more). Encoder padding
    makes the m4a a little LONGER than the WAV, so this rarely changes anything; when a table
    does overrun, every time is scaled by duration / last end — order and proportions kept — and
    it is logged.

    The scaled table is rebuilt in ONE forward pass in whole milliseconds, like
    `timings.enforce_order`: each start at least the previous end, each end after its start.
    Rounding s and e separately used to collapse a 1 ms word (a squeezed "—") and then let the
    NEXT word start before its bumped end — a table the server refuses on every retry (review
    2026-09-26: 75 of 56,700 swept placements)."""
    last = max((float(w["e"]) for w in words), default=0.0)
    if last <= duration or last <= 0:
        return [dict(w) for w in words]
    k = duration / last
    logger.warning("narration timings end at %.3fs, after the %.3fs audio — scaled by %.4f", last, duration, k)
    out = []
    prev_end = 0
    for w in words:
        s_ms = max(int(round(float(w["s"]) * k * 1000)), prev_end)
        e_ms = max(int(round(float(w["e"]) * k * 1000)), s_ms + 1)   # never collapses a word
        out.append({**w, "s": s_ms / 1000.0, "e": e_ms / 1000.0})
        prev_end = e_ms
    return out


def _reusable(listing: Dict[str, Any], sha: str, voice: str) -> Optional[str]:
    for a in listing.get("assets") or []:
        md = a.get("metadata") or {}
        if (a.get("kind") == "audio" and a.get("status") == "ready" and md.get("script_sha256") == sha
                and md.get("pipeline_version") == PIPELINE_VERSION and md.get("voice") == voice
                and md.get("words")):
            return a.get("id")
    return None


def synthesize_fitting(lines: List[str], *, voice: str, speed: float, workdir: Path,
                       heartbeat: Optional[Callable[[], None]], budget: float,
                       runner: Callable[..., Narration]) -> Narration:
    """Synthesise; if the narration overruns `budget`, once more at the speed that fits."""
    first = runner(lines, voice=voice, speed=speed, out_dir=workdir, heartbeat=heartbeat)
    if first.duration <= budget:
        return first
    faster = round(min(MAX_SPEED, speed * first.duration / budget * 1.02), 3)
    if faster <= speed:
        raise NarrationTooLong(f"{first.duration:.1f}s > {budget:.1f}s at the maximum speed")
    logger.warning("narration %.1fs > budget %.1fs at speed %.2f — re-synthesising at %.2f",
                   first.duration, budget, speed, faster)
    second_dir = workdir / "retry"
    second_dir.mkdir(exist_ok=True)
    second = runner(lines, voice=voice, speed=faster, out_dir=second_dir, heartbeat=heartbeat)
    if second.duration > budget:
        raise NarrationTooLong(f"{second.duration:.1f}s > {budget:.1f}s even at speed {faster:.2f}")
    return second


def stage_voice(api: Any, run: Dict[str, Any], ctx: Dict[str, Any], *,
                skip: Callable[[str], BaseException],
                uploader: Callable[..., None],
                hasher: Callable[[bytes], str],
                runner: Optional[Callable[..., Narration]] = None,
                encoder: Optional[Callable[[Path, Path], Tuple[bytes, float]]] = None) -> Dict[str, Any]:
    """Phase 3: narrate the accepted script; return the checkpoint metadata
    (`{"voice_asset_id": …}`) that `run_pipeline` writes in the SAME PATCH as `stage=voiced`.

    `skip`, `uploader` and `hasher` are main.py's SkipRun / upload_signed / sha256_hex, INJECTED:
    importing `marketing.main` from here would, under `python -m marketing.main`, load a second
    copy of it — whose SkipRun the pipeline's `except SkipRun` would never catch."""
    SkipRun, upload_signed, sha256_hex = skip, uploader, hasher
    # Looked up at CALL time, so tests (and a future engine switch) replace the module attribute.
    runner = runner or run_child
    encoder = encoder or encode_m4a

    script = ctx["script"]
    lines = tm.narrated_lines(script)
    if not lines:
        raise SkipRun("empty_narration")
    voice = os.environ.get("MARKETING_TTS_VOICE", DEFAULT_VOICE).strip() or DEFAULT_VOICE
    speed = min(max(env_float("MARKETING_TTS_SPEED", 1.0), 0.8), MAX_SPEED)
    sha = script_sha256(script)
    reuse = _reusable(api.list_assets(run["id"]), sha, voice)
    if reuse:
        logger.info("narration REUSED asset=%s (same script, pipeline and voice)", reuse)
        return {"voice_asset_id": reuse}

    def heartbeat() -> None:
        try:
            api.update_run(run["id"])          # bumps the claim's liveness, nothing else
        except Exception as e:  # noqa: BLE001 — a missed beat is logged, the synthesis goes on
            logger.warning("voice heartbeat failed run_id=%s: %s: %s", run["id"], type(e).__name__, e)

    with tempfile.TemporaryDirectory(prefix="voice-") as tmp:
        workdir = Path(tmp)
        try:
            narration = synthesize_fitting(lines, voice=voice, speed=speed, workdir=workdir,
                                           heartbeat=heartbeat, budget=video_budget_seconds(),
                                           runner=runner)
        except NarrationTooLong as e:
            logger.warning("narration too long run_id=%s: %s — skipping the day", run["id"], e)
            raise SkipRun("narration_too_long") from e
        data, duration = encoder(narration.wav_path, workdir / "narration.m4a")
        words = clamp_to_duration(narration.words, duration)
        reg = api.register_asset(
            run["id"], kind="audio", ext="m4a", sha256=sha256_hex(data), bytes=len(data),
            duration_seconds=round(duration, 3),
            metadata={"words": words, "script_sha256": sha,
                      "pipeline_version": PIPELINE_VERSION, "voice": voice,
                      "speed": narration.speed, "seed": SEED, "sample_rate": OUTPUT_SAMPLE_RATE,
                      "lines": len(lines), "exact_lines": narration.exact_lines},
        )
        asset = reg["asset"]
        if reg.get("upload"):
            upload_signed(reg["upload"], data, apikey=ctx.get("apikey"))
            api.complete_asset(asset["id"])
    logger.info("narration READY run_id=%s asset=%s duration=%.1fs words=%d", run["id"], asset["id"],
                duration, len(words))
    return {"voice_asset_id": asset["id"]}


if __name__ == "__main__":  # the synthesis child
    if len(sys.argv) == 3 and sys.argv[1] == "child":
        sys.exit(_child(sys.argv[2]))
    print("usage: python -m marketing.voice child <job.json>", file=sys.stderr)
    sys.exit(2)
