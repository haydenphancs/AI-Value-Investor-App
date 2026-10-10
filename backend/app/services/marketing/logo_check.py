"""
Company-logo header checks for Company Weekly (contract D10, the pure half).

The adapter (`company_news_adapter.fetch_logo`) downloads a logo; this module decides whether
those bytes may be stored in the public `marketing-media` bucket and drawn, unaltered, on a
post. Pure stdlib (`struct`, `zlib`, `hashlib`): the web image has no Pillow, and nothing here
decodes pixels — the worker's Pillow decode (`backend/marketing/logos.py`) is the second check.

Accepted, and nothing else:

* **PNG** — the signature, then IHDR as the first chunk (length 13, a valid bit-depth /
  colour-type pair, compression 0, filter 0, interlace 0/1), every chunk's CRC, a PLTE before
  the first IDAT for a palette image, at least one IDAT and IEND last. An `acTL` chunk anywhere
  is an animated PNG (APNG) and is refused: a post shows one frame of it, never the logo.
* **JPEG** — SOI, then the segment walk must reach the first SOFn within the first 64 KB;
  only baseline / extended / progressive Huffman (SOF0-2) with 1 or 3 components; EOI at the
  end (trailing zero padding tolerated).

The declared content type must match the magic bytes (``image/png`` ↔ PNG, ``image/jpeg`` ↔
JPEG): WebP, SVG and GIF are refused whatever they claim. Size: ≤ `LOGO_MAX_BYTES`; each side
`LOGO_MIN_SIDE_PX` .. `LOGO_MAX_SIDE_PX`; aspect (width / height) 0.25 .. 4. A sha256 listed
in `KNOWN_PLACEHOLDER_SHA256` (the vendor's generic "no logo" image) is refused.

Any refusal is a `LogoRejected(reason)` with `reason` ∈ `LOGO_REJECT_REASONS`. The caller logs it
and draws the company-name wordmark instead: a logo never refuses a post.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass
from typing import Any, FrozenSet, Optional, Tuple

LOGO_MAX_BYTES = 512_000
LOGO_MIN_SIDE_PX = 100
LOGO_MAX_SIDE_PX = 2048
LOGO_MIN_ASPECT = 0.25
LOGO_MAX_ASPECT = 4.0
#: The SOFn marker must appear within this many bytes of a JPEG's start.
JPEG_SOF_SCAN_BYTES = 64 * 1024

#: sha256 hex digests of known placeholder images (a vendor's generic "no logo" tile). The
#: adapter also refuses a profile whose `defaultImage` is true; this set catches a placeholder
#: served without that flag. Add a digest when one is observed in the WARNING log.
KNOWN_PLACEHOLDER_SHA256: FrozenSet[str] = frozenset()

LOGO_REJECT_REASONS: FrozenSet[str] = frozenset({
    "logo_not_bytes", "logo_empty", "logo_too_large", "logo_bad_type", "logo_bad_magic",
    "logo_truncated", "logo_bad_png", "logo_bad_crc", "logo_animated", "logo_bad_jpeg",
    "logo_too_small", "logo_too_large_dims", "logo_bad_aspect", "logo_placeholder",
})

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
_CONTENT_TYPES = {"image/png": "png", "image/jpeg": "jpg"}
_EXT_TYPES = {"png": "image/png", "jpg": "image/jpeg"}
# JPEG markers with no length field: TEM and RST0-7.
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xD8)})
_JPEG_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
_JPEG_SOF_SUPPORTED = frozenset({0xC0, 0xC1, 0xC2})


class LogoRejected(ValueError):
    """The bytes are not a logo we may store and draw. ``reason`` ∈ `LOGO_REJECT_REASONS`."""

    def __init__(self, reason: str, detail: str = "") -> None:
        if reason not in LOGO_REJECT_REASONS:
            raise ValueError(f"unknown logo reject reason {reason!r}")
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class LogoInfo:
    ext: str        # "png" | "jpg"
    width: int
    height: int
    sha256: str     # hex digest of the exact bytes (never altered)

    @property
    def content_type(self) -> str:
        return _EXT_TYPES[self.ext]


def _declared_ext(content_type: Any) -> Optional[str]:
    if not isinstance(content_type, str):
        return None
    return _CONTENT_TYPES.get(content_type.split(";", 1)[0].strip().lower())


def _magic_ext(data: bytes) -> Optional[str]:
    if data.startswith(_PNG_SIGNATURE):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    return None


def _png_dims(data: bytes) -> Tuple[int, int]:
    pos = len(_PNG_SIGNATURE)
    first = True
    width = height = 0
    colour = -1
    saw_plte = saw_idat = saw_iend = False
    while pos < len(data):
        if pos + 12 > len(data):
            raise LogoRejected("logo_truncated", "chunk header past the end")
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        end = pos + 12 + length
        if length > 0x7FFFFFFF or end > len(data):
            raise LogoRejected("logo_truncated", f"chunk {ctype!r} past the end")
        if not all(65 <= b <= 90 or 97 <= b <= 122 for b in ctype):
            raise LogoRejected("logo_bad_png", "chunk type is not four letters")
        body = data[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length:end])
        if zlib.crc32(ctype + body) & 0xFFFFFFFF != crc:
            raise LogoRejected("logo_bad_crc", f"chunk {ctype.decode('ascii')}")
        if first:
            if ctype != b"IHDR" or length != 13:
                raise LogoRejected("logo_bad_png", "IHDR is not the first chunk")
            width, height, depth, colour, comp, filt, interlace = struct.unpack(">IIBBBBB", body)
            if width == 0 or height == 0 or width > 0x7FFFFFFF or height > 0x7FFFFFFF:
                raise LogoRejected("logo_bad_png", "zero or oversized dimension")
            if depth not in _PNG_DEPTHS.get(colour, ()):
                raise LogoRejected("logo_bad_png", f"colour type {colour} / bit depth {depth}")
            if comp != 0 or filt != 0 or interlace not in (0, 1):
                raise LogoRejected("logo_bad_png", "unknown compression, filter or interlace")
            first = False
        elif ctype == b"IHDR":
            raise LogoRejected("logo_bad_png", "a second IHDR")
        if ctype == b"acTL":
            raise LogoRejected("logo_animated", "APNG acTL chunk")
        if ctype == b"PLTE":
            saw_plte = True
        if ctype == b"IDAT":
            if colour == 3 and not saw_plte:
                raise LogoRejected("logo_bad_png", "palette image without PLTE before IDAT")
            saw_idat = True
        pos = end
        if ctype == b"IEND":
            saw_iend = True
            break
    if first:
        raise LogoRejected("logo_truncated", "no IHDR")
    if not saw_idat or not saw_iend:
        raise LogoRejected("logo_truncated", "no IDAT or no IEND")
    return width, height


def _jpeg_dims(data: bytes) -> Tuple[int, int]:
    if not data.rstrip(b"\x00").endswith(b"\xff\xd9"):
        raise LogoRejected("logo_truncated", "no EOI at the end")
    pos = 2
    limit = min(len(data), JPEG_SOF_SCAN_BYTES)
    while pos < limit:
        if data[pos] != 0xFF:
            raise LogoRejected("logo_bad_jpeg", f"expected a marker at byte {pos}")
        while pos < limit and data[pos] == 0xFF:   # fill bytes
            pos += 1
        if pos >= limit:
            break
        marker = data[pos]
        pos += 1
        if marker in _JPEG_STANDALONE:
            continue
        if marker in (0xD8, 0xD9, 0xDA, 0x00):
            raise LogoRejected("logo_bad_jpeg", f"marker {marker:#04x} before any SOFn")
        if pos + 2 > len(data):
            raise LogoRejected("logo_truncated", "segment length past the end")
        (seg_len,) = struct.unpack(">H", data[pos:pos + 2])
        if seg_len < 2 or pos + seg_len > len(data):
            raise LogoRejected("logo_truncated", f"segment {marker:#04x} past the end")
        if marker in _JPEG_SOF:
            if marker not in _JPEG_SOF_SUPPORTED:
                raise LogoRejected("logo_bad_jpeg", f"unsupported SOF{marker - 0xC0}")
            if seg_len < 8:
                raise LogoRejected("logo_bad_jpeg", "short SOF segment")
            _precision, height, width, components = struct.unpack(">BHHB", data[pos + 2:pos + 8])
            if components not in (1, 3):
                raise LogoRejected("logo_bad_jpeg", f"{components} components")
            if width == 0 or height == 0:
                raise LogoRejected("logo_bad_jpeg", "zero dimension")
            return width, height
        pos += seg_len
    raise LogoRejected("logo_bad_jpeg", f"no SOFn within the first {JPEG_SOF_SCAN_BYTES} bytes")


def inspect_logo(data: Any, content_type: Any) -> LogoInfo:
    """Validate logo bytes and their declared content type; `LogoInfo` or `LogoRejected`."""
    if not isinstance(data, (bytes, bytearray)):
        raise LogoRejected("logo_not_bytes", type(data).__name__)
    data = bytes(data)
    if not data:
        raise LogoRejected("logo_empty")
    if len(data) > LOGO_MAX_BYTES:
        raise LogoRejected("logo_too_large", f"{len(data)} bytes > {LOGO_MAX_BYTES}")
    declared = _declared_ext(content_type)
    if declared is None:
        raise LogoRejected("logo_bad_type", "content type is not image/png or image/jpeg")
    magic = _magic_ext(data)
    if magic != declared:
        raise LogoRejected("logo_bad_magic", f"declared {declared}, bytes are {magic or 'neither'}")
    width, height = _png_dims(data) if magic == "png" else _jpeg_dims(data)
    if min(width, height) < LOGO_MIN_SIDE_PX:
        raise LogoRejected("logo_too_small", f"{width}x{height}")
    if max(width, height) > LOGO_MAX_SIDE_PX:
        raise LogoRejected("logo_too_large_dims", f"{width}x{height}")
    aspect = width / height
    if not (LOGO_MIN_ASPECT <= aspect <= LOGO_MAX_ASPECT):
        raise LogoRejected("logo_bad_aspect", f"{width}x{height}")
    digest = hashlib.sha256(data).hexdigest()
    if digest in KNOWN_PLACEHOLDER_SHA256:
        raise LogoRejected("logo_placeholder", digest[:12])
    return LogoInfo(ext=magic, width=width, height=height, sha256=digest)


def logo_path(info: LogoInfo) -> str:
    """The content-addressed bucket path of a logo: ``logos/<sha256[:32]>.<png|jpg>``."""
    return f"logos/{info.sha256[:32]}.{info.ext}"


__all__ = [
    "LOGO_MAX_BYTES", "LOGO_MIN_SIDE_PX", "LOGO_MAX_SIDE_PX", "LOGO_MIN_ASPECT", "LOGO_MAX_ASPECT",
    "JPEG_SOF_SCAN_BYTES", "KNOWN_PLACEHOLDER_SHA256", "LOGO_REJECT_REASONS", "LogoRejected",
    "LogoInfo", "inspect_logo", "logo_path",
]
