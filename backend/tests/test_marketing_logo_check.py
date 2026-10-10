"""`logo_check` — the pure header checks a company logo passes before it is stored and drawn
(contract D10). The web image has no Pillow, so the module parses PNG / JPEG headers itself; these
tests build the bytes by hand (exact control over every field) and, where Pillow is installed in
the test venv, also feed it real encoder output so the hand parser is checked against reality.

Pinned: the header table (signature/IHDR/CRC/PLTE/IDAT/IEND; SOI → SOFn within 64 KB → EOI),
APNG refusal, declared type == magic bytes (WebP / SVG / GIF refused), byte and pixel limits with
their exact boundaries, the aspect band, the placeholder digest list, and the bucket path.
"""

import hashlib
import importlib.util
import io
import struct
import zlib

import pytest

from app.services.marketing import logo_check as L


# ── hand-built images ─────────────────────────────────────────────────────────

def _chunk(ctype: bytes, body: bytes, *, bad_crc: bool = False) -> bytes:
    crc = zlib.crc32(ctype + body) & 0xFFFFFFFF
    if bad_crc:
        crc ^= 1
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", crc)


def png(w=200, h=200, *, colour=6, depth=8, before_idat=(), after_idat=(), plte=None, idat=True, iend=True,
        ihdr=True, interlace=0, comp=0, bad_crc_on=None, trailing=b"") -> bytes:
    out = L._PNG_SIGNATURE
    if ihdr:
        out += _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, colour, comp, 0, interlace),
                      bad_crc=bad_crc_on == b"IHDR")
    if plte is None:
        plte = colour == 3
    if plte:
        out += _chunk(b"PLTE", b"\x00\x00\x00\xff\xff\xff")
    for ctype, body in before_idat:
        out += _chunk(ctype, body)
    if idat:
        out += _chunk(b"IDAT", zlib.compress(b"\x00" * 16), bad_crc=bad_crc_on == b"IDAT")
    for ctype, body in after_idat:
        out += _chunk(ctype, body)
    if iend:
        out += _chunk(b"IEND", b"")
    return out + trailing


def _seg(marker: int, body: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body


def jpeg(w=200, h=200, *, sof=0xC0, comps=3, pre=(), eoi=True, trailing=b"", sos_first=False) -> bytes:
    out = b"\xff\xd8"
    out += _seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    for marker, body in pre:
        out += _seg(marker, body)
    if sos_first:
        out += _seg(0xDA, b"\x01\x01\x00\x00\x3f\x00")
    out += _seg(sof, struct.pack(">BHHB", 8, h, w, comps) + b"\x01\x11\x00" * comps)
    out += _seg(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\x34\x56"
    if eoi:
        out += b"\xff\xd9"
    return out + trailing


# ── accepted ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("colour,depth", [(0, 1), (0, 8), (0, 16), (2, 8), (2, 16), (3, 1), (3, 8), (4, 8), (4, 16),
                                         (6, 8), (6, 16)])
def test_every_valid_png_colour_type_and_depth(colour, depth):
    info = L.inspect_logo(png(colour=colour, depth=depth), "image/png")
    assert (info.ext, info.width, info.height) == ("png", 200, 200)


def test_a_valid_png_and_its_info():
    data = png(300, 150, interlace=1, before_idat=[(b"tEXt", b"Title\x00Logo")], trailing=b"\x00junk")
    info = L.inspect_logo(data, "image/png; charset=binary")
    assert info == L.LogoInfo(ext="png", width=300, height=150, sha256=hashlib.sha256(data).hexdigest())
    assert info.content_type == "image/png"
    assert L.logo_path(info) == f"logos/{info.sha256[:32]}.png"
    assert L.inspect_logo(bytearray(data), "IMAGE/PNG").sha256 == info.sha256


