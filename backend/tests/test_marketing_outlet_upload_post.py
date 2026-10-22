"""The Upload-Post OUTLET (`app/services/marketing/outlet_upload_post.py`) — design doc §12.10, Stage 2.

One adapter instance per platform: TikTok, YouTube and Instagram get the day's verified MP4 by its
PUBLIC URL; Facebook, LinkedIn and Threads get text — or (drop 1) the run's post image, by its public
URL after a checked download, through /upload_photos. Driven here through the REAL thin client
(`app/integrations/upload_post.py`) over an `httpx.MockTransport`, so the client's outcome split and
the adapter's mapping of it are tested together — the seam where a mistake turns into a double post
(an accepted job read as "not sent") or a lost one (a pending job read as failed).

What these pin, by what a regression would cost:

1. An accepted request is NOT a published post: an async ack is SUBMITTED (the row stays queued);
   only a post URL or a platform post id makes it PUBLISHED / FOUND.
2. The request id is the Idempotency-Key: `<key>:a<attempts+1>` for a fresh attempt, the STORED id on
   a resend, and a resend is only offered while Upload-Post has no job AND we are inside 20 h.
3. Every send ends in exactly one Outcome kind; the owner-action refusals (key, plan, monthly quota,
   not connected, reconnect) alert; a 5xx / timeout / 409 is AMBIGUOUS, never "not sent".
4. Reconcile trusts only history rows that name OUR request id on OUR platform; a TikTok inbox draft
   is a failure; Upload-Post's own retries are PENDING.
5. The AI-disclosure / brand / privacy fields per platform (rules/marketing.md §1) are pinned.
6. End to end through the publisher: SUBMITTED → PENDING → FOUND, and AMBIGUOUS → not_found →
   a resend carrying the SAME Idempotency-Key.

Hermetic: Upload-Post is a MockTransport on the client's `_client`; the asset ledger is the
in-memory PostgREST fake; settings are monkeypatched; backend/conftest.py blocks sockets.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest

from app.integrations import upload_post
from app.services.marketing import outlet_upload_post as oup
from app.services.marketing import outlet_base, outlets, post_copy
from app.services.marketing import publisher_service as pub
from app.services.marketing import run_service as mrs
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
    POST_IMAGE_KEY,
    MarketingPublishRefused,
    Outcome,
    PostImage,
    Prepared,
)
from test_marketing_publisher import Env, _assert_review_kept
from test_marketing_run_service import FakeSupabase

#: Opaque (not JWT-shaped): only the client's per-call literal replacement hides it.
API_KEY = "UPkeyOUTLET0123456789abcdefghijKLMNOPqrs"
PROFILE = "caydex"
OWNER_EMAIL = "owner-mailbox@example.com"
FB_PAGE = "104857600123456"
LI_PAGE = "86753090"
SB_URL = "https://xyz.supabase.co"
BUCKET = "marketing-media"
POST_ID = "5f0c2b1e-9a77-4c1e-8f43-0d8f1a2b3c4d"
ASSET_ID = "0b6f3a52-3c1d-4f7e-9a10-2b8c4d6e8f01"
VIDEO_PATH = "2026-10-01/video-3f9a.mp4"
VIDEO_URL = f"{SB_URL}/storage/v1/object/public/{BUCKET}/{VIDEO_PATH}"
DAY = "2026-10-01"
CAPTION = ("Time in the market beats timing it. #investing #financialliteracy Link in bio. "
           "Caydex · Educational, impersonal information — not investment advice.")
TITLE = "Why time in the market compounds"
TT_URL = "https://www.tiktok.com/@caydex/video/7412000000000000001"
TT_ID = "7412000000000000001"

VIDEO = ("tiktok", "youtube", "instagram")
TEXT = ("facebook", "linkedin", "threads")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(platform: str) -> str:
    return "video" if platform in VIDEO else "text"


def _rid(platform: str, n: int = 1) -> str:
    return f"{DAY}:{platform}:{_fmt(platform)}:a{n}"


def _ts(value: Any) -> datetime:
    parsed = mrs._parse_ts(value)
    assert parsed is not None, f"not a timestamp: {value!r}"
    return parsed


def _close(a: datetime, b: datetime, tolerance: float = 5.0) -> bool:
    return abs((a - b).total_seconds()) < tolerance


# ── Upload-Post over MockTransport ──────────────────────────────────────────────────────────────


class UpServer:
    """Records every request and answers per PATH (after `/api`): a FIFO of scripted answers per
    route, then a sticky answer, then — for `/uploadposts/me` only — the account's usage. An answer
    is `(status, body[, headers])`, a callable(request) → Response, or an exception CLASS raised
    where a transport would raise it. An unscripted call is recorded in `unexpected` (the fixture
    fails the test on it) and answered 599, so it can never pass silently."""

    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.queues: Dict[str, List[Any]] = defaultdict(list)
        self.sticky: Dict[str, Any] = {}
        self.unexpected: List[str] = []
        self.usage = 3

    def answer(self, path: str, *items: Any) -> None:
        self.queues[path].extend(items)

    def always(self, path: str, item: Any) -> None:
        self.sticky[path] = item

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        path = path[len("/api"):] if path.startswith("/api") else path
        if path in ("/upload", "/upload_text", "/upload_photos"):
            self.usage += 1
        if self.queues.get(path):
            item = self.queues[path].pop(0)
        elif path in self.sticky:
            item = self.sticky[path]
        elif path == "/uploadposts/me":
            item = (200, {"success": True, "message": "ok", "email": OWNER_EMAIL, "plan": "free",
                          "api_usage": {"count": self.usage, "limit": 10}})
        else:
            self.unexpected.append(f"{request.method} {path}")
            return httpx.Response(599, json={"success": False, "error": "unscripted"})
        if isinstance(item, type) and issubclass(item, BaseException):
            if item is httpx.InvalidURL:
                raise item(f"simulated {item.__name__}")
            raise item(f"simulated {item.__name__} {request.headers.get('Authorization')}", request=request)
        if callable(item):
            return item(request)
        status, body, *rest = item
        headers = rest[0] if rest else None
        if body is None:
            return httpx.Response(status, headers=headers)
        if isinstance(body, (bytes, str)):
            return httpx.Response(status, content=body, headers=headers)
        return httpx.Response(status, json=body, headers=headers)

    def calls(self, path: str) -> List[httpx.Request]:
        return [r for r in self.requests if r.url.path == f"/api{path}"]

    @property
    def uploads(self) -> List[httpx.Request]:
        return [r for r in self.requests if r.url.path in ("/api/upload", "/api/upload_text", "/api/upload_photos")]

    @property
    def sequence(self) -> List[str]:
        return [f"{r.method} {r.url.path[len('/api'):]}" for r in self.requests]


def _parts(request: httpx.Request) -> List[Tuple[str, str]]:
    """The multipart form as (name, value) pairs, in order; asserts no part is a file."""
    ctype = request.headers["content-type"]
    assert ctype.startswith("multipart/form-data; boundary="), ctype
    msg = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + request.content,
                                   policy=email.policy.HTTP)
    out = []
    for part in msg.iter_parts():
        assert part.get_filename() is None
        name = part.get_param("name", header="content-disposition")
        out.append((name, part.get_payload(decode=True).decode("utf-8")))
    return out


@pytest.fixture
def creds(monkeypatch):
    for name, value in (("MARKETING_UPLOAD_POST_API_KEY", API_KEY), ("MARKETING_UPLOAD_POST_USER", PROFILE),
                        ("MARKETING_UPLOAD_POST_FACEBOOK_PAGE_ID", FB_PAGE),
                        ("MARKETING_UPLOAD_POST_LINKEDIN_PAGE_ID", LI_PAGE),
                        ("SUPABASE_URL", SB_URL), ("MARKETING_MEDIA_BUCKET", BUCKET),
                        # Batch 3: the three new switches at their declared defaults, whatever this machine's
                        # environment says — the outlet reads the Instagram trial mode at send time.
                        ("MARKETING_VIDEO_EXTRA_PLATFORMS", ""), ("MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat"),
                        ("MARKETING_INSTAGRAM_TRIAL_MODE", "off")):
        monkeypatch.setattr(oup.settings, name, value)


@pytest.fixture
def up(monkeypatch, creds):
    server = UpServer()
    monkeypatch.setattr(upload_post, "_client", httpx.AsyncClient(transport=httpx.MockTransport(server.handler)))
    yield server
    assert server.unexpected == [], f"unscripted Upload-Post calls: {server.unexpected}"


def _asset(**over: Any) -> Dict[str, Any]:
    row = {"id": ASSET_ID, "run_id": "11111111-2222-4333-8444-555555555555", "kind": "video",
           "status": "ready", "storage_path": VIDEO_PATH, "content_type": "video/mp4", "bytes": 4_200_000,
           "metadata": {}}
    row.update(over)
    return row


@pytest.fixture
def ledger(monkeypatch):
    """The asset ledger the adapter resolves a video by. `_video_url` imports
    `get_marketing_run_service` INSIDE the call, so the SOURCE module's binding is what to patch."""
    fake = FakeSupabase()
    svc = mrs.MarketingRunService(supabase=fake)
    fake.tables[mrs.ASSETS].rows.append(_asset())
    monkeypatch.setattr(mrs, "get_marketing_run_service", lambda: svc)
    svc.fake = fake  # type: ignore[attr-defined]
    return svc


def _post(platform: str, **over: Any) -> Dict[str, Any]:
    video = platform in VIDEO
    post = {
        "id": POST_ID, "run_id": "11111111-2222-4333-8444-555555555555", "platform": platform,
        "format": _fmt(platform), "status": "approved", "caption": CAPTION,
        "title": TITLE if platform == "youtube" else None,
        "asset_ids": [ASSET_ID] if video else [], "idempotency_key": f"{DAY}:{platform}:{_fmt(platform)}",
        "attempts": 0, "metadata": {"dry_run": False},
    }
    post.update(over)
    return post


def _queued(platform: str = "tiktok", *, request_id: Any = "default", sent_ago: timedelta = timedelta(minutes=15),
            **stored_extra: Any) -> Dict[str, Any]:
    """A queued row as reconcile / a resend sees it: the write-ahead's request id + first send."""
    stored: Dict[str, Any] = {"first_sent_at": (_now() - sent_ago).isoformat(), **stored_extra}
    if request_id == "default":
        stored["request_id"] = _rid(platform)
    elif request_id is not None:
        stored["request_id"] = request_id
    return _post(platform, status="queued", attempts=1,
                 metadata={"dry_run": False, "publish": {"state": "submitted", "upload_post": stored}})


async def _send(platform: str, **over: Any) -> Tuple[Outcome, Prepared]:
    adapter = oup.UploadPostAdapter(platform)
    post = _post(platform, **over)
    prepared = adapter.prepare(post)
    outcome = await adapter.send({**post, "status": "queued", "attempts": 1}, prepared)
    return outcome, prepared


def _ack(rid: str) -> Tuple[int, Dict[str, Any]]:
    return 200, {"success": True, "message": "Upload initiated successfully in background.",
                 "request_id": rid, "total_platforms": 1}


def _status(top: str, *results: Dict[str, Any], rid: Optional[str] = None) -> Tuple[int, Dict[str, Any]]:
    return 200, {"request_id": rid or _rid("tiktok"), "external_id": POST_ID, "status": top, "completed": 0,
                 "total": 1, "results": list(results), "last_update": "2026-10-01T14:00:00Z"}


NOT_FOUND = (404, {"status": "not_found", "message": "No upload found for this request_id"})


def _item(platform: str = "tiktok", *, rid: Any = "default", **over: Any) -> Dict[str, Any]:
    item = {"platform": platform, "success": True, "platform_post_id": TT_ID, "post_url": TT_URL,
            "error_message": None, "fallback_to_inbox": False, "external_id": POST_ID,
            "upload_timestamp": "2026-10-01T14:03:00Z", "user_email": OWNER_EMAIL}
    if rid == "default":
        item["request_id"] = _rid(platform)
    elif rid is not None:
        item["request_id"] = rid
    item.update(over)
    return item


def _hist(*items: Dict[str, Any], in_progress: Tuple[Dict[str, Any], ...] = ()) -> Tuple[int, Dict[str, Any]]:
    return 200, {"history": list(items), "in_progress": list(in_progress), "total": len(items),
                 "page": 1, "limit": 20}


# ── configuration ───────────────────────────────────────────────────────────────────────────────


def test_one_adapter_per_platform_with_its_flags():
    assert oup.PLATFORMS == ("tiktok", "youtube", "instagram", "facebook", "linkedin", "threads")
    for platform in oup.PLATFORMS:
        adapter = oup.ADAPTERS[platform]
        assert adapter.platform == platform and outlets.ADAPTERS[platform] is adapter
        assert adapter.video is (platform in VIDEO)
        assert adapter.retractable is (platform in ("facebook", "youtube", "linkedin"))
        assert adapter.resend_safe is True
        assert adapter.reconcile_reserve_micros == 0 and adapter.retract_cost_micros == 0
    schedule = oup.UploadPostAdapter.reconcile_schedule
    assert list(schedule) == sorted(schedule) and len(set(schedule)) == len(schedule)
    # Every check happens long inside the resend window (the idempotency key lives 24 h).
    assert schedule[-1] < oup.RESEND_WINDOW.total_seconds() < 24 * 3600


@pytest.mark.parametrize("platform", oup.PLATFORMS)
@pytest.mark.parametrize("key, user, ok", [
    (API_KEY, PROFILE, True),
    (None, PROFILE, False),
    (API_KEY, None, False),
    ("   ", PROFILE, False),
    (API_KEY, "  ", False),
])
def test_configured_needs_the_key_and_the_profile(monkeypatch, creds, platform, key, user, ok):
    monkeypatch.setattr(oup.settings, "MARKETING_UPLOAD_POST_API_KEY", key)
    monkeypatch.setattr(oup.settings, "MARKETING_UPLOAD_POST_USER", user)
    adapter = oup.UploadPostAdapter(platform)
    assert adapter.configured() is ok
    assert adapter.configured_for_retract() is ok


@pytest.mark.parametrize("platform, setting, other", [
    ("facebook", "MARKETING_UPLOAD_POST_FACEBOOK_PAGE_ID", "linkedin"),
    ("linkedin", "MARKETING_UPLOAD_POST_LINKEDIN_PAGE_ID", "facebook"),
])
@pytest.mark.parametrize("value", [None, "", "   "])
def test_facebook_and_linkedin_also_need_their_page_id(monkeypatch, creds, platform, setting, other, value):
    """Without the organization page id LinkedIn would post to the member's PERSONAL profile."""
    monkeypatch.setattr(oup.settings, setting, value)
    assert oup.UploadPostAdapter(platform).configured() is False
    # A delete needs only the key and the profile; the other page and the rest are unaffected.
    assert oup.UploadPostAdapter(platform).configured_for_retract() is True
    assert oup.UploadPostAdapter(other).configured() is True
    for p in ("tiktok", "youtube", "instagram", "threads"):
        assert oup.UploadPostAdapter(p).configured() is True


