"""
Shared torchaudio MMS_FA forced-alignment primitives.

Reused by align_journey_audio.py (word-level) and align_money_moves_audio.py (sentence-level).
Mirrors the alignment core of align_book_audio.py, but standalone: no Gemini key, no module-level
side effects, no book imports — alignment is free (local CTC) and key-independent.

Also provides a tiny public-bucket downloader so the aligners can pull the deployed audio bytes
(the exact bytes users hear) before aligning.
"""
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx
import torch
import torchaudio
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]                 # backend/

_BUNDLE = torchaudio.pipelines.MMS_FA
_model = None
_tokenizer = None
_aligner = None


def load_model():
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


def strip_markup(s: str) -> str:
    """Remove the **bold** markers — matches the text the TTS spoke and iOS displays."""
    return re.sub(r"\*\*", "", s or "")


# Split on whitespace that follows a sentence terminator (. ! ?), optionally followed by a closing
# quote — the quote stays in the LEFT sentence (it's in the lookbehind, only \s+ is consumed) so the
# span text rejoins to the original. Same splitter as gen_book_read_along.split_sentences.
_SENT = re.compile(r'(?:(?<=[.!?])|(?<=[.!?]["”’]))\s+(?=["“‘A-Z0-9])')


def split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [s.strip() for s in _SENT.split(text) if s.strip()]


def decode_to_wav(m4a: Path) -> Path:
    wav = Path(tempfile.gettempdir()) / f"{m4a.stem}.align.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(m4a),
         "-ac", "1", "-ar", str(_BUNDLE.sample_rate), str(wav)],
        check=True,
    )
    return wav


def load_waveform(m4a: Path):
    """Decode an m4a to a normalized mono float waveform [1, samples] at the model's sample rate."""
    wav = decode_to_wav(m4a)
    sr, data = wavfile.read(wav)
    if data.ndim > 1:
        data = data.mean(axis=1)
    waveform = torch.from_numpy(data.astype("float32") / 32768.0).unsqueeze(0)
    wav.unlink(missing_ok=True)
    return waveform, sr


def align_word_spans(waveform, sr, norm_words: list[str]):
    """Forced-align `norm_words` (already normalized, non-empty) against the whole waveform.
    Returns (spans, total_seconds) where spans[i] = (start_sec, end_sec) | None for word i."""
    model, tokenizer, aligner = load_model()
    with torch.inference_mode():
        emission, _ = model(waveform)
        token_spans = aligner(emission[0], tokenizer(norm_words))
    num_frames = emission.size(1)
    total = waveform.size(1) / sr
    spf = total / num_frames                                   # seconds per emission frame
    out = []
    for spans in token_spans:
        out.append((spans[0].start * spf, spans[-1].end * spf) if spans else None)
    return out, total


def fill_gaps(timings: list, total: float) -> list:
    """Turn a sparse [(start,end)|None] list into a dense, monotonic non-decreasing one.
    A None word (out-of-vocab: pure number/punctuation) gets [prev_end, next_known_start]."""
    n = len(timings)
    out = [None] * n
    last_end = 0.0
    for i in range(n):
        t = timings[i]
        if t is None:
            nxt = next((timings[j][0] for j in range(i + 1, n) if timings[j] is not None), None)
            s = last_end
            e = nxt if (nxt is not None and nxt >= s) else last_end
            out[i] = (s, e)
        else:
            s, e = t
            s = max(s, last_end)
            e = max(e, s)
            out[i] = (s, e)
            last_end = e
    return [(min(s, total), min(e, total)) for s, e in out]


def _supabase_base() -> str:
    url = os.environ.get("SUPABASE_URL")
    if not url:
        env = ROOT / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("SUPABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    assert url, "SUPABASE_URL not found in env or backend/.env"
    return url.rstrip("/")


def download_public(bucket: str, object_path: str, dest: Path) -> bool:
    """Download a public Storage object to `dest`. Returns False (and warns) if it 404s."""
    url = f"{_supabase_base()}/storage/v1/object/public/{bucket}/{object_path}"
    try:
        r = httpx.get(url, timeout=120)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"    ! could not download {object_path}: {exc}")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return True
