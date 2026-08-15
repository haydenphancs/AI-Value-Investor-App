"""
Stage 1 of 2 — generate the editorial cover art for each Money Moves article. PAID.

    generate_money_moves_art.py  ->  seed_money_moves.py (uploads + bakes the URLs)

THE SYSTEM, in one line: ONE metaphor object, isolated like an exhibit, on a deliberately
DESIGNED GROUND, in a limited palette.

The variable that carries the range is the GROUND TREATMENT — graph paper, a flat colour
field, seamless white, a specimen plate, aged ledger stock, a physical print lying in a
scene — not the lighting. Eleven treatments live in TREATMENTS below and each article is
assigned the one its story actually wants: an accounting fraud gets ledger paper, a failed
blood-testing startup gets a specimen plate, a rivalry gets both halves knolled side by
side. Vary the treatment, hold the system: one object, real margin, restrained palette.

  Style varies per article. The SYSTEM does not.

WHY THE ART CARRIES NO TEXT. Every re-roll of an image prompt produces a DIFFERENT picture,
so a headline baked into the plate means you cannot fix one line break without losing art
you already approved — and the model cannot spell reliably at any size. Type is set
afterwards in real fonts (the compose_book_cover.py split). Where a reference shows a
caption card or a data label, the plate renders it BLANK and the words go on top later.

WHY THERE IS NO DARK TYPE BAND ANY MORE. The art is a CONTAINED rounded card with the
headline below it, not a full-bleed hero with white type over it. That is what lets a
treatment use a white, olive or pastel ground at all. It also means the only tone guard
worth having is the opposite one: an image whose edges are near-white needs the light-mode
hairline (AppColors.cardEdge) or it dissolves into the #F4F5F8 page.

Checkpointed on a hash of the prompt: an unchanged prompt makes NO API call, so re-running
is free. Change one subject and only that article regenerates. NOTE that the treatment
block and the palette are part of the hashed prompt — editing either deliberately re-rolls
every article it touches, at full cost.

Output (full run), under backend/data/money_moves_art/:
    <slug>.art.jpg          16:9 2K master, q95
    <slug>.hero.jpg         1206x678  the contained article hero card + See-All featured
    <slug>.card.jpg          640x360  the 200pt catalog tile + related-article tiles
    <slug>.manifest.json
    <slug>.subject.txt      only for --auto-written subjects (COMMIT THESE — see below)

Usage (from backend/):
    ./venv/bin/python scripts/generate_money_moves_art.py                # all, skips up-to-date
    ./venv/bin/python scripts/generate_money_moves_art.py --only the-fall-of-enron [--force]
    ./venv/bin/python scripts/generate_money_moves_art.py --category valueTraps
    ./venv/bin/python scripts/generate_money_moves_art.py --compare the-fall-of-enron
    ./venv/bin/python scripts/generate_money_moves_art.py --auto        # write missing subjects
    ./venv/bin/python scripts/generate_money_moves_art.py --recheck     # FREE, remeasure only
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # backend/
REPO = ROOT.parent
OUT = ROOT / "data/money_moves_art"
CMP_OUT = OUT / "_compare"

# Frontend tree is the source of truth locally; on Railway only backend/ ships, so fall back
# to the vendored copy. Same resolution seed_money_moves.py uses, deliberately.
_FRONTEND_JSON = REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
ARTICLES_JSON = _FRONTEND_JSON if _FRONTEND_JSON.exists() else ROOT / "data/money_moves.json"

# --- key, same hand-rolled .env parse the rest of the content pipeline uses -----
KEY = os.environ.get("GEMINI_API_KEY")
_env = ROOT / ".env"
if not KEY and _env.exists():
    for line in _env.read_text().splitlines():
        if line.startswith("GEMINI_API_KEY="):
            KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
if not KEY and __name__ == "__main__":
    raise SystemExit("GEMINI_API_KEY not found in env or backend/.env")

MODEL = os.environ.get("MM_ART_MODEL", "gemini-3-pro-image")
SUBJECT_MODEL = os.environ.get("MM_SUBJECT_MODEL", "gemini-2.5-flash")
THROTTLE = float(os.environ.get("MM_ART_THROTTLE", "4"))
MAX_ATTEMPTS = 4

ART_ASPECT = "16:9"         # native — the hero is a contained 16:9 card, so nothing is cropped
ART_SIZE = "2K"

# name -> (width, height). Both 16:9, so a derivative is a clean downscale of the master and
# never a crop. Always resampled FROM THE MASTER, never from another derivative: a second
# resample of an already-resampled frame softens detail and shimmers during scroll
# (compose_book_cover.py:10-13).
#   hero  1206x678  the 402pt-wide article card @3x, and the See-All featured card
#   card   640x360  the 200pt catalog tile and the related-article tiles. A separate file so
#                   a 13-card scroll row does not pull 1206px images for a 600px slot.
DERIVATIVES = {"hero": (1206, 678), "card": (640, 360)}
DERIVATIVE_QUALITY = 84

# --- guards ------------------------------------------------------------------------
# The subject must sit inside a real margin: these are contained cards, and an object
# jammed against the rounded corner reads as a mistake rather than a crop.
MARGIN = 0.04
# A plate whose border is this bright needs the light-mode hairline or it dissolves into
# the #F4F5F8 page. Not a failure — a rendering instruction, reported per image.
LIGHT_GROUND_LUMA = 0.55

# =============================================================================
# TREATMENTS — the ground, the medium and the register. This is the range.
# Each block is dropped into BASE whole; it owns the background, the light and the
# rendering medium, because those three move together and splitting them produced
# prompts that argued with themselves.
# =============================================================================
TREATMENTS = {
    # --- bright, graphic, editorial -----------------------------------------
    "duotone_press": (
        "Bold two-colour duotone editorial poster",
        "A photographic subject flattened into a hard two-colour DUOTONE screenprint and laid "
        "over a flat saturated background field, with a fine regular grid of hairlines ruled "
        "across the whole frame like graph paper. The object is a real photograph, not a "
        "drawing, but reduced to exactly two inks — one saturated field colour and one cool "
        "pale ink — with crisp edges, high contrast and no intermediate hues at all. Slight "
        "offset-print misregistration and a faint paper tooth. Confident, loud and modern: a "
        "financial magazine cover, not a photograph."
    ),
    "specimen_plate": (
        "Museum specimen, pinned and catalogued",
        "The object presented as a MUSEUM SPECIMEN, laid flat and perfectly centred on a "
        "soft off-white cotton-rag paper ground with a faint fibrous texture and one gentle "
        "fold crease. Flat, even, shadowless top light exactly as a collections photographer "
        "would use, with one very soft contact shadow directly beneath the object. Directly "
        "below the object sits one small COMPLETELY BLANK cream card with a thin printed rule "
        "across it and no writing on it whatsoever. Clinical, immaculate, reverent — the "
        "object is evidence, catalogued after the fact."
    ),
    "colour_field": (
        "One object on concentric colour fields",
        "One real photographed object resting exactly at the centre of a set of CONCENTRIC "
        "RECTANGLES of flat unmodulated colour, each band a different hue, nested inside one "
        "another and filling the whole frame edge to edge. A single sheet of heavy paper "
        "texture with one soft vertical fold crease runs across the entire image, colour "
        "bands and object alike, so the whole thing reads as one printed page. Flat even "
        "light, minimal shadow. Calm, graphic and abstract — a Rothko with one honest thing "
        "sitting on it."
    ),
    "cutout_grid": (
        "Cutout on bright seamless white",
        "The object photographed against pure bright seamless white and treated as a crisp "
        "CUTOUT, with a faint pale-blue engineering grid of hairlines ruled behind it and a "
        "soft realistic drop shadow anchoring it to the ground. Clean daylight-balanced "
        "studio light, full colour, everything sharp. A few small related props scatter "
        "loosely around it at the edges of the composition. Bright, weightless and current — "
        "a news explainer, not a mood piece."
    ),
    "flat_deco": (
        "Flat vector, warm and confident",
        "A flat 2D VECTOR ILLUSTRATION of the object, drawn with clean confident linework, "
        "solid unmodulated colour fills and simple geometric decorative shapes behind it — "
        "soft rounded clouds, arcs or bands filling the background. A limited warm palette of "
        "four or five colours, gently retro, with fine engraved-looking detail lines on the "
        "object itself. Absolutely flat: no photographic texture, no 3D rendering, no bevels, "
        "no gradients, no drop shadows. Warm, approachable and designed."
    ),
    "exploded_kit": (
        "Knolling — every part laid out",
        "The object completely DISASSEMBLED and every single component laid out flat and "
        "photographed from directly overhead, arranged in strict knolling order: parts "
        "aligned to an invisible grid, all edges parallel or at right angles, evenly spaced, "
        "grouped by size, nothing overlapping. A plain matte mid-tone surface beneath, even "
        "shadowless overhead light. Meticulous, obsessive, catalogue-like — the anatomy of "
        "the thing, shown as parts."
    ),
    "blueprint_cyanotype": (
        "White linework on Prussian blue",
        "A CYANOTYPE technical drawing: the object rendered entirely as fine white engineering "
        "linework on a deep Prussian-blue ground, drawn as a precise orthographic exploded "
        "assembly diagram with thin leader lines, section hatching, centre lines and arcs. The "
        "blue ground carries the mottled uneven wash and slight paper grain of a real "
        "sun-exposed blueprint, with soft edges where the coating thinned. Leader lines end in "
        "plain dots — no numbers, no dimensions, no annotations of any kind."
    ),

    # --- warm, archival, physical --------------------------------------------
    "ledger_still": (
        "Still life on aged accounting stock",
        "The object resting on a sheet of aged green-bar ACCOUNTING LEDGER PAPER — pale green "
        "and cream alternating horizontal bands with fine ruled column lines — foxed and "
        "yellowed at the edges, on a dark wooden desk. Warm low tungsten light from one side, "
        "deep but not black shadows, visible dust and paper fibre. The ruled bands and columns "
        "are empty: no figures, no writing, no annotations anywhere. Archival, forensic and "
        "quietly damning — the record, long after the fact."
    ),
    "found_artefact": (
        "A physical print, lying in its own scene",
        "A single physical instant PHOTOGRAPH — a square print with a thick white border and "
        "a wide blank strip along its bottom edge — lying at a slight angle on a surface that "
        "belongs to the subject's own world. The print itself shows the object, sharply and "
        "unmistakably. The surface it lies on continues the same subject beyond the print's "
        "edges, so the photograph sits inside the scene it depicts. Shallow depth of field: "
        "the print is critically sharp, the surroundings fall away. The white bottom strip is "
        "COMPLETELY BLANK — no handwriting, no printing, nothing on it at all."
    ),

    # --- low-key, dramatic (kept from the first round) ------------------------
    "macro_noir": (
        "Low-key documentary macro",
        "Straight documentary macro photography. A single hard key light rakes across the "
        "subject from one side and everything outside its throw falls to genuine black — no "
        "fill, no bounce, no softbox wash. Shot on a fast macro prime with shallow depth of "
        "field. Real physical materials with visible microscopic texture — machined metal, "
        "oxidation, wear, dust suspended in the beam. Fine natural film grain, subtle lens "
        "vignette, slight halation on the brightest speculars. Restrained and near-monochrome. "
        "No digital gloss, no HDR, no plastic CGI look."
    ),
    "tech_noir": (
        "Designed light, carved out of black",
        "Cinematic tech-noir still. The subject is carved out of darkness by a strong coloured "
        "rim light with a second opposing kicker on the far side, faint atmospheric haze "
        "holding visible shafts of that light, and a wet black glass surface beneath throwing "
        "a short soft mirrored reflection. Gentle anamorphic bloom on the brightest specular "
        "edges, deep crushed blacks. The saturated colour lives entirely in the LIGHT — every "
        "surface and material stays neutral and unpainted. Photoreal and razor sharp."
    ),
}

# =============================================================================
# PALETTE — colour still carries the CATEGORY, so a scrolling list of eleven different
# treatments is navigable rather than a jumble. Deliberately written as a colour BRIEF
# rather than a lighting recipe, because each treatment realises it differently: the same
# "cold steel-blue" is a duotone ink, a cyanotype ground, and a rim light.
# =============================================================================
PALETTES = {
    "blueprints": (
        "cold steel-blue and pale cyan against deep slate and near-black, with clean bone "
        "white. One warm accent at most, and only if the object needs it. Precise and "
        "engineered."
    ),
    "valueTraps": (
        "oxblood, rust and burnished copper-red against warm charcoal and aged cream. Rich, "
        "dark and restrained — never bright, never scarlet, never a pure saturated red."
    ),
    "battles": (
        "cold steel-blue on one side against warm amber and old gold on the other, over a "
        "neutral ground. Exactly two hues plus neutrals, split so the two halves of the "
        "picture are plainly opposed — the rivalry is carried by the colour."
    ),
}

# =============================================================================
BASE = """{treatment}

