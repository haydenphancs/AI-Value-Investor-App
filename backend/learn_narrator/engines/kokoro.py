"""
learn_narrator/engines/kokoro.py — the fallback engine: Kokoro-82M (Apache-2.0), used if the
Chatterbox benchmark fails (plan Phase 0: pick a MALE voice — the catalog narrator sits at
F0 ≈ 133 Hz; `af_heart`, marketing's voice, is female). The job freezes the voice id
(`{"voice": "am_michael"}`); it must be baked into the image (`runtime.baked_kokoro_voices`).

Read-along comes from Kokoro's OWN word timestamps — no aligner. misaki's tokens are grouped into
the block's whitespace words by `timings.align_line` (vendored from marketing/timings.py, measured
on the same kokoro 0.9.4 / misaki 0.9.4), then into the server's sentences by `timings.build_block`.

Assembly: blocks at Kokoro's natural pace (speed 1.0, no tempo pass), BLOCK_PAUSE between them,
AAC 96k + faststart. The per-block gate is the spoken words/s band only — Kokoro chunks long text
itself, so there is no 40 s cap to detect.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from learn_narrator import timings as tm
from learn_narrator.engines import chatterbox as cb

PIPELINE_VERSION = "kokoro-0.9.4+misaki-0.9.4/block/aac96k/v1"
KOKORO_REPO = "hexgrad/Kokoro-82M"
SR = 24000
SPEED = 1.0
BLOCK_PAUSE = cb.BLOCK_PAUSE
SEED = cb.SEED
ENCODE_TIMEOUT_SECONDS = 600
_VOICE_ID = re.compile(r"[ab][fm]_[a-z0-9]{1,40}")


class VoiceUnsupported(ValueError):
    pass


def voice_id(voice: Mapping[str, Any]) -> str:
    v = (voice or {}).get("voice")
    if not isinstance(v, str) or not _VOICE_ID.fullmatch(v):
        raise VoiceUnsupported(f"Kokoro voice {v!r} is not a voice id like am_michael")
    return v


def block_gate(spoken_words: int, raw_seconds: float) -> Optional[str]:
    """The spoken words/s band (no truncation cap: Kokoro chunks long text itself)."""
    return cb.pace_problem(spoken_words, raw_seconds)


def encode_command(wav: Path, out: Path, *, ffmpeg: str = "ffmpeg") -> List[str]:
    return [ffmpeg, "-nostdin", "-y", "-loglevel", "error", "-i", str(wav),
            "-c:a", "aac", "-b:a", "96k", "-map_metadata", "-1", "-metadata", f"comment={PIPELINE_VERSION}",
            "-fflags", "+bitexact", "-flags:a", "+bitexact", "-movflags", "+faststart", str(out)]


def block_word_spans(text: str, tokens: List[tm.Token], duration: float, offset: float) -> Tuple[List[List[float]], bool, float]:
    """(absolute [start, end] per whitespace word, exact, coverage) for one block. Coverage = the
    share of words the ENGINE timed (exact grouping only; the proportional fallback is 0)."""
    timed, exact = tm.align_line(text, tokens, duration)
    words = text.split()
    groups = tm.group_tokens(tokens)
    engine_timed = sum(1 for g in groups if g[1] is not None and g[2] is not None)
    coverage = round(min(1.0, engine_timed / len(words)), 4) if (exact and words) else 0.0
    spans = [[round(offset + max(s, 0.0), 3), round(offset + max(e, 0.0), 3)] for _w, s, e in timed]
    return spans, exact, coverage


def render(job: Mapping[str, Any], out_dir: Path, *, threads: int,
           progress: Callable[[str], None] = print) -> Dict[str, Any]:
    import numpy as np
    import soundfile as sf
    import torch
    from kokoro import KPipeline

    voice = voice_id(job.get("voice") or {})
    torch.set_num_threads(threads)
    t0 = time.monotonic()
    pipe = KPipeline(lang_code=voice[0], repo_id=KOKORO_REPO)
    progress(f"Kokoro loaded in {time.monotonic() - t0:.0f}s; voice={voice}")

    def synth(text: str, seed: int) -> Tuple[Any, int]:
        torch.manual_seed(seed)
        parts: List[Any] = []
        tokens: List[tm.Token] = []
        samples = 0
        for r in pipe(text, voice=voice, speed=SPEED):
            audio = r.audio.numpy() if hasattr(r.audio, "numpy") else np.asarray(r.audio)
            audio = audio.astype(np.float32).reshape(-1)
            tokens += tm.tokens_from_engine(r.tokens, offset=samples / SR)
            samples += len(audio)
            parts.append(audio)
        audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
        return (audio, tokens), int(audio.shape[0])

    rendered = cb.render_blocks(job["blocks"], synth, seed=int(job.get("seed") or SEED), gate=block_gate,
                                sr=SR, split=False, progress=progress)
    pause_samples = int(round(BLOCK_PAUSE * SR))
    pause = np.zeros(pause_samples, dtype=np.float32)
    texts = {int(b["i"]): str(b["text"]) for b in job["blocks"]}
    parts: List[Any] = []
    blocks_out = []
    offset = 0
    for k, b in enumerate(rendered):
        audio, tokens = b.audio                  # one call per block (split=False)
        spans, exact, coverage = block_word_spans(texts[b.i], tokens, b.samples / SR, offset / SR)
        rec = cb.block_record(b, offset, b.samples, SR)
        rec.update({"word_spans": spans, "exact": exact, "align_coverage": coverage})
        blocks_out.append(rec)
        parts.append(audio)
        offset += b.samples
        if k < len(rendered) - 1:
            parts.append(pause)
            offset += pause_samples
    full = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    raw_wav = out_dir / "narration.raw.wav"
    sf.write(str(raw_wav), full, SR, subtype="FLOAT")
    m4a = out_dir / "narration.m4a"
    res = subprocess.run(encode_command(raw_wav, m4a), capture_output=True, text=True,
                         timeout=ENCODE_TIMEOUT_SECONDS)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg encode failed ({res.returncode}): {res.stderr[-400:]}")
    raw_wav.unlink(missing_ok=True)
    return {"engine": "kokoro", "pipeline": PIPELINE_VERSION, "sample_rate": SR, "atempo": 1.0,
            "voice": voice, "pause_samples": pause_samples, "audio": m4a.name,
            "wall_seconds": round(time.monotonic() - t0, 1), "blocks": blocks_out}
