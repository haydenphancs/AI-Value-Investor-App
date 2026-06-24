"""
clone_learn_audio.py — Chatterbox clone narration for Investor Journey lessons + Money Moves articles.

Speaks the SAME text as the Gemini generators (generate_journey_audio.py / generate_money_moves_audio.py)
so the existing read-along structure stays valid, but in ONE consistent cloned voice (a reference clip),
fixing Gemini's per-call voice drift. NO Gemini key needed (reads the committed JSON), runs in venv_clone.

Voice/pacing (approved 2026-06-24):
  - reference: caydex_voice_achird_v2.wav (single-clip, expressive)
  - exaggeration 0.65 / cfg_weight 0.40  (energetic, human character)
  - normalized to TARGET_WPM (165 = the Gemini 170 WPM minus 3%)
  - SENTENCE pause after every '.'/'!'/'?'  +  longer BLOCK pause at paragraph / title->paragraph
    boundaries (commas stay at Chatterbox's natural shorter pause -> period pause > comma pause)

Pauses are inserted PRE-atempo (scaled by the atempo factor) so they land at the exact target length
AFTER the WPM tempo pass. Re-alignment runs on the final audio, so the pauses are reflected in the
read-along timings automatically.

Usage (from backend/):
    ./venv_clone/bin/python scripts/clone_learn_audio.py journey compound_interest
    ./venv_clone/bin/python scripts/clone_learn_audio.py moneymoves how-amazon-built-its-moat
    ./venv_clone/bin/python scripts/clone_learn_audio.py journey --all
    ./venv_clone/bin/python scripts/clone_learn_audio.py moneymoves --all

Outputs (skip-existing checkpoint):
    data/journey_audio_clone/<audioClip>.m4a      (one per narrated card)
    data/money_moves_audio_clone/<slug>.m4a       (one per article)
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import torch
import torchaudio as ta
from chatterbox.tts import ChatterboxTTS

ROOT = Path(__file__).resolve().parents[1]      # backend/
REPO = ROOT.parent
REF = ROOT / "data/voice_clone/refs/caydex_voice_achird_v2.wav"

EXAG = 0.65            # expressiveness / energy (approved)
CFG = 0.40
TARGET_WPM = 165       # 170 (Gemini original) - 3%
SENT_PAUSE = 0.20      # seconds of silence after a sentence (period/!/?)
BLOCK_PAUSE = 0.60     # seconds at a paragraph / title->paragraph boundary
SR = 24000             # Chatterbox sample rate
CACHE = ROOT / "data/voice_clone/_sent_cache"   # per-sentence wav cache (keyed by text+voice settings)


def _json(rel_fe: str, rel_be: str) -> Path:
    fe = REPO / rel_fe
    return fe if fe.exists() else ROOT / rel_be


JOURNEY_JSON = _json("frontend/ios/ios/Resources/Journey/journey_lessons.json", "data/journey_lessons.json")
MM_JSON = _json("frontend/ios/ios/Resources/MoneyMoves/money_moves.json", "data/money_moves.json")


def strip_markup(s: str) -> str:
    return re.sub(r"\*\*", "", s or "")


def split_sentences(t: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", (t or "").strip()) if s.strip()]


def narration_blocks(article: dict) -> list[str]:
    """Ordered readable blocks for an article — copied from generate_money_moves_audio.py:67-85."""
    blocks: list[str] = []
    if article.get("title"):
        blocks.append(article["title"])
    if article.get("subtitle"):
        blocks.append(article["subtitle"])
    for section in article.get("sections", []):
        if section.get("title"):
            blocks.append(section["title"])
        for block in section.get("content", []):
            kind = block.get("type")
            if kind in ("paragraph", "subheading", "quote") and block.get("text"):
                blocks.append(block["text"])
            elif kind == "bulletList":
                blocks += (block.get("items") or [])
            elif kind == "callout" and block.get("text"):
                blocks.append(block["text"])
    return [strip_markup(b) for b in blocks if b]


def journey_items(only: str):
    """[(audioClip, [[sentences]])] — one block per card (mirrors generate_journey_audio.py:118-130)."""
    data = json.loads(JOURNEY_JSON.read_text())
    out = []
    for lesson in data["lessons"]:
        cards = lesson["cards"]
        title_clip = next((c.get("audioClip") for c in cards if c.get("audioClip")), "")
        if only != "--all" and not title_clip.startswith(only):
            continue
        for card in cards:
            clip = card.get("audioClip")
            if not clip:
                continue
            sents = split_sentences(strip_markup(card.get("text", "")))
            if sents:
                out.append((clip, [sents]))           # a card is one block
    return out, ROOT / "data/journey_audio_clone"


def moneymoves_items(only: str):
    """[(slug, [[sentences], ...])] — one block per narration block (title/section/paragraph/...)."""
    data = json.loads(MM_JSON.read_text())
    out = []
    for article in data["articles"]:
        slug = article["slug"]
        if only != "--all" and only not in slug:
            continue
        blocks = [split_sentences(b) for b in narration_blocks(article)]
        blocks = [b for b in blocks if b]
        if blocks:
            out.append((slug, blocks))
    return out, ROOT / "data/money_moves_audio_clone"


_MODEL = None


def _model():
    """Lazy-load Chatterbox only on a cache miss (so pure re-assembly runs need no GPU)."""
    global _MODEL
    if _MODEL is None:
        dev = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
        print(f"  (loading Chatterbox on {dev})", flush=True)
        _MODEL = ChatterboxTTS.from_pretrained(device=dev)
    return _MODEL


def gen_sentence(sent: str) -> torch.Tensor:
    """Per-sentence wav, cached by (text + voice settings). Pause/WPM changes reuse the cache."""
    key = hashlib.sha1(f"{sent}|{EXAG}|{CFG}|{REF.name}".encode("utf-8")).hexdigest()[:16]
    cpath = CACHE / f"{key}.wav"
    if cpath.exists():
        wav, _ = ta.load(str(cpath))
        return wav
    wav = _model().generate(sent, audio_prompt_path=str(REF), exaggeration=EXAG, cfg_weight=CFG)
    CACHE.mkdir(parents=True, exist_ok=True)
    ta.save(str(cpath), wav.cpu(), _model().sr)
    return wav


def synth_clip(blocks: list[list[str]], out: Path) -> float:
    """One clip: per-sentence (cached) Chatterbox, normalize to TARGET_WPM, insert sentence/block pauses."""
    gen: list[tuple] = []          # (wav, is_last_in_block) per sentence, in order
    words = 0
    speech_samples = 0
    for block in blocks:
        for i, sent in enumerate(block):
            w = gen_sentence(sent)
            gen.append((w, i == len(block) - 1))
            words += len(sent.split())
            speech_samples += w.shape[-1]
    native_wpm = (words / (speech_samples / SR) * 60) if speech_samples else TARGET_WPM
    atempo = max(0.5, min(2.0, TARGET_WPM / native_wpm))
    # pre-atempo pause lengths so they land at SENT_PAUSE / BLOCK_PAUSE after the tempo pass
    sil_sent = torch.zeros(1, int(round(SENT_PAUSE * atempo * SR)))
    sil_block = torch.zeros(1, int(round(BLOCK_PAUSE * atempo * SR)))
    parts = []
    for i, (w, is_block_end) in enumerate(gen):
        parts.append(w)
        if i == len(gen) - 1:
            break
        parts.append(sil_block if is_block_end else sil_sent)
    full = torch.cat(parts, dim=-1)
    wp = out.with_suffix(".wav")
    ta.save(str(wp), full.cpu(), SR)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(wp),
                    "-filter:a", f"atempo={atempo:.5f}", "-c:a", "aac", "-b:a", "96k", str(out)], check=True)
    wp.unlink(missing_ok=True)
    return full.shape[-1] / SR / atempo


def main():
    kind = sys.argv[1] if len(sys.argv) > 1 else ""
    only = sys.argv[2] if len(sys.argv) > 2 else "--all"
    if kind == "journey":
        items, outdir = journey_items(only)
    elif kind == "moneymoves":
        items, outdir = moneymoves_items(only)
    else:
        raise SystemExit("usage: clone_learn_audio.py {journey|moneymoves} {<slug/prefix>|--all}")
    if not REF.exists():
        raise SystemExit(f"reference clip missing: {REF}")
    if not items:
        raise SystemExit(f"no items matched '{only}' for {kind}")
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"[clone_learn] {kind} '{only}': {len(items)} item(s) -> {outdir.name} · "
          f"ref={REF.name} · exag={EXAG} · {TARGET_WPM}wpm · pauses {SENT_PAUSE}/{BLOCK_PAUSE}s", flush=True)
    done = 0
    for label, blocks in items:
        out = outdir / f"{label}.m4a"
        if out.exists():
            print(f"  {label:34s} (exists, skip)", flush=True)
            continue
        nsent = sum(len(b) for b in blocks)
        print(f"  {label:34s} {len(blocks)} block(s), {nsent} sentence(s)", flush=True)
        secs = synth_clip(blocks, out)
        print(f"    -> {out.name} ({secs:.0f}s)", flush=True)
        done += 1
    print(f"DONE {kind}: {done} new clip(s) -> {outdir}", flush=True)


if __name__ == "__main__":
    main()
