"""The Money Moves cover-art SYSTEM — one source for the catalog script and the weekly engine.

    scripts/generate_money_moves_art.py      the catalog's paid plates (hand-run, checkpointed)
    app/services/learn_weekly/cover_art.py   one plate per Wiser weekly piece (unattended)

Both draw from THIS module, so a weekly piece looks like a catalog article: one metaphor object,
isolated like an exhibit, on a designed ground, in its category's palette (the script's docstring
explains the system and why the art carries no text).

⚠️ BYTE-IDENTICAL, ON PURPOSE. Everything below was MOVED here verbatim from the script on
2026-11-01 (lines sliced, never retyped). The script decides a plate is up to date by comparing
sha1(prompt)[:12] with the manifest's `art_prompt_sha1`, and BASE, TREATMENTS, PALETTES, ARTICLES,
PALETTE_OVERRIDES and `prompt_for` all feed that prompt — so one changed character here re-rolls
every approved, paid catalog plate it touches on the next generate run.
`tests/test_money_moves_art_style.py` recomputes every manifest's hash from this module and fails
on any drift; `tests/test_money_moves_art_parity.py` pins the rest of the system.

Pure: no app settings, no network, no Supabase. Pillow is imported inside the image helpers only
(numpy-based measurement — `plate_report` — stays in the script).
"""

from __future__ import annotations

import hashlib
import io
import re
from typing import Dict, Tuple

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
    # Promoted from PLACEHOLDER_TOPICS when the article was authored. The subject string is
    # UNCHANGED, byte for byte — it feeds art_prompt_sha1, and the plate is already approved
    # and published, so any edit here re-rolls it at full cost.
    "nvidias-ai-dominance": ("blueprints", "cutout_grid",
        "a dense graphics accelerator board lying flat and seen from above, its finned "
        "heatsink, twin fan shrouds and long gold edge connector all clearly readable, "
        "completely blank with no printing or markings anywhere on the board"),

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
    # Promoted verbatim — see the note on nvidias-ai-dominance above.
    "the-fall-of-sears": ("valueTraps", "ledger_still",
        "one very thick mail-order catalogue lying closed, its pages swollen and rippled "
        "with old water damage and its cover worn blank, a few loose pages slipping out of "
        "the block"),

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
    # Promoted verbatim — see the note on nvidias-ai-dominance above.
    "amd-vs-intel-the-cpu-wars": ("battles", "macro_noir",
        "two identical square computer processor chips lying flat side by side, their "
        "polished metal heat spreaders and the dense grid of gold contact pads around the "
        "edge of each clearly readable, both completely blank and unmarked"),

    # ---- PROMOTED 2026-08-21 ------------------------------------------------
    # All seven moved verbatim out of PLACEHOLDER_TOPICS when their articles were
    # authored. The subject strings are UNCHANGED, byte for byte — they feed
    # art_prompt_sha1, and every plate is already approved, published and recorded in
    # its manifest, so an edit here re-rolls paid artwork at full cost.
    "the-rise-of-lvmh": ("blueprints", "colour_field",
        "two rigid leather travel trunks stacked one on the other, their brass corner caps, "
        "riveted edging and unmarked latches clearly readable, the leather grain visible"),
    "microsofts-cloud-metamorphosis": ("blueprints", "duotone_press",
        "a rack-mount server blade pulled halfway out on its rails, its rows of drive bays, "
        "vent slots and handle clearly readable, every face completely blank and unlabelled"),
    "tsmc-the-foundry-that-runs-the-world": ("blueprints", "tech_noir",
        "a large polished silicon wafer standing on its edge, the fine concentric grid of "
        "identical square dies covering its mirrored surface catching the light, its single "
        "flat notch visible at the rim and no printing anywhere on it"),

    # ---- BATTLES: two identical objects, the product both rivals actually sell ----
    "the-home-depot-vs-lowes": ("battles", "cutout_grid",
        "two identical open metal paint tins standing side by side, each filled to the rim "
        "with smooth level paint and a clean stir stick resting across it, both tins "
        "completely blank with no printing or labelling"),
    "boeing-vs-airbus-the-aerospace-duopoly": ("battles", "duotone_press",
        "two identical turbofan jet engines seen head on and side by side, the wide fan "
        "blades, spinner cone and nacelle lip of each clearly readable, both completely "
        "unmarked"),
    "the-rise-of-tiktok-vs-instagram-reels": ("battles", "flat_deco",
        "two identical smartphones standing upright side by side, seen straight on, their "
        "screens completely blank and their bodies bare with no buttons marked and no "
        "symbols anywhere"),

    # ---- VALUE TRAPS: the thing that broke, emptied, or was faked ----
    "metas-metaverse-pivot": ("valueTraps", "specimen_plate",
        "a single virtual-reality headset lying on its side, its twin lens ports, foam face "
        "seal and slack head strap all clearly readable, the whole thing completely blank "
        "and unmarked"),

    # ---- AUTHORED 2026-11-01: hand-written, one per category ----
    # The article's argument is the plot under the building, not the building. The cyanotype is
    # how land and structures are actually drawn, so the property reads as an engineered asset.
    "mcdonalds-the-real-estate-empire": ("blueprints", "blueprint_cyanotype",
        "a small single-storey roadside restaurant with a drive-through lane, drawn as an "
        "exploded assembly with its flat roof, walls and foundation slab lifted apart along "
        "one vertical axis above the surveyed boundary lines and corner stakes of its own "
        "rectangular plot of land, with no sign, emblem or mark of any kind on the building"),
    # The Battles rule's own example, made literal: two bare cans, told apart only by the light.
    "coca-cola-vs-pepsico": ("battles", "tech_noir",
        "two identical aluminium drinks cans of exactly the same size standing upright side "
        "by side, beaded with cold condensation, their ring-pull tabs and rolled rims "
        "clearly readable, both bare brushed metal with no printing, colour, label or mark "
        "of any kind"),
    # A photograph of film: the consumable that paid for everything, shown on the medium it sold.
    "kodak-inventing-the-future-it-couldnt-sell": ("valueTraps", "found_artefact",
        "a single roll of 35mm photographic film in its small metal canister with the film "
        "leader curling out of the slot, lying on a darkroom counter beside a few more "
        "canisters and loose curled strips of developed film negatives, every canister bare "
        "and unmarked"),
}

