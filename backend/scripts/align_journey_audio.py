"""
Forced-align each Investor Journey card narration clip against its text to get accurate per-WORD
start/end times, so the lesson reading view highlights the exact word being spoken (replacing the
old character-fraction estimate). Reads audio only; never modifies it. No Gemini key needed.

For each narrated card it writes:
    card["readAlongWords"] = [ {text, start, end}, ... ]   (clip-relative seconds)
where readAlongWords[i] corresponds 1:1 to the i-th whitespace token of strip_markup(card["text"])
— the same tokenization iOS uses for its word ranges (JourneyContentStore sets the card's
audioText to text with **markup** stripped; AIVoiceManager splits that on whitespace). So iOS just
picks the active word index by time and the existing word-range highlight becomes accurate.

Missing clips are downloaded from the public `journey-media` bucket first (free).

Usage (from backend/):
    ./venv/bin/python scripts/align_journey_audio.py            # all lessons
    ./venv/bin/python scripts/align_journey_audio.py compound   # lesson-key / clip prefix filter
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _forced_align as fa  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
_FRONTEND_JSON = REPO / "frontend/ios/ios/Resources/Journey/journey_lessons.json"
JSON_PATH = _FRONTEND_JSON if _FRONTEND_JSON.exists() else BACKEND / "data/journey_lessons.json"
_VENDORED = BACKEND / "data/journey_lessons.json"
AUDIO_DIR = BACKEND / "data/journey_audio"
BUCKET = "journey-media"

only = sys.argv[1] if len(sys.argv) > 1 else None


def ensure_clip(clip: str) -> Path | None:
    """Local path to <clip>.m4a, downloading from the bucket if absent. None if unavailable."""
    local = AUDIO_DIR / f"{clip}.m4a"
    if local.exists():
        return local
    if fa.download_public(BUCKET, f"audio/{clip}.m4a", local):
        print(f"    ↓ fetched {clip}.m4a")
        return local
    return None


def align_card(clip: str, text: str) -> list | None:
    m4a = ensure_clip(clip)
    if m4a is None:
        return None
    display = fa.strip_markup(text).split()
    norm = [fa.normalize_word(w) for w in display]
    idxs = [k for k, n in enumerate(norm) if n]
    if not idxs:
        return None
    waveform, sr = fa.load_waveform(m4a)
    spans, total = fa.align_word_spans(waveform, sr, [norm[k] for k in idxs])
    timings = [None] * len(display)
    for p, k in enumerate(idxs):
        timings[k] = spans[p]
    filled = fa.fill_gaps(timings, total)
    return [{"text": display[k], "start": round(s, 2), "end": round(e, 2)}
            for k, (s, e) in enumerate(filled)]


def main():
    data = json.loads(JSON_PATH.read_text())
    aligned = skipped = 0
    for lesson in data["lessons"]:
        cards = lesson["cards"]
        key = next((c.get("audioClip") for c in cards if c.get("audioClip")), "") or ""
        if only and not key.startswith(only):
            continue
        print(f"[{lesson['title']}]")
        for card in cards:
            clip = card.get("audioClip")
            text = card.get("text")
            if not clip or not text:
                continue
            words = align_card(clip, text)
            if words:
                card["readAlongWords"] = words
                aligned += 1
                print(f"  {clip:28s} {len(words):3d} words  ({words[-1]['end']:.1f}s)")
            else:
                skipped += 1
                print(f"  {clip:28s} skipped (no audio / no alignable words)")

    JSON_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    if _VENDORED != JSON_PATH and _VENDORED.exists():
        _VENDORED.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"(also updated vendored {_VENDORED.relative_to(BACKEND)})")
    print(f"\nwrote readAlongWords into {JSON_PATH.name}: {aligned} cards aligned, {skipped} skipped")
    print("Next: ./venv/bin/python scripts/seed_journey.py   (re-seed lessons with timings)")


if __name__ == "__main__":
    main()
