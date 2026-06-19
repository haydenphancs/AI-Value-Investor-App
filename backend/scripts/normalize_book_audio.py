"""
Even out per-core loudness in a Book Library narration WITHOUT re-generating (no Gemini quota).

Each book .m4a is stitched from many independent Gemini TTS "takes" (one per chunk/core) whose
loudness varies core-to-core (measured spread up to ~4 dB) — which reads as "each core sounds a bit
different." This measures each core's speech loudness (gated RMS, so pauses/silence don't skew it)
and applies a single pure LINEAR GAIN per core to bring every core down to the book's quietest core
(reductions only → never clips). Result: all cores at one consistent level, spread ≈ 0 dB.

It is sample-exact (numpy gain, no resampling/limiting), so DURATION is preserved — the baked
per-core seek offsets (BookAudioContent.swift) and read-along timings (BookReadAlong.swift) stay
valid; no re-align needed for already-aligned books.

Originals are backed up to data/book_audio/orig/ (once) and every run normalizes FROM that pristine
master, so it is reversible and re-run safe (never double-applies).

Usage (from backend/):
    ./venv/bin/python scripts/normalize_book_audio.py            # all books with a manifest
    ./venv/bin/python scripts/normalize_book_audio.py 8          # one curriculum order (file prefix)
"""
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]                 # backend/
AUDIO_DIR = ROOT / "data/book_audio"
ORIG_DIR = AUDIO_DIR / "orig"
GATE = 327.0          # ~ -40 dBFS (int16): ignore near-silence so pauses don't skew core loudness


def dur(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    return float(out.stdout.strip())


def decode(m4a: Path, rate: int) -> np.ndarray:
    wav = Path(tempfile.gettempdir()) / f"{m4a.stem}.norm.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(m4a),
                    "-ac", "1", "-ar", str(rate), str(wav)], check=True)
    with wave.open(str(wav), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
    wav.unlink(missing_ok=True)
    return data


def core_loudness_db(seg: np.ndarray) -> float:
    """Gated RMS of a core segment in dBFS (speech only — pauses below GATE excluded)."""
    speech = seg[np.abs(seg) > GATE]
    if speech.size < 100:
        speech = seg
    rms = np.sqrt(np.mean(speech ** 2)) if speech.size else 1.0
    return 20 * np.log10(max(rms, 1e-9) / 32768.0)


def normalize(mpath: Path, rate: int = 24000) -> None:
    manifest = json.loads(mpath.read_text())
    fname = manifest["audio_file"]
    m4a = AUDIO_DIR / fname
    if not m4a.exists():
        print(f"  {fname:34s} (m4a missing, skip)")
        return

    ORIG_DIR.mkdir(exist_ok=True)
    backup = ORIG_DIR / fname
    if not backup.exists():                       # preserve the pristine pre-normalization master
        shutil.copy2(m4a, backup)

    data = decode(backup, rate)                   # always normalize FROM the pristine original
    n = data.size
    starts = [c["start_seconds"] for c in sorted(manifest["cores"], key=lambda c: c["number"])]
    bounds = [int(round(s * rate)) for s in starts] + [n]

    db = [core_loudness_db(data[bounds[i]:bounds[i + 1]]) for i in range(len(starts))]
    target = min(db)                              # reduce every core down to the quietest -> no clip
    out = data.copy()
    for i in range(len(starts)):
        g = 10 ** ((target - db[i]) / 20.0)       # <= 1.0
        out[bounds[i]:bounds[i + 1]] *= g

    tmp_wav = Path(tempfile.gettempdir()) / f"{m4a.stem}.leveled.wav"
    with wave.open(str(tmp_wav), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(np.clip(out, -32768, 32767).astype(np.int16).tobytes())
    tmp_m4a = AUDIO_DIR / f".norm_{fname}"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp_wav),
                    "-c:a", "aac", "-b:a", "96k", "-ar", str(rate), "-ac", "1", str(tmp_m4a)], check=True)
    tmp_wav.unlink(missing_ok=True)

    d0, d1 = dur(backup), dur(tmp_m4a)
    if abs(d1 - d0) > 0.2:
        tmp_m4a.unlink(missing_ok=True)
        raise SystemExit(f"  {fname}: duration drift {d0:.2f}->{d1:.2f}s — aborted")
    tmp_m4a.replace(m4a)

    after = [core_loudness_db(decode(m4a, rate)[bounds[i]:bounds[i + 1]]) for i in range(len(starts))]
    print(f"  {fname:34s} dur {d0:.1f}->{d1:.1f}s  spread Δ{max(db)-min(db):.1f}dB -> Δ{max(after)-min(after):.1f}dB"
          f"  (target {target:.1f} dBFS)")


def main():
    only = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    manifests = sorted(AUDIO_DIR.glob("*.manifest.json"))
    if only:
        manifests = [m for m in manifests if m.name.startswith(f"{only}_")]
    if not manifests:
        raise SystemExit(f"No manifests in {AUDIO_DIR}")
    print(f"Per-core loudness leveling; pristine originals backed up to {ORIG_DIR}/\n")
    for mpath in manifests:
        normalize(mpath)
    print("\nDone. Next: re-seed (seed_book_audio.py --force) so the bucket serves the leveled audio.")


if __name__ == "__main__":
    main()
