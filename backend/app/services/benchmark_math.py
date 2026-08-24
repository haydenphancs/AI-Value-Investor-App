"""Annualised-return (CAGR) math shared by every detail screen's Performance card.

WHY THIS MODULE EXISTS
----------------------
Four services build the same "asset vs benchmark" summary — stock, ETF, crypto,
commodity — and each had grown its OWN CAGR helper. They disagreed, and two of them
were wrong in ways nobody could see from the screen:

  * `etf_service` measured the ETF's CAGR from the ETF's first date and the S&P's from
    the S&P's own first date, then sent `benchmark_since_date=None` so the UI printed a
    single date and the reader assumed it covered both columns. It did not.
  * `commodity_service` computed `total_return / years` — an ARITHMETIC mean, not a
    CAGR — and compared it against a hardcoded `10.5`.
  * `stock_overview_service` aligned the S&P to the stock's start date but, when SPY had
    no row that far back, silently fell back to SPY's FIRST AVAILABLE row and kept the
    stock's label. MEASURED on 2026-08-23: FMP caps a daily series at 5,000 rows, so
    SPY's full-history fetch starts 2006-10-05. A card reading "S&P 500 9.1% · Since
    Dec 31, 1981" was showing SPY's 2006→2026 CAGR — 9.0966%, i.e. the label was off by
    twenty-five years. That is the defect a TestFlight tester reported as "hard to read
    or compare the average annual return and sp500 benchmark".

THE RULE THIS MODULE ENFORCES
-----------------------------
    Both sides are measured over the window BOTH series actually cover, and the caller
    is handed the start of that window so it can label the row truthfully.

`overlapping_cagrs` returns `(asset_cagr, benchmark_cagr, since_date)` and the caller
publishes `since_date` verbatim. There is deliberately no way to get one number without
the date that qualifies it.

WHAT AN ANCHOR IS FOR — AND WHY IT CANNOT HELP TODAY
----------------------------------------------------
`asset_anchor` / `benchmark_anchor` let a caller extend a series backwards with a single
price fetched separately from before the capped range (the trick `stock_overview_service`
uses for a stock's IPO close). An anchored series is NOT dense: it is one old point, a
gap of years, then the daily range.

MEASURED, and worth knowing before reaching for one: because the cap is on ROW COUNT and
is applied to every symbol, every still-trading asset's full-history fetch begins at the
SAME date — 2006-10-05 as of 2026-08-23, for SPY, QQQ and AAPL alike. So the shared
window floors there regardless, an anchor on one side has no partner on the other, and
`overlapping_cagrs` correctly falls through to the dense ranges. Verified end to end:
AAPL's all-time row is 27.0% vs 9.1% since Oct 2006 with or without a 1993 SPY anchor.

The parameters stay because the candidate scan below handles them correctly and they are
the right shape the day a deeper series is available. Do not add an FMP call to populate
one without first re-measuring the cap.

NON-FINITE INPUTS
-----------------
Every price goes through `_finite_or_none`. FMP can emit a bare `NaN`/`Infinity` JSON
token; those are truthy, survive `x or 0` and `x <= 0`, and land in the REQUIRED
`avg_annual_return` / `sp_benchmark` floats, where Starlette's `allow_nan=False` 500s the
entire detail response. Non-finite in means `None` out, never a poisoned number.
"""

from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any, Dict, List, Optional, Tuple

from app.services.chart_helper import _finite_or_none

logger = logging.getLogger(__name__)

#: A CAGR over a very short span is arithmetic noise raised to a huge power — a 2x move
#: across three days annualises to ~1e30. Anything under six months is reported as
#: unmeasurable rather than as a number. Every current caller gates on >= 252 daily rows
#: (~1 trading year), so this is a backstop against a sparse or truncated series, not a
#: constraint the normal paths ever feel.
MIN_MEASURABLE_YEARS = 0.5

#: How far the benchmark's first usable observation may sit from the asset's without the
#: two being different windows. A few trading days of slack absorbs holidays and the
#: listing-date-vs-first-print gap; a month of slack would let a 1981-vs-2006 mismatch
#: through, which is the bug this module was written for.
_ALIGNMENT_TOLERANCE_DAYS = 10


def _iso(value: Any) -> Optional[_date]:
    """Parse the leading `YYYY-MM-DD` of an FMP date field."""
    if not value:
        return None
    try:
        return _date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _close_of(row: Dict[str, Any]) -> Optional[float]:
    """FMP daily rows carry `close`; some endpoints only fill `adjClose`."""
    return _finite_or_none(row.get("close") if row.get("close") is not None
                           else row.get("adjClose"))


