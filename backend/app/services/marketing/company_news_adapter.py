"""
Company Weekly (Drop 2a) — THE one FMP exemption of the marketing engine (contract D6).

Every other module under `app/services/marketing/` is FMP-free (`tests/test_marketing_import_boundary.py`):
this one does the I/O and nothing else. It reads FMP and the FMP-backed services, runs the pure
gates of `company_news_rules` over what came back, and hands out frozen RECORDS that carry no
price, market cap, profile CEO, image, URL or member identity (`tests/test_marketing_company_news_leaks.py`
pins the field list and plants canaries in every input key). Its only importer is
`MarketingScriptService._news_source_fn` (lazy, function-scoped — pinned too).

API (contract D6):

* `candidates(series, *, run_date, exclude, limit, deadline, deps)` → `Candidates(series, records,
  skip_reason, rejections)`: at most ``limit`` records in the series' rank order, so a candidate the
  template refuses falls through to the next. ``records == ()`` always comes with a `SKIP_REASONS`
  code (an honest "nothing qualified"); per-candidate refusals are counted in ``rejections``
  (`REJECTION_REASONS`; a ledger hit is counted as ``already_posted`` and never fetched further).
* Any upstream failure, a partial page, less than one second of budget left, or an unexpected
  error RAISES `MarketingNewsUnavailable(series, reason, detail)` — never a record built on
  partial data, never `FMPPartialPageException.partial`, never a retry (the FMP client retries
  5xx itself). An unexpected error is reason ``internal_error``, logged with ``logger.exception``.
  Two deliberate exceptions, both per CANDIDATE: the Form 4/A check's per-issuer read — when it
  fails, that row is dropped (``amendment_check_failed``, WARNING) and the week keeps the rest —
  and an earnings report's reporting-currency read (a failed read refuses that report as
  ``non_usd_reporter``, WARNING).
* Form 4 amendments (review rounds 7-9): ONE issuer-level rule, no exception — ANY amendment row
  filed since the window opened, on the issuer's symbol or issuer CIK (every share class, any
  reporter, any code), refuses every row of that issuer for the week (``amended_filing``), so a
  published row is never built from, or beside, an amendment of its company. The read that
  applies it to the codes the P walk never returns is per ISSUER (``companyCik`` = the profile's
  CIK: every share class and symbol spelling at once, review round 9), and must hold the person's
  newest-day lines and every walk row of the issuer filed since (else ``amendment_check_failed``;
  no profile CIK → the same). A CEO / CFO is published only while every title of theirs is a
  sitting officer's — an allow-list of title words, never a list of bad ones, applied to an
  ``other:`` text naming the role too; a director's ``other:`` text naming the seat must be a
  sitting director's (``role_uncertain``).
* 13F moves (review round 8) are chosen by dollar size with each kind's largest reserved; an
  exit carries its previous-quarter value (``prev_value_usd``, never drawn) so it ranks too.
* Review round 9 (2026-10-10), each fail-closed:
  - Form 4: a member of Congress is matched by the surname rule too (a legal first name, a
    nickname twin, an initial — `company_news_rules.is_congress_name`), and the whole PERSON is
    dropped on every symbol once any walk row, line or issuer-read row of theirs matches; a
    roster that is not fresh for the run date (`R.roster_fresh_for`: older than
    `R.ROSTER_MAX_AGE_DAYS`, or — review round 11 — not holding the sitting Congress) refuses both
    Form 4 series (`internal_error`, the chain falls back); a person some of
    whose in-window purchases of the issuer the row would not hold (a line a gate or the
    extractor dropped, another class, a line the walk missed) is refused (``partial_person``),
    never re-summed; two CEOs — or two CFOs — on one issuer are grouped BEFORE the role
    refusal, and the per-issuer read is checked for a co-officer or a second one.
  - 13F: a move whose issuer has option / note rows on either book is dropped
    (``move_has_options``, and leaves its kind's count when it is a new / exited holding); the
    subject is the EDGAR filer (`R.THIRTEEN_F_FILERS`; a natural-person filer is refused,
    ``filer_is_person``); the filer's chip only when its profile CIK is the filer's; the club
    path ties share classes against its stored previous book and the profiled names.
  - A held theme survives the next theme's failed lookups; a Congress count is refused beside a
    ticker-less purchase naming the company with its words run together (``count_uncertain``).
* `fetch_logo(symbol, *, max_bytes, timeout)` → ``(bytes, content_type)`` or None. Never raises,
  never validates (that is `logo_check`), logs a WARNING with the reason — never the URL.
* `COLLECTORS` — one collector per series whose code exists: the 2a four and, since Drop 2b,
  `congress_count`, `company_stakes`, `earnings` and `theme_explainer` (their keys join
  `selection.SHIPPED_SERIES` in the plumbing step; production runs only what MARKETING_NEWS_SERIES
  lists, the 2a four by default).
* Drop 2b, each fail-closed — a refused candidate costs one post; a wrong figure, role or claim
  about a named company, or a narrowed member of Congress, is the harm:
  - ``congress_count``: purchases (never an exchange or a sale) of stock (never an option, a bond
    or a fund) disclosed in the month before the run's month, both chambers together; the walk
    must provably reach past the month (the feeds are sorted by disclosure date, newest first —
    re-checked on every read) and a second read must agree; DISTINCT members ≥ 2, counted by
    salted per-call hashes of the identity fields — every identity field (``senateID`` included)
    is dropped at the source and no hash leaves the count — and refused when the identity
    fields cannot settle the count either way or an in-month purchase of the same company sits
    under another class or no usable symbol. No identity ever reaches a record, a log line, the
    memo or a fact sheet.
  - ``company_stakes``: Trillion Club stakes (published, `stake_problem`-valid, a named investee,
    a disclosed dollar figure and its basis — never a price); catalogue stakes allowed
    (`company_news_rules.STAKES_INCLUDE_CATALOGUE`), newest ``as_of`` first, one per ledger key.
  - ``earnings``: the previous seven ET days' calendar, one day per call (a day at the 4,000-row
    cap is unavailable, never ranked), EPS digit-shift and implausible gaps refused, revenue only
    inside [0.5, 1.5], one report per company (two that disagree are refused); profiled in rank
    order, batch by batch, until ``limit`` reports qualify (review round 9), and a report is kept
    only when its latest quarterly statement is REPORTED in USD (``non_usd_reporter`` otherwise,
    or when that read fails: the profile's currency is only the trading currency).
  - ``theme_explainer``: an Emerging Frontiers theme's name and ticker list only, every member
    through the company gate, the largest revenue segment of the first members (a member with no
    segment fact is still listed); the record carries the theme's full ticker count (the app
    card's, before any gate — ``theme_size``) so the copy never calls a gated list complete.

The memo (CLAUDE.md invariant 4, process-local only — the durable copy of what was used is the
run's `marketing_scripts.fact_sheet`): insider raw rows 30 min (the market-wide P walk, and the
per-issuer all-code rows of the Form 4/A check), profiles 10 min, a registry 13F
build 6 h, logo bytes 24 h, the club / whale / theme / stake lists and a club filer's stored
previous-quarter book (an exit's materiality) 10 min, a Congress month's identity-free counts and
each earnings-calendar day 30 min. Concurrent callers share one
upstream walk through ``_inflight`` futures (shielded; failures settled with
`fail_shared_future`). An exception is never memoized, and neither is an empty insider feed.

Logs never carry a member-of-Congress name, a refused raw reporting name, or a URL (exception
texts are scrubbed of URLs before they are logged or stored on the exception).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import secrets
import time
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import (
    Any, Awaitable, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set,
    Tuple,
)

import httpx

from app.config import settings
from app.database import get_supabase
from app.integrations.fmp import FMPUnavailableException, get_fmp_client
from app.services._insider_buys_common import (
    BAD_SYMBOLS as _BAD_FORM4_SYMBOLS, SYMBOL_RE as _FORM4_SYMBOL_RE, canonical_symbol as form4_symbol,
    extract_insider_buys, insider_role, normalize_cik, price_plausible, rank_buy_symbols,
)
from app.services._earnings_common import eps_digit_shift_suspect
from app.services._insider_common import (
    classify_insider_transaction, insider_reporter_key, is_common_stock, officer_title,
)
from app.services._whale_common import (
    is_13f_non_share_row, select_13f_comparison, thirteen_f_share_positions,
)
from app.services.company_facts_service import get_company_facts
# Pure at import (stdlib + market_hours + two pure leaves; checked 2026-10-10): the one-day-per-call,
# all-or-nothing calendar pager the Home Earnings Shockers card uses.
from app.services.earnings_window_service import fetch_calendar_days
from app.services.marketing import company_news_rules as R
from app.services.marketing import selection
from app.services.profit_power_service import get_profit_power_service
from app.services.revenue_breakdown_service import get_revenue_breakdown_service
from app.services.trillion_club import builder as _builder
from app.services.trillion_club import rules as _tc_rules
from app.services.trillion_club_service import get_trillion_club_service, stake_problem
from app.utils.inflight import fail_shared_future
from app.utils.supabase_async import sb_exec

logger = logging.getLogger(__name__)

# ── budgets, TTLs, caps ───────────────────────────────────────────────────────

#: A step never starts with less than this much of the caller's deadline left.
MIN_STEP_SECONDS = 1.0

INSIDER_RAW_TTL_SECONDS = 30 * 60.0
PROFILE_TTL_SECONDS = 10 * 60.0
BUILT_13F_TTL_SECONDS = 6 * 3600.0
LOGO_TTL_SECONDS = 24 * 3600.0
#: The club rows, the 13F whale rows and the Emerging Frontiers ticker lists.
LIST_TTL_SECONDS = 10 * 60.0
_MEMO_MAX_ENTRIES = 4096

#: Form 4 feed walk: 1000 rows ≈ 14 days of market-wide P rows, so one page covers the week.
INSIDER_PAGE_SIZE = 1000
INSIDER_MAX_PAGES = 3
#: Symbols (signals' rank order) that reach the profile stage per call.
INSIDER_PROFILE_CANDIDATES = 15
#: One person's buys above this share of the issuer's market cap are a unit error, not news.
INSIDER_MAX_CAP_SHARE = 0.10
#: The per-ISSUER Form 4/A check (review rounds 2, 7, 8 and 9): the market-wide walk asks FMP for
#: P rows only, so an amendment under another code (a re-code, a derivative-only 4/A) never
#: reaches it. The rows a week can publish (and a few spares) are re-read per ISSUER — by the
#: profile's issuer CIK (``companyCik``), so every share class, symbol spelling and blank symbol
#: of the company comes back at once, whatever the walk happened to show — with NO transaction
#: filter, and THE amendment rule is applied to that read: at most this many rows per call, one
#: page per issuer in the normal case (a short page ends an issuer's feed; a grant-season week of
#: every code still fits one 1000-row page), memoized like the walk and shared by both Form 4
#: series. FMP calls per Form 4 call: ≤ `INSIDER_MAX_PAGES` walk pages + ≤
#: `INSIDER_PROFILE_CANDIDATES` profile calls (one per symbol) + ≤ `INSIDER_AMENDMENT_MAX_SYMBOLS`
#: × `INSIDER_AMENDMENT_MAX_PAGES` per-issuer pages (normally one page per row).
INSIDER_AMENDMENT_MAX_SYMBOLS = R.INSIDER_MAX_ROWS + 3
INSIDER_AMENDMENT_PAGE_SIZE = INSIDER_PAGE_SIZE
INSIDER_AMENDMENT_MAX_PAGES = 2

#: Registry 13F builds per call that actually hit FMP (a memo hit is free; a refused book —
#: `FilingRefused` — does not count).
MAX_LIVE_13F_BUILDS = 2
#: This many `FilingUnavailable` (or failed date lists) in one call → the series is unavailable.
THIRTEEN_F_MAX_UNAVAILABLE = 2
#: Moves presented (and profiled) per filing before the final cut to `THIRTEEN_F_MAX_MOVES`.
THIRTEEN_F_MAX_PROFILED = 10
#: Both cuts are by dollar size (`_select_moves`, review round 8), with each kind's largest this
#: many moves reserved first, so a large increase or exit is never cut by kind order.
THIRTEEN_F_KIND_RESERVE = 3
#: An increase / decrease smaller than this share of the previous count is not shown.
THIRTEEN_F_MIN_CHANGE = 0.10
#: A newly reported or no-longer-reported move is shown only when its value reaches
#: max(`THIRTEEN_F_MATERIAL_MIN_USD`, `THIRTEEN_F_MATERIAL_SHARE` × the filer's reported total)
#: (review round 7, live L1: Berkshire's 2026-Q2 13F led with 3,564 D.R. Horton shares, $580,504
#: in a $299B book). An exit is valued on the PREVIOUS quarter's book (a registry filer's
#: previous extract; a club filer's stored previous-quarter build); an exit whose previous value
#: is unknown is below the floor — fail closed.
THIRTEEN_F_MATERIAL_MIN_USD = 5_000_000.0
THIRTEEN_F_MATERIAL_SHARE = 0.0005

#: Money Map, per call: candidates past the ledger check that ended in a record or in an
#: UPSTREAM gap (`_MONEY_MAP_UPSTREAM_GAPS`), the deterministic content refusals (a gross
#: segment stack, a thin breakdown, an excluded sector, a statement mismatch …) — counted apart
#: only while NO record is held, so a pool whose first seeds are always refused still reaches a
#: publishable company; once one is held the loop stops at `MONEY_MAP_MAX_ATTEMPTS` of all three
#: together (the old cap: more reads only risk the budget) — and breakdown reads in total. Out of
#: budget with a record held ends the loop with the records found (never discarded).
MONEY_MAP_MAX_ATTEMPTS = 3
MONEY_MAP_MAX_CONTENT_REJECTIONS = 8
MONEY_MAP_MAX_LOOKUPS = 12
#: `_money_map_record` reasons that mean the data was not there (a degraded breakdown, profit
#: power or statement row, a facts miss) — the rest are content refusals.
_MONEY_MAP_UPSTREAM_GAPS = frozenset({"money_map_degraded", "profile_missing"})
#: The fiscal year's statement must be dated within ~18 months of the run.
MONEY_MAP_STALE_DAYS = 548
#: Statement-vs-breakdown agreement (revenue, net income), relative.
MONEY_MAP_STATEMENT_TOLERANCE = 0.005
#: |100·net/revenue − profit power's net margin| above this (points) → inconsistent.
MONEY_MAP_NET_MARGIN_TOLERANCE_PT = 0.5
#: A gross / operating bar is drawn only when it matches profit power within this (points).
MONEY_MAP_BAR_TOLERANCE_PT = 1.0
#: A map whose "Other" (unnamed and folded revenue) is larger than its largest named segment, or
#: more than this share of revenue, explains too little of the company (review round 7, live L2:
#: Microsoft FY2026 drew "Other" at $143.7B, 43% of revenue, above its largest segment) —
#: refused `money_map_mostly_other`, a content refusal.
MONEY_MAP_MAX_OTHER_SHARE = 0.30
_MONEY_MAP_REST_NAMES = frozenset({"other", "unallocated"})
_SEGMENT_NAME_RE = re.compile(r"[A-Za-z0-9 &.,'+/-]{2,40}")

# ── Drop 2b bounds (each series' FMP / service calls per `candidates()` call) ───
#
# congress_count: 2 chambers × 8 pages (2000 rows); a chamber that walk did not cover is re-read
#   at 30 pages (7500); then EVERY chamber's covering pages are read once more (the confirming
#   read, at the size that covered it); + the profile "batch", which is one call per symbol (≤
#   `CONGRESS_PROFILE_CANDIDATES`). Normally 2×8 + 2×8 = 32 page requests + ≤ 10 profile calls;
#   at most 2×8 + 2×30 + 2×30 = 136 page requests + ≤ 10 profile calls = 146 (review round 9:
#   the old "107" left out the confirming reads at 7500 — pinned by a page-counting test).
# company_stakes: 0 FMP calls for data (the club group, its rows and one stakes read), + 1
#   profile batch for at most `STAKES_PROFILE_CANDIDATES` stakes' investor and investee.
# earnings: 7 one-day calendar calls (4 at a time, all-or-nothing) + profile batches of
#   `EARNINGS_PROFILE_CANDIDATES` walked in rank order until ``limit`` reports qualify (≤
#   `EARNINGS_MAX_PROFILES` profile calls) + one latest-quarter income statement per report that
#   reaches the record stage (its reporting currency; ≤ `EARNINGS_MAX_CURRENCY_READS`).
# theme_explainer: 1 themes read; per theme evaluated (≤ `THEME_MAX_EVALUATED`) 1 profile batch
#   and ≤ `THEME_SEGMENT_LOOKUPS` revenue breakdowns (the service's own two-tier cache first).

#: The two walk sizes of the Congress feeds (rows per chamber; the client fetches 250-row pages
#: in parallel, at most 30). The 2026-10-09 probe: Senate 500 rows ≈ 2.5 months, House 500 rows ≈
#: 1 month — so 2000 covers a disclosure month in the normal case and 7500 is the one retry.
CONGRESS_WALK_LIMITS: Tuple[int, ...] = (2000, 7500)
#: A chamber's walk covers the disclosure month only when its LAST row (the feed is sorted by
#: disclosure date, newest first — verified 2026-10-09, and re-checked on every read) is dated
#: more than this many days before the month's first day.
CONGRESS_COVERAGE_MARGIN_DAYS = 7
#: The month is counted only once its last day is at least this many days behind the run.
CONGRESS_DUE_DAYS = 7
#: Symbols (members desc, then symbol) that reach the profile stage per call.
CONGRESS_PROFILE_CANDIDATES = 10
#: The aggregated counts (never a row, never an identity) are memoized this long.
CONGRESS_TTL_SECONDS = 30 * 60.0
_CONGRESS_CHAMBERS: Tuple[Tuple[str, str], ...] = (
    ("senate", "get_senate_latest"), ("house", "get_house_latest"))
#: Member identity — dropped at the source (main-session decision 3, 2026-10-09): `_scrub_congress`
#: keeps only salted, per-call hashes of these, and those never leave `_congress_month`.
CONGRESS_IDENTITY_FIELDS: FrozenSet[str] = frozenset({
    "firstName", "lastName", "first_name", "last_name", "office", "senator", "representative",
    "district", "owner", "link", "party", "state", "comment", "senateID",
})
_IDENT_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
_IDENT_WORD_RE = re.compile(r"[a-z0-9]+")
_IDENT_MAX = 200
#: An asset description is kept (folded, capped) only to find an in-month purchase of the same
#: company under another or no symbol.
_DESC_MAX = 200
#: Congress asset types that are clearly NOT a share of stock: such a purchase is simply not counted.
#: Any OTHER non-"stock" type ("", "Other", "Other Securities", "REIT" …) on a symbol makes that
#: symbol's count uncertain — the row may be the same stock — so the symbol is refused.
_CONGRESS_NON_STOCK_ASSETS = frozenset({
    "stock option", "corporate bond", "municipal security", "government securities", "mutual fund",
    "etf", "cryptocurrency", "non-public stock",
})
#: A "stock" row whose description names another instrument (FMP's asset type is the filer's
#: box, not a check): the symbol's count is uncertain. Accepted over-block: "Option Care Health".
_NON_COMMON_DESC_RE = re.compile(
    r"\b(?:options?|calls?|puts?|warrants?|preferred|pfd|notes?|bonds?|debentures?|units?|rights?)\b")
#: Words a company name shares with too many descriptions to identify it.
_GENERIC_NAME_WORDS = frozenset({
    "the", "and", "of", "inc", "corp", "corporation", "co", "company", "companies", "holdings",
    "holding", "group", "ltd", "limited", "plc", "class", "common", "stock", "shares",
})

#: Earnings: report days ``[run_date - 7, run_date - 1]``, one ET day per calendar call.
EARNINGS_WINDOW_DAYS = 7
#: FMP's silent row cap: a one-day answer this long is probably TRUNCATED → the series is
#: unavailable (`earnings_window_service` only logs it; a post must not rank a cut list).
EARNINGS_TRUNCATION_ROWS = 4000
EARNINGS_DAY_TTL_SECONDS = 30 * 60.0
#: Reports (|actual − estimate| / |estimate| desc) profiled per batch. Batches are walked in rank
#: order until ``limit`` reports qualify (review round 9: one cut at the top 20 let a peak week's
#: sub-$2B gaps crowd out every large cap), at most `EARNINGS_MAX_PROFILES` profiles per call; the
#: kept reports are ordered by the full rank key at the end (a later batch can only tie on the
#: gap ratio, and then the market cap decides).
EARNINGS_PROFILE_CANDIDATES = 20
EARNINGS_MAX_PROFILES = 60
#: Latest-quarter income statements read per call for the REPORTING currency (review round 9): the
#: profile's ``currency`` is the TRADING currency, and a USD-traded ordinary share (RY, TD, RACE,
#: SPOT …) reports EPS and revenue in CAD / EUR — figures the template would print with "$".
#: One read per report that reaches the record stage, in waves of the reports still needed.
EARNINGS_MAX_CURRENCY_READS = 20
#: |actual − estimate| above this multiple of |estimate| is a feed error, not a result.
EARNINGS_MAX_GAP_MULTIPLE = 10.0

#: Company stakes: the stakes read's row cap (the club service's `_MAX_STAKE_ROWS`); a full page
#: may be cut, so it is unavailable, never a partial catalogue.
STAKES_READ_LIMIT = 2000
#: Unposted stakes (rank order) whose investor and investee reach the profile stage per call.
STAKES_PROFILE_CANDIDATES = 8
_STAKES_TABLE = "trillion_club_stakes"
#: Explicit columns: the club's wire shape lacks ``id`` (the ledger key) and ``created_at`` (new
#: vs catalogue); ``source_url`` is read for `stake_problem` and never leaves this module.
_STAKE_COLUMNS = (
    "id,created_at,company_slug,kind,investee_name,investee_us_symbol,local_listing,ownership_pct,"
    "ownership_basis,disclosed_value_usd,value_basis,as_of,source_title,source_url,"
    "source_confidence,published,listed_since,background,verified_on,sort_order"
)

#: Theme explainer: themes evaluated past the ledger and the cheap gates per call (each costs a
#: profile batch and segment lookups), the members whose largest segment is looked up (theme
#: order; a later member is still listed, without a segment fact), and the lookups in flight.
THEME_MAX_EVALUATED = 2
THEME_SEGMENT_LOOKUPS = 12
THEME_LOOKUP_CONCURRENCY = 6
#: A segment fact's fiscal year must be this recent (years before the run's year).
THEME_FACT_MAX_AGE_YEARS = 2
#: Segments may add up to at most this much more than revenue for a share to be stated.
THEME_SEGMENT_SUM_TOLERANCE = 0.005
_THEME_COLUMNS = "slug,title,tickers,blocked_tickers,tickers_as_of,sort_order,is_active"
_THEME_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")

#: The ONLY logo URL `fetch_logo` will request, built from the validated symbol and compared
#: byte for byte with the profile's ``image``.
LOGO_URL_TEMPLATE = "https://images.financialmodelingprep.com/symbol/{symbol}.png"

_CLUB_COMPANIES_TABLE = "trillion_club_companies"
_CLUB_FILINGS_TABLE = "trillion_club_filings"
_WHALES_TABLE = "whales"
_THEMES_TABLE = "trending_themes"
#: `whales.lifecycle_status` values of a filer that is still read. The column is curated as ""
#: or "inactive" (migration 145; `sync_whale_registry.py`), so anything else — "inactive",
#: "dormant", a value added later — is skipped, never read (fail closed).
_ACTIVE_LIFECYCLE = frozenset({"", "active"})
#: Logo bytes as they came off the wire, never decoded: a compressed body inflates past any
#: cap before a decoded-size check could refuse it.
_IDENTITY_ENCODINGS = frozenset({"", "identity"})

# ── public types ──────────────────────────────────────────────────────────────


class MarketingNewsUnavailable(Exception):
    """An upstream failure (or budget exhaustion, or an unexpected error) while collecting one
    series — never "nothing qualified". ``reason`` ∈ `company_news_rules.UNAVAILABLE_REASONS`.

    ``reason`` defaults to ``internal_error`` only so the class is constructible from a single
    argument (the classifier walk in `tests/test_marketing_script_flow.py` builds ``cls("x")``);
    every raise in this module names its reason. ``detail`` is scrubbed of URLs."""

    def __init__(self, series: str, reason: str = "internal_error", detail: str = "") -> None:
        if reason not in R.UNAVAILABLE_REASONS:
            raise ValueError(f"unknown unavailable reason {reason!r}")
        self.series = str(series)
        self.reason = reason
        self.detail = _scrub(detail)
        super().__init__(f"{self.series}: {reason}" + (f" ({self.detail})" if self.detail else ""))


@dataclass(frozen=True)
class Candidates:
    """One series' answer. ``records`` is at most ``limit`` long, in rank order; empty only
    with a `SKIP_REASONS` ``skip_reason``. ``rejections`` counts per-candidate refusals."""

    series: str
    records: Tuple[Any, ...]
    skip_reason: Optional[str]
    rejections: Mapping[str, int]

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple) or any(not isinstance(r, R.RECORD_TYPES) for r in self.records):
            raise ValueError("Candidates.records must be a tuple of news records")
        if self.records and self.skip_reason is not None:
            raise ValueError("Candidates with records carry no skip_reason")
        if not self.records and self.skip_reason not in R.SKIP_REASONS:
            raise ValueError(f"empty Candidates need a SKIP_REASONS code, got {self.skip_reason!r}")
        bad = sorted(k for k in self.rejections if k not in R.REJECTION_REASONS)
        if bad:
            raise ValueError(f"unknown rejection reason(s) {bad}")
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in self.rejections.values()):
            raise ValueError("rejection counts must be non-negative ints")


@dataclass(frozen=True)
class NewsDeps:
    """Injection seam (tests); None → the production singleton, resolved only when a series
    needs it. ``facts`` is a ``get_company_facts``-shaped coroutine function; ``sb`` a Supabase
    client. Beyond the contract: ``actions`` (the corporate-actions primitive the 13F builder
    uses for splits; None → the builder's own default) and ``http`` (an httpx transport for
    `fetch_logo`; None → the network)."""

    fmp: Any = None
    club: Any = None
    revenue: Any = None
    profit: Any = None
    facts: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None
    sb: Any = None
    monotonic: Callable[[], float] = time.monotonic
    actions: Any = None
    http: Any = None


# ── helpers: scrubbing, numbers, dates ────────────────────────────────────────

_URL_RE = re.compile(r"(?:https?|wss?)://\S+", re.IGNORECASE)
_KEY_RE = re.compile(r"(?i)(api[_-]?key|token|secret)=\S+")


def _scrub(text: Any, limit: int = 300) -> str:
    """One line, no URL, no key=value secret, length-capped — safe for a log or an exception."""
    s = _URL_RE.sub("<url>", str(text or ""))
    s = _KEY_RE.sub(r"\1=<redacted>", s)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _describe(e: BaseException) -> str:
    return _scrub(f"{type(e).__name__}: {e}")


def _num(v: Any) -> Optional[float]:
    """A finite non-bool number as float, else None (strings are not numbers here)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _day(v: Any) -> Optional[date]:
    """The ``YYYY-MM-DD`` prefix of a str (or a date) as a date, else None."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and len(v) >= 10:
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _sym_key(raw: Any) -> str:
    """Upper-case, "." → "-": the join key between FMP 13F symbols and our canonical form."""
    return str(raw or "").strip().upper().replace(".", "-")


# ── the memo + in-flight dedup ────────────────────────────────────────────────

_MISS = object()
_MEMO: Dict[Tuple[Any, ...], Tuple[float, Any]] = {}
_INFLIGHT: Dict[Tuple[Any, ...], "asyncio.Future[Any]"] = {}
#: Leader tasks keep running when a caller's deadline expires (the next call reads the memo);
#: held here so they are not garbage-collected mid-flight.
_LEADS: Set["asyncio.Task[Any]"] = set()


def _memo_get(key: Tuple[Any, ...]) -> Any:
    hit = _MEMO.get(key)
    if hit is None:
        return _MISS
    expires, value = hit
    if time.monotonic() >= expires:
        _MEMO.pop(key, None)
        return _MISS
    return value


def _memo_put(key: Tuple[Any, ...], value: Any, ttl: float) -> None:
    if ttl <= 0:
        return
    now = time.monotonic()
    if len(_MEMO) >= _MEMO_MAX_ENTRIES:
        for k in [k for k, (exp, _v) in _MEMO.items() if exp <= now]:
            _MEMO.pop(k, None)
        while len(_MEMO) >= _MEMO_MAX_ENTRIES:
            _MEMO.pop(min(_MEMO, key=lambda k: _MEMO[k][0]), None)
    _MEMO[key] = (now + ttl, value)


def clear_memo() -> None:
    """Drop every memoized value and in-flight future (tests; never needed in production)."""
    _MEMO.clear()
    _INFLIGHT.clear()


async def _shared(key: Tuple[Any, ...], ttl: float, fetch: Callable[[], Awaitable[Any]], *,
                  keep: Callable[[Any], bool] = lambda _v: True) -> Any:
    """Memo hit, else join the in-flight fetch for ``key``, else lead it. The leader runs as its
    own task, so a caller whose deadline expires (``wait_for`` cancels it) leaves the fetch
    running and the next call finds it memoized. A result is memoized only when ``keep`` says
    so; an exception never is."""
    hit = _memo_get(key)
    if hit is not _MISS:
        return hit
    loop = asyncio.get_running_loop()
    fut = _INFLIGHT.get(key)
    if fut is None or fut.get_loop() is not loop:
        fut = loop.create_future()
        _INFLIGHT[key] = fut
        task = loop.create_task(_lead(key, ttl, fetch, fut, keep), name=f"marketing_news:{key[0]}")
        _LEADS.add(task)
        task.add_done_callback(_LEADS.discard)
    return await asyncio.shield(fut)


async def _lead(key: Tuple[Any, ...], ttl: float, fetch: Callable[[], Awaitable[Any]],
                fut: "asyncio.Future[Any]", keep: Callable[[Any], bool]) -> None:
    try:
        value = await fetch()
    except asyncio.CancelledError:
        fail_shared_future(fut, RuntimeError(f"company news fetch {key[0]!r} was cancelled"))
        raise
    except Exception as e:  # noqa: BLE001 — handed to every joiner; never memoized
        fail_shared_future(fut, e)
    else:
        if keep(value):
            _memo_put(key, value, ttl)
        if not fut.done():
            fut.set_result(value)
    finally:
        if _INFLIGHT.get(key) is fut:
            del _INFLIGHT[key]


# ── the per-call context ──────────────────────────────────────────────────────


class _Ctx:
    """One `candidates()` call: its inputs, resolved dependencies, budget and counters."""

    def __init__(self, series: str, run_date: date, exclude: FrozenSet[str], limit: int,
                 deadline: float, deps: NewsDeps) -> None:
        self.series = series
        self.run_date = run_date
        self.exclude = exclude
        self.limit = limit
        self.deadline = deadline
        self.deps = deps
        self.rejections: Counter = Counter()
        self.considered = 0
        self.profiles_requested = 0
        self.profiles_returned = 0
        self._resolved: Dict[str, Any] = {}

    # Production singletons are resolved lazily, so a series only touches what it reads.
    def _dep(self, name: str, factory: Callable[[], Any]) -> Any:
        if name not in self._resolved:
            given = getattr(self.deps, name)
            self._resolved[name] = given if given is not None else factory()
        return self._resolved[name]

    @property
    def fmp(self) -> Any:
        return self._dep("fmp", get_fmp_client)

    @property
    def club(self) -> Any:
        return self._dep("club", get_trillion_club_service)

    @property
    def revenue(self) -> Any:
        return self._dep("revenue", get_revenue_breakdown_service)

    @property
    def profit(self) -> Any:
        return self._dep("profit", get_profit_power_service)

    @property
    def facts(self) -> Callable[..., Awaitable[Dict[str, Any]]]:
        return self._dep("facts", lambda: get_company_facts)

    @property
    def sb(self) -> Any:
        return self._dep("sb", get_supabase)

    def reject(self, reason: str, n: int = 1) -> None:
        if reason not in R.REJECTION_REASONS:
            raise ValueError(f"unknown rejection reason {reason!r}")
        self.rejections[reason] += n

    def left(self) -> float:
        return self.deadline - self.deps.monotonic()

    async def step(self, stage: str, factory: Callable[[], Awaitable[Any]], *, reason: str,
                   passthrough: Tuple[type, ...] = ()) -> Any:
        """Await one upstream step inside the remaining budget. Less than `MIN_STEP_SECONDS`
        left, or the budget running out mid-step → ``budget_exhausted``. Any other exception →
        ``reason`` (an upstream failure), unless it is one of ``passthrough``."""
        left = self.left()
        if left < MIN_STEP_SECONDS:
            raise MarketingNewsUnavailable(self.series, "budget_exhausted",
                                           f"stage={stage} left={left:.1f}s")
        try:
            return await asyncio.wait_for(factory(), timeout=left)
        except MarketingNewsUnavailable as e:
            if e.series != self.series:      # raised for another series sharing the fetch
                raise MarketingNewsUnavailable(self.series, e.reason, e.detail) from e
            raise
        except asyncio.TimeoutError as e:
            raise MarketingNewsUnavailable(self.series, "budget_exhausted",
                                           f"stage={stage} timed out after {left:.1f}s") from e
        except asyncio.CancelledError:
            raise
        except passthrough:
            raise
        except Exception as e:  # noqa: BLE001 — an upstream failure, typed below
            raise MarketingNewsUnavailable(self.series, reason, f"stage={stage}; {_describe(e)}") from e

    def check_profiles(self) -> None:
        """Every requested profile missing → the profile source is down, not "nothing qualified"."""
        if self.profiles_requested and not self.profiles_returned:
            raise MarketingNewsUnavailable(
                self.series, "profiles_unavailable",
                f"0 of {self.profiles_requested} requested profile(s) returned")

    def done(self, records: Sequence[Any], skip_reason: Optional[str]) -> Candidates:
        records = tuple(records)[: self.limit]
        return Candidates(
            series=self.series,
            records=records,
            skip_reason=None if records else skip_reason,
            # A fresh plain dict per call (JSON-serialisable as is), sorted by reason.
            rejections=dict(sorted(self.rejections.items())),
        )


# ── shared fetchers ───────────────────────────────────────────────────────────


async def _fetch_profiles(fmp: Any, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """``{SYMBOL: profile}`` for the symbols FMP returned (batch of ≤ 50 per call); each is
    memoized on its own. The batch call itself swallows per-symbol failures (→ missing)."""
    want = list(dict.fromkeys(symbols))
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(want), 50):
        chunk = want[i:i + 50]
        got = await fmp.get_company_profiles_batch(chunk)
        if not isinstance(got, list):
            raise FMPUnavailableException(f"profile batch returned {type(got).__name__}, not a list")
        for p in got:
            if not isinstance(p, dict):
                continue
            sym = R.canonical_symbol(p.get("symbol"))
            if sym in chunk and sym not in out:
                out[sym] = p
                _memo_put(("profile", sym), p, PROFILE_TTL_SECONDS)
    return out


async def _profiles(ctx: _Ctx, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    want = [s for s in dict.fromkeys(symbols) if s]
    out: Dict[str, Dict[str, Any]] = {}
    missing: List[str] = []
    for s in want:
        hit = _memo_get(("profile", s))
        if hit is _MISS:
            missing.append(s)
        else:
            out[s] = hit
    if missing:
        key = ("profiles", tuple(sorted(missing)))
        got = await ctx.step("profiles", lambda: _shared(key, 0, lambda: _fetch_profiles(ctx.fmp, missing)),
                             reason="profiles_unavailable")
        out.update({s: p for s, p in got.items() if s in missing})
    ctx.profiles_requested += len(want)
    ctx.profiles_returned += sum(1 for s in want if s in out)
    return out


async def _rows(ctx: _Ctx, stage: str, key: Tuple[Any, ...], build_query: Callable[[Any], Any], *,
                reason: str) -> List[Dict[str, Any]]:
    """A memoized Supabase read (10 min), off the event loop; dict rows only."""
    async def fetch() -> List[Dict[str, Any]]:
        res = await sb_exec(build_query(ctx.sb))
        data = getattr(res, "data", None)
        if not isinstance(data, list):
            raise RuntimeError(f"{stage}: Supabase returned {type(data).__name__}, not a list")
        return [r for r in data if isinstance(r, dict)]

    return await ctx.step(stage, lambda: _shared(key, LIST_TTL_SECONDS, fetch), reason=reason)


async def _club_rows(ctx: _Ctx, *, reason: str) -> Dict[str, Dict[str, Any]]:
    rows = await _rows(
        ctx, "club_companies", ("club_rows",),
        lambda sb: sb.table(_CLUB_COMPANIES_TABLE)
        .select("slug,ciks,card_kind,use_13f,detail_symbol,published")
        .eq("published", True).limit(500),
        reason=reason,
    )
    return {r["slug"]: r for r in rows if isinstance(r.get("slug"), str)}


@lru_cache(maxsize=1)
def _registry_order() -> Dict[str, int]:
    """CIK → index of the 13F filers in `data/whale_registry.json` (the curated order). An
    unreadable registry yields {} (no registry filers — fail closed), logged at ERROR."""
    try:
        rows = json.loads(Path(R.WHALE_REGISTRY_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error("company news: whale registry unreadable (%s) — no registry 13F filers", _describe(e))
        return {}
    order: Dict[str, int] = {}
    for i, row in enumerate(rows if isinstance(rows, list) else ()):
        if isinstance(row, dict) and row.get("data_source") == "13f":
            cik = R.cik10(row.get("cik"))
            if cik and cik not in order:
                order[cik] = i
    return order


# ── ceo_buys / insider_buys ───────────────────────────────────────────────────


async def _walk_insider(fmp: Any, window_start: date) -> List[Dict[str, Any]]:
    rows = await fmp.get_insider_trades_since(
        window_start.isoformat(), transaction_type="P-Purchase",
        page_size=INSIDER_PAGE_SIZE, max_pages=INSIDER_MAX_PAGES,
    )
    if not isinstance(rows, list):
        raise FMPUnavailableException(f"insider feed returned {type(rows).__name__}, not a list")
    return rows


async def _walk_issuer(fmp: Any, cik: str, window_start: date) -> List[Dict[str, Any]]:
    """One ISSUER's insider rows filed since the window opened — every share class, symbol spelling
    and blank symbol under its CIK, every transaction code and form (the fail-closed per-issuer
    feed of the Form 4/A check; a short page is its normal end). ``cik`` is the 10-digit form."""
    rows = await fmp.get_insider_trades_since(
        window_start.isoformat(), company_cik=cik,
        page_size=INSIDER_AMENDMENT_PAGE_SIZE, max_pages=INSIDER_AMENDMENT_MAX_PAGES,
    )
    if not isinstance(rows, list):
        raise FMPUnavailableException(f"per-issuer insider feed returned {type(rows).__name__}, not a list")
    return rows


# ── THE amendment rule (review rounds 7-8) ────────────────────────────────────
#
# Rounds 1-6 tried to tie each Form 4/A to the lines it replaces (a one-to-one matcher, relabel
# and unit-offering excuses, re-code shapes, a late-filing key, a lag exemption …) and every
# round's exception opened a new hole. Round 7 refused the amended PERSON — and round 8's probes
# showed the person tie is itself an exception: a 4/A filed under the issuer's other share class,
# with an unusable symbol, by a joint co-reporter entity, or naming the person in another word
# order never met the person's key. Refusing costs ONE candidate; a wrong figure or role about a
# named company is the harm. So the rule is per ISSUER and has no exception: ANY amendment row
# filed since the window opened, on the issuer's symbol or its issuer CIK (every share class),
# refuses every row of that issuer for the week (`amended_filing`). A published row is therefore
# never built from, or beside, an amendment of its company.


def _is_amendment_row(row: Mapping[str, Any]) -> bool:
    """An amendment row: its ``formType`` carries "/A" ("4/A", "3/A", "5/A", " 4/a "), read exactly
    as `extract_insider_buys` reads it (stripped, upper-cased; a missing form is a plain "4")."""
    form = row.get("formType")
    return "/A" in (str(form).strip().upper() if form not in (None, "") else "4")


def _row_symbol(row: Mapping[str, Any]) -> str:
    """A raw Form 4 row's symbol in our join form ("BRK.B" → "BRK-B"), or "" when it has none."""
    raw = row.get("symbol")
    return form4_symbol(raw.strip()) if isinstance(raw, str) else ""