def test_enabled_platforms_lists_only_complete_upload_post_outlets(monkeypatch, creds):
    monkeypatch.setattr(outlets.settings, "MARKETING_PUBLISH_PLATFORMS", "tiktok, facebook,LinkedIn,threads,youtube")
    monkeypatch.setattr(oup.settings, "MARKETING_UPLOAD_POST_LINKEDIN_PAGE_ID", None)
    assert outlets.enabled_platforms() == ["tiktok", "facebook", "threads", "youtube"]
    monkeypatch.setattr(oup.settings, "MARKETING_UPLOAD_POST_API_KEY", None)
    assert outlets.enabled_platforms() == []          # fail-closed: no key, no Upload-Post outlet


def test_retract_capable_only_where_upload_post_can_delete(monkeypatch, creds):
    for platform in ("facebook", "youtube", "linkedin"):
        assert outlets.retract_capable(platform) is True
    for platform in ("instagram", "tiktok", "threads"):
        assert outlets.retract_capable(platform) is False
    monkeypatch.setattr(oup.settings, "MARKETING_UPLOAD_POST_USER", None)
    assert outlets.retract_capable("facebook") is False


def test_caption_limits_match_post_copy():
    """The publish-time re-check must agree with the limits the copy was composed under."""
    assert oup.CAPTION_LIMITS == {"tiktok": 2200, "instagram": 2200, "youtube": 5000, "facebook": 5000,
                                  "linkedin": 3000, "threads": 500}
    for platform, limit in oup.CAPTION_LIMITS.items():
        field = "youtube_description" if platform == "youtube" else platform
        assert post_copy.LIMITS[field] == limit
    assert oup.YOUTUBE_TITLE_MAX == post_copy.LIMITS["youtube_title"] == 100


# ── prepare: the guard before the claim ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("platform, fmt", [
    ("tiktok", "text"), ("youtube", "text"), ("instagram", None), ("instagram", "carousel"),
    # Facebook / LinkedIn video is published since Batch 3 (an EXTRA post, tested below); a carousel never is.
    ("facebook", "carousel"), ("linkedin", "carousel"), ("threads", "carousel"),
    # Image posts go to Facebook, LinkedIn and Threads only: TikTok stays video, YouTube has no
    # photo route, Instagram stays Reels (drop 1).
    ("tiktok", "image"), ("youtube", "image"), ("instagram", "image"),
])
def test_prepare_refuses_a_format_the_outlet_does_not_publish(creds, platform, fmt):
    with pytest.raises(MarketingPublishRefused, match="is not published here"):
        oup.UploadPostAdapter(platform).prepare(_post(platform, format=fmt))


@pytest.mark.parametrize("platform", ["tiktok", "youtube", "facebook", "threads"])
@pytest.mark.parametrize("caption", ["", "   \n\t", None, 42, ["a"]])
def test_prepare_refuses_an_empty_caption(creds, platform, caption):
    with pytest.raises(MarketingPublishRefused, match="no caption"):
        oup.UploadPostAdapter(platform).prepare(_post(platform, caption=caption))


@pytest.mark.parametrize("platform", oup.PLATFORMS)
def test_the_caption_limit_is_inclusive(creds, platform):
    limit = oup.CAPTION_LIMITS[platform]
    adapter = oup.UploadPostAdapter(platform)
    prepared = adapter.prepare(_post(platform, caption="a" * limit))
    assert prepared.payload["text"] == "a" * limit
    with pytest.raises(MarketingPublishRefused, match=f"{limit + 1} characters > {limit}"):
        adapter.prepare(_post(platform, caption="a" * (limit + 1)))


@pytest.mark.parametrize("platform", VIDEO)
@pytest.mark.parametrize("assets, n", [([], 0), (None, 0), (["", None], 0), ([ASSET_ID, "second"], 2)])
def test_a_video_post_needs_exactly_one_asset(creds, platform, assets, n):
    with pytest.raises(MarketingPublishRefused, match=f"exactly one video asset, it has {n}"):
        oup.UploadPostAdapter(platform).prepare(_post(platform, asset_ids=assets))


@pytest.mark.parametrize("platform", TEXT)
def test_a_text_post_may_not_carry_media(creds, platform):
    with pytest.raises(MarketingPublishRefused, match="may not carry media"):
        oup.UploadPostAdapter(platform).prepare(_post(platform, asset_ids=[ASSET_ID]))


@pytest.mark.parametrize("title", [None, "", "   ", 7, "a" * 101, "Gains < losses", "Patience > timing",
                                   "Two\nlines", "Carriage\rreturn"])
def test_a_youtube_title_must_exist_fit_and_avoid_brackets_and_line_breaks(creds, title):
    with pytest.raises(MarketingPublishRefused, match="youtube: the title"):
        oup.UploadPostAdapter("youtube").prepare(_post("youtube", title=title))


@pytest.mark.parametrize("title", ["a" * 100, "Patience → compounding", "Why diversification lowers risk"])
def test_a_valid_youtube_title_passes(creds, title):
    prepared = oup.UploadPostAdapter("youtube").prepare(_post("youtube", title=title))
    assert prepared.payload["fields"]["youtube_title"] == title


def test_a_youtube_description_with_angle_brackets_is_refused_before_the_claim(creds):
    with pytest.raises(MarketingPublishRefused):
        oup.UploadPostAdapter("youtube").prepare(_post("youtube", caption="Time in the market > timing it."))


@pytest.mark.parametrize("platform", oup.PLATFORMS)
def test_prepare_builds_the_payload(creds, platform):
    adapter = oup.UploadPostAdapter(platform)
    t0 = _now()
    prepared = adapter.prepare(_post(platform))
    p = prepared.payload
    assert p["kind"] == _fmt(platform) and p["request_id"] == _rid(platform)
    assert p["external_id"] == POST_ID and p["text"] == CAPTION
    assert p["asset_id"] == (ASSET_ID if platform in VIDEO else None)
    assert p["fields"] == oup.platform_fields(platform, _post(platform))
    assert prepared.text_sha256 == hashlib.sha256(CAPTION.encode("utf-8")).hexdigest()
    assert prepared.reserve_micros == 0
    meta = prepared.publish_meta["upload_post"]
    assert meta["request_id"] == _rid(platform) and _ts(meta["first_sent_at"]) >= t0 - timedelta(seconds=1)
    assert _rid(platform) in prepared.summary and "upload-post" in prepared.summary


@pytest.mark.parametrize("attempts, suffix", [(0, "a1"), (None, "a1"), (2, "a3"), ("1", "a2")])
def test_a_fresh_attempt_gets_key_colon_attempt_plus_one(creds, attempts, suffix):
    prepared = oup.UploadPostAdapter("tiktok").prepare(_post("tiktok", attempts=attempts))
    assert prepared.payload["request_id"] == f"{DAY}:tiktok:video:{suffix}"
    assert prepared.publish_meta["upload_post"]["request_id"] == f"{DAY}:tiktok:video:{suffix}"


def test_an_unreadable_attempt_count_is_refused(creds):
    with pytest.raises(MarketingPublishRefused, match="unreadable attempt count"):
        oup.UploadPostAdapter("tiktok").prepare(_post("tiktok", attempts="x"))


def test_a_resend_reuses_the_stored_request_id_and_first_send_time(creds):
    """A queued row (reconcile's resend): the SAME id = the same Idempotency-Key, and the window
    keeps counting from the FIRST send — a resend never extends it."""
    first = (_now() - timedelta(hours=5)).isoformat()
    stored = {"request_id": "2026-10-01:tiktok:video:a1", "first_sent_at": first,
              "usage_before": {"count": 3, "limit": 10}, "submitted_at": first}
    post = _post("tiktok", status="queued", attempts=1,
                 metadata={"dry_run": False, "publish": {"state": "unknown", "upload_post": stored}})
    prepared = oup.UploadPostAdapter("tiktok").prepare(post)
    assert prepared.payload["request_id"] == "2026-10-01:tiktok:video:a1"     # not a2
    assert prepared.publish_meta["upload_post"] == stored                    # every stored key kept


def test_a_queued_row_without_a_stored_id_gets_a_fresh_one(creds):
    post = _post("tiktok", status="queued", attempts=1, metadata={"dry_run": False, "publish": {"state": "sending"}})
    assert oup.UploadPostAdapter("tiktok").prepare(post).payload["request_id"] == _rid("tiktok", 2)


def test_one_key_for_the_posts_whole_life_inside_the_window(creds):
    """Back to `approved` after a NOT_SENT: the next attempt REUSES the first key (review 2026-10-01
    — one Idempotency-Key per post, so a request that did land after all can never become a second
    job), keeping the first-send time."""
    old = (_now() - timedelta(hours=3)).isoformat()
    post = _post("tiktok", status="approved", attempts=1, metadata={
        "dry_run": False, "publish": {"state": "not_sent", "upload_post": {"request_id": _rid("tiktok"),
                                                                          "first_sent_at": old}}})
    prepared = oup.UploadPostAdapter("tiktok").prepare(post)
    meta = prepared.publish_meta["upload_post"]
    assert prepared.payload["request_id"] == meta["request_id"] == _rid("tiktok")
    assert meta["first_sent_at"] == old


def test_a_new_key_only_after_the_idempotency_window(creds):
    old = (_now() - oup.RESEND_WINDOW - timedelta(minutes=1)).isoformat()
    post = _post("tiktok", status="approved", attempts=1, metadata={
        "dry_run": False, "publish": {"upload_post": {"request_id": _rid("tiktok"), "first_sent_at": old}}})
    meta = oup.UploadPostAdapter("tiktok").prepare(post).publish_meta["upload_post"]
    assert meta["request_id"] == _rid("tiktok", 2) and meta["first_sent_at"] != old


@pytest.mark.parametrize("metadata", [None, "garbage", {"publish": "garbage"}, {"publish": {"upload_post": [1]}}])
def test_malformed_metadata_never_crashes_prepare(creds, metadata):
    post = _post("threads", status="queued", attempts=1, metadata=metadata)
    assert oup.UploadPostAdapter("threads").prepare(post).payload["request_id"] == _rid("threads", 2)


# ── platform fields (AI disclosure, brand, privacy, targets) ───────────────────────────────────


def test_platform_fields_are_pinned(creds):
    assert oup.platform_fields("tiktok", _post("tiktok")) == {
        "title": CAPTION, "tiktok_title": CAPTION, "privacy_level": "PUBLIC_TO_EVERYONE",
        "post_mode": "DIRECT_POST", "disable_inbox_fallback": True, "brand_organic_toggle": True,
        "is_aigc": True, "cover_timestamp": 500}
    assert oup.platform_fields("instagram", _post("instagram")) == {
        "title": CAPTION, "instagram_title": CAPTION, "media_type": "REELS", "share_to_feed": True,
        "is_ai_generated": True, "thumb_offset": "500"}
    assert oup.platform_fields("youtube", _post("youtube")) == {
        "title": TITLE, "youtube_title": TITLE, "youtube_description": CAPTION, "privacyStatus": "public",
        "containsSyntheticMedia": True, "categoryId": "27", "selfDeclaredMadeForKids": False}
    assert oup.platform_fields("facebook", _post("facebook")) == {"facebook_page_id": FB_PAGE}
    assert oup.platform_fields("linkedin", _post("linkedin")) == {"target_linkedin_page_id": LI_PAGE}
    assert oup.platform_fields("threads", _post("threads")) == {"threads_long_text_as_post": True}
    assert oup.platform_fields("mastodon", _post("threads")) == {}


_COVER_KEYS = {"cover_timestamp", "thumb_offset"}


def test_every_video_post_pins_its_cover_to_the_opening_card(creds):
    """Review round 2 (medium): the burned narration captions may name a Form 4 filer (allowed in
    narration), so a platform's auto-picked cover could show a person's name. Every video — template
    and lesson alike, nothing here reads the authorship — pins its cover to the opening card where
    Upload-Post allows it: TikTok `cover_timestamp` (integer ms), Instagram `thumb_offset` (ms, a string),
    both VIDEO_COVER_MS = 500 (inside card 0, which runs from t = 0 for ≥ 1.2 s — the timeline side is
    pinned in tests/test_marketing_video.py). YouTube has no reliable pin and gets none."""
    tiktok = oup.platform_fields("tiktok", _post("tiktok"))
    assert tiktok["cover_timestamp"] == oup.VIDEO_COVER_MS == 500 and type(tiktok["cover_timestamp"]) is int
    assert oup.platform_fields("instagram", _post("instagram"))["thumb_offset"] == "500"
    assert not _COVER_KEYS & set(oup.platform_fields("youtube", _post("youtube")))
    for platform in ("tiktok", "instagram"):
        assert oup.UploadPostAdapter(platform).prepare(_post(platform)).payload["fields"] == \
            oup.platform_fields(platform, _post(platform))


@pytest.mark.parametrize("platform", oup.PLATFORMS)
@pytest.mark.parametrize("fmt", ["text", "image", None, "carousel"])
def test_no_cover_field_on_anything_but_a_video(creds, platform, fmt):
    """The cover pin is a VIDEO field: a text or image post (or a format no outlet publishes) never
    carries it — on the text fields, on the photo fields, or on any platform."""
    post = _post(platform, format=fmt)
    assert not _COVER_KEYS & set(oup.platform_fields(platform, post))
    assert not _COVER_KEYS & set(oup.photo_fields(platform, post, ALT))


def test_page_ids_are_stripped(monkeypatch, creds):
    monkeypatch.setattr(oup.settings, "MARKETING_UPLOAD_POST_FACEBOOK_PAGE_ID", f"  {FB_PAGE}\n")
    monkeypatch.setattr(oup.settings, "MARKETING_UPLOAD_POST_LINKEDIN_PAGE_ID", f"\t{LI_PAGE} ")
    assert oup.platform_fields("facebook", _post("facebook")) == {"facebook_page_id": FB_PAGE}
    assert oup.platform_fields("linkedin", _post("linkedin")) == {"target_linkedin_page_id": LI_PAGE}


def _expected_parts(platform: str) -> List[Tuple[str, str]]:
    media = ("video", VIDEO_URL) if platform in VIDEO else ("title", CAPTION)
    own = [("user", PROFILE), ("platform[]", platform), media, ("async_upload", "true"),
           ("request_id", _rid(platform)), ("external_id", POST_ID)]
    rendered = [(k, ("true" if v else "false") if isinstance(v, bool) else str(v))
                for k, v in oup.platform_fields(platform, _post(platform)).items()]
    return own + rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", oup.PLATFORMS)
async def test_the_request_actually_sent(up, ledger, platform):
    path = "/upload" if platform in VIDEO else "/upload_text"
    up.answer(path, _ack(_rid(platform)))
    outcome, _prepared = await _send(platform)
    assert outcome.kind == SUBMITTED
    assert up.sequence == ["GET /uploadposts/me", f"POST {path}", "GET /uploadposts/me"]
    req = up.uploads[0]
    assert req.headers["idempotency-key"] == _rid(platform)
    assert req.headers["authorization"] == f"Apikey {API_KEY}"
    assert _parts(req) == _expected_parts(platform)
    if platform in VIDEO:
        assert dict(_parts(req))["video"] == VIDEO_URL                 # the asset's PUBLIC URL
    # AI disclosure travels on the request itself (rules/marketing.md §1).
    flags = dict(_parts(req))
    if platform == "tiktok":
        assert flags["is_aigc"] == "true" and flags["brand_organic_toggle"] == "true"
        assert flags["disable_inbox_fallback"] == "true" and flags["privacy_level"] == "PUBLIC_TO_EVERYONE"
    elif platform == "instagram":
        assert flags["is_ai_generated"] == "true" and flags["media_type"] == "REELS"
    elif platform == "youtube":
        assert flags["containsSyntheticMedia"] == "true" and flags["privacyStatus"] == "public"
    # the cover pin travels on the request too (review round 2): TikTok and Instagram videos only
    assert flags.get("cover_timestamp") == ("500" if platform == "tiktok" else None)
    assert flags.get("thumb_offset") == ("500" if platform == "instagram" else None)


