"""
The public smart link `GET /go/{campaign}` and the landing page's App Store slots
(SYSTEM_DESIGN_GUIDELINES §12.6, rules marketing.md).

Every public post's call to action points at `caydexinvest.com/go/<platform>` (`post_copy.py`).
This module decides where that link goes and counts who followed it, and nothing else:

* **Destination.** Pre-launch there is no App Store page (the app id 404s until approval), so
  the link lands on the static landing page at `/` — a RELATIVE `Location`, so no host is ever
  built from request data. Post-launch it is `MARKETING_APP_STORE_URL`, accepted only as
  `https://apps.apple.com/...`; with `MARKETING_APP_STORE_PROVIDER_TOKEN` set it carries App
  Analytics campaign parameters (`pt`, `ct`, `mt=8`). A misconfigured URL logs ERROR once and
  falls back to `/` — a typo in a Railway variable must never turn the brand's link into an
  open redirect or a 500.
* **Campaign.** Only a member of `POST_PLATFORMS` survives; everything else is `"other"`.
  `ct` is always one of those CONSTANTS, never request text, so nothing the caller typed is
  ever reflected into a header, a query string or a log line.
* **Counting.** Link-preview crawlers (every platform fetches the link once per post), native
  HTTP stacks, HEAD probes, browser prefetches, anything the browser itself labels as NOT a
  top-level navigation (an `<img>`, a `fetch`, an iframe — `Sec-Fetch-Mode`/`Sec-Fetch-Dest`),
  bursts from one address and anything past its CAMPAIGN's per-minute ceiling are not counted.
  They all still get the 302 — only the count skips them. The count is an in-process dict
  flushed by a lifespan loop through `increment_marketing_link_hits` (migration 173), so the
  request path does no I/O.
* **The number is INDICATIVE, not exact.** A lost minute on a crash under-counts (a clean
  shutdown logs what its final flush could not send as "LOST at shutdown"). A flush whose RPC
  committed but whose response was lost (a read timeout, a gateway 502/503/504/520/524) cannot
  be told from one that never landed, so those hits are re-sent ONCE: delivery is AT-LEAST-ONCE
  and AT MOST TWICE per hit — a second unknown outcome drops them rather than risk a third
  count (both logged as "UNKNOWN OUTCOME"; a dropped hit counts zero, one or two times). A
  failure that PROVES nothing reached the database — a refused connection, a SQLSTATE, a
  gateway 4xx other than 408/499, an edge that never reached the origin (521/522/523/525/526/
  530) — is not an unknown outcome: it is re-sent every flush until it lands. A scripted
  client with an address pool and forged headers can still inflate ONE campaign up to that
  campaign's ceiling, never suppress another's. App Store Connect's `ct` campaign data is the
  attribution source of record.

FMP-free by construction: public marketing surfaces may never touch licensed market data
(rules marketing.md §1). Imports are limited to config, the Supabase client, the in-memory
rate limiter class and small utilities.
"""

from __future__ import annotations

import asyncio
import html
import ipaddress
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.config import settings
from app.core.security import RateLimiter, trusted_client_ip
from app.database import get_supabase
from app.schemas.marketing import POST_PLATFORMS
from app.utils.market_hours import ET
from app.utils.supabase_async import sb_exec
from app.utils.supabase_errors import _status_from_code

logger = logging.getLogger(__name__)

# ── campaign ──────────────────────────────────────────────────────────────────

KNOWN_CAMPAIGNS = frozenset(POST_PLATFORMS)
OTHER = "other"
_CAMPAIGN_RE = re.compile(r"[a-z0-9_-]{1,40}")
#: Anything longer cannot be a campaign; refuse before lower()/strip() walk it.
_RAW_CAMPAIGN_CAP = 64


def normalize_campaign(raw: Any) -> str:
    """A member of KNOWN_CAMPAIGNS, or "other". Never the caller's text in any other form.

    ASCII-only before lowercasing: `str.lower()` folds a few non-ASCII letters INTO ASCII
    (KELVIN SIGN → "k"), and a campaign should never be reachable through a lookalike.
    """
    if not isinstance(raw, str) or len(raw) > _RAW_CAMPAIGN_CAP or not raw.isascii():
        return OTHER
    slug = raw.strip().lower()
    if not _CAMPAIGN_RE.fullmatch(slug) or slug not in KNOWN_CAMPAIGNS:
        return OTHER
    return slug


# ── destination ───────────────────────────────────────────────────────────────