SUBJECT: {subject}

THE OBJECT MUST BE IMMEDIATELY RECOGNISABLE. Frame it so its whole shape and identity read clearly at a glance — never cropped so tightly that it becomes an abstract texture or an unidentifiable piece of material. It must be correctly and plausibly formed, mechanically accurate, exactly as the real thing looks.

PALETTE: {palette} Use three or four colours at most plus neutrals — restrained and harmonious, never rainbow.

COMPOSITION: horizontal landscape 16:9 frame. ONE single clear idea, calm and uncluttered, never a busy scene. The subject is complete and entirely inside the frame with a clear unbroken margin on all four sides — nothing touches or is cut by any edge, because this image is shown as a rounded card and anything against an edge reads as a mistake. The background treatment, however, runs fully edge to edge into all four corners with no border, no frame, no vignette and no second card drawn inside the picture.

ABSOLUTE REQUIREMENT: NO text, letters, numbers, digits, words, handwriting, engraving, embossed lettering, serial numbers, dial numerals, dimension labels, captions, brand names, logos, trademarks, signage or watermarks ANYWHERE in the image — every card, label, plaque, sheet, screen and printed surface in the picture is completely blank. No human faces, no hands, no people. No real company's product, packaging or identifying mark."""

# =============================================================================
# ARTICLES — slug -> (category, treatment, subject). Keyed on the AUTHORED slug from
# money_moves.json, which is the same key seed_money_moves.py feeds to uuid5 for the row
# id, so the Storage path and the database identity can never drift apart.
#
# The treatment is chosen for the STORY, never rotated for variety's sake: Enron gets
# ledger paper because it is an accounting fraud, Theranos gets a specimen plate because
# it is a post-mortem, Tesla-vs-legacy gets knolling because the whole argument is that
# the two machines have different anatomies. Balance is a tie-breaker, not the rule —
# pinned by tests/test_money_moves_art_parity.py::test_treatment_spread.
#
# HOW TO WRITE A SUBJECT — the rule the whole set lives or dies by:
#
#   The object must be THE TOPIC'S OWN THING, not a generic symbol of success or failure.
#   Ask what physical object this story actually contains before asking what would look
#   good. A picture that could sit on any other article in the same section is the wrong
#   picture — which is exactly how two meshed gears ended up standing in for a card-network
#   duopoly, and why they were replaced with two blank payment cards.
#
#   BATTLES has a structural form on top of that: TWO IDENTICAL objects of the same kind,
#   the product both rivals actually sell, told apart only by which side's light falls on
#   them. Coke versus Pepsi is two unbranded cans. One object, however good, cannot say
#   "versus". Pinned by tests/test_money_moves_art_parity.py::test_battles_subjects_are_two_up.
#
#   Generic, never branded. These articles are about Amazon, Apple, Tesla and Visa, and
#   about Enron, Theranos and FTX — a real logo or a recognisable person in the frame is a
#   trademark/likeness problem, not a style problem. Describe the product CATEGORY and say
#   in the subject itself that it is blank and unmarked.
#
# A slug missing from this table is legal: --auto writes one and caches it to
# <slug>.subject.txt. An ORPHAN key here is not — it generates art nothing points at.
# =============================================================================
ARTICLES = {
    # ---- BLUEPRINTS ---------------------------------------------------------
    # An argument about where money goes next has no object of its own, which is exactly
    # the case the colour-field treatment was built for: the bands carry the idea and the
    # one honest thing in the middle keeps it from being decoration.
    "the-future-of-digital-finance": ("blueprints", "colour_field",
        "a single thick machined coin standing upright on its edge, dead centre, its face "
        "covered in a fine purely geometric lattice of connected nodes and hairline traces "
        "with no symbols or characters of any kind"),
    # The flywheel is the article's own word for the strategy. Duotone because the point is
    # that it is still turning.
    "how-amazon-built-its-moat": ("blueprints", "duotone_press",
        "a heavy cast-iron industrial flywheel on a machined shaft, seen three-quarters on so "
        "the full circle of the rim, the spokes and the central hub all read clearly, caught "
        "mid-rotation with a few clean motion arcs trailing from the rim"),
    # Cigar-butt investing, the actual method of the partnership years. A person is
    # forbidden, so the artefact has to carry the era on its own — hence the print.
    "warren-buffetts-early-days": ("blueprints", "found_artefact",
        "a worn cigar butt resting in a heavy cut-crystal ashtray, one last ember alive at "
        "its tip, lying on a dark wooden desk scattered with more spent cigar butts"),
    # "THE PIVOT" is the article's own tag. A hinge caught half open IS a pivot, and the
    # cyanotype says the pivot was engineered rather than lucky.
    "apples-services-revolution": ("blueprints", "blueprint_cyanotype",
        "a precision machined hinge caught exactly half open, drawn as an exploded assembly "
        "with its two leaves, the interleaved knuckles and the pivot pin separated along one "
        "axis so the whole mechanism is legible"),
    # A membership business is a gate you choose to pay for. Flat vector keeps it warm
    # rather than sinister, which is the difference between Costco and a turnstile.
    "costcos-membership-magic": ("blueprints", "flat_deco",
        "a chrome three-armed turnstile rotor on its polished central post, seen from a "
        "slight angle, with soft rounded geometric shapes and arcs filling the space behind it"),

    # ---- VALUE TRAPS --------------------------------------------------------
    # Smoke and mirrors, on the ledger it was hidden in. The two halves of the story in one
    # picture, and the reason this is the strongest pairing in the set.
    "the-fall-of-enron": ("valueTraps", "ledger_still",
        "a large antique mirror in a heavy tarnished metal frame lying face up, its glass "
        "cracked, a fine web of fractures radiating outward from one single impact point near "
        "its centre"),
    "weworks-unraveling": ("valueTraps", "duotone_press",
        "a thick hemp rope under visible tension, its outer strands unravelling and springing "
        "loose in a burst of frayed fibres at the point where it is about to part"),
    # Low-key is right exactly once in this set, and this is it: the story is that the room
    # was empty and nobody looked.
    "the-ftx-collapse": ("valueTraps", "macro_noir",
        "a heavy steel safe door standing wide open on a dark vault, its thick cylindrical "
        "locking bolts thrown out around the edge and the spoked handwheel clearly visible, "
        "the interior behind it completely empty and black"),
    # A post-mortem, catalogued. The blank specimen card is where the real ticker label gets
    # composited later.
    "theranos-blood-and-lies": ("valueTraps", "specimen_plate",
        "a single small glass laboratory vial lying flat and perfectly horizontal, completely "
        "unlabelled and unmarked, with one dark red droplet pooled beside its open mouth"),

    # ---- BATTLES ------------------------------------------------------------
    "netflix-vs-disney-plus": ("battles", "cutout_grid",
        "two identical metal film reels standing upright on their edges, squarely facing each "
        "other a short distance apart, the spokes and the wound film of both clearly readable, "
        "with a scatter of loose film frames on the ground around them"),
    # The whole argument is that the two machines have different anatomies, so show the
    # anatomies. Knolling is the only treatment that can hold two part-counts at once.
    "tesla-vs-traditional-auto": ("battles", "exploded_kit",
        "the components of an electric drive unit laid out on the left half and the components "
        "of a petrol engine laid out on the right half, both fully disassembled, the left group "
        "obviously far fewer and simpler parts than the dense crowded right group"),
    # Two gears were the first attempt and broke the Battles rule: a gear has nothing to do
    # with payments, so the picture was a generic metaphor rather than the topic's own
    # object. Two cards is the "two cans of drink" answer — same product, same size, told
    # apart only by which side's light falls on them.
    "visa-vs-mastercard": ("battles", "flat_deco",
        "two identical bank payment cards of exactly the same size standing upright side by "
        "side and facing the viewer, each with a small square contact chip on its face, both "
        "completely blank with no printing, embossing, numbers, names or marks of any kind"),
    "google-vs-microsoft-ai-wars": ("battles", "tech_noir",
        "two dense server-grade aluminium heatsinks standing face to face, their fin stacks "
        "almost interleaving, both complete and clearly readable as heatsinks"),
}

