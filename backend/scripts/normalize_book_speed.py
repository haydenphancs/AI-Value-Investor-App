"""
Even out per-core SPEECH RATE in a Book Library narration WITHOUT re-generating (no Gemini quota).

`generate_book_audio.py` atempo's every core to the same AVERAGE WPM, but the model's underlying
speech cadence still varies per take (e.g. book 3 measured ~199–213 WPM excluding pauses), so a few
cores sound faster than others. This measures each core's pause-excluded speech rate (voiced samples)
and applies a per-core ffmpeg `atempo` time-stretch to pull them all to the book's MEDIAN speech rate
(a small ±few-% nudge → transparent), keeping the 2.5 s inter-core silences intact.

`atempo` changes DURATION, so the manifest's per-core `start_seconds` are recomputed. Run AFTER
loudness leveling and BEFORE align/seed/bake (read-along is aligned against the new timing). The
pre-speed master is backed up to data/book_audio/orig_speed/ (re-run safe — always works from it).

Usage (from backend/):
    ./venv/bin/python scripts/normalize_book_speed.py 3
    ./venv/bin/python scripts/normalize_book_speed.py 3 --target 200   # optional explicit speech WPM
"""
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
with contextlib.redirect_stdout(io.StringIO()):
    import gen_books_swift as g  # noqa: E402
import generate_book_audio as gba  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AUDIO = ROOT / "data/book_audio"
ORIG = AUDIO / "orig_speed"
BREAK = gba.BREAK_SECONDS
RATE = 24000
GATE = 327.0          # ~ -40 dBFS: samples below this are pause/silence (excluded from speech rate)


def decode(m4a: Path) -> np.ndarray:
    wav = Path(tempfile.gettempdir()) / f"{m4a.stem}.spd.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(m4a),
                    "-ac", "1", "-ar", str(RATE), str(wav)], check=True)
    with wave.open(str(wav), "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    wav.unlink(missing_ok=True)
    return data


def atempo(seg: np.ndarray, k: float) -> np.ndarray:
    """Time-stretch int16 mono samples by atempo=k (k>1 faster / shorter, k<1 slower / longer)."""
    if abs(k - 1.0) < 2e-3:
        return seg
    tin = Path(tempfile.gettempdir()) / "spd_in.wav"
    tout = Path(tempfile.gettempdir()) / "spd_out.wav"
    with wave.open(str(tin), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(seg.tobytes())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tin),
                    "-filter:a", f"atempo={k:.5f}", str(tout)], check=True)
    with wave.open(str(tout), "rb") as w:
        out = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    tin.unlink(missing_ok=True); tout.unlink(missing_ok=True)
    return out


def detect_core_segments(data: np.ndarray, ncores: int) -> list[tuple[int, int]]:
    """Core SPEECH segment [a, b) sample ranges, found by detecting the ~BREAK-second near-silent gaps
    the generator inserts between cores (the inserted silence is digital zeros → ≈ -65 dBFS after the
    AAC round-trip, far quieter than any intra-core TTS pause). This is robust to a stale/ drifted
    manifest — we never trust its offsets for slicing. Errors if the gap count doesn't yield exactly
    `ncores` segments (so a bad detection fails loudly instead of silently mis-cutting)."""
    thr = 32768 * 10 ** (-65 / 20)
    minl = int(RATE * (BREAK - 0.3))      # gaps are ~BREAK s; intra-core pauses are shorter
    quiet = np.abs(data.astype(np.int32)) < thr
    gaps, i, n = [], 0, data.size
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if j - i >= minl:
                gaps.append((i, j))
            i = j
        else:
            i += 1
    segs, prev = [], 0
    for gs, ge in gaps:
        segs.append((prev, gs))
        prev = ge
    segs.append((prev, n))
    if len(segs) != ncores:
        raise SystemExit(f"gap detection found {len(segs)} core segments, expected {ncores} "
                         f"— adjust threshold/minlen in detect_core_segments")
    return segs


