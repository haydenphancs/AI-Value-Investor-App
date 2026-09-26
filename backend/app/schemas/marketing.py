"""
Marketing engine schemas — the internal worker API contract (SYSTEM_DESIGN_GUIDELINES §12).

These are consumed by ONE client: `marketing/main.py`, the media worker. iOS never
sees them, so there is no Swift parity test to keep — but the worker is a separate deploy
(its own Railway service and image), so a renamed field here IS a wire break just the same.
Keep field names stable; add, don't rename.

Dates travel as ISO strings (`YYYY-MM-DD`), timestamps as ISO-8601 strings, ids as UUID
strings — the same conventions as every other schema in this folder.
"""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Kept in lockstep with the CHECK constraints in migration 170. The service validates against
# these before touching the database so a typo fails with a 422 that names the field rather
# than a 23514 that names nothing.
RUN_STATUSES = ("planned", "in_progress", "media_ready", "published", "failed", "skipped")
RUN_STAGES = ("planned", "selected", "scripted", "voiced", "rendered", "assets_ready")
CONTENT_CLASSES = ("A", "C")
ASSET_KINDS = (
    "manifest", "script", "audio", "podcast_audio", "video", "card", "carousel", "caption", "blog",
)
ASSET_STATUSES = ("pending_upload", "ready", "failed")
POST_PLATFORMS = (
    "tiktok", "youtube", "instagram", "facebook", "linkedin", "threads", "bluesky", "x",
    "pinterest", "mastodon", "podcast", "blog", "hashnode", "devto",
)
POST_FORMATS = ("video", "carousel", "image", "text", "podcast", "article")
POST_STATUSES = (
    "pending_review", "approved", "rejected", "queued", "published", "failed", "skipped",
    "retracted",
)

# Extensions the worker may register, with the content type each one is served as. The
# bucket's `allowed_mime_types` is the same list; keep them together (the LATEST migration that
# sets it — 173 — is what `test_marketing_run_service` compares against).
#
# 173 dropped html/txt/md: the worker is the least-trusted process in the engine, and a PUBLIC
# bucket that accepts text/html lets a compromised worker host a phishing page under the brand.
# Drafts (script, captions) live in `marketing_scripts`, never in the bucket.
ASSET_EXTENSIONS: Dict[str, str] = {
    "mp4": "video/mp4",
    "png": "image/png",
    "jpg": "image/jpeg",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "json": "application/json",
}

#: Asset kinds the WORKER may register. `script` / `caption` / `blog` stay in the CHECK list
#: (migration 170) but are server-authored: the worker renders media, it never supplies copy.
WORKER_ASSET_KINDS = ("manifest", "audio", "podcast_audio", "video", "card", "carousel")

#: Which extensions each asset kind may carry. Checked at registration, so a `video` row can
#: never point at a `.json` object (or the run's manifest pass itself off as a video). Every
#: kind in ASSET_KINDS has an entry; every extension is in ASSET_EXTENSIONS.
ASSET_KIND_EXTENSIONS: Dict[str, Tuple[str, ...]] = {
    "manifest": ("json",),
    "audio": ("mp3", "m4a"),
    "podcast_audio": ("mp3", "m4a"),
    "video": ("mp4",),
    "card": ("png", "jpg"),
    "carousel": ("png", "jpg"),
    # server-authored kinds (never registered by the worker)
    "script": ("json",),
    "caption": ("json",),
    "blog": ("json",),
}

#: Statuses the WORKER may write on its own run (PATCH /runs/{id}). `in_progress` / `planned`
#: belong to the claim, `published` to the web-side publisher.
WORKER_RUN_STATUSES = ("failed", "skipped", "media_ready")

#: Metadata keys the server owns on `marketing_runs.metadata`; a worker PATCH may not write
#: them. `claim_nonce` is trusted by `decide_claim` AHEAD of the attempts cap.
SERVER_OWNED_RUN_METADATA = ("claim_nonce",)

#: The formats the server will record a post in, per platform. The accepted package composes
#: ONE caption per platform (`post_copy.PLATFORMS`) and carries no format, so this map is what
#: bounds how many posts that caption can become — and the Phase-5 adapters read the same map.
#: A platform missing here has no Phase-2 copy and is refused anyway.
POST_FORMATS_BY_PLATFORM: Dict[str, Tuple[str, ...]] = {
    "tiktok": ("video",),
    "youtube": ("video",),
    "instagram": ("video", "carousel"),
    "facebook": ("text", "video"),
    "linkedin": ("text", "video"),
    "x": ("text",),
    "threads": ("text",),
    "bluesky": ("text",),
}

