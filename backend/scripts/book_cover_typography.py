"""
Shared typesetting + legibility helpers for the cover pipeline.

Generic on purpose — nothing here knows about books. Reuse this for any future
"generated art + composited type" content (Money Moves heroes, Journey cards,
theme art) rather than re-deriving it.

Everything in here exists because a naive version of it shipped a visible defect:

  balanced_wrap      greedy wrapping produced ragged blocks that read as a bug
  fit_title          per-item sizing; use uniform_cap when a SET must match
  uniform_cap        one size across a set — bound by the widest WORD, not the
                     longest string, since a word cannot break across lines
  grey_equiv         a coloured ink's grey equivalent; needed because a scrim
                     solves against one grey value and a blue accent's channel
                     average badly understates it
  scrim_to           darken a band until the WEAKEST ink clears a ratio. Bisects
                     on a bright PERCENTILE, not the mean — a mean-based check
                     reported 9.9:1 on a line that was unreadable because it sat
                     on sky far brighter than the mean. Also defocuses a BUSY
                     band: small type dies on visual noise at any contrast ratio,
                     and a veil scales mean and sd together so it can never fix it
  halo_under         a local dark halo behind specific glyphs. A band-level scrim
                     cannot see a single bright object behind one letter
  assert_spelling    strings are never retyped; a dropped word fails the build
"""
import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --------------------------------------------------------------------- fonts
_FACE_CACHE: dict = {}


def load_faces(font_dir, faces: dict):
    """Preflight every face once. Fails in milliseconds rather than after the
    first six composites, and NEVER falls back to ImageFont.load_default() —
    that is a 10px bitmap that would 'succeed' into a set of unusable images."""
    from pathlib import Path
    out = {}
    for name, filename in faces.items():
        p = Path(font_dir) / filename
        if not p.exists():
            raise SystemExit(f"missing font '{name}': {p}\nSee {Path(font_dir) / 'SOURCES.md'}")
        try:
            ImageFont.truetype(str(p), 20)
        except OSError as e:
            raise SystemExit(f"font '{name}' at {p} is unreadable: {e}") from e
        out[name] = str(p)
    return out


def face(paths, name, size, wght=None, wdth=None):
    key = (name, size, wght, wdth)
    if key in _FACE_CACHE:
        return _FACE_CACHE[key]
    f = ImageFont.truetype(paths[name], size)
    try:
        axes = [a["name"].decode() for a in f.get_variation_axes()]
    except OSError:
        _FACE_CACHE[key] = f
        return f                                   # static face
    vals = []
    for ax in axes:
        if ax == "Weight":
            vals.append(wght if wght is not None else 400)
        elif ax == "Width":
            vals.append(wdth if wdth is not None else 100)
        elif ax == "Optical size":
            vals.append(min(32, max(14, size)))
    if vals:
        f.set_variation_by_axes(vals)
    _FACE_CACHE[key] = f
    return f


def font_for_cap(paths, name, cap_px, wght=None, wdth=None):
    """Solve the font size whose cap height ('H') is ~cap_px. Sizing by cap height
    rather than by em keeps two different faces optically equal."""
    lo, hi = 4, 400
    best = face(paths, name, 12, wght, wdth)
    while lo <= hi:
        mid = (lo + hi) // 2
        f = face(paths, name, mid, wght, wdth)
        b = f.getbbox("H")
        h = b[3] - b[1]
        if h < cap_px:
            best, lo = f, mid + 1
        elif h > cap_px:
            hi = mid - 1
        else:
            return f
    return best


def cap_of(f):
    b = f.getbbox("H")
    return b[3] - b[1]


# ----------------------------------------------------------------- text layout
def text_w(draw, s, f, track=0.0):
    if not s:
        return 0
    return sum(draw.textlength(c, font=f) for c in s) + track * f.size * (len(s) - 1)


def draw_tracked(draw, xy, s, f, fill, track=0.0):
    x, y = xy
    for c in s:
        draw.text((x, y), c, font=f, fill=fill)
        x += draw.textlength(c, font=f) + track * f.size
    return x


