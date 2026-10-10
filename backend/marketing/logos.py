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
from typing import Any, Callable, Dict, List, Mapping, Optional

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
    would draw an empty plate and no wordmark). Any Pillow error (a truncated file, a bomb warning) is a
    problem, never a raise."""
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
    except Exception as e:  # noqa: BLE001 — any decode failure is a wordmark, reported by type
        return f"{type(e).__name__}: {str(e)[:120]}"
    return None


def visible_pixels(rgba: Any) -> int:
    """How many pixels of the RGBA image `rgba` visibly differ from the plate once composited onto it
    exactly as cards.draw_plate does (alpha over PLATE_RGB): those whose darkest channel is below
    VISIBLE_CHANNEL_BELOW. Linear: two channel-wise minimums and one histogram, no Python pixel loop."""
    from PIL import Image, ImageChops

    plate = Image.new("RGBA", rgba.size, PLATE_RGB + (255,))
    plate.alpha_composite(rgba)
    r, g, b, _a = plate.split()
    darkest = ImageChops.darker(ImageChops.darker(r, g), b)
    return sum(darkest.histogram()[:VISIBLE_CHANNEL_BELOW])


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