# --compare renders one article across several treatments, so the "how much variation do we
# want" question gets answered by looking rather than arguing.
COMPARE_TREATMENTS = ["ledger_still", "specimen_plate", "duotone_press", "colour_field",
                      "cutout_grid"]


# --------------------------------------------------------------------------
# Subject resolution: hand-authored > cached --auto > nothing.
# --------------------------------------------------------------------------
def subject_path(slug: str) -> Path:
    return OUT / f"{slug}.subject.txt"


def _load_articles() -> list[dict]:
    return json.loads(ARTICLES_JSON.read_text())["articles"]


def article_meta() -> dict[str, dict]:
    return {a["slug"]: a for a in _load_articles()}


def default_treatment(slug: str, category: str) -> str:
    """The treatment for an article nobody has assigned one to.

    Deterministic on the slug, NOT random: a random pick would change on every run, change
    the prompt hash with it, and re-roll the whole set. Restricted to the treatments that
    survive an unknown subject — knolling needs something with parts, the cyanotype needs
    something mechanical, and neither degrades gracefully on a guess.
    """
    safe = {"blueprints": ["duotone_press", "cutout_grid", "colour_field", "flat_deco"],
            "valueTraps": ["specimen_plate", "ledger_still", "macro_noir", "duotone_press"],
            "battles": ["cutout_grid", "duotone_press", "flat_deco", "tech_noir"]}[category]
    n = int(hashlib.sha1(slug.encode()).hexdigest()[:8], 16)
    return safe[n % len(safe)]


