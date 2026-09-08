"""Corporate actions derived from entitled price series — splits and ex-dividend dates.

WHY THIS EXISTS
---------------
FMP's ``/splits`` and ``/dividends`` are in the "Market Calendar" package, which is NOT on
the signed Order Form. Since enforcement (2026-09-03) both answer ``402``, and
``fmp.py``'s guard refuses them before the request. Every caller catches to ``[]``, so
**no split is detected at all** — and that is not a cosmetic loss:

* ``whale_service._diff_quarters`` restates a holder's previous-quarter share count across
  a split. With an empty ratio map a position merely HELD through a 10:1 split reads as a
  purchase, and a fabricated multi-million-dollar BOUGHT is written to ``whale_trades``,
  which feeds user alerts.
* ``holders_service._build_institutional_activities`` produced the documented KLAC case:
  BlackRock rendered as **+$34,275.0M / +901.88%** on a true move of about +$71M.

Both are entitled to be fixed from data we DO own. ``historical-price-eod/full`` is
adjusted; ``historical-price-eod/non-split-adjusted`` is raw. Their ratio is the cumulative
adjustment factor, and it changes on — and only on — a corporate action.

THE PART THAT IS NOT OBVIOUS
----------------------------
**"The factor changed" is NOT "a split happened."** FMP's ``/full`` series is adjusted for
**spin-offs** as well, and a spin-off changes **no holder's share count**. Treating one as a
split fabricates exactly the phantom trade this module exists to prevent. Measured over a
77-ticker / 49-event sweep, a bare "factor changed" rule produced **13 false positives** —
GE HealthCare (1.280866), GE Vernova (1.252967), T→WBD (1.323820), XPO→GXO (1.717958),
DHR→Veralto (1.128001), MMM, IBM→Kyndryl, MRK→Organon, IP→Sylvamo, ZBH→ZimVie, LH→Fortrea,
DD→Qnity.

Three discriminators were tested against live data and **ruled out** — do not re-litigate:

* **Volume ratio** — FMP applies the identical factor to volume, spin-offs included.
* **``historical-market-capitalization``** — back-computed from the adjusted series, so it
  carries the same factor. (It is therefore also *wrong* for any company that has spun off:
  GE's implied 2023 share count is 678M against a real 1.09B.)
* **``income-statement.weightedAverageShsOut`` / ``enterprise-values.numberOfShares``** —
  **split-restated**. GE sits flat at ~1.09B straight through its 1:8 reverse split and NVDA
  flat at ~24.6B through both of its splits, so they are blind to splits *and* spin-offs.

What works is the shape of the number itself. A real split is an exact small rational
(10:1, 3:2, 1:30); a spin-off adjustment is an arbitrary real. Snapping to ``p/q`` with
``min(p,q) <= 4``, ``max(p,q) <= 100`` and a relative tolerance of 1.4e-4 separated the
measured set perfectly:

===========================  =========  ========  ========
class                        detected   accepted  rejected
===========================  =========  ========  ========
forward splits                     28        28         0
reverse splits                      8         8         0
spin-offs / other actions          13         0        13
controls (15 tickers)               0         -         -
===========================  =========  ========  ========

Worst accepted real split: **2.80e-04** — GE's 1:8 reverse. That one is NOT machine-exact
like the other 35, and the reason sets the precision floor for this whole module: FMP
stores a rounded cumulative adjustment factor, so a symbol whose factor compounds a split
with spin-offs inherits that rounding. (Taking the median factor across the regime instead
of a single day-pair does not help — measured 2.93e-04, i.e. systematic, not per-bar noise.)

Nearest rejected non-split: **2.91e-02** (ZBH/ZimVie, nearest candidate 1:1). The tolerance
sits at the geometric midpoint, roughly 10x clear in both directions.

AN UNCLASSIFIED EVENT IS NOT "SUPPRESS"
---------------------------------------
Tempting, and wrong. A spin-off leaves share counts untouched, so for a spin-off the **raw
13F diff is already correct** and suppressing it would delete real flow. The genuinely
dangerous case is the opposite one: an out-of-range reverse split (1:150, 1:200 — routine in
delisting-defence microcaps, excluded by ``max(p,q) <= 100``). Price data alone cannot tell
those two apart, and this module does not pretend to.

The discriminator for that lives on the 13F side and already exists:
:func:`app.services._whale_common.is_implausible_share_flow` — "a quarterly net change
cannot plausibly exceed ~half the shares held". A spin-off's flow is ordinary and passes; a
missed 1:200 reverse moves >=50% of the position and is suppressed. So an unclassified event
here is a **log line**, and the safety belongs to that backstop.

THE RETURN SHAPE IS FMP'S OWN
-----------------------------
:meth:`CorporateActionsService.get_split_rows` emits ``{"date", "numerator", "denominator"}``
— byte-compatible with what ``/splits`` returned. ``holders_service._quarter_split_ratios``
and ``whale_service._split_ratio_in_window`` are pure functions over that shape and are
**not modified**, keeping every test they already have. Only the *fetch* moves.

Because the classifier yields an EXACT rational, two latent traps close for free:
``whale_service``'s ``if r and r != 1.0`` exact-float comparison can no longer see a
``1.0000001``, and ``_split_ratio_in_window``'s multiplication of ratios inside a window
compounds exact small rationals (4:1 then 10:1 gives exactly ``40.0``, not ``39.999999``).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.integrations.fmp import get_fmp_client
from app.integrations.fmp_entitlements import is_blocked_symbol

logger = logging.getLogger(__name__)


# ── Classifier constants ────────────────────────────────────────────────────────────────

#: A day-over-day change in the SPLIT adjustment factor smaller than this is noise, not an
#: event. Every real corporate action in the measured sweep moved it by >= 3%.
_FACTOR_EPS = 0.005

#: The same idea for DIVIDENDS, and it has to be an order of magnitude tighter — which is
#: exactly the bug this constant exists to fix. A dividend moves the factor by roughly the
#: yield of one payment, so `_FACTOR_EPS` (tuned for splits) silently discarded every
#: payment smaller than 0.5%: KO's 0.7% steps survived and AAPL's 0.09% ones did not, so
#: AAPL reported NO ex-dividend dates at all. Found by running it, not by a test.
#:
#: Measured over 2025-01-01..2026-06-30, real dividend steps against the background noise
#: of the same series:
#:
#:     AAPL 9.14e-04 .. 1.31e-03  (noise 4.58e-05)   MSFT 1.63e-03 .. 2.27e-03 (2.36e-05)
#:     KO   6.41e-03 .. 7.68e-03  (noise 1.51e-04)   JNJ  5.28e-03 .. 8.48e-03 (6.96e-05)
#:
#: So real steps start at 9.14e-04 and noise tops out at 1.51e-04; this is their geometric
#: midpoint, ~2.6x above the worst noise and ~2.3x below the smallest real payment.
#:
#: ⚠️ KNOWN FLOOR: a token dividend is undetectable this way. NVDA yields ~0.02% and only
#: ONE of its payments (1.16e-03) clears the noise; the rest sit at ~8e-05, indistinguishable
#: from price rounding. Ex-dividend dates are therefore best-effort for very low yielders —
#: which is why nothing derives an AMOUNT from them (see `get_ex_dividend_dates`).
_DIVIDEND_FACTOR_EPS = 4e-4

#: Relative error allowed when snapping an observed factor to a rational.
#: Chosen from measurement, not taste. Over 36 real splits and 13 spin-offs the two
#: populations sit at [2.80e-04 .. 2.91e-02] and do not overlap; this is their geometric
#: midpoint, ~9x above the worst real split and ~12x below the nearest spin-off.
#: Re-derive it — do not nudge it — if `_MIN_TERM` or `_MAX_TERM` change, because the
#: separation is a property of the candidate set (see `_MIN_TERM`).
_SNAP_REL_TOL = 2.5e-3

#: Split ratios are small rationals. `_MAX_TERM` bounds the extreme (1:100 reverse splits
#: are real, in delisting-defence microcaps).
#:
#: `_MIN_TERM` is the load-bearing one, and 2 is not arbitrary. Every one of the 36 real
#: splits measured is `n:1` or `1:n`; spin-off factors are arbitrary reals that only look
#: rational once you allow busy denominators. Measured separation between the worst real
#: split and the nearest spin-off, by `_MIN_TERM`:
#:
#:     1 -> 104x     2 -> 104x     3 -> 17x     4 -> 8x     10 -> 8x
#:
#: So 2 buys the full separation of an `n:1`-only rule while still admitting a genuine 3:2
#: or 5:2 split. Raising it to 3 costs a factor of six for ratios (4:3, 5:3) that have not
#: occurred in the modern market, and 4 lets GE Vernova's 1.252967 sit 2.4e-03 from 5:4.
_MAX_TERM = 100
_MIN_TERM = 2

#: Floor for any price used as a denominator. Guards sub-penny symbols and bad rows.
_MIN_PRICE = 1e-9

#: Trading days do not fall on fixed calendar dates, so a window that starts exactly on a
#: quarter boundary can begin AFTER a split that took effect on the quarter's first trading
#: day — leaving no prior bar to compare against, and silently missing the split. Ten
#: calendar days guarantees at least one prior session across every US market closure.
_WINDOW_LEAD_DAYS = 10

_EVENTS_TTL_OPEN = 900.0      # window touches today — a new action could still appear
_EVENTS_TTL_CLOSED = 21600.0  # window fully in the past — immutable, see below

_CACHE_MAX_ENTRIES = 2048

_cache: Dict[str, Tuple[float, Any]] = {}
_inflight: Dict[str, asyncio.Future] = {}


def _cache_get(key: str, ttl: float) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        # The hydration scripts sweep the whole universe; without a bound this leaks.
        for stale in list(_cache)[: _CACHE_MAX_ENTRIES // 4]:
            _cache.pop(stale, None)
    _cache[key] = (time.time(), value)


def _finite(value: Any) -> Optional[float]:
    """A float, or None for anything that is not a real finite number.

    Guards on ``math.isfinite`` rather than truthiness because NaN defeats ordinary
    ``<= 0`` comparisons *and* ``except (TypeError, ValueError)`` — the trap recorded in
    `project_whale_tab_deep_check_2026_08`. Mirrors ``price_service._finite``.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


