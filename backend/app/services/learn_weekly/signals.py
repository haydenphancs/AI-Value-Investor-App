"""Learn weekly: the topic step's ranking signals (plan "Wiser weekly content engine" §1.5).

THE ONLY `learn_weekly` MODULE THAT IMPORTS FMP (`tests/test_learn_weekly_fmp_boundary.py`).

Why the boundary exists: the FMP Order Form licenses display "through the Licensee's
authenticated platform" and bans data "in any downloadable fashion". A Learn article is stored,
narrated and cached offline on the device (400 MB), so nothing FMP may be FROZEN into one. FMP
therefore only RANKS the week's topics, and what leaves this module is
`contracts.TickerSignal` / `contracts.ThemeInput` — counts, booleans and identifiers. No FMP
string (a headline, a company name, a description, an exchange name) and no FMP figure (a market
cap, a price) ever leaves: the size check reads the market cap and returns a BOOL, the news reader
counts rows and returns INTS. The sentinel test feeds FMP fixtures carrying unique strings and a
7-digit number through `collect_signals` and the topic step and proves none comes out.

What this module reads, and from where:

* `universe(now)` — the tickers worth scoring this week: the active Emerging Frontiers theme
  members (`trending_themes`, our own table) ∪ this week's earnings-calendar names, pre-ordered by
  the calendar's revenue estimate (a size proxy before any profile is read) ∪ the owner's extra
  list `backend/data/learn_candidates.json` (CRWV, the owner's own example, sits in no basket).
  Capped at `UNIVERSE_CAP` with a WARNING — theme members first, then the owner's list, then the
  calendar, so a busy earnings week can never crowd out a basket.
* `collect_signals(now, tickers)` — per ticker, concurrently and bounded:
  - NEWS (FMP `news/stock`): one paginated read over the last 9 weeks, counted per WEEK. A "week"
    here is 7 ET calendar days ending on the run's ET date (week 0 = today and the 6 days before
    it), NOT an ISO week: the topic step runs on THURSDAY, and an ISO week would compare Monday-
    Thursday (≈ 3.75 days) against full 7-day baseline weeks — a built-in ~45% under-count of the
    very week being ranked. A page cap that stops the walk before the window's start marks the
    count SATURATED (a lower bound), and only the weeks the walk fully covered enter the baseline
    — an uncovered week read as 0 would fake a spike.
  - SIZE (FMP `profile`): market cap ≥ $2B on a major US exchange, not an ETF/fund, actively
    trading → `size_ok` True/False. A FAILED read is `None` (unknown), never False.
  - CIK from SEC's own `company_tickers.json` (never FMP), then SEC submissions
    (`sec_facts.company_summary`): 8-K counts per week, this week's 8-K item codes, and whether
    the company files 20-F/40-F/6-K (`foreign_filer`).
  - EARNINGS this week from the calendar, one call per ET day (FMP cuts a multi-day answer at
    4,000 rows keeping the NEWEST dates — `earnings_window_service.fetch_calendar_days`).
  EVERY raised exception — `FMPRateLimitException` and `FMPAuthException` included — and every
  `EmptyAfterFailure` (`fetch_failed`) answer makes that ticker's news count MISSING (`None`),
  never zero: `topic_scorer` aborts the ranking when more than 20% of the universe is missing, so
  an outage can never read as "a quiet week for everyone".
* `theme_inputs()` — the active themes with their members; a theme is ELIGIBLE only with ≥ 2
  verified case-library primers (`case_library.primer_count`), and `last_covered` is the ET date
  of the newest approved / published Deep Dive on it (`ledger.list_pieces`).

Caches (process-local, no Supabase tier — nothing FMP-derived is ever stored, plan §1.5): SEC's
ticker map for 24 h, the earnings calendar for 30 min. Single-flight by an `asyncio.Lock`, not a
shared future: a weekly job has no concurrent callers worth deduplicating beyond that.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.integrations.sec_edgar import (
    SECEdgarClient,
    SECException,
    get_sec_edgar_client,
    normalize_cik,
)
from app.services.earnings_window_service import fetch_calendar_days
from app.services.learn_weekly import case_library, ledger, schedule, sec_facts
from app.services.learn_weekly import contracts as C
from app.services.learn_weekly.contracts import ThemeInput, TickerSignal
from app.services.learn_weekly.errors import LearnWeeklyError

logger = logging.getLogger(__name__)

__all__ = [
    "SignalsUnavailable",
    "WEEKS",
    "WINDOW_DAYS",
    "NEWS_PAGE_SIZE",
    "NEWS_MAX_PAGES",
    "UNIVERSE_CAP",
    "MIN_MARKET_CAP_USD",
    "MAJOR_US_EXCHANGES",
    "MIN_THEME_PRIMERS",
    "CANDIDATES_PATH",
    "load_learn_candidates",
    "parse_company_tickers",
    "ticker_cik_map",
    "cik_for",
    "universe",
    "collect_signals",
    "theme_inputs",
    "theme_of_piece",
    "week_index",
    "clear_caches",
]

# ── Constants ─────────────────────────────────────────────────────────────────────────────────

#: This week + the 8 trailing weeks the scorer's baseline median is taken over.
WEEKS = 9
WINDOW_DAYS = WEEKS * 7
#: Articles per news page, and pages per ticker: ≤ 3,000 articles cover ~7 weeks of a mega-cap
#: (~60 a day) and every ordinary name whole. Most tickers need ONE call.
NEWS_PAGE_SIZE = 500
NEWS_MAX_PAGES = 6
#: Tickers scored in parallel (each one is ≤ NEWS_MAX_PAGES + 1 FMP calls and one SEC read).
SIGNAL_CONCURRENCY = 6
#: The universe ceiling (plan: ~150-300 FMP calls a week).
UNIVERSE_CAP = 250
#: Plan §1.5 hard exclusion: < $2B, or not on a major US exchange.
MIN_MARKET_CAP_USD = 2_000_000_000.0
#: A copy, on purpose, of marketing's `company_news_rules.MAJOR_US_EXCHANGES` (that package's
#: rules must stay free to move without changing what Learn ranks).
MAJOR_US_EXCHANGES: FrozenSet[str] = frozenset({"NASDAQ", "NYSE", "AMEX"})
#: An earnings-calendar name joins the universe only with a quarterly revenue (estimate, else
#: actual) at least this large — a loose size proxy read BEFORE any profile (≈ $1B a year); the
#: profile's market cap decides `size_ok` afterwards.
EARNINGS_MIN_QUARTER_REVENUE = 250_000_000.0
EARNINGS_NAMES_MAX = 100
#: Plan §1.5: a theme is eligible only with ≥ 2 verified case-library primers.
MIN_THEME_PRIMERS = 2
#: A ticker news feed where more than half the rows are unusable (no date, another symbol, not an
#: object) has changed shape — its count is MISSING, never a quiet week.
_MAX_UNUSABLE_SHARE = 0.5

_CIK_MAP_TTL_SECONDS = 24 * 3600.0
_CALENDAR_TTL_SECONDS = 30 * 60.0
_THEMES_TABLE = "trending_themes"
_PIECES_READ_LIMIT = 1000

CANDIDATES_PATH = Path(__file__).resolve().parents[3] / "data" / "learn_candidates.json"
CANDIDATES_VERSION = 1

#: A ticker the universe may carry (the ledger's own subject-ticker grammar).
_SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
#: An earnings-calendar name: a plain common-stock symbol (no class suffix, no foreign line).
_CALENDAR_SYMBOL_RE = re.compile(r"[A-Z]{1,5}")
#: SEC's ticker grammar in company_tickers.json ("BRK-B").
_SEC_TICKER_RE = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,11}")


class SignalsUnavailable(LearnWeeklyError):
    """A read the whole topic step depends on failed (the active themes, SEC's ticker map). The
    step must RETRY later — this is never "nothing to rank"."""


# ── Small helpers ─────────────────────────────────────────────────────────────────────────────


def _norm_symbol(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    sym = value.strip().upper()
    return sym if _SYMBOL_RE.fullmatch(sym) else None


def _finite(value: Any) -> Optional[float]:
    """A finite real number, or None (bool, NaN, ±inf, strings and huge ints are not)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        f = float(value)
    except (OverflowError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _row_day(value: Any) -> Optional[date]:
    """`YYYY-MM-DD…` → its date; anything else None (never guessed)."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    head = value[:10]
    if head[4:5] != "-" or head[7:8] != "-" or not (head[:4] + head[5:7] + head[8:10]).isdigit():
        return None
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None


def week_index(today: date, day: date) -> Optional[int]:
    """Which counting week `day` falls in, relative to the run's ET date `today`: 0 = today and the
    6 days before it, …, `WEEKS - 1` the oldest. A day ONE day ahead of `today` (an article stamped
    in a zone east of ET) counts in week 0; anything further ahead or older than the window is None."""
    age = (today - day).days
    if age < -1 or age >= WINDOW_DAYS:
        return None
    return max(age, 0) // 7


def _covered_weeks(today: date, oldest: Optional[date]) -> int:
    """How many weeks, newest first, a walk that stopped at `oldest` covered COMPLETELY. Rows on
    `oldest` itself may have been cut mid-day, so only weeks whose first day is AFTER it count."""
    if oldest is None:
        return 0
    n = 0
    for k in range(WEEKS):
        first_day = today - timedelta(days=7 * k + 6)
        if first_day > oldest:
            n += 1
        else:
            break
    return n


def _loop_lock_factory():
    """An `asyncio.Lock` rebuilt when the running loop changes (a module-level lock reused from
    another loop raises — pytest runs every test on a fresh loop; the same idiom as sec_edgar)."""
    state: Dict[str, Any] = {"lock": None, "loop": None}

    def get() -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if state["lock"] is None or state["loop"] is not loop:
            state["lock"] = asyncio.Lock()
            state["loop"] = loop
        return state["lock"]

    return get


_cik_lock = _loop_lock_factory()
_calendar_lock = _loop_lock_factory()
_cik_cache: Optional[Tuple[float, Dict[str, str]]] = None
_calendar_cache: Dict[Tuple[str, str], Tuple[float, "_EarningsWeek"]] = {}


def clear_caches() -> None:
    """Drop the process-local caches (tests; a preview run that wants fresh reads)."""
    global _cik_cache
    _cik_cache = None
    _calendar_cache.clear()


@dataclass
class _CallCounter:
    """FMP calls made by one run, logged at the end (plan: log the FMP call count per run)."""

    news: int = 0
    profile: int = 0
    calendar: int = 0

    @property
    def total(self) -> int:
        return self.news + self.profile + self.calendar


# ── The owner's candidate list ────────────────────────────────────────────────────────────────


def load_learn_candidates(path: Optional[Path] = None) -> Tuple[str, ...]:
    """The owner-maintained extra tickers (`backend/data/learn_candidates.json`). Never raises: an
    absent file gives () with a WARNING, bad JSON or a wrong version () with an ERROR, a malformed
    ticker is skipped with an ERROR naming it."""
    target = Path(path) if path is not None else CANDIDATES_PATH
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("[learn-signals-candidates] %s is absent — no owner candidates this week", target)
        return ()
    except (OSError, ValueError) as e:
        logger.error("[learn-signals-candidates] %s is unreadable (%s: %s) — no owner candidates",
                     target, type(e).__name__, e)
        return ()
    if not isinstance(raw, dict) or raw.get("version") != CANDIDATES_VERSION or not isinstance(
            raw.get("tickers"), list):
        logger.error("[learn-signals-candidates] %s: expected {\"version\": %d, \"tickers\": [...]} — "
                     "no owner candidates", target, CANDIDATES_VERSION)
        return ()
    out: List[str] = []
    for t in raw["tickers"]:
        sym = _norm_symbol(t)
        if sym is None:
            logger.error("[learn-signals-candidates] %s: %r is not a ticker — skipped", target, t)
            continue
        if sym not in out:
            out.append(sym)
    return tuple(out)


# ── SEC's ticker → CIK map ────────────────────────────────────────────────────────────────────


def parse_company_tickers(data: Any) -> Dict[str, str]:
    """`company_tickers.json` (`{"0": {"cik_str": 320193, "ticker": "AAPL", …}, …}`) → {TICKER:
    10-digit CIK}. A malformed row is skipped (counted, WARNING); a ticker listed under two CIKs
    keeps the FIRST (WARNING): guessing between two filers would cite the wrong company's filings."""
    if not isinstance(data, Mapping):
        return {}
    out: Dict[str, str] = {}
    malformed = 0
    conflicts: List[str] = []
    for row in data.values():
        if not isinstance(row, Mapping):
            malformed += 1
            continue
        ticker = row.get("ticker")
        cik = normalize_cik(row.get("cik_str"))
        sym = ticker.strip().upper() if isinstance(ticker, str) else ""
        if cik is None or not _SEC_TICKER_RE.fullmatch(sym):
            malformed += 1
            continue
        held = out.get(sym)
        if held is None:
            out[sym] = cik
        elif held != cik:
            conflicts.append(sym)
    if malformed:
        logger.warning("[learn-signals-cik-map] %d malformed rows in company_tickers.json skipped",
                       malformed)
    if conflicts:
        logger.warning("[learn-signals-cik-map] %d tickers listed under two CIKs kept their first "
                       "(e.g. %s)", len(conflicts), ", ".join(sorted(set(conflicts))[:10]))
    return out


async def ticker_cik_map(*, sec: Optional[SECEdgarClient] = None) -> Dict[str, str]:
    """SEC's ticker → CIK map, cached 24 h per process.

    Raises `SignalsUnavailable` when SEC cannot answer (config, throttle, outage, 404, an empty
    map): every SEC read after it is keyed by CIK, so without the map the topic step can find no
    filing at all and must retry rather than rank a week it cannot peg."""
    global _cik_cache
    cached = _cik_cache
    if cached is not None and time.monotonic() - cached[0] < _CIK_MAP_TTL_SECONDS:
        return cached[1]
    async with _cik_lock():
        cached = _cik_cache
        if cached is not None and time.monotonic() - cached[0] < _CIK_MAP_TTL_SECONDS:
            return cached[1]
        client = sec if sec is not None else get_sec_edgar_client()
        try:
            data = await client.get_company_tickers()
        except SECException as e:
            raise SignalsUnavailable(
                f"SEC company_tickers.json failed ({type(e).__name__}: {e}) — no CIK map",
                step="cik_map") from e
        if data is None:
            raise SignalsUnavailable("SEC company_tickers.json answered 404 — no CIK map",
                                     step="cik_map")
        mapping = parse_company_tickers(data)
        if not mapping:
            raise SignalsUnavailable("SEC company_tickers.json held no usable row — no CIK map",
                                     step="cik_map")
        logger.info("[learn-signals-cik-map] loaded %d tickers from SEC", len(mapping))
        _cik_cache = (time.monotonic(), mapping)
        return mapping


def cik_for(ticker: str, mapping: Mapping[str, str]) -> Optional[str]:
    """The CIK SEC lists `ticker` under (a class suffix written "BRK.B" is SEC's "BRK-B")."""
    sym = ticker.strip().upper()
    return mapping.get(sym) or mapping.get(sym.replace(".", "-"))


# ── Active themes (our own table) ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _ThemeRow:
    slug: str
    title: str
    members: Tuple[str, ...]


def _read_active_themes_sync() -> List[_ThemeRow]:
    """The active Emerging Frontiers themes, `sort_order` first. Synchronous (the Supabase SDK
    is) — always called through `asyncio.to_thread`. A failed read raises `SignalsUnavailable`:
    a topic step that read no theme must retry, not decide the week has none."""
    try:
        res = (get_supabase().table(_THEMES_TABLE).select("slug,title,tickers,is_active,sort_order")
               .eq("is_active", True).order("sort_order").execute())
    except Exception as e:
        raise SignalsUnavailable(f"reading {_THEMES_TABLE} failed ({type(e).__name__}: {e})",
                                 step="themes") from e
    rows = getattr(res, "data", None)
    if not isinstance(rows, list):
        raise SignalsUnavailable(f"{_THEMES_TABLE} answered {type(rows).__name__}, not rows",
                                 step="themes")
    out: List[_ThemeRow] = []
    seen: set = set()
    for r in rows:
        if not isinstance(r, Mapping) or r.get("is_active") is False:
            continue
        slug = r.get("slug").strip() if isinstance(r.get("slug"), str) else ""
        if not slug or slug in seen:
            if slug:
                logger.warning("[learn-signals-themes] theme slug %r appears twice — first kept", slug)
            continue
        seen.add(slug)
        title = " ".join(r.get("title").split())[:120] if isinstance(r.get("title"), str) else ""
        raw = r.get("tickers") if isinstance(r.get("tickers"), list) else []
        members: List[str] = []
        for t in raw:
            sym = _norm_symbol(t)
            if sym is not None and sym not in members:
                members.append(sym)
        out.append(_ThemeRow(slug=slug, title=title or slug, members=tuple(members)))
    if not out:
        logger.warning("[learn-signals-themes] %s holds no active theme", _THEMES_TABLE)
    return out


# ── The earnings calendar (FMP) ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _EarningsWeek:
    symbols: FrozenSet[str]           # every plain symbol with a calendar row this week
    ranked: Tuple[str, ...]           # the size-proxy-qualified ones, largest revenue first


def _earnings_days(today: date) -> List[date]:
    """The counting week's ET days, oldest first (the same 7 days as news week 0)."""
    return [today - timedelta(days=6 - i) for i in range(7)]


async def _earnings_week(today: date, fmp: Any, counter: _CallCounter) -> _EarningsWeek:
    """This week's calendar, one call per ET day, all-or-nothing (raises on any failed day)."""
    days = _earnings_days(today)
    key = (days[0].isoformat(), days[-1].isoformat())
    cached = _calendar_cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < _CALENDAR_TTL_SECONDS:
        return cached[1]
    async with _calendar_lock():
        cached = _calendar_cache.get(key)
        if cached is not None and time.monotonic() - cached[0] < _CALENDAR_TTL_SECONDS:
            return cached[1]

        async def getter(*, from_date: str, to_date: str) -> Any:
            counter.calendar += 1
            return await fmp.get_earnings_calendar(from_date=from_date, to_date=to_date)

        by_day = await fetch_calendar_days(getter, days)
        symbols: set = set()
        revenue: Dict[str, float] = {}
        for rows in by_day.values():
            for row in rows:
                raw = row.get("symbol") if isinstance(row, Mapping) else None
                sym = raw.strip().upper() if isinstance(raw, str) else ""
                if not _CALENDAR_SYMBOL_RE.fullmatch(sym):
                    continue
                symbols.add(sym)
                rev = _finite(row.get("revenueEstimated"))
                if rev is None:
                    rev = _finite(row.get("revenueActual"))
                if rev is not None and rev >= EARNINGS_MIN_QUARTER_REVENUE:
                    revenue[sym] = max(revenue.get(sym, 0.0), rev)
        ranked = tuple(sorted(revenue, key=lambda s: (-revenue[s], s))[:EARNINGS_NAMES_MAX])
        week = _EarningsWeek(symbols=frozenset(symbols), ranked=ranked)
        _calendar_cache[key] = (time.monotonic(), week)
        logger.info("[learn-signals-earnings] %s..%s: %d names on the calendar, %d pass the size "
                    "proxy (kept %d)", key[0], key[1], len(symbols), len(revenue), len(ranked))
        return week


# ── universe ──────────────────────────────────────────────────────────────────────────────────


async def universe(now: datetime, *, fmp: Any = None,
                   candidates_path: Optional[Path] = None) -> List[str]:
    """The tickers to score this week, theme members first, then the owner's list, then this
    week's earnings names (largest revenue first); capped at `UNIVERSE_CAP` (WARNING).

    Raises `SignalsUnavailable` when the theme table cannot be read. A failed earnings calendar
    only drops the calendar names (WARNING): the baskets and the owner's list still rank."""
    today = schedule.et_date(now)
    themes = await asyncio.to_thread(_read_active_themes_sync)
    owner = load_learn_candidates(candidates_path)
    client = fmp if fmp is not None else get_fmp_client()
    counter = _CallCounter()
    try:
        earnings = (await _earnings_week(today, client, counter)).ranked
    except Exception as e:
        logger.warning("[learn-signals-universe] earnings calendar for the week to %s failed "
                       "(%s) — the universe has no calendar names this week", today,
                       type(e).__name__)
        earnings = ()
    ordered: List[str] = []
    seen: set = set()
    for sym in [m for t in themes for m in t.members] + list(owner) + list(earnings):
        if sym not in seen:
            seen.add(sym)
            ordered.append(sym)
    if len(ordered) > UNIVERSE_CAP:
        logger.warning("[learn-signals-universe] %d tickers (themes %d, owner %d, calendar %d) — "
                       "CUT to the first %d; the tail (calendar names first) is not scored this week",
                       len(ordered), sum(len(t.members) for t in themes), len(owner), len(earnings),
                       UNIVERSE_CAP)
        ordered = ordered[:UNIVERSE_CAP]
    logger.info("[learn-signals-universe] %s: %d tickers (%d themes, %d owner, %d calendar; %d FMP "
                "calls)", today, len(ordered), len(themes), len(owner), len(earnings), counter.total)
    return ordered


# ── collect_signals ───────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _News:
    week: Optional[int]
    baseline: Tuple[int, ...]
    saturated: bool
    failure: Optional[str] = None     # why the count is missing (an exception class name, …)


@dataclass(frozen=True)
class _Size:
    ok: Optional[bool]
    excluded: Optional[str] = None
    failure: Optional[str] = None


@dataclass(frozen=True)
class _Sec:
    week: Optional[int]
    baseline: Tuple[int, ...]
    items: Tuple[str, ...]
    foreign: Optional[bool]
    failure: Optional[str] = None


def _missing_news(why: str) -> _News:
    return _News(week=None, baseline=(), saturated=False, failure=why)


async def _news_counts(fmp: Any, ticker: str, today: date, counter: _CallCounter) -> _News:
    """Weekly article counts for `ticker` over the window — the newest page first, until a page is
    short, reaches past the window's start, repeats, or `NEWS_MAX_PAGES` is spent (saturated).

    MISSING (never zero) on: any raised exception, an `EmptyAfterFailure` (`fetch_failed`), a
    non-list answer, or a feed whose rows are mostly unusable."""
    start = today - timedelta(days=WINDOW_DAYS - 1)
    to_day = today + timedelta(days=1)  # an article stamped ahead of ET still lands in week 0
    counts = [0] * WEEKS
    seen: set = set()
    oldest: Optional[date] = None
    rows_total = 0
    unusable = 0
    saturated = True
    for page in range(NEWS_MAX_PAGES):
        counter.news += 1
        try:
            rows = await fmp.get_stock_news(ticker, limit=NEWS_PAGE_SIZE, from_date=start.isoformat(),
                                            to_date=to_day.isoformat(), page=page)
        except Exception as e:  # rate limit and auth included: missing, never zero
            return _missing_news(type(e).__name__)
        if getattr(rows, "fetch_failed", False):
            return _missing_news("fetch_failed")
        if not isinstance(rows, list):
            return _missing_news(f"non_list:{type(rows).__name__}")
        if not rows:
            saturated = False
            break
        new = 0
        page_oldest: Optional[date] = None
        for row in rows:
            rows_total += 1
            if not isinstance(row, Mapping):
                unusable += 1
                continue
            sym = row.get("symbol")
            if isinstance(sym, str) and sym.strip() and sym.strip().upper() != ticker:
                unusable += 1
                continue
            day = _row_day(row.get("publishedDate"))
            if day is None:
                unusable += 1
                continue
            url = row.get("url")
            key = url if isinstance(url, str) and url else (str(row.get("title")), row.get("publishedDate"))
            if key in seen:
                continue
            seen.add(key)
            new += 1
            page_oldest = day if page_oldest is None or day < page_oldest else page_oldest
            idx = week_index(today, day)
            if idx is not None:
                counts[idx] += 1
        if page_oldest is not None and (oldest is None or page_oldest < oldest):
            oldest = page_oldest
        if new == 0:
            # The page repeated (or held nothing usable): paging cannot get further back, so the
            # count is a lower bound over the weeks it did cover.
            break
        if page_oldest is not None and page_oldest < start:
            saturated = False
            break
        if len(rows) < NEWS_PAGE_SIZE:
            saturated = False
            break
    if rows_total and unusable > rows_total * _MAX_UNUSABLE_SHARE:
        return _missing_news(f"unusable_rows:{unusable}/{rows_total}")
    if not saturated:
        return _News(week=counts[0], baseline=tuple(counts[k] for k in range(WEEKS - 1, 0, -1)),
                     saturated=False)
    covered = _covered_weeks(today, oldest)
    baseline = tuple(counts[k] for k in range(min(covered, WEEKS) - 1, 0, -1))
    return _News(week=counts[0], baseline=baseline, saturated=True)


def _flag(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


async def _profile_size(fmp: Any, ticker: str, counter: _CallCounter) -> _Size:
    """`size_ok` from FMP's profile, returned as a BOOL only — the market cap and the exchange
    name never leave this function. A failed read is unknown (`None`), never "too small"."""
    counter.profile += 1
    try:
        prof = await fmp.get_company_profile(ticker)
    except Exception as e:
        return _Size(ok=None, failure=type(e).__name__)
    if not isinstance(prof, Mapping):
        return _Size(ok=None, failure=f"non_dict:{type(prof).__name__}")
    if not prof:
        return _Size(ok=False)  # FMP answered: no profile for this symbol
    sym = prof.get("symbol")
    if isinstance(sym, str) and sym.strip() and sym.strip().upper() != ticker:
        return _Size(ok=None, failure="symbol_mismatch")
    if _flag(prof.get("isEtf")) or _flag(prof.get("isFund")):
        return _Size(ok=False, excluded="etf_or_fund")
    if prof.get("isActivelyTrading") is False:
        return _Size(ok=False, excluded="inactive")
    exchange = prof.get("exchange")
    if not isinstance(exchange, str) or not exchange.strip():
        exchange = prof.get("exchangeShortName")
    exchange_ok = isinstance(exchange, str) and exchange.strip().upper() in MAJOR_US_EXCHANGES
    cap = _finite(prof.get("marketCap"))
    if cap is None:
        cap = _finite(prof.get("mktCap"))
    return _Size(ok=bool(exchange_ok and cap is not None and cap >= MIN_MARKET_CAP_USD))


async def _sec_counts(cik: Optional[str], today: date, sec: Optional[SECEdgarClient]) -> _Sec:
    """8-K counts per counting week, this week's item codes, and the foreign-filer flag, from SEC
    submissions. Missing (None) when there is no CIK or the read fails — never "no 8-Ks"."""
    if cik is None:
        return _Sec(week=None, baseline=(), items=(), foreign=None, failure="no_cik")
    try:
        summary = await sec_facts.company_summary(cik, client=sec)
    except Exception as e:
        return _Sec(week=None, baseline=(), items=(), foreign=None, failure=type(e).__name__)
    if summary is None:
        return _Sec(week=None, baseline=(), items=(), foreign=None, failure="not_found")
    counts = [0] * WEEKS
    items: set = set()
    oldest: Optional[date] = None
    for f in summary.recent_8k:
        day = _row_day(f.filing_date)
        if day is None:
            continue
        oldest = day if oldest is None or day < oldest else oldest
        idx = week_index(today, day)
        if idx is None:
            continue
        counts[idx] += 1
        if idx == 0:
            items.update(f.items)
    baseline_weeks = WEEKS
    if len(summary.recent_8k) >= sec_facts.RECENT_8K_MAX:
        # The summary keeps only the newest RECENT_8K_MAX 8-Ks: weeks older than its oldest are
        # not "no 8-Ks", they are unread.
        baseline_weeks = _covered_weeks(today, oldest)
        logger.warning("[learn-signals-8k-cut] CIK%s: %d 8-Ks in the summary — the 8-K baseline "
                       "covers %d weeks", cik, len(summary.recent_8k), baseline_weeks)
    baseline = tuple(counts[k] for k in range(min(baseline_weeks, WEEKS) - 1, 0, -1))
    return _Sec(week=counts[0], baseline=baseline, items=tuple(sorted(items)),
                foreign=bool(summary.foreign_filer))


async def collect_signals(now: datetime, tickers: Iterable[str], *, fmp: Any = None,
                          sec: Optional[SECEdgarClient] = None) -> List[TickerSignal]:
    """One `TickerSignal` per distinct ticker, in input order (module docstring).

    Raises `SignalsUnavailable` only when SEC's ticker map cannot be read (checked FIRST, before
    any FMP call is spent). Every per-ticker failure is a MISSING field on that ticker."""
    today = schedule.et_date(now)
    started = time.monotonic()
    ordered: List[str] = []
    bad: List[str] = []
    for t in tickers:
        sym = _norm_symbol(t)
        if sym is None:
            bad.append(str(t)[:20])
        elif sym not in ordered:
            ordered.append(sym)
    if bad:
        logger.warning("[learn-signals] %d unusable tickers dropped: %s", len(bad), bad[:10])
    cik_map = await ticker_cik_map(sec=sec)
    client = fmp if fmp is not None else get_fmp_client()
    counter = _CallCounter()
    earnings_known = True
    try:
        earnings = (await _earnings_week(today, client, counter)).symbols
    except Exception as e:
        earnings_known = False
        earnings = frozenset()
        logger.warning("[learn-signals] earnings calendar for the week to %s failed (%s) — no "
                       "ticker gets the earnings bonus this week", today, type(e).__name__)
    sem = asyncio.Semaphore(SIGNAL_CONCURRENCY)

    async def one(ticker: str) -> Tuple[TickerSignal, _News, _Size, _Sec]:
        cik = cik_for(ticker, cik_map)
        async with sem:
            news, size, sec_c = await asyncio.gather(
                _news_counts(client, ticker, today, counter),
                _profile_size(client, ticker, counter),
                _sec_counts(cik, today, sec),
                return_exceptions=True,
            )
        # Each reader handles its own failures; a raise here is a bug in this module — still a
        # MISSING field, logged with its stack, never a dropped ticker.
        if isinstance(news, BaseException):
            logger.error("[learn-signals] news reader raised for %s", ticker, exc_info=news)
            news = _missing_news(f"bug:{type(news).__name__}")
        if isinstance(size, BaseException):
            logger.error("[learn-signals] profile reader raised for %s", ticker, exc_info=size)
            size = _Size(ok=None, failure=f"bug:{type(size).__name__}")
        if isinstance(sec_c, BaseException):
            logger.error("[learn-signals] SEC reader raised for %s", ticker, exc_info=sec_c)
            sec_c = _Sec(week=None, baseline=(), items=(), foreign=None,
                         failure=f"bug:{type(sec_c).__name__}")
        sig = TickerSignal(
            ticker=ticker,
            cik=cik,
            news_week=news.week,
            news_baseline=news.baseline,
            news_saturated=news.saturated,
            filings_8k_week=sec_c.week,
            filings_8k_baseline=sec_c.baseline,
            items_8k=sec_c.items,
            earnings_this_week=ticker in earnings,
            size_ok=size.ok,
            foreign_filer=sec_c.foreign,
            excluded_reason=size.excluded,
        )
        return sig, news, size, sec_c

    results = await asyncio.gather(*(one(t) for t in ordered), return_exceptions=True)
    out: List[TickerSignal] = []
    news_fail: Dict[str, int] = {}
    size_fail: Dict[str, int] = {}
    sec_fail: Dict[str, int] = {}
    saturated = size_ok = no_cik = 0
    for ticker, res in zip(ordered, results):
        if isinstance(res, BaseException):
            logger.error("[learn-signals] scoring inputs for %s failed outright", ticker, exc_info=res)
            out.append(TickerSignal(ticker=ticker))
            news_fail["bug"] = news_fail.get("bug", 0) + 1
            continue
        sig, news, size, sec_c = res
        out.append(sig)
        if news.failure:
            key = news.failure.split(":")[0]
            news_fail[key] = news_fail.get(key, 0) + 1
        if size.failure:
            key = size.failure.split(":")[0]
            size_fail[key] = size_fail.get(key, 0) + 1
        if sec_c.failure and sec_c.failure != "no_cik":
            key = sec_c.failure.split(":")[0]
            sec_fail[key] = sec_fail.get(key, 0) + 1
        saturated += bool(sig.news_saturated)
        size_ok += sig.size_ok is True
        no_cik += sig.cik is None
    log = logger.warning if (news_fail or size_fail or sec_fail) else logger.info
    log("[learn-signals] %s: %d tickers in %.1fs — news missing %d %s, saturated %d; profile failed "
        "%d %s, size_ok %d; no CIK %d; SEC failed %d %s; earnings calendar %s; FMP calls %d "
        "(news %d, profile %d, calendar %d)",
        today, len(out), time.monotonic() - started, sum(news_fail.values()), news_fail, saturated,
        sum(size_fail.values()), size_fail, size_ok, no_cik, sum(sec_fail.values()), sec_fail,
        "ok" if earnings_known else "FAILED", counter.total, counter.news, counter.profile,
        counter.calendar)
    return out


# ── theme_inputs ──────────────────────────────────────────────────────────────────────────────


def theme_of_piece(row: Mapping[str, Any]) -> Optional[str]:
    """The theme slug a piece covered: its fact sheet's `theme_slug`, else its topic's CHOSEN
    candidate when that is a theme (`topic.candidates[chosen_index].key`). None for a single-company
    piece or an unreadable row."""
    sheet = row.get("fact_sheet") if isinstance(row.get("fact_sheet"), Mapping) else {}
    slug = sheet.get("theme_slug")
    if isinstance(slug, str) and slug.strip():
        return slug.strip()
    topic = row.get("topic") if isinstance(row.get("topic"), Mapping) else {}
    idx = topic.get("chosen_index")
    cands = topic.get("candidates")
    if isinstance(idx, bool) or not isinstance(idx, int) or not isinstance(cands, list):
        return None
    if not 0 <= idx < len(cands) or not isinstance(cands[idx], Mapping):
        return None
    cand = cands[idx]
    key = cand.get("key")
    if cand.get("kind") == "theme" and isinstance(key, str) and key.strip():
        return key.strip()
    return None


async def _last_covered_by_theme() -> Dict[str, str]:
    """{theme slug: ET date (ISO) of the newest approved / published Deep Dive on it}."""
    rows = await ledger.list_pieces(statuses=ledger.RECENT_STATUSES, kind=C.KIND_DEEP_DIVE,
                                    limit=_PIECES_READ_LIMIT)
    if len(rows) >= _PIECES_READ_LIMIT:
        logger.warning("[learn-signals-themes] %d Deep Dives read — the last-covered dates were CUT "
                       "at the read limit", len(rows))
    out: Dict[str, str] = {}
    for r in rows:
        slug = theme_of_piece(r)
        at = r.get("scheduled_publish_at")
        if slug is None or not isinstance(at, str):
            continue
        try:
            stamp = datetime.fromisoformat(at.replace("Z", "+00:00"))
            day = schedule.et_date(stamp).isoformat()
        except (ValueError, TypeError):
            logger.warning("[learn-signals-themes] piece %s has an unreadable scheduled_publish_at "
                           "%r — skipped", r.get("period_key"), at)
            continue
        if day > out.get(slug, ""):
            out[slug] = day
    return out


async def theme_inputs(*, library: Optional[case_library.CaseLibrary] = None) -> List[ThemeInput]:
    """The active themes that have members, each ELIGIBLE only with ≥ `MIN_THEME_PRIMERS` verified
    primers, with the ET date it was last covered. Raises `SignalsUnavailable` (themes) or
    `LearnLedgerError` (Deep Dive history) — a step that cannot read either must retry."""
    themes = await asyncio.to_thread(_read_active_themes_sync)
    lib = library if library is not None else await asyncio.to_thread(case_library.get_case_library)
    last = await _last_covered_by_theme()
    out: List[ThemeInput] = []
    for t in themes:
        if not t.members:
            logger.warning("[learn-signals-themes] theme %s has no members — not offered", t.slug)
            continue
        primers = lib.primer_count(t.slug)
        out.append(ThemeInput(slug=t.slug, title=t.title, members=t.members,
                              eligible=primers >= MIN_THEME_PRIMERS, last_covered=last.get(t.slug)))
    logger.info("[learn-signals-themes] %d themes, %d eligible (≥ %d verified primers): %s",
                len(out), sum(t.eligible for t in out), MIN_THEME_PRIMERS,
                ", ".join(f"{t.slug}{'' if t.eligible else '(ineligible)'}" for t in out))
    return out
