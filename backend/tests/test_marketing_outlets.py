"""The publisher's platform adapters — `outlet_x`, `outlet_bluesky` and the `outlets` registry
(design doc §12.10, rules/marketing.md §1-§2; plan "Phase 5 — real publishing", Stage 1).

The adapters sit between one `marketing_posts` row and one thin client. They are driven here
through the REAL clients (`app/integrations/x_api.py`, `app/integrations/bluesky.py`) over an
`httpx.MockTransport` installed on each client's `_client`, so the client's outcome split and the
adapter's mapping of it are tested together — the seam where a mistake turns into a double post
(an ambiguous X create treated as "not sent") or a lost one (a Bluesky RecordNotFound misread).

What these pin, by what a regression would cost:

1. Every send ends in exactly one Outcome kind, and only a request that provably never left is
   NOT_SENT. An X ambiguity is never resent; a Bluesky one is resent only with the same rkey and
   record (`swapRecord: null`).
2. The X guard runs BEFORE the claim and refuses what X would bill or block: any link token (the
   same definition the length counter uses), two cashtags, an @mention, media, > 280 weighted.
3. Bluesky's exactly-once key: a deterministic TID, the stored record reused on a retry, link
   facets in UTF-8 BYTE offsets, and a session that is reused, refreshed once, capped and
   circuit-broken instead of burning the 300-a-day createSession allowance.
4. `enabled_platforms()` — the ONE predicate for "publishes and gets Approve buttons" — needs both
   a listing and complete credentials (and an X budget).
5. IMAGE posts (drop 1): only the run's verified post image is ever uploaded — downloaded from its
   public URL and checked (byte cap, sha256) first; any failure before the post itself is NOT_SENT
   (a picture that is not the one reviewed is REFUSED); X refunds exactly what was not billed and is
   matched without its media t.co; Bluesky names the blob's CID in the claim-time record and uploads
   the blob again on every resend.

Hermetic: backend/conftest.py blocks sockets; nothing here reaches a network.
"""

from __future__ import annotations

import base64 as _b64
import copy as _copy
import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.integrations import bluesky, x_api
from app.services.marketing import outlet_base, outlet_bluesky, outlet_x, outlets, post_copy
from app.services.marketing import run_service as mrs
from app.services.marketing.outlet_base import (
    POST_IMAGE_KEY,
    ABSENT,
    AMBIGUOUS,
    FOUND,
    GAVE_UP,
    MANUAL,
    NOT_SENT,
    PUBLISHED,
    REFUSED,
    RETRACTED,
    RETRY,
    UNKNOWN,
    Adapter,
    MarketingPublishRefused,
    MediaProblem,
    PostImage,
    Prepared,
)

# ── shared harness ──────────────────────────────────────────────────────────────────────────────

CK = "ckTESTconsumerKEY0001aaaaaaaa"
CS = "csTESTconsumerSECRET0002bbbbbbbbbbbbbbbbbbbbbb"
X_USER_ID = "1234567890"
AT = f"{X_USER_ID}-TESTaccessTOKEN0003cccccccccccccccccc"
ATS = "atsTESTaccessSECRET0004dddddddddddddddddddddd"
X_CREDS = {
    "MARKETING_X_CONSUMER_KEY": CK,
    "MARKETING_X_CONSUMER_SECRET": CS,
    "MARKETING_X_ACCESS_TOKEN": AT,
    "MARKETING_X_ACCESS_TOKEN_SECRET": ATS,
}
X_POST_ID = "1790000000000000001"

HANDLE = "caydex.bsky.social"
APP_PASSWORD = "abcd-efgh-ijkl-mnop"
ACCESS = ("eyJhbGciOiJFUzI1NksifQ.eyJzY29wZSI6ImNvbS5hdHByb3RvLmFjY2VzcyJ9"
          ".c2lnbmF0dXJlLWFjY2Vzcy10b2tlbi12YWx1ZQ")
NEW_ACCESS = ("eyJhbGciOiJFUzI1NksifQ.eyJzY29wZSI6ImNvbS5hdHByb3RvLmFjY2VzczIifQ"
              ".bmV3LWFjY2Vzcy10b2tlbi12YWx1ZQ")
REFRESH = ("eyJhbGciOiJFUzI1NksifQ.eyJzY29wZSI6ImNvbS5hdHByb3RvLnJlZnJlc2gifQ"
           ".c2lnbmF0dXJlLXJlZnJlc2gtdG9rZW4tdmFsdWU")
NEW_REFRESH = ("eyJhbGciOiJFUzI1NksifQ.eyJzY29wZSI6ImNvbS5hdHByb3RvLnJlZnJlc2gyIn0"
               ".bmV3LXJvdGF0ZWQtcmVmcmVzaC10b2tlbg")
DID = "did:plc:abc123xyz789"
SERVICE = "https://bsky.social"
PDS = "https://morel.us-east.host.bsky.network"
CID = "bafyreib2rxk3rh6kzwq"

CREATE = "com.atproto.server.createSession"
REFRESH_NSID = "com.atproto.server.refreshSession"
PUT = "com.atproto.repo.putRecord"
GET = "com.atproto.repo.getRecord"
DELETE = "com.atproto.repo.deleteRecord"

#: The atproto TID syntax (https://atproto.com/specs/tid), written out independently of the module.
SPEC_TID_RE = re.compile(r"^[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}$")
_S32 = "234567abcdefghijklmnopqrstuvwxyz"

RUN_DAY = date(2026, 9, 30)
X_KEY = "2026-09-30:x:text"
B_KEY = "2026-09-30:bluesky:text"
SMART_LINK = f"{post_copy.LINK_BASE_URL}/bluesky"
B_CAPTION = f"Moats protect returns over decades.\n\nLearn more: {SMART_LINK}"
X_CAPTION = "Compounding needs time, not timing."


def _raise(exc_type: type) -> Callable[[httpx.Request], httpx.Response]:
    """A transport failure raised exactly where a real transport would raise it."""
    def respond(request: httpx.Request) -> httpx.Response:
        raise exc_type(f"simulated {exc_type.__name__}", request=request)
    return respond


def _response(answer: Any, request: httpx.Request) -> httpx.Response:
    if callable(answer):
        return answer(request)
    status, body, *rest = answer
    headers = rest[0] if rest else None
    if body is None:
        return httpx.Response(status, headers=headers)
    if isinstance(body, (bytes, str)):
        return httpx.Response(status, content=body, headers=headers)
    return httpx.Response(status, json=body, headers=headers)


class FakeX:
    """Answers consumed in order; an unscripted call is a 599 (ambiguous) so it can never pass."""

    def __init__(self, answers: List[Any]) -> None:
        self.answers = list(answers)
        self.requests: List[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.answers:
            return httpx.Response(599, json={"title": "Unscripted"})
        return _response(self.answers.pop(0), request)


def _x(monkeypatch, *answers: Any) -> FakeX:
    fake = FakeX(list(answers))
    monkeypatch.setattr(x_api, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


class FakeBsky:
    """A scripted XRPC host keyed by NSID; an unscripted NSID answers 599 (ambiguous)."""

    def __init__(self) -> None:
        self.script: Dict[str, List[Any]] = defaultdict(list)
        self.requests: List[httpx.Request] = []

    def add(self, nsid: str, *answers: Any) -> "FakeBsky":
        self.script[nsid].extend(answers)
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self.script.get(request.url.path.rsplit("/", 1)[-1])
        if not queue:
            return httpx.Response(599, json={"error": "Unscripted"})
        return _response(queue.pop(0), request)

    def nsids(self) -> List[str]:
        return [r.url.path.rsplit("/", 1)[-1] for r in self.requests]

    def of(self, nsid: str) -> List[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/" + nsid)]


@pytest.fixture(autouse=True)
def _fresh_bluesky_state():
    """The Bluesky adapter keeps a module-level session, login log and back-off."""
    outlet_bluesky._reset_state()
    yield
    outlet_bluesky._reset_state()


@pytest.fixture
def x_on(monkeypatch):
    for name, value in X_CREDS.items():
        monkeypatch.setattr(outlet_x.settings, name, value)
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_MONTHLY_BUDGET_USD", 2.0)
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ALLOW_URLS", False)
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_MADE_WITH_AI", True)
    # Image posts are pinned off here (never the .env's); the image tests turn them on.
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_IMAGES", False)
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_IMAGE_POST_MICROS", 200_000)


@pytest.fixture
def bsky_on(monkeypatch):
    monkeypatch.setattr(outlet_bluesky.settings, "MARKETING_BLUESKY_HANDLE", HANDLE)
    monkeypatch.setattr(outlet_bluesky.settings, "MARKETING_BLUESKY_APP_PASSWORD", APP_PASSWORD)
    monkeypatch.setattr(outlet_bluesky.settings, "MARKETING_BLUESKY_SERVICE", SERVICE)


@pytest.fixture
def bsky(monkeypatch, bsky_on) -> FakeBsky:
    fake = FakeBsky()
    monkeypatch.setattr(bluesky, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


def _x_post(caption: Any = X_CAPTION, **over: Any) -> Dict[str, Any]:
    post = {"id": "post-x-1", "platform": "x", "format": "text", "caption": caption, "asset_ids": [],
            "idempotency_key": X_KEY, "metadata": {}, "attempts": 1}
    post.update(over)
    return post


def _b_post(caption: Any = B_CAPTION, **over: Any) -> Dict[str, Any]:
    post = {"id": "post-b-1", "platform": "bluesky", "format": "text", "caption": caption, "asset_ids": [],
            "idempotency_key": B_KEY, "metadata": {}, "attempts": 1}
    post.update(over)
    return post


def _with_bsky_meta(post: Dict[str, Any], **bsky_meta: Any) -> Dict[str, Any]:
    return {**post, "metadata": {"publish": {"state": "unknown", "bluesky": dict(bsky_meta)}}}


def _session_body(access: str = ACCESS, refresh: str = REFRESH) -> Dict[str, Any]:
    return {"accessJwt": access, "refreshJwt": refresh, "did": DID, "handle": HANDLE,
            "didDoc": {"id": DID, "service": [
                {"id": "#atproto_pds", "type": "AtprotoPersonalDataServer", "serviceEndpoint": PDS}]}}


def _put_ok(rkey: str) -> tuple:
    return (200, {"uri": f"at://{DID}/{bluesky.POST_COLLECTION}/{rkey}", "cid": CID,
                  "commit": {"cid": "bafycommit", "rev": "3l2abc"}})


def _secs_from_now(when: Optional[datetime]) -> float:
    assert when is not None and when.tzinfo is not None
    return (when - datetime.now(timezone.utc)).total_seconds()


def _no_secret(*surfaces: Any) -> None:
    blob = " ".join(str(s) for s in surfaces)
    for secret in (CK, CS, AT, ATS, APP_PASSWORD, ACCESS, NEW_ACCESS, REFRESH, NEW_REFRESH):
        assert secret not in blob


# ════════════════════════════════════════════════════════════════════════════════════════════════
# X — prepare (the guard that runs BEFORE the claim: a refusal costs nothing and claims nothing)
# ════════════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("caption", ["", "   \n\t ", None, 42, ["text"]])
def test_x_prepare_refuses_a_post_without_text(x_on, caption):
    with pytest.raises(MarketingPublishRefused, match="no text"):
        outlet_x.ADAPTER.prepare(_x_post(caption))


@pytest.mark.parametrize("fmt", ["video", "carousel", "", None, "TEXT", "IMAGE"])
def test_x_prepare_refuses_anything_but_a_text_or_image_post(x_on, fmt):
    with pytest.raises(MarketingPublishRefused, match="is not published on X"):
        outlet_x.ADAPTER.prepare(_x_post(format=fmt))


def test_x_prepare_refuses_an_image_post_while_x_images_is_off(x_on):
    """MARKETING_X_IMAGES is read at PUBLISH time: a run that froze "image" for X while it was on is
    refused (failed + alert) once it is off — never sent as a text post the owner did not review."""
    with pytest.raises(MarketingPublishRefused, match="MARKETING_X_IMAGES is off — X publishes text only"):
        outlet_x.ADAPTER.prepare(_x_post(format="image"))


def test_x_prepare_refuses_media_on_a_text_post(x_on):
    # The owner reviewed text; media that would not be sent must not ride along unseen.
    with pytest.raises(MarketingPublishRefused, match="media"):
        outlet_x.ADAPTER.prepare(_x_post(asset_ids=["asset-1"]))


@pytest.mark.parametrize("assets", [[], None])
def test_x_prepare_accepts_an_empty_asset_list(x_on, assets):
    assert outlet_x.ADAPTER.prepare(_x_post(asset_ids=assets)).reserve_micros == outlet_x.POST_MICROS


@pytest.mark.parametrize("caption", [
    f"Read more: {post_copy.LINK_BASE_URL}/x",
    "Details at http://example.org/a?b=c",
    "Visit caydex.com for the lesson.",
    "Check investor.gov before you buy anything.",
    "Learn.Money is a phrase X would autolink.",
    "Glued to a newline\nhttps://a.io/x",
])
def test_x_prepare_refuses_every_link_token_while_urls_are_off(x_on, caption):
    # A post with ANY URL costs $0.20 instead of $0.015 — and a bare domain is a URL to X.
    with pytest.raises(MarketingPublishRefused, match="link"):
        outlet_x.ADAPTER.prepare(_x_post(caption))


def test_x_prepare_with_urls_allowed_reserves_the_url_price(monkeypatch, x_on):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ALLOW_URLS", True)
    prepared = outlet_x.ADAPTER.prepare(_x_post(f"Lesson: {post_copy.LINK_BASE_URL}/x and investor.gov"))
    assert prepared.reserve_micros == outlet_x.URL_POST_MICROS == 200_000
    assert prepared.publish_meta["x"]["links"] == 2


@pytest.mark.parametrize("caption", [
    "The U.S.dollar fell against the yen.",
    "A buyer paid $6.9 billion for the business.",
    "Q&A: what is a moat? e.g.the toll bridge.",
    "Rates moved 0.25% and $5 went further.",
])
def test_x_prepare_allows_abbreviations_and_dollar_amounts(x_on, caption):
    prepared = outlet_x.ADAPTER.prepare(_x_post(caption))
    assert prepared.reserve_micros == outlet_x.POST_MICROS == 15_000
    assert prepared.publish_meta["x"]["links"] == 0


@pytest.mark.parametrize("caption", [
    "Compare $AAPL with $MSFT.",
    "Compare $AAPL with $msft.",          # X cashtags are case-insensitive
    "$aapl vs $msft",
    "Class shares: $BRK.B and $AAPL",
])
def test_x_prepare_refuses_two_distinct_cashtags(x_on, caption):
    with pytest.raises(MarketingPublishRefused, match="cashtag"):
        outlet_x.ADAPTER.prepare(_x_post(caption))


@pytest.mark.parametrize("caption", ["One ticker: $AAPL.", "$AAPL then $aapl again — the same tag."])
def test_x_prepare_allows_one_distinct_cashtag(x_on, caption):
    assert outlet_x.ADAPTER.prepare(_x_post(caption)).reserve_micros == outlet_x.POST_MICROS


@pytest.mark.parametrize("caption", ["Thanks @caydex for the lesson.", "@someone_1 asked a good question."])
def test_x_prepare_refuses_an_at_mention(x_on, caption):
    with pytest.raises(MarketingPublishRefused, match="mention"):
        outlet_x.ADAPTER.prepare(_x_post(caption))


def test_x_prepare_does_not_read_an_email_address_as_a_mention(monkeypatch, x_on):
    # With links allowed, the only thing left to refuse would be a (wrong) mention match.
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ALLOW_URLS", True)
    prepared = outlet_x.ADAPTER.prepare(_x_post("Questions? owner@example.com answers them."))
    assert prepared.reserve_micros == outlet_x.URL_POST_MICROS   # example.com IS a link to X
    # …and with links off it is refused for the LINK, never misreported as a mention.
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ALLOW_URLS", False)
    with pytest.raises(MarketingPublishRefused) as err:
        outlet_x.ADAPTER.prepare(_x_post("Questions? owner@example.com answers them."))
    assert "link" in str(err.value) and "mention" not in str(err.value)


def test_x_prepare_refuses_a_fullwidth_at_mention(x_on):
    with pytest.raises(MarketingPublishRefused, match="mention"):
        outlet_x.ADAPTER.prepare(_x_post("Thanks \uff20caydex for the lesson."))


@pytest.mark.parametrize("caption, ok", [
    ("a" * 280, True),
    ("a" * 281, False),
    ("\U0001F4C8" * 140, True),     # an emoji weighs 2
    ("\U0001F4C8" * 141, False),
])
def test_x_prepare_length_limit_is_280_weighted_inclusive(x_on, caption, ok):
    if ok:
        assert outlet_x.ADAPTER.prepare(_x_post(caption)).publish_meta["x"]["weighted_len"] == 280
    else:
        with pytest.raises(MarketingPublishRefused, match="weighted"):
            outlet_x.ADAPTER.prepare(_x_post(caption))


@pytest.mark.parametrize("flag", [True, False])
def test_x_prepare_payload_made_with_ai_follows_the_setting(monkeypatch, x_on, flag):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_MADE_WITH_AI", flag)
    prepared = outlet_x.ADAPTER.prepare(_x_post())
    assert prepared.payload == {"text": X_CAPTION, "made_with_ai": flag}
    assert prepared.publish_meta["x"]["made_with_ai"] is flag


def test_x_prepare_records_the_exact_text_hash_and_never_puts_the_text_in_the_summary(x_on):
    prepared = outlet_x.ADAPTER.prepare(_x_post())
    assert prepared.text_sha256 == hashlib.sha256(X_CAPTION.encode("utf-8")).hexdigest()
    assert X_CAPTION not in prepared.summary and "cost_micros=15000" in prepared.summary


# ── budget + configured ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, micros", [
    (0, 0), (0.0, 0), (-1, 0), (-0.01, 0), (float("nan"), 0), (float("inf"), 0), (float("-inf"), 0),
    (None, 0), ("", 0), ("abc", 0), ("nan", 0), ("inf", 0), (1e-7, 0),
    # a finite but absurd budget whose micro-dollars overflow to inf: X off, never an OverflowError
    # (round(inf) raised here — and outside the digest's _optional, it took the whole digest down)
    (1e303, 0), ("1e303", 0), (1e308, 0),
    ("2", 2_000_000), (2, 2_000_000), (2.0, 2_000_000), (0.015, 15_000), ("0.5", 500_000),
])
def test_x_budget_micros_parsing_is_fail_closed(monkeypatch, raw, micros):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_MONTHLY_BUDGET_USD", raw)
    assert outlet_x.budget_micros() == micros


@pytest.mark.parametrize("missing", sorted(X_CREDS))
def test_x_configured_needs_every_credential(monkeypatch, x_on, missing):
    monkeypatch.setattr(outlet_x.settings, missing, "   ")
    assert outlet_x.ADAPTER.configured() is False
    assert outlet_x.ADAPTER.configured_for_retract() is False


@pytest.mark.parametrize("budget", [0, 0.0, None, -2, float("nan")])
def test_x_configured_needs_a_budget_but_retract_does_not(monkeypatch, x_on, budget):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_MONTHLY_BUDGET_USD", budget)
    assert outlet_x.ADAPTER.configured() is False
    # Lowering the budget to 0 stops new posts; it must not strand a takedown.
    assert outlet_x.ADAPTER.configured_for_retract() is True


def test_x_configured_with_credentials_and_a_budget(x_on):
    assert outlet_x.ADAPTER.configured() is True
    assert outlet_x.ADAPTER.available() is True


# ════════════════════════════════════════════════════════════════════════════════════════════════
# X — send (the outcome matrix over the real client)
# ════════════════════════════════════════════════════════════════════════════════════════════════


async def _x_send(caption: str = X_CAPTION):
    post = _x_post(caption)
    return await outlet_x.ADAPTER.send(post, outlet_x.ADAPTER.prepare(post))


@pytest.mark.asyncio
async def test_x_send_201_is_published_with_the_status_url(monkeypatch, x_on):
    fake = _x(monkeypatch, (201, {"data": {"id": X_POST_ID, "text": X_CAPTION}}))
    outcome = await _x_send()
    assert outcome.kind == PUBLISHED
    assert outcome.external_id == X_POST_ID
    assert outcome.external_url == f"https://x.com/i/web/status/{X_POST_ID}"
    assert abs(_secs_from_now(datetime.fromisoformat(outcome.published_at))) < 30
    (request,) = fake.requests
    assert request.method == "POST" and request.url.path == "/2/tweets"
    assert json.loads(request.content) == {"text": X_CAPTION, "made_with_ai": True}


@pytest.mark.asyncio
async def test_x_send_without_the_ai_flag_omits_it_from_the_body(monkeypatch, x_on):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_MADE_WITH_AI", False)
    fake = _x(monkeypatch, (201, {"data": {"id": X_POST_ID, "text": X_CAPTION}}))
    assert (await _x_send()).kind == PUBLISHED
    assert json.loads(fake.requests[0].content) == {"text": X_CAPTION}


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
async def test_x_send_a_request_that_never_left_is_not_sent(monkeypatch, x_on, exc):
    _x(monkeypatch, _raise(exc))
    outcome = await _x_send()
    assert (outcome.kind, outcome.category) == (NOT_SENT, "transport")
    assert outcome.retry_at is None and outcome.alert is None


@pytest.mark.asyncio
async def test_x_send_429_is_not_sent_until_the_reset(monkeypatch, x_on):
    reset = int(time.time()) + 300
    _x(monkeypatch, (429, {"title": "Too Many Requests"}, {"x-rate-limit-reset": str(reset)}))
    outcome = await _x_send()
    assert (outcome.kind, outcome.category) == (NOT_SENT, "rate_limited")
    assert outcome.retry_at == datetime.fromtimestamp(reset, tz=timezone.utc)


@pytest.mark.asyncio
async def test_x_send_429_without_a_reset_leaves_the_standard_backoff(monkeypatch, x_on):
    _x(monkeypatch, (429, {"title": "Too Many Requests"}))
    outcome = await _x_send()
    assert (outcome.kind, outcome.category, outcome.retry_at) == (NOT_SENT, "rate_limited", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"title": "Forbidden", "detail": "You are not allowed to create a Tweet with duplicate content.",
     "type": "about:blank"},
    {"errors": [{"message": "Status is a duplicate.", "code": 187}]},
])
async def test_x_send_duplicate_403_is_ambiguous_never_refused(monkeypatch, x_on, body):
    # The "duplicate" may be our own earlier, ambiguous attempt: reconcile, never fail, never resend.
    _x(monkeypatch, (403, body))
    outcome = await _x_send()
    assert (outcome.kind, outcome.category) == (AMBIGUOUS, "duplicate")
    assert outcome.alert is None and outcome.refund_micros == 0


