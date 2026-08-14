"""
Stage 2 of 4 — draw the type onto the art plates. FREE, deterministic, always re-runs.

Separate from stage 1 on purpose: this is the script you run thirty times while
tuning a tagline or a wrap, and it must never cost money or risk the approved art.
Fusing the two would mean every wording tweak either re-pays for art or needs a
--skip-art flag that, forgotten once, silently replaces art you had signed off.

Emits TWO masters per book, with the type re-set optically at each size:
    HERO  480x660  = 160x220pt @3x   (BookDetailView)
    THUMB 240x330  =  80x110pt @3x   (LibraryBookCard / EducationBookCard / SearchBookCard)
Never downscale the hero into the thumb — a 2:1 resample of composited type aliases
and shimmers during scroll.

Usage (from backend/):
    ./venv/bin/python scripts/compose_book_cover.py        # all books
    ./venv/bin/python scripts/compose_book_cover.py 7      # one book
"""
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw  # noqa: E402

from book_cover_typography import (  # noqa: E402
    assert_spelling, balanced_wrap, cap_of, contrast, crop_aspect, draw_tracked,
    fit_text, font_for_cap, grey_equiv, halo_under, hexc, lift, load_faces,
    luminance, rel_l, scrim_to, sharpness, text_w, uniform_cap)
from generate_book_cover_art import BOOKS, PALETTES, read_manifest, write_manifest  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/book_covers"
FONT_DIR = DATA / "fonts"

HERO = (480, 660)          # 160x220pt @3x
THUMB = (240, 330)         # 80x110pt @3x

INK = (240, 240, 236)      # bone — never pure white, matches the grade's ceiling
AUTHOR_ALPHA = 178
# Tagline ink, one per palette. Each is the frame's own hue lifted toward bone at draw
# time (see lift()), so it stays in family with the photograph instead of sitting on it.
ACCENTS = {
    "gold": "#E0B36A",
    "gray": "#8FB4DC",
    "green": "#86C9A0",
    "red": "#E0928A",
}
assert set(ACCENTS) == set(PALETTES), (
    f"every palette needs a tagline accent: {set(PALETTES) ^ set(ACCENTS)}")
TITLE_TRACK = 0.005    # must match between uniform_cap() and draw time

# The mastered-badge disc in LibraryBookCard is a 24pt circle at .offset(x:6,y:-6)
# in a .topTrailing ZStack, so within an 80x110 cover it covers x 62-80pt / y 0-18pt.
BADGE_PX_THUMB = (186, 0, 240, 54)

# title / author / tagline. Titles and authors are VERBATIM from LearnModels.swift —
# the cover byline sits 8pt from `Text(book.author)` on the same card, so a divergence
# reads as a data bug.
META = {
    1:  ("Rich Dad Poor Dad", "Robert T. Kiyosaki", "ASSETS BUY YOUR FREEDOM"),
    2:  ("The Intelligent Investor", "Benjamin Graham", "PRICE IS NOT VALUE"),
    3:  ("The Psychology of Money", "Morgan Housel", "BEHAVIOR BEATS BRILLIANCE"),
    4:  ("One Up On Wall Street", "Peter Lynch", "KNOW WHAT YOU OWN"),
    5:  ("Common Stocks and Uncommon Profits", "Philip Fisher", "LISTEN BEFORE YOU BUY"),
    6:  ("The Little Book of Common Sense Investing", "John C. Bogle",
         "COSTS ARE THE ONLY CERTAINTY"),
    7:  ("A Random Walk Down Wall Street", "Burton Malkiel", "NOBODY OUTRUNS THE ODDS"),
    8:  ("The Essays of Warren Buffett", "Warren Buffett & Lawrence Cunningham",
         "TIME DOES THE LIFTING"),
    9:  ("The Little Book that Still Beats the Market", "Joel Greenblatt",
         "GOOD COMPANIES, CHEAP PRICES"),
    10: ("The Most Important Thing", "Howard Marks", "THINK SECOND LEVEL"),
}

FACES = load_faces(FONT_DIR, {"inter": "Inter-var.ttf"})