# ── The classifier — pure, no I/O, so it tests without a network or a client ─────────────

def _build_candidates() -> Tuple[Fraction, ...]:
    """Every plausible split ratio as a reduced fraction, built once at import.

    Enumerated rather than found by a continued-fraction expansion, deliberately. A CF
    convergent is the *best* approximation at a given denominator and would happily return
    a fraction outside the admissible set (XPO's 1.717958 converges on 12/7, whose
    ``min(p, q)`` is 7), which then has to be filtered out afterwards. Enumerating the
    admissible set first means the reported error is the error against a ratio a split could
    actually have — for XPO that is 7:4, an error of 1.9e-02 instead of 2.1e-03, which is a
    ten-fold wider reject margin for the same rule.
    """
    out = set()
    for p in range(1, _MAX_TERM + 1):
        for q in range(1, _MAX_TERM + 1):
            if min(p, q) > _MIN_TERM:
                continue
            if math.gcd(p, q) != 1:
                continue
            out.add(Fraction(p, q))
    return tuple(sorted(out))


_CANDIDATES: Tuple[Fraction, ...] = _build_candidates()


def snap_to_rational(x: float) -> Optional[Fraction]:
    """The observed factor as an exact split ratio, or None if it is not one.

    Returns the nearest admissible ``p/q`` when it is within :data:`_SNAP_REL_TOL`
    relatively, else ``None`` — meaning "a corporate action happened here, but it is not a
    share split we can name". See the module docstring for why ``None`` must NOT be treated
    as either "no event" or "suppress".
    """
    if x is None or isinstance(x, bool):
        return None
    try:
        value = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None

    best: Optional[Fraction] = None
    best_err = float("inf")
    for cand in _CANDIDATES:
        err = abs(value - float(cand)) / value
        if err < best_err:
            best, best_err = cand, err
    if best is not None and best_err < _SNAP_REL_TOL:
        return best
    return None


