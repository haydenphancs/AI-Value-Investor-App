"""
Stage 1 of 4 — generate the cinematic macro ART plate for a book cover. PAID.

    generate_book_cover_art.py  ->  compose_book_cover.py  ->  seed_book_covers.py
                                ->  gen_book_covers_swift.py

Art ONLY: the prompt forbids text, and the title/author/tagline are drawn afterwards
as real type by stage 2. That split is deliberate — every image-model retry produces a
DIFFERENT picture, so baking the words in means you cannot fix a line break without
losing art you already approved. It also guarantees spelling and one type system
across the whole set.

Checkpointed on a hash of the prompt: an unchanged prompt makes NO API call, so
re-running is free. Change a subject and only that book regenerates.

Output: backend/data/book_covers/<order>_<slug>.art.jpg  (3:4, 2K, q95, ungraded)

Usage (from backend/):
    ./venv/bin/python scripts/generate_book_cover_art.py            # all, skips up-to-date
    ./venv/bin/python scripts/generate_book_cover_art.py 7          # one book
    ./venv/bin/python scripts/generate_book_cover_art.py 7 --force  # re-roll it
"""
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # backend/
OUT = ROOT / "data/book_covers"
OUT.mkdir(parents=True, exist_ok=True)

# --- key, same hand-rolled .env parse the audio pipeline uses -------------------
KEY = os.environ.get("GEMINI_API_KEY")
_env = ROOT / ".env"
if not KEY and _env.exists():
    for line in _env.read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
if not KEY and __name__ == "__main__":
    raise SystemExit("GEMINI_API_KEY not found in env or backend/.env")

MODEL = os.environ.get("COVER_MODEL", "gemini-3-pro-image")
THROTTLE = float(os.environ.get("COVER_THROTTLE", "5"))
MAX_ATTEMPTS = 4

# =============================================================================
# The approved recipe. Base prompt is the product owner's own wording; the three
# blocks after it are the corrections that came out of review.
# =============================================================================
BASE = """Cinematic macro-photography of {subject}. Dramatic moody lighting, deep shadows, high contrast, premium documentary style, photorealistic, 8k resolution.

THE OBJECT MUST BE IMMEDIATELY RECOGNISABLE. Frame it so its whole shape and identity read clearly at a glance — do not crop so tightly that it becomes an abstract texture or an unidentifiable piece of metal. The object must be correctly and plausibly formed, mechanically accurate, exactly as the real thing looks.

LIGHT: {light} Deep, genuinely black shadows across much of the frame — a low-key image. Specular highlights are small, hard and controlled. No flat even lighting, no softbox wash, no fill light.

LENS: shot macro on a fast prime with shallow depth of field — the object itself is critically sharp front to back on its key plane, while the background falls away completely. Fine natural grain, subtle lens vignette. No HDR, no over-sharpening, no plastic CGI look.

PALETTE: {palette} Rich but restrained — a graded film frame, not a colourful photograph.

COMPOSITION: vertical portrait 3:4 frame. The sharp, recognisable object sits in the UPPER 55% of the frame. THE BOTTOM 40% OF THE FRAME FALLS AWAY INTO NEAR-BLACK SHADOW AND DEFOCUS — plain, dark, empty and quiet, with no detail, no bright highlight and no object crossing into it.

ABSOLUTE REQUIREMENT: NO text, letters, numbers, digits, words, engraving, embossed lettering, serial numbers, dial numerals, brand names, logos, signage or watermarks ANYWHERE in the image. No human faces, no hands, no people. No books, book covers or bookshelves."""

# Two palettes in a balanced 5/5 split across the set: W C C W W C C W W C. NOT strict
# alternation — the invariant that matters is that the palette never runs flat for three
# books together, which is where a scroll starts to look monotonous. Colour is doing real
# work here: it is what separates ten dark covers from each other at speed.
# Pinned by tests/test_book_covers_parity.py::test_palette_is_balanced_and_never_runs_three_deep.
WARM = ("One warm tungsten key light low and to the right, raking across the surfaces so "
        "the metal glows along its edges and everything else falls into darkness.",
        "deep brass and old gold, warm amber, rich brown-black. Burnished and warm, "
        "never yellow or garish.")
