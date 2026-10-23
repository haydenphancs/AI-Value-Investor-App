"""
learn_narrator/align.py — read-along for the Chatterbox engine: CTC forced alignment of each
block's words against the FINAL m4a (the bytes listeners hear), with torchaudio's
WAV2VEC2_ASR_BASE_960H bundle (MIT licence; the old catalog aligner was CC-BY-NC and is banned from
this package by a test).

Per block, not per file:
  * The block's window in the final file is known exactly from the assembly: raw offset ÷ SR ÷
    atempo to (raw offset + raw length) ÷ SR ÷ atempo (`block_window`), widened by WINDOW_MARGIN
    (well inside the 0.6 s block pause, so a neighbour's speech never enters the window).
  * Emissions are computed in EMISSION_WINDOW_SECONDS chunks (`emission_chunks`) and concatenated:
    wav2vec2's self-attention grows with the square of the input, so a 40 s block in one pass
    costs far more memory than two 20-30 s passes, for no accuracy the read-along needs.
  * The transcript is the block's whitespace words in their SPOKEN form (`timings.spoken_words_per_word`
    — the model's alphabet is A-Z and the apostrophe; "$574.8" must become the letters the voice
    said), joined by the model's word separator `|`. A word with no letters at all ("—") gets no
    tokens and no span (`None`); `timings.build_block` handles it.

Recorded per block (`checks`): `confidence` (mean posterior probability of the aligned letters),
`word_coverage` (words that had letters to align) and, from the parent, `align_coverage` (sentences
with at least one aligned word) and `start_error` (aligned first word vs the speech onset the
render measured) — the bench's A6 reads them.

Heavy imports (torch, torchaudio, numpy) live inside `align_job` only; everything else is pure.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from learn_narrator import runtime
from learn_narrator import timings as tm

MODEL = "WAV2VEC2_ASR_BASE_960H"
SAMPLE_RATE = 16000
EMISSION_WINDOW_SECONDS = 30.0
#: A tail chunk shorter than this joins the previous chunk (a few hundred samples give no frame).
MIN_CHUNK_SECONDS = 1.0
WINDOW_MARGIN_SECONDS = 0.25
SEPARATOR = "|"
BLANK = 0
DECODE_TIMEOUT_SECONDS = 300


class AlignFailed(RuntimeError):
    pass


# ── pure helpers (unit-tested) ────────────────────────────────────────────────


def block_window(raw_offset: int, raw_samples: int, *, sr: int, atempo: float,
                 margin: float = WINDOW_MARGIN_SECONDS, total: Optional[float] = None) -> Tuple[float, float, float, float]:
    """(lo, hi, core_lo, core_hi) in seconds of the FINAL file. core = the block's own audio
    (raw position ÷ sr ÷ atempo — the tempo pass scales time by 1/atempo); lo/hi = core widened by
    `margin`, clamped to [0, total]."""
    if atempo <= 0:
        raise ValueError(f"atempo must be > 0, got {atempo}")
    core_lo = raw_offset / sr / atempo
    core_hi = (raw_offset + raw_samples) / sr / atempo
    lo = max(0.0, core_lo - margin)
    hi = core_hi + margin
    if total is not None:
        hi = min(hi, float(total))
        lo = min(lo, hi)
    return lo, hi, core_lo, core_hi


def emission_chunks(n_samples: int, sr: int = SAMPLE_RATE, window_seconds: float = EMISSION_WINDOW_SECONDS,
                    min_seconds: float = MIN_CHUNK_SECONDS) -> List[Tuple[int, int]]:
    """[(start, end)) sample ranges covering [0, n_samples) in `window_seconds` chunks; a short
    tail is merged into the chunk before it."""
    if n_samples <= 0:
        return []
    size = int(window_seconds * sr)
    out: List[Tuple[int, int]] = []
    a = 0
    while a < n_samples:
        b = min(a + size, n_samples)
        out.append((a, b))
        a = b
    if len(out) > 1 and out[-1][1] - out[-1][0] < int(min_seconds * sr):
        tail = out.pop()
        out[-1] = (out[-1][0], tail[1])
    return out


def transcript_targets(words: Sequence[str], dictionary: Mapping[str, int]) -> Tuple[List[int], List[Optional[Tuple[int, int]]]]:
    """(target ids, per-word (first, last) target index or None). Each whitespace word becomes its
    spoken words' letters, `|` between spoken words and between whitespace words; a character the
    model has no label for is dropped; a word left with no letter gets None."""
    sep = dictionary[SEPARATOR]
    spoken = tm.spoken_words_per_word(list(words))
    targets: List[int] = []
    slices: List[Optional[Tuple[int, int]]] = []
    for parts in spoken:
        letters = ["".join(c for c in w.upper() if c in dictionary and c != SEPARATOR) for w in parts]
        letters = [w for w in letters if w]
        if not letters:
            slices.append(None)
            continue
        if targets:
            targets.append(sep)
        first = len(targets)
        for k, w in enumerate(letters):
            if k:
                targets.append(sep)
            targets += [dictionary[c] for c in w]
        slices.append((first, len(targets) - 1))
    return targets, slices


def ctc_frames_needed(targets: Sequence[int]) -> int:
    """Minimum emission frames a CTC alignment of `targets` needs (a repeated label needs a blank
    between its two frames)."""
    repeats = sum(1 for a, b in zip(targets, targets[1:]) if a == b)
    return len(targets) + repeats


def word_spans(slices: Sequence[Optional[Tuple[int, int]]], token_spans: Sequence[Tuple[int, int, float]],
               targets: Sequence[int], *, sep: int, seconds_per_frame: float,
               offset: float) -> List[Optional[List[float]]]:
    """Per word: [start, end, confidence] in seconds of the final file, or None. `token_spans[k]` =
    (start frame, end frame exclusive, probability) of target k; separators do not count toward a
    word's confidence."""
    if len(token_spans) != len(targets):
        raise AlignFailed(f"{len(token_spans)} token spans for {len(targets)} targets")
    out: List[Optional[List[float]]] = []
    for sl in slices:
        if sl is None:
            out.append(None)
            continue
        a, b = sl
        probs = [token_spans[k][2] for k in range(a, b + 1) if targets[k] != sep]
        start = offset + token_spans[a][0] * seconds_per_frame
        end = offset + token_spans[b][1] * seconds_per_frame
        out.append([round(start, 3), round(max(end, start), 3),
                    round(sum(probs) / len(probs), 4) if probs else 0.0])
    return out


