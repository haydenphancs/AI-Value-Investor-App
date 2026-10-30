"""
learn_narrator/verify.py — does a fresh render carry the expected voice? The staging gate of
`scripts/verify_learn_voice.py`, run on the worker's own file before anything is uploaded.

The Chatterbox bands and the three measuring functions (`duration`, `median_f0`, `lufs`, with their
`_run` helper and `SR`) are copied VERBATIM from the script and AST-pinned equal by
tests/test_learn_narrator_worker.py — the bench's A4 is "verify_learn_voice passes", so the worker
must measure exactly the way the script does. The script is never imported (the scripts tree is
not in the image).

Per engine (`BANDS`):
  * chatterbox — the catalog bands (F0 130–142 Hz, 144–166 wpm, 11.5–14.0 kB/s, −17.5..−14.5 LUFS),
    ENFORCED: a render outside them is not the catalog voice and fails `voice_out_of_band`.
  * kokoro — UNMEASURED: only the encode band the server also checks (10–14 kB/s), a pace band
    wide enough for any natural voice, and a loudness floor/ceiling that only a silent or clipped
    file misses. Pitch is recorded, never enforced. Tighten after the first real Kokoro renders.

numpy is imported inside `median_f0` only — the parent process calls `measure`, and stays free of
the heavy stack at import.
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SR = 16000

# Measured 2026-08-22 over all 13 clone clips + a 362 s Investor Journey sample. The Gemini
# renders they exclude sat at 124-126 Hz / 170 WPM / 9.7k B/s, well outside every band.
F0_HZ = (130.0, 142.0)          # clone 132.2-137, Journey 133.3; Gemini 124-128. The floor sits
                                # BETWEEN the two clusters, not on one: at 128.0 it exactly equalled
                                # nvidias-ai-dominance and let a known-bad clip through this axis.
                                # Pitch is the weakest of the four anyway — the clone reference was
                                # cut from a Gemini Achird narration, so the voices are related.
                                # Encode and pace are what separate them cleanly.
WPM = (144.0, 166.0)            # TARGET_WPM 165 -> ~158 after block pauses; one clip re-speeded to 150
BYTES_PER_SEC = (11_500, 14_000)  # -b:a 96k; ffmpeg's default (Gemini path) lands ~9.7k. The ceiling was
                                  # 12,800 (local ffmpeg 7.0 encodes, ~12.1k); the narrator image's ffmpeg 5.1
                                  # overshoots 96k (12,629 and 12,888 on Railway, 2026-10-24/30), so it is
                                  # Kokoro's 14,000: the floor separates the voices, the ceiling only
                                  # catches a stereo / 128k+ encode (16k+).
LUFS = (-17.5, -14.5)

Band = Optional[Tuple[float, float]]

BANDS: Dict[str, Dict[str, Band]] = {
    "chatterbox": {"f0_hz": F0_HZ, "wpm": WPM, "bytes_per_sec": BYTES_PER_SEC, "lufs": LUFS},
    "kokoro": {"f0_hz": None, "wpm": (120.0, 200.0), "bytes_per_sec": (10_000, 14_000), "lufs": (-30.0, -9.0)},
}


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def duration(p: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(p)]).stdout.strip()
    return float(out) if out else 0.0


def median_f0(p: Path) -> float:
    """Median voiced pitch via frame autocorrelation. No model, no extra dependency — the point is
    to separate two narrators that differ by ~10 Hz, not to transcribe."""
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-ac", "1",
                          "-ar", str(SR), "-f", "s16le", "-"], capture_output=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    W, H = int(0.040 * SR), int(0.020 * SR)
    lo, hi = int(SR / 300), int(SR / 70)          # 70-300 Hz search window
    out = []
    for i in range(0, max(0, len(x) - W), H):
        fr = x[i:i + W]
        if np.sqrt((fr ** 2).mean()) < 0.02:       # silence
            continue
        fr = fr - fr.mean()
        ac = np.correlate(fr, fr, "full")[W - 1:]
        if ac[0] <= 0:
            continue
        seg = ac[lo:hi]
        if not len(seg):
            continue
        k = int(np.argmax(seg)) + lo
        if ac[k] / ac[0] < 0.30:                   # unvoiced / weakly periodic
            continue
        out.append(SR / k)
    return float(np.median(out)) if out else float("nan")


def lufs(p: Path) -> float:
    err = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(p),
                "-filter:a", "loudnorm=print_format=json", "-f", "null", "-"]).stderr
    m = re.search(r'"input_i"\s*:\s*"?(-?[\d.]+)', err)
    return float(m.group(1)) if m else float("nan")


# ── the worker's use of them ─────────────────────────────────────────────────


def _clean(x: float, places: int) -> Optional[float]:
    return round(float(x), places) if isinstance(x, (int, float)) and math.isfinite(float(x)) else None


def measure(path: Path, words: int, *, size_bytes: Optional[int] = None) -> Dict[str, Any]:
    """The four measurements of a file (`words` = whitespace words of every narrated block, the
    script's basis). NaN becomes None (JSON has no NaN)."""
    d = duration(path)
    size = path.stat().st_size if size_bytes is None else size_bytes
    return {
        "duration": _clean(d, 3),
        "f0_hz": _clean(median_f0(path), 1) if d > 0 else None,
        "wpm": _clean(words / d * 60, 1) if d > 0 else None,
        "bytes_per_sec": _clean(size / d, 0) if d > 0 else None,
        "lufs": _clean(lufs(path), 2) if d > 0 else None,
    }


def band_problems(engine: str, m: Dict[str, Any]) -> List[str]:
    """Why `m` is outside `engine`'s bands (empty = in band). A missing measurement of an ENFORCED
    axis is a problem too — an unreadable file must not pass."""
    bands = BANDS.get(engine)
    if bands is None:
        return [f"no bands for engine {engine!r}"]
    if not m.get("duration"):
        return ["the file has no readable duration"]
    out = []
    for key, band in bands.items():
        if band is None:
            continue
        v = m.get(key)
        if v is None:
            out.append(f"{key} could not be measured")
        elif not band[0] <= v <= band[1]:
            out.append(f"{key} {v} outside {band}")
    return out
