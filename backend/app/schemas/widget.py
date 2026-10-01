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



class WidgetIndexResponse(BaseModel):
    """One index in the market band — or one asset in the Market tile's grid.

    The equities cost ZERO extra calls: they ride the universe batch quote the service
    already makes (`get_batch_quotes_bulk` chunks at 300, and the swept universe is 200).
    They previously rode a SEPARATE call that was cached for an hour — see the note on
    `_CONTEXT_TTL_SECONDS`. Bitcoin (`market_assets` only) comes from the Home Market
    Pulse's own 600 s crypto tile, never a fresh CoinGecko call.
    """

    symbol: str
    # The display name is owned by the BACKEND, not the client: an already-installed
    # widget cannot learn that '^RUT' is "Russell 2000" without an app update. It is the
    # honest fund name ("S&P 500 ETF") and is the one shown beside a PRICE.
    label: str
    # None ⇒ iOS hides the number. Never 0.0 — same rule as `change_percent` below. Also
    # None when the row's own session stamp is not the payload's session: a number from
    # one session under another session's label is the "Nasdaq 0.00%" bug.
    change_percent: Optional[float] = None
    price: Optional[float] = None
    # The cramped-grid label ("S&P 500", "Nasdaq") for Small/Medium, where only the % is
    # drawn. Never rendered beside a price: an ETF's ~$650 price under "S&P 500" reads as
    # the index being off by 10x. None ⇒ the client falls back to `label`.
    short_label: Optional[str] = None
    # True for a round-the-clock asset (Bitcoin) whose change is a ROLLING 24 h move, not a
    # session move — the client tags it "24h" so the equity session footer does not govern it.
    rolling_24h: bool = False
    # "etf" | "crypto" | "index" | "commodity" | "stock". SERVER-owned for the same reason as
    # `label`: an installed widget cannot learn an asset's class, and a tap-through that opens
    # the wrong detail screen (SPY as a stock) is worse than none. None ⇒ unknown, link nowhere.
    asset_type: Optional[str] = None


class WidgetMarketContextResponse(BaseModel):
    """How the market ITSELF is doing — the band the tile leads with.

    Not to be confused with `WidgetMoveContextResponse`, which is arithmetic about one
    ticker's move. This describes the tape.

    Every field is optional and every leg degrades on its own: losing the sector snapshot
    must not cost the indices, and vice versa.
    """

    indices: List["WidgetIndexResponse"] = Field(default_factory=list)
    # Breadth over the 11 SECTORS, which is a real population — so "8 of 11" is a defined
    # statistic. Deliberately not computed from FMP's biggest-gainers list: that is a
    # top-50 cut, not a population, and "how many are up" in it is meaningless.
    breadth_up: Optional[int] = None
    breadth_total: Optional[int] = None
    leading_sector: Optional[str] = None
    leading_sector_change_percent: Optional[float] = None
    lagging_sector: Optional[str] = None
    lagging_sector_change_percent: Optional[float] = None
    # The rendered sentence, deterministic — same posture as `basket.text` and
    # `cause.detail`: the wording lives in ONE place and can never contradict the numbers
    # printed beside it. None when nothing was readable.
    text: Optional[str] = None


class WidgetMoverResponse(BaseModel):
    """One mover: the headline, a runner-up, or a top gainer / loser."""

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
    # True for a round-the-clock asset (a CoinGecko-priced crypto pair): its change is its
    # own rolling 24 h, so the client tags the row "24h" instead of letting the tile's
    # equity footer ("Fri close") date it.
    rolling_24h: bool = False
    # "etf" | "crypto" | "stock", from the batch quote's own `isEtf` flag (no extra call).
    # None when the quote carried no class — the client then resolves by symbol.
    asset_type: Optional[str] = None


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
    # The same attribution worded for a reader on a LATER day ("…in Tuesday's news",
    # "Down 2.7% on Tue"), never "today". `detail` is true at `as_of`, but a Holdings
    # snapshot built Tuesday is still on the Home Screen Wednesday, where the client ages
    # the footer to "Tue close" — and a verbatim "today's news" beside it contradicted it.
    # The client shows this whenever it ages the footer. HEADLINE only. A round-the-clock
    # (crypto) headline is worded with the build's ET CALENDAR day ("…on Sat"), since its
    # rolling 24 h move has no equity session to name; the client ages it by as-of day.
    detail_aged: Optional[str] = None


class WidgetMoveContextResponse(BaseModel):
    """Arithmetic about the move. Always true, never a guess, always present."""

    change_percent: float
    # |move| / σ_daily. None when σ is unknown — NOT 0.0, which would read as
    # "judged, perfectly normal" for a ticker we cannot judge.
    z: Optional[float] = None
    # The overnight half of the move. Needs the session `open`, which the licensed batch
    # quote (`price_service._shape`, built from the screener) does NOT carry since the
    # `quote` family went 402 — so these are `None` / `False` on every mover today and
    # the client treats them as absent. Kept on the wire so the shape is unchanged and a
    # licensed `open` re-enables them with no client change.
    gap_percent: Optional[float] = None
    intraday_percent: Optional[float] = None
    gap_dominant: bool = False
    industry_name: Optional[str] = None
    industry_change_percent: Optional[float] = None
    market_change_percent: Optional[float] = None