# ── send: the outcome matrix ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_async_ack_is_submitted_with_usage_before_and_after(up, ledger):
    up.answer("/upload", _ack(_rid("tiktok")))
    t0 = _now()
    outcome, prepared = await _send("tiktok")
    assert outcome.kind == SUBMITTED and outcome.category == "" and outcome.alert is None
    assert outcome.external_id is None and outcome.external_url is None and outcome.error is None
    meta = outcome.publish_meta["upload_post"]
    assert meta["request_id"] == _rid("tiktok")
    assert meta["first_sent_at"] == prepared.publish_meta["upload_post"]["first_sent_at"]
    assert meta["usage_before"] == {"count": 3, "limit": 10}
    assert meta["usage_after"] == {"count": 4, "limit": 10}
    assert _ts(meta["submitted_at"]) >= t0 - timedelta(seconds=1)
    assert OWNER_EMAIL not in repr(outcome)                          # /me's e-mail never leaves the client


@pytest.mark.asyncio
async def test_usage_is_a_best_effort_measurement(up, ledger):
    up.answer("/uploadposts/me", (500, {"success": False, "error": "boom"}), httpx.ConnectError)
    up.answer("/upload", _ack(_rid("tiktok")))
    outcome, _ = await _send("tiktok")
    assert outcome.kind == SUBMITTED
    meta = outcome.publish_meta["upload_post"]
    assert meta["usage_before"] is None and meta["usage_after"] is None


@pytest.mark.asyncio
async def test_a_slow_usage_read_never_holds_the_outcome(up, ledger, monkeypatch):
    """Review 2026-10-07: the quota reads around an upload had no bound of their own (one attempt, a 30 s
    read timeout), and the /go early window opens only once `send` returns — with the post already live —
    so a slow /uploadposts/me let a live post's first scanner taps count as people. Each read is now cut
    off after USAGE_READ_TIMEOUT_SECONDS: the same outcome, the reading None."""
    assert 0 < oup.USAGE_READ_TIMEOUT_SECONDS <= 2.0
    calls: List[int] = []

    async def slow_usage() -> Dict[str, Any]:
        calls.append(1)
        await asyncio.sleep(30)
        return {"count": 99, "limit": 10}

    monkeypatch.setattr(upload_post, "get_usage", slow_usage)
    monkeypatch.setattr(oup, "USAGE_READ_TIMEOUT_SECONDS", 0.05)
    up.answer("/upload", _ack(_rid("tiktok")))
    loop = asyncio.get_running_loop()
    started = loop.time()
    outcome, _ = await _send("tiktok")
    assert loop.time() - started < 5.0                                  # never the 30 s read
    assert outcome.kind == SUBMITTED and len(calls) == 2                # before AND after the upload
    meta = outcome.publish_meta["upload_post"]
    assert meta["usage_before"] is None and meta["usage_after"] is None
    assert meta["request_id"] == _rid("tiktok") and _ts(meta["submitted_at"])
    assert len(up.uploads) == 1                                         # the upload itself went out once


@pytest.mark.asyncio
async def test_a_scheduled_202_is_submitted_with_its_job_id(up, ledger):
    up.answer("/upload", (202, {"success": True, "job_id": "job-77", "message": "queued"}))
    outcome, _ = await _send("tiktok")
    assert outcome.kind == SUBMITTED
    meta = outcome.publish_meta["upload_post"]
    assert meta["job_id"] == "job-77" and meta["request_id"] == _rid("tiktok")


@pytest.mark.asyncio
@pytest.mark.parametrize("result, ext_id, ext_url", [
    ({"success": True, "url": TT_URL, "post_id": TT_ID}, TT_ID, TT_URL),
    ({"success": True, "url": TT_URL, "post_id": ["", TT_ID, "other"]}, TT_ID, TT_URL),
    ({"success": True, "post_url": TT_URL, "platform_post_id": TT_ID}, TT_ID, TT_URL),
    ({"success": True, "url": TT_URL}, TT_URL, TT_URL),
    ({"success": True, "video_id": TT_ID}, TT_ID, None),
])
async def test_a_sync_success_with_a_url_or_id_is_published(up, ledger, result, ext_id, ext_url):
    up.answer("/upload", (200, {"success": True, "request_id": _rid("tiktok"), "results": {"tiktok": result},
                                "usage": {"count": 4, "limit": 10}}))
    t0 = _now()
    outcome, _ = await _send("tiktok")
    assert outcome.kind == PUBLISHED and outcome.external_id == ext_id and outcome.external_url == ext_url
    assert _ts(outcome.published_at) >= t0 - timedelta(seconds=1)
    assert outcome.publish_meta["upload_post"]["request_id"] == _rid("tiktok")


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [{"success": True, "message": "Queued"}, {"success": None}, {}])
async def test_a_sync_answer_without_a_url_or_id_is_only_submitted(up, ledger, result):
    """A per-platform `success: true` can mean merely "Queued" — never published on that alone."""
    up.answer("/upload", (200, {"success": True, "results": {"tiktok": result}}))
    outcome, _ = await _send("tiktok")
    assert outcome.kind == SUBMITTED


@pytest.mark.asyncio
@pytest.mark.parametrize("result, wanted", [
    ({"success": False, "error": "token expired", "error_code": "account_reauth_required"},
     ["reconnect the account in Upload-Post", "account_reauth_required", "token expired"]),
    ({"success": False, "error": "complete the security check", "error_code": "Account_Checkpoint_Required"},
     ["reconnect the account in Upload-Post", "account_checkpoint_required"]),
    ({"success": False, "error": "session invalid", "reauth_required": True},
     ["reconnect the account in Upload-Post", "reauth required"]),
    ({"success": False, "error": "paused 24h", "error_code": "account_restricted"},
     ["restricted by the platform", "paused 24h"]),
    ({"success": False, "error": "Video too short"}, ["tiktok: Video too short"]),
    ({"success": False}, ["tiktok: the platform refused it"]),
])
async def test_a_sync_failure_is_refused_with_an_alert(up, ledger, result, wanted):
    up.answer("/upload", (200, {"success": False, "results": {"tiktok": result}}))
    outcome, _ = await _send("tiktok")
    assert outcome.kind == REFUSED and outcome.category == "platform" and outcome.alert == "failed"
    for text in wanted:
        assert text in outcome.error, outcome.error


@pytest.mark.asyncio
async def test_an_async_ack_naming_another_valid_id_is_polled_by_theirs(up, ledger):
    """Upload-Post's own id is kept APART (`poll_id`) for status / history; our request id — the
    Idempotency-Key — is never overwritten (review 2026-10-01)."""
    up.answer("/upload", _ack("up-job-0001"))
    outcome, prepared = await _send("tiktok")
    meta = outcome.publish_meta["upload_post"]
    assert outcome.kind == SUBMITTED and meta["poll_id"] == "up-job-0001"
    assert meta["request_id"] == prepared.payload["request_id"]


@pytest.mark.asyncio
async def test_an_async_ack_with_a_request_id_that_is_not_an_id_is_ambiguous(up, ledger):
    up.answer("/upload", _ack("not an id\n" + "x" * 300))
    outcome, prepared = await _send("tiktok")
    assert outcome.kind == AMBIGUOUS
    assert outcome.publish_meta["upload_post"]["request_id"] == prepared.payload["request_id"]


@pytest.mark.asyncio
async def test_a_sync_answer_with_a_request_id_that_is_not_an_id_keeps_ours(up, ledger):
    up.answer("/upload", (200, {"success": True, "request_id": "not an id\n" + "x" * 300,
                                "results": {"tiktok": {"success": True, "message": "Queued"}}}))
    outcome, prepared = await _send("tiktok")
    assert outcome.kind == SUBMITTED
    assert outcome.publish_meta["upload_post"]["request_id"] == prepared.payload["request_id"]


@pytest.mark.asyncio
async def test_a_sync_skipped_platform_is_refused_as_not_connected(up, ledger):
    up.answer("/upload", (200, {"success": True, "results": {"tiktok": {"success": False, "skipped": True}}}))
    outcome, _ = await _send("tiktok")
    assert outcome.kind == REFUSED and outcome.category == "not_connected" and outcome.alert == "failed"
    assert "not connected" in outcome.error


def _epoch_in(seconds: int) -> str:
    return str(int((_now() + timedelta(seconds=seconds)).timestamp()))


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, kind, category, alert, wanted", [
    pytest.param((401, {"success": False, "message": "Invalid or expired token"}), NOT_SENT, "auth", "auth",
                 "Invalid or expired token", id="401-key"),
    pytest.param((403, {"success": False, "message": "TikTok is not available on the Free plan"}), REFUSED,
                 "plan", "failed", "plan does not include tiktok", id="403-plan"),
    pytest.param((429, {"success": False, "message": "This upload would exceed your monthly limit.",
                        "usage": {"count": 10, "limit": 10}}), REFUSED, "quota", "failed",
                 "monthly quota", id="429-monthly-quota"),
    pytest.param((400, {"success": False, "message": "No valid platforms",
                        "invalid_platforms": {"tiktok": "not connected"}}), REFUSED, "not_connected", "failed",
                 "not connected to the Upload-Post profile", id="400-invalid-platforms"),
    pytest.param((400, {"success": False, "message": "Reconnect", "error_code": "account_reauth_required"}),
                 REFUSED, "reauth", "failed", "reconnect tiktok in Upload-Post", id="400-reauth"),
    pytest.param((400, {"success": False, "message": "Bad caption"}), REFUSED, "invalid", "failed",
                 "Bad caption", id="400-plain"),
    pytest.param(httpx.ConnectError, NOT_SENT, "transport", None, "not sent", id="connect-error"),
    pytest.param(httpx.ConnectTimeout, NOT_SENT, "transport", None, "not sent", id="connect-timeout"),
    pytest.param(httpx.ReadTimeout, AMBIGUOUS, "server", None, "outcome unknown", id="read-timeout"),
    pytest.param(httpx.RemoteProtocolError, AMBIGUOUS, "server", None, "outcome unknown", id="dropped"),
    pytest.param((500, {"success": False, "error": "internal"}), AMBIGUOUS, "server", None, "HTTP 500", id="500"),
    pytest.param((502, None), AMBIGUOUS, "server", None, "HTTP 502", id="502"),
    pytest.param((503, b"<html>down</html>"), AMBIGUOUS, "server", None, "HTTP 503", id="503"),
    pytest.param((409, {"success": False, "message": "job exists"}), AMBIGUOUS, "server", None, "HTTP 409",
                 id="409-idempotency-conflict"),
    pytest.param((408, None), AMBIGUOUS, "server", None, "HTTP 408", id="408"),
    pytest.param((200, b"not json"), AMBIGUOUS, "server", None, "unreadable", id="200-unreadable"),
    pytest.param((200, {"success": False}), AMBIGUOUS, "server", None, "without success", id="200-no-success"),
])
async def test_the_upload_outcome_matrix(up, ledger, answer, kind, category, alert, wanted):
    up.answer("/upload", answer)
    t0 = _now()
    outcome, prepared = await _send("tiktok")
    assert (outcome.kind, outcome.category, outcome.alert) == (kind, category, alert), outcome
    assert wanted in outcome.error, outcome.error
    assert len(up.uploads) == 1 and up.sequence[-1] == "POST /upload"   # no usage-after on a failure
    if kind == NOT_SENT and category == "auth":
        assert _close(outcome.retry_at, t0 + timedelta(seconds=AUTH_BACKOFF_SECONDS))
    elif kind == NOT_SENT:
        assert outcome.retry_at is None                                  # the standard back-off applies
    if kind == AMBIGUOUS:
        # Reconcile polls by the id the write-ahead stored; the outcome keeps it too.
        assert outcome.publish_meta["upload_post"] == prepared.publish_meta["upload_post"]
    assert API_KEY not in (outcome.error or "")


@pytest.mark.asyncio
async def test_a_daily_cap_429_waits_an_hour_not_the_per_minute_reset(up, ledger):
    """`violations` = the rolling per-platform daily cap: the per-minute X-RateLimit-Reset riding on
    every answer would send us straight back into it."""
    up.answer("/upload", (429, {"success": False, "message": "Daily limit reached",
                                "violations": [{"platform": "tiktok", "message": "TikTok: 15/15 posts in 24h"}]},
                          {"X-RateLimit-Reset": _epoch_in(30)}))
    t0 = _now()
    outcome, _ = await _send("tiktok")
    assert outcome.kind == NOT_SENT and outcome.category == "rate_limited" and outcome.alert is None
    assert _close(outcome.retry_at, t0 + oup.DAILY_CAP_BACKOFF)
    assert "15/15" in outcome.error


@pytest.mark.asyncio
async def test_a_per_minute_429_comes_back_at_the_window_reset(up, ledger):
    up.answer("/upload", (429, {"success": False, "message": "Too many requests"},
                          {"X-RateLimit-Reset": _epoch_in(120)}))
    t0 = _now()
    outcome, _ = await _send("tiktok")
    assert outcome.kind == NOT_SENT and outcome.category == "rate_limited"
    assert _close(outcome.retry_at, t0 + timedelta(seconds=120))


@pytest.mark.asyncio
async def test_a_restricted_account_comes_back_when_the_platform_says(up, ledger):
    up.answer("/upload", (403, {"success": False, "error_code": "account_restricted", "message": "parked",
                                "retry_after_seconds": 7200}))
    t0 = _now()
    outcome, _ = await _send("tiktok")
    assert outcome.kind == NOT_SENT and outcome.category == "rate_limited"
    assert _close(outcome.retry_at, t0 + timedelta(hours=2))


@pytest.mark.asyncio
async def test_the_api_key_never_reaches_an_outcome(up, ledger):
    up.answer("/upload", (401, {"success": False, "message": f"Invalid token Apikey {API_KEY}"}))
    outcome, _ = await _send("tiktok")
    assert outcome.kind == NOT_SENT and API_KEY not in repr(outcome)
    up.answer("/upload_text", httpx.ConnectError)                    # the transport text echoes the header
    outcome, _ = await _send("facebook")
    assert outcome.kind == NOT_SENT and API_KEY not in repr(outcome)


@pytest.mark.asyncio
async def test_a_key_removed_after_prepare_is_an_auth_not_sent_with_no_request(up, ledger, monkeypatch):
    adapter = oup.UploadPostAdapter("threads")
    prepared = adapter.prepare(_post("threads"))
    monkeypatch.setattr(oup.settings, "MARKETING_UPLOAD_POST_API_KEY", None)
    outcome = await adapter.send(_post("threads", status="queued", attempts=1), prepared)
    assert outcome.kind == NOT_SENT and outcome.category == "auth" and outcome.alert == "auth"
    assert up.requests == []