def cagr_between(
    start_price: Any,
    end_price: Any,
    start_date: Any,
    end_date: Any,
    *,
    min_years: float = MIN_MEASURABLE_YEARS,
) -> Optional[float]:
    """Compound annual growth rate between two dated prices, in percent (1 dp).

    Returns `None` — never `0.0` — when it cannot be computed. `0.0` was the previous
    convention in three services and is indistinguishable from a market that genuinely
    returned nothing, which is how "S&P 500 Benchmark 0.0%" reached the screen with an
    "Outperforming" badge beside it whenever an upstream fetch failed.
    """
    sp = _finite_or_none(start_price)
    ep = _finite_or_none(end_price)
    if sp is None or ep is None or sp <= 0 or ep <= 0:
        return None

    sd, ed = _iso(start_date), _iso(end_date)
    if sd is None or ed is None:
        return None

    years = (ed - sd).days / 365.25
    if years < min_years:
        return None

    return round(((ep / sp) ** (1 / years) - 1) * 100, 1)


def _bounds(rows: List[Dict[str, Any]],
            anchor: Optional[Dict[str, Any]]) -> Tuple[Optional[_date], Optional[_date]]:
    """(earliest, latest) dates a series can speak for, anchor included."""
    dates = [d for d in (_iso(r.get("date")) for r in rows or []) if d is not None]
    if anchor:
        anchored = _iso(anchor.get("date"))
        if anchored is not None and _finite_or_none(anchor.get("price")) is not None:
            dates.append(anchored)
    if not dates:
        return None, None
    return min(dates), max(dates)


def _observation_at(
    rows: List[Dict[str, Any]],
    anchor: Optional[Dict[str, Any]],
    on_or_after: _date,
) -> Optional[Tuple[float, _date]]:
    """The earliest usable (price, date) at or after `on_or_after`.

    The anchor competes with the series on equal terms and wins only when it sits
    closer to the requested date — it is an EXTENSION of the series, not an override.
    """
    best: Optional[Tuple[float, _date]] = None

    if anchor:
        a_date, a_price = _iso(anchor.get("date")), _finite_or_none(anchor.get("price"))
        if a_date is not None and a_price is not None and a_price > 0 and a_date >= on_or_after:
            best = (a_price, a_date)

    for row in rows or []:
        d = _iso(row.get("date"))
        if d is None or d < on_or_after:
            continue
        price = _close_of(row)
        if price is None or price <= 0:
            continue
        if best is None or d < best[1]:
            best = (price, d)
        # Rows are not guaranteed sorted, so scan them all rather than breaking early.

    return best


def _first_dense_date(rows: List[Dict[str, Any]]) -> Optional[_date]:
    """Earliest date in the series proper — the anchor is deliberately excluded."""
    dates = [d for d in (_iso(r.get("date")) for r in rows or []) if d is not None]
    return min(dates) if dates else None


def _observation_before(
    rows: List[Dict[str, Any]],
    on_or_before: _date,
) -> Optional[Tuple[float, _date]]:
    """The latest usable (price, date) at or before `on_or_before`.

    `>=` rather than `>` so that when a series carries MORE THAN ONE row for the same
    date, the one appearing last wins — reproducing `rows[-1]` on an oldest-first list,
    which is what every caller used before this module existed. With `>` the first of a
    tied pair won instead, and a duplicated final date silently priced the window off the
    earlier of the two.
    """
    best: Optional[Tuple[float, _date]] = None
    for row in rows or []:
        d = _iso(row.get("date"))
        if d is None or d > on_or_before:
            continue
        price = _close_of(row)
        if price is None or price <= 0:
            continue
        if best is None or d >= best[1]:
            best = (price, d)
    return best