@pytest.mark.asyncio
async def test_x_send_generic_403_is_refused_with_an_alert_and_no_refund(monkeypatch, x_on):
    _x(monkeypatch, (403, {"title": "Forbidden", "detail": "You are not permitted to perform this action.",
                           "type": "about:blank"}))
    outcome = await _x_send()
    assert (outcome.kind, outcome.category, outcome.alert) == (REFUSED, "forbidden", "failed")
    assert outcome.refund_micros == 0       # X bills a refused PostCreate: the charge stays
    assert outcome.retry_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status, body", [
    (402, {"title": "Payment Required", "type": "https://api.x.com/2/problems/credits-depleted"}),
    (402, {"title": "Payment Required"}),
    (403, {"title": "Forbidden", "type": "https://api.x.com/2/problems/credits-depleted"}),
])
async def test_x_send_credits_depleted_is_refused_and_refunded(monkeypatch, x_on, status, body):
    _x(monkeypatch, (status, body))
    outcome = await _x_send()
    assert (outcome.kind, outcome.category, outcome.alert) == (REFUSED, "credits", "failed")
    assert outcome.refund_micros == -15_000


@pytest.mark.asyncio
async def test_x_send_credits_depleted_refunds_what_the_claim_reserved_for_a_url_post(monkeypatch, x_on):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ALLOW_URLS", True)
    _x(monkeypatch, (402, {"title": "Payment Required"}))
    post = _x_post(f"Lesson: {post_copy.LINK_BASE_URL}/x")
    prepared = outlet_x.ADAPTER.prepare(post)
    assert prepared.reserve_micros == 200_000
    outcome = await outlet_x.ADAPTER.send(post, prepared)
    assert outcome.refund_micros == -prepared.reserve_micros


@pytest.mark.asyncio
async def test_x_send_401_waits_an_hour_and_raises_an_auth_alert(monkeypatch, x_on):
    _x(monkeypatch, (401, {"title": "Unauthorized", "detail": "Unauthorized"}))
    outcome = await _x_send()
    assert (outcome.kind, outcome.category, outcome.alert) == (NOT_SENT, "auth", "auth")
    assert abs(_secs_from_now(outcome.retry_at) - 3600) < 30


@pytest.mark.asyncio
async def test_x_send_without_credentials_never_calls_and_raises_an_auth_alert(monkeypatch, x_on):
    post = _x_post()
    prepared = outlet_x.ADAPTER.prepare(post)
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ACCESS_TOKEN_SECRET", None)
    fake = _x(monkeypatch)
    outcome = await outlet_x.ADAPTER.send(post, prepared)
    assert (outcome.kind, outcome.category, outcome.alert) == (NOT_SENT, "auth", "auth")
    assert fake.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    (500, {"title": "Internal Error"}),
    (502, "<html>bad gateway</html>"),
    (503, None),
    (408, {"title": "Request Timeout"}),
    _raise(httpx.ReadTimeout),
    _raise(httpx.WriteTimeout),
    _raise(httpx.RemoteProtocolError),
    _raise(httpx.ReadError),
    (201, {"data": {"text": X_CAPTION}}),            # created, but no id to prove it
    (201, b"not json"),
    (200, {"data": {"id": "not-a-number"}}),
])
async def test_x_send_anything_that_may_have_landed_is_ambiguous(monkeypatch, x_on, answer):
    _x(monkeypatch, answer)
    outcome = await _x_send()
    assert (outcome.kind, outcome.category) == (AMBIGUOUS, "server")
    assert outcome.alert is None and outcome.retry_at is None


@pytest.mark.asyncio
async def test_x_send_other_4xx_is_refused(monkeypatch, x_on):
    _x(monkeypatch, (400, {"title": "Invalid Request", "detail": "text is too long",
                           "type": "https://api.x.com/2/problems/invalid-request"}))
    outcome = await _x_send()
    assert (outcome.kind, outcome.category, outcome.alert) == (REFUSED, "invalid", "failed")


@pytest.mark.asyncio
async def test_x_send_error_text_is_scrubbed_and_capped(monkeypatch, x_on, caplog):
    caplog.set_level(logging.DEBUG)
    echo = f"bad token {AT} secret {CS} {ATS} " + "x" * 2000
    _x(monkeypatch, (403, {"title": "Forbidden", "detail": echo}))
    outcome = await _x_send()
    assert outcome.kind == REFUSED
    assert outcome.error and len(outcome.error) <= 500
    _no_secret(outcome.error, caplog.text)


# ════════════════════════════════════════════════════════════════════════════════════════════════
# X — reconcile (read our own timeline; never resend)
# ════════════════════════════════════════════════════════════════════════════════════════════════


def _timeline(*items: Dict[str, Any], count: Optional[int] = None) -> tuple:
    body: Dict[str, Any] = {"meta": {"result_count": len(items) if count is None else count}}
    if items:
        body["data"] = list(items)
    return (200, body)


def _tweet(text: str, pid: str = X_POST_ID, created: str = "2026-09-30T12:00:03.000Z") -> Dict[str, Any]:
    return {"id": pid, "text": text, "created_at": created, "edit_history_tweet_ids": [pid]}


def _queued_x(caption: str = X_CAPTION, started_at: Any = "2026-09-30T12:00:00+00:00", **over: Any):
    publish = {"state": "unknown"}
    if started_at is not None:
        publish["started_at"] = started_at
    return _x_post(caption, status="queued", metadata={"publish": publish}, **over)


@pytest.mark.parametrize("token, uid", [
    (AT, X_USER_ID),
    ("  987-xyz  ", "987"),
    ("abc-123", None),
    ("12a-xyz", None),
    ("1234567890", None),
    ("", None),
    (None, None),
])
def test_x_user_id_comes_from_the_access_token_prefix(monkeypatch, token, uid):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ACCESS_TOKEN", token)
    assert x_api.user_id_from_access_token() == uid


@pytest.mark.asyncio
async def test_x_reconcile_reads_our_timeline_from_ten_minutes_before_the_send(monkeypatch, x_on):
    fake = _x(monkeypatch, _timeline(_tweet(X_CAPTION)))
    result = await outlet_x.ADAPTER.reconcile(_queued_x())
    assert result.kind == FOUND
    (request,) = fake.requests
    assert request.method == "GET" and request.url.path == f"/2/users/{X_USER_ID}/tweets"
    query = parse_qs(urlsplit(str(request.url)).query)
    assert query["start_time"] == ["2026-09-30T11:50:00Z"]
    assert query["max_results"] == ["5"]


@pytest.mark.asyncio
@pytest.mark.parametrize("started, expected", [
    ("2026-09-30T12:00:00Z", "2026-09-30T11:50:00Z"),
    ("2026-09-30T12:00:00", "2026-09-30T11:50:00Z"),               # naive → UTC, never local time
    ("2026-09-30T08:00:00-04:00", "2026-09-30T11:50:00Z"),
    ("2026-10-01T00:05:00+00:00", "2026-09-30T23:55:00Z"),          # crosses midnight and the month
])
async def test_x_reconcile_start_time_handles_every_timestamp_shape(monkeypatch, x_on, started, expected):
    fake = _x(monkeypatch, _timeline())
    await outlet_x.ADAPTER.reconcile(_queued_x(started_at=started))
    assert parse_qs(urlsplit(str(fake.requests[0].url)).query)["start_time"] == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("started", [None, "garbage", "", 12345])
async def test_x_reconcile_falls_back_to_claimed_at_when_started_at_is_unreadable(monkeypatch, x_on, started):
    fake = _x(monkeypatch, _timeline())
    post = _queued_x(started_at=started, claimed_at="2026-09-30T13:00:00+00:00")
    await outlet_x.ADAPTER.reconcile(post)
    assert parse_qs(urlsplit(str(fake.requests[0].url)).query)["start_time"] == ["2026-09-30T12:50:00Z"]