@dataclass(frozen=True)
class AdjustmentEvent:
    """One day-over-day change in the split/dividend adjustment factor.

    ``date`` is the first session on the NEW basis, which is what FMP's ``/splits.date``
    was and what both quarter-bucketing helpers key on, so attribution is unchanged.
    """

    date: str
    observed: float
    numerator: Optional[int] = None
    denominator: Optional[int] = None

    @property
    def is_split(self) -> bool:
        """True when the factor snapped to a nameable split ratio."""
        return self.numerator is not None and self.denominator is not None

    @property
    def ratio(self) -> Optional[float]:
        """The EXACT share multiplier, or None when unclassified.

        Exact matters: callers multiply these together across a window and one compares
        against 1.0 with ``!=``.
        """
        if not self.is_split:
            return None
        return self.numerator / self.denominator  # type: ignore[operator]


def _rows_by_date(rows: Optional[Iterable[Dict[str, Any]]], *fields: str) -> Dict[str, float]:
    """`{date: price}` for the first usable field, dropping unusable rows entirely."""
    out: Dict[str, float] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        raw_date = row.get("date")
        if not isinstance(raw_date, str) or len(raw_date) < 10:
            continue
        value = None
        for field in fields:
            value = _finite(row.get(field))
            if value is not None:
                break
        if value is None or value <= _MIN_PRICE:
            continue
        out[raw_date[:10]] = value
    return out