#: Asset kinds a post of each format may carry. `manifest`, `audio`, `script`, `caption` and
#: `blog` are never post media.
POST_MEDIA_KINDS: Dict[str, Tuple[str, ...]] = {
    "video": ("video",),
    "carousel": ("carousel", "card"),
    "image": ("card",),
    "podcast": ("podcast_audio",),
    "text": ("card",),
    "article": ("card",),
}

#: Formats that are nothing without their media: refused unless at least one READY asset of a
#: matching kind rides along (a media-less "video" post is born broken).
MEDIA_REQUIRED_FORMATS: FrozenSet[str] = frozenset({"video", "carousel", "image", "podcast"})

#: `marketing_scripts.status` (migration 173). One row per run, written ONLY by the web side.
SCRIPT_STATUSES = ("rest_day", "selected", "generating", "accepted", "rejected")

#: `marketing_scripts.reject_reason` (migration 176) and the `reason` of a `rejected` kick body.
#: The worker maps it to the run's skip_reason — only `content` is a compliance verdict:
#:   content            — MAX_GENERATIONS generations were rejected by the validators
#:   writer_unavailable — MAX_WRITER_FAILURES generations ended without a verdict (Gemini
#:                        failures, a ledger blip, a crashed or cancelled owner)
#:   empty_pool         — selection found nothing eligible
#:   source_ineligible  — the selected item left the eligible pool before it was written
SCRIPT_REJECT_REASONS = ("content", "writer_unavailable", "empty_pool", "source_ineligible")

_SHA256_HEX_LEN = 64

#: The most a worker may put in one metadata object (a run PATCH or an asset registration).
#: The least-trusted process in the engine writes these JSONB columns; unbounded, it could fill
#: the database. 64 KiB carries a 400-word timing table with room to spare.
METADATA_MAX_BYTES = 64 * 1024
#: Word-timing table carried by an `audio` asset (Phase 3, `metadata.words`).
AUDIO_WORDS_MAX = 400
AUDIO_WORD_MAX_CHARS = 48
#: Slack between the last word's end and the audio's measured duration (encoder padding).
AUDIO_TAIL_SLACK_SECONDS = 0.5


def capped_metadata(v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if v is None:
        return v
    try:
        size = len(json.dumps(v, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError) as e:
        raise ValueError(f"metadata is not plain JSON ({type(e).__name__})") from None
    if size > METADATA_MAX_BYTES:
        raise ValueError(f"metadata is {size} bytes (max {METADATA_MAX_BYTES})")
    return v


def validate_audio_words(words: Any, duration_seconds: Optional[float]) -> None:
    """`metadata.words` of an audio asset: [{"w": text, "s": start, "e": end}, …] in seconds,
    each word non-empty, 0 ≤ s < e, starts non-decreasing and never before the previous end,
    and the last end within the audio's duration (+ slack). ValueError names the first fault."""
    if not isinstance(words, list) or not words:
        raise ValueError("metadata.words must be a non-empty list")
    if len(words) > AUDIO_WORDS_MAX:
        raise ValueError(f"metadata.words has {len(words)} entries (max {AUDIO_WORDS_MAX})")
    prev_end = 0.0
    for i, w in enumerate(words):
        if not isinstance(w, dict) or set(w) - {"w", "s", "e", "line"}:
            raise ValueError(f"metadata.words[{i}] must be {{w, s, e[, line]}}")
        text, start, end = w.get("w"), w.get("s"), w.get("e")
        if not isinstance(text, str) or not text.strip() or len(text) > AUDIO_WORD_MAX_CHARS:
            raise ValueError(f"metadata.words[{i}].w must be 1-{AUDIO_WORD_MAX_CHARS} characters")
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                   for x in (start, end)):
            raise ValueError(f"metadata.words[{i}] needs finite numeric s and e")
        if not 0 <= start < end:
            raise ValueError(f"metadata.words[{i}]: need 0 <= s < e, got s={start} e={end}")
        if start < prev_end - 1e-6:
            raise ValueError(f"metadata.words[{i}] starts at {start} before the previous end {prev_end}")
        line = w.get("line")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 0):
            raise ValueError(f"metadata.words[{i}].line must be a non-negative int")
        prev_end = end
    if duration_seconds is not None and prev_end > duration_seconds + AUDIO_TAIL_SLACK_SECONDS:
        raise ValueError(f"the last word ends at {prev_end}s, after the audio ({duration_seconds}s)")


class _Row(BaseModel):
    """A row echoed back to the worker. Unknown columns are ignored so a later migration
    can add one without breaking the pinned worker image."""

    model_config = ConfigDict(extra="ignore")


