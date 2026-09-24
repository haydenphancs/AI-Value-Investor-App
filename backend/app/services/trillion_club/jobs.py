"""The Trillion-Dollar Club jobs: what the loops in ``scheduler.py`` run.

``run_daily(now)`` — two stages, both must succeed for the run to count:

1. **Membership** for every registry row. An FMP-sized company (``cap_source`` ``fmp_us`` /
   ``fmp_adr``) is replayed from up to 260 DATED closes of
   ``historical-market-capitalization`` through ``rules.evaluate_membership``; a hand-sized
   one (Aramco, Samsung) makes no FMP call and keeps its owner-forced mode, and a
   ``manual_cap_as_of`` older than 45 days logs a WARNING. ``None`` from the rule means FAIL
   CLOSED: the stored state is kept and ``membership_checked_at`` is NOT advanced — the
   request path hides the section after 7 stale days, so a stamp on a row nobody refreshed
   would be a lie. The same holds for an FMP-sized company the OWNER forced in or out: the
   rule never fails closed for it, so :func:`evaluate_fmp_sized` stamps it only when a
   usable close was actually read. A fetch or write error fails the stage, and so does FMP
   answering for two or more companies without a single usable close among them (an
   upstream soft outage a same-day retry can fix — one company's structural fail-closed,
   such as a delisted symbol, is not a failed run).
2. **13F filings** for ``use_13f`` companies only (the owner's opt-in; joining the club never
   turns ingestion on). Per CIK: ``dates`` fetched STRICTLY; the newest listed quarter every
   day, plus any of the newest ``MAX_QUARTERS`` that is missing or ``degraded``. A quarter
   whose raw extract hashes to the stored ``raw_hash`` with a ``complete`` build is
   skipped. Quarter N-1 is always fetched LIVE (never read back from the table) — memoised
   within one run only, so a quarter fetched for its own hash check is not fetched twice.
   ``FilingRefused`` (a >200-row client-asset book) and ``FilingUnavailable`` write
   NOTHING. When a stored quarter's raw data CHANGED (a 13F-HR/A), the NEXT stored quarter
   is rebuilt too — its "vs previous quarter" diff was computed against the old rows — and
   the changed quarter is written only AFTER that rebuild succeeds, so a failed cascade is
   retried by the next run instead of being forgotten. An OLDER quarter's problem fails the
   run only when a retry today could fix it: FMP not having the quarter waits for the next
   run, but a failed Supabase write or an unexpected build error never does.

``run_weekly(now)`` — three stages:

1. **Re-hash** each filer's newest ``MAX_QUARTERS`` quarters that are within 12 months of
   their quarter end (FMP folds a 13F-HR/A into the ORIGINAL quarter; Berkshire's Chubb
   amendment landed 228 days late), with the same skip / cascade rules.
2. **New-filer probe**: ``institutional-ownership/dates`` for every CIK of every US member.
   A company the registry calls a non-filer that now lists a recent 13F logs a WARNING —
   ``use_13f`` is NEVER switched on by code.
3. **Discovery**: the company screener at ≥ $900B (NASDAQ/NYSE, no ETFs or funds), each
   unknown symbol confirmed by ``market-capitalization-batch`` (a second source; both are
   discovery-only figures, never displayed). A confirmed unknown is inserted UNPUBLISHED
   with a WARNING. Nothing reaches Home without the owner. "Unknown" is judged against the
   RAW registry rows — a row the parser rejected is still that company — and the share
   classes the screener lists as separate rows of one company become ONE row with the
   other classes as ``symbol_aliases``.

Both runs log ``trillion club STALE`` at ERROR when a published company's membership has not
been refreshed for more than 3 days (the early warning before the request path's 7-day hide).

Isolation: nothing here creates a ``whales`` row or touches follows, pushes, alerts or the
notification tables — a company's 13F is not an investor's "trade". A test pins it (imports
and every ``.table()`` name in this package).

Every function returns / fills a summary dict; ``summary["ok"]`` is the scheduler's
``run.success`` and ``summary["items"]`` the rows written.
"""

from __future__ import annotations

import copy
import logging
import math
import re
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Deque, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.services.trillion_club import rules
from app.services.trillion_club.builder import (
    BUILD_COMPLETE,
    FilingRefused,
    FilingUnavailable,
    build_filing,
    raw_hash_of,
)
from app.services.trillion_club.rules import MembershipState
from app.services.trillion_club.store import TrillionClubStore, TrillionClubStoreError
from app.utils.market_hours import ET

logger = logging.getLogger(__name__)

# ── Knobs ────────────────────────────────────────────────────────────────────────────

FMP_CAP_SOURCES = frozenset({"fmp_us", "fmp_adr"})
CAP_SOURCE_MANUAL = "manual"
#: ~One trading year of dated closes (``rules.MAX_REPLAY_ROWS``).
HISTORY_LIMIT = rules.MAX_REPLAY_ROWS
#: Calendar days of history asked for. ⚠️ ``limit`` ALONE IS NOT ENOUGH: without
#: ``from``/``to`` FMP silently answers with about three months (probed 2026-09-24: AAPL,
#: ``limit=260`` -> 64 rows, 2026-06-24..09-23; with a 400-day window -> 260 rows). 260
#: sessions span ~378 calendar days, so 400 leaves room for holidays.
HISTORY_LOOKBACK_DAYS = 400
#: Newest listed 13F quarters per CIK the jobs keep built (the Pro history).
MAX_QUARTERS = 4
#: The weekly job re-hashes a quarter for this long after its quarter end.
REHASH_WINDOW_DAYS = 365
#: ERROR "trillion club STALE" when a published company's membership is older than this.
STALE_ERROR_AFTER = timedelta(days=3)
#: WARNING when a hand-entered cap (Aramco, Samsung) is older than this.
MANUAL_CAP_WARN_DAYS = 45
#: Discovery screen floor (the club line is $1T; the screen looks a little below it).
DISCOVERY_MIN_CAP_USD = 900_000_000_000
DISCOVERY_EXCHANGES = ("NASDAQ", "NYSE")
#: The ≥ $900B screen is ~20 rows; hitting this ceiling means truncation — logged.
DISCOVERY_SCREEN_LIMIT = 200
#: A non-filer's 13F only counts as "started filing" when its newest quarter is recent.
NEW_FILER_RECENT_DAYS = 400
#: Card kinds that say "this company does not file 13Fs".
NON_FILER_CARD_KINDS = frozenset({"no_thirteen_f", "non_us"})
#: Bound on the next-quarter cascade (a stored quarter's rebuild can only ever cascade
#: through the few quarters the jobs keep).
MAX_CASCADE_DEPTH = MAX_QUARTERS + 2
#: FMP answered (no exception) for at least this many FMP-sized companies and NOT ONE gave
#: a usable close -> the membership stage FAILS (same-day retry + ``last_error``). One
#: company failing closed is structural (delisted, renamed, < 20 rows) and a retry cannot
#: fix it; every company at once is FMP's history endpoint answering 200 with nothing
#: usable, which a retry can. Two, so a single structural case never turns the ledger red.
MEMBERSHIP_OUTAGE_MIN_ANSWERS = 2
#: Problems on an OLDER quarter (neither the card's newest nor a forced cascade rebuild)
#: that wait for the next run instead of failing this one: FMP not having the quarter (a
#: permanently empty old extract would otherwise fail every run) and a cascade blocked by
#: a REFUSED next quarter (permanent, logged, never a failure itself — any other cascade
#: failure already failed the run, since the cascaded quarter is forced). Every other
#: kind — a failed Supabase write, an unexpected build error — fails the run so the
#: scheduler retries it today (store.py's contract).
DEFERRABLE_OLDER_QUARTER_PROBLEMS = frozenset({"unavailable", "cascade_blocked"})