# ── send: the video asset ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_over", [
    {"status": "pending_upload"}, {"status": "failed"}, {"kind": "audio"}, {"kind": "card"},
    {"storage_path": None}, {"storage_path": ""}, {"id": "some-other-asset"},
])
async def test_a_video_asset_that_is_not_a_ready_video_is_refused_before_any_call(up, ledger, asset_over):
    ledger.fake.tables[mrs.ASSETS].rows[0].update(asset_over)
    outcome, _ = await _send("instagram")
    assert outcome.kind == REFUSED and outcome.category == "asset" and outcome.alert == "failed"
    assert "not a ready video" in outcome.error
    assert up.requests == []                                         # not even the usage read


@pytest.mark.asyncio
async def test_a_failing_asset_lookup_is_not_sent(up, ledger, monkeypatch):
    async def outage(asset_id):
        raise mrs.MarketingRunError("get_asset failed: PostgREST 503")

    monkeypatch.setattr(ledger, "get_asset", outage)
    outcome, _ = await _send("youtube")
    assert outcome.kind == NOT_SENT and outcome.category == "transport"
    assert "video asset lookup failed" in outcome.error and "503" in outcome.error
    assert up.requests == []


@pytest.mark.asyncio
async def test_a_non_https_media_url_is_refused_and_never_uploaded(up, ledger, monkeypatch):
    """Upload-Post fetches the video by URL: the client refuses anything but a public https URL."""
    monkeypatch.setattr(oup.settings, "SUPABASE_URL", "http://127.0.0.1:54321")
    outcome, _ = await _send("tiktok")
    assert outcome.kind == REFUSED and outcome.alert == "failed" and "https" in outcome.error
    assert up.uploads == []


# ── reconcile ───────────────────────────────────────────────────────────────────────────────────


async def _reconcile(post: Dict[str, Any]):
    return await oup.UploadPostAdapter(post["platform"]).reconcile(post)


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [None, {}, {"request_id": ""}, "garbage"])
async def test_reconcile_without_a_stored_request_id_is_unknown(up, stored):
    post = _post("tiktok", status="queued", metadata={"dry_run": False, "publish": {"upload_post": stored}})
    result = await _reconcile(post)
    assert result.kind == UNKNOWN and "no Upload-Post request id" in result.error
    assert up.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ago", [timedelta(minutes=10), timedelta(hours=19, minutes=59)])
async def test_not_found_inside_the_window_is_absent_and_resend_safe(up, ago):
    up.answer("/uploadposts/status", NOT_FOUND)
    result = await _reconcile(_queued("tiktok", sent_ago=ago))
    assert result.kind == ABSENT and result.resend_safe is True
    assert up.sequence == ["GET /uploadposts/status"]                # no history read needed
    assert up.requests[0].url.params["request_id"] == _rid("tiktok")


@pytest.mark.asyncio
@pytest.mark.parametrize("ago", [timedelta(hours=20, seconds=1), timedelta(hours=23), timedelta(days=3)])
async def test_not_found_after_20_hours_is_never_resent(up, ago):
    up.answer("/uploadposts/status", NOT_FOUND)
    result = await _reconcile(_queued("tiktok", sent_ago=ago))
    assert result.kind == UNKNOWN and result.resend_safe is False
    assert "will not be resent" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("first_sent_at", [None, "", "not-a-time"])
async def test_not_found_without_a_readable_first_send_is_never_resent(up, first_sent_at):
    up.answer("/uploadposts/status", NOT_FOUND)
    post = _queued("tiktok")
    post["metadata"]["publish"]["upload_post"]["first_sent_at"] = first_sent_at
    result = await _reconcile(post)
    assert result.kind == UNKNOWN and result.resend_safe is False


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    pytest.param((404, {"message": "Not Found"}), id="bare-404"),
    pytest.param((404, None), id="empty-404"),
    pytest.param((500, {"error": "boom"}), id="500"),
    pytest.param(httpx.ConnectError, id="connect-error"),
    pytest.param(httpx.ReadTimeout, id="read-timeout"),
    pytest.param((200, {"results": []}), id="no-status"),
])
async def test_a_status_read_that_is_not_a_clear_answer_is_unknown(up, answer):
    """Only Upload-Post SAYING `not_found` licenses a resend — a bare 404 never does."""
    up.answer("/uploadposts/status", answer)
    result = await _reconcile(_queued("tiktok"))
    assert result.kind == UNKNOWN and result.resend_safe is False
    assert up.sequence == ["GET /uploadposts/status"]


@pytest.mark.asyncio
async def test_a_history_row_with_a_post_url_is_found(up):
    up.answer("/uploadposts/status", _status("completed", {"platform": "tiktok", "status": "completed",
                                                            "success": True}))
    up.answer("/uploadposts/history", _hist(_item()))
    post = _queued("tiktok", usage_before={"count": 3, "limit": 10})
    result = await _reconcile(post)
    assert result.kind == FOUND and result.external_id == TT_ID and result.external_url == TT_URL
    assert result.published_at == "2026-10-01T14:03:00+00:00"          # parsed, never raw upstream text
    assert result.publish_meta == {"upload_post": {**post["metadata"]["publish"]["upload_post"],
                                                   "platform_post_id": TT_ID}}
    assert up.calls("/uploadposts/history")[0].url.params["request_id"] == _rid("tiktok")
    assert OWNER_EMAIL not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("over, ext_id, ext_url", [
    ({"platform_post_id": ["", TT_ID, "7412999"]}, TT_ID, TT_URL),
    ({"platform_post_id": None}, TT_URL, TT_URL),
    ({"platform_post_id": [], "post_url": TT_URL}, TT_URL, TT_URL),
    ({"post_url": None}, TT_ID, None),
    ({"upload_timestamp": None}, TT_ID, TT_URL),
])
async def test_found_takes_the_first_post_id_or_the_url(up, over, ext_id, ext_url):
    up.answer("/uploadposts/status", _status("completed"))
    up.answer("/uploadposts/history", _hist(_item(**over)))
    result = await _reconcile(_queued("tiktok"))
    assert result.kind == FOUND and result.external_id == ext_id and result.external_url == ext_url


@pytest.mark.asyncio
async def test_success_without_a_url_or_id_is_not_found(up):
    up.answer("/uploadposts/status", _status("completed"))
    up.answer("/uploadposts/history", _hist(_item(platform_post_id=None, post_url=None)))
    result = await _reconcile(_queued("tiktok"))
    assert result.kind == UNKNOWN


@pytest.mark.asyncio
async def test_a_tiktok_inbox_draft_is_a_failure_the_owner_finishes(up):
    up.answer("/uploadposts/status", _status("completed"))
    up.answer("/uploadposts/history", _hist(_item(fallback_to_inbox=True, post_url=None, platform_post_id=None)))
    result = await _reconcile(_queued("tiktok"))
    assert result.kind == FAILED and "INBOX DRAFT" in result.error and "by hand" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["retryable", "queued", "processing", "Retryable"])
async def test_a_platform_still_working_is_pending(up, status):
    up.answer("/uploadposts/status", _status("completed", {"platform": "tiktok", "status": status}))
    up.answer("/uploadposts/history", _hist(_item(success=False, post_url=None, platform_post_id=None,
                                                  error_message="transient")))
    result = await _reconcile(_queued("tiktok"))
    assert result.kind == PENDING


@pytest.mark.asyncio
async def test_an_in_progress_history_row_is_pending(up):
    up.answer("/uploadposts/status", _status("completed"))
    up.answer("/uploadposts/history", _hist(in_progress=(_item(success=None, post_url=None,
                                                                 platform_post_id=None),)))
    assert (await _reconcile(_queued("tiktok"))).kind == PENDING


@pytest.mark.asyncio
@pytest.mark.parametrize("top", ["pending", "queued", "processing", "in_progress", "IN_PROGRESS"])
async def test_a_job_in_flight_is_pending(up, top):
    up.answer("/uploadposts/status", _status(top))
    up.answer("/uploadposts/history", _hist())
    assert (await _reconcile(_queued("tiktok"))).kind == PENDING


@pytest.mark.asyncio
async def test_another_platforms_progress_does_not_hold_ours(up):
    up.answer("/uploadposts/status", _status("completed", {"platform": "instagram", "status": "processing"}))
    up.answer("/uploadposts/history", _hist())
    assert (await _reconcile(_queued("tiktok"))).kind == UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("over, wanted", [
    ({"error_message": "Video too short"}, "tiktok: Video too short"),
    ({"error_message": "token expired", "error_code": "account_reauth_required"},
     "tiktok: reconnect the account in Upload-Post (account_reauth_required) — token expired"),
    ({"error_message": "checkpoint", "error_code": "account_checkpoint_required"}, "reconnect the account"),
    ({"error_message": "parked", "error_code": "account_restricted"}, "restricted by the platform"),
    ({"error_message": None}, "tiktok: the platform refused it"),
])
async def test_a_failed_history_row_is_failed(up, over, wanted):
    up.answer("/uploadposts/status", _status("completed", {"platform": "tiktok", "status": "failed"}))
    up.answer("/uploadposts/history", _hist(_item(success=False, post_url=None, platform_post_id=None, **over)))
    result = await _reconcile(_queued("tiktok"))
    assert result.kind == FAILED and wanted in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [{"platform": "tiktok", "status": "skipped"}, {"platform": "TikTok", "skipped": True}])
async def test_a_skipped_platform_is_failed_as_not_connected(up, entry):
    up.answer("/uploadposts/status", _status("completed", entry))
    up.answer("/uploadposts/history", _hist())
    result = await _reconcile(_queued("tiktok"))
    assert result.kind == FAILED and "not connected" in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("top", ["completed", "failed"])
async def test_a_finished_job_without_a_platform_verdict_is_unknown(up, top):
    """History lags "completed", and a top-level "failed" can be Upload-Post's no-activity-for-1-h
    heuristic: neither is proof either way."""
    up.answer("/uploadposts/status", _status(top, {"platform": "tiktok", "status": "completed", "success": True}))
    up.answer("/uploadposts/history", _hist())
    result = await _reconcile(_queued("tiktok"))
    assert result.kind == UNKNOWN and "no platform result yet" in result.error


@pytest.mark.asyncio
async def test_a_history_row_naming_another_request_is_never_trusted(up):
    """Through the client: the row of ANOTHER post (or one that names no request) is dropped, so a
    later retract can never unpublish someone else's post."""
    up.answer("/uploadposts/status", _status("completed"))
    up.answer("/uploadposts/history", _hist(_item(rid="2026-09-30:tiktok:video:a1"), _item(rid=None)))
    result = await _reconcile(_queued("tiktok"))
    assert result.kind == UNKNOWN and result.external_id is None


@pytest.mark.asyncio
async def test_the_adapter_rechecks_the_request_id_and_platform_itself(monkeypatch, creds):
    """Defence in depth behind the client's filter: rows handed to the adapter that name another
    request, another platform, or are not dicts at all are ignored."""
    async def status(request_id):
        return {"status": "completed", "results": [], "request_id": request_id}

    ours = _rid("tiktok")

    async def history(request_id):
        assert request_id == ours
        return {"history": [_item(rid="2026-09-30:tiktok:video:a1"), _item("instagram", rid=ours),
                            _item(rid=None), "junk", None],
                "in_progress": [_item(rid="2026-09-30:tiktok:video:a1", success=None),
                                _item("instagram", rid=ours, success=None), _item(rid=None, success=None), "junk"]}

    monkeypatch.setattr(upload_post, "get_status", status)
    monkeypatch.setattr(upload_post, "get_history", history)
    result = await _reconcile(_queued("tiktok"))
    # Each row fails exactly ONE check (request id OR platform), so dropping either check flips this
    # to FOUND (history) or PENDING (in_progress).
    assert result.kind == UNKNOWN and result.external_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [{"submitted_at": "2026-10-01T13:00:00+00:00"}, {"job_id": "up-job-0001"}])
async def test_an_acknowledged_job_reported_not_found_is_never_resent(up, ack):
    """Upload-Post took the job (an async ack); "not found" afterwards is not proof nothing posted."""
    up.answer("/uploadposts/status", NOT_FOUND)
    result = await _reconcile(_queued("tiktok", **ack))
    assert result.kind == UNKNOWN and result.resend_safe is False
    assert "acknowledged" in result.error and "will not be resent" in result.error


@pytest.mark.asyncio
async def test_reconcile_polls_by_upload_posts_own_id_when_it_answered_one(up):
    up.answer("/uploadposts/status", _status("completed", rid="up-job-0001"))
    up.answer("/uploadposts/history", _hist(_item(rid="up-job-0001")))
    result = await _reconcile(_queued("tiktok", poll_id="up-job-0001"))
    assert result.kind == FOUND and result.external_id == TT_ID
    assert [r.url.params["request_id"] for r in up.requests] == ["up-job-0001", "up-job-0001"]


@pytest.mark.asyncio
@pytest.mark.parametrize("entry, wanted", [
    ({"platform": "tiktok", "status": "failed", "error_message": "could not fetch the video"},
     "tiktok: could not fetch the video"),
    ({"platform": "TikTok", "status": "FAILED", "message": "media rejected"}, "tiktok: media rejected"),
    ({"platform": "tiktok", "status": "failed"}, "tiktok: the platform refused it"),
])
async def test_a_per_platform_failed_status_without_a_history_row_is_failed(up, entry, wanted):
    up.answer("/uploadposts/status", _status("completed", entry))
    up.answer("/uploadposts/history", _hist())
    result = await _reconcile(_queued("tiktok"))
    assert result.kind == FAILED and wanted in result.error


@pytest.mark.asyncio
async def test_another_platforms_failed_status_is_not_ours(up):
    up.answer("/uploadposts/status", _status("completed", {"platform": "instagram", "status": "failed",
                                                            "error_message": "nope"}))
    up.answer("/uploadposts/history", _hist())
    assert (await _reconcile(_queued("tiktok"))).kind == UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("top, kind", [("processing", PENDING), ("completed", UNKNOWN), ("failed", UNKNOWN)])
async def test_a_history_read_that_fails_is_pending_only_while_in_flight(up, top, kind):
    up.answer("/uploadposts/status", _status(top))
    up.answer("/uploadposts/history", (500, {"error": "boom"}))
    result = await _reconcile(_queued("tiktok"))
    assert result.kind == kind and "HTTP 500" in result.error


# ── retract ─────────────────────────────────────────────────────────────────────────────────────


def _published(platform: str, **over: Any) -> Dict[str, Any]:
    cols: Dict[str, Any] = {"status": "published", "external_id": "104857600123456_998877",
                            "external_url": f"https://{platform}.example/p/1"}
    cols.update(over)
    return _post(platform, **cols)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["facebook", "youtube", "linkedin"])
async def test_a_retract_unpublishes_by_the_platform_post_id(up, platform):
    up.answer("/uploadposts/posts/unpublish", (200, {"success": True, "message": "Post deleted"}))
    result = await oup.UploadPostAdapter(platform).retract(_published(platform))
    assert result.kind == RETRACTED
    req = up.requests[0]
    assert req.method == "POST" and req.url.path == "/api/uploadposts/posts/unpublish"
    assert json.loads(req.content) == {"platform": platform, "user": PROFILE, "post_id": "104857600123456_998877"}