# Slugs in ARTICLES whose article is NOT in money_moves.json yet: hand-written articles whose plates
# were generated on 2026-11-01 before their narration exists, so the bundle does not carry them. Their
# rows ship now because a missing row sends the next generate run to --auto for a NEW subject, which
# changes the prompt hash and re-rolls a paid plate. The generator exempts them from its orphan and
# category checks and never draws them (a bare run walks the bundle's slugs; --only refuses them).
# Each slug leaves this set the day it joins the bundle (tests/test_money_moves_art_parity.py).
STAGED_ARTICLES = frozenset({
    "coca-cola-vs-pepsico",
    "kodak-inventing-the-future-it-couldnt-sell",
    "mcdonalds-the-real-estate-empire",
})

# =============================================================================
# PLACEHOLDER TOPICS — cards that exist in the iOS catalog but have no article yet.
#
# `MoneyMove.sampleData` carries a few "coming soon" teasers that are NOT in
# money_moves.json, so they have no row, no slug and no artwork, and they sat as blank
# tiles beside thirteen illustrated ones. These get the same treatment system and the same
# bucket; the only difference is that their URL is compiled into the Swift static (the
# `gen_book_covers_swift.py` precedent) because there is no `content` blob to bake it into.
#
# The slug is the one the article WILL have when it is authored — kebab-case of the title,
# matching every existing entry. So when the content lands, its row takes over by slug and
# the already-published plate is already at the right path. Nothing has to be re-uploaded.
# =============================================================================
PLACEHOLDER_TOPICS: dict[str, tuple[str, str, str]] = {}

# =============================================================================
# PALETTE OVERRIDES — slug -> a palette brief that REPLACES the category's.
#
# The category palette is a default, not a law. Blueprints is cold steel-blue, which is
# right for machinery and wrong for the two topics below: a luxury house rendered in
# gunmetal reads as a hardware company, and the first Costco/Buffett renders came back cold
# when the subject plainly wanted tungsten. This is the same content-driven override the
# Journey art pipeline needed (crypto->ember, warnings->crimson).
#
# Keyed by slug so it can never change another article's prompt — and therefore its hash.
# =============================================================================
PALETTE_OVERRIDES = {
    "the-rise-of-lvmh": (
        "warm champagne gold, aged cognac leather and deep bronze over a soft ivory ground. "
        "Rich and quiet — the colour of a boutique, never brassy and never yellow."
    ),
}


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


def prompt_for(subject: str, category: str, treatment: str, palette: str = None) -> str:
    """`palette` defaults to the CATEGORY's brief.

    The parameter is optional rather than required specifically so that every prompt written
    before overrides existed hashes identically — passing nothing reproduces the old string
    byte for byte, and no approved plate re-rolls.
    """
    return BASE.format(treatment=TREATMENTS[treatment][1], subject=subject,
                       palette=palette or PALETTES[category])


