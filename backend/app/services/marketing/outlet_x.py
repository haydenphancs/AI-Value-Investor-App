"""
X (formerly Twitter) adapter for the marketing publisher (design doc §12.10, rules/marketing.md §1-§2).

Text posts only, link-free, to the brand account that owns the X developer app (OAuth 1.0a user
context — `app/integrations/x_api.py`). What makes X different from every other outlet:

* **There is no idempotency key** on POST /2/tweets, and a "duplicate content" 403 proves nothing
  either way (verified 2026-09-30). So an outcome that is not certain is NEVER retried: the post
  stays `queued`, reconcile reads our own timeline (owned reads, $0.001 per post returned) for the
  exact text, and if it still cannot tell, the OWNER decides in Telegram ("It's live" / "Not
  posted"). There is no automatic resend of an ambiguous X post, ever.
* **Every call costs money**, refused ones included (X bills a refused PostCreate). The publisher
  charges each attempt at the claim and keeps a monthly cap in our own ledger
  (`MARKETING_X_MONTHLY_BUDGET_USD`); X's console cap has failed to hold for others.
* **A URL makes a post cost $0.20 instead of $0.015.** The caption is composed link-free
  (`post_copy.cta_for`), and this adapter refuses any link-shaped token — the SAME definition the
  length counter uses (`post_copy.x_link_tokens`) — unless `MARKETING_X_ALLOW_URLS`, read here at
  publish time (it may have changed since the copy was written).
* **A generic 403 is never retried.** New pay-per-use apps often get "You are not permitted to
  perform this action" on every post (X anti-spam); X staff ask apps not to retry it.
* At most one cashtag per post on pay-per-use (a second is a billed 403), and unsolicited
  @mentions are blocked for API posts — both refused here, before any spend.

IMAGE posts (drop 1, contract C9), only while MARKETING_X_IMAGES is on (read here at publish time —
a run that froze "image" for X while it was on is REFUSED if it was turned off since):
* The send downloads the run's post image from its public URL (byte cap, sha256 checked against the
  asset row), uploads it (POST /2/media/upload), sets its alt text — the image's title and whole
  paragraphs (POST /2/media/metadata) — and posts the same caption with the media id. The upload and
  the alt text are not a post: ANY failure before the create is NOT_SENT (the post cannot exist), and
  every resend uploads again (a media id expires; uploads are unpriced).
* Money: the claim reserves the post at MARKETING_X_IMAGE_POST_MICROS (`image_post_micros`, never
  below the text or URL price) as `x_create`; the alt text is journaled `x_media_alt` ($0.005) by
  the publisher AFTER the claim and BEFORE the send (`Prepared.pre_send_charge`). Each NOT_SENT /
  credits-depleted refusal refunds exactly what provably was not billed (`Outcome.refund_micros`).
* Reconcile: X stores a post with media with the media's t.co link appended, so an image post's
  timeline copy is matched with that trailing link removed (and as stored, in case X changes).
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union

from app.config import settings
from app.integrations import x_api
from app.services.marketing import outlet_base, post_copy, tlds
from app.services.marketing.outlet_base import (
    ABSENT,
    AMBIGUOUS,
    AUTH_BACKOFF_SECONDS,
    FOUND,
    GAVE_UP,
    NOT_SENT,
    PUBLISHED,
    REFUSED,
    RETRACTED,
    RETRY,
    UNKNOWN,
    Adapter,
    MarketingPublishRefused,
    MediaProblem,
    Outcome,
    Prepared,
    ReconcileResult,
    RetractResult,
    image_of,
    scrub,
    text_sha256,
)

logger = logging.getLogger(__name__)

PLATFORM = "x"
#: X pay-per-use prices (docs.x.com pricing + changelog, effective 2026-04-20; verified 2026-09-30).
POST_MICROS = 15_000            # POST /2/tweets, text only
URL_POST_MICROS = 200_000       # a post whose text carries any URL
OWNED_READ_MICROS = 1_000       # per post returned by GET /2/users/{me}/tweets with our user token
DELETE_MICROS = 10_000          # bucket undocumented ($0.005 or $0.010) — budget the larger
#: POST /2/media/metadata (an image's alt text): "Media Metadata $0.005 per request" — the pricing page
#: maps no row to an endpoint, so this mapping is an inference (research 2026-10-09). A media upload
#: has no price line.
MEDIA_ALT_MICROS = 5_000
MEDIA_ALT_OP = "x_media_alt"
RECONCILE_MAX_RESULTS = 5       # the endpoint's minimum page size
#: Look back this far before the send's start when reading our timeline (X documents no
#: read-after-write ordering; created_at can differ from our clock).
RECONCILE_LOOKBACK = timedelta(minutes=10)

#: The measure step (`metrics_service`, design doc §12.11). GET /2/users/me is not an owned read:
#: X bills it as a User read.
USER_READ_MICROS = 10_000
#: An X post is read when it has crossed one of these ages (days after it went out) that is not yet
#: measured — once, for the largest one crossed.
METRICS_CHECKPOINT_DAYS = (1, 3, 7, 28)
#: The timeline window read around the post's publish time, either side.
METRICS_WINDOW = timedelta(minutes=10)
#: A metrics read starts only while the month's spend leaves the read's reserve PLUS this many posts
#: (a week of posting days) under the cap — posting always wins. Priced by `metrics_headroom_micros()`
#: at CALL time (a fixed text-post figure left room for less than one $0.20 URL post — review 2026-10-01).
METRICS_HEADROOM_POSTS = 4
#: Charged before one metrics read (a page of at most 5 posts), corrected afterwards to the count
#: X returned.
METRICS_READ_RESERVE_MICROS = 5 * OWNED_READ_MICROS

_CASHTAG_RE = re.compile(r"(?<![\w$])\$[A-Za-z]{1,10}(?:[._][A-Za-z]{1,4})?\b")
#: X's parser also takes the full-width ＠ (U+FF20) as a mention sign.
_MENTION_RE = re.compile(r"(?<![\w@＠])[@＠][A-Za-z0-9_]{1,15}\b")
_WS_RE = re.compile(r"\s+")
#: A scheme or `www.` in ANY case — X autolinks both.
_SCHEME_RE = re.compile(r"(?i)(?:https?://|\bwww\.)\S+")
#: `<label>.<Tail>` glued by a missing space ("fell.Today"): X autolinks it whenever the tail, in
#: ANY case, is a delegated TLD ("today", "now", "world", "markets" … are). Decided by what is KNOWN
#: (`tlds.NON_TLD_TAILS`), never by a short list of TLDs — over-refusing costs one refused post the
#: owner sees; under-counting costs $0.185 per post on X's bill.
_GLUED_RE = re.compile(r"(?<![\w.@＠/-])([A-Za-z0-9][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)*)\.([A-Za-z]{2,24})(?![A-Za-z0-9-])")
_ANY_URL_RE = re.compile(r"(?i)https?://\S+")
_LINK_PLACEHOLDER = "\u0000link\u0000"
#: The media link X appends to a post with media: ONE t.co URL at the very end of the stored text.
_TRAILING_MEDIA_LINK_RE = re.compile(r"\s*https?://t\.co/[A-Za-z0-9]{1,40}\s*$")


def budget_micros() -> int:
    """`MARKETING_X_MONTHLY_BUDGET_USD` in micro-dollars; 0 (X off) when unset, non-finite or ≤ 0."""
    try:
        usd = float(settings.MARKETING_X_MONTHLY_BUDGET_USD or 0)
    except (TypeError, ValueError):
        return 0
    if usd != usd or usd in (float("inf"), float("-inf")) or usd <= 0:
        return 0
    micros = usd * 1_000_000
    if micros in (float("inf"), float("-inf")):   # a finite but absurd budget (≥ ~1.8e302 USD)
        return 0                                   # overflows to inf; round(inf) would RAISE
    return int(round(micros))


def image_post_micros(links: bool) -> int:
    """What the claim reserves for one X IMAGE post: MARKETING_X_IMAGE_POST_MICROS (read at call time —
    the real price is confirmed by the one live test post, OWNER_TASKS), never below the text price,
    and never below the URL price when the caption carries a link. An unreadable setting reads as the
    URL price (the largest documented)."""
    try:
        configured = int(settings.MARKETING_X_IMAGE_POST_MICROS)
    except (TypeError, ValueError, OverflowError):
        logger.warning("marketing x: MARKETING_X_IMAGE_POST_MICROS is unreadable — an image post is reserved at "
                       "the URL price (%d micros)", URL_POST_MICROS)
        configured = URL_POST_MICROS
    return max(configured, URL_POST_MICROS if links else POST_MICROS)


def metrics_headroom_micros() -> int:
    """The spend a metrics read must leave under the monthly cap beyond its own reserve:
    `METRICS_HEADROOM_POSTS` posts at the price a post would reserve right now — $0.20 each while
    MARKETING_X_ALLOW_URLS is on (read at CALL time, like `prepare`: the switch may change between
    reads), else $0.015; while MARKETING_X_IMAGES is on, an image post's price instead when it is
    higher (`image_post_micros` plus its $0.005 alt text — $0.205 at the default setting).

    Deliberately keyed on MARKETING_X_IMAGES ALONE, not also on MARKETING_IMAGE_POSTS (review money:F1,
    kept as a fail-safe choice): with IMAGE_POSTS off and X_IMAGES on, NEW runs freeze X as "text"
    (`script_service.freeze_post_formats` needs both switches), yet a post frozen as "image" before
    IMAGE_POSTS was switched off is still published by `prepare` (which checks X_IMAGES only) until it
    expires — its run day or the next, ET (`run_service.is_fresh`). Pricing the headroom at the text
    price then would let metrics reads spend what that pending $0.205 post needs. The cost of the
    choice: while that switch combination lasts, every read reserves the image headroom ($0.82 instead
    of $0.06 for 4 text posts), so X metrics reads record `capped` about $0.76 earlier in the month;
    a `capped` record writes no checkpoint, so the post stays due and is read on a later day the
    headroom allows (a checkpoint is lost only if a later one is crossed first, or the post leaves the
    30-day listing). It never spends more — it only defers measurement. Fix the switch pair (turn
    X_IMAGES off with IMAGE_POSTS) rather than narrowing this check."""
    urls = bool(settings.MARKETING_X_ALLOW_URLS)
    per_post = URL_POST_MICROS if urls else POST_MICROS
    # X_IMAGES alone, on purpose (see the docstring): an image post frozen before MARKETING_IMAGE_POSTS
    # was switched off may still be pending, so over-reserve rather than starve its publish.
    if settings.MARKETING_X_IMAGES:
        per_post = max(per_post, image_post_micros(urls) + MEDIA_ALT_MICROS)
    return METRICS_HEADROOM_POSTS * per_post


def _billed_reads(result_count: Any, returned: int) -> int:
    """The posts X bills for one reconcile read (owned reads cost per post RETURNED): the posts it
    returned, or its own `result_count` when that is larger — but never more than the page we asked
    for. A count that is not a non-negative int (absent, a bool, a string, a float, negative) is
    unreadable and reads as 0, so the posts returned decide; a corrupt count (10**9) can no longer
    journal a charge that caps X for the rest of the month. Never raises."""
    count = result_count if isinstance(result_count, int) and not isinstance(result_count, bool) else 0
    return max(returned, min(max(count, 0), RECONCILE_MAX_RESULTS))


def normalize_text(text: Any) -> str:
    """What two copies of a post have in common once X has stored one: HTML entities decoded
    (X returns `&amp;`), NFC, whitespace runs collapsed, ends stripped."""
    s = html.unescape(str(text or ""))
    s = unicodedata.normalize("NFC", s)
    return _WS_RE.sub(" ", s).strip()


def link_suspects(text: str) -> list:
    """Every span X may turn into a link — and bill at $0.20 — in `text`: what the length counter
    counts as a link (`post_copy.x_link_tokens`), plus any scheme or `www.` in any case, plus any
    glued `<label>.<tail>` whose tail is not KNOWN to be a non-TLD. A superset on purpose."""
    found = list(post_copy.x_link_tokens(text))
    found += [m.group(0) for m in _SCHEME_RE.finditer(text) if m.group(0) not in found]
    for m in _GLUED_RE.finditer(text):
        if not tlds.is_non_tld_tail(m.group(2)) and m.group(0) not in found and \
                not any(m.group(0) in f for f in found):
            found.append(m.group(0))
    return found


def match_key(text: Any) -> str:
    """The text two copies of a post share once X has stored one: `normalize_text`, with every link
    (X stores a t.co URL in place of each link it detected) reduced to one placeholder."""
    s = normalize_text(text)
    for token in sorted(set(link_suspects(s)), key=len, reverse=True):
        s = s.replace(token, _LINK_PLACEHOLDER)
    return _ANY_URL_RE.sub(_LINK_PLACEHOLDER, s)


def stored_match_keys(text: Any, *, image: bool) -> set:
    """The `match_key`s X's stored copy of one of our posts may equal: the text as stored and — for an
    IMAGE post, which X stores with the media's t.co link appended — the text with that ONE trailing
    t.co link removed (both, so a caption ending in its own link, or an X that stops appending, still
    matches)."""
    keys = {match_key(text)}
    if image:
        keys.add(match_key(_TRAILING_MEDIA_LINK_RE.sub("", normalize_text(text), count=1)))
    return keys


def post_url_for(external_id: Any) -> Optional[str]:
    """The public URL of a post. The `/i/web/status/` form needs no handle (none is configured)."""
    return f"https://x.com/i/web/status/{external_id}" if external_id else None


def _publish_meta(post: Dict[str, Any]) -> Dict[str, Any]:
    meta = post.get("metadata") if isinstance(post.get("metadata"), dict) else {}
    pub = meta.get("publish")
    return pub if isinstance(pub, dict) else {}


class XAdapter(Adapter):
    platform = PLATFORM
    retractable = True
    resend_safe = False
    #: Seconds after the send's start: four timeline reads, then the owner decides.
    reconcile_schedule = (600, 1800, 5400, 14400)
    reconcile_reserve_micros = RECONCILE_MAX_RESULTS * OWNED_READ_MICROS
    retract_cost_micros = DELETE_MICROS

    def configured(self) -> bool:
        return x_api.configured() and budget_micros() > 0

    def configured_for_retract(self) -> bool:
        # A delete is wanted even after the budget was lowered to 0 to stop new posts.
        return x_api.configured()

    def prepare(self, post: Dict[str, Any]) -> Prepared:
        text = post.get("caption") if isinstance(post.get("caption"), str) else ""
        if not text.strip():
            raise MarketingPublishRefused("x: the post has no text")
        image = None
        if post.get("format") == "image":
            if not settings.MARKETING_X_IMAGES:
                raise MarketingPublishRefused(
                    "x: an image post while MARKETING_X_IMAGES is off — X publishes text only", category="media")
            image = image_of(post)
            if image is None:
                raise MarketingPublishRefused("x: the image post's picture was not resolved", category="media")
            if [str(a) for a in post.get("asset_ids") or [] if a] != [image.asset_id]:
                raise MarketingPublishRefused("x: an image post carries exactly its one picture", category="media")
        elif post.get("format") != "text":
            raise MarketingPublishRefused(
                f"x: format {post.get('format')!r} is not published on X (text, or image while MARKETING_X_IMAGES)")
        elif post.get("asset_ids"):
            # The owner reviewed a text post; media that would not be sent must not ride along.
            raise MarketingPublishRefused("x: a text post may not carry media")
        links = link_suspects(text)
        if links and not settings.MARKETING_X_ALLOW_URLS:
            raise MarketingPublishRefused(
                f"x: the caption carries {len(links)} link(s) and MARKETING_X_ALLOW_URLS is off "
                "(a post with a URL costs $0.20 instead of $0.015)")
        cashtags = {m.group(0).upper() for m in _CASHTAG_RE.finditer(text)}
        if len(cashtags) >= 2:
            raise MarketingPublishRefused(
                f"x: {len(cashtags)} cashtags — X refuses (and bills) more than one per API post")
        if _MENTION_RE.search(text):
            raise MarketingPublishRefused("x: an @mention — X blocks unsolicited mentions from API posts")
        weighted = post_copy.x_weighted_length(text)
        if weighted > post_copy.LIMITS["x"]:
            raise MarketingPublishRefused(f"x: {weighted} weighted characters > {post_copy.LIMITS['x']}")
        # The post's own AI flag (drop 2: create_posts stamps `metadata.made_with_ai` — False only on a
        # template image/text post, written by a fixed template from public data) ANDed with the switch.
        # Anything but an explicit False (absent, a pre-drop-2 row, a hand-edited value) discloses.
        post_md = post.get("metadata") if isinstance(post.get("metadata"), dict) else {}
        payload: Dict[str, Any] = {"text": text, "made_with_ai": bool(settings.MARKETING_X_MADE_WITH_AI)
                                   and post_md.get("made_with_ai", True) is not False}
        sha = text_sha256(text)
        meta: Dict[str, Any] = {"weighted_len": weighted, "links": len(links), "made_with_ai": payload["made_with_ai"]}
        if image is None:
            reserve = URL_POST_MICROS if links else POST_MICROS
            return Prepared(
                payload=payload, text_sha256=sha, reserve_micros=reserve, publish_meta={"x": meta},
                summary=(f"x text weighted_len={weighted} links={len(links)} made_with_ai={payload['made_with_ai']} "
                         f"cost_micros={reserve} sha256={sha[:12]}"),
            )
        reserve = image_post_micros(bool(links))
        payload["image"] = {**image.meta(), "alt": image.alt(x_api.ALT_TEXT_MAX)}
        meta.update({"image": image.meta(), "alt_micros": MEDIA_ALT_MICROS})
        return Prepared(
            payload=payload, text_sha256=sha, reserve_micros=reserve, publish_meta={"x": meta},
            pre_send_charge=(MEDIA_ALT_OP, MEDIA_ALT_MICROS),
            summary=(f"x image weighted_len={weighted} links={len(links)} made_with_ai={payload['made_with_ai']} "
                     f"asset={image.asset_id} bytes={image.size} cost_micros={reserve}+{MEDIA_ALT_MICROS} "
                     f"sha256={sha[:12]}"),
        )

    async def _upload_image(self, prepared: Prepared) -> Union[str, Outcome]:
        """The image's media id on X — downloaded from its public URL and checked, uploaded, its alt
        text set — or the Outcome that ends this attempt. Nothing here creates a post, so every
        failure is NOT_SENT (or a definite refusal of the picture / the account), and each refunds
        exactly what provably was not billed: before the alt-text call nothing was (an upload has no
        price), after it the alt text may have been."""
        image = prepared.payload["image"]
        post_only = -int(prepared.reserve_micros)               # the create was never made
        nothing_billed = post_only - MEDIA_ALT_MICROS           # …nor the alt text
        data = await outlet_base.fetch_post_image(image["url"], size=image["size"], sha256=image["sha256"])
        if isinstance(data, MediaProblem):
            if data.definite:
                return Outcome(REFUSED, "media", error=scrub(f"x: {data.error}"), refund_micros=nothing_billed,
                               alert="failed")
            return Outcome(NOT_SENT, "media", error=scrub(f"x: {data.error}"), refund_micros=nothing_billed)
        try:
            uploaded = await x_api.upload_media(data)
        except x_api.XApiNotSentError as e:
            return Outcome(NOT_SENT, "transport", error=scrub(e), refund_micros=nothing_billed)
        except x_api.XApiRateLimitError as e:
            return Outcome(NOT_SENT, "rate_limited", error=scrub(e), retry_at=getattr(e, "retry_at", None),
                           refund_micros=nothing_billed)
        except (x_api.XApiAuthError, x_api.XApiNotConfiguredError) as e:
            return Outcome(NOT_SENT, "auth", error=scrub(e), refund_micros=nothing_billed, alert="auth",
                           retry_at=datetime.now(timezone.utc) + timedelta(seconds=AUTH_BACKOFF_SECONDS))
        except x_api.XApiCreditsDepletedError as e:
            return Outcome(REFUSED, "credits", error=scrub(e), refund_micros=nothing_billed, alert="failed")
        except x_api.XApiException as e:
            # Refused, forbidden, ambiguous: the media may or may not exist on X, the POST cannot.
            return Outcome(NOT_SENT, "media", error=scrub(e), refund_micros=nothing_billed)
        if uploaded.get("state") in ("pending", "in_progress"):
            return Outcome(NOT_SENT, "media", refund_micros=nothing_billed,
                           error=f"x: media {uploaded.get('id')} is still processing ({uploaded.get('state')})")
        media_id = str(uploaded["id"])
        try:
            await x_api.create_media_metadata(media_id, alt_text=image["alt"])
        except (x_api.XApiNotSentError, x_api.XApiNotConfiguredError) as e:
            category = "transport" if isinstance(e, x_api.XApiNotSentError) else "auth"
            return Outcome(NOT_SENT, category, error=scrub(e), refund_micros=nothing_billed,
                           alert="auth" if category == "auth" else None)
        except x_api.XApiRateLimitError as e:
            return Outcome(NOT_SENT, "rate_limited", error=scrub(e), retry_at=getattr(e, "retry_at", None),
                           refund_micros=post_only)
        except x_api.XApiAuthError as e:
            return Outcome(NOT_SENT, "auth", error=scrub(e), refund_micros=post_only, alert="auth",
                           retry_at=datetime.now(timezone.utc) + timedelta(seconds=AUTH_BACKOFF_SECONDS))
        except x_api.XApiCreditsDepletedError as e:
            return Outcome(REFUSED, "credits", error=scrub(e), refund_micros=nothing_billed, alert="failed")
        except x_api.XApiException as e:
            # The alt-text call may have been billed (an ambiguous or refused write) — kept.
            return Outcome(NOT_SENT, "media", error=scrub(e), refund_micros=post_only)
        return media_id

    async def send(self, post: Dict[str, Any], prepared: Prepared) -> Outcome:
        image = prepared.payload.get("image")
        media_ids = None
        if image:
            uploaded = await self._upload_image(prepared)
            if isinstance(uploaded, Outcome):
                return uploaded
            media_ids = [uploaded]
        # The media id rides on every outcome that may be live (merged with the claim's `x` record —
        # `metadata.publish` merges one level deep).
        media_meta = {"x": {**prepared.publish_meta.get("x", {}), "media_id": media_ids[0]}} if media_ids else {}
        try:
            res = await x_api.create_post(prepared.payload["text"],
                                          made_with_ai=bool(prepared.payload.get("made_with_ai")),
                                          media_ids=media_ids)
        except x_api.XApiNotSentError as e:
            # Never left: X did not bill the create (an image post's alt text, before it, may be).
            return Outcome(NOT_SENT, "transport", error=scrub(e),
                           refund_micros=-int(prepared.reserve_micros) if image else 0)
        except x_api.XApiRateLimitError as e:
            return Outcome(NOT_SENT, "rate_limited", error=scrub(e), retry_at=getattr(e, "retry_at", None))
        except x_api.XApiDuplicateContentError as e:
            # NOT proof either way (verified 2026-09-30): reconcile reads the timeline.
            return Outcome(AMBIGUOUS, "duplicate", error=scrub(e), publish_meta=media_meta)
        except x_api.XApiAmbiguousError as e:
            return Outcome(AMBIGUOUS, "server", error=scrub(e), publish_meta=media_meta)
        except x_api.XApiAuthError as e:
            # The request was refused before anything was created — but the owner must fix the
            # credential first, so wait an hour and say so.
            return Outcome(NOT_SENT, "auth", error=scrub(e),
                           retry_at=datetime.now(timezone.utc) + timedelta(seconds=AUTH_BACKOFF_SECONDS),
                           alert="auth")
        except x_api.XApiCreditsDepletedError as e:
            # Not billed: refund exactly what the claim reserved (a URL post reserved $0.20).
            return Outcome(REFUSED, "credits", error=scrub(e), refund_micros=-int(prepared.reserve_micros),
                           alert="failed")
        except x_api.XApiForbiddenError as e:
            return Outcome(REFUSED, "forbidden", error=scrub(e), alert="failed")
        except x_api.XApiRefusedError as e:
            return Outcome(REFUSED, "invalid", error=scrub(e), alert="failed")
        except x_api.XApiNotConfiguredError as e:
            return Outcome(NOT_SENT, "auth", error=scrub(e),
                           retry_at=datetime.now(timezone.utc) + timedelta(seconds=AUTH_BACKOFF_SECONDS),
                           alert="auth")
        except x_api.XApiException as e:
            return Outcome(AMBIGUOUS, "server", error=scrub(e), publish_meta=media_meta)
        post_id = str(res.get("id") or "")
        if not post_id:
            return Outcome(AMBIGUOUS, "server", error="x: create answered without a post id", publish_meta=media_meta)
        return Outcome(PUBLISHED, external_id=post_id, external_url=post_url_for(post_id),
                       published_at=datetime.now(timezone.utc).isoformat(), publish_meta=media_meta)

    async def reconcile(self, post: Dict[str, Any]) -> ReconcileResult:
        user_id = x_api.user_id_from_access_token()
        if not user_id:
            return ReconcileResult(UNKNOWN, error="x: no user id in MARKETING_X_ACCESS_TOKEN; the owner must decide")
        pub = _publish_meta(post)
        started = _parse(pub.get("started_at")) or _parse(post.get("claimed_at")) or datetime.now(timezone.utc)
        try:
            res = await x_api.list_user_posts(user_id, start_time=started - RECONCILE_LOOKBACK,
                                              max_results=RECONCILE_MAX_RESULTS)
        except x_api.XApiException as e:
            # Not-sent reads cost nothing; anything else may have been billed — count the worst case.
            cost = 0 if isinstance(e, (x_api.XApiNotSentError, x_api.XApiNotConfiguredError)) \
                else self.reconcile_reserve_micros
            return ReconcileResult(UNKNOWN, error=scrub(e), cost_micros=cost)
        posts = res.get("posts") or []
        cost = _billed_reads(res.get("result_count"), len(posts)) * OWNED_READ_MICROS
        wanted = match_key(post.get("caption"))
        image = post.get("format") == "image"
        for item in posts:
            if isinstance(item, dict) and wanted in stored_match_keys(item.get("text"), image=image) and item.get("id"):
                pid = str(item["id"])
                return ReconcileResult(FOUND, external_id=pid, external_url=post_url_for(pid),
                                       published_at=str(item.get("created_at") or "") or None, cost_micros=cost)
        return ReconcileResult(ABSENT, resend_safe=False, cost_micros=cost)

    async def retract(self, post: Dict[str, Any]) -> RetractResult:
        external_id = post.get("external_id")
        if not external_id:
            return RetractResult(GAVE_UP, error="x: the post id is unknown — remove it by hand")
        try:
            await x_api.delete_post(str(external_id))
        except (x_api.XApiNotSentError, x_api.XApiNotConfiguredError) as e:
            return RetractResult(RETRY, error=scrub(e), cost_micros=0)
        except (x_api.XApiAmbiguousError, x_api.XApiRateLimitError) as e:
            return RetractResult(RETRY, error=scrub(e), cost_micros=DELETE_MICROS)
        except x_api.XApiAuthError as e:
            # A credential problem the owner can fix — keep trying (bounded by the retract cap).
            return RetractResult(RETRY, error=scrub(e), cost_micros=0)
        except x_api.XApiRefusedError as e:
            return RetractResult(GAVE_UP, error=scrub(e), cost_micros=DELETE_MICROS)
        except x_api.XApiException as e:
            return RetractResult(RETRY, error=scrub(e), cost_micros=DELETE_MICROS)
        return RetractResult(RETRACTED, cost_micros=DELETE_MICROS)

    def post_url(self, post: Dict[str, Any]) -> Optional[str]:
        return post.get("external_url") or post_url_for(post.get("external_id"))


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


ADAPTER = XAdapter()
