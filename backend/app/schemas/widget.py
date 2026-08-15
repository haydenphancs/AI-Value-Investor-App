"""
Pydantic schemas for the iOS home-screen widget (`CaydexWidgets`).

Same wire contract as `schemas/updates.py` — read that file's header first. In
short: ISO-8601 strings with **no fractional seconds**, and field names stay
snake_case because `APIClient` deliberately does not use `.convertFromSnakeCase`.

WHY A SEPARATE SCHEMA RATHER THAN REUSING `AIInsightCardResponse`
-----------------------------------------------------------------
The card answers "what is the news on this scope, over the last 24-48 hours". The
widget answers a strictly DAILY question — "which ticker moved most TODAY, and why" —
and the window is the whole difference.

An earlier build served the card's news `headline` as the reason. Measured against the
live corpus, that shipped lines like *"Archer Aviation explores new markets and
strategic growth"* under a red −5.02%: a generic PR headline that explains nothing,
reading as a cause purely by adjacency. The grounded catalyst was no better — the only
cached ACHR row described a **+42.7% fifteen-day rally**, a correct answer to a
different question.

So the payload now carries two separate things instead of one ambiguous string:
`cause` (established from dated, structured data — earnings today, an analyst action
today, a classified headline, an industry move) and `context` (pure arithmetic — the σ
multiple, the overnight gap split, the industry delta). `cause.kind == "none"` is a
real, common, useful answer, not a failure.

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

from typing import List, Optional

from pydantic import BaseModel, Field



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
    # Why it moved today. Always present; `cause.kind == "none"` is a real answer.
    cause: "WidgetCauseResponse"
    # The arithmetic beside it — σ multiple, gap split, industry delta.
    context: "WidgetMoveContextResponse"


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


class WidgetCauseResponse(BaseModel):
    """Why the stock moved TODAY — established from structured data, not generated.

    Produced by `daily_move_attribution`, which checks a small enumerable answer set
    (earnings today · an analyst action today · classifiable company news · the
    industry moved · the market moved) and returns `kind="none"` when none of them
    holds. Every branch is dated by construction, so a multi-day rally narrative can
    never appear here — the bug that motivated the rebuild.
    """

    # 'earnings' | 'analyst' | 'company_news' | 'sector' | 'market' | 'none'.
    kind: str
    # 2-4 word badge label ("Earnings Beat"). None when kind == 'none'.
    tag: Optional[str] = None
    # One punchy sentence. NEVER empty — the 'none' branch says what it checked and
    # how the move compares with its industry, which is more useful than silence.
    detail: str


class WidgetMoveContextResponse(BaseModel):
    """Arithmetic about the move. Always true, never a guess, always present."""

    change_percent: float
    # |move| / σ_daily. None when σ is unknown — NOT 0.0, which would read as
    # "judged, perfectly normal" for a ticker we cannot judge.
    z: Optional[float] = None
    # The overnight half of the move. Both `open` and `previousClose` ride on the
    # batch-quote row already fetched, so this costs nothing — and a gap means the
    # stock moved before anyone could trade, which is itself an explanation.
    gap_percent: Optional[float] = None
    intraday_percent: Optional[float] = None
    gap_dominant: bool = False
    industry_name: Optional[str] = None
    industry_change_percent: Optional[float] = None
    market_change_percent: Optional[float] = None


class WidgetMoverPayload(BaseModel):
    """What one widget timeline entry renders.

    Deliberately NOT carrying: a market news headline (`__MARKET__`'s roll-up is not a
    move and not today-scoped), a `universe_label` (was decoded by iOS and rendered
    nowhere), or an `is_stale` flag (it meant "the market is closed", which is not the
    same thing — a Saturday snapshot of Friday's close is correct, not stale).
    `market_session` + `as_of` carry all of that honestly.
    """

    # 'market' | 'portfolio'.
    mode: str
    # ISO-8601 UTC, no fractional seconds. REQUIRED — see the module header.
    # This is when the payload was BUILT, not what day the numbers describe.
    # Keeping those two separate is the whole fix below.
    as_of: str
    # 'premarket' | 'regular' | 'afterhours' | 'closed', from `market_hours.session_phase`.
    # Unchanged — an already-installed widget still switches on exactly this.
    market_session: str

    # ── Session LABELLING. NOT a staleness flag; see the class docstring. ──
    #
    # `market_session` alone cannot carry this. On Monday at 08:00 the phase is
    # 'premarket', so a phase-only client labels FRIDAY'S CLOSE as pre-market and
    # says nothing about the date — which is exactly the case the widget got wrong.
    #
    # ET calendar date of the trading session these numbers describe, YYYY-MM-DD.
    # A plain DATE, deliberately not a timestamp: iOS decodes it as `String` and
    # must never run it through the `.iso8601` Date strategy.
    #
    # This is what lets the tile age its own label with no refresh and no flag.
    # The app may have fetched Friday 15:58 with session_label="Live 3:58 PM ET"
    # and the tile may be read on Sunday; `session_date` says WHICH DAY, so the
    # client re-derives "Fri close" at RENDER time.
    session_date: Optional[str] = None
    # The sentence true AT `as_of` — 'Live 2:14 PM ET', 'Fri close'. Built once,
    # server-side, same posture as `cause.detail` and `basket.text`. Clients may
    # DOWNGRADE it as it ages ('Live' stops being true within minutes) but never
    # compose their own.
    session_label: Optional[str] = None
    # Which universe the movers were drawn from — 'The stocks Caydex tracks',
    # 'Your holdings'. Needed because an empty active group falls back to market
    # data at the endpoint, and nothing told the user their "My Holdings" tile
    # was showing the market.
    scope_label: Optional[str] = None

    # None only when nothing was readable — an empty portfolio falls back to market
    # mode at the endpoint, so this is absent far less often than it used to be.
    headline_mover: Optional[WidgetMoverResponse] = None
    # Portfolio mode only, and only when the correlated-move test passes.
    basket: Optional[WidgetBasketResponse] = None
    # Next few movers, for the large family. The service already reads their cards;
    # without this the 4x4 tile renders a void.
    runners_up: List[WidgetMoverResponse] = Field(default_factory=list)


WidgetMoverResponse.model_rebuild()