def compose(order, size, title_cap):
    W, H = size
    s = W / 480.0
    is_thumb = W < 300
    slug, palette, _subject = BOOKS[order]
    title, author, tagline = META[order]
    key = hexc(ACCENTS[palette])

    art = DATA / f"{order}_{slug}.art.jpg"
    if not art.exists():
        raise FileNotFoundError(f"no art plate for book {order}: {art}\n"
                                f"Run: ./venv/bin/python scripts/generate_book_cover_art.py {order}")
    base = crop_aspect(Image.open(art).convert("RGB"), 8 / 11).resize(size, Image.LANCZOS)

    mx = int(W * 0.09)
    box_w = W - 2 * mx

    ov = Image.new("RGBA", size, (0, 0, 0, 0))       # author + title
    od = ImageDraw.Draw(ov, "RGBA")
    tov = Image.new("RGBA", size, (0, 0, 0, 0))      # tagline, on its own layer for the halo
    tod = ImageDraw.Draw(tov, "RGBA")
    y = H - int(H * 0.085)

    # --- author (shrinks to fit; "Warren Buffett & Lawrence Cunningham" is 36 chars
    #     and was being clipped mid-word at the frame edge)
    fa = font_for_cap(FACES, "inter", round((20 if is_thumb else 22) * s), 500)
    a_lines = balanced_wrap(od, author.upper().split(), fa, box_w, 0.11, 2)
    while a_lines is None and cap_of(fa) > 9:
        fa = font_for_cap(FACES, "inter", cap_of(fa) - 2, 500)
        a_lines = balanced_wrap(od, author.upper().split(), fa, box_w, 0.11, 2)
    if a_lines is None:
        fa, tr = fit_text(od, author.upper(), FACES, "inter", box_w, round(20 * s), 500, 0.11)
        a_lines = [author.upper()]
    for ln in reversed(a_lines):
        y -= cap_of(fa)
        draw_tracked(od, (mx, y), ln, fa, (*INK, AUTHOR_ALPHA), track=0.11)
        y -= round(7 * s)
    y -= round(16 * s)

    # --- title, at the SET-WIDE cap (see uniform_cap)
    ft = font_for_cap(FACES, "inter", title_cap, 700)
    lines = balanced_wrap(od, title.upper().split(), ft, box_w, TITLE_TRACK, 5)
    if lines is None:
        raise RuntimeError(f"book {order}: title does not fit at the uniform cap {title_cap}px")
    for ln in reversed(lines):
        y -= int(title_cap * 1.30)
        draw_tracked(od, (mx, y), ln, ft, (*INK, 255), track=TITLE_TRACK)
    y -= round(16 * s)

    # --- tagline: smallest AND dimmest element, and it lands highest in the block
    #     where the photograph still has content. Lifted close to bone, hue intact.
    tag_rgb = lift(key, 0.52)
    ftag = font_for_cap(FACES, "inter", round((17 if is_thumb else 20) * s), 650)
    tag_lines = balanced_wrap(tod, tagline.split(), ftag, box_w, 0.12, 2) or [tagline]
    tag_bottom = y
    for ln in reversed(tag_lines):
        y -= cap_of(ftag)
        draw_tracked(tod, (mx, y), ln, ftag, (*tag_rgb, 255), track=0.12)
        y -= round(6 * s)
    tag_top = y

    # --- darken the band for the WEAKEST ink (the tagline), starting far enough above
    #     the block that the tagline sits in the scrim's full strength, not its shoulder
    obox, tbox = ov.getbbox(), tov.getbbox()
    top = min(obox[1], tbox[1]) if (obox and tbox) else int(H * 0.60)
    im = scrim_to(base, top, ink_luma=grey_equiv(tag_rgb), target_ratio=6.5, pad=round(34 * s))

    zone = _mean_luma(im, (mx, top, W - mx, H))
    tag_zone = _mean_luma(im, (mx, max(0, tag_top), W - mx, min(H, tag_bottom + 2)))
    sharp = sharpness(im)

    out = im.convert("RGBA")
    halo_under(out, tov, radius=max(1.5, 3.2 * s))   # local fix for a bright hotspot
    out.alpha_composite(ov)
    out.alpha_composite(tov)

    assert_spelling([title], title)
    return (out.convert("RGB"),
            contrast(luminance(INK), rel_l(zone)),
            contrast(luminance(tag_rgb), rel_l(tag_zone)),
            sharp)


