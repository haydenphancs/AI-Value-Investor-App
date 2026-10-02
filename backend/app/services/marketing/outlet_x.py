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
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.config import settings
from app.integrations import x_api
from app.services.marketing import post_copy, tlds
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
    Outcome,
    Prepared,
    ReconcileResult,
    RetractResult,
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


def budget_micros() -> int:
    """`MARKETING_X_MONTHLY_BUDGET_USD` in micro-dollars; 0 (X off) when unset, non-finite or ≤ 0."""
    try:
        usd = float(settings.MARKETING_X_MONTHLY_BUDGET_USD or 0)
    except (TypeError, ValueError):
        return 0
    if usd != usd or usd in (float("inf"), float("-inf")) or usd <= 0:
        return 0
    return int(round(usd * 1_000_000))


def metrics_headroom_micros() -> int:
    """The spend a metrics read must leave under the monthly cap beyond its own reserve:
    `METRICS_HEADROOM_POSTS` posts at the price a post would reserve right now — $0.20 each while
    MARKETING_X_ALLOW_URLS is on (read at CALL time, like `prepare`: the switch may change between
    reads), else $0.015."""
    per_post = URL_POST_MICROS if settings.MARKETING_X_ALLOW_URLS else POST_MICROS
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
        if post.get("format") != "text":
            raise MarketingPublishRefused(f"x: format {post.get('format')!r} is not published on X (text only)")
        if post.get("asset_ids"):
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
        payload: Dict[str, Any] = {"text": text, "made_with_ai": bool(settings.MARKETING_X_MADE_WITH_AI)}
        sha = text_sha256(text)
        reserve = URL_POST_MICROS if links else POST_MICROS
        return Prepared(
            payload=payload, text_sha256=sha, reserve_micros=reserve,
            publish_meta={"x": {"weighted_len": weighted, "links": len(links),
                                "made_with_ai": payload["made_with_ai"]}},
            summary=(f"x text weighted_len={weighted} links={len(links)} made_with_ai={payload['made_with_ai']} "
                     f"cost_micros={reserve} sha256={sha[:12]}"),
        )

    async def send(self, post: Dict[str, Any], prepared: Prepared) -> Outcome:
        try:
            res = await x_api.create_post(prepared.payload["text"],
                                          made_with_ai=bool(prepared.payload.get("made_with_ai")))
        except x_api.XApiNotSentError as e:
            return Outcome(NOT_SENT, "transport", error=scrub(e))
        except x_api.XApiRateLimitError as e:
            return Outcome(NOT_SENT, "rate_limited", error=scrub(e), retry_at=getattr(e, "retry_at", None))
        except x_api.XApiDuplicateContentError as e:
            # NOT proof either way (verified 2026-09-30): reconcile reads the timeline.
            return Outcome(AMBIGUOUS, "duplicate", error=scrub(e))
        except x_api.XApiAmbiguousError as e:
            return Outcome(AMBIGUOUS, "server", error=scrub(e))
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
            return Outcome(AMBIGUOUS, "server", error=scrub(e))
        post_id = str(res.get("id") or "")
        if not post_id:
            return Outcome(AMBIGUOUS, "server", error="x: create answered without a post id")
        return Outcome(PUBLISHED, external_id=post_id, external_url=post_url_for(post_id),
                       published_at=datetime.now(timezone.utc).isoformat())

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
        for item in posts:
            if isinstance(item, dict) and match_key(item.get("text")) == wanted and item.get("id"):
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
