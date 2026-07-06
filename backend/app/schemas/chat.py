"""Chat schemas matching DB chat_sessions + chat_messages tables."""

from enum import Enum
from pydantic import BaseModel
from typing import Optional, List, Any, Dict


# ── Context types ───────────────────────────────────────────────────
#
# The screen the user is asking from. iOS sends {context_type, reference_id}
# instead of a big raw context string; the backend's ChatContextResolver
# fetches the already-cached data for that screen and injects a compact
# grounding block into the Gemini prompt. Carried as plain `Optional[str]`
# in the request/response models (NOT the enum) so an unknown value from an
# older client degrades gracefully instead of 422-ing — the resolver
# normalizes and validates internally.

class ChatContextType(str, Enum):
    TICKER_REPORT = "TICKER_REPORT"
    STOCK = "STOCK"
    ETF = "ETF"
    CRYPTO = "CRYPTO"
    INDEX = "INDEX"
    COMMODITY = "COMMODITY"
    MONEY_MOVES_ARTICLE = "MONEY_MOVES_ARTICLE"
    JOURNEY_LESSON = "JOURNEY_LESSON"
    BOOK = "BOOK"
    NONE = "NONE"


# ── Request Schemas ─────────────────────────────────────────────────

class CreateChatSessionRequest(BaseModel):
    stock_id: Optional[str] = None  # ticker symbol, optional (back-compat)
    context_type: Optional[str] = None  # ChatContextType value (screen the user asks from)
    reference_id: Optional[str] = None  # e.g. ticker, "TICKER|persona", article slug, book order


class SendChatMessageRequest(BaseModel):
    message: str
    context: Optional[str] = None  # legacy/BOOK client context string (title/author/core)
    context_type: Optional[str] = None  # per-message override of the session's context type
    reference_id: Optional[str] = None  # per-message override of the session's reference id


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


class MarketOverviewSector(BaseModel):
    """Sector entry for the market overview widget."""
    sector: str
    change_percent: float


class MarketOverviewMacroItem(BaseModel):
    """Macro indicator for the market overview widget."""
    title: str
    signal: str  # "positive", "neutral", "cautious"


class MarketOverviewWidget(BaseModel):
    """Structured payload the frontend uses to render a market overview card."""
    widget_type: str = "market_overview"
    pe_ratio: float
    forward_pe: float
    valuation_level: str  # "Bargain", "Fair Value", "Expensive", "Overheated"
    earnings_yield: float
    historical_avg_pe: float
    sectors: List[MarketOverviewSector] = []
    advancing: int = 0
    declining: int = 0
    macro_indicators: List[MarketOverviewMacroItem] = []


# ── Chat Message / Session Response Schemas ─────────────────────────

class ChatMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    widget: Optional[Any] = None  # StockChartWidget or MarketOverviewWidget (discriminated by widget_type)
    rich_content: Optional[Any] = None
    citations: Optional[List[Any]] = None
    tokens_used: Optional[int] = None
    created_at: str


class ChatSessionResponse(BaseModel):
    id: str
    title: Optional[str] = None
    session_type: Optional[str] = "NORMAL"
    stock_id: Optional[str] = None
    context_type: Optional[str] = None  # screen the chat is grounded on (re-grounds on history reload)
    reference_id: Optional[str] = None  # ticker / "TICKER|persona" / slug / book order
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
