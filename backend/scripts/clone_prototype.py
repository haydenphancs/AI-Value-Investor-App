"""
PROTOTYPE — local voice-cloning narration with Chatterbox (MIT, commercial-OK).

Clones a reference voice clip (one of our Gemini audition samples) and narrates ONE book core in that
voice, so we can judge quality + consistency BEFORE switching the book pipeline off Gemini. Because
every chunk is conditioned on the SAME reference clip, the voice stays consistent throughout (the
thing Gemini prebuilt voices couldn't guarantee).

Runs in the ISOLATED venv so it can't disturb the working backend venv:
    ./venv_clone/bin/python scripts/clone_prototype.py            # default: book 2, core 1
    ./venv_clone/bin/python scripts/clone_prototype.py 2 1

Only depends on gen_books_swift (stdlib) for the core text + chatterbox/torch — NOT generate_book_audio
(which needs httpx + a Gemini key), so the isolated env stays minimal.
"""
import contextlib
import io
import re
import subprocess
import sys
from pathlib import Path

import torch
import torchaudio as ta
from chatterbox.tts import ChatterboxTTS

sys.path.insert(0, str(Path(__file__).resolve().parent))
with contextlib.redirect_stdout(io.StringIO()):
    import gen_books_swift as g  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REFDIR = ROOT / "data/voice_clone/refs"
# Per-book reference voice clip (the clone source), matched by author to the cast Gemini voice.
REFS = {2: "graham_iapetus.wav", 5: "fisher_schedar.wav", 6: "bogle_alnilam.wav",
        7: "malkiel_orus.wav", 8: "buffett_zubenelgenubi.wav",
        9: "greenblatt_achird.wav", 10: "marks_sadaltager.wav"}
OUTDIR = ROOT / "data/voice_clone"


def body_blocks(sections):
    out = []
    for kind, text in sections:
        if kind == "heading" and re.search(r"action\s+plan", text, re.I):
            continue
        t = re.sub(r"\*\*", "", text or "")
        if t:
            out.append(t)
    return out


def split_sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    order = int(args[0]) if args else 2
    core = int(args[1]) if len(args) > 1 else 1
    if order not in REFS:
        raise SystemExit(f"no reference clip mapped for book {order} (add to REFS)")
    REF = REFDIR / REFS[order]
    if not REF.exists():
        raise SystemExit(f"reference clip missing: {REF}")

    _, dirname, btitle, bauthor = next(b for b in g.BOOKS if b[0] == order)
    p = g.BD / dirname / f"core {core}.txt"
    title, sections, action, *_ = g.parse_core(p)
    bridge = f"Welcome to {btitle}, by {bauthor}. Let's begin with the first core: {title}."
    blocks = [bridge] + body_blocks(sections)
    chunks = [s for b in blocks for s in split_sentences(b)]

    dev = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[clone] book {order} core {core} '{title}' · device={dev} · {len(chunks)} chunks · ref={REF.name}")
    model = ChatterboxTTS.from_pretrained(device=dev)

    wavs = []
    for i, c in enumerate(chunks):
        wavs.append(model.generate(c, audio_prompt_path=str(REF)))
        print(f"  chunk {i + 1}/{len(chunks)}")
    full = torch.cat(wavs, dim=-1)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"book{order}_core{core}_chatterbox.m4a"
    wp = out.with_suffix(".wav")
    ta.save(str(wp), full.cpu(), model.sr)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wp),
                    "-c:a", "aac", "-b:a", "96k", str(out)], check=True)
    wp.unlink(missing_ok=True)
    print(f"\nwrote {out}  ({full.shape[-1] / model.sr:.0f}s @ {model.sr} Hz)")
    print("Listen and compare to the Gemini version + the reference clip.")


if __name__ == "__main__":
    main()
