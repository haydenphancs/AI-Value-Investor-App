"""
Home Dashboard Service — aggregates the redesigned Caydex Home screen
(`HomeDashboardView`) into a single response.

Currently powers the top "Market Pulse" strip: the major US indices plus
Bitcoin and key commodities, each with a live quote and a short daily-close
sparkline. Built to grow top-to-bottom (scanners, signals, themes) behind the
same `HomeDashboardResponse`.

Caching (CLAUDE.md invariant 4, lite):
- The pulse is GLOBAL (not per-user) and fast-moving, so a 5-minute in-memory
  tier is the right freshness ceiling — same tier `index_service` uses for live
  market data. A Supabase tier would only serve stale prices here, so it is
  intentionally omitted (~6 cheap FMP quote calls repopulate a cold cache).
- An `_inflight` dedup future collapses concurrent cold-cache loads into ONE
  FMP fan-out, preventing a thundering herd when many users open Home at once.

Each symbol degrades gracefully: a failed quote/history drops that one tile
rather than failing the whole strip (mirrors the legacy `home_service`).
"""

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client, FMPClient
from app.integrations.finra_short_interest import get_short_interest
from app.services.chart_helper import fetch_chart_data
from app.schemas.home_dashboard import (
    HomeDashboardResponse,
    MarketPulseItemResponse,
    ScannerGroupResponse,
    ScannerGroupsResponse,
    ScannerRowResponse,
    ThemesGroupResponse,
    TrendingThemeResponse,
)

logger = logging.getLogger(__name__)


# ── Market Pulse universe ─────────────────────────────────────────────
# Order is meaningful — it drives the card order in the iOS strip.
_PULSE_SYMBOLS: List[Dict[str, str]] = [
    {"symbol": "^GSPC", "name": "S&P 500", "type": "index"},
    {"symbol": "^IXIC", "name": "Nasdaq", "type": "index"},
    {"symbol": "^DJI", "name": "Dow Jones", "type": "index"},
    {"symbol": "BTCUSD", "name": "Bitcoin", "type": "crypto"},
    {"symbol": "GCUSD", "name": "Gold", "type": "commodity"},
    {"symbol": "CLUSD", "name": "Crude Oil", "type": "commodity"},
]

_SPARKLINE_POINTS = 30          # downsampled intraday closes per mini-chart
_CACHE_TTL_SECONDS = 300        # 5 min — live market-data freshness ceiling
_CACHE_KEY = "dashboard"


# ── Daily Scanners config ─────────────────────────────────────────────
# Thresholds are researched industry standards, not the mock's placeholders:
#   • RVOL ≥ 2.0× = standard "unusual volume" cutoff.
#   • Movers liquidity = price ≥ $5 (SEC penny-stock line) + avg vol ≥ 1M +
#     major exchange.
#   • Short interest ≥ 20% of float = "high" (context); list is ranked desc.
_SCANNER_ROWS = 10                          # top-N rows per leaderboard
_SCANNER_CACHE_TTL_SECONDS = 1200           # 20 min — scanners move slowly; the
                                            # short-interest scan is too costly for 5 min
_SCANNER_CACHE_KEY = "scanners"
_SCANNER_BUILD_TIMEOUT_SECONDS = 8          # never let a cold shorts scan block the dashboard
_MOVERS_MIN_PRICE = 5.0
_MOVERS_MIN_AVG_VOLUME = 1_000_000
_MOVERS_EXCHANGES = {"NASDAQ", "NYSE", "AMEX"}
# Quality bar (market-cap floor) for movers + Heavy Traffic. FMP's
# biggest-gainers/losers are topped by micro-cap pumps (e.g. SDOT +247% at a
# $21M cap); $250M drops those while keeping real small/mid/large caps. Movers
# are ranked from the WHOLE in-play universe by % change (not the raw top-50
# lists), so most-actives backfill the list with real names and it isn't thin.
_SCANNER_MIN_MARKET_CAP = 250_000_000
_RVOL_MIN = 2.0
# A mover whose % change would render as "0.0%" is not a mover. The iOS row
# formats change with ONE decimal ("%+.1f%%"), so any |change| < 0.05 displays
# as "+0.0%"/"-0.0%". Dropping that range keeps a near-flat name off the board
# (and out of the #1 slot on a quiet session, when the dramatic movers are all
# sub-$5/ETFs and get filtered) and removes the signed-zero path where
# round(-0.003, 2) == -0.0 decodes as positive on iOS and paints a loser green.
_MOVERS_MIN_ABS_CHANGE_PCT = 0.05
# Bound the shared profile fan-out. Sized to fit the WHOLE in-play universe
# (≈ filtered gainers + losers + most-actives, ~65–90), so most-actives — the
# source of "real down names" that backfill Top Losers — aren't truncated.
_UNIVERSE_CAP = 90
# Short % of float above this is treated as bad data and dropped — a stale FMP
# float vs a post-reverse-split FINRA shares_short can yield absurd ratios
# (e.g. WOLF computing 435%). Genuine values essentially never exceed 100%.
_SHORT_PCT_SANITY_MAX = 100.0
# The card computes % of float as FINRA shares_short / CURRENT FMP float. A stale
# settlement date against a current float yields a misleading ratio, so a FINRA/
# Nasdaq print whose settlement is older than this is dropped. FINRA publishes
# ~twice monthly, so a latest print older than ~2 cycles means the name stopped
# being reported (delisted/halted) or the feed is stale. Yahoo's precomputed % of
# float carries no settlement date and is internally date-consistent → exempt.
_SHORT_MAX_SETTLEMENT_AGE_DAYS = 60
# Only quote the top-N most-shorted candidates (rather than the whole universe)
# before applying the market-cap floor — covers the 10 floor-passers in realistic
# cases while bounding the per-build quote burst (rate hygiene under the pre-warm).
_SHORT_QUOTE_CANDIDATES = 25
_FLOAT_TTL_SECONDS = 86_400                 # 24h in-mem float cache (float moves slowly)