@pytest.mark.asyncio
@pytest.mark.parametrize("caption, stored", [
    ("Q&A: what is a moat?", "Q&amp;A: what is a moat?"),
    ("<b> & </b>", "&lt;b&gt; &amp; &lt;/b&gt;"),
    ("Two  spaces\n\nand a break", "Two spaces and a break"),
    ("Price\u00a0and value", "Price and value"),
    ("Price and value", "Price\u202fand\u00a0value"),
    ("Caf\u00e9 notes", "Cafe\u0301 notes"),
    ("Cafe\u0301 notes", "Caf\u00e9 notes"),
    ("  trimmed  ", "trimmed"),
])
async def test_x_reconcile_matches_on_normalized_text(monkeypatch, x_on, caption, stored):
    _x(monkeypatch, _timeline(_tweet(stored)))
    result = await outlet_x.ADAPTER.reconcile(_queued_x(caption))
    assert result.kind == FOUND and result.external_id == X_POST_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", [
    "Compounding needs time, not timing",          # punctuation is content, never normalized away
    "compounding needs time, not timing.",         # case is content
    "Compounding needs time, not timing. 2/2",
    "",
])
async def test_x_reconcile_does_not_over_match(monkeypatch, x_on, stored):
    _x(monkeypatch, _timeline(_tweet(stored or "something else")))
    result = await outlet_x.ADAPTER.reconcile(_queued_x())
    assert result.kind == ABSENT


@pytest.mark.asyncio
async def test_x_reconcile_found_among_several_costs_the_posts_returned(monkeypatch, x_on):
    _x(monkeypatch, _timeline(
        _tweet("An unrelated post", pid="1790000000000000003"),
        _tweet(X_CAPTION, pid="1790000000000000002", created="2026-09-30T12:00:05.000Z"),
        _tweet("Another one", pid="1790000000000000001"),
    ))
    result = await outlet_x.ADAPTER.reconcile(_queued_x())
    assert result.kind == FOUND
    assert result.external_id == "1790000000000000002"
    assert result.external_url == "https://x.com/i/web/status/1790000000000000002"
    assert result.published_at == "2026-09-30T12:00:05.000Z"
    assert result.cost_micros == 3 * outlet_x.OWNED_READ_MICROS == 3_000


@pytest.mark.asyncio
async def test_x_reconcile_no_match_is_absent_and_never_resend_safe(monkeypatch, x_on):
    _x(monkeypatch, _timeline(_tweet("Something else entirely"), _tweet("And more", pid="17900000000000009")))
    result = await outlet_x.ADAPTER.reconcile(_queued_x())
    assert (result.kind, result.resend_safe) == (ABSENT, False)
    assert result.cost_micros == 2_000
    assert outlet_x.ADAPTER.resend_safe is False


@pytest.mark.asyncio
async def test_x_reconcile_an_empty_timeline_is_absent_and_free(monkeypatch, x_on):
    _x(monkeypatch, _timeline())
    result = await outlet_x.ADAPTER.reconcile(_queued_x())
    assert (result.kind, result.resend_safe, result.cost_micros) == (ABSENT, False, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, cost", [
    ((503, {"title": "Service Unavailable"}), 5_000),
    (_raise(httpx.ReadTimeout), 5_000),
    ((429, {"title": "Too Many Requests"}), 5_000),
    ((200, {"data": [{"id": X_POST_ID}], "meta": {"result_count": 1}}), 5_000),   # malformed item
    ((200, {"meta": {"result_count": 2}}), 5_000),                                # count, no data
    ((401, {"title": "Unauthorized"}), 5_000),
    (_raise(httpx.ConnectError), 0),                                              # never left: free
])
async def test_x_reconcile_an_error_is_unknown_and_counts_its_worst_cost(monkeypatch, x_on, answer, cost):
    _x(monkeypatch, answer)
    result = await outlet_x.ADAPTER.reconcile(_queued_x())
    assert result.kind == UNKNOWN
    assert result.cost_micros == cost
    assert result.error


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["no-user-id-in-here", "", None])
async def test_x_reconcile_without_a_user_id_is_unknown_and_makes_no_call(monkeypatch, x_on, token):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ACCESS_TOKEN", token)
    fake = _x(monkeypatch, _timeline(_tweet(X_CAPTION)))
    result = await outlet_x.ADAPTER.reconcile(_queued_x())
    assert (result.kind, result.cost_micros) == (UNKNOWN, 0)
    assert fake.requests == []


# ════════════════════════════════════════════════════════════════════════════════════════════════
# X — retract
# ════════════════════════════════════════════════════════════════════════════════════════════════


def _published_x(**over: Any) -> Dict[str, Any]:
    return _x_post(status="published", external_id=X_POST_ID,
                   external_url=f"https://x.com/i/web/status/{X_POST_ID}", **over)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    (200, {"data": {"deleted": True}}),
    (204, None),
    (404, {"title": "Not Found Error", "detail": "Could not find tweet"}),   # already gone
])
async def test_x_retract_success_shapes_are_retracted(monkeypatch, x_on, answer):
    fake = _x(monkeypatch, answer)
    result = await outlet_x.ADAPTER.retract(_published_x())
    assert (result.kind, result.cost_micros) == (RETRACTED, outlet_x.DELETE_MICROS)
    (request,) = fake.requests
    assert request.method == "DELETE" and request.url.path == f"/2/tweets/{X_POST_ID}"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, cost", [
    ((503, {"title": "Service Unavailable"}), 10_000),
    ((200, {"data": {"deleted": False}}), 10_000),
    ((429, {"title": "Too Many Requests"}), 10_000),
    (_raise(httpx.ReadTimeout), 10_000),
    ((401, {"title": "Unauthorized"}), 0),
    (_raise(httpx.ConnectError), 0),
])
async def test_x_retract_transient_failures_retry(monkeypatch, x_on, answer, cost):
    _x(monkeypatch, answer)
    result = await outlet_x.ADAPTER.retract(_published_x())
    assert (result.kind, result.cost_micros) == (RETRY, cost)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403])
async def test_x_retract_a_definite_refusal_gives_up(monkeypatch, x_on, status):
    _x(monkeypatch, (status, {"title": "Refused", "detail": "nope"}))
    result = await outlet_x.ADAPTER.retract(_published_x())
    assert (result.kind, result.cost_micros) == (GAVE_UP, outlet_x.DELETE_MICROS)


@pytest.mark.asyncio
@pytest.mark.parametrize("external_id", [None, ""])
async def test_x_retract_without_a_post_id_gives_up_without_calling(monkeypatch, x_on, external_id):
    fake = _x(monkeypatch, (200, {"data": {"deleted": True}}))
    result = await outlet_x.ADAPTER.retract(_x_post(status="published", external_id=external_id))
    assert (result.kind, result.cost_micros) == (GAVE_UP, 0)
    assert fake.requests == []


def test_x_post_url_prefers_the_stored_url_then_builds_one():
    assert outlet_x.ADAPTER.post_url({"external_url": "https://x.com/brand/status/1"}) == "https://x.com/brand/status/1"
    assert outlet_x.ADAPTER.post_url({"external_id": "42"}) == "https://x.com/i/web/status/42"
    assert outlet_x.ADAPTER.post_url({}) is None
    assert outlet_x.ADAPTER.retractable is True


# ════════════════════════════════════════════════════════════════════════════════════════════════
# Bluesky — pure helpers
# ════════════════════════════════════════════════════════════════════════════════════════════════


def _tid_parts(tid: str):
    value = 0
    for ch in tid:
        value = value * 32 + _S32.index(ch)
    return value >> 10, value & 1023, value


@pytest.mark.parametrize("key", [B_KEY, "2026-01-01:bluesky:text", "", "ünïcødé:key", "x" * 500])
@pytest.mark.parametrize("salt", [0, 1, 7, 10_000])
def test_tid_for_is_a_valid_tid_inside_the_run_day(key, salt):
    tid = outlet_bluesky.tid_for(key, salt, RUN_DAY)
    assert SPEC_TID_RE.fullmatch(tid) and outlet_bluesky.TID_RE.fullmatch(tid)
    micros, clock_id, value = _tid_parts(tid)
    assert value < 2 ** 63                          # the top bit is zero
    day_start = int(datetime(2026, 9, 30, tzinfo=timezone.utc).timestamp()) * 1_000_000
    assert day_start <= micros < day_start + 86_400_000_000
    assert 0 <= clock_id < 1024


def test_tid_for_is_deterministic_and_differs_by_salt_key_and_day():
    a = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    assert a == outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    assert a != outlet_bluesky.tid_for(B_KEY, 1, RUN_DAY)
    assert a != outlet_bluesky.tid_for("2026-09-30:x:text", 0, RUN_DAY)
    assert a != outlet_bluesky.tid_for(B_KEY, 0, date(2026, 10, 1))
    salts = {outlet_bluesky.tid_for(B_KEY, s, RUN_DAY) for s in range(50)}
    assert len(salts) == 50


@pytest.mark.parametrize("day", [date(2026, 1, 1), date(2026, 12, 31), date(2028, 2, 29)])
def test_tid_for_sorts_by_day(day):
    # TIDs are base32-SORTABLE: a later run day sorts after every key of an earlier one.
    later = outlet_bluesky.tid_for("a", 0, day + timedelta(days=1))
    assert outlet_bluesky.tid_for("zzz", 999, day) < later
    micros, _clock, _v = _tid_parts(outlet_bluesky.tid_for(B_KEY, 3, day))
    start = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) * 1_000_000
    assert start <= micros < start + 86_400_000_000


def test_link_facets_use_utf8_byte_offsets():
    text = f"Café — \U0001F4C8 {SMART_LINK}"
    (facet,) = outlet_bluesky.link_facets(text)
    start, end = facet["index"]["byteStart"], facet["index"]["byteEnd"]
    assert text.encode("utf-8")[start:end] == SMART_LINK.encode("utf-8")
    assert start == len("Café — \U0001F4C8 ".encode("utf-8")) == 15     # not the 9 code points
    assert facet["features"] == [{"$type": "app.bsky.richtext.facet#link", "uri": SMART_LINK}]


def test_link_facets_two_links_each_get_their_own_offsets():
    text = f"\U0001F4C8 {SMART_LINK} and é {SMART_LINK}/more"
    facets = outlet_bluesky.link_facets(text)
    assert [f["features"][0]["uri"] for f in facets] == [SMART_LINK, f"{SMART_LINK}/more"]
    raw = text.encode("utf-8")
    for f in facets:
        assert raw[f["index"]["byteStart"]:f["index"]["byteEnd"]].decode("utf-8") == f["features"][0]["uri"]


@pytest.mark.parametrize("tail", [".", ",", ")", "!", "?", ";", ":", "\"", "'", ").", "]", "}", "!?"])
def test_link_facets_strip_trailing_punctuation(tail):
    text = f"(Learn more: {SMART_LINK}{tail}"
    (facet,) = outlet_bluesky.link_facets(text)
    assert facet["features"][0]["uri"] == SMART_LINK
    raw = text.encode("utf-8")
    assert raw[facet["index"]["byteStart"]:facet["index"]["byteEnd"]] == SMART_LINK.encode("utf-8")


@pytest.mark.parametrize("url", [
    "https://evil.example/go/bluesky",
    f"{post_copy.LINK_BASE_URL}bble",                                   # /gobble
    post_copy.LINK_BASE_URL,                                           # /go with nothing after
    post_copy.LINK_BASE_URL.replace("https://", "http://") + "/bluesky",
    post_copy.LINK_BASE_URL.replace(".com", ".com.evil.io") + "/bluesky",
    "https://caydexinvest.com/",
])
def test_link_facets_refuse_any_link_but_the_smart_link(url):
    with pytest.raises(MarketingPublishRefused, match="smart link"):
        outlet_bluesky.link_facets(f"Read this: {url} now")


def test_link_facets_without_a_link_is_empty():
    assert outlet_bluesky.link_facets("No links. A bare caydex.com is plain text on Bluesky.") == []


def test_build_record_shape_with_and_without_facets():
    created = datetime(2026, 9, 30, 12, 34, 56, 789_999, tzinfo=timezone.utc)
    plain = outlet_bluesky.build_record("Just text.", created)
    assert plain == {"$type": "app.bsky.feed.post", "text": "Just text.",
                     "createdAt": "2026-09-30T12:34:56.789Z", "langs": ["en"]}
    assert "facets" not in plain                     # omitted, never an empty list
    linked = outlet_bluesky.build_record(B_CAPTION, created)
    assert len(linked["facets"]) == 1 and linked["text"] == B_CAPTION


def test_build_record_created_at_is_utc_milliseconds_z():
    eastern = timezone(timedelta(hours=-4))
    record = outlet_bluesky.build_record("t", datetime(2026, 9, 30, 20, 0, 0, 5_000, tzinfo=eastern))
    assert record["createdAt"] == "2026-10-01T00:00:00.005Z"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", record["createdAt"])


# ════════════════════════════════════════════════════════════════════════════════════════════════
# Bluesky — prepare
# ════════════════════════════════════════════════════════════════════════════════════════════════


def test_bluesky_prepare_mints_the_key_and_record(bsky_on):
    prepared = outlet_bluesky.ADAPTER.prepare(_b_post())
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    assert prepared.payload["rkey"] == rkey
    record = prepared.payload["record"]
    assert record["text"] == B_CAPTION and record["langs"] == ["en"] and len(record["facets"]) == 1
    assert prepared.reserve_micros == 0
    sha = hashlib.sha256(B_CAPTION.encode("utf-8")).hexdigest()
    assert prepared.text_sha256 == sha
    assert prepared.publish_meta == {"bluesky": {"rkey": rkey, "record": record, "salt": 0, "text_sha256": sha}}


def test_bluesky_prepare_reuses_the_stored_key_and_record_on_a_retry(bsky_on):
    first = outlet_bluesky.ADAPTER.prepare(_b_post())
    # What the write-ahead claim stored; time has moved on, so a fresh build would differ.
    stored = _with_bsky_meta(_b_post(), **first.publish_meta["bluesky"])
    stored["metadata"]["publish"]["bluesky"]["record"] = {**first.payload["record"],
                                                          "createdAt": "2026-09-30T01:02:03.004Z"}
    again = outlet_bluesky.ADAPTER.prepare(stored)
    assert again.payload["rkey"] == first.payload["rkey"]
    assert again.payload["record"]["createdAt"] == "2026-09-30T01:02:03.004Z"   # the exact stored bytes


def test_bluesky_prepare_rebuilds_the_record_when_the_text_changed_but_keeps_the_key(bsky_on):
    first = outlet_bluesky.ADAPTER.prepare(_b_post())
    edited = "Moats protect returns. Edited after the claim."
    post = _with_bsky_meta(_b_post(edited), **first.publish_meta["bluesky"])
    again = outlet_bluesky.ADAPTER.prepare(post)
    assert again.payload["record"]["text"] == edited
    assert "facets" not in again.payload["record"]
    assert again.text_sha256 == hashlib.sha256(edited.encode("utf-8")).hexdigest()
    # The key depends on (idempotency key, salt) only: a changed caption can never become a
    # SECOND post — at worst it meets the first one as InvalidSwap.
    assert again.payload["rkey"] == first.payload["rkey"]


def test_bluesky_prepare_after_an_rkey_refusal_mints_the_next_salt(bsky_on):
    post = _with_bsky_meta(_b_post(), salt=1)
    prepared = outlet_bluesky.ADAPTER.prepare(post)
    assert prepared.payload["rkey"] == outlet_bluesky.tid_for(B_KEY, 1, RUN_DAY)
    assert prepared.payload["rkey"] != outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    assert prepared.publish_meta["bluesky"]["salt"] == 1


@pytest.mark.parametrize("bad_rkey", ["not-a-tid", "", "3MWRGAH2LKTRV", "kkkkkkkkkkkkk", 12345])
def test_bluesky_prepare_never_reuses_an_invalid_stored_key(bsky_on, bad_rkey):
    first = outlet_bluesky.ADAPTER.prepare(_b_post())
    post = _with_bsky_meta(_b_post(), **{**first.publish_meta["bluesky"], "rkey": bad_rkey})
    assert outlet_bluesky.ADAPTER.prepare(post).payload["rkey"] == outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)


