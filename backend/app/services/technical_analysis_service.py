"""
Technical Analysis Service — computes 18 technical indicators on daily and
weekly OHLCV data, produces a gauge score and full detail breakdown.

Uses the ``ta`` library (Technical Analysis Library in Python) for indicator
computation.  Follows the same service patterns as sentiment_service.py:
stateless class, in-memory TTL cache, singleton getter.
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd
import ta as ta_lib
from fastapi import HTTPException

from app.integrations.fmp import FMPClient, get_fmp_client
from app.schemas.technical_analysis import (
    FibonacciLevel,
    FibonacciRetracementData,
    IndicatorSignal,
    IndicatorSummary,
    LevelStrength,
    MovingAverageIndicator,
    OscillatorIndicator,
    PivotLevelType,
    PivotPointLevel,
    PivotPointsData,
    SupportResistanceData,
    SupportResistanceLevel,
    TechnicalAnalysisDetailResponse,
    TechnicalAnalysisResponse,
    TechnicalIndicatorResult,
    TechnicalSignal,
    VolumeAnalysisData,
    VolumeTrend,
)
from app.services.asset_class import detect_asset_class

logger = logging.getLogger(__name__)

# ── In-memory cache ──────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 43_200  # 12 hours in seconds
_CACHE_TTL_CRYPTO = 14_400  # 4 hours — crypto is 24/7 and more volatile

TOTAL_INDICATORS = 18


def _cache_get(key: str, ttl: float = _CACHE_TTL) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
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


# Thundering-herd guard. This service had NONE: a cold-start burst of N concurrent
# viewers of the same ticker each ran its own 600-day fetch AND its own pandas indicator
# pass. commodity_service has carried this guard for a while; this is the same pattern.
_inflight: Dict[str, asyncio.Future] = {}

# The 600-day OHLCV frame is shared between get_analysis and get_analysis_detail. They
# cached their RESULTS under separate keys but each fetched its own copy of the identical
# history, so opening the Analysis tab and then its detail sheet cost two 600-day calls.
_OHLCV_TTL = 3600  # 1h — daily bars, so anything finer is wasted


def _cache_set(key: str, value: Any) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


def _round_price(v: Optional[float], default: float = 0.0) -> float:
    """Round a PRICE with magnitude-aware precision.

    Everything here used to be `round(x, 2)`, which is right for equities and destroys
    sub-cent assets: SHIB trades near $0.00000495 (verified live on
    /stable/historical-price-eod/full), so every pivot, every Fibonacci level, every
    support/resistance band and the current price all serialised as `0.0`. The Technical
    Analysis detail sheet then showed a full set of actionable price levels, all zero,
    for SHIB / PEPE / BONK and any other sub-penny coin.

    2 dp at/above $1, 6 dp down to $0.0001, 10 dp below that — enough to keep a
    meme-coin's levels distinguishable without turning equity prices into noise.
    """
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    mag = abs(f)
    if mag >= 1:
        return round(f, 2)
    if mag >= 0.0001:
        return round(f, 6)
    return round(f, 10)


def _safe_float(v: Any) -> Optional[float]:
    """Convert a value to float, returning None for NaN / None / non-numeric."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _safe_round(v: Optional[float], digits: int = 2) -> Optional[float]:
    return round(v, digits) if v is not None else None


# ── Gauge ↔ Signal mapper ───────────────────────────────────────

def _gauge_to_signal(gauge_value: float) -> TechnicalSignal:
    """Map 0.0-1.0 gauge value to TechnicalSignal (matches Swift gaugeLevel)."""
    if gauge_value < 0.2:
        return TechnicalSignal.STRONG_SELL
    if gauge_value < 0.4:
        return TechnicalSignal.SELL
    if gauge_value < 0.6:
        return TechnicalSignal.HOLD
    if gauge_value < 0.8:
        return TechnicalSignal.BUY
    return TechnicalSignal.STRONG_BUY


def _count_summary(
    indicators: Union[List[MovingAverageIndicator], List[OscillatorIndicator]],
) -> IndicatorSummary:
    buy = sum(1 for i in indicators if i.signal == IndicatorSignal.BUY)
    sell = sum(1 for i in indicators if i.signal == IndicatorSignal.SELL)
    neutral = sum(1 for i in indicators if i.signal == IndicatorSignal.NEUTRAL)
    return IndicatorSummary(buy_count=buy, neutral_count=neutral, sell_count=sell)


