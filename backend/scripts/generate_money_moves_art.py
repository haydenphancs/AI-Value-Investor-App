"""
Stage 1 of 2 — generate the editorial cover art for each Money Moves article. PAID.

    generate_money_moves_art.py  ->  seed_money_moves.py (uploads + bakes the URLs)

THE SYSTEM, in one line: ONE metaphor object, isolated like an exhibit, on a deliberately
DESIGNED GROUND, in a limited palette.

The variable that carries the range is the GROUND TREATMENT — graph paper, a flat colour
field, seamless white, a specimen plate, aged ledger stock, a physical print lying in a
scene — not the lighting. Eleven treatments live in TREATMENTS (now in
app/services/money_moves_art_style.py, shared with the weekly engine) and each article is
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

# The art SYSTEM (treatments, palettes, BASE, the subject table, the subject prompt and the
# derivative geometry) lives in app/services/money_moves_art_style.py since 2026-11-01, shared
# with the weekly engine's cover art. Moved byte for byte and imported back under the same names,
# so every hash below is unchanged (tests/test_money_moves_art_style.py recomputes them all).
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from app.services.money_moves_art_style import (  # noqa: E402
    ARTICLES, ART_ASPECT, ART_SIZE, BASE, DERIVATIVES, DERIVATIVE_QUALITY, LIGHT_GROUND_LUMA,
    MARGIN, PALETTE_OVERRIDES, PALETTES, PLACEHOLDER_TOPICS, STAGED_ARTICLES, TREATMENTS, _BANNED_SUBJECT,
    _CATEGORY_NOTE, _SHAPE, _SUBJECT_PROMPT, _examples_for, default_treatment, fit_to,
    prompt_for, save_derivative, save_master,
)


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




def resolve(slug: str) -> tuple[str, str, str] | None:
    """(category, treatment, subject), or None when nothing has been authored yet.

    A hand-authored ARTICLES entry always wins: --auto is a floor, not an override, so
    editing the table is how you correct a picture you do not like.
    """
    if slug in ARTICLES:
        return ARTICLES[slug]
    if slug in PLACEHOLDER_TOPICS:
        return PLACEHOLDER_TOPICS[slug]
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
    # STAGED_ARTICLES: rows for plates whose article is not in the bundle yet (see the style module).
    orphan = set(ARTICLES) - authored - STAGED_ARTICLES
    if orphan:
        raise SystemExit(f"ARTICLES has key(s) not in {ARTICLES_JSON.name}: {sorted(orphan)}")
    # The reverse for placeholders: once a topic IS authored it must move into ARTICLES, or
    # the same slug would carry two different subjects and whichever table wins is luck.
    promoted = set(PLACEHOLDER_TOPICS) & authored
    if promoted:
        raise SystemExit(
            f"{sorted(promoted)} now exist in {ARTICLES_JSON.name} — move them from "
            f"PLACEHOLDER_TOPICS into ARTICLES so one slug has exactly one subject")
    dupe = set(PLACEHOLDER_TOPICS) & set(ARTICLES)
    if dupe:
        raise SystemExit(f"slug(s) in BOTH tables: {sorted(dupe)}")
    bad_ph = {s: t for s, (_c, t, _sub) in PLACEHOLDER_TOPICS.items() if t not in TREATMENTS}
    if bad_ph:
        raise SystemExit(f"unknown treatment on placeholder(s) {bad_ph}")
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
                  if s in meta and meta[s].get("category") != c}
    if mismatched:
        raise SystemExit(f"category disagrees with money_moves.json: {mismatched}")




def prompt_for_slug(slug: str, treatment: str = None) -> str:
    resolved = resolve(slug)
    if resolved is None:
        raise SystemExit(
            f"no subject for '{slug}'. Add an ARTICLES entry, or run with --auto to have "
            f"one written and cached to {subject_path(slug).name}.")
    category, tr, subject = resolved
    return prompt_for(subject, category, treatment or tr, PALETTE_OVERRIDES.get(slug))


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
    if the model returned something off-aspect — which it does by a pixel or two. The geometry
    and the encoding are the shared `fit_to` / `save_derivative` (the weekly engine uses them)."""
    from PIL import Image

    with Image.open(master) as im:
        im, cropped = fit_to(im, out_w, out_h)
        tmp = dest.with_suffix(".part")
        save_derivative(im, tmp)
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
    save_master(img, tmp)
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

    # Placeholders are not in money_moves.json, so they are opt-in: a bare run stays exactly
    # the thirteen authored articles and cannot quietly start spending on teaser art.
    if "--placeholders" in argv:
        all_slugs = list(PLACEHOLDER_TOPICS)
    elif "--all" in argv:
        all_slugs = all_slugs + list(PLACEHOLDER_TOPICS)

    only = [argv[i + 1] for i, a in enumerate(argv) if a == "--only" and i + 1 < len(argv)]
    unknown = [s for s in only if s not in all_slugs and s not in PLACEHOLDER_TOPICS]
    if unknown:
        raise SystemExit(f"unknown article slug(s): {unknown}")

    cats = [argv[i + 1] for i, a in enumerate(argv) if a == "--category" and i + 1 < len(argv)]
    bad = [c for c in cats if c not in PALETTES]
    if bad:
        raise SystemExit(f"unknown category/ies: {bad} — expected {sorted(PALETTES)}")

    meta = article_meta()
    slugs = only or [s for s in all_slugs
                     if not cats or (meta[s].get("category") if s in meta
                                     else PLACEHOLDER_TOPICS[s][0]) in cats]

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
