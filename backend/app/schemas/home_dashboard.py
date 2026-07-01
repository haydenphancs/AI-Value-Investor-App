"""
Caydex Home dashboard schemas — aggregated response for the redesigned
`HomeDashboardView` (NOT the legacy `HomeView`, which uses `schemas/home.py`).

Single-response design: one `GET /home/dashboard` call returns everything the
dashboard renders, built top-to-bottom. Today that is the market-status header
plus the "Market Pulse" strip; scanners / signals / themes will be added to
this same response later.

Field names are snake_case. The Swift frontend decodes via explicit
`CodingKeys` (the iOS `APIClient` deliberately does NOT use
`.convertFromSnakeCase`), so the wire contract is the literal snake_case below.

Values are RAW numbers — the iOS repository formats them into the display
strings the views consume (price text, signed percent, green/red), mirroring
how the mock repository built those strings.
"""

from pydantic import BaseModel
from typing import List, Optional


class MarketPulseItemResponse(BaseModel):
    """One tile in the Market Pulse strip (an index, crypto, or commodity)."""

    symbol: str                 # e.g. "^GSPC", "BTCUSD", "GCUSD"
    name: str                   # display name, e.g. "S&P 500"
    type: str                   # "index" | "crypto" | "commodity" | "stock" | "etf"
    price: float                # latest quote price (raw)
    change_percent: float       # today's % change (raw; iOS derives sign/colour)
    # Prior trading day's close — the iOS sparkline draws a dashed reference line
    # here and colours the line green ABOVE / red BELOW it (same as the Holdings
    # cards). Null when FMP didn't return a previous close.
    previous_close: Optional[float] = None
    # Latest-session INTRADAY closes, OLDEST-first (ascending in time), so the
    # dashed previous-close reference is meaningful (mirrors the holdings-card
    # 1D sparkline). May be empty if the session series was unavailable.
    spark: List[float]


# ── Daily Scanners ─────────────────────────────────────────────────────
# Three ranked-leaderboard cards: Top Movers (gainers/losers), Heavy Traffic
# (relative volume), Skeptical Money (short interest). The backend sends only
# the ranked DATA rows + raw numbers; the iOS repository supplies the fixed
# per-card chrome (title/icon/accent/badge/CTA) and formats the strings.


class ScannerRowResponse(BaseModel):
    """One ranked row in a scanner leaderboard (raw numbers; iOS formats)."""

    rank: int                                       # 1-based, assigned by the service
    symbol: str
    name: str
    price: float
    change_percent: float                           # signed; movers=primary, volume=secondary
    market_cap: Optional[float] = None              # raw; iOS shows "$x · 45.2B Cap" next to price
    volume_multiple: Optional[float] = None         # RVOL (volume/avg), Heavy Traffic only
    short_percent_of_float: Optional[float] = None  # Skeptical Money only
    spark: List[float] = []                         # latest-session intraday; rank-1 only, else []


class ScannerGroupResponse(BaseModel):
    """One scanner card. Movers uses gainers+losers (toggle); the others use entries."""

    kind: str                                       # "movers" | "volume" | "shorts"
    gainers: List[ScannerRowResponse] = []          # movers only
    losers: List[ScannerRowResponse] = []           # movers only
    entries: List[ScannerRowResponse] = []          # volume / shorts
    # Card-level "as of" date (ISO YYYY-MM-DD) — SHORTS only. Short interest is a
    # bi-monthly FINRA settlement figure, not a live daily number; iOS renders this
    # as the "As of Jun 15" subtitle so the cadence is honest. Null for
    # movers/volume (and for shorts when no shown row carries a settlement date).
    as_of_date: Optional[str] = None


class ScannerGroupsResponse(BaseModel):
    """The three Daily Scanner cards. A null group → iOS omits that card."""

    movers: Optional[ScannerGroupResponse] = None
    volume: Optional[ScannerGroupResponse] = None
    shorts: Optional[ScannerGroupResponse] = None


# ── App-Exclusive Signals ──────────────────────────────────────────────
# Three "signals you won't find on free trackers" cards: Congressional Buys
# (most-bought on Capitol Hill), Whale Accumulation (13F funds adding), and
# Earnings Shockers (biggest beats/misses). Same contract as the scanners: the
# backend sends only the ranked DATA rows + raw numbers; the iOS repository
# supplies the fixed per-card chrome (title/icon/accent/subtitle) and formats
# the display strings.


class SignalRowResponse(BaseModel):
    """One ranked leader row in a signal card (raw numbers; iOS formats).

    ``value`` is polymorphic by the enclosing group's ``kind`` (a single wire
    field keeps the contract simple — the card already dispatches on kind):
      • congress → distinct-member count (how many members bought this ticker)
      • whale    → distinct-fund count (how many 13F funds are adding; deduped by CIK)
      • earnings → SIGNED EPS surprise % (beat is +, miss is −)
    """

    rank: int                       # 1-based, assigned by the service
    symbol: str
    name: str = ""                  # company/display name; "" when the source lacks it
    value: float                    # meaning depends on kind (see class docstring)


class SignalGroupResponse(BaseModel):
    """One signal card: ranked drill-down entries. The headline (top ticker +
    its stat) is ``entries[0]`` — iOS derives it, mirroring ScannerGroupResponse."""

    kind: str                                   # "congress" | "whale" | "earnings"
    entries: List[SignalRowResponse] = []
    # Card-level "as of" context (honest cadence — these sources are NOT live):
    #   • congress → latest DISCLOSURE date in the window (filings lag 30–45d)
    #   • whale    → whales.last_hydrated_at (quarterly 13F basis)
    #   • earnings → latest report date in the window
    as_of_date: Optional[str] = None


class SignalsGroupResponse(BaseModel):
    """The three App-Exclusive Signal cards. A null group → iOS omits that card."""

    congress: Optional[SignalGroupResponse] = None
    whale: Optional[SignalGroupResponse] = None
    earnings: Optional[SignalGroupResponse] = None


class HomeDashboardResponse(BaseModel):
    """Top-level aggregated payload for the Caydex Home dashboard.

    Grows top-to-bottom: the market-status header, Market Pulse strip, and Daily
    Scanners are populated today. The iOS repository fills the not-yet-served
    sections (signals / themes) with empty arrays, which render nothing.
    """

    market_status_text: str     # "Markets Open" | "Markets Closed" | "Pre-Market" | "After Hours"
    market_is_open: bool
    pulse: List[MarketPulseItemResponse]
    # Additive + defaulted so old clients ignore it and a failed scanner build
    # still returns a valid response with all-null groups.
    scanners: ScannerGroupsResponse = ScannerGroupsResponse()
    # Additive + defaulted (same rationale): a failed signals build degrades to
    # all-null groups; old clients ignore the field.
    signals: SignalsGroupResponse = SignalsGroupResponse()
