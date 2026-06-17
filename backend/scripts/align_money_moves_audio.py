"""
Forced-align each Money Moves article narration (ONE .m4a per article) against its transcript to
get accurate per-SENTENCE start/end times, so the article reading view highlights the exact
sentence being spoken — like the Book Library. Reads audio only; no Gemini key needed.

Writes (additive, optional) into frontend money_moves.json:
  - paragraph / subheading / quote / callout blocks: block["readAlong"]      = [{text,start,end}, ...]
  - bulletList blocks:                                block["itemsReadAlong"] = [[{text,start,end}], ...]  (per item)

Times are absolute within the article's single audio file (iOS compares to AudioManager.currentTime).
The transcript order mirrors generate_money_moves_audio.narration_blocks (title, subtitle, then each
section's title + content). Title / subtitle / section-titles are spoken CONTEXT — fed to the
aligner so the body timings stay correct, but not highlighted. Missing audio is downloaded from the
public money-moves-media bucket first (free).

Usage (from backend/):
    ./venv/bin/python scripts/align_money_moves_audio.py            # all articles
    ./venv/bin/python scripts/align_money_moves_audio.py amazon     # slug substring filter
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _forced_align as fa  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
_FRONTEND_JSON = REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
JSON_PATH = _FRONTEND_JSON if _FRONTEND_JSON.exists() else BACKEND / "data/money_moves.json"
_VENDORED = BACKEND / "data/money_moves.json"
AUDIO_DIR = BACKEND / "data/money_moves_audio"
BUCKET = "money-moves-media"

only = sys.argv[1] if len(sys.argv) > 1 else None

_BODY_TEXT_TYPES = ("paragraph", "subheading", "quote", "callout")


def ensure_audio(slug: str) -> Path | None:
    local = AUDIO_DIR / f"{slug}.m4a"
    if local.exists():
        return local
    if fa.download_public(BUCKET, f"audio/{slug}.m4a", local):
        print(f"    ↓ fetched {slug}.m4a")
        return local
    return None


def collect_units(article: dict):
    """Ordered word units + a per-destination sentence registry.

    units: [(normalized_word, tag)] in spoken order; tag = (base, sentence_idx) for highlighted body
           sentences, or None for spoken context (title/subtitle/section titles).
    reg:   {base: [sentence_text, ...]} where base identifies where the timings get written back.
    """
    units: list = []
    reg: dict = {}

    def context(text: str):
        for w in fa.strip_markup(text).split():
            n = fa.normalize_word(w)
            if n:
                units.append((n, None))

    def body(text: str, base: tuple):
        sents = fa.split_sentences(fa.strip_markup(text))
        if not sents:
            return
        reg[base] = sents
        for senti, sent in enumerate(sents):
            for w in sent.split():
                n = fa.normalize_word(w)
                if n:
                    units.append((n, (base, senti)))

    if article.get("title"):
        context(article["title"])
    if article.get("subtitle"):
        context(article["subtitle"])
    for si, section in enumerate(article.get("sections", [])):
        if section.get("title"):
            context(section["title"])
        for bi, block in enumerate(section.get("content", [])):
            kind = block.get("type")
            if kind in _BODY_TEXT_TYPES and block.get("text"):
                body(block["text"], ("blk", si, bi))
            elif kind == "bulletList":
                for ii, item in enumerate(block.get("items") or []):
                    body(item, ("item", si, bi, ii))
    return units, reg


def align_article(slug: str, article: dict) -> bool:
    m4a = ensure_audio(slug)
    if m4a is None:
        return False
    units, reg = collect_units(article)
    if not units:
        return False

    waveform, sr = fa.load_waveform(m4a)
    spans, total = fa.align_word_spans(waveform, sr, [n for n, _ in units])

    bounds: dict = {}
    for (_, tag), sp in zip(units, spans):
        if tag is None or sp is None:
            continue
        s, e = sp
        lo, hi = bounds.get(tag, (s, e))
        bounds[tag] = (min(lo, s), max(hi, e))

    def spans_for(base: tuple) -> list:
        out, last = [], 0.0
        for senti, sent in enumerate(reg.get(base, [])):
            sp = bounds.get((base, senti))
            s, e = sp if sp else (last, last)        # OOV-only sentence -> zero-width (skipped)
            s = max(s, last)
            e = max(e, s)
            out.append({"text": sent, "start": round(min(s, total), 2), "end": round(min(e, total), 2)})
            last = e
        return out

    n_sent = 0
    for si, section in enumerate(article.get("sections", [])):
        for bi, block in enumerate(section.get("content", [])):
            kind = block.get("type")
            if kind in _BODY_TEXT_TYPES and ("blk", si, bi) in reg:
                block["readAlong"] = spans_for(("blk", si, bi))
                n_sent += len(block["readAlong"])
            elif kind == "bulletList":
                items = block.get("items") or []
                block["itemsReadAlong"] = [spans_for(("item", si, bi, ii)) for ii in range(len(items))]
                n_sent += sum(len(x) for x in block["itemsReadAlong"])
    print(f"  {slug:32s} {n_sent:3d} sentences  ({total:.0f}s)")
    return True


def main():
    data = json.loads(JSON_PATH.read_text())
    aligned = skipped = 0
    for article in data["articles"]:
        slug = article["slug"]
        if only and only not in slug:
            continue
        if align_article(slug, article):
            aligned += 1
        else:
            skipped += 1
            print(f"  {slug:32s} skipped (no audio)")

    JSON_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    if _VENDORED != JSON_PATH and _VENDORED.exists():
        _VENDORED.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"(also updated vendored {_VENDORED.relative_to(BACKEND)})")
    print(f"\nwrote read-along into {JSON_PATH.name}: {aligned} articles aligned, {skipped} skipped")
    print("Next: ./venv/bin/python scripts/seed_money_moves.py   (re-seed articles with timings)")


if __name__ == "__main__":
    main()