# ═══════════════════════════════════════════════════════════════════
async def _deduped(key: str, build):
    """Run `build()` once per key, sharing the result with concurrent callers.

    Mirrors news_cache_service._deduped: the join is SHIELDED so a joiner that gives up
    cannot cancel the future the leader is about to publish into, and the map entry is
    cleared in a `finally` so a cancellation cannot strand every later caller.
    """
    inflight = _inflight.get(key)
    if inflight is not None:
        logger.info("Technical analysis already in flight for %s — joining", key)
        return await asyncio.shield(inflight)

    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _inflight[key] = fut
    try:
        result = await build()
        if not fut.done():
            fut.set_result(result)
        return result
    except BaseException as e:
        # BaseException, not Exception: a CancelledError must still resolve the future or
        # every joiner hangs forever waiting on a dead build.
        if not fut.done():
            fut.set_exception(e)
        raise
    finally:
        _inflight.pop(key, None)


class TechnicalAnalysisService:
    """Stateless service for technical indicator computation."""

    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()

    # ── Public API ─────────────────────────────────────────────

    async def get_analysis(self, ticker: str) -> TechnicalAnalysisResponse:
        """Gauge endpoint: daily + weekly signals, overall gauge value."""
        ticker = ticker.upper()
        # `detect_asset_class`, NOT a bare `endswith("USD")`. Every FMP commodity code
        # is exactly that shape — GCUSD, CLUSD, SIUSD, NGUSD, ZCUSD — so the suffix test
        # classified all 15 commodities as CRYPTO: they got the 4h crypto TTL instead of
        # 12h, and `_daily_to_weekly(..., is_crypto=True)` applied crypto weekly bucketing
        # to futures bars. `asset_class` already owns the commodity set and documents this
        # exact trap; this copy of the heuristic simply never got the fix.
        is_crypto = detect_asset_class(ticker) == "crypto"

        ttl = _CACHE_TTL_CRYPTO if is_crypto else _CACHE_TTL
        cached = _cache_get(f"ta:{ticker}", ttl)
        if cached is not None:
            return cached

        return await _deduped(
            f"ta:build:{ticker}", lambda: self._build_analysis(ticker, is_crypto)
        )

    async def _build_analysis(
        self, ticker: str, is_crypto: bool
    ) -> TechnicalAnalysisResponse:

        df_daily = await self._fetch_daily_ohlcv(ticker)
        df_weekly = self._daily_to_weekly(df_daily, is_crypto=is_crypto)

        daily_result, daily_gauge, _, _ = self._compute_timeframe_signal(df_daily)
        weekly_result, weekly_gauge, _, _ = self._compute_timeframe_signal(df_weekly)

        # Overall gauge: average of the daily and weekly NET gauges (each already
        # centres NEUTRAL at 0.5), so a neutral/insufficient-data ticker reads HOLD.
        overall_gauge = (daily_gauge + weekly_gauge) / 2.0

        response = TechnicalAnalysisResponse(
            symbol=ticker,
            daily_signal=daily_result,
            weekly_signal=weekly_result,
            overall_signal=_gauge_to_signal(overall_gauge),
            gauge_value=round(overall_gauge, 4),
        )

        _cache_set(f"ta:{ticker}", response)
        return response

    async def get_analysis_detail(
        self, ticker: str
    ) -> TechnicalAnalysisDetailResponse:
        """Detail endpoint: full indicator breakdown + extras."""
        ticker = ticker.upper()
        # `detect_asset_class`, NOT a bare `endswith("USD")`. Every FMP commodity code
        # is exactly that shape — GCUSD, CLUSD, SIUSD, NGUSD, ZCUSD — so the suffix test
        # classified all 15 commodities as CRYPTO: they got the 4h crypto TTL instead of
        # 12h, and `_daily_to_weekly(..., is_crypto=True)` applied crypto weekly bucketing
        # to futures bars. `asset_class` already owns the commodity set and documents this
        # exact trap; this copy of the heuristic simply never got the fix.
        is_crypto = detect_asset_class(ticker) == "crypto"

        ttl = _CACHE_TTL_CRYPTO if is_crypto else _CACHE_TTL
        cached = _cache_get(f"ta_detail:{ticker}", ttl)
        if cached is not None:
            return cached

        df_daily = await self._fetch_daily_ohlcv(ticker)
        df_weekly = self._daily_to_weekly(df_daily, is_crypto=is_crypto)

        _, _, ma_list, osc_list = self._compute_timeframe_signal(df_daily)
        _, _, weekly_ma_list, weekly_osc_list = self._compute_timeframe_signal(df_weekly)

        response = TechnicalAnalysisDetailResponse(
            symbol=ticker,
            # Daily
            moving_averages=ma_list,
            moving_averages_summary=_count_summary(ma_list),
            oscillators=osc_list,
            oscillators_summary=_count_summary(osc_list),
            # Weekly
            weekly_moving_averages=weekly_ma_list,
            weekly_moving_averages_summary=_count_summary(weekly_ma_list),
            weekly_oscillators=weekly_osc_list,
            weekly_oscillators_summary=_count_summary(weekly_osc_list),
            # Extras
            pivot_points=self._compute_pivot_points(df_daily),
            volume_analysis=self._compute_volume_analysis(df_daily),
            fibonacci_retracement=self._compute_fibonacci(df_daily),
            support_resistance=self._compute_support_resistance(df_daily),
        )

        _cache_set(f"ta_detail:{ticker}", response)
        return response

    # ── Data Fetching ──────────────────────────────────────────

    async def _fetch_daily_ohlcv(self, ticker: str) -> pd.DataFrame:
        """~600 calendar days of daily OHLCV, cached and shared across both endpoints.

        `get_analysis` and `get_analysis_detail` cache their RESULTS separately but were
        each fetching their own copy of this identical frame, so a user who opened the
        Analysis tab and then tapped through to the detail sheet paid for two 600-day
        histories. One key, one fetch, 1h TTL (they are daily bars).
        """
        ohlcv_key = f"ta_ohlcv:{ticker}"
        cached = _cache_get(ohlcv_key, _OHLCV_TTL)
        if cached is not None:
            return cached
        df = await _deduped(ohlcv_key, lambda: self._fetch_daily_ohlcv_uncached(ticker))
        if df is not None and not df.empty:
            _cache_set(ohlcv_key, df)
        return df

    async def _fetch_daily_ohlcv_uncached(self, ticker: str) -> pd.DataFrame:
        """Fetch ~600 calendar days of daily OHLCV and return as DataFrame."""
        to_date = datetime.utcnow().strftime("%Y-%m-%d")
        from_date = (datetime.utcnow() - timedelta(days=600)).strftime("%Y-%m-%d")

        raw = await self.fmp.get_historical_prices(ticker, from_date, to_date)

        # Parse FMP response
        historical: List[Dict[str, Any]] = []
        if isinstance(raw, dict):
            historical = raw.get("historical", [])
        elif isinstance(raw, list):
            historical = raw

        if not historical:
            raise HTTPException(
                status_code=404,
                detail=f"No historical price data available for {ticker}",
            )

        # Sort oldest-first
        historical.sort(key=lambda p: p.get("date") or "")

        df = pd.DataFrame(historical)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Prefer adjClose when available and positive
        if "adjClose" in df.columns:
            adj = pd.to_numeric(df["adjClose"], errors="coerce")
            mask = adj.notna() & (adj > 0)
            df.loc[mask, "close"] = adj[mask]

        df = df.dropna(subset=["close"])

        if df.empty:
            raise HTTPException(
                status_code=404,
                detail=f"No valid price data for {ticker}",
            )

        return df[["open", "high", "low", "close", "volume"]]

    @staticmethod
    def _daily_to_weekly(df: pd.DataFrame, *, is_crypto: bool = False) -> pd.DataFrame:
        """Resample daily OHLCV into weekly bars.

        Stocks use W-FRI (week ending Friday — US market convention).
        Crypto uses W-SUN (week ending Sunday — 24/7 market, no sessions).
        """
        rule = "W-SUN" if is_crypto else "W-FRI"
        weekly = (
            df.resample(rule)
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna(subset=["close"])
        )
        return weekly

    # ── Indicator Computation & Signal Classification ──────────

    def _compute_timeframe_signal(
        self, df: pd.DataFrame
    ) -> Tuple[
        TechnicalIndicatorResult,
        float,
        List[MovingAverageIndicator],
        List[OscillatorIndicator],
    ]:
        """Compute all 18 indicators, classify signals, return result + lists."""
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        current_price = float(close.iloc[-1])

        # ── Moving Averages (10) ─────────────────────────────
        ma_configs: List[Tuple[str, Optional[float]]] = []
        for window in [10, 20, 50, 100, 200]:
            sma_val = _safe_float(
                ta_lib.trend.SMAIndicator(close, window=window).sma_indicator().iloc[-1]
            ) if len(df) >= window else None
            ma_configs.append((f"SMA({window})", sma_val))

        for window in [10, 20, 50, 100, 200]:
            ema_val = _safe_float(
                ta_lib.trend.EMAIndicator(close, window=window).ema_indicator().iloc[-1]
            ) if len(df) >= window else None
            ma_configs.append((f"EMA({window})", ema_val))

        ma_list: List[MovingAverageIndicator] = []
        for name, value in ma_configs:
            signal = self._classify_ma_signal(current_price, value)
            ma_list.append(
                MovingAverageIndicator(
                    name=name, value=_safe_round(value), signal=signal
                )
            )

        # ── Oscillators (8) ──────────────────────────────────
        # RSI
        rsi_val = _safe_float(
            ta_lib.momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
        ) if len(df) >= 15 else None

        # Stochastic
        stoch_k: Optional[float] = None
        if len(df) >= 14:
            stoch = ta_lib.momentum.StochasticOscillator(
                high, low, close, window=14, smooth_window=3
            )
            stoch_k = _safe_float(stoch.stoch().iloc[-1])

        # StochRSI
        stochrsi_k: Optional[float] = None
        if len(df) >= 28:
            stoch_rsi = ta_lib.momentum.StochRSIIndicator(
                close, window=14, smooth1=3, smooth2=3
            )
            stochrsi_k = _safe_float(stoch_rsi.stochrsi_k().iloc[-1])

        # MACD
        macd_line: Optional[float] = None
        macd_signal_val: Optional[float] = None
        if len(df) >= 35:
            macd_ind = ta_lib.trend.MACD(
                close, window_slow=26, window_fast=12, window_sign=9
            )
            macd_line = _safe_float(macd_ind.macd().iloc[-1])
            macd_signal_val = _safe_float(macd_ind.macd_signal().iloc[-1])

        # ADX
        adx_val: Optional[float] = None
        plus_di: Optional[float] = None
        minus_di: Optional[float] = None
        if len(df) >= 28:
            adx_ind = ta_lib.trend.ADXIndicator(high, low, close, window=14)
            adx_val = _safe_float(adx_ind.adx().iloc[-1])
            plus_di = _safe_float(adx_ind.adx_pos().iloc[-1])
            minus_di = _safe_float(adx_ind.adx_neg().iloc[-1])

        # Williams %R
        willr_val = _safe_float(
            ta_lib.momentum.WilliamsRIndicator(high, low, close, lbp=14)
            .williams_r()
            .iloc[-1]
        ) if len(df) >= 14 else None

        # CCI
        cci_val = _safe_float(
            ta_lib.trend.CCIIndicator(high, low, close, window=14).cci().iloc[-1]
        ) if len(df) >= 14 else None

        # ATR
        atr_val = _safe_float(
            ta_lib.volatility.AverageTrueRange(high, low, close, window=14)
            .average_true_range()
            .iloc[-1]
        ) if len(df) >= 14 else None

        osc_list: List[OscillatorIndicator] = [
            OscillatorIndicator(
                name="RSI(14)",
                value=_safe_round(rsi_val),
                signal=self._classify_rsi(rsi_val),
            ),
            OscillatorIndicator(
                name="Stoch(14,3)",
                value=_safe_round(stoch_k),
                signal=self._classify_stoch(stoch_k),
            ),
            OscillatorIndicator(
                name="StochRSI(14)",
                value=_safe_round(stochrsi_k),
                signal=self._classify_stochrsi(stochrsi_k),
            ),
            OscillatorIndicator(
                name="MACD(12,26)",
                value=_safe_round(macd_line),
                signal=self._classify_macd(macd_line, macd_signal_val),
            ),
            OscillatorIndicator(
                name="ADX(14)",
                value=_safe_round(adx_val),
                signal=self._classify_adx(adx_val, plus_di, minus_di),
            ),
            OscillatorIndicator(
                name="Williams %R",
                value=_safe_round(willr_val),
                signal=self._classify_williams(willr_val),
            ),
            OscillatorIndicator(
                name="CCI(14)",
                value=_safe_round(cci_val),
                signal=self._classify_cci(cci_val),
            ),
            OscillatorIndicator(
                name="ATR(14)",
                value=_safe_round(atr_val),
                signal=IndicatorSignal.NEUTRAL,  # ATR is non-directional
            ),
        ]

        # ── Gauge scoring ────────────────────────────────────
        # NET score: BUY pulls up, SELL pulls down, NEUTRAL sits at the 0.5 midpoint.
        # The old `buy_count / TOTAL_INDICATORS` counted ONLY buys, so an all-NEUTRAL
        # set (e.g. a freshly-listed ticker with <15 candles → every indicator None →
        # NEUTRAL) collapsed to gauge 0.0 → a fabricated "Strong Sell", and every
        # NEUTRAL indicator was silently scored as bearish (systematic bearish bias).
        all_signals = [m.signal for m in ma_list] + [o.signal for o in osc_list]
        buy_count = sum(1 for s in all_signals if s == IndicatorSignal.BUY)
        sell_count = sum(1 for s in all_signals if s == IndicatorSignal.SELL)
        neutral_count = TOTAL_INDICATORS - buy_count - sell_count
        gauge_value = min(
            1.0, max(0.0, 0.5 + (buy_count - sell_count) / (2 * TOTAL_INDICATORS))
        )
        signal = _gauge_to_signal(gauge_value)

        # "N of 18 indicators" should reflect the count AGREEING with the verdict,
        # not always the buy_count.
        if signal in (TechnicalSignal.BUY, TechnicalSignal.STRONG_BUY):
            matching = buy_count
        elif signal in (TechnicalSignal.SELL, TechnicalSignal.STRONG_SELL):
            matching = sell_count
        else:
            matching = neutral_count

        result = TechnicalIndicatorResult(
            signal=signal,
            matching_indicators=matching,
            total_indicators=TOTAL_INDICATORS,
        )
        return result, gauge_value, ma_list, osc_list

    # ── Signal classifiers ────────────────────────────────────

    @staticmethod
    def _classify_ma_signal(
        price: float, ma_val: Optional[float]
    ) -> IndicatorSignal:
        # `ma_val == 0` guard: _safe_float keeps a legitimate 0.0 (not None), and
        # `(price - 0) / 0` would raise ZeroDivisionError → 500 the whole response.
        if ma_val is None or ma_val == 0:
            return IndicatorSignal.NEUTRAL
        pct = (price - ma_val) / ma_val
        if pct > 0.005:
            return IndicatorSignal.BUY
        if pct < -0.005:
            return IndicatorSignal.SELL
        return IndicatorSignal.NEUTRAL

    @staticmethod
    def _classify_rsi(rsi: Optional[float]) -> IndicatorSignal:
        if rsi is None:
            return IndicatorSignal.NEUTRAL
        if rsi < 30:
            return IndicatorSignal.BUY
        if rsi > 70:
            return IndicatorSignal.SELL
        return IndicatorSignal.NEUTRAL

    @staticmethod
    def _classify_stoch(k: Optional[float]) -> IndicatorSignal:
        if k is None:
            return IndicatorSignal.NEUTRAL
        if k < 20:
            return IndicatorSignal.BUY
        if k > 80:
            return IndicatorSignal.SELL
        return IndicatorSignal.NEUTRAL

    @staticmethod
    def _classify_stochrsi(v: Optional[float]) -> IndicatorSignal:
        if v is None:
            return IndicatorSignal.NEUTRAL
        if v < 20:
            return IndicatorSignal.BUY
        if v > 80:
            return IndicatorSignal.SELL
        return IndicatorSignal.NEUTRAL

    @staticmethod
    def _classify_macd(
        macd_line: Optional[float], signal_line: Optional[float]
    ) -> IndicatorSignal:
        if macd_line is None or signal_line is None:
            return IndicatorSignal.NEUTRAL
        if macd_line > signal_line:
            return IndicatorSignal.BUY
        if macd_line < signal_line:
            return IndicatorSignal.SELL
        return IndicatorSignal.NEUTRAL

    @staticmethod
    def _classify_adx(
        adx: Optional[float],
        plus_di: Optional[float],
        minus_di: Optional[float],
    ) -> IndicatorSignal:
        if adx is None or plus_di is None or minus_di is None:
            return IndicatorSignal.NEUTRAL
        if adx > 25:
            if plus_di > minus_di:
                return IndicatorSignal.BUY
            if minus_di > plus_di:
                return IndicatorSignal.SELL
        return IndicatorSignal.NEUTRAL

    @staticmethod
    def _classify_williams(wr: Optional[float]) -> IndicatorSignal:
        if wr is None:
            return IndicatorSignal.NEUTRAL
        if wr < -80:
            return IndicatorSignal.BUY
        if wr > -20:
            return IndicatorSignal.SELL
        return IndicatorSignal.NEUTRAL

    @staticmethod
    def _classify_cci(cci: Optional[float]) -> IndicatorSignal:
        if cci is None:
            return IndicatorSignal.NEUTRAL
        if cci < -100:
            return IndicatorSignal.BUY
        if cci > 100:
            return IndicatorSignal.SELL
        return IndicatorSignal.NEUTRAL

    # ── Detail computations ───────────────────────────────────

    @staticmethod
    def _compute_pivot_points(df: pd.DataFrame) -> PivotPointsData:
        """Classic pivot points from the prior day's H/L/C."""
        prev = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
        # The df is dropna'd on `close` only (see _fetch_daily_ohlcv), so a bar can
        # carry a NaN high/low (pd.to_numeric coerced a missing/non-numeric field).
        # A raw float(NaN) here would poison every REQUIRED PivotPointLevel.value (and
        # the SupportResistanceLevel.value that reuses them) → allow_nan=False 500s the
        # whole detail sheet. Degrade to no pivot levels, mirroring the volume guard.
        h = _safe_float(prev["high"])
        l_ = _safe_float(prev["low"])
        c = _safe_float(prev["close"])
        if h is None or l_ is None or c is None:
            return PivotPointsData(method="Classic Method", levels=[])

        pivot = (h + l_ + c) / 3
        r1 = 2 * pivot - l_
        s1 = 2 * pivot - h
        r2 = pivot + (h - l_)
        s2 = pivot - (h - l_)
        r3 = h + 2 * (pivot - l_)
        s3 = l_ - 2 * (h - pivot)

        levels = [
            PivotPointLevel(name="R3", value=_round_price(r3), level_type=PivotLevelType.RESISTANCE),
            PivotPointLevel(name="R2", value=_round_price(r2), level_type=PivotLevelType.RESISTANCE),
            PivotPointLevel(name="R1", value=_round_price(r1), level_type=PivotLevelType.RESISTANCE),
            PivotPointLevel(name="Pivot", value=_round_price(pivot), level_type=PivotLevelType.PIVOT),
            PivotPointLevel(name="S1", value=_round_price(s1), level_type=PivotLevelType.SUPPORT),
            PivotPointLevel(name="S2", value=_round_price(s2), level_type=PivotLevelType.SUPPORT),
            PivotPointLevel(name="S3", value=_round_price(s3), level_type=PivotLevelType.SUPPORT),
        ]
        return PivotPointsData(method="Classic Method", levels=levels)

    @staticmethod
    def _compute_volume_analysis(df: pd.DataFrame) -> VolumeAnalysisData:
        """Volume metrics: current, change, 30d avg, trend, OBV, MFI."""
        # df is dropna'd on `close` only, so the latest bar can carry a NaN volume
        # (pd.to_numeric coerced a missing/non-numeric value). `NaN or 0` is NaN
        # (truthy), which would land in current_volume and serialize to an invalid
        # -JSON `NaN` that crashes the iOS decode. _safe_float degrades NaN→None→0.0,
        # matching every other indicator in this file.
        current_vol = _safe_float(df["volume"].iloc[-1]) or 0.0
        prev_vol = (_safe_float(df["volume"].iloc[-2]) or 0.0) if len(df) >= 2 else 0.0

        # A volume-less history (indices/commodities often have null volume, or a
        # brand-new listing) makes `.mean()` NaN → a raw float() lands NaN in the
        # REQUIRED avg_volume_30d → allow_nan=False 500s the detail sheet. Guard with
        # _safe_float like current_vol/obv/mfi already are.
        avg_30d = (
            (_safe_float(df["volume"].tail(30).mean()) if len(df) >= 30
             else _safe_float(df["volume"].mean())) or 0.0
        )
        vol_change = (
            ((current_vol - prev_vol) / prev_vol * 100) if prev_vol > 0 else 0.0
        )

        # Trend: 5d avg vs 20d avg (only feeds the trend comparison, but guard anyway)
        avg_5d = (_safe_float(df["volume"].tail(5).mean()) or 0.0) if len(df) >= 5 else current_vol
        avg_20d = (_safe_float(df["volume"].tail(20).mean()) or 0.0) if len(df) >= 20 else avg_30d

        if avg_5d > avg_20d * 1.1:
            trend = VolumeTrend.INCREASING
        elif avg_5d < avg_20d * 0.9:
            trend = VolumeTrend.DECREASING
        else:
            trend = VolumeTrend.STABLE

        # OBV
        obv_series = ta_lib.volume.OnBalanceVolumeIndicator(
            df["close"], df["volume"]
        ).on_balance_volume()
        obv_val = _safe_float(obv_series.iloc[-1]) or 0.0
        obv_normalized = obv_val / 1_000_000  # in millions

        # MFI
        mfi_val: float = 50.0
        if len(df) >= 14:
            mfi_series = ta_lib.volume.MFIIndicator(
                df["high"], df["low"], df["close"], df["volume"], window=14
            ).money_flow_index()
            mfi_val = _safe_float(mfi_series.iloc[-1]) or 50.0

        return VolumeAnalysisData(
            current_volume=round(current_vol, 0),
            current_volume_change=round(vol_change, 1),
            avg_volume_30d=round(avg_30d, 0),
            volume_trend=trend,
            obv=round(obv_normalized, 2),
            money_flow_index=round(mfi_val, 2),
        )

    @staticmethod
    def _compute_fibonacci(df: pd.DataFrame) -> FibonacciRetracementData:
        """Fibonacci retracement from 52-week high/low."""
        lookback = min(len(df), 252)
        window = df.tail(lookback)

        # high/low can be all-NaN (df is dropna'd on close only) → float(NaN) poisons
        # the REQUIRED FibonacciLevel.value → 500. Degrade to the last close so every
        # level stays finite (a flat, non-meaningful retracement rather than a crash).
        high = _safe_float(window["high"].max())
        low = _safe_float(window["low"].min())
        if high is None or low is None:
            fallback = _safe_float(df["close"].iloc[-1]) or 0.0
            high = low = fallback
        diff = high - low

        fib_ratios = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
        fib_labels = ["0.0%", "23.6%", "38.2%", "50.0%", "61.8%", "78.6%", "100.0%"]

        levels = [
            FibonacciLevel(
                percentage=label,
                value=_round_price(high - diff * ratio),
                is_key=(ratio in (0.0, 0.382, 0.5, 0.618, 1.0)),
            )
            for ratio, label in zip(fib_ratios, fib_labels)
        ]

        return FibonacciRetracementData(timeframe="52-Week Levels", levels=levels)

    def _compute_support_resistance(
        self, df: pd.DataFrame
    ) -> SupportResistanceData:
        """Derive S/R levels from pivot points."""
        current_price = float(df["close"].iloc[-1])
        pivot_data = self._compute_pivot_points(df)

        strength_map = {
            "R1": LevelStrength.WEAK,
            "R2": LevelStrength.MODERATE,
            "R3": LevelStrength.STRONG,
            "S1": LevelStrength.WEAK,
            "S2": LevelStrength.MODERATE,
            "S3": LevelStrength.STRONG,
        }

        resistance: List[SupportResistanceLevel] = []
        support: List[SupportResistanceLevel] = []

        for level in pivot_data.levels:
            strength = strength_map.get(level.name, LevelStrength.MODERATE)
            if level.level_type == PivotLevelType.RESISTANCE:
                resistance.append(
                    SupportResistanceLevel(
                        name=level.name, value=level.value, strength=strength
                    )
                )
            elif level.level_type == PivotLevelType.SUPPORT:
                support.append(
                    SupportResistanceLevel(
                        name=level.name, value=level.value, strength=strength
                    )
                )

        return SupportResistanceData(
            current_price=_round_price(current_price),
            resistance_levels=resistance,
            support_levels=support,
        )


# ── Singleton ────────────────────────────────────────────────────

_service: Optional[TechnicalAnalysisService] = None


def get_technical_analysis_service() -> TechnicalAnalysisService:
    global _service
    if _service is None:
        _service = TechnicalAnalysisService()
    return _service