_STORE_HOST = "apps.apple.com"
#: Characters that can never appear in a URL we are willing to put in a `Location` header or
#: an `href`: whitespace/controls (header splitting), quotes and angle brackets (markup),
#: backslash (browsers read it as `/`, which reshapes the host).
_UNSAFE_URL_CHARS = frozenset(" \t\r\n\"'<>\\`")
_APP_ID_RE = re.compile(r"/id([0-9]{1,20})(?![0-9])")
_misconfig_logged = False


def _log_misconfig(reason: str) -> None:
    global _misconfig_logged
    if not _misconfig_logged:
        _misconfig_logged = True
        # The value itself is not logged: it is operator config, but a mangled paste can carry
        # anything, and the reason is enough to fix it.
        logger.error(
            "MARKETING_APP_STORE_URL is misconfigured (%s) — /go and the landing page fall "
            "back to the pre-launch landing page until it is fixed", reason,
        )


def store_url() -> Optional[str]:
    """`MARKETING_APP_STORE_URL` if it is a well-formed `https://apps.apple.com/...` URL,
    else None (empty = pre-launch, silently; anything else = ERROR once)."""
    raw = (getattr(settings, "MARKETING_APP_STORE_URL", "") or "").strip()
    if not raw:
        return None
    if len(raw) > 512 or not raw.isascii() or any(ch in _UNSAFE_URL_CHARS for ch in raw) \
            or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        _log_misconfig("contains characters a URL may not carry")
        return None
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        _log_misconfig("not parseable as a URL")
        return None
    if parts.scheme != "https":
        _log_misconfig("scheme is not https")
        return None
    if (parts.hostname or "") != _STORE_HOST or parts.username or parts.password \
            or port not in (None, 443):
        _log_misconfig(f"host is not {_STORE_HOST}")
        return None
    return raw


def store_app_id() -> Optional[str]:
    """The numeric App Store id from the configured URL (`/id6759525689`), or None."""
    url = store_url()
    if url is None:
        return None
    m = _APP_ID_RE.search(urlsplit(url).path)
    return m.group(1) if m else None


def destination(campaign: str) -> str:
    """Where `/go/<campaign>` sends the caller. `"/"` pre-launch or on misconfiguration."""
    url = store_url()
    if url is None:
        return "/"
    # Re-normalised here too, so `ct` cannot come from anything but the constant set even if a
    # future caller forgets to normalise first.
    slug = campaign if campaign in KNOWN_CAMPAIGNS else OTHER
    token = (getattr(settings, "MARKETING_APP_STORE_PROVIDER_TOKEN", "") or "").strip()
    if not token or slug == OTHER:
        return url
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in ("pt", "ct", "mt")]
    query += [("pt", token), ("ct", slug), ("mt", "8")]
    return urlunsplit(parts._replace(query=urlencode(query)))


# ── landing page slots ────────────────────────────────────────────────────────

_PLACEHOLDER_RE = re.compile(r"\{\{(APP_STORE_CTA|SMART_BANNER)\}\}")

_PRELAUNCH_CTA = '<p class="cta cta-soon">Coming soon to the App Store</p>'


def landing_slots() -> Dict[str, str]:
    """HTML fragments for the landing page's two placeholders. Every dynamic value is escaped."""
    url = store_url()
    if url is None:
        return {"APP_STORE_CTA": _PRELAUNCH_CTA, "SMART_BANNER": ""}
    cta = (
        f'<a class="cta" href="{html.escape(url, quote=True)}">Get Caydex for iPhone</a>'
        '<p class="cta-note">Opens the App Store</p>'
    )
    app_id = store_app_id()
    banner = (f'<meta name="apple-itunes-app" content="app-id={html.escape(app_id, quote=True)}">'
              if app_id else "")
    return {"APP_STORE_CTA": cta, "SMART_BANNER": banner}


def render_landing(template: str) -> str:
    """Fill the landing template's placeholders in ONE pass: a substituted value is never
    rescanned, so a fragment can never smuggle in another placeholder."""
    slots = landing_slots()
    return _PLACEHOLDER_RE.sub(lambda m: slots[m.group(1)], template)


# ── who counts ────────────────────────────────────────────────────────────────