@pytest.mark.parametrize("sof", [0xC0, 0xC1, 0xC2])
@pytest.mark.parametrize("comps", [1, 3])
def test_a_valid_jpeg(sof, comps):
    data = jpeg(400, 300, sof=sof, comps=comps, pre=[(0xE1, b"Exif\x00\x00" + b"x" * 100), (0xDB, b"\x00" * 65)])
    info = L.inspect_logo(data, "image/jpeg")
    assert (info.ext, info.width, info.height) == ("jpg", 400, 300)
    assert info.content_type == "image/jpeg"
    assert L.logo_path(info).endswith(".jpg") and L.logo_path(info).startswith("logos/")


def test_jpeg_fill_bytes_restart_markers_and_zero_padding_after_eoi():
    data = b"\xff\xd8" + b"\xff\xff" + _seg(0xE0, b"JFIF\x00") + b"\xff\xd0" + _seg(
        0xC0, struct.pack(">BHHB", 8, 120, 160, 3) + b"\x01\x11\x00" * 3) + b"\xff\xd9" + b"\x00" * 10
    info = L.inspect_logo(data, "image/jpeg")
    assert (info.width, info.height) == (160, 120)


# ── refused ───────────────────────────────────────────────────────────────────

def _reason(data, content_type="image/png"):
    with pytest.raises(L.LogoRejected) as exc:
        L.inspect_logo(data, content_type)
    assert exc.value.reason in L.LOGO_REJECT_REASONS
    return exc.value.reason