def resolve(slug: str) -> tuple[str, str, str] | None:
    """(category, treatment, subject), or None when nothing has been authored yet.

    A hand-authored ARTICLES entry always wins: --auto is a floor, not an override, so
    editing the table is how you correct a picture you do not like.
    """
    if slug in ARTICLES:
        return ARTICLES[slug]
    meta = article_meta().get(slug)
    if meta is None:
        return None
    cached = subject_path(slug)
    if cached.exists() and cached.read_text().strip():
        cat = meta.get("category", "blueprints")
        if cat not in PALETTES:
            cat = "blueprints"
        return cat, default_treatment(slug, cat), cached.read_text().strip()
    return None


def _check_article_table() -> None:
    """An orphan key generates art nothing points at, and a slug typo is invisible
    otherwise. The reverse (an article with no entry) is deliberately NOT an error —
    that is exactly the case --auto exists to fill."""
    authored = {a["slug"] for a in _load_articles()}
    orphan = set(ARTICLES) - authored
    if orphan:
        raise SystemExit(f"ARTICLES has key(s) not in {ARTICLES_JSON.name}: {sorted(orphan)}")
    bad_cat = {s: c for s, (c, _t, _sub) in ARTICLES.items() if c not in PALETTES}
    if bad_cat:
        raise SystemExit(f"unknown category on {bad_cat} — expected {sorted(PALETTES)}")
    bad_tr = {s: t for s, (_c, t, _sub) in ARTICLES.items() if t not in TREATMENTS}
    if bad_tr:
        raise SystemExit(f"unknown treatment on {bad_tr} — expected {sorted(TREATMENTS)}")
    # A picture lit and coloured for one section but filed under another is a wayfinding
    # bug that nobody notices until the list looks wrong.
    meta = article_meta()
    mismatched = {s: (c, meta[s].get("category")) for s, (c, _t, _sub) in ARTICLES.items()
                  if meta[s].get("category") != c}
    if mismatched:
        raise SystemExit(f"category disagrees with money_moves.json: {mismatched}")