_UA_CAP = 512
#: Literal alternations only (linear). `bot\b` is the generic marker ("AhrefsBot/7",
#: "PetalBot;"); it also catches the odd phone model whose name ends in "bot", which only
#: under-counts. Link-preview fetchers are listed by name because several do not say "bot".
#:
#: Each crawler is matched by ITS OWN product token (`twitterbot`, `linkedinbot`,
#: `pinterestbot`), never by a bare platform name: the bare name is exactly what that
#: platform's in-app browser appends to an ordinary WebKit UA (`... Mobile/15E148
#: [Pinterest/iOS]`), and those are people who tapped the link. `mastodon` and `whatsapp` stay
#: bare on purpose — their preview fetchers ("http.rb/5.1.1 (Mastodon/4.2; ...)",
#: "WhatsApp/2.23.20.0 A") carry no "bot", and neither app tags its in-app browser.
_BOT_UA_RE = re.compile(
    r"facebookexternalhit|facebookcatalog|meta-externalagent|twitterbot|slackbot|"
    r"slack-imgproxy|linkedinbot|discordbot|telegrambot|whatsapp|applebot|googlebot|"
    r"google-inspectiontool|bingbot|bingpreview|pinterestbot|pinterest/0\.|redditbot|"
    r"embedly|skypeuripreview|mastodon|bluesky|cardyb|iframely|googleimageproxy|"
    r"yahoomailproxy|bot\b|crawler|crawling|spider|preview|headless|"
    r"lighthouse|curl|wget|python-requests|python-urllib|httpx|aiohttp|go-http-client|"
    r"okhttp|libwww|scrapy|axios|node-fetch|java/",
    re.IGNORECASE,
)
#: Every browser and every in-app WebView a person taps a link in opens its UA with one of
#: these (Opera Mini still sends the pre-2013 "Opera/9.80" form). Native HTTP stacks — the
#: client-side link unfurlers inside chat apps — do not: "Dalvik/2.1.0 (Linux; ...)",
#: "Slack/24.03 CFNetwork/1494 Darwin/23.4.0", "Microsoft Office/16.0 (...)".
_BROWSER_UA_PREFIXES = ("mozilla/", "opera/")


def is_bot(user_agent: Optional[str]) -> bool:
    """A crawler, link-preview fetcher, native HTTP stack or script — anything that is not a
    browser. An absent/blank UA counts as a bot."""
    if not user_agent or not user_agent.strip():
        return True
    ua = user_agent[:_UA_CAP]
    if not ua.lstrip()[:8].lower().startswith(_BROWSER_UA_PREFIXES):
        return True
    return _BOT_UA_RE.search(ua) is not None


def is_prefetch(headers: Mapping[str, str]) -> bool:
    """A speculative browser fetch (`Sec-Purpose`/`Purpose: prefetch`, `X-Moz: prefetch`)."""
    lowered = {str(k).lower(): str(v).lower() for k, v in headers.items()}
    for name in ("sec-purpose", "purpose"):
        if "prefetch" in lowered.get(name, ""):
            return True
    return lowered.get("x-moz", "").strip() == "prefetch"


#: Fetch-metadata values are single short tokens; anything longer is not one of ours to parse.
_FETCH_META_CAP = 32


def is_non_navigation(headers: Mapping[str, str]) -> bool:
    """The BROWSER says this request is not a person opening the link: an `<img>`/`<script>`
    embed, a `fetch()` (any mode) or an iframe, via Fetch Metadata. A person following a link
    is always `Sec-Fetch-Mode: navigate` + `Sec-Fetch-Dest: document`, and page script cannot
    forge either header, so one rule covers every subresource vector — a third-party page or an
    HTML newsletter embedding `/go/<campaign>` as an image would otherwise count every reader.

    ABSENT headers are NOT evidence: WKWebView before iOS 16.4 and several embedded browsers
    send no fetch metadata at all, and their taps are real. Only a header that is PRESENT with
    another value disqualifies. `iframe`/`nested-navigate` are deliberately not navigations.
    """
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    mode = lowered.get("sec-fetch-mode")
    if mode is not None and mode[:_FETCH_META_CAP].strip().lower() != "navigate":
        return True
    dest = lowered.get("sec-fetch-dest")
    return dest is not None and dest[:_FETCH_META_CAP].strip().lower() != "document"


def rate_key(ip: Optional[str]) -> str:
    """Limiter key for a client address: IPv4 as-is, IPv6 by its /64 (one subscriber usually
    holds a whole /64, so per-address keys would be free to rotate), garbage → "unknown"."""
    try:
        addr = ipaddress.ip_address((ip or "").strip()[:64])
    except ValueError:
        return "unknown"
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network((int(addr), 64), strict=False))
    return str(addr)


#: Per-address budget for COUNTED hits. Over it the caller is still redirected.
_RATE_MAX = 20
_RATE_WINDOW_SECONDS = 60


class _LinkLimiter(RateLimiter):
    """The per-address limiter's OWN pool — never the process-wide `rate_limiter`.

    Private so the per-address check can run FIRST without its keys touching the shared general
    pool (chat / report / analytics buckets): every hit that reaches a campaign's ceiling has
    then already been allowed by its address's budget, so the ceiling's refused count is
    EXACTLY what raising the ceiling would recover. With the shared pool the ceiling had to run
    first (so a flood could not insert `go:ip:` keys there), which meant it could not tell a
    hit its address was entitled to from one it was not, and it reported both as lost.
    A smaller cap than the shared pool: only /go addresses live here, and evicting one merely
    hands that address a fresh budget, which its campaign's ceiling still bounds."""

    _MAX_TRACKED = 5_000