# =============================================================================
# DERIVATIVES — the master → hero / card geometry, shared by the script (files) and the weekly
# engine (bytes). Same operations in the same order as the script always ran them, so a plate the
# script re-derives is unchanged.
# =============================================================================
def fit_to(im, out_w: int, out_h: int):
    """(the resized RGB image, cropped). Both derivatives share the master's 16:9, so this only crops
    if the model returned something off-aspect — which it does by a pixel or two."""
    from PIL import Image

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
    return im.resize((out_w, out_h), Image.LANCZOS), cropped


def save_derivative(im, fp) -> None:
    """A derivative's JPEG encoding (path or file object)."""
    im.save(fp, "JPEG", quality=DERIVATIVE_QUALITY, optimize=True, progressive=True)


def save_master(img, fp) -> None:
    """The master's JPEG encoding (path or file object): q95, no chroma subsampling."""
    img.convert("RGB").save(fp, "JPEG", quality=95, subsampling=0, optimize=True)


def encode_master(blob: bytes) -> Tuple[bytes, Tuple[int, int]]:
    """(the master JPEG, its (width, height)) from the model's image bytes — in memory, the same
    encoding the script writes to `<slug>.art.jpg`. Raises (Pillow's own errors) on bytes that are
    not a complete image: the encoded master is decoded back in full, because `verify()` alone
    passes a truncated body."""
    from PIL import Image

    img = Image.open(io.BytesIO(blob))
    img.load()
    buf = io.BytesIO()
    save_master(img, buf)
    data = buf.getvalue()
    with Image.open(io.BytesIO(data)) as check:
        check.verify()
    with Image.open(io.BytesIO(data)) as check:
        check.load()
        return data, check.size


def derive_jpeg(master: bytes, out_w: int, out_h: int) -> Tuple[bytes, bool]:
    """(JPEG bytes, cropped): one derivative resampled FROM THE MASTER, never from another one."""
    from PIL import Image

    with Image.open(io.BytesIO(master)) as im:
        fitted, cropped = fit_to(im, out_w, out_h)
    buf = io.BytesIO()
    save_derivative(fitted, buf)
    return buf.getvalue(), cropped


def derive_all_jpeg(master: bytes) -> Dict[str, bytes]:
    """{"hero": bytes, "card": bytes} — every entry of DERIVATIVES from one master."""
    return {name: derive_jpeg(master, *spec)[0] for name, spec in DERIVATIVES.items()}


# =============================================================================
# SUBJECTS — the prompt that asks a text model for a subject, and the checks on its answer.
# =============================================================================
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



def subject_prompt(title: str, subtitle: str, category: str) -> str:
    """The subject request for one article — the exact prompt `--auto` sends."""
    category = category if category in PALETTES else "blueprints"
    return _SUBJECT_PROMPT.format(
        title=title, subtitle=subtitle, category=category,
        category_note=_CATEGORY_NOTE[category], shape=_SHAPE[category],
        examples=_examples_for(category))


#: A subject shorter than this cannot describe an object well enough to render (`--auto`'s floor).
SUBJECT_MIN_CHARS = 40


def subject_problem(text: str, category: str) -> str | None:
    """Why a model-written subject is unusable, or None. The script's `--auto` floor (length, the
    brand/person deny-list) plus the Battles shape the parity test pins on authored subjects: a
    Battles subject opens by naming TWO objects."""
    if not text or len(text) < SUBJECT_MIN_CHARS:
        return f"too short ({len(text or '')} characters)"
    hit = _BANNED_SUBJECT.search(text)
    if hit:
        return f"names {hit.group(0)!r}"
    if category == "battles" and not text[:90].lower().startswith("two "):
        return "a Battles subject must open with TWO identical objects"
    return None



__all__ = [
    "ARTICLES", "ART_ASPECT", "ART_SIZE", "BASE", "DERIVATIVES", "DERIVATIVE_QUALITY",
    "LIGHT_GROUND_LUMA", "MARGIN", "PALETTES", "PALETTE_OVERRIDES", "PLACEHOLDER_TOPICS",
    "SUBJECT_MIN_CHARS", "TREATMENTS", "default_treatment", "derive_all_jpeg", "derive_jpeg",
    "encode_master", "fit_to", "prompt_for", "save_derivative", "save_master", "subject_problem",
    "subject_prompt",
]