def _filed_since(row: Mapping[str, Any], window_start: date) -> bool:
    """Filed on or after ``window_start``, with NO upper bound (the reads run through the run day,
    so a run-day filing counts). An unreadable filing date counts as in the window (fail closed)."""
    filed = _day(row.get("filingDate"))
    return filed is None or filed >= window_start


def _amended_issuers(rows: Iterable[Any], window_start: date) -> Tuple[Set[str], Set[str]]:
    """``(symbols, issuer CIKs)`` refused for the week by the walk's amendment rows.

    Every amendment row (`_is_amendment_row`) filed since ``window_start`` (`_filed_since`) — any
    transaction code, any security, any line, any reporter — adds its symbol and its issuer CIK
    (``companyCik``; a blank- or junk-symbol row still names its issuer this way). Then one hop
    over the rows' own (symbol, issuer CIK) pairs: every symbol the rows show under an amended
    CIK (the issuer's other share classes), and every CIK they show under an amended symbol (an
    amendment row that carries only the symbol). An amendment naming neither is logged and can
    refuse no one."""
    syms: Set[str] = set()
    ciks: Set[str] = set()
    pairs: List[Tuple[str, Optional[str]]] = []
    anonymous = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym, cik = _row_symbol(r), normalize_cik(r.get("companyCik"))
        pairs.append((sym, cik))
        if not _is_amendment_row(r) or not _filed_since(r, window_start):
            continue
        if sym:
            syms.add(sym)
        if cik:
            ciks.add(cik)
        if not sym and not cik:
            anonymous += 1
    if anonymous:
        logger.info("company news: %d amendment row(s) name no symbol and no issuer — tied to no one",
                    anonymous)
    seed_syms, seed_ciks = frozenset(syms), frozenset(ciks)
    for sym, cik in pairs:
        if sym and cik in seed_ciks:
            syms.add(sym)
        if cik and sym in seed_syms:
            ciks.add(cik)
    return syms, ciks


def _issuer_amended(b: Any, syms: Set[str], ciks: Set[str]) -> bool:
    """Is this buy line's issuer — its symbol, or the issuer CIK its line names — refused?"""
    return b.symbol in syms or (b.company_cik is not None and b.company_cik in ciks)


_NAME_WORD_RE = re.compile(r"[a-z0-9]+")
#: A reporting name longer than this is junk; its order-free key is not built.
_REPORTER_NAME_MAX = 200


def _identity_keys(cik: Any, name: Any) -> Set[str]:
    """Every identity key a reporter carries: `insider_reporter_key` of the CIK alone and of the
    name alone, plus an ORDER-FREE key of the name's words ("COHEN RYAN", "Cohen, Ryan" and
    "Ryan Cohen" — which `normalize_insider_name` reads last-first — are one person). Used to find
    the person's OTHER rows (the role check and the per-issuer read's coverage); amendments never
    match on it any more. Matching more rows only checks more titles, so two people whose names
    share every word are an accepted over-block."""
    keys = {insider_reporter_key({"reportingCik": cik}), insider_reporter_key({"reportingName": name})}
    if isinstance(name, str) and len(name) <= _REPORTER_NAME_MAX:
        words = sorted(_NAME_WORD_RE.findall(name.lower()))
        if len(words) >= 2:
            keys.add("words:" + " ".join(words))
    keys.discard("")
    return keys


def _line_keys(b: Any) -> Set[str]:
    """A buy line's identity keys: its reporter key (CIK first, as the extractor keys it) plus the
    keys of its reporting name."""
    return {b.reporter} | _identity_keys(None, b.name_raw)


def _row_keys(r: Mapping[str, Any]) -> Set[str]:
    return _identity_keys(r.get("reportingCik"), r.get("reportingName"))


def _person_keys(lines: Sequence[Any]) -> Set[str]:
    """A week row's PERSON, across issuers (review round 10): every identity key of its lines
    (`_line_keys` — the reporting CIK's key, the name's key and its order-free word key), plus the
    order-free key of the name's words WITHOUT single-letter initials. That last key mirrors the
    template: `news_templates._person_key` reads "Arthur H. Penn" and "Arthur J. Penn" as one
    person (a middle initial ignored) and refuses the WHOLE week as `record_invalid` — so two such
    rows under different CIKs are tied here first and the larger one kept. Two rows sharing any key
    are one person; two people whose names share every word (initials aside) are an accepted
    over-block (one row fewer, never a wrong count). A name with fewer than two multi-letter words
    gets no such key (too broad to tie on)."""
    keys: Set[str] = set()
    for b in lines:
        keys |= _line_keys(b)
        name = b.name_raw
        if isinstance(name, str) and len(name) <= _REPORTER_NAME_MAX:
            words = sorted(w for w in _NAME_WORD_RE.findall(name.lower()) if len(w) > 1)
            if len(words) >= 2:
                keys.add("names:" + " ".join(words))
    keys.discard("")
    return keys


def _shares_of(v: Any) -> Optional[float]:
    """A row's share count as the extractor reads it (`_insider_buys_common._finite`): a finite
    non-bool number or numeric string, else None."""
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


#: One coverage target of the issuer read: (symbol, identity keys, filing day, shares).
_Target = Tuple[str, Set[str], date, float]


def _coverage_targets(symbol: str, lines: Sequence[Any], issuer: str,
                      walk_rows: Sequence[Any]) -> Optional[List[_Target]]:
    """What an up-to-date issuer read must hold (review round 9): the person's published lines
    from their NEWEST filing day, and every walk row of the issuer (its CIK, any symbol) filed on
    or after that day. An amendment is always filed after the line it amends, so a read that
    stops before the newest line the walk saw is stale exactly where a 4/A would sit — "any one
    line" was not enough. A walk row with no readable day, size or identity cannot be matched and
    is not a target; the person's own lines always are. None when the lines carry no filing day
    (fail closed)."""
    days = [d for d in (_day(b.filing_date) for b in lines) if d is not None]
    if not days:
        return None
    newest = max(days)
    person = set().union(*(_line_keys(b) for b in lines))
    targets: List[_Target] = [(b.symbol, person, newest, b.shares)
                              for b in lines if _day(b.filing_date) == newest]
    own = {(sym, filed, round(shares, 4)) for sym, _k, filed, shares in targets}
    for r in walk_rows:
        if not isinstance(r, dict) or normalize_cik(r.get("companyCik")) != issuer:
            continue
        filed, shares, keys = _day(r.get("filingDate")), _shares_of(r.get("securitiesTransacted")), _row_keys(r)
        if filed is None or filed < newest or shares is None or not keys:
            continue
        sym = _row_symbol(r)
        if (sym, filed, round(shares, 4)) in own and keys & person:
            continue                          # one of the person's own lines, already a target
        targets.append((sym, keys, filed, shares))
    return targets


def _missing_targets(feed: Sequence[Any], targets: Sequence[_Target]) -> int:
    """How many ``targets`` the read ``feed`` does not hold: a row of the same symbol, filing day
    and share count (to 4 decimals) whose identity keys meet the target's."""
    index: Dict[Tuple[str, date, float], List[Set[str]]] = {}
    for r in feed:
        if not isinstance(r, dict):
            continue
        filed, shares = _day(r.get("filingDate")), _shares_of(r.get("securitiesTransacted"))
        if filed is None or shares is None:
            continue
        index.setdefault((_row_symbol(r), filed, round(shares, 4)), []).append(_row_keys(r))
    return sum(1 for sym, keys, filed, shares in targets
               if not any(k & keys for k in index.get((sym, filed, round(shares, 4)), ())))


async def _issuer_feed(ctx: "_Ctx", cik: str, window_start: date) -> List[Dict[str, Any]]:
    """One issuer's per-issuer read (every code, every class), memoized like the walk and shared by
    both Form 4 series. Budget exhaustion raises `MarketingNewsUnavailable`; any other failure
    passes through raw (the caller fails the row closed)."""
    return await ctx.step(
        "insider_amendment_check",
        lambda: _shared(("insider_issuer", cik, window_start.isoformat()), INSIDER_RAW_TTL_SECONDS,
                        lambda: _walk_issuer(ctx.fmp, cik, window_start), keep=bool),
        reason="insider_feed_unavailable", passthrough=(Exception,),
    )