DAILY = "daily"
WEEKLY = "weekly"

_SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")
_CIK_RE = re.compile(r"^[0-9]{10}$")
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


# ── The registry row ─────────────────────────────────────────────────────────────────


def norm_symbol(value: Any) -> Optional[str]:
    """Upper-case, trimmed, share-class dot as a dash (``BRK.B`` == ``BRK-B``)."""
    if not isinstance(value, str):
        return None
    s = value.strip().upper().replace(".", "-")
    return s or None


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str) and value.strip():
        try:
            ts = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n >= 0 else 0


def _positive_finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


@dataclass(frozen=True)
class Company:
    """One ``trillion_club_companies`` row, validated for what the jobs need."""

    slug: str
    display_name: str
    ciks: Tuple[str, ...]
    card_kind: str
    use_13f: bool
    cap_source: str
    cap_symbol: Optional[str]
    symbols: FrozenSet[str]
    home_country: str
    manual_cap_as_of: Optional[date]
    membership_mode: str
    prior: MembershipState
    checked_at: Optional[datetime]
    published: bool

    @property
    def effective_member(self) -> bool:
        """The owner's override wins, as on the read path."""
        if self.membership_mode == rules.MODE_FORCE_IN:
            return True
        if self.membership_mode == rules.MODE_FORCE_OUT:
            return False
        return self.prior.is_member


def parse_company(row: Mapping[str, Any]) -> Company:
    """A validated :class:`Company`; ``ValueError`` naming the problem otherwise.

    Migration 175's CHECKs make a bad row nearly impossible; this is for a Studio edit that
    slipped past them and for a future column change — a malformed row is skipped loudly,
    never guessed at.
    """
    if not isinstance(row, Mapping):
        raise ValueError(f"company row is {type(row).__name__}, not an object")
    slug = row.get("slug")
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise ValueError(f"bad slug {slug!r}")
    cap_source = row.get("cap_source")
    if cap_source not in FMP_CAP_SOURCES and cap_source != CAP_SOURCE_MANUAL:
        raise ValueError(f"{slug}: unknown cap_source {cap_source!r}")
    cap_symbol = norm_symbol(row.get("cap_symbol"))
    if cap_source in FMP_CAP_SOURCES and cap_symbol is None:
        raise ValueError(f"{slug}: cap_source {cap_source} with no cap_symbol")
    mode = row.get("membership_mode") or rules.MODE_AUTO
    if mode not in rules.MEMBERSHIP_MODES:
        raise ValueError(f"{slug}: unknown membership_mode {mode!r}")
    raw_ciks = row.get("ciks") if isinstance(row.get("ciks"), list) else []
    ciks: List[str] = []
    for c in raw_ciks:
        s = str(c).strip() if isinstance(c, (str, int)) and not isinstance(c, bool) else ""
        s = s.zfill(10) if s.isdigit() else s
        if not _CIK_RE.match(s):
            raise ValueError(f"{slug}: bad CIK {c!r}")
        if s not in ciks:
            ciks.append(s)
    aliases = row.get("symbol_aliases") if isinstance(row.get("symbol_aliases"), list) else []
    candidates = [cap_symbol, norm_symbol(row.get("detail_symbol")), norm_symbol(row.get("logo_symbol"))]
    candidates += [norm_symbol(a) for a in aliases]
    symbols = {s for s in candidates if s}
    country = row.get("home_country") if isinstance(row.get("home_country"), str) else "US"
    last_cap = _positive_finite(row.get("last_market_cap"))
    prior = MembershipState(
        is_member=row.get("is_member") is True,
        member_since=_as_date(row.get("member_since")),
        closes_at_or_above=_count(row.get("closes_at_or_above")),
        closes_below=_count(row.get("closes_below")),
        last_cap=last_cap,
        last_cap_date=_as_date(row.get("last_cap_date")) if last_cap is not None else None,
    )
    name = row.get("display_name")
    return Company(
        slug=slug,
        display_name=name.strip() if isinstance(name, str) and name.strip() else slug,
        ciks=tuple(ciks),
        card_kind=str(row.get("card_kind") or ""),
        use_13f=row.get("use_13f") is True,
        cap_source=cap_source,
        cap_symbol=cap_symbol,
        symbols=frozenset(symbols),
        home_country=country.strip().upper() if _COUNTRY_RE.match(country.strip().upper()) else "US",
        manual_cap_as_of=_as_date(row.get("manual_cap_as_of")),
        membership_mode=mode,
        prior=prior,
        checked_at=_as_timestamp(row.get("membership_checked_at")),
        published=row.get("published") is True,
    )


def _parse_companies(rows: Iterable[Any], report: Dict[str, Any]) -> List[Company]:
    out: List[Company] = []
    seen: Set[str] = set()
    for row in rows:
        try:
            company = parse_company(row)
        except ValueError as e:
            logger.error("trillion club: registry row skipped — %s (fix it in Studio)", e)
            report["malformed_companies"].append(str(e))
            continue
        if company.slug in seen:
            logger.error("trillion club: duplicate registry slug %r skipped", company.slug)
            report["malformed_companies"].append(f"duplicate slug {company.slug}")
            continue
        seen.add(company.slug)
        out.append(company)
    return out


# ── Per-run FMP view ─────────────────────────────────────────────────────────────────


class _RunFMP:
    """The FMP client for ONE run, memoising strict 13F extracts by ``(cik, year, quarter)``.

    Quarter N-1 is still fetched LIVE when N is built — within this run, from FMP, never
    from ``trillion_club_filings`` — but a quarter fetched for its own hash check is not
    fetched a second time as the next quarter's N-1. Only successful LIST answers are kept
    (an exception is never memoised, so a retry within the run asks FMP again). Everything
    else passes straight through.
    """

    def __init__(self, fmp: Any):
        self._fmp = fmp
        self._extracts: Dict[Tuple[str, int, int], List[Any]] = {}

    async def get_institutional_holdings(self, cik: str, year: int, quarter: int, *, strict: bool = False):
        if not strict:
            return await self._fmp.get_institutional_holdings(cik, year, quarter)
        key = (cik, int(year), int(quarter))
        if key in self._extracts:
            return copy.deepcopy(self._extracts[key])
        raw = await self._fmp.get_institutional_holdings(cik, year, quarter, strict=True)
        if isinstance(raw, list):
            self._extracts[key] = copy.deepcopy(raw)
        return raw

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fmp, name)


