"""
Technical Analysis Service — computes 18 technical indicators on daily and
weekly OHLCV data, produces a gauge score and full detail breakdown.

Uses the ``ta`` library (Technical Analysis Library in Python) for indicator
computation.  Follows the same service patterns as sentiment_service.py:
stateless class, in-memory TTL cache, singleton getter.
"""

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

logger = logging.getLogger(__name__)

# ── In-memory cache ──────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 43_200  # 12 hours in seconds

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


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


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
class TechnicalAnalysisService:
    """Stateless service for technical indicator computation."""

    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()

    # ── Public API ─────────────────────────────────────────────

    async def get_analysis(self, ticker: str) -> TechnicalAnalysisResponse:
        """Gauge endpoint: daily + weekly signals, overall gauge value."""
        ticker = ticker.upper()

        cached = _cache_get(f"ta:{ticker}")
        if cached is not None:
            return cached

        df_daily = await self._fetch_daily_ohlcv(ticker)
        df_weekly = self._daily_to_weekly(df_daily)

        daily_result, _, _ = self._compute_timeframe_signal(df_daily)
        weekly_result, _, _ = self._compute_timeframe_signal(df_weekly)

        # Overall gauge: average of daily and weekly buy ratios
        daily_ratio = daily_result.matching_indicators / TOTAL_INDICATORS
        weekly_ratio = weekly_result.matching_indicators / TOTAL_INDICATORS
        overall_gauge = (daily_ratio + weekly_ratio) / 2.0

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

        cached = _cache_get(f"ta_detail:{ticker}")
        if cached is not None:
            return cached

        df_daily = await self._fetch_daily_ohlcv(ticker)

        _, ma_list, osc_list = self._compute_timeframe_signal(df_daily)

        response = TechnicalAnalysisDetailResponse(
            symbol=ticker,
            moving_averages=ma_list,
            moving_averages_summary=_count_summary(ma_list),
            oscillators=osc_list,
            oscillators_summary=_count_summary(osc_list),
            pivot_points=self._compute_pivot_points(df_daily),
            volume_analysis=self._compute_volume_analysis(df_daily),
            fibonacci_retracement=self._compute_fibonacci(df_daily),
            support_resistance=self._compute_support_resistance(df_daily),
        )

        _cache_set(f"ta_detail:{ticker}", response)
        return response

    # ── Data Fetching ──────────────────────────────────────────

    async def _fetch_daily_ohlcv(self, ticker: str) -> pd.DataFrame:
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
        historical.sort(key=lambda p: p.get("date", ""))

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
    def _daily_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
        """Resample daily OHLCV into weekly bars (week ending Friday)."""
        weekly = (
            df.resample("W-FRI")
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
        all_signals = [m.signal for m in ma_list] + [o.signal for o in osc_list]
        buy_count = sum(1 for s in all_signals if s == IndicatorSignal.BUY)
        gauge_value = buy_count / TOTAL_INDICATORS

        result = TechnicalIndicatorResult(
            signal=_gauge_to_signal(gauge_value),
            matching_indicators=buy_count,
            total_indicators=TOTAL_INDICATORS,
        )
        return result, ma_list, osc_list

    # ── Signal classifiers ────────────────────────────────────

    @staticmethod
    def _classify_ma_signal(
        price: float, ma_val: Optional[float]
    ) -> IndicatorSignal:
        if ma_val is None:
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
        h = float(prev["high"])
        l_ = float(prev["low"])
        c = float(prev["close"])

        pivot = (h + l_ + c) / 3
        r1 = 2 * pivot - l_
        s1 = 2 * pivot - h
        r2 = pivot + (h - l_)
        s2 = pivot - (h - l_)
        r3 = h + 2 * (pivot - l_)
        s3 = l_ - 2 * (h - pivot)

        levels = [
            PivotPointLevel(name="R3", value=round(r3, 2), level_type=PivotLevelType.RESISTANCE),
            PivotPointLevel(name="R2", value=round(r2, 2), level_type=PivotLevelType.RESISTANCE),
            PivotPointLevel(name="R1", value=round(r1, 2), level_type=PivotLevelType.RESISTANCE),
            PivotPointLevel(name="Pivot", value=round(pivot, 2), level_type=PivotLevelType.PIVOT),
            PivotPointLevel(name="S1", value=round(s1, 2), level_type=PivotLevelType.SUPPORT),
            PivotPointLevel(name="S2", value=round(s2, 2), level_type=PivotLevelType.SUPPORT),
            PivotPointLevel(name="S3", value=round(s3, 2), level_type=PivotLevelType.SUPPORT),
        ]
        return PivotPointsData(method="Classic Method", levels=levels)

    @staticmethod
    def _compute_volume_analysis(df: pd.DataFrame) -> VolumeAnalysisData:
        """Volume metrics: current, change, 30d avg, trend, OBV, MFI."""
        current_vol = float(df["volume"].iloc[-1] or 0)
        prev_vol = float(df["volume"].iloc[-2] or 0) if len(df) >= 2 else 0.0

        avg_30d = (
            float(df["volume"].tail(30).mean())
            if len(df) >= 30
            else float(df["volume"].mean())
        )
        vol_change = (
            ((current_vol - prev_vol) / prev_vol * 100) if prev_vol > 0 else 0.0
        )

        # Trend: 5d avg vs 20d avg
        avg_5d = float(df["volume"].tail(5).mean()) if len(df) >= 5 else current_vol
        avg_20d = float(df["volume"].tail(20).mean()) if len(df) >= 20 else avg_30d

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

        high = float(window["high"].max())
        low = float(window["low"].min())
        diff = high - low

        fib_ratios = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
        fib_labels = ["0.0%", "23.6%", "38.2%", "50.0%", "61.8%", "78.6%", "100.0%"]

        levels = [
            FibonacciLevel(
                percentage=label,
                value=round(high - diff * ratio, 2),
                is_key=(ratio == 0.0 or ratio == 1.0),
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
            current_price=round(current_price, 2),
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