async def _amendment_verdict(ctx: "_Ctx", symbol: str, lines: Sequence[Any], *, issuer: Optional[str],
                             walk_rows: Sequence[Any], window_start: date) -> Optional[str]:
    """None when the row stands; else its rejection reason. The market-wide walk asks FMP for P
    rows only, so an amendment under another code (a re-code, a derivative-only 4/A) never
    reaches it: the row's ISSUER — ``issuer``, its profile's CIK — is re-read with no transaction
    filter (review round 9: by CIK, so a 4/A under the issuer's other share class, a blank or a
    variant symbol comes back too, whether or not the P walk showed that class).

    * No profile CIK to read by, the read failing, a read holding another issuer's rows (the
      filter was not applied), or a read that does not hold what the walk saw
      (`_coverage_targets`: the person's newest-day lines and every walk row of the issuer filed
      since) → ``amendment_check_failed`` (WARNING): fail closed for THIS row, never the week.
      Budget exhaustion still raises.
    * ANY amendment row in the read filed since the window opened → ``amended_filing``, whoever
      filed it and whatever its symbol.
    * A row of the person there whose role text is not the sitting role's (`_role_text_ok`:
      "officer: Former CEO" on an F-coded Form 4 the P walk never returns; a director's
      "other: Former Director") → ``role_uncertain``.
    * Review round 9, on the same read: a row of the person whose name is on the Congress
      block-list → ``congress_name``; for a CEO / CFO row, a co-CEO / co-CFO title anywhere, or
      ANOTHER person with that officer role (the shared role rule or a sitting title) →
      ``ambiguous_ceo``; a purchase of the person filed in the window that the row does not hold
      (`_unaccounted_purchases`) → ``partial_person``."""
    cik = R.cik10(issuer) if issuer is not None else None
    if cik is None:
        logger.warning("company news %s: %s Form 4/A check impossible — its profile names no issuer CIK to "
                       "read; row dropped", ctx.series, symbol)
        return "amendment_check_failed"
    try:
        feed = await _issuer_feed(ctx, cik, window_start)
    except (MarketingNewsUnavailable, asyncio.CancelledError):
        raise
    except Exception as e:  # noqa: BLE001 — any read failure fails THIS row closed, logged
        logger.warning("company news %s: %s Form 4/A check failed reading its issuer (CIK %s) — row dropped (%s)",
                       ctx.series, symbol, cik, _describe(e))
        return "amendment_check_failed"
    rows = [r for r in feed if isinstance(r, dict)]
    foreign = {c for c in (normalize_cik(r.get("companyCik")) for r in rows) if c is not None and c != issuer}
    if foreign:
        logger.warning("company news %s: %s Form 4/A check read rows of %d other issuer(s) under CIK %s — not "
                       "one issuer's read; row dropped", ctx.series, symbol, len(foreign), cik)
        return "amendment_check_failed"
    if any(_is_amendment_row(r) and _filed_since(r, window_start) for r in rows):
        logger.info("company news %s: %s an amendment of the issuer was filed since the window opened — "
                    "row dropped", ctx.series, symbol)
        return "amended_filing"
    targets = _coverage_targets(symbol, lines, issuer, walk_rows)
    missing = len(lines) if targets is None else _missing_targets(rows, targets)
    if missing:
        logger.warning("company news %s: %s Form 4/A check read is missing %d row(s) the walk saw from the "
                       "person's newest filing day on (a stale or degraded read) — row dropped",
                       ctx.series, symbol, missing)
        return "amendment_check_failed"
    role = _person_role(lines)
    person = set().union(*(_line_keys(b) for b in lines)) if lines else set()
    for r in rows:
        if (_filed_since(r, window_start) and _row_keys(r) & person
                and not _role_text_ok(r.get("typeOfOwner"), role)):
            logger.info("company news %s: %s the row's %s has a role text that is not the sitting %s's — "
                        "row dropped", ctx.series, symbol, role, role)
            return "role_uncertain"
    # Review round 9: the read holds every code, so the person's other rows here may carry a
    # spelling of their name the block-list knows (never logged by name) …
    if any(_row_keys(r) & person and R.is_congress_name(r.get("reportingName")) for r in rows):
        logger.info("company news %s: %s the row's person matches the Congress block-list in the issuer "
                    "read — row dropped", ctx.series, symbol)
        return "congress_name"
    # … a co-CEO / co-CFO title (anyone's, any text), or ANOTHER person holding the row's officer
    # role (a tax-withholding or grant row the P walk never returns): "the company's CEO" would be
    # false for one of two …
    if role in ("ceo", "cfo"):
        for r in rows:
            if not _filed_since(r, window_start):
                continue
            text = r.get("typeOfOwner")
            if role in _co_title_roles(text) or (
                    isinstance(text, str) and not (_row_keys(r) & person)
                    and (insider_role(text) == role or _sitting_title(officer_title(text), role))):
                logger.info("company news %s: %s the issuer read shows a co-%s or a second %s — row dropped",
                            ctx.series, symbol, role, role)
                return "ambiguous_ceo"
    # … and a purchase of the person the row does not hold (the walk's pages, another class, a
    # line the extractor skipped): the row would understate what the filings report.
    _ws, window_end = R.insider_window(ctx.run_date)
    unaccounted = _unaccounted_purchases(_purchase_index(rows), lines, symbol=symbol, issuer=issuer,
                                         window_start=window_start, window_end=window_end)
    if unaccounted:
        logger.info("company news %s: %s the issuer read holds %d purchase(s) of the row's person the row "
                    "does not — row dropped", ctx.series, symbol, unaccounted)
        return "partial_person"
    return None


async def _amendment_checked(ctx: "_Ctx", purchases: Sequence[Any], chosen: Mapping[str, Sequence[Any]],
                             issuers: Mapping[str, Optional[str]], walk_rows: Sequence[Any],
                             window_start: date) -> List[Any]:
    """The rows (rank order) whose per-issuer Form 4/A check passes, at most `R.INSIDER_MAX_ROWS`,
    checking at most `INSIDER_AMENDMENT_MAX_SYMBOLS` rows: the rows a week can publish first (in
    parallel), then spares for the rows a check dropped. ``issuers`` maps a row's symbol to its
    PROFILE's issuer CIK (the read's key).

    Review round 10: ONE row per PERSON across issuers. The week counts people ("At least 2 CEOs
    disclosed buying"), and one CEO of two affiliated funds (PennantPark's PNNT and PFLT) buying at
    both was two CEOs. A row whose person (`_person_keys`: the reporting CIK, else the name) already
    holds a kept row is dropped (`same_person`), so the person keeps their LARGEST row that passes
    the check — a queued duplicate is never checked."""
    out: List[Any] = []
    seen: Set[str] = set()
    queue = list(purchases)
    checked = 0
    while queue and len(out) < R.INSIDER_MAX_ROWS and checked < INSIDER_AMENDMENT_MAX_SYMBOLS:
        fresh = []
        for row in queue:
            if _person_keys(chosen[row.company.symbol]) & seen:
                ctx.reject("same_person")
            else:
                fresh.append(row)
        queue = fresh
        if not queue:
            break
        n = min(R.INSIDER_MAX_ROWS - len(out), INSIDER_AMENDMENT_MAX_SYMBOLS - checked, len(queue))
        wave, queue = queue[:n], queue[n:]
        checked += n
        verdicts = await asyncio.gather(
            *(_amendment_verdict(ctx, row.company.symbol, chosen[row.company.symbol],
                                 issuer=issuers.get(row.company.symbol), walk_rows=walk_rows,
                                 window_start=window_start)
              for row in wave),
            return_exceptions=True,
        )
        for v in verdicts:
            if isinstance(v, BaseException):
                raise v
        for row, v in zip(wave, verdicts):
            if v is not None:
                ctx.reject(v)
                continue
            keys = _person_keys(chosen[row.company.symbol])
            if keys & seen:                  # a larger row of the person passed in this wave
                ctx.reject("same_person")
                continue
            seen |= keys
            out.append(row)
    return out


def _holding(ownerships: Iterable[str]) -> Optional[str]:
    kinds = set(ownerships)
    if not kinds or kinds - {"D", "I"}:
        return None                       # an unknown ownership is not guessed
    if kinds == {"D"}:
        return "direct"
    if kinds == {"I"}:
        return "indirect"
    return "mixed"


_ROLE_RANK = {"ceo": 0, "cfo": 1, "director": 2}

#: A co-CEO / co-CFO officer title ("Co-CEO", "Co Chief Executive Officer", "co-CFO"). Marketing
#: only: the shared `is_ceo_role` keeps a co-CEO for the Home card, but "the company's chief
#: executive" (the role is the video's only identifier) is false for either of two.
#: Every Unicode dash U+2010-U+2015 counts as the hyphen (a "Co-CEO" typed with a non-breaking
#: hyphen is still a co-CEO).
_CO_OFFICER_RE = re.compile(
    r"\bco[\s\-\u2010-\u2015]*(?:ceo|cfo|chief[\s\-]+(?:executive|financial))", re.I)


def _co_officer(title_raw: Any) -> bool:
    return bool(_CO_OFFICER_RE.search(officer_title(title_raw)))     # linear: no cap that could hide one


def _raw_co_officers(rows: Sequence[Any]) -> Tuple[Set[Tuple[str, str]], Set[Tuple[str, str]]]:
    """``(role groups, people)`` of every raw walk row whose ``typeOfOwner`` — ANY of its text,
    the free ``other:`` field included, not only the officer title — names a co-CEO or co-CFO
    (review round 6). A role group is ``(role, "sym:<symbol>")`` and ``(role, "cik:<issuer>")``,
    as `_issuer_groups` keys a line; a person is ``(symbol, reporter)``. Such a row is never a
    CEO line when its Officer box is unchecked ("director, other: Co-CEO"), so the symbol's
    other co-CEO, filing as plain "Chief Executive Officer", would read as THE CEO."""
    groups: Set[Tuple[str, str]] = set()
    people: Set[Tuple[str, str]] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        text, raw = r.get("typeOfOwner"), r.get("symbol")
        if not isinstance(text, str) or not isinstance(raw, str):
            continue
        roles = {"cfo" if ("cfo" in m.group(0).lower() or "financial" in m.group(0).lower()) else "ceo"
                 for m in _CO_OFFICER_RE.finditer(text)}
        if not roles:
            continue
        sym, cik, reporter = form4_symbol(raw.strip()), normalize_cik(r.get("companyCik")), insider_reporter_key(r)
        for role in roles:
            groups.add((role, f"sym:{sym}"))
            if cik:
                groups.add((role, f"cik:{cik}"))
        if reporter:
            people.add((sym, reporter))
    return groups, people


def _issuer_groups(b: Any) -> Tuple[str, ...]:
    """The co-officer groups of a line: its symbol, and its issuer CIK when the line names one."""
    return (f"sym:{b.symbol}",) + ((f"cik:{b.company_cik}",) if b.company_cik else ())


# ── sitting-officer titles (review rounds 8-9, marketing only) ────────────────
#
# Round 7 refused a CEO / CFO whose role text carried a TRANSITIONAL word (incoming, elect,
# former …): a denylist, and round 8's probes walked straight past it ("Previously Chief Executive
# Officer", "CEO (Resigned)", "CEO until 12/31/2026", "CEO Nominee", "Departing CEO"). The role
# is the post's one identifying fact ("GameStop's CEO disclosed buying …"), so the rule is now an
# ALLOW-LIST: an officer title qualifies only when every one of its words is a word a sitting
# CEO's (or CFO's) title is made of. Anything else — a date, a digit, "previously", "resigned",
# "until", "effective", "nominee", "corporate" — is `role_uncertain` (accepted over-block:
# "Co-Chairman and CEO", "CFO and Corporate Controller", "CFO of the Company").
# Round 9: the free ``other:`` text runs through the same allow-list whenever it NAMES the role
# ("officer: Chief Executive Officer, other: Former CEO" is not the sitting CEO), and a director's
# ``other:`` text naming the seat runs through `DIRECTOR_TITLE_WORDS` ("director, other: Former
# Director" is not "a director"); other free text ("Member of 13(d) group") stays ignored. The
# lists also learned the common spellings of sitting titles ("Pres.", "Exec.", "Principal
# Financial Officer", "Chief Financial and Accounting Officer", "CFO & Director", "Board of
# Directors", "Secretary").
# Marketing only: the shared role rules (`is_ceo_role`, `insider_role`) stay the Home card's.

#: The words of a sitting CEO's officer title, after `_title_words` ("Chief Executive Officer"
#: and "Principal Executive Officer" are "ceo"; "Co-Founder", "Co Founder" and "Cofounder" are
#: "cofounder").
CEO_TITLE_WORDS: FrozenSet[str] = frozenset({
    "ceo", "president", "chairman", "chairwoman", "chair", "chairperson", "executive", "of", "the",
    "board", "and", "director", "directors", "founder", "cofounder", "interim", "acting", "secretary",
})
#: The words of a sitting CFO's officer title ("Chief Financial Officer", "Principal Financial
#: Officer" and "Chief / Principal Financial and (Principal) Accounting Officer" are "cfo").
CFO_TITLE_WORDS: FrozenSet[str] = frozenset({
    "cfo", "treasurer", "evp", "svp", "executive", "senior", "vice", "president", "principal",
    "accounting", "officer", "chief", "and", "interim", "acting", "of", "finance", "director",
    "secretary",
})
#: The words of a sitting director's seat, read only from an ``other:`` text that names it
#: ("Lead Independent Director", "Member of the Board of Directors", "Non-Executive Director").
DIRECTOR_TITLE_WORDS: FrozenSet[str] = frozenset({
    "director", "directors", "independent", "lead", "presiding", "non", "executive", "chairman",
    "chairwoman", "chair", "chairperson", "vice", "of", "the", "board", "and", "member",
})
_TITLE_WORDS = {"ceo": CEO_TITLE_WORDS, "cfo": CFO_TITLE_WORDS, "director": DIRECTOR_TITLE_WORDS}
#: The word(s) that name each role in a title (one must be there).
_ROLE_WORDS: Dict[str, FrozenSet[str]] = {
    "ceo": frozenset({"ceo"}), "cfo": frozenset({"cfo"}), "director": frozenset({"director", "directors"}),
}
#: Does an ``other:`` text NAME the role (on the normalised text: lower case, dots and
#: apostrophes dropped, every other punctuation a space)?
_ROLE_NAMED_RE: Dict[str, "re.Pattern[str]"] = {
    "ceo": re.compile(r"\b(?:ceo|chief executive|principal executive)\b"),
    "cfo": re.compile(r"\b(?:cfo|chief financial|principal financial)\b"),
    "director": re.compile(r"\bdirectors?\b"),
}
#: A title longer than this is refused, never truncated (a cut could drop the word that matters).
_TITLE_MAX = 200
#: FMP's "10 percent owner" FLAG, should it ever trail the officer title: a flag, not a role word.
_TEN_PERCENT_FLAG_RE = re.compile(r"\b10\s*(?:%|percent)\s*owner\b", re.I)
#: "&", ",", "/" and ";" join two titles: each becomes " and ".
_TITLE_JOIN_RE = re.compile(r"[&,/;]")
#: Dots and apostrophes are dropped inside a word ("C.E.O." → "ceo", "Sr." → "sr").
_TITLE_DROP_RE = re.compile(r"[.'‘’ʼ]")
#: Every other non-letter, non-digit (hyphens and every Unicode dash, parentheses, colons …)
#: separates words: "CEO-Elect" is "ceo elect", "Co-Founder" is "co founder".
_TITLE_SPLIT_RE = re.compile(r"[^a-z0-9]+")
#: Applied after the abbreviations are spelled out, so "Principal Exec. Officer" is "ceo" too.
_TITLE_PHRASES = (
    (re.compile(r"\b(?:chief|principal) executive officer\b"), "ceo"),
    (re.compile(r"\b(?:chief|principal) financial and (?:principal )?accounting officer\b"), "cfo"),
    (re.compile(r"\b(?:chief|principal) financial officer\b"), "cfo"),
    (re.compile(r"\bco founder\b"), "cofounder"),
)
#: Spelling only: abbreviations of words already on a list.
_TITLE_ABBREVIATIONS = {"vp": ("vice", "president"), "sr": ("senior",), "pres": ("president",),
                        "exec": ("executive",)}


def _title_words(title: str) -> Optional[List[str]]:
    """An officer title as its words: lower case; "&", ",", "/", ";" → "and"; dots and
    apostrophes dropped; every other punctuation a separator; "vp", "sr", "pres" and "exec"
    spelled out; then "chief / principal executive officer" → "ceo", "chief / principal financial
    officer" and "chief / principal financial and (principal) accounting officer" → "cfo",
    "co founder" → "cofounder". None for a title too long to read whole."""
    if len(title) > _TITLE_MAX:
        return None
    s = _TEN_PERCENT_FLAG_RE.sub(" ", title).lower()
    s = _TITLE_JOIN_RE.sub(" and ", s)
    s = _TITLE_DROP_RE.sub("", s)
    words: List[str] = []
    for w in _TITLE_SPLIT_RE.split(s):
        if w:
            words.extend(_TITLE_ABBREVIATIONS.get(w, (w,)))
    s = " ".join(words)
    for pattern, word in _TITLE_PHRASES:
        s = pattern.sub(word, s)
    return s.split()


def _sitting_title(title: Any, role: str) -> bool:
    """Is ``title`` the SITTING ``role``'s — every word on the role's list (`CEO_TITLE_WORDS`,
    `CFO_TITLE_WORDS`, `DIRECTOR_TITLE_WORDS`) and the role itself named ("ceo", "cfo",
    "director(s)")?"""
    if not isinstance(title, str):
        return False
    words = _title_words(title)
    return bool(words) and bool(set(words) & _ROLE_WORDS[role]) and set(words) <= _TITLE_WORDS[role]


def _other_text(text: str) -> str:
    """The free ``other:`` text of a ``typeOfOwner`` — everything after its first "other:" — or ""."""
    at = text.lower().find("other:")
    return text[at + len("other:"):] if at >= 0 else ""


def _names_role(text: str, role: str) -> bool:
    """Does ``text`` name ``role`` ("Former CEO", "Chief Financial Officer until …", "Director
    Nominee")? Linear: the text is normalised and searched once, whatever its length."""
    s = " ".join(_TITLE_SPLIT_RE.split(_TITLE_DROP_RE.sub("", text.lower())))
    return bool(_ROLE_NAMED_RE[role].search(s))


def _role_text_ok(text: Any, role: str) -> bool:
    """One row of a candidate (its raw ``typeOfOwner``): True when it describes the sitting
    ``role``, or says nothing about it.

    * A CEO / CFO: the officer title must be the sitting officer's — a row filed as a non-officer
      ("director", "10 percent owner", "officer:" with no title) is NOT the sitting officer's,
      that filing says otherwise.
    * Any role: an ``other:`` text that NAMES the role ("Former CEO", "CFO until 12/31/2026",
      "Former Director") must be the sitting role's too; other free text ("Member of 13(d)
      group") is no evidence.
    * No role text at all (None, blank, not a string) is no evidence either way: the person's
      other rows decide."""
    if not isinstance(text, str) or not text.strip():
        return True
    if role in ("ceo", "cfo") and not _sitting_title(officer_title(text), role):
        return False
    other = _other_text(text)
    if other.strip() and _names_role(other, role):
        return _sitting_title(other, role)
    return True


def _person_role(lines: Sequence[Any]) -> str:
    """The role a person is published under: "ceo" before "cfo", else "director"."""
    roles = {b.role for b in lines}
    return "ceo" if "ceo" in roles else "cfo" if "cfo" in roles else "director"


def _role_uncertain_people(rows: Sequence[Any], lines: Sequence[Any],
                           window_start: date) -> Set[Tuple[str, str]]:
    """``(symbol, reporter)`` of every person any of whose role texts is not the sitting role's
    (`_role_text_ok`: a CEO / CFO's officer title and an ``other:`` text naming the role; a
    director's ``other:`` text naming the seat): their own lines, and EVERY walk row of the same
    person (identity keys) on the same issuer — the symbol, or the issuer CIK the lines name —
    filed since the window opened. The per-issuer read applies the same check to the codes the P
    walk never returns (`_amendment_verdict`)."""
    people: Dict[Tuple[str, str], List[Any]] = {}
    for b in lines:
        people.setdefault((b.symbol, b.reporter), []).append(b)
    out: Set[Tuple[str, str]] = set()
    officers: Dict[Tuple[str, str], Tuple[str, Set[str], Set[str]]] = {}
    by_symbol: Dict[str, List[Tuple[str, str]]] = {}
    by_cik: Dict[str, List[Tuple[str, str]]] = {}
    for person, own in people.items():
        role = _person_role(own)
        if any(not _role_text_ok(b.title_raw, role) for b in own):
            out.add(person)
            continue
        ciks = {b.company_cik for b in own if b.company_cik}
        officers[person] = (role, set().union(*(_line_keys(b) for b in own)), ciks)
        by_symbol.setdefault(person[0], []).append(person)
        for cik in ciks:
            by_cik.setdefault(cik, []).append(person)
    if not officers:
        return out
    for r in rows:
        if not isinstance(r, dict) or not _filed_since(r, window_start):
            continue
        candidates = set(by_symbol.get(_row_symbol(r), ()))
        cik = normalize_cik(r.get("companyCik"))
        if cik:
            candidates.update(by_cik.get(cik, ()))
        candidates -= out
        if not candidates:
            continue
        keys = _row_keys(r)
        for person in candidates:
            role, person_keys, _ciks = officers[person]
            if keys & person_keys and not _role_text_ok(r.get("typeOfOwner"), role):
                out.add(person)
    return out


# ── the whole person (review round 9) ─────────────────────────────────────────
#
# A published row states what the filings report for ONE person on ONE issuer in the window: how
# many purchases, how many shares, how many dollars. A person some of whose purchases were dropped
# one by one (an implausible price, no trade date, a line the extractor skipped for its quality,
# a line under the issuer's other class or under no symbol) would be published with a smaller
# count and amount than the filings report — so such a person is REFUSED (`partial_person`) and
# the next one is tried, never re-summed from what survived. A line of ANOTHER issuer
# (`issuer_mismatch`), a sale, a non-common security and a line filed outside the window are not
# the person's purchases of this company and change nothing.


def _is_purchase_row(r: Mapping[str, Any]) -> bool:
    """A raw Form 4 row the filing reports as an open-market purchase of the common stock: code P
    (`classify_insider_transaction`), acquired (or unmarked), a Form 4, and a common-stock — or
    UNLABELLED (fail closed: it may be the stock) — security."""
    tx = r.get("transactionType")
    if not isinstance(tx, str) or classify_insider_transaction(tx) != "Informative Buy":
        return False
    acq = r.get("acquisitionOrDisposition")
    if acq not in (None, "") and str(acq).strip().upper() != "A":
        return False
    form = r.get("formType")
    if not (str(form).strip().upper() if form not in (None, "") else "4").startswith("4"):
        return False
    sec = r.get("securityName")
    return is_common_stock(sec) or not (isinstance(sec, str) and sec.strip())


def _line_purchase_key(b: Any) -> Tuple[Any, ...]:
    """A buy line as the extractor de-duplicates it: (symbol, trade-or-filing day, ownership,
    shares, price) — the same (shares, price) line on two filings is one purchase."""
    return (b.symbol, b.transaction_date or b.filing_date, b.ownership, round(b.shares, 4), round(b.price, 4))


def _raw_purchase_key(r: Mapping[str, Any]) -> Optional[Tuple[Any, ...]]:
    """`_line_purchase_key` of a raw row, or None when it cannot be read (never accounted for)."""
    shares, price = _shares_of(r.get("securitiesTransacted")), _shares_of(r.get("price"))
    if shares is None or price is None:
        return None
    when = _day(r.get("transactionDate")) or _day(r.get("filingDate"))
    if when is None:
        return None
    own = r.get("directOrIndirect")
    return (_row_symbol(r), when.isoformat(), str(own).strip().upper() if isinstance(own, str) else "",
            round(shares, 4), round(price, 4))


def _purchase_index(raw_rows: Iterable[Any]) -> Dict[str, List[Dict[str, Any]]]:
    """identity key → the purchase rows (`_is_purchase_row`) carrying it: built once per walk, so
    the per-person check reads only that person's rows (the walk holds up to 3,000)."""
    index: Dict[str, List[Dict[str, Any]]] = {}
    for r in raw_rows:
        if isinstance(r, dict) and _is_purchase_row(r):
            for k in _row_keys(r):
                index.setdefault(k, []).append(r)
    return index


def _unaccounted_purchases(index: Mapping[str, Sequence[Dict[str, Any]]], lines: Sequence[Any], *,
                           symbol: str, issuer: Optional[str], window_start: date, window_end: date) -> int:
    """How many purchases (`_purchase_index`) of the person behind ``lines`` (identity keys) on
    the row's issuer — ``issuer`` (a row's own ``companyCik``), else the symbol when either has
    none — filed in ``[window_start, window_end]`` (an unreadable filing day counts) are NOT among
    ``lines``. A row of another issuer is never the person's purchase of this one."""
    if not lines:
        return 0
    person = set().union(*(_line_keys(b) for b in lines))
    have = {_line_purchase_key(b) for b in lines}
    seen: Set[int] = set()
    missing = 0
    for r in (r for k in sorted(person) for r in index.get(k, ())):
        if id(r) in seen:
            continue
        seen.add(id(r))
        rc = normalize_cik(r.get("companyCik"))
        if rc is not None and issuer is not None:
            if rc != issuer:
                continue
        elif _row_symbol(r) != symbol:
            continue
        filed = _day(r.get("filingDate"))
        if filed is not None and not (window_start <= filed <= window_end):
            continue
        if _raw_purchase_key(r) not in have:
            missing += 1
    return missing