class MarketingRun(_Row):
    id: str
    run_date: str
    status: str
    stage: str
    content_class: str
    template_id: Optional[str] = None
    source_ref: Optional[str] = None
    worker_version: Optional[str] = None
    dry_run: bool = True
    attempts: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    timings: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    last_error: Optional[str] = None


class MarketingAsset(_Row):
    id: str
    run_id: str
    kind: str
    storage_path: str
    content_type: str
    bytes: Optional[int] = None
    sha256: str
    duration_seconds: Optional[float] = None
    status: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MarketingPost(_Row):
    id: str
    run_id: str
    platform: str
    format: str
    status: str
    title: Optional[str] = None
    caption: str = ""
    asset_ids: List[str] = Field(default_factory=list)
    idempotency_key: str
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    attempts: int = 0
    last_error: Optional[str] = None
    cost_micros: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── requests ─────────────────────────────────────────────────────────────────


class RunClaimRequest(BaseModel):
    run_date: str = Field(..., description="ET calendar day, YYYY-MM-DD")
    worker_version: str = Field(..., min_length=1, max_length=64)
    dry_run: bool = True
    # A per-process nonce the worker mints once. If the response to its own successful claim
    # is lost (timeout after the INSERT committed), the retry finds a fresh `in_progress` row
    # carrying its own nonce and is told CLAIMED again instead of "someone else has it".
    # REQUIRED since the caller-claim fence (2026-09-26): every later worker call presents
    # `X-Marketing-Claim: <attempts>.<claim_nonce>`, and a claim with no nonce could never be
    # presented. Lower-case hex, the shape `uuid.uuid4().hex` mints.
    claim_nonce: str = Field(..., pattern=r"^[0-9a-f]{16,64}$")
    # True = resume an existing resumable run only; never create one. Used by ticks outside
    # the ET window to finish a run killed after the last in-window tick.
    resume_only: bool = False

    @field_validator("run_date")
    @classmethod
    def _iso_date(cls, v: str) -> str:
        # `date.fromisoformat` accepts only YYYY-MM-DD on 3.11 — exactly the contract.
        return date.fromisoformat(v).isoformat()


class RunClaimResponse(BaseModel):
    claimed: bool
    # Why the claim was or was not granted: claimed | already_done | in_progress |
    # media_ready | attempts_exhausted | no_run. The worker branches on this and exits 0 on
    # every non-claimed reason.
    reason: str
    # None only for reason == no_run (a resume_only request found nothing to resume).
    run: Optional[MarketingRun] = None