def derive_adjustment_events(
    full_rows: Optional[Iterable[Dict[str, Any]]],
    raw_rows: Optional[Iterable[Dict[str, Any]]],
    *,
    eps: float = _FACTOR_EPS,
    classify: bool = True,
) -> List[AdjustmentEvent]:
    """Every adjustment-factor discontinuity between an adjusted and a raw price series.

    ``full_rows`` come from ``historical-price-eod/full`` (``close``, adjusted);
    ``raw_rows`` from ``/non-split-adjusted`` or ``/dividend-adjusted`` (``adjClose`` —
    FMP's field naming, not a description).

    The two series are **intersected on date, never zipped by index**: they are aligned in
    practice (measured: zero divergence across large caps, ETFs, recent IPOs, penny stocks,
    international and dash-class symbols) but a single missing bar would otherwise shift
    every subsequent comparison and manufacture events wholesale.
    """
    adjusted = _rows_by_date(full_rows, "close", "adjClose", "price")
    raw = _rows_by_date(raw_rows, "adjClose", "close", "price")

    dates = sorted(set(adjusted) & set(raw))
    if len(dates) < 2:
        # A nonexistent or delisted symbol returns 200 with [] on BOTH legs, and a
        # single-bar window has no pair to compare. Both mean "no split", not "unknown".
        return []

    factors: List[Tuple[str, float]] = []
    for d in dates:
        f = raw[d] / adjusted[d]
        if math.isfinite(f) and f > _MIN_PRICE:
            factors.append((d, f))

    events: List[AdjustmentEvent] = []
    for i in range(1, len(factors)):
        _, f_prev = factors[i - 1]
        d_cur, f_cur = factors[i]
        ratio = f_prev / f_cur
        if not math.isfinite(ratio) or ratio <= 0:
            continue
        if abs(ratio - 1.0) <= eps:
            continue
        # `classify=False` for the dividend series. A dividend step is ~0.1-1% and would
        # snap to 1:1 inside `_SNAP_REL_TOL`, labelling every payment a "1-for-1 split" —
        # harmless downstream but a lie in the data.
        frac = snap_to_rational(ratio) if classify else None
        events.append(
            AdjustmentEvent(
                date=d_cur,
                observed=ratio,
                numerator=frac.numerator if frac else None,
                denominator=frac.denominator if frac else None,
            )
        )
    return events