_link_limiter = _LinkLimiter()

#: Per-CAMPAIGN budget for counted hits per window. The per-address limit above is only as
#: good as the address: anyone with an address pool (a residential proxy, a botnet, the 256
#: /64s of one home /56) gets 20 fresh counts per address per minute, so without a ceiling a
#: flood inflates a row without bound. 600/min (10 taps a second, sustained) is far above
#: anything a brand-new account's post has produced; reaching it logs a WARNING and, when the
#: window ends, how many hits the ceiling refused, so a real spike is visible.
#:
#: PER CAMPAIGN, never one shared budget: a ceiling trades inflation for SUPPRESSION of what it
#: refuses, and a single shared one let a flood of any slug — junk `/go/wp-login` traffic
#: normalised to "other" included — refuse every real campaign's taps while the junk was
#: counted. Now a flood can saturate only the campaign it targets. Each `(campaign, day)` row's
#: inflation bound is its own ceiling; the sum across campaigns is larger (14 × 600 + 60), but
#: nothing reads the sum, and the addresses it can hold are the private limiter's, not the
#: shared pool's.
_CAMPAIGN_RATE_MAX = 600
#: "other" — bare `/go`, typos and junk slugs. No post links there (`post_copy.cta_for` only
#: emits KNOWN_CAMPAIGNS), so it gets a tenth of a campaign's budget.
_OTHER_RATE_MAX = 60
_CEILING_WINDOW_SECONDS = 60.0
#: Hard bound on distinct (campaign, day) keys held in memory, fresh and re-send tiers together.
#: Campaigns are a closed set, so this is ~125 days of a flush outage — reaching it means the
#: RPC has been failing unnoticed.
_MAX_PENDING_KEYS = 2000
#: `increment_marketing_link_hits` rejects p_count outside [1, 1e6].
_MAX_RPC_COUNT = 1_000_000

#: Hits never sent yet.
_pending: Dict[Tuple[str, str], int] = {}
#: Hits whose ONE send had an UNKNOWN OUTCOME — the RPC may already have counted them. They get
#: exactly one more send and are then dropped. Kept apart from `_pending` so a later tap never
#: inherits their attempt and an unknown outcome cannot compound: merged back into `_pending`,
#: a batch was re-sent on every failing flush, so the first minute of a k-minute gateway
#: incident was counted k+1 times while the log promised "twice".
_retry: Dict[Tuple[str, str], int] = {}
_cap_warned = False


def _today_et() -> str:
    return datetime.now(ET).date().isoformat()


def _monotonic() -> float:
    return time.monotonic()


def _wall_clock() -> float:
    return time.time()


def _utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ceiling_for(campaign: str) -> int:
    return _OTHER_RATE_MAX if campaign == OTHER else _CAMPAIGN_RATE_MAX


class _Window:
    """One campaign's ceiling window. `refused` counts hits the ceiling refused AFTER the
    per-address limiter allowed them — exactly what raising the ceiling would have counted."""

    __slots__ = ("started", "wall", "counted", "refused")

    def __init__(self, started: float, wall: float) -> None:
        self.started = started
        self.wall = wall
        self.counted = 0
        self.refused = 0


#: campaign → its current window. A closed set: KNOWN_CAMPAIGNS plus "other".
_windows: Dict[str, _Window] = {}


def _window_ended(w: _Window, now: float) -> bool:
    return not 0 <= now - w.started < _CEILING_WINDOW_SECONDS


def _ceiling_advice(campaign: str) -> str:
    """What the refused count means for THIS campaign's ceiling, naming the constant that
    actually caps it (`_ceiling_for`) — "other" is not capped by _CAMPAIGN_RATE_MAX, and
    telling an operator to raise that recovered nothing."""
    if campaign == OTHER:
        return ("'other' is bare /go, typos and junk slugs — no post links there — so this is "
                "almost certainly scanner traffic; its ceiling is _OTHER_RATE_MAX and raising it "
                "is not expected to recover a real spike")
    return "that is what raising _CAMPAIGN_RATE_MAX would have recovered if it was a real spike"


