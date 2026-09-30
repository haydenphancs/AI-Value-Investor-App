"""
The `rendered` and `assets_ready` stages (Phase 4, SYSTEM_DESIGN_GUIDELINES §12.8): turn the day's
accepted script and its narration into ONE 9:16 MP4, then record the day's posts.

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
* **Formats**: `POST_FORMAT` below. TikTok, YouTube and Instagram get the video; Facebook and
  LinkedIn stay text (their caption disclaimer says "Written with AI assistance" — right for text,
  an under-disclosure on the narrated video); X, Threads and Bluesky are text-only outlets. The
  Instagram carousel is deferred until the caption disclaimer is composed per format.

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

#: The format each outlet is recorded in (Phase 4). Every pair must be one the server records
#: (`POST_FORMATS_BY_PLATFORM`); tests/test_marketing_worker.py pins that, and that the map covers
#: exactly the outlets the writer composes copy for.
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
#: The narration is ~1-2 MB of AAC; anything near this is not our file.
AUDIO_MAX_BYTES = 50 * 1024 * 1024
#: A rendered clip this large is refused by the ffprobe gate (Reels allows 300 MB; a 75 s clip at
#: crf 20 is ~10-30 MB).
VIDEO_MAX_BYTES = 250 * 1024 * 1024
RENDER_THREADS_MAX = 4
#: Bump when anything this module feeds the render changes (the card set, the caption file, the
#: timeline inputs): part of the reuse key, so a new render is a new object, never a stale reuse.
RENDER_STAGE_VERSION = "render/v1"


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
    """The longest the rendered stage can run (video.worst_case_seconds: ffmpeg timeout + probe +
    download + one upload + card rendering). tests/test_marketing_worker.py pins it inside
    STAGE_START_MARGIN_SECONDS."""
    from marketing import video

    return video.worst_case_seconds(upload_timeout)


# ── pure helpers (unit-tested) ────────────────────────────────────────────────


def video_outlets(outlets: Sequence[str]) -> List[str]:
    return [p for p in outlets if POST_FORMAT.get(p) == "video"]


def post_specs(outlets: Sequence[str], video_asset_id: Optional[str]) -> List[Dict[str, Any]]:
    """The day's PostSpecs: one per outlet the accepted script carries copy for, in the format of
    POST_FORMAT. The server authors every caption; the worker names only platform, format and the
    video. An outlet the worker does not know is skipped (logged); a video outlet with no verified
    video is a RenderInputError — the render stage should have produced one."""
    specs: List[Dict[str, Any]] = []
    for platform in outlets:
        fmt = POST_FORMAT.get(platform)
        if fmt is None:
            logger.warning("outlet %r has no Phase-4 format — no post recorded for it", platform)
            continue
        if fmt == "video":
            if not video_asset_id:
                raise RenderInputError(f"outlet {platform!r} needs the day's video, but the run has "
                                       "no verified video asset")
            specs.append({"platform": platform, "format": "video", "asset_ids": [video_asset_id]})
        else:
            specs.append({"platform": platform, "format": fmt})
    return specs


def render_key(*, audio_sha256: str, words: Sequence[Dict[str, Any]], card_texts: Sequence[Sequence[str]],
               threads: int, card_version: str, video_version: str, max_seconds: float,
               layout_engine: str) -> str:
    """Identity of a render: everything that decides its bytes — including Pillow's text layout
    engine (raqm and basic measure, wrap and draw differently; the cards AND the caption phrase
    splits depend on it)."""
    payload = {
        "stage": RENDER_STAGE_VERSION, "audio": audio_sha256, "words": list(words),
        "cards": [list(t) for t in card_texts], "threads": int(threads),
        "card_version": card_version, "video_version": video_version, "max_seconds": float(max_seconds),
        "layout_engine": layout_engine,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _reusable(listing: Dict[str, Any], key: str) -> Optional[str]:
    for a in listing.get("assets") or []:
        md = a.get("metadata") or {}
        if a.get("kind") == "video" and a.get("status") == "ready" and md.get("render_key") == key:
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


def download(url: str, *, max_bytes: int, timeout: float) -> bytes:
    """GET a PUBLIC object (the media bucket is public by design, §12.3) with a hard byte cap."""
    import httpx

    chunks: List[bytes] = []
    size = 0
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                raise RenderInputError(f"narration download -> HTTP {resp.status_code}")
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise RenderInputError(f"narration download exceeded {max_bytes} bytes")
                chunks.append(chunk)
    return b"".join(chunks)


def produce_video(*, workdir: Path, specs: Sequence[Any], words: Sequence[Dict[str, Any]],
                  narration_seconds: float, audio_file: str, fonts_dir: str, logo_path: Optional[str],
                  threads: int, heartbeat: Optional[Callable[[], None]], run_id: str,
                  max_seconds: float, layout_engine: str) -> Tuple[bytes, float, List[int]]:
    """Cards → PNGs, words → captions.ass, then ONE ffmpeg call. Returns (mp4 bytes, duration from
    ffprobe, the card indices the timeline actually shows). `audio_file` is relative to workdir."""
    from marketing import captions, cards, video, voice

    font = str(Path(fonts_dir) / "Inter-Bold.ttf")
    # libass is given a RELATIVE fontsdir (no filtergraph escaping): the face is copied beside
    # the job.
    (workdir / "fonts").mkdir(exist_ok=True)
    shutil.copyfile(font, workdir / "fonts" / "Inter-Bold.ttf")
    segments = video.timeline(words, len(specs), narration_seconds, voice.DISCLAIMER_CARD_SECONDS)
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


def stage_render(api: Any, run: Dict[str, Any], ctx: Dict[str, Any], *,
                 skip: Callable[[str], BaseException],
                 uploader: Callable[..., None],
                 hasher: Callable[[bytes], str]) -> Dict[str, Any]:
    """Phase 4: render the day's video; return the checkpoint metadata (`{"video_asset_id": …}`,
    or `{}` on a day with no video outlet) that `run_pipeline` writes in the SAME PATCH as
    `stage=rendered`."""
    from marketing import cards, video

    SkipRun, upload_signed, sha256_hex = skip, uploader, hasher
    script = ctx["script"]
    outlets = list(script.get("outlets") or [])
    if not video_outlets(outlets):
        logger.info("render SKIPPED run_id=%s: no video outlet among %s", run["id"], outlets)
        return {}
    listing = api.list_assets(run["id"])
    voice_row = _voice_row(listing)
    words = voice_row["metadata"]["words"]
    narration = float(voice_row["duration_seconds"])
    try:
        specs = cards.cards_for_script(script)
    except ValueError as e:
        # The server always supplies the disclaimer card; a script without one must not render.
        raise RenderInputError(f"run {run['id']}: {e}") from e
    fonts_dir = ctx["fonts_dir"]
    font = str(Path(fonts_dir) / "Inter-Bold.ttf")
    logo = brand_logo_path(fonts_dir)
    if not os.path.isfile(logo):
        logger.warning("brand logo missing at %s — the brand and disclaimer cards render without it", logo)
        logo = None
    threads = render_threads()
    limit = max_video_seconds()
    # Resolved ONCE and passed explicitly: Pillow would otherwise pick per call, and a card laid
    # out with basic in one attempt and raqm in the next would be a different object.
    engine = cards.resolve_layout_engine(None)
    key = render_key(
        audio_sha256=str(voice_row["sha256"]), words=words,
        card_texts=[cards.onscreen_strings(s) for s in specs], threads=threads,
        card_version=cards.CARD_RENDER_VERSION, video_version=video.VIDEO_RENDER_VERSION,
        max_seconds=limit, layout_engine=engine,
    )
    reuse = _reusable(listing, key)
    if reuse:
        logger.info("render REUSED asset=%s run_id=%s (same inputs)", reuse, run["id"])
        return {"video_asset_id": reuse}
    try:
        cards.check_glyphs(specs, font, extra=[str(w.get("w", "")) for w in words])
    except cards.MissingGlyphs as e:
        logger.warning("render run_id=%s: the font cannot draw %s — skipping the day", run["id"], e)
        raise SkipRun("unrenderable_text") from e

    def heartbeat() -> None:
        try:
            api.update_run(run["id"])          # bumps the claim's liveness, nothing else
        except Exception as e:  # noqa: BLE001 — a missed beat is logged, the render goes on
            logger.warning("render heartbeat failed run_id=%s: %s: %s", run["id"], type(e).__name__, e)

    with tempfile.TemporaryDirectory(prefix="render-") as tmp:
        work = Path(tmp)
        audio = download(str(voice_row["public_url"]), max_bytes=AUDIO_MAX_BYTES,
                         timeout=video.DOWNLOAD_TIMEOUT_SECONDS)
        if sha256_hex(audio) != str(voice_row["sha256"]).lower():
            raise RenderInputError(f"run {run['id']}: the downloaded narration does not match its "
                                   f"row's sha256 ({voice_row['id']})")
        (work / "narration.m4a").write_bytes(audio)
        try:
            data, duration, shown = produce_video(
                workdir=work, specs=specs, words=words, narration_seconds=narration,
                audio_file="narration.m4a", fonts_dir=fonts_dir, logo_path=logo, threads=threads,
                heartbeat=heartbeat, run_id=str(run["id"]), max_seconds=limit, layout_engine=engine,
            )
        except cards.CardOverflow as e:
            logger.warning("render run_id=%s: a card cannot fit (%s) — skipping the day", run["id"], e)
            raise SkipRun("unrenderable_text") from e
    onscreen: List[str] = []
    for i in shown:
        for text in cards.onscreen_strings(specs[i]):
            if text not in onscreen:
                onscreen.append(text)
    reg = api.register_asset(
        run["id"], kind="video", ext="mp4", sha256=sha256_hex(data), bytes=len(data),
        duration_seconds=round(duration, 3),
        metadata={"onscreen_text": onscreen, "voice_asset_id": voice_row["id"], "render_key": key,
                  "render_version": RENDER_STAGE_VERSION, "card_version": cards.CARD_RENDER_VERSION,
                  "video_version": video.VIDEO_RENDER_VERSION, "threads": threads,
                  "layout_engine": engine,
                  "cards_shown": len(shown), "template_id": ctx.get("template_id")},
    )
    asset = reg["asset"]
    if reg.get("upload"):
        upload_signed(reg["upload"], data, apikey=ctx.get("apikey"))
        api.complete_asset(asset["id"])
    logger.info("video READY run_id=%s asset=%s duration=%.1fs bytes=%d cards=%d threads=%d",
                run["id"], asset["id"], duration, len(data), len(shown), threads)
    return {"video_asset_id": asset["id"]}


def stage_posts(api: Any, run: Dict[str, Any], ctx: Dict[str, Any], *,
                skip: Callable[[str], BaseException]) -> Dict[str, Any]:
    """Phase 4: record the day's posts (`POST /runs/{id}/posts`). The server writes every caption
    from the accepted script and births every media post `pending_review`; the worker names only
    the outlet, the format and the SERVER-VERIFIED video (read back, never from `ctx`)."""
    SkipRun = skip
    outlets = list(ctx["script"].get("outlets") or [])
    video_id = None
    if video_outlets(outlets):
        video_id = api.list_assets(run["id"]).get("video_asset_id")
    specs = post_specs(outlets, video_id)
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
        raise
    posts = body.get("posts") or []
    logger.info("posts RECORDED run_id=%s n=%d %s", run["id"], len(posts),
                sorted(f"{p.get('platform')}/{p.get('format')}:{p.get('status')}" for p in posts))
    return {"posts_recorded": len(posts)}
