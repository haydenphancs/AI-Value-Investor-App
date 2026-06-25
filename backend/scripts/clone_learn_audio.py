"""
clone_learn_audio.py — Chatterbox clone narration for Investor Journey lessons + Money Moves articles.

Speaks the SAME text as the Gemini generators (generate_journey_audio.py / generate_money_moves_audio.py)
so the existing read-along structure stays valid, but in ONE consistent cloned voice (a reference clip),
fixing Gemini's per-call voice drift. NO Gemini key needed (reads the committed JSON), runs in venv_clone.

Voice/pacing (approved 2026-06-24):
  - reference: caydex_voice_achird_v2.wav (single-clip, expressive)
  - exaggeration 0.65 / cfg_weight 0.40  (energetic, human character)
  - normalized to TARGET_WPM (165 = the Gemini 170 WPM minus 3%)

Two generation modes (CLONE_MODE env var):
  - "block" (per-paragraph, APPROVED for rollout): one Chatterbox call per block/card, so the pauses
    INSIDE a paragraph are natural/AI-decided; a fixed BLOCK_PAUSE is inserted only between blocks.
    Cached per-block in _block_cache (key "BLK|text|exag|cfg|ref").
  - "sentence" (default/legacy): one call per sentence; fixed SENT_PAUSE between sentences + BLOCK_PAUSE
    between blocks. Cached per-sentence in _sent_cache.

Pauses are inserted PRE-atempo (scaled by the atempo factor) so they land at the exact target length
AFTER the WPM tempo pass. Re-alignment runs on the final audio, so the pauses are reflected in the
read-along timings automatically.

Usage (from backend/):
    CLONE_MODE=block ./venv_clone/bin/python scripts/clone_learn_audio.py journey --all
    CLONE_MODE=block ./venv_clone/bin/python scripts/clone_learn_audio.py moneymoves --all
    # parallel sharding (disjoint stride; run N processes, shard 0..N-1):
    CLONE_MODE=block ./venv_clone/bin/python scripts/clone_learn_audio.py journey --all --shard 0 3

Outputs (skip-existing checkpoint):
    data/journey_audio_clone/<audioClip>.m4a      (one per narrated card)
    data/money_moves_audio_clone/<slug>.m4a       (one per article)
"""
import hashlib
import json
import os
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
SENT_PAUSE = 0.20      # seconds of silence after a sentence (sentence mode only)
BLOCK_PAUSE = 0.60     # seconds at a paragraph / title->paragraph boundary
SR = 24000             # Chatterbox sample rate
CACHE = ROOT / "data/voice_clone/_sent_cache"    # per-sentence wav cache
BLOCK_CACHE = ROOT / "data/voice_clone/_block_cache"  # per-block (per-paragraph) wav cache
MODE = os.environ.get("CLONE_MODE", "sentence")  # "block" (per-paragraph, approved) or "sentence"


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
    """[(audioClip, [raw_block])] — one raw block (the whole card text) per card.
    Block text is strip_markup(card.text) UN-stripped, matching the per-block cache prototype."""
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
            raw = strip_markup(card.get("text", ""))
            if raw.strip():
                out.append((clip, [raw]))             # a card is one block
    return out, ROOT / "data/journey_audio_clone"


def moneymoves_items(only: str):
    """[(slug, [raw_blocks])] — raw narration block strings (title/section/paragraph/bullet/...)."""
    data = json.loads(MM_JSON.read_text())
    out = []
    for article in data["articles"]:
        slug = article["slug"]
        if only != "--all" and only not in slug:
            continue
        blocks = narration_blocks(article)
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
    """Per-sentence wav, cached by (text + voice settings)."""
    key = hashlib.sha1(f"{sent}|{EXAG}|{CFG}|{REF.name}".encode("utf-8")).hexdigest()[:16]
    cpath = CACHE / f"{key}.wav"
    if cpath.exists():
        wav, _ = ta.load(str(cpath))
        return wav
    wav = _model().generate(sent, audio_prompt_path=str(REF), exaggeration=EXAG, cfg_weight=CFG)
    CACHE.mkdir(parents=True, exist_ok=True)
    ta.save(str(cpath), wav.cpu(), _model().sr)
    return wav


def gen_block(text: str) -> torch.Tensor:
    """Per-block (whole paragraph/card) wav — Chatterbox renders the natural pauses inside. Cached by
    (text + voice settings). Key matches the per-block prototype so warm blocks are reused."""
    key = hashlib.sha1(f"BLK|{text}|{EXAG}|{CFG}|{REF.name}".encode("utf-8")).hexdigest()[:16]
    cpath = BLOCK_CACHE / f"{key}.wav"
    if cpath.exists():
        wav, _ = ta.load(str(cpath))
        return wav
    wav = _model().generate(text, audio_prompt_path=str(REF), exaggeration=EXAG, cfg_weight=CFG)
    BLOCK_CACHE.mkdir(parents=True, exist_ok=True)
    ta.save(str(cpath), wav.cpu(), _model().sr)
    return wav


def synth_clip(raw_blocks: list[str], out: Path) -> float:
    """One clip from raw block strings. MODE 'block' = one Chatterbox call per block (natural intra-block
    pauses, BLOCK_PAUSE between blocks). MODE 'sentence' = per-sentence calls (SENT_PAUSE within a block,
    BLOCK_PAUSE between blocks). Both normalize to TARGET_WPM."""
    gen: list[tuple] = []          # (wav, is_block_end) in order
    words = 0
    speech_samples = 0
    for raw in raw_blocks:
        if MODE == "block":
            w = gen_block(raw)
            gen.append((w, True))
            words += len(raw.split())
            speech_samples += w.shape[-1]
        else:
            sents = split_sentences(raw)
            for i, sent in enumerate(sents):
                w = gen_sentence(sent)
                gen.append((w, i == len(sents) - 1))
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
    argv = list(sys.argv[1:])
    shard_i, shard_n = 0, 1
    if "--shard" in argv:
        si = argv.index("--shard")
        shard_i, shard_n = int(argv[si + 1]), int(argv[si + 2])
        del argv[si:si + 3]
    kind = argv[0] if argv else ""
    only = argv[1] if len(argv) > 1 else "--all"
    if kind == "journey":
        items, outdir = journey_items(only)
    elif kind == "moneymoves":
        items, outdir = moneymoves_items(only)
    else:
        raise SystemExit("usage: clone_learn_audio.py {journey|moneymoves} {<slug/prefix>|--all} [--shard i N]")
    if not REF.exists():
        raise SystemExit(f"reference clip missing: {REF}")
    items = items[shard_i::shard_n]
    if not items:
        raise SystemExit(f"no items matched '{only}' for {kind} (shard {shard_i}/{shard_n})")
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"[clone_learn] {kind} '{only}' mode={MODE} shard={shard_i}/{shard_n}: {len(items)} item(s) -> "
          f"{outdir.name} · ref={REF.name} · exag={EXAG} · {TARGET_WPM}wpm · pause(blk={BLOCK_PAUSE}s)", flush=True)
    done = 0
    for label, blocks in items:
        out = outdir / f"{label}.m4a"
        if out.exists():
            print(f"  {label:34s} (exists, skip)", flush=True)
            continue
        print(f"  {label:34s} {len(blocks)} block(s)", flush=True)
        secs = synth_clip(blocks, out)
        print(f"    -> {out.name} ({secs:.0f}s)", flush=True)
        done += 1
    print(f"DONE {kind} shard {shard_i}/{shard_n}: {done} new clip(s) -> {outdir}", flush=True)


if __name__ == "__main__":
    main()