@pytest.mark.parametrize("metadata", [None, "junk", [], {"publish": "junk"}, {"publish": {"bluesky": "junk"}},
                                      {"publish": {"bluesky": {"record": "junk", "rkey": "x"}}}])
def test_bluesky_prepare_tolerates_malformed_metadata(bsky_on, metadata):
    prepared = outlet_bluesky.ADAPTER.prepare(_b_post(metadata=metadata))
    assert prepared.payload["rkey"] == outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)


@pytest.mark.parametrize("salt", ["abc", [1], {"n": 1}])
def test_bluesky_prepare_raises_only_the_guard_exception_on_a_malformed_salt(bsky_on, salt):
    post = _with_bsky_meta(_b_post(), salt=salt)
    try:
        outlet_bluesky.ADAPTER.prepare(post)
    except MarketingPublishRefused:
        pass


@pytest.mark.parametrize("key", ["", None, "not-a-date:bluesky:text", "2026-13-40:bluesky:text"])
def test_bluesky_prepare_refuses_a_key_without_a_run_date(bsky_on, key):
    with pytest.raises(MarketingPublishRefused, match="run date"):
        outlet_bluesky.ADAPTER.prepare(_b_post(idempotency_key=key))


@pytest.mark.parametrize("over, match", [
    ({"caption": ""}, "no text"),
    ({"caption": "  \n"}, "no text"),
    ({"caption": None}, "no text"),
    ({"format": "video"}, "is not published on Bluesky"),
    ({"format": None}, "is not published on Bluesky"),
    ({"format": "IMAGE"}, "is not published on Bluesky"),
    ({"format": "image"}, "picture was not resolved"),
    ({"asset_ids": ["a1"]}, "media"),
    ({"caption": "a" * 301}, "characters"),
    ({"caption": "Read https://evil.example/x"}, "smart link"),
])
def test_bluesky_prepare_refusals(bsky_on, over, match):
    with pytest.raises(MarketingPublishRefused, match=match):
        outlet_bluesky.ADAPTER.prepare(_b_post(**over))


def test_bluesky_prepare_accepts_exactly_300_characters(bsky_on):
    assert outlet_bluesky.ADAPTER.prepare(_b_post("é" * 300)).payload["record"]["text"] == "é" * 300


# ════════════════════════════════════════════════════════════════════════════════════════════════
# Bluesky — send (sessions + the outcome matrix over the real client)
# ════════════════════════════════════════════════════════════════════════════════════════════════


async def _b_send(post: Optional[Dict[str, Any]] = None):
    post = post or _b_post()
    prepared = outlet_bluesky.ADAPTER.prepare(post)
    return prepared, await outlet_bluesky.ADAPTER.send(post, prepared)


@pytest.mark.asyncio
async def test_bluesky_send_logs_in_then_puts_with_an_explicit_null_swap(bsky):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body())).add(PUT, _put_ok(rkey))
    prepared, outcome = await _b_send()
    assert bsky.nsids() == [CREATE, PUT]
    login, put = bsky.requests
    assert str(login.url).startswith(SERVICE + "/xrpc/")
    assert str(put.url) == f"{PDS}/xrpc/{PUT}"                 # the account's own PDS, from the didDoc
    assert put.headers["Authorization"] == f"Bearer {ACCESS}"
    assert b'"swapRecord":null' in put.content
    body = json.loads(put.content)
    assert body == {"repo": DID, "collection": bluesky.POST_COLLECTION, "rkey": rkey,
                    "record": prepared.payload["record"], "swapRecord": None}
    assert outcome.kind == PUBLISHED
    assert outcome.external_id == f"at://{DID}/{bluesky.POST_COLLECTION}/{rkey}"
    assert outcome.external_url == f"https://bsky.app/profile/{DID}/post/{rkey}"
    meta = outcome.publish_meta["bluesky"]
    assert meta["rkey"] == rkey and meta["record"] == prepared.payload["record"]
    assert (meta["uri"], meta["cid"], meta["repo"], meta["pds"]) == (outcome.external_id, CID, DID, PDS)


@pytest.mark.asyncio
async def test_bluesky_send_reuses_the_session_across_sends(bsky):
    key2 = "2026-10-01:bluesky:text"
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, _put_ok(outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)),
             _put_ok(outlet_bluesky.tid_for(key2, 0, date(2026, 10, 1))))
    assert (await _b_send())[1].kind == PUBLISHED
    assert (await _b_send(_b_post(idempotency_key=key2)))[1].kind == PUBLISHED
    assert bsky.nsids() == [CREATE, PUT, PUT]


@pytest.mark.asyncio
async def test_bluesky_send_refreshes_once_on_an_expired_token_and_publishes(bsky):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, (400, {"error": "ExpiredToken", "message": "Token has expired"}), _put_ok(rkey))
    bsky.add(REFRESH_NSID, (200, _session_body(access=NEW_ACCESS, refresh=NEW_REFRESH)))
    _prepared, outcome = await _b_send()
    assert outcome.kind == PUBLISHED
    assert bsky.nsids() == [CREATE, PUT, REFRESH_NSID, PUT]
    assert bsky.of(REFRESH_NSID)[0].headers["Authorization"] == f"Bearer {REFRESH}"
    assert bsky.of(PUT)[1].headers["Authorization"] == f"Bearer {NEW_ACCESS}"
    assert bsky.of(PUT)[0].content == bsky.of(PUT)[1].content        # the SAME bytes, same key


@pytest.mark.asyncio
async def test_bluesky_send_logs_in_again_when_the_refresh_token_is_gone_too(bsky):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body()), (200, _session_body(access=NEW_ACCESS, refresh=NEW_REFRESH)))
    bsky.add(PUT, (400, {"error": "ExpiredToken"}), _put_ok(rkey))
    bsky.add(REFRESH_NSID, (400, {"error": "ExpiredToken", "message": "refresh token expired"}))
    _prepared, outcome = await _b_send()
    assert outcome.kind == PUBLISHED
    assert bsky.nsids() == [CREATE, PUT, REFRESH_NSID, CREATE, PUT]


@pytest.mark.asyncio
async def test_bluesky_send_expired_twice_is_not_sent_and_never_loops(bsky):
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, (400, {"error": "ExpiredToken"}), (400, {"error": "ExpiredToken"}))
    bsky.add(REFRESH_NSID, (200, _session_body(access=NEW_ACCESS, refresh=NEW_REFRESH)))
    _prepared, outcome = await _b_send()
    assert (outcome.kind, outcome.category) == (NOT_SENT, "auth")
    assert bsky.nsids() == [CREATE, PUT, REFRESH_NSID, PUT]


@pytest.mark.asyncio
async def test_bluesky_send_refreshes_a_session_older_than_its_max_age(monkeypatch, bsky):
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(REFRESH_NSID, (200, _session_body(access=NEW_ACCESS, refresh=NEW_REFRESH)))
    key2 = "2026-10-01:bluesky:text"
    bsky.add(PUT, _put_ok(outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)),
             _put_ok(outlet_bluesky.tid_for(key2, 0, date(2026, 10, 1))))
    await _b_send()
    monkeypatch.setattr(outlet_bluesky, "_session_at",
                        time.monotonic() - outlet_bluesky.SESSION_MAX_AGE_SECONDS - 1)
    assert (await _b_send(_b_post(idempotency_key=key2)))[1].kind == PUBLISHED
    assert bsky.nsids() == [CREATE, PUT, REFRESH_NSID, PUT]


@pytest.mark.asyncio
async def test_bluesky_login_401_opens_an_hour_long_circuit(bsky, caplog):
    caplog.set_level(logging.DEBUG)
    bsky.add(CREATE, (401, {"error": "AuthenticationRequired",
                            "message": f"Invalid identifier or password {APP_PASSWORD}"}))
    _prepared, outcome = await _b_send()
    assert (outcome.kind, outcome.category, outcome.alert) == (NOT_SENT, "auth", "auth")
    assert abs(_secs_from_now(outcome.retry_at) - 3600) < 30
    assert outlet_bluesky.ADAPTER.available() is False
    assert 3500 < outlet_bluesky._blocked_until - time.monotonic() <= 3600
    # A second send inside the hour spends nothing from the 300-a-day login allowance.
    _prepared, again = await _b_send()
    assert (again.kind, again.category) == (NOT_SENT, "auth")
    assert bsky.nsids() == [CREATE]
    _no_secret(outcome.error, again.error, caplog.text)


@pytest.mark.asyncio
async def test_bluesky_login_429_backs_off_until_the_reset(bsky):
    reset = int(time.time()) + 600
    bsky.add(CREATE, (429, {"error": "RateLimitExceeded"}, {"ratelimit-reset": str(reset)}))
    _prepared, outcome = await _b_send()
    assert (outcome.kind, outcome.category) == (NOT_SENT, "rate_limited")
    assert outcome.retry_at == datetime.fromtimestamp(reset, tz=timezone.utc)
    assert outlet_bluesky.ADAPTER.available() is False


@pytest.mark.asyncio
async def test_bluesky_login_cap_per_hour_is_enforced(bsky):
    cap = outlet_bluesky.LOGIN_CAP_PER_HOUR
    bsky.add(CREATE, *[(503, {"error": "InternalServerError"})] * (cap + 3))
    for _ in range(cap):
        _prepared, outcome = await _b_send()
        assert (outcome.kind, outcome.category) == (NOT_SENT, "transport")   # no block on a 5xx login
    _prepared, capped = await _b_send()
    assert (capped.kind, capped.category) == (NOT_SENT, "rate_limited")
    assert abs(_secs_from_now(capped.retry_at) - 1800) < 30
    assert bsky.nsids() == [CREATE] * cap                                    # the next one never went


@pytest.mark.asyncio
async def test_bluesky_put_429_is_not_sent_and_pauses_until_the_reset(bsky):
    reset = int(time.time()) + 120
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, (429, {"error": "RateLimitExceeded"}, {"ratelimit-reset": str(reset)}))
    _prepared, outcome = await _b_send()
    assert (outcome.kind, outcome.category) == (NOT_SENT, "rate_limited")
    assert outcome.retry_at == datetime.fromtimestamp(reset, tz=timezone.utc)
    assert outlet_bluesky.ADAPTER.available() is False
    assert 100 < outlet_bluesky._blocked_until - time.monotonic() <= 121
    _prepared, again = await _b_send()                     # paused: no request at all
    assert (again.kind, again.category) == (NOT_SENT, "rate_limited")
    assert bsky.nsids() == [CREATE, PUT]


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{"ratelimit-reset": str(int(time.time()) - 60)}, {}])
async def test_bluesky_put_429_with_a_past_or_no_reset_does_not_pause(bsky, headers):
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, (429, {"error": "RateLimitExceeded"}, headers))
    _prepared, outcome = await _b_send()
    assert (outcome.kind, outcome.category) == (NOT_SENT, "rate_limited")
    assert outlet_bluesky.ADAPTER.available() is True


@pytest.mark.asyncio
async def test_bluesky_put_invalid_swap_is_ambiguous(bsky):
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, (400, {"error": "InvalidSwap", "message": "Record was at bafy..."}))
    _prepared, outcome = await _b_send()
    assert (outcome.kind, outcome.category) == (AMBIGUOUS, "duplicate")
    assert outcome.alert is None


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    (500, {"error": "InternalServerError"}),
    (502, "<html>bad gateway</html>"),
    (408, {"error": "RequestTimeout"}),
    (200, {"uri": "at://x"}),                                  # no cid: not proof
    (200, b"not json"),
    _raise(httpx.ReadTimeout),
    _raise(httpx.RemoteProtocolError),
])
async def test_bluesky_put_that_may_have_landed_is_ambiguous(bsky, answer):
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, answer)
    _prepared, outcome = await _b_send()
    assert (outcome.kind, outcome.category) == (AMBIGUOUS, "server")


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout])
async def test_bluesky_put_that_never_left_is_not_sent(bsky, exc):
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, _raise(exc))
    _prepared, outcome = await _b_send()
    assert (outcome.kind, outcome.category) == (NOT_SENT, "transport")
    assert outlet_bluesky.ADAPTER.available() is True


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["Invalid rkey: must be a valid record key",
                                     "Input/rkey must be a valid Record Key",
                                     "Bad record key syntax"])
@pytest.mark.parametrize("stored_salt, next_salt", [(None, 1), (2, 3)])
async def test_bluesky_put_rkey_refusal_is_not_sent_with_the_next_salt(bsky, message, stored_salt, next_salt):
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, (400, {"error": "InvalidRequest", "message": message}))
    post = _b_post() if stored_salt is None else _with_bsky_meta(_b_post(), salt=stored_salt)
    _prepared, outcome = await _b_send(post)
    assert (outcome.kind, outcome.category) == (NOT_SENT, "invalid")
    assert outcome.publish_meta == {"bluesky": {"salt": next_salt}}


@pytest.mark.asyncio
async def test_bluesky_put_other_400_is_refused(bsky):
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, (400, {"error": "InvalidRequest",
                         "message": "Invalid app.bsky.feed.post record: text must not be longer than 300 graphemes"}))
    _prepared, outcome = await _b_send()
    assert (outcome.kind, outcome.category, outcome.alert) == (REFUSED, "invalid", "failed")
    assert outcome.publish_meta == {}


@pytest.mark.asyncio
async def test_bluesky_put_401_is_not_sent_with_an_auth_alert_and_a_pause(bsky, caplog):
    caplog.set_level(logging.DEBUG)
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, (401, {"error": "AuthenticationRequired", "message": f"bad token {ACCESS}"}))
    _prepared, outcome = await _b_send()
    assert (outcome.kind, outcome.category, outcome.alert) == (NOT_SENT, "auth", "auth")
    assert abs(_secs_from_now(outcome.retry_at) - 3600) < 30
    assert outlet_bluesky.ADAPTER.available() is False
    _no_secret(outcome.error, caplog.text)


# ════════════════════════════════════════════════════════════════════════════════════════════════
# Bluesky — reconcile (getRecord answers it; only RecordNotFound licenses a resend)
# ════════════════════════════════════════════════════════════════════════════════════════════════


def _queued_b(rkey: str, **bsky_meta: Any) -> Dict[str, Any]:
    return _with_bsky_meta(_b_post(status="queued"), rkey=rkey, **bsky_meta)


def _record_body(rkey: str, text: str = B_CAPTION) -> tuple:
    return (200, {"uri": f"at://{DID}/{bluesky.POST_COLLECTION}/{rkey}", "cid": CID,
                  "value": {"$type": bluesky.POST_COLLECTION, "text": text, "createdAt": "2026-09-30T12:00:00.000Z"}})


