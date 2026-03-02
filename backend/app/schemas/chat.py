"""Chat schemas matching DB chat_sessions + chat_messages tables."""

from pydantic import BaseModel
from typing import Optional, List, Any


# ── Request Schemas ─────────────────────────────────────────────────

class CreateChatSessionRequest(BaseModel):
    stock_id: Optional[str] = None  # ticker symbol, optional


class SendChatMessageRequest(BaseModel):
    message: str


class UpdateChatSessionRequest(BaseModel):
    title: Optional[str] = None
    is_saved: Optional[bool] = None


# ── Rich Media Widget Schemas ───────────────────────────────────────

class HistoricalDataPoint(BaseModel):
    """Single day of OHLCV price data for chart rendering."""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class StockChartWidget(BaseModel):
    """Structured payload the frontend uses to render a native stock chart."""
    widget_type: str = "stock_chart"
    ticker: str
    company_name: str
    current_price: float
    change: float
    change_percent: float
    day_high: float
    day_low: float
    volume: int
    avg_volume: int
    market_cap: Optional[float] = None
    pe_ratio: Optional[float] = None
    year_high: Optional[float] = None
    year_low: Optional[float] = None
    historical_data: List[HistoricalDataPoint] = []


# ── Chat Message / Session Response Schemas ─────────────────────────

class ChatMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    widget: Optional[StockChartWidget] = None
    rich_content: Optional[Any] = None
    citations: Optional[List[Any]] = None
    tokens_used: Optional[int] = None
    created_at: str


class ChatSessionResponse(BaseModel):
    id: str
    title: Optional[str] = None
    session_type: Optional[str] = "NORMAL"
    stock_id: Optional[str] = None
    preview_message: Optional[str] = None
    message_count: int = 0
    is_saved: bool = False
    created_at: str
    last_message_at: Optional[str] = None


class ChatSessionListResponse(BaseModel):
    """Response for GET /chat/sessions — list of user sessions."""
    sessions: List[ChatSessionResponse]
    total: int


class ChatHistoryResponse(BaseModel):
    """Response for GET /chat/sessions/{id} — session + messages."""
    session: ChatSessionResponse
    messages: List[ChatMessageResponse]