def _co_title_roles(text: Any) -> Set[str]:
    """The officer roles ("ceo", "cfo") a ``typeOfOwner`` names a CO- holder of, anywhere in its
    text (the ``other:`` field too)."""
    if not isinstance(text, str):
        return set()
    return {"cfo" if ("cfo" in m.group(0).lower() or "financial" in m.group(0).lower()) else "ceo"
            for m in _CO_OFFICER_RE.finditer(text)}


def _insider_row(ctx: _Ctx, company: "R.CompanyRef", profile: Mapping[str, Any],
                 lines: Sequence[Any]) -> Any:
    """One reporter's plausible lines → `InsiderPurchase`, or a rejection reason (str). Reached
    only while the Congress roster is fresh for the run date (`_collect_insider` refuses the series
    otherwise — review round 11: role-only would still point at a member a stale list misses)."""
    # Unreachable by construction (THE amendment rule refused every issuer with an amendment
    # before ranking), kept so a published row can never carry a 4/A line: `amended` is False.
    if any("/A" in b.form_type for b in lines):
        return "amended_filing"
    # A fund or LLC with a board designee files with the Director box checked: never published
    # as "a director", named or role-only. The reporter loop then tries the next person.
    if any(R.is_entity_reporter(b.name_raw) for b in lines):
        return "reporter_is_entity"
    holding = _holding(b.ownership for b in lines)
    trades = [_day(b.transaction_date) for b in lines]
    filings = sorted({_day(b.filing_date) for b in lines} - {None})
    if holding is None or None in trades or not filings:
        return "record_invalid"
    role = min((b.role for b in lines), key=lambda r: _ROLE_RANK.get(r, 9))
    names = {b.name_raw for b in lines}
    rendered = {R.render_person_name(n, company=company.name) for n in names}
    person = rendered.pop() if len(rendered) == 1 else None
    if person is not None and role == "ceo" and not R.corroborates(person, profile.get("ceo")):
        person = None
    try:
        return R.InsiderPurchase(
            company=company,
            role=role,
            person_name=person,
            amount_usd=math.fsum(b.dollars for b in lines),
            shares=math.fsum(b.shares for b in lines),
            purchases=len(lines),
            earliest_trade_date=min(trades),
            latest_trade_date=max(trades),
            filing_dates=tuple(filings),
            holding=holding,
            amended=False,
        )
    except ValueError as e:
        logger.info("company news %s: %s row refused as record_invalid (%s)",
                    ctx.series, company.symbol, _describe(e))
        return "record_invalid"


def _one_row_per_issuer(ctx: _Ctx, purchases: Sequence[Any],
                        issuer_of: Mapping[str, Optional[str]]) -> List[Any]:
    """The issuer, not the symbol, is the unit: two share classes of one company (BF-A and
    BF-B, both "Brown-Forman") would read as two companies — "2 CEOs disclosed buying" about one
    person. ``purchases`` is in rank order; the first (largest) row of an issuer is kept, every
    later row sharing its issuer CIK (the one its Form 4 lines name, already checked against the
    profile) or its display name is dropped (`share_class_overlap`)."""
    seen_ciks: Set[str] = set()
    seen_names: Set[str] = set()
    out = []
    for row in purchases:
        cik = issuer_of.get(row.company.symbol)
        name = row.company.name.casefold()
        if (cik is not None and cik in seen_ciks) or name in seen_names:
            ctx.reject("share_class_overlap")
            continue
        if cik is not None:
            seen_ciks.add(cik)
        seen_names.add(name)
        out.append(row)
    return out


async def _collect_insider(ctx: _Ctx, roles: Tuple[str, ...], none_reason: str) -> Candidates:
    window_start, window_end = R.insider_window(ctx.run_date)
    key = f"{R.LEDGER_PREFIX}{ctx.series}:{window_start.isoformat()}"
    if key in ctx.exclude:                 # the week is posted: no feed, no profile call
        ctx.reject("already_posted")
        return ctx.done((), none_reason)
    if not R.person_names_allowed():
        # The Congress block-list could not be built (roster / registry unreadable, logged at
        # ERROR by the rules module). A member's row must be DROPPED, and role-only would still
        # point at them — so no Form 4 series runs until the block-list is whole again.
        raise MarketingNewsUnavailable(ctx.series, "internal_error",
                                       "the congress block-list is unusable; Form 4 series refused")
    # Review round 11 (main-session decision 2026-10-10; it replaces round 9/10's role-only path): a
    # roster that does not hold the SITTING Congress on the run date (its `congress_start`), is
    # undated, or is older than `R.ROSTER_MAX_AGE_DAYS` cannot know every member — and role-only
    # would still point at one it misses ("GameStop's director disclosed buying …"), exactly as with
    # an unusable block-list above. So the series is REFUSED (the chain falls back) until the roster
    # is refreshed; `R.roster_fresh_for` logs why at ERROR. Read once, from the RUN date.
    if not R.roster_fresh_for(ctx.run_date):
        logger.error("company news %s: the congress roster is not fresh for %s (it must hold the sitting "
                     "Congress) — Form 4 series refused until scripts/refresh_congress_roster.py refreshes it",
                     ctx.series, ctx.run_date)
        raise MarketingNewsUnavailable(ctx.series, "internal_error",
                                       "the congress roster does not hold the sitting Congress, is undated "
                                       "or too old; Form 4 series refused")

    rows = await ctx.step(
        "insider_walk",
        lambda: _shared(("insider", window_start.isoformat()), INSIDER_RAW_TTL_SECONDS,
                        lambda: _walk_insider(ctx.fmp, window_start), keep=bool),
        reason="insider_feed_unavailable",
    )
    if not rows:
        # A market-wide week of open-market purchases is never empty: an empty answer is the
        # client's page-0 403/404 degrade (or an outage), not "nobody bought".
        raise MarketingNewsUnavailable(ctx.series, "insider_feed_empty",
                                       f"stage=insider_walk; 0 rows filed since {window_start}")

    buys = extract_insider_buys(rows, window_start=window_start, window_end=window_end, roles=roles)
    ctx.considered = len({b.symbol for b in buys})

    # Symbols our grammar refuses (warrants, units, odd tickers) never reach a profile call.
    usable = []
    refused_symbols: Dict[str, str] = {}
    for b in buys:
        problem = R.symbol_problem(b.symbol)
        if problem:
            refused_symbols[b.symbol] = problem
        else:
            usable.append(b)
    for problem in refused_symbols.values():
        ctx.reject(problem)

    # THE amendment rule (review rounds 7-8): ANY amendment row of an issuer filed since the
    # window opened (the walk's rows, run day included) — on its symbol or its issuer CIK, every
    # share class — refuses every person of that issuer for the week (`amended_filing`, one count
    # per person), before anything else reads them. The profile stage refuses a row whose
    # PROFILE CIK is amended; the per-issuer read below applies the same rule to the codes the
    # P-only walk never returns.
    amended_syms, amended_ciks = _amended_issuers(rows, window_start)
    if amended_syms or amended_ciks:
        refused = {(b.symbol, b.reporter) for b in usable if _issuer_amended(b, amended_syms, amended_ciks)}
        if refused:
            ctx.reject("amended_filing", len(refused))
            usable = [b for b in usable if (b.symbol, b.reporter) not in refused]

    # A co-CEO / co-CFO title (review round 5): that person is dropped and, when the line is a
    # CEO or CFO line, so is every line of THAT role on its symbol and issuer CIK — the other
    # co-officer may file as plain "CEO", and dropping only the co- line would hide them from the
    # grouping below. One `ambiguous_ceo` count per person dropped.
    co_lines = [b for b in usable if _co_officer(b.title_raw)]
    co_groups = {(b.role, g) for b in co_lines if b.role in ("ceo", "cfo") for g in _issuer_groups(b)}
    co_people = {(b.symbol, b.reporter) for b in co_lines}
    # Review round 6: a co-CEO / co-CFO title the officer title does not carry ("director,
    # other: Co-CEO" — no Officer box, so never a CEO line) still means the symbol's other CEO
    # is one of two. Every raw row of the walk is read, whatever its role text.
    raw_groups, raw_people = _raw_co_officers(rows)
    co_groups |= raw_groups
    co_people |= raw_people
    if co_groups or co_people:
        dropped = {(b.symbol, b.reporter) for b in usable
                   if (b.symbol, b.reporter) in co_people
                   or any((b.role, g) in co_groups for g in _issuer_groups(b))}
        if dropped:
            ctx.reject("ambiguous_ceo", len(dropped))
            usable = [b for b in usable if (b.symbol, b.reporter) not in dropped]

    # Two or more people holding one officer role on one issuer: "its CEO" (or "its CFO") would be
    # false for one of them. Grouped per SYMBOL and per ISSUER CIK (a line's companyCik), so
    # co-officers buying two share classes are caught too — and a line without a CIK still meets
    # its symbol's other officer. Review round 9: the groups are built BEFORE the role_uncertain
    # refusal below, from EVERY line the shared rule calls that role — a co-CEO whose title the
    # co- pattern does not know ("Joint CEO", "Dual CEO", "CEO (Co-Lead)") is refused as
    # role_uncertain AND still makes the other one of two; an incoming CEO beside the sitting one
    # does too (accepted over-block) — and CFOs are grouped like CEOs (insider_buys), dropping only
    # the symbol's lines of that role (its directors stay).
    officer_roles = tuple(r for r in ("ceo", "cfo") if r in roles)
    reporters: Dict[Tuple[str, str], Set[str]] = {}
    issuer_symbols: Dict[Tuple[str, str], Set[str]] = {}
    for b in usable:
        if b.role in officer_roles:
            for group in _issuer_groups(b):
                reporters.setdefault((b.role, group), set()).add(b.reporter)
                issuer_symbols.setdefault((b.role, group), set()).add(b.symbol)
    ambiguous_sets = {(role, frozenset(issuer_symbols[(role, g)]))
                      for (role, g), who in reporters.items() if len(who) >= 2}

    # Review round 8: a CEO or CFO any of whose titles — its lines, and every walk row of the
    # same person on the issuer — is not a SITTING officer's (the allow-list `_sitting_title`:
    # "Incoming CEO", "CEO (Resigned)", "CEO until 12/31/2026", a filing as a non-officer) is not
    # shown as the company's CEO / CFO: that person is refused (`role_uncertain`, one count per
    # person).
    uncertain = _role_uncertain_people(rows, usable, window_start)
    if uncertain:
        ctx.reject("role_uncertain", len(uncertain))
        usable = [b for b in usable if (b.symbol, b.reporter) not in uncertain]

    if ambiguous_sets:
        ctx.reject("ambiguous_ceo", len(ambiguous_sets))
        ambiguous = {(role, sym) for role, syms in ambiguous_sets for sym in syms}
        usable = [b for b in usable if (b.role, b.symbol) not in ambiguous]

    # A member of Congress is never shown, and never shown role-only either (the role would
    # still point at the member). Review round 9: the whole PERSON goes, not the matching lines —
    # a member matched on ANY walk row (any code, any spelling of the name) or line is tied to
    # every line by identity key (the reporting CIK, the name), on every symbol. One count per
    # reporter dropped; never logged by name.
    congress_cache: Dict[str, bool] = {}

    def _member(name: Any) -> bool:
        if not isinstance(name, str):
            return False
        hit = congress_cache.get(name)
        if hit is None:
            hit = congress_cache[name] = R.is_congress_name(name)
        return hit

    bad_keys: Set[str] = set()
    for r in rows:
        if isinstance(r, dict) and _member(r.get("reportingName")):
            bad_keys |= _row_keys(r)
    for b in usable:
        if _member(b.name_raw):
            bad_keys |= _line_keys(b)
    kept = usable
    if bad_keys:
        congress_reporters = {b.reporter for b in usable if _line_keys(b) & bad_keys}
        if congress_reporters:
            ctx.reject("congress_name", len(congress_reporters))
            kept = [b for b in usable if b.reporter not in congress_reporters]

    ranked = rank_buy_symbols(kept, min_dollars=R.INSIDER_MIN_AMOUNT_USD)
    ranked_syms = [s for s, _total, _latest in ranked]
    below = {b.symbol for b in kept} - set(ranked_syms)
    if below:
        ctx.reject("below_dollar_floor", len(below))
    top = ranked_syms[:INSIDER_PROFILE_CANDIDATES]

    profiles = await _profiles(ctx, top) if top else {}
    purchase_index = _purchase_index(rows)             # the whole-person check (review round 9)
    purchases: List[Any] = []
    issuer_of: Dict[str, Optional[str]] = {}
    profile_cik: Dict[str, Optional[str]] = {}         # symbol → its profile's issuer CIK
    chosen: Dict[str, List[Any]] = {}                  # symbol → its row's person's lines
    for sym in top:
        profile = profiles.get(sym)
        company = R.company_from_profile(profile, sym, purpose="insider")
        if isinstance(company, str):
            ctx.reject(company)
            continue
        issuer = normalize_cik(profile.get("cik"))
        # The amendment rule on the PROFILE's issuer CIK: the row's own lines may name no CIK
        # while the walk's amendment row (another share class, a blank symbol) names the issuer.
        if issuer is not None and issuer in amended_ciks:
            ctx.reject("amended_filing")
            continue
        profile_cik[sym] = issuer
        # FMP keys the symbol on the filing's EDGAR folder: a public company filing as the
        # reporting owner of ANOTHER issuer carries its own ticker with that issuer's CIK. Such
        # a line is never this company's purchase; with no profile CIK to check against, no
        # line that names an issuer is trusted (fail closed).
        sym_lines = [b for b in kept if b.symbol == sym]
        if issuer is None and any(b.company_cik for b in sym_lines):
            ctx.reject("issuer_unverified")
            continue
        ref_price, cap = profile.get("price"), _num(profile.get("marketCap"))
        good = []
        for b in sym_lines:
            if b.company_cik is not None and b.company_cik != issuer:
                ctx.reject("issuer_mismatch")
                continue
            if not price_plausible(b.price, ref_price, require_reference=True):
                ref = _num(ref_price)
                ctx.reject("price_reference_missing" if ref is None or ref <= 0 else "price_implausible")
                continue
            if not b.transaction_date:
                ctx.reject("record_invalid")      # a buy with no trade date is not dated news
                continue
            good.append(b)
        # The row's issuer identity for the share-class check: the CIK its own Form 4 lines
        # name (equal to the profile's after the check above), else none — the name decides.
        issuer_of[sym] = issuer if any(b.company_cik for b in good) else None
        by_reporter: Dict[str, List[Any]] = {}
        for b in good:
            by_reporter.setdefault(b.reporter, []).append(b)
        # One person per company row: the largest reporter that passes every gate. When none
        # does, the LARGEST reporter's reason is counted (one count per symbol).
        best, first_reason = None, None
        for reporter in sorted(by_reporter, key=lambda r: (-math.fsum(x.dollars for x in by_reporter[r]), r)):
            lines = by_reporter[reporter]
            amount = math.fsum(x.dollars for x in lines)
            if _unaccounted_purchases(purchase_index, lines, symbol=sym, issuer=issuer,
                                      window_start=window_start, window_end=window_end):
                # Review round 9: a purchase of this person on this issuer in the window that the
                # row does not hold (a line dropped above, or one the extractor skipped) — the row
                # would understate the filings. The person is refused, never re-summed.
                why = "partial_person"
            elif amount < R.INSIDER_MIN_AMOUNT_USD:
                why = "below_dollar_floor"
            elif cap is None or amount > INSIDER_MAX_CAP_SHARE * cap:
                why = "over_cap_share"
            elif amount > R.INSIDER_MAX_AMOUNT_USD:
                why = "record_invalid"
            else:
                row = _insider_row(ctx, company, profile, lines)
                if not isinstance(row, str):
                    best = row
                    chosen[sym] = lines
                    break
                why = row
            first_reason = first_reason or why
        if best is None:
            ctx.reject(first_reason or "below_dollar_floor")
            continue
        purchases.append(best)

    ctx.check_profiles()
    purchases.sort(key=lambda r: (-r.amount_usd, r.company.name.casefold(), r.company.symbol))
    purchases = _one_row_per_issuer(ctx, purchases, issuer_of)
    # Only the rows a week can publish (plus spares) are re-read per ISSUER (the profile's CIK:
    # every share class, symbol spelling and blank symbol at once), every code — for an amendment
    # the P-only walk never returned, a stale read, or a role text that is not the sitting role's.
    # The same pass keeps one row per PERSON across issuers (`same_person`, review round 10).
    purchases = await _amendment_checked(ctx, purchases, chosen, profile_cik, rows, window_start)
    rows_out = tuple(purchases[: R.INSIDER_MAX_ROWS])
    if not rows_out:
        return ctx.done((), none_reason)
    try:
        record = R.InsiderBuysWeek(series=ctx.series, window_start=window_start,
                                   window_end=window_end, rows=rows_out)
    except ValueError as e:
        logger.warning("company news %s: week record refused (%s)", ctx.series, _describe(e))
        ctx.reject("record_invalid")
        return ctx.done((), none_reason)
    if R.ledger_key(record) != key:       # the pre-check above must have tested THIS key
        raise RuntimeError(f"ledger key drift: {R.ledger_key(record)} != {key}")
    return ctx.done((record,), None)


async def _collect_ceo_buys(ctx: _Ctx) -> Candidates:
    return await _collect_insider(ctx, ("ceo",), "ceo_none_qualified")


async def _collect_insider_buys(ctx: _Ctx) -> Candidates:
    # Reuses the memoized raw rows of ceo_buys (same window, same walk).
    return await _collect_insider(ctx, ("cfo", "director"), "insider_none_qualified")


# ── thirteen_f ────────────────────────────────────────────────────────────────


class _RecordingFMP:
    """Delegates to the FMP client and keeps the raw 13F extracts the builder fetched, so the
    presented share counts can be checked against `_whale_common.thirteen_f_share_positions`
    over the very same rows (zero extra calls)."""

    def __init__(self, fmp: Any) -> None:
        self._fmp = fmp
        self.extracts: Dict[Tuple[int, int], Any] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fmp, name)

    async def get_institutional_holdings(self, cik: str, year: int, quarter: int, *,
                                         strict: bool = False) -> Any:
        raw = await self._fmp.get_institutional_holdings(cik, year, quarter, strict=strict)
        self.extracts[(int(year), int(quarter))] = raw
        return raw


async def _build_registry(ctx: _Ctx, cik: str, year: int, quarter: int) -> Tuple[Any, Any, Any]:
    proxy = _RecordingFMP(ctx.fmp)
    built = await _builder.build_filing(
        proxy, cik, year, quarter, prev_quarter_available=True, actions=ctx.deps.actions,
        stored_unresolved={}, today=ctx.run_date,
    )
    prev = _tc_rules.previous_quarter(year, quarter)
    return built, proxy.extracts.get((year, quarter)), proxy.extracts.get(prev)


def _share_check(ctx: _Ctx, moves: List[Dict[str, Any]], raw_cur: Any, raw_prev: Any) -> List[Dict[str, Any]]:
    """Registry builds only: a presented share count must agree with the shared 13F share
    positions (`thirteen_f_share_positions`: options / principal rows dropped, per-manager rows
    summed, the latest accession wins) of the same raw extracts. The builder diffs by CUSIP;
    the helper is symbol-keyed, so a symbol it does not carry (resolved from a CUSIP by the
    builder) is left to the builder. Any disagreement drops the move (`degraded_build`)."""
    if not isinstance(raw_cur, list) or not isinstance(raw_prev, list):
        if moves:
            ctx.reject("degraded_build", len(moves))
        return []

    def by_symbol(raw: Any) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for sym, pos in thirteen_f_share_positions(raw).items():
            k = _sym_key(sym)
            out[k] = out.get(k, 0.0) + float(pos.get("shares") or 0)
        return out

    cur, prev = by_symbol(raw_cur), by_symbol(raw_prev)
    kept = []
    for m in moves:
        k = _sym_key(m.get("symbol"))
        shares, prev_shares = _num(m.get("shares")), _num(m.get("prev_shares"))
        ok = True
        if m["move"] in ("newly_reported", "increased", "decreased"):
            if k in cur and (shares is None or abs(cur[k] - shares) >= 1.0):
                ok = False
        if m["move"] == "newly_reported" and prev.get(k, 0.0) > 0:
            ok = False                      # the symbol was on last quarter's book after all
        if m["move"] == "no_longer_reported":
            if cur.get(k, 0.0) > 0:
                ok = False                  # still on this quarter's book under that symbol
            elif k in prev and (prev_shares is None or abs(prev[k] - prev_shares) >= 1.0):
                ok = False
        if ok:
            kept.append(m)
        else:
            ctx.reject("degraded_build")
            logger.warning("company news thirteen_f: %s %s share count disagrees with the shared "
                           "13F positions — move dropped", m["move"], k or "?")
    return kept


def _non_share_issuers(raw: Any) -> Set[str]:
    """Issuer keys of a raw 13F extract's OPTION and NOTE rows (`is_13f_non_share_row`: put, call,
    PRN): the full CUSIP, its 6-character issuer prefix and the symbol."""
    out: Set[str] = set()
    for r in raw if isinstance(raw, list) else ():
        if not isinstance(r, dict) or not is_13f_non_share_row(r):
            continue
        cusip = _tc_rules.normalize_cusip(r.get("securityCusip"))
        if cusip:
            out.update((f"cusip:{cusip}", f"issuer:{cusip[:6]}"))
        sym = _sym_key(r.get("symbol"))
        if sym:
            out.add(f"sym:{sym}")
    return out


def _option_moves_dropped(ctx: _Ctx, moves: List[Dict[str, Any]], counts: Any, raw_cur: Any,
                          raw_prev: Any) -> Tuple[List[Dict[str, Any]], Any]:
    """Review round 9 (#7): the builder diffs SHARE rows only, so "No longer reported: X" was
    published while the same 13F still lists X as call options or notes, and "Newly reported: X"
    while the previous one did. A move whose issuer — its CUSIP, CUSIP issuer prefix or symbol —
    has an option / note row on the current OR the previous book is dropped
    (`move_has_options`), whatever its kind. A dropped newly / no-longer-reported move also leaves
    the builder's count of its kind (the hook's "no longer reports N holdings" must not count a
    position the filing still reports); more / fewer shares counts stay (still true of shares).
    Returns (moves kept, counts)."""
    out_counts = dict(counts) if isinstance(counts, Mapping) else counts   # junk stays junk (record_invalid)
    options = _non_share_issuers(raw_cur) | _non_share_issuers(raw_prev)
    if not options:
        return moves, out_counts
    kept = []
    for m in moves:
        cusip, sym = _tc_rules.normalize_cusip(m.get("cusip")), _sym_key(m.get("symbol"))
        keys = ({f"cusip:{cusip}", f"issuer:{cusip[:6]}"} if cusip else set()) | ({f"sym:{sym}"} if sym else set())
        if not keys & options:
            kept.append(m)
            continue
        ctx.reject("move_has_options")
        if m["move"] in ("newly_reported", "no_longer_reported") and isinstance(out_counts, dict):
            n = out_counts.get(m["move"])
            if isinstance(n, int) and not isinstance(n, bool) and n > 0:
                out_counts[m["move"]] = n - 1
        logger.info("company news thirteen_f: %s %s — the issuer has option / note rows on the current or "
                    "previous book; move dropped", m["move"], sym or "?")
    return kept, out_counts


def _material_floor(total: float) -> float:
    """The value a newly reported / no-longer-reported move must reach to be news."""
    return max(THIRTEEN_F_MATERIAL_MIN_USD, THIRTEEN_F_MATERIAL_SHARE * total)