@pytest.mark.asyncio
async def test_a_retract_falls_back_to_the_stored_platform_post_id(up):
    up.answer("/uploadposts/posts/unpublish", (200, {"success": True}))
    post = _published("youtube", external_id=None,
                      metadata={"dry_run": False, "publish": {"upload_post": {"platform_post_id": "dQw4w9WgXcQ"}}})
    assert (await oup.UploadPostAdapter("youtube").retract(post)).kind == RETRACTED
    assert json.loads(up.requests[0].content)["post_id"] == "dQw4w9WgXcQ"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, kind", [
    pytest.param((200, {"success": False, "message": "Post not found"}), RETRY, id="200-without-success"),
    pytest.param((200, {}), RETRY, id="200-empty"),
    pytest.param((500, {"error": "boom"}), RETRY, id="500"),
    pytest.param(httpx.ConnectError, RETRY, id="connect-error"),
    pytest.param(httpx.ReadTimeout, RETRY, id="read-timeout"),
    pytest.param((401, {"message": "Invalid or expired token"}), RETRY, id="401"),
    pytest.param((429, {"message": "slow down"}), RETRY, id="429"),
    pytest.param((404, {"message": "Post not found"}), GAVE_UP, id="404"),
    pytest.param((400, {"error_code": "platform_not_supported"}), GAVE_UP, id="400-unsupported"),
    pytest.param((403, {"message": "not authorized"}), GAVE_UP, id="403"),
])
async def test_the_retract_outcome_matrix(up, answer, kind):
    up.answer("/uploadposts/posts/unpublish", answer)
    result = await oup.UploadPostAdapter("facebook").retract(_published("facebook"))
    assert result.kind == kind and result.error
    assert API_KEY not in result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["instagram", "tiktok", "threads"])
async def test_a_platform_upload_post_cannot_delete_is_manual(up, platform):
    result = await oup.UploadPostAdapter(platform).retract(_published(platform))
    assert result.kind == MANUAL and "by hand" in result.error
    assert up.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("external_id", [None, "", TT_URL, "https://www.facebook.com/104857600123456/posts/1"])
async def test_a_retract_without_a_platform_post_id_gives_up(up, external_id):
    result = await oup.UploadPostAdapter("facebook").retract(_published("facebook", external_id=external_id))
    assert result.kind == GAVE_UP and "post id is unknown" in result.error
    assert up.requests == []


def test_post_url_is_the_stored_external_url():
    adapter = oup.UploadPostAdapter("tiktok")
    assert adapter.post_url({"external_url": TT_URL}) == TT_URL
    assert adapter.post_url({"external_url": None}) is None and adapter.post_url({}) is None


# ── end to end through the publisher (the real adapter, the real client) ────────────────────────


@pytest.fixture
def penv(monkeypatch, up):
    """The publisher over the in-memory ledger with the REAL Upload-Post adapters enabled."""
    env = Env()
    env.fake.tables[mrs.ASSETS].rows.append(_asset())
    monkeypatch.setattr(pub, "get_marketing_run_service", lambda: env.svc)
    monkeypatch.setattr(mrs, "get_marketing_run_service", lambda: env.svc)
    for name, value in (("MARKETING_ENABLED", True), ("MARKETING_DRY_RUN", False),
                        ("MARKETING_PUBLISH_MAX_ATTEMPTS", 3), ("MARKETING_PUBLISH_RECONCILE_AFTER_SECONDS", 600),
                        ("MARKETING_TELEGRAM_BOT_TOKEN", None)):
        monkeypatch.setattr(pub.settings, name, value)
    monkeypatch.setattr(pub, "_escalation_logged", {})
    env.enabled = ["tiktok", "threads"]
    monkeypatch.setattr(outlets, "enabled_platforms", lambda: list(env.enabled))
    env.up = up  # type: ignore[attr-defined]
    return env


def _today_key(platform: str) -> str:
    return f"{mrs.run_date_et().isoformat()}:{platform}:{_fmt(platform)}"


def _backdate(env: Env, pid: str, seconds: int) -> None:
    env.raw(pid)["metadata"]["publish"]["started_at"] = (_now() - timedelta(seconds=seconds)).isoformat()


@pytest.mark.asyncio
async def test_e2e_submitted_then_pending_then_found(penv):
    key = _today_key("tiktok")
    rid = f"{key}:a1"
    pid = penv.seed(platform="tiktok", key=key, format="video", asset_ids=[ASSET_ID], caption=CAPTION)
    penv.up.answer("/upload", _ack(rid))

    counters = await pub.publish_cycle()
    assert counters["submitted"] == 1 and counters["published"] == 0
    row = penv.row(pid)
    assert row["status"] == "queued" and row["attempts"] == 1
    p = row["metadata"]["publish"]
    assert p["state"] == "submitted" and p["upload_post"]["request_id"] == rid
    assert p["upload_post"]["usage_before"] == {"count": 3, "limit": 10}
    assert p["upload_post"]["usage_after"] == {"count": 4, "limit": 10}
    upload = penv.up.uploads[0]
    assert upload.headers["idempotency-key"] == rid and dict(_parts(upload))["video"] == VIDEO_URL

    await pub.reconcile_cycle()                                      # not before the 10-minute delay
    assert penv.up.calls("/uploadposts/status") == []

    _backdate(penv, pid, 660)
    penv.up.answer("/uploadposts/status", _status("processing", {"platform": "tiktok", "status": "processing"},
                                                  rid=rid))
    penv.up.answer("/uploadposts/history", _hist())
    counters = await pub.reconcile_cycle()
    assert counters["pending"] == 1 and counters["checked"] == 1
    row = penv.row(pid)
    assert row["status"] == "queued" and row["metadata"]["publish"]["reconcile"]["last_result"] == PENDING

    _backdate(penv, pid, 1260)
    penv.up.answer("/uploadposts/status", _status("completed", rid=rid))
    penv.up.answer("/uploadposts/history", _hist(_item(rid=rid)))
    counters = await pub.reconcile_cycle()
    assert counters["found"] == 1
    row = penv.row(pid)
    assert row["status"] == "published" and row["external_id"] == TT_ID and row["external_url"] == TT_URL
    assert row["metadata"]["publish"]["upload_post"]["platform_post_id"] == TT_ID
    assert row["metadata"]["publish"]["upload_post"]["request_id"] == rid
    _assert_review_kept(row)

    await pub.publish_cycle()
    await pub.reconcile_cycle()
    assert len(penv.up.uploads) == 1                                  # published exactly once


@pytest.mark.asyncio
async def test_e2e_an_ambiguous_send_is_resent_with_the_same_idempotency_key(penv):
    key = _today_key("threads")
    rid = f"{key}:a1"
    pid = penv.seed(platform="threads", key=key, caption=CAPTION)
    penv.up.answer("/upload_text", (503, {"success": False, "error": "upstream"}))

    counters = await pub.publish_cycle()
    assert counters["unknown"] == 1
    row = penv.row(pid)
    p = row["metadata"]["publish"]
    assert row["status"] == "queued" and p["state"] == "unknown" and p["upload_post"]["request_id"] == rid
    first_sent = p["upload_post"]["first_sent_at"]

    _backdate(penv, pid, 660)
    penv.up.answer("/uploadposts/status", NOT_FOUND)
    penv.up.answer("/upload_text", _ack(rid))
    counters = await pub.reconcile_cycle()
    assert counters["resent"] == 1
    first, second = penv.up.uploads
    assert first.headers["idempotency-key"] == second.headers["idempotency-key"] == rid
    assert dict(_parts(first))["request_id"] == dict(_parts(second))["request_id"] == rid
    assert _parts(first) == _parts(second)                           # the same request, byte for byte
    row = penv.row(pid)
    p = row["metadata"]["publish"]
    assert row["status"] == "queued" and p["state"] == "submitted" and p["resends"] == 1
    assert p["upload_post"]["request_id"] == rid and p["upload_post"]["first_sent_at"] == first_sent
    assert row["attempts"] == 1                                       # a resend is not a new attempt


@pytest.mark.asyncio
async def test_e2e_not_found_after_the_window_is_never_resent_and_goes_to_the_owner(penv):
    key = _today_key("threads")
    rid = f"{key}:a1"
    long_ago = (_now() - timedelta(hours=21)).isoformat()
    pid = penv.seed_queued(platform="threads", started_ago=21 * 3600, key=key, publish={
        "attempt": 1, "state": "unknown", "started_at": long_ago,
        "upload_post": {"request_id": rid, "first_sent_at": long_ago}})
    penv.up.always("/uploadposts/status", NOT_FOUND)
    schedule = oup.UploadPostAdapter.reconcile_schedule
    for n in range(1, len(schedule) + 1):
        counters = await pub.reconcile_cycle()
        assert counters["checked"] == 1 and counters["resent"] == 0
        assert penv.row(pid)["metadata"]["publish"]["reconcile"]["n"] == n
    counters = await pub.reconcile_cycle()
    assert counters["escalated"] == 1
    row = penv.row(pid)
    assert row["status"] == "queued" and row["metadata"]["alert_kind"] == "unknown"
    assert penv.up.uploads == []


@pytest.mark.asyncio
async def test_e2e_an_inbox_draft_fails_the_post_with_an_alert(penv):
    key = _today_key("tiktok")
    rid = f"{key}:a1"
    started = (_now() - timedelta(seconds=700)).isoformat()
    pid = penv.seed_queued(platform="tiktok", started_ago=700, key=key, format="video", asset_ids=[ASSET_ID],
                           publish={"attempt": 1, "state": "submitted", "started_at": started,
                                    "upload_post": {"request_id": rid, "first_sent_at": started}})
    penv.up.answer("/uploadposts/status", _status("completed", rid=rid))
    penv.up.answer("/uploadposts/history", _hist(_item(rid=rid, fallback_to_inbox=True, post_url=None,
                                                       platform_post_id=None)))
    counters = await pub.reconcile_cycle()
    assert counters["failed"] == 1
    row = penv.row(pid)
    meta = row["metadata"]
    assert row["status"] == "failed" and meta["publish"]["state"] == "failed_on_platform"
    assert meta["alert_kind"] == "failed" and "INBOX DRAFT" in meta["alert_text"]
    assert "INBOX DRAFT" in row["last_error"]
    assert penv.up.uploads == []


@pytest.mark.asyncio
async def test_e2e_a_plan_refusal_fails_the_post_with_an_alert(penv):
    key = _today_key("tiktok")
    pid = penv.seed(platform="tiktok", key=key, format="video", asset_ids=[ASSET_ID], caption=CAPTION)
    penv.up.answer("/upload", (403, {"success": False, "message": "TikTok requires a paid plan"}))
    counters = await pub.publish_cycle()
    assert counters["failed"] == 1
    row = penv.row(pid)
    meta = row["metadata"]
    assert row["status"] == "failed" and meta["publish"]["state"] == "refused"
    assert meta["publish"]["category"] == "plan"
    assert meta["alert_kind"] == "failed" and "plan does not include tiktok" in meta["alert_text"]
    await pub.publish_cycle()
    await pub.reconcile_cycle()
    assert len(penv.up.uploads) == 1                                  # a refusal is never retried


# ── IMAGE posts on Facebook, LinkedIn and Threads (drop 1, contract C9) ─────────────────────────

PHOTO = ("facebook", "linkedin", "threads")
IMG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8 + b"\xff\xd9"
IMG_SHA = hashlib.sha256(IMG).hexdigest()
CARD_ID = "0b6f3a52-3c1d-4f7e-9a10-2b8c4d6e8f03"
CARD_PATH = "2026-10-01/card-0123456789abcdef.jpg"
CARD_URL = f"{SB_URL}/storage/v1/object/public/{BUCKET}/{CARD_PATH}"
PIC_TITLE = "Why time in the market compounds"
PIC_PARAS = ("Compounding needs years, not weeks.", "Missing a few strong days changes the result a lot.")
ALT = PIC_TITLE + "\n\n" + "\n\n".join(PIC_PARAS)


def _pic() -> PostImage:
    return PostImage(asset_id=CARD_ID, url=CARD_URL, sha256=IMG_SHA, size=len(IMG), title=PIC_TITLE,
                     paragraphs=PIC_PARAS)


def _img_post(platform: str, **over: Any) -> Dict[str, Any]:
    post = _post(platform, format="image", asset_ids=[CARD_ID], idempotency_key=f"{DAY}:{platform}:image")
    post[POST_IMAGE_KEY] = _pic()
    post.update(over)
    return post


class _Bucket:
    """The public media bucket the picture is downloaded from before Upload-Post is asked to fetch it."""

    def __init__(self) -> None:
        self.body = IMG
        self.status = 200
        self.requests: List[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) != CARD_URL:
            return httpx.Response(404)
        return httpx.Response(self.status, content=self.body)


@pytest.fixture
def bucket(monkeypatch) -> _Bucket:
    fake = _Bucket()
    monkeypatch.setattr(outlet_base, "_fetch_transport", httpx.MockTransport(fake.handler))
    return fake


@pytest.mark.parametrize("platform", PHOTO)
def test_prepare_an_image_post(creds, platform):
    prepared = oup.UploadPostAdapter(platform).prepare(_img_post(platform))
    payload = prepared.payload
    assert payload["kind"] == "photo" and payload["asset_id"] == CARD_ID and payload["image"] == _pic().meta()
    assert payload["request_id"] == f"{DAY}:{platform}:image:a1"
    assert payload["fields"] == oup.photo_fields(platform, _img_post(platform), ALT)
    assert prepared.publish_meta["upload_post"]["image"] == _pic().meta()
    assert f"image={CARD_ID}" in prepared.summary and PIC_TITLE not in prepared.summary


def test_photo_fields_are_the_text_fields_plus_the_alt_text(creds):
    """Pinned (rules/marketing.md §1): the page ids that keep a post off a PERSONAL profile, Threads'
    no-split flag, and the alt text. No AI flag — Upload-Post forwards none to these three platforms;
    the caption's disclaimer is the disclosure."""
    assert oup.photo_fields("facebook", _post("facebook"), ALT) == {"facebook_page_id": FB_PAGE,
                                                                     "facebook_alt_text": ALT}
    assert oup.photo_fields("linkedin", _post("linkedin"), ALT) == {"target_linkedin_page_id": LI_PAGE,
                                                                     "linkedin_alt_text": ALT}
    assert oup.photo_fields("threads", _post("threads"), ALT) == {"threads_long_text_as_post": True,
                                                                   "threads_alt_text": ALT}
    assert oup.PHOTO_PLATFORMS == frozenset(PHOTO)


def test_photo_alt_text_keeps_whole_paragraphs_within_1000(creds):
    long = PostImage(asset_id=CARD_ID, url=CARD_URL, sha256=IMG_SHA, size=len(IMG), title="T",
                     paragraphs=("a" * 600, "b" * 600))
    prepared = oup.UploadPostAdapter("threads").prepare({**_img_post("threads"), POST_IMAGE_KEY: long})
    assert prepared.payload["fields"]["threads_alt_text"] == "T\n\n" + "a" * 600


