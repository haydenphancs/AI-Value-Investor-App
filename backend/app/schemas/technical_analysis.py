"""
Technical Analysis schemas — response models for:
  GET /stocks/{ticker}/technical-analysis
  GET /stocks/{ticker}/technical-analysis/detail

All field names use snake_case. The Swift frontend decodes via Codable DTO
structs with explicit CodingKeys mapping to these snake_case names.
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


# ── Enums ─────────────────────────────────────────────────────────

class TechnicalSignal(str, Enum):
    STRONG_SELL = "Strong Sell"
    SELL = "Sell"
    HOLD = "Hold"
    BUY = "Buy"
    STRONG_BUY = "Strong Buy"


class IndicatorSignal(str, Enum):
    BUY = "Buy"
    SELL = "Sell"
    NEUTRAL = "Neutral"


class VolumeTrend(str, Enum):
    INCREASING = "Increasing"
    DECREASING = "Decreasing"
    STABLE = "Stable"


class LevelStrength(str, Enum):
    STRONG = "Strong"
    MODERATE = "Moderate"
    WEAK = "Weak"


class PivotLevelType(str, Enum):
    RESISTANCE = "resistance"
    PIVOT = "pivot"
    SUPPORT = "support"


# ── Gauge endpoint models ─────────────────────────────────────────

class TechnicalIndicatorResult(BaseModel):
    signal: TechnicalSignal
    matching_indicators: int
    total_indicators: int


class TechnicalAnalysisResponse(BaseModel):
    """Top-level response for GET /stocks/{ticker}/technical-analysis."""

    symbol: str
    daily_signal: TechnicalIndicatorResult
    weekly_signal: TechnicalIndicatorResult
    overall_signal: TechnicalSignal
    gauge_value: float  # 0.0 to 1.0


# ── Detail endpoint models ────────────────────────────────────────

class MovingAverageIndicator(BaseModel):
    name: str
    value: Optional[float] = None
    signal: IndicatorSignal


class OscillatorIndicator(BaseModel):
    name: str
    value: Optional[float] = None
    signal: IndicatorSignal


class IndicatorSummary(BaseModel):
    buy_count: int
    neutral_count: int
    sell_count: int


class PivotPointLevel(BaseModel):
    name: str
    value: float
    level_type: PivotLevelType


class PivotPointsData(BaseModel):
    method: str
    levels: List[PivotPointLevel]


class VolumeAnalysisData(BaseModel):
    current_volume: float
    current_volume_change: float
    avg_volume_30d: float
    volume_trend: VolumeTrend
    obv: float
    money_flow_index: float


class FibonacciLevel(BaseModel):
    percentage: str
    value: float
    is_key: bool


class FibonacciRetracementData(BaseModel):
    timeframe: str
    levels: List[FibonacciLevel]


class SupportResistanceLevel(BaseModel):
    name: str
    value: float
    strength: LevelStrength


class SupportResistanceData(BaseModel):
    current_price: float
    resistance_levels: List[SupportResistanceLevel]
    support_levels: List[SupportResistanceLevel]


class TechnicalAnalysisDetailResponse(BaseModel):
    """Top-level response for GET /stocks/{ticker}/technical-analysis/detail."""

    symbol: str
    moving_averages: List[MovingAverageIndicator]
    moving_averages_summary: IndicatorSummary
    oscillators: List[OscillatorIndicator]
    oscillators_summary: IndicatorSummary
    pivot_points: PivotPointsData
    volume_analysis: VolumeAnalysisData
    fibonacci_retracement: FibonacciRetracementData
    support_resistance: SupportResistanceData
