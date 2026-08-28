"""
Valuation Snapshot service — computes sector-relative valuation ratings
for P/E, P/S, P/FCF, and EV/EBITDA using pre-computed sector medians
from the sector_benchmarks table.

Uses the same data as the Financials tab (FMP financial_ratios + key_metrics)
so the user sees consistent numbers.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``snapshot_cache`` table (24-hour TTL)

Matches the iOS SnapshotItemDTO struct.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services.sector_benchmark_lookup import get_sector_benchmark_lookup
from app.services.sector_benchmark_service import _normalize_sector

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes

# ── Payload version: invalidate rows written before a FORMATTING change ──────
#
# `snapshot_cache` stores `result.model_dump()`, i.e. the FORMATTED STRINGS — so a change
# to how a value is RENDERED does not reach a user until that row ages out, and the 24h
# window is long enough to be seen.
#
# It was seen. The "Neg." vs "—" fix rebuilt Key Statistics live while the Price card on
# the SAME SCREEN kept serving a pre-fix row, so one screen said both things about one
# company at once — the exact self-contradiction the bug report was about. Measured while
# fixing it: MRNA's row (written 16:22 UTC) still read "—" beside a Key Statistics
# "Neg.", while a cold RIVN rebuilt correctly.
#
# A VERSION, not a timestamp floor like `ticker_report_cache.CACHE_SCHEMA_FLOOR`. That
# pattern needs its literal to equal the deploy instant: set it earlier and rows the old
# build writes between the bump and the deploy survive; set it later and it is
# future-dated, which rejects even fresh rows and turns the cache permanently cold. A
# version has no such window — a row is either the current shape or it is not, whenever
# it was written. **Bump it whenever these strings change shape.**
#
# Forward-safe in both directions: an OLD build reading a NEW row passes the extra key
# into `SnapshotItemResponse(**json_data)`, which raises, and the surrounding `except`
# turns that into a rebuild. Nothing crashes; the worst case is one recomputation.
#
# 2 (2026-08-26): negative multiples render "Neg." (undefined) instead of "—" (unknown).
# 3 (2026-08-26): EV/EBITDA reads `enterpriseValueMultipleTTM` — the key `/stable`
#     actually populates — instead of falling through to a current-EV ÷ ANNUAL-EBITDA
#     reconstruction. Changes the number on every cached ticker (AAPL 32.19 → 27.59).
_SNAPSHOT_PAYLOAD_VERSION = 3
_VERSION_KEY = "_schema_v"


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


# Hard cap on the in-memory tier. Without it this dict grew with the number of DISTINCT
# keys ever requested and was never pruned: `_cache_get` only deletes an entry when that
# SAME key is read again after expiry, so a ticker fetched once and never revisited stayed
# resident for the life of the process. Across ~17 services on a long-lived Railway
# container that is a slow leak whose only resolution is an OOM restart — which drops every
# in-flight report with it. Bounded LRU-ish: evict from the head (least recently WRITTEN).
_CACHE_MAX_ENTRIES = 1024


def _cache_set(key: str, value: Any) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


# ── In-flight deduplication ───────────────────────────────────────
_inflight: Dict[str, asyncio.Future] = {}

# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    val = record.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _fmt_ratio(val: Optional[float]) -> str:
    """Format a valuation ratio for display. THREE outcomes, not two.

    A price multiple is undefined when its denominator is negative — a company losing
    money has no meaningful P/E, and one with negative book equity has no meaningful P/B.
    But "undefined" and "we could not fetch it" are different facts, and this used to
    collapse both to "—".

    That cost a TestFlight report ("Data is missing? Double check for me", MRNA): FMP
    returns `priceToEarningsRatioTTM = -18.75` for Moderna, we knew the number, and the
    card said the same thing it says when the upstream is down. Worse, the SAME card
    rendered `P/FCF = "Neg."` two rows below, because `_fmt_pfcf` had already worked this
    out and the rule was never generalised.

      None → "—"      genuinely unknown
      < 0  → "Neg."   known, and the multiple is undefined because earnings/book/etc.
                      are negative — which is itself the useful signal
      0    → "—"      FMP uses 0 for absent on these fields; a true zero multiple is not
                      a real quantity either way
      > 0  → the number

    ⚠️ DISPLAY ONLY. `_valuation_score` and `_sector_ctx` take the float and already treat
    `<= 0` as "no comparison" — do not route scoring through here, or a loss-maker's star
    rating would move as a side effect of a copy change.
    """
    if val is None or val == 0:
        return "—"
    if val < 0:
        return "Neg."
    return f"{val:.2f}"


def _fmt_pfcf(
    pfcf: Optional[float],
    km: Dict[str, Any],
    cf: Optional[Dict[str, Any]] = None,
) -> str:
    """P/FCF is undefined when free cash flow is negative. Surface that
    explicitly as "Neg." so the user knows the company is burning cash —
    different signal from "data missing" ("—"). Detected via the FMP
    `freeCashFlowYield` field which carries the sign of FCF; falls back to
    the cash-flow statement's `freeCashFlow` when TTM yield is absent.
    """
    # The ratio itself carries the sign when we have it, so `_fmt_ratio` now answers the
    # whole question — this function exists only for the case FMP leaves the ratio absent
    # (MRNA: `priceToFreeCashFlowsRatioTTM` is null) and the SIGN has to be recovered from
    # a sibling field. `!= 0` keeps the old fall-through for a zero ratio, which on these
    # fields means "absent" rather than "zero".
    if pfcf is not None and pfcf != 0:
        return _fmt_ratio(pfcf)
    fcf_yield = _safe_float(km, "freeCashFlowYield")
    if fcf_yield is not None and fcf_yield < 0:
        return "Neg."
    if cf:
        fcf = _safe_float(cf, "freeCashFlow")
        if fcf is not None and fcf < 0:
            return "Neg."
    return "—"


def _valuation_score(value: Optional[float], sector_median: Optional[float]) -> int:
    """
    Score 1-5 based on how a company's valuation compares to sector median.
    Lower multiples = better value (inverted scoring).
    """
    if value is None or value <= 0:
        return 3  # neutral if no data

    if sector_median is None or sector_median <= 0:
        # Absolute fallback thresholds (no sector data available)
        # These are general "reasonable" ranges for any sector
        if value < 10:
            return 5
        if value < 18:
            return 4
        if value < 28:
            return 3
        if value < 40:
            return 2
        return 1

    ratio = value / sector_median
    if ratio <= 0.7:
        return 5   # 30%+ cheaper than sector
    if ratio <= 0.9:
        return 4   # 10-30% cheaper
    if ratio <= 1.2:
        return 3   # within 20% of sector
    if ratio <= 1.5:
        return 2   # 20-50% more expensive
    return 1        # 50%+ more expensive


# Single-value sector comparisons use the mature-period picker
# (`mature_benchmark_value`) — it holds the last fully-reported year instead of a
# thin just-closed one. (The old `_get_latest_benchmark` max-year helper was
# replaced; it had no sample-size floor.)


def _sector_ctx(val: Optional[float], sector_median: Optional[float]) -> str:
    """Build sector context string like '1.2x sector avg 25'. When the
    company's value is missing or non-positive (e.g. P/FCF rendered as
    "Neg." or EV/EBITDA unavailable) but the sector benchmark exists, we
    still emit "sector avg N" — iOS's displayLabel regex picks that up to
    render the "*" footnote marker. Returns '' only when no sector data.
    """
    if sector_median is None or sector_median <= 0:
        return ""
    if val is None or val <= 0:
        return f"sector avg {sector_median:.0f}"
    ratio = val / sector_median
    return f"{ratio:.2f}x sector avg {sector_median:.0f}"


def _metric_name(label: str, val: Optional[float], sector_median: Optional[float]) -> str:
    """Build metric name with optional sector context. No '(—)' when data is missing."""
    ctx = _sector_ctx(val, sector_median)
    if ctx:
        return f"{label} ({ctx})"
    return label


def _fmt_pct(val: Optional[float]) -> str:
    """Format a decimal-form percentage for display (e.g. 0.0425 → '4.25%')."""
    if val is None or val <= 0:
        return "N/A"
    return f"{val * 100:.2f}%"


def _sector_ctx_pct(val: Optional[float], sector_median: Optional[float]) -> str:
    """Sector context for percentage metrics. Both inputs are decimals
    (e.g. 0.0425 for 4.25%). Output displays the median as a percent.
    Same fallback as `_sector_ctx`: emit "sector avg X%" when the company
    value is missing so iOS still adds the asterisk."""
    if sector_median is None or sector_median <= 0:
        return ""
    if val is None or val <= 0:
        return f"sector avg {sector_median * 100:.2f}%"
    ratio = val / sector_median
    return f"{ratio:.2f}x sector avg {sector_median * 100:.2f}%"


def _metric_name_pct(label: str, val: Optional[float], sector_median: Optional[float]) -> str:
    """Same as `_metric_name` but for decimal percentage metrics."""
    ctx = _sector_ctx_pct(val, sector_median)
    if ctx:
        return f"{label} ({ctx})"
    return label


# ── Service ───────────────────────────────────────────────────────

class ValuationSnapshotService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_valuation_snapshot(self, ticker: str) -> SnapshotItemResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"val_snapshot:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Valuation snapshot in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Valuation snapshot Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Valuation snapshot in-flight JOIN for {ticker}")
            return await asyncio.shield(_inflight[cache_key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Valuation snapshot cache MISS for {ticker} — computing")
            result = await self._compute(ticker)

            # Persist to Supabase in background thread
            asyncio.get_running_loop().run_in_executor(
                None, self._upsert_supabase_cache, ticker, result,
            )

            _cache_set(cache_key, result)
            if not future.done():
                future.set_result(result)
            return result
        except asyncio.CancelledError:
            # CancelledError is a BaseException, NOT an Exception, so it skips the handler
            # below and used to leave this future unresolved forever — every joiner attached
            # via `await _inflight[...]` then hung for the life of the process. Reachable
            # whenever the LEADER is a cancellable caller: a report run hitting
            # RESEARCH_PIPELINE_TIMEOUT_SECONDS, or any pre-warm task cancelled at shutdown.
            # Hand waiters a normal exception so they fail fast through their own error path.
            if not future.done():
                future.set_exception(RuntimeError("in-flight fetch was cancelled"))
            raise
        except Exception as e:
            if not future.done():
                future.set_exception(e)
            raise
        finally:
            _inflight.pop(cache_key, None)

    # ── Supabase helpers ──────────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[SnapshotItemResponse]:
        try:
            row = (
                self.supabase.table("snapshot_cache")
                .select("response_json, cached_at")
                .eq("ticker", ticker)
                .eq("category", "Price")
                .limit(1)
                .execute()
            )
            if not row.data:
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Valuation snapshot Supabase STALE (age={age}) for {ticker}")
                return None
            json_data = dict(entry["response_json"] or {})
            version = json_data.pop(_VERSION_KEY, 1)
            if version != _SNAPSHOT_PAYLOAD_VERSION:
                # Written by a build that formatted these strings differently — see the
                # version's comment. Rebuild rather than serve a row that disagrees with
                # the rest of the screen.
                logger.info(
                    "Valuation snapshot payload v%s != v%s for %s — rebuilding",
                    version, _SNAPSHOT_PAYLOAD_VERSION, ticker,
                )
                return None
            return SnapshotItemResponse(**json_data)

        except Exception as e:
            logger.warning(f"Valuation snapshot cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache(self, ticker: str, result: SnapshotItemResponse) -> None:
        try:
            self.supabase.table("snapshot_cache").upsert(
                {
                    "ticker": ticker,
                    "category": "Price",
                    "response_json": {
                        **result.model_dump(),
                        _VERSION_KEY: _SNAPSHOT_PAYLOAD_VERSION,
                    },
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker,category",
            ).execute()
        except Exception as e:
            logger.warning(f"Valuation snapshot upsert failed for {ticker}: {e}")

    # ── Core computation ──────────────────────────────────────────

    async def _compute(self, ticker: str) -> SnapshotItemResponse:
        """Fetch TTM ratios + same fallback data as Financials tab and score
        against sector benchmarks.

        Switched from `period=annual` to TTM (`ratios-ttm` / `key-metrics-ttm`)
        in 2026-05 — the annual endpoints anchor to the last fiscal year-end,
        which for ORCL (FY ends May) was up to 24 months stale and drove a
        62% gap on P/B vs Webull. TTM rolls the trailing four quarters so
        denominators stay current.
        """

        # Parallel fetch — TTM-first for valuation ratios, annual cash_flow +
        # income kept only as fallback for the P/FCF / EV/EBITDA reconstruction
        # paths (FMP doesn't expose TTM cash flow statements directly). The
        # latest quarterly balance sheet feeds the EV reconstruction rung
        # (EV = mcap + totalDebt − cash) when FMP omits enterpriseValue.
        results = await asyncio.gather(
            self.fmp.get_company_profile(ticker),
            self.fmp.get_ratios_ttm(ticker),
            self.fmp.get_key_metrics_ttm(ticker),
            self.fmp.get_cash_flow_statement(ticker, period="annual", limit=1),
            self.fmp.get_income_statement(ticker, period="annual", limit=1),
            self.fmp.get_balance_sheet(ticker, period="quarter", limit=1),
            return_exceptions=True,
        )

        def _parse_first(raw) -> Dict:
            if isinstance(raw, list) and raw:
                return raw[0]
            if isinstance(raw, dict):
                return raw
            return {}

        profile = _parse_first(results[0]) if not isinstance(results[0], Exception) else {}
        fr = _parse_first(results[1]) if not isinstance(results[1], Exception) else {}
        km = _parse_first(results[2]) if not isinstance(results[2], Exception) else {}
        cf = _parse_first(results[3]) if not isinstance(results[3], Exception) else {}
        inc = _parse_first(results[4]) if not isinstance(results[4], Exception) else {}
        bs = _parse_first(results[5]) if not isinstance(results[5], Exception) else {}

        # Extract valuation metrics. /ratios-ttm uses a `TTM` suffix on field
        # names; /key-metrics-ttm uses a different convention. Cover both
        # plus the legacy names so a quiet FMP rename doesn't NULL out the
        # whole card.
        def _first_valid(*vals) -> Optional[float]:
            for v in vals:
                if v is not None:
                    return v
            return None

        pe = _first_valid(
            _safe_float(fr, "priceToEarningsRatioTTM"),
            _safe_float(fr, "priceToEarningsRatio"),
            _safe_float(km, "peRatioTTM"),
            _safe_float(km, "peRatio"),
        )
        ps = _first_valid(
            _safe_float(fr, "priceToSalesRatioTTM"),
            _safe_float(fr, "priceToSalesRatio"),
            _safe_float(km, "priceToSalesRatioTTM"),
            _safe_float(km, "priceToSalesRatio"),
        )
        pb = _first_valid(
            _safe_float(fr, "priceToBookRatioTTM"),
            _safe_float(fr, "priceToBookRatio"),
            _safe_float(km, "pbRatioTTM"),
            _safe_float(km, "pbRatio"),
        )
        pfcf = _first_valid(
            _safe_float(fr, "priceToFreeCashFlowsRatioTTM"),
            _safe_float(fr, "priceToFreeCashFlowsRatio"),
            _safe_float(km, "pfcfRatioTTM"),
            _safe_float(km, "pfcfRatio"),
        )
        # ⚠️ `enterpriseValueMultipleTTM` FIRST — it is the only one of these `/stable`
        # actually populates. Measured across AAPL, MSFT, KO, NVDA, JPM, XOM, MRNA, PLUG,
        # RIVN and UBER: `enterpriseValueOverEBITDATTM` is None for EVERY one, so the
        # primary lookup always failed and the reconstruction ladder below ran instead —
        # dividing a CURRENT enterprise value by the LAST FISCAL YEAR's EBITDA, because
        # `inc` is fetched `period="annual", limit=1`.
        #
        # For AAPL that read 4648.51B / 144.43B = 32.19 against a true TTM 4648.51B /
        # 168.49B = 27.59: we overstated it by ~17%, and EV/EBITDA was the only annual
        # metric on a card whose P/E, P/B, P/S and P/FCF are all TTM. Verified that
        # `enterpriseValueMultipleTTM` equals EV(TTM)/EBITDA(TTM) exactly.
        #
        # The older names stay behind it so an upstream rename back still works.
        ev_ebitda = _first_valid(
            _safe_float(fr, "enterpriseValueMultipleTTM"),
            _safe_float(fr, "enterpriseValueMultiple"),
            _safe_float(km, "enterpriseValueMultipleTTM"),
            _safe_float(km, "enterpriseValueMultiple"),
            _safe_float(fr, "enterpriseValueOverEBITDATTM"),
            _safe_float(fr, "enterpriseValueOverEBITDA"),
            _safe_float(km, "enterpriseValueOverEBITDATTM"),
            _safe_float(km, "enterpriseValueOverEBITDA"),
        )

        # Market-cap fallback chain — FMP sometimes omits it from key_metrics
        # for less-covered tickers. Profile.mktCap and key_metrics.marketCap
        # are typically identical; profile is the more reliable surface.
        #
        # `mktCap` is safe despite `/stable` having renamed the raw field to `marketCap`:
        # `fmp._normalize_profile` aliases it back on BOTH profile paths (single and
        # batch), precisely so this class of rename cannot silently zero out
        # market-cap-driven logic. Do not "fix" this to read `marketCap` — it is already
        # handled one layer down, and duplicating it here just implies the shim is absent.
        mcap = _safe_float(km, "marketCap") or _safe_float(profile, "mktCap")

        # Fallback: compute P/FCF from marketCap / freeCashFlow. When FCF is
        # negative the ratio is meaningless (negative multiples don't compare),
        # so we leave pfcf as None and the renderer shows "—".
        if pfcf is None:
            fcf = _safe_float(cf, "freeCashFlow")
            if mcap and mcap > 0 and fcf and fcf > 0:
                pfcf = round(mcap / fcf, 2)

        # Fallback chain for EV/EBITDA when both /ratios-ttm and /key-metrics-ttm
        # return null:
        #   1. Reconstruct EV/EBITDA from key_metrics.enterpriseValue ÷ inc.ebitda
        #   2. EBITDA fallback: operatingIncome + D&A from cf or inc
        #   3. EBITDA last resort: netIncome + interestExpense + incomeTaxExpense + D&A
        #   4. EV fallback: mcap + totalDebt − cash from latest quarterly balance sheet
        # Logs which rung succeeded so the next time a ticker drops to "—" we can
        # see why without rerunning instrumentation.
        if ev_ebitda is None:
            ev = _safe_float(km, "enterpriseValue")
            ev_source = "key_metrics.enterpriseValue"

            ebitda = _safe_float(inc, "ebitda")
            ebitda_source = "inc.ebitda"

            # `!= 0`, not `> 0`: a negative EBITDA is a real (if undefined-as-a-multiple)
            # denominator, and `_fmt_ratio` now renders the resulting negative as "Neg.".
            # Gating it out here is what left loss-makers on "—" even when the numbers
            # were all present. Zero stays excluded — it would divide by zero.
            if ebitda is None or ebitda == 0:
                op_income = _safe_float(inc, "operatingIncome")
                d_and_a = (
                    _safe_float(cf, "depreciationAndAmortization")
                    or _safe_float(inc, "depreciationAndAmortization")
                )
                if op_income is not None and d_and_a is not None:
                    ebitda = op_income + d_and_a
                    ebitda_source = "operatingIncome + D&A"

            if ebitda is None or ebitda == 0:
                # Last resort: EBITDA ≈ NI + interest + tax + D&A
                ni = _safe_float(inc, "netIncome")
                interest = _safe_float(inc, "interestExpense")
                tax = _safe_float(inc, "incomeTaxExpense")
                d_and_a = (
                    _safe_float(cf, "depreciationAndAmortization")
                    or _safe_float(inc, "depreciationAndAmortization")
                )
                if ni is not None and d_and_a is not None:
                    ebitda = ni + (interest or 0) + (tax or 0) + d_and_a
                    ebitda_source = "NI + interest + tax + D&A"

            if (ev is None or ev <= 0) and mcap and mcap > 0:
                # Reconstruct EV from balance sheet: mcap + totalDebt − cash.
                total_debt = _safe_float(bs, "totalDebt")
                cash = (
                    _safe_float(bs, "cashAndShortTermInvestments")
                    or _safe_float(bs, "cashAndCashEquivalents")
                )
                if total_debt is not None:
                    ev = mcap + total_debt - (cash or 0)
                    ev_source = "mcap + totalDebt − cash"

            # `ebitda != 0`, not `> 0` — the final gate, and the one that actually kept
            # loss-makers on "—" even when every input above resolved. A negative ratio
            # now flows out and `_fmt_ratio` renders it "Neg.". `ev > 0` is left alone: a
            # negative enterprise value (net cash above market cap) is a different and far
            # rarer condition, and not what this change is about.
            if ev and ev > 0 and ebitda:
                ev_ebitda = round(ev / ebitda, 2)
                logger.info(
                    "EV/EBITDA reconstructed for %s via ev=%s, ebitda=%s, ratio=%.2f",
                    ticker, ev_source, ebitda_source, ev_ebitda,
                )
            else:
                logger.warning(
                    "EV/EBITDA unavailable for %s — ev=%s (source=%s), ebitda=%s (source=%s)",
                    ticker, ev, ev_source, ebitda, ebitda_source,
                )

        # Earnings Yield (decimal form, e.g. 0.0425 for 4.25%). Fallback chain:
        #   1. ratios.earningsYield (TTM-suffixed first, then bare name)
        #   2. key_metrics.earningsYield (TTM and legacy)
        #   3. 1/PE  (matches the canonical formula)
        #   4. netIncome / marketCap
        ey = _first_valid(
            _safe_float(fr, "earningsYieldTTM"),
            _safe_float(fr, "earningsYield"),
            _safe_float(km, "earningsYieldTTM"),
            _safe_float(km, "earningsYield"),
        )
        if ey is None and pe is not None and pe > 0:
            ey = round(1.0 / pe, 4)
        if ey is None:
            ni = _safe_float(inc, "netIncome")
            if ni is not None and ni > 0 and mcap and mcap > 0:
                ey = round(ni / mcap, 4)

        # Get sector for benchmark comparison
        raw_sector = profile.get("sector", "")
        sector = _normalize_sector(raw_sector) if raw_sector else ""
        # Industry-relative: prefer INDUSTRY peers, fall back to sector per cell.
        industry = profile.get("industry", "") if isinstance(profile, dict) else ""

        # CURRENT benchmark per metric: TTM row if present, else latest mature
        # annual value (fallback). {metric: value | None}.
        cur_bench: Dict[str, Optional[float]] = {}
        if sector:
            try:
                lookup = get_sector_benchmark_lookup()
                cur_bench = lookup.get_current_benchmark_values(
                    industry,
                    sector,
                    ["pe_ratio", "ps_ratio", "pb_ratio", "pfcf_ratio", "ev_ebitda", "earnings_yield"],
                )
            except Exception as e:
                logger.warning(f"Sector benchmark lookup failed for {ticker}: {e}")

        sector_pe = cur_bench.get("pe_ratio")
        sector_ps = cur_bench.get("ps_ratio")
        sector_pb = cur_bench.get("pb_ratio")
        sector_pfcf = cur_bench.get("pfcf_ratio")
        sector_ev = cur_bench.get("ev_ebitda")
        sector_ey = cur_bench.get("earnings_yield")

        # Score each metric against sector median (lower = better)
        score_pe = _valuation_score(pe, sector_pe)
        score_ps = _valuation_score(ps, sector_ps)
        score_pb = _valuation_score(pb, sector_pb)
        score_pfcf = _valuation_score(pfcf, sector_pfcf)
        score_ev = _valuation_score(ev_ebitda, sector_ev)

        # Weighted average: P/E 25%, P/B 15%, P/S 15%, P/FCF 20%, EV/EBITDA 25%
        weighted = (
            score_pe * 0.25
            + score_pb * 0.15
            + score_ps * 0.15
            + score_pfcf * 0.20
            + score_ev * 0.25
        )
        rating = max(1, min(5, round(weighted)))

        metrics = [
            SnapshotMetricResponse(
                name=_metric_name("P/E", pe, sector_pe),
                value=_fmt_ratio(pe),
                metric_key="pe",
                score=score_pe if pe is not None else None,
            ),
            SnapshotMetricResponse(
                name=_metric_name("P/B", pb, sector_pb),
                value=_fmt_ratio(pb),
                metric_key="pb",
                score=score_pb if pb is not None else None,
            ),
            SnapshotMetricResponse(
                name=_metric_name("P/S", ps, sector_ps),
                value=_fmt_ratio(ps),
                metric_key="ps",
                score=score_ps if ps is not None else None,
            ),
            SnapshotMetricResponse(
                name=_metric_name("P/FCF", pfcf, sector_pfcf),
                value=_fmt_pfcf(pfcf, km, cf),
                metric_key="pfcf",
                score=score_pfcf if pfcf is not None else None,
            ),
            SnapshotMetricResponse(
                name=_metric_name("EV/EBITDA", ev_ebitda, sector_ev),
                value=_fmt_ratio(ev_ebitda),
                metric_key="ev_ebitda",
                score=score_ev if ev_ebitda is not None else None,
            ),
            # Earnings Yield: informational — not part of the composite
            # star-rating (which weights P/E, P/B, P/S, P/FCF, EV/EBITDA only)
            # to keep historical ratings comparable. score=None → not a verdict driver.
            SnapshotMetricResponse(
                name=_metric_name_pct("Earnings Yield", ey, sector_ey),
                value=_fmt_pct(ey),
                metric_key="earnings_yield",
                score=None,
            ),
        ]

        return SnapshotItemResponse(
            category="Price",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
            weighted_score=round(weighted, 3),
        )


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[ValuationSnapshotService] = None


def get_valuation_snapshot_service() -> ValuationSnapshotService:
    global _service
    if _service is None:
        _service = ValuationSnapshotService()
    return _service