def _previous_values(raw_prev: Any, *, cik: str, prev_end: date) -> Dict[str, float]:
    """CUSIP → the position's value on the PREVIOUS quarter's book, normalised exactly as the
    builder normalised it for its diff (`normalize_rows`: SH rows of that period and CIK, summed
    per accession, the latest accession wins). An exit's materiality is read from it. ``{}``
    (every exit below the floor) when the extract is missing — `_share_check` has then dropped
    every move already — or cannot be normalised (WARNING)."""
    if not isinstance(raw_prev, list):
        return {}
    try:
        norm = _builder.normalize_rows(raw_prev, expected_period_end=prev_end, expected_cik=cik,
                                       log_ctx=f"cik={cik} previous book (materiality)")
    except Exception as e:  # noqa: BLE001 — pure, but never let it sink the filer
        logger.warning("company news thirteen_f: cik=%s previous book unreadable for materiality — "
                       "no exit shown (%s)", cik, _describe(e))
        return {}
    out: Dict[str, float] = {}
    for r in norm.rows:
        cusip, value = r.get("cusip"), _num(r.get("value"))
        if isinstance(cusip, str) and value is not None:
            out[cusip] = value
    return out


def _pre_gate_move(ctx: _Ctx, m: Mapping[str, Any], total: float) -> Optional[Dict[str, Any]]:
    """The pure gates of one diff row, before any profile call. None = dropped (counted)."""
    move = m.get("move")
    if move not in R.THIRTEEN_F_MOVES:
        return None                          # unchanged / corporate_action are never shown
    sym = R.canonical_symbol(m.get("symbol"))
    if sym is None:
        ctx.reject("move_not_routable")
        return None
    shares, prev, value = _num(m.get("shares")), _num(m.get("prev_shares")), _num(m.get("value"))
    if move in ("newly_reported", "increased", "decreased"):
        if value is None or value <= 0 or shares is None or shares <= 0:
            ctx.reject("record_invalid")
            return None
        if value > total:
            ctx.reject("move_value_exceeds_total")
            return None
    if move in ("increased", "decreased"):
        if prev is None or prev <= 0 or (move == "increased") != (shares > prev) or shares == prev:
            ctx.reject("record_invalid")
            return None
        if abs(shares - prev) / prev < THIRTEEN_F_MIN_CHANGE:
            ctx.reject("move_too_small")
            return None
    if move == "no_longer_reported":
        if prev is None or prev <= 0:
            ctx.reject("record_invalid")
            return None
    prev_value = None
    if move in ("newly_reported", "no_longer_reported"):
        basis = value if move == "newly_reported" else _num(m.get("prev_value"))
        if basis is None or basis < _material_floor(total):
            ctx.reject("move_immaterial")
            return None
        if move == "no_longer_reported":
            prev_value = basis              # kept: the exit's size (`ThirteenFMove.prev_value_usd`)
    if move == "no_longer_reported":
        shares, value = None, None
    if move == "newly_reported":
        prev = None
    flow = abs((shares or 0.0) - (prev or 0.0)) * (value or 0.0) / shares if shares else 0.0
    return {"symbol": sym, "name": str(m.get("name") or ""), "move": move, "shares": shares,
            "prev_shares": prev, "value": value, "prev_value": prev_value,
            "newly_listed": bool(m.get("newly_listed")), "flow": flow, "cusip": m.get("cusip")}


# One position on a 13F book, as the share-class check reads it: (symbol key, the 9-character
# CUSIP or None, folded display name or None).
_BookEntry = Tuple[str, Optional[str], Optional[str]]


def _issuer_prefix(cusip: Optional[str]) -> Optional[str]:
    """The CUSIP's 6-character issuer number (GOOG 02079K107 and GOOGL 02079K305 share
    "02079K"), or None."""
    return cusip[:6] if cusip else None


def _name_key(name: Any) -> Optional[str]:
    shown = R.display_company_name(name)
    return shown.casefold() if shown else None


def _book_entry(symbol: Any, cusip: Any, name: Any) -> _BookEntry:
    return (_sym_key(symbol), _tc_rules.normalize_cusip(cusip), _name_key(name))


def _book_of_holdings(holdings: Any) -> List[_BookEntry]:
    """The builder's current book (`BuiltFiling.holdings`: dicts with cusip/symbol/name)."""
    return [_book_entry(h.get("symbol"), h.get("cusip"), h.get("name"))
            for h in holdings or () if isinstance(h, Mapping)]


def _book_of_raw(raw: Any) -> List[_BookEntry]:
    """A raw FMP 13F extract's SHARE rows (options and principal-amount rows are not
    positions, `is_13f_non_share_row`)."""
    if not isinstance(raw, list):
        return []
    return [_book_entry(r.get("symbol"), r.get("securityCusip"), r.get("nameOfIssuer"))
            for r in raw if isinstance(r, dict) and not is_13f_non_share_row(r)]


def _book_of_club(holdings: Any) -> List[_BookEntry]:
    """A club filer's stored current book (`ClubHoldingResponse`: name and symbol, no CUSIP)."""
    return [_book_entry(getattr(h, "symbol", None), None, getattr(h, "name", None))
            for h in holdings or ()]


def _class_overlaps(ctx: _Ctx, moves: List[Dict[str, Any]], *, current: Sequence[_BookEntry],
                    previous: Sequence[_BookEntry]) -> List[Dict[str, Any]]:
    """The issuer, not the symbol, is the unit. A move is dropped (`share_class_overlap`) when
    its issuer — the CUSIP issuer prefix, or the display name — sits on the book under ANOTHER
    symbol: "No longer reported: Alphabet" is false while GOOGL is still held after GOOG left,
    and "Increased: Alphabet" about one class misstates the issuer's position. An exit is
    checked against the current book; every other move against both books."""
    kept = []
    for m in moves:
        sym, cusip, name = _sym_key(m.get("symbol")), _tc_rules.normalize_cusip(m.get("cusip")), _name_key(m.get("name"))
        pre = _issuer_prefix(cusip)
        books = (current,) if m["move"] == "no_longer_reported" else (current, previous)
        # The move's own security — its symbol, or its CUSIP (a raw row's symbol may be empty;
        # the builder resolved it) — is never "another class" of itself.
        clash = any(
            e_sym != sym and (cusip is None or e_cusip != cusip)
            and ((pre is not None and _issuer_prefix(e_cusip) == pre) or (name is not None and e_name == name))
            for book in books for e_sym, e_cusip, e_name in book
        )
        if clash:
            ctx.reject("share_class_overlap")
            logger.info("company news thirteen_f: %s %s shares an issuer with another class on the "
                        "book — move dropped", m["move"], sym or "?")
        else:
            kept.append(m)
    return kept


#: The record's kind order (the moves tuple reads new, exits, more, fewer — each largest first).
_KIND_RANK = {"newly_reported": 0, "no_longer_reported": 1, "increased": 2, "decreased": 3}


def _move_size(m: Mapping[str, Any]) -> float:
    """A gated move's dollar size, the measure the templates rank by: a new holding's value; an
    exit's value on the PREVIOUS book; more / fewer shares = the shares that changed at the
    filing's value per share (``flow``). Every gated move has one (> 0)."""
    if m["move"] == "newly_reported":
        return m["value"] or 0.0
    if m["move"] == "no_longer_reported":
        return m["prev_value"] or 0.0
    return m["flow"]


def _size_order(m: Mapping[str, Any]) -> Tuple[Any, ...]:
    return (-_move_size(m), _KIND_RANK[m["move"]], m["symbol"])


def _move_order(m: Mapping[str, Any]) -> Tuple[Any, ...]:
    """The order of the record's moves: by kind, then largest first."""
    return (_KIND_RANK[m["move"]], -_move_size(m), m["symbol"])


def _select_moves(moves: Sequence[Mapping[str, Any]], limit: int) -> List[Mapping[str, Any]]:
    """At most ``limit`` moves chosen by SIZE (review round 8; kind order used to cut first, so a
    $2B increase never reached a record of nine new holdings): each kind's
    `THIRTEEN_F_KIND_RESERVE` largest moves are reserved — taken tier by tier (every kind's
    largest, then every kind's second …), largest first within a tier, while room is left — and
    the rest of the room is filled largest first. So each kind with moves keeps its largest move
    whenever ``limit`` reaches the number of kinds. Returned in `_size_order`."""
    ordered = sorted(moves, key=_size_order)
    seen: Counter = Counter()
    reserved: List[Tuple[int, Mapping[str, Any]]] = []
    rest: List[Mapping[str, Any]] = []
    for m in ordered:
        tier = seen[m["move"]]
        if tier < THIRTEEN_F_KIND_RESERVE:
            reserved.append((tier, m))
            seen[m["move"]] += 1
        else:
            rest.append(m)
    reserved.sort(key=lambda t: (t[0],) + _size_order(t[1]))
    picked = [m for _tier, m in reserved[:limit]]
    picked += rest[: max(0, limit - len(picked))]
    return sorted(picked, key=_size_order)


async def _filing_record(
    ctx: _Ctx, *, filer_name: Any, cik: str, filer_symbol: Any, period: str, period_end: date,
    prev_end: date, filed_on: Optional[date], amended_on: Optional[date], total: Any,
    position_count: Any, raw_moves: Sequence[Mapping[str, Any]], counts: Mapping[str, Any],
    current_book: Sequence[_BookEntry], previous_book: Sequence[_BookEntry],
) -> Optional[Any]:
    total_f = _num(total)
    if total_f is None or total_f <= 0 or not isinstance(position_count, int) or isinstance(position_count, bool):
        ctx.reject("record_invalid")
        return None
    pre = [g for g in (_pre_gate_move(ctx, m, total_f) for m in raw_moves) if g is not None]
    pre = _class_overlaps(ctx, pre, current=current_book, previous=previous_book)
    # Chosen by size with each kind's largest reserved (`_select_moves`), profiled in record order.
    presented = sorted(_select_moves(pre, THIRTEEN_F_MAX_PROFILED), key=_move_order)
    if not presented:
        return None
    # The filer's own ticker rides in the same profile batch: it is kept only when it passes
    # the listing gate (ARK's "ARKK" is an ETF — no ETF data of any kind, §1).
    own = R.canonical_symbol(filer_symbol) if filer_symbol else None
    profiles = await _profiles(ctx, [m["symbol"] for m in presented] + ([own] if own else []))
    if own is not None and isinstance(R.company_from_profile(profiles.get(own), own, purpose="listing"), str):
        own = None
    # Review round 9 (#9 / #23): the logo and chip are the FILER's own listing only — the ticker's
    # profile CIK must be the 13F filer's CIK ("IEP" on Carl C. Icahn's own 13F was another
    # entity's logo). No profile CIK → no chip (fail closed).
    if own is not None and R.cik10((profiles.get(own) or {}).get("cik")) != cik:
        logger.info("company news thirteen_f: cik=%s filer symbol %s is not the filer's own listing (its "
                    "profile CIK differs) — no logo or chip", cik, own)
        own = None

    final: List[Tuple[Mapping[str, Any], Any]] = []
    for m in presented:
        profile = profiles.get(m["symbol"])
        company = R.company_from_profile(profile, m["symbol"], purpose="listing")
        if isinstance(company, str):
            ctx.reject(company)
            continue
        listed_on = None
        if m["move"] == "newly_reported":
            ipo = _day(profile.get("ipoDate"))
            newly_listed = ipo is not None and ipo > prev_end
            # A listing after the quarter's end cannot be a holding AT its end: the date is
            # wrong, and "it was listed in <month>" would be too (`ThirteenFFiling` refuses it).
            if ipo is None or newly_listed != m["newly_listed"] or ipo > period_end:
                ctx.reject("move_unknown_listing")
                continue
            listed_on = ipo if newly_listed else None
        try:
            mv = R.ThirteenFMove(company=company, move=m["move"], shares=m["shares"],
                                 prev_shares=m["prev_shares"], value_usd=m["value"], listed_on=listed_on,
                                 prev_value_usd=m["prev_value"])
        except ValueError as e:
            logger.info("company news thirteen_f: move %s refused (%s)", m["symbol"], _describe(e))
            ctx.reject("record_invalid")
            continue
        final.append((m, mv))
    # Review round 9 (#8): the PROFILED name against the books — an exit's SEC name ("NEWS CORP
    # NEW") never met the club book's display name ("News Corporation") of the class still held.
    # A move whose profiled display name sits on the book under ANOTHER symbol is dropped
    # (`share_class_overlap`): an exit against the current book, every other move against both.
    # Review round 10: the move's OWN security — its CUSIP — is never another class of itself (a
    # ticker rename, SQ → XYZ, leaves the old symbol on the previous raw extract), as in
    # `_class_overlaps`. A club move carries no CUSIP: the name decides.
    tied = []
    for m, mv in final:
        name = mv.company.name.casefold()
        sym, cusip = _sym_key(m["symbol"]), _tc_rules.normalize_cusip(m.get("cusip"))
        books = (current_book,) if m["move"] == "no_longer_reported" else (current_book, previous_book)
        if any(e_name == name and e_sym != sym and (cusip is None or e_cusip != cusip)
               for book in books for e_sym, e_cusip, e_name in book):
            ctx.reject("share_class_overlap")
            logger.info("company news thirteen_f: %s %s — its company is on the book under another class; "
                        "move dropped", m["move"], m["symbol"])
        else:
            tied.append((m, mv))
    final = tied
    # Backstop on the profiled names: two presented moves that resolve to one display name (two
    # share classes the book check could not tie — a club book has no CUSIPs) would print one
    # company twice, possibly with opposite moves. Every move of such a name is dropped.
    by_name = Counter(mv.company.name.casefold() for _m, mv in final)
    for _m, mv in final:
        if by_name[mv.company.name.casefold()] > 1:
            ctx.reject("share_class_overlap")
    final = [(m, mv) for m, mv in final if by_name[mv.company.name.casefold()] == 1]
    # The final cut is by size too (each kind's largest reserved), then the record's kind order.
    move_of = {id(m): mv for m, mv in final}
    kept_moves = sorted(_select_moves([m for m, _mv in final], R.THIRTEEN_F_MAX_MOVES), key=_move_order)
    moves = tuple(move_of[id(m)] for m in kept_moves)
    headline = sum(1 for mv in moves if mv.move in ("newly_reported", "no_longer_reported"))
    if not moves or (headline == 0 and len(moves) < 2):
        return None

    count_pairs = []
    for kind in R.THIRTEEN_F_MOVES:
        n = counts.get(kind, 0) if isinstance(counts, Mapping) else None
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            ctx.reject("record_invalid")
            return None
        count_pairs.append((kind, n))
    try:
        return R.ThirteenFFiling(
            series="thirteen_f", filer_name=filer_name, filer_cik=cik, filer_symbol=own,
            period=period, period_end=period_end, filed_on=filed_on, amended_on=amended_on,
            total_value_usd=total_f, position_count=position_count, moves=moves,
            counts=tuple(count_pairs),
        )
    except ValueError as e:
        logger.warning("company news thirteen_f: filing %s %s refused (%s)", cik, period, _describe(e))
        ctx.reject("record_invalid")
        return None


def _counts_of(obj: Any) -> Dict[str, Any]:
    if isinstance(obj, Mapping):
        return dict(obj)
    return {k: getattr(obj, k, None) for k in R.THIRTEEN_F_MOVES} if obj is not None else {}


async def _club_filings(ctx: _Ctx, *, year: int, quarter: int, period: str, period_end: date,
                        prev_end: date, room: int, club_ciks: Set[str]) -> List[Any]:
    """Trillion Club 13F filers (`card_kind == "thirteen_f"` with `use_13f`), in group order.
    Data from the stored builds (`get_detail`, unredacted): zero FMP calls for the filing."""
    prev_period = R.period_label(*_tc_rules.previous_quarter(year, quarter))
    group = await ctx.step("club_group", lambda: ctx.club.get_group(), reason="club_unavailable")
    rows = await _club_rows(ctx, reason="club_unavailable")
    out: List[Any] = []
    options_unchecked: Set[str] = set()
    for card in getattr(group, "companies", None) or ():
        if getattr(card, "card_kind", None) != "thirteen_f":
            continue
        row = rows.get(getattr(card, "slug", None))
        if not row or row.get("use_13f") is not True:
            continue
        ciks = sorted({c for c in (R.cik10(x) for x in (row.get("ciks") or ())) if c})
        if not ciks:
            continue
        club_ciks.update(ciks)
        if len(out) >= room:
            continue                         # keep collecting CIKs for the registry dedup
        ctx.considered += 1
        cik = ciks[0]
        if f"{R.LEDGER_PREFIX}thirteen_f:{cik}:{period}" in ctx.exclude:
            ctx.reject("already_posted")
            continue
        problem = R.filer_name_problem(getattr(card, "name", None))
        if problem:
            ctx.reject(problem)
            continue
        if R.filer_looks_like_person(getattr(card, "name", None)):
            ctx.reject("filer_is_person")     # the subject is the filing ENTITY, never a person
            continue
        if getattr(card, "period", None) != period:
            ctx.reject("filer_not_filed")
            continue
        if getattr(card, "comparison", None) != "quarter":
            ctx.reject("non_comparable")
            continue
        detail = await ctx.step("club_detail", lambda slug=card.slug: ctx.club.get_detail(slug),
                                reason="club_unavailable")
        company = getattr(detail, "company", None)
        if detail is None or company is None or getattr(company, "period", None) != period:
            ctx.reject("filer_unavailable")
            continue
        if getattr(company, "comparison", None) != "quarter":
            ctx.reject("non_comparable")
            continue
        filed = _day(getattr(company, "filed_on", None))
        if filed is None or not (period_end < filed <= ctx.run_date):
            ctx.reject("filer_not_filed")
            continue
        amended = _day(getattr(company, "amended_on", None))
        raw_moves = [{
            "symbol": getattr(ch, "symbol", None), "name": getattr(ch, "name", ""),
            "move": getattr(ch, "change", None), "shares": getattr(ch, "shares", None),
            "prev_shares": getattr(ch, "prev_shares", None), "value": getattr(ch, "value", None),
            "newly_listed": getattr(ch, "newly_listed", False), "prev_value": None,
        } for ch in (getattr(detail, "changes", None) or ())]
        # The stored previous-quarter build (one memoized read): an exit's value (its change row
        # carries none) AND the previous book the share-class tie needs (review round 9 #8: with no
        # previous book, "Newly reported: Alphabet" was published while class C was held last
        # quarter). Missing or unreadable → the filer is refused (fail closed, WARNING).
        previous = await _club_previous_book(ctx, cik, prev_period) if raw_moves else ({}, [])
        if previous is None:
            ctx.reject("filer_unavailable")
            continue
        prev_values, previous_book = previous
        for m in raw_moves:
            if m["move"] == "no_longer_reported":
                m["prev_value"] = prev_values.get(_sym_key(m["symbol"])) if m["symbol"] else None
        # Review round 10, a RECORDED residual (no behaviour change): the stored club builds keep
        # SHARE rows only — `normalize_rows` dropped put / call and PRN rows and kept just an
        # `excluded_rows` COUNT, mixed with other exclusions — so the registry path's options /
        # notes rule (`_option_moves_dropped`, `move_has_options`) cannot run here: "No longer
        # reported: X" may sit beside X calls on the same 13F. Said once per filer.
        if raw_moves and cik not in options_unchecked:
            options_unchecked.add(cik)
            logger.info("company news thirteen_f: club cik=%s %s — its stored build carries no option / note "
                        "rows, so the options rule (move_has_options) is not applied to its %d change row(s)",
                        cik, period, len(raw_moves))
        rec = await _filing_record(
            ctx, filer_name=company.name, cik=cik, filer_symbol=getattr(company, "detail_symbol", None),
            period=period, period_end=period_end, prev_end=prev_end, filed_on=filed,
            amended_on=amended, total=getattr(company, "total_value", None),
            position_count=getattr(company, "position_count", None), raw_moves=raw_moves,
            counts=_counts_of(getattr(company, "change_counts", None)),
            current_book=_book_of_club(getattr(detail, "holdings", None)), previous_book=previous_book,
        )
        if rec is not None:
            out.append(rec)
    return out


async def _club_previous_book(ctx: _Ctx, cik: str, prev_period: str
                              ) -> Optional[Tuple[Dict[str, float], List[_BookEntry]]]:
    """A club filer's stored PREVIOUS-quarter build (`trillion_club_filings.holdings`, one
    memoized read): (symbol key → value, for an exit's materiality; the book's entries — CUSIP,
    symbol, profiled display name — for the share-class tie). None (WARNING) when the row is
    missing or unreadable — the caller refuses the filer; only the budget running out raises."""
    try:
        rows = await _rows(
            ctx, "club_previous_filing", ("club_previous_filing", cik, prev_period),
            lambda sb: sb.table(_CLUB_FILINGS_TABLE).select("cik,period,holdings")
            .eq("cik", cik).eq("period", prev_period).limit(1),
            reason="club_unavailable",
        )
    except MarketingNewsUnavailable as e:
        if e.reason == "budget_exhausted":
            raise
        logger.warning("company news thirteen_f: club cik=%s %s book unreadable — filer refused, no move "
                       "shown (%s)", cik, prev_period, e.detail)
        return None
    row = next((r for r in rows if r.get("cik") == cik and r.get("period") == prev_period), None)
    holdings = row.get("holdings") if row is not None else None
    if isinstance(holdings, str):
        try:
            holdings = json.loads(holdings)
        except ValueError:
            holdings = None
    if not isinstance(holdings, list):
        logger.warning("company news thirteen_f: club cik=%s has no readable %s book — filer refused, no move "
                       "shown", cik, prev_period)
        return None
    out: Dict[str, float] = {}
    for h in holdings:
        if not isinstance(h, dict):
            continue
        k, value = _sym_key(h.get("symbol")), _num(h.get("value"))
        if k and value is not None and value > 0:
            out[k] = out.get(k, 0.0) + value
    return out, _book_of_holdings(holdings)


async def _registry_rows(ctx: _Ctx) -> List[Dict[str, Any]]:
    rows = await _rows(
        ctx, "whales_13f", ("whales_13f",),
        lambda sb: sb.table(_WHALES_TABLE)
        .select("cik,firm_name,category,data_source,associated_ticker,lifecycle_status")
        .eq("data_source", "13f").eq("category", "investors").limit(1000),
        reason="thirteen_f_unavailable",
    )
    order = _registry_order()
    usable = []
    for r in rows:
        if r.get("data_source") != "13f" or r.get("category") != "investors":
            continue
        if str(r.get("lifecycle_status") or "").strip().lower() not in _ACTIVE_LIFECYCLE:
            continue                         # "inactive" (Scion), "dormant", anything curated later
        cik = R.cik10(r.get("cik"))
        if cik is None or cik not in order:
            continue                         # only the curated registry's filers
        usable.append((order[cik], cik, r))
    usable.sort(key=lambda t: (t[0], t[1]))
    seen: Set[str] = set()
    out = []
    for _i, cik, r in usable:
        if cik not in seen:
            seen.add(cik)
            out.append({**r, "cik": cik})
    return out


