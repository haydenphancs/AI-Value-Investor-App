"""Chat schemas matching DB chat_sessions + chat_messages tables."""

from enum import Enum
from pydantic import BaseModel, ConfigDict, field_validator
from typing import Optional, List, Any, Dict

from app.config import settings


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

    # Structural hard ceilings (a cheap Pydantic 422 for absurd payloads BEFORE any
    # work). The friendly, contract-shaped 400 (CHAT_MESSAGE_TOO_LONG) is enforced in
    # the endpoint via chat_security.validate_message — a Pydantic 422 emits FastAPI's
    # default body, NOT the {error_code,...} shape iOS decodes, so both layers exist.
    @field_validator("message")
    @classmethod
    def _message_within_hard_max(cls, v: str) -> str:
        if v is not None and len(v) > settings.CHAT_MESSAGE_HARD_MAX:
            raise ValueError(
                f"message exceeds the {settings.CHAT_MESSAGE_HARD_MAX}-character hard limit"
            )
        return v

    @field_validator("context")
    @classmethod
    def _context_within_hard_max(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > settings.CHAT_MESSAGE_HARD_MAX:
            raise ValueError(
                f"context exceeds the {settings.CHAT_MESSAGE_HARD_MAX}-character hard limit"
            )
        return v


class UpdateChatSessionRequest(BaseModel):
    title: Optional[str] = None
    is_saved: Optional[bool] = None


# ── Rich Media Widget Schemas ───────────────────────────────────────

# A non-finite float is not merely a bad number here — `json.dumps` emits the bare tokens
# `NaN` / `Infinity`, which are invalid JSON (the iOS decoder rejects the WHOLE message) and
# which Postgres JSONB refuses outright, losing the persisted turn. `allow_inf_nan=False` makes
# Pydantic reject it at the boundary instead, as a last line behind the service-layer filtering
# in `ChatService._normalize_historical` / `_build_stock_widget`.
_NO_INF_NAN = ConfigDict(allow_inf_nan=False)


class HistoricalDataPoint(BaseModel):
    """Single day of OHLCV price data for chart rendering."""
    model_config = _NO_INF_NAN

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class StockChartWidget(BaseModel):
    """Structured payload the frontend uses to render a native stock chart."""
    model_config = _NO_INF_NAN

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
    # None = unknown (old builds/data); True = US session open → the card shows a green "Live"
    # dot, otherwise "Closed". Optional so old iOS builds ignore it (subset parity holds).
    is_market_open: Optional[bool] = None
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

class ChatTurnCost(BaseModel):
    """What one chat turn actually cost the user.

    Chat is a flat 1 credit and always will be, so the interesting cases are the ones
    where it cost LESS — a follow-up covered by the previous turn, or a credit handed
    back because the answer was degraded or came from a zero-cost cache hit. Those are
    the only ones iOS renders; a normal charge shows nothing, because stamping a price
    on every answer turns the chat into a meter and suppresses the asking this product
    depends on.

    Persisted into `chat_messages.rich_content["credit"]` (no migration — the same trick
    `thinking`/`sources`/`suggestions` already use), so the chip survives a history
    reload. `balance` is deliberately NOT persisted and lives only on the live SSE frame:
    a spendable balance is an ACCOUNT fact, and replaying "42 credits left" on a
    three-day-old message would be showing the user a number that was true once.
    """

    outcome: str                       # "charged" | "free_followup" | "refunded" | "guest"
    credits: int = 0                   # credits actually retained for this turn
    reason: Optional[str] = None       # machine-readable: chat_cache_hit, chat_degraded_unmerged, …
    label: Optional[str] = None        # server-authored display string (ships without an App Store release)


class ChatMessageResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    widget: Optional[Any] = None  # single widget (back-compat); StockChartWidget/MarketOverviewWidget
    widgets: Optional[List[Any]] = None  # Phase 2: multi-widget list (chart + comparison charts, …)
    rich_content: Optional[Any] = None
    citations: Optional[List[Any]] = None
    tokens_used: Optional[int] = None
    # Futuristic-chat additions (all Optional → legacy rows + old iOS builds stay valid):
    #  • sources     — grounded-context "source" pills shown in the thinking card
    #  • suggestions — 1-2 AI-generated follow-up questions to show after the answer
    #  • thinking    — {stages: [str], source_count: int, elapsed_ms: int} for the "Done in Xs · N sources" card
    #  • credit      — what this turn cost (see ChatTurnCost). Absent on every legacy row
    #                   and on any turn charged normally; present only when there is GOOD
    #                   news to show, so iOS renders a chip for a free or refunded turn
    #                   and nothing at all otherwise.
    sources: Optional[List[Any]] = None
    suggestions: Optional[List[str]] = None
    thinking: Optional[Dict[str, Any]] = None
    credit: Optional[Dict[str, Any]] = None
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
