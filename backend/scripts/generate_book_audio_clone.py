"""
Generate a Book Library narration with LOCAL voice cloning (Chatterbox, MIT, commercial-OK).

Clones a per-book reference clip so the voice stays CONSISTENT across every core (no per-call timbre
drift like Gemini prebuilt voices). Narrates bridge + body + the FULL action plan (heading + each
step's title and description). No Gemini quota; runs locally (slow on MPS — a book is a few hours).

Per-core CHECKPOINT (data/book_audio/.clone_cache_<order>/core<n>.wav) so an interrupted run resumes
for free. Output is the SAME shape as generate_book_audio.py — data/book_audio/<order>_<slug>.m4a +
.manifest.json — so the existing seed / align / read-along / swift pipeline works unchanged.

Run in the ISOLATED venv (from backend/):
    ./venv_clone/bin/python scripts/generate_book_audio_clone.py 2
"""
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import torch
from chatterbox.tts import ChatterboxTTS

sys.path.insert(0, str(Path(__file__).resolve().parent))
with contextlib.redirect_stdout(io.StringIO()):
    import gen_books_swift as g  # noqa: E402
import generate_book_audio as gba  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/book_audio"
REFDIR = ROOT / "data/voice_clone/refs"
BREAK = gba.BREAK_SECONDS

# Per-book reference voice clip (the clone source) — built from the Gemini audition samples so each
# book keeps the voice we cast. Add entries here as we roll out to more books.
REFS = {2: "graham_iapetus.wav"}


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def core_sentences(idx, title, sections, action, bridges) -> list[str]:
    """Spoken sentences for a core: bridge + body + FULL action plan, sentence-split (one Chatterbox
    call per sentence — same granularity as the approved prototype)."""
    blocks = [bridges[idx]] + gba.body_blocks(title, sections) + gba.action_plan_narration(action)
    return [s for b in blocks for s in split_sentences(b)]


def main():
    order = int(sys.argv[1])
    entry = next(b for b in g.BOOKS if b[0] == order)
    _, dirname, btitle, bauthor = entry
    slug = gba.slugify(btitle)
    if order not in REFS:
        raise SystemExit(f"no reference clip mapped for book {order} (add to REFS)")
    ref = REFDIR / REFS[order]
    if not ref.exists():
        raise SystemExit(f"reference clip missing: {ref}")
    m4a = OUT / f"{order}_{slug}.m4a"
    manifest_path = OUT / f"{order}_{slug}.manifest.json"
    cache = OUT / f".clone_cache_{order}"
    cache.mkdir(parents=True, exist_ok=True)

    cores = sorted([p for p in (g.BD / dirname).glob("*.txt")
                    if re.fullmatch(r"core\s+\d+", p.stem, re.I)], key=g.core_num)
    parsed = [(g.core_num(p), *g.parse_core(p)) for p in cores]
    titles = {num: title for num, title, *_ in parsed}
    bridges = gba.BRIDGES.get(order) or gba._generic_bridges(btitle, bauthor, [t for _, t, *_ in parsed])

    dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[{btitle}] {len(parsed)} cores · Chatterbox clone of {ref.name} · device={dev}", flush=True)
    model = None
    rate = None
    seg_by_num: dict[int, np.ndarray] = {}

    for idx, (num, title, sections, action, *_rest) in enumerate(parsed):
        cf = cache / f"core{num}.wav"
        if cf.exists():                                   # resume: reuse a finished core
            with wave.open(str(cf), "rb") as w:
                rate = w.getframerate()
                seg_by_num[num] = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            print(f"  core {num} [cached]", flush=True)
            continue
        if model is None:
            model = ChatterboxTTS.from_pretrained(device=dev)
        sents = core_sentences(idx, title, sections, action, bridges)
        print(f"  core {num} '{title}' · {len(sents)} sentences"
              + (f" · {len(action)} action steps read" if action else ""), flush=True)
        pieces = []
        for i, s in enumerate(sents):
            wav = model.generate(s, audio_prompt_path=str(ref))
            pieces.append(wav.squeeze(0).detach().cpu().numpy())
            print(f"    {i + 1}/{len(sents)}", flush=True)
        rate = model.sr
        seg = np.clip(np.concatenate(pieces) * 32767.0, -32768, 32767).astype(np.int16)
        with wave.open(str(cf), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(seg.tobytes())
        seg_by_num[num] = seg

    # assemble cores + a fixed inter-core silence, measure per-core offsets
    sil = np.zeros(int(rate * BREAK), dtype=np.int16)
    nums = sorted(seg_by_num)
    final, cores_manifest, cur = [], [], 0.0
    for j, num in enumerate(nums):
        cores_manifest.append({"number": num, "title": titles[num],
                               "start_seconds": round(cur, 2), "start_label": gba.fmt_ts(cur)})
        final.append(seg_by_num[num]); cur += seg_by_num[num].size / rate
        if j < len(nums) - 1:
            final.append(sil); cur += sil.size / rate
    full = np.concatenate(final)
    total = full.size / rate

    wp = OUT / f".clone_{order}.wav"
    with wave.open(str(wp), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(full.tobytes())
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wp),
                    "-c:a", "aac", "-b:a", "96k", "-ar", str(rate), "-ac", "1", str(m4a)], check=True)
    wp.unlink(missing_ok=True)

    manifest = {
        "curriculum_order": order, "book_title": btitle, "author": bauthor, "slug": slug,
        "audio_file": m4a.name, "voice": f"clone:{ref.stem}", "engine": "chatterbox",
        "target_wpm": None, "pitch_semitones": 0, "break_seconds": BREAK,
        "total_seconds": round(total, 2), "total_label": gba.fmt_ts(total), "cores": cores_manifest,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    shutil.rmtree(cache, ignore_errors=True)             # full book assembled → drop checkpoints
    print(f"\n  -> {m4a}  ({gba.fmt_ts(total)} @ {rate} Hz)  [voice-clone, action plan read]", flush=True)


if __name__ == "__main__":
    main()