async def _registry_filings(ctx: _Ctx, *, year: int, quarter: int, period: str, period_end: date,
                            prev_end: date, room: int, skip_ciks: Set[str]) -> List[Any]:
    """Whale-registry 13F filers (investors, lifecycle "" or "active"), in registry order. The pair is
    chosen with `select_13f_comparison` (owner rule 2026-10-09: never across a missing
    quarter); a live `build_filing` normalises per accession by CUSIP and diffs share counts."""
    out: List[Any] = []
    builds = strikes = 0

    def strike(stage: str, e: BaseException) -> None:
        nonlocal strikes
        strikes += 1
        ctx.reject("filer_unavailable")
        logger.warning("company news thirteen_f: filer unavailable cik=%s stage=%s (%s)", cik, stage, _describe(e))
        if strikes >= THIRTEEN_F_MAX_UNAVAILABLE:
            raise MarketingNewsUnavailable(ctx.series, "thirteen_f_unavailable",
                                           f"stage={stage}; {strikes} filers unavailable; last {_describe(e)}")

    for row in await _registry_rows(ctx):
        if len(out) >= room:
            break
        cik = row["cik"]
        if cik in skip_ciks:
            continue
        ctx.considered += 1
        if f"{R.LEDGER_PREFIX}thirteen_f:{cik}:{period}" in ctx.exclude:
            ctx.reject("already_posted")
            continue
        # Review round 9 (#9 / #23): the subject is the EDGAR FILER (`R.THIRTEEN_F_FILERS`), never
        # the curated firm_name ("Icahn Enterprises" for Carl C. Icahn's own 13F). An unlisted CIK
        # is refused; a natural-person filer too — before any FMP call for this filer.
        if cik not in R.THIRTEEN_F_FILERS:
            ctx.reject("filer_entity_unknown")
            logger.info("company news thirteen_f: cik=%s is not in THIRTEEN_F_FILERS — no verified filer "
                        "entity; filer skipped", cik)
            continue
        firm = R.THIRTEEN_F_FILERS[cik]
        if firm is None or R.filer_looks_like_person(firm):
            ctx.reject("filer_is_person")
            continue
        problem = R.filer_name_problem(firm)
        if problem:
            ctx.reject(problem)              # before any FMP call for this filer
            continue
        key = ("13f", cik, period)
        live = _memo_get(key) is _MISS
        if live and builds >= MAX_LIVE_13F_BUILDS:
            break
        try:
            dates = await ctx.step("13f_dates", lambda: ctx.fmp.get_institutional_filing_dates(cik, strict=True),
                                   reason="thirteen_f_unavailable", passthrough=(Exception,))
        except MarketingNewsUnavailable:
            raise
        except Exception as e:  # noqa: BLE001 — an upstream failure for this filer
            strike("13f_dates", e)
            continue
        choice = select_13f_comparison(dates, today=ctx.run_date)
        if choice is None or choice.latest != (year, quarter):
            ctx.reject("filer_not_filed")
            continue
        if choice.comparison != "quarter":
            ctx.reject("non_comparable")     # a gap or a first filing writes no moves
            continue
        if live:
            builds += 1
        try:
            built, raw_cur, raw_prev = await ctx.step(
                "13f_build",
                lambda: _shared(key, BUILT_13F_TTL_SECONDS, lambda: _build_registry(ctx, cik, year, quarter)),
                reason="thirteen_f_unavailable",
                passthrough=(_builder.FilingRefused, _builder.FilingUnavailable),
            )
        except _builder.FilingRefused:
            if live:
                builds -= 1                  # a refused book costs no build slot
            ctx.reject("book_too_large")
            continue
        except _builder.FilingUnavailable as e:
            strike("13f_build", e)
            continue
        reasons = list(getattr(built, "degraded_reasons", None) or ())
        if any(r.startswith(("split_lookup_failed", "symbol_lookup_failed")) for r in reasons):
            ctx.reject("degraded_build")
            continue
        changes = getattr(built, "changes", None) or {}
        if changes.get("comparison") != "quarter":
            ctx.reject("non_comparable")
            continue
        if getattr(built, "period", None) != period or getattr(built, "cik", None) != cik:
            ctx.reject("record_invalid")
            continue
        filed = _day(getattr(built, "filed_on", None))
        if filed is None or not (period_end < filed <= ctx.run_date):
            ctx.reject("filer_not_filed")
            continue
        prev_values = _previous_values(raw_prev, cik=cik, prev_end=prev_end)
        raw_moves = [{
            "symbol": r.get("symbol"), "name": r.get("name") or "", "move": r.get("change"),
            "shares": r.get("shares"), "prev_shares": r.get("prev_shares"), "value": r.get("value"),
            "newly_listed": r.get("newly_listed"), "cusip": r.get("cusip"),
            "prev_value": prev_values.get(_tc_rules.normalize_cusip(r.get("cusip")) or ""),
        } for r in (changes.get("rows") or ()) if isinstance(r, dict)]
        raw_moves, counts = _option_moves_dropped(
            ctx, [m for m in raw_moves if m["move"] in R.THIRTEEN_F_MOVES], changes.get("counts") or {},
            raw_cur, raw_prev)
        raw_moves = _share_check(ctx, raw_moves, raw_cur, raw_prev)
        rec = await _filing_record(
            ctx, filer_name=firm, cik=cik, filer_symbol=row.get("associated_ticker"), period=period,
            period_end=period_end, prev_end=prev_end, filed_on=filed,
            amended_on=_day(getattr(built, "amended_on", None)),
            total=getattr(built, "total_value", None), position_count=getattr(built, "position_count", None),
            raw_moves=raw_moves, counts=counts,
            current_book=_book_of_holdings(getattr(built, "holdings", None)),
            previous_book=_book_of_raw(raw_prev),
        )
        if rec is not None:
            out.append(rec)
    return out


async def _collect_thirteen_f(ctx: _Ctx) -> Candidates:
    season = selection.thirteen_f_season(ctx.run_date)
    if season is None:
        return ctx.done((), "thirteen_f_off_season")
    year, quarter = season
    period = R.period_label(year, quarter)
    period_end = R.period_end_of(period)
    prev_end = _tc_rules.quarter_end(*_tc_rules.previous_quarter(year, quarter))
    records: List[Any] = []
    club_ciks: Set[str] = set()
    if settings.TRILLION_CLUB_ENABLED:
        records += await _club_filings(ctx, year=year, quarter=quarter, period=period,
                                       period_end=period_end, prev_end=prev_end, room=ctx.limit,
                                       club_ciks=club_ciks)
    else:
        logger.info("company news thirteen_f: TRILLION_CLUB_ENABLED is off — no club filers")
    if len(records) < ctx.limit:
        records += await _registry_filings(ctx, year=year, quarter=quarter, period=period,
                                           period_end=period_end, prev_end=prev_end,
                                           room=ctx.limit - len(records), skip_ciks=club_ciks)
    ctx.check_profiles()
    return ctx.done(records, "thirteen_f_none_qualified")


# ── money_map ─────────────────────────────────────────────────────────────────


async def _club_symbols(ctx: _Ctx) -> List[str]:
    """US-listed Trillion Club MEMBERS (the group's cards and its also-in-club list), in group
    order. Best effort: the curated seed alone is a full pool, so a failed read is a WARNING."""
    if not settings.TRILLION_CLUB_ENABLED:
        logger.info("company news money_map: TRILLION_CLUB_ENABLED is off — no club symbols")
        return []
    try:
        group = await ctx.step("club_group", lambda: ctx.club.get_group(), reason="club_unavailable")
        rows = await _club_rows(ctx, reason="club_unavailable")
    except MarketingNewsUnavailable as e:
        if e.reason == "budget_exhausted":
            raise
        logger.warning("company news money_map: club members unavailable (%s) — seed only", e)
        return []
    out = [getattr(c, "detail_symbol", None) for c in (getattr(group, "companies", None) or ())]
    for brief in getattr(group, "also_in_club", None) or ():
        row = rows.get(getattr(brief, "slug", None)) or {}
        out.append(row.get("detail_symbol"))
    return [s for s in out if isinstance(s, str) and s]


async def _theme_symbols(ctx: _Ctx) -> List[str]:
    """Emerging Frontiers members (`trending_themes` tickers minus blocked), theme order. Best
    effort, like `_club_symbols`. Never reads quotes, images, subtitles or Gemini text."""
    try:
        rows = await _rows(
            ctx, "themes", ("themes",),
            lambda sb: sb.table(_THEMES_TABLE).select("slug,tickers,blocked_tickers,sort_order")
            .eq("is_active", True).order("sort_order").limit(200),
            reason="themes_unavailable",
        )
    except MarketingNewsUnavailable as e:
        if e.reason == "budget_exhausted":
            raise
        logger.warning("company news money_map: theme members unavailable (%s) — seed + club only", e)
        return []

    def order(r: Mapping[str, Any]) -> Tuple[int, str]:
        so = r.get("sort_order")
        return (so if isinstance(so, int) and not isinstance(so, bool) else 10 ** 9, str(r.get("slug") or ""))

    out: List[str] = []
    for r in sorted(rows, key=order):
        blocked = {_sym_key(b) for b in (r.get("blocked_tickers") or ()) if isinstance(b, str)}
        for t in r.get("tickers") or ():
            if isinstance(t, str) and _sym_key(t) not in blocked:
                out.append(t)
    return out


def _row_fy(row: Mapping[str, Any]) -> str:
    """The revenue breakdown's own year key (`_record_year`): fiscalYear → calendarYear → date."""
    for key in ("fiscalYear", "calendarYear"):
        v = row.get(key)
        if v not in (None, ""):
            return str(v)
    d = row.get("date") or ""
    return d[:4] if isinstance(d, str) and len(d) >= 4 else ""


def _segments(bd: Any) -> Tuple[List[Any], float]:
    """(≤ 5 named `Segment`s, the rest) from the breakdown's sources. Each name goes through
    `R.segment_display_name` first (FMP's own labels: "Linked In Corporation" → "LinkedIn").
    "Other", "Unallocated", a name we cannot draw or a duplicate folds into the rest."""
    named: List[Any] = []
    rest = 0.0
    seen: Set[str] = set()
    for src in getattr(bd, "revenue_sources", None) or ():
        name, value = R.segment_display_name(getattr(src, "name", None)), _num(getattr(src, "value", None))
        if value is None or value <= 0:
            continue
        if (not isinstance(name, str) or name.strip().lower() in _MONEY_MAP_REST_NAMES
                or not _SEGMENT_NAME_RE.fullmatch(name) or not any(ch.isalpha() for ch in name)
                or name.casefold() in seen or len(named) >= R.MONEY_MAP_SEGMENTS[1]):
            rest += value
            continue
        try:
            seg = R.Segment(name=name, value_usd=value)
        except ValueError:
            rest += value
            continue
        seen.add(name.casefold())
        named.append(seg)
    return named, rest


def _annual_point(profit: Any, fy: str) -> Any:
    for p in getattr(profit, "annual", None) or ():
        if getattr(p, "period", None) == fy:
            return p
    return None


async def _money_map_record(ctx: _Ctx, sym: str, bd: Any, fy: str) -> Any:
    """The gates of contract D6 / adapter design §3.6 → `MoneyMap`, or a rejection reason."""
    if list(getattr(bd, "degraded", None) or ()):
        return "money_map_degraded"
    results = await asyncio.gather(
        ctx.step("money_map_profit", lambda: ctx.profit.get_profit_power(sym), reason="revenue_unavailable"),
        ctx.step("money_map_facts", lambda: ctx.facts(sym, need_executives=False), reason="revenue_unavailable"),
        ctx.step("money_map_income", lambda: ctx.fmp.get_income_statement(sym, "annual", limit=5),
                 reason="revenue_unavailable"),
        _profiles(ctx, [sym]),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, BaseException):
            raise r
    profit, facts, income, profiles = results
    if list(getattr(profit, "degraded", None) or ()):
        return "money_map_degraded"
    if not isinstance(facts, Mapping) or not facts.get("available"):
        if isinstance(facts, Mapping) and facts.get("upstream"):
            raise MarketingNewsUnavailable(ctx.series, "revenue_unavailable", "stage=money_map_facts; upstream")
        return "profile_missing"
    if facts.get("is_etf") is True or facts.get("is_fund") is True:
        return "etf_or_fund"
    profile = profiles.get(sym)
    company = R.company_from_profile(profile, sym, purpose="listing")
    if isinstance(company, str):
        return company
    sector = facts.get("sector") or (profile or {}).get("sector")
    if not isinstance(sector, str) or not sector.strip() or sector.strip() in R.MONEY_MAP_EXCLUDED_SECTORS:
        return "sector_excluded"             # an unknown sector cannot be shown to be non-financial

    if not isinstance(income, list):
        raise MarketingNewsUnavailable(ctx.series, "revenue_unavailable",
                                       f"stage=money_map_income; {type(income).__name__}, not a list")
    rows = sorted((r for r in income if isinstance(r, dict) and _row_fy(r) == fy),
                  key=lambda r: str(r.get("date") or ""), reverse=True)
    if not rows:
        return "money_map_degraded"
    row = rows[0]
    if str(row.get("reportedCurrency") or "").strip().upper() != "USD":
        return "non_usd_reporter"
    period_end = _day(row.get("date"))
    if period_end is None or period_end > ctx.run_date or (ctx.run_date - period_end).days > MONEY_MAP_STALE_DAYS:
        return "money_map_stale"
    revenue, reported = _num(row.get("revenue")), _num(getattr(bd, "reported_revenue", None))
    if revenue is None or reported is None or revenue <= 0 or reported <= 0 \
            or abs(revenue - reported) > MONEY_MAP_STATEMENT_TOLERANCE * reported:
        return "revenue_mismatch"
    net, net_bd = _num(row.get("netIncome")), _num(getattr(bd, "net_income", None))
    if net is None or net_bd is None or abs(net - net_bd) > MONEY_MAP_STATEMENT_TOLERANCE * abs(net_bd) + 1.0:
        return "net_income_mismatch"
    point = _annual_point(profit, fy)
    net_margin = _num(getattr(point, "net_margin", None))
    if net_margin is None or abs(net_margin - 100.0 * net_bd / reported) > MONEY_MAP_NET_MARGIN_TOLERANCE_PT:
        return "money_map_inconsistent"

    named, rest = _segments(bd)
    if len(named) < R.MONEY_MAP_SEGMENTS[0]:
        return "segments_thin"
    # Segments that add up to MORE than revenue are never published. The breakdown's
    # `intersegment_eliminations` is a residual (Σ segments − revenue), derived whether or not
    # the company reports an eliminations line — a feed double-listing a sub-line ("Products" +
    # "Wearables") becomes "Sales between its own segments, $40 billion" under "as reported".
    # So an eliminations bar is never drawn (`eliminations_usd` stays None) and the map is
    # refused instead.
    elim_mag = _num(getattr(bd, "intersegment_eliminations", None))
    if elim_mag is not None and elim_mag > 0:
        return "money_map_inconsistent"
    gap = reported - (math.fsum(s.value_usd for s in named) + rest)
    if gap < -R.MONEY_MAP_SUM_TOLERANCE * reported:
        return "money_map_inconsistent"      # segments exceed revenue
    if gap > R.MONEY_MAP_SUM_TOLERANCE * reported:
        rest += gap                          # revenue no segment names → "Other", never a bar of its own
    other = rest if rest > 0 else None
    if other is not None and (other > max(s.value_usd for s in named)
                              or other > MONEY_MAP_MAX_OTHER_SHARE * reported):
        return "money_map_mostly_other"

    gross = operating = None
    cos = _num(getattr(bd, "cost_of_sales", None))
    gm = _num(getattr(point, "gross_margin", None))
    if cos is not None and cos > 0 and gm is not None:
        g = reported - cos
        if abs(100.0 * g / reported - gm) <= MONEY_MAP_BAR_TOLERANCE_PT:
            gross = g
    opex = _num(getattr(bd, "operating_expense", None))
    om = _num(getattr(point, "operating_margin", None))
    if gross is not None and opex is not None and opex > 0 and om is not None:
        o = gross - opex
        if abs(100.0 * o / reported - om) <= MONEY_MAP_BAR_TOLERANCE_PT:
            operating = o
    try:
        return R.MoneyMap(
            series="money_map", company=company, fiscal_year=fy, period_end=period_end,
            segments=tuple(named), other_usd=other, eliminations_usd=None,
            revenue_usd=reported, gross_profit_usd=gross, operating_profit_usd=operating,
            net_income_usd=net_bd,
        )
    except ValueError as e:
        logger.info("company news money_map: %s FY%s refused (%s)", sym, fy, _describe(e))
        return "record_invalid"


async def _collect_money_map(ctx: _Ctx) -> Candidates:
    """Owner decision 2 (2026-10-09): the curated seed FIRST, then the US-listed Trillion Club
    members, then the Emerging Frontiers members — de-duplicated, every candidate through every
    gate. Symbols never posted come before those posted for an earlier fiscal year."""
    pool = R.merge_money_map_pool(await _club_symbols(ctx), await _theme_symbols(ctx))
    posted = set()
    for ref in ctx.exclude:
        parts = ref.split(":") if isinstance(ref, str) else []
        if len(parts) == 4 and parts[0] + ":" == R.LEDGER_PREFIX and parts[1] == "money_map":
            posted.add(parts[2])
    order = [s for s in pool if s not in posted] + [s for s in pool if s in posted]

    records: List[Any] = []
    attempts = refusals = lookups = 0
    for sym in order:
        if (len(records) >= ctx.limit or attempts >= MONEY_MAP_MAX_ATTEMPTS
                # A record held: the old total cap (records + gaps + refusals) is back.
                or (records and attempts + refusals >= MONEY_MAP_MAX_ATTEMPTS)
                or refusals >= MONEY_MAP_MAX_CONTENT_REJECTIONS or lookups >= MONEY_MAP_MAX_LOOKUPS):
            break
        lookups += 1
        ctx.considered += 1
        try:
            bd = await ctx.step("money_map_breakdown", lambda s=sym: ctx.revenue.get_revenue_breakdown(s),
                                reason="revenue_unavailable")
            fy = getattr(bd, "fiscal_year", None)
            if not isinstance(fy, str) or not re.fullmatch(r"[0-9]{4}", fy):
                attempts += 1
                ctx.reject("money_map_degraded")
                continue
            if f"{R.LEDGER_PREFIX}money_map:{sym}:{fy}" in ctx.exclude:
                ctx.reject("already_posted")     # before any profile call
                continue
            got = await _money_map_record(ctx, sym, bd, fy)
        except MarketingNewsUnavailable as e:
            if e.reason != "budget_exhausted" or not records:
                raise
            # Out of budget on a LATER candidate: the records found stand (each was built on
            # complete data); only an upstream failure or an empty hand is the series' answer.
            logger.warning(
                "company news money_map: budget exhausted at %s with %d record(s) held — returning them "
                "(lookups=%d attempts=%d refusals=%d; %s)", sym, len(records), lookups, attempts, refusals,
                e.detail)
            break
        if isinstance(got, str):
            ctx.reject(got)
            if got in _MONEY_MAP_UPSTREAM_GAPS:
                attempts += 1
            else:
                refusals += 1                # deterministic: never eats an upstream attempt
        else:
            attempts += 1
            records.append(got)
    ctx.check_profiles()
    return ctx.done(records, "money_map_none_qualified")


# ── congress_count (Drop 2b) ──────────────────────────────────────────────────
#
# "N members of Congress disclosed purchases of X stock in <month>": a COUNT, never a member.
# Every member-identity field is dropped at the source (`_scrub_congress`: only salted, per-call
# hashes survive, inside `_congress_month`, and the memo holds the counts alone). The count is
# refused whenever it could be wrong either way: an unidentifiable member, identity fields that
# do not say whether two rows are one member or two, or an in-month purchase of the same company
# under another share class or under no usable symbol. Purchases only (never an exchange or a
# sale), stock only (never an option, a bond, a fund), both chambers together (no split), and the
# month is read only when both feeds provably reach past it — twice, with the two reads equal.


@dataclass(frozen=True)
class _CongressRow:
    """One Congress feed row AFTER the source scrub. No identity field survives: only salted,
    per-call hashes of the identity keys, which never leave `_congress_month`."""

    symbol: str                   # the raw symbol, stripped ("" when none)
    disclosed: Optional[date]
    kind: str                     # folded transaction type: "purchase", "sale (full)", "exchange" …
    asset: str                    # folded asset type: "stock", "stock option" …
    description: str              # folded asset description, capped (stock purchases only)
    ids: Tuple[str, ...]          # hashes of the strict identity keys (an id, the full name, an office)
    last: Optional[str]           # hash of the folded last name; None → the member is unidentifiable


def _ident_text(v: Any) -> str:
    """An identity value as folded ASCII words ("Sánchez Jr." → "sanchez jr"); "" when absent,
    not a string or an int, or over-long."""
    if isinstance(v, int) and not isinstance(v, bool):
        v = str(v)
    if not isinstance(v, str) or len(v) > _IDENT_MAX:
        return ""
    s = unicodedata.normalize("NFKD", v).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(_IDENT_WORD_RE.findall(s))


def _fold_text(v: Any, limit: int) -> str:
    """Lower-case ASCII, whitespace collapsed, capped (punctuation kept: "(EA)" stays "(ea)")."""
    if not isinstance(v, str):
        return ""
    s = unicodedata.normalize("NFKD", v[: limit * 2]).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(s.split())[:limit]


def _scrub_congress(raw: Sequence[Any], chamber: str, salt: str) -> List[_CongressRow]:
    """The source scrub: each feed row → a `_CongressRow`. Identity (`CONGRESS_IDENTITY_FIELDS`)
    is read once, hashed with the call's ``salt`` and dropped; nothing else of it is kept.

    Strict identity keys (any one shared → the same member): the feed's member id
    (``senateID``), the full name (last + first) and an office / senator / representative name —
    each chamber-scoped. The last name alone is kept apart, unscoped, to catch two rows the
    strict keys cannot tell apart (`_members`)."""
    def h(key: str) -> str:
        return hashlib.sha256(f"{salt}|{key}".encode("utf-8")).hexdigest()

    out: List[_CongressRow] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        last = " ".join(w for w in _ident_text(r.get("lastName") or r.get("last_name")).split()
                        if w not in _IDENT_SUFFIXES)
        first = _ident_text(r.get("firstName") or r.get("first_name"))
        ids: List[str] = []
        member_id = _ident_text(r.get("senateID"))
        if member_id:
            ids.append(h(f"{chamber}|id|{member_id}"))
        if last:
            ids.append(h(f"{chamber}|name|{last}|{first}"))
        for field_name in ("office", "senator", "representative"):
            office = _ident_text(r.get(field_name))
            if office:
                ids.append(h(f"{chamber}|office|{office}"))
        kind = " ".join(str(r.get("type") or "").lower().split())[:40]
        asset = " ".join(str(r.get("assetType") or "").lower().split())[:40]
        symbol = r.get("symbol")
        out.append(_CongressRow(
            symbol=symbol.strip()[:16] if isinstance(symbol, str) else "",
            disclosed=_day(r.get("disclosureDate")) if isinstance(r.get("disclosureDate"), str) else None,
            kind=kind,
            asset=asset,
            description=_fold_text(r.get("assetDescription"), _DESC_MAX) if kind == "purchase" else "",
            ids=tuple(ids),
            last=h(f"last|{last}") if last else None,
        ))
    return out


def _feed_problem(rows: Sequence[_CongressRow], month_start: date) -> Optional[str]:
    """None when one chamber's feed, IN ITS OWN ORDER, provably holds every row disclosed on or
    after ``month_start``: every disclosure date parses, the dates never rise (newest first,
    verified 2026-10-09 — a single rise means the order the walk relies on does not hold), and
    the last row is dated more than `CONGRESS_COVERAGE_MARGIN_DAYS` before the month.
    ``congress_feed_unordered`` / ``"uncovered"`` (a longer walk may cover it) otherwise."""
    dates = [r.disclosed for r in rows]
    if not dates or any(d is None for d in dates):
        return "congress_feed_unordered"
    if any(a < b for a, b in zip(dates, dates[1:])):
        return "congress_feed_unordered"
    if dates[-1] < month_start - timedelta(days=CONGRESS_COVERAGE_MARGIN_DAYS):
        return None
    return "uncovered"


def _members(rows: Sequence[_CongressRow]) -> Tuple[int, Optional[str]]:
    """(distinct members, None) among one symbol's in-month purchases, or (0, reason).

    Rows sharing ANY strict identity key are one member (union-find over the hashed keys). The
    count stands only when it is certain both ways: every row has a last name
    (``member_unidentifiable`` otherwise), no member's rows carry two last names, and no last
    name spans two members (``member_ambiguous`` — "Rick Scott" and "Tim Scott", or one member
    filed under two first-name spellings: either could make the figure wrong)."""
    if not rows:
        return 0, None
    if any(r.last is None for r in rows):
        return 0, "member_unidentifiable"
    parent: Dict[Any, Any] = {}

    def find(x: Any) -> Any:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, r in enumerate(rows):
        node = ("row", i)
        find(node)
        for k in r.ids:
            parent[find(("key", k))] = find(node)
    groups: Dict[Any, Set[str]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(("row", i)), set()).add(r.last)
    if any(len(lasts) != 1 for lasts in groups.values()):
        return 0, "member_ambiguous"
    if len({r.last for r in rows}) != len(groups):
        return 0, "member_ambiguous"
    return len(groups), None


def _in_month_signature(rows: Sequence[_CongressRow], start: date, end: date) -> Counter:
    return Counter(r for r in rows if r.disclosed is not None and start <= r.disclosed <= end)


def _count_month(rows: Sequence[_CongressRow], start: date, end: date) -> Dict[str, Any]:
    """The month's aggregate, identity-free: ``counts`` {symbol: members} (certain counts only),
    ``refused`` {symbol: reason}, ``rejections`` {reason: n} and ``stock_purchases`` — every
    in-month stock purchase as (canonical symbol or None, folded description) for the issuer
    check. Purchases only ("purchase" exactly — never an exchange or a sale); stock only (the
    asset type exactly "stock": an option, a bond, a fund is not counted). A symbol is refused
    (``asset_uncertain``) when one of its in-month purchases has an asset type that is neither
    "stock" nor clearly something else (missing, "Other", "REIT" …), or a "stock" row whose
    description names another instrument (an option, a warrant, a note …)."""
    rejections: Counter = Counter()
    per_symbol: Dict[str, List[_CongressRow]] = {}
    stock: List[Tuple[Optional[str], str]] = []
    bad_symbols: Dict[str, str] = {}
    uncertain: Set[str] = set()
    for r in rows:
        if r.disclosed is None or not (start <= r.disclosed <= end):
            continue
        if "exchange" in r.kind:
            rejections["exchange_type"] += 1
            continue
        if r.kind != "purchase":
            continue                          # a sale: not what the post counts
        sym = R.canonical_symbol(r.symbol)
        if r.asset != "stock":
            rejections["option_asset"] += 1
            if r.asset not in _CONGRESS_NON_STOCK_ASSETS:
                # Not clearly anything but stock: it may be one more purchase of the stock.
                stock.append((sym, r.description))
                if sym is not None:
                    uncertain.add(sym)
            continue
        stock.append((sym, r.description))
        if sym is None:
            bad_symbols[r.symbol] = R.symbol_problem(r.symbol) or "symbol_grammar"
            continue
        if _NON_COMMON_DESC_RE.search(r.description):
            uncertain.add(sym)
        per_symbol.setdefault(sym, []).append(r)
    for problem in bad_symbols.values():
        rejections[problem] += 1
    counts: Dict[str, int] = {}
    refused: Dict[str, str] = {}
    for sym, sym_rows in per_symbol.items():
        n, why = _members(sym_rows)
        if why is None and sym in uncertain:
            why = "asset_uncertain"
        if why is not None:
            refused[sym] = why
        else:
            counts[sym] = n
    return {"counts": counts, "refused": refused, "rejections": dict(rejections),
            "stock_purchases": stock}