@pytest.mark.asyncio
async def test_bluesky_reconcile_found_is_published_with_the_profile_url(bsky, caplog):
    caplog.set_level(logging.WARNING, logger=outlet_bluesky.__name__)
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(GET, _record_body(rkey))
    result = await outlet_bluesky.ADAPTER.reconcile(_queued_b(rkey, repo=DID, pds=PDS, salt=0))
    assert result.kind == FOUND
    assert result.external_id == f"at://{DID}/{bluesky.POST_COLLECTION}/{rkey}"
    assert result.external_url == f"https://bsky.app/profile/{DID}/post/{rkey}"
    assert result.publish_meta["bluesky"]["rkey"] == rkey and result.publish_meta["bluesky"]["cid"] == CID
    (request,) = bsky.requests
    assert request.method == "GET" and str(request.url).startswith(f"{PDS}/xrpc/{GET}?")
    assert parse_qs(urlsplit(str(request.url)).query) == {
        "repo": [DID], "collection": [bluesky.POST_COLLECTION], "rkey": [rkey]}
    assert "Authorization" not in request.headers              # getRecord needs no auth
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_bluesky_reconcile_without_a_recorded_pds_logs_in_to_ask_the_accounts_own_pds(bsky):
    """No PDS recorded (a crash right after the claim): an absent answer from a mirror is not proof
    (review 2026-09-30), so reconcile logs in to learn the account's OWN PDS and asks it."""
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(GET, _record_body(rkey))
    result = await outlet_bluesky.ADAPTER.reconcile(_queued_b(rkey))
    assert result.kind == FOUND
    assert result.external_url == f"https://bsky.app/profile/{DID}/post/{rkey}"
    get = [r for r in bsky.requests if GET in str(r.url)][0]
    assert str(get.url).startswith(f"{PDS}/xrpc/{GET}?")
    assert parse_qs(urlsplit(str(get.url)).query)["repo"] == [DID]


@pytest.mark.asyncio
async def test_bluesky_reconcile_without_a_recorded_pds_and_no_session_is_unknown(bsky):
    bsky.add(CREATE, (401, {"error": "AuthenticationRequired", "message": "Invalid identifier or password"}))
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    result = await outlet_bluesky.ADAPTER.reconcile(_queued_b(rkey))
    assert result.kind == UNKNOWN and not result.resend_safe
    assert not any(GET in str(r.url) for r in bsky.requests)      # never asks a mirror instead


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_handle", [f"@{HANDLE}", f"  {HANDLE} "])
async def test_bluesky_handle_is_normalized_like_the_client(monkeypatch, bsky, raw_handle):
    monkeypatch.setattr(outlet_bluesky.settings, "MARKETING_BLUESKY_HANDLE", raw_handle)
    assert outlet_bluesky.ADAPTER.configured() is True          # the client accepts this spelling
    assert bluesky.handle() == HANDLE
    # The post URL fallback (no repo recorded, no session) uses the normalized handle too.
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    assert outlet_bluesky.ADAPTER.post_url(_queued_b(rkey)) == f"https://bsky.app/profile/{HANDLE}/post/{rkey}"


@pytest.mark.asyncio
async def test_bluesky_reconcile_record_not_found_is_absent_and_resend_safe(bsky):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(GET, (400, {"error": "RecordNotFound", "message": "Could not locate record"}))
    result = await outlet_bluesky.ADAPTER.reconcile(_queued_b(rkey, repo=DID, pds=PDS))
    assert (result.kind, result.resend_safe) == (ABSENT, True)
    assert outlet_bluesky.ADAPTER.resend_safe is True


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    (503, {"error": "ServiceUnavailable"}),
    (400, {"error": "InvalidRequest", "message": "bad repo"}),   # a 400 that is NOT RecordNotFound
    (404, {"error": "RecordNotFound"}),                           # undocumented status: not proof
    (401, {"error": "AuthenticationRequired"}),
    (200, {"uri": "at://x"}),                                     # no value
    _raise(httpx.ReadTimeout),
    _raise(httpx.ConnectError),
])
async def test_bluesky_reconcile_anything_but_record_not_found_is_unknown(bsky, answer):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(GET, answer)
    result = await outlet_bluesky.ADAPTER.reconcile(_queued_b(rkey, repo=DID, pds=PDS))
    assert result.kind == UNKNOWN and result.resend_safe is False
    assert result.error


@pytest.mark.asyncio
async def test_bluesky_reconcile_a_different_text_is_found_with_a_warning(bsky, caplog):
    caplog.set_level(logging.WARNING, logger=outlet_bluesky.__name__)
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(GET, _record_body(rkey, text="An older draft of the caption."))
    result = await outlet_bluesky.ADAPTER.reconcile(_queued_b(rkey, repo=DID, pds=PDS))
    assert result.kind == FOUND
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and r.name == outlet_bluesky.__name__]
    assert len(warnings) == 1
    assert "post-b-1" in warnings[0].getMessage() and rkey in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_bluesky_reconcile_without_a_key_is_unknown_and_makes_no_get(bsky):
    bsky.add(CREATE, (200, _session_body()))
    result = await outlet_bluesky.ADAPTER.reconcile(_b_post(status="queued", metadata={"publish": {}}))
    assert result.kind == UNKNOWN
    assert not any(GET in str(r.url) for r in bsky.requests)


@pytest.mark.asyncio
async def test_bluesky_reconcile_reads_the_key_from_the_external_uri_when_meta_is_gone(bsky):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(GET, _record_body(rkey))
    bsky.add(CREATE, (200, _session_body()))
    post = _b_post(status="queued", metadata={}, external_id=f"at://{DID}/{bluesky.POST_COLLECTION}/{rkey}")
    assert (await outlet_bluesky.ADAPTER.reconcile(post)).kind == FOUND
    get = [r for r in bsky.requests if GET in str(r.url)][0]
    assert parse_qs(urlsplit(str(get.url)).query)["rkey"] == [rkey]


# ════════════════════════════════════════════════════════════════════════════════════════════════
# Bluesky — retract
# ════════════════════════════════════════════════════════════════════════════════════════════════


def _published_b(rkey: str) -> Dict[str, Any]:
    post = _with_bsky_meta(_b_post(status="published"), rkey=rkey, repo=DID, pds=PDS)
    post.update(external_id=f"at://{DID}/{bluesky.POST_COLLECTION}/{rkey}",
                external_url=f"https://bsky.app/profile/{DID}/post/{rkey}")
    return post


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [(200, {"commit": {"cid": "bafy", "rev": "3l"}}), (200, {}), (200, b"")])
async def test_bluesky_retract_delete_ok_is_retracted(bsky, answer):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body())).add(DELETE, answer)
    result = await outlet_bluesky.ADAPTER.retract(_published_b(rkey))
    assert (result.kind, result.cost_micros) == (RETRACTED, 0)
    assert bsky.nsids() == [CREATE, DELETE]
    delete = bsky.of(DELETE)[0]
    assert str(delete.url) == f"{PDS}/xrpc/{DELETE}"
    assert json.loads(delete.content) == {"repo": DID, "collection": bluesky.POST_COLLECTION, "rkey": rkey}
    assert delete.headers["Authorization"] == f"Bearer {ACCESS}"


@pytest.mark.asyncio
async def test_bluesky_retract_refreshes_once_on_an_expired_token(bsky):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(DELETE, (400, {"error": "ExpiredToken"}), (200, {}))
    bsky.add(REFRESH_NSID, (200, _session_body(access=NEW_ACCESS, refresh=NEW_REFRESH)))
    result = await outlet_bluesky.ADAPTER.retract(_published_b(rkey))
    assert result.kind == RETRACTED
    assert bsky.nsids() == [CREATE, DELETE, REFRESH_NSID, DELETE]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    (503, {"error": "ServiceUnavailable"}),
    (429, {"error": "RateLimitExceeded"}),
    (401, {"error": "AuthenticationRequired"}),
    _raise(httpx.ReadTimeout),
    _raise(httpx.ConnectError),
])
async def test_bluesky_retract_transient_failures_retry(bsky, answer):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body())).add(DELETE, answer)
    assert (await outlet_bluesky.ADAPTER.retract(_published_b(rkey))).kind == RETRY


@pytest.mark.asyncio
async def test_bluesky_retract_expired_twice_retries(bsky):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(DELETE, (400, {"error": "ExpiredToken"}), (400, {"error": "ExpiredToken"}))
    bsky.add(REFRESH_NSID, (200, _session_body(access=NEW_ACCESS, refresh=NEW_REFRESH)))
    assert (await outlet_bluesky.ADAPTER.retract(_published_b(rkey))).kind == RETRY


@pytest.mark.asyncio
async def test_bluesky_retract_a_refusal_gives_up(bsky):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body())).add(DELETE, (400, {"error": "InvalidRequest", "message": "no"}))
    result = await outlet_bluesky.ADAPTER.retract(_published_b(rkey))
    assert result.kind == GAVE_UP and result.error


@pytest.mark.asyncio
async def test_bluesky_retract_when_the_login_is_refused_retries(bsky):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (401, {"error": "AuthenticationRequired"}))
    assert (await outlet_bluesky.ADAPTER.retract(_published_b(rkey))).kind == RETRY
    assert bsky.nsids() == [CREATE]


@pytest.mark.asyncio
async def test_bluesky_retract_without_a_key_gives_up_without_calling(bsky):
    result = await outlet_bluesky.ADAPTER.retract(_b_post(status="published", metadata={}, external_id=None))
    assert result.kind == GAVE_UP
    assert bsky.requests == []


@pytest.mark.asyncio
async def test_bluesky_retract_finds_the_key_in_the_external_uri(bsky):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body())).add(DELETE, (200, {}))
    post = _b_post(status="published", metadata={}, external_id=f"at://{DID}/{bluesky.POST_COLLECTION}/{rkey}")
    assert (await outlet_bluesky.ADAPTER.retract(post)).kind == RETRACTED
    assert json.loads(bsky.of(DELETE)[0].content)["rkey"] == rkey


def test_bluesky_post_url_and_flags(bsky_on):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    assert outlet_bluesky.ADAPTER.post_url(_published_b(rkey)) == f"https://bsky.app/profile/{DID}/post/{rkey}"
    built = _with_bsky_meta(_b_post(), rkey=rkey, repo=DID)
    assert outlet_bluesky.ADAPTER.post_url(built) == f"https://bsky.app/profile/{DID}/post/{rkey}"
    assert outlet_bluesky.ADAPTER.post_url(_b_post(metadata={})) is None
    assert outlet_bluesky.ADAPTER.retractable is True
    assert outlet_bluesky.ADAPTER.reconcile_reserve_micros == 0 and outlet_bluesky.ADAPTER.retract_cost_micros == 0


@pytest.mark.parametrize("handle, password, expected", [
    (HANDLE, APP_PASSWORD, True), (HANDLE, None, False), (None, APP_PASSWORD, False), ("  ", APP_PASSWORD, False),
])
def test_bluesky_configured_needs_both(monkeypatch, handle, password, expected):
    monkeypatch.setattr(outlet_bluesky.settings, "MARKETING_BLUESKY_HANDLE", handle)
    monkeypatch.setattr(outlet_bluesky.settings, "MARKETING_BLUESKY_APP_PASSWORD", password)
    assert outlet_bluesky.ADAPTER.configured() is expected
    assert outlet_bluesky.ADAPTER.configured_for_retract() is expected


# ════════════════════════════════════════════════════════════════════════════════════════════════
# outlets — the registry and the ONE "publishes / gets buttons" predicate
# ════════════════════════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def fresh_unknown_log(monkeypatch):
    monkeypatch.setattr(outlets, "_unknown_logged", set())


@pytest.fixture
def nothing_configured(monkeypatch):
    for name in X_CREDS:
        monkeypatch.setattr(outlets.settings, name, None)
    monkeypatch.setattr(outlets.settings, "MARKETING_X_MONTHLY_BUDGET_USD", 0.0)
    monkeypatch.setattr(outlets.settings, "MARKETING_BLUESKY_HANDLE", None)
    monkeypatch.setattr(outlets.settings, "MARKETING_BLUESKY_APP_PASSWORD", None)


def test_registry_holds_every_outlet_under_its_own_name():
    # Stage 1: X + Bluesky direct; Stage 2: the six Upload-Post platforms.
    assert set(outlets.ADAPTERS) == {"x", "bluesky", "tiktok", "youtube", "instagram", "facebook",
                                     "linkedin", "threads"}
    for name, adapter in outlets.ADAPTERS.items():
        assert adapter.platform == name and outlets.adapter_for(name) is adapter
    assert outlets.adapter_for("mastodon") is None and outlets.adapter_for(None) is None
    assert outlets.adapter_for("") is None


@pytest.mark.parametrize("raw, expected", [
    ("", []),
    (None, []),
    (",, ,", []),
    ("x", ["x"]),
    ("X", ["x"]),
    ("bluesky,x", ["bluesky", "x"]),
    (" X , Bluesky ", ["x", "bluesky"]),
    ("x,X, x ,bluesky,BLUESKY", ["x", "bluesky"]),
    ("\tbluesky\n", ["bluesky"]),
])
def test_listed_platforms_parsing(monkeypatch, fresh_unknown_log, raw, expected):
    monkeypatch.setattr(outlets.settings, "MARKETING_PUBLISH_PLATFORMS", raw)
    assert outlets.listed_platforms() == expected


def test_an_unknown_platform_name_is_logged_once_and_ignored(monkeypatch, fresh_unknown_log, caplog):
    caplog.set_level(logging.ERROR, logger=outlets.__name__)
    monkeypatch.setattr(outlets.settings, "MARKETING_PUBLISH_PLATFORMS", "x,Mastodon,mastodon, mastodon ")
    assert outlets.listed_platforms() == ["x"]
    assert outlets.listed_platforms() == ["x"]
    errors = [r for r in caplog.records if r.name == outlets.__name__ and r.levelno == logging.ERROR]
    assert len(errors) == 1 and "mastodon" in errors[0].getMessage()
    monkeypatch.setattr(outlets.settings, "MARKETING_PUBLISH_PLATFORMS", "pinterest,x,mastodon")
    assert outlets.listed_platforms() == ["x"]
    errors = [r for r in caplog.records if r.name == outlets.__name__ and r.levelno == logging.ERROR]
    assert len(errors) == 2 and "pinterest" in errors[1].getMessage()


def test_enabled_platforms_needs_a_listing_and_credentials(monkeypatch, fresh_unknown_log, nothing_configured):
    monkeypatch.setattr(outlets.settings, "MARKETING_PUBLISH_PLATFORMS", "x,bluesky")
    assert outlets.enabled_platforms() == []                          # listed, nothing configured
    monkeypatch.setattr(outlets.settings, "MARKETING_BLUESKY_HANDLE", HANDLE)
    monkeypatch.setattr(outlets.settings, "MARKETING_BLUESKY_APP_PASSWORD", APP_PASSWORD)
    assert outlets.enabled_platforms() == ["bluesky"]
    for name, value in X_CREDS.items():
        monkeypatch.setattr(outlets.settings, name, value)
    assert outlets.enabled_platforms() == ["bluesky"]                 # X credentials, budget still 0
    monkeypatch.setattr(outlets.settings, "MARKETING_X_MONTHLY_BUDGET_USD", 2.0)
    assert outlets.enabled_platforms() == ["x", "bluesky"]            # listing order kept
    monkeypatch.setattr(outlets.settings, "MARKETING_PUBLISH_PLATFORMS", "bluesky")
    assert outlets.enabled_platforms() == ["bluesky"]                 # configured but not listed: off
    monkeypatch.setattr(outlets.settings, "MARKETING_PUBLISH_PLATFORMS", "")
    assert outlets.enabled_platforms() == []


def test_enabled_platforms_with_a_half_set_x_credential_is_off(monkeypatch, fresh_unknown_log, nothing_configured):
    monkeypatch.setattr(outlets.settings, "MARKETING_PUBLISH_PLATFORMS", "x")
    for name, value in X_CREDS.items():
        monkeypatch.setattr(outlets.settings, name, value)
    monkeypatch.setattr(outlets.settings, "MARKETING_X_MONTHLY_BUDGET_USD", 2.0)
    assert outlets.enabled_platforms() == ["x"]
    monkeypatch.setattr(outlets.settings, "MARKETING_X_CONSUMER_SECRET", "")
    assert outlets.enabled_platforms() == []