@pytest.mark.parametrize("platform", PHOTO)
@pytest.mark.parametrize("over, why", [
    ({POST_IMAGE_KEY: None}, "not resolved"),
    ({"asset_ids": []}, "exactly its one picture"),
    ({"asset_ids": [CARD_ID, "x"]}, "exactly its one picture"),
    ({"asset_ids": [ASSET_ID]}, "exactly its one picture"),
])
def test_prepare_refuses_a_bad_image_post(creds, platform, over, why):
    with pytest.raises(MarketingPublishRefused, match=why):
        oup.UploadPostAdapter(platform).prepare(_img_post(platform, **over))


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", PHOTO)
async def test_an_image_post_goes_to_upload_photos_by_its_checked_url(up, bucket, platform):
    rid = f"{DAY}:{platform}:image:a1"
    up.answer("/upload_photos", _ack(rid))
    adapter = oup.UploadPostAdapter(platform)
    post = _img_post(platform)
    prepared = adapter.prepare(post)
    outcome = await adapter.send({k: v for k, v in post.items() if k != POST_IMAGE_KEY}, prepared)
    assert outcome.kind == SUBMITTED
    assert up.sequence == ["GET /uploadposts/me", "POST /upload_photos", "GET /uploadposts/me"]
    (req,) = up.calls("/upload_photos")
    assert req.headers["idempotency-key"] == rid
    parts = _parts(req)
    assert parts[:7] == [("user", PROFILE), ("platform[]", platform), ("title", CAPTION), ("photos[]", CARD_URL),
                         ("async_upload", "true"), ("request_id", rid), ("external_id", POST_ID)]
    assert dict(parts[7:]) == {k: ("true" if v is True else v) for k, v in prepared.payload["fields"].items()}
    assert not {"cover_timestamp", "thumb_offset"} & {name for name, _ in parts}   # an image has no cover
    assert len(bucket.requests) == 1          # downloaded and checked BEFORE Upload-Post was asked
    assert outcome.publish_meta["upload_post"]["image"] == _pic().meta()


@pytest.mark.asyncio
async def test_an_image_post_never_sends_a_picture_that_is_not_the_one_reviewed(up, bucket):
    bucket.body = IMG[:-1] + b"\x00"
    adapter = oup.UploadPostAdapter("threads")
    outcome = await adapter.send(_post("threads", status="queued"), adapter.prepare(_img_post("threads")))
    assert outcome.kind == REFUSED and outcome.category == "media" and outcome.alert == "failed"
    assert up.requests == []                  # not even the usage read


@pytest.mark.asyncio
async def test_an_image_post_whose_picture_cannot_be_downloaded_is_not_sent(up, bucket):
    bucket.status = 503
    adapter = oup.UploadPostAdapter("facebook")
    outcome = await adapter.send(_post("facebook", status="queued"), adapter.prepare(_img_post("facebook")))
    assert outcome.kind == NOT_SENT and outcome.category == "media" and up.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, kind, category", [
    ((500, {"success": False, "error": "boom"}), AMBIGUOUS, "server"),
    (httpx.ConnectError, NOT_SENT, "transport"),
    ((403, {"success": False, "message": "plan"}), REFUSED, "plan"),
    ((400, {"success": False, "message": "x", "invalid_platforms": {"linkedin": "no"}}), REFUSED, "not_connected"),
])
async def test_an_image_post_keeps_the_outcome_split(up, bucket, answer, kind, category):
    up.answer("/upload_photos", answer)
    adapter = oup.UploadPostAdapter("linkedin")
    outcome = await adapter.send(_post("linkedin", status="queued"), adapter.prepare(_img_post("linkedin")))
    assert (outcome.kind, outcome.category) == (kind, category)


@pytest.mark.asyncio
async def test_e2e_an_image_post_through_the_publisher(penv, bucket):
    """The publisher resolves the picture from the LEDGER (a ready card of the post's run + the accepted
    image_post), hands prepare a copy with it attached, claims the row AS READ (no picture written to
    it), and the adapter posts the checked URL."""
    run_id = "22222222-3333-4444-8555-666666666666"
    key = f"{mrs.run_date_et().isoformat()}:threads:image"
    penv.fake.tables[mrs.ASSETS].rows.append({
        "id": CARD_ID, "run_id": run_id, "kind": "card", "status": "ready", "storage_path": CARD_PATH,
        "content_type": "image/jpeg", "bytes": len(IMG), "sha256": IMG_SHA,
        "metadata": {"image_role": "post_image", "onscreen_text": [PIC_TITLE, *PIC_PARAS, "footer"]}})
    penv.fake.tables[mrs.SCRIPTS].rows.append({
        "run_id": run_id, "status": "accepted",
        "output": {"image_post": {"title": PIC_TITLE, "paragraphs": list(PIC_PARAS)}}})
    pid = penv.seed(platform="threads", key=key, format="image", asset_ids=[CARD_ID], caption=CAPTION, run_id=run_id)
    penv.up.answer("/upload_photos", _ack(f"{key}:a1"))
    counters = await pub.publish_cycle()
    assert counters["submitted"] == 1
    row = penv.row(pid)
    assert row["status"] == "queued" and row["metadata"]["publish"]["state"] == "submitted"
    assert POST_IMAGE_KEY not in row and POST_IMAGE_KEY not in row["metadata"]
    assert row["metadata"]["publish"]["upload_post"]["image"]["url"] == CARD_URL
    (req,) = penv.up.calls("/upload_photos")
    assert ("photos[]", CARD_URL) in _parts(req) and ("threads_alt_text", ALT) in _parts(req)


@pytest.mark.asyncio
async def test_e2e_an_image_post_without_its_card_is_refused_before_any_claim(penv, bucket):
    key = f"{mrs.run_date_et().isoformat()}:threads:image"
    pid = penv.seed(platform="threads", key=key, format="image", asset_ids=["no-such-card"], caption=CAPTION)
    counters = await pub.publish_cycle()
    assert counters["failed"] == 1
    row = penv.row(pid)
    assert row["status"] == "failed" and row["attempts"] == 0          # never claimed
    assert row["metadata"]["publish"]["category"] == "media" and "no asset" in row["last_error"]
    assert penv.up.uploads == [] and bucket.requests == []


# ── Batch 1 (2026-10-15): the App Store first comment and the answer's warnings ───────────────
#
# A post `create_posts` stamped with `metadata.first_comment` (the code-owned search line) sends it as
# `<platform>_first_comment` on whichever route it takes; Upload-Post's answer may carry `warnings`
# (a refused comment, an ignored field — "the first comment never fails the publish") and
# `first_comment_posted`, both kept on the post through the publisher's fenced merge.

import logging  # noqa: E402

from app.schemas import marketing as schemas  # noqa: E402
from test_marketing_publisher import _review_meta  # noqa: E402

SEARCH = post_copy.SEARCH_LINE


def _commented(platform: str, text: Any = SEARCH, base: Optional[Dict[str, Any]] = None, **over: Any) -> Dict[str, Any]:
    post = base if base is not None else _post(platform, **over)
    post["metadata"] = {**(post.get("metadata") or {}), schemas.POST_METADATA_FIRST_COMMENT: text}
    return post


def test_the_outlet_sends_only_the_code_owned_comment_on_platforms_that_take_one():
    assert oup.FIRST_COMMENT_TEXTS == frozenset({"Search Caydex on the App Store."})
    assert set(oup.PLATFORMS) == set(schemas.FIRST_COMMENT_PLATFORMS)    # every Upload-Post outlet takes one


@pytest.mark.parametrize("platform", oup.PLATFORMS)
def test_a_stamped_first_comment_rides_as_the_platforms_own_field(creds, platform):
    plain = oup.platform_fields(platform, _post(platform))
    fields = oup.platform_fields(platform, _commented(platform))
    assert fields == {**plain, f"{platform}_first_comment": SEARCH}
    assert "first_comment" not in fields                                   # never the generic fallback field
    prepared = oup.UploadPostAdapter(platform).prepare(_commented(platform))
    assert prepared.payload["fields"] == fields and "first_comment=1" in prepared.summary
    # no stamp: exactly the fields pinned above, and no marker in the summary
    assert "first_comment=1" not in oup.UploadPostAdapter(platform).prepare(_post(platform)).summary


@pytest.mark.parametrize("platform", PHOTO)
def test_a_picture_post_carries_its_first_comment_too(creds, platform):
    post = _commented(platform, base=_img_post(platform))
    fields = oup.photo_fields(platform, post, ALT)
    assert fields[f"{platform}_first_comment"] == SEARCH and fields[f"{platform}_alt_text"] == ALT
    assert oup.UploadPostAdapter(platform).prepare(post).payload["fields"] == fields


@pytest.mark.parametrize("text", ["Download Caydex now — free!", "", "search caydex on the app store.",
                                  " Search Caydex on the App Store.", "Search Caydex on the App Store. https://x.co",
                                  42, True, ["Search Caydex on the App Store."], {"t": SEARCH}])
def test_a_first_comment_that_is_not_the_code_owned_line_is_never_sent(creds, caplog, text):
    """Nothing scans a comment at runtime: a stamp that is not EXACTLY the pinned line (a hand-edited row)
    sends no comment at all — logged at ERROR — and never stops the post."""
    with caplog.at_level(logging.ERROR, logger=oup.logger.name):
        fields = oup.platform_fields("youtube", _commented("youtube", text))
        prepared = oup.UploadPostAdapter("youtube").prepare(_commented("youtube", text))
    assert fields == oup.platform_fields("youtube", _post("youtube"))
    assert prepared.payload["fields"] == fields
    assert any("WITHOUT a comment" in r.getMessage() and POST_ID in r.getMessage() for r in caplog.records)


def test_no_stamp_or_a_null_stamp_is_no_comment_and_no_error(creds, caplog):
    with caplog.at_level(logging.ERROR, logger=oup.logger.name):
        for post in (_post("tiktok"), _commented("tiktok", None), _post("tiktok", metadata=None),
                     _post("tiktok", metadata="garbage")):
            assert "tiktok_first_comment" not in oup.platform_fields("tiktok", post)
    assert not caplog.records