async def _congress_month(fmp: Any, month: str) -> Dict[str, Any]:
    """Walk both chambers until each provably reaches past ``month`` (`CONGRESS_WALK_LIMITS`),
    read the covering pages once more and require the month's rows of both reads to be equal (a
    row inserted while the parallel pages were read can shift a page boundary and LOSE a row
    without breaking the order), then count. Returns the identity-free aggregate only; the
    salted hashes die with this frame. Any failed, partial or empty read →
    ``congress_feed_unavailable``; a rising date → ``congress_feed_unordered``; not covered at
    the largest walk → ``congress_window_uncovered``."""
    start = date(int(month[:4]), int(month[5:7]), 1)
    end = R.month_end_of(month)
    salt = secrets.token_hex(16)

    def unavailable(reason: str, detail: str) -> MarketingNewsUnavailable:
        return MarketingNewsUnavailable("congress_count", reason, f"stage=congress_walk; {detail}")

    async def read(chamber: str, method: str, limit: int) -> List[_CongressRow]:
        try:
            raw = await getattr(fmp, method)(limit)
        except Exception as e:  # noqa: BLE001 — a partial page set or an outage: fail closed
            raise unavailable("congress_feed_unavailable", f"{chamber} read failed ({_describe(e)})") from e
        if not isinstance(raw, list) or not raw:
            # `[]` is the client's every-page-403/404 degrade — or an outage it swallowed.
            raise unavailable("congress_feed_unavailable", f"{chamber} returned no rows")
        return _scrub_congress(raw, chamber, salt)

    covered: Dict[str, Tuple[int, List[_CongressRow]]] = {}
    for limit in CONGRESS_WALK_LIMITS:
        todo = [(c, m) for c, m in _CONGRESS_CHAMBERS if c not in covered]
        reads = await asyncio.gather(*(read(c, m, limit) for c, m in todo), return_exceptions=True)
        for got in reads:
            if isinstance(got, BaseException):
                raise got
        for (chamber, _m), rows in zip(todo, reads):
            problem = _feed_problem(rows, start)
            if problem == "congress_feed_unordered":
                raise unavailable("congress_feed_unordered", f"{chamber} at {limit} rows")
            if problem is None:
                covered[chamber] = (limit, rows)
        if len(covered) == len(_CONGRESS_CHAMBERS):
            break
    missing = [c for c, _m in _CONGRESS_CHAMBERS if c not in covered]
    if missing:
        raise unavailable("congress_window_uncovered",
                          f"{','.join(missing)} did not reach {start} within {CONGRESS_WALK_LIMITS[-1]} rows")

    confirms = await asyncio.gather(*(read(c, m, covered[c][0]) for c, m in _CONGRESS_CHAMBERS),
                                    return_exceptions=True)
    for got in confirms:
        if isinstance(got, BaseException):
            raise got
    for (chamber, _m), again in zip(_CONGRESS_CHAMBERS, confirms):
        if _in_month_signature(covered[chamber][1], start, end) != _in_month_signature(again, start, end):
            raise unavailable("congress_feed_unavailable",
                              f"{chamber}: the month's rows differ between two reads (the feed moved)")
    rows = [r for c, _m in _CONGRESS_CHAMBERS for r in covered[c][1]]
    return _count_month(rows, start, end)


def _words(text: str) -> Set[str]:
    return set(_IDENT_WORD_RE.findall(text))


def _compact_named(name_words: Sequence[str], desc: str) -> bool:
    """Review round 9 (#11): does a run of CONSECUTIVE description words spell the display name's
    significant words run together ("jp morgan chase" → "jpmorganchase", "exxonmobil",
    "united health" → "unitedhealth")? Word-aligned on purpose: a bare substring would refuse
    "Visa" inside "advisa…" and "Meta" inside "metals". Linear in the description's words."""
    compact = "".join(name_words)
    if len(compact) < 2:
        return False
    words = _IDENT_WORD_RE.findall(desc)
    for i in range(len(words)):
        joined = ""
        for w in words[i:]:
            joined += w
            if len(joined) >= len(compact):
                if joined == compact:
                    return True
                break
            if not compact.startswith(joined):
                break
    return False


def _issuer_clash(symbol: str, company: "R.CompanyRef",
                  stock_purchases: Sequence[Tuple[Optional[str], str]]) -> Optional[str]:
    """Another in-month stock purchase that may be the SAME company (the count would be low):
    another share class of the symbol's root (BRK-A beside BRK-B → ``share_class_overlap``), or
    a row under another or no usable symbol whose description names the company — every
    significant word of its display name, or "(SYM)" / "SYM - …" (``share_class_overlap`` /
    ``unmapped_purchase``), or — review round 9 — those words run together or split another way
    on a word boundary (`_compact_named`: ``share_class_overlap`` / ``count_uncertain``). Errs
    toward refusing: an unrelated description that happens to hold the name costs the candidate."""
    root = symbol.split("-")[0]
    ordered = _IDENT_WORD_RE.findall(_fold_text(company.name, 64))
    name_words = set(ordered)
    significant = name_words - _GENERIC_NAME_WORDS or name_words
    # The significant words in NAME order, for the run-together spelling ("JPMorgan Chase").
    in_order = [w for w in ordered if w in significant]
    tags = {s.lower() for s in (symbol, symbol.replace("-", "."), symbol.replace("-", ""))}
    for other, desc in stock_purchases:
        if other == symbol:
            continue
        if other is not None and other.split("-")[0] == root:
            return "share_class_overlap"
        named = bool(significant) and significant <= _words(desc)
        tagged = any(f"({t})" in desc or desc.startswith(f"{t} -") for t in tags)
        if named or tagged:
            return "unmapped_purchase" if other is None else "share_class_overlap"
        # Review round 9 (#11): the same company spelled with its words run together or split
        # differently ("JP Morgan Chase & Co." for "JPMorgan Chase & Co.", "ExxonMobil Corp",
        # "United Health Group Inc") — the count could be low.
        if _compact_named(in_order, desc):
            return "count_uncertain" if other is None else "share_class_overlap"
    return None


async def _collect_congress_count(ctx: _Ctx) -> Candidates:
    month = selection.congress_disclosure_month(ctx.run_date)
    end = R.month_end_of(month)
    if ctx.run_date < end + timedelta(days=CONGRESS_DUE_DAYS):
        return ctx.done((), "congress_not_due")
    key = f"{R.LEDGER_PREFIX}congress_count:{month}"
    if key in ctx.exclude:                  # the month is posted: no feed, no profile call
        ctx.reject("already_posted")
        return ctx.done((), "congress_none_qualified")
    agg = await ctx.step(
        "congress_walk",
        lambda: _shared(("congress", month), CONGRESS_TTL_SECONDS, lambda: _congress_month(ctx.fmp, month)),
        reason="congress_feed_unavailable",
    )
    for reason, n in sorted(agg["rejections"].items()):
        ctx.reject(reason, n)
    for _sym, why in sorted(agg["refused"].items()):
        ctx.reject(why)
    ranked = sorted(((n, s) for s, n in agg["counts"].items() if n >= R.CONGRESS_MIN_MEMBERS),
                    key=lambda t: (-t[0], t[1]))
    ctx.considered = len(ranked)
    top = ranked[:CONGRESS_PROFILE_CANDIDATES]
    profiles = await _profiles(ctx, [s for _n, s in top]) if top else {}
    found: List[Any] = []
    for n, sym in top:
        company = R.company_from_profile(profiles.get(sym), sym, purpose="common")
        if isinstance(company, str):
            ctx.reject(company)
            continue
        clash = _issuer_clash(sym, company, agg["stock_purchases"])
        if clash:
            ctx.reject(clash)
            logger.info("company news congress_count: %s refused (%s) — another in-month purchase may "
                        "be the same company", sym, clash)
            continue
        try:
            found.append(R.CongressCount(series="congress_count", company=company, month=month,
                                         members=n, fetched_on=ctx.run_date))
        except ValueError as e:
            logger.info("company news congress_count: %s refused as record_invalid (%s)", sym, _describe(e))
            ctx.reject("record_invalid")
    ctx.check_profiles()
    # Backstop: two candidates that resolved to one display name are one company counted twice.
    by_name = Counter(rec.company.name.casefold() for rec in found)
    records = []
    for rec in found:
        if by_name[rec.company.name.casefold()] > 1:
            ctx.reject("share_class_overlap")
        else:
            records.append(rec)
    return ctx.done(records, "congress_none_qualified")


# ── company_stakes (Drop 2b) ──────────────────────────────────────────────────
#
# A Trillion Club member's disclosed stake in a NAMED company with a disclosed DOLLAR figure and
# its basis ("invested", "committed up to", "carrying value", "fair value" — never a price).
# Catalogue stakes are allowed (`R.STAKES_INCLUDE_CATALOGUE`, main-session decision 1), newest
# ``as_of`` first, each posted once (its ledger key). FMP-free for the data: the club group, its
# rows and one stakes read; one profile batch makes the CompanyRefs.


@dataclass(frozen=True)
class _StakeCandidate:
    stake_id: str
    slug: str
    investor_symbol: str
    investee_symbol: Optional[str]
    investee_name: str
    kind: str
    value_usd: float
    value_basis: str
    ownership_pct: Optional[float]
    as_of: date
    verified_on: date
    source_title: str
    background: Optional[str]
    listed_since: Optional[date]
    local_listing: Optional[str]
    is_new: bool

    @property
    def ledger_key(self) -> str:
        return f"{R.LEDGER_PREFIX}company_stakes:{self.stake_id}"


def _stake_candidate(ctx: _Ctx, row: Mapping[str, Any], investors: Mapping[str, Optional[str]]
                     ) -> Optional[_StakeCandidate]:
    """One stake row through the pure gates (no I/O), or None (counted)."""
    slug = row.get("company_slug")
    if slug not in investors:
        return None                           # another company's row (defensive; the read filters)
    try:
        stake_id = str(uuid.UUID(str(row.get("id")))) if isinstance(row.get("id"), str) else None
    except ValueError:
        stake_id = None
    if stake_id is None:
        ctx.reject("stake_invalid")
        return None
    problem = stake_problem(row, ctx.run_date)
    if problem is not None:
        logger.info("company news company_stakes: stake %s refused as stake_invalid (%s)", stake_id, _scrub(problem))
        ctx.reject("stake_invalid")
        return None
    name = row.get("investee_name")
    if not isinstance(name, str):
        ctx.reject("stake_invalid")
        return None
    if "(" in name or "not named" in name.lower():
        ctx.reject("stake_aggregate")
        return None
    value, basis = _num(row.get("disclosed_value_usd")), row.get("value_basis")
    if value is None or value <= 0 or basis not in R.VALUE_BASES:
        ctx.reject("stake_no_figure")
        return None
    if value > R.STAKE_MAX_VALUE_USD:
        ctx.reject("stake_invalid")           # a unit error, not a stake
        return None
    as_of, verified_on = _day(row.get("as_of")), _day(row.get("verified_on"))
    if as_of is None or verified_on is None or as_of > verified_on or as_of > ctx.run_date:
        ctx.reject("stake_invalid")
        return None
    if (ctx.run_date - verified_on).days > R.STAKE_STALE_DAYS:
        ctx.reject("stake_stale")
        return None
    if (ctx.run_date - as_of).days > R.STAKE_MAX_AGE_DAYS:
        ctx.reject("stake_too_old")
        return None
    created = _day(row.get("created_at"))
    is_new = created is not None and created >= R.COMPANY_WEEKLY_LAUNCH
    if not is_new and not R.STAKES_INCLUDE_CATALOGUE:
        return None                           # catalogue switched off: not a candidate at all
    if R.company_name_problem(name) is not None:
        ctx.reject("stake_invalid")           # a name the post cannot draw or say
        return None
    title = row.get("source_title")
    if not R.free_text_ok(title, lo=2, hi=R.STAKE_SOURCE_TITLE_MAX):
        ctx.reject("stake_invalid")
        return None
    listed_raw = row.get("listed_since")
    listed_since = _day(listed_raw) if listed_raw is not None else None
    if listed_raw is not None and (listed_since is None or listed_since > ctx.run_date):
        ctx.reject("stake_invalid")           # "listed since <a future date>" would be false
        return None
    investor_symbol = R.canonical_symbol(investors[slug]) if investors[slug] else None
    if investor_symbol is None:
        ctx.reject("investor_unlisted")
        return None
    # A percentage is carried only when the source states it with no qualifier of its own
    # ("voting", "economic"): the record has no field for the qualifier, and "owns 25%" without
    # it can be false.
    pct = _num(row.get("ownership_pct"))
    if pct is not None and (not 0 < pct <= 100 or row.get("ownership_basis") not in (None, "")):
        pct = None
    background = row.get("background")
    background = background if R.free_text_ok(background, hi=R.STAKE_BACKGROUND_MAX) else None
    local = row.get("local_listing")
    local = local if R.free_text_ok(local, hi=R.STAKE_LOCAL_LISTING_MAX) else None
    return _StakeCandidate(
        stake_id=stake_id, slug=slug, investor_symbol=investor_symbol,
        investee_symbol=R.canonical_symbol(row.get("investee_us_symbol")) if row.get("investee_us_symbol") else None,
        investee_name=name, kind=row.get("kind"), value_usd=value, value_basis=basis, ownership_pct=pct,
        as_of=as_of, verified_on=verified_on, source_title=title, background=background,
        listed_since=listed_since, local_listing=local, is_new=is_new,
    )


def _names_agree(listed_name: str, stake_name: str) -> bool:
    """Does the investee's listing (its profile's display name) name the stake's company? Every
    significant word of the listing's name must be in the stake's name ("Rivian Automotive" ⊆
    "Rivian Automotive"; "Intel" ⊆ "Intel"). Otherwise the symbol is not trusted to be this
    investee and the stake is shown by name only (no logo, no link)."""
    words = _words(_fold_text(listed_name, 64))
    significant = words - _GENERIC_NAME_WORDS or words
    return bool(significant) and significant <= _words(_fold_text(stake_name, 128))


async def _collect_company_stakes(ctx: _Ctx) -> Candidates:
    if not settings.TRILLION_CLUB_ENABLED:
        logger.info("company news company_stakes: TRILLION_CLUB_ENABLED is off — no stakes")
        return ctx.done((), "stakes_feature_off")
    group = await ctx.step("club_group", lambda: ctx.club.get_group(), reason="club_unavailable")
    club = await _club_rows(ctx, reason="club_unavailable")
    investors: Dict[str, Optional[str]] = {}
    for item in [*(getattr(group, "companies", None) or ()), *(getattr(group, "also_in_club", None) or ())]:
        slug = getattr(item, "slug", None)
        if isinstance(slug, str) and slug in club and slug not in investors:
            symbol = club[slug].get("detail_symbol")
            investors[slug] = symbol if isinstance(symbol, str) and symbol.strip() else None
    if not investors:
        return ctx.done((), "stakes_none_qualified")
    members = sorted(investors)
    rows = await _rows(
        ctx, "club_stakes", ("club_stakes", tuple(members)),
        lambda sb: sb.table(_STAKES_TABLE).select(_STAKE_COLUMNS)
        .in_("company_slug", members).eq("published", True).eq("source_confidence", "primary")
        .limit(STAKES_READ_LIMIT),
        reason="club_unavailable",
    )
    if len(rows) >= STAKES_READ_LIMIT:
        raise MarketingNewsUnavailable(ctx.series, "stakes_truncated",
                                       f"stage=club_stakes; {len(rows)} rows at the {STAKES_READ_LIMIT}-row cap")
    eligible = [c for c in (_stake_candidate(ctx, r, investors) for r in rows) if c is not None]
    # Newest `as_of` first (main-session decision 1), a new row before a catalogue one on the
    # same day, then the id — deterministic.
    eligible.sort(key=lambda c: (-c.as_of.toordinal(), not c.is_new, c.stake_id))
    ctx.considered = len(eligible)
    fresh = []
    for c in eligible:
        if c.ledger_key in ctx.exclude:
            ctx.reject("already_posted")      # before any profile call
        else:
            fresh.append(c)
    batch = fresh[:STAKES_PROFILE_CANDIDATES]
    symbols: List[str] = []
    for c in batch:
        symbols.append(c.investor_symbol)
        if c.investee_symbol:
            symbols.append(c.investee_symbol)
    profiles = await _profiles(ctx, symbols) if symbols else {}
    records: List[Any] = []
    for c in batch:
        investor = R.company_from_profile(profiles.get(c.investor_symbol), c.investor_symbol, purpose="listing")
        if isinstance(investor, str):
            ctx.reject(investor)
            continue
        investee = None
        if c.investee_symbol:
            ref = R.company_from_profile(profiles.get(c.investee_symbol), c.investee_symbol, purpose="common")
            if isinstance(ref, R.CompanyRef) and ref.symbol != investor.symbol and _names_agree(ref.name, c.investee_name):
                investee = ref
            else:
                logger.info("company news company_stakes: stake %s investee %s shown by name only (%s)",
                            c.stake_id, c.investee_symbol, ref if isinstance(ref, str) else "name or symbol disagrees")
        try:
            records.append(R.CompanyStake(
                series="company_stakes", stake_id=c.stake_id, investor=investor, investee_name=c.investee_name,
                investee=investee, kind=c.kind, value_usd=c.value_usd, value_basis=c.value_basis,
                ownership_pct=c.ownership_pct, as_of=c.as_of, verified_on=c.verified_on,
                source_title=c.source_title, background=c.background, listed_since=c.listed_since,
                local_listing=c.local_listing, is_new=c.is_new,
            ))
        except ValueError as e:
            logger.info("company news company_stakes: stake %s refused as record_invalid (%s)", c.stake_id, _describe(e))
            ctx.reject("record_invalid")
            continue
        if len(records) >= ctx.limit:
            break
    ctx.check_profiles()
    if records:
        return ctx.done(records, None)
    if eligible and not fresh:
        return ctx.done((), "stakes_none_unposted")
    if not R.STAKES_INCLUDE_CATALOGUE and not eligible:
        return ctx.done((), "stakes_none_new")
    return ctx.done((), "stakes_none_qualified")


# ── earnings (Drop 2b) ────────────────────────────────────────────────────────
#
# One company per record: its reported EPS against the analyst estimate (and revenue against its
# estimate when the pair is plausible), from the previous seven ET days' calendar, ONE day per
# call (a multi-day answer is cut at 4,000 rows, newest kept). Ranked by |actual − estimate| /
# |estimate|, then market cap (read in the gate only), then symbol.


async def _calendar_day(fmp: Any, *, from_date: str, to_date: str) -> List[Dict[str, Any]]:
    """One ET day of the earnings calendar. A day at FMP's silent row cap is probably CUT:
    ``earnings_calendar_truncated`` (raised as the series' own exception — a new exception class
    here would need its own `classify_exception` branch)."""
    rows = await fmp.get_earnings_calendar(from_date=from_date, to_date=to_date)
    if not isinstance(rows, list):
        raise FMPUnavailableException(f"earnings calendar {from_date} returned {type(rows).__name__}, not a list")
    if len(rows) >= EARNINGS_TRUNCATION_ROWS:
        raise MarketingNewsUnavailable("earnings", "earnings_calendar_truncated",
                                       f"stage=earnings_calendar; {from_date}: {len(rows)} rows "
                                       f"(cap {EARNINGS_TRUNCATION_ROWS})")
    return rows


async def _earnings_days(ctx: _Ctx, days: Sequence[date]) -> Dict[date, List[Dict[str, Any]]]:
    """{day: that day's calendar rows}: memoized per day (30 min); the missing days are fetched
    together, all-or-nothing (`fetch_calendar_days`), and only then memoized."""
    out: Dict[date, List[Dict[str, Any]]] = {}
    missing: List[date] = []
    for d in days:
        hit = _memo_get(("earnings_day", d.isoformat()))
        if hit is _MISS:
            missing.append(d)
        else:
            out[d] = hit
    if missing:
        async def fetch() -> Dict[date, List[Dict[str, Any]]]:
            got = await fetch_calendar_days(lambda **kw: _calendar_day(ctx.fmp, **kw), list(missing))
            for d, rows in got.items():
                _memo_put(("earnings_day", d.isoformat()), rows, EARNINGS_DAY_TTL_SECONDS)
            return got

        key = ("earnings_days", tuple(d.isoformat() for d in missing))
        got = await ctx.step("earnings_calendar", lambda: _shared(key, 0, fetch),
                             reason="earnings_calendar_unavailable")
        for d in missing:
            if d not in got:
                raise MarketingNewsUnavailable(ctx.series, "earnings_calendar_unavailable",
                                               f"stage=earnings_calendar; {d} missing from the answer")
            out[d] = got[d]
    return out


