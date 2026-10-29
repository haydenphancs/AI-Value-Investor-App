"""Web search for WHY a stock is jumping — the Updates Insights jump mode (2026-10-28).

WHY. TestFlight, ORCL, Wed 2026-10-28: the stock rose ~12% from ~14:15 ET on its Investor
Day while its Insights card still summarised the previous day's news. When a ticker JUMPS (an
Unusual/Extreme move in the current session, `updates_materiality.jump_band`), the sweeper asks
this service to search the web for the reason; the pages it keeps are a generation input of
that ticker's card (a fenced block in the prompt) and the first entries of its Sources list,
which the app opens in its in-app browser.

OWNER DECISION 2026-10-28 ("i pick option 2, i don't think we break any terms"): ON at deploy,
and the card — shared by every watcher, served until a newer card for the ticker replaces it —
may hold text written from these pages and their titles and links. That replaces, FOR INSIGHTS
CARDS ONLY, the 2026-10-09 rule "no cross-user cache of web results". What still binds: web
results are never used to evaluate or tune the AI (Brave terms §3(b)(xiii)) — so the preview and
eval scripts force the switch off, and chat's grounding audit and refund judge skip any turn that
read a web-sourced card — and they are never kept anywhere else: `WebPage`'s repr carries no
title, snippet or link, and the Sentry scrubber drops the frame locals that hold them
(`log_redaction`).

WHAT IS SENT. Only a code-authored query built from the company's name as the FMP screener
spells it ("why is tesla stock up today" from "Tesla, Inc.") — never the client-writable
watchlist name, never a figure, never anything about a user. A name that is not, once cleaned,
letters, digits and . & ' - means no search.

BUDGET. One durable, fail-CLOSED bucket in `chat_usage_budget` (`UPDATES_JUMP_WEB_SEARCH_DAILY_CAP`,
outside chat's 180, which it must never eat), claimed before every call and refunded only when
Brave proves the search did not run. Per scope and ET day: at most `SEARCHES_PER_SCOPE_PER_DAY`,
spaced `RESEARCH_SECONDS` apart, at once again when the move changes sign; at most
`MAX_SEARCHES_PER_REFRESH` searches start in one sweep.

WHAT IS KEPT. https pages outside the deny-list (social, search engines, quote pages), one per
host, published in the last 24 h by their own date (or Brave's minute/hour age), naming the
company or its symbol, at most `MAX_KEPT` — the card lists every page it reads — text cleaned and
capped. The kept set lives in THIS process for the rest of the ET day (a restart starts over); a
re-search that keeps nothing never erases it, and it is returned only while the move keeps the
direction it was searched for. `refreshed_at` moves only when the SET of pages changes — the
gate's one-shot trigger (`decide(web_refreshed_at=)`).

LOGGING. One counts-only line per search (`UPDATES_JUMP_WEB_SEARCH`): never the query, a title, a
snippet or a host — `unknown_host` counts the kept pages from a publisher we do not name.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.config import settings
from app.integrations import brave_search
from app.services.chat_budget_service import (
    ChatBudgetUnavailable,
    budget_day,
    get_chat_budget_service,
)
from app.services.news_insight_service import (
    _GENERIC_NAME_HEADS,
    _NAME_PREFIX_STOPS,
    MAX_SOURCE_URL_CHARS,
    MAX_WEB_SOURCES,
    company_name_variants,
)
from app.services.web_result_hygiene import (
    _INJECTION_RE,
    _clean_text,
    _host_denied,
    _known_publisher,
    _publisher_for_host,
    _safe_https_url,
    parse_page_age,
    parse_relative_age,
)
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

# ── Sizes and cadence ────────────────────────────────────────────────────────
#: The card lists every page it reads (Privacy §3: "stored with the title and link of each web
#: page it used"), so no more are kept than its Sources show.
MAX_KEPT = MAX_WEB_SOURCES
#: Over-fetch: Brave bills per REQUEST, and the deny-list and the 24 h window drop a share.
_FETCH_COUNT = 10
SEARCHES_PER_SCOPE_PER_DAY = 4
#: Searches one sweep may start. A broad sell-off makes most watched names jumpers at once;
#: two at a time, a long list would hold that sweep's cards for minutes. The rest are searched
#: on later sweeps, biggest move first, inside the same daily bucket.
MAX_SEARCHES_PER_REFRESH = 8
#: The card's own session cooldown: a sooner re-search could not reach a card anyway.
RESEARCH_SECONDS = 900
_MAX_PAGE_AGE = timedelta(hours=24)
#: A page dated a little ahead of our clock is clock skew; further ahead is a wrong date.
_FUTURE_SKEW = timedelta(minutes=10)
_TITLE_CAP = 160
_SNIPPET_CAP = 300
#: Brave's own timeout plus this bounds the whole call (the claim-steal window is 120 s).
_HARD_BOUND_SLACK = 2.0
_CONCURRENCY = 2
#: A symbol is matched as a whole uppercase token only at this length or more ("F", "BE"
#: and "ON" are words); shorter tickers rely on the company name.
_MIN_SYMBOL_MATCH_CHARS = 3

# The name a query carries, once cleaned: letters (any script), digits, spaces and . & ' - only —
# no URL, no email, no operator soup — at most 80 characters.
_QUERY_NAME_RE = re.compile(r"[^\W_](?:[^\W_]|[ .&'\-]){0,79}")
_SPACES_RE = re.compile(r"\s+")
# A share-class tail is not part of a company's name: "CoreWeave, Inc. Class A Common Stock".
_SHARE_CLASS_TAIL_RE = re.compile(
    r"\s+(?:class\s+[a-z](?![a-z])|(?:common|ordinary|preferred)\s+(?:stock|shares?)(?![a-z])|"
    r"american\s+depositary\s+(?:shares?|receipts?)(?![a-z])).*$",
    re.IGNORECASE,
)
# Slashed legal forms must reach the suffix list as its own tokens: "Novo Nordisk A/S" → "as",
# "Anheuser-Busch InBev SA/NV" → "sa nv", "Sendas Distribuidora S/A" → "sa".
_LEGAL_SLASH_RE = re.compile(r"(?<![^\W_])(?:SA/NV|S/A|N/V|A/S)(?![^\W_])", re.IGNORECASE)
_LEGAL_SLASH = {"sa/nv": "SA NV", "s/a": "SA", "n/v": "NV", "a/s": "AS"}
# Marks a brand carries that no headline or query needs: "Yum! Brands".
_BRAND_MARKS_RE = re.compile(r"[!®™©]")
# "JPMorgan Chase & Co." loses "co." to the suffix list and leaves a dangling "&".
_DANGLING_AND_RE = re.compile(r"(?:\s+(?:&|and))+$")

# A ONE-word name (an alias, the head of a longer name, or the first two words standing in for a
# longer one) makes a page about the company only beside a COMPANY CUE. Capitalisation cannot
# tell: a Title Case headline capitalises every word, so "Walmart Price Target Raised" would be
# about Target and "High-Beta Stocks Lead" about BETA Technologies (review 2026-10-29, round 2).
# The cue: a possessive, "stock"/"shares", a legal form, a listing in brackets — or "shares of".
_CUE_AFTER = (
    r"(?:['’]s(?![A-Za-z0-9])"
    r"|\s+(?i:stock|shares|inc\.?|corp\.?|corporation|company|co\.|ltd\.?|limited|plc|llc|"
    r"holdings?|group|technologies)(?![A-Za-z0-9])"
    r"|\s*\(\s*(?:(?i:nyse|nasdaq|amex|otc|tsx|lse)(?:\s*arca)?\s*:\s*)?[A-Z][A-Z.]{0,6}\s*\))"
)
_CUE_BEFORE = r"(?i:shares|stock)\s+(?i:of|in)\s+"

# ── Budget bucket (uuid5 — the column is shared with chat turns' per-account rows) ──
_BUDGET_BUCKET = str(uuid.uuid5(uuid.NAMESPACE_URL, "caydex:updates:jump-web-search-budget"))


def _clock() -> float:
    """The cadence clock — its own function so a test can move it without freezing
    `time.monotonic`, which the event loop itself reads."""
    return time.monotonic()


@dataclass(frozen=True, repr=False)
class WebPage:
    """One kept page: everything the card needs, nothing it does not."""

    title: str
    snippet: Optional[str]
    url: str
    publisher: str
    published_at: datetime   # aware UTC

    def __repr__(self) -> str:
        # Never the title, snippet or link: a repr is what an error report captures from a
        # frame, and a web result may be kept only in the card it explains.
        return f"WebPage(publisher={self.publisher!r})"


@dataclass(frozen=True)
class JumpCandidate:
    """A jumper the sweeper asks about, best first by ``score`` (the gate's `move_score`)."""

    scope: str
    name: Optional[str]          # the FMP screener's company name, never a watchlist one
    change_percent: float
    score: float = 0.0


@dataclass
class _ScopeDay:
    day: date
    searches: int = 0
    last_search: Optional[float] = None      # _clock()
    direction: Optional[str] = None           # of the last search attempt
    pages: Tuple[WebPage, ...] = ()
    pages_direction: Optional[str] = None     # the move those pages were searched for
    refreshed_at: Optional[datetime] = None   # when the kept SET last changed


def _clean_company_name(name: Any) -> Optional[str]:
    """The FMP company name as the press writes it: one line, no comma, no share-class tail,
    "A/S" as its suffix "AS". None when nothing is left."""
    if not isinstance(name, str):
        return None
    text = _BRAND_MARKS_RE.sub("", unicodedata.normalize("NFC", name))
    text = _SPACES_RE.sub(" ", text.replace(",", " ")).strip()
    text = _SHARE_CLASS_TAIL_RE.sub("", text)
    text = _LEGAL_SLASH_RE.sub(lambda m: _LEGAL_SLASH[m.group(0).lower()], text).strip()
    return text or None


def _fold(text: str) -> str:
    """Accents off ("Estée" → "Estee"), so a page and a name match however either spells it."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def build_query(name: Any, direction: str) -> Optional[str]:
    """``why is <name> stock up|down today`` from the company name, else None.

    The name is the FMP screener's, so it is not user text; cleaning it ("Tesla, Inc." →
    "tesla", through the legal-form rule the news subject filter uses) and then requiring
    letters, digits and . & ' - in at most 60 characters is the second wall: no URL, no email,
    no operator soup. The whitelist reads the name the QUERY carries, not FMP's raw string — a
    comma in "Tesla, Inc." once kept about half of all large caps from ever being searched.
    """
    variants = company_name_variants(_clean_company_name(name))
    if not variants:
        return None
    query_name = _DANGLING_AND_RE.sub("", variants[0]).strip()
    if not _QUERY_NAME_RE.fullmatch(query_name):
        return None
    word = "up" if direction == "up" else "down"
    return f"why is {query_name} stock {word} today"


def _et_day(now: datetime) -> date:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET).date()


def _name_patterns(scope: str, name: Optional[str]) -> List[re.Pattern]:
    """Whole-token patterns that make a page ABOUT this company, matched on accent-folded text.

    * ALONE: the full name when it has two or more words ("meta platforms", "american
      airlines"), in any case — a phrase is specific; a multi-word press name ("John Deere");
      the symbol, uppercase, when it is at least `_MIN_SYMBOL_MATCH_CHARS` long.
    * Only BESIDE A COMPANY CUE (`_CUE_AFTER` / `_CUE_BEFORE`): a one-word full name
      ("Target", "Oracle"), the head of a longer name ("Archer"), a one-word press name
      ("Disney", "Google": `widget_movers_service._LEAN_BRAND_ALIASES`), and the first two
      words of a longer name whose head is an ordinary word ("Rocket Lab" for Rocket Lab USA) —
      each as a name is written (capitalised, in capitals, or as FMP or the press spells it).
      A head that is an ordinary or sector word ("american", "general": `_GENERIC_NAME_HEADS`)
      never counts by itself.
    """
    from app.services.widget_movers_service import _LEAN_BRAND_ALIASES

    out: List[re.Pattern] = []
    cleaned = _fold(_clean_company_name(name) or "")
    spelled = {t.lower(): t for t in cleaned.split()}
    weak: set = set()

    def _written(words: List[str]) -> set:
        return {" ".join(spelled.get(w, w.capitalize()) for w in words),
                " ".join(w.capitalize() for w in words), " ".join(words).upper()}

    for i, variant in enumerate(company_name_variants(cleaned)):
        words = _DANGLING_AND_RE.sub("", variant).strip().split()
        if len(words) >= 2:
            out.append(_phrase(" ".join(words)))
            head = words[0]
            if (len(words) >= 3 and words[1] not in _NAME_PREFIX_STOPS
                    and head in _GENERIC_NAME_HEADS):
                weak |= _written(words[:2])
            continue
        if words and not (i > 0 and words[0] in _GENERIC_NAME_HEADS):
            weak |= _written(words)
    symbol = (scope or "").strip().upper()
    for alias in _LEAN_BRAND_ALIASES.get(symbol, ()):
        alias = _fold(alias)
        if " " in alias:
            out.append(_phrase(alias))
        else:
            weak |= {alias} if alias.isupper() else {alias, alias.capitalize(), alias.upper()}
    if weak:
        alternation = "|".join(re.escape(f) for f in sorted(weak, key=len, reverse=True))
        out.append(re.compile(
            rf"(?<![A-Za-z0-9])(?:{alternation}){_CUE_AFTER}"
            rf"|{_CUE_BEFORE}(?:{alternation})(?![A-Za-z0-9])"
        ))
    if len(symbol) >= _MIN_SYMBOL_MATCH_CHARS and symbol.isalnum():
        out.append(re.compile(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])"))
    return out


def _phrase(text: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(text)}(?![A-Za-z0-9])", re.IGNORECASE)


def digest(
    raw: Any, scope: str, name: Optional[str], now: datetime,
) -> Tuple[List[WebPage], Counter]:
    """The pages worth keeping from one Brave answer, and why the others were dropped.

    Pure apart from the regexes it compiles; ``now`` must be aware.
    """
    drops: Counter = Counter()
    rows = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return [], drops
    about = _name_patterns(scope, name)
    kept: List[WebPage] = []
    hosts: set = set()
    for row in rows:
        if len(kept) >= MAX_KEPT:
            break
        if not isinstance(row, dict):
            drops["malformed"] += 1
            continue
        safe = _safe_https_url(row.get("url"))
        if safe is None:
            drops["url"] += 1
            continue
        url, host, path = safe
        if len(url) > MAX_SOURCE_URL_CHARS:
            # The card stores links clipped at this length; a page it cannot link is not read.
            drops["url"] += 1
            continue
        if _host_denied(host, path):
            drops["denied"] += 1
            continue
        bare = host[4:] if host.startswith("www.") else host
        if bare in hosts:
            drops["same_host"] += 1
            continue
        title = _clean_text(row.get("title"), _TITLE_CAP)
        if not title:
            drops["no_title"] += 1
            continue
        snippet = _clean_text(row.get("description"), _SNIPPET_CAP)
        if _INJECTION_RE.search(title) or (snippet and _INJECTION_RE.search(snippet)):
            drops["injection"] += 1
            continue
        published = parse_page_age(row.get("page_age")) or parse_relative_age(row.get("age"), now)
        if published is None:
            drops["no_age"] += 1
            continue
        if published > now + _FUTURE_SKEW:
            drops["future"] += 1
            continue
        if now - published > _MAX_PAGE_AGE:
            drops["stale"] += 1
            continue
        text = _fold(f"{title} {snippet or ''}")
        if not about or not any(p.search(text) for p in about):
            drops["off_subject"] += 1
            continue
        hosts.add(bare)
        kept.append(WebPage(
            title=title, snippet=snippet, url=url,
            publisher=_publisher_for_host(host), published_at=published,
        ))
    return kept, drops


class JumpWebSearchService:
    """Process-local state for the day's jump searches; see the module docstring."""

    def __init__(self) -> None:
        self._days: Dict[str, _ScopeDay] = {}
        self._capped_day: Optional[str] = None

    # ── reads ────────────────────────────────────────────────────────────

    @staticmethod
    def enabled() -> bool:
        """The switch AND a configured key — read at call time, never cached."""
        return bool(getattr(settings, "UPDATES_JUMP_WEB_SEARCH_ENABLED", False)) and (
            brave_search.is_configured()
        )

    def context(
        self, scope: str, now: datetime, *, direction: Optional[str],
    ) -> Tuple[Tuple[WebPage, ...], Optional[datetime]]:
        """This process's kept pages for ``scope`` on the current ET day, and when that set
        last changed. Empty on another day, after a restart, or when nothing was kept — and
        unless ``direction`` ("up"/"down", the sign of today's move now) is the one the pages
        were searched for: pages explaining a rally must never be presented under a fall."""
        st = self._days.get(scope)
        if st is None or st.day != _et_day(now) or not st.pages:
            return (), None
        if direction is None or st.pages_direction != direction:
            return (), None
        return st.pages, st.refreshed_at

    # ── the search ───────────────────────────────────────────────────────

    async def refresh(
        self, candidates: Sequence[JumpCandidate], now: datetime,
    ) -> Dict[str, int]:
        """Search for each candidate that is due, biggest move first, two at a time. Returns
        status counts for the sweep log. Never raises (cancellation propagates)."""
        counts: Counter = Counter()
        if not candidates or not self.enabled():
            return dict(counts)
        ranked = sorted(candidates, key=lambda c: -c.score)
        sem = asyncio.Semaphore(_CONCURRENCY)
        slots = [MAX_SEARCHES_PER_REFRESH]

        async def _one(c: JumpCandidate) -> None:
            async with sem:
                try:
                    status = await self._search_one(c, now, slots)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 — one scope must not stop the sweep
                    logger.warning(
                        "UPDATES_JUMP_WEB_SEARCH scope=%s status=error unexpected=%s",
                        c.scope, type(e).__name__, exc_info=True,
                    )
                    status = "error"
                counts[status] += 1

        await asyncio.gather(*(_one(c) for c in ranked))
        return dict(counts)

    def _state(self, scope: str, now: datetime) -> _ScopeDay:
        day = _et_day(now)
        st = self._days.get(scope)
        if st is None or st.day != day:
            st = _ScopeDay(day=day)
            self._days[scope] = st
        return st

    async def _search_one(
        self, c: JumpCandidate, now: datetime, slots: Optional[List[int]] = None,
    ) -> str:
        st = self._state(c.scope, now)
        direction = "up" if c.change_percent > 0 else "down"
        clock = _clock()
        flipped = st.direction is not None and st.direction != direction
        if st.searches >= SEARCHES_PER_SCOPE_PER_DAY:
            return "day_limit"
        if st.last_search is not None and not flipped and clock - st.last_search < RESEARCH_SECONDS:
            return "not_due"
        query = build_query(c.name, direction)
        if query is None:
            # The scope, never the name: a name that cannot be searched should be visible.
            logger.info("UPDATES_JUMP_WEB_SEARCH scope=%s status=no_name", c.scope)
            return "no_name"
        if slots is not None:
            # Checked and taken with no await between: the event loop cannot interleave them.
            if slots[0] <= 0:
                return "sweep_limit"
            slots[0] -= 1

        claim = await self._claim()
        if claim != "ok":
            return claim

        # The attempt counts the moment the unit is claimed: a failing upstream must not be
        # retried every tick.
        st.searches += 1
        st.last_search = clock
        st.direction = direction
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                brave_search.web_search(query, count=_FETCH_COUNT, freshness="pd"),
                timeout=float(settings.BRAVE_SEARCH_TIMEOUT_SECONDS) + _HARD_BOUND_SLACK,
            )
        except asyncio.CancelledError:
            # Fail toward UNDER-spend: the unit stays claimed (the search may have been billed).
            raise
        except brave_search.BraveSearchException as e:
            if getattr(e, "not_run", False):
                await self._refund()
            self._log(c.scope, f"upstream_{type(e).__name__}", 0, Counter(), started, st)
            return "error"
        except asyncio.TimeoutError:
            self._log(c.scope, "timeout", 0, Counter(), started, st)
            return "timeout"

        pages, drops = digest(raw, c.scope, c.name, now)
        if pages:
            if {p.url for p in pages} != {p.url for p in st.pages}:
                st.refreshed_at = now
            st.pages = tuple(pages)
            st.pages_direction = direction
        self._log(
            c.scope, "ok" if pages else "no_results", len(pages), drops, started, st,
            unknown_hosts=_unknown_hosts(pages),
        )
        return "ok" if pages else "no_results"

    async def _claim(self) -> str:
        """"ok" | "capped" | "budget_unavailable" — one unit of the day's bucket. Fails CLOSED."""
        cap = int(getattr(settings, "UPDATES_JUMP_WEB_SEARCH_DAILY_CAP", 0) or 0)
        today = budget_day()
        if cap <= 0 or self._capped_day == today:
            return "capped"
        try:
            count = await asyncio.to_thread(
                get_chat_budget_service().try_claim_turn, _BUDGET_BUCKET, cap,
            )
        except ChatBudgetUnavailable as e:
            logger.warning(
                "UPDATES_JUMP_WEB_SEARCH budget unavailable — failing CLOSED, no search: %s", e,
            )
            return "budget_unavailable"
        except Exception as e:  # noqa: BLE001 — a budget read never runs a search on a guess
            logger.warning(
                "UPDATES_JUMP_WEB_SEARCH budget raised (%s: %s) — failing CLOSED, no search",
                type(e).__name__, e,
            )
            return "budget_unavailable"
        if count == -1:
            self._capped_day = today
            logger.info("UPDATES_JUMP_WEB_SEARCH daily cap reached (limit=%d)", cap)
            return "capped"
        return "ok"

    async def _refund(self) -> None:
        try:
            await asyncio.to_thread(get_chat_budget_service().refund_turn, _BUDGET_BUCKET)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "UPDATES_JUMP_WEB_SEARCH unit refund failed (%s: %s)", type(e).__name__, e,
            )
        else:
            # A refunded unit reopens a bucket this process may have latched shut.
            self._capped_day = None

    @staticmethod
    def _log(
        scope: str, status: str, kept: int, drops: Counter, started: float, st: _ScopeDay,
        *, unknown_hosts: int = 0,
    ) -> None:
        newest = max((p.published_at for p in st.pages), default=None)
        age_min = (
            int((datetime.now(timezone.utc) - newest).total_seconds() // 60)
            if newest is not None else None
        )
        logger.info(
            "UPDATES_JUMP_WEB_SEARCH scope=%s status=%s kept=%d unknown_host=%d dropped=%s ms=%d "
            "searches_today=%d newest_age_min=%s",
            scope, status, kept, unknown_hosts, dict(drops),
            int((time.monotonic() - started) * 1000), st.searches, age_min,
        )


def _unknown_hosts(pages: Sequence[WebPage]) -> int:
    """How many kept pages come from a publisher we do not name — a count for the log (the
    card's Sources are shared with every watcher), never the hosts themselves."""
    unknown = 0
    for page in pages:
        safe = _safe_https_url(page.url)
        if safe is None or not _known_publisher(safe[1]):
            unknown += 1
    return unknown


_service: Optional[JumpWebSearchService] = None


def get_jump_web_search_service() -> JumpWebSearchService:
    global _service
    if _service is None:
        _service = JumpWebSearchService()
    return _service