def _log_window_summary(campaign: str, w: _Window, *, partial: bool = False) -> None:
    if not w.refused:
        return
    logger.warning(
        "marketing link hits: campaign=%s window %s..%s%s: %d hit(s) over the ceiling of %d "
        "were redirected but NOT counted — every one was within its address's own budget; %s",
        campaign, _utc(w.wall), _utc(w.wall + _CEILING_WINDOW_SECONDS),
        " (partial: process stopping)" if partial else "", w.refused, _ceiling_for(campaign),
        _ceiling_advice(campaign),
    )


def _campaign_window(campaign: str) -> _Window:
    """The campaign's current window; an ended one is reported (if it refused anything) and
    replaced."""
    now = _monotonic()
    w = _windows.get(campaign)
    if w is not None and not _window_ended(w, now):
        return w
    if w is not None:
        _log_window_summary(campaign, w)
    w = _windows[campaign] = _Window(now, _wall_clock())
    return w


def report_ended_windows(*, final: bool = False) -> None:
    """Report every ended window's refused count NOW — from the flush loop each cycle — instead
    of whenever that campaign's next hit happens to arrive (hours later, or never). `final`
    (shutdown) also reports the windows still open, marked partial. A reported window is
    closed, so nothing is logged twice. Never raises."""
    try:
        now = _monotonic()
        for campaign, w in list(_windows.items()):
            ended = _window_ended(w, now)
            if ended or final:
                _log_window_summary(campaign, w, partial=not ended)
                del _windows[campaign]
    except Exception as e:
        logger.warning("marketing link hits: ceiling window report failed (%s: %s)",
                       type(e).__name__, e, exc_info=True)


def _put(store: Dict[Tuple[str, str], int], key: Tuple[str, str], n: int) -> bool:
    """Add `n` hits under `key` in `store`, respecting the key cap. Returns False when dropped."""
    global _cap_warned
    if n <= 0:
        return False
    if key not in store and len(_pending) + len(_retry) >= _MAX_PENDING_KEYS:
        if not _cap_warned:
            _cap_warned = True
            logger.warning(
                "marketing link hits: pending counter is full (%d keys) — dropping new keys "
                "until a flush succeeds; is increment_marketing_link_hits failing?",
                _MAX_PENDING_KEYS,
            )
        return False
    store[key] = store.get(key, 0) + n
    return True


def _add(key: Tuple[str, str], n: int) -> bool:
    """Add `n` never-sent hits under `key`. Returns False when dropped by the key cap."""
    return _put(_pending, key, n)


def _add_retry(key: Tuple[str, str], n: int) -> bool:
    """Add `n` hits that already had ONE unknown-outcome send. Returns False when dropped."""
    return _put(_retry, key, n)


def record_hit(request: Any, campaign: str) -> bool:
    """Count one smart-link follow if it looks like a person. Never raises, never does I/O.
    Returns whether the hit was counted."""
    try:
        if str(getattr(request, "method", "")).upper() == "HEAD":
            return False
        headers = request.headers
        if is_bot(headers.get("user-agent")):
            return False
        if is_prefetch(headers):
            return False
        if is_non_navigation(headers):
            return False
        slug = campaign if campaign in KNOWN_CAMPAIGNS else OTHER
        # Per-address FIRST (the link's private pool), the campaign's ceiling second: a hit its
        # address is not entitled to never spends or reaches the ceiling, so what the ceiling
        # refuses is exactly what raising it would count.
        key = f"go:ip:{rate_key(trusted_client_ip(request))}"
        if not _link_limiter.is_allowed(key, _RATE_MAX, _RATE_WINDOW_SECONDS):
            return False
        window = _campaign_window(slug)
        if window.counted >= _ceiling_for(slug):
            if not window.refused:
                logger.warning(
                    "marketing link hits: campaign=%s reached its ceiling of %d counted hits per "
                    "%ds (window from %s) — further hits to it this window are redirected but "
                    "not counted; other campaigns are unaffected. The window's summary says how "
                    "many were refused", slug, _ceiling_for(slug), int(_CEILING_WINDOW_SECONDS),
                    _utc(window.wall),
                )
            window.refused += 1
            return False
        window.counted += 1
        return _add((slug, _today_et()), 1)
    except Exception as e:
        # The redirect must never fail because of the counter.
        logger.warning("marketing link hit not recorded (%s: %s)", type(e).__name__, e,
                       exc_info=True)
        return False


# ── flush ─────────────────────────────────────────────────────────────────────

_MISSING_OBJECT_CODES = frozenset({"PGRST202", "PGRST205", "42P01", "42883"})
#: The same codes as whole TOKENS, for the one fallback that reads text (see `_looks_missing`):
#: a code embedded in a longer alphanumeric run (a hex Ray ID) is not a code.
_MISSING_OBJECT_TEXT_RE = re.compile(r"(?<![0-9A-Za-z])(?:PGRST20[25]|42P01|42883)(?![0-9A-Za-z])")
#: Sentinel: the exception has no `code` attribute at all (as opposed to `code=None`).
_NO_CODE = object()
_missing_logged = False