@pytest.mark.parametrize("data,ctype,reason", [
    ("not bytes", "image/png", "logo_not_bytes"),
    (None, "image/png", "logo_not_bytes"),
    (b"", "image/png", "logo_empty"),
    (png(), None, "logo_bad_type"),
    (png(), "image/webp", "logo_bad_type"),
    (png(), "image/svg+xml", "logo_bad_type"),
    (png(), "image/gif", "logo_bad_type"),
    (png(), "image/jpg", "logo_bad_type"),
    (png(), "application/octet-stream", "logo_bad_type"),
    (png(), "image/jpeg", "logo_bad_magic"),
    (jpeg(), "image/png", "logo_bad_magic"),
    (b"GIF89a" + b"\x00" * 100, "image/png", "logo_bad_magic"),
    (b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 100, "image/png", "logo_bad_magic"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/png", "logo_bad_magic"),
    (b"\x89PNG", "image/png", "logo_bad_magic"),
])
def test_type_and_magic(data, ctype, reason):
    assert _reason(data, ctype) == reason


def test_png_structure_refusals():
    assert _reason(png(ihdr=False)) == "logo_bad_png"                           # IDAT first
    assert _reason(png()[:40]) == "logo_truncated"                              # cut mid-chunk
    assert _reason(png(iend=False)) == "logo_truncated"
    assert _reason(png(idat=False)) == "logo_truncated"
    assert _reason(L._PNG_SIGNATURE) == "logo_truncated"
    assert _reason(png(bad_crc_on=b"IHDR")) == "logo_bad_crc"
    assert _reason(png(bad_crc_on=b"IDAT")) == "logo_bad_crc"
    assert _reason(png(colour=2, depth=4)) == "logo_bad_png"
    assert _reason(png(colour=5)) == "logo_bad_png"
    assert _reason(png(colour=3, plte=False)) == "logo_bad_png"
    assert _reason(png(comp=1)) == "logo_bad_png"
    assert _reason(png(interlace=2)) == "logo_bad_png"
    assert _reason(png(0, 200)) == "logo_bad_png"
    assert _reason(png(before_idat=[(b"IHDR", struct.pack(">IIBBBBB", 9, 9, 8, 6, 0, 0, 0))])) == "logo_bad_png"
    assert _reason(png(before_idat=[(b"t3Xt", b"x")])) == "logo_bad_png"
    huge_len = L._PNG_SIGNATURE + struct.pack(">I", 0xFFFFFFF0) + b"IHDR" + b"\x00" * 20
    assert _reason(huge_len) == "logo_truncated"


@pytest.mark.parametrize("where", ["before", "after"])
def test_an_animated_png_is_refused(where):
    actl = [(b"acTL", struct.pack(">II", 2, 0))]
    data = png(before_idat=actl) if where == "before" else png(after_idat=actl)
    assert _reason(data) == "logo_animated"


def test_jpeg_structure_refusals():
    assert _reason(jpeg(eoi=False), "image/jpeg") == "logo_truncated"
    assert _reason(jpeg(sof=0xC3), "image/jpeg") == "logo_bad_jpeg"          # lossless
    assert _reason(jpeg(sof=0xC9), "image/jpeg") == "logo_bad_jpeg"          # arithmetic
    assert _reason(jpeg(comps=4), "image/jpeg") == "logo_bad_jpeg"           # CMYK
    assert _reason(jpeg(comps=2), "image/jpeg") == "logo_bad_jpeg"
    assert _reason(jpeg(sos_first=True), "image/jpeg") == "logo_bad_jpeg"
    assert _reason(jpeg(0, 200), "image/jpeg") == "logo_bad_jpeg"
    # a SOF beyond the first 64 KB: two max-size APP segments push it out
    big = [(0xE2, b"x" * 65_000), (0xE3, b"y" * 1_000)]
    assert _reason(jpeg(pre=big), "image/jpeg") == "logo_bad_jpeg"
    # garbage between segments, a segment running past the end, no SOF at all
    assert _reason(b"\xff\xd8\x00\x00" + b"\xff\xd9", "image/jpeg") == "logo_bad_magic"
    assert _reason(b"\xff\xd8" + _seg(0xE0, b"JFIF") + b"\x00\x00" + b"\xff\xd9", "image/jpeg") == "logo_bad_jpeg"
    assert _reason(b"\xff\xd8\xff\xe0\xff\xf0" + b"\xff\xd9", "image/jpeg") == "logo_truncated"
    assert _reason(b"\xff\xd8" + _seg(0xE0, b"JFIF") + b"\xff\xd9", "image/jpeg") == "logo_bad_jpeg"


def test_the_sof_boundary_is_64_kb():
    # SOI (2) + JFIF segment (18) + one APP segment (4 + pad): the SOF marker byte sits at 25 + pad.
    pad = L.JPEG_SOF_SCAN_BYTES - 26                      # marker byte = the last byte inside the window
    inside = jpeg(pre=[(0xE2, b"p" * pad)])
    assert inside.index(b"\xff\xc0") + 1 == L.JPEG_SOF_SCAN_BYTES - 1
    assert L.inspect_logo(inside, "image/jpeg").width == 200
    outside = jpeg(pre=[(0xE2, b"p" * (pad + 1))])
    assert _reason(outside, "image/jpeg") == "logo_bad_jpeg"


# ── sizes and shapes (exact boundaries) ───────────────────────────────────────

@pytest.mark.parametrize("w,h,ok", [
    (100, 100, True), (99, 200, False), (200, 99, False), (2048, 2048, True), (2049, 600, False), (600, 2049, False),
    (100, 400, True), (400, 100, True), (100, 401, False), (401, 100, False), (2048, 512, True), (2048, 511, False),
])
def test_pixel_limits_and_aspect(w, h, ok):
    for data, ctype in ((png(w, h), "image/png"), (jpeg(w, h), "image/jpeg")):
        if ok:
            assert (L.inspect_logo(data, ctype).width, L.inspect_logo(data, ctype).height) == (w, h)
        else:
            assert _reason(data, ctype) in {"logo_too_small", "logo_too_large_dims", "logo_bad_aspect"}
    if not ok:
        expect = ("logo_too_small" if min(w, h) < L.LOGO_MIN_SIDE_PX else
                  "logo_too_large_dims" if max(w, h) > L.LOGO_MAX_SIDE_PX else "logo_bad_aspect")
        assert _reason(png(w, h)) == expect


def test_byte_limit_boundary():
    base = png()
    filler = L.LOGO_MAX_BYTES - len(base) - 12        # one tEXt chunk brings it to exactly the cap
    exact = png(before_idat=[(b"tEXt", b"x" * filler)])
    assert len(exact) == L.LOGO_MAX_BYTES
    assert L.inspect_logo(exact, "image/png").width == 200
    over = png(before_idat=[(b"tEXt", b"x" * (filler + 1))])
    assert _reason(over) == "logo_too_large"


def test_a_known_placeholder_is_refused(monkeypatch):
    data = png()
    digest = hashlib.sha256(data).hexdigest()
    assert L.inspect_logo(data, "image/png").sha256 == digest
    monkeypatch.setattr(L, "KNOWN_PLACEHOLDER_SHA256", frozenset({digest}))
    assert _reason(data) == "logo_placeholder"
    assert L.inspect_logo(png(201, 200), "image/png")      # any other image still passes


def test_the_limits_are_pinned():
    assert (L.LOGO_MAX_BYTES, L.LOGO_MIN_SIDE_PX, L.LOGO_MAX_SIDE_PX) == (512_000, 100, 2048)
    assert (L.LOGO_MIN_ASPECT, L.LOGO_MAX_ASPECT) == (0.25, 4.0)
    assert all(len(d) == 64 and int(d, 16) >= 0 for d in L.KNOWN_PLACEHOLDER_SHA256)


def test_logo_rejected_takes_known_reasons_only():
    with pytest.raises(ValueError):
        L.LogoRejected("made_up")
    e = L.LogoRejected("logo_empty", "detail")
    assert isinstance(e, ValueError) and e.reason == "logo_empty" and "detail" in str(e)


def test_the_module_is_stdlib_only():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(L))
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
    assert mods <= {"__future__", "hashlib", "struct", "zlib", "dataclasses", "typing"}, mods