def balanced_wrap(draw, words, f, max_w, track=0.0, max_lines=4):
    """Wrap minimising the variance of line widths. Greedy wrapping on a long title
    produces something like 'The Little Book that Still / Beats the / Market', which
    reads as a layout bug rather than a decision. Returns None if it cannot fit."""
    n = len(words)
    if n == 0:
        return []
    widths = {}
    for a in range(n):
        for b in range(a + 1, n + 1):
            widths[(a, b)] = text_w(draw, " ".join(words[a:b]), f, track)
    best = None
    INF = float("inf")
    for k in range(1, min(max_lines, n) + 1):
        dp = [[INF] * (k + 1) for _ in range(n + 1)]
        cut = [[0] * (k + 1) for _ in range(n + 1)]
        dp[0][0] = 0.0
        for j in range(1, k + 1):
            for i in range(1, n + 1):
                for a in range(j - 1, i):
                    w = widths[(a, i)]
                    if w > max_w or dp[a][j - 1] == INF:
                        continue
                    c = dp[a][j - 1] + (max_w - w) ** 2
                    if c < dp[i][j]:
                        dp[i][j], cut[i][j] = c, a
        if dp[n][k] == INF:
            continue
        lines, i = [], n
        for j in range(k, 0, -1):
            a = cut[i][j]
            lines.append(" ".join(words[a:i]))
            i = a
        lines.reverse()
        if best is None or dp[n][k] < best[0]:
            best = (dp[n][k], lines)
    return best[1] if best else None


def uniform_cap(draw, strings, paths, name, max_w, wght=700,
                max_lines=5, track=0.0, cap_hi=80, cap_lo=10):
    """ONE cap height that every string in the set clears.

    Use this whenever a SET must look like a set. Per-item fitting is correct
    typography for one item and wrong for a shelf — short titles come out large and
    long ones small.

    The ceiling is the widest single WORD, not the longest string, because a word
    cannot be broken. On the book set that word is 'PSYCHOLOGY' (ten round letters),
    not the longer-looking 'INTELLIGENT' (four narrow ones)."""
    lo, hi, best = cap_lo, cap_hi, cap_lo
    while lo <= hi:
        mid = (lo + hi) // 2
        f = font_for_cap(paths, name, mid, wght)
        if all(balanced_wrap(draw, s.split(), f, max_w, track, max_lines) for s in strings):
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def fit_text(draw, s, paths, name, max_w, cap_px, wght, track, min_cap=9):
    """Shrink tracking first, then cap, until a single line fits. Never returns
    something that overflows — a clipped byline reading 'WARREN BUFFETT & LAWR' is
    how the set loses an author."""
    cap = cap_px
    while cap >= min_cap:
        f = font_for_cap(paths, name, cap, wght)
        for tr in (track, track * 0.6, track * 0.3, 0.0):
            if text_w(draw, s, f, tr) <= max_w:
                return f, tr
        cap -= 1
    return font_for_cap(paths, name, min_cap, wght), 0.0


def assert_spelling(parts, expected):
    """The strings are never retyped — they come from the app's own data. This guard
    catches a dropped word, a reorder or a typo before a glyph is drawn."""
    got = "".join(parts).replace(" ", "").replace("’", "'").upper()
    want = expected.replace(" ", "").replace("’", "'").upper()
    if got != want:
        raise RuntimeError(f"SPELLING GUARD: composed {got!r} != expected {want!r}")


# ------------------------------------------------------------------ legibility
def rel_l(v):
    c = v / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    return 0.2126 * rel_l(rgb[0]) + 0.7152 * rel_l(rgb[1]) + 0.0722 * rel_l(rgb[2])


def contrast(l1, l2):
    a, b = max(l1, l2), min(l1, l2)
    return (a + 0.05) / (b + 0.05)


def grey_equiv(rgb):
    """The grey level with the same relative luminance as `rgb`. A scrim bisects
    against one grey value, so a coloured ink must be converted first — a raw channel
    average understates a blue accent badly."""
    L = luminance(rgb)
    c = L * 12.92 if L <= 0.0031308 else 1.055 * (L ** (1 / 2.4)) - 0.055
    return max(0.0, min(255.0, c * 255.0))


def band_stats(im, y0):
    W, H = im.size
    band = sorted(im.convert("L").crop((0, y0, W, H)).getdata())
    mean = sum(band) / len(band)
    sd = math.sqrt(sum((v - mean) ** 2 for v in band) / len(band))
    return band, mean, sd