def _looks_missing(e: BaseException) -> bool:
    """The RPC or its table does not exist (migration 173 not applied).

    A structured `.code` IS the answer, as in `supabase_errors._classify_one` ("never classify
    on str(exc)"): postgrest's non-JSON path puts the gateway's whole HTML page into `str(e)`,
    so a substring scan read a Cloudflare Ray ID like `8c9d42883e1f0a7b` as SQLSTATE 42883 —
    labelling a possibly-committed batch "definitely unsent" and firing a false "migration 173
    not applied" ERROR. Only an exception with NO `code` attribute at all (a wrapper quoting
    the SQLSTATE in its message) falls back to its text, and then only to a whole token."""
    code = getattr(e, "code", _NO_CODE)
    if code is not _NO_CODE:
        return isinstance(code, str) and code.strip().upper() in _MISSING_OBJECT_CODES
    return _MISSING_OBJECT_TEXT_RE.search(str(e)) is not None


#: The request never reached the server: nothing can have committed.
_UNSENT_TRANSPORT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
#: A SQLSTATE (`22023`, `42P01`) — PostgreSQL answered with an error, so the statement rolled
#: back. postgrest's "JSON could not be generated" carries the HTTP status as an INT instead.
_SQLSTATE_RE = re.compile(r"[0-9A-Z]{5}")
#: HTTP statuses that PROVE the request never reached PostgREST. postgrest turns every body that
#: is not a four-key PostgREST error into an HTTP-status `code` (its error model requires all
#: four keys), and PostgREST's OWN 4xx always carry a PGRST*/SQLSTATE string — so a 4xx STATUS is
#: the gateway refusing before it forwarded: a revoked/rotated key (401 `{"message": "Invalid
#: API key"}`), 403, a wrong URL (404), its rate limit (429), a body limit (413). Minus 408 and
#: 499, which a proxy chain can emit for a request the origin was already handling. Plus the
#: Cloudflare statuses for an origin it never reached: 521 refused, 522 no TCP handshake / no
#: ACK of the request, 523 unreachable, 525/526 TLS failed, 530 origin DNS.
#: NOT 500/502/503/504/520/524 — those can arrive AFTER the origin committed (an int 503 is the
#: gateway's, PostgREST's own 503 carries PGRST00x), so they stay unknown outcomes. And NOT
#: `supabase_errors.TRANSIENT_HTTP_STATUS`: that is a RETRY list, and it holds 520/524.
_PRE_ORIGIN_5XX = frozenset({521, 522, 523, 525, 526, 530})
_AMBIGUOUS_4XX = frozenset({408, 499})


def _status_never_reached_origin(status: int) -> bool:
    if 400 <= status < 500:
        return status not in _AMBIGUOUS_4XX
    return status in _PRE_ORIGIN_5XX


def _definitely_unsent(e: BaseException) -> bool:
    """True only when the failure PROVES the increment did not commit. Everything else — a
    read timeout, a torn-down response, a gateway 5xx, an unknown exception — is an unknown
    outcome: the server may already have applied it.

    Decided on structured fields only. A present `code` is the whole answer: an HTTP status
    (an int, or a 3-digit string — `supabase_errors._status_from_code`) is unsent only when the
    status itself proves the origin was never reached (`_status_never_reached_origin`), else
    unknown; None is unknown; a PGRST* or a SQLSTATE is a PostgreSQL/PostgREST refusal, i.e.
    rolled back — except class 08 (connection exception), where the connection died and the
    COMMIT may or may not have run."""
    if isinstance(e, _UNSENT_TRANSPORT_ERRORS):
        return True
    code = getattr(e, "code", _NO_CODE)
    if code is _NO_CODE:
        return _looks_missing(e)
    status = _status_from_code(code)
    if status is not None:
        return _status_never_reached_origin(status)
    if not isinstance(code, str):
        return False
    code = code.strip().upper()
    if code.startswith("PGRST"):
        return True
    return _SQLSTATE_RE.fullmatch(code) is not None and not code.startswith("08")


