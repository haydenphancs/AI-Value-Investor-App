"""
Pydantic schemas for the iOS home-screen widget (`CaydexWidgets`).

Same wire contract as `schemas/updates.py` — read that file's header first. In
short: ISO-8601 strings with **no fractional seconds**, and field names stay
snake_case because `APIClient` deliberately does not use `.convertFromSnakeCase`.

WHY A SEPARATE SCHEMA RATHER THAN REUSING `AIInsightCardResponse`
-----------------------------------------------------------------
The card answers "what is the news on this scope". The widget answers a different
question — "which of these tickers moved most, and why" — and the difference is
load-bearing in one specific way: **provenance**.

The card carries a `headline` (a free, always-generated news roll-up) and an
optional `price_move` (a web-grounded, cited causal explanation). Those are not
interchangeable, and a widget is exactly the surface where conflating them does
damage: 30 characters under a red −4.8%, a news summary *reads* as the cause even
when nothing established one. Measured 2026-08-14: every one of the 12 live cards
had a headline and **none** had a `price_move`, so a naive implementation would
have shipped 100% roll-ups presented as reasons.

So the reason is a tagged union on `kind`, and the tag is not cosmetic — iOS
renders a "why it moved" treatment only for `catalyst`. See `WidgetReasonKind`.

CONTRACT NOTES
--------------
* `change_percent` is Optional and iOS hides the number when it is None. Never
  substitute 0.0 — a fabricated flat reading on a stock that actually moved is
  worse than no reading.
* `z` is the continuous volatility-relative magnitude (`updates_materiality.move_z`),
  not the 4-bucket tier. Sent so the client can render "about 1.1× its normal day"
  without carrying its own σ.
* `as_of` is **required and must be rendered**. The insight sweeper sleeps
  20:00-04:00 ET and all weekend, so a Saturday widget legitimately shows Friday's
  close. Labelling that is honest; hiding it makes the widget look broken.
"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.updates import SourceRefResponse


class WidgetReasonKind(str, Enum):
    """Provenance of the reason text. Never collapse these.

    * ``catalyst`` — the web-grounded, source-cited `price_move.reason`. The only
      kind that may be presented as *why* the move happened.
    * ``context`` — the insight card's news headline. Says what is going on around
      the ticker; establishes no causal link. Rendered without the "why it moved"
      framing.
    * ``none`` — a deterministic sentence built from the numbers we already have
      ("−4.8%, about 1.1× its normal daily range"). Always available, never wrong,
      and the reason a quiet-news mover still renders something truthful.
    """

    CATALYST = "catalyst"
    CONTEXT = "context"
    NONE = "none"


class WidgetReasonResponse(BaseModel):
    """The line under the ticker, plus what it is allowed to claim."""

    kind: WidgetReasonKind
    text: str
    # 2-4 word label ("Earnings Beat"). Only ever set when kind == catalyst.
    catalyst_tag: Optional[str] = None
    # Cited sources. Only ever non-empty when kind == catalyst — a `context`
    # reason must not borrow the card's article list and thereby look sourced.
    sources: List[SourceRefResponse] = Field(default_factory=list)


class WidgetMoverResponse(BaseModel):
    """The single ticker the widget leads with."""

    ticker: str
    company_name: Optional[str] = None
    # Session change, PERCENT (e.g. -4.81). None ⇒ iOS hides the number.
    change_percent: Optional[float] = None
    price: Optional[float] = None
    # 'Typical' | 'Notable' | 'Unusual' | 'Extreme', or a fixed-band label when σ
    # is unavailable. None when the move could not be classified at all.
    tier: Optional[str] = None
    # Continuous |move| / σ_daily. None when σ is unknown — NOT 0.0, which would
    # read as "perfectly normal" for a ticker we simply cannot judge.
    z: Optional[float] = None
    reason: WidgetReasonResponse


class WidgetBasketResponse(BaseModel):
    """Set when several holdings moved together — the correlated-move case.

    Deliberately describes the **factor**, not the set. "Your holdings fell" is
    not an explanation; "your Technology holdings fell together" is, and it is
    also the version that is true for every user holding tech, which is what
    makes it cacheable and cheap.
    """

    # 'up' | 'down'. A mixed-direction day produces no basket at all.
    direction: str
    moved_count: int
    total_count: int
    # 'sector' | 'market'. None when the movers share no identifiable factor —
    # in that case the text says so rather than inventing one.
    factor_kind: Optional[str] = None
    # Human label for the factor ('Technology'). None mirrors factor_kind.
    factor_label: Optional[str] = None
    average_change_percent: Optional[float] = None
    tickers: List[str] = Field(default_factory=list)
    # The rendered sentence. Deterministic today (no LLM), so it is always
    # available and can never contradict the numbers beside it.
    text: str


class WidgetMoverPayload(BaseModel):
    """What one widget timeline entry renders."""

    # 'market' | 'portfolio'.
    mode: str
    # ISO-8601 UTC, no fractional seconds. REQUIRED — see the module header.
    as_of: str
    # 'premarket' | 'regular' | 'afterhours' | 'closed', from `market_hours.session_phase`.
    # Lets the widget say "at Friday's close" instead of implying live data.
    market_session: str
    # True when the data predates the current session — the widget shows a muted
    # timestamp rather than pretending to be live.
    is_stale: bool = False

    # None on a genuinely flat day, or when a portfolio is empty. iOS falls back
    # to `market_story`; it must never render an empty card.
    headline_mover: Optional[WidgetMoverResponse] = None
    # Portfolio mode only, and only when the correlated-move test passes.
    basket: Optional[WidgetBasketResponse] = None
    # The market-level line ('Mixed Signals Cloud US Market Outlook'). Always
    # populated for market mode so the fallback path can never be blank.
    market_story: Optional[str] = None

    # Honest framing for what was ranked. Market mode ranks inside the universe
    # Caydex actually tracks — those are the only tickers with a σ, a card and a
    # possible catalyst — so the widget says so rather than implying it scanned
    # the whole market.
    universe_label: Optional[str] = None