# ── Emerging Frontiers (server-driven Trending Themes) config ─────────
# Editorial megatrend cards live in the `trending_themes` Supabase table so
# editors add/remove cards or edit tickers/images with NO app release. Per card
# the backend computes ticker_count (len of the configured tickers) and
# change_percent (avg daily % over the RESOLVABLE tickers) from ONE FMP
# batch-quote fan-out over the deduped union of every active theme's tickers.
_THEMES_CACHE_TTL_SECONDS = 600             # 10 min — editorial rows + intraday % move slowly
_THEMES_CACHE_KEY = "themes"
_THEMES_BUILD_TIMEOUT_SECONDS = 8           # never let a cold themes build block the dashboard
_THEMES_TABLE = "trending_themes"
_THEMES_MAX_ROWS = 50                        # sane ceiling on active theme cards

_SHORT_UNIVERSE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "short_interest_universe.json"
)
# Embedded fallback if the JSON isn't deployed/readable — keeps the card alive.
_SHORT_UNIVERSE_FALLBACK = [
    "BYND", "CVNA", "UPST", "WOLF", "BIGC", "FUBO", "AI", "LCID", "RIVN", "CHWY",
    "W", "KSS", "GME", "AMC", "MARA", "RIOT", "PLUG", "FCEL", "HOOD", "SOFI",
]


def _load_short_universe() -> List[str]:
    """Load the curated high-short-interest universe (committed JSON), with an
    embedded fallback so a missing/garbled file never kills the card."""
    try:
        with open(_SHORT_UNIVERSE_PATH, "r") as f:
            data = json.load(f)
        tickers = data.get("tickers") if isinstance(data, dict) else data
        if isinstance(tickers, list):
            cleaned: List[str] = []
            seen: set = set()
            for t in tickers:  # de-dup (order-preserving) so a dup can't double a row
                u = str(t).strip().upper()
                if u and u not in seen:
                    seen.add(u)
                    cleaned.append(u)
            if cleaned:
                return cleaned
    except Exception as exc:
        logger.warning(
            "Short-interest universe load failed (%s) — using embedded fallback", exc
        )
    return list(_SHORT_UNIVERSE_FALLBACK)


# ── Pure helpers (unit-tested without network) ────────────────────────


def _market_status(now: Optional[datetime] = None) -> Tuple[str, bool]:
    """Return ``(display_text, is_open)`` for the US equity session.

    Uses real America/New_York time (DST-aware) so the open/closed copy is
    correct year-round. ``now`` is injectable for testing.
    """
    if now is None:
        try:
            from zoneinfo import ZoneInfo

            now = datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            # Fallback: fixed EST offset if tzdata is somehow unavailable.
            now = datetime.now(tz=timezone(timedelta(hours=-5)))

    weekday = now.weekday()  # 0=Monday … 6=Sunday
    minutes = now.hour * 60 + now.minute

    if weekday >= 5:
        return "Markets Closed", False
    if minutes < 4 * 60:                 # before 4:00 AM
        return "Markets Closed", False
    if minutes < 9 * 60 + 30:            # 4:00 AM – 9:30 AM
        return "Pre-Market", False
    if minutes < 16 * 60:                # 9:30 AM – 4:00 PM
        return "Markets Open", True
    if minutes < 20 * 60:                # 4:00 PM – 8:00 PM
        return "After Hours", False
    return "Markets Closed", False


def _finite_float(v: Any) -> Optional[float]:
    """``float(v)`` but ``None`` for missing/non-numeric AND non-finite (NaN/inf).

    Non-finite values are the dangerous case: they pass naive ``x <= 0`` /
    ``x < threshold`` guards (every NaN comparison is False), survive into the
    response, and serialize as the non-standard JSON tokens ``NaN``/``Infinity``
    — which either 500s the endpoint or crashes the iOS decode of the WHOLE
    dashboard. Filtering them at the source keeps one bad upstream cell from
    poisoning the entire payload."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _downsample(values: List[float], target: int) -> List[float]:
    """Evenly downsample to at most *target* points, always keeping the FIRST
    and LAST (the iOS SparklineView colours green/red off the reference and dots
    values[-1], so the open baseline and end point must survive). Mirrors the
    holdings-card helper in tracking_service."""
    if len(values) <= target:
        return values
    step = (len(values) - 1) / (target - 1)
    idxs = sorted({round(i * step) for i in range(target)} | {0, len(values) - 1})
    return [values[i] for i in idxs]


def _intraday_sparkline(bars: Any, points: int = _SPARKLINE_POINTS) -> List[float]:
    """Pure transform: 1D intraday bars → downsampled closes for the mini-chart.

    Mirrors the holdings-card sparkline (tracking_service): keep only the MOST
    RECENT trading day (so warm-up bars from prior sessions don't fold several
    days into one tiny chart), take closes oldest-first, downsample to ``points``.

    Robust to the shapes FMP/chart_helper return:
    - non-list / fewer than 2 bars → []
    - non-dict rows, missing/None/non-numeric/non-positive closes → skipped
    - fewer than 2 usable closes after filtering → []

    Never fabricates a synthetic series — returns [] so the iOS SparklineView
    simply draws nothing rather than a fake trend.
    """
    if not isinstance(bars, list) or len(bars) < 2:
        return []

    dict_bars = [b for b in bars if isinstance(b, dict)]
    if not dict_bars:
        return []

    # chart_helper returns bars sorted oldest-first, so the last bar is newest.
    last_day = str(dict_bars[-1].get("date", ""))[:10]  # "YYYY-MM-DD"
    if last_day:
        day_bars = [
            b for b in dict_bars if str(b.get("date", "")).startswith(last_day)
        ]
    else:
        day_bars = dict_bars

    closes: List[float] = []
    for b in day_bars:
        cf = _finite_float(b.get("close"))
        if cf is not None and cf > 0:
            closes.append(round(cf, 2))

    if len(closes) < 2:
        return []
    return [round(c, 2) for c in _downsample(closes, points)]


# ── Scanner pure helpers (unit-tested without network) ────────────────


def _parse_pct(v: Any) -> Optional[float]:
    """Parse a percent that may be a float OR a string (``"+14.2%"``, ``"14.2"``,
    or accounting-negative ``"(2.3%)"``). Returns ``None`` for missing/garbage so
    one bad row degrades instead of poisoning a ranking."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None  # bool is an int subclass — don't treat True/False as 1/0%
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else None
    s = str(v).strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "").replace("%", "").replace("+", "").replace(",", "").strip()
    val = _finite_float(s)  # rejects "nan"/"inf"/"Infinity" and non-numeric
    if val is None:
        return None
    return -val if neg else val