def _ramp(size, y0, frac, peak=255):
    W, H = size
    m = Image.new("L", (1, H), 0)
    p = m.load()
    r = max(1, int(H * frac))
    for y in range(H):
        if y < y0 - r:
            p[0, y] = 0
        elif y < y0:
            p[0, y] = int(peak * ((y - (y0 - r)) / r) ** 1.5)
        else:
            p[0, y] = peak
    return m.resize((W, H))


def scrim_to(im, top_y, ink_luma=236.0, target_ratio=7.0, pad=0, sd_ceil_at_480=22.0):
    """Darken from `top_y` down until ink at `ink_luma` clears `target_ratio`.

    TWO failure modes, and only one of them is brightness:
      too BRIGHT -> no contrast          -> darken
      too BUSY   -> glyphs compete       -> defocus
    A cover once measured 9.9:1 and was still unreadable: mean luma was fine, sd was
    42, and the line sat on sky far brighter than the mean. So this bisects on a
    bright PERCENTILE, and separately softens a band whose variance is too high — a
    veil scales mean and sd together and can never fix busy on its own.

    Pass `ink_luma` for the WEAKEST ink on the artwork, not the brightest.
    """
    W, H = im.size
    y0 = max(0, top_y - pad)
    if y0 >= H:
        return im
    ink = rel_l(ink_luma)
    band, mean, sd = band_stats(im, y0)
    p85 = band[int(0.85 * (len(band) - 1))]

    sd_ceil = sd_ceil_at_480 * (W / 480.0)
    if sd > sd_ceil:
        over = min(1.0, (sd - sd_ceil) / (sd_ceil * 1.6))
        radius = (1.5 + 7.0 * over) * (W / 480.0)
        im = Image.composite(im.filter(ImageFilter.GaussianBlur(radius)), im,
                             _ramp((W, H), y0, 0.13))
        band, mean, sd = band_stats(im, y0)
        p85 = band[int(0.85 * (len(band) - 1))]

    lo, hi = 0.0, 0.90
    for _ in range(20):
        a = (lo + hi) / 2
        if contrast(ink, rel_l(p85 * (1 - a))) >= target_ratio:
            hi = a
        else:
            lo = a
    if hi <= 0.01:
        return im
    return Image.composite(Image.new("RGB", (W, H), (6, 8, 12)), im,
                           _ramp((W, H), y0, 0.16, int(255 * hi)))


def halo_under(base_rgba, glyph_layer, radius, passes=2, colour=(6, 8, 12, 235)):
    """A soft dark halo behind SPECIFIC glyphs.

    A band-level scrim equalises a whole strip but cannot see one bright object
    sitting behind a single letter — which is exactly what was left under one word on
    an otherwise-passing cover. This is local by construction, so it fixes hotspots
    no band measurement can detect."""
    size = base_rgba.size
    halo = Image.new("RGBA", size, (0, 0, 0, 0))
    halo.paste(colour, (0, 0) + size, glyph_layer.split()[3])
    halo = halo.filter(ImageFilter.GaussianBlur(radius))
    for _ in range(passes):
        base_rgba.alpha_composite(halo)
    return base_rgba


def crop_aspect(im, w_over_h):
    """Centre-crop to an exact aspect ratio."""
    w, h = im.size
    tw = round(h * w_over_h)
    if tw <= w:
        x = (w - tw) // 2
        return im.crop((x, 0, x + tw, h))
    th = round(w / w_over_h)
    y = (h - th) // 2
    return im.crop((0, y, w, y + th))


def sharpness(im, top_frac=0.55):
    """Edge energy over the subject area. Biased low on warm, low-contrast frames —
    treat it as a re-roll HINT and confirm by eye, not as a verdict."""
    W, H = im.size
    sub = im.convert("L").crop((0, 0, W, int(H * top_frac))).filter(ImageFilter.FIND_EDGES)
    px = sub.resize((96, 96)).getdata()
    return math.sqrt(sum(v * v for v in px) / len(px))


def lift(rgb, amount=0.30):
    """Brighten an accent for use as ink WITHOUT shifting its hue. Scaling channels
    (c*1.2+30) clips whichever channel is already high — on #E8A758 that clipped red
    and turned a warm amber into a school-bus yellow."""
    import colorsys
    r, g, b = (c / 255.0 for c in rgb)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return tuple(int(round(c * 255))
                 for c in colorsys.hls_to_rgb(h, min(0.92, l + (1.0 - l) * amount), s * 0.94))


def hexc(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