def test_a_platform_that_takes_no_comment_never_gets_one(creds, caplog):
    with caplog.at_level(logging.ERROR, logger=oup.logger.name):
        assert oup.platform_fields("mastodon", _commented("threads")) == {}
        assert oup.first_comment_of("bluesky", _commented("threads")) is None
        assert oup.first_comment_of("x", _commented("threads")) is None
    assert len([r for r in caplog.records if "WITHOUT a comment" in r.getMessage()]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", oup.PLATFORMS)
async def test_the_first_comment_is_on_the_request_actually_sent(up, ledger, platform):
    path = "/upload" if platform in VIDEO else "/upload_text"
    up.answer(path, _ack(_rid(platform)))
    outcome, _prepared = await _send(platform, metadata={"dry_run": False, "first_comment": SEARCH})
    assert outcome.kind == SUBMITTED
    parts = _parts(up.uploads[0])
    assert dict(parts)[f"{platform}_first_comment"] == SEARCH
    assert [name for name, _ in parts].count(f"{platform}_first_comment") == 1


_WARN = "A first comment on TikTok needs a reconnected TikTok account. The post was published without it."


@pytest.mark.asyncio
async def test_an_answers_warnings_and_comment_flag_are_kept_and_logged(up, ledger, caplog):
    up.answer("/upload", (200, {**_ack(_rid("tiktok"))[1], "warnings": [_WARN, "brand_content_toggle was ignored"],
                                "first_comment_posted": False}))
    with caplog.at_level(logging.INFO, logger=oup.logger.name):
        outcome, _ = await _send("tiktok", metadata={"dry_run": False, "first_comment": SEARCH})
    assert outcome.kind == SUBMITTED                                     # a note never changes the outcome
    meta = outcome.publish_meta["upload_post"]
    assert meta["warnings"] == [_WARN, "brand_content_toggle was ignored"]
    assert meta["first_comment_posted"] is False
    warned = [r for r in caplog.records if "warning(s)" in r.getMessage()]
    assert len(warned) == 1 and warned[0].levelno == logging.WARNING
    assert POST_ID in warned[0].getMessage() and _rid("tiktok") in warned[0].getMessage()
    assert any(r.levelno == logging.WARNING and "first comment requested" in r.getMessage()
               and "posted=False" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_sync_answer_keeps_its_platforms_notes_and_is_still_published(up, ledger):
    result = {"success": True, "url": TT_URL, "post_id": TT_ID, "first_comment_posted": True,
              "warnings": ["location_id was ignored"]}
    up.answer("/upload", (200, {"success": True, "results": {"tiktok": result}, "warnings": [_WARN]}))
    outcome, _ = await _send("tiktok", metadata={"dry_run": False, "first_comment": SEARCH})
    assert outcome.kind == PUBLISHED and outcome.external_id == TT_ID
    meta = outcome.publish_meta["upload_post"]
    assert meta["warnings"] == [_WARN, "location_id was ignored"] and meta["first_comment_posted"] is True


@pytest.mark.asyncio
async def test_a_refused_sync_answer_keeps_its_notes_too(up, ledger):
    up.answer("/upload", (200, {"success": False, "warnings": [_WARN],
                                "results": {"tiktok": {"success": False, "error": "Video too short"}}}))
    outcome, _ = await _send("tiktok")
    assert outcome.kind == REFUSED and outcome.publish_meta["upload_post"]["warnings"] == [_WARN]


@pytest.mark.asyncio
async def test_notes_of_an_earlier_attempt_are_never_carried_over(up, ledger):
    stored = {"publish": {"upload_post": {"warnings": ["an old warning"], "first_comment_posted": True}}}
    up.answer("/upload", _ack(_rid("tiktok")))
    outcome, prepared = await _send("tiktok", metadata={"dry_run": False, **stored})
    assert prepared.publish_meta["upload_post"]["warnings"] == ["an old warning"]   # the write-ahead keeps them
    meta = outcome.publish_meta["upload_post"]
    assert "warnings" not in meta and "first_comment_posted" not in meta             # this answer said nothing


@pytest.mark.asyncio
async def test_an_answer_without_notes_adds_no_note_keys(up, ledger, caplog):
    up.answer("/upload", _ack(_rid("tiktok")))
    with caplog.at_level(logging.INFO, logger=oup.logger.name):
        outcome, _ = await _send("tiktok")
    meta = outcome.publish_meta["upload_post"]
    assert "warnings" not in meta and "first_comment_posted" not in meta
    assert not [r for r in caplog.records if "first comment" in r.getMessage() or "warning(s)" in r.getMessage()]


def test_keeping_the_notes_can_never_cost_a_sent_upload(caplog):
    """The upload already went out: a raise here would read as an UNKNOWN outcome. Anything unexpected is
    logged and the outcome's own metadata stands."""

    class Hostile(dict):
        def get(self, *_a, **_k):
            raise RuntimeError("an unexpected answer shape")

    meta = {"request_id": "r", "warnings": ["stale"], "first_comment_posted": True}
    with caplog.at_level(logging.WARNING, logger=oup.logger.name):
        oup.UploadPostAdapter("tiktok")._keep_notes(_post("tiktok"), {"request_id": "r", "fields": {}}, Hostile(), meta)
    assert meta == {"request_id": "r"}
    assert any("could not keep the answer's notes" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("raw, kept", [
    (["a", 3, None, "b"], ["a", "b"]),
    ("a lone string", []),                  # the CLIENT normalises strings; the outlet takes a list only
    ({"w": "x"}, []),
    ([f"w{i}" for i in range(9)], [f"w{i}" for i in range(5)]),
])
def test_the_outlet_keeps_only_a_list_of_strings_capped(raw, kept):
    meta: Dict[str, Any] = {}
    oup.UploadPostAdapter("threads")._keep_notes(_post("threads"), {"request_id": "r", "fields": {}},
                                                 {"warnings": raw}, meta)
    assert meta.get("warnings", []) == kept


@pytest.mark.asyncio
async def test_e2e_the_first_comment_goes_out_and_its_warnings_land_on_the_post(penv):
    """Through the publisher: the stamped comment rides on the request, and the answer's warnings and flag
    land in the LEDGER under metadata.publish.upload_post (the fenced merge) — the review record kept."""
    key = _today_key("threads")
    rid = f"{key}:a1"
    pid = penv.seed(platform="threads", key=key, caption=CAPTION, meta=_review_meta(first_comment=SEARCH))
    penv.up.answer("/upload_text", (200, {**_ack(rid)[1], "warnings": ["threads_topic_tag was ignored"],
                                          "first_comment_posted": True}))
    counters = await pub.publish_cycle()
    assert counters["submitted"] == 1
    (upload,) = penv.up.uploads
    assert dict(_parts(upload))["threads_first_comment"] == SEARCH
    row = penv.row(pid)
    assert row["status"] == "queued"
    up_meta = row["metadata"]["publish"]["upload_post"]
    assert up_meta["warnings"] == ["threads_topic_tag was ignored"] and up_meta["first_comment_posted"] is True
    assert row["metadata"]["first_comment"] == SEARCH
    _assert_review_kept(row)


# ── Batch 3 (2026-10-20, contract): Instagram Trial Reels — the setting and its values ───────────────
#
# MARKETING_INSTAGRAM_TRIAL_MODE (off | share_if_liked | dont_share) is read at SEND time by this outlet and maps
# to Upload-Post's documented `share_mode` values (D14); "off" — the default, and whatever a bad value reads as —
# sends exactly today's fields.


def test_batch3_instagram_trial_setting_is_declared_off_and_its_literal_mirrors_the_modes():
    """config.py imports no app module, so its validator carries an inline copy of the modes. Read by AST (the
    literal itself) and by behaviour (every mode survives the validator)."""
    import ast
    from pathlib import Path

    from app.config import Settings, settings

    assert hasattr(settings, "MARKETING_INSTAGRAM_TRIAL_MODE")
    assert Settings.model_fields["MARKETING_INSTAGRAM_TRIAL_MODE"].default == schemas.INSTAGRAM_TRIAL_OFF == "off"
    src = (Path(__file__).resolve().parents[1] / "app" / "config.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_instagram_trial_mode_fail_closed")
    literals = [ast.literal_eval(a.value) for a in ast.walk(fn)
                if isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "known" for t in a.targets)]
    assert literals == [(schemas.INSTAGRAM_TRIAL_OFF, *schemas.INSTAGRAM_TRIAL_MODES)]
    for mode in (schemas.INSTAGRAM_TRIAL_OFF, *schemas.INSTAGRAM_TRIAL_MODES):
        assert Settings(MARKETING_INSTAGRAM_TRIAL_MODE=mode).MARKETING_INSTAGRAM_TRIAL_MODE == mode


@pytest.mark.parametrize("value, expected, logs_error", [
    ("off", "off", False),
    ("OFF", "off", False),
    ("", "off", False),                                    # blank is off, quietly
    ("   ", "off", False),
    ("share_if_liked", "share_if_liked", False),
    ("Share_If_Liked ", "share_if_liked", False),
    (" DONT_SHARE", "dont_share", False),
    ("trial", "off", True),
    ("share-if-liked", "off", True),
    ("TRIAL_REELS_SHARE_TO_FOLLOWERS_IF_LIKED", "off", True),    # Upload-Post's string is not a setting value
    ("share_if_liked,dont_share", "off", True),
    (None, "off", True),
    (1, "off", True),
    (True, "off", True),
    (["share_if_liked"], "off", True),
])
def test_batch3_instagram_trial_setting_matrix(monkeypatch, caplog, value, expected, logs_error):
    from app.config import Settings

    with caplog.at_level(logging.ERROR, logger="app.config"):
        if isinstance(value, str):
            monkeypatch.setenv("MARKETING_INSTAGRAM_TRIAL_MODE", value)
            got = Settings().MARKETING_INSTAGRAM_TRIAL_MODE
        else:
            got = Settings(MARKETING_INSTAGRAM_TRIAL_MODE=value).MARKETING_INSTAGRAM_TRIAL_MODE
    assert got == expected
    errors = [r.getMessage() for r in caplog.records if r.name == "app.config" and r.levelno >= logging.ERROR
              and "MARKETING_INSTAGRAM_TRIAL_MODE" in r.getMessage()]
    assert bool(errors) == logs_error, errors
    assert all(len(m) < 400 for m in errors)
    assert got == schemas.INSTAGRAM_TRIAL_OFF or got in schemas.INSTAGRAM_TRIAL_MODES


def test_batch3_trial_mode_values_are_upload_posts_documented_strings():
    """`share_mode` takes Upload-Post's documented values (docs.upload-post.com, upload-video, Instagram section,
    read 2026-10-20): TRIAL_REELS_SHARE_TO_FOLLOWERS_IF_LIKED and TRIAL_REELS_DONT_SHARE_TO_FOLLOWERS. CUSTOM — a
    regular Reel, what no field means — is never a trial value, and "off" maps to nothing (today's fields)."""
    assert schemas.INSTAGRAM_TRIAL_MODES == {
        "share_if_liked": "TRIAL_REELS_SHARE_TO_FOLLOWERS_IF_LIKED",
        "dont_share": "TRIAL_REELS_DONT_SHARE_TO_FOLLOWERS",
    }
    assert schemas.INSTAGRAM_TRIAL_OFF == "off" and schemas.INSTAGRAM_TRIAL_OFF not in schemas.INSTAGRAM_TRIAL_MODES
    values = list(schemas.INSTAGRAM_TRIAL_MODES.values())
    assert len(set(values)) == len(values) and "CUSTOM" not in values
    assert all(v.startswith("TRIAL_REELS_") and v.isascii() and v == v.upper() for v in values)
    # the setting's tokens are lower-case words, never Upload-Post's own strings
    assert all(k == k.lower() and k not in values for k in schemas.INSTAGRAM_TRIAL_MODES)


# ── Batch 3 (2026-10-20, send side): the day's VIDEO on Facebook, LinkedIn and Threads ──────────────────────
#
# A Facebook / LinkedIn / Threads post may be a VIDEO — that platform's EXTRA post of the run, beside its
# picture/text post. The route follows the post's FORMAT (POST /upload with the verified MP4), and the caption
# goes in that platform's description / commentary / text field — never as `title`, which Upload-Post would make
# a Reel's 255-character title or LinkedIn's media title (D9). The text and photo routes are unchanged.

import uuid  # noqa: E402

#: An extra video's caption (its platform's accepted caption with the narrated-video disclaimer swapped in).
REEL = ("Time in the market beats timing it. #investing\n\n"
        "Educational only, not investment advice. Script and narration generated with AI. Caydex")
_EXTRA = ("facebook", "linkedin", "threads")
#: What a Facebook / LinkedIn / Threads video must never carry: the caption as a title, the generic description
#: field, Threads' text-route flag, or a cover/thumbnail (a Reel's cover cannot be pinned).
_NOT_ON_AN_EXTRA = {"title", "facebook_title", "linkedin_title", "description", "threads_long_text_as_post",
                    "cover_timestamp", "thumb_offset", "thumbnail", "thumbnail_url", "facebook_thumbnail", "cover_url"}


def _vover(platform: str, **over: Any) -> Dict[str, Any]:
    """The columns of an EXTRA video post as create_posts records it: format video, the run's one video, no title."""
    return {"format": "video", "asset_ids": [ASSET_ID], "title": None, "caption": REEL,
            "idempotency_key": f"{DAY}:{platform}:video", **over}


def _vpost(platform: str, **over: Any) -> Dict[str, Any]:
    return _post(platform, **_vover(platform, **over))


def _video_fields(platform: str, caption: str = REEL) -> Dict[str, Any]:
    """D9, pinned EXACTLY (key order included — it is the multipart order)."""
    return {
        "facebook": {"facebook_page_id": FB_PAGE, "facebook_media_type": "REELS", "facebook_description": caption,
                     "facebook_is_ai_generated": True},
        "linkedin": {"target_linkedin_page_id": LI_PAGE, "linkedin_description": caption, "visibility": "PUBLIC"},
        "threads": {"threads_title": caption},
    }[platform]


def _rendered(fields: Dict[str, Any]) -> List[Tuple[str, str]]:
    return [(k, ("true" if v else "false") if isinstance(v, bool) else str(v)) for k, v in fields.items()]


@pytest.mark.parametrize("platform", _EXTRA)
def test_batch3_video_on_facebook_linkedin_threads_prepares(creds, platform):
    adapter = oup.UploadPostAdapter(platform)
    assert adapter.video is False                     # still not a PRIMARY video platform: the post's format decides
    prepared = adapter.prepare(_vpost(platform))
    p = prepared.payload
    assert p["kind"] == "video" and p["asset_id"] == ASSET_ID and p["image"] is None and p["text"] == REEL
    assert p["request_id"] == f"{DAY}:{platform}:video:a1" and p["external_id"] == POST_ID
    assert p["fields"] == _video_fields(platform) and list(p["fields"]) == list(_video_fields(platform))
    assert p["fields"] == oup.platform_fields(platform, _vpost(platform))
    assert not _NOT_ON_AN_EXTRA & set(p["fields"])
    meta = prepared.publish_meta["upload_post"]
    assert meta["request_id"] == f"{DAY}:{platform}:video:a1" and "image" not in meta and "share_mode" not in meta
    assert f"kind=video request_id={DAY}:{platform}:video:a1 " in prepared.summary
    assert prepared.text_sha256 == hashlib.sha256(REEL.encode("utf-8")).hexdigest()
    # The same platform's text post keeps its own fields (pinned in test_platform_fields_are_pinned).
    assert oup.platform_fields(platform, _post(platform)) == {
        "facebook": {"facebook_page_id": FB_PAGE}, "linkedin": {"target_linkedin_page_id": LI_PAGE},
        "threads": {"threads_long_text_as_post": True}}[platform]


@pytest.mark.parametrize("platform", _EXTRA)
@pytest.mark.parametrize("assets, n", [([], 0), (None, 0), (["", None], 0), ([ASSET_ID, "second"], 2)])
def test_batch3_a_video_post_on_a_text_platform_needs_exactly_one_asset(creds, platform, assets, n):
    with pytest.raises(MarketingPublishRefused, match=f"exactly one video asset, it has {n}"):
        oup.UploadPostAdapter(platform).prepare(_vpost(platform, asset_ids=assets))


@pytest.mark.asyncio
async def test_batch3_threads_video_over_500_bytes_is_refused_before_the_claim(penv):
    adapter = oup.UploadPostAdapter("threads")
    exact = "a" * 500
    assert adapter.prepare(_vpost("threads", caption=exact)).payload["fields"] == {"threads_title": exact}
    wide = "é" * 251                                   # 251 characters (under 500), 502 UTF-8 bytes (over)
    with pytest.raises(MarketingPublishRefused, match="the video caption is 502 UTF-8 bytes > 500"):
        adapter.prepare(_vpost("threads", caption=wide))
    # the byte cap is the VIDEO route's: a Threads text post keeps its no-split flag, Facebook counts characters
    assert adapter.prepare(_post("threads", caption=wide)).payload["fields"] == {"threads_long_text_as_post": True}
    assert oup.UploadPostAdapter("facebook").prepare(_vpost("facebook", caption="é" * 2000)).payload["kind"] == "video"
    # the count itself never raises: a lone surrogate (not encodable as UTF-8) counts its 3 bytes
    with pytest.raises(MarketingPublishRefused, match="501 UTF-8 bytes > 500"):
        adapter.prepare(_vpost("threads", caption="\ud800" * 167))
    # Through the publisher: refused by the guard, never claimed, nothing sent.
    penv.enabled = ["threads"]
    pid = penv.seed(platform="threads", key=f"{mrs.run_date_et().isoformat()}:threads:video", format="video",
                    asset_ids=[ASSET_ID], caption=wide, run_id=str(uuid.uuid4()))
    counters = await pub.publish_cycle()
    assert counters["failed"] == 1
    row = penv.row(pid)
    assert row["status"] == "failed" and row["attempts"] == 0 and "502 UTF-8 bytes" in row["last_error"]
    assert row["metadata"]["alert_kind"] == "failed" and penv.up.uploads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", _EXTRA)
async def test_batch3_the_video_requests_actually_sent(up, ledger, platform):
    rid = f"{DAY}:{platform}:video:a1"
    up.answer("/upload", _ack(rid))
    outcome, _prepared = await _send(platform, **_vover(platform))
    assert outcome.kind == SUBMITTED and outcome.publish_meta["upload_post"]["request_id"] == rid
    assert up.sequence == ["GET /uploadposts/me", "POST /upload", "GET /uploadposts/me"]
    (req,) = up.uploads
    assert req.headers["idempotency-key"] == rid and req.headers["authorization"] == f"Apikey {API_KEY}"
    own = [("user", PROFILE), ("platform[]", platform), ("video", VIDEO_URL), ("async_upload", "true"),
           ("request_id", rid), ("external_id", POST_ID)]
    assert _parts(req) == own + _rendered(_video_fields(platform))
    flags = dict(_parts(req))
    assert not _NOT_ON_AN_EXTRA & set(flags)            # the caption never travels as `title`
    if platform == "facebook":
        assert flags["facebook_media_type"] == "REELS" and flags["facebook_description"] == REEL
        assert flags["facebook_is_ai_generated"] == "true"      # Facebook's own AI label on the request itself
    elif platform == "linkedin":
        assert flags["linkedin_description"] == REEL and flags["visibility"] == "PUBLIC"
    else:
        assert flags["threads_title"] == REEL


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", _EXTRA)
async def test_batch3_the_first_comment_rides_on_the_video_route(up, ledger, platform):
    rid = f"{DAY}:{platform}:video:a1"
    up.answer("/upload", _ack(rid))
    outcome, prepared = await _send(platform, **_vover(platform, metadata={"dry_run": False, "first_comment": SEARCH}))
    assert outcome.kind == SUBMITTED and "first_comment=1" in prepared.summary
    parts = _parts(up.uploads[0])
    assert [n for n, _ in parts].count(f"{platform}_first_comment") == 1
    assert parts[-1] == (f"{platform}_first_comment", SEARCH)
    assert parts[:-1][6:] == _rendered(_video_fields(platform))


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", _EXTRA)
async def test_batch3_text_and_photo_routes_are_unchanged(up, ledger, bucket, platform):
    """Byte for byte what they sent before Batch 3: the text post to /upload_text, the picture post to
    /upload_photos — with the caption as `title`, the page id / no-split flag, the alt text — never /upload."""
    text_fields = {"facebook": {"facebook_page_id": FB_PAGE}, "linkedin": {"target_linkedin_page_id": LI_PAGE},
                   "threads": {"threads_long_text_as_post": True}}[platform]
    up.answer("/upload_text", _ack(_rid(platform)))
    outcome, prepared = await _send(platform)
    assert outcome.kind == SUBMITTED and prepared.payload["kind"] == "text"
    assert _parts(up.calls("/upload_text")[0]) == [
        ("user", PROFILE), ("platform[]", platform), ("title", CAPTION), ("async_upload", "true"),
        ("request_id", _rid(platform)), ("external_id", POST_ID), *_rendered(text_fields)]
    rid = f"{DAY}:{platform}:image:a1"
    up.answer("/upload_photos", _ack(rid))
    adapter = oup.UploadPostAdapter(platform)
    post = _img_post(platform)
    prepared = adapter.prepare(post)
    assert prepared.payload["kind"] == "photo"
    outcome = await adapter.send({k: v for k, v in post.items() if k != POST_IMAGE_KEY}, prepared)
    assert outcome.kind == SUBMITTED
    assert _parts(up.calls("/upload_photos")[0]) == [
        ("user", PROFILE), ("platform[]", platform), ("title", CAPTION), ("photos[]", CARD_URL),
        ("async_upload", "true"), ("request_id", rid), ("external_id", POST_ID),
        *_rendered({**text_fields, f"{platform}_alt_text": ALT})]
    assert up.calls("/upload") == []


@pytest.mark.asyncio
async def test_batch3_two_posts_on_one_platform_each_reconcile_only_their_own_job(up, monkeypatch):
    """Facebook's picture post and its video of one run are two Upload-Post jobs: Upload-Post's history may hold
    both, and each post settles on its OWN row (request id), never the other's — through the client, and in the
    adapter's own re-check behind it."""
    pic_rid, vid_rid = f"{DAY}:facebook:image:a1", f"{DAY}:facebook:video:a1"
    pic = _queued("facebook", request_id=pic_rid)
    pic.update(format="image", idempotency_key=f"{DAY}:facebook:image")
    vid = _queued("facebook", request_id=vid_rid)
    vid.update(id=str(uuid.uuid4()), **_vover("facebook"))
    rows = (_item("facebook", rid=pic_rid, platform_post_id="104857600123456_111",
                  post_url="https://www.facebook.com/104857600123456/posts/111"),
            _item("facebook", rid=vid_rid, platform_post_id="998877665544",
                  post_url="https://www.facebook.com/reel/998877665544"))
    up.always("/uploadposts/status", _status("completed"))
    up.always("/uploadposts/history", _hist(*rows))
    first, second = await _reconcile(pic), await _reconcile(vid)
    assert (first.kind, first.external_id) == (FOUND, "104857600123456_111")
    assert (second.kind, second.external_id) == (FOUND, "998877665544")
    assert [r.url.params["request_id"] for r in up.requests] == [pic_rid, pic_rid, vid_rid, vid_rid]

    async def status(request_id):
        return {"status": "completed", "results": [], "request_id": request_id}

    async def history(request_id):                     # both rows, unfiltered: the adapter's own check decides
        return {"history": [dict(r) for r in rows], "in_progress": []}

    monkeypatch.setattr(upload_post, "get_status", status)
    monkeypatch.setattr(upload_post, "get_history", history)
    assert (await _reconcile(pic)).external_id == "104857600123456_111"
    assert (await _reconcile(vid)).external_id == "998877665544"


@pytest.mark.asyncio
@pytest.mark.parametrize("platform, post_id", [("facebook", "998877665544"),
                                               ("linkedin", "urn:li:ugcPost:7254000000000000000")])
async def test_batch3_retract_a_facebook_video_by_its_platform_post_id(up, platform, post_id):
    up.answer("/uploadposts/posts/unpublish", (200, {"success": True, "message": "Post deleted"}),
              (200, {"success": True}))
    published = {**_vpost(platform), "status": "published", "external_id": post_id,
                 "external_url": f"https://{platform}.example/v/1"}
    assert (await oup.UploadPostAdapter(platform).retract(published)).kind == RETRACTED
    # …or by the platform post id reconcile stored, when the row has no external id
    stored = {**published, "external_id": None,
              "metadata": {"dry_run": False, "publish": {"upload_post": {"platform_post_id": post_id}}}}
    assert (await oup.UploadPostAdapter(platform).retract(stored)).kind == RETRACTED
    assert [json.loads(r.content) for r in up.requests] == [{"platform": platform, "user": PROFILE,
                                                             "post_id": post_id}] * 2
    # a Threads video, like any Threads post, is removed by hand
    threads = {**_vpost("threads"), "status": "published", "external_id": "18000000000000000"}
    assert (await oup.UploadPostAdapter("threads").retract(threads)).kind == MANUAL
    assert len(up.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("platform, result, ext_id, ext_url", [
    ("facebook", {"success": True, "video_reel_id": "998877665544"}, "998877665544", None),
    ("facebook", {"success": True, "video_reel_id": ["", "998877665544"]}, "998877665544", None),
    ("linkedin", {"success": True, "video_urn": "urn:li:video:C4E10AQF"}, "urn:li:video:C4E10AQF", None),
    # the usual keys still come first …
    ("facebook", {"success": True, "post_id": "104857600123456_1", "video_reel_id": "998877665544"},
     "104857600123456_1", None),
    ("linkedin", {"success": True, "platform_post_id": "urn:li:share:1", "video_urn": "urn:li:video:C4E"},
     "urn:li:share:1", None),
    # … and an id beats the URL, which a Retract cannot use
    ("facebook", {"success": True, "url": "https://www.facebook.com/reel/998877665544",
                  "video_reel_id": "998877665544"}, "998877665544", "https://www.facebook.com/reel/998877665544"),
])
async def test_batch3_a_sync_reel_answer_names_its_video_reel_id(up, ledger, platform, result, ext_id, ext_url):
    rid = f"{DAY}:{platform}:video:a1"
    up.answer("/upload", (200, {"success": True, "request_id": rid, "results": {platform: result}}))
    outcome, _ = await _send(platform, **_vover(platform))
    assert outcome.kind == PUBLISHED and outcome.external_id == ext_id and outcome.external_url == ext_url
    assert not str(outcome.external_id).startswith("http")          # so Retract has the id it needs


# ── Batch 3: Instagram Trial Reels at send time ───────────────────────────────────────────────────────────

#: Today's Reel fields (test_platform_fields_are_pinned), in their multipart order.
_REEL_FIELDS = {"title": CAPTION, "instagram_title": CAPTION, "media_type": "REELS", "share_to_feed": True,
                "is_ai_generated": True, "thumb_offset": "500"}


@pytest.fixture
def trial_cache():
    """A bad live value logs once per distinct value (the parse is memoised): every test starts from none."""
    oup._parse_instagram_trial_mode_text.cache_clear()
    yield
    oup._parse_instagram_trial_mode_text.cache_clear()


@pytest.mark.parametrize("mode", ["off", "share_if_liked", "dont_share"])
def test_batch3_instagram_trial_modes(creds, monkeypatch, caplog, trial_cache, mode):
    monkeypatch.setattr(oup.settings, "MARKETING_INSTAGRAM_TRIAL_MODE", mode)
    with caplog.at_level(logging.INFO, logger=oup.logger.name):
        fields = oup.platform_fields("instagram", _post("instagram"))
        prepared = oup.UploadPostAdapter("instagram").prepare(_post("instagram"))
    meta = prepared.publish_meta["upload_post"]
    assert prepared.payload["fields"] == fields
    if mode == "off":
        assert fields == _REEL_FIELDS and list(fields) == list(_REEL_FIELDS)     # today's dict, byte for byte
        assert "share_mode" not in meta and "share_mode" not in prepared.summary
    else:
        value = schemas.INSTAGRAM_TRIAL_MODES[mode]
        assert list(fields.items()) == [("title", CAPTION), ("instagram_title", CAPTION), ("media_type", "REELS"),
                                        ("share_mode", value), ("is_ai_generated", True), ("thumb_offset", "500")]
        assert "share_to_feed" not in fields                                # regular Reels only
        assert meta["share_mode"] == value and f"share_mode={value} " in prepared.summary
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    # no other platform, and no other format, ever carries it
    for platform in ("tiktok", "youtube", "facebook", "linkedin", "threads"):
        assert "share_mode" not in oup.platform_fields(platform, _post(platform))
    for platform in _EXTRA:
        assert "share_mode" not in oup.platform_fields(platform, _vpost(platform))


@pytest.mark.parametrize("raw", ["trial", "share-if-liked", "TRIAL_REELS_SHARE_TO_FOLLOWERS_IF_LIKED", "custom",
                                 7, None, True, ["share_if_liked"]])
def test_batch3_a_bad_live_trial_value_is_a_regular_reel_with_an_error(creds, monkeypatch, caplog, trial_cache, raw):
    """The validator normalises the setting; a value put on the LIVE settings object past it (a shell, a test)
    is re-checked at send time: anything but a known mode is a regular Reel — today's fields — and an ERROR,
    once per distinct string (memoised), every time for a non-string."""
    monkeypatch.setattr(oup.settings, "MARKETING_INSTAGRAM_TRIAL_MODE", raw)
    with caplog.at_level(logging.ERROR, logger=oup.logger.name):
        for _ in range(5):
            assert oup.platform_fields("instagram", _post("instagram")) == _REEL_FIELDS
            assert "share_mode" not in oup.UploadPostAdapter("instagram").prepare(_post("instagram")).publish_meta[
                "upload_post"]
    errors = [r.getMessage() for r in caplog.records
              if r.levelno >= logging.ERROR and "MARKETING_INSTAGRAM_TRIAL_MODE" in r.getMessage()]
    assert len(errors) == (1 if isinstance(raw, str) else 10)
    assert all(len(m) < 300 for m in errors)


@pytest.mark.parametrize("raw, expected", [
    ("off", None), ("OFF", None), ("", None), ("   ", None),
    ("share_if_liked", "TRIAL_REELS_SHARE_TO_FOLLOWERS_IF_LIKED"), (" Share_If_Liked ", "TRIAL_REELS_SHARE_TO_FOLLOWERS_IF_LIKED"),
    ("DONT_SHARE", "TRIAL_REELS_DONT_SHARE_TO_FOLLOWERS"),
])
def test_batch3_parse_instagram_trial_mode_reads_the_validators_tokens(caplog, trial_cache, raw, expected):
    with caplog.at_level(logging.ERROR, logger=oup.logger.name):
        assert oup.parse_instagram_trial_mode(raw) == expected
    assert not caplog.records


def test_batch3_the_trial_mode_is_read_at_send_time_and_recorded_only_when_on(creds, monkeypatch, trial_cache):
    """Read when each post is prepared (no restart), and `metadata.publish.upload_post.share_mode` says what THIS
    request asks for: present only when on, never carried over from an earlier attempt sent under another mode."""
    adapter = oup.UploadPostAdapter("instagram")
    first = (_now() - timedelta(hours=1)).isoformat()
    stored = {"request_id": f"{DAY}:instagram:video:a1", "first_sent_at": first,
              "share_mode": "TRIAL_REELS_DONT_SHARE_TO_FOLLOWERS"}
    post = _post("instagram", status="approved", attempts=1,
                 metadata={"dry_run": False, "publish": {"state": "not_sent", "upload_post": stored}})
    off = adapter.prepare(post)
    assert off.payload["fields"] == _REEL_FIELDS and off.payload["request_id"] == stored["request_id"]
    assert "share_mode" not in off.publish_meta["upload_post"]
    assert off.publish_meta["upload_post"]["first_sent_at"] == first          # the rest of the record is kept
    monkeypatch.setattr(oup.settings, "MARKETING_INSTAGRAM_TRIAL_MODE", "share_if_liked")
    on = adapter.prepare(post)
    assert on.payload["fields"]["share_mode"] == "TRIAL_REELS_SHARE_TO_FOLLOWERS_IF_LIKED"
    assert on.publish_meta["upload_post"]["share_mode"] == "TRIAL_REELS_SHARE_TO_FOLLOWERS_IF_LIKED"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "share_if_liked", "dont_share"])
async def test_batch3_a_trial_reel_on_the_wire(up, ledger, monkeypatch, trial_cache, mode):
    monkeypatch.setattr(oup.settings, "MARKETING_INSTAGRAM_TRIAL_MODE", mode)
    up.answer("/upload", _ack(_rid("instagram")))
    outcome, _ = await _send("instagram")
    own = [("user", PROFILE), ("platform[]", "instagram"), ("video", VIDEO_URL), ("async_upload", "true"),
           ("request_id", _rid("instagram")), ("external_id", POST_ID)]
    reel = (("share_to_feed", "true"),) if mode == "off" else (("share_mode", schemas.INSTAGRAM_TRIAL_MODES[mode]),)
    assert _parts(up.uploads[0]) == own + [("title", CAPTION), ("instagram_title", CAPTION), ("media_type", "REELS"),
                                           *reel, ("is_ai_generated", "true"), ("thumb_offset", "500")]
    assert outcome.kind == SUBMITTED
    assert outcome.publish_meta["upload_post"].get("share_mode") == (
        None if mode == "off" else schemas.INSTAGRAM_TRIAL_MODES[mode])


@pytest.mark.asyncio
async def test_batch3_e2e_the_facebook_reel_goes_out_after_its_picture_text_post(penv):
    """Through the publisher with the REAL adapter and client: the Facebook text post of a run goes first
    (/upload_text); its video waits (held, unclaimed) until that post is published, then goes as a Reel."""
    day = mrs.run_date_et().isoformat()
    run_id = str(uuid.uuid4())
    penv.enabled = ["facebook"]
    text_key, video_key = f"{day}:facebook:text", f"{day}:facebook:video"
    primary = penv.seed(platform="facebook", key=text_key, caption=CAPTION, run_id=run_id)
    extra = penv.seed(platform="facebook", key=video_key, run_id=run_id, **_vover("facebook", idempotency_key=video_key))
    penv.up.answer("/upload_text", _ack(f"{text_key}:a1"))
    counters = await pub.publish_cycle()
    assert counters["submitted"] == 1 and counters["retry"] == 1
    assert [r.url.path for r in penv.up.uploads] == ["/api/upload_text"]
    assert penv.row(extra)["status"] == "approved" and penv.row(extra)["attempts"] == 0

    _backdate(penv, primary, 660)
    penv.up.answer("/uploadposts/status", _status("completed", rid=f"{text_key}:a1"))
    penv.up.answer("/uploadposts/history", _hist(_item("facebook", rid=f"{text_key}:a1",
                                                       platform_post_id="104857600123456_1",
                                                       post_url="https://www.facebook.com/104857600123456/posts/1")))
    assert (await pub.reconcile_cycle())["found"] == 1 and penv.row(primary)["status"] == "published"

    penv.up.answer("/upload", _ack(f"{video_key}:a1"))
    counters = await pub.publish_cycle()
    assert counters["submitted"] == 1 and counters["retry"] == 0
    upload = penv.up.uploads[-1]
    assert upload.url.path == "/api/upload" and upload.headers["idempotency-key"] == f"{video_key}:a1"
    parts = dict(_parts(upload))
    assert parts["video"] == VIDEO_URL and parts["facebook_media_type"] == "REELS"
    assert parts["facebook_description"] == REEL and parts["facebook_is_ai_generated"] == "true"
    assert not _NOT_ON_AN_EXTRA & set(parts)
    row = penv.row(extra)
    assert row["status"] == "queued" and row["metadata"]["publish"]["state"] == "submitted"
    assert row["metadata"]["publish"]["upload_post"]["request_id"] == f"{video_key}:a1"
    _assert_review_kept(row)
