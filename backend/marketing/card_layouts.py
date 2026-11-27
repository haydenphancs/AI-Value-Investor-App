"""
The VIDEO CARDS and the LESSON PICTURE in the approved look (news-v23, owner 2026-11-19: "i need exactly these
styles, colors, words"; `marketing/look.py`). `cards.py` keeps the contract — what each kind draws and declares
(`cards.onscreen_strings`), the ramp, the errors, the bytes — and lays each card out here:

* every 9:16 card (`look.VIDEO_FRAME`): the gradient + glow, the BRAND ROW 140 px from the top (the Caydex tile +
  "Caydex", and the card's kicker in the gold pill right-aligned — a template's opening-card kicker, the lesson's
  LESSON_KICKER), the content in cards.SAFE_X above the caption band (cards.TEXT_ZONE_Y[1]), the card's short
  FOOTER under the caption band, nothing in the safe bottom 420 px (cover_13f_berkshire.jpg);
* `opening` (a template's cover): its headline two-tone beside the company plate (the logo disc in a gold ring, the
  chip in a gold pill under it; two companies side by side; the list covers' plate size, the content from the top —
  never centred), then the figure big on a warm card in the series' tone
  (`CardSpec.tone`) with its label under it inside the card (`CardSpec.body`, news-v23); with per-post ART, the art across the top edge to edge under a navy scrim, the disc in its
  gold ring on the band's edge, the "AI illustration" pill at the band's bottom-right, the chip beside the disc;
* `text`: the title as the two-tone headline, the body on a warm card after a gold dot; `hook`: the hook alone, big,
  two-tone, centred; `stat`: the figure, its label and body on a warm card; `disclaimer` (the end card): the
  disclaimer text and the end card's CTA in gold, centred; `brand`: the big tile and the wordmark, no brand row;
* the lesson PICTURE (1080×1350, `look.POST_FRAME`): the brand row with LESSON_KICKER, the title two-tone, each
  paragraph on its own warm card after a gold dot joined by a gold rail (lesson.jpg), the footer.

Every string is drawn whole (word-wrapped, a headline upper-cased only where the face has the upper case — its words
unchanged), every size steps down together along cards.RAMP_STEPS, every ink box is checked inside its region —
_NoFit at a step that does not fit (cards.layout_card / layout_image turn the floor's into CardOverflow). Pure apart
from Pillow; nothing here imports app.*.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from marketing import cards, look
from marketing.cards import Box, CardLayout, CardSpec, DrawnLine, _NoFit, _Style, _present

# ── sizes (px at ramp step 0 → the floor): cards' own constants, so the contract and the look name one size ──

TITLE_STYLE, BODY_STYLE, HOOK_STYLE = cards.TITLE_STYLE, cards.BODY_STYLE, cards.HOOK_STYLE
OPENING_HEADLINE_STYLE, OPENING_DISC, OPENING_FIGURE_STYLE = (cards.OPENING_HEADLINE_STYLE, cards.OPENING_DISC,
                                                              cards.OPENING_FIGURE_STYLE)
CHIP_STYLE = cards.CHIP_STYLE
STAT_FIGURE_STYLE, STAT_LABEL_STYLE, STAT_BODY_STYLE = cards.FIGURE_STYLE, cards.LABEL_STYLE, cards.STAT_BODY_STYLE
DISCLAIMER_STYLE, CTA_STYLE, STORE_CTA_STYLE = cards.DISCLAIMER_STYLE, cards.CTA_STYLE, cards.STORE_CTA_STYLE
WORDMARK_STYLE, BRAND_TILE_STYLE = cards.WORDMARK_STYLE, cards.BRAND_TILE_STYLE
OPENING_ART_H, OPENING_ART_DISC = cards.OPENING_ART_H, cards.OPENING_ART_DISC
OPENING_ART_SCRIM_H = 330
LESSON_TITLE_STYLE, LESSON_PARAGRAPH_STYLE = cards.IMAGE_TITLE_STYLE, cards.IMAGE_PARAGRAPH_STYLE
LESSON_RAIL: look.RGB = (217, 160, 30)          # the gold dot and rail on the lesson's cards (style_kit BAR_GOLD)
#: The cover headline's sizes beside the plate (mock_money_map's head ladder 80 / 72 / 66, ≤ 3 lines), tried before it
#: goes full width under the plate (cover_13f_berkshire.jpg: the two-tone headline BESIDE the plate) — never above the
#: ramp's size at the step, so the floor step is unchanged.
OPENING_BESIDE_PX: Tuple[int, ...] = (80, 72, 66)


def _gap(page: look.Page, px: int) -> int:
    return int(round(px * (1 - 0.4 * page.step / cards.RAMP_STEPS)))


def _frame_top(page: look.Page, spec: CardSpec, *, kicker: str, kicker_field: str) -> Tuple[Box, Optional[Box], int]:
    """The brand row (with the kicker pill when there is one) and the footer (when the card has one). Returns (the
    tile, the pill or None, the bottom of the content: the area's bottom — the footer sits under the caption band)."""
    tile, pill = look.brand_row(page, kicker if _present(kicker) else None, kicker_field=kicker_field)
    if _present(spec.footer):
        top = look.footer(page, spec.footer)
        if top < cards.CAPTION_BAND_Y[1]:
            raise _NoFit("footer: it would run into the caption band")
    return tile, pill, page.frame.area[3]


def _centre(page: look.Page, mark: Any, top: int, end: int, bottom: int) -> int:
    """Centre what was laid since `mark` (from `top` to `end`) in the room down to `bottom`. Returns the shift."""
    if end > bottom:
        raise _NoFit(f"the card's content ends at {end}px, room to {bottom}px")
    dy = max(0, (bottom - end) // 2)
    page.shift(mark, dy)
    return dy


def _body_card(page: look.Page, field: str, text: str, style: _Style, x0: int, x1: int, y: float) -> int:
    """`text` on a warm card after a gold dot (lesson.jpg's device). Returns the card's bottom."""
    size = page.px(style)
    pad = _gap(page, 44)
    tx = x0 + 96
    t = page.measure(text, size, x1 - 40 - tx, field, leading=style.leading, max_lines=24)
    h = t.height + 2 * pad
    box = look.card(page, (x0, y, x1, y + h))
    page.ellipse((x0 + 38, y + h / 2 - 13, x0 + 64, y + h / 2 + 13), LESSON_RAIL)
    page.put(field, t, tx, y + pad, "la", look.INK, box)
    return int(y + h)


def _badge(page: look.Page, spec: CardSpec, x: int, y: int) -> Tuple[Optional[Box], int]:
    """A text / stat card's own badge (a chip, GOLD on PILL) at (x, y), or nothing. Returns (its box, the y under
    it)."""
    if not _present(spec.badge):
        return None, y
    size = page.px(CHIP_STYLE)
    box = look.chip(page, "badge", spec.badge, size, x, y, fill=look.PILL, ink=look.GOLD)
    if box[2] > page.frame.area[2]:
        raise _NoFit(f"badge: the chip is {box[2] - box[0]}px wide, wider than the column")
    return box, box[3] + _gap(page, 24)


def _text(page: look.Page, spec: CardSpec, top: int, bottom: int) -> Optional[Box]:
    x0, _y0, x1, _y1 = page.frame.area
    mark = page.mark()
    badge, y = _badge(page, spec, x0 + 8, top)
    if _present(spec.title):
        y = look.headline(page, "title", spec.title, TITLE_STYLE, x0 + 8, y, x1 - x0 - 16)
    if _present(spec.body):
        y = _body_card(page, "body", spec.body, BODY_STYLE, x0, x1, y + (_gap(page, 40) if y > top else 0))
    shift = _centre(page, mark, top, y, bottom)
    return None if badge is None else (badge[0], badge[1] + shift, badge[2], badge[3] + shift)


def _stat(page: look.Page, spec: CardSpec, top: int, bottom: int) -> Optional[Box]:
    x0, _y0, x1, _y1 = page.frame.area
    mark = page.mark()
    badge, y = _badge(page, spec, x0 + 8, top)
    pad = _gap(page, 44)
    ix0, ix1 = x0 + pad, x1 - pad
    fig = page.measure(spec.figure, page.px(STAT_FIGURE_STYLE), ix1 - ix0, "figure", leading=1.04)
    parts: List[Tuple[str, look.Text, look.RGB]] = [("figure", fig, look.tone_colour(spec.tone or None))]
    if _present(spec.label):
        parts.append(("label", page.measure(spec.label, page.px(STAT_LABEL_STYLE), ix1 - ix0, "label",
                                            leading=STAT_LABEL_STYLE.leading), look.INK))
    if _present(spec.body):
        parts.append(("body", page.measure(spec.body, page.px(STAT_BODY_STYLE), ix1 - ix0, "body",
                                           leading=STAT_BODY_STYLE.leading), look.INK2))
    inner = _gap(page, 18)
    h = 2 * pad + sum(t.height for _f, t, _c in parts) + inner * (len(parts) - 1)
    box = look.card(page, (x0, y, x1, y + h))
    ty = y + pad
    for f, t, c in parts:
        page.put(f, t, ix0, ty, "la", c, box)
        ty += t.height + inner
    shift = _centre(page, mark, top, int(y + h), bottom)
    return None if badge is None else (badge[0], badge[1] + shift, badge[2], badge[3] + shift)


def _hook(page: look.Page, spec: CardSpec, top: int, bottom: int) -> None:
    x0, _y0, x1, _y1 = page.frame.area
    mark = page.mark()
    end = look.headline(page, "title", spec.title, HOOK_STYLE, x0 + 8, top, x1 - x0 - 16, max_lines=16)
    _centre(page, mark, top, end, bottom)


def _figure_card(page: look.Page, spec: CardSpec, x0: int, x1: int, y: float, style: _Style) -> int:
    """The cover's figure big on a warm card, centred, in the series' tone — and (news-v23, 2026-11-20) the figure's
    LABEL under it inside the same card (`spec.body`, the opening card's `figure_label`: INK, centred, word-wrapped;
    money_map_costco.jpg's callout reads "For every $100 of revenue, $2.94 was net income", so the cover reads
    "$2.94" / "of every $100 of revenue was net income"). Returns the card's bottom."""
    size = page.px(style)
    page.legible(size, "figure")
    pad = _gap(page, 40)
    font = page.font(size)
    while size > style.floor and font.getlength(spec.figure) > (x1 - x0) - 2 * pad:
        size -= 4
        font = page.font(size)
    label: Optional[look.Text] = None
    gap = 0
    if _present(spec.body):
        label = page.measure(spec.body, page.px(cards.OPENING_FIGURE_LABEL_STYLE), (x1 - x0) - 2 * pad, "body",
                             leading=cards.OPENING_FIGURE_LABEL_STYLE.leading, max_lines=3, balance=True)
        gap = _gap(page, 22)
    extra = 0 if label is None else gap + label.height
    tone = look.tone_colour(spec.tone or None)
    l, t, r, b = font.getbbox(spec.figure, anchor="ms")
    if r - l > (x1 - x0) - 2 * pad:
        t_ = page.measure(spec.figure, size, (x1 - x0) - 2 * pad, "figure", leading=1.04, max_lines=2)
        h = t_.height + 2 * pad + extra
        box = look.card(page, (x0, y, x1, y + h))
        page.put("figure", t_, (x0 + x1) / 2, y + pad, "ma", tone, box)
        if label is not None:
            page.put("body", label, (x0 + x1) / 2, y + pad + t_.height + gap, "ma", look.INK, box)
        return int(y + h)
    h = (b - t) + 2 * pad + extra
    box = look.card(page, (x0, y, x1, y + h))
    page.line("figure", spec.figure, size, (x0 + x1) / 2, y + pad - t, "ms", tone, box)
    if label is not None:
        page.put("body", label, (x0 + x1) / 2, y + pad + (b - t) + gap, "ma", look.INK, box)
    return int(y + h)


def _opening(page: look.Page, spec: CardSpec, top: int, bottom: int) -> None:
    x0, _y0, x1, _y1 = page.frame.area
    tx0, tx1 = x0 + 8, x1 - 8
    n = len(spec.logos)
    r = page.px(OPENING_DISC) if n == 1 else int(round(page.px(OPENING_DISC) * 0.75))
    ring = r + look.GOLD_RING_GAP
    gap = 16
    group_w = n * 2 * ring + (n - 1) * gap
    cy = top + 6 + ring
    for i, art in enumerate(spec.logos):
        cx = tx1 - group_w + ring + i * (2 * ring + gap)
        page.disc(cx, cy, r, art, ring=True)
    plate_bottom = cy + ring
    group_x0 = tx1 - group_w
    if _present(spec.label):
        cs = page.px(cards.OPENING_CHIP_STYLE)
        cw, ch = look.chip_size(page, spec.label, cs)
        mid = tx1 - group_w / 2
        px0 = max(tx0, min(mid - cw / 2, tx1 - cw))
        pill = look.chip(page, "label", spec.label, cs, px0, cy + r - 4, fill=look.GOLD, ink=look.INK)
        plate_bottom = pill[3]
        group_x0 = min(group_x0, pill[0])
    col_w = group_x0 - tx0 - 28
    y = None
    if col_w >= 240:
        try:
            y = max(look.headline(page, "title", spec.title, OPENING_HEADLINE_STYLE, tx0, top, col_w), plate_bottom)
        except _NoFit:
            y = None
        # still beside the plate at a smaller size (≤ 3 lines) before it goes under it
        for px in OPENING_BESIDE_PX:
            if y is not None:
                break
            if px > page.px(OPENING_HEADLINE_STYLE):
                continue
            mk = page.mark()
            try:
                y = max(look.headline(page, "title", spec.title, OPENING_HEADLINE_STYLE, tx0, top, col_w,
                                      max_lines=3, size=px), plate_bottom)
            except _NoFit:
                page.rollback(mk)
    if y is None:
        # a long headline (or a chip as wide as the column): under the plate, full width, up to 6 lines
        y = look.headline(page, "title", spec.title, OPENING_HEADLINE_STYLE, tx0, plate_bottom + _gap(page, 24),
                          tx1 - tx0, max_lines=8)
    if _present(spec.figure):
        y = _figure_card(page, spec, x0, x1, y + _gap(page, 40), OPENING_FIGURE_STYLE)
    # the cover's content starts at the top, as the list covers' does (cover_13f_berkshire.jpg) — never centred
    if y > bottom:
        raise _NoFit(f"the cover's content ends at {y}px, room to {bottom}px")


def _opening_art(page: look.Page, spec: CardSpec, bottom: int) -> Tuple[Box, Box]:
    """The cover WITH per-post art: the band (0, 0, W, OPENING_ART_H), the disc in a gold ring cut into its
    bottom-left edge, the "AI illustration" pill at its bottom-right, the chip (gold) beside the disc under the band,
    then the headline and the figure card. Returns (the band, the label pill)."""
    W = page.frame.canvas[0]
    x0, _y0, x1, _y1 = page.frame.area
    tx0, tx1 = x0 + 8, x1 - 8
    band: Box = (0, 0, W, OPENING_ART_H)
    lw, lh = cards.art_label_size(page.font_path, page.engine)
    label: Box = (W - 40 - lw, OPENING_ART_H - 28 - lh, W - 40, OPENING_ART_H - 28)
    r = page.px(OPENING_ART_DISC)
    (art,) = spec.logos
    cx, cy = x0 + 12 + r, OPENING_ART_H - 6
    page.disc(cx, cy, r, art, ring=True, cutout=look.page_colour(page.frame, int(cy + r)))
    if label[0] < cx + r + 12:
        raise _NoFit("opening art: the label runs into the disc")
    y = OPENING_ART_H + _gap(page, 24)
    if _present(spec.label):
        cs = page.px(CHIP_STYLE)
        cw, ch = look.chip_size(page, spec.label, cs)
        nx = cx + r + 42
        if nx + cw > tx1:
            raise _NoFit("label: the chip does not fit beside the disc")
        look.chip(page, "label", spec.label, cs, nx, OPENING_ART_H + 12, fill=look.GOLD, ink=look.INK)
        y = OPENING_ART_H + 12 + ch
    y = max(y, cy + r + look.GOLD_RING_GAP) + _gap(page, 28)
    mark = page.mark()
    top = y
    y = look.headline(page, "title", spec.title, OPENING_HEADLINE_STYLE, tx0, y, tx1 - tx0, max_lines=8)
    if _present(spec.figure):
        y = _figure_card(page, spec, x0, x1, y + _gap(page, 30), OPENING_FIGURE_STYLE)
    _centre(page, mark, top, y, bottom)
    return band, label


def _disclaimer(page: look.Page, spec: CardSpec, top: int, bottom: int) -> None:
    x0, _y0, x1, _y1 = page.frame.area
    mark = page.mark()
    body = page.measure(spec.body, page.px(DISCLAIMER_STYLE), x1 - x0, "body", leading=DISCLAIMER_STYLE.leading)
    cta = spec.end_cta
    cstyle = STORE_CTA_STYLE if cta == cards.CTA else CTA_STYLE
    ct = page.measure(cta, page.px(cstyle), x1 - x0, "cta", leading=cstyle.leading)
    cx = (x0 + x1) / 2
    area = page.frame.area
    page.put("cta", ct, cx, top, "ma", look.GOLD, area)
    y = top + ct.height + _gap(page, 56)
    page.put("body", body, cx, y, "ma", look.SUB, area)
    _centre(page, mark, top, int(y + body.height), bottom)


def _brand(page: look.Page, top: int, bottom: int, with_logo: bool) -> Optional[Box]:
    x0, _y0, x1, _y1 = page.frame.area
    mark = page.mark()
    y = top
    tile: Optional[Box] = None
    if with_logo:
        side = page.px(BRAND_TILE_STYLE)
        tx = int((x0 + x1) / 2 - side / 2)
        tile = (tx, int(y), tx + side, int(y) + side)
        y += side + _gap(page, 48)
    t = page.measure(cards.WORDMARK, page.px(WORDMARK_STYLE), x1 - x0, "wordmark", leading=1.2)
    page.put("wordmark", t, (x0 + x1) / 2, y, "ma", look.WHITE, page.frame.area)
    end = int(y + t.height)
    if end > bottom:
        raise _NoFit("brand: does not fit")
    dy = (bottom - end) // 2
    page.shift(mark, dy)
    if tile is not None:
        tile = (tile[0], tile[1] + dy, tile[2], tile[3] + dy)
    return tile


def place_card(spec: CardSpec, step: int, font_path: str, engine: str, with_logo: bool) -> CardLayout:
    """`spec` laid out on the 9:16 card at ramp `step` (_NoFit when it does not fit there)."""
    frame = look.VIDEO_FRAME
    page = look.Page(frame, step, font_path, engine)
    art_band: Optional[Box] = None
    label: Optional[Box] = None
    tile: Optional[Box] = None
    pill: Optional[Box] = None
    big_tile: Optional[Box] = None
    text_badge: Optional[Box] = None
    top, bottom = frame.area[1], frame.area[3]
    if spec.kind == "brand":
        big_tile = _brand(page, top, bottom, with_logo)
    else:
        kicker, kfield = (spec.badge, "badge") if spec.kind == "opening" else (spec.pill, "pill")
        tile, pill, bottom = _frame_top(page, spec, kicker=kicker, kicker_field=kfield)
        if spec.kind == "opening" and spec.art is not None:
            art_band, label = _opening_art(page, spec, bottom)
        elif spec.kind == "opening":
            _opening(page, spec, top, bottom)
        elif spec.kind in ("text",):
            text_badge = _text(page, spec, top, bottom)
        elif spec.kind == "stat":
            text_badge = _stat(page, spec, top, bottom)
        elif spec.kind == "hook":
            _hook(page, spec, top, bottom)
        else:   # disclaimer
            _disclaimer(page, spec, top, bottom)
    page.check()
    for ln in page.lines:
        if ln.field in ("brand", "badge", "pill", "footer") or look.is_logo_standin(ln.field):
            continue
        if art_band is not None and ln.ink[1] < art_band[3]:
            raise _NoFit(f"{ln.field}: drawn over the art band")
        if ln.ink[3] > cards.TEXT_ZONE_Y[1] or ln.ink[1] < frame.brand[1]:
            raise _NoFit(f"{ln.field}: leaves the card's content zone")
    lines: List[DrawnLine] = list(page.lines)
    if label is not None:
        lines.append(cards.art_label_line(label, font_path, engine))
    return CardLayout(kind=spec.kind, step=step, engine=engine, panel=None,
                      badge=pill if spec.kind == "opening" else text_badge, logo=big_tile, lines=tuple(lines),
                      tiles=tuple((d.box, d.art) for d in page.discs), art_box=art_band, label_box=label,
                      rects=tuple(page.rects), ellipses=tuple(page.ellipses), discs=tuple(page.discs),
                      brand=tile, pill=pill)


def draw_card(layout: CardLayout, spec: CardSpec, font_path: str, logo_path: Optional[str]):
    """The laid-out card as an RGB PIL image (1080×1920)."""
    frame = look.VIDEO_FRAME
    img = look.canvas(frame)
    if layout.art_box is not None:
        _art_band(img, layout.art_box, spec.art)
    if layout.label_box is not None:
        lb = layout.label_box
        cards._fill_rounded(img, lb, (lb[3] - lb[1]) // 2, look.NAVY_CHIP)
    if layout.logo is not None and logo_path:
        side = layout.logo[2] - layout.logo[0]
        img.paste(cards._logo_tile(logo_path, side), layout.logo[:2], cards._rounded_mask(side, side, int(side * 0.22)))
    look.draw_page(img, layout, frame, font_path, layout.engine, brand=spec.kind != "brand")
    return img


def _art_band(img, band: Box, art: Any) -> None:
    from PIL import Image, ImageDraw

    W, h = band[2] - band[0], band[3] - band[1]
    sh = 44
    shade = Image.new("L", (W, sh), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(sh):
        sd.line([(0, y), (W, y)], fill=int(150 * (1 - y / sh) ** 2))
    page = img.crop((0, h, W, h + sh))
    img.paste(Image.composite(Image.new("RGB", (W, sh), (0, 0, 0)), page, shade), (0, h))
    img.paste(cards.art_tile(art, W, h), (0, 0))
    mask = Image.new("L", (W, OPENING_ART_SCRIM_H), 0)
    md = ImageDraw.Draw(mask)
    hold = 0.45
    for y in range(OPENING_ART_SCRIM_H):
        u = min(1.0, max(0.0, (y / OPENING_ART_SCRIM_H - hold) / (1 - hold)))
        md.line([(0, y), (W, y)], fill=int(225 * (1 - u * u * (3 - 2 * u))))
    region = img.crop((0, 0, W, OPENING_ART_SCRIM_H))
    img.paste(Image.composite(Image.new("RGB", (W, OPENING_ART_SCRIM_H), (9, 14, 30)), region, mask), (0, 0))


# ── the lesson picture ────────────────────────────────────────────────────────


#: The lesson picture's step cards (mock_lesson.lesson): the gaps above and below them, between two, the text's
#: inset from a card's top and bottom, the gold step dot (radius, inset) and the rail's clearance from each dot.
LESSON_CARDS_TOP_GAP, LESSON_CARDS_BOTTOM_GAP, LESSON_CARD_GAP, LESSON_TEXT_PAD = 34, 34, 24, 40
LESSON_DOT_R, LESSON_DOT_X, LESSON_RAIL_W, LESSON_RAIL_CLEAR = 16, 34, 4, 10
#: The paragraphs' size search inside one ramp step (mock_lesson: `psize` from 46 down to 30, the largest at which
#: every block fits its card): from the step's LESSON_PARAGRAPH_STYLE size down to this (the style's floor at the
#: ramp's floor step, so the writer's maximum still fits).
LESSON_PARAGRAPH_MIN_PX = 30


def place_lesson_image(spec: Any, step: int, font_path: str, engine: str) -> CardLayout:
    """The lesson's 4:5 picture at `step` (lesson.jpg, mock_lesson.lesson): the brand row with LESSON_KICKER (the
    tile alone for a read-back from a web before news-v23 — `ImageSpec.look`), the title two-tone, then one warm
    card per paragraph — ALL THE SAME HEIGHT, filling the room from the title (+ 34) to the footer (− 34) — each
    paragraph centred in its card at the largest size (from the step's size down to LESSON_PARAGRAPH_MIN_PX) at
    which every paragraph fits its card, a gold dot centred on each card's left, the dots joined by gold rail
    segments that stop short of them; the footer."""
    frame = look.POST_FRAME
    page = look.Page(frame, step, font_path, engine)
    tile, pill = look.brand_row(page, look.LESSON_KICKER if spec.look else None, word=spec.look)
    foot_top = look.footer(page, spec.footer)
    x0, top, x1, _ = frame.area
    y = look.headline(page, "title", spec.title, LESSON_TITLE_STYLE, x0 + 8, top, x1 - x0 - 16)
    cards_top = y + _gap(page, LESSON_CARDS_TOP_GAP)
    bottom = foot_top - _gap(page, LESSON_CARDS_BOTTOM_GAP)
    n = len(spec.paragraphs)
    gap = _gap(page, LESSON_CARD_GAP)
    ch = (bottom - cards_top - (n - 1) * gap) / n
    pad = _gap(page, LESSON_TEXT_PAD)
    tx = x0 + LESSON_DOT_X + 2 * LESSON_DOT_R + 30
    text_w = x1 - 44 - tx
    room = ch - 2 * pad
    if room <= 0:
        raise _NoFit(f"post image: {n} paragraph cards leave no room under the title ({ch:.0f}px each)")
    start = page.px(LESSON_PARAGRAPH_STYLE)
    floor = min(start, LESSON_PARAGRAPH_MIN_PX if step < cards.RAMP_STEPS else LESSON_PARAGRAPH_STYLE.floor)
    texts: Optional[List[look.Text]] = None
    reason = ""
    for size in range(start, floor - 1, -1):
        try:
            trial = [page.measure(p, size, text_w, f"paragraph{i}", leading=LESSON_PARAGRAPH_STYLE.leading,
                                  max_lines=24) for i, p in enumerate(spec.paragraphs)]
        except _NoFit as e:
            reason = str(e)
            continue
        tallest = max(t.height for t in trial)
        if tallest <= room:
            texts = trial
            break
        reason = f"post image: a paragraph is {tallest}px tall at {size}px, its card holds {room:.0f}px"
    if texts is None:
        raise _NoFit(reason or "post image: the paragraphs do not fit their cards")
    dots: List[float] = []
    for i, t in enumerate(texts):
        c_top = cards_top + i * (ch + gap)
        box = look.card(page, (x0, c_top, x1, c_top + ch))
        mid = c_top + ch / 2
        page.put(f"paragraph{i}", t, tx, mid - t.height / 2, "la", look.INK, box)
        dots.append(mid)
    dcx = x0 + LESSON_DOT_X + LESSON_DOT_R
    r, clear, half = LESSON_DOT_R, LESSON_RAIL_CLEAR, LESSON_RAIL_W // 2
    for a, b in zip(dots, dots[1:]):
        page.rects.append(look.Rect((dcx - half, int(round(a + r + clear)), dcx + half, int(round(b - r - clear))), 0,
                                    LESSON_RAIL))
    for d in dots:
        page.ellipse((dcx - r, d - r, dcx + r, d + r), LESSON_RAIL)
    page.check()
    panel: Box = (x0, top, x1, bottom)
    return CardLayout(kind="post_image", step=step, engine=engine, panel=panel, lines=tuple(page.lines),
                      rects=tuple(page.rects), ellipses=tuple(page.ellipses), brand=tile, pill=pill,
                      footer_top=foot_top)


def draw_lesson_image(layout: CardLayout, font_path: str):
    img = look.canvas(look.POST_FRAME)
    look.draw_page(img, layout, look.POST_FRAME, font_path, layout.engine)
    return img