def core_words(order: int, dirname: str, btitle: str, bauthor: str) -> dict[int, int]:
    cores = sorted([p for p in (g.BD / dirname).glob("*.txt")
                    if re.fullmatch(r"core\s+\d+", p.stem, re.I)], key=g.core_num)
    parsed = [(g.core_num(p), *g.parse_core(p)) for p in cores]
    titles = [t for _, t, *_ in parsed]
    bridges = gba.BRIDGES.get(order) or gba._generic_bridges(btitle, bauthor, titles)
    out = {}
    for i, (num, title, sections, action, *_rest) in enumerate(parsed):
        blocks = [bridges[i]] + gba.body_blocks(title, sections)
        recap = gba.action_recap(action)
        if recap:
            blocks.append(recap)
        out[num] = sum(len(b.split()) for b in blocks)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    order = int(args[0])
    target = None
    if "--target" in sys.argv:
        target = float(sys.argv[sys.argv.index("--target") + 1])

    _, dirname, btitle, bauthor = next(b for b in g.BOOKS if b[0] == order)
    slug = gba.slugify(btitle)
    mpath = AUDIO / f"{order}_{slug}.manifest.json"
    m4a = AUDIO / f"{order}_{slug}.m4a"
    man = json.loads(mpath.read_text())
    words = core_words(order, dirname, btitle, bauthor)

    ORIG.mkdir(exist_ok=True)
    if not (ORIG / m4a.name).exists():        # keep the pre-speed master; always work from it
        shutil.copy2(m4a, ORIG / m4a.name)
    data = decode(ORIG / m4a.name)
    nums = [c["number"] for c in sorted(man["cores"], key=lambda c: c["number"])]
    bounds = detect_core_segments(data, len(nums))   # core boundaries from the audio, not the manifest

    # measure each core's pause-excluded speech rate
    segs = []   # [num, speech_samples, speech_wpm]
    for num, (a, b) in zip(nums, bounds):
        seg = data[a:b]
        voiced = int(np.count_nonzero(np.abs(seg.astype(np.int32)) > GATE))
        speak = max(0.1, voiced / RATE)
        segs.append([num, seg, words[num] / speak * 60])

    # per-core overrides: --slow N:FACTOR (repeatable), FACTOR<1 ⇒ that core ends slower than target
    overrides: dict[int, float] = {}
    for i, a in enumerate(sys.argv):
        if a == "--slow":
            n, f = sys.argv[i + 1].split(":")
            overrides[int(n)] = float(f)

    rates = [w for *_, w in segs]
    tgt = target if target else float(np.median(rates))
    print(f"book {order}: speech-rate spread {min(rates):.0f}-{max(rates):.0f} WPM "
          f"(Δ{max(rates) - min(rates):.0f}); leveling all cores to {tgt:.0f} WPM"
          + (f"; overrides {overrides}" if overrides else ""))

    title_by_num = {c["number"]: c.get("title", "") for c in man["cores"]}
    sil = np.zeros(int(RATE * BREAK), dtype=np.int16)
    pieces, new_cores, cur = [], [], 0.0
    for idx, (num, seg, wpm) in enumerate(segs):
        core_tgt = tgt * overrides.get(num, 1.0)   # e.g. --slow 10:0.92 ⇒ core 10 at 92% of the target
        k = core_tgt / wpm                     # k<1 SLOWS a faster-than-target core; k>1 speeds a slower one
        #   (atempo=k: k>1 faster/shorter, k<1 slower/longer; new speech rate = wpm*k = core_tgt)
        stretched = atempo(seg, k)
        new_cores.append({"number": num, "title": title_by_num.get(num, ""),
                          "start_seconds": round(cur, 2), "start_label": gba.fmt_ts(cur)})
        pieces.append(stretched)
        cur += stretched.size / RATE
        if idx < len(segs) - 1:
            pieces.append(sil); cur += sil.size / RATE

    final = np.concatenate(pieces)
    tw = Path(tempfile.gettempdir()) / f"{m4a.stem}.spd_book.wav"
    with wave.open(str(tw), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(final.tobytes())
    tmp_m4a = AUDIO / f".spd_{m4a.name}"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tw),
                    "-c:a", "aac", "-b:a", "96k", "-ar", str(RATE), "-ac", "1", str(tmp_m4a)], check=True)
    tw.unlink(missing_ok=True)
    tmp_m4a.replace(m4a)

    man["cores"] = sorted(new_cores, key=lambda c: c["number"])
    man["total_seconds"] = round(final.size / RATE, 2)
    man["total_label"] = gba.fmt_ts(final.size / RATE)
    mpath.write_text(json.dumps(man, indent=2))
    print(f"  -> {m4a.name}  ({man['total_label']})  manifest offsets recomputed")
    print("  Next: re-level (optional), re-align, re-bake read-along, seed, gen_book_audio_swift.")


if __name__ == "__main__":
    main()