COOL = ("One hard key light from the upper left grazing across the surfaces, picking out "
        "every machined edge and tooling mark.",
        "cold gunmetal, brushed steel, blue-grey and near-black, with one restrained "
        "steel-blue highlight. Almost no warmth.")

# order: (slug, palette, subject)
BOOKS = {
    1: ("rich-dad-poor-dad", WARM,
        "two neat stacks of gold coins standing on a dark polished wood desk, one stack clearly "
        "taller than the other, with a single brass house key lying flat in front of them, its bow "
        "and bit plainly visible. BOTH COIN STACKS ARE COMPLETE AND FULLY INSIDE THE FRAME with "
        "clear space above them — nothing is cropped by the top edge. Shot slightly above coin "
        "level so the milled edges and the round faces of the coins both read, and the key is sharp"),
    2: ("the-intelligent-investor", COOL,
        "a heavy circular steel bank vault door, seen three-quarters open from the front so the "
        "whole round door reads clearly — the spoked handwheel at its centre, the ring of thick "
        "cylindrical locking bolts thrown out around its edge, and the polished steel face. It must "
        "be instantly recognisable as a bank vault door, not a close abstract detail of metal"),
    3: ("the-psychology-of-money", COOL,
        "a single gold coin standing balanced upright on its edge on a dark polished stone surface, "
        "casting a long dramatic shadow away from the light. The coin is clearly a coin, sharp and "
        "complete, alone in the frame"),
    4: ("one-up-on-wall-street", WARM,
        "an antique brass magnifying glass with a turned wooden handle resting on a dark desk, its "
        "round lens clearly visible and catching the light, with a softly defocused rising line "
        "chart glowing far behind it. The magnifying glass is complete and unmistakable"),
    5: ("common-stocks-and-uncommon-profits", WARM,
        "a small green seedling with two fresh leaves growing up out of a heap of gold coins in "
        "dark rich soil. Both the seedling and the individual coins are clearly readable"),
    6: ("the-little-book-of-common-sense-investing", COOL,
        "an antique brass ship's compass with its glass dome and floating card, sitting on dark "
        "slate. The whole instrument is in frame and obviously a compass, its needle steady"),
    7: ("a-random-walk-down-wall-street", COOL,
        "a wide shallow woven basket sitting on dark slate, brimming right to the rim with many "
        "identical silver coins heaped together — hundreds of them, the whole market held in one "
        "basket rather than a single pick. The basket and the individual coins are both clearly "
        "readable, and a few loose coins rest on the slate beside it"),
    8: ("the-essays-of-warren-buffett", WARM,
        "a classic black and gold fountain pen, cap off and its polished gold nib clearly exposed, "
        "lying at a slight angle across a dark leather desk. THE WHOLE PEN IS SHARP AND IN FOCUS "
        "from nib to barrel end and is the unmistakable subject of the photograph, occupying a good "
        "part of the frame. A short stack of gold coins sits well behind it, small and softly "
        "defocused. Do not blur the pen"),
    9: ("the-little-book-that-still-beats-the-market", WARM,
        "the round brass keys of a vintage mechanical adding machine, seen at an angle so the "
        "banks of keys and the machine's body clearly read as an old calculating machine, warm "
        "brass and black enamel, worn with use"),
    10: ("the-most-important-thing", COOL,
         "the polished brass pendulum bob and rod of an antique longcase clock, hanging in the dark "
         "interior of the clock case, the disc of the bob catching the light. It is clearly a clock "
         "pendulum, whole and recognisable"),
}


def prompt_for(order: int) -> str:
    _slug, (light, palette), subject = BOOKS[order]
    return BASE.format(subject=subject, light=light, palette=palette)


def manifest_path(order: int) -> Path:
    return OUT / f"{order}_{BOOKS[order][0]}.manifest.json"


def read_manifest(order: int) -> dict:
    p = manifest_path(order)
    return json.loads(p.read_text()) if p.exists() else {}