class _ManualAdapter(Adapter):
    platform = "manualtest"
    retractable = False

    def configured(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_retract_capable(monkeypatch, nothing_configured):
    assert outlets.retract_capable("x") is False
    assert outlets.retract_capable("bluesky") is False
    for name, value in X_CREDS.items():
        monkeypatch.setattr(outlets.settings, name, value)
    # Budget 0 stops publishing, never a takedown.
    assert outlets.retract_capable("x") is True
    monkeypatch.setattr(outlets.settings, "MARKETING_BLUESKY_HANDLE", HANDLE)
    monkeypatch.setattr(outlets.settings, "MARKETING_BLUESKY_APP_PASSWORD", APP_PASSWORD)
    assert outlets.retract_capable("bluesky") is True
    for name in ("tiktok", "", None, "X"):
        assert outlets.retract_capable(name) is False
    manual = _ManualAdapter()
    monkeypatch.setitem(outlets.ADAPTERS, manual.platform, manual)
    assert outlets.retract_capable(manual.platform) is False          # no delete API → no button
    result = await manual.retract({"id": "p"})
    assert result.kind == MANUAL and "by hand" in (result.error or "")
    assert manual.available() is True and manual.configured_for_retract() is True


def test_prepared_is_immutable():
    prepared = Prepared(payload={"text": "t"}, text_sha256="abc")
    with pytest.raises(Exception):
        prepared.reserve_micros = 1  # type: ignore[misc]


# ── adversarial review 2026-09-30: fixes pinned ───────────────────────────────


@pytest.mark.parametrize("caption", [
    "Prices fell.Today the lesson is patience.",       # a glued Title-case word that is a gTLD
    "Stay invested.Now is not the time to panic.",
    "Read HTTPS://EXAMPLE.CO/x for more.",              # an upper-case scheme
    "Visit WWW.EXAMPLE.CO today.",                      # www. in any case
])
def test_x_prepare_refuses_what_x_would_autolink_and_bill_at_twenty_cents(x_on, caption):
    with pytest.raises(MarketingPublishRefused):
        outlet_x.ADAPTER.prepare(_x_post(caption=caption))


@pytest.mark.parametrize("caption", ["The U.S.dollar moved.", "Costs, e.g.the fees, add up.",
                                     "A 3.5x return is rare.", "$6.9 billion was paid."])
def test_x_prepare_still_allows_known_non_links(x_on, caption):
    assert outlet_x.ADAPTER.prepare(_x_post(caption=caption)).reserve_micros == outlet_x.POST_MICROS


def test_x_match_key_treats_a_tco_copy_as_the_same_post():
    caption = "Diversify. Read more: https://caydexinvest.com/go/x #investing"
    stored = "Diversify. Read more: https://t.co/AbCdE12345 #investing"
    assert outlet_x.match_key(caption) == outlet_x.match_key(stored)
    assert outlet_x.match_key(caption) != outlet_x.match_key("Concentrate. Read more: https://t.co/x #investing")


def test_bluesky_is_off_with_an_unusable_service_url(monkeypatch, bsky):
    assert outlet_bluesky.ADAPTER.configured() is True
    for bad in ("", "http://bsky.social", "https://", "  "):
        monkeypatch.setattr(outlet_bluesky.settings, "MARKETING_BLUESKY_SERVICE", bad)
        assert outlet_bluesky.ADAPTER.configured() is False, bad


@pytest.mark.asyncio
async def test_bluesky_an_ambiguous_put_records_the_account_and_its_pds(bsky):
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(PUT, (503, {"error": "ServiceUnavailable"}))
    post = _b_post(status="queued")
    outcome = await outlet_bluesky.ADAPTER.send(post, outlet_bluesky.ADAPTER.prepare(post))
    assert outcome.kind == AMBIGUOUS
    assert outcome.publish_meta["bluesky"]["repo"] == DID and outcome.publish_meta["bluesky"]["pds"] == PDS


@pytest.mark.asyncio
async def test_bluesky_retract_from_another_account_gives_up_without_a_delete(bsky):
    rkey = outlet_bluesky.tid_for(B_KEY, 0, RUN_DAY)
    post = _published_b(rkey)
    other = "did:plc:someoneelse0000"
    post["metadata"]["publish"]["bluesky"]["repo"] = other
    post["external_id"] = f"at://{other}/{bluesky.POST_COLLECTION}/{rkey}"
    bsky.add(CREATE, (200, _session_body()))
    result = await outlet_bluesky.ADAPTER.retract(post)
    assert result.kind == GAVE_UP and "another account" in (result.error or "")
    assert not any(DELETE in str(r.url) for r in bsky.requests)


@pytest.mark.asyncio
async def test_bluesky_a_refresh_without_a_did_document_keeps_the_accounts_own_pds(bsky):
    bsky.add(CREATE, (200, _session_body()))
    first = await outlet_bluesky._get_session()
    assert first["pds"] == PDS
    bsky.add(REFRESH_NSID, (200, {"accessJwt": ACCESS, "refreshJwt": REFRESH, "did": DID, "handle": HANDLE}))
    renewed = await outlet_bluesky._get_session(renew=True)
    assert renewed["pds"] == PDS                       # not the entryway fallback


# ── adversarial review 2026-10-01: fixes pinned ───────────────────────────────


def _others(n: int) -> List[Dict[str, Any]]:
    """`n` unrelated posts from our timeline (distinct ids, none matching X_CAPTION)."""
    return [_tweet(f"Unrelated post {i}", pid=f"17900000000000002{i:02d}") for i in range(n)]


@pytest.mark.asyncio
@pytest.mark.parametrize("returned, count, billed", [
    (1, 1, 1),                  # a normal answer: X bills the posts it returned
    (3, 3, 3),
    (1, 4, 4),                  # a count inside the page is X's own figure, as before
    (1, 5, 5),
    (1, 6, 5),                  # …but never more than the page we asked for
    (1, 10 ** 9, 5),            # the corrupt count of finding #0: $0.005, not $1,000,000
    (1, 2 ** 63, 5),
    (7, 7, 7),                  # more posts than asked: every one returned is billed
    (7, 10 ** 9, 7),
])
async def test_x_reconcile_bills_the_posts_returned_and_never_more_than_the_page(monkeypatch, x_on, returned, count,
                                                                                 billed):
    _x(monkeypatch, _timeline(_tweet(X_CAPTION), *_others(returned - 1), count=count))
    result = await outlet_x.ADAPTER.reconcile(_queued_x())
    assert result.kind == FOUND and result.external_id == X_POST_ID
    assert result.cost_micros == billed * outlet_x.OWNED_READ_MICROS


@pytest.mark.asyncio
async def test_x_reconcile_a_corrupt_count_on_an_absent_post_costs_at_most_the_reserve(monkeypatch, x_on):
    _x(monkeypatch, _timeline(*_others(2), count=10 ** 9))
    result = await outlet_x.ADAPTER.reconcile(_queued_x())
    assert (result.kind, result.resend_safe) == (ABSENT, False)
    assert result.cost_micros == outlet_x.ADAPTER.reconcile_reserve_micros == 5_000


_NO_COUNT = object()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [
    pytest.param(_NO_COUNT, id="absent"), pytest.param(None, id="None"), pytest.param(-1, id="-1"),
    pytest.param(-(10 ** 9), id="-1e9"), pytest.param(True, id="True"), pytest.param(False, id="False"),
    pytest.param("5", id="str-5"), pytest.param("abc", id="str-abc"), pytest.param("", id="str-empty"),
    pytest.param(b"5", id="bytes-5"), pytest.param(2.5, id="2.5"), pytest.param(3.0, id="3.0"),
    pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
    pytest.param(float("-inf"), id="-inf"), pytest.param([], id="list-empty"), pytest.param([5], id="list-5"),
    pytest.param({}, id="dict-empty"), pytest.param({"n": 5}, id="dict"), pytest.param(object(), id="object"),
])
@pytest.mark.parametrize("returned", [0, 2])
async def test_x_reconcile_never_raises_on_a_junk_result_count(monkeypatch, x_on, count, returned):
    # The real client already drops most of these; the adapter must not depend on it. Each one used
    # to raise (nan, inf, "abc", [5] …), bill a negative read (-1), or bill a count X never gave.
    posts = [_tweet(X_CAPTION), *_others(1)][:returned]
    calls: List[str] = []

    async def answered(user_id: str, **_kw: Any) -> Dict[str, Any]:
        calls.append(user_id)
        res: Dict[str, Any] = {"posts": [dict(p) for p in posts]}
        if count is not _NO_COUNT:
            res["result_count"] = count
        return res

    monkeypatch.setattr(x_api, "list_user_posts", answered)
    result = await outlet_x.ADAPTER.reconcile(_queued_x())
    assert calls == [X_USER_ID]
    assert result.kind == (FOUND if returned else ABSENT)
    assert result.cost_micros == returned * outlet_x.OWNED_READ_MICROS


def test_x_metrics_headroom_retired_constant_while_it_lasts():
    assert outlet_x.METRICS_HEADROOM_POSTS == 4
    # The text-post figure, kept only until the measure step reads `metrics_headroom_micros()`. It is
    # deleted then — read with a default so that deletion needs no edit here — and must not drift
    # meanwhile.
    assert getattr(outlet_x, "METRICS_HEADROOM_MICROS", 60_000) == 60_000


@pytest.mark.parametrize("allow_urls, caption, read_edge", [
    (False, X_CAPTION, 1_935_000),
    (True, f"{X_CAPTION} {post_copy.LINK_BASE_URL}/x", 1_195_000),   # URLs on: every X caption carries the /go link
])
def test_x_metrics_headroom_is_four_posts_at_the_price_prepare_would_reserve(monkeypatch, x_on, allow_urls,
                                                                             caption, read_edge):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ALLOW_URLS", allow_urls)
    reserve = outlet_x.ADAPTER.prepare(_x_post(caption)).reserve_micros
    assert reserve == (outlet_x.URL_POST_MICROS if allow_urls else outlet_x.POST_MICROS)
    assert outlet_x.metrics_headroom_micros() == outlet_x.METRICS_HEADROOM_POSTS * reserve
    # "Posting always wins" on the $2 cap: the last month-to-date spend at which a read may start,
    # once the read is charged its full reserve, still leaves exactly four of those posts' money.
    cap = outlet_x.budget_micros()
    assert cap == 2_000_000
    assert cap - outlet_x.METRICS_READ_RESERVE_MICROS - outlet_x.metrics_headroom_micros() == read_edge
    assert read_edge + outlet_x.METRICS_READ_RESERVE_MICROS + 4 * reserve == cap
    # Finding #1's numbers: at $1.799 spent with URLs on, the text-post figure let a read start, after
    # which not one $0.20 post fitted. Now it may start only while text posts are what X costs.
    may_read = 1_799_000 + outlet_x.METRICS_READ_RESERVE_MICROS + outlet_x.metrics_headroom_micros() <= cap
    assert may_read is (not allow_urls)


def test_x_metrics_headroom_reads_the_url_switch_at_call_time(monkeypatch, x_on):
    for allow_urls, expected in ((False, 60_000), (True, 800_000), (False, 60_000)):
        monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ALLOW_URLS", allow_urls)
        assert outlet_x.metrics_headroom_micros() == expected


# ════════════════════════════════════════════════════════════════════════════════════════════════
# IMAGE posts (drop 1, contract C9) — the shared picture helpers, then X and Bluesky
# ════════════════════════════════════════════════════════════════════════════════════════════════

IMG = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 8 + b"\xff\xd9"
IMG_SHA = hashlib.sha256(IMG).hexdigest()
IMG_CID = bluesky.raw_cid_for_sha256(IMG_SHA)
PIC_URL = "https://xyz.supabase.co/storage/v1/object/public/marketing-media/2026-09-30/card-0123456789abcdef.jpg"
PIC_ID = "0b6f3a52-3c1d-4f7e-9a10-2b8c4d6e8f02"
RUN_ID = "11111111-2222-4333-8444-555555555555"
PIC_TITLE = "Why time in the market compounds"
PIC_PARAS = ("Compounding needs years, not weeks.", "Missing a few strong days changes the result a lot.")
ALT = PIC_TITLE + "\n\n" + "\n\n".join(PIC_PARAS)
MEDIA_ID = "1880000000000000001"
X_IMG_KEY = "2026-09-30:x:image"
B_IMG_KEY = "2026-09-30:bluesky:image"
B_IMG_CAPTION = "Moats protect returns over decades. AI-assisted"


def _pic(**over: Any) -> PostImage:
    kw = dict(asset_id=PIC_ID, url=PIC_URL, sha256=IMG_SHA, size=len(IMG), title=PIC_TITLE, paragraphs=PIC_PARAS)
    kw.update(over)
    return PostImage(**kw)


class PicServer:
    """The public media bucket: serves the picture at PIC_URL (or a scripted answer) and records reads."""

    def __init__(self) -> None:
        self.body: Any = IMG
        self.status = 200
        self.requests: List[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if callable(self.body):
            return self.body(request)
        if str(request.url) != PIC_URL:
            return httpx.Response(404)
        return httpx.Response(self.status, content=self.body)


@pytest.fixture
def pics(monkeypatch) -> PicServer:
    server = PicServer()
    monkeypatch.setattr(outlet_base, "_fetch_transport", httpx.MockTransport(server.handler))
    return server


# ── outlet_base: alt text, the verified download, the ledger loader ─────────────────────────────


def test_alt_text_is_the_title_and_whole_paragraphs():
    assert outlet_base.alt_text(PIC_TITLE, PIC_PARAS, 1000) == ALT
    assert _pic().alt(1000) == ALT


def test_alt_text_drops_whole_paragraphs_never_a_cut_sentence():
    paras = ("a" * 300, "This is not a recommendation to buy.", "c" * 300)
    title = "T" * 10
    full = outlet_base.alt_text(title, paras, 10_000)
    assert full.count("\n\n") == 3
    # One character short of the full text: the LAST paragraph goes as a whole.
    limited = outlet_base.alt_text(title, paras, len(full) - 1)
    assert limited == title + "\n\n" + paras[0] + "\n\n" + paras[1]
    # A limit that cuts the second paragraph keeps only what fits whole — never "…is not a".
    assert outlet_base.alt_text(title, paras, len(title) + 2 + 300 + 5) == title + "\n\n" + paras[0]
    # Once a paragraph does not fit, none after it is added (the order of the picture is kept).
    assert outlet_base.alt_text(title, ("x" * 50, "y"), len(title) + 10) == title


def test_alt_text_cuts_only_a_title_longer_than_the_limit():
    assert outlet_base.alt_text("T" * 20, ("p",), 8) == "T" * 8
    assert outlet_base.alt_text("", ("p", "q"), 100) == "p\n\nq"


@pytest.mark.asyncio
async def test_fetch_post_image_returns_the_checked_bytes(pics):
    assert await outlet_base.fetch_post_image(PIC_URL, size=len(IMG), sha256=IMG_SHA) == IMG
    (req,) = pics.requests
    assert req.method == "GET" and "authorization" not in req.headers   # a public object, no credential


@pytest.mark.asyncio
@pytest.mark.parametrize("body, definite, why", [
    (IMG[:-1] + b"\x00", True, "not the one recorded"),     # same size, other bytes
    (IMG[:-1], True, "not the one recorded"),                # shorter
    (IMG + b"x", True, "larger than"),                       # longer: the cap stops the read
    (IMG * 50, True, "larger than"),
])
async def test_fetch_post_image_refuses_a_picture_that_is_not_the_one_reviewed(pics, body, definite, why):
    pics.body = body
    out = await outlet_base.fetch_post_image(PIC_URL, size=len(IMG), sha256=IMG_SHA)
    assert isinstance(out, MediaProblem) and out.definite is definite and why in out.error


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 403, 500, 301, 206])
async def test_fetch_post_image_non_200_is_retryable(pics, status):
    pics.status = status
    out = await outlet_base.fetch_post_image(PIC_URL, size=len(IMG), sha256=IMG_SHA)
    assert isinstance(out, MediaProblem) and out.definite is False and f"HTTP {status}" in out.error


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
async def test_fetch_post_image_transport_failure_is_retryable(pics, error):
    pics.body = _raise(error)
    out = await outlet_base.fetch_post_image(PIC_URL, size=len(IMG), sha256=IMG_SHA)
    assert isinstance(out, MediaProblem) and out.definite is False and error.__name__ in out.error


