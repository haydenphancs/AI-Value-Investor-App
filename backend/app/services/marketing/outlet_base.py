"""
Shared types for the marketing publisher's platform adapters (design doc §12.10, rules/marketing.md §2).

An ADAPTER is the services-side glue between one `marketing_posts` row and one thin platform client
in `app/integrations/` (`x_api.py`, `bluesky.py`): it validates the post BEFORE the claim
(`prepare`), turns the client's typed exceptions into an `Outcome` (`send`), finds out what really
happened after an ambiguous call (`reconcile`) and deletes a published post (`retract`). The
publisher loop (`publisher_service.py`) is the only caller, and the only code that ever calls a
platform.

The one rule every adapter keeps: **an outcome is `ambiguous` unless it is certain.** A request that
may have reached the platform — a read timeout, a dropped connection after sending, a 5xx on a
create, an unreadable answer, a bug in our own code — leaves the post `queued` for reconciliation;
only a definite refusal is `failed`, and only a request that provably never left (`ConnectError`,
`ConnectTimeout`, `PoolTimeout`) is `not_sent` and may be retried. X takes no idempotency key, so
calling a post failed that X had in fact accepted would invite a double post.

IMAGE posts (drop 1, contract C9): an `image` post carries ONE picture — the run's post image, a
1080×1350 JPEG `card` asset whose drawn text the server checked at registration. `prepare` is pure,
so the publisher resolves the picture from our own ledger FIRST (`load_post_image`) and hands
`prepare` a copy of the row with it attached (`POST_IMAGE_KEY`); a send that uploads the picture
downloads it from its public URL with a byte cap and checks its sha256 against the asset row
(`fetch_post_image`) — the bytes a platform receives are exactly the picture the owner reviewed.
Whatever goes wrong with the picture is RETURNED as a `MediaProblem` (never raised: no exception class
here the marketing exception walk would have to classify).

The type definitions are pure (stdlib, `app.config`, `app.log_redaction`, `app.schemas.marketing`);
the two image helpers reach our ledger (`run_service`, imported inside the call) and the public media
bucket (httpx). No FMP — tests/test_marketing_import_boundary.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Sequence, Tuple, Union

import httpx

from app.log_redaction import redact_secrets
from app.schemas.marketing import IMAGE_ROLE_POST, POST_IMAGE_MAX_BYTES, normalize_image_post

logger = logging.getLogger(__name__)

#: Outcome kinds a `send` can end in.
PUBLISHED = "published"
NOT_SENT = "not_sent"
REFUSED = "refused"
AMBIGUOUS = "ambiguous"
#: A middleman (Upload-Post) ACCEPTED the job; the platform result comes later. The row stays
#: `queued` (state `submitted`) and reconcile polls the job — never "published" on the ack alone.
SUBMITTED = "submitted"

#: Reconcile kinds.
FOUND = "found"          # the platform has the post → published
ABSENT = "absent"        # the platform definitely does not have it
UNKNOWN = "unknown"      # could not tell this time (an error, a read blocked by the cap)
PENDING = "pending"      # a submitted job is still being processed — ask again later
FAILED = "failed"        # the platform / middleman reports a DEFINITIVE failure → `failed` + alert

#: Retract kinds.
RETRACTED = "retracted"  # gone from the platform (or was already gone)
RETRY = "retry"          # transient failure — try again next cycle
GAVE_UP = "gave_up"      # a definite refusal — the owner must remove it by hand
MANUAL = "manual"        # the platform has no delete API — the owner must remove it by hand

#: Seconds a NOT-SENT post waits before its next attempt (attempt 1, 2, 3 …; the last value repeats).
RETRY_BACKOFF_SECONDS: Tuple[int, ...] = (300, 1800, 3600)
#: A 401 / bad credential: wait an hour (the owner has to fix a setting first).
AUTH_BACKOFF_SECONDS = 3600
#: Cap on any error text stored on a row or sent to Telegram.
ERROR_TEXT_MAX = 500


class MarketingPublishRefused(Exception):
    """The publisher's own guard refused a post BEFORE any platform call — a URL on X, two
    cashtags, an @mention, over the platform's length, media on a text outlet. The same post can
    never pass, so the row is closed `failed` without ever being claimed. Classified as
    MARKETING_REQUEST_INVALID (app/api/error_response.py)."""

    def __init__(self, message: str = "", *, category: str = "guard") -> None:
        super().__init__(message)
        self.category = category


def scrub(detail: Any) -> str:
    """Error text that may be stored on a row or shown in Telegram: secrets redacted, capped."""
    return redact_secrets(str(detail))[:ERROR_TEXT_MAX]


def text_sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def backoff_seconds(attempt: int) -> int:
    """Wait before attempt `attempt + 1` after a not-sent attempt number `attempt` (1-based)."""
    idx = min(max(attempt, 1), len(RETRY_BACKOFF_SECONDS)) - 1
    return RETRY_BACKOFF_SECONDS[idx]


@dataclass(frozen=True)
class Prepared:
    """A post validated for its platform, ready to send. Built BEFORE the claim (a refusal touches
    no platform and never claims the row)."""

    #: What `send` posts (already validated).
    payload: Dict[str, Any]
    #: sha256 of the exact text sent — recorded in the write-ahead, so a caption can never change
    #: silently between the claim and a resend.
    text_sha256: str
    #: Micro-dollars charged at the claim (X: $0.015 per attempt; free platforms: 0).
    reserve_micros: int = 0
    #: Extra keys merged into `metadata.publish` by the write-ahead claim (Bluesky: rkey + record).
    publish_meta: Dict[str, Any] = field(default_factory=dict)
    #: One log line describing the payload — what a dry run prints instead of sending.
    summary: str = ""
    #: A second write-ahead charge `(op, micros)` the publisher journals AFTER the claim and BEFORE
    #: `send` — a billed call the send makes besides the post itself (X: the image's alt text,
    #: `x_media_alt`). The spend cap is checked for `reserve_micros` plus it, before the claim.
    pre_send_charge: Optional[Tuple[str, int]] = None


@dataclass(frozen=True)
class Outcome:
    """What one `send` ended in. `kind` is one of PUBLISHED / NOT_SENT / REFUSED / AMBIGUOUS /
    SUBMITTED (a middleman accepted the job; the platform result comes later)."""

    kind: str
    #: transport | rate_limited | auth | credits | forbidden | duplicate | invalid | server | bug | guard
    category: str = ""
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    published_at: Optional[str] = None
    error: Optional[str] = None
    #: NOT_SENT only: the earliest time to try again (a 429's reset, an auth back-off). None → the
    #: standard back-off for the attempt number.
    retry_at: Optional[datetime] = None
    #: A charge correction to journal (X 402 credits-depleted: the attempt was not billed → −15000).
    #: On a NOT_SENT it REPLACES the publisher's default (a transport not-sent refunds the whole
    #: claim reserve): an X image post knows exactly which of its calls went out (the alt text may be
    #: billed while the post itself never left).
    refund_micros: int = 0
    #: Keys merged into `metadata.publish` (Bluesky: uri, cid).
    publish_meta: Dict[str, Any] = field(default_factory=dict)
    #: Telegram alert to raise with this outcome (`failed`, `auth` …), or None.
    alert: Optional[str] = None


@dataclass(frozen=True)
class ReconcileResult:
    """What a platform says about a post whose outcome was unknown (or a submitted job). `kind` is
    FOUND / ABSENT / UNKNOWN / PENDING (still processing) / FAILED (a definitive platform failure)."""

    kind: str
    external_id: Optional[str] = None
    external_url: Optional[str] = None
    published_at: Optional[str] = None
    error: Optional[str] = None
    #: ABSENT only: the same post may be sent again WITHOUT any risk of a second copy (Bluesky:
    #: the same record key and record). X is never resend-safe.
    resend_safe: bool = False
    #: Micro-dollars this reconcile actually cost (X owned reads), journaled as a correction to
    #: what was reserved before the read.
    cost_micros: int = 0
    publish_meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetractResult:
    """`kind` is RETRACTED / RETRY / GAVE_UP / MANUAL."""

    kind: str
    error: Optional[str] = None
    cost_micros: int = 0


class Adapter:
    """The interface `publisher_service` drives. Subclasses override what their platform supports."""

    platform: str = ""
    #: Can a published post be deleted through the API (→ a Retract button in Telegram)?
    retractable: bool = False
    #: Is an ABSENT post safe to resend automatically (an idempotent create)? Only Bluesky.
    resend_safe: bool = False
    #: Seconds after the send's start at which reconcile looks; after the last one the post is
    #: escalated to the owner.
    reconcile_schedule: Tuple[int, ...] = (600, 1800, 5400, 14400)
    #: Micro-dollars reserved before one reconcile call / one delete (X bills them; others free).
    reconcile_reserve_micros: int = 0
    retract_cost_micros: int = 0

    def configured(self) -> bool:
        """Every credential (and, for X, a budget) is set — the adapter may publish."""
        raise NotImplementedError

    def configured_for_retract(self) -> bool:
        """Credentials enough to delete (a retract runs even with publishing switched off)."""
        return self.configured()

    def available(self) -> bool:
        """False while an in-memory back-off (a refused login, a rate limit) is open."""
        return True

    def prepare(self, post: Dict[str, Any]) -> Prepared:
        """Validate and build the payload. Pure; raises `MarketingPublishRefused`."""
        raise NotImplementedError

    async def send(self, post: Dict[str, Any], prepared: Prepared) -> Outcome:
        """Publish. Never raises except `CancelledError` — every failure is an `Outcome`."""
        raise NotImplementedError

    async def reconcile(self, post: Dict[str, Any]) -> ReconcileResult:
        raise NotImplementedError

    async def retract(self, post: Dict[str, Any]) -> RetractResult:
        return RetractResult(MANUAL, error=f"{self.platform}: no delete API — remove it by hand")

    def post_url(self, post: Dict[str, Any]) -> Optional[str]:
        url = post.get("external_url")
        return str(url) if url else None


# ── the post image (drop 1, contract C9) ──────────────────────────────────────────────────────────

#: The post image (contract C5/C6): a 1080×1350 (4:5) JPEG of at most POST_IMAGE_MAX_BYTES.
IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1350
IMAGE_MIME = "image/jpeg"
#: The key under which the publisher attaches the resolved `PostImage` to (a COPY of) an image post
#: before `prepare`. Never written to the ledger: the claim is fenced on the row as it was read.
POST_IMAGE_KEY = "_post_image"
#: One download of the picture from its public URL.
IMAGE_FETCH_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
#: The separator between the title and each paragraph in alt text.
_ALT_SEPARATOR = "\n\n"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
#: Tests install an `httpx.MockTransport` here; production uses httpx's own transport.
_fetch_transport: Optional[httpx.AsyncBaseTransport] = None


@dataclass(frozen=True)
class PostImage:
    """The ONE picture an image post carries, as our ledger knows it (never read from the worker's
    word: a READY card of the post's run, verified by the server)."""

    asset_id: str
    #: The object's PUBLIC URL in the marketing-media bucket (unsigned by design, rules §4).
    url: str
    #: The asset row's sha256 (lowercase hex) and size — what a download must match byte for byte.
    sha256: str
    size: int
    #: The accepted script's `image_post` — what the picture says; the alt text is built from it.
    title: str
    paragraphs: Tuple[str, ...]
    mime: str = IMAGE_MIME

    def alt(self, limit: int) -> str:
        return alt_text(self.title, self.paragraphs, limit)

    def meta(self) -> Dict[str, Any]:
        """What a write-ahead / a payload records about the picture (no text: the alt is derived)."""
        return {"asset_id": self.asset_id, "url": self.url, "sha256": self.sha256, "size": self.size,
                "mime": self.mime}


@dataclass(frozen=True)
class MediaProblem:
    """Why an image post's picture cannot be used this time — RETURNED, never raised. `definite`: it
    can never work (the post names the wrong asset, the bytes are not the picture reviewed) — the post
    is refused. Otherwise (a ledger read or a download that failed) it is tried again later."""

    error: str
    definite: bool = False


def image_of(post: Any) -> Optional[PostImage]:
    """The `PostImage` the publisher attached to this post copy, or None."""
    image = post.get(POST_IMAGE_KEY) if isinstance(post, dict) else None
    return image if isinstance(image, PostImage) else None


def alt_text(title: str, paragraphs: Sequence[str], limit: int) -> str:
    """The picture's alt text: its title, then each paragraph, a blank line between them — WHOLE
    paragraphs only, the last ones dropped until it fits `limit` (a sentence cut short could say the
    opposite of the picture: "…is not a recommendation to buy" → "…is not a"). A title longer than
    `limit` on its own is cut at it (impossible within the 600-character on-screen cap: every
    outlet's limit is at least 1000)."""
    text = str(title or "").strip()
    for paragraph in paragraphs:
        candidate = f"{text}{_ALT_SEPARATOR}{str(paragraph).strip()}" if text else str(paragraph).strip()
        if len(candidate) > limit:
            break
        text = candidate
    return text[:max(int(limit), 0)]


async def load_post_image(post: Dict[str, Any]) -> Union[PostImage, MediaProblem]:
    """Resolve an image post's picture from our own ledger. Its `asset_ids` must name exactly ONE
    asset: a READY `card` of the post's own run whose `metadata.image_role` is the post image, a JPEG
    of 1..POST_IMAGE_MAX_BYTES bytes with a sha256 and a storage path whose public URL is https. Its
    words come from the run's ACCEPTED script (`image_post`, what the server checked the drawn text
    against). A read that fails is a retryable `MediaProblem`; anything else wrong is definite.
    Never raises (except CancelledError)."""
    from app.services.marketing.run_service import get_marketing_run_service

    raw_ids = post.get("asset_ids")
    ids = [str(a) for a in raw_ids if a] if isinstance(raw_ids, list) else []
    if len(ids) != 1:
        return MediaProblem(f"an image post carries exactly one picture, it names {len(ids)}", definite=True)
    run_id = post.get("run_id")
    if not run_id:
        return MediaProblem("the image post names no run", definite=True)
    svc = get_marketing_run_service()
    try:
        asset = await svc.get_asset(ids[0])
        script = await svc.get_script(str(run_id)) if isinstance(asset, dict) else None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        return MediaProblem(f"the picture's ledger rows could not be read ({type(e).__name__}: {scrub(e)})")
    if not isinstance(asset, dict):
        return MediaProblem(f"no asset {ids[0]} in the ledger", definite=True)
    md = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
    size = asset.get("bytes")
    sha = str(asset.get("sha256") or "").strip().lower()
    path = asset.get("storage_path")
    wrong = []
    if asset.get("status") != "ready":
        wrong.append(f"status {str(asset.get('status'))[:20]!r}")
    if asset.get("kind") != "card" or md.get("image_role") != IMAGE_ROLE_POST:
        wrong.append("not the run's post image (a card whose image_role is the post image)")
    if str(asset.get("run_id") or "") != str(run_id):
        wrong.append("an asset of another run")
    if str(asset.get("content_type") or "").lower() != IMAGE_MIME:
        wrong.append(f"content type {str(asset.get('content_type'))[:40]!r}")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= POST_IMAGE_MAX_BYTES:
        wrong.append(f"size {str(size)[:20]!r} (1-{POST_IMAGE_MAX_BYTES} bytes)")
    if not _SHA256_RE.fullmatch(sha):
        wrong.append("no sha256")
    if not isinstance(path, str) or not path.strip():
        wrong.append("no storage path")
    if wrong:
        return MediaProblem(f"asset {ids[0]} is not a usable post image: {'; '.join(wrong)}", definite=True)
    url = svc.public_url(str(path))
    if not url.lower().startswith("https://"):
        return MediaProblem("the picture's public URL is not https", definite=True)
    output = script.get("output") if isinstance(script, dict) and script.get("status") == "accepted" else None
    image_post = normalize_image_post(output.get("image_post")) if isinstance(output, dict) else None
    if image_post is None:
        return MediaProblem(f"run {run_id}'s accepted script carries no image_post for the picture's alt text",
                            definite=True)
    return PostImage(asset_id=ids[0], url=url, sha256=sha, size=size, title=image_post["title"],
                     paragraphs=tuple(image_post["paragraphs"]))


async def fetch_post_image(url: str, *, size: int, sha256: str) -> Union[bytes, MediaProblem]:
    """Download the picture from its PUBLIC URL (no credential: the bucket is public by design) —
    reading no more than the `size` bytes the ledger recorded — and check its sha256 against the
    asset row. A transport failure or a status other than 200 is a retryable `MediaProblem`; a body
    longer, shorter or different than recorded is DEFINITE (the bytes are not the picture the owner
    reviewed, and such a picture is never posted). Never raises (except CancelledError)."""
    digest = hashlib.sha256()
    chunks = []
    total = 0
    problem: Optional[MediaProblem] = None
    try:
        expected = int(size)
    except (TypeError, ValueError):
        return MediaProblem(f"unreadable recorded size {str(size)[:20]!r}", definite=True)
    try:
        async with httpx.AsyncClient(timeout=IMAGE_FETCH_TIMEOUT, follow_redirects=False,
                                     transport=_fetch_transport) as client:
            async with client.stream("GET", str(url), headers={"Accept": IMAGE_MIME}) as resp:
                if resp.status_code != 200:
                    problem = MediaProblem(f"the picture download answered HTTP {resp.status_code}")
                else:
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > expected:
                            problem = MediaProblem(f"the picture at its URL is larger than the {expected} bytes "
                                                   "recorded — not the picture reviewed", definite=True)
                            break
                        digest.update(chunk)
                        chunks.append(chunk)
    except asyncio.CancelledError:
        raise
    except Exception as e:   # transport, a closed stream …: retry later — nothing was sent anywhere
        problem = MediaProblem(f"the picture download failed ({type(e).__name__}: {scrub(e)})")
    if problem is None and (total != expected or digest.hexdigest() != str(sha256).strip().lower()):
        problem = MediaProblem(f"the picture at its URL ({total} bytes) is not the one recorded "
                               f"({expected} bytes, sha256 {str(sha256)[:12]}…) — not the picture reviewed",
                               definite=True)
    if problem is not None:
        logger.warning("marketing publish: post image NOT usable url=%s definite=%s: %s", scrub(url),
                       problem.definite, problem.error)
        return problem
    return b"".join(chunks)