def start_error(first_word_start: Optional[float], core_lo: float, lead_silence: float, atempo: float) -> Optional[float]:
    """|aligned first word − where the render says the voice starts| in seconds (A6: ≤ 0.15 s)."""
    if first_word_start is None:
        return None
    return round(abs(first_word_start - (core_lo + lead_silence / atempo)), 3)


# ── the aligner (child process only) ─────────────────────────────────────────


def _refuse_download(*args: Any, **kwargs: Any) -> None:
    raise runtime.ChildFailed("weights_missing", "a model download was attempted; the image must bake every "
                                                 "weight (torch hub WAV2VEC2_ASR_BASE_960H)")


def load_model(threads: int):
    """(model, dictionary) with downloads refused: the checkpoint must already be in TORCH_HOME."""
    import torch
    import torch.hub
    import torchaudio

    torch.set_num_threads(threads)
    torch.hub.download_url_to_file = _refuse_download  # the baked file is the only file
    ckpt = Path(torch.hub.get_dir()) / "checkpoints" / runtime.WAV2VEC2_CHECKPOINT
    if not ckpt.exists():
        raise runtime.ChildFailed("weights_missing", f"{ckpt} is not baked into the image")
    bundle = getattr(torchaudio.pipelines, MODEL)
    model = bundle.get_model()
    model.eval()
    labels = bundle.get_labels()
    if labels[BLANK] != "-" or SEPARATOR not in labels:
        raise AlignFailed(f"unexpected {MODEL} labels {labels[:4]}…")
    return model, {c: i for i, c in enumerate(labels)}


