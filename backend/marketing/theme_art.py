"""
The weekly theme post's quiet DECORATION (news-v23 phase 3, 2026-11-20): a text-free gold drawing in the picture's
top-right glow, one per theme (the spec's `icon` token, `cards.THEME_ICONS`), ported line for line from the approved
mocks (out/marketing_v2_preview/FINAL_REFERENCE/scripts): the microchip of mock_theme.py (theme_ai_chips.jpg), the bolt
of mock_theme_biggest.py (theme_biggest_power.jpg), the wafer of mock_theme_growth_profit.py, the shield of
mock_theme_insiders.py and the radar of mock_theme_congress.py — "beside the title when the title is short, beside the
sub lines when the title runs full width" (theme_family.py).

It draws no string, holds no data and never reaches the server's allow-list: a decoration only. `extent` gives a
drawing's outer box relative to its anchor, so the placer (`news_layouts`) keeps it clear of every line of text; `draw`
composites it onto the canvas before anything else is drawn (as the mocks do). Deterministic: the same kind, anchor and
size give the same pixels (Pillow's GaussianBlur and LANCZOS are). Pillow is imported inside the functions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

#: The kit's gold (style_kit.GOLD == look.GOLD).
GOLD = (246, 196, 69)
Box = Tuple[float, float, float, float]


@dataclass(frozen=True)
class Icon:
    """One decoration: its kind (a `cards.THEME_ICONS` token), its anchor (cx, cy) on the canvas and its size (the mock's
    own size parameter: the chip's and bolt's `s`, the wafer's and radar's radius, the shield's width)."""

    kind: str
    cx: float
    cy: float
    size: int

    @property
    def box(self) -> Box:
        x0, y0, x1, y1 = extent(self.kind, self.size)
        return self.cx + x0, self.cy + y0, self.cx + x1, self.cy + y1


#: Each kind's size beside a short TITLE and beside the SUB lines (the mocks' own where they drew it there: the chip
#: 80, the wafer 56 and the shield 88 beside a short title; the bolt 56 and the radar 36 beside the sub lines; the other
#: placements scaled to the same box height).
SIZES = {"chip": (80, 56), "bolt": (76, 56), "wafer": (56, 40), "shield": (88, 62), "radar": (48, 36)}


def extent(kind: str, size: int) -> Box:
    """The drawing's outer box relative to its anchor (pins, traces and rims included) — the mocks' `*_extent`."""
    s = float(size)
    if kind == "chip":
        h, plen = s // 2, int(s * 0.16)
        return -h - plen, -h - plen, h + plen + 48, h + plen
    if kind == "bolt":
        r = int(s * 0.66)
        return -r - 3, -r - 3, r + 62 + 10, r + 3
    if kind == "wafer":
        return -s - 3, -s - 3, s + 3, s * 0.88 + 3
    if kind == "shield":
        h = s * 1.18
        return -s / 2 - 4, -h / 2 - 4, s / 2 + 68, h / 2 + 4
    if kind == "radar":
        e = s + 15 + 2
        return -e, -e, e, e
    raise ValueError(f"unknown theme icon {kind!r}")


def _composite(img, layer):
    from PIL import Image, ImageFilter

    glow = layer.filter(ImageFilter.GaussianBlur(6))
    base = Image.alpha_composite(img.convert("RGBA"), glow)
    return Image.alpha_composite(base, layer).convert("RGB")


def _chip(img, cx: float, cy: float, s: int):
    from PIL import Image, ImageDraw

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(layer)
    col, soft = GOLD + (150,), GOLD + (70,)
    h = s // 2
    g.rounded_rectangle([cx - h, cy - h, cx + h, cy + h], radius=s // 7, outline=col, width=5)
    q = int(s * 0.27)
    g.rounded_rectangle([cx - q, cy - q, cx + q, cy + q], radius=s // 14, outline=col, width=4)
    pins = 5
    step = s / (pins + 1)
    plen = int(s * 0.16)
    for i in range(1, pins + 1):
        o = int(-h + i * step)
        g.line([(cx + o, cy - h - plen), (cx + o, cy - h)], fill=col, width=5)
        g.line([(cx + o, cy + h), (cx + o, cy + h + plen)], fill=col, width=5)
        g.line([(cx - h - plen, cy + o), (cx - h, cy + o)], fill=col, width=5)
        g.line([(cx + h, cy + o), (cx + h + plen, cy + o)], fill=col, width=5)
    for i in (1, 3, 5):
        o = int(-h + i * step)
        g.line([(cx + h + plen, cy + o), (cx + h + plen + 40, cy + o)], fill=soft, width=3)
        g.ellipse([cx + h + plen + 38, cy + o - 5, cx + h + plen + 48, cy + o + 5], outline=soft, width=3)
    return _composite(img, layer)


def _bolt(img, cx: float, cy: float, s: int):
    from PIL import Image, ImageDraw

    r = int(s * 0.66)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(layer)
    col, soft = GOLD + (160,), GOLD + (70,)
    pts = [(0.14, -0.50), (-0.24, 0.06), (-0.02, 0.06), (-0.14, 0.50), (0.26, -0.08), (0.04, -0.08)]
    k = 1.05
    g.polygon([(cx + x * s * k, cy + y * s * k) for x, y in pts], fill=GOLD + (34,), outline=col, width=5)
    g.ellipse([cx - r, cy - r, cx + r, cy + r], outline=GOLD + (110,), width=4)
    for dy, length in ((-0.42, 46), (0.0, 62), (0.42, 46)):
        yy = cy + dy * s
        x_start = cx + math.sqrt(max(0.0, r * r - (dy * s) ** 2))
        g.line([(x_start + 2, yy), (x_start + length, yy)], fill=soft, width=3)
        g.ellipse([x_start + length - 1, yy - 5, x_start + length + 9, yy + 5], outline=soft, width=3)
    return _composite(img, layer)


def _wafer(img, cx: float, cy: float, r: int):
    from PIL import Image, ImageDraw

    flat_y = cy + r * 0.88
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(layer)
    col, die, die_hi = GOLD + (165,), GOLD + (58,), GOLD + (135,)
    s, gap = 10, 4
    step = s + gap
    n = int(r // step) + 1
    hi = {(-3, 1), (-2, 0), (-1, -1), (0, -2), (1, -3), (0, 0), (1, -1), (2, -2)}
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            x0 = cx + i * step - s / 2
            y0 = cy + j * step - s / 2
            corners = [(x0, y0), (x0 + s, y0), (x0, y0 + s), (x0 + s, y0 + s)]
            if all(math.hypot(px - cx, py - cy) <= r - 8 for px, py in corners) and y0 + s <= flat_y - 6:
                g.rounded_rectangle([x0, y0, x0 + s, y0 + s], radius=2, fill=die_hi if (i, j) in hi else die)
    g.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=5)
    g.rectangle([cx - r - 4, flat_y + 3, cx + r + 4, cy + r + 4], fill=(0, 0, 0, 0))
    half = math.sqrt(max(0.0, r * r - (flat_y - cy) ** 2))
    g.line([(cx - half + 1, flat_y), (cx + half - 1, flat_y)], fill=col, width=5)
    return _composite(img, layer)


def _bezier(p0, p1, p2, n: int = 24):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in (i / n for i in range(n + 1))]


def _shield_points(cx: float, top: float, w: float, h: float):
    hw = w / 2
    shoulder = top + h * 0.14
    side_end = top + h * 0.50
    tip = (cx, top + h)
    right = [(cx, top), (cx + hw * 0.5, top + h * 0.10), (cx + hw, shoulder), (cx + hw, side_end)]
    right += _bezier((cx + hw, side_end), (cx + hw, top + h * 0.82), tip)[1:]
    left = [(2 * cx - x, y) for (x, y) in reversed(right)]
    return right + left[1:]


def _shield(img, cx: float, cy: float, s: int, k: int = 4):
    from PIL import Image, ImageDraw

    w, h = float(s), s * 1.18
    top = cy - h / 2
    ex0, ey0, ex1, ey1 = (int(v) for v in (cx - w / 2 - 4, top - 4, cx + w / 2 + 68, top + h + 4))
    pad = 16
    ox, oy = ex0 - pad, ey0 - pad
    lw, lh = ex1 - ex0 + 2 * pad, ey1 - ey0 + 2 * pad
    big = Image.new("RGBA", (lw * k, lh * k), (0, 0, 0, 0))
    g = ImageDraw.Draw(big)

    def p(x: float, y: float):
        return ((x - ox) * k, (y - oy) * k)

    col, soft = GOLD + (150,), GOLD + (70,)
    outer = [p(*q) for q in _shield_points(cx, top, w, h)]
    g.line(outer + [outer[0], outer[1]], fill=col, width=5 * k, joint="curve")
    inner = [p(*q) for q in _shield_points(cx, top + h * 0.13, w * 0.70, h * 0.70)]
    g.line(inner + [inner[0], inner[1]], fill=soft, width=4 * k, joint="curve")
    ky = top + h * 0.40
    kr = s * 0.09
    (ax, ay), (bx, by) = p(cx - kr, ky - kr), p(cx + kr, ky + kr)
    g.ellipse([ax, ay, bx, by], outline=col, width=4 * k)
    g.line([p(cx - kr * 0.40, ky + kr * 0.95), p(cx - kr * 0.75, ky + kr * 2.6), p(cx + kr * 0.75, ky + kr * 2.6),
            p(cx + kr * 0.40, ky + kr * 0.95)], fill=col, width=4 * k, joint="curve")
    hw = w / 2
    for i, fy in enumerate((0.24, 0.38, 0.52)):
        y = top + h * fy
        x0 = cx + hw + 10
        x1 = x0 + 34 + (i % 2) * 14
        g.line([p(x0, y), p(x1, y)], fill=soft, width=3 * k)
        (qx, qy), (rx, ry) = p(x1 - 1, y - 5), p(x1 + 9, y + 5)
        g.ellipse([qx, qy, rx, ry], outline=soft, width=3 * k)
    small = big.resize((lw, lh), Image.LANCZOS)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    layer.paste(small, (ox, oy))
    return _composite(img, layer)


def _radar(img, cx: float, cy: float, r: int):
    from PIL import Image, ImageDraw

    tick0, tick1 = r + 6, r + 15
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g = ImageDraw.Draw(layer)
    col, soft, faint = GOLD + (150,), GOLD + (70,), GOLD + (34,)
    lead = -38
    for i, a in enumerate(range(lead - 70, lead, 2)):
        alpha = int(4 + 86 * (i / 35) ** 1.8)
        g.pieslice([cx - r, cy - r, cx + r, cy + r], a, a + 2, fill=GOLD + (alpha,))
    g.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=4)
    for kk in (0.66, 0.33):
        rr = int(r * kk)
        g.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=soft, width=3)
    g.line([(cx - r, cy), (cx + r, cy)], fill=faint, width=2)
    g.line([(cx, cy - r), (cx, cy + r)], fill=faint, width=2)
    ex, ey = cx + r * math.cos(math.radians(lead)), cy + r * math.sin(math.radians(lead))
    g.line([(cx, cy), (ex, ey)], fill=col, width=4)
    g.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=col)
    for deg in range(0, 360, 30):
        a = math.radians(deg)
        g.line([(cx + tick0 * math.cos(a), cy + tick0 * math.sin(a)),
                (cx + tick1 * math.cos(a), cy + tick1 * math.sin(a))], fill=soft, width=3)
    for ang, kk, sz in ((-62, 0.52, 6), (-88, 0.80, 5)):
        a = math.radians(ang)
        bx, by = cx + r * kk * math.cos(a), cy + r * kk * math.sin(a)
        g.ellipse([bx - sz, by - sz, bx + sz, by + sz], fill=GOLD + (190,))
    return _composite(img, layer)


_DRAW = {"chip": _chip, "bolt": _bolt, "wafer": _wafer, "shield": _shield, "radar": _radar}


def draw(img, icon: Icon):
    """`img` (RGB) with `icon` composited on it — a new image (the mocks' `f.art`)."""
    if icon.kind not in _DRAW:
        raise ValueError(f"unknown theme icon {icon.kind!r}")
    return _DRAW[icon.kind](img, icon.cx, icon.cy, icon.size)