@pytest.mark.asyncio
async def test_fetch_post_image_unreadable_size_is_definite(pics):
    out = await outlet_base.fetch_post_image(PIC_URL, size="big", sha256=IMG_SHA)
    assert isinstance(out, MediaProblem) and out.definite and pics.requests == []


class _Ledger:
    """`get_asset` / `get_script` / `public_url` — what `load_post_image` reads."""

    def __init__(self) -> None:
        self.asset: Any = {"id": PIC_ID, "run_id": RUN_ID, "kind": "card", "status": "ready",
                           "storage_path": "2026-09-30/card-0123456789abcdef.jpg", "content_type": "image/jpeg",
                           "bytes": len(IMG), "sha256": IMG_SHA.upper(),
                           "metadata": {"image_role": "post_image", "onscreen_text": [PIC_TITLE, *PIC_PARAS, "f"]}}
        self.script: Any = {"run_id": RUN_ID, "status": "accepted",
                            "output": {"image_post": {"title": PIC_TITLE, "paragraphs": list(PIC_PARAS)}}}
        self.fail: Optional[BaseException] = None
        self.calls: List[str] = []

    async def get_asset(self, asset_id: str) -> Any:
        self.calls.append(f"asset:{asset_id}")
        if self.fail is not None:
            raise self.fail
        return _copy.deepcopy(self.asset)

    async def get_script(self, run_id: str) -> Any:
        self.calls.append(f"script:{run_id}")
        return _copy.deepcopy(self.script)

    @staticmethod
    def public_url(path: str) -> str:
        return f"https://xyz.supabase.co/storage/v1/object/public/marketing-media/{path}"


@pytest.fixture
def ledger(monkeypatch) -> _Ledger:
    fake = _Ledger()
    # `load_post_image` imports the getter INSIDE the call: the SOURCE module's binding is patched.
    monkeypatch.setattr(mrs, "get_marketing_run_service", lambda: fake)
    return fake


def _image_row(**over: Any) -> Dict[str, Any]:
    row = {"id": "post-img-1", "run_id": RUN_ID, "platform": "bluesky", "format": "image",
           "asset_ids": [PIC_ID], "caption": B_IMG_CAPTION, "idempotency_key": B_IMG_KEY}
    row.update(over)
    return row


@pytest.mark.asyncio
async def test_load_post_image_resolves_the_runs_verified_card(ledger):
    image = await outlet_base.load_post_image(_image_row())
    assert image == _pic()                       # sha256 lower-cased, URL from the storage path
    assert ledger.calls == [f"asset:{PIC_ID}", f"script:{RUN_ID}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_over, why", [
    ({"status": "pending_upload"}, "status"),
    ({"kind": "video"}, "post image"),
    ({"metadata": {"image_role": "other"}}, "post image"),
    ({"metadata": None}, "post image"),
    ({"run_id": "another-run"}, "another run"),
    ({"content_type": "image/png"}, "content type"),
    ({"bytes": 0}, "size"),
    ({"bytes": 950_001}, "size"),
    ({"bytes": True}, "size"),
    ({"bytes": "5"}, "size"),
    ({"sha256": "abc"}, "sha256"),
    ({"sha256": None}, "sha256"),
    ({"storage_path": ""}, "storage path"),
])
async def test_load_post_image_refuses_an_asset_that_is_not_the_post_image(ledger, asset_over, why):
    ledger.asset.update(asset_over)
    out = await outlet_base.load_post_image(_image_row())
    assert isinstance(out, MediaProblem) and out.definite and why in out.error


@pytest.mark.asyncio
@pytest.mark.parametrize("row_over, why", [
    ({"asset_ids": []}, "names 0"), ({"asset_ids": None}, "names 0"), ({"asset_ids": [PIC_ID, "b"]}, "names 2"),
    ({"asset_ids": PIC_ID}, "names 0"), ({"run_id": None}, "no run"),
])
async def test_load_post_image_needs_exactly_one_asset_and_a_run(ledger, row_over, why):
    out = await outlet_base.load_post_image(_image_row(**row_over))
    assert isinstance(out, MediaProblem) and out.definite and why in out.error and ledger.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("script", [
    None, {"status": "rejected", "output": {"image_post": {"title": "t", "paragraphs": ["a", "b"]}}},
    {"status": "accepted", "output": {}}, {"status": "accepted", "output": {"image_post": {"title": "t",
                                                                                          "paragraphs": ["a"]}}},
    {"status": "accepted", "output": "x"},
])
async def test_load_post_image_needs_the_accepted_image_post(ledger, script):
    ledger.script = script
    out = await outlet_base.load_post_image(_image_row())
    assert isinstance(out, MediaProblem) and out.definite and "image_post" in out.error


@pytest.mark.asyncio
async def test_load_post_image_missing_asset_is_definite_and_a_failed_read_is_not(ledger):
    ledger.asset = None
    out = await outlet_base.load_post_image(_image_row())
    assert isinstance(out, MediaProblem) and out.definite and "no asset" in out.error
    ledger.fail = RuntimeError("ledger down")
    out = await outlet_base.load_post_image(_image_row())
    assert isinstance(out, MediaProblem) and not out.definite and "RuntimeError" in out.error


@pytest.mark.asyncio
async def test_load_post_image_refuses_a_non_https_url(ledger, monkeypatch):
    monkeypatch.setattr(_Ledger, "public_url", staticmethod(lambda path: f"http://local/{path}"))
    out = await outlet_base.load_post_image(_image_row())
    assert isinstance(out, MediaProblem) and out.definite and "https" in out.error


# ── X: the image post ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def x_images(monkeypatch, x_on):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_IMAGES", True)


def _x_img_post(caption: str = X_CAPTION, **over: Any) -> Dict[str, Any]:
    post = _x_post(caption, format="image", asset_ids=[PIC_ID], idempotency_key=X_IMG_KEY)
    post[POST_IMAGE_KEY] = _pic()
    post.update(over)
    return post


def test_x_prepare_an_image_post(x_images):
    prepared = outlet_x.ADAPTER.prepare(_x_img_post())
    assert prepared.payload["image"] == {**_pic().meta(), "alt": ALT}
    assert prepared.reserve_micros == 200_000 and prepared.pre_send_charge == ("x_media_alt", 5_000)
    x_meta = prepared.publish_meta["x"]
    assert x_meta["image"] == _pic().meta() and x_meta["alt_micros"] == 5_000 and "alt" not in x_meta
    assert "x image" in prepared.summary and PIC_TITLE not in prepared.summary


@pytest.mark.parametrize("setting, allow_urls, caption, reserve", [
    (200_000, False, X_CAPTION, 200_000),
    (15_000, False, X_CAPTION, 15_000),     # the console confirmed a text-post price
    (0, False, X_CAPTION, 15_000),          # never below a text post
    (15_000, True, f"{X_CAPTION} {post_copy.LINK_BASE_URL}/x", 200_000),   # never below a URL post
    (500_000, True, f"{X_CAPTION} {post_copy.LINK_BASE_URL}/x", 500_000),
])
def test_x_image_reserve_is_the_setting_with_floors(monkeypatch, x_images, setting, allow_urls, caption, reserve):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_IMAGE_POST_MICROS", setting)
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ALLOW_URLS", allow_urls)
    assert outlet_x.ADAPTER.prepare(_x_img_post(caption)).reserve_micros == reserve


@pytest.mark.parametrize("over, why", [
    ({POST_IMAGE_KEY: None}, "not resolved"),
    ({"asset_ids": []}, "exactly its one picture"),
    ({"asset_ids": [PIC_ID, "other"]}, "exactly its one picture"),
    ({"asset_ids": ["other"]}, "exactly its one picture"),
    ({"caption": "$AAPL and $MSFT"}, "cashtags"),
    ({"caption": "Thanks @someone"}, "@mention"),
    ({"caption": "Read https://example.com/x"}, "MARKETING_X_ALLOW_URLS"),
    ({"caption": "a" * 281}, "weighted"),
])
def test_x_prepare_refuses_a_bad_image_post(x_images, over, why):
    with pytest.raises(MarketingPublishRefused, match=why):
        outlet_x.ADAPTER.prepare(_x_img_post(**over))


def test_x_text_post_is_unchanged_while_images_are_on(x_images):
    prepared = outlet_x.ADAPTER.prepare(_x_post())
    assert prepared.reserve_micros == 15_000 and prepared.pre_send_charge is None
    assert "image" not in prepared.payload


def _x_paths(fake: FakeX) -> List[str]:
    return [r.url.path for r in fake.requests]


async def _x_img_send(post: Optional[Dict[str, Any]] = None):
    post = post or _x_img_post()
    prepared = outlet_x.ADAPTER.prepare(post)
    sent = {k: v for k, v in post.items() if k != POST_IMAGE_KEY}     # send() sees the CLAIMED row
    return prepared, await outlet_x.ADAPTER.send(sent, prepared)


UPLOADED = (200, {"data": {"id": MEDIA_ID, "media_key": f"3_{MEDIA_ID}", "size": len(IMG)}})
ALT_SET = (200, {"data": {"id": MEDIA_ID}})


@pytest.mark.asyncio
async def test_x_image_send_uploads_sets_alt_then_posts_with_the_media(monkeypatch, x_images, pics):
    fake = _x(monkeypatch, UPLOADED, ALT_SET,
              (201, {"data": {"id": X_POST_ID, "text": f"{X_CAPTION} https://t.co/AbC123"}}))
    prepared, outcome = await _x_img_send()
    assert outcome.kind == PUBLISHED and outcome.external_id == X_POST_ID and outcome.refund_micros == 0
    assert outcome.publish_meta == {"x": {**prepared.publish_meta["x"], "media_id": MEDIA_ID}}
    assert _x_paths(fake) == ["/2/media/upload", "/2/media/metadata", "/2/tweets"]
    upload, alt, create = (json.loads(r.content) for r in fake.requests)
    assert _b64.b64decode(upload["media"]) == IMG and upload["media_category"] == "tweet_image"
    assert alt == {"id": MEDIA_ID, "metadata": {"alt_text": {"text": ALT}}}
    assert create == {"text": X_CAPTION, "made_with_ai": True, "media": {"media_ids": [MEDIA_ID]}}
    assert len(pics.requests) == 1          # the picture was downloaded and checked once


@pytest.mark.asyncio
@pytest.mark.parametrize("body, kind", [(IMG[:-1] + b"\x00", REFUSED), (IMG + b"!", REFUSED)])
async def test_x_image_send_never_uploads_a_picture_that_is_not_the_one_reviewed(monkeypatch, x_images, pics,
                                                                                  body, kind):
    fake = _x(monkeypatch)
    pics.body = body
    prepared, outcome = await _x_img_send()
    assert outcome.kind == kind and outcome.category == "media" and outcome.alert == "failed"
    assert outcome.refund_micros == -(200_000 + 5_000)       # nothing was billed
    assert fake.requests == []


@pytest.mark.asyncio
async def test_x_image_send_download_failure_is_not_sent(monkeypatch, x_images, pics):
    fake = _x(monkeypatch)
    pics.status = 503
    _prepared, outcome = await _x_img_send()
    assert outcome.kind == NOT_SENT and outcome.category == "media" and outcome.refund_micros == -205_000
    assert fake.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, kind, category, alert", [
    (_raise(httpx.ConnectError), NOT_SENT, "transport", None),
    (_raise(httpx.ReadTimeout), NOT_SENT, "media", None),          # the MEDIA may exist; the post cannot
    ((503, {"title": "Service Unavailable"}), NOT_SENT, "media", None),
    ((400, {"title": "Invalid Request", "detail": "bad image"}), NOT_SENT, "media", None),
    ((403, {"title": "Forbidden"}), NOT_SENT, "media", None),
    (lambda request: httpx.Response(429, json={"title": "Too Many Requests"},   # reset read at REQUEST time
                                    headers={"x-rate-limit-reset": str(int(time.time()) + 900)}),
     NOT_SENT, "rate_limited", None),
    ((401, {"title": "Unauthorized"}), NOT_SENT, "auth", "auth"),
    ((402, {"title": "Payment Required"}), REFUSED, "credits", "failed"),
    ((200, {"data": {"id": MEDIA_ID, "processing_info": {"state": "pending"}}}), NOT_SENT, "media", None),
    ((200, {"data": {"id": MEDIA_ID, "processing_info": {"state": "failed"}}}), NOT_SENT, "media", None),
])
async def test_x_image_upload_failure_ends_before_any_post_and_refunds_everything(monkeypatch, x_images, pics,
                                                                                  answer, kind, category, alert):
    fake = _x(monkeypatch, answer)
    _prepared, outcome = await _x_img_send()
    assert (outcome.kind, outcome.category, outcome.alert) == (kind, category, alert)
    assert outcome.refund_micros == -205_000                   # an upload has no price; nothing else ran
    assert _x_paths(fake) == ["/2/media/upload"]
    _no_secret(outcome.error)
    if category == "rate_limited":
        assert 800 < _secs_from_now(outcome.retry_at) < 1000


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, kind, category, refund", [
    (_raise(httpx.ConnectError), NOT_SENT, "transport", -205_000),     # never left: not billed
    (_raise(httpx.ReadTimeout), NOT_SENT, "media", -200_000),          # may have been billed: kept
    ((503, {"title": "Service Unavailable"}), NOT_SENT, "media", -200_000),
    ((400, {"title": "Invalid Request"}), NOT_SENT, "media", -200_000),
    ((429, {"title": "Too Many Requests"}), NOT_SENT, "rate_limited", -200_000),
    ((401, {"title": "Unauthorized"}), NOT_SENT, "auth", -200_000),
    ((402, {"title": "Payment Required"}), REFUSED, "credits", -205_000),
    ((200, {"data": {"id": "1880000000000000009"}}), NOT_SENT, "media", -200_000),   # another media's id
])
async def test_x_image_alt_text_failure_ends_before_the_post(monkeypatch, x_images, pics, answer, kind, category,
                                                             refund):
    fake = _x(monkeypatch, UPLOADED, answer)
    _prepared, outcome = await _x_img_send()
    assert (outcome.kind, outcome.category, outcome.refund_micros) == (kind, category, refund)
    assert _x_paths(fake) == ["/2/media/upload", "/2/media/metadata"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, kind, refund", [
    (_raise(httpx.ConnectError), NOT_SENT, -200_000),      # the create never left; the alt text did
    ((402, {"title": "Payment Required"}), REFUSED, -200_000),
    ((503, {"title": "Service Unavailable"}), AMBIGUOUS, 0),
    (_raise(httpx.ReadTimeout), AMBIGUOUS, 0),
    ((429, {"title": "Too Many Requests"}), NOT_SENT, 0),  # as for a text post: a refused create may be billed
])
async def test_x_image_create_failure_keeps_the_text_post_rules(monkeypatch, x_images, pics, answer, kind, refund):
    fake = _x(monkeypatch, UPLOADED, ALT_SET, answer)
    prepared, outcome = await _x_img_send()
    assert outcome.kind == kind and outcome.refund_micros == refund
    assert _x_paths(fake) == ["/2/media/upload", "/2/media/metadata", "/2/tweets"]
    if kind == AMBIGUOUS:
        # Whatever may be live names its media, merged with the claim's own `x` record.
        assert outcome.publish_meta == {"x": {**prepared.publish_meta["x"], "media_id": MEDIA_ID}}