def _first_num(row: Mapping[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        v = _num(row.get(k))
        if v is not None:
            return v
    return None


def _earnings_row(row: Mapping[str, Any]) -> Tuple[Optional[str], Any]:
    """One calendar row → (symbol, its report or a rejection reason), or (None, None) when it is
    no candidate at all (not reported yet), or (None, reason) for an unusable symbol."""
    actual = _first_num(row, "epsActual", "eps")
    estimate = _first_num(row, "epsEstimated", "epsEstimate")
    if actual is None or estimate is None:
        return None, None                     # not reported (yet): not a candidate
    raw = row.get("symbol")
    if not isinstance(raw, str) or "." in raw:
        return None, "symbol_grammar"         # a foreign line (ZOO.L, 005930.KS) or no symbol
    sym = R.canonical_symbol(raw)
    if sym is None:
        return None, R.symbol_problem(raw) or "symbol_grammar"
    if abs(estimate) < R.EPS_MIN_ABS_ESTIMATE:
        return sym, "eps_estimate_too_small"
    if eps_digit_shift_suspect(actual, estimate, None):
        return sym, "eps_digit_shift"         # no filed GAAP EPS to break the tie: refused
    if abs(actual - estimate) > EARNINGS_MAX_GAP_MULTIPLE * abs(estimate):
        return sym, "eps_gap_implausible"
    ra = _first_num(row, "revenueActual", "revenue")
    re_ = _first_num(row, "revenueEstimated", "revenueEstimate")
    return sym, {"actual": actual, "estimate": estimate, "ra": ra, "re": re_}


async def _collect_earnings(ctx: _Ctx) -> Candidates:
    days = [ctx.run_date - timedelta(days=i) for i in range(EARNINGS_WINDOW_DAYS, 0, -1)]
    by_day = await _earnings_days(ctx, days)
    reports: Dict[str, List[Tuple[date, Any]]] = {}
    for d in days:
        for row in by_day.get(d) or ():
            if not isinstance(row, dict):
                continue
            sym, got = _earnings_row(row)
            if got is None:
                continue
            if sym is None:
                ctx.reject(got)
                continue
            reports.setdefault(sym, []).append((d, got))
    best: Dict[str, Dict[str, Any]] = {}
    for sym, found in reports.items():
        refused = [got for _d, got in found if isinstance(got, str)]
        if refused:
            ctx.reject(refused[-1])           # any refused row refuses the company (its newest reason)
            continue
        # One report per company. Two reported rows in one week must agree on EPS — otherwise
        # which one is the result is a guess (`record_invalid`). Revenue is used only when every
        # row that carries it agrees and the pair is plausible; else it is omitted, never guessed.
        if len({(g["actual"], g["estimate"]) for _d, g in found}) > 1:
            ctx.reject("record_invalid")
            continue
        day, first = max(found, key=lambda t: t[0])
        revenue_pairs = {(g["ra"], g["re"]) for _d, g in found if g["ra"] is not None or g["re"] is not None}
        ra = re_ = None
        revenue_dropped = False
        if revenue_pairs:
            lo, hi = R.REVENUE_RATIO_BAND
            pa, pe = next(iter(revenue_pairs)) if len(revenue_pairs) == 1 else (None, None)
            if pa is not None and pe is not None and pa > 0 and pe > 0 and lo <= pa / pe <= hi:
                ra, re_ = pa, pe
            else:
                revenue_dropped = True
        best[sym] = {"day": day, "ratio": abs(first["actual"] - first["estimate"]) / abs(first["estimate"]),
                     "actual": first["actual"], "estimate": first["estimate"], "ra": ra, "re": re_,
                     "revenue_dropped": revenue_dropped}
    ctx.considered = len(best)
    pre: List[Tuple[str, Dict[str, Any]]] = []
    for sym, b in sorted(best.items(), key=lambda kv: (-kv[1]["ratio"], kv[0])):
        if f"{R.LEDGER_PREFIX}earnings:{sym}:{b['day'].isoformat()}" in ctx.exclude:
            ctx.reject("already_posted")      # before any profile call
            continue
        pre.append((sym, b))
    held: List[Tuple[Tuple[float, float, str], Any]] = []     # (rank key, record)
    reads = [0]                               # currency reads so far (shared by every batch)
    i = 0
    while i < len(pre) and len(held) < ctx.limit:
        if i >= EARNINGS_MAX_PROFILES or reads[0] >= EARNINGS_MAX_CURRENCY_READS:
            logger.info("company news earnings: %s cap reached with %d of %d report(s) unprofiled and %d "
                        "record(s) kept", "profile" if i >= EARNINGS_MAX_PROFILES else "currency-read",
                        len(pre) - i, len(pre), len(held))
            break
        batch = pre[i:min(i + EARNINGS_PROFILE_CANDIDATES, EARNINGS_MAX_PROFILES)]
        i += len(batch)
        try:
            await _earnings_batch(ctx, batch, held, reads)
        except MarketingNewsUnavailable as e:
            # A record already held is fully checked: out of budget on a LATER batch ends the walk
            # with it (never discarded); with none held — or any other failure — the series raises.
            if e.reason != "budget_exhausted" or not held:
                raise
            logger.warning("company news earnings: out of budget after %d record(s) — the walk ends with them (%s)",
                           len(held), _describe(e))
            break
    # The series' rank order over everything profiled: a later batch can only tie an earlier one on
    # the gap ratio, and then the market cap decides.
    held.sort(key=lambda t: t[0])
    return ctx.done([rec for _key, rec in held], "earnings_none_qualified")


async def _earnings_batch(ctx: _Ctx, batch: Sequence[Tuple[str, Dict[str, Any]]],
                          held: List[Tuple[Tuple[float, float, str], Any]], reads: List[int]) -> None:
    """One profile batch (rank order): the company gate, then — in rank order, in waves of the
    reports still needed — the record and its reporting-currency check, appending ``(rank key,
    record)`` to ``held``."""
    profiles = await _profiles(ctx, [s for s, _b in batch])
    ctx.check_profiles()
    gated: List[Tuple[float, float, str, Any, Dict[str, Any]]] = []
    for sym, b in batch:
        company = R.company_from_profile(profiles.get(sym), sym, purpose="earnings")
        if isinstance(company, str):
            ctx.reject(company)
            continue
        cap = _num((profiles.get(sym) or {}).get("marketCap")) or 0.0   # the tie-break only; never leaves
        gated.append((-b["ratio"], -cap, sym, company, b))
    gated.sort(key=lambda t: (t[0], t[1], t[2]))
    queue = list(gated)
    while queue and len(held) < ctx.limit:
        if reads[0] >= EARNINGS_MAX_CURRENCY_READS:
            logger.info("company news earnings: currency-read cap %d reached with %d record(s) kept",
                        EARNINGS_MAX_CURRENCY_READS, len(held))
            return
        want = min(ctx.limit - len(held), EARNINGS_MAX_CURRENCY_READS - reads[0])
        wave: List[Tuple[Tuple[float, float, str], Dict[str, Any], Any]] = []
        while queue and len(wave) < want:
            neg_ratio, neg_cap, sym, company, b = queue.pop(0)
            try:
                rec = R.EarningsReport(
                    series="earnings", company=company, report_date=b["day"],
                    # The calendar states no reliable fiscal period: the post says "reported on
                    # <date>", never a quarter it cannot vouch for.
                    period_end=None, eps_actual=b["actual"], eps_estimate=b["estimate"],
                    revenue_actual=b["ra"], revenue_estimate=b["re"],
                )
            except ValueError as e:
                logger.info("company news earnings: %s refused as record_invalid (%s)", sym, _describe(e))
                ctx.reject("record_invalid")
                continue
            wave.append(((neg_ratio, neg_cap, sym), b, rec))
        if not wave:
            return
        reads[0] += len(wave)
        currencies = await asyncio.gather(*(_reporting_currency(ctx, key[2]) for key, _b, _rec in wave),
                                          return_exceptions=True)
        for got in currencies:
            if isinstance(got, (MarketingNewsUnavailable, asyncio.CancelledError)):
                raise got
        for (key, b, rec), cur in zip(wave, currencies):
            sym = key[2]
            if isinstance(cur, BaseException):
                logger.warning("company news earnings: %s reporting currency unreadable — refused as "
                               "non_usd_reporter (%s)", sym, _describe(cur))
                ctx.reject("non_usd_reporter")
                continue
            if cur != "USD":
                logger.info("company news earnings: %s reports in %s, not USD — refused (its EPS and revenue "
                            "are not dollars)", sym, _scrub(cur, 12))
                ctx.reject("non_usd_reporter")
                continue
            if b["revenue_dropped"]:
                ctx.reject("revenue_dropped")
            held.append((key, rec))


async def _fetch_reporting_currency(fmp: Any, sym: str) -> str:
    """The REPORTING currency of ``sym``'s latest quarterly income statement (upper-cased). No
    statement row, or one without a ``reportedCurrency``, raises (the caller refuses the report)."""
    rows = await fmp.get_income_statement(sym, "quarter", limit=1)
    dated = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    if not dated:
        raise FMPUnavailableException(f"quarterly income statement of {sym}: {type(rows).__name__} with no row")
    latest = max(dated, key=lambda r: str(r.get("date") or ""))
    cur = latest.get("reportedCurrency")
    if not isinstance(cur, str) or not cur.strip():
        raise FMPUnavailableException(f"quarterly income statement of {sym}: no reportedCurrency")
    return cur.strip().upper()


async def _reporting_currency(ctx: _Ctx, sym: str) -> str:
    """`_fetch_reporting_currency`, memoized like a calendar day (an exception never is). Budget
    exhaustion raises `MarketingNewsUnavailable`; any other failure passes through raw (the caller
    refuses THAT report — fail closed — and the week keeps the rest)."""
    return await ctx.step(
        "earnings_currency",
        lambda: _shared(("reported_currency", sym), EARNINGS_DAY_TTL_SECONDS,
                        lambda: _fetch_reporting_currency(ctx.fmp, sym)),
        reason="earnings_calendar_unavailable", passthrough=(Exception,),
    )


# ── theme_explainer (Drop 2b, a fallback) ─────────────────────────────────────
#
# What the companies of one Emerging Frontiers theme sell: the theme's NAME and its ticker list
# (never its performance, momentum, ETF-based reasons, image, subtitle or any Gemini text), each
# member through the company gate, and the largest revenue segment of the first members (the
# revenue breakdown service, its own two-tier cache first). A member without a segment fact is
# still listed: the record keeps every member that passed the gates (6..24), and the theme's full
# ticker count before any gate (`theme_size`, the app card's number).


def _segment_fact(bd: Any, run_date: date) -> Optional[Tuple[str, float, str]]:
    """(largest named segment, its share of revenue in (0, 1], fiscal year) — or None whenever
    the claim "largest segment, X% of revenue" could be wrong: a degraded breakdown, an old or
    unreadable fiscal year, segments that include intersegment sales (eliminations), no reported
    revenue, one segment only, a tie for the top, an unnamed remainder bigger than the top, or
    segments adding up to more than revenue."""
    if list(getattr(bd, "degraded", None) or ()):
        return None
    fy = getattr(bd, "fiscal_year", None)
    if not isinstance(fy, str) or not re.fullmatch(r"[0-9]{4}", fy):
        return None
    if not (run_date.year - THEME_FACT_MAX_AGE_YEARS <= int(fy) <= run_date.year):
        return None
    elim = _num(getattr(bd, "intersegment_eliminations", None))
    if elim is not None and elim > 0:
        return None
    revenue = _num(getattr(bd, "reported_revenue", None))
    if revenue is None or revenue <= 0:
        return None
    named, rest = _segments(bd)
    if not named or len(named) + (1 if rest > 0 else 0) < 2:
        return None
    ordered = sorted(named, key=lambda s: -s.value_usd)
    top = ordered[0]
    if len(ordered) > 1 and ordered[1].value_usd == top.value_usd:
        return None
    if rest > top.value_usd:
        return None
    if math.fsum(s.value_usd for s in named) + rest > revenue * (1.0 + THEME_SEGMENT_SUM_TOLERANCE):
        return None
    share = top.value_usd / revenue
    if not 0 < share <= 1:
        return None
    return top.name, share, fy


async def _theme_facts(ctx: _Ctx, refs: Sequence[Any]) -> Dict[str, Tuple[str, float, str]]:
    """{symbol: segment fact} for ``refs`` (≤ `THEME_SEGMENT_LOOKUPS`, `THEME_LOOKUP_CONCURRENCY`
    at a time). One member's failed lookup is that member's missing fact (WARNING); every lookup
    failing is the service being down → ``revenue_unavailable``."""
    if not refs:
        return {}
    sem = asyncio.Semaphore(THEME_LOOKUP_CONCURRENCY)

    async def one(sym: str) -> Any:
        async with sem:
            return await ctx.revenue.get_revenue_breakdown(sym)

    results = await ctx.step(
        "theme_segments",
        lambda: asyncio.gather(*(one(r.symbol) for r in refs), return_exceptions=True),
        reason="revenue_unavailable",
    )
    failed = [r for r in results if isinstance(r, BaseException)]
    if len(failed) == len(results):
        raise MarketingNewsUnavailable(ctx.series, "revenue_unavailable",
                                       f"stage=theme_segments; all {len(results)} lookups failed; "
                                       f"first {_describe(failed[0])}")
    out: Dict[str, Tuple[str, float, str]] = {}
    for ref, bd in zip(refs, results):
        if isinstance(bd, BaseException):
            logger.warning("company news theme_explainer: %s segment lookup failed — listed without a "
                           "segment (%s)", ref.symbol, _describe(bd))
            continue
        fact = _segment_fact(bd, ctx.run_date)
        if fact is not None:
            out[ref.symbol] = fact
    return out


async def _collect_theme_explainer(ctx: _Ctx) -> Candidates:
    rows = await _rows(
        ctx, "themes_explainer", ("themes_explainer",),
        lambda sb: sb.table(_THEMES_TABLE).select(_THEME_COLUMNS)
        .eq("is_active", True).order("sort_order").limit(200),
        reason="themes_unavailable",
    )

    def order(r: Mapping[str, Any]) -> Tuple[int, str]:
        so = r.get("sort_order")
        return (so if isinstance(so, int) and not isinstance(so, bool) else 10 ** 9, str(r.get("slug") or ""))

    records: List[Any] = []
    evaluated = 0
    for r in sorted(rows, key=order):
        if len(records) >= ctx.limit or evaluated >= THEME_MAX_EVALUATED:
            break
        if r.get("is_active") is not True:
            continue
        slug = r.get("slug")
        if not isinstance(slug, str) or not _THEME_SLUG_RE.fullmatch(slug):
            ctx.reject("record_invalid")
            continue
        raw_as_of = r.get("tickers_as_of")
        as_of = _day(raw_as_of) if isinstance(raw_as_of, str) else None
        if as_of is None or as_of > ctx.run_date or (ctx.run_date - as_of).days > R.THEME_STALE_DAYS:
            ctx.reject("theme_stale")
            continue
        ctx.considered += 1
        if f"{R.LEDGER_PREFIX}theme_explainer:{slug}:{as_of.isoformat()}" in ctx.exclude:
            ctx.reject("already_posted")      # before any profile call
            continue
        title = r.get("title")
        problem = R.theme_title_problem(title)
        if problem:
            ctx.reject(problem)
            continue
        tickers, blocked_raw = r.get("tickers"), r.get("blocked_tickers")
        if not isinstance(tickers, list) or (blocked_raw is not None and not isinstance(blocked_raw, list)):
            ctx.reject("record_invalid")
            continue
        # The theme's size as the app card shows it, BEFORE any gate (the shared contract's
        # `theme_size`): the copy says "{n} of its {m} companies" whenever a gate dropped one.
        size = R.theme_ticker_count(tickers)
        blocked = {_sym_key(b) for b in (blocked_raw or ()) if isinstance(b, str)}
        members: List[str] = []
        for t in tickers:
            if isinstance(t, str) and _sym_key(t) in blocked:
                continue
            sym = R.canonical_symbol(t)
            if sym is None:
                ctx.reject(R.symbol_problem(t) or "symbol_grammar")
                continue
            if sym not in members and sym not in blocked:
                members.append(sym)
        lo, hi = R.THEME_MEMBERS
        if len(members) > hi:
            ctx.reject("theme_too_large")
            continue
        if len(members) < lo:
            ctx.reject("theme_members_thin")
            continue
        evaluated += 1
        # Review round 9 (#10): a theme already held was built and checked on complete data; a
        # LATER theme's lookups failing or running out of budget (profiles, segments) ends the walk
        # with the held record(s) — never throws them away. With none held the series raises as
        # before (the `_collect_earnings` / `_collect_money_map` precedent, every upstream reason).
        try:
            profiles = await _profiles(ctx, members)
            refs: List[Any] = []
            names: Set[str] = set()
            for sym in members:
                company = R.company_from_profile(profiles.get(sym), sym, purpose="listing")
                if isinstance(company, str):
                    ctx.reject(company)
                    continue
                if company.name.casefold() in names:
                    ctx.reject("share_class_overlap")     # GOOG beside GOOGL: one company, listed once
                    continue
                names.add(company.name.casefold())
                refs.append(company)
            if len(refs) < lo:
                ctx.reject("theme_members_thin")
                continue
            facts = await _theme_facts(ctx, refs[:THEME_SEGMENT_LOOKUPS])
        except MarketingNewsUnavailable as e:
            if not records:
                raise
            logger.warning("company news theme_explainer: %s unavailable at theme %s with %d record(s) held — "
                           "returning them (%s)", e.reason, slug, len(records), e.detail)
            break
        if len(facts) < R.THEME_MIN_FACTS:
            ctx.reject("theme_facts_thin")
            continue
        try:
            members_out = tuple(
                R.ThemeMember(company=ref, top_segment=facts[ref.symbol][0] if ref.symbol in facts else None,
                              top_segment_share=facts[ref.symbol][1] if ref.symbol in facts else None,
                              fiscal_year=facts[ref.symbol][2] if ref.symbol in facts else None)
                for ref in refs)
            records.append(R.ThemeExplainer(series="theme_explainer", slug=slug, title=title,
                                            members=members_out, tickers_as_of=as_of, theme_size=size))
        except ValueError as e:
            logger.info("company news theme_explainer: %s refused as record_invalid (%s)", slug, _describe(e))
            ctx.reject("record_invalid")
    ctx.check_profiles()
    return ctx.done(records, "theme_none_qualified")


# ── the public entry points ───────────────────────────────────────────────────

#: One collector per series whose code exists (2a + 2b). Pinned == `selection.SHIPPED_SERIES` once
#: the plumbing step ships the 2b ids there; production still runs only what the per-series switch
#: MARKETING_NEWS_SERIES lists (its default is the 2a four).
COLLECTORS: Dict[str, Callable[[_Ctx], Awaitable[Candidates]]] = {
    "ceo_buys": _collect_ceo_buys,
    "insider_buys": _collect_insider_buys,
    "thirteen_f": _collect_thirteen_f,
    "congress_count": _collect_congress_count,
    "company_stakes": _collect_company_stakes,
    "earnings": _collect_earnings,
    "money_map": _collect_money_map,
    "theme_explainer": _collect_theme_explainer,
}


async def candidates(series: str, *, run_date: date, exclude: FrozenSet[str], limit: int = 5,
                     deadline: float, deps: Optional[NewsDeps] = None) -> Candidates:
    """The qualifying records of one series for ``run_date``, best first (contract D6).

    ``exclude`` holds the recent ledger refs (`marketing_scripts.source_ref`); a candidate whose
    ledger key is in it is dropped before any profile or logo call and counted ``already_posted``.
    ``deadline`` is an absolute `NewsDeps.monotonic` time. Raises ValueError for an unknown series
    or a bad argument, `MarketingNewsUnavailable` for everything that is not an honest answer."""
    if series not in COLLECTORS:
        raise ValueError(f"unknown or unshipped company-news series {series!r}")
    if not isinstance(run_date, date) or isinstance(run_date, datetime):
        raise ValueError("run_date must be a date")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(f"limit must be a positive int, got {limit!r}")
    if _num(deadline) is None:
        raise ValueError("deadline must be a finite monotonic time")
    deps = deps or NewsDeps()
    ctx = _Ctx(series, run_date, frozenset(x for x in (exclude or ()) if isinstance(x, str)),
               limit, float(deadline), deps)
    started = deps.monotonic()
    try:
        if ctx.left() < MIN_STEP_SECONDS:
            raise MarketingNewsUnavailable(series, "budget_exhausted", f"stage=start left={ctx.left():.1f}s")
        result = await COLLECTORS[series](ctx)
    except MarketingNewsUnavailable as e:
        logger.warning("marketing news UNAVAILABLE series=%s run_date=%s reason=%s detail=%s rejections=%s",
                       series, run_date, e.reason, e.detail, dict(ctx.rejections))
        raise
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — a bug, not an upstream answer
        logger.exception("marketing news INTERNAL ERROR series=%s run_date=%s (%s)",
                         series, run_date, _describe(e))
        raise MarketingNewsUnavailable(series, "internal_error", _describe(e)) from e
    logger.info(
        "marketing news candidates series=%s run_date=%s candidates=%d kept=%d skip=%s "
        "rejections=%s keys=%s in %.1fs",
        series, run_date, ctx.considered, len(result.records), result.skip_reason,
        dict(result.rejections), [R.ledger_key(r) for r in result.records], deps.monotonic() - started,
    )
    return result


async def fetch_logo(symbol: str, *, max_bytes: int, timeout: float,
                     deps: Optional[NewsDeps] = None) -> Optional[Tuple[bytes, str]]:
    """The company's logo bytes and the response's content type, or None. Never raises
    (cancellation aside) and never validates the bytes (`logo_check.inspect_logo` does).

    Only ``https://images.financialmodelingprep.com/symbol/<SYM>.png`` is requested — built from
    the validated symbol and required to equal the profile's ``image`` exactly — and only while
    the profile says it is not the vendor's default image. No redirects; status 200 only;
    ``Accept-Encoding: identity`` and an encoded answer refused; the raw body is streamed and
    abandoned past ``max_bytes`` wire bytes. A WARNING names the symbol and the reason, never
    the URL."""
    sym = R.canonical_symbol(symbol)
    if sym is None:
        logger.warning("marketing logo: refused symbol=%r reason=bad_symbol", str(symbol)[:16])
        return None
    try:
        return await asyncio.wait_for(_fetch_logo(sym, int(max_bytes), float(timeout), deps or NewsDeps()),
                                      timeout=float(timeout))
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.warning("marketing logo: no logo symbol=%s reason=timeout", sym)
    except Exception as e:  # noqa: BLE001 — a logo never fails a candidate
        logger.warning("marketing logo: no logo symbol=%s reason=error (%s)", sym, _describe(e))
    return None


async def _fetch_logo(sym: str, max_bytes: int, timeout: float, deps: NewsDeps) -> Optional[Tuple[bytes, str]]:
    if max_bytes <= 0 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("max_bytes and timeout must be positive")
    hit = _memo_get(("logo", sym))
    if hit is not _MISS:
        return hit

    def no(reason: str) -> None:
        logger.warning("marketing logo: no logo symbol=%s reason=%s", sym, reason)

    profile = _memo_get(("profile", sym))
    if profile is _MISS:
        fmp = deps.fmp if deps.fmp is not None else get_fmp_client()
        profile = (await _shared(("profiles", (sym,)), 0, lambda: _fetch_profiles(fmp, [sym]))).get(sym)
    if not isinstance(profile, Mapping):
        no("no_profile")
        return None
    if profile.get("defaultImage") is not False:
        no("default_image")
        return None
    url = LOGO_URL_TEMPLATE.format(symbol=sym)
    if profile.get("image") != url:
        no("url_mismatch")
        return None
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=False,
                                 transport=deps.http) as client:
        # Ask for the bytes as stored, refuse any encoded answer, and count WIRE bytes: httpx
        # decodes gzip/deflate/brotli per network read with no output limit, so a decoded-size
        # cap would let a few hundred compressed bytes inflate to hundreds of MB in this (the
        # single web) process before refusing them.
        async with client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as resp:
            if resp.status_code != 200:
                no(f"http_{resp.status_code}")
                return None
            if resp.headers.get("content-encoding", "").strip().lower() not in _IDENTITY_ENCODINGS:
                no("encoded")
                return None
            declared = resp.headers.get("content-length")
            if declared is not None and declared.strip().isdigit() and int(declared) > max_bytes:
                no("too_large")
                return None
            chunks: List[bytes] = []
            size = 0
            async for chunk in resp.aiter_raw():
                size += len(chunk)
                if size > max_bytes:
                    no("too_large")
                    return None
                chunks.append(chunk)
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    body = b"".join(chunks)
    if not body:
        no("empty")
        return None
    _memo_put(("logo", sym), (body, ctype), LOGO_TTL_SECONDS)
    return body, ctype


__all__ = [
    "MarketingNewsUnavailable", "Candidates", "NewsDeps", "candidates", "fetch_logo", "COLLECTORS",
    "clear_memo", "MIN_STEP_SECONDS", "INSIDER_RAW_TTL_SECONDS", "PROFILE_TTL_SECONDS",
    "BUILT_13F_TTL_SECONDS", "LOGO_TTL_SECONDS", "LIST_TTL_SECONDS", "INSIDER_PAGE_SIZE",
    "INSIDER_MAX_PAGES", "INSIDER_PROFILE_CANDIDATES", "INSIDER_MAX_CAP_SHARE", "MAX_LIVE_13F_BUILDS",
    "THIRTEEN_F_MAX_UNAVAILABLE", "THIRTEEN_F_MAX_PROFILED", "THIRTEEN_F_KIND_RESERVE", "THIRTEEN_F_MIN_CHANGE",
    "THIRTEEN_F_MATERIAL_MIN_USD", "THIRTEEN_F_MATERIAL_SHARE",
    "MONEY_MAP_MAX_ATTEMPTS", "MONEY_MAP_MAX_CONTENT_REJECTIONS", "MONEY_MAP_MAX_LOOKUPS",
    "MONEY_MAP_MAX_OTHER_SHARE",
    "INSIDER_AMENDMENT_MAX_SYMBOLS", "INSIDER_AMENDMENT_PAGE_SIZE", "INSIDER_AMENDMENT_MAX_PAGES",
    "CEO_TITLE_WORDS", "CFO_TITLE_WORDS", "DIRECTOR_TITLE_WORDS",
    "MONEY_MAP_STALE_DAYS", "LOGO_URL_TEMPLATE",
    "CONGRESS_WALK_LIMITS", "CONGRESS_COVERAGE_MARGIN_DAYS", "CONGRESS_DUE_DAYS",
    "CONGRESS_PROFILE_CANDIDATES", "CONGRESS_TTL_SECONDS", "CONGRESS_IDENTITY_FIELDS",
    "EARNINGS_WINDOW_DAYS", "EARNINGS_TRUNCATION_ROWS", "EARNINGS_DAY_TTL_SECONDS",
    "EARNINGS_PROFILE_CANDIDATES", "EARNINGS_MAX_PROFILES", "EARNINGS_MAX_CURRENCY_READS",
    "EARNINGS_MAX_GAP_MULTIPLE", "STAKES_READ_LIMIT",
    "STAKES_PROFILE_CANDIDATES", "THEME_MAX_EVALUATED", "THEME_SEGMENT_LOOKUPS",
    "THEME_LOOKUP_CONCURRENCY", "THEME_FACT_MAX_AGE_YEARS", "THEME_SEGMENT_SUM_TOLERANCE",
]
