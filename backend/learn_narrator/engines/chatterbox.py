"""
learn_narrator/engines/chatterbox.py — the catalog voice on CPU: the Chatterbox 0.1.7 clone of
`assets/caydex_voice_achird_v2.wav`, rendered and assembled like `scripts/clone_learn_audio.py`
voiced every Money Moves article (CLONE_MODE=block, approved 2026-06-24). The owner's decision
(2026-10-22) keeps this voice if the Phase-0 benchmark passes.

What is pinned, and why:
  * EXAG / CFG / TARGET_WPM / BLOCK_PAUSE / SENT_PAUSE / SR are AST-pinned equal to
    clone_learn_audio.py (tests/test_learn_narrator_worker.py) — the job also carries the voice it
    froze, and `voice_params` refuses one outside the ranges below.
  * EVERY `generate(` call passes `exaggeration=`: chatterbox/tts.py:208-231 re-builds the emotion
    conditioning whenever the argument differs from the prepared one, and its DEFAULT is 0.5 — so
    leaving it out silently renders at 0.5 after `prepare_conditionals(…, exaggeration=0.65)`.
    An AST test fails on any `generate(` without it; `render` also asserts the prepared
    `conds.t3.emotion_adv` equals the job's exaggeration before the first block.
  * The conditioning is prepared ONCE (`prepare_conditionals(REF, exaggeration=…)`), then each call
    is `generate(text, exaggeration=…, cfg_weight=…)` under `torch.manual_seed(SEED + i)` for block
    i. The script re-prepared it per call (same deterministic result, ~1 s each wasted).

The 40-second cap. Chatterbox stops at 1,000 speech tokens = 40.0 s of raw audio and SILENTLY drops
the rest of the text. One call per block (the catalog's block mode) fits plain prose — the
catalog's longest block, 93 words, fits — but not a block dense with figures: the plan's own
75-word worst case says ~128 words aloud ("$574.8 billion" is eight), ≈ 47 s at a natural pace. So:
  1. `call_groups`: a block whose SPOKEN words would run past SPLIT_ESTIMATE_SECONDS at NATURAL_WPS is
     rendered as consecutive sentence groups, joined by SENT_PAUSE — exactly how the script's
     sentence mode joins sentences. A block that fits is ONE call, as in block mode.
  2. The per-call gate (`block_gate`): ≥ TRUNCATION_SECONDS raw, or outside WPS_BAND spoken words per
     raw second (a skipped clause, a run-on, a stall) → rendered once more with seed +
     RERENDER_SEED_OFFSET. A TRUNCATED call of two or more sentences is re-rendered as two halves
     instead (a new seed cannot make the same text fit). Failing again, the job fails with
     `block_gate` naming the block.
Words are SPOKEN words (`timings.spoken_word_count`): counting whitespace tokens would fail an honest
figure-dense block for being slow.

Assembly (`assemble_plan`, as the script): native wpm over the blocks' whitespace words and raw
speech samples → atempo = TARGET_WPM / native (clamped 0.5–2.0); pauses (BLOCK_PAUSE between blocks,
SENT_PAUSE inside a split block) inserted BEFORE the tempo pass, so they land at their length after
it; then ffmpeg atempo + AAC 96k, plus `+faststart` so iOS can start playback before the file is in.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from learn_narrator import timings as tm

logger = logging.getLogger("learn_narrator.chatterbox")

#: Bump with any change to synthesis, the gate, assembly or encoding (written into the m4a and
#: reported with every job).
PIPELINE_VERSION = "chatterbox-0.1.7/block/aac96k/v1"

ASSETS = Path(__file__).resolve().parent.parent / "assets"
REF_NAME = "caydex_voice_achird_v2"
REF = ASSETS / f"{REF_NAME}.wav"
#: sha256 of the reference clip (tests pin it to backend/data/voice_clone/refs/ too).
REF_SHA256 = "7c99bdcefdd6b1e673d10072e78a84293d065a7587869109b4e2a05f220eb1ae"

EXAG = 0.65            # expressiveness / energy (approved)
CFG = 0.40
TARGET_WPM = 165       # 170 (Gemini original) - 3%
SENT_PAUSE = 0.20      # seconds of silence after a sentence (sentence mode only)
BLOCK_PAUSE = 0.60     # seconds at a paragraph / title->paragraph boundary
SR = 24000             # Chatterbox sample rate

SEED = 20261022
RERENDER_SEED_OFFSET = 1000
#: Seed distance between the calls of one split block (keeps them apart from every block's seed).
CALL_SEED_STRIDE = 100_000
#: Chatterbox's hard cap is 1,000 speech tokens = 40.0 s; a call this long was (almost surely) cut.
TRUNCATION_SECONDS = 39.5
#: Spoken words per RAW second (before the tempo pass) — the plan's band. Measured 2026-10-22 on the
#: 23 raw block renders cached on the owner's Mac (data/voice_clone/_block_cache, the catalog's own
#: settings): paragraphs speak 2.6-3.6 spoken words/s (mean ≈ 3.0; one at exactly 3.60).
WPS_BAND: Tuple[float, float] = (1.8, 3.6)
#: A call of fewer spoken words than this is held to SHORT_WPS_BAND instead: Chatterbox pads every
#: call with silence, which a title cannot amortise — the same cache has "Two Identical Engines"
#: (3 words) at 1.96 s = 1.53 w/s and 3-6-word titles at 1.5-3.4 w/s. Under the plan's 1.8 floor
#: every 3-word section title would fail twice and fail the job. The wide band still catches an
#: empty render or a stall (3 words in 4 s).
SHORT_CALL_WORDS = 8
SHORT_WPS_BAND: Tuple[float, float] = (0.9, 5.0)
#: A block estimated longer than this (spoken words ÷ NATURAL_WPS) is split at sentence boundaries:
#: 38 s at the measured mean of 3.0 w/s ≈ 114 spoken words, so every catalog block (the longest says
#: 109 words and was voiced in one call) stays ONE call, while the plan's figure-dense worst case
#: (~128 spoken words) is split. A slower render that still truncates is split reactively (`halves`).
SPLIT_ESTIMATE_SECONDS = 38.0
NATURAL_WPS = 3.0
ATEMPO_RANGE: Tuple[float, float] = (0.5, 2.0)
ENCODE_TIMEOUT_SECONDS = 600

#: What a job's frozen voice may ask for. The reference is the one clip baked into the image.
EXAG_RANGE: Tuple[float, float] = (0.25, 2.0)
CFG_RANGE: Tuple[float, float] = (0.0, 1.0)
WPM_RANGE: Tuple[int, int] = (120, 200)


class BlockGateFailed(RuntimeError):
    def __init__(self, i: int, reason: str) -> None:
        super().__init__(f"block {i}: {reason}")
        self.i = i
        self.reason = reason


class VoiceUnsupported(ValueError):
    pass


@dataclass(frozen=True)
class Voice:
    exaggeration: float
    cfg_weight: float
    target_wpm: int


def voice_params(voice: Mapping[str, Any]) -> Voice:
    """The job's frozen voice → render parameters. A missing key takes the catalog value (with a
    WARNING); a value outside its range, or any reference but the baked clip, is VoiceUnsupported."""
    voice = dict(voice or {})
    ref = voice.get("ref", REF_NAME)
    if ref != REF_NAME:
        raise VoiceUnsupported(f"reference {ref!r} is not baked into this image (only {REF_NAME})")

    def num(key: str, default: float, lo: float, hi: float) -> float:
        if key not in voice:
            logger.warning("job voice has no %s — using the catalog's %s", key, default)
            return default
        v = voice[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
            raise VoiceUnsupported(f"{key}={v!r} is outside {lo}-{hi}")
        return float(v)

    exag = num("exaggeration", EXAG, *EXAG_RANGE)
    cfg = num("cfg_weight", CFG, *CFG_RANGE)
    wpm = num("target_wpm", TARGET_WPM, *WPM_RANGE)
    if exag != EXAG or cfg != CFG or wpm != TARGET_WPM:
        logger.warning("job voice differs from the catalog's (exaggeration %s, cfg %s, %s wpm): "
                       "exaggeration=%s cfg_weight=%s target_wpm=%s", EXAG, CFG, TARGET_WPM, exag, cfg, wpm)
    return Voice(exag, cfg, int(round(wpm)))


# ── pure helpers (unit-tested) ────────────────────────────────────────────────


def band_for(spoken_words: int) -> Tuple[float, float]:
    return SHORT_WPS_BAND if spoken_words < SHORT_CALL_WORDS else WPS_BAND


def pace_problem(spoken_words: int, raw_seconds: float) -> Optional[str]:
    """The words/s half of the gate (shared with Kokoro's)."""
    if raw_seconds <= 0:
        return "no audio"
    wps = spoken_words / raw_seconds
    lo, hi = band_for(spoken_words)
    if not lo <= wps <= hi:
        return f"{wps:.2f} spoken words/s outside {lo}-{hi} ({spoken_words} words in {raw_seconds:.2f}s)"
    return None


def block_gate(spoken_words: int, raw_seconds: float) -> Optional[str]:
    """None when one call's audio is plausible, else why not."""
    if raw_seconds >= TRUNCATION_SECONDS:
        return f"truncated: {raw_seconds:.2f}s raw ≥ {TRUNCATION_SECONDS}s (Chatterbox stops at 40.0 s)"
    return pace_problem(spoken_words, raw_seconds)


def estimate_seconds(text: str) -> float:
    return tm.spoken_word_count(text) / NATURAL_WPS


def _partition(counts: Sequence[int], k: int) -> List[int]:
    """Cut points splitting `counts` into k contiguous runs of about equal sum (each non-empty)."""
    total = float(sum(counts))
    cuts: List[int] = []
    acc = 0
    for j, c in enumerate(counts[:-1]):
        acc += c
        boundary = total * (len(cuts) + 1) / k
        remaining_items = len(counts) - (j + 1)
        remaining_cuts = k - 1 - len(cuts)
        if remaining_cuts <= 0:
            break
        nxt = acc + counts[j + 1]
        if abs(acc - boundary) <= abs(nxt - boundary) or remaining_items <= remaining_cuts:
            cuts.append(j + 1)
    return cuts


def call_groups(text: str, *, budget: float = SPLIT_ESTIMATE_SECONDS) -> List[str]:
    """The text of each Chatterbox call for one block: the whole block when it is estimated to fit
    `budget`; else the FEWEST runs of whole sentences that each fit, balanced by spoken length (two
    ~20 s calls, never a 37 s call and a 4 s one). A single sentence over the budget stays whole —
    the gate decides. The calls' words concatenate back to the block's words."""
    sents = tm.split_sentences(text)
    total = estimate_seconds(text)
    if total <= budget or len(sents) < 2:
        return [text]
    counts = [tm.spoken_word_count(x) for x in sents]
    k = max(2, -(-int(total * 1000) // int(budget * 1000)))
    while True:
        k = min(k, len(sents))
        cuts = [0] + _partition(counts, k) + [len(sents)]
        groups = [" ".join(sents[a:b]) for a, b in zip(cuts, cuts[1:]) if b > a]
        if k == len(sents) or all(estimate_seconds(g) <= budget for g in groups):
            return groups
        k += 1


def halves(text: str) -> List[str]:
    """Two runs of whole sentences with the most even spoken length ([text] when it has one)."""
    sents = tm.split_sentences(text)
    if len(sents) < 2:
        return [text]
    counts = [tm.spoken_word_count(s) for s in sents]
    total = sum(counts)
    best_k, best = 1, None
    acc = 0
    for k in range(1, len(sents)):
        acc += counts[k - 1]
        gap = abs(total - 2 * acc)
        if best is None or gap < best:
            best_k, best = k, gap
    return [" ".join(sents[:best_k]), " ".join(sents[best_k:])]


@dataclass
class Call:
    text: str
    seed: int
    samples: int
    spoken_words: int
    rerendered: bool = False
    first_reason: Optional[str] = None
    audio: Any = field(default=None, repr=False)


@dataclass
class BlockRender:
    i: int
    words: int
    calls: List[Call]
    sr: int = SR

    @property
    def samples(self) -> int:
        """Speech samples (the calls' audio, without the pauses between them)."""
        return sum(c.samples for c in self.calls)

    @property
    def spoken_words(self) -> int:
        return sum(c.spoken_words for c in self.calls)

    @property
    def rerendered(self) -> bool:
        return any(c.rerendered for c in self.calls)

    @property
    def first_reason(self) -> Optional[str]:
        return next((c.first_reason for c in self.calls if c.first_reason), None)

    @property
    def audio(self) -> Any:
        return self.calls[0].audio


def _render_call(i: int, text: str, seed: int, synth: Callable[[str, int], Tuple[Any, int]],
                 gate: Callable[[int, float], Optional[str]], sr: int, split: bool,
                 progress: Callable[[str], None]) -> List[Call]:
    spoken = max(1, tm.spoken_word_count(text))
    audio, samples = synth(text, seed)
    reason = gate(spoken, samples / sr)
    if reason is None:
        return [Call(text, seed, int(samples), spoken, audio=audio)]
    retry = seed + RERENDER_SEED_OFFSET
    parts = halves(text) if (split and samples / sr >= TRUNCATION_SECONDS) else [text]
    progress(f"block {i}: {reason} — re-rendering {'as two halves ' if len(parts) > 1 else ''}with seed {retry}")
    out = []
    for k, part in enumerate(parts):
        sp = max(1, tm.spoken_word_count(part))
        s = retry + CALL_SEED_STRIDE * k
        a, n = synth(part, s)
        again = gate(sp, n / sr)
        if again:
            raise BlockGateFailed(i, f"{again} (also on the first render: {reason})")
        out.append(Call(part, s, int(n), sp, rerendered=True, first_reason=reason, audio=a))
    return out


def render_blocks(blocks: Sequence[Mapping[str, Any]], synth: Callable[[str, int], Tuple[Any, int]], *,
                  seed: int = SEED, gate: Callable[[int, float], Optional[str]] = block_gate, sr: int = SR,
                  split: bool = True, progress: Callable[[str], None] = lambda s: None) -> List[BlockRender]:
    """Render every block through `synth(text, seed) -> (audio, samples)`, gating each call (see
    the module docstring). Pure orchestration — the torch calls live in `synth` — so the gate, the
    split and the seeds are tested without a model. Kokoro passes its own `gate` and `split=False`."""
    out: List[BlockRender] = []
    t0 = time.monotonic()
    for n, block in enumerate(blocks):
        i = int(block["i"])
        text = str(block["text"])
        calls: List[Call] = []
        for k, part in enumerate(call_groups(text) if split else [text]):
            calls += _render_call(i, part, seed + i + CALL_SEED_STRIDE * k, synth, gate, sr, split, progress)
        b = BlockRender(i=i, words=len(text.split()), calls=calls, sr=sr)
        out.append(b)
        progress(f"block {n + 1}/{len(blocks)} i={i} {b.samples / sr:.1f}s raw in {len(calls)} call(s) "
                 f"({b.spoken_words / max(b.samples / sr, 1e-9):.2f} w/s){' re-rendered' if b.rerendered else ''} "
                 f"elapsed {time.monotonic() - t0:.0f}s")
    return out


@dataclass(frozen=True)
class Plan:
    atempo: float
    native_wpm: float
    pause_samples: int             # between blocks (pre-tempo)
    sent_pause_samples: int        # between the calls of one split block (pre-tempo)
    offsets: Tuple[int, ...]       # each block's first sample in the PRE-tempo assembly
    lengths: Tuple[int, ...]       # each block's samples, its inner pauses included
    total_samples: int


def assemble_plan(lengths: Sequence[Union[int, Sequence[int]]], words: Sequence[int], *,
                  target_wpm: int = TARGET_WPM, sr: int = SR, block_pause: float = BLOCK_PAUSE,
                  sent_pause: float = SENT_PAUSE) -> Plan:
    """clone_learn_audio.synth_clip's maths: native wpm from whitespace words over the raw SPEECH
    (pauses excluded), atempo clamped to ATEMPO_RANGE, pauses sized pre-tempo, none after the last.
    `lengths[b]` is block b's speech samples, or the list of its calls' samples when it was split."""
    parts = [[int(x)] if isinstance(x, int) else [int(y) for y in x] for x in lengths]
    speech = sum(sum(p) for p in parts)
    total_words = sum(int(w) for w in words)
    native_wpm = (total_words / (speech / sr) * 60) if speech else float(target_wpm)
    atempo = max(ATEMPO_RANGE[0], min(ATEMPO_RANGE[1], target_wpm / native_wpm)) if native_wpm else 1.0
    pause = int(round(block_pause * atempo * sr))
    inner = int(round(sent_pause * atempo * sr))
    offsets, block_lengths = [], []
    t = 0
    for k, p in enumerate(parts):
        offsets.append(t)
        length = sum(p) + inner * max(0, len(p) - 1)
        block_lengths.append(length)
        t += length + (pause if k < len(parts) - 1 else 0)
    return Plan(atempo=atempo, native_wpm=native_wpm, pause_samples=pause, sent_pause_samples=inner,
                offsets=tuple(offsets), lengths=tuple(block_lengths), total_samples=t)


def final_seconds(raw_samples: int, plan: Plan, sr: int = SR) -> float:
    """A pre-tempo sample position → seconds in the final (tempo-corrected) file."""
    return raw_samples / sr / plan.atempo


def encode_command(wav: Path, out: Path, atempo: float, *, ffmpeg: str = "ffmpeg") -> List[str]:
    """The script's encode (atempo + AAC 96k) plus +faststart; the bitexact flags and the comment
    change no audio, they make a re-render of the same input byte-identical (content-addressed path)."""
    return [ffmpeg, "-nostdin", "-y", "-loglevel", "error", "-i", str(wav),
            "-filter:a", f"atempo={atempo:.5f}", "-c:a", "aac", "-b:a", "96k",
            "-map_metadata", "-1", "-metadata", f"comment={PIPELINE_VERSION}",
            "-fflags", "+bitexact", "-flags:a", "+bitexact", "-movflags", "+faststart", str(out)]


def leading_silence_seconds(samples: Any, sr: int, *, rms: float = 0.02, frame: float = 0.01) -> float:
    """Seconds before the first 10 ms frame whose RMS reaches `rms` (verify_learn_voice's silence
    floor) — where the voice starts inside a block's raw audio. `samples` is a 1-D numpy array."""
    import numpy as np

    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    n = max(1, int(frame * sr))
    usable = (len(x) // n) * n
    if usable == 0:
        return 0.0
    frames = x[:usable].reshape(-1, n)
    voiced = np.nonzero(np.sqrt((frames ** 2).mean(axis=1)) >= rms)[0]
    return float(voiced[0] * n / sr) if len(voiced) else float(len(x) / sr)


def ref_sha256(path: Path = REF) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def block_record(b: BlockRender, offset: int, length: int, sr: int = SR) -> Dict[str, Any]:
    """One block of render.json. raw_* describe the block's span in the PRE-tempo assembly (its inner
    pauses included — what the aligner's window needs); words_per_sec is over its speech only."""
    speech = b.samples / sr
    return {
        "i": b.i, "words": b.words, "spoken_words": b.spoken_words,
        "raw_offset": offset, "raw_samples": length, "raw_seconds": round(speech, 3),
        "words_per_sec": round(b.spoken_words / speech, 3) if b.samples else 0.0,
        "rerendered": b.rerendered, "first_reason": b.first_reason, "seed": b.calls[0].seed,
        "calls": [{"raw_seconds": round(c.samples / sr, 3), "spoken_words": c.spoken_words,
                   "words_per_sec": round(c.spoken_words / (c.samples / sr), 3) if c.samples else 0.0,
                   "seed": c.seed, "rerendered": c.rerendered} for c in b.calls],
        "max_call_seconds": round(max(c.samples for c in b.calls) / sr, 3),
    }


# ── the render (child process only) ──────────────────────────────────────────


def render(job: Mapping[str, Any], out_dir: Path, *, threads: int,
           progress: Callable[[str], None] = print) -> Dict[str, Any]:
    """Render, gate, assemble and encode the job's blocks; write narration.m4a. Returns the
    render.json body (per block: raw offset/length, words/s, its calls, leading silence)."""
    import numpy as np
    import soundfile as sf
    import torch
    from chatterbox.tts import ChatterboxTTS

    voice = voice_params(job.get("voice") or {})
    if ref_sha256() != REF_SHA256:
        raise VoiceUnsupported(f"{REF.name} does not match its pinned sha256 — the image is not the reviewed one")
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(max(1, min(threads, 2)))
    t0 = time.monotonic()
    model = ChatterboxTTS.from_pretrained(device="cpu")
    model.prepare_conditionals(str(REF), exaggeration=voice.exaggeration)
    emotion = float(model.conds.t3.emotion_adv.reshape(-1)[0])
    if abs(emotion - voice.exaggeration) > 1e-6:
        raise VoiceUnsupported(f"prepared emotion_adv {emotion} != exaggeration {voice.exaggeration}")
    progress(f"model loaded in {time.monotonic() - t0:.0f}s; emotion_adv={emotion}")

    def synth(text: str, seed: int) -> Tuple[Any, int]:
        torch.manual_seed(seed)
        wav = model.generate(text, exaggeration=voice.exaggeration, cfg_weight=voice.cfg_weight)
        audio = wav.detach().cpu().numpy().astype(np.float32).reshape(-1)
        return audio, int(audio.shape[0])

    t_render = time.monotonic()
    rendered = render_blocks(job["blocks"], synth, seed=int(job.get("seed") or SEED), progress=progress)
    render_seconds = time.monotonic() - t_render
    plan = assemble_plan([[c.samples for c in b.calls] for b in rendered], [b.words for b in rendered],
                         target_wpm=voice.target_wpm)
    block_pause = np.zeros(plan.pause_samples, dtype=np.float32)
    inner_pause = np.zeros(plan.sent_pause_samples, dtype=np.float32)
    parts: List[Any] = []
    for k, b in enumerate(rendered):
        for j, c in enumerate(b.calls):
            parts.append(c.audio)
            if j < len(b.calls) - 1:
                parts.append(inner_pause)
        if k < len(rendered) - 1:
            parts.append(block_pause)
    full = np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)
    raw_wav = out_dir / "narration.raw.wav"
    sf.write(str(raw_wav), full, SR, subtype="FLOAT")
    m4a = out_dir / "narration.m4a"
    res = subprocess.run(encode_command(raw_wav, m4a, plan.atempo), capture_output=True, text=True,
                         timeout=ENCODE_TIMEOUT_SECONDS)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg encode failed ({res.returncode}): {res.stderr[-400:]}")
    raw_wav.unlink(missing_ok=True)
    blocks_out = []
    for b, off, length in zip(rendered, plan.offsets, plan.lengths):
        rec = block_record(b, off, length)
        rec["lead_silence"] = round(leading_silence_seconds(b.audio, SR), 3)
        blocks_out.append(rec)
    return {
        "engine": "chatterbox", "pipeline": PIPELINE_VERSION, "sample_rate": SR,
        "atempo": plan.atempo, "native_wpm": round(plan.native_wpm, 3), "pause_samples": plan.pause_samples,
        "sent_pause_samples": plan.sent_pause_samples,
        "emotion_adv": emotion, "exaggeration": voice.exaggeration, "cfg_weight": voice.cfg_weight,
        "target_wpm": voice.target_wpm, "audio": m4a.name, "render_seconds": round(render_seconds, 1),
        "wall_seconds": round(time.monotonic() - t0, 1), "blocks": blocks_out,
    }