@pytest.mark.asyncio
async def test_x_image_resend_uploads_again(monkeypatch, x_images, pics):
    """A NOT_SENT attempt goes back to `approved`; the next one uploads afresh (a media id expires)."""
    fake = _x(monkeypatch, UPLOADED, (503, {"title": "Service Unavailable"}),
              UPLOADED, ALT_SET, (201, {"data": {"id": X_POST_ID, "text": X_CAPTION}}))
    _p, first = await _x_img_send()
    _p, second = await _x_img_send()
    assert (first.kind, second.kind) == (NOT_SENT, PUBLISHED)
    assert _x_paths(fake).count("/2/media/upload") == 2 and len(pics.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("stored, caption, found", [
    (f"{X_CAPTION} https://t.co/AbC123", X_CAPTION, True),
    (X_CAPTION, X_CAPTION, True),                                    # if X ever stops appending it
    (f"{X_CAPTION}  https://t.co/AbC123  ", X_CAPTION, True),
    (f"{X_CAPTION} https://t.co/L1nk https://t.co/M3dia", f"{X_CAPTION} https://caydexinvest.com/go/x", True),
    (f"Another post https://t.co/AbC123", X_CAPTION, False),
    (f"{X_CAPTION} https://t.co/AbC123 extra", X_CAPTION, False),   # only a TRAILING link is the media's
])
async def test_x_image_reconcile_matches_without_the_media_link(monkeypatch, x_images, stored, caption, found):
    async def timeline(user_id: str, **_kw: Any) -> Dict[str, Any]:
        return {"posts": [_tweet(stored)], "result_count": 1}

    monkeypatch.setattr(x_api, "list_user_posts", timeline)
    post = _queued_x(caption, format="image", asset_ids=[PIC_ID])
    result = await outlet_x.ADAPTER.reconcile(post)
    assert result.kind == (FOUND if found else ABSENT)


@pytest.mark.asyncio
async def test_x_text_reconcile_never_strips_a_trailing_link(monkeypatch, x_on):
    async def timeline(user_id: str, **_kw: Any) -> Dict[str, Any]:
        return {"posts": [_tweet(f"{X_CAPTION} https://t.co/AbC123")], "result_count": 1}

    monkeypatch.setattr(x_api, "list_user_posts", timeline)
    assert (await outlet_x.ADAPTER.reconcile(_queued_x())).kind == ABSENT


@pytest.mark.parametrize("images, allow_urls, setting, per_post", [
    (False, False, 200_000, 15_000),
    (False, True, 200_000, 200_000),
    (True, False, 200_000, 205_000),
    (True, True, 200_000, 205_000),
    (True, False, 15_000, 20_000),           # a confirmed $0.015 image post + its $0.005 alt text
    (True, True, 15_000, 205_000),
])
def test_x_metrics_headroom_prices_image_posts_while_they_are_on(monkeypatch, x_on, images, allow_urls, setting,
                                                                 per_post):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_IMAGES", images)
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_ALLOW_URLS", allow_urls)
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_IMAGE_POST_MICROS", setting)
    assert outlet_x.metrics_headroom_micros() == 4 * per_post


# ── Bluesky: the image post ─────────────────────────────────────────────────────────────────────


def _b_img_post(caption: str = B_IMG_CAPTION, **over: Any) -> Dict[str, Any]:
    post = _b_post(caption, format="image", asset_ids=[PIC_ID], idempotency_key=B_IMG_KEY)
    post[POST_IMAGE_KEY] = _pic()
    post.update(over)
    return post


UPLOAD_BLOB = "com.atproto.repo.uploadBlob"


def _blob_ok(cid: str = IMG_CID, size: int = len(IMG)) -> tuple:
    return 200, {"blob": {"$type": "blob", "ref": {"$link": cid}, "mimeType": "image/jpeg", "size": size}}


def test_bluesky_prepare_an_image_post_names_its_blob_before_any_upload(bsky_on):
    prepared = outlet_bluesky.ADAPTER.prepare(_b_img_post())
    record = prepared.payload["record"]
    assert record["text"] == B_IMG_CAPTION
    assert record["embed"] == {"$type": "app.bsky.embed.images", "images": [{
        "alt": ALT,
        "image": {"$type": "blob", "ref": {"$link": IMG_CID}, "mimeType": "image/jpeg", "size": len(IMG)},
        "aspectRatio": {"width": 1080, "height": 1350}}]}
    assert prepared.payload["image"] == {**_pic().meta(), "cid": IMG_CID}
    meta = prepared.publish_meta["bluesky"]
    assert meta["record"] == record and meta["image"] == prepared.payload["image"]
    assert prepared.payload["rkey"] == outlet_bluesky.tid_for(B_IMG_KEY, 0, RUN_DAY)
    assert prepared.reserve_micros == 0 and prepared.pre_send_charge is None
    assert f"cid={IMG_CID}" in prepared.summary and PIC_TITLE not in prepared.summary


def test_bluesky_image_retry_reuses_the_stored_record_byte_for_byte(bsky_on):
    first = outlet_bluesky.ADAPTER.prepare(_b_img_post())
    stored = {**first.publish_meta["bluesky"]}
    again = outlet_bluesky.ADAPTER.prepare({**_with_bsky_meta(_b_img_post(), **stored),
                                            POST_IMAGE_KEY: _pic()})
    assert again.payload["record"] == first.payload["record"] and again.payload["rkey"] == first.payload["rkey"]


@pytest.mark.parametrize("record_cid", ["bafkreiother", None])
def test_bluesky_image_retry_refuses_a_stored_record_naming_another_picture(bsky_on, record_cid):
    first = outlet_bluesky.ADAPTER.prepare(_b_img_post())
    stored = _copy.deepcopy(first.publish_meta["bluesky"])
    if record_cid is None:
        stored["record"].pop("embed")
    else:
        stored["record"]["embed"]["images"][0]["image"]["ref"]["$link"] = record_cid
    with pytest.raises(MarketingPublishRefused, match="another picture"):
        outlet_bluesky.ADAPTER.prepare({**_with_bsky_meta(_b_img_post(), **stored), POST_IMAGE_KEY: _pic()})


def test_bluesky_text_retry_refuses_a_stored_record_with_a_picture(bsky_on):
    first = outlet_bluesky.ADAPTER.prepare(_b_img_post())
    stored = _copy.deepcopy(first.publish_meta["bluesky"])
    with pytest.raises(MarketingPublishRefused, match="another picture"):
        outlet_bluesky.ADAPTER.prepare(_with_bsky_meta(_b_post(B_IMG_CAPTION), **stored))


@pytest.mark.parametrize("over, why", [
    ({POST_IMAGE_KEY: None}, "not resolved"),
    ({"asset_ids": []}, "exactly its one picture"),
    ({"asset_ids": ["other"]}, "exactly its one picture"),
    ({POST_IMAGE_KEY: _pic(sha256="zz")}, "no blob CID"),
    ({"caption": "a" * 301}, "characters"),
])
def test_bluesky_prepare_refuses_a_bad_image_post(bsky_on, over, why):
    with pytest.raises(MarketingPublishRefused, match=why):
        outlet_bluesky.ADAPTER.prepare(_b_img_post(**over))


async def _b_img_send(post: Optional[Dict[str, Any]] = None):
    post = post or _b_img_post()
    prepared = outlet_bluesky.ADAPTER.prepare(post)
    sent = {k: v for k, v in post.items() if k != POST_IMAGE_KEY}
    return prepared, await outlet_bluesky.ADAPTER.send(sent, prepared)


@pytest.mark.asyncio
async def test_bluesky_image_send_uploads_the_blob_then_puts_the_record(bsky, pics):
    rkey = outlet_bluesky.tid_for(B_IMG_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body())).add(UPLOAD_BLOB, _blob_ok()).add(PUT, _put_ok(rkey))
    prepared, outcome = await _b_img_send()
    assert outcome.kind == PUBLISHED and outcome.external_id == f"at://{DID}/{bluesky.POST_COLLECTION}/{rkey}"
    assert bsky.nsids() == [CREATE, UPLOAD_BLOB, PUT]
    (upload,) = bsky.of(UPLOAD_BLOB)
    assert str(upload.url).startswith(PDS) and upload.content == IMG
    assert upload.headers["content-type"] == "image/jpeg" and upload.headers["authorization"] == f"Bearer {ACCESS}"
    (put,) = bsky.of(PUT)
    assert json.loads(put.content)["record"] == prepared.payload["record"]
    assert outcome.publish_meta["bluesky"]["image"] == prepared.payload["image"]


@pytest.mark.asyncio
async def test_bluesky_image_send_refuses_a_cid_the_pds_disagrees_with(bsky, pics, caplog):
    bsky.add(CREATE, (200, _session_body())).add(UPLOAD_BLOB, _blob_ok(cid="bafkreiotherotherotherotherother"))
    _prepared, outcome = await _b_img_send()
    assert outcome.kind == REFUSED and outcome.category == "media" and outcome.alert == "failed"
    assert bsky.of(PUT) == []      # the record could never resolve its blob: never written


@pytest.mark.asyncio
async def test_bluesky_image_never_uploads_a_picture_that_is_not_the_one_reviewed(bsky, pics):
    pics.body = IMG[:-1] + b"\x00"
    _prepared, outcome = await _b_img_send()
    assert outcome.kind == REFUSED and outcome.category == "media" and outcome.alert == "failed"
    assert bsky.requests == []     # not even a login


@pytest.mark.asyncio
async def test_bluesky_image_download_failure_is_not_sent(bsky, pics):
    pics.status = 500
    _prepared, outcome = await _b_img_send()
    assert outcome.kind == NOT_SENT and outcome.category == "media" and bsky.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("answer, category, alert", [
    (_raise(httpx.ConnectError), "transport", None),
    (_raise(httpx.ReadTimeout), "media", None),
    ((502, {"error": "UpstreamFailure"}), "media", None),
    ((400, {"error": "BlobTooLarge", "message": "too big"}), "media", None),
    ((429, {"error": "RateLimitExceeded"}), "rate_limited", None),
    ((401, {"error": "AuthenticationRequired"}), "auth", "auth"),
    ((200, {"blob": {}}), "media", None),
])
async def test_bluesky_image_upload_failure_writes_no_record(bsky, pics, answer, category, alert):
    bsky.add(CREATE, (200, _session_body())).add(UPLOAD_BLOB, answer)
    _prepared, outcome = await _b_img_send()
    assert (outcome.kind, outcome.category, outcome.alert) == (NOT_SENT, category, alert)
    assert bsky.of(PUT) == []
    _no_secret(outcome.error)


@pytest.mark.asyncio
async def test_bluesky_image_upload_expired_token_renews_once_and_uploads_again(bsky, pics):
    rkey = outlet_bluesky.tid_for(B_IMG_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body()))
    bsky.add(UPLOAD_BLOB, (400, {"error": "ExpiredToken", "message": "Token has expired"}), _blob_ok())
    bsky.add(REFRESH_NSID, (200, _session_body(access=NEW_ACCESS, refresh=NEW_REFRESH)))
    bsky.add(PUT, _put_ok(rkey))
    _prepared, outcome = await _b_img_send()
    assert outcome.kind == PUBLISHED
    assert bsky.nsids() == [CREATE, UPLOAD_BLOB, REFRESH_NSID, UPLOAD_BLOB, PUT]
    assert bsky.of(UPLOAD_BLOB)[1].headers["authorization"] == f"Bearer {NEW_ACCESS}"


@pytest.mark.asyncio
async def test_bluesky_image_resend_after_absent_uploads_the_blob_again(bsky, pics):
    """An ambiguous put is reconciled; absent → the SAME record is resent, and the blob (which the PDS
    garbage-collects when no record names it) is uploaded again first."""
    rkey = outlet_bluesky.tid_for(B_IMG_KEY, 0, RUN_DAY)
    bsky.add(CREATE, (200, _session_body())).add(UPLOAD_BLOB, _blob_ok(), _blob_ok())
    bsky.add(PUT, (502, {"error": "UpstreamFailure"}), _put_ok(rkey))
    prepared, first = await _b_img_send()
    assert first.kind == AMBIGUOUS
    stored = {**prepared.publish_meta["bluesky"], **first.publish_meta["bluesky"]}
    retry = {**_with_bsky_meta(_b_img_post(), **stored), POST_IMAGE_KEY: _pic()}
    again, second = await _b_img_send(retry)
    assert again.payload["record"] == prepared.payload["record"] and second.kind == PUBLISHED
    assert bsky.nsids() == [CREATE, UPLOAD_BLOB, PUT, UPLOAD_BLOB, PUT]
    puts = [json.loads(r.content) for r in bsky.of(PUT)]
    assert puts[0] == puts[1]       # byte-identical record, same key: putRecord is idempotent


@pytest.mark.parametrize("setting", [None, "abc", float("inf")])
def test_x_image_reserve_with_an_unreadable_setting_is_the_url_price(monkeypatch, x_images, caplog, setting):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_IMAGE_POST_MICROS", setting)
    assert outlet_x.image_post_micros(False) == outlet_x.URL_POST_MICROS
    assert any("MARKETING_X_IMAGE_POST_MICROS is unreadable" in r.getMessage() for r in caplog.records)


# ── drop 2 (contract D14): X's "made with AI" flag is per post ───────────────────────────────────


@pytest.mark.parametrize("setting", [True, False])
@pytest.mark.parametrize("stamped, sent_if_on", [
    (True, True),             # an AI-written lesson, or a narrated template video
    (False, False),           # a template image/text post: written by a fixed template from public data
    ("missing", True),        # a row from before drop 2 carries no flag: it discloses, as before
    (None, True), (0, True), ("false", True), ("False", True), ([], True),   # never read as "no": discloses
])
def test_x_made_with_ai_is_the_setting_and_the_posts_own_flag(monkeypatch, x_on, setting, stamped, sent_if_on):
    """`made_with_ai` = MARKETING_X_MADE_WITH_AI AND the post's `metadata.made_with_ai` (stamped by
    create_posts with `post_copy.made_with_ai`). Only an explicit False turns the flag off — an absent or
    unreadable value is a disclosure, never a silent "not AI"."""
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_MADE_WITH_AI", setting)
    metadata = {} if stamped == "missing" else {"made_with_ai": stamped}
    prepared = outlet_x.ADAPTER.prepare(_x_post(metadata=metadata))
    want = setting and sent_if_on
    assert prepared.payload == {"text": X_CAPTION, "made_with_ai": want}
    assert prepared.publish_meta["x"]["made_with_ai"] is want
    assert f"made_with_ai={want}" in prepared.summary


def test_x_made_with_ai_tolerates_a_post_with_no_metadata_object(monkeypatch, x_on):
    monkeypatch.setattr(outlet_x.settings, "MARKETING_X_MADE_WITH_AI", True)
    for metadata in (None, "x", ["made_with_ai", False]):
        assert outlet_x.ADAPTER.prepare(_x_post(metadata=metadata)).payload["made_with_ai"] is True
