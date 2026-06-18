"""
Forced-align an EXISTING book narration .m4a against its known transcript to get accurate
per-sentence start/end times (replacing the word-count estimate). Reads the audio only; the
audio file is never modified.

Uses torchaudio's MMS_FA CTC forced aligner. For each core we slice the audio to that core's
segment (from the manifest), feed the spoken transcript for that segment (bridge + body, in order
— the bridge is spoken but not highlighted, so it's aligned as context and discarded), and read
off each BODY sentence's [start, end].

Output: backend/data/book_audio/<order>_<slug>.readalong.json
    { curriculum_order, slug, cores: { <num>: [ {isHeading, sentences:[{text,start,end}]} ] } }
which gen_book_read_along.py prefers over the proportional estimate when present.

Usage (from backend/):
    ./venv/bin/python scripts/align_book_audio.py 1
"""
import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import torch
import torchaudio
from scipy.io import wavfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
with contextlib.redirect_stdout(io.StringIO()):
    import gen_books_swift as g  # noqa: E402
import generate_book_audio as gba  # noqa: E402
import gen_book_read_along as gra  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "data/book_audio"

_BUNDLE = torchaudio.pipelines.MMS_FA
_model = None
_tokenizer = None
_aligner = None


def _load_model():
    global _model, _tokenizer, _aligner
    if _model is None:
        print("loading MMS_FA forced-alignment model (first run downloads it)…")
        _model = _BUNDLE.get_model()
        _model.eval()
        _tokenizer = _BUNDLE.get_tokenizer()
        _aligner = _BUNDLE.get_aligner()
    return _model, _tokenizer, _aligner


def normalize_word(w: str) -> str:
    """Reduce a transcript word to the aligner's alphabet (lowercase a-z plus apostrophe)."""
    return re.sub(r"[^a-z']", "", w.lower())


def decode_to_wav(m4a: Path) -> Path:
    wav = Path(tempfile.gettempdir()) / f"{m4a.stem}.align.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(m4a),
         "-ac", "1", "-ar", str(_BUNDLE.sample_rate), str(wav)],
        check=True,
    )
    return wav


def spoken_units(bridge: str, blocks, recap: str = ""):
    """Ordered (normalized_word, tag) for a core segment. tag = (block_idx, sentence_idx) for body
    words, or None for bridge / action-recap words (spoken but not highlighted). Drops words that
    normalize to empty (rare: pure numbers)."""
    units = []
    for w in bridge.split():
        n = normalize_word(w)
        if n:
            units.append((n, None))
    for bi, (is_heading, text) in enumerate(blocks):
        sents = [text] if is_heading else gra.split_sentences(text)
        for si, sent in enumerate(sents):
            for w in sent.split():
                n = normalize_word(w)
                if n:
                    units.append((n, (bi, si)))
    for w in recap.split():                                # spoken action-plan close — context only
        n = normalize_word(w)
        if n:
            units.append((n, None))
    return units


def align_core(waveform, sr, core_start, core_end, bridge, blocks, recap=""):
    model, tokenizer, aligner = _load_model()
    s0, s1 = int(round(core_start * sr)), int(round(core_end * sr))
    seg = waveform[:, s0:s1]
    units = spoken_units(bridge, blocks, recap)
    words = [n for n, _ in units]
    with torch.inference_mode():
        emission, _ = model(seg)
        token_spans = aligner(emission[0], tokenizer(words))
    num_frames = emission.size(1)
    spf = (s1 - s0) / sr / num_frames                      # seconds per emission frame
    # Aggregate word spans into per-sentence [start, end] (absolute time in the full audio).
    sent_bounds: dict = {}
    for (n, tag), spans in zip(units, token_spans):
        if tag is None or not spans:
            continue
        start = core_start + spans[0].start * spf
        end = core_start + spans[-1].end * spf
        lo, hi = sent_bounds.get(tag, (start, end))
        sent_bounds[tag] = (min(lo, start), max(hi, end))
    return sent_bounds


def build_book(order: int, dirname: str, manifest: dict, waveform, sr):
    cores = sorted([p for p in (g.BD / dirname).glob("*.txt")
                    if re.fullmatch(r"core\s+\d+", p.stem, re.I)], key=g.core_num)
    parsed = [(g.core_num(p), *g.parse_core(p)) for p in cores]
    bridges = gba.BRIDGES.get(order) or gba._generic_bridges(
        manifest["book_title"], manifest["author"], [t for _, t, *_ in parsed])
    starts = {c["number"]: c["start_seconds"] for c in manifest["cores"]}
    total = manifest["total_seconds"]
    nums = sorted(starts)

    out_cores = {}
    for idx, (num, title, sections, action, *_rest) in enumerate(parsed):
        core_start = starts[num]
        # Slice through to the next core start (or end) so the last word is never truncated; the
        # inter-core break is silence and doesn't disturb word alignment.
        core_end = starts[nums[idx + 1]] if idx + 1 < len(nums) else total
        blocks = gra.narrated_blocks(sections)
        recap = gba.action_recap(action)        # spoken at the core's end (context, not highlighted)
        bounds = align_core(waveform, sr, core_start, core_end, bridges[idx], blocks, recap)

        out_blocks = []
        for bi, (is_heading, text) in enumerate(blocks):
            sents = [text] if is_heading else gra.split_sentences(text)
            timed = []
            for si, sent in enumerate(sents):
                if (bi, si) in bounds:
                    s, e = bounds[(bi, si)]
                else:                                       # all words OOV — fall back later in gen
                    s, e = None, None
                timed.append({"text": sent, "start": s, "end": e})
            out_blocks.append({"isHeading": is_heading, "sentences": timed})
        out_cores[num] = out_blocks
        spoken = sum(len(b["sentences"]) for b in out_blocks)
        print(f"  core {num}: aligned {spoken} sentences  (core {core_start:.1f}s–{core_end:.1f}s)")
    return out_cores


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: align_book_audio.py <curriculum_order>")
    order = int(sys.argv[1])
    entry = next((b for b in g.BOOKS if b[0] == order), None)
    if entry is None:
        raise SystemExit(f"No book with curriculum order {order}")
    _, dirname, btitle, _ = entry
    slug = gba.slugify(btitle)
    mpath = AUDIO_DIR / f"{order}_{slug}.manifest.json"
    m4a = AUDIO_DIR / f"{order}_{slug}.m4a"
    if not (mpath.exists() and m4a.exists()):
        raise SystemExit(f"Missing manifest or audio for order {order} ({slug}).")

    manifest = json.loads(mpath.read_text())
    print(f"[{btitle}] forced-aligning {m4a.name}")
    wav = decode_to_wav(m4a)
    # ffmpeg already produced 16kHz mono PCM16; read it directly (avoids torchaudio's torchcodec
    # backend) and convert to a normalized float waveform [1, samples].
    sr, data = wavfile.read(wav)
    if data.ndim > 1:
        data = data.mean(axis=1)
    waveform = torch.from_numpy(data.astype("float32") / 32768.0).unsqueeze(0)

    cores = build_book(order, dirname, manifest, waveform, sr)
    out = AUDIO_DIR / f"{order}_{slug}.readalong.json"
    out.write_text(json.dumps(
        {"curriculum_order": order, "slug": slug, "cores": cores}, indent=2))
    wav.unlink(missing_ok=True)
    print(f"\nwrote {out.name}")
    print("Next: ./venv/bin/python scripts/gen_book_read_along.py  (bakes aligned times into iOS)")


if __name__ == "__main__":
    main()
