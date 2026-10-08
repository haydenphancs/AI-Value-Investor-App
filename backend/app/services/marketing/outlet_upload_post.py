"""
Upload-Post adapters for the marketing publisher (design doc §12.10, Stage 2; rules/marketing.md §1-§2).

ONE middleman API (`app/integrations/upload_post.py`) publishes to six outlets — TikTok, YouTube and
Instagram get the day's verified MP4 (by its public URL; Upload-Post fetches it), Facebook, LinkedIn
and Threads get text. One adapter instance per platform, one Upload-Post request per post row.

What makes this outlet different (docs.upload-post.com, verified 2026-10-01):

* **An accepted request is not a published post.** Uploads are async: the 200 answer only means
  Upload-Post took the job (`SUBMITTED`; the row stays `queued`). Reconcile polls the job by OUR
  `request_id` — `status` for progress, `history` for the platform's verdict, post URL and post id.
  A per-platform `success: true` can mean merely "Queued"; only a post URL or a platform post id
  makes it `published`. TikTok can silently land a post as an INBOX DRAFT (`fallback_to_inbox`);
  we send `disable_inbox_fallback=true`, and a draft that still appears is reported as a failure the
  owner must finish by hand.
* **Idempotency is real but lasts 24 h.** `request_id` doubles as the `Idempotency-Key`, stored in
  the claim's write-ahead and reused on every resend; a resend is only allowed while the job is
  `not_found` and within 20 h of the first send. Outside that window an unclear post goes to the
  owner, never back to Upload-Post.
* **Owner-action failures are refusals with an alert**: an expired key (401), a plan that does not
  include the platform (403 — TikTok on Free), the monthly quota (429 with `usage`), a platform not
  connected to the profile (400 `invalid_platforms`), a social account that needs reconnecting
  (`account_reauth_required` / `account_checkpoint_required`).
* **Delete** exists for Facebook, YouTube and LinkedIn only; for Instagram, TikTok and Threads
  Telegram tells the owner to remove the post by hand.
* **One key per post.** `request_id` (= the Idempotency-Key) is minted once, at the first send,
  and reused by every later attempt while the post can still go out (20 h) — never overwritten by
  the id Upload-Post answers, which is kept apart (`poll_id`) for the status / history polls. A
  job Upload-Post ACKNOWLEDGED is never "absent": a later `not_found` is an unknown outcome for the
  owner, never a resend.
* **AI disclosure** (rules/marketing.md §1): TikTok `is_aigc`, Instagram `is_ai_generated`, YouTube
  `containsSyntheticMedia`; TikTok also carries "Your brand" (`brand_organic_toggle`) — undisclosed
  self-promotion is For-You-ineligible. Facebook / LinkedIn / Threads text has no API-level AI flag;
  the caption's disclaimer is the disclosure.

Free-tier verification: each successful submit records Upload-Post's account usage before and after
(`metadata.publish.upload_post.usage_before/after`), which answers the one undocumented quota
question — whether one upload costs one credit per platform.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.integrations import upload_post
from app.schemas.marketing import POST_FORMATS_BY_PLATFORM
from app.services.marketing.post_copy import FORBIDDEN_CHARS
from app.services.marketing.outlet_base import (
    ABSENT,
    AMBIGUOUS,
    AUTH_BACKOFF_SECONDS,
    FAILED,
    FOUND,
    GAVE_UP,
    MANUAL,
    NOT_SENT,
    PENDING,
    PUBLISHED,
    REFUSED,
    RETRACTED,
    RETRY,
    SUBMITTED,
    UNKNOWN,
    Adapter,
    MarketingPublishRefused,
    Outcome,
    Prepared,
    ReconcileResult,
    RetractResult,
    scrub,
    text_sha256,
)

logger = logging.getLogger(__name__)

PLATFORMS = ("tiktok", "youtube", "instagram", "facebook", "linkedin", "threads")
VIDEO_PLATFORMS = frozenset({"tiktok", "youtube", "instagram"})
#: Upload-Post's unpublish works for these (not Instagram, TikTok or Threads).
DELETE_PLATFORMS = frozenset({"facebook", "youtube", "linkedin"})
#: A resend reuses the Idempotency-Key, which Upload-Post honours for 24 h — stay well inside it.
RESEND_WINDOW = timedelta(hours=20)
#: Hard limits on the caption (post_copy enforces the same; re-checked at publish time).
CAPTION_LIMITS: Dict[str, int] = {"tiktok": 2200, "instagram": 2200, "youtube": 5000,
                                  "facebook": 5000, "linkedin": 3000, "threads": 500}
YOUTUBE_TITLE_MAX = 100
#: YouTube "Education".
YOUTUBE_CATEGORY_ID = "27"
_IN_FLIGHT = frozenset({"pending", "queued", "processing", "in_progress"})
#: Per-platform error codes that mean the OWNER must reconnect the social account in Upload-Post.
_REAUTH_CODES = frozenset({"account_reauth_required", "account_checkpoint_required", "tiktok_reconnect_required"})
#: A daily per-account cap without a reset time: try again in an hour (the post expires otherwise).
DAILY_CAP_BACKOFF = timedelta(hours=1)
#: The quota reads around an upload (`_usage`) are measurements taken while the send waits — and the
#: /go early window opens only once the send returns (`publisher_service.record_outcome`), with the
#: post already live — so each read is cut off after this long (one attempt, 30 s read timeout, before).
USAGE_READ_TIMEOUT_SECONDS = 2.0


def _failure_text(platform: str, item: Dict[str, Any]) -> str:
    """A per-platform failure in owner terms — a reconnect request when that is what it is."""
    code = str(item.get("error_code") or "").lower()
    reason = item.get("error_message") or item.get("error") or item.get("message") or "the platform refused it"
    if code in _REAUTH_CODES or item.get("reauth_required") is True:
        return f"{platform}: reconnect the account in Upload-Post ({code or 'reauth required'}) — {reason}"
    if code == "account_restricted":
        return f"{platform}: the account is restricted by the platform for now — {reason}"
    return f"{platform}: {reason}"


def _meta(post: Dict[str, Any]) -> Dict[str, Any]:
    return post["metadata"] if isinstance(post.get("metadata"), dict) else {}


def _up_meta(post: Dict[str, Any]) -> Dict[str, Any]:
    pub = _meta(post).get("publish")
    up = pub.get("upload_post") if isinstance(pub, dict) else None
    return up if isinstance(up, dict) else {}


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _first_id(value: Any) -> Optional[str]:
    """`platform_post_id` is a string, an array or null."""
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    return str(value) if value not in (None, "") else None


def platform_fields(platform: str, post: Dict[str, Any]) -> Dict[str, Any]:
    """The per-platform form fields of one request (pure; pinned by tests)."""
    caption = str(post.get("caption") or "")
    if platform == "tiktok":
        return {"title": caption, "tiktok_title": caption, "privacy_level": "PUBLIC_TO_EVERYONE",
                "post_mode": "DIRECT_POST", "disable_inbox_fallback": True, "brand_organic_toggle": True,
                "is_aigc": True}
    if platform == "instagram":
        return {"title": caption, "instagram_title": caption, "media_type": "REELS", "share_to_feed": True,
                "is_ai_generated": True}
    if platform == "youtube":
        title = str(post.get("title") or "")
        return {"title": title, "youtube_title": title, "youtube_description": caption,
                "privacyStatus": "public", "containsSyntheticMedia": True, "categoryId": YOUTUBE_CATEGORY_ID,
                "selfDeclaredMadeForKids": False}
    if platform == "facebook":
        return {"facebook_page_id": (settings.MARKETING_UPLOAD_POST_FACEBOOK_PAGE_ID or "").strip()}
    if platform == "linkedin":
        return {"target_linkedin_page_id": (settings.MARKETING_UPLOAD_POST_LINKEDIN_PAGE_ID or "").strip()}
    if platform == "threads":
        # One post, never an auto-split thread (Upload-Post splits over 500 BYTES by default).
        return {"threads_long_text_as_post": True}
    return {}


class UploadPostAdapter(Adapter):
    resend_safe = True
    #: Polls of a submitted job (seconds after the send): TikTok shows its post id after ~3 min,
    #: a YouTube Short can take longer to process. After the last one the owner decides.
    reconcile_schedule = (600, 1200, 1800, 3600, 7200, 14400, 28800)

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.retractable = platform in DELETE_PLATFORMS
        self.video = platform in VIDEO_PLATFORMS

    def configured(self) -> bool:
        if not upload_post.configured():
            return False
        if self.platform == "facebook":
            return bool((settings.MARKETING_UPLOAD_POST_FACEBOOK_PAGE_ID or "").strip())
        if self.platform == "linkedin":
            # Without the organization page id LinkedIn would post to the member's PERSONAL profile.
            return bool((settings.MARKETING_UPLOAD_POST_LINKEDIN_PAGE_ID or "").strip())
        return True

    def configured_for_retract(self) -> bool:
        return upload_post.configured()

    def prepare(self, post: Dict[str, Any]) -> Prepared:
        platform = self.platform
        expected = POST_FORMATS_BY_PLATFORM.get(platform, ())
        if post.get("format") not in expected:
            raise MarketingPublishRefused(f"{platform}: format {post.get('format')!r} is not published here "
                                          f"(expected {'/'.join(expected) or 'none'})")
        caption = post.get("caption") if isinstance(post.get("caption"), str) else ""
        if not caption.strip():
            raise MarketingPublishRefused(f"{platform}: the post has no caption")
        if len(caption) > CAPTION_LIMITS[platform]:
            raise MarketingPublishRefused(f"{platform}: {len(caption)} characters > {CAPTION_LIMITS[platform]}")
        asset_ids = [str(a) for a in (post.get("asset_ids") or []) if a]
        if self.video:
            if len(asset_ids) != 1:
                raise MarketingPublishRefused(f"{platform}: a video post needs exactly one video asset, "
                                              f"it has {len(asset_ids)}")
        elif asset_ids:
            raise MarketingPublishRefused(f"{platform}: a text post may not carry media")
        if platform == "youtube":
            title = post.get("title") if isinstance(post.get("title"), str) else ""
            if (not title.strip() or len(title) > YOUTUBE_TITLE_MAX
                    or any(c in title for c in FORBIDDEN_CHARS["youtube_title"])):
                raise MarketingPublishRefused("youtube: the title is missing, over 100 characters or has < > / a line break")
            if any(c in caption for c in FORBIDDEN_CHARS["youtube_description"]):
                # The YouTube API refuses < and > in a description (post_copy blocks them upstream).
                raise MarketingPublishRefused("youtube: the description contains < or >")
        stored = _up_meta(post)
        sha = text_sha256(caption)
        first = _parse(stored.get("first_sent_at"))
        if stored.get("request_id") and first is not None and datetime.now(timezone.utc) - first < RESEND_WINDOW:
            # ONE key for the post's whole life: a resend from reconcile, or a new claim after a
            # not-sent attempt, reuses the request id = the Idempotency-Key the first send carried.
            request_id = str(stored["request_id"])
            first_sent_at = stored.get("first_sent_at")
        else:
            try:
                attempt = int(post.get("attempts") or 0) + 1
            except (TypeError, ValueError):
                raise MarketingPublishRefused(f"{platform}: unreadable attempt count") from None
            request_id = f"{post.get('idempotency_key')}:a{attempt}"
            first_sent_at = datetime.now(timezone.utc).isoformat()
        payload = {"kind": "video" if self.video else "text", "request_id": request_id,
                   "external_id": str(post.get("id") or ""), "text": caption,
                   "asset_id": asset_ids[0] if self.video else None,
                   "fields": platform_fields(platform, post)}
        return Prepared(
            payload=payload, text_sha256=sha, reserve_micros=0,
            publish_meta={"upload_post": {**stored, "request_id": request_id, "first_sent_at": first_sent_at}},
            summary=(f"{platform} via upload-post kind={payload['kind']} request_id={request_id} "
                     f"chars={len(caption)} sha256={sha[:12]}"),
        )

    async def _video_url(self, asset_id: str) -> Optional[str]:
        """The verified MP4's public URL, or None when the asset is not a READY video of the ledger."""
        from app.services.marketing.run_service import get_marketing_run_service
        svc = get_marketing_run_service()
        asset = await svc.get_asset(asset_id)
        if not asset or asset.get("status") != "ready" or asset.get("kind") != "video" or not asset.get("storage_path"):
            return None
        return svc.public_url(str(asset["storage_path"]))

    async def _usage(self) -> Optional[Dict[str, Any]]:
        try:
            return await asyncio.wait_for(upload_post.get_usage(), USAGE_READ_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.info("marketing upload-post: usage not read (no answer within %.1f s)", USAGE_READ_TIMEOUT_SECONDS)
            return None
        except Exception as e:   # best effort — a measurement, never a reason to fail a post
            logger.info("marketing upload-post: usage not read (%s: %s)", type(e).__name__, scrub(e))
            return None

    async def send(self, post: Dict[str, Any], prepared: Prepared) -> Outcome:
        platform, payload = self.platform, prepared.payload
        base_meta = dict(prepared.publish_meta["upload_post"])
        video_url: Optional[str] = None
        if payload["kind"] == "video":
            try:
                video_url = await self._video_url(str(payload["asset_id"]))
            except Exception as e:   # our ledger, before anything was sent
                return Outcome(NOT_SENT, "transport", error=scrub(f"video asset lookup failed: {e}"))
            if not video_url:
                return Outcome(REFUSED, "asset", error=f"{platform}: the video asset is not a ready video",
                               alert="failed")
        usage_before = await self._usage()
        try:
            if payload["kind"] == "video":
                res = await upload_post.upload_video(platform=platform, video_url=str(video_url),
                                                     fields=payload["fields"], request_id=payload["request_id"],
                                                     external_id=payload["external_id"])
            else:
                res = await upload_post.upload_text(platform=platform, text=payload["text"],
                                                    fields=payload["fields"], request_id=payload["request_id"],
                                                    external_id=payload["external_id"])
        except upload_post.UploadPostNotSentError as e:
            return Outcome(NOT_SENT, "transport", error=scrub(e))
        except upload_post.UploadPostRateLimitError as e:
            retry_at = getattr(e, "retry_at", None) or datetime.now(timezone.utc) + DAILY_CAP_BACKOFF
            return Outcome(NOT_SENT, "rate_limited", error=scrub(e), retry_at=retry_at)
        except (upload_post.UploadPostAuthError, upload_post.UploadPostNotConfiguredError) as e:
            return Outcome(NOT_SENT, "auth", error=scrub(e),
                           retry_at=datetime.now(timezone.utc) + timedelta(seconds=AUTH_BACKOFF_SECONDS), alert="auth")
        except upload_post.UploadPostReauthError as e:
            return Outcome(REFUSED, "reauth", alert="failed",
                           error=scrub(f"reconnect {platform} in Upload-Post: {e}"))
        except upload_post.UploadPostPlanError as e:
            return Outcome(REFUSED, "plan", alert="failed",
                           error=scrub(f"the Upload-Post plan does not include {platform}: {e}"))
        except upload_post.UploadPostQuotaError as e:
            return Outcome(REFUSED, "quota", alert="failed", error=scrub(f"Upload-Post monthly quota reached: {e}"))
        except upload_post.UploadPostNotConnectedError as e:
            return Outcome(REFUSED, "not_connected", alert="failed",
                           error=scrub(f"{platform} is not connected to the Upload-Post profile: {e}"))
        except upload_post.UploadPostRefusedError as e:
            return Outcome(REFUSED, "invalid", error=scrub(e), alert="failed")
        except upload_post.UploadPostException as e:
            return Outcome(AMBIGUOUS, "server", error=scrub(e), publish_meta={"upload_post": base_meta})
        usage_after = await self._usage()
        meta = {**base_meta, "usage_before": usage_before, "usage_after": usage_after,
                "submitted_at": datetime.now(timezone.utc).isoformat()}
        if res.get("request_id") and str(res["request_id"]) != payload["request_id"]:
            # Upload-Post's own id for the job, used ONLY to poll it; `request_id` (our
            # Idempotency-Key) is never overwritten.
            meta["poll_id"] = str(res["request_id"])
        mode = res.get("mode")
        if mode in ("async", "scheduled"):
            if mode == "scheduled":
                meta["job_id"] = res.get("job_id")
            return Outcome(SUBMITTED, publish_meta={"upload_post": meta})
        result = (res.get("results") or {}).get(platform) if isinstance(res.get("results"), dict) else None
        if not isinstance(result, dict):
            return Outcome(SUBMITTED, publish_meta={"upload_post": meta})
        url = result.get("url") or result.get("post_url")
        pid = _first_id(result.get("post_id") or result.get("platform_post_id") or result.get("video_id"))
        if result.get("skipped"):
            return Outcome(REFUSED, "not_connected", alert="failed", publish_meta={"upload_post": meta},
                           error=f"{platform} is not connected to the Upload-Post profile (skipped)")
        if result.get("success") is True and (url or pid):
            return Outcome(PUBLISHED, external_id=pid or str(url), external_url=str(url) if url else None,
                           published_at=datetime.now(timezone.utc).isoformat(), publish_meta={"upload_post": meta})
        if result.get("success") is False:
            return Outcome(REFUSED, "platform", alert="failed", publish_meta={"upload_post": meta},
                           error=scrub(_failure_text(platform, result)))
        return Outcome(SUBMITTED, publish_meta={"upload_post": meta})

    async def reconcile(self, post: Dict[str, Any]) -> ReconcileResult:
        platform = self.platform
        stored = _up_meta(post)
        request_id = stored.get("poll_id") or stored.get("request_id")
        if not request_id:
            return ReconcileResult(UNKNOWN, error=f"{platform}: no Upload-Post request id recorded")
        try:
            status = await upload_post.get_status(str(request_id))
        except upload_post.UploadPostException as e:
            return ReconcileResult(UNKNOWN, error=scrub(e))
        top = str(status.get("status") or "")
        if top == "not_found":
            if stored.get("submitted_at") or stored.get("job_id"):
                # Upload-Post ACKNOWLEDGED this job: "not found" now is not proof nothing was posted.
                return ReconcileResult(UNKNOWN, error=f"{platform}: Upload-Post acknowledged job {request_id} "
                                                      "but now reports it not found — it will not be resent")
            first = _parse(stored.get("first_sent_at"))
            inside = first is not None and datetime.now(timezone.utc) - first < RESEND_WINDOW
            if inside:
                return ReconcileResult(ABSENT, resend_safe=True)
            return ReconcileResult(UNKNOWN, error=f"{platform}: Upload-Post has no job {request_id}, and the "
                                                  "idempotency window has passed — it will not be resent")
        try:
            history = await upload_post.get_history(str(request_id))
        except upload_post.UploadPostException as e:
            if top in _IN_FLIGHT:
                return ReconcileResult(PENDING, error=scrub(e))
            return ReconcileResult(UNKNOWN, error=scrub(e))
        # The client keeps only rows that NAME our request id; re-checked here (defence in depth: a
        # row of another post on this platform would carry an id a retract could unpublish).
        def ours(rows: Any) -> List[Dict[str, Any]]:
            return [h for h in rows or [] if isinstance(h, dict)
                    and str(h.get("platform") or "").lower() == platform and h.get("request_id") == request_id]

        mine = ours(history.get("history"))
        for item in mine:
            url = item.get("post_url")
            pid = _first_id(item.get("platform_post_id"))
            if item.get("fallback_to_inbox"):
                return ReconcileResult(FAILED, error=f"{platform}: it landed as an INBOX DRAFT, not a public post "
                                                     "— publish it by hand in the TikTok app")
            if item.get("success") is True and (url or pid):
                when = _parse(item.get("upload_timestamp"))
                return ReconcileResult(FOUND, external_id=pid or str(url), external_url=str(url) if url else None,
                                       published_at=when.isoformat() if when else None,
                                       publish_meta={"upload_post": {**stored, "platform_post_id": pid}})
        statuses = [r for r in status.get("results") or []
                    if isinstance(r, dict) and str(r.get("platform") or "").lower() == platform]
        if any(str(r.get("status") or "").lower() in ("retryable", "queued", "processing") for r in statuses):
            return ReconcileResult(PENDING, error=f"{platform}: still processing at Upload-Post")
        definitive = [r for r in statuses if str(r.get("status") or "").lower() == "failed"]
        in_progress = ours(history.get("in_progress"))
        if in_progress or top in _IN_FLIGHT:
            return ReconcileResult(PENDING, error=f"{platform}: still processing at Upload-Post")
        failed = [i for i in mine if i.get("success") is False]
        if failed:
            return ReconcileResult(FAILED, error=scrub(_failure_text(platform, failed[-1])))
        if definitive:
            # A per-platform "failed" in the status answer (never "retryable") with no success row:
            # Upload-Post has said it outright — e.g. it could not fetch the video.
            return ReconcileResult(FAILED, error=scrub(_failure_text(platform, definitive[-1])))
        if any(r.get("skipped") or str(r.get("status") or "").lower() == "skipped" for r in statuses):
            return ReconcileResult(FAILED, error=f"{platform} is not connected to the Upload-Post profile")
        # "completed" with no history row yet (history lags), or a heuristic top-level "failed" with
        # no per-platform verdict: not proof either way.
        return ReconcileResult(UNKNOWN, error=f"{platform}: job status {top or '?'} with no platform result yet")

    async def retract(self, post: Dict[str, Any]) -> RetractResult:
        platform = self.platform
        if platform not in DELETE_PLATFORMS:
            return RetractResult(MANUAL, error=f"{platform}: Upload-Post cannot delete here — remove it by hand")
        post_id = post.get("external_id") or _up_meta(post).get("platform_post_id")
        if not post_id or str(post_id).startswith("http"):
            return RetractResult(GAVE_UP, error=f"{platform}: the platform post id is unknown — remove it by hand")
        try:
            await upload_post.unpublish(platform=platform, post_id=str(post_id))
        except (upload_post.UploadPostNotSentError, upload_post.UploadPostAmbiguousError,
                upload_post.UploadPostRateLimitError, upload_post.UploadPostAuthError,
                upload_post.UploadPostNotConfiguredError) as e:
            return RetractResult(RETRY, error=scrub(e))
        except upload_post.UploadPostRefusedError as e:
            return RetractResult(GAVE_UP, error=scrub(e))
        except upload_post.UploadPostException as e:
            return RetractResult(RETRY, error=scrub(e))
        return RetractResult(RETRACTED)

    def post_url(self, post: Dict[str, Any]) -> Optional[str]:
        url = post.get("external_url")
        return str(url) if url else None


ADAPTERS: Dict[str, UploadPostAdapter] = {p: UploadPostAdapter(p) for p in PLATFORMS}