# ── real encoder output (Pillow, when the test venv has it; the web image does not) ──

_HAS_PIL = importlib.util.find_spec("PIL") is not None
needs_pil = pytest.mark.skipif(not _HAS_PIL, reason="Pillow is not installed in this venv")
_FILL = {"RGBA": (10, 20, 30, 255), "RGB": (10, 20, 30), "L": 128, "P": 7, "1": 1, "LA": (128, 255),
         "CMYK": (10, 20, 30, 40)}


def _pil(fmt, mode="RGBA", size=(256, 128), **save):
    from PIL import Image
    img = Image.new(mode, size, _FILL[mode])
    buf = io.BytesIO()
    img.save(buf, fmt, **save)
    return buf.getvalue()


@needs_pil
@pytest.mark.parametrize("mode", ["RGBA", "RGB", "L", "P", "1", "LA"])
def test_real_pngs_pass(mode):
    data = _pil("PNG", mode)
    info = L.inspect_logo(data, "image/png")
    assert (info.width, info.height) == (256, 128)


@needs_pil
@pytest.mark.parametrize("mode,save", [("RGB", {}), ("RGB", {"progressive": True}), ("L", {}),
                                       ("RGB", {"optimize": True, "quality": 70})])
def test_real_jpegs_pass(mode, save):
    data = _pil("JPEG", mode, **save)
    info = L.inspect_logo(data, "image/jpeg")
    assert (info.width, info.height) == (256, 128)


@needs_pil
def test_a_real_cmyk_jpeg_is_refused():
    assert _reason(_pil("JPEG", "CMYK"), "image/jpeg") == "logo_bad_jpeg"


@needs_pil
def test_a_real_apng_is_refused():
    from PIL import Image
    frames = [Image.new("RGBA", (128, 128), (i * 40, 0, 0, 255)) for i in range(3)]
    buf = io.BytesIO()
    frames[0].save(buf, "PNG", save_all=True, append_images=frames[1:], duration=100, loop=0)
    assert b"acTL" in buf.getvalue()
    assert _reason(buf.getvalue()) == "logo_animated"


@needs_pil
@pytest.mark.parametrize("fmt,ctype", [("GIF", "image/gif"), ("WEBP", "image/webp"), ("GIF", "image/png"),
                                       ("WEBP", "image/jpeg")])
def test_real_gif_and_webp_are_refused(fmt, ctype):
    try:
        data = _pil(fmt, "RGB")
    except (KeyError, OSError):
        pytest.skip(f"Pillow built without {fmt}")
    assert _reason(data, ctype) in {"logo_bad_type", "logo_bad_magic"}