def write_manifest(order: int, data: dict) -> None:
    manifest_path(order).write_text(json.dumps(data, indent=2) + "\n")


def generate(order: int, force: bool = False) -> Path:
    from google import genai
    from google.genai import types
    from PIL import Image

    slug = BOOKS[order][0]
    art = OUT / f"{order}_{slug}.art.jpg"
    prompt = prompt_for(order)
    sha = hashlib.sha1(prompt.encode()).hexdigest()[:12]
    man = read_manifest(order)

    if art.exists() and man.get("art_prompt_sha1") == sha and not force:
        print(f"  {order:2d} {slug:44s} [up to date — no API call]")
        return art

    client = genai.Client(api_key=KEY)
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = client.models.generate_content(
                model=MODEL, contents=prompt,
                # NOTE: person_generation is Vertex-only; the Gemini API rejects it.
                # People are excluded by the prompt, not by a parameter.
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="3:4", image_size="2K"),
                ),
            )
        except ValueError as e:
            raise RuntimeError(f"book {order} ({slug}): bad request config: {e}") from e
        except Exception as e:  # noqa: BLE001
            last = e
            wait = min(8 * attempt, 60)
            print(f"  {order:2d} {slug:44s} ! {type(e).__name__}: {str(e)[:90]} — retry {wait}s")
            time.sleep(wait)
            continue

        cands = resp.candidates or []
        if not cands:
            raise RuntimeError(f"book {order} ({slug}): no candidates. "
                               f"prompt_feedback={getattr(resp, 'prompt_feedback', None)}")
        cand = cands[0]
        parts = (cand.content.parts if cand.content else None) or []
        # Walk for inline_data — never index parts[0]; a refusal comes back as a text part.
        blob = next((p.inline_data.data for p in parts
                     if getattr(p, "inline_data", None) and p.inline_data.data), None)
        if blob is None:
            txt = " ".join((p.text or "") for p in parts if getattr(p, "text", None))
            raise RuntimeError(
                f"book {order} ({slug}): model returned no image. "
                f"finish_reason={cand.finish_reason} safety={cand.safety_ratings} "
                f"text={txt[:300]!r}")

        img = Image.open(io.BytesIO(blob))
        img.load()
        tmp = OUT / f".tmp_{order}.part"
        img.convert("RGB").save(tmp, "JPEG", quality=95, subsampling=0, optimize=True)
        os.replace(tmp, art)                      # atomic: a Ctrl-C cannot leave a half file
        with Image.open(art) as c:
            c.verify()                            # headers
        with Image.open(art) as c:
            c.load()                              # and the body — verify() alone passes on a truncation
            w, h = c.size

        man.update(curriculum_order=order, slug=slug, art_file=art.name,
                   art_model=MODEL, art_aspect="3:4", art_size="2K",
                   art_prompt=prompt, art_prompt_sha1=sha,
                   art_native_width=w, art_native_height=h)
        write_manifest(order, man)
        print(f"  {order:2d} {slug:44s} + {w}x{h}  {art.stat().st_size // 1024} KB")
        return art

    raise RuntimeError(f"book {order} ({slug}): exhausted {MAX_ATTEMPTS} attempts: "
                       f"{type(last).__name__}: {last}")


def main():
    force = "--force" in sys.argv
    only = [int(a) for a in sys.argv[1:] if a.isdigit()]
    orders = only or sorted(BOOKS)
    print(f"Cover art — {len(orders)} book(s) · {MODEL} · 3:4 · 2K · art only\n")
    failed = []
    for i, o in enumerate(orders):
        try:
            generate(o, force)
        except Exception as e:  # noqa: BLE001
            print(f"  {o:2d} FAILED {type(e).__name__}: {str(e)[:300]}")
            failed.append(o)
        if i < len(orders) - 1:
            time.sleep(THROTTLE)
    print(f"\n{len(orders) - len(failed)}/{len(orders)} plate(s) in {OUT}")
    if failed:
        print("failed:", failed)
        sys.exit(1)
    print("Next: ./venv/bin/python scripts/compose_book_cover.py")


if __name__ == "__main__":
    main()
