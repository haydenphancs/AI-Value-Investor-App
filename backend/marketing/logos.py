"""
The company LOGOS of a template (news) post — drop 2, contract D14 (the worker half of D10).

The web side fetched each logo, checked its header (`app/services/marketing/logo_check.py`) and
stored it, content-addressed and immutable, in the PUBLIC `marketing-media` bucket under
`logos/<sha256[:32]>.<png|jpg>`. The accepted script names them in `script["logos"]`:
`[{key, name, url|None, sha256|None, bytes|None, width|None, height|None}]` (≤ MAX_LOGOS). This
module turns that list into local files the cards can draw — or None for a logo that is not
there or does not verify, which the cards draw as a company-name WORDMARK tile instead.

Why each piece is shaped the way it is:

* **The URL is ours or it is not fetched.** It must be exactly
  `https://<bucket origin>/storage/v1/object/public/marketing-media/logos/<hex32>.<png|jpg>`, the
  hex equal to the entry's `sha256[:32]`. The bucket origin is not configured here (the worker
  holds no Supabase setting): it is the host of the run's own VERIFIED narration URL from the
  read-back (`bucket_origin`), else of another ready asset of the run (the preflight manifest — an
  image-only day has no narration). A logo URL on any other host, path, bucket or extension is
  never requested.
* **The bytes are the stored bytes.** Downloaded through `render.download` (no redirects, a hard
  byte cap of LOGO_MAX_BYTES, the web's own limit), then the FULL sha256 must equal the entry's —
  a mismatch is logged at ERROR (the object behind a content address changed, or the read-back
  lied) and the wordmark is drawn.
* **Decoded before it is drawn, bounded.** Pillow opens it (header only), the format must be the
  URL's (PNG ↔ .png, JPEG ↔ .jpg), one frame (an APNG is refused), each side
  LOGO_MIN_SIDE_PX..LOGO_MAX_SIDE_PX, aspect 0.25..4 and at most MAX_IMAGE_PIXELS (2048²) —
  checked from the header BEFORE any pixel is decoded, so a decompression bomb is refused without
  being inflated; then `verify()`, then a full decode of a fresh handle. A mode with samples wider
  than 8 bits (a 16-bit greyscale PNG opens as `I;16`) is refused too: Pillow's RGBA conversion
  CLIPS such samples instead of scaling them, so its greys would be drawn as white (`wide_sample_mode`).
* **A logo never ends the day.** Every failure is `None` for that key (a wordmark tile), logged
  with the key and the reason — ERROR for a sha mismatch, WARNING for anything else. Never a
  SkipRun, never an exception out of `resolve_logos`.
* **Visible on its plate.** The cards draw a logo unaltered on a WHITE plate (`cards.PLATE`). A
  white-on-transparent mark (a dark-theme logo), a fully transparent PNG or a white JPEG passes every
  header check and would draw an empty white square — with no company name either, since a verified
  logo draws no wordmark. So the decode composites it onto the plate's white and counts the pixels
  that visibly differ from it (darkest channel below VISIBLE_CHANNEL_BELOW, one linear histogram);
  under MIN_VISIBLE_FRACTION of the area it is refused, and the wordmark is drawn instead (WARNING).
* **Whole on its plate** (LIVE-LOGO-1, 2026-10-20). A logo made for a DARK background — white
  artwork plus one coloured accent, on real transparency — is visible on the white plate, but only
  its accent: FMP's GameStop drew as a lone red "S", Amazon's as a lone orange smile, on the 500 ms
  platform cover. Such a logo is refused (wordmark) when at least TRANSPARENT_SHARE_MIN of its
  pixels are transparent and the bounding box of its VISIBLE ink covers less than
  MIN_VISIBLE_BOX_SHARE of the bounding box of its solid pixels (alpha ≥ BOX_ALPHA_MIN, so a faint
  stray pixel, glow or shadow tail never widens that box — review RR-B2F-3: two alpha-20 corner
  pixels refused a fully visible disc; white artwork under half opacity is read as a glow too;
  `visible_box_share`). Measured on the 30 real logos of the 2026-10-20 live review: GME 0.49 and
  AMZN 0.27 are refused; the lowest kept transparent logo is BSX at 0.81. A logo with no real
  transparency is exempt: its ground is part of the design (ETN, ALMR and CBRS draw a small mark on
  their own white tile, 0.22-0.32). Accepted over-refusal: a white tile with a small mark on a
  transparent canvas reads as the same shape, and draws its wordmark. Known gap (a box test, not a
  shape test): white artwork whose coloured accents span its whole box — a white wordmark between a
  coloured underline and a dot, or inside a thin coloured ring — passes and draws only the accents;
  no real logo reviewed has that shape, and every template post is reviewed before it goes out.
* **No baked checkerboard** (LIVE-LOGO-2). An editor's "transparent" grid saved INTO the pixels —
  two light neutral greys alternating in squares (FMP's Boston Scientific: 254/237 in 6 px squares
  behind the name; the editors' standard light grid is 255/204, Photoshop's and GIMP's "light
  checks") — would be drawn as a chequer on every plate. Refused (wordmark) when most of the logo's
  light neutral pixels (darkest channel ≥ CHECKER_LIGHT_MIN = 200) that can be tested alternate on
  one fixed square side in BOTH directions (`checker_share`; conservative, and only tried when the
  two commonest light levels each hold a real share of them): on the 30 real logos BSX reaches 0.86
  and no other logo even qualifies for the test. A mid-tone grid (GIMP's default 153/102) is not
  light and is not caught.
* **Bounded time — a real wall clock.** All downloads share LOGOS_BUDGET_SECONDS (MAX_LOGOS ×
  LOGO_DOWNLOAD_TIMEOUT_SECONDS = 60 s). Each one is given a SHARE of at most half of what is left;
  httpx bounds each phase separately (connect, TLS, write, every read — never the whole request).
  Connect, TLS and the write are quick on a warm edge, so each is capped at share / DOWNLOAD_PHASES
  (1.25 s of a full 5 s share — `timeout`); every READ, the response headers included, gets the rest
  of the share (3.75 s — `read_timeout`), because the slow phase is a cold CDN fetching the freshly
  stored object from its origin before its first byte (review round 2: an even quarter, 1.25 s, turned
  a real logo into a wordmark on a 1.3 s first byte). `render.download` checks the elapsed time once
  the headers land and after every chunk, so one download ends by `worst_case_download_seconds(share)`
  = the share + one read = 1.75 × share ≤ 0.875 × what was left, inside the budget. A logo that would
  start with less than MIN_DOWNLOAD_SECONDS left is a wordmark. `render.worst_case_seconds` counts
  LOGOS_WORST_CASE_SECONDS (the budget + a connect cap's worth of headroom for the last logo's sha,
  decode and write, which run after its download).

Pure apart from Pillow (imported inside the function, like cards.py). Nothing here imports
app.* (tests/test_marketing_worker.py scans it); the limits mirror app.services.marketing.
logo_check and are pinned equal by tests/test_marketing_news_layouts.py.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import time
import warnings
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger("marketing.logos")

#: Mirrors app.services.marketing.logo_check (pinned equal by the tests): the web stores nothing
#: bigger, smaller or stranger, so anything else here is not the stored logo.
LOGO_MAX_BYTES = 512_000
LOGO_MIN_SIDE_PX = 100
LOGO_MAX_SIDE_PX = 2048
LOGO_MIN_ASPECT = 0.25
LOGO_MAX_ASPECT = 4.0
#: Mirrors app.services.marketing.template_onscreen.MAX_LOGOS / LOGO_KEY_MAX_CHARS.
MAX_LOGOS = 12
LOGO_KEY_MAX_CHARS = 16
LOGO_DOWNLOAD_TIMEOUT_SECONDS = 5
#: What every logo download of one stage may take together (the stage's worst-case share).
LOGOS_BUDGET_SECONDS = MAX_LOGOS * LOGO_DOWNLOAD_TIMEOUT_SECONDS
#: A download that would start with less than this left is not started (wordmark).
MIN_DOWNLOAD_SECONDS = 1.0
#: The phases httpx times SEPARATELY before a body byte arrives — connect, TLS start, the request
#: write, the header read. Connect, TLS and the write (and the pool wait, instant on a fresh client)
#: are each capped at share / DOWNLOAD_PHASES (`connect_seconds`); every read — the header read and
#: each body read — gets what is left of the share (`read_seconds`). `render.download` then checks
#: the elapsed time before the body and after every chunk (one read past the share, at most).
DOWNLOAD_PHASES = 4
#: A download's share is at most this fraction of what is left of the budget.
DOWNLOAD_SHARE_OF_LEFT = 0.5


def connect_seconds(share: float) -> float:
    """The cap on each of connect, TLS and the request write (and the pool wait) of a download given
    `share` seconds: a quarter of it — 1.25 s of a full share, never above the share."""
    return share / DOWNLOAD_PHASES


def read_seconds(share: float) -> float:
    """The cap on each READ of a download given `share` seconds (the response headers, then every
    body read): what is left of the share after the connect cap — 3.75 s of a full share."""
    return share - connect_seconds(share)


def worst_case_download_seconds(share: float) -> float:
    """The longest one download given `share` seconds can take. Before the body: pool + connect +
    TLS + write ≤ 4 × connect_seconds = the share, then the header read ≤ read_seconds — the
    elapsed check then ends it. In the body: the last elapsed check passed at most at the share, and
    one more read ≤ read_seconds runs before the next. Either way: share + read_seconds(share).
    (Not bounded, as before: DNS, and response HEADERS dripped a byte per read — the host is our
    own bucket origin.)"""
    return share + read_seconds(share)


#: The logo step's worst case, counted by `render.worst_case_seconds`: every download ends inside the
#: budget (1.75 × a share of at most half of what was left), plus a connect cap's worth of headroom
#: (a full share's, 1.25 s) for the last logo's sha, decode and file write, which run after its download.
LOGOS_WORST_CASE_SECONDS = LOGOS_BUDGET_SECONDS + LOGO_DOWNLOAD_TIMEOUT_SECONDS / DOWNLOAD_PHASES
#: The plate every logo is drawn on (mirrors marketing/cards.py PLATE = TEXT = #FFFFFF; pinned equal by
#: tests/test_marketing_news_layouts.py) and what counts as VISIBLE on it: a pixel, composited onto the
#: plate, whose darkest channel is below this (≈ 1.3:1 luminance contrast against white or more).
PLATE_RGB = (255, 255, 255)
VISIBLE_CHANNEL_BELOW = 225
#: A logo with fewer visible pixels than this fraction of its area draws as a blank plate: refused.
MIN_VISIBLE_FRACTION = 0.005
#: LIVE-LOGO-1 (module doc): a pixel is TRANSPARENT below this alpha (and opaque from it — a fainter one
#: composites to ≥ 240 on the plate, never visible); a logo with at least TRANSPARENT_SHARE_MIN of its
#: pixels transparent whose visible ink spans less than MIN_VISIBLE_BOX_SHARE of its SOLID box — the
#: bounding box of its pixels of alpha ≥ BOX_ALPHA_MIN, so a faint stray pixel, glow or shadow tail never
#: widens it (RR-B2F-3) — is refused.
OPAQUE_ALPHA_MIN = 16
BOX_ALPHA_MIN = 128
TRANSPARENT_SHARE_MIN = 0.30
MIN_VISIBLE_BOX_SHARE = 0.60
#: LIVE-LOGO-2 (module doc): a baked checkerboard's pixels are LIGHT (darkest channel ≥ CHECKER_LIGHT_MIN —
#: BSX's darker squares are 237, the editors' standard light grid's 204: RR-B2F-2), NEUTRAL (channels
#: within CHECKER_NEUTRAL_SPREAD) and fully opaque (alpha ≥ CHECKER_OPAQUE_MIN). The test runs only
#: when those pixels are at least CHECKER_AREA_SHARE_MIN of
#: the opaque ones and their two commonest levels, CHECKER_STEP_MIN or more apart, each hold
#: CHECKER_LEVEL_SHARE_MIN of them. A pixel ALTERNATES at square side s when the pixel s further on (all
#: three light) differs by ≥ CHECKER_STEP_MIN and the one 2·s on matches within CHECKER_SAME_MAX; the logo is
#: refused when, for one side s in CHECKER_SIDES, at least CHECKER_SHARE_MIN of the testable pixels
#: alternate in BOTH directions and they make up CHECKER_COVER_MIN of its light pixels — unless they already
#: alternate at a side of 1 px (a dither between two greys, which reads as one flat grey, not a grid).
CHECKER_LIGHT_MIN = 200
CHECKER_NEUTRAL_SPREAD = 3
CHECKER_OPAQUE_MIN = 250
CHECKER_AREA_SHARE_MIN = 0.25
CHECKER_LEVEL_SHARE_MIN = 0.10
CHECKER_STEP_MIN = 3
CHECKER_SAME_MAX = 1
CHECKER_SIDES: Tuple[int, ...] = tuple(range(2, 17))
CHECKER_SHARE_MIN = 0.60
CHECKER_COVER_MIN = 0.25
#: The decode bound: a logo is a small picture; Pillow's own bomb guard is ~89 M pixels.
MAX_IMAGE_PIXELS = 2048 * 2048
#: The public bucket every logo lives in (app.config MARKETING_MEDIA_BUCKET's default; the
#: worker never reads the web's settings, and the bucket is fixed by contract D14).
MEDIA_BUCKET = "marketing-media"
_PUBLIC_PATH = f"/storage/v1/object/public/{MEDIA_BUCKET}/"
_EXT_FORMAT = {"png": "PNG", "jpg": "JPEG"}
#: A bucket origin: a DNS host (optionally with a port) — nothing that could smuggle a path,
#: credentials or a query into the pattern built from it.
_HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?(?::[0-9]{1,5})?\Z")
_ORIGIN_RE = re.compile(r"https://([^/?#@\s]+)" + re.escape(_PUBLIC_PATH) + r"[^?#\s]+\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _origin_of(url: Any) -> Optional[str]:
    if not isinstance(url, str):
        return None
    m = _ORIGIN_RE.match(url)
    if not m or not _HOST_RE.match(m.group(1)):
        return None
    return m.group(1)


def bucket_origin(listing: Any) -> Optional[str]:
    """The host of the media bucket, read from the run's read-back (`GET /runs/{id}/assets`):
    the verified narration's public URL first, else the first ready asset whose public URL has
    the bucket's shape (an image-only day has no narration; the preflight manifest is always
    there). None — every logo is then a wordmark — when no URL has that shape (logged)."""
    if not isinstance(listing, Mapping):
        logger.warning("logos: the asset read-back is not an object — every logo is a wordmark")
        return None
    assets = [a for a in (listing.get("assets") or []) if isinstance(a, Mapping)]
    voice_id = listing.get("voice_asset_id")
    ordered = [a for a in assets if voice_id and a.get("id") == voice_id] + assets
    for asset in ordered:
        origin = _origin_of(asset.get("public_url"))
        if origin:
            return origin
    logger.warning("logos: no ready asset of the run has a public %s URL — cannot place the bucket, "
                   "every logo is a wordmark", MEDIA_BUCKET)
    return None


def logo_url_pattern(origin: str) -> "re.Pattern[str]":
    """The only logo URL shape fetched: `https://<origin>/storage/v1/object/public/marketing-media/
    logos/<32 lowercase hex>.<png|jpg>`, nothing before or after it."""
    return re.compile("https://" + re.escape(origin) + re.escape(_PUBLIC_PATH)
                      + r"logos/([0-9a-f]{32})\.(png|jpg)\Z")


def wide_sample_mode(mode: Any) -> bool:
    """Is `mode` a Pillow mode whose samples are wider than 8 bits — `I;16` / `I;16B` / `I;16L` / `I;16N`
    (a 16-bit grayscale PNG), `I` (32-bit int) or `F` (32-bit float)? `convert("RGBA")`, which
    cards.draw_plate runs, CLIPS those samples at 255 instead of scaling them (review R9: a 50%-grey
    0x8000 band of a 16-bit greyscale PNG drew as pure white), so the logo would be drawn altered —
    never unaltered, as the owner requires (2026-10-09 decision 3). Refused: the wordmark is drawn.
    (16-bit RGB and grey+alpha PNGs open as RGB / RGBA and convert correctly.)"""
    return isinstance(mode, str) and (mode in ("I", "F") or mode.startswith("I;"))


def decode_problem(data: bytes, ext: str) -> Optional[str]:
    """Why `data` is not a drawable logo of type `ext` ("png" | "jpg"), or None. The size limits
    are read from the HEADER before any pixel is decoded; then `verify()` and a full decode of a
    fresh handle; then the logo must be VISIBLE on the white plate (`visible_pixels` ≥
    MIN_VISIBLE_FRACTION of its area — review LOGO-1: a white-on-transparent, transparent or white logo
    would draw an empty plate and no wordmark), WHOLE on it (`visible_box_share` — LIVE-LOGO-1: a logo
    made for a dark background shows only its accent) and free of a baked checkerboard (`checker_share`
    — LIVE-LOGO-2). Any Pillow error (a truncated file, a bomb warning) is a problem, never a raise."""
    from PIL import Image

    want = _EXT_FORMAT.get(ext)
    if want is None:
        return f"unknown extension {ext!r}"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                fmt, (w, h) = im.format, im.size
                if fmt != want:
                    return f"a {fmt} file behind a .{ext} URL"
                if w <= 0 or h <= 0 or w * h > MAX_IMAGE_PIXELS:
                    return f"{w}x{h} is over {MAX_IMAGE_PIXELS} pixels"
                if not (LOGO_MIN_SIDE_PX <= w <= LOGO_MAX_SIDE_PX and LOGO_MIN_SIDE_PX <= h <= LOGO_MAX_SIDE_PX):
                    return f"{w}x{h} is outside {LOGO_MIN_SIDE_PX}..{LOGO_MAX_SIDE_PX} px a side"
                if not LOGO_MIN_ASPECT <= w / h <= LOGO_MAX_ASPECT:
                    return f"aspect {w}x{h} is outside {LOGO_MIN_ASPECT}..{LOGO_MAX_ASPECT}"
                if getattr(im, "n_frames", 1) != 1 or getattr(im, "is_animated", False):
                    return "an animated image"
                if wide_sample_mode(im.mode):
                    return (f"a {im.mode} logo (16/32-bit samples: drawing it would clip every sample "
                            "above 255 to white, not scale it — the logo would not be drawn unaltered)")
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                if im.size != (w, h):
                    return f"decoded {im.size}, header said {(w, h)}"
                rgba = im.convert("RGBA")   # what cards.draw_plate does: a mode it cannot is refused HERE
            visible = visible_pixels(rgba)
            if visible < MIN_VISIBLE_FRACTION * w * h:
                return (f"an invisible logo on the white plate ({visible} visible pixels of {w}x{h}; "
                        f"needs {MIN_VISIBLE_FRACTION:.1%})")
            box_share = visible_box_share(rgba)
            if box_share is not None and box_share < MIN_VISIBLE_BOX_SHARE:
                return (f"only part of the logo shows on the white plate (its visible ink spans {box_share:.0%} "
                        f"of its solid box, needs {MIN_VISIBLE_BOX_SHARE:.0%}: a logo made for a dark background)")
            checker = checker_share(rgba)
            if checker is not None:
                side, across, down = checker
                return (f"a baked checkerboard in the logo ({across:.0%} / {down:.0%} of its light pixels "
                        f"alternate in {side} px squares): it would be drawn on the plate")
    except Exception as e:  # noqa: BLE001 — any decode failure is a wordmark, reported by type
        return f"{type(e).__name__}: {str(e)[:120]}"
    return None


def _lut(keep: Callable[[int], bool]) -> List[int]:
    """A 256-entry lookup table for `Image.point` on an 8-bit band: 255 where `keep(value)`, else 0 (built
    once per call from the 256 values, never per pixel)."""
    return [255 if keep(v) else 0 for v in range(256)]


def _darkest_on_plate(rgba: Any) -> Any:
    """The darkest channel ("L") of `rgba` composited onto the plate exactly as cards.draw_plate does
    (alpha over PLATE_RGB)."""
    from PIL import Image, ImageChops

    plate = Image.new("RGBA", rgba.size, PLATE_RGB + (255,))
    plate.alpha_composite(rgba)
    r, g, b, _a = plate.split()
    return ImageChops.darker(ImageChops.darker(r, g), b)


def visible_pixels(rgba: Any) -> int:
    """How many pixels of the RGBA image `rgba` visibly differ from the plate once composited onto it
    exactly as cards.draw_plate does (alpha over PLATE_RGB): those whose darkest channel is below
    VISIBLE_CHANNEL_BELOW. Linear: two channel-wise minimums and one histogram, no Python pixel loop."""
    return sum(_darkest_on_plate(rgba).histogram()[:VISIBLE_CHANNEL_BELOW])


def _box_area(box: Any) -> int:
    return (box[2] - box[0]) * (box[3] - box[1])


def visible_box_share(rgba: Any) -> Optional[float]:
    """LIVE-LOGO-1: the area of the bounding box of the RGBA logo's VISIBLE ink on the plate (darkest
    channel below VISIBLE_CHANNEL_BELOW once composited, as `visible_pixels` counts it) over the area of the
    bounding box of its SOLID pixels (alpha ≥ BOX_ALPHA_MIN: a faint stray pixel, glow or shadow tail is not
    part of the logo's shape, and never widens the box — RR-B2F-3) — about 1.0 when everything solid shows
    (above 1.0 when visible anti-aliasing reaches past the solid pixels), low when white artwork made for a
    dark background vanishes into the plate around a coloured accent. None — exempt — for a logo with less
    than TRANSPARENT_SHARE_MIN of its pixels transparent (alpha below OPAQUE_ALPHA_MIN: its ground is part of
    the design) or with nothing solid or visible. Linear: two lookup-table passes and two bounding boxes
    (`Image.point` / `getbbox`), no Python pixel loop."""
    w, h = rgba.size
    alpha = rgba.getchannel("A")
    if sum(alpha.histogram()[:OPAQUE_ALPHA_MIN]) < TRANSPARENT_SHARE_MIN * w * h:
        return None
    solid_box = alpha.point(_lut(lambda v: v >= BOX_ALPHA_MIN)).getbbox()
    visible_box = _darkest_on_plate(rgba).point(_lut(lambda v: v < VISIBLE_CHANNEL_BELOW)).getbbox()
    if not solid_box or not visible_box:
        return None
    return _box_area(visible_box) / _box_area(solid_box)


def checker_share(rgba: Any) -> Optional[Tuple[int, float, float]]:
    """LIVE-LOGO-2: (square side px, share across, share down) when the RGBA logo carries a baked
    fake-transparency CHECKERBOARD — two light neutral greys alternating in squares — else None.

    Only LIGHT pixels count (darkest channel ≥ CHECKER_LIGHT_MIN, channels within CHECKER_NEUTRAL_SPREAD,
    alpha ≥ CHECKER_OPAQUE_MIN). The test runs only when they are at least CHECKER_AREA_SHARE_MIN of the
    opaque pixels and their two commonest levels are CHECKER_STEP_MIN or more apart and each hold
    CHECKER_LEVEL_SHARE_MIN of them — a white or flat light ground, a JPEG's near-white noise and an
    anti-aliased edge never qualify. A light pixel is TESTABLE at side s across when the pixels s and 2·s to
    its right are light too, and ALTERNATES when the first differs from it by ≥ CHECKER_STEP_MIN and the
    second matches it within CHECKER_SAME_MAX (down: the same, below it). The side with the highest share
    across (CHECKER_SIDES) is checked down too: a checkerboard alternates both ways, stripes one way only.
    A board needs ≥ CHECKER_SHARE_MIN of the testable pixels alternating BOTH ways, and those to be ≥
    CHECKER_COVER_MIN of all its light pixels; pixels that already alternate at a side of 1 px are a dither
    (it alternates at every odd side too), never a board. Linear image passes only (crop / difference /
    point / multiply / histogram — about 15 per side tried, len(CHECKER_SIDES) + 2 sides), no Python pixel
    loop: ≤ 12 ms on each of the 30 real logos, ~0.8 s for a 2048² one that reaches the test."""
    from PIL import Image, ImageChops

    r, g, b, a = rgba.split()
    darkest = ImageChops.darker(ImageChops.darker(r, g), b)
    spread = ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(r, g), b), darkest)
    light = ImageChops.multiply(
        ImageChops.multiply(darkest.point(_lut(lambda v: v >= CHECKER_LIGHT_MIN)),
                            spread.point(_lut(lambda v: v <= CHECKER_NEUTRAL_SPREAD))),
        a.point(_lut(lambda v: v >= CHECKER_OPAQUE_MIN)))
    n_light = light.histogram()[255]
    n_opaque = sum(a.histogram()[OPAQUE_ALPHA_MIN:])
    if not n_light or n_light < CHECKER_AREA_SHARE_MIN * n_opaque:
        return None
    levels = Image.composite(darkest, Image.new("L", rgba.size, 0), light).histogram()
    (_c1, v1), (c2, v2) = sorted(((levels[v], v) for v in range(CHECKER_LIGHT_MIN, 256)), reverse=True)[:2]
    if c2 < CHECKER_LEVEL_SHARE_MIN * n_light or abs(v1 - v2) < CHECKER_STEP_MIN:
        return None
    w, h = rgba.size
    flips, repeats = _lut(lambda v: v >= CHECKER_STEP_MIN), _lut(lambda v: v <= CHECKER_SAME_MAX)

    def alternating(side: int, across: bool) -> Tuple[int, int]:
        """(alternating pixels, testable pixels) at square side `side`, across or down."""
        n = (w if across else h) - 2 * side
        if n <= 0:
            return 0, 0
        boxes = [(k * side, 0, k * side + n, h) if across else (0, k * side, w, k * side + n) for k in range(3)]
        g0, g1, g2 = (darkest.crop(box) for box in boxes)
        m0, m1, m2 = (light.crop(box) for box in boxes)
        testable = ImageChops.multiply(ImageChops.multiply(m0, m1), m2)
        hits = ImageChops.multiply(testable, ImageChops.multiply(ImageChops.difference(g0, g1).point(flips),
                                                                 ImageChops.difference(g0, g2).point(repeats)))
        return hits.histogram()[255], testable.histogram()[255]

    hits_1, testable_1 = alternating(1, True)
    if testable_1 and hits_1 >= CHECKER_SHARE_MIN * testable_1:
        return None                     # a 1 px dither: one flat grey on the plate, not an editor's grid
    best: Optional[Tuple[float, int, int]] = None
    for side in CHECKER_SIDES:
        hits, testable = alternating(side, True)
        if testable and (best is None or hits / testable > best[0]):
            best = (hits / testable, side, hits)
    if best is None:
        return None
    across, side, hits_across = best
    hits_down, testable_down = alternating(side, False)
    down = hits_down / testable_down if testable_down else 0.0
    if (min(across, down) >= CHECKER_SHARE_MIN
            and min(hits_across, hits_down) >= CHECKER_COVER_MIN * n_light):
        return side, across, down
    return None


def _key_of(entry: Any) -> Optional[str]:
    if not isinstance(entry, Mapping):
        return None
    key = entry.get("key")
    if isinstance(key, str) and 1 <= len(key) <= LOGO_KEY_MAX_CHARS:
        return key
    return None


def resolve_logos(script: Mapping[str, Any], *, bucket_origin: Optional[str],
                  download: Callable[..., bytes], dest_dir: Path,
                  monotonic: Callable[[], float] = time.monotonic) -> Dict[str, Optional[Path]]:
    """{key: a verified local logo file, or None (draw the wordmark)} for every well-formed entry
    of `script["logos"]` (a key of 1..LOGO_KEY_MAX_CHARS characters, first entry of a key wins; at
    most MAX_LOGOS are fetched). `download(url, max_bytes=, timeout=, max_seconds=)` is
    `render.download` (tests inject a fake); files are written into `dest_dir` as
    `logo-<hex32>.<ext>`. Never raises for a logo: every failure is None + a log line."""
    raw = script.get("logos") if isinstance(script, Mapping) else None
    if raw is None:
        return {}
    if not isinstance(raw, list):
        logger.warning("logos: script.logos is a %s, not a list — no logo drawn", type(raw).__name__)
        return {}
    out: Dict[str, Optional[Path]] = {}
    entries: List[Mapping[str, Any]] = []
    for i, entry in enumerate(raw):
        key = _key_of(entry)
        if key is None:
            logger.warning("logos: script.logos[%d] has no usable key — skipped", i)
            continue
        if key in out:
            continue
        out[key] = None
        entries.append(entry)  # type: ignore[arg-type]
    pattern = logo_url_pattern(bucket_origin) if bucket_origin else None
    deadline = monotonic() + LOGOS_BUDGET_SECONDS
    fetched = 0
    for entry in entries:
        key = str(entry["key"])
        url = entry.get("url")
        if url is None:
            logger.info("logos key=%s: no stored logo — wordmark", key)
            continue
        if fetched >= MAX_LOGOS:
            logger.warning("logos key=%s: more than %d logos — wordmark", key, MAX_LOGOS)
            continue
        if pattern is None:
            logger.warning("logos key=%s: the bucket origin is unknown — wordmark", key)
            continue
        m = pattern.match(url) if isinstance(url, str) else None
        if m is None:
            logger.warning("logos key=%s: the logo URL is not a %s logos/ object of this bucket — "
                           "not fetched, wordmark", key, MEDIA_BUCKET)
            continue
        sha = entry.get("sha256")
        if not isinstance(sha, str) or not _SHA256_RE.match(sha):
            logger.warning("logos key=%s: the entry carries no sha256 — wordmark", key)
            continue
        hex32, ext = m.group(1), m.group(2)
        if hex32 != sha[:32]:
            logger.warning("logos key=%s: the URL's content address is not the entry's sha256 — wordmark", key)
            continue
        left = deadline - monotonic()
        if left < MIN_DOWNLOAD_SECONDS:
            logger.warning("logos key=%s: the %ds logo budget is spent — wordmark", key, LOGOS_BUDGET_SECONDS)
            continue
        fetched += 1
        # The wall-clock share of this download: connect / TLS / write get a quarter of it each, every
        # read (headers included) the rest — a cold CDN's slow first byte must fit (module doc).
        share = min(float(LOGO_DOWNLOAD_TIMEOUT_SECONDS), left * DOWNLOAD_SHARE_OF_LEFT)
        try:
            data = download(url, max_bytes=LOGO_MAX_BYTES, timeout=connect_seconds(share),
                            read_timeout=read_seconds(share), max_seconds=share)
        except Exception as e:  # noqa: BLE001 — a logo never ends the day
            logger.warning("logos key=%s: download failed (%s: %s) — wordmark", key, type(e).__name__, str(e)[:160])
            continue
        if not isinstance(data, (bytes, bytearray)) or not data or len(data) > LOGO_MAX_BYTES:
            logger.warning("logos key=%s: the download is not 1-%d bytes — wordmark", key, LOGO_MAX_BYTES)
            continue
        if hashlib.sha256(bytes(data)).hexdigest() != sha:
            logger.error("logos key=%s: the downloaded bytes do not match the stored sha256 %s… — the "
                         "object behind a content address changed; wordmark", key, sha[:12])
            continue
        problem = decode_problem(bytes(data), ext)
        if problem:
            logger.warning("logos key=%s: not a drawable logo (%s) — wordmark", key, problem)
            continue
        path = Path(dest_dir) / f"logo-{hex32}.{ext}"
        try:
            path.write_bytes(bytes(data))
        except OSError as e:
            logger.warning("logos key=%s: could not write the logo (%s: %s) — wordmark", key, type(e).__name__, e)
            continue
        out[key] = path
    drawn = sum(1 for p in out.values() if p is not None)
    logger.info("logos resolved: %d of %d drawn as logos, the rest as wordmarks", drawn, len(out))
    return out
