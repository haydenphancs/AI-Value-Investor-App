"""
The `rendered` and `assets_ready` stages (Phase 4, SYSTEM_DESIGN_GUIDELINES §12.8): turn the day's
accepted script and its narration into ONE 9:16 MP4 — and, since drop 1 (2026-10-09), its
`image_post` into ONE 4:5 post image — then record the day's posts.

Why each piece is shaped the way it is:

* **The narration comes back from the server**, never from an earlier stage's memory: the render
  reads the run's VERIFIED voice pointer through `GET /runs/{id}/assets` (a resumed run skipped the
  voice stage), downloads the public object with a byte cap and checks it against the row's
  sha256 before using a single frame of it (rules marketing.md §2).
* **What the video draws is declared and checked**: the stage registers the video with
  `metadata.onscreen_text` — every string its cards drew (`cards.onscreen_strings`) — and the
  narration it burned (`metadata.voice_asset_id`). The server refuses anything that is not the
  accepted script's cards, its disclaimer card or the code-owned end card, and refuses a video
  without the disclaimer card (`run_service._check_onscreen_text`). The pixels themselves are not
  verified; that is why video posts are always born `pending_review`.
* **The disclaimer card is never cut**: it plays AFTER the narration for
  `voice.DISCLAIMER_CARD_SECONDS`, and the audio is padded to cover it (`video.build_argv` —
  never `-shortest`), and the ffprobe gate asserts the full duration.
* **Reuse before rendering**: a `ready` video of this run whose `render_key` matches (same audio
  bytes and word table, same cards, same card/video pipeline versions, same thread count — x264
  output depends on it) IS the render; nothing is encoded again, so a re-claimed attempt never
  mints a second public object.
* **Content that cannot be drawn ends the day, not the attempts**: a glyph the font lacks or a card
  that cannot fit at the floor size fails the same way on every retry, so it is a
  `SkipRun("unrenderable_text")`, not six failed attempts.
* **Formats are the server's, frozen per run** (drop 1): the accepted script carries
  `post_formats` ({platform: "video" | "image" | "text"}), decided once at write time, and the
  worker records each outlet in exactly that format — never its own choice (`create_posts`
  refuses any other). A script accepted before drop 1 carries none (`post_formats` null), and the
  worker falls back to `POST_FORMAT` below: TikTok, YouTube and Instagram get the video; Facebook
  and LinkedIn stay text (their caption disclaimer says "Written with AI assistance" — right for
  text, an under-disclosure on the narrated video); X, Threads and Bluesky are text-only outlets.
  The Instagram carousel is deferred until the caption disclaimer is composed per format.
* **The post image** (an "image" format anywhere in the run): ONE 1080×1350 baseline JPEG of the
  script's `image_post` title + paragraphs and the server's `image_footer` (`cards.render_image`),
  registered as a `card` with `metadata.image_role = "post_image"` and its drawn strings in
  `metadata.onscreen_text` — the server allows only those strings and requires the footer
  (`run_service._check_post_image_text`). Every image post of the run carries it, read back as the
  verified `image_asset_id`. Reused by `render_key` like the video.
* **Template (news) scripts** (drop 2a, `authorship == "template"`): the company logos are
  resolved ONCE per stage (`logos.resolve_logos` — verified files or wordmark plates; a logo never
  ends the day) and shared by both media; the video is the opening card over the hook plus one card
  per narration line (`hook_card=True`); the post image is the closed `image_spec`
  (`news_layouts`), never the `image_post` alt text; both reuse keys carry the logos drawn,
  `image_spec`, `opening_card` and `video_layout`. A `create_posts` refused with
  MARKETING_TEMPLATE_REFUSED skips the day `template_refused` (deterministic, never retried).
* **Nothing is uploaded before every refusal is known**: the glyph checks, the image's layout and
  byte cap and the video's cards all run before the first registration, so a day skipped for its
  content leaves no public object behind.

Heavy imports (Pillow via cards.py) live inside the functions. Nothing here imports app.*
(tests/test_marketing_worker.py scans it). `skip`, `uploader` and `hasher` are injected from
main.py for the same reason voice.py takes them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("marketing.render")

#: The format each outlet is recorded in when the accepted script froze none (a script accepted
#: before drop 1). Every pair must be one the server records (`POST_FORMATS_BY_PLATFORM`);
#: tests/test_marketing_worker.py pins that, and that the map covers exactly the outlets the writer
#: composes copy for.
POST_FORMAT: Dict[str, str] = {
    "tiktok": "video",
    "youtube": "video",
    "instagram": "video",
    "facebook": "text",
    "linkedin": "text",
    "x": "text",
    "threads": "text",
    "bluesky": "text",
}
#: The `error_code` the server answers `create_posts` with when the day's script was not judged in
#: `enforce` mode (app.api.error_response.ErrorCode.MARKETING_JUDGE_NOT_ENFORCED; duplicated —
#: the worker never imports app.* — and pinned equal by the worker tests).
JUDGE_NOT_ENFORCED = "MARKETING_JUDGE_NOT_ENFORCED"
#: The `error_code` the server answers `create_posts` with when the day's TEMPLATE (news) script
#: failed its re-check at write time, or MARKETING_CONTENT_CLASSES no longer lists its class
#: (ErrorCode.MARKETING_TEMPLATE_REFUSED, contract D13; duplicated and pinned like the one above).
#: Deterministic for the run: the day is skipped `template_refused`, never retried six times.
TEMPLATE_REFUSED = "MARKETING_TEMPLATE_REFUSED"
#: The narration is ~1-2 MB of AAC; anything near this is not our file.
AUDIO_MAX_BYTES = 50 * 1024 * 1024
#: A rendered clip this large is refused by the ffprobe gate (Reels allows 300 MB; a 75 s clip at
#: crf 20 is ~10-30 MB).
VIDEO_MAX_BYTES = 250 * 1024 * 1024
RENDER_THREADS_MAX = 4
#: Bump when anything this module feeds the render changes (the card set, the caption file, the
#: timeline inputs): part of the reuse keys, so a new render is a new object, never a stale reuse.
#: v2 (drop 1): videos open on the first content card (`video.timeline(..., hook_card=False)`);
#: the post image joined the stage. v3 (drop 2a): template (news) scripts — the opening card over
#: the hook (`hook_card=True`), one card per line, company logos, the template image layouts; the
#: keys carry the logos, `image_spec`, `opening_card` and `video_layout`.
RENDER_STAGE_VERSION = "render/v3"
#: The values the server freezes into an accepted script's `post_formats` (mirrors
#: app.schemas.marketing.FROZEN_POST_FORMATS; tests/test_marketing_worker.py pins them equal).
FROZEN_FORMATS: Tuple[str, ...] = ("video", "image", "text")
#: The post image's share of the stage's worst case: laying it out (≤ RAMP_STEPS + 1 tries) and up
#: to len(JPEG_QUALITIES) encodes of a 1080×1350 frame, generously, on a slow vCPU.
IMAGE_RENDER_SECONDS = 20


class RenderInputError(RuntimeError):
    """The render's inputs are not what the ledger promised (no verified narration, audio bytes
    that do not match their row, a video outlet with no video). A bug or a race — the run fails
    loudly and the next tick retries; never papered over."""


# ── configuration from the worker's environment ──────────────────────────────


def render_threads() -> int:
    """x264's thread count: MARKETING_RENDER_THREADS, else the container's CPU quota (cgroup v2
    `cpu.max`; x264 would size its pool from the HOST's cores), else 2 — capped at
    RENDER_THREADS_MAX. Part of the reuse key: x264's bytes depend on it."""
    raw = os.environ.get("MARKETING_RENDER_THREADS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return min(int(raw), RENDER_THREADS_MAX)
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        if quota != "max":
            return max(1, min(math.ceil(int(quota) / int(period)), RENDER_THREADS_MAX))
    except (OSError, ValueError):
        pass
    return 2


def max_video_seconds() -> float:
    from marketing import voice

    return voice.env_float("MARKETING_MAX_VIDEO_SECONDS", voice.DEFAULT_MAX_VIDEO_SECONDS)


def brand_logo_path(fonts_dir: str) -> str:
    """`assets/brand/caydex-logo.png`, beside the fonts directory (the image copies `marketing/`)."""
    return str(Path(fonts_dir).parent / "brand" / "caydex-logo.png")


def worst_case_seconds(upload_timeout: float) -> float:
    """The longest the rendered stage can run: the video's (video.worst_case_seconds: ffmpeg
    timeout + probes + download + one upload + card rendering) plus the post image's (its render
    and one more upload) plus a template day's logos (drop 2a: logos.LOGOS_WORST_CASE_SECONDS — the
    shared LOGOS_BUDGET_SECONDS = MAX_LOGOS × LOGO_DOWNLOAD_TIMEOUT_SECONDS, which every download ends
    inside (each one at most `logos.worst_case_download_seconds(share)` = 1.75 × a share of at most
    half of what was left), plus a connect cap's worth of headroom for the last logo's sha, decode and
    write). tests/test_marketing_worker.py pins it inside STAGE_START_MARGINS["rendered"]."""
    from marketing import logos, video

    return (video.worst_case_seconds(upload_timeout) + IMAGE_RENDER_SECONDS + float(upload_timeout)
            + logos.LOGOS_WORST_CASE_SECONDS)


# ── pure helpers (unit-tested) ────────────────────────────────────────────────


def frozen_formats(script: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """The accepted script's frozen `post_formats` ({platform: "video" | "image" | "text"}), or
    None when it carries none (a script accepted before drop 1 — the worker then uses
    POST_FORMAT). RenderInputError when present but not that shape: the server sends a validated
    map or null, so anything else is contract drift — never guessed around."""
    raw = script.get("post_formats") if isinstance(script, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RenderInputError(f"the script's post_formats is a {type(raw).__name__}, not an object")
    for platform, fmt in raw.items():
        if not isinstance(platform, str) or not isinstance(fmt, str) or fmt not in FROZEN_FORMATS:
            raise RenderInputError(f"the script's post_formats[{str(platform)[:40]!r}] = {str(fmt)[:40]!r} "
                                   f"is not one of {FROZEN_FORMATS}")
    return dict(raw)


def outlet_formats(script: Dict[str, Any]) -> Dict[str, str]:
    """{platform: format} for each of the script's outlets that gets a post: the frozen
    `post_formats` when the script carries them, else POST_FORMAT. An outlet with no format is left
    out SILENTLY here (this is asked by several stages); `post_specs` is where that is logged."""
    frozen = frozen_formats(script)
    table = POST_FORMAT if frozen is None else frozen
    outlets = script.get("outlets") if isinstance(script, dict) else None
    return {p: table[p] for p in list(outlets or []) if isinstance(p, str) and p in table}


def video_needed(script: Dict[str, Any]) -> bool:
    """Does any outlet get the day's video (and so the narration)?"""
    return "video" in outlet_formats(script).values()


def image_needed(script: Dict[str, Any]) -> bool:
    """Does any outlet get the day's post image?"""
    return "image" in outlet_formats(script).values()


def post_specs(outlets: Sequence[str], video_asset_id: Optional[str], image_asset_id: Optional[str] = None,
               *, formats: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """The day's PostSpecs: one per outlet the accepted script carries copy for, in its FROZEN
    format (`formats` = `frozen_formats(script)`), or POST_FORMAT when the script froze none. The
    server authors every caption; the worker names only platform, format and the media.

    * An outlet with no format is skipped and logged — at ERROR when the script froze formats (the
      server froze one for every outlet with copy, and would refuse any other), else at WARNING.
    * A video outlet with no verified video, or an image outlet with no verified post image, is a
      RenderInputError — the render stage should have produced it.
    * An image post carries exactly the run's post image (the server refuses anything else)."""
    specs: List[Dict[str, Any]] = []
    for platform in outlets:
        if formats is None:
            fmt = POST_FORMAT.get(platform)
            if fmt is None:
                logger.warning("outlet %r has no Phase-4 format — no post recorded for it", platform)
                continue
        else:
            fmt = formats.get(platform)
            if fmt is None:
                logger.error("outlet %r has no frozen format in the accepted script — no post recorded "
                             "for it (the server would refuse a guess)", platform)
                continue
            if fmt not in FROZEN_FORMATS:
                raise RenderInputError(f"outlet {platform!r} has the frozen format {fmt!r}, not one of "
                                       f"{FROZEN_FORMATS}")
        if fmt == "video":
            if not video_asset_id:
                raise RenderInputError(f"outlet {platform!r} needs the day's video, but the run has "
                                       "no verified video asset")
            specs.append({"platform": platform, "format": "video", "asset_ids": [video_asset_id]})
        elif fmt == "image":
            if not image_asset_id:
                raise RenderInputError(f"outlet {platform!r} needs the day's post image, but the run "
                                       "has no verified post image")
            specs.append({"platform": platform, "format": "image", "asset_ids": [image_asset_id]})
        else:
            specs.append({"platform": platform, "format": fmt})
    return specs


def _template_inputs(logos: Optional[Sequence[Sequence[Any]]], image_spec: Any, opening_card: Any,
                     video_layout: Any) -> Dict[str, Any]:
    """The drop-2a inputs of both keys: the logos actually drawn as logos ([[key, sha256 | None]]
    — None for a wordmark plate), the closed `image_spec`, the `opening_card` and the
    `video_layout`, all as the script carries them (None / [] for a lesson)."""
    return {"logos": [[str(k), (str(sha) if sha else None)] for k, sha in (logos or [])],
            "image_spec": image_spec, "opening_card": opening_card, "video_layout": video_layout}


def render_key(*, audio_sha256: str, words: Sequence[Dict[str, Any]], card_texts: Sequence[Sequence[str]],
               threads: int, card_version: str, video_version: str, max_seconds: float,
               layout_engine: str, logos: Optional[Sequence[Sequence[Any]]] = None, image_spec: Any = None,
               opening_card: Any = None, video_layout: Any = None) -> str:
    """Identity of a render: everything that decides its bytes — including Pillow's text layout
    engine (raqm and basic measure, wrap and draw differently; the cards AND the caption phrase
    splits depend on it), and (drop 2a) the logos drawn, the opening card and the video layout."""
    payload = {
        "stage": RENDER_STAGE_VERSION, "audio": audio_sha256, "words": list(words),
        "cards": [list(t) for t in card_texts], "threads": int(threads),
        "card_version": card_version, "video_version": video_version, "max_seconds": float(max_seconds),
        "layout_engine": layout_engine, **_template_inputs(logos, image_spec, opening_card, video_layout),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def image_render_key(*, texts: Sequence[str], card_version: str, layout_engine: str, max_bytes: int,
                     logos: Optional[Sequence[Sequence[Any]]] = None, image_spec: Any = None,
                     opening_card: Any = None, video_layout: Any = None) -> str:
    """Identity of a post image: everything that decides its bytes — the drawn strings in order,
    the card pipeline (layout, palette, JPEG ladder: CARD_RENDER_VERSION), the layout engine and
    the byte cap (it picks the quality step), and (drop 2a) the logos drawn and the template spec."""
    payload = {
        "stage": RENDER_STAGE_VERSION, "kind": "post_image", "texts": list(texts),
        "card_version": card_version, "layout_engine": layout_engine, "max_bytes": int(max_bytes),
        **_template_inputs(logos, image_spec, opening_card, video_layout),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _reusable(listing: Dict[str, Any], key: str) -> Optional[str]:
    for a in listing.get("assets") or []:
        md = a.get("metadata") or {}
        if a.get("kind") == "video" and a.get("status") == "ready" and md.get("render_key") == key:
            return a.get("id")
    return None


def _reusable_image(listing: Dict[str, Any], key: str, role: str) -> Optional[str]:
    """A `ready` post image of this run with the same render key — it IS the render."""
    for a in listing.get("assets") or []:
        md = a.get("metadata") or {}
        if (a.get("kind") == "card" and a.get("status") == "ready" and md.get("image_role") == role
                and md.get("render_key") == key):
            return a.get("id")
    return None


def _voice_row(listing: Dict[str, Any]) -> Dict[str, Any]:
    voice_id = listing.get("voice_asset_id")
    row = next((a for a in listing.get("assets") or [] if voice_id and a.get("id") == voice_id), None)
    if row is None:
        raise RenderInputError(f"the run has no verified narration (voice_asset_id={voice_id!r})")
    words = (row.get("metadata") or {}).get("words")
    if not isinstance(words, list) or not words:
        raise RenderInputError(f"narration {voice_id} carries no word timings")
    try:
        duration = float(row.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if not duration > 0:
        raise RenderInputError(f"narration {voice_id} has no duration")
    if not row.get("public_url") or not row.get("sha256"):
        raise RenderInputError(f"narration {voice_id} has no public URL or sha256")
    return row


# ── I/O seams (tests replace these module attributes) ────────────────────────


def download(url: str, *, max_bytes: int, timeout: float, max_seconds: Optional[float] = None,
             read_timeout: Optional[float] = None) -> bytes:
    """GET a PUBLIC object (the media bucket is public by design, §12.3) with a hard byte cap, no
    redirects, and — with `max_seconds` (the logos' share of the stage) — a wall-clock cap checked
    once the response headers have landed (before any body byte is read) and after every chunk.

    httpx bounds each PHASE separately (connect, TLS start, the request write, every socket read),
    never the whole request. `timeout` bounds the pool wait, connect, TLS and the write; `read_timeout`
    (default `timeout`) bounds each read — the response headers and every body read. A logo download
    (review round 2) gives connect / TLS / write a quarter of its share each and every read the other
    three quarters, so a cold CDN that is slow to its FIRST byte still answers inside the share, while
    the check after the headers stops a slow start before the body and a server that drips body
    bytes cannot hold the stage past `max_seconds` + one read (`logos.worst_case_download_seconds`).
    (DNS resolution is not bounded by httpx at all; the logo host is our own Supabase origin.)

    The bytes counted against `max_bytes` are the bytes ON THE WIRE: the request asks for
    `Accept-Encoding: identity`, a response that is content-encoded anyway is refused before its
    body is read (a small gzip body may inflate far past the cap), and the body is read raw."""
    import time

    import httpx

    chunks: List[bytes] = []
    size = 0
    started = time.monotonic()
    limits = httpx.Timeout(timeout, read=timeout if read_timeout is None else read_timeout)
    with httpx.Client(timeout=limits, follow_redirects=False) as client:
        with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as resp:
            if resp.status_code != 200:
                raise RenderInputError(f"download -> HTTP {resp.status_code}")
            encoding = resp.headers.get("content-encoding", "").strip().lower()
            if encoding not in ("", "identity"):
                raise RenderInputError(f"download -> a {encoding[:40]!r}-encoded response, refused unread")
            if max_seconds is not None and time.monotonic() - started > max_seconds:
                raise RenderInputError(f"download exceeded {max_seconds:.1f}s before its body")
            # The wire bytes. A transport that builds its response in memory (httpx.MockTransport in
            # the tests) has already loaded the body, which iter_raw would refuse: those bytes are
            # the identity body checked above, still counted against the cap.
            body = iter((resp.content,)) if resp.is_stream_consumed else resp.iter_raw()
            for chunk in body:
                size += len(chunk)
                if size > max_bytes:
                    raise RenderInputError(f"download exceeded {max_bytes} bytes")
                if max_seconds is not None and time.monotonic() - started > max_seconds:
                    raise RenderInputError(f"download exceeded {max_seconds:.1f}s")
                chunks.append(chunk)
    return b"".join(chunks)


def produce_video(*, workdir: Path, specs: Sequence[Any], words: Sequence[Dict[str, Any]],
                  narration_seconds: float, audio_file: str, fonts_dir: str, logo_path: Optional[str],
                  threads: int, heartbeat: Optional[Callable[[], None]], run_id: str,
                  max_seconds: float, layout_engine: str, hook_card: bool = False) -> Tuple[bytes, float, List[int]]:
    """Cards → PNGs, words → captions.ass, then ONE ffmpeg call. Returns (mp4 bytes, duration from
    ffprobe, the card indices the timeline actually shows). `audio_file` is relative to workdir.
    `hook_card=True` for a template (per-line) video: card 0 is its opening card, alone over the
    hook, and every narration line opens its own card; a lesson keeps the drop-1 opening."""
    from marketing import captions, cards, video, voice

    font = str(Path(fonts_dir) / "Inter-Bold.ttf")
    # libass is given a RELATIVE fontsdir (no filtergraph escaping): the face is copied beside
    # the job.
    (workdir / "fonts").mkdir(exist_ok=True)
    shutil.copyfile(font, workdir / "fonts" / "Inter-Bold.ttf")
    # A lesson (hook_card=False): the first content card opens the video at frame 0 (drop 1) — no
    # brand card. A template (hook_card=True): the company's opening card over the hook (drop 2a).
    segments = video.timeline(words, len(specs), narration_seconds, voice.DISCLAIMER_CARD_SECONDS,
                              hook_card=hook_card)
    shown = sorted({seg.card for seg in segments})
    names: Dict[int, str] = {}
    for i in shown:
        names[i] = f"card{i:02d}.png"
        (workdir / names[i]).write_bytes(
            cards.render_card(specs[i], font_path=font, logo_path=logo_path, layout_engine=layout_engine))
    (workdir / "captions.ass").write_text(captions.build_ass(words, captions.font_measurer(font)),
                                          encoding="utf-8")
    data, probe = video.render_video(
        workdir=str(workdir), card_files=[names[seg.card] for seg in segments], segments=segments,
        audio_file=audio_file, ass_file="captions.ass", fonts_dir="fonts", threads=threads,
        heartbeat=heartbeat, run_id=run_id, max_seconds=max_seconds, max_bytes=VIDEO_MAX_BYTES,
    )
    return data, float(probe.duration or 0.0), shown


# ── the stages ────────────────────────────────────────────────────────────────


def _register(api: Any, run_id: str, ctx: Dict[str, Any], data: bytes, *,
              uploader: Callable[..., None], hasher: Callable[[bytes], str], **fields: Any) -> Dict[str, Any]:
    """Register the bytes, PUT them through the signed URL and have the server verify them — or
    nothing more when the server already holds them `ready` (`upload` None). Returns the row."""
    reg = api.register_asset(run_id, sha256=hasher(data), bytes=len(data), **fields)
    asset = reg["asset"]
    if reg.get("upload"):
        uploader(reg["upload"], data, apikey=ctx.get("apikey"))
        api.complete_asset(asset["id"])
    return asset


def _render_post_image(run_id: str, spec: Any, *, font: str, engine: str,
                       skip: Callable[[str], BaseException], template: bool = False) -> Any:
    """The post image's bytes (`cards.RenderedImage`) — the lesson image of an `ImageSpec`, or with
    `template` the news image of a `news_layouts.TemplateImage`. Content that cannot be drawn ends
    the day, the same way on every retry: text that cannot fit whole → `unrenderable_text`; a JPEG
    over the cap at every quality step → `image_too_large`."""
    from marketing import cards

    try:
        if template:
            from marketing import news_layouts

            image = news_layouts.render_template_image(spec, font_path=font, layout_engine=engine,
                                                       max_bytes=cards.POST_IMAGE_MAX_BYTES)
        else:
            image = cards.render_image(spec, font_path=font, layout_engine=engine,
                                       max_bytes=cards.POST_IMAGE_MAX_BYTES)
    except cards.CardOverflow as e:
        logger.warning("render run_id=%s: the post image cannot fit its text (%s) — skipping the day", run_id, e)
        raise skip("unrenderable_text") from e
    except cards.ImageTooLarge as e:
        logger.error("render run_id=%s: the post image is too large (%s) — skipping the day", run_id, e)
        raise skip("image_too_large") from e
    # The byte assert: the server refuses a bigger post image at registration anyway — never send one.
    if len(image.data) > cards.POST_IMAGE_MAX_BYTES:
        raise RenderInputError(f"run {run_id}: the post image is {len(image.data)} bytes, over "
                               f"{cards.POST_IMAGE_MAX_BYTES}")
    return image


def _logo_table(script: Dict[str, Any], listing: Dict[str, Any], *, template: bool, dest_dir: Path,
                run_id: str) -> Dict[str, Any]:
    """{key: cards.LogoArt} for a template script: its `logos` resolved ONCE for the stage (the
    video's opening card and the post image share them) — each a verified local file or a wordmark
    (logos.resolve_logos never raises for a logo). {} for a lesson (a lesson draws no company logo;
    a logo list on one is contract drift, logged and ignored)."""
    from marketing import cards, logos

    if not template:
        if script.get("logos"):
            logger.warning("render run_id=%s: a lesson script carries logos — ignored (only a template "
                           "draws company logos)", run_id)
        return {}
    resolved = logos.resolve_logos(script, bucket_origin=logos.bucket_origin(listing), download=download,
                                   dest_dir=dest_dir)
    try:
        return cards.logo_table(script, resolved)
    except ValueError as e:
        raise RenderInputError(f"run {run_id}: {e}") from e


def drawn_logos(table: Dict[str, Any]) -> List[List[Optional[str]]]:
    """[[key, sha256 | None]] in the script's order: the stored sha of each logo drawn AS A LOGO,
    None for a wordmark plate — what the drop-2a render keys carry."""
    return [[key, None if art.wordmark else art.sha256] for key, art in table.items()]


def stage_render(api: Any, run: Dict[str, Any], ctx: Dict[str, Any], *,
                 skip: Callable[[str], BaseException],
                 uploader: Callable[..., None],
                 hasher: Callable[[bytes], str]) -> Dict[str, Any]:
    """Phase 4 + drop 1 + drop 2a: render the day's media — the 9:16 video when an outlet is
    recorded as video, the 4:5 post image when one is recorded as image — and return the checkpoint
    metadata (`{"image_asset_id": …, "video_asset_id": …}`, only the media the day needs; `{}` on a
    day with neither) that `run_pipeline` writes in the SAME PATCH as `stage=rendered`.

    A TEMPLATE script (`authorship == "template"`, drop 2a) resolves its company logos ONCE
    (`logos.resolve_logos`: verified files, else wordmark plates — a logo never ends the day); its
    video is [opening card] + one card per narration line + [disclaimer], timed with
    `hook_card=True`; its post image is its `image_spec` drawn by `news_layouts` (never the
    `image_post`, which is alt text on a template). A lesson renders exactly as in drop 1.

    Order: work out each medium and whether a `ready` asset already IS it (reuse by render key);
    check every glyph; render what is missing (image, then video); only then register and upload
    (image first). So every refusal of the content — a glyph, text that cannot fit, an image over
    its cap — is known before the first public object exists."""
    from marketing import cards

    run_id = run["id"]
    script = ctx["script"]
    formats = outlet_formats(script)
    need_video = "video" in formats.values()
    need_image = "image" in formats.values()
    if not (need_video or need_image):
        logger.info("render SKIPPED run_id=%s: no outlet needs media (%s)", run_id, formats)
        return {}
    try:
        template = cards.is_template(script)
    except ValueError as e:
        raise RenderInputError(f"run {run_id}: {e}") from e
    listing = api.list_assets(run_id)
    with tempfile.TemporaryDirectory(prefix="logos-") as logo_dir:
        table = _logo_table(script, listing, template=template, dest_dir=Path(logo_dir), run_id=str(run_id))
        return _render_media(api, run, ctx, listing=listing, table=table, template=template,
                             need_video=need_video, need_image=need_image, formats=formats,
                             skip=skip, uploader=uploader, hasher=hasher)


def _render_media(api: Any, run: Dict[str, Any], ctx: Dict[str, Any], *, listing: Dict[str, Any],
                  table: Dict[str, Any], template: bool, need_video: bool, need_image: bool,
                  formats: Dict[str, str], skip: Callable[[str], BaseException],
                  uploader: Callable[..., None], hasher: Callable[[bytes], str]) -> Dict[str, Any]:
    """`stage_render`'s body, inside the stage's logo directory (the files live until it returns)."""
    from marketing import cards, video

    SkipRun, sha256_hex = skip, hasher
    run_id = run["id"]
    script = ctx["script"]
    image_spec: Any = None
    if need_image:
        try:
            if template:
                from marketing import news_layouts

                image_spec = news_layouts.template_image_for_script(script, table)
            else:
                image_spec = cards.image_for_script(script)
        except ValueError as e:
            raise RenderInputError(f"run {run_id}: {e}") from e
        if image_spec is None:
            raise RenderInputError(
                f"run {run_id}: the frozen post formats record {sorted(p for p, f in formats.items() if f == 'image')} "
                "as image posts, but the script carries no image_post")
    fonts_dir = ctx["fonts_dir"]
    font = str(Path(fonts_dir) / "Inter-Bold.ttf")
    # Resolved ONCE and passed explicitly: Pillow would otherwise pick per call, and a card laid
    # out with basic in one attempt and raqm in the next would be a different object.
    engine = cards.resolve_layout_engine(None)
    # What a template's keys carry beside the drawn strings (None / [] for a lesson).
    extra_key: Dict[str, Any] = {}
    if template:
        extra_key = {"logos": drawn_logos(table), "image_spec": script.get("image_spec"),
                     "opening_card": script.get("opening_card"), "video_layout": script.get("video_layout")}

    # ── each medium's identity, and whether a ready asset already IS it ──────
    image_texts: List[str] = []
    image_key = image_reuse = None
    if image_spec is not None:
        image_texts = (list(image_spec.strings) if template else cards.image_onscreen_strings(image_spec))
        image_key = image_render_key(texts=image_texts, card_version=cards.CARD_RENDER_VERSION,
                                     layout_engine=engine, max_bytes=cards.POST_IMAGE_MAX_BYTES, **extra_key)
        image_reuse = _reusable_image(listing, image_key, cards.IMAGE_ROLE_POST)
    voice_row: Dict[str, Any] = {}
    words: List[Dict[str, Any]] = []
    specs: List[Any] = []
    threads, limit = 0, 0.0
    video_key = video_reuse = None
    per_line = False
    if need_video:
        threads, limit = render_threads(), max_video_seconds()
        voice_row = _voice_row(listing)
        words = voice_row["metadata"]["words"]
        try:
            specs = cards.cards_for_script(script, logos=table)
        except ValueError as e:
            # The server always supplies the disclaimer card; a script without one must not render.
            raise RenderInputError(f"run {run_id}: {e}") from e
        per_line = script.get("video_layout") == cards.VIDEO_LAYOUT_PER_LINE
        video_key = render_key(
            audio_sha256=str(voice_row["sha256"]), words=words,
            card_texts=[cards.onscreen_strings(s) for s in specs], threads=threads,
            card_version=cards.CARD_RENDER_VERSION, video_version=video.VIDEO_RENDER_VERSION,
            max_seconds=limit, layout_engine=engine, **extra_key,
        )
        video_reuse = _reusable(listing, video_key)

    # ── every glyph, before anything is drawn or uploaded ─────────────────────
    try:
        if image_spec is not None and not image_reuse:
            if template:
                from marketing import news_layouts

                news_layouts.check_glyphs(image_spec, font)
            else:
                cards.check_image_glyphs(image_spec, font)
        if need_video and not video_reuse:
            cards.check_glyphs(specs, font, extra=[str(w.get("w", "")) for w in words])
    except cards.MissingGlyphs as e:
        logger.warning("render run_id=%s: the font cannot draw %s — skipping the day", run_id, e)
        raise SkipRun("unrenderable_text") from e

    # ── render what is missing: the image (cheap), then the video ─────────────
    image = None
    if image_spec is not None and not image_reuse:
        image = _render_post_image(run_id, image_spec, font=font, engine=engine, skip=SkipRun, template=template)
    rendered_video: Optional[Tuple[bytes, float, List[int]]] = None
    if need_video and not video_reuse:
        logo: Optional[str] = brand_logo_path(fonts_dir)
        if not os.path.isfile(logo):
            logger.warning("brand logo missing at %s — the brand and disclaimer cards render without it", logo)
            logo = None

        def heartbeat() -> None:
            try:
                api.update_run(run_id)             # bumps the claim's liveness, nothing else
            except Exception as e:  # noqa: BLE001 — a missed beat is logged, the render goes on
                logger.warning("render heartbeat failed run_id=%s: %s: %s", run_id, type(e).__name__, e)

        # Only a template passes hook_card (a lesson's call is drop 1's, keyword for keyword).
        opening = {"hook_card": True} if per_line else {}
        with tempfile.TemporaryDirectory(prefix="render-") as tmp:
            work = Path(tmp)
            audio = download(str(voice_row["public_url"]), max_bytes=AUDIO_MAX_BYTES,
                             timeout=video.DOWNLOAD_TIMEOUT_SECONDS)
            if sha256_hex(audio) != str(voice_row["sha256"]).lower():
                raise RenderInputError(f"run {run_id}: the downloaded narration does not match its "
                                       f"row's sha256 ({voice_row['id']})")
            (work / "narration.m4a").write_bytes(audio)
            try:
                rendered_video = produce_video(
                    workdir=work, specs=specs, words=words,
                    narration_seconds=float(voice_row["duration_seconds"]), audio_file="narration.m4a",
                    fonts_dir=fonts_dir, logo_path=logo, threads=threads, heartbeat=heartbeat,
                    run_id=str(run_id), max_seconds=limit, layout_engine=engine, **opening,
                )
            except cards.CardOverflow as e:
                logger.warning("render run_id=%s: a card cannot fit (%s) — skipping the day", run_id, e)
                raise SkipRun("unrenderable_text") from e

    # ── register + upload (image first) ───────────────────────────────────────
    produced: Dict[str, Any] = {}
    if image_spec is not None:
        if image_reuse:
            logger.info("post image REUSED asset=%s run_id=%s (same inputs)", image_reuse, run_id)
            produced["image_asset_id"] = image_reuse
        else:
            metadata = {"onscreen_text": image_texts, "image_role": cards.IMAGE_ROLE_POST,
                        "render_key": image_key, "render_version": RENDER_STAGE_VERSION,
                        "card_version": cards.CARD_RENDER_VERSION, "layout_engine": engine,
                        "width": cards.IMAGE_WIDTH, "height": cards.IMAGE_HEIGHT,
                        "jpeg_quality": image.quality, "template_id": ctx.get("template_id")}
            if template:
                metadata.update({"image_layout": image_spec.layout, "logos": drawn_logos(table)})
            asset = _register(api, run_id, ctx, image.data, uploader=uploader, hasher=sha256_hex,
                              kind="card", ext=cards.POST_IMAGE_EXT, metadata=metadata)
            logger.info("post image READY run_id=%s asset=%s bytes=%d quality=%d step=%d strings=%d%s",
                        run_id, asset["id"], len(image.data), image.quality, image.layout.step, len(image_texts),
                        f" layout={image_spec.layout} logos={sum(1 for k, s in drawn_logos(table) if s)}/{len(table)}"
                        if template else "")
            produced["image_asset_id"] = asset["id"]
    if need_video:
        if video_reuse:
            logger.info("render REUSED asset=%s run_id=%s (same inputs)", video_reuse, run_id)
            produced["video_asset_id"] = video_reuse
        else:
            data, duration, shown = rendered_video  # type: ignore[misc]
            onscreen: List[str] = []
            for i in shown:
                for text in cards.onscreen_strings(specs[i]):
                    if text not in onscreen:
                        onscreen.append(text)
            metadata = {"onscreen_text": onscreen, "voice_asset_id": voice_row["id"], "render_key": video_key,
                        "render_version": RENDER_STAGE_VERSION, "card_version": cards.CARD_RENDER_VERSION,
                        "video_version": video.VIDEO_RENDER_VERSION, "threads": threads,
                        "layout_engine": engine,
                        "cards_shown": len(shown), "template_id": ctx.get("template_id")}
            if per_line:
                metadata["video_layout"] = cards.VIDEO_LAYOUT_PER_LINE
            asset = _register(api, run_id, ctx, data, uploader=uploader, hasher=sha256_hex,
                              kind="video", ext="mp4", duration_seconds=round(duration, 3), metadata=metadata)
            logger.info("video READY run_id=%s asset=%s duration=%.1fs bytes=%d cards=%d threads=%d%s",
                        run_id, asset["id"], duration, len(data), len(shown), threads,
                        " layout=per_line" if per_line else "")
            produced["video_asset_id"] = asset["id"]
    return produced


def stage_posts(api: Any, run: Dict[str, Any], ctx: Dict[str, Any], *,
                skip: Callable[[str], BaseException]) -> Dict[str, Any]:
    """Phase 4: record the day's posts (`POST /runs/{id}/posts`). The server writes every caption
    from the accepted script and births every media post `pending_review`; the worker names only
    the outlet, its frozen format and the SERVER-VERIFIED media — the video and the post image,
    read back, never from `ctx`."""
    SkipRun = skip
    script = ctx["script"]
    outlets = list(script.get("outlets") or [])
    frozen = frozen_formats(script)
    used = set(outlet_formats(script).values())
    video_id = image_id = None
    if used & {"video", "image"}:
        listing = api.list_assets(run["id"])
        video_id, image_id = listing.get("video_asset_id"), listing.get("image_asset_id")
    specs = post_specs(outlets, video_id, image_id, formats=frozen)
    if not specs:
        logger.warning("run_id=%s: the accepted script carries no outlet the worker posts to (%s) — "
                       "no post recorded", run["id"], outlets)
        return {"posts_recorded": 0}
    try:
        body = api.create_posts(run["id"], specs)
    except Exception as e:
        if getattr(e, "error_code", None) == JUDGE_NOT_ENFORCED:
            # Deterministic for the run: the day's script was not judged in `enforce` mode, so it
            # never becomes a post. An operator must set MARKETING_JUDGE_MODE=enforce.
            logger.error("run_id=%s: the server refused the posts — the script was not judged in "
                         "enforce mode (MARKETING_JUDGE_MODE on the web service): %s", run["id"], e)
            raise SkipRun("judge_not_enforced") from e
        if getattr(e, "error_code", None) == TEMPLATE_REFUSED:
            # Deterministic for the run (drop 2a, contract D13): the day's template post failed the
            # server's re-check, or its content class was switched off — never retried six times.
            logger.error("run_id=%s: the server refused the template posts (its re-check failed or "
                         "MARKETING_CONTENT_CLASSES no longer lists the class — read the web log "
                         "'create_posts TEMPLATE REFUSED' for this run): %s", run["id"], e)
            raise SkipRun("template_refused") from e
        raise
    posts = body.get("posts") or []
    logger.info("posts RECORDED run_id=%s n=%d %s", run["id"], len(posts),
                sorted(f"{p.get('platform')}/{p.get('format')}:{p.get('status')}" for p in posts))
    return {"posts_recorded": len(posts)}