# ── Windows ─────────────────────────────────────────────────────────────────────────────

def _quarter_bounds(year: int, quarter: int) -> Tuple[date, date]:
    """Calendar bounds of a 13F reporting quarter, inclusive."""
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"quarter must be 1-4, got {quarter!r}")
    start_month = 3 * (quarter - 1) + 1
    start = date(year, start_month, 1)
    end = date(year + (quarter == 4), 1 if quarter == 4 else start_month + 3, 1) - timedelta(days=1)
    return start, end


def window_for_quarters(pairs: Sequence[Tuple[int, int]]) -> Optional[Tuple[str, str]]:
    """One merged ``(from, to)`` covering every ``(year, quarter)`` asked for.

    Two things this exists to get right:

    **The lead.** The window does NOT start on the quarter boundary. Trading days do not
    land on fixed calendar dates — 2024-03-31 was a Sunday — so ``from=<quarter start>``
    can begin *after* a split that took effect on the quarter's first trading day, leaving
    no prior bar to compare and silently missing it. :data:`_WINDOW_LEAD_DAYS` of calendar
    slack guarantees at least one earlier session across every US market closure.

    **One window, not N.** Callers ask about a run of consecutive quarters. Fetching each
    separately multiplies a ~24 KB pair of responses by the number of quarters for data
    that is one contiguous series.
    """
    valid = [(y, q) for y, q in (pairs or []) if isinstance(y, int) and q in (1, 2, 3, 4)]
    if not valid:
        return None
    starts, ends = zip(*(_quarter_bounds(y, q) for y, q in valid))
    return (
        (min(starts) - timedelta(days=_WINDOW_LEAD_DAYS)).isoformat(),
        max(ends).isoformat(),
    )


#: Lookback when a caller has no lower bound (a fund's first filing, so there is no
#: previous quarter to restate). Generous enough to cover any realistic diff, bounded so
#: the request stays small.
_UNBOUNDED_LOOKBACK_DAYS = 400


def window_for_range(start_excl: Optional[str], end_incl: str) -> Tuple[str, str]:
    """Fetch window covering ``(start_excl, end_incl]``, with the lead applied.

    The consumers filter splits by ``start_excl < date <= end_incl``, so the *fetch* has to
    reach back far enough to have a bar BEFORE ``start_excl`` — otherwise a split effective
    on the first trading day of the range has nothing to compare against. See
    :func:`window_for_quarters` for why the lead is calendar days rather than sessions.
    """
    end = str(end_incl)[:10]
    if start_excl:
        try:
            begin = date.fromisoformat(str(start_excl)[:10]) - timedelta(days=_WINDOW_LEAD_DAYS)
            return begin.isoformat(), end
        except (TypeError, ValueError):
            pass
    try:
        begin = date.fromisoformat(end) - timedelta(days=_UNBOUNDED_LOOKBACK_DAYS)
    except (TypeError, ValueError):
        return end, end
    return begin.isoformat(), end