def prompt_for(subject: str, category: str, treatment: str) -> str:
    return BASE.format(treatment=TREATMENTS[treatment][1], subject=subject,
                       palette=PALETTES[category])


def prompt_for_slug(slug: str, treatment: str = None) -> str:
    resolved = resolve(slug)
    if resolved is None:
        raise SystemExit(
            f"no subject for '{slug}'. Add an ARTICLES entry, or run with --auto to have "
            f"one written and cached to {subject_path(slug).name}.")
    category, tr, subject = resolved
    return prompt_for(subject, category, treatment or tr)


# --------------------------------------------------------------------------
# Manifests
# --------------------------------------------------------------------------
def manifest_path(slug: str) -> Path:
    return OUT / f"{slug}.manifest.json"


def read_manifest(slug: str) -> dict:
    p = manifest_path(slug)
    return json.loads(p.read_text()) if p.exists() else {}


def write_manifest(slug: str, data: dict) -> None:
    manifest_path(slug).write_text(json.dumps(data, indent=2) + "\n")


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------
def _luma(a):
    """Per-pixel WCAG relative luminance for an HxWx3 uint8 array."""
    import numpy as np

    s = a.astype(np.float32) / 255.0
    lin = np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)
    return lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722


def plate_report(master: Path) -> dict:
    """One actionable flag, one reported number, and deliberately nothing else.

    `needs_hairline` IS actionable. A plate with a near-white ground dissolves into the
    #F4F5F8 light-mode page unless iOS draws AppColors.cardEdge around the card — the same
    lesson LessonImageSlot's plate exists for. Well defined, and it tells the renderer
    something it cannot work out for itself.

    `edge_activity` is REPORTED, NOT JUDGED, and the history is the reason. It first shipped
    as a pass/fail `margin_clear` measuring how much the border ring deviated from its own
    median — and it failed 8 of the first 13 plates, every one of them fine. The metric was
    reading the GROUND, not the subject: a designed ground is non-uniform by definition
    (graph-paper rules, a split blue/amber field, concentric colour bands all deviate wildly
    at the border) and the treatments are the whole point of the system.

    So it now compares the border's variation against the INTERIOR's — a ratio near 1 means
    the edge is as busy as the middle, i.e. the composition genuinely runs off the frame;
    well under 1 means the picture is contained. Nothing downstream crops these plates (the
    master is native 16:9 and both derivatives are pure downscales), so "does this read as
    cut off" is a human judgement and is left as one. A guard that cries wolf on two thirds
    of the set is worse than no guard, because the next real failure gets ignored with it.
    """
    import numpy as np
    from PIL import Image

    with Image.open(master) as im:
        a = np.asarray(im.convert("RGB"), dtype=np.uint8)
    y = _luma(a)
    h, w = y.shape
    r = max(2, int(round(min(h, w) * MARGIN)))

    def spread(v):
        return float(np.percentile(np.abs(v - np.median(v)), 95))

    ring = np.concatenate([y[:r, :].ravel(), y[-r:, :].ravel(),
                           y[:, :r].ravel(), y[:, -r:].ravel()])
    inner = y[r:-r, r:-r]
    ring_spread, inner_spread = spread(ring), spread(inner.ravel())
    ratio = ring_spread / inner_spread if inner_spread > 1e-6 else 0.0
    ring_med = float(np.median(ring))

    return {
        "margin_px_fraction": MARGIN,
        "ring_median_luma": round(ring_med, 4),
        "ring_spread": round(ring_spread, 4),
        "inner_spread": round(inner_spread, 4),
        "edge_activity": round(ratio, 3),        # reported, not judged — see docstring
        "mean_luma": round(float(y.mean()), 4),
        "inner_mean_luma": round(float(inner.mean()), 4),
        "needs_hairline": bool(ring_med >= LIGHT_GROUND_LUMA),
    }