def decode_audio(path: Path) -> Any:
    """The final m4a → mono float32 numpy array at SAMPLE_RATE (ffmpeg honours the AAC edit list,
    so sample 0 is the first sample listeners hear)."""
    import numpy as np

    res = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-ac", "1", "-ar",
                          str(SAMPLE_RATE), "-f", "f32le", "-"], capture_output=True, timeout=DECODE_TIMEOUT_SECONDS)
    if res.returncode != 0:
        raise AlignFailed(f"ffmpeg could not decode {path.name}: {res.stderr[-300:]!r}")
    return np.frombuffer(res.stdout, dtype=np.float32).copy()


def align_block(model: Any, dictionary: Mapping[str, int], audio: Any, text: str, lo: float, hi: float) -> Dict[str, Any]:
    import torch
    import torchaudio.functional as F

    a, b = int(lo * SAMPLE_RATE), int(hi * SAMPLE_RATE)
    window = audio[a:b]
    words = tm.strip_markup(text).split()
    targets, slices = transcript_targets(words, dictionary)
    result: Dict[str, Any] = {"window": [round(lo, 3), round(hi, 3)], "targets": len(targets)}
    if not targets:
        result.update({"words": [None] * len(words), "confidence": None, "word_coverage": 0.0, "frames": 0})
        return result
    emissions = []
    with torch.inference_mode():
        for c0, c1 in emission_chunks(len(window)):
            chunk = torch.from_numpy(window[c0:c1]).unsqueeze(0)
            em, _ = model(chunk)
            emissions.append(em[0])
        log_probs = torch.log_softmax(torch.cat(emissions, dim=0), dim=-1).unsqueeze(0)
    frames = int(log_probs.shape[1])
    if frames < ctc_frames_needed(targets):
        raise AlignFailed(f"window {lo:.2f}-{hi:.2f}s has {frames} frames for {len(targets)} letters — "
                          "the audio is far shorter than the text (truncated or skipped)")
    tgt = torch.tensor([targets], dtype=torch.int32)
    aligned, scores = F.forced_align(log_probs, tgt, blank=BLANK)
    spans = F.merge_tokens(aligned[0], scores[0].exp(), blank=BLANK)
    token_spans = [(int(s.start), int(s.end), float(s.score)) for s in spans]
    spf = (len(window) / SAMPLE_RATE) / frames
    per_word = word_spans(slices, token_spans, targets, sep=dictionary[SEPARATOR], seconds_per_frame=spf, offset=lo)
    confs = [w[2] for w in per_word if w is not None]
    result.update({
        "words": per_word, "frames": frames,
        "confidence": round(sum(confs) / len(confs), 4) if confs else None,
        "word_coverage": round(sum(1 for w in per_word if w is not None) / len(words), 4) if words else 1.0,
    })
    return result


def align_job(job: Mapping[str, Any], out_dir: Path, *, threads: int,
              progress: Callable[[str], None] = print) -> Dict[str, Any]:
    """Align every block of a rendered Chatterbox job (render.json from the render child)."""
    import json

    t0 = time.monotonic()
    render = json.loads((out_dir / "render.json").read_text(encoding="utf-8"))
    audio = decode_audio(out_dir / render["audio"])
    total = len(audio) / SAMPLE_RATE
    model, dictionary = load_model(threads)
    texts = {int(b["i"]): str(b["text"]) for b in job["blocks"]}
    blocks = []
    for n, rb in enumerate(render["blocks"]):
        lo, hi, core_lo, core_hi = block_window(int(rb["raw_offset"]), int(rb["raw_samples"]),
                                                sr=int(render["sample_rate"]), atempo=float(render["atempo"]),
                                                total=total)
        res = align_block(model, dictionary, audio, texts[int(rb["i"])], lo, hi)
        res.update({"i": int(rb["i"]), "core": [round(core_lo, 3), round(core_hi, 3)]})
        blocks.append(res)
        if (n + 1) % 10 == 0 or n + 1 == len(render["blocks"]):
            progress(f"aligned {n + 1}/{len(render['blocks'])} blocks, {time.monotonic() - t0:.0f}s")
    return {"model": MODEL, "sample_rate": SAMPLE_RATE, "audio_seconds": round(total, 3),
            "wall_seconds": round(time.monotonic() - t0, 1), "blocks": blocks}