def recent_quarters(count: int = 4, *, today: Optional[date] = None) -> List[Tuple[int, int]]:
    """The last ``count`` COMPLETED calendar quarters, oldest first.

    13F data always describes a finished quarter (filings lag it by up to 45 days), so a
    caller that does not yet know which quarter it is looking at can bound the fetch with
    this instead of reaching back an arbitrary number of days to "today".

    Ending on a completed quarter also makes the window CLOSED, which matters: a closed
    window's split factors are immutable, so it earns the long cache TTL rather than the
    15-minute one a window touching today would get.
    """
    ref = today or date.today()
    y, q = ref.year, (ref.month - 1) // 3 + 1
    out: List[Tuple[int, int]] = []
    for _ in range(max(1, count)):
        q -= 1
        if q == 0:
            q, y = 4, y - 1
        out.append((y, q))
    return list(reversed(out))


def _window_is_closed(to_date: Optional[str]) -> bool:
    """True when the window ends strictly in the past, so no new action can land in it."""
    if not to_date:
        return False
    try:
        return date.fromisoformat(str(to_date)[:10]) < date.today()
    except (TypeError, ValueError):
        return False


# ── Service ─────────────────────────────────────────────────────────────────────────────

class CorporateActionsService:
    """Splits and ex-dividend dates, derived from entitled price series."""

    async def get_adjustment_events(
        self,
        symbol: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        *,
        kind: str = "split",
    ) -> List[AdjustmentEvent]:
        """Every adjustment-factor discontinuity for ``symbol`` inside the window.

        ``kind="split"`` compares against ``/non-split-adjusted`` (share-basis changes);
        ``kind="dividend"`` against ``/dividend-adjusted`` (ex-dividend dates).
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return []
        if is_blocked_symbol(sym):
            # Indices, commodities, crypto and FX are outside the licence at the SYMBOL
            # level. `fmp.py` would refuse anyway; short-circuiting keeps that off the
            # exception path. ⚠️ FMP itself serves `/non-split-adjusted` for these even
            # though `/full` 402s them — our guard is deliberately stricter than theirs.
            logger.debug("corporate_actions: %s is outside the licence — no events", sym)
            return []

        key = f"ca:{kind}:{sym}:{from_date or ''}:{to_date or ''}"
        ttl = _EVENTS_TTL_CLOSED if _window_is_closed(to_date) else _EVENTS_TTL_OPEN
        hit = _cache_get(key, ttl)
        if hit is not None:
            return hit

        if key in _inflight:
            # Shielded so a caller that times out cannot cancel the shared fetch and leave
            # every other awaiter with a CancelledError. A whale profile fans out over many
            # suspect tickers and they collide on the same symbol constantly.
            return await asyncio.shield(_inflight[key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[key] = future
        try:
            events = None
            closed = _window_is_closed(to_date) and bool(from_date)
            # Belt AND braces around the cache tier. `_db_get`/`_db_put` already swallow
            # their own failures, but a cache must never be ABLE to break the request it
            # is meant to accelerate — including when a future edit forgets that.
            if closed:
                try:
                    events = await self._db_get(sym, kind, from_date, to_date)
                except Exception as e:
                    logger.warning(
                        "corporate_actions: cache read raised for %s (%s: %s) — deriving",
                        sym, type(e).__name__, e,
                    )
                    events = None
            if events is None:
                events = await self._derive(sym, from_date, to_date, kind)
                if closed:
                    try:
                        await self._db_put(sym, kind, from_date, to_date, events)
                    except Exception as e:
                        logger.warning(
                            "corporate_actions: cache write raised for %s (%s: %s) "
                            "— non-fatal", sym, type(e).__name__, e,
                        )
            _cache_set(key, events)
            if not future.done():
                future.set_result(events)
            return events
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            raise
        finally:
            _inflight.pop(key, None)

    async def _derive(
        self, sym: str, from_date: Optional[str], to_date: Optional[str], kind: str
    ) -> List[AdjustmentEvent]:
        fmp = get_fmp_client()
        is_dividend = kind == "dividend"
        raw_call = (
            fmp.get_historical_prices_dividend_adjusted(sym, from_date, to_date)
            if is_dividend
            else fmp.get_historical_prices_non_split_adjusted(sym, from_date, to_date)
        )
        full, raw = await asyncio.gather(
            fmp.get_historical_prices(sym, from_date, to_date),
            raw_call,
            return_exceptions=True,
        )

        # BOTH legs or nothing. The derivation is a ratio between the two series, so one
        # leg alone cannot produce an answer — and must not be allowed to look like one.
        # ⚠️ Degrading to `[]` here means "no split", which for the 13F callers means the
        # raw quarter diff is kept. That is why `_whale_common.is_implausible_share_flow`
        # is wired into those paths: it is the backstop that does not depend on this
        # module succeeding. Logged at WARNING so a systematic failure is visible.
        for label, leg in (("full", full), (kind, raw)):
            if isinstance(leg, Exception):
                logger.warning(
                    "corporate_actions: %s leg failed for %s (%s: %s) — no events derived",
                    label, sym, type(leg).__name__, leg,
                )
                return []

        full_rows = full if isinstance(full, list) else (full or {}).get("historical", [])
        raw_rows = raw if isinstance(raw, list) else (raw or {}).get("historical", [])

        # A caller bug must not silently derive a split from a different company's prices.
        for rows, label in ((full_rows, "full"), (raw_rows, kind)):
            for row in rows or []:
                got = (row.get("symbol") or "").upper() if isinstance(row, dict) else ""
                if got and got != sym:
                    logger.error(
                        "corporate_actions: %s leg for %s returned rows for %s — refusing",
                        label, sym, got,
                    )
                    return []
                break

        events = derive_adjustment_events(
            full_rows,
            raw_rows,
            eps=_DIVIDEND_FACTOR_EPS if is_dividend else _FACTOR_EPS,
            classify=not is_dividend,
        )
        for ev in events:
            if not ev.is_split and kind == "split":
                logger.info(
                    "corporate_actions: %s %s factor %.6f is not a nameable split ratio "
                    "(spin-off, or a reverse split outside 1:%d) — left unclassified; the "
                    "share-flow magnitude backstop decides",
                    sym, ev.date, ev.observed, _MAX_TERM,
                )
        return events

    # ── Supabase tier ───────────────────────────────────────────────────────────────
    # ONLY closed windows are stored. A closed window's factors are immutable: FMP
    # restates its adjusted series after every corporate action, so `f[d]` for an old date
    # does change — but the RATIO between two consecutive days inside the window does not,
    # because any later rescaling multiplies both sides equally. That is what makes this
    # safe to keep with no expiry, and it is why nothing here persists a price.
    #
    # Both halves degrade silently-but-loudly: a missing table (migration 159 not yet
    # applied) or any Supabase failure just means the derivation runs. Deploy order does
    # not matter, same posture as 157/158.

    async def _db_get(
        self, sym: str, kind: str, from_date: str, to_date: str
    ) -> Optional[List[AdjustmentEvent]]:
        try:
            from app.database import get_supabase  # noqa: PLC0415

            def _q():
                return (
                    get_supabase()
                    .table("corporate_action_cache")
                    .select("events")
                    .eq("symbol", sym).eq("kind", kind)
                    .eq("from_date", from_date).eq("to_date", to_date)
                    .limit(1)
                    .execute()
                )

            rows = (await asyncio.to_thread(_q)).data or []
        except Exception as e:
            logger.warning(
                "corporate_actions: cache read failed for %s %s (%s: %s) — deriving",
                sym, kind, type(e).__name__, e,
            )
            return None
        if not rows:
            return None
        raw = rows[0].get("events")
        if not isinstance(raw, list):
            return None
        out: List[AdjustmentEvent] = []
        for item in raw:
            if not isinstance(item, dict) or not item.get("date"):
                continue
            observed = _finite(item.get("observed"))
            if observed is None:
                continue
            out.append(
                AdjustmentEvent(
                    date=str(item["date"])[:10],
                    observed=observed,
                    numerator=item.get("numerator"),
                    denominator=item.get("denominator"),
                )
            )
        return out

    async def _db_put(
        self, sym: str, kind: str, from_date: str, to_date: str,
        events: List[AdjustmentEvent],
    ) -> None:
        try:
            from app.database import get_supabase  # noqa: PLC0415

            payload = {
                "symbol": sym, "kind": kind,
                "from_date": from_date, "to_date": to_date,
                # An EMPTY list is a meaningful, valuable result — "no split in this
                # window" is the answer to the large majority of queries — so it is
                # stored, not skipped.
                "events": [
                    {"date": e.date, "observed": e.observed,
                     "numerator": e.numerator, "denominator": e.denominator}
                    for e in events
                ],
            }

            def _w():
                return (
                    get_supabase()
                    .table("corporate_action_cache")
                    .upsert(payload, on_conflict="symbol,kind,from_date,to_date",
                            returning="minimal")
                    .execute()
                )

            await asyncio.to_thread(_w)
        except Exception as e:
            logger.warning(
                "corporate_actions: cache write failed for %s %s (%s: %s) — non-fatal",
                sym, kind, type(e).__name__, e,
            )

    async def get_split_rows(
        self, symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Splits in FMP's own ``/splits`` row shape.

        ``[{"date": "2024-06-10", "numerator": 10, "denominator": 1}, ...]``, newest first
        to match what the retired endpoint returned.

        Emitting FMP's shape rather than a new one is deliberate: it lets
        ``holders_service._quarter_split_ratios`` and
        ``whale_service._split_ratio_in_window`` stay byte-for-byte unchanged, keeping the
        tests they already have. Only the *fetch* moved.

        Unclassified adjustments are **not** included — they are not splits, and a caller
        parsing this shape must never see a row it cannot interpret. Use
        :meth:`get_adjustment_events` for the full picture.
        """
        events = await self.get_adjustment_events(symbol, from_date, to_date, kind="split")
        return [
            {"symbol": (symbol or "").strip().upper(),
             "date": ev.date,
             "numerator": ev.numerator,
             "denominator": ev.denominator}
            for ev in sorted(events, key=lambda e: e.date, reverse=True)
            if ev.is_split
        ]

    async def get_ex_dividend_dates(
        self, symbol: str, from_date: Optional[str] = None, to_date: Optional[str] = None
    ) -> List[str]:
        """Ex-dividend dates, newest first. Verified exact on AAPL (11/11).

        ⚠️ Dates only. The size of each step implies an AMOUNT to only ~0.04-1.2%, which is
        not good enough to print as money — KO's half-cent dividends do not round to the
        declared value. Per-share amounts come from ``ratios`` (period=annual), which is
        entitled and exact.
        """
        events = await self.get_adjustment_events(
            symbol, from_date, to_date, kind="dividend"
        )
        # A split moves the dividend-adjusted series too, so drop anything that looks like
        # one: a dividend step is a fraction of a percent, never a factor of 2.
        return sorted(
            (ev.date for ev in events if 0.5 < ev.observed < 2.0), reverse=True
        )


_service: Optional[CorporateActionsService] = None


def get_corporate_actions_service() -> CorporateActionsService:
    global _service
    if _service is None:
        _service = CorporateActionsService()
    return _service


def corporate_actions_source(owner: Any = None) -> Any:
    """The corporate-actions primitive for ``owner``, honouring an injected stand-in.

    Mirrors ``price_service.price_source``. Services hold their upstream as ``self.fmp``
    and dozens of tests drive them by assigning a fake to it; splits no longer come from
    that client, so they need a seam of their own — set ``svc.corporate_actions``.
    Production never sets it, so ``owner`` falls through to the singleton.
    """
    injected = getattr(owner, "corporate_actions", None)
    return injected if injected is not None else get_corporate_actions_service()