# --------------------------------------------------------------------------
# Image IO
# --------------------------------------------------------------------------
def _derive(master: Path, dest: Path, out_w: int, out_h: int) -> dict:
    """Downscale the master. Both derivatives share the master's 16:9, so this only crops
    if the model returned something off-aspect — which it does by a pixel or two."""
    from PIL import Image

    with Image.open(master) as im:
        im = im.convert("RGB")
        w, h = im.size
        target = out_w / out_h
        cropped = False
        if abs(w / h - target) > 0.002:
            cropped = True
            if w / h > target:
                nw = int(round(h * target))
                im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
            else:
                nh = int(round(w / target))
                im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
        im = im.resize((out_w, out_h), Image.LANCZOS)
        tmp = dest.with_suffix(".part")
        im.save(tmp, "JPEG", quality=DERIVATIVE_QUALITY, optimize=True, progressive=True)
        os.replace(tmp, dest)

    data = dest.read_bytes()
    return {"file": dest.name, "width": out_w, "height": out_h,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "cropped": cropped}


def _derive_all(master: Path, stem: Path) -> dict:
    return {name: _derive(master, stem.with_name(f"{stem.name}.{name}.jpg"), *spec)
            for name, spec in DERIVATIVES.items()}


def _write_master(blob: bytes, dest: Path) -> tuple[int, int]:
    from PIL import Image

    img = Image.open(io.BytesIO(blob))
    img.load()
    tmp = dest.with_suffix(".part")
    img.convert("RGB").save(tmp, "JPEG", quality=95, subsampling=0, optimize=True)
    os.replace(tmp, dest)                       # atomic: a Ctrl-C cannot leave a half file
    with Image.open(dest) as c:
        c.verify()                              # headers
    with Image.open(dest) as c:
        c.load()                                # and the body — verify() passes on truncation
        return c.size


def _call_model(prompt: str, label: str) -> bytes:
    """One image, with the book-cover retry semantics: retry transient empties and
    transport errors, fail fast on a genuine refusal (retrying one only burns quota)."""
    from google import genai
    from google.genai import types

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
                    image_config=types.ImageConfig(aspect_ratio=ART_ASPECT,
                                                   image_size=ART_SIZE),
                ),
            )
        except ValueError as e:
            raise RuntimeError(f"{label}: bad request config: {e}") from e
        except Exception as e:  # noqa: BLE001
            last = e
            wait = min(8 * attempt, 60)
            print(f"     ! {type(e).__name__}: {str(e)[:90]} — retry {wait}s")
            time.sleep(wait)
            continue

        cands = resp.candidates or []
        if not cands:
            raise RuntimeError(f"{label}: no candidates. "
                               f"prompt_feedback={getattr(resp, 'prompt_feedback', None)}")
        cand = cands[0]
        parts = (cand.content.parts if cand.content else None) or []
        # Walk for inline_data — never index parts[0]; a refusal comes back as a text part.
        blob = next((p.inline_data.data for p in parts
                     if getattr(p, "inline_data", None) and p.inline_data.data), None)
        if blob is not None:
            return blob

        txt = " ".join((p.text or "") for p in parts if getattr(p, "text", None))
        reason = str(cand.finish_reason)
        transient = ("NO_IMAGE" in reason and not cand.safety_ratings and not txt.strip())
        if transient and attempt < MAX_ATTEMPTS:
            wait = min(6 * attempt, 30)
            print(f"     ! empty result ({reason}) — retry {wait}s")
            time.sleep(wait)
            continue
        raise RuntimeError(f"{label}: model returned no image. finish_reason={reason} "
                           f"safety={cand.safety_ratings} text={txt[:300]!r}")

    raise RuntimeError(f"{label}: exhausted {MAX_ATTEMPTS} attempts: "
                       f"{type(last).__name__}: {last}")


# --------------------------------------------------------------------------
def _flags(man: dict) -> str:
    """Only `needs_hairline` is a warning. `edge_activity` is printed for the eye, never as
    a verdict — see plate_report's docstring for why it stopped being one."""
    p = man.get("plate", {})
    out = [f"edge {p.get('edge_activity', 0):.2f}"]
    if p.get("needs_hairline"):
        out.append("· light ground, needs the card hairline")
    return "  " + "  ".join(out)


def generate(slug: str, force: bool = False) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    category, treatment, subject = resolve(slug)
    art = OUT / f"{slug}.art.jpg"
    hero = OUT / f"{slug}.hero.jpg"
    prompt = prompt_for_slug(slug)
    sha = hashlib.sha1(prompt.encode()).hexdigest()[:12]
    man = read_manifest(slug)

    have_all = art.exists() and all((OUT / f"{slug}.{n}.jpg").exists() for n in DERIVATIVES)
    if have_all and man.get("art_prompt_sha1") == sha and not force:
        print(f"  {slug:32s} [up to date — no API call]")
        return hero

    # Archive the outgoing master BEFORE overwriting it. A re-roll is destructive and the
    # previous picture is often still wanted — "keep the old mirror but the new paper" is a
    # normal note, and it is unanswerable once the file is gone. Keyed by the prompt hash
    # that produced it, so each archived version traces back to its own subject text.
    if art.exists():
        prev = OUT / "_prev"
        prev.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(art, prev / f"{slug}.{man.get('art_prompt_sha1', 'unknown')}.art.jpg")

    blob = _call_model(prompt, slug)
    w, h = _write_master(blob, art)
    masters = _derive_all(art, OUT / slug)

    man.update(slug=slug, category=category, treatment=treatment, subject=subject,
               subject_source="authored" if slug in ARTICLES else "auto",
               art_model=MODEL, art_aspect=ART_ASPECT, art_size=ART_SIZE,
               art_prompt=prompt, art_prompt_sha1=sha,
               art_file=art.name, art_native_width=w, art_native_height=h,
               masters=masters, plate=plate_report(art))
    write_manifest(slug, man)
    print(f"  {slug:32s} {treatment:20s} + {w}x{h} → hero "
          f"{masters['hero']['bytes'] // 1024} KB{_flags(man)}")
    return hero