class _FlushTally:
    """What one flush decided, logged ONCE — at the end of the batch, or from the cancel branch
    when the batch is cut short (`log(cancelled_at=i)`). Sync and I/O-free, so the cancel path
    awaits nothing."""

    __slots__ = ("total", "failures", "first_error", "unknown_keys", "unknown_hits",
                 "dropped_keys", "dropped_hits")

    def __init__(self, total: int) -> None:
        self.total = total
        self.failures = 0
        self.first_error = ""
        self.unknown_keys = self.unknown_hits = 0
        self.dropped_keys = self.dropped_hits = 0

    def note_error(self, campaign: str, day: str, e: BaseException) -> None:
        if not self.first_error:
            self.first_error = f"campaign={campaign} day={day} {type(e).__name__}: {e}"[:300]

    def log(self, *, cancelled_at: Optional[int] = None) -> None:
        if not (self.first_error or self.unknown_keys or self.dropped_keys):
            return
        # A cancelled batch promises no re-send: the cancel may be the shutdown's own final
        # flush, after which nothing runs (`run_link_hit_flush_loop` then logs what was LOST).
        again = ("will be re-sent ONCE" if cancelled_at is None
                 else "are held for ONE re-send if another flush runs")
        notes = []
        if self.unknown_keys:
            notes.append(
                f"{self.unknown_keys} key(s) / {self.unknown_hits} hit(s) had an UNKNOWN OUTCOME "
                f"(the RPC may have committed) and {again} — at-least-once, so they may be "
                f"counted twice, never more")
        if self.dropped_keys:
            notes.append(
                f"{self.dropped_keys} key(s) / {self.dropped_hits} hit(s) that were ALREADY a "
                f"re-send had a second UNKNOWN OUTCOME and were DROPPED rather than risk a third "
                f"count — each of those is counted zero, one or two times")
        if cancelled_at is None:
            head = (f"marketing link hits: {self.failures} of {self.total} key(s) failed and "
                    f"were kept for the next flush except where noted")
        else:
            head = (f"marketing link hits: flush CANCELLED at key {cancelled_at + 1} of "
                    f"{self.total}; {self.failures} key(s) before it failed and are held in "
                    f"memory except where noted")
        logger.warning("%s (%s)%s", head, self.first_error or "no error text",
                       "".join(f"; {n}" for n in notes))


async def flush_hits() -> int:
    """Send every held count to `increment_marketing_link_hits`, one RPC per key.

    Both dicts are SWAPPED before the first await, so hits recorded while the RPCs run land in
    the fresh dicts and are never mixed into the batch being sent.

    Delivery is AT-LEAST-ONCE and AT MOST TWICE per hit. The RPC is a plain increment with no
    batch id, so a failure cannot be told apart from "applied, but the response was lost".

    * A PROVEN non-commit (a refused connection, migration 173 not applied, a SQLSTATE, a
      gateway refusal that never reached the origin — `_status_never_reached_origin`) is held in
      its own tier and re-sent every flush until it lands or the process stops (the tiers are
      in memory; the shutdown path logs what it could not send) — nothing was counted.
    * An UNKNOWN OUTCOME (a read timeout, a gateway 520/524) moves those hits to `_retry`: they
      may already be counted ONCE, so they get exactly one more send — a DOUBLE COUNT if the
      first had in fact committed.
    * A second unknown outcome for hits already in `_retry` DROPS them rather than risk a third
      count: they end up counted zero, one or two times.

    Each key's two tiers share one RPC (the re-send portion first under the 1e6 cap); on an
    unknown outcome the split is kept, so a fresh tap never inherits a re-send's attempt. Both
    are logged as "UNKNOWN OUTCOME" with the counts, so an inflated day can be found.
    Exactly-once would need a batch id and a dedup ledger in the RPC; for an indicative
    counter that is not worth a table (the module docstring says why). Returns the hits the
    database confirmed.
    """
    global _pending, _retry, _missing_logged
    if not _pending and not _retry:
        return 0
    fresh, again = _pending, _retry
    _pending, _retry = {}, {}

    keys = sorted(set(fresh) | set(again))
    try:
        client = get_supabase()
    except Exception as e:
        for key in keys:
            _add_retry(key, again.get(key, 0))
            _add(key, fresh.get(key, 0))
        logger.warning("marketing link hits: no Supabase client, %d key(s) kept (%s: %s)",
                       len(keys), type(e).__name__, e)
        return 0

    flushed = 0
    tally = _FlushTally(len(keys))
    for i, key in enumerate(keys):
        campaign, day = key
        resend = min(again.get(key, 0), _MAX_RPC_COUNT)
        new = min(fresh.get(key, 0), _MAX_RPC_COUNT - resend)
        # Whatever the RPC's ceiling leaves over stays in its own tier for the next flush.
        _add_retry(key, again.get(key, 0) - resend)
        _add(key, fresh.get(key, 0) - new)
        sent = resend + new
        if sent <= 0:
            continue
        try:
            await sb_exec(client.rpc(
                "increment_marketing_link_hits",
                {"p_campaign": campaign, "p_day": day, "p_count": sent},
            ))
        except asyncio.CancelledError:
            # AT-MOST-ONCE for the in-flight key, the one exception to the rule above. Keys
            # not yet attempted are definitely unsent and keep their tiers. The in-flight one
            # is running in a worker thread that cancellation cannot stop, so it most likely
            # lands; re-queuing it would count it again in the shutdown flush (if that RPC then
            # fails, its hits are lost — the accepted cost).
            for later in keys[i + 1:]:
                _add_retry(later, again.get(later, 0))
                _add(later, fresh.get(later, 0))
            logger.warning(
                "marketing link hits: flush cancelled mid-batch; campaign=%s day=%s count=%d "
                "outcome unknown, %d key(s) kept", campaign, day, sent, len(keys) - i - 1,
            )
            # What the keys BEFORE this one already decided (an unknown outcome parked, a
            # re-send DROPPED) is reported here too: skipping the end-of-batch summary on a
            # cancel was a silent drop, most likely exactly during an incident plus a redeploy.
            tally.log(cancelled_at=i)
            raise
        except Exception as e:
            tally.failures += 1
            if _definitely_unsent(e):
                # Nothing was counted: each portion goes back to its own tier, attempt unspent.
                _add_retry(key, resend)
                _add(key, new)
                if _looks_missing(e):
                    if not _missing_logged:
                        _missing_logged = True
                        logger.error(
                            "marketing link hits: increment_marketing_link_hits is missing — "
                            "migration 173 not applied (%s: %s); counts are held in memory",
                            type(e).__name__, e,
                        )
                else:
                    tally.note_error(campaign, day, e)
                continue
            tally.note_error(campaign, day, e)
            if new:
                _add_retry(key, new)
                tally.unknown_keys += 1
                tally.unknown_hits += new
            if resend:
                tally.dropped_keys += 1
                tally.dropped_hits += resend
            continue
        flushed += sent
    tally.log()
    return flushed


