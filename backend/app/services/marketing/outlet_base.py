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

Pure: stdlib, `app.config` and `app.log_redaction` only (no FMP — tests/test_marketing_import_boundary.py).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from app.log_redaction import redact_secrets

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
