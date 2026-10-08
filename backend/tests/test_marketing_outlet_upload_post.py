"""The Upload-Post OUTLET (`app/services/marketing/outlet_upload_post.py`) — design doc §12.10, Stage 2.

One adapter instance per platform: TikTok, YouTube and Instagram get the day's verified MP4 by its
PUBLIC URL; Facebook, LinkedIn and Threads get text. Driven here through the REAL thin client
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
from app.services.marketing import outlets, post_copy
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
    MarketingPublishRefused,
    Outcome,
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
        if path in ("/upload", "/upload_text"):
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
        return [r for r in self.requests if r.url.path in ("/api/upload", "/api/upload_text")]

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
                        ("SUPABASE_URL", SB_URL), ("MARKETING_MEDIA_BUCKET", BUCKET)):
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
    ("facebook", "video"), ("linkedin", "video"), ("threads", "image"),
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
        "is_aigc": True}
    assert oup.platform_fields("instagram", _post("instagram")) == {
        "title": CAPTION, "instagram_title": CAPTION, "media_type": "REELS", "share_to_feed": True,
        "is_ai_generated": True}
    assert oup.platform_fields("youtube", _post("youtube")) == {
        "title": TITLE, "youtube_title": TITLE, "youtube_description": CAPTION, "privacyStatus": "public",
        "containsSyntheticMedia": True, "categoryId": "27", "selfDeclaredMadeForKids": False}
    assert oup.platform_fields("facebook", _post("facebook")) == {"facebook_page_id": FB_PAGE}
    assert oup.platform_fields("linkedin", _post("linkedin")) == {"target_linkedin_page_id": LI_PAGE}
    assert oup.platform_fields("threads", _post("threads")) == {"threads_long_text_as_post": True}
    assert oup.platform_fields("mastodon", _post("threads")) == {}


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