_FLUSH_INTERVAL_SECONDS = 60
_FINAL_FLUSH_TIMEOUT_SECONDS = 5


async def run_link_hit_flush_loop() -> None:
    """Lifespan task (`app/main.py` `_spawn`): flush every minute; one last bounded flush on
    shutdown so a redeploy loses at most what that final flush cannot send in 5 s. Each cycle
    also reports the campaign ceiling windows that have ended (`report_ended_windows`)."""
    try:
        while True:
            await asyncio.sleep(_FLUSH_INTERVAL_SECONDS)
            report_ended_windows()
            try:
                n = await flush_hits()
                if n:
                    logger.info("marketing link hits: flushed %d hit(s)", n)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("marketing link hit flush failed (%s: %s)", type(e).__name__, e,
                             exc_info=True)
    except asyncio.CancelledError:
        report_ended_windows(final=True)
        try:
            n = await asyncio.wait_for(flush_hits(), _FINAL_FLUSH_TIMEOUT_SECONDS)
            if n:
                logger.info("marketing link hits: final flush sent %d hit(s)", n)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("marketing link hits: final flush failed (%s: %s)",
                           type(e).__name__, e, exc_info=True)
        finally:
            # There is no next flush: whatever is still held dies with the process.
            _log_lost_at_shutdown()
        raise


#: How many held keys the shutdown line names individually (the key cap is 2000).
_LOST_KEYS_LISTED = 20


def _log_lost_at_shutdown() -> None:
    """After the final flush: every count still held in memory is LOST with the process — the
    keys the flush could not send in its 5 s, the ones it kept after a failure, and anything
    a cancel handed back. Said once, with the numbers, instead of a bare "final flush failed".
    Never raises."""
    try:
        if not _pending and not _retry:
            return
        held = sorted(set(_pending) | set(_retry))
        listed = ", ".join(
            f"{c}/{d}={_pending.get((c, d), 0)}+{_retry.get((c, d), 0)}"
            for c, d in held[:_LOST_KEYS_LISTED]
        )
        more = len(held) - _LOST_KEYS_LISTED
        logger.warning(
            "marketing link hits: LOST at shutdown — %d key(s) still held after the final "
            "flush: %d never-sent hit(s) and %d re-send hit(s) (an earlier send had an UNKNOWN "
            "OUTCOME, so each of those is counted zero or one time) will not be counted; "
            "campaign/day=never-sent+re-send: %s%s",
            len(held), sum(_pending.values()), sum(_retry.values()), listed,
            f" (+{more} more)" if more > 0 else "",
        )
    except Exception as e:
        logger.warning("marketing link hits: shutdown loss report failed (%s: %s)",
                       type(e).__name__, e, exc_info=True)