def _mean_luma(im, box):
    g = im.convert("L").crop(box)
    px = list(g.getdata())
    return sum(px) / max(1, len(px))


def main():
    only = [int(a) for a in sys.argv[1:] if a.isdigit()]
    orders = only or sorted(BOOKS)

    # ONE cap for every title, at each master size. A set is only a set if the titles
    # match; per-book fitting made short titles large and long ones small.
    titles = [META[o][0].upper() for o in META]
    caps = {}
    for label, size in (("hero", HERO), ("thumb", THUMB)):
        pd = ImageDraw.Draw(Image.new("RGB", size))
        # TITLE_TRACK must be identical here and at draw time — measuring at 0 tracking
        # and drawing at 0.005 picked a cap that then did not fit, and the fit guard
        # (correctly) refused to clip.
        caps[label] = uniform_cap(pd, titles, FACES, "inter",
                                  size[0] - 2 * int(size[0] * 0.09),
                                  wght=700, max_lines=5, track=TITLE_TRACK)
    print(f"uniform title cap — hero {caps['hero']}px ({caps['hero']/3:.1f}pt) · "
          f"thumb {caps['thumb']}px ({caps['thumb']/3:.1f}pt)\n")

    rows = []
    for order in orders:
        slug = BOOKS[order][0]
        man = read_manifest(order)
        title, author, tagline = META[order]
        rec = {}
        for label, size in (("hero", HERO), ("thumb", THUMB)):
            im, cr, tcr, sharp = compose(order, size, caps[label])
            name = f"{order}_{slug}.{label}.jpg"
            dst = DATA / name
            tmp = DATA / f".tmp_{order}.{label}.part"
            im.save(tmp, "JPEG", quality=86, subsampling=0, progressive=True, optimize=True)
            os.replace(tmp, dst)
            with Image.open(dst) as c:
                c.verify()
            with Image.open(dst) as c:
                c.load()
                w, h = c.size
            rec[label] = dict(file=name, width=w, height=h, bytes=dst.stat().st_size,
                              sha256=hashlib.sha256(dst.read_bytes()).hexdigest())
            if label == "hero":
                rows.append((order, slug, dst.stat().st_size // 1024, cr, tcr, sharp))

        # Identity fields belong on the manifest no matter which stage wrote it first —
        # stage 4 reads them, and the art may have been produced out of band.
        man.setdefault("curriculum_order", order)
        man.setdefault("slug", slug)
        man.setdefault("art_file", f"{order}_{slug}.art.jpg")
        man.update(title=title, author=author, tagline=tagline,
                   palette=BOOKS[order][1],
                   title_cap_hero_px=caps["hero"], title_cap_thumb_px=caps["thumb"],
                   masters=rec,
                   legibility=dict(title_contrast=round(rows[-1][3], 2),
                                   tagline_contrast=round(rows[-1][4], 2),
                                   sharpness=round(rows[-1][5], 1)))
        write_manifest(order, man)

    med = sorted(r[5] for r in rows)[len(rows) // 2]
    worst_tag = min(r[4] for r in rows)
    print(f"{'#':>2} {'book':44s} {'pal':>5s} {'KB':>4s} {'title':>8s} {'tagline':>9s} {'sharp':>7s}")
    for o, slug, kb, cr, tcr, sharp in rows:
        pal = BOOKS[o][1]
        flags = ""
        if tcr < 4.5:
            flags += "  ** TAGLINE LOW **"
        if sharp < med * 0.62:
            flags += "  ** soft (hint) **"
        print(f"{o:2d} {slug:44s} {pal:>5s} {kb:4d} {cr:7.2f}:1 {tcr:8.2f}:1 {sharp:7.1f}{flags}")
    print(f"\nworst tagline contrast {worst_tag:.2f}:1 (AA body text needs 4.5) · "
          f"median sharpness {med:.1f}")
    print("Note: the sharpness score is biased low on warm, low-contrast frames — "
          "treat it as a re-roll hint and confirm by eye.")
    print("\nNext: ./venv/bin/python scripts/seed_book_covers.py")


if __name__ == "__main__":
    main()