class RunUpdateRequest(BaseModel):
    """Every field optional; only the ones sent are written."""

    stage: Optional[str] = None
    status: Optional[str] = None
    content_class: Optional[str] = None
    template_id: Optional[str] = None
    source_ref: Optional[str] = None
    last_error: Optional[str] = Field(default=None, max_length=2000)
    # Merged into the existing JSONB, key by key — a stage reports only its own timing.
    timings: Optional[Dict[str, float]] = None
    metadata: Optional[Dict[str, Any]] = None
    finished: bool = False

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, v: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return capped_metadata(v)

    @field_validator("stage")
    @classmethod
    def _stage(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in RUN_STAGES:
            raise ValueError(f"stage must be one of {RUN_STAGES}")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in RUN_STATUSES:
            raise ValueError(f"status must be one of {RUN_STATUSES}")
        return v

    @field_validator("content_class")
    @classmethod
    def _cls(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in CONTENT_CLASSES:
            raise ValueError(f"content_class must be one of {CONTENT_CLASSES}")
        return v


class AssetRegisterRequest(BaseModel):
    kind: str
    ext: str
    sha256: str
    bytes: int = Field(..., ge=0)
    duration_seconds: Optional[float] = Field(default=None, ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        # The worker renders media; copy (script/caption/blog) is server-authored and never
        # uploaded by it. ASSET_KINDS (the CHECK list) is wider on purpose.
        if v not in WORKER_ASSET_KINDS:
            raise ValueError(f"kind must be one of {WORKER_ASSET_KINDS}")
        return v

    @field_validator("ext")
    @classmethod
    def _ext(cls, v: str) -> str:
        v = v.lower().lstrip(".")
        if v not in ASSET_EXTENSIONS:
            raise ValueError(f"ext must be one of {tuple(ASSET_EXTENSIONS)}")
        return v

    @field_validator("sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        v = v.lower()
        if len(v) != _SHA256_HEX_LEN or any(c not in "0123456789abcdef" for c in v):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return v

    @field_validator("metadata")
    @classmethod
    def _metadata(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        return capped_metadata(v) or {}

    @model_validator(mode="after")
    def _kind_matches_ext(self) -> "AssetRegisterRequest":
        allowed = ASSET_KIND_EXTENSIONS.get(self.kind, ())
        if self.ext not in allowed:
            raise ValueError(f"a {self.kind!r} asset must be one of {allowed}, not {self.ext!r}")
        if "words" in self.metadata:
            if self.kind != "audio":
                raise ValueError("only an audio asset carries metadata.words")
            validate_audio_words(self.metadata["words"], self.duration_seconds)
        return self


class SignedUpload(BaseModel):
    method: str = "PUT"
    url: str
    token: str
    bucket: str
    path: str
    content_type: str


class AssetRegisterResponse(BaseModel):
    asset: MarketingAsset
    # None when the asset was already `ready` (a resumed run re-registering the same bytes).
    upload: Optional[SignedUpload] = None


class MarketingAssetView(MarketingAsset):
    """A `ready` asset as the worker reads it back (`GET /runs/{id}/assets`): the row plus its
    public URL, so a later stage re-derives media from ready rows (rules marketing.md §2)
    instead of trusting its own memory of an earlier stage."""
    public_url: Optional[str] = None


class RunAssetsResponse(BaseModel):
    #: The canonical narration of the run (`metadata.voice_asset_id`, written in the SAME
    #: PATCH as `stage=voiced`), verified by the server: kind `audio`, `ready`, this run.
    #: None when the run has none (or the pointer does not verify — logged).
    voice_asset_id: Optional[str] = None
    assets: List[MarketingAssetView] = Field(default_factory=list)


class AssetCompleteResponse(BaseModel):
    asset: MarketingAsset


class PostSpec(BaseModel):
    platform: str
    format: str
    title: Optional[str] = Field(default=None, max_length=500)
    caption: str = Field(default="", max_length=10000)
    asset_ids: List[str] = Field(default_factory=list, max_length=20)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("asset_ids")
    @classmethod
    def _uuids(cls, v: List[str]) -> List[str]:
        # The column is UUID[]; a non-UUID would surface as a 22P02 from PostgREST after the
        # ledger call, not as a 422 that names the field.
        from uuid import UUID

        out = []
        for item in v:
            try:
                out.append(str(UUID(str(item))))
            except (ValueError, AttributeError, TypeError):
                raise ValueError(f"asset_ids must be UUIDs, got {item!r}")
        return out

    @field_validator("platform")
    @classmethod
    def _platform(cls, v: str) -> str:
        if v not in POST_PLATFORMS:
            raise ValueError(f"platform must be one of {POST_PLATFORMS}")
        return v

    @field_validator("format")
    @classmethod
    def _format(cls, v: str) -> str:
        if v not in POST_FORMATS:
            raise ValueError(f"format must be one of {POST_FORMATS}")
        return v


class PostsCreateRequest(BaseModel):
    posts: List[PostSpec] = Field(..., min_length=1, max_length=40)


class PostsCreateResponse(BaseModel):
    posts: List[MarketingPost]


# ── the day's script (Phase 2, migration 173) ────────────────────────────────


class WorkerScript(BaseModel):
    """What the worker needs to voice and render. Captions are NOT here — they are
    server-authored and reach the ledger only through `create_posts`."""

    hook: str
    video_script: List[str]
    cards: List[Dict[str, str]]
    carousel_slides: List[Dict[str, str]]
    disclaimer_card: str
    outlets: List[str] = Field(default_factory=list)


class ScriptKickResponse(BaseModel):
    """`POST /runs/{run_id}/script` always answers 200 with one of these states; content
    outcomes are never HTTP errors (a 4xx/5xx is reserved for the ledger itself):

    * `rest_day`   — not a posting day. Final: close the run `skipped`.
    * `generating` — the writer is working; poll again in ~15 s.
    * `deferred`   — the writer failed transiently; stop this tick (retry_after_seconds).
    * `accepted`   — `script` is set. Final.
    * `rejected`   — the day is skipped. Final. `reason` (SCRIPT_REJECT_REASONS) says why, and
      only `content` means the drafts failed the compliance checks — `violations` is filled
      for that reason alone. `writer_unavailable` is an outage, not a content verdict.
    """

    status: str
    source_ref: Optional[str] = None
    template_id: Optional[str] = None
    retry_after_seconds: Optional[int] = None
    violations: List[str] = Field(default_factory=list)
    script: Optional[WorkerScript] = None
    # Set only when status == "rejected"; one of SCRIPT_REJECT_REASONS.
    reason: Optional[str] = None