# ── Summary plumbing ─────────────────────────────────────────────────────────────────


def _new_summary(kind: str, now: datetime) -> Dict[str, Any]:
    return {"job": kind, "now": now.isoformat(), "ok": True, "items": 0, "failures": [],
            "malformed_companies": [], "stale": []}


def _fail(summary: Dict[str, Any], stage: Dict[str, Any], message: str) -> None:
    stage["ok"] = False
    summary["ok"] = False
    summary["failures"].append(message)


def _check_now(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError(f"trillion club jobs: now must be a timezone-aware datetime, got {now!r}")
    return now


def _store_for(db: Any) -> Any:
    if db is None:
        return TrillionClubStore()
    if hasattr(db, "read_companies") and hasattr(db, "upsert_filing"):
        return db                      # already a store (or a test stand-in for one)
    return TrillionClubStore(db)       # a Supabase client


def _fmp_for(fmp: Any) -> Any:
    if fmp is not None:
        return fmp
    from app.integrations.fmp import get_fmp_client

    return get_fmp_client()


def _invalidate_read_cache() -> None:
    try:
        from app.services.trillion_club_service import invalidate

        invalidate()
    except Exception as e:
        logger.warning(
            "trillion club: read-cache invalidation after a write failed (%s: %s) — the Home "
            "section catches up within its 10-minute TTL", type(e).__name__, e,
        )


async def _read_registry(
    store: Any, summary: Dict[str, Any],
) -> Optional[Tuple[List[Company], List[Any]]]:
    """``(parsed companies, raw rows)``, or None when the read failed (the run then stops).

    The raw rows are kept for discovery: a row the parser rejected is still a company the
    registry holds, and must not be "discovered" a second time."""
    try:
        rows = await store.read_companies()
    except Exception as e:
        logger.error("trillion club %s: registry read failed (%s: %s) — nothing done this run",
                     summary["job"], type(e).__name__, e, exc_info=True)
        summary["ok"] = False
        summary["failures"].append(f"registry read: {type(e).__name__}: {e}")
        return None
    if not isinstance(rows, (list, tuple)):
        logger.error("trillion club %s: registry read answered %s, not a list — nothing done "
                     "this run", summary["job"], type(rows).__name__)
        summary["ok"] = False
        summary["failures"].append(f"registry read: got {type(rows).__name__}, not a list")
        return None
    raw = list(rows)
    return _parse_companies(raw, summary), raw


# ── Membership ───────────────────────────────────────────────────────────────────────


def _closes_from(rows: Any, symbol: str, ctx: str) -> List[Tuple[Any, Any]]:
    """``(date, marketCap)`` pairs from FMP's history rows; rows for another symbol dropped."""
    closes: List[Tuple[Any, Any]] = []
    foreign = 0
    for r in rows if isinstance(rows, list) else ():
        if not isinstance(r, dict):
            continue
        got = norm_symbol(r.get("symbol"))
        if got is not None and got != symbol:
            foreign += 1
            continue
        closes.append((r.get("date"), r.get("marketCap")))
    if foreign:
        logger.warning("trillion club membership (%s): dropped %d row(s) FMP returned for "
                       "another symbol", ctx, foreign)
    return closes


def evaluate_fmp_sized(
    closes: Sequence[Tuple[Any, Any]],
    *,
    mode: str,
    prior: Optional[MembershipState],
    today_et: date,
    now_et: datetime,
    log_ctx: str = "",
) -> Optional[MembershipState]:
    """``rules.evaluate_membership`` for an FMP-sized company, plus the jobs' STAMP rule:
    ``None`` (keep the stored row, do not advance ``membership_checked_at``) unless the
    answer was decided from at least one usable close.

    In ``auto`` mode the rule already fails closed without enough usable closes. In
    ``force_in`` / ``force_out`` mode it never does: with no usable close it rebuilds the
    counters and the last cap from ``prior`` — fine facts to SHOW, but writing them stamps a
    row nobody refreshed, and one forced company stamped every day through an FMP soft
    outage (200 with ``[]``, a stale series, garbage) would vouch for the whole registry on
    the request path's staleness check. The read path applies the owner's override itself,
    so keeping the stored row loses nothing that is shown.

    To tell the two apart WITHOUT re-implementing the rule's cleaning (the close cutoff,
    staleness, conflicting duplicates), the rule is asked with ``prior``'s last-cap facts
    blanked. Per its contract ("the counters and last cap then come from the data when it
    is usable, else from prior") they come back blank exactly when no usable close was
    read. Nothing else the rule takes from ``prior`` (``is_member``, ``member_since``)
    changes, so a usable answer is the one the unblanked prior gives —
    ``test_trillion_club_jobs.py`` pins that equivalence over every mode.
    """
    base = prior if prior is not None else rules.NOT_A_MEMBER
    probe = replace(base, last_cap=None, last_cap_date=None)
    state = rules.evaluate_membership(
        closes, mode=mode, prior=probe, today_et=today_et, now_et=now_et, log_ctx=log_ctx,
    )
    if state is None or state.last_cap is None or state.last_cap_date is None:
        return None
    return state


async def _membership_stage(
    companies: Sequence[Company], fmp: Any, store: Any, now: datetime, summary: Dict[str, Any],
) -> Dict[str, datetime]:
    stage: Dict[str, Any] = {"ok": True, "evaluated": 0, "written": [], "kept": [],
                             "changed": [], "manual_cap_stale": [], "errors": [], "outage": []}
    summary["membership"] = stage
    now_et = now.astimezone(ET)
    today_et = now_et.date()
    checked: Dict[str, datetime] = {}
    answered: List[str] = []        # FMP-sized companies whose history call did not raise
    unusable: List[str] = []        # ... of which the evaluation found no usable close
    for c in companies:
        ctx = f"slug={c.slug} symbol={c.cap_symbol or '-'} mode={c.membership_mode}"
        fmp_sized = c.cap_source != CAP_SOURCE_MANUAL
        if not fmp_sized:
            closes: List[Tuple[Any, Any]] = []
            age = (today_et - c.manual_cap_as_of).days if c.manual_cap_as_of else None
            if age is None or age > MANUAL_CAP_WARN_DAYS:
                logger.warning(
                    "trillion club membership (%s): the hand-entered cap is %s — older than %d "
                    "days; membership stays the owner's %s, but re-check manual_cap_usd / "
                    "manual_cap_as_of in Studio", ctx,
                    f"dated {c.manual_cap_as_of.isoformat()} ({age} days)" if age is not None else "undated",
                    MANUAL_CAP_WARN_DAYS, c.membership_mode,
                )
                stage["manual_cap_stale"].append(c.slug)
        else:
            try:
                rows = await fmp.get_historical_market_cap(
                    c.cap_symbol,
                    from_date=(today_et - timedelta(days=HISTORY_LOOKBACK_DAYS)).isoformat(),
                    to_date=today_et.isoformat(),
                    limit=HISTORY_LIMIT,
                )
            except Exception as e:
                logger.warning(
                    "trillion club membership (%s): market-cap history failed (%s: %s) — "
                    "keeping the stored state", ctx, type(e).__name__, e,
                )
                stage["errors"].append(f"{c.slug}: {type(e).__name__}")
                _fail(summary, stage, f"membership {c.slug}: history fetch {type(e).__name__}: {e}")
                continue
            answered.append(c.slug)
            closes = _closes_from(rows, c.cap_symbol or "", ctx)
        stage["evaluated"] += 1
        evaluate = evaluate_fmp_sized if fmp_sized else rules.evaluate_membership
        try:
            state = evaluate(
                closes, mode=c.membership_mode, prior=c.prior, today_et=today_et, now_et=now_et,
                log_ctx=ctx,
            )
        except Exception as e:                        # a bug in the rule, not in the data
            logger.exception("trillion club membership (%s): evaluate_membership raised", ctx)
            stage["errors"].append(f"{c.slug}: {type(e).__name__}")
            _fail(summary, stage, f"membership {c.slug}: evaluate {type(e).__name__}: {e}")
            continue
        if state is None:
            logger.warning(
                "trillion club membership (%s): no usable close (%d row(s) from FMP; see any "
                "line above) — the stored state is KEPT and membership_checked_at is NOT "
                "advanced%s", ctx, len(closes),
                "" if c.membership_mode == rules.MODE_AUTO
                else "; the owner's override still decides what Home shows",
            )
            stage["kept"].append(c.slug)
            if fmp_sized:
                unusable.append(c.slug)
            continue
        try:
            await store.update_membership(c.slug, state, checked_at=now)
        except Exception as e:
            logger.error("trillion club membership (%s): write failed (%s: %s)", ctx,
                         type(e).__name__, e, exc_info=True)
            stage["errors"].append(f"{c.slug}: write {type(e).__name__}")
            _fail(summary, stage, f"membership {c.slug}: write {type(e).__name__}: {e}")
            continue
        checked[c.slug] = now
        stage["written"].append(c.slug)
        summary["items"] += 1
        if state.is_member != c.prior.is_member:
            stage["changed"].append({"slug": c.slug, "is_member": state.is_member})
            logger.info(
                "trillion club membership CHANGE (%s): %s the club — last close %s on %s, "
                "%d close(s) at/above and %d below $1T in the current streak", ctx,
                "JOINED" if state.is_member else "LEFT",
                f"${state.last_cap:,.0f}" if state.last_cap else "n/a",
                state.last_cap_date.isoformat() if state.last_cap_date else "n/a",
                state.closes_at_or_above, state.closes_below,
            )
    if len(answered) >= MEMBERSHIP_OUTAGE_MIN_ANSWERS and len(unusable) == len(answered):
        stage["outage"] = list(answered)
        logger.error(
            "trillion club membership: FMP answered for %d FMP-sized companies without a "
            "single usable close among them (%s) — an upstream soft outage (200 with no rows, a stale or "
            "unreadable series), not %d structural problems. Nothing was stamped; the run is "
            "marked FAILED so the scheduler retries it today.",
            len(answered), ", ".join(answered), len(answered),
        )
        _fail(summary, stage, f"membership: no usable close for any of the {len(answered)} "
                              f"FMP-sized companies FMP answered for ({', '.join(answered)})")
    return checked


def _log_stale(companies: Sequence[Company], checked: Mapping[str, datetime], now: datetime,
               summary: Dict[str, Any]) -> None:
    stale: List[str] = []
    for c in companies:
        if not c.published:
            continue
        at = checked.get(c.slug, c.checked_at)
        if at is None or now - at > STALE_ERROR_AFTER:
            stale.append(f"{c.slug} ({at.isoformat() if at else 'never'})")
    summary["stale"] = stale
    if stale:
        logger.error(
            "trillion club STALE: membership of %d published compan%s last checked more than "
            "%d days ago: %s — the daily job is failing, or failing closed for them; the "
            "request path stops trusting membership that is 7 days stale",
            len(stale), "y" if len(stale) == 1 else "ies", STALE_ERROR_AFTER.days, ", ".join(stale),
        )


# ── 13F filings ──────────────────────────────────────────────────────────────────────


def listed_periods(dates: Any) -> Set[Tuple[int, int]]:
    """``{(year, quarter)}`` from FMP's ``institutional-ownership/dates`` rows."""
    out: Set[Tuple[int, int]] = set()
    for d in dates if isinstance(dates, list) else ():
        if not isinstance(d, dict):
            continue
        y, q = d.get("year"), d.get("quarter")
        if isinstance(y, bool) or isinstance(q, bool):
            continue
        try:
            y, q = int(y), int(q)
        except (TypeError, ValueError):
            continue
        if q in (1, 2, 3, 4) and 1900 <= y <= 9998:
            out.add((y, q))
    return out


def _needs_build(stored_row: Optional[Mapping[str, Any]]) -> bool:
    return stored_row is None or stored_row.get("build_status") != BUILD_COMPLETE


def select_targets(
    listed: Set[Tuple[int, int]], stored: Mapping[str, Mapping[str, Any]], mode: str, today: date,
) -> List[Tuple[int, int]]:
    """Quarters to check, NEWEST FIRST (the one on the card is what matters most).

    Daily: the newest listed quarter always (hash-checked), plus any of the newest
    ``MAX_QUARTERS`` that is not stored or not ``complete``. Weekly: every quarter of that
    window still within ``REHASH_WINDOW_DAYS`` of its quarter end (late amendments), plus
    the same missing / degraded ones.
    """
    window = sorted(listed, reverse=True)[:MAX_QUARTERS]
    targets: List[Tuple[int, int]] = []
    for i, yq in enumerate(window):
        label = rules.period_label(*yq)
        if _needs_build(stored.get(label)):
            targets.append(yq)
        elif mode == DAILY and i == 0:
            targets.append(yq)
        elif mode == WEEKLY and (today - rules.quarter_end(*yq)).days <= REHASH_WINDOW_DAYS:
            targets.append(yq)
    return targets


@dataclass
class _CikRun:
    company: Company
    cik: str
    listed: Set[Tuple[int, int]]
    stored: Dict[str, Dict[str, Any]]
    newest: Tuple[int, int]
    built: Set[Tuple[int, int]] = field(default_factory=set)


async def _process_quarter(
    run: _CikRun, yq: Tuple[int, int], *, force: bool, depth: int, fmp: Any, store: Any,
    actions: Any, today: date, now: datetime, summary: Dict[str, Any], stage: Dict[str, Any],
) -> bool:
    """Bring one quarter's stored row up to date. True = the stored row is now current.

    ``force`` rebuilds even when the raw hash matches (the cascade: this quarter's diff was
    computed against a previous quarter whose rows changed).
    """
    if yq in run.built:
        return True
    period = rules.period_label(*yq)
    ctx = f"slug={run.company.slug} cik={run.cik} period={period}"
    essential = force or yq == run.newest
    stored_row = run.stored.get(period)

    def _problem(kind: str, message: str) -> None:
        entry = f"{run.company.slug} {run.cik} {period}: {message}"
        stage[kind].append(entry)
        if essential or kind not in DEFERRABLE_OLDER_QUARTER_PROBLEMS:
            _fail(summary, stage, f"filings {entry}")
        else:
            logger.warning("trillion club filings (%s): older quarter not built this run "
                           "(%s) — retried on the next run; the card's quarter is unaffected",
                           ctx, kind)

    try:
        raw = await fmp.get_institutional_holdings(run.cik, yq[0], yq[1], strict=True)
    except Exception as e:
        logger.warning("trillion club filings (%s): 13F extract failed (%s: %s) — nothing "
                       "written", ctx, type(e).__name__, e)
        _problem("unavailable", f"extract {type(e).__name__}: {e}")
        return False
    new_hash = raw_hash_of(raw) if isinstance(raw, list) else None
    if (not force and stored_row is not None and new_hash is not None
            and stored_row.get("raw_hash") == new_hash
            and stored_row.get("build_status") == BUILD_COMPLETE):
        stage["unchanged"].append(f"{run.cik}:{period}")
        return True

    prev = rules.previous_quarter(*yq)
    try:
        built = await build_filing(
            fmp, run.cik, yq[0], yq[1],
            prev_quarter_available=prev in run.listed,
            actions=actions,
            stored_unresolved=(stored_row or {}).get("unresolved") or {},
            today=today,
            older_filing_exists=any(p < yq for p in run.listed),
        )
    except FilingRefused as e:
        # Permanent (the book's size), so never a run failure — retrying cannot fix it.
        logger.warning(
            "trillion club filings (%s): REFUSED, nothing written — %s. A book this size is "
            "client assets, not %s's own stakes: turn use_13f OFF for it in Studio.",
            ctx, e, run.company.display_name,
        )
        stage["refused"].append(f"{run.company.slug} {run.cik} {period}")
        return False
    except FilingUnavailable as e:
        logger.warning("trillion club filings (%s): unavailable, nothing written — %s", ctx, e)
        _problem("unavailable", str(e))
        return False
    except Exception as e:
        logger.exception("trillion club filings (%s): build raised %s", ctx, type(e).__name__)
        _problem("errors", f"build {type(e).__name__}: {e}")
        return False

    try:
        row = built.as_row()
    except ValueError as e:                          # a bug: the builder produced a bad row
        logger.exception("trillion club filings (%s): built row refused before the write", ctx)
        _problem("errors", f"as_row {e}")
        return False

    changed = stored_row is None or stored_row.get("raw_hash") != built.raw_hash
    nxt = rules.next_quarter(*yq)
    nxt_label = rules.period_label(*nxt)
    if changed and nxt_label in run.stored and nxt not in run.built:
        if depth >= MAX_CASCADE_DEPTH:
            logger.error("trillion club filings (%s): cascade depth %d reached — %s not "
                         "rebuilt", ctx, depth, nxt_label)
            _problem("errors", f"cascade depth {depth} reached before {nxt_label}")
            return False
        logger.info("trillion club filings (%s): raw rows changed — rebuilding %s, whose diff "
                    "was computed against them, before writing", ctx, nxt_label)
        stage["cascaded"].append(f"{run.cik}:{nxt_label}")
        ok = await _process_quarter(
            run, nxt, force=True, depth=depth + 1, fmp=fmp, store=store, actions=actions,
            today=today, now=now, summary=summary, stage=stage,
        )
        if not ok:
            # Writing this quarter now would store the new hash and the next run would never
            # know the next quarter still diffs against the OLD rows. Keep the old row.
            logger.warning("trillion club filings (%s): NOT written — %s could not be rebuilt "
                           "against it; both are retried on the next run", ctx, nxt_label)
            _problem("cascade_blocked", f"next quarter {nxt_label} not rebuilt")
            return False

    try:
        await store.upsert_filing(row, built_at=now)
    except Exception as e:
        logger.error("trillion club filings (%s): write failed (%s: %s)", ctx,
                     type(e).__name__, e, exc_info=True)
        _problem("errors", f"write {type(e).__name__}: {e}")
        return False
    run.built.add(yq)
    stage["written"].append(f"{run.cik}:{period}")
    summary["items"] += 1
    if built.build_status != BUILD_COMPLETE:
        stage["degraded"].append({"cik": run.cik, "period": period, "reasons": list(built.degraded_reasons)})
        logger.warning("trillion club filings (%s): written DEGRADED (%s) — rebuilt on the "
                       "next run", ctx, "; ".join(built.degraded_reasons) or "no reason given")
    return True


async def _filings_stage(
    companies: Sequence[Company], fmp: Any, store: Any, actions: Any, now: datetime,
    summary: Dict[str, Any], *, mode: str, stage_name: str,
) -> None:
    stage: Dict[str, Any] = {"ok": True, "written": [], "unchanged": [], "refused": [],
                             "unavailable": [], "cascaded": [], "cascade_blocked": [],
                             "degraded": [], "no_filings": [], "errors": []}
    summary[stage_name] = stage
    today = now.astimezone(ET).date()
    run_fmp = _RunFMP(fmp)
    for c in companies:
        if not c.use_13f:
            continue
        if not c.ciks:
            logger.error("trillion club filings (slug=%s): use_13f is on but no CIK is stored",
                         c.slug)
            _fail(summary, stage, f"filings {c.slug}: use_13f with no CIK")
            continue
        for cik in c.ciks:
            ctx = f"slug={c.slug} cik={cik}"
            try:
                dates = await run_fmp.get_institutional_filing_dates(cik, strict=True)
            except Exception as e:
                logger.warning("trillion club filings (%s): 13F dates failed (%s: %s) — nothing "
                               "written for this CIK", ctx, type(e).__name__, e)
                stage["errors"].append(f"{c.slug} {cik}: dates {type(e).__name__}")
                _fail(summary, stage, f"filings {c.slug} {cik}: dates {type(e).__name__}: {e}")
                continue
            listed = listed_periods(dates)
            if not listed:
                logger.warning("trillion club filings (%s): use_13f is on but FMP lists no 13F "
                               "quarter for this CIK (a CIK move, or it stopped filing?) — "
                               "stored rows are kept", ctx)
                stage["no_filings"].append(f"{c.slug} {cik}")
                continue
            try:
                stored = await store.read_filings(cik)
            except Exception as e:
                logger.error("trillion club filings (%s): stored-quarter read failed (%s: %s) — "
                             "nothing written for this CIK", ctx, type(e).__name__, e, exc_info=True)
                stage["errors"].append(f"{c.slug} {cik}: read {type(e).__name__}")
                _fail(summary, stage, f"filings {c.slug} {cik}: read {type(e).__name__}: {e}")
                continue
            run = _CikRun(company=c, cik=cik, listed=listed, stored=dict(stored or {}),
                          newest=max(listed))
            queue: Deque[Tuple[int, int]] = deque(select_targets(listed, run.stored, mode, today))
            while queue:
                yq = queue.popleft()
                await _process_quarter(
                    run, yq, force=False, depth=0, fmp=run_fmp, store=store, actions=actions,
                    today=today, now=now, summary=summary, stage=stage,
                )


# ── Weekly: new-filer probe and discovery ────────────────────────────────────────────


async def _probe_stage(companies: Sequence[Company], fmp: Any, today: date,
                       summary: Dict[str, Any]) -> None:
    stage: Dict[str, Any] = {"ok": True, "probed": 0, "new_filers": [], "no_filings": [],
                             "errors": []}
    summary["probe"] = stage
    for c in companies:
        if c.home_country != "US" or not c.effective_member or not c.ciks:
            continue
        for cik in c.ciks:
            ctx = f"slug={c.slug} cik={cik} card_kind={c.card_kind} use_13f={c.use_13f}"
            stage["probed"] += 1
            try:
                dates = await fmp.get_institutional_filing_dates(cik, strict=True)
            except Exception as e:
                logger.warning("trillion club probe (%s): 13F dates failed (%s: %s)", ctx,
                               type(e).__name__, e)
                stage["errors"].append(f"{c.slug} {cik}: {type(e).__name__}")
                _fail(summary, stage, f"probe {c.slug} {cik}: {type(e).__name__}: {e}")
                continue
            listed = listed_periods(dates)
            if not listed:
                if c.use_13f:
                    logger.warning("trillion club probe (%s): use_13f is on but FMP lists no 13F "
                                   "for this CIK", ctx)
                    stage["no_filings"].append(f"{c.slug} {cik}")
                continue
            newest = max(listed)
            recent = (today - rules.quarter_end(*newest)).days <= NEW_FILER_RECENT_DAYS
            if c.card_kind in NON_FILER_CARD_KINDS and recent:
                logger.warning(
                    "trillion club NEW 13F FILER (%s): the registry says %s does not file 13Fs, "
                    "but FMP lists %d 13F quarter(s), newest %s. use_13f stays OFF — code never "
                    "turns it on; review the filing and the card_kind in Studio.",
                    ctx, c.display_name, len(listed), rules.period_label(*newest),
                )
                stage["new_filers"].append({"slug": c.slug, "cik": cik,
                                            "newest": rules.period_label(*newest)})
            elif c.card_kind in NON_FILER_CARD_KINDS:
                logger.info("trillion club probe (%s): only old 13F quarters (newest %s) — not "
                            "a new filer", ctx, rules.period_label(*newest))


def _screen_rows(rows: Any, known: Set[str]) -> Dict[str, Dict[str, Any]]:
    """Unknown screener candidates by normalised symbol (ETFs, funds, other exchanges and
    sub-floor or garbage caps dropped)."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows if isinstance(rows, list) else ():
        if not isinstance(r, dict):
            continue
        sym = norm_symbol(r.get("symbol"))
        if sym is None or sym in known or sym in out:
            continue
        if r.get("isEtf") is True or r.get("isFund") is True or r.get("isActivelyTrading") is False:
            continue
        exchange = str(r.get("exchangeShortName") or r.get("exchange") or "").strip().upper()
        if exchange not in DISCOVERY_EXCHANGES:
            continue
        cap = _positive_finite(r.get("marketCap"))
        if cap is None or cap < DISCOVERY_MIN_CAP_USD:
            continue
        out[sym] = {"symbol": sym, "name": r.get("companyName"), "exchange": exchange,
                    "country": r.get("country"), "screen_cap": cap}
    return out


_CLASS_SUFFIX_RE = re.compile(r"[\s,-]+(?:class|cl\.?|series)\s+[a-z0-9]$")


def company_key(name: Any) -> Optional[str]:
    """A share-class-blind key for a screener ``companyName`` (None when there is none).

    FMP's company-screener lists every share class as its own row at the company's TOTAL
    cap and carries no CIK (the research probe saw BRK-A and BRK-B both at ~$1.1T; both
    classes carry the same ``companyName``), so the name is the only thing that ties two
    rows to one company. Only case, spacing, a trailing "Class B" / "Series A" and trailing
    ``.``/``,`` are ignored — deliberately NOT inner punctuation: a false merge hides a real
    company inside another's aliases for good, a false split is only a second row to
    review."""
    if not isinstance(name, str):
        return None
    key = " ".join(name.casefold().split())
    key = _CLASS_SUFFIX_RE.sub("", key).rstrip(" .,")
    return key or None


def registry_symbols(rows: Iterable[Any]) -> Set[str]:
    """Every symbol the RAW registry rows name — ``cap_symbol``, ``detail_symbol``,
    ``logo_symbol`` and ``symbol_aliases`` — rows the parser rejected included."""
    out: Set[str] = set()
    for r in rows:
        if not isinstance(r, Mapping):
            continue
        aliases = r.get("symbol_aliases")
        values = [r.get("cap_symbol"), r.get("detail_symbol"), r.get("logo_symbol")]
        values += list(aliases) if isinstance(aliases, (list, tuple)) else []
        out.update(s for s in (norm_symbol(v) for v in values) if s)
    return out


def registry_slugs(rows: Iterable[Any]) -> Set[str]:
    """Every slug the RAW registry rows hold (a malformed row's slug is still taken)."""
    return {r["slug"] for r in rows if isinstance(r, Mapping) and isinstance(r.get("slug"), str)}


def _slug_for(symbol: str, taken: Set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", symbol.lower()).strip("-")[:36] or "company"
    slug, n = base, 2
    while slug in taken:
        slug = f"{base}-{n}"
        n += 1
    return slug


def discovered_row(candidate: Mapping[str, Any], taken_slugs: Set[str], *,
                   aliases: Iterable[str] = ()) -> Dict[str, Any]:
    """The unpublished registry row for a discovered symbol (satisfies every 175 CHECK).

    ``aliases`` are the company's other share classes (normalised, de-duplicated, never the
    primary symbol itself)."""
    sym = candidate["symbol"]
    primary = norm_symbol(sym)
    other_classes: List[str] = []
    for a in aliases:
        s = norm_symbol(a)
        if s and s != primary and s not in other_classes:
            other_classes.append(s)
    country = candidate.get("country")
    country = country.strip().upper() if isinstance(country, str) else ""
    if not _COUNTRY_RE.match(country):
        country = "US"
    name = candidate.get("name")
    name = name.strip()[:60] if isinstance(name, str) and name.strip() else sym
    return {
        "slug": _slug_for(sym, taken_slugs),
        "display_name": name,
        "ciks": [],
        "card_kind": "no_thirteen_f",
        "use_13f": False,
        "cap_symbol": sym,
        "symbol_aliases": other_classes,
        "detail_symbol": sym,
        "logo_symbol": sym,
        "home_country": country,
        "cap_source": "fmp_us" if country == "US" else "fmp_adr",
        "membership_mode": rules.MODE_AUTO,
        "link_whale": False,
        "published": False,
    }


async def _discovery_stage(companies: Sequence[Company], registry_rows: Sequence[Any], fmp: Any,
                           store: Any, summary: Dict[str, Any]) -> None:
    """``registry_rows`` are the RAW rows (malformed ones included): "already in the
    registry" is judged on them, not on the rows that parsed."""
    stage: Dict[str, Any] = {"ok": True, "screened": 0, "candidates": [], "unconfirmed": [],
                             "share_classes": [], "known_share_classes": [], "inserted": [],
                             "not_inserted": []}
    summary["discovery"] = stage
    try:
        rows = await fmp.get_company_screener(
            market_cap_more_than=DISCOVERY_MIN_CAP_USD, exchange=",".join(DISCOVERY_EXCHANGES),
            actively_trading=True, is_fund=False, is_etf=False, limit=DISCOVERY_SCREEN_LIMIT,
        )
    except Exception as e:
        logger.warning("trillion club discovery: screener failed (%s: %s)", type(e).__name__, e)
        _fail(summary, stage, f"discovery screener: {type(e).__name__}: {e}")
        return
    if not isinstance(rows, list) or not rows:
        # ~15 companies clear $900B; an empty screen is FMP failing, not "none left".
        logger.warning("trillion club discovery: the ≥$900B screen came back empty — treated "
                       "as a failure, not as 'no companies'")
        _fail(summary, stage, "discovery screener: empty result")
        return
    stage["screened"] = len(rows)
    if len(rows) >= DISCOVERY_SCREEN_LIMIT:
        logger.warning("trillion club discovery: the screen hit its %d-row ceiling — some "
                       "candidates may be missing", DISCOVERY_SCREEN_LIMIT)
    # RAW rows: a row the parser skipped (e.g. cap_symbol '' after a Studio edit) is still
    # that company, and re-inserting it under a new slug would duplicate it.
    known = registry_symbols(registry_rows) | {s for c in companies for s in c.symbols}
    candidates = _screen_rows(rows, known)
    # Another class of a company the registry already holds (its row lacks this alias): the
    # screener gives both classes the same companyName. Not a new company — and without
    # this, deleting the duplicate a past run inserted would never stick.
    known_by_key: Dict[str, str] = {}
    for r in rows:
        if isinstance(r, dict) and norm_symbol(r.get("symbol")) in known:
            key = company_key(r.get("companyName"))
            if key:
                known_by_key.setdefault(key, norm_symbol(r.get("symbol")) or "")
    for sym in sorted(candidates):
        held_as = known_by_key.get(company_key(candidates[sym].get("name")) or "")
        if held_as:
            logger.warning(
                "trillion club discovery: %s screens as another share class of %s (%r), which "
                "the registry already holds — NOT inserted. Add %s to that row's "
                "symbol_aliases in Studio so holdings of it are tagged as the club member.",
                sym, held_as, candidates[sym].get("name"), sym,
            )
            stage["known_share_classes"].append({"symbol": sym, "held_as": held_as})
            del candidates[sym]
    stage["candidates"] = sorted(candidates)
    if not candidates:
        logger.info("trillion club discovery: every company screened at ≥ $900B is already in "
                    "the registry (%d rows screened)", len(rows))
        return
    try:
        batch = await fmp.get_market_cap_batch(sorted(candidates))
    except Exception as e:
        logger.warning("trillion club discovery: market-capitalization-batch failed (%s: %s) — "
                       "nothing inserted (%s unconfirmed)", type(e).__name__, e,
                       ", ".join(sorted(candidates)))
        _fail(summary, stage, f"discovery batch: {type(e).__name__}: {e}")
        return
    caps: Dict[str, float] = {}
    for b in batch if isinstance(batch, list) else ():
        if isinstance(b, dict):
            sym, cap = norm_symbol(b.get("symbol")), _positive_finite(b.get("marketCap"))
            if sym and cap is not None:
                caps[sym] = cap
    taken = registry_slugs(registry_rows) | {c.slug for c in companies}
    # One company per share-class group: the screener lists each class as its own row at the
    # company's total cap, with no CIK — two rows would mean two daily history calls, two
    # reviews, and a deleted duplicate coming back next Monday.
    groups: Dict[str, List[str]] = {}
    for sym in sorted(candidates):
        key = company_key(candidates[sym].get("name")) or f"symbol:{sym}"
        groups.setdefault(key, []).append(sym)
    rows_to_insert: List[Dict[str, Any]] = []
    for syms in groups.values():
        confirmed = [s for s in syms
                     if caps.get(s) is not None and caps[s] >= DISCOVERY_MIN_CAP_USD]
        if not confirmed:
            for sym in syms:
                cap = caps.get(sym)
                logger.info("trillion club discovery: %s screened at $%.0f but "
                            "market-capitalization-batch says %s — not inserted", sym,
                            candidates[sym]["screen_cap"], f"${cap:,.0f}" if cap else "nothing")
                stage["unconfirmed"].append(sym)
            continue
        primary = confirmed[0]
        others = [s for s in syms if s != primary]
        if others:
            logger.info("trillion club discovery: %s screened as %d share classes of one "
                        "company (%r) — ONE row with cap_symbol %s and symbol_aliases %s",
                        ", ".join(syms), len(syms), candidates[primary].get("name"), primary,
                        others)
            stage["share_classes"].append({"cap_symbol": primary, "aliases": others})
        row = discovered_row(candidates[primary], taken, aliases=others)
        taken.add(row["slug"])
        rows_to_insert.append(row)
    stage["unconfirmed"].sort()
    if not rows_to_insert:
        return
    try:
        inserted = await store.insert_discovered(rows_to_insert)
    except Exception as e:
        logger.error("trillion club discovery: insert failed (%s: %s)", type(e).__name__, e,
                     exc_info=True)
        _fail(summary, stage, f"discovery insert: {type(e).__name__}: {e}")
        return
    stage["inserted"] = list(inserted)
    summary["items"] += len(inserted)
    for r in rows_to_insert:
        if r["slug"] in stage["inserted"]:
            continue
        # ON CONFLICT (slug) DO NOTHING dropped it: the slug appeared after this run's
        # registry read. Never silent — a confirmed candidate must not just vanish.
        stage["not_inserted"].append(r["slug"])
        logger.warning(
            "trillion club discovery: %s (%s) was confirmed at ≥ $900B but NOT inserted — a row "
            "with slug %r already exists (added after this run read the registry?). Nothing "
            "was overwritten; check Studio and add %s to that row's symbol_aliases if it is "
            "the same company.", r["cap_symbol"], r["display_name"], r["slug"], r["cap_symbol"],
        )
    by_slug = {r["slug"]: r for r in rows_to_insert}
    for slug in inserted:
        r = by_slug.get(slug, {})
        cap = caps.get(r.get("cap_symbol") or "")
        aliases = r.get("symbol_aliases") or []
        logger.warning(
            "trillion club DISCOVERED %s%s (%s) at about $%s (intraday, discovery only) — "
            "inserted UNPUBLISHED as slug %r with use_13f off. Review it in Studio (card_kind, "
            "ciks, stakes) before publishing; the daily job now tracks its membership.",
            r.get("cap_symbol"),
            f" (other share classes as symbol_aliases: {', '.join(aliases)})" if aliases else "",
            r.get("display_name"), f"{cap / 1e12:.2f}T" if cap else "?", slug,
        )


# ── Entry points ─────────────────────────────────────────────────────────────────────


async def run_daily(now: datetime, *, fmp: Any = None, db: Any = None, actions: Any = None) -> Dict[str, Any]:
    """The daily job: membership for every company, then 13F builds for ``use_13f`` ones.

    ``fmp`` defaults to the FMP singleton, ``db`` to a :class:`TrillionClubStore` over the
    service-role client (a Supabase client or a store may be passed), ``actions`` to the
    corporate-actions singleton (the builder's default). Never raises for a data or I/O
    problem — it is reported in the summary (``ok`` False) and logged; a programming error
    (a naive ``now``) raises ``ValueError``.
    """
    now = _check_now(now)
    summary = _new_summary(DAILY, now)
    store, fmp = _store_for(db), _fmp_for(fmp)
    registry = await _read_registry(store, summary)
    if registry is None:
        return summary
    companies, _raw_rows = registry
    checked: Dict[str, datetime] = {}
    try:
        checked = await _membership_stage(companies, fmp, store, now, summary)
    except Exception as e:
        logger.exception("trillion club daily: membership stage crashed")
        summary.setdefault("membership", {"ok": False})
        _fail(summary, summary["membership"], f"membership crashed: {type(e).__name__}: {e}")
    _log_stale(companies, checked, now, summary)
    try:
        await _filings_stage(companies, fmp, store, actions, now, summary,
                             mode=DAILY, stage_name="filings")
    except Exception as e:
        logger.exception("trillion club daily: filings stage crashed")
        summary.setdefault("filings", {"ok": False})
        _fail(summary, summary["filings"], f"filings crashed: {type(e).__name__}: {e}")
    if summary["items"]:
        _invalidate_read_cache()
    log = logger.info if summary["ok"] else logger.warning
    log("trillion club daily: %s — %d row(s) written; membership %s, filings %s%s",
        "ok" if summary["ok"] else "INCOMPLETE", summary["items"],
        _stage_brief(summary.get("membership")), _stage_brief(summary.get("filings")),
        f"; failures: {' | '.join(summary['failures'])}" if summary["failures"] else "")
    return summary


async def run_weekly(now: datetime, *, fmp: Any = None, db: Any = None, actions: Any = None) -> Dict[str, Any]:
    """The weekly job: 13F re-hash (12 months), US-member new-filer probe, discovery.

    Same contract as :func:`run_daily`.
    """
    now = _check_now(now)
    summary = _new_summary(WEEKLY, now)
    store, fmp = _store_for(db), _fmp_for(fmp)
    registry = await _read_registry(store, summary)
    if registry is None:
        return summary
    companies, raw_rows = registry
    today = now.astimezone(ET).date()
    stages = (
        ("rehash", lambda: _filings_stage(companies, fmp, store, actions, now, summary,
                                          mode=WEEKLY, stage_name="rehash")),
        ("probe", lambda: _probe_stage(companies, fmp, today, summary)),
        ("discovery", lambda: _discovery_stage(companies, raw_rows, fmp, store, summary)),
    )
    for name, stage_fn in stages:
        try:
            await stage_fn()
        except Exception as e:
            logger.exception("trillion club weekly: %s stage crashed", name)
            summary.setdefault(name, {"ok": False})
            _fail(summary, summary[name], f"{name} crashed: {type(e).__name__}: {e}")
    _log_stale(companies, {}, now, summary)
    if summary["items"]:
        _invalidate_read_cache()
    log = logger.info if summary["ok"] else logger.warning
    log("trillion club weekly: %s — %d row(s) written; rehash %s, probe %s, discovery %s%s",
        "ok" if summary["ok"] else "INCOMPLETE", summary["items"],
        _stage_brief(summary.get("rehash")), _stage_brief(summary.get("probe")),
        _stage_brief(summary.get("discovery")),
        f"; failures: {' | '.join(summary['failures'])}" if summary["failures"] else "")
    return summary


def _stage_brief(stage: Optional[Mapping[str, Any]]) -> str:
    if not stage:
        return "not run"
    parts = [("ok" if stage.get("ok") else "FAILED")]
    for key, value in stage.items():
        if key == "ok":
            continue
        n = len(value) if isinstance(value, (list, dict)) else value
        if n:
            parts.append(f"{key}={n}")
    return "(" + ", ".join(str(p) for p in parts) + ")"


def failure_text(summary: Mapping[str, Any], limit: int = 480) -> Optional[str]:
    """A short ``last_error`` for the job ledger, or None for a successful run.

    Success is ``ok is True`` exactly — the scheduler's own test — so a malformed verdict
    (``"yes"``, ``1``) can never record a failed run with no reason."""
    if summary.get("ok") is True:
        return None
    text = " | ".join(str(f) for f in summary.get("failures") or []) or "run incomplete"
    return text[:limit]


__all__ = [
    "FMP_CAP_SOURCES", "CAP_SOURCE_MANUAL", "HISTORY_LIMIT", "HISTORY_LOOKBACK_DAYS", "MAX_QUARTERS",
    "REHASH_WINDOW_DAYS", "STALE_ERROR_AFTER", "MANUAL_CAP_WARN_DAYS", "DISCOVERY_MIN_CAP_USD",
    "DISCOVERY_EXCHANGES", "DISCOVERY_SCREEN_LIMIT", "NEW_FILER_RECENT_DAYS",
    "NON_FILER_CARD_KINDS", "MEMBERSHIP_OUTAGE_MIN_ANSWERS", "DEFERRABLE_OLDER_QUARTER_PROBLEMS",
    "DAILY", "WEEKLY", "Company", "parse_company", "norm_symbol", "evaluate_fmp_sized",
    "listed_periods", "select_targets", "company_key", "registry_symbols", "registry_slugs",
    "discovered_row", "run_daily", "run_weekly", "failure_text",
]