def overlapping_cagrs(
    asset_rows: List[Dict[str, Any]],
    benchmark_rows: List[Dict[str, Any]],
    *,
    asset_anchor: Optional[Dict[str, Any]] = None,
    benchmark_anchor: Optional[Dict[str, Any]] = None,
    asset_end_price: Optional[float] = None,
    label: str = "",
) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Annualise both series over the window they BOTH cover.

    Returns `(asset_cagr, benchmark_cagr, since_iso_date)`.

    * `since_iso_date` describes BOTH numbers. Publish it; do not publish a per-column
      date, and do not publish one number with the other column's date.
    * `benchmark_cagr` is `None` when the benchmark cannot be aligned to the shared
      start within `_ALIGNMENT_TOLERANCE_DAYS`. Callers surface that as
      `benchmark_available=False` rather than inventing a figure.
    * `asset_cagr` is `None` only when the asset itself is unmeasurable, in which case
      the caller should omit the whole summary — there is nothing to compare.
    * `asset_end_price` lets a caller end the asset's window on a LIVE quote instead of
      its last close (commodity does this). The benchmark still ends on its own last
      close; over a multi-year CAGR the difference is far below the 1 dp published.
    """
    a_start, a_end = _bounds(asset_rows, asset_anchor)
    b_start, b_end = _bounds(benchmark_rows, benchmark_anchor)

    if a_start is None or a_end is None:
        return None, None, None

    # ── Asset side, over its own full history when the benchmark is absent ──
    if b_start is None or b_end is None:
        a_obs = _observation_at(asset_rows, asset_anchor, a_start)
        a_last = _observation_before(asset_rows, a_end)
        if a_obs is None or a_last is None:
            return None, None, None
        end_price = _finite_or_none(asset_end_price)
        asset_cagr = cagr_between(a_obs[0], end_price if end_price is not None else a_last[0],
                                  a_obs[1], a_last[1])
        if asset_cagr is None:
            return None, None, None
        logger.info(
            "benchmark_math: no benchmark series for %s — asset-only window from %s",
            label or "?", a_obs[1].isoformat(),
        )
        return asset_cagr, None, a_obs[1].isoformat()

    window_end = min(a_end, b_end)

    # ── Pick the earliest start BOTH series can actually serve ──────────────────
    #
    # `max(a_start, b_start)` is the obvious choice and it is WRONG whenever an anchor
    # is in play, because an anchored series is not dense: it is one old point, then a
    # gap, then the capped daily range. AAPL is the worked example — anchor 1980-12-12,
    # nothing until 2006-10-05 — so `max()` against a SPY anchored at 1993-01-29 asks
    # for 1993, which the asset cannot answer at all. It resolved to 2006 for the asset
    # and 1993 for the benchmark: thirteen years apart, the exact mislabel this module
    # was written to stop.
    #
    # So walk the candidate starts in ascending order and take the first date at which
    # both sides land within tolerance of each other. A pair of anchors that happen to
    # line up is used; one that does not falls through to the dense ranges.
    candidates = sorted({
        d for d in (a_start, b_start,
                    _first_dense_date(asset_rows), _first_dense_date(benchmark_rows))
        if d is not None and d <= window_end
    })

    chosen: Optional[Tuple[Tuple[float, _date], Tuple[float, _date]]] = None
    best_drift: Optional[int] = None
    for candidate in candidates:
        a_try = _observation_at(asset_rows, asset_anchor, candidate)
        b_try = _observation_at(benchmark_rows, benchmark_anchor, candidate)
        if a_try is None or b_try is None:
            continue
        drift = abs((b_try[1] - a_try[1]).days)
        if best_drift is None or drift < best_drift:
            best_drift = drift
        if drift <= _ALIGNMENT_TOLERANCE_DAYS:
            chosen = (a_try, b_try)
            break

    # Asset side first — it is computed even when no shared window exists, because the
    # caller still needs to know whether there is anything at all to show.
    a_obs = _observation_at(asset_rows, asset_anchor, a_start)
    a_last = _observation_before(asset_rows, window_end)
    if a_obs is None or a_last is None:
        return None, None, None

    if chosen is None:
        end_price = _finite_or_none(asset_end_price)
        asset_cagr = cagr_between(a_obs[0], end_price if end_price is not None else a_last[0],
                                  a_obs[1], a_last[1])
        if asset_cagr is None:
            return None, None, None
        logger.warning(
            "benchmark_math: %s has no window the benchmark can share (closest start "
            "gap %s days) — reporting the benchmark unavailable rather than "
            "mislabelling the window",
            label or "?", best_drift if best_drift is not None else "n/a",
        )
        return asset_cagr, None, a_obs[1].isoformat()

    (a_obs, b_obs) = chosen
    b_last = _observation_before(benchmark_rows, window_end)

    end_price = _finite_or_none(asset_end_price)
    asset_cagr = cagr_between(
        a_obs[0], end_price if end_price is not None else a_last[0], a_obs[1], a_last[1]
    )
    if asset_cagr is None:
        return None, None, None

    since = a_obs[1]
    if b_last is None:
        return asset_cagr, None, since.isoformat()

    benchmark_cagr = cagr_between(b_obs[0], b_last[0], b_obs[1], b_last[1])
    return asset_cagr, benchmark_cagr, since.isoformat()


def format_since(iso_date: Optional[str], *, style: str = "month") -> Optional[str]:
    """Render a window start for display. `style`: "month" -> "Aug 2021", "day" -> "Aug 12, 2021"."""
    d = _iso(iso_date)
    if d is None:
        return None
    return d.strftime("%b %d, %Y") if style == "day" else d.strftime("%b %Y")
