"""Percentage change over a trailing window, from a dated price/level series.

Phase 4 needs a 5D / 1M / 3M / 1Y change for a handful of macro series, and the
endpoint that used to supply them — FMP `stock-price-change` — is outside the licence.
Every replacement source (FMP `historical-price-eod/full`, FRED `get_observations`)
hands back a dated series instead, so the arithmetic has to live somewhere.

It lives HERE, once, because the three ad-hoc precedents each got a different part of
it wrong:

* **Row order is not a constant.** The RAW FMP response is **newest-first**
  (`home_service.py` says so in a comment, and it is verified live: index 0 of a
  `2026-08-25..2026-09-05` fetch is `2026-09-04`). But `chart_helper._fetch_all_daily`
  **sorts oldest-first** before returning. So the same endpoint reaches different
  callers in opposite orders, and `fmp.py`'s inline 1Y calc reads
  `hist_list[0].get("close") or hist_list[-1].get("close")` — the NEWEST close as the
  "1Y-ago price". This module never trusts the caller's order: it sorts by date.
* **A calendar offset is not an index offset.** Markets shut at weekends and FRED
  prints `"."` on holidays (dropped before it reaches us), so "12 observations back"
  is not "a year ago". `fred.get_snapshot` documents exactly this trap — its `obs[6]`
  is a ~6-DAY window on a daily series, not six months — which is why nothing here
  reuses it. We bisect on the date, stepping back to the last observation at or before
  the target, the way `price_volatility` already does for its σ windows.
* **A short series must refuse, not shrink.** Returning a 4-month change under a "1Y"
  label is the mislabelling `_compute_return` guards against in `crypto_service`.
  Not enough history → `None`.

Non-finite values are dropped rather than propagated: a NaN close is truthy, survives
`or 0`, and serializes as an invalid JSON `NaN` token that fails the iOS decode.
"""
from __future__ import annotations

import bisect
import logging
import math
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "normalize_history",
    "series_from_rows",
    "series_from_observations",
    "window_change_pct",
    "latest_value",
]


def normalize_history(raw: Any) -> List[Dict[str, Any]]:
    """FMP `historical-price-eod/full` is a flat list on some plan tiers and a
    ``{"historical": [...]}`` dict on others. Normalize.

    Consolidates three byte-identical private copies (`volatility_cache_service`,
    `agents/ticker_report_data_collector`, and an inline block in `home_service`).
    """
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        rows = raw.get("historical") or []
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    return []


def _finite(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _as_date(raw: Any) -> Optional[date]:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if not isinstance(raw, str) or len(raw) < 10:
        return None
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _clean(pairs: Iterable[Tuple[Any, Any]]) -> List[Tuple[date, float]]:
    """Drop unparseable dates, non-finite and non-positive values, and duplicate dates
    (last one wins); return sorted OLDEST-FIRST.

    Sorting here rather than trusting the caller is the whole point of this module —
    see the order note in the docstring above.
    """
    by_date: Dict[date, float] = {}
    for raw_date, raw_value in pairs:
        d = _as_date(raw_date)
        v = _finite(raw_value)
        if d is None or v is None or v <= 0:
            continue
        by_date[d] = v
    return sorted(by_date.items())


def series_from_rows(rows: Any, *, price_key: str = "close") -> List[Tuple[date, float]]:
    """A dated series from FMP price rows, in EITHER input order."""
    normalized = normalize_history(rows) if not isinstance(rows, list) else [
        r for r in rows if isinstance(r, dict)
    ]
    return _clean(
        (r.get("date"), r.get(price_key) if r.get(price_key) is not None else r.get("adjClose"))
        for r in normalized
    )


def series_from_observations(observations: Any) -> List[Tuple[date, float]]:
    """A dated series from `FREDClient.get_observations` (newest-first) or from plain
    ``{"date","value"}`` dicts."""
    pairs: List[Tuple[Any, Any]] = []
    for o in observations or []:
        if isinstance(o, dict):
            pairs.append((o.get("date"), o.get("value")))
        else:
            pairs.append((getattr(o, "date", None), getattr(o, "value", None)))
    return _clean(pairs)


def latest_value(series: Sequence[Tuple[date, float]]) -> Optional[float]:
    """The most recent level, or None for an empty series."""
    return series[-1][1] if series else None


def window_change_pct(
    series: Sequence[Tuple[date, float]],
    days: int,
    *,
    tolerance_days: int = 10,
) -> Optional[float]:
    """Percent change over the trailing ``days`` CALENDAR days, or None.

    ``None`` — never 0.0 — when the series is empty, has one point, or does not reach
    back far enough. A caller that wants "no signal" must be able to tell it apart from
    a genuine flat market.

    ``tolerance_days`` bounds how much older than the target the anchor may be, so a
    series with a long gap (a stale FRED print, a delisted proxy) refuses rather than
    quietly measuring a much longer window than its label claims. It is generous by
    default because a 3-day weekend plus a holiday is normal, and because FRED's EIA
    energy series legitimately run ~5 business days behind.
    """
    if days <= 0 or len(series) < 2:
        return None
    dates = [d for d, _ in series]
    newest_date, newest = series[-1]
    target = newest_date - timedelta(days=days)

    # Rightmost observation at or before the target: steps back over weekends and
    # holidays instead of assuming an index offset.
    idx = bisect.bisect_right(dates, target) - 1
    if idx < 0:
        return None  # series does not reach back that far — refuse, do not shrink
    anchor_date, anchor = series[idx]
    if (target - anchor_date).days > tolerance_days:
        return None
    if anchor <= 0:
        return None
    pct = (newest / anchor - 1.0) * 100.0
    return round(pct, 4) if math.isfinite(pct) else None