def recheck(slugs: list[str]) -> bool:
    """FREE. Re-derive and remeasure from the masters on disk, so a manifest written before
    a new check exists gains it without an API call."""
    bad = False
    for slug in slugs:
        art = OUT / f"{slug}.art.jpg"
        if not art.exists():
            print(f"  {slug:32s} (no master — skipped)")
            continue
        man = read_manifest(slug)
        man["masters"] = _derive_all(art, OUT / slug)
        man["plate"] = plate_report(art)
        write_manifest(slug, man)
        flags = _flags(man)
        bad = bad or "⚠" in flags
        print(f"  {slug:32s} ground {man['plate']['ring_median_luma']:.2f}{flags}")
    return bad


def run_compare(slug: str, force: bool = False) -> None:
    """Render ONE article across several treatments, so the only variable is the treatment
    and the amount of range in the system can be judged by looking."""
    resolved = resolve(slug)
    if resolved is None:
        raise SystemExit(f"no subject for '{slug}'")
    category, own, subject = resolved
    CMP_OUT.mkdir(parents=True, exist_ok=True)
    wanted = [own] + [t for t in COMPARE_TREATMENTS if t != own]
    index = []
    print(f"Compare — {slug} ({category}) · {len(wanted)} treatment(s) · {MODEL}\n")
    for i, tr in enumerate(wanted):
        master = CMP_OUT / f"{slug}__{tr}.art.jpg"
        prompt = prompt_for(subject, category, tr)
        try:
            if not master.exists() or force:
                _write_master(_call_model(prompt, f"{slug}/{tr}"), master)
            stem = CMP_OUT / f"{slug}__{tr}"
            masters = _derive_all(master, stem)
            index.append({"slug": slug, "treatment": tr, "category": category,
                          "subject": subject, "prompt": prompt, "masters": masters,
                          "plate": plate_report(master)})
            print(f"  {tr:22s} + hero {masters['hero']['bytes'] // 1024} KB")
        except Exception as e:  # noqa: BLE001
            print(f"  {tr:22s} FAILED {type(e).__name__}: {str(e)[:200]}")
        if i < len(wanted) - 1:
            time.sleep(THROTTLE)
    (CMP_OUT / f"{slug}.index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"\n{len(index)}/{len(wanted)} in {CMP_OUT}")


# --------------------------------------------------------------------------
# --auto : write a subject for an article that has none
# --------------------------------------------------------------------------
# Two nets, deliberately separate.
#
# Brands and people are matched on the bare word — an unqualified "amazon" or "holmes" in a
# subject is always wrong. Human anatomy is NOT, because a coin, a dial and a clock all have
# a "face" and a machine can be "hand-cranked": matching those bare words rejected a correct
# subject on its first run. Only the plural or an explicitly human form counts.
_BANNED_SUBJECT = re.compile(
    r"\b(amazon|apple|tesla|visa|mastercard|netflix|disney|google|microsoft|costco|"
    r"enron|wework|ftx|theranos|nvidia|meta|facebook|openai|coinbase|binance|"
    r"buffett|munger|bezos|jobs|musk|cook|holmes|neumann|bankman|skilling|"
    r"logo|trademark|brand|signage|lettering|letters|numerals|watermark|"
    r"person|people|portrait|man|woman)\b"
    r"|human (?:face|hand|figure)|\bfaces\b|\bhands\b", re.I)

_SUBJECT_PROMPT = """You are choosing the SUBJECT of one still-life photograph for a financial case-study article. Reply with the subject description ONLY — one sentence, no preamble, no quotes, no markdown.

ARTICLE TITLE: {title}
ARTICLE SUBTITLE: {subtitle}
SECTION: {category} — {category_note}

{shape}

{examples}

THE ONE RULE THAT MATTERS MOST: the object must be THE TOPIC'S OWN THING, not a generic symbol of success or failure. If the article is about two soft-drink companies, the answer is two cans of drink — not two boxing gloves, not two chess pieces, not two gears. Ask "what physical object does this story actually contain?" before you ask "what would look good". A picture that could sit on any article in this section is the wrong picture.

HARD RULES, all of them:
- A real physical object that exists and can be photographed. Never an abstract concept, never a scene, never a place, never a diagram.
- NEVER name a company, a product, a brand, a logo, or any real person. Describe the product CATEGORY generically and say explicitly that it carries no branding — "an unbranded aluminium drinks can", never the company's can.
- The object must carry NO text, letters, numbers, dials with numerals, labels or markings of any kind — say so explicitly in your description if the real object usually has them.
- No people, no faces, no hands.
- Describe only the OBJECT and how it sits. Do NOT describe lighting, background, colour, mood or camera — those are decided separately.
- One sentence. Under 55 words."""

_CATEGORY_NOTE = {
    "blueprints": "success stories — how a winning business was built. Reach for the thing the "
                  "company actually makes, sells or runs on.",
    "valueTraps": "failures, frauds and collapses — what went wrong. Reach for the thing that "
                  "broke, went missing, or was faked.",
    "battles": "two rivals compared head to head.",
}

# Battles gets its own shape instruction because the rule is structural, not thematic: the
# picture must contain TWO of the same product, told apart only by which side's light falls
# on them. One object, however good, cannot say "versus".
_SHAPE = {
    "blueprints": "Describe ONE single physical object (or a small group of at most three "
                  "objects) that this business itself would contain — what it makes, ships, "
                  "runs on, or charges for. Follow the shape of these examples exactly:",
    "valueTraps": "Describe ONE single physical object (or a small group of at most three "
                  "objects) drawn from this specific failure — the thing that broke, the "
                  "thing that was empty, the thing that was faked. Follow the shape of these "
                  "examples exactly:",
    "battles": "Describe TWO IDENTICAL objects of the SAME kind, standing side by side and "
               "plainly opposed — the product both rivals actually sell. If the story is two "
               "soft-drink companies, that is two unbranded drinks cans; if it is two card "
               "networks, two blank payment cards. Both must be the same size and the same "
               "type of thing, so only the lighting separates them, and both must be "
               "completely unbranded and unmarked. Follow the shape of these examples "
               "exactly:",
}


def _examples_for(category: str) -> str:
    out = [sub for _s, (c, _t, sub) in ARTICLES.items() if c == category][:3]
    return "\n".join(f"- {s}" for s in out)


def write_auto_subject(slug: str, meta: dict) -> str:
    """Ask for a subject, validate it, cache it to <slug>.subject.txt.

    CACHING IS THE POINT, not a speed-up. The subject is part of the hashed prompt, so a
    subject re-written on every run would change the hash on every run and re-roll the
    whole set at full cost. Once written, the file behaves exactly like a hand-authored
    ARTICLES entry — edit it (or add the entry) to override.
    """
    from google import genai

    category = meta.get("category", "blueprints")
    if category not in PALETTES:
        category = "blueprints"
    prompt = _SUBJECT_PROMPT.format(
        title=meta.get("title", slug), subtitle=meta.get("subtitle", ""),
        category=category, category_note=_CATEGORY_NOTE[category],
        shape=_SHAPE[category], examples=_examples_for(category))

    client = genai.Client(api_key=KEY)
    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(model=SUBJECT_MODEL, contents=prompt)
            text = " ".join((resp.text or "").split()).strip().strip('"').strip()
        except Exception as e:  # noqa: BLE001
            print(f"    ! subject call failed ({type(e).__name__}: {str(e)[:80]}) "
                  f"— attempt {attempt}/3")
            time.sleep(4 * attempt)
            continue
        hit = _BANNED_SUBJECT.search(text)
        if not text or len(text) < 40:
            print(f"    ! subject too short on attempt {attempt}/3: {text[:80]!r}")
        elif hit:
            print(f"    ! subject named '{hit.group(0)}' on attempt {attempt}/3 — rejected")
        else:
            subject_path(slug).write_text(text + "\n")
            print(f"    + {default_treatment(slug, category)} · {text[:70]}…")
            return text

    # A new topic must NEVER ship artless. Falling back to a sibling's subject would be
    # wrong (two articles with the same picture), so fail loudly instead and let --auto be
    # re-run; the article simply keeps its gradient until then.
    raise RuntimeError(f"could not write a usable subject for {slug} after 3 attempts")


def run_auto(slugs: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = article_meta()
    missing = [s for s in slugs if resolve(s) is None]
    if not missing:
        print("  every article already has a subject — nothing to write.\n")
        return
    print(f"  writing {len(missing)} subject(s) with {SUBJECT_MODEL}\n")
    for slug in missing:
        print(f"  {slug}")
        try:
            write_auto_subject(slug, meta[slug])
        except Exception as e:  # noqa: BLE001
            print(f"    ! {type(e).__name__}: {e}")


# --------------------------------------------------------------------------
def main():
    _check_article_table()
    argv = sys.argv[1:]
    force = "--force" in argv
    all_slugs = [a["slug"] for a in _load_articles()]

    if "--compare" in argv:
        i = argv.index("--compare")
        if i + 1 >= len(argv):
            raise SystemExit("--compare needs an article slug")
        run_compare(argv[i + 1], force)
        return

    only = [argv[i + 1] for i, a in enumerate(argv) if a == "--only" and i + 1 < len(argv)]
    unknown = [s for s in only if s not in all_slugs]
    if unknown:
        raise SystemExit(f"unknown article slug(s): {unknown}")

    cats = [argv[i + 1] for i, a in enumerate(argv) if a == "--category" and i + 1 < len(argv)]
    bad = [c for c in cats if c not in PALETTES]
    if bad:
        raise SystemExit(f"unknown category/ies: {bad} — expected {sorted(PALETTES)}")

    meta = article_meta()
    slugs = only or [s for s in all_slugs if not cats or meta[s].get("category") in cats]

    if "--auto" in argv:
        run_auto(slugs)

    if "--recheck" in argv:
        sys.exit(1 if recheck(slugs) else 0)

    unresolved = [s for s in slugs if resolve(s) is None]
    if unresolved:
        raise SystemExit(f"no subject for {unresolved}. Add ARTICLES entries, or use --auto.")

    print(f"Money Moves art — {len(slugs)} article(s) · {MODEL} · {ART_ASPECT} · {ART_SIZE}\n")
    failed, warned = [], []
    for i, slug in enumerate(slugs):
        try:
            generate(slug, force)
            if "⚠" in _flags(read_manifest(slug)):
                warned.append(slug)
        except Exception as e:  # noqa: BLE001
            print(f"  {slug:32s} FAILED {type(e).__name__}: {str(e)[:300]}")
            failed.append(slug)
        if i < len(slugs) - 1:
            time.sleep(THROTTLE)

    print(f"\n{len(slugs) - len(failed)}/{len(slugs)} article(s) in {OUT}")
    if warned:
        print("⚠ review these — a margin warning is a RE-WRITE of the subject, not a "
              "re-roll of the same prompt:", warned)
    if failed:
        print("failed:", failed)
        sys.exit(1)
    print("Next: ./venv/bin/python scripts/seed_money_moves.py --dry-run")


if __name__ == "__main__":
    main()