class WidgetMarketBriefResponse(BaseModel):
    """The one-sentence read on the whole market, for the Market tile.

    This is the `__MARKET__` roll-up the Updates screen already shows — same cache,
    same sweeper, no extra AI spend. It exists so the Market tile can answer "what is
    going on with the market" at a glance, which is a different question from the
    biggest-mover tile that Holdings mode answers.

    ⚠️ SESSION-GATED AT THE SOURCE. The roll-up's own window is 24-96h and its hard TTL
    is 96h, so it can outlive the session it describes — which is precisely why this
    payload used to refuse to carry it. The service omits the whole object unless the
    card was generated during the session the rest of the payload describes, so the
    widget keeps rendering only facts dated to today BY CONSTRUCTION rather than by a
    label the client has to be trusted to respect (`test_widget_daily_scope.py`).
    """

    # One sentence, already word-capped by the roll-up prompt.
    headline: str
    # 'Bullish' | 'Bearish' | 'Neutral' — same vocabulary as the Updates card's badge.
    sentiment: Optional[str] = None
    # ISO-8601 UTC. Present so the client can age the tile, never to be shown raw.
    generated_at: Optional[str] = None


class WidgetMoverPayload(BaseModel):
    """What one widget timeline entry renders.

    Deliberately NOT carrying: a `universe_label` (was decoded by iOS and rendered
    nowhere) or an `is_stale` flag (it meant "the market is closed", which is not the
    same thing — a Saturday snapshot of Friday's close is correct, not stale).
    `market_session` + `as_of` carry all of that honestly.

    It DOES now carry a market headline (`market_brief`), which this docstring used to
    rule out. The objection was that `__MARKET__`'s roll-up "is not a move and not
    today-scoped", and both halves still stand — so the field is session-gated in the
    service and the tile that renders it is no longer a move tile. Market mode answers
    "what is the market doing"; Holdings mode answers "what moved most of mine". Two
    different questions, and conflating them is what made the Market tile a
    biggest-mover list nobody asked for.

    THE TWO MODES RANK DIFFERENTLY, ON PURPOSE. Portfolio mode orders the caller's
    holdings by ABSOLUTE % move (|%| desc, then z): the tile is "my biggest movers", and a
    volatility-z order (AAPL −2.7% above ORCL +4.0%, every σ-less holding last) read as
    random. Market mode keeps the z order over the swept universe for the legacy
    `headline_mover` / `runners_up`, which the current Market tile no longer renders — it
    draws `market_assets`, the Home Market Pulse in the same order.

    The portfolio route no longer falls back to MARKET data. An empty group is answered
    with `mode="portfolio"` and `holdings_count=0`, and an unreadable one with a degraded
    portfolio payload (`holdings_count=None`): a "My Holdings" tile showing the market was
    indistinguishable from the user's own holdings.
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
    # 'Your holdings'. Introduced when an empty active group fell back to market data at
    # the endpoint (it no longer does); still sent because installed builds caption a
    # scope mismatch with it.
    scope_label: Optional[str] = None

    # Market mode only, and only when the roll-up is dated to this session. Absent is
    # normal and must render fine: the tile falls back to the index numbers alone.
    market_brief: Optional[WidgetMarketBriefResponse] = None

    # How the market itself did. Absent only when every leg failed — the tile then leads
    # with the mover, exactly as it did before this field existed.
    market_context: Optional[WidgetMarketContextResponse] = None

    # None when nothing was rankable: an empty or unpriced group, or a degraded build.
    # Market mode still sends it (z-ranked) for installed builds; the current Market tile
    # ignores it. Portfolio mode: the biggest |%| mover among the holdings.
    headline_mover: Optional[WidgetMoverResponse] = None
    # Portfolio mode only, and only when the correlated-move test passes.
    basket: Optional[WidgetBasketResponse] = None
    # Next few movers in ranking order (|%| in portfolio mode, z in market mode). The
    # service already reads their cards; without this the 4x4 tile renders a void.
    runners_up: List[WidgetMoverResponse] = Field(default_factory=list)

    # ── Portfolio mode only. Never set in market mode. ──
    #
    # The active group's name; "" is normalised to None, which the client renders as
    # "My Holdings". Applied by the endpoint AFTER the 60 s cache (the cache key carries
    # neither the name nor the group id), so a rename shows at once.
    group_name: Optional[str] = None
    # How many holdings the tile describes — the valid group tickers BEFORE the 200 cap.
    #   N    the group's size;
    #   0    an authoritative empty group ("No holdings in <name> yet");
    #   None DEGRADED — the holdings or the quote leg were unreadable. The client keeps its
    #        last good snapshot rather than replacing it with this.
    holdings_count: Optional[int] = None
    # Over the ranked current-session rows; flat = |change| < 0.005, exactly the client's
    # `isFlat` (it rounds to the 2 displayed decimals). The client derives "N no price"
    # as holdings_count − up − down − flat.
    up_count: Optional[int] = None
    down_count: Optional[int] = None
    flat_count: Optional[int] = None
    # ≤5 each, the headline excluded. Gainers by change descending, losers ascending.
    top_gainers: List[WidgetMoverResponse] = Field(default_factory=list)
    top_losers: List[WidgetMoverResponse] = Field(default_factory=list)

    # ── Market mode only. ──
    #
    # The Home Market Pulse in its own order — S&P 500, Nasdaq, Dow, Russell 2000, Gold
    # ETFs, then Bitcoin. Empty is valid (every leg failed); never fabricated.
    market_assets: List[WidgetIndexResponse] = Field(default_factory=list)


class WidgetTokenResponse(BaseModel):
    """The Home Screen extension's market-data credential.

    `expires_at` is ISO-8601 UTC and is carried so the client can renew BEFORE the token dies.
    Without it the only way to learn a token expired is a 401 inside the widget process, which
    has no error state and no way to tell the app — the tile would simply stop updating, which
    is the exact failure this whole mechanism exists to remove.
    """

    token: str
    expires_at: str


WidgetMoverResponse.model_rebuild()
WidgetMarketContextResponse.model_rebuild()