def _rvol(volume: Any, avg_volume: Any) -> Optional[float]:
    """Relative volume = today's volume / average volume. ``None`` when either is
    missing, non-finite, or non-positive (an unknown can't be ranked)."""
    v = _finite_float(volume)
    a = _finite_float(avg_volume)
    if v is None or a is None or v <= 0 or a <= 0:
        return None
    r = v / a
    return r if math.isfinite(r) and r > 0 else None


def _short_pct(shares_short: Any, float_shares: Any, precomputed_pct: Any) -> Optional[float]:
    """Short interest as % of float. Mirrors ``stock_overview_service``: PRIMARY =
    ``shares_short`` (FINRA) / ``floatShares`` (FMP) × 100; FALLBACK = the
    integration's already-percent ``short_percent_of_float`` (Yahoo tier).
    Implausible results (> 100%, from a float/shares-short unit or split mismatch)
    are rejected. ``None`` when neither path yields a sane positive number."""
    try:
        ss = float(shares_short)
        fl = float(float_shares)
        if ss > 0 and fl > 0:
            computed = round(ss / fl * 100, 2)
            if 0 < computed <= _SHORT_PCT_SANITY_MAX:
                return computed
    except (TypeError, ValueError):
        pass
    try:
        p = float(precomputed_pct)
        if 0 < p <= _SHORT_PCT_SANITY_MAX:
            return round(p, 2)
    except (TypeError, ValueError):
        pass
    return None


