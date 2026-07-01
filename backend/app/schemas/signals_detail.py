"""
Per-ticker drill-down for the Home "App-Exclusive Signals" cards.

Backs the iOS `SignalTickerDetailView` reached by tapping a ticker in the Whale
Accumulation or Congressional Buys cards: WHO bought/added the ticker, WHEN, and
HOW MUCH — with each holder deep-linkable to their profile when they're in our
whale registry.

Field names are snake_case; the Swift frontend decodes via explicit `CodingKeys`
(the iOS `APIClient` deliberately does NOT use `.convertFromSnakeCase`). Values
are RAW — iOS formats them into display strings. Most fields are Optional and
polymorphic by `kind` ("whale" vs "congress"), mirroring how the signals card's
`SignalRowResponse.value` is polymorphic.
"""

from pydantic import BaseModel
from typing import List, Optional


class SignalHolderResponse(BaseModel):
    """One holder row: a 13F fund (whale) or a congress member behind the ticker."""

    # Registry id → iOS deep-links to WhaleProfileView. None = plain (non-tappable) row.
    # WHALE rows always carry it (the list IS our registry); CONGRESS rows carry it
    # only for the ~8 politicians we track.
    whale_id: Optional[str] = None
    name: str                              # fund or member name
    subtitle: str = ""                     # whale: "13F fund" · congress: role "Senator (KY)"
    transaction_date: Optional[str] = None  # ISO; congress = traded date
    disclosure_date: Optional[str] = None   # ISO; congress = filed date; whale = filing date

    # ── whale (13F): allocation headline + $ estimate ──
    allocation_percent: Optional[float] = None   # portfolio weight (%)
    allocation_change: Optional[float] = None    # QoQ change in allocation points
    is_new_position: Optional[bool] = None        # brand-new position this filing
    amount_est: Optional[float] = None            # implied-price $ estimate (13F only; NOT exact)

    # ── congress: filed range (never a synthetic midpoint) ──
    amount_range: Optional[str] = None            # "$1,001 – $15,000" (as filed)
    owner: Optional[str] = None                   # "Self" | "Spouse" | "Joint"

    action: str = "BOUGHT"                         # BOUGHT (both cards are buy-side)


class SignalTickerDetailResponse(BaseModel):
    """The per-ticker detail payload. `holders` is ranked; an empty list → iOS
    shows an honest empty state (never fabricated)."""

    symbol: str
    kind: str                              # "whale" | "congress"
    company_name: str = ""                 # header
    price: Optional[float] = None          # header (may be null if the profile fetch failed)
    market_cap: Optional[float] = None     # header
    as_of_date: Optional[str] = None       # whale: latest filing/hydration · congress: latest disclosure
    holders: List[SignalHolderResponse] = []
