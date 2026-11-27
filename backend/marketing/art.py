"""
The per-post ART of a template (news) post — workstream C, owner decisions 2026-11-15 ("Google draws, Claude
checks"; "Picture and video cover"). The worker half of `app/services/marketing/post_art.py`.

The web side drew the picture, had it checked, and stored ONLY a picture that passed — content-addressed and
immutable, in the PUBLIC `marketing-media` bucket under `art/<sha256[:32]>.jpg` (no asset row: the logos'
precedent, rules marketing.md §4). While MARKETING_ART_ENABLED is on (read at call time on the web), the script
read-back carries it as `script["art"]` = `{url, sha256, bytes, width, height}` — never the models, the subject,
the checks or the usage. This module turns that entry into a local file the cards can draw (`cards.ArtImage`), or
None — and None is TODAY'S drawing: art is never required, never refuses, skips or fails a day.

Why each piece is shaped the way it is (it mirrors `logos.py`):

* **The URL is ours or it is not fetched.** It must be exactly
  `https://<bucket origin>/storage/v1/object/public/marketing-media/art/<hex32>.jpg`, the hex equal to the
  entry's `sha256[:32]`. The bucket origin is the run's own (`logos.bucket_origin`: the host of its VERIFIED
  narration URL, else of another ready asset of the run) — the worker holds no Supabase setting. Any other host,
  bucket, prefix (a `logos/` object included) or extension is never requested.
* **The entry is the web's own shape.** Its `bytes` 1..ART_MAX_BYTES, its width ART_MIN_WIDTH..ART_MAX_WIDTH and
  its height within ART_ASPECT_TOLERANCE of 16:9 — `post_art.art_problem`'s bounds, pinned equal by
  tests/test_marketing_news_layouts.py. Anything else is not the stored art, and is never fetched.
* **The bytes are the stored bytes.** Downloaded through `render.download` (no redirects, a hard byte cap of
  ART_MAX_BYTES, connect / TLS / write and every read bounded like a logo's), then the FULL sha256 must equal the
  entry's — a mismatch is logged at ERROR (the object behind a content address changed, or the read-back lied)
  and nothing is drawn — and the size must be the entry's.
* **Decoded before it is drawn, bounded.** Pillow reads the HEADER first: a JPEG, one frame, mode RGB or L, the
  entry's own width × height, at most MAX_IMAGE_PIXELS (2752 × 1548 — the widest picture the web stores at 16:9;
  its own bomb guard, NOT the logos' 2048²) — so a decompression bomb is refused without being inflated; then
  `verify()`, then a full decode of a fresh handle (a truncated body is refused).
* **Art never ends the day.** Every failure is None (today's drawing), logged with the reason — ERROR for a sha
  mismatch, WARNING for anything else. Never a SkipRun, never an exception out of `resolve_art`.
* **Bounded time.** One download, ART_DOWNLOAD_TIMEOUT_SECONDS of share (`logos.connect_seconds` /
  `logos.read_seconds`), so it ends by `logos.worst_case_download_seconds(share)`; `render.worst_case_seconds`
  counts ART_WORST_CASE_SECONDS (that, plus a second for the sha, the decode and the write).

Pure apart from Pillow (imported inside the functions). Nothing here imports app.* (tests/test_marketing_worker.py
scans it).
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import warnings
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from marketing import cards, logos

logger = logging.getLogger("marketing.art")

#: Mirrors app.services.marketing.post_art (pinned equal by tests/test_marketing_news_layouts.py): what the web
#: stores and what its `art_problem` lets the read-back carry — anything else is not the stored art.
ART_PREFIX = "art"
ART_EXT = "jpg"
ART_CONTENT_TYPE = "image/jpeg"
ART_MAX_BYTES = 1_900_000
ART_MIN_WIDTH = 1024
ART_MAX_WIDTH = 2752
ART_ASPECT = 16 / 9
ART_ASPECT_TOLERANCE = 0.01          # |w/h − 16/9|
#: The decode bound, sized for the widest art the web can store (ART_MAX_WIDTH at 16:9: 2752 × 1548) — this
#: module's own guard; Pillow's is ~89 M pixels, and the logos' (2048²) is too small for it.
MAX_IMAGE_PIXELS = 2752 * 1548
#: The JPEG modes the web's normalisation writes (`style.save_master`: RGB) — and greyscale, drawn as RGB.
DRAWABLE_MODES = ("RGB", "L")
#: The download's share of the stage (connect / TLS / write get a quarter each, every read the rest — logos.py).
ART_DOWNLOAD_TIMEOUT_SECONDS = 4
#: The art step's worst case, counted by `render.worst_case_seconds`: the download (its share + one read past
#: it) plus a second of headroom for the sha, the decode and the file write that run after it.
ART_WORST_CASE_SECONDS = logos.worst_case_download_seconds(float(ART_DOWNLOAD_TIMEOUT_SECONDS)) + 1.0
#: The public bucket the art lives in (the logos' — fixed by contract, never read from the web's settings).
MEDIA_BUCKET = logos.MEDIA_BUCKET
_PUBLIC_PATH = f"/storage/v1/object/public/{MEDIA_BUCKET}/"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def art_url_pattern(origin: str) -> "re.Pattern[str]":
    """The only art URL shape fetched: `https://<origin>/storage/v1/object/public/marketing-media/art/<32
    lowercase hex>.jpg`, nothing before or after it."""
    return re.compile("https://" + re.escape(origin) + re.escape(_PUBLIC_PATH)
                      + re.escape(ART_PREFIX) + r"/([0-9a-f]{32})\." + re.escape(ART_EXT) + r"\Z")


def _plain_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def entry_problem(entry: Any) -> Optional[str]:
    """Why the read-back's `art` entry is not one the web could have sent (post_art.art_problem's bounds), or
    None: an object with a url string, a 64-hex sha256, bytes 1..ART_MAX_BYTES, a width ART_MIN_WIDTH..
    ART_MAX_WIDTH and a height within ART_ASPECT_TOLERANCE of 16:9. Pure."""
    if not isinstance(entry, Mapping):
        return f"not an object ({type(entry).__name__})"
    if not isinstance(entry.get("url"), str):
        return "no url"
    sha = entry.get("sha256")
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        return "no sha256"
    size, width, height = _plain_int(entry.get("bytes")), _plain_int(entry.get("width")), _plain_int(entry.get("height"))
    if size is None or not 1 <= size <= ART_MAX_BYTES:
        return f"bytes {str(entry.get('bytes'))[:20]} outside 1..{ART_MAX_BYTES}"
    if width is None or not ART_MIN_WIDTH <= width <= ART_MAX_WIDTH:
        return f"width {str(entry.get('width'))[:20]} outside {ART_MIN_WIDTH}..{ART_MAX_WIDTH}"
    if height is None or height < 1 or abs(width / height - ART_ASPECT) > ART_ASPECT_TOLERANCE:
        return f"{width}x{str(entry.get('height'))[:20]} is not 16:9"
    return None


def decode_problem(data: bytes, *, width: int, height: int) -> Optional[str]:
    """Why `data` is not the drawable art the entry describes (`width` × `height`), or None. The format, the frame
    count, the mode and the size are read from the HEADER before any pixel is decoded — over MAX_IMAGE_PIXELS is
    refused uninflated (a decompression bomb); then `verify()` and a full decode of a fresh handle. Any Pillow
    error (a truncated file, a bomb warning) is a problem, never a raise."""
    from PIL import Image

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                fmt, (w, h), mode = im.format, im.size, im.mode
                if fmt != "JPEG":
                    return f"a {fmt} file behind a .{ART_EXT} URL"
                if w <= 0 or h <= 0 or w * h > MAX_IMAGE_PIXELS:
                    return f"{w}x{h} is over {MAX_IMAGE_PIXELS} pixels"
                if (w, h) != (width, height):
                    return f"{w}x{h}, but the read-back says {width}x{height}"
                if getattr(im, "n_frames", 1) != 1 or getattr(im, "is_animated", False):
                    return "more than one frame"
                if mode not in DRAWABLE_MODES:
                    return f"a {mode} JPEG (only {'/'.join(DRAWABLE_MODES)} is drawn)"
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                if im.size != (w, h):
                    return f"decoded {im.size}, header said {(w, h)}"
    except Exception as e:  # noqa: BLE001 — any decode failure is "no art", reported by type
        return f"{type(e).__name__}: {str(e)[:120]}"
    return None


def resolve_art(script: Any, *, bucket_origin: Optional[str], download: Callable[..., bytes],
                dest_dir: Path) -> Optional["cards.ArtImage"]:
    """The read-back's `art` as a VERIFIED local file the cards can draw (`cards.ArtImage`), or None — today's
    drawing. None silently when the script carries no art (the normal case: a lesson, the switch off, a record
    with no art); every other None is logged with the reason (module docstring). `download(url, max_bytes=,
    timeout=, read_timeout=, max_seconds=)` is `render.download` (tests inject a fake); the file is written into
    `dest_dir` as `art-<hex32>.jpg`. Never raises."""
    try:
        return _resolve(script, bucket_origin=bucket_origin, download=download, dest_dir=dest_dir)
    except Exception as e:  # noqa: BLE001 — art never ends the day: a bug here is logged with its stack, no art
        logger.exception("art: resolving the art failed unexpectedly (%s: %s) — no art, today's drawing",
                         type(e).__name__, str(e)[:160])
        return None


def _resolve(script: Any, *, bucket_origin: Optional[str], download: Callable[..., bytes],
             dest_dir: Path) -> Optional["cards.ArtImage"]:
    entry = script.get("art") if isinstance(script, Mapping) else None
    if entry is None:
        return None
    problem = entry_problem(entry)
    if problem is not None:
        logger.warning("art: the read-back's art entry is not the web's shape (%s) — not fetched, today's drawing",
                       problem)
        return None
    url, sha = entry["url"], entry["sha256"]
    size, width, height = entry["bytes"], entry["width"], entry["height"]
    if not bucket_origin:
        logger.warning("art sha=%s…: the bucket origin is unknown — not fetched, today's drawing", sha[:12])
        return None
    m = art_url_pattern(bucket_origin).match(url)
    if m is None:
        logger.warning("art sha=%s…: the art URL is not a %s %s/ object of this run's bucket — not fetched, "
                       "today's drawing", sha[:12], MEDIA_BUCKET, ART_PREFIX)
        return None
    hex32 = m.group(1)
    if hex32 != sha[:32]:
        logger.warning("art sha=%s…: the URL's content address is not the entry's sha256 — not fetched, today's "
                       "drawing", sha[:12])
        return None
    share = float(ART_DOWNLOAD_TIMEOUT_SECONDS)
    try:
        data = download(url, max_bytes=ART_MAX_BYTES, timeout=logos.connect_seconds(share),
                        read_timeout=logos.read_seconds(share), max_seconds=share)
    except Exception as e:  # noqa: BLE001 — art never ends the day
        logger.warning("art sha=%s…: download failed (%s: %s) — today's drawing", sha[:12], type(e).__name__,
                       str(e)[:160])
        return None
    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > ART_MAX_BYTES:
        logger.warning("art sha=%s…: the download is not 1-%d bytes — today's drawing", sha[:12], ART_MAX_BYTES)
        return None
    data = bytes(data)
    if hashlib.sha256(data).hexdigest() != sha:
        logger.error("art sha=%s…: the downloaded bytes do not match the stored sha256 — the object behind a content "
                     "address changed, or the read-back lied; today's drawing", sha[:12])
        return None
    if len(data) != size:
        logger.warning("art sha=%s…: %d bytes, but the read-back says %d — today's drawing", sha[:12], len(data), size)
        return None
    problem = decode_problem(data, width=width, height=height)
    if problem is not None:
        logger.warning("art sha=%s…: not drawable art (%s) — today's drawing", sha[:12], problem)
        return None
    path = Path(dest_dir) / f"art-{hex32}.{ART_EXT}"
    try:
        path.write_bytes(data)
    except OSError as e:
        logger.warning("art sha=%s…: could not write the art (%s: %s) — today's drawing", sha[:12], type(e).__name__, e)
        return None
    logger.info("art resolved sha=%s… %dx%d bytes=%d", sha[:12], width, height, len(data))
    return cards.ArtImage(sha256=sha, path=str(path), width=width, height=height)