def _short_data_is_fresh(
    si: Dict[str, Any], *, now: Optional[datetime] = None
) -> bool:
    """True if a short-interest payload is fresh enough to compute % of float.

    The card divides a FINRA/Nasdaq ``shares_short`` (as of a settlement date) by
    the CURRENT FMP float, so a stale settlement against a current float produces
    a misleading ratio. When a ``settlement_date`` is present, require it within
    ``_SHORT_MAX_SETTLEMENT_AGE_DAYS``. When absent (Yahoo's precomputed % of
    float is internally date-consistent) or unparseable, don't reject — the
    freshness guard is for the compute-from-float path, not a blanket filter.
    """
    sd = si.get("settlement_date") if isinstance(si, dict) else None
    if not sd:
        return True
    try:
        settled = datetime.strptime(str(sd)[:10], "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    return (now - settled).days <= _SHORT_MAX_SETTLEMENT_AGE_DAYS


def _is_quality_company(profile: Any) -> bool:
    """A liquid, real operating company (not an ETF/fund, not a micro-cap pump).
    Uses the FMP company profile, which carries marketCap, averageVolume, and the
    isEtf/isFund flags — the only reliable bulk source for these (the /quote
    endpoint returns avgVolume=0)."""
    if not isinstance(profile, dict):
        return False
    if profile.get("isEtf") or profile.get("isFund"):
        return False
    market_cap = _finite_float(profile.get("marketCap"))
    avg_volume = _finite_float(profile.get("averageVolume"))
    # A MISSING/non-finite marketCap or averageVolume fails the gate (a NaN must
    # NOT slip through — NaN < threshold is False). This is also the one silent
    # data-loss case worth surfacing: the name already cleared the raw-list /
    # price / exchange gates upstream, so a sparse FMP profile (e.g. a freshly
    # listed name with no cap yet) is dropped here with no other trace. Log it at
    # debug so the loss is diagnosable; a present-but-too-small value is an
    # ordinary micro-cap filter and stays silent.
    if market_cap is None:
        logger.debug(
            "Quality gate dropped %s: marketCap missing/non-finite (%r)",
            profile.get("symbol"), profile.get("marketCap"),
        )
        return False
    if market_cap < _SCANNER_MIN_MARKET_CAP:
        return False
    if avg_volume is None:
        logger.debug(
            "Quality gate dropped %s: averageVolume missing/non-finite (%r)",
            profile.get("symbol"), profile.get("averageVolume"),
        )
        return False
    if avg_volume < _MOVERS_MIN_AVG_VOLUME:
        return False
    return True


def _canonical_symbol(symbol: Any) -> str:
    """Canonical join key for a ticker across FMP endpoints.

    FMP's mover lists and its profile endpoint disagree on the class-share
    delimiter — the biggest-losers / most-actives lists return e.g. ``"BRK-B"``
    while ``/stable/profile`` echoes ``"BRK.B"``. A raw uppercase compare then
    fails to join the two, silently dropping real class-share movers from the
    leaderboard. Folding ``"."`` → ``"-"`` makes both forms match. Returns
    ``""`` for missing/empty input.
    """
    return str(symbol or "").upper().replace(".", "-")


def _theme_change(tickers: List[str], change_map: Dict[str, float]) -> Optional[float]:
    """Average daily % change over an Emerging Frontiers theme's RESOLVABLE tickers.

    ``change_map`` is keyed by ``_canonical_symbol`` and holds ONLY finite
    percents (quotes FMP actually returned). A ticker with no quote is skipped —
    the card still reports the full configured ``ticker_count``. Returns ``None``
    when NO ticker resolves, so the iOS card hides the % badge rather than showing
    a fabricated 0%. The mean is routed through ``_finite_float`` (defensive
    against a degenerate all-huge set), rounded to 2 dp, with ``+ 0.0`` collapsing
    any signed ``-0.0`` (which decodes as positive/green on iOS)."""
    resolved: List[float] = []
    for t in tickers:
        v = change_map.get(_canonical_symbol(t))
        if v is not None:
            resolved.append(v)
    if not resolved:
        return None
    avg = _finite_float(sum(resolved) / len(resolved))
    if avg is None:
        return None
    return round(avg, 2) + 0.0


def _movers_from_universe(
    profile_map: Dict[str, Dict[str, Any]],
    change_map: Dict[str, float],
    *,
    positive: bool,
    rows: int = _SCANNER_ROWS,
) -> List[ScannerRowResponse]:
    """Rank the in-play quality universe by TODAY'S % change to build Top Movers.

    ``positive=True`` → top gainers (change > 0, ranked desc); ``positive=False``
    → top losers (change < 0, ranked asc). Ranking the whole universe (which
    includes most-actives) — rather than filtering FMP's raw top-50 % lists —
    means both directions reliably fill with ~``rows`` REAL companies: the
    dramatic quality movers lead, and notable moves among the most-traded names
    backfill. (FMP's biggest-losers top-50 is mostly sub-$5 penny names, so a
    raw-list filter yields only a handful of real companies.)"""
    scored: List[Tuple[float, float, str, float, Dict[str, Any]]] = []
    for symbol, p in profile_map.items():
        if not _is_quality_company(p):
            continue
        # Canonical lookup so a class-share whose profile symbol uses a different
        # delimiter than the raw mover list (e.g. profile "BRK.B" vs list
        # "BRK-B") still joins its change and isn't silently dropped.
        change = change_map.get(_canonical_symbol(symbol))
        if change is None or not math.isfinite(change):
            continue
        # Drop anything that would render as 0.0% (see _MOVERS_MIN_ABS_CHANGE_PCT)
        # — this also excludes the signed-zero range that would paint a "loser"
        # green on iOS (round(-0.003, 2) == -0.0, and -0.0 >= 0 is True in Swift).
        if abs(change) < _MOVERS_MIN_ABS_CHANGE_PCT:
            continue
        if (change > 0) != positive:
            continue
        price = _finite_float(p.get("price"))
        if price is None or price <= 0:
            continue
        market_cap = _finite_float(p.get("marketCap"))
        scored.append((change, market_cap or 0.0, symbol, price, p))
    # Rank by % change (gainers desc, losers asc), breaking ties by larger market
    # cap then symbol so the order is deterministic and defensible rather than
    # dependent on universe insertion order (which otherwise decides the #1
    # sparkline and the row-N cutoff). Both branches sort ascending on a
    # sign-adjusted key.
    if positive:
        scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    else:
        scored.sort(key=lambda t: (t[0], -t[1], t[2]))
    out: List[ScannerRowResponse] = []
    for i, (change, _market_cap, symbol, price, p) in enumerate(scored[:rows]):
        out.append(ScannerRowResponse(
            rank=i + 1,
            symbol=symbol,
            name=p.get("companyName") or symbol,
            price=round(price, 2),
            # `+ 0.0` collapses a possible signed -0.0 to 0.0 (defensive; the abs
            # floor above already excludes the near-zero range).
            change_percent=round(change, 2) + 0.0,
            market_cap=_finite_float(p.get("marketCap")),
        ))
    return out


def _volume_rows(
    profile_map: Dict[str, Dict[str, Any]],
    rows: int = _SCANNER_ROWS,
    rvol_min: float = _RVOL_MIN,
) -> List[ScannerRowResponse]:
    """Rank the in-play universe by relative volume (RVOL = volume / averageVolume,
    both from the profile), keeping only genuinely unusual REAL companies
    (RVOL ≥ 2.0×, not an ETF/fund, marketCap ≥ $300M). Mega-caps at ~1× and
    micro-cap pumps fall out."""
    scored: List[Tuple[float, str, float, Dict[str, Any]]] = []
    for symbol, p in profile_map.items():
        if not _is_quality_company(p):
            continue
        rvol = _rvol(p.get("volume"), p.get("averageVolume"))
        if rvol is None or rvol < rvol_min:
            continue
        price = _finite_float(p.get("price"))
        if price is None or price <= 0:
            continue
        scored.append((rvol, symbol, price, p))
    scored.sort(key=lambda t: t[0], reverse=True)
    out: List[ScannerRowResponse] = []
    for i, (rvol, symbol, price, p) in enumerate(scored[:rows]):
        change = _parse_pct(p.get("changePercentage"))
        if change is None:
            change = _parse_pct(p.get("changesPercentage"))
        out.append(ScannerRowResponse(
            rank=i + 1,
            symbol=symbol,
            name=p.get("companyName") or symbol,
            price=round(price, 2),
            change_percent=round(change or 0.0, 2),
            market_cap=_finite_float(p.get("marketCap")),
            volume_multiple=round(rvol, 1),
        ))
    return out


def _short_rows(
    items: List[Dict[str, Any]],
    rows: int = _SCANNER_ROWS,
) -> List[ScannerRowResponse]:
    """Rank already-enriched short-interest items by % of float (desc), top N.
    Each item: ``{symbol, name, price, change_percent, short_percent_of_float}``."""
    valid = [
        it for it in items
        if isinstance(it, dict) and it.get("short_percent_of_float") is not None
    ]
    valid.sort(key=lambda it: it["short_percent_of_float"], reverse=True)
    out: List[ScannerRowResponse] = []
    for i, it in enumerate(valid[:rows]):
        out.append(ScannerRowResponse(
            rank=i + 1,
            symbol=(it.get("symbol") or "").upper(),
            name=it.get("name") or it.get("symbol") or "",
            price=round(_finite_float(it.get("price")) or 0.0, 2),
            change_percent=round(_finite_float(it.get("change_percent")) or 0.0, 2),
            market_cap=_finite_float(it.get("market_cap")),
            short_percent_of_float=round(float(it["short_percent_of_float"]), 2),
        ))
    return out


# ── Service ───────────────────────────────────────────────────────────


class HomeDashboardService:
    """Builds the aggregated Caydex Home dashboard from FMP market data."""

    # Class-level so the cache/dedup are shared across requests.
    # NOTE: the pulse cache holds ONLY the Market Pulse list, NOT the whole
    # dashboard — see get_dashboard() for why (scanner freshness).
    _cache: Dict[str, Tuple[float, List[MarketPulseItemResponse]]] = {}
    _inflight: Dict[str, asyncio.Future] = {}
    # Scanners get their OWN, longer-lived cache (movers/volume/shorts change
    # slowly and the shorts scan is expensive) + their own dedup future.
    _scanner_cache: Dict[str, Tuple[float, ScannerGroupsResponse]] = {}
    _scanner_inflight: Dict[str, asyncio.Future] = {}
    _float_cache: Dict[str, Tuple[float, Optional[float]]] = {}
    # Emerging Frontiers themes: own 10-min cache + dedup future (editorial rows
    # from Supabase + one batch-quote fan-out; cheap but cached to avoid
    # re-reading + re-quoting on every dashboard request).
    _themes_cache: Dict[str, Tuple[float, ThemesGroupResponse]] = {}
    _themes_inflight: Dict[str, asyncio.Future] = {}

    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()

    # ── Public API ────────────────────────────────────────────────────

    async def get_dashboard(self) -> HomeDashboardResponse:
        """Aggregate the dashboard.

        Market status is computed FRESH each call (cheap datetime math, never
        stale on an open↔closed transition). The Market Pulse list is cache-aside
        (5 min) + in-flight dedup. The scanners come from their OWN 20-min cache
        behind a timeout guard.

        Caching pulse and scanners under SEPARATE keys (rather than baking the
        whole response into one 5-min entry) is deliberate: it means a slow/cold
        scanner build that misses the 8s guard is never pinned as "empty" inside
        a 5-minute dashboard cache — the scanners appear on the very next request
        as soon as their own background build warms _scanner_cache.
        """
        # Signals (App-Exclusive) ride in the SAME response as a 3rd branch, each
        # behind its own cache + guard. The import is function-local to avoid a
        # module cycle — signals_service imports `_canonical_symbol`/`_finite_float`
        # from THIS module (same pattern the pre-warmer uses in main.py).
        from app.services.signals_service import get_signals_service

        pulse, scanners, signals, themes = await asyncio.gather(
            self._get_pulse_cached(),
            self._get_scanners_guarded(),
            get_signals_service().get_signals_guarded(),
            self._get_themes_guarded(),
        )
        status_text, is_open = _market_status()
        return HomeDashboardResponse(
            market_status_text=status_text,
            market_is_open=is_open,
            pulse=pulse,
            scanners=scanners,
            signals=signals,
            themes=themes,
        )

    # ── Market Pulse (cache-aside) ────────────────────────────────────

    async def _get_pulse_cached(self) -> List[MarketPulseItemResponse]:
        """The Market Pulse list, cache-aside (5 min) + in-flight dedup."""
        cached = self._cache.get(_CACHE_KEY)
        if cached is not None and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            logger.debug("Market pulse served from in-memory cache")
            return cached[1]

        inflight = self._inflight.get(_CACHE_KEY)
        if inflight is not None:
            logger.debug("Market pulse joining in-flight fetch")
            return await inflight

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[_CACHE_KEY] = fut
        try:
            pulse = await self._build_pulse()
            self._cache[_CACHE_KEY] = (time.time(), pulse)  # cache only on success
            if not fut.done():
                fut.set_result(pulse)
            return pulse
        except BaseException as exc:  # propagate to all awaiters, then re-raise
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(_CACHE_KEY, None)

    async def _build_pulse(self) -> List[MarketPulseItemResponse]:
        results = await asyncio.gather(
            *[self._fetch_pulse_item(cfg) for cfg in _PULSE_SYMBOLS],
            return_exceptions=True,
        )
        pulse: List[MarketPulseItemResponse] = []
        for cfg, res in zip(_PULSE_SYMBOLS, results):
            if isinstance(res, BaseException):
                logger.warning(
                    "Pulse item %s failed: %s: %s",
                    cfg["symbol"], type(res).__name__, res,
                )
                continue
            if res is not None:
                pulse.append(res)

        if not pulse:
            logger.warning("Home dashboard: all %d pulse symbols failed", len(_PULSE_SYMBOLS))
        return pulse

    # ── Daily Scanners ────────────────────────────────────────────────

    async def get_scanners(self) -> ScannerGroupsResponse:
        """Build the three scanner cards, cache-aside (20-min) + in-flight dedup.

        Never re-raises: a build failure returns empty groups (and is NOT cached,
        so the next request retries). This keeps awaiters from being poisoned and
        keeps the shielded background build (see ``_get_scanners_guarded``) from
        leaving an unretrieved exception.
        """
        cached = self._scanner_cache.get(_SCANNER_CACHE_KEY)
        if cached is not None and (time.time() - cached[0]) < _SCANNER_CACHE_TTL_SECONDS:
            logger.debug("Scanners served from in-memory cache")
            return cached[1]

        inflight = self._scanner_inflight.get(_SCANNER_CACHE_KEY)
        if inflight is not None:
            logger.debug("Scanners joining in-flight build")
            return await inflight

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._scanner_inflight[_SCANNER_CACHE_KEY] = fut
        try:
            try:
                result = await self._build_scanner_groups()
                self._scanner_cache[_SCANNER_CACHE_KEY] = (time.time(), result)
            except Exception as exc:  # noqa: BLE001 — scanners must never fail the dashboard
                logger.warning("Scanner build failed: %s: %s", type(exc).__name__, exc)
                result = ScannerGroupsResponse()  # empty; not cached → retries
            if not fut.done():
                fut.set_result(result)
            return result
        except BaseException as exc:
            # CancelledError (a BaseException, NOT caught above) on shutdown/cancel
            # must still settle the future, or a joined request hangs forever on a
            # popped-but-unresolved future. Mirrors get_dashboard / _get_pulse_cached.
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._scanner_inflight.pop(_SCANNER_CACHE_KEY, None)

    async def _get_scanners_guarded(self) -> ScannerGroupsResponse:
        """Await scanners up to a hard timeout. ``asyncio.shield`` ensures a
        timeout never CANCELS the shared build (it keeps running in the
        background and caches for the next request) — we just stop waiting and
        ship the dashboard without scanners this round."""
        try:
            return await asyncio.wait_for(
                asyncio.shield(self.get_scanners()),
                _SCANNER_BUILD_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # TimeoutError or anything unexpected
            # The shielded build keeps running and will refresh _scanner_cache.
            # Meanwhile serve the LAST cached scanners (any age) — slightly stale
            # cards beat a blank section — falling back to empty only if nothing
            # has ever been built.
            cached = self._scanner_cache.get(_SCANNER_CACHE_KEY)
            if cached is not None:
                logger.info(
                    "Scanners build slow (%s); serving last cached (age=%.0fs)",
                    type(exc).__name__, time.time() - cached[0],
                )
                return cached[1]
            logger.warning(
                "Scanners not ready this build (no cache yet): %s: %s",
                type(exc).__name__, exc,
            )
            return ScannerGroupsResponse()

    async def _build_scanner_groups(self) -> ScannerGroupsResponse:
        """Build all three cards concurrently; one card's failure → that card is
        ``None`` (iOS omits it), the others still render."""
        mv_result, shorts = await asyncio.gather(
            self._build_movers_and_volume(),
            self._build_shorts(),
            return_exceptions=True,
        )

        movers: Optional[ScannerGroupResponse] = None
        volume: Optional[ScannerGroupResponse] = None
        if isinstance(mv_result, BaseException):
            logger.warning(
                "Movers/volume scanner failed: %s: %s",
                type(mv_result).__name__, mv_result,
            )
        else:
            movers, volume = mv_result

        if isinstance(shorts, BaseException):
            logger.warning(
                "Shorts scanner failed: %s: %s", type(shorts).__name__, shorts
            )
            shorts = None

        return ScannerGroupsResponse(movers=movers, volume=volume, shorts=shorts)

    async def _build_movers_and_volume(
        self,
    ) -> Tuple[Optional[ScannerGroupResponse], Optional[ScannerGroupResponse]]:
        """Top Movers (gainers/losers) + Heavy Traffic (RVOL) share ONE batch of
        quotes over the in-play universe."""
        gainers_raw, losers_raw, actives_raw = await asyncio.gather(
            self.fmp.get_biggest_gainers(),
            self.fmp.get_biggest_losers(),
            self.fmp.get_most_actives(),
        )

        # Universe = price/exchange-eligible candidates from all three lists.
        # Build each list's eligibles, then ROUND-ROBIN interleave so the cap
        # can't starve one list — in particular most-actives, the RVOL backbone,
        # which would otherwise be truncated on a day with many gainers. One
        # profile fan-out then supplies marketCap / averageVolume / isEtf / volume.
        def _eligible(raw: Any) -> List[str]:
            out: List[str] = []
            for r in raw or []:
                if not isinstance(r, dict):
                    continue
                price = _finite_float(r.get("price"))
                if price is None or price < _MOVERS_MIN_PRICE:
                    continue
                if (r.get("exchange") or "").upper() not in _MOVERS_EXCHANGES:
                    continue
                s = (r.get("symbol") or "").upper()
                if s:
                    out.append(s)
            return out

        eligible_lists = [
            _eligible(gainers_raw), _eligible(losers_raw), _eligible(actives_raw)
        ]
        universe: List[str] = []
        seen: set = set()
        depth = max((len(lst) for lst in eligible_lists), default=0)
        for i in range(depth):
            for lst in eligible_lists:
                if i < len(lst) and len(universe) < _UNIVERSE_CAP:
                    s = lst[i]
                    if s not in seen:
                        seen.add(s)
                        universe.append(s)
            if len(universe) >= _UNIVERSE_CAP:
                break

        # get_company_profiles_batch hard-caps at 50 symbols/call, so chunk the
        # universe (otherwise the tail — where most-actives land after the
        # round-robin — is silently dropped, starving Top Losers of real names).
        profile_chunks = await asyncio.gather(
            *[
                self.fmp.get_company_profiles_batch(universe[i:i + 50])
                for i in range(0, len(universe), 50)
            ]
        )
        profile_map = {
            (p.get("symbol") or "").upper(): p
            for chunk in profile_chunks
            for p in chunk
            if isinstance(p, dict) and p.get("symbol")
        }

        # Today's % change per symbol, from the three raw lists (each row carries
        # `changesPercentage`). Used to rank the quality universe into movers.
        change_map: Dict[str, float] = {}
        for raw in (gainers_raw, losers_raw, actives_raw):
            for r in raw or []:
                if not isinstance(r, dict):
                    continue
                # Canonical key so the join survives the class-share delimiter
                # mismatch between these lists and the profile endpoint.
                s = _canonical_symbol(r.get("symbol"))
                if not s or s in change_map:
                    continue
                chg = _parse_pct(r.get("changesPercentage"))
                if chg is None:
                    chg = _parse_pct(r.get("changePercentage"))
                if chg is not None:
                    change_map[s] = chg

        gainers = _movers_from_universe(profile_map, change_map, positive=True)
        losers = _movers_from_universe(profile_map, change_map, positive=False)
        volume_entries = _volume_rows(profile_map)

        await self._attach_rank1_sparks([gainers, losers, volume_entries])

        movers_group = (
            ScannerGroupResponse(kind="movers", gainers=gainers, losers=losers)
            if (gainers or losers) else None
        )
        volume_group = (
            ScannerGroupResponse(kind="volume", entries=volume_entries)
            if volume_entries else None
        )
        return movers_group, volume_group

    async def _build_shorts(self) -> Optional[ScannerGroupResponse]:
        """Skeptical Money — scan the curated universe for short interest, rank by
        % of float, top N. Bounded concurrency; per-ticker short interest is
        3-day cached, float is 24h cached, so warm runs are cheap."""
        universe = _load_short_universe()
        sem = asyncio.Semaphore(10)

        async def _one(ticker: str) -> Optional[Dict[str, Any]]:
            async with sem:
                try:
                    si = await get_short_interest(ticker)
                except Exception as exc:
                    logger.warning(
                        "Short interest for %s failed: %s: %s",
                        ticker, type(exc).__name__, exc,
                    )
                    return None
            if not si:
                return None
            if not _short_data_is_fresh(si):
                logger.debug(
                    "Skeptical Money: %s short data stale (settlement %s) — skipped",
                    ticker, si.get("settlement_date"),
                )
                return None
            shares_short = si.get("shares_short")
            precomputed = si.get("short_percent_of_float")
            # Fetch float to COMPUTE % when there's no usable precomputed value —
            # None OR <= 0 (a Yahoo data glitch is falsy-but-present, and must not
            # suppress the FINRA/float compute path) — but we do have shares_short.
            precomputed_usable = isinstance(precomputed, (int, float)) and precomputed > 0
            float_shares = None
            if shares_short and not precomputed_usable:
                float_shares = await self._cached_float(ticker)
            pct = _short_pct(shares_short, float_shares, precomputed)
            if pct is None:
                return None
            return {
                "symbol": ticker.upper(),
                "short_percent_of_float": pct,
                # Carried through so the card can show "As of {settlement}" — the
                # FINRA print is bi-monthly, not a live daily figure. None for the
                # Yahoo precomputed path (no settlement date).
                "settlement_date": si.get("settlement_date"),
            }

        results = await asyncio.gather(*[_one(t) for t in universe])
        items = [r for r in results if r is not None]
        if not items:
            return None

        # Rank by % of float, then quote the TOP candidates in one fan-out so the
        # $250M market-cap floor — the same quality bar the movers/volume cards
        # apply — can filter BEFORE the top-N cut. The most-shorted names skew
        # small, so filtering only the top 10 would leave gaps; we over-fetch a
        # bounded set (_SHORT_QUOTE_CANDIDATES) to keep the per-build quote burst
        # in check while reliably yielding 10 floor-passers.
        items.sort(key=lambda it: it["short_percent_of_float"], reverse=True)
        candidates = items[:_SHORT_QUOTE_CANDIDATES]
        candidate_symbols = [it["symbol"] for it in candidates]

        quotes = await self.fmp.get_batch_quotes(candidate_symbols)
        qmap = {
            (q.get("symbol") or "").upper(): q
            for q in quotes
            if isinstance(q, dict) and q.get("symbol")
        }

        enriched: List[Dict[str, Any]] = []
        # candidates is sorted desc by short% → the first N clearing the floor are
        # the top N, so we can stop once we have a full board.
        for it in candidates:
            q = qmap.get(it["symbol"], {})
            market_cap = _finite_float(q.get("marketCap"))
            # $250M quality floor (parity with movers/volume). A missing/non-finite
            # market cap can't prove it clears the floor → drop.
            if market_cap is None or market_cap < _SCANNER_MIN_MARKET_CAP:
                continue
            price_f = _finite_float(q.get("price")) or 0.0
            change = _parse_pct(q.get("changesPercentage"))
            if change is None:
                change = _parse_pct(q.get("changePercentage"))
            enriched.append({
                "symbol": it["symbol"],
                "name": q.get("name") or it["symbol"],
                "price": price_f,
                "change_percent": change or 0.0,
                "market_cap": market_cap,
                "short_percent_of_float": it["short_percent_of_float"],
                "settlement_date": it.get("settlement_date"),
            })
            if len(enriched) >= _SCANNER_ROWS:
                break

        rows = _short_rows(enriched)
        if not rows:
            return None
        # Card-level "as of" = the latest settlement among the shown rows (ISO
        # strings sort chronologically); None when none carry a date (all-Yahoo).
        as_of = max(
            (e["settlement_date"] for e in enriched if e.get("settlement_date")),
            default=None,
        )
        await self._attach_rank1_sparks([rows])
        return ScannerGroupResponse(kind="shorts", entries=rows, as_of_date=as_of)

    async def _attach_rank1_sparks(
        self, lists: List[List[ScannerRowResponse]]
    ) -> None:
        """Fetch a 1D intraday sparkline for the rank-1 (head) row of each
        non-empty list, in parallel, and attach it. Only rank-1 carries a spark
        (matching the iOS model); failures leave it ``[]``."""
        heads = [rows[0] for rows in lists if rows]
        if not heads:
            return
        sparks = await asyncio.gather(
            *[self._fetch_sparkline(h.symbol) for h in heads]
        )
        for head, spark in zip(heads, sparks):
            head.spark = spark

    # ── Emerging Frontiers themes (server-driven, cache-aside + guard) ─

    async def get_themes(self) -> ThemesGroupResponse:
        """Build the Emerging Frontiers cards, cache-aside (10-min) + in-flight dedup.

        Never re-raises: a build failure returns an empty group (NOT cached, so
        the next request retries). Mirrors ``get_scanners`` — keeps awaiters
        unpoisoned and the shielded background build (see ``_get_themes_guarded``)
        from leaving an unretrieved exception."""
        cached = self._themes_cache.get(_THEMES_CACHE_KEY)
        if cached is not None and (time.time() - cached[0]) < _THEMES_CACHE_TTL_SECONDS:
            logger.debug("Themes served from in-memory cache")
            return cached[1]

        inflight = self._themes_inflight.get(_THEMES_CACHE_KEY)
        if inflight is not None:
            logger.debug("Themes joining in-flight build")
            return await inflight

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._themes_inflight[_THEMES_CACHE_KEY] = fut
        try:
            try:
                result = await self._build_themes()
                # Cache ONLY a non-empty build — never pin an empty result (a
                # transient Supabase/FMP blip) for 10 min and blank the section.
                if result.themes:
                    self._themes_cache[_THEMES_CACHE_KEY] = (time.time(), result)
            except Exception as exc:  # noqa: BLE001 — themes must never fail the dashboard
                logger.warning("Themes build failed: %s: %s", type(exc).__name__, exc)
                result = ThemesGroupResponse()  # empty; not cached → retries
            if not fut.done():
                fut.set_result(result)
            return result
        except BaseException as exc:
            # CancelledError (shutdown) must still settle the future or a joined
            # request hangs on a popped-but-unresolved future. Mirrors get_scanners.
            if not fut.done():
                fut.set_exception(exc)
            raise
        finally:
            self._themes_inflight.pop(_THEMES_CACHE_KEY, None)

    async def _get_themes_guarded(self) -> ThemesGroupResponse:
        """Await themes up to a hard timeout. ``asyncio.shield`` ensures a timeout
        never CANCELS the shared build (it keeps running + caches for the next
        request) — we just stop waiting and ship the dashboard without themes this
        round, serving the last cached list (any age) if we have one."""
        try:
            return await asyncio.wait_for(
                asyncio.shield(self.get_themes()),
                _THEMES_BUILD_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # TimeoutError or anything unexpected
            cached = self._themes_cache.get(_THEMES_CACHE_KEY)
            if cached is not None:
                logger.info(
                    "Themes build slow (%s); serving last cached (age=%.0fs)",
                    type(exc).__name__, time.time() - cached[0],
                )
                return cached[1]
            logger.warning(
                "Themes not ready this build (no cache yet): %s: %s",
                type(exc).__name__, exc,
            )
            return ThemesGroupResponse()

    def _read_theme_rows(self) -> List[Dict[str, Any]]:
        """Read active theme rows from Supabase (SYNC — call via ``asyncio.to_thread``).

        Self-guarded: a Supabase failure returns ``[]`` so the section degrades
        (hides) rather than erroring the dashboard. Function-local import mirrors
        the signals service (avoids a module import cycle at load time)."""
        try:
            from app.database import get_supabase

            sb = get_supabase()
            res = (
                sb.table(_THEMES_TABLE)
                .select("slug, title, image_url, accent_hex, tickers, sort_order")
                .eq("is_active", True)
                .order("sort_order")
                .limit(_THEMES_MAX_ROWS)
                .execute()
            )
            return res.data or []
        except Exception as exc:  # noqa: BLE001 — degrade the section, never the dashboard
            logger.warning("Themes table read failed: %s: %s", type(exc).__name__, exc)
            return []

    async def _build_themes(self) -> ThemesGroupResponse:
        """Assemble the Emerging Frontiers cards.

        1. Read active rows from ``trending_themes``.
        2. Build the DEDUPED union of every theme's tickers and fetch quotes ONCE
           (``get_batch_quotes`` fans out per-ticker; querying per theme would
           re-fetch shared names N times).
        3. Per theme: ``ticker_count`` = len(configured tickers); ``change_percent``
           = avg daily % over the resolvable tickers (``_theme_change``)."""
        rows = await asyncio.to_thread(self._read_theme_rows)
        if not rows:
            return ThemesGroupResponse()

        # Normalize each row's tickers once; build the deduped union for one fetch.
        row_tickers: List[List[str]] = []
        union = set()
        for row in rows:
            raw = row.get("tickers") or []
            tickers = [str(t).strip().upper() for t in raw if str(t or "").strip()]
            row_tickers.append(tickers)
            union.update(_canonical_symbol(t) for t in tickers)

        change_map: Dict[str, float] = {}
        if union:
            # Fetch by the canonical (dash) form — FMP's /quote resolves BRK-B.
            quotes = await self.fmp.get_batch_quotes(sorted(union))
            for q in quotes:
                if not isinstance(q, dict):
                    continue
                key = _canonical_symbol(q.get("symbol"))
                if not key:
                    continue
                pct = _parse_pct(q.get("changesPercentage"))
                if pct is None:
                    pct = _parse_pct(q.get("changePercentage"))
                if pct is not None:  # only finite values (NaN/inf already dropped)
                    change_map[key] = pct

        themes: List[TrendingThemeResponse] = []
        for row, tickers in zip(rows, row_tickers):
            slug = str(row.get("slug") or "").strip()
            title = str(row.get("title") or "").strip()
            if not slug or not title:
                logger.warning(
                    "Skipping theme row with missing slug/title: slug=%r", row.get("slug")
                )
                continue
            accent_hex = (str(row.get("accent_hex") or "").strip()) or "22D3EE"
            themes.append(
                TrendingThemeResponse(
                    slug=slug,
                    title=title,
                    image_url=(row.get("image_url") or None),
                    accent_hex=accent_hex,
                    ticker_count=len(tickers),
                    change_percent=_theme_change(tickers, change_map),
                )
            )
        return ThemesGroupResponse(themes=themes)

    async def _cached_float(self, ticker: str) -> Optional[float]:
        """Float-shares count for a ticker, 24h in-memory cached (float moves
        slowly; only fetched when a short% must be computed)."""
        key = ticker.upper()
        cached = self._float_cache.get(key)
        if cached is not None and (time.time() - cached[0]) < _FLOAT_TTL_SECONDS:
            return cached[1]
        fl_f: Optional[float] = None
        try:
            data = await self.fmp.get_shares_float(ticker)
            fl = data.get("floatShares") if isinstance(data, dict) else None
            fl_f = _finite_float(fl)
        except Exception as exc:
            logger.warning("Shares float for %s failed: %s", ticker, exc)
        # Cache ONLY a real value — never pin a None (transient FMP failure) for
        # 24h, or one blip silently drops the ticker from shorts for a day.
        if fl_f is not None and fl_f > 0:
            self._float_cache[key] = (time.time(), fl_f)
        return fl_f

    async def _fetch_pulse_item(
        self, cfg: Dict[str, str]
    ) -> Optional[MarketPulseItemResponse]:
        """Fetch one tile: a live quote + a daily-close sparkline, concurrently.

        A missing quote/price drops the tile (returns None). Sparkline failure is
        non-fatal — the tile still renders with an empty series.
        """
        symbol = cfg["symbol"]
        quote, spark = await asyncio.gather(
            self.fmp.get_stock_price_quote(symbol),
            self._fetch_sparkline(symbol),
        )

        if not quote:
            logger.warning("No quote for pulse symbol %s — dropping tile", symbol)
            return None

        price = quote.get("price")
        if price is None:
            logger.warning("Quote for %s missing price — dropping tile", symbol)
            return None

        # FMP returns the % field as `changesPercentage`; some endpoints/versions
        # use the singular `changePercentage`. Accept either.
        change = quote.get("changesPercentage")
        if change is None:
            change = quote.get("changePercentage")

        try:
            price_f = float(price)
        except (TypeError, ValueError):
            logger.warning("Quote for %s has non-numeric price %r — dropping tile", symbol, price)
            return None

        try:
            change_f = float(change) if change is not None else 0.0
        except (TypeError, ValueError):
            change_f = 0.0

        # Prior close → the dashed reference line on the iOS sparkline.
        prev_close_raw = quote.get("previousClose")
        try:
            previous_close = (
                round(float(prev_close_raw), 2) if prev_close_raw else None
            )
        except (TypeError, ValueError):
            previous_close = None

        return MarketPulseItemResponse(
            symbol=symbol,
            name=cfg["name"],
            type=cfg["type"],
            price=round(price_f, 2),
            change_percent=round(change_f, 2),
            previous_close=previous_close,
            spark=spark,
        )

    async def _fetch_sparkline(self, symbol: str) -> List[float]:
        """Latest-session 1D intraday closes (oldest-first) for the mini-chart.

        Uses the SAME series the TickerDetailView 1D chart and the holdings
        cards draw (5-min intraday, regular hours, via the shared chart_helper),
        so the dashed previous-close reference reads correctly. Returns [] on
        failure — never a synthetic series.
        """
        try:
            bars = await fetch_chart_data(self.fmp, symbol, "1D")
            return _intraday_sparkline(bars)
        except Exception as exc:
            logger.warning(
                "Sparkline (1D intraday) for %s failed: %s: %s",
                symbol, type(exc).__name__, exc,
            )
            return []


# ── Singleton ─────────────────────────────────────────────────────────

_service: Optional[HomeDashboardService] = None


def get_home_dashboard_service() -> HomeDashboardService:
    global _service
    if _service is None:
        _service = HomeDashboardService()
    return _service
