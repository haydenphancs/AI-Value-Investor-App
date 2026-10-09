"""
Pydantic schemas for the Holders endpoint.

JSON keys use snake_case (matching the codebase convention).
Swift Codable DTOs map snake_case → camelCase via CodingKeys.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


# ── Shareholder Breakdown ────────────────────────────────────────

class InstitutionalHolderSchema(BaseModel):
    """Individual institutional holder (legacy top holders list)."""
    name: str
    shares_held: float = Field(0.0)
    percent_ownership: float = Field(0.0)
    change_percent: Optional[float] = None


class TopInstitutionSchema(BaseModel):
    """Top institutional owner for the Top 10 sheet."""
    rank: int
    name: str
    category: str = "Asset Management"
    value_in_billions: float = Field(0.0)
    percent_ownership: float = Field(0.0)


class TopInsiderSchema(BaseModel):
    """Top insider owner for the Top 10 sheet."""
    rank: int
    name: str
    title: str = "Officer"
    value_in_millions: float = Field(0.0)
    percent_ownership: float = Field(0.0)


class Top10OwnersSchema(BaseModel):
    """Combined top 10 institutions and insiders."""
    institutions: List[TopInstitutionSchema] = []
    insiders: List[TopInsiderSchema] = []


class ShareholderBreakdownSchema(BaseModel):
    """Ownership distribution pie chart data."""
    insiders_percent: float = Field(0.0)
    institutions_percent: float = Field(0.0)
    public_other_percent: float = Field(0.0)
    # An implausible 13F aggregate (>100%, or >100% beside a near-zero insider block —
    # AAPL read 100.0% / Public 0.0% on 2026-09-03) is UNKNOWN, not clamped. The floats
    # above stay non-Optional because shipped builds decode a plain Double; this is the
    # `pe_known` companion-flag pattern: `institutions_unknown=True` ⇒ the two floats are
    # 0.0 placeholders and iOS renders "—". `institutions_source` names which rung of
    # the fallback chain produced the figure (summary / recomputed / last_quarter /
    # top_holders_sum / unknown) so a wrong number can be traced from the payload.
    institutions_unknown: bool = False
    institutions_source: Optional[str] = None
    top_holders: List[InstitutionalHolderSchema] = []
    top_10_owners: Top10OwnersSchema = Top10OwnersSchema()


# ── Smart Money Flow (placeholder for now) ───────────────────────

class DailyPricePointSchema(BaseModel):
    """Daily stock price for detailed smart money price chart."""
    date: str
    price: float = 0.0


class StockPriceDataPointSchema(BaseModel):
    """Monthly stock price for smart money chart."""
    month: str
    price: float = 0.0


class SmartMoneyFlowDataPointSchema(BaseModel):
    """Buy/sell volume per period (monthly for insider/congress, quarterly for hedge funds)."""
    month: str  # "MM/YYYY" for monthly, "Q1\n'24" for quarterly
    buy_volume: float = Field(0.0)
    sell_volume: float = Field(0.0)
    has_activity: bool = True
    # Real 13F signal for the hedge-fund (Institutions) chart: the net share
    # change and the real counts of institutions that added vs trimmed. These
    # come straight from the positions-summary (no estimation). Optional so the
    # insider/congress tabs — which don't populate them — still validate, and so
    # legacy persisted reports decode.
    net_flow: Optional[float] = None
    buyers_count: Optional[int] = None
    sellers_count: Optional[int] = None


class SmartMoneyFlowSummarySchema(BaseModel):
    """Summary of smart money activity.

    `total_*` are in the BARS' unit — millions of SHARES for the Insider and Institutions
    tabs, dollars for Congress (the STOCK Act discloses ranges, never share counts).

    `*_usd_millions` are populated for the INSIDER tab only and are the VERDICT: the
    badge's value, sign and colour, and `is_positive`, all derive from the dollar net so
    this card cannot contradict `TickerReportView`, whose `_build_insider_sections` has
    always judged insider sentiment on net dollar value. The bars stay share-denominated
    because Form 4 reports exact share counts and a row with no price would silently
    vanish from a dollar chart.

    Optional with a `None` default so an older client simply ignores them and keeps the
    previous share-denominated badge — no forced app update.
    """
    total_net_flow: float = Field(0.0)
    total_buy: float = Field(0.0)
    total_sell: float = Field(0.0)
    is_positive: bool = True
    period_description: str = "12-Month"
    net_flow_usd_millions: Optional[float] = None
    total_buy_usd_millions: Optional[float] = None
    total_sell_usd_millions: Optional[float] = None


class SmartMoneyDataSchema(BaseModel):
    """Complete smart money data for one tab (Insider / Institutions / Congress).

    NAMING: the "Institutions" tab carries FMP 13F "hedge fund" data — the code
    says "hedge fund", the iOS label is "Institutions" (SmartMoneyTab.hedgeFunds).
    """
    tab: str
    price_data: List[StockPriceDataPointSchema] = []
    daily_prices: List[DailyPricePointSchema] = []
    flow_data: List[SmartMoneyFlowDataPointSchema] = []
    summary: SmartMoneyFlowSummarySchema = SmartMoneyFlowSummarySchema()
    # True when this tab's source fetch FAILED for this build (only the Insider tab sets
    # it, 2026-10-03): the zero bars are a placeholder, not "no activity". Optional, so
    # older builds and cached rows decode it as absent.
    unavailable: Optional[bool] = None


# ── Recent Activities ────────────────────────────────────────────

class RecentActivitiesFlowSummarySchema(BaseModel):
    """Summary of institutional flow for a quarter."""
    period_description: str = ""
    quarter_description: str = ""
    in_flow_in_billions: float = Field(0.0)
    out_flow_in_billions: float = Field(0.0)


class InstitutionalActivitySchema(BaseModel):
    """A single recent institutional trading activity."""
    institution_name: str
    category: str = "Asset Management"
    date: str  # ISO date string "yyyy-MM-dd"
    change_in_millions: float = Field(0.0)
    change_percent: float = Field(0.0)
    total_held_in_billions: float = Field(0.0)
    # True when this is a brand-new position (prev quarter held 0). FMP reports
    # such rows as "+100%", which reads like "doubled" — the iOS side renders
    # this flag as "New" instead.
    is_new_position: bool = False


class InsiderActivitySummarySchema(BaseModel):
    """Summary of insider trading activity."""
    period_description: str = "Last 12 Months"
    informative_buys_in_millions: float = Field(0.0)
    informative_sells_in_millions: float = Field(0.0)
    num_buyers: int = 0
    num_sellers: int = 0


class InsiderActivitySchema(BaseModel):
    """A single recent insider trading activity."""
    name: str
    title: str = "Officer"
    date: str  # ISO date string "yyyy-MM-dd"
    change_in_millions: float = Field(0.0)
    transaction_type: str = "Uninformative Sell"
    price_at_transaction: float = Field(0.0)


class InsiderActivitiesDataSchema(BaseModel):
    """Insider activities with summary."""
    summary: InsiderActivitySummarySchema = InsiderActivitySummarySchema()
    activities: List[InsiderActivitySchema] = []
    # True when the insider fetch FAILED for this build: an empty list is then not "no
    # insider transactions" (iOS says the trades couldn't be loaded). Optional.
    unavailable: Optional[bool] = None


class CongressActivitySummarySchema(BaseModel):
    """Summary of congressional trading activity."""
    period_description: str = "Last 12 Months"
    total_buys_in_millions: float = Field(0.0)
    total_sells_in_millions: float = Field(0.0)
    num_buyers: int = 0
    num_sellers: int = 0


class CongressActivitySchema(BaseModel):
    """A single recent congressional trading activity."""
    name: str                                      # "Pelosi, Nancy"
    role: str = "Representative"                   # "Senator (KY)" or "Representative (TX-11)"
    date: str                                      # ISO date string "yyyy-MM-dd"
    change_in_millions: float = Field(0.0)         # midpoint of range, signed
    amount_range: str = ""                         # raw range "$1,001 - $15,000"
    amount_range_max_millions: float = Field(0.0)  # max of range in millions (for sorting)
    owner: str = "Self"                            # "Self", "Spouse", "Joint"
    transaction_type: str = "Purchase"             # "Purchase" or "Sale"
    price_at_transaction: float = Field(0.0)
    # When the report was FILED (a periodic transaction report may come up to 45 days after
    # the trade `date`). Additive and Optional (2026-10-08): shipped builds ignore the key;
    # Ask Cay AI's ownership tool states it ("disclosed on …").
    disclosure_date: Optional[str] = None


class CongressActivitiesDataSchema(BaseModel):
    """Congress activities with summary."""
    summary: CongressActivitySummarySchema = CongressActivitySummarySchema()
    activities: List[CongressActivitySchema] = []


class RecentActivitiesSchema(BaseModel):
    """Combined recent activities data."""
    institutional_flow_summary: RecentActivitiesFlowSummarySchema = (
        RecentActivitiesFlowSummarySchema()
    )
    institutional_activities: List[InstitutionalActivitySchema] = []
    insider_activities: InsiderActivitiesDataSchema = InsiderActivitiesDataSchema()
    congress_activities: CongressActivitiesDataSchema = CongressActivitiesDataSchema()


# ── Chat-only ownership detail (never on the wire) ──────────────
#
# Ask Cay AI's `check_ownership_filings` tool (2026-10-05) reads these from the Holders build:
# what each insider HOLDS after their latest Form 4 transactions (`_insider_holdings`), and
# which 13F quarter the institutional figures describe. iOS decodes none of it — the field
# that carries it is excluded from serialization (see `HoldersResponse.ownership_detail`).


class InsiderHoldingSchema(BaseModel):
    """One reported balance: the shares held right AFTER a Form 4 transaction."""
    security: str = "Shares"                      # the filing's line, e.g. "Class A Common Stock"
    held: str = "direct"                          # "direct" | "indirect" (a trust, family, entity)
    shares: Optional[float] = None                # None ⇔ ambiguous: see `possible_shares`
    possible_shares: Optional[List[float]] = None  # the same-day balances the day may have ended on
    as_of: str = ""                               # the transaction date that produced the balance
    filed: Optional[str] = None
    # A balance last reported before the line's newest indirect transaction: a separate
    # holding that has not traded since, OR an older figure for a listed one. Never summed.
    reported_earlier: bool = False
    # A later transaction (this date) on the line reported no usable balance.
    changed_after: Optional[str] = None
    # Set (≥ 2) when that many lines that day ended on exactly this balance and nothing shows
    # they are separate holdings: one entry, which may stand for more than one holding of
    # this size (review F2, 2026-10-07).
    same_balance_lines: Optional[int] = None


class InsiderTradeSchema(BaseModel):
    """The person's newest day of transactions, one entry per (code, direction)."""
    transaction_type: str = ""                    # FMP code, e.g. "S-Sale", "M-Exempt"
    acquired: Optional[bool] = None
    shares: Optional[float] = None
    average_price: Optional[float] = None


class InsiderOwnerSchema(BaseModel):
    name: str
    role: str = "Insider"
    # The filing's raw role string ("director, officer: VP Sales"), for the one insider roster
    # (`roster_from_holdings`, 2026-10-08). Optional: chat never prints it.
    type_of_owner: Optional[str] = None
    latest_transaction_date: str = ""
    latest_filing_date: Optional[str] = None
    latest_trades: List[InsiderTradeSchema] = []
    holdings: List[InsiderHoldingSchema] = []
    # Entries a line's cap left out: indirect holdings, direct lots, earlier direct figures.
    holdings_not_shown: int = 0


class InsiderHoldingsSchema(BaseModel):
    # False when the insider fetch lost pages: the balances shown are the newest in what
    # arrived (a person's newest rows come first), but people may be missing.
    complete: bool = True
    # The oldest filing date among the rows read: a holding with no reported transaction
    # since then is not visible here.
    covers_filings_since: Optional[str] = None
    insiders: List[InsiderOwnerSchema] = []
    insiders_not_shown: int = 0
    # Everyone left out is NAMED, so a question about them is answered "not loaded", never
    # "no filing of theirs was found" (review finding 7, 2026-10-07).
    insiders_not_shown_names: List[str] = []
    # People whose newest filing (on any of their rows) is over two years older than the
    # newest filing anyone made (most likely no longer insiders) — left out, counted, named.
    inactive_not_shown: int = 0
    inactive_not_shown_names: List[str] = []


class OwnershipDetailSchema(BaseModel):
    # None when the insider rows could not be read (or attributed to this issuer): the chat
    # tool says "unavailable", never "no insider holds anything".
    insider_holdings: Optional[InsiderHoldingsSchema] = None
    institutions_quarter: Optional[str] = None    # e.g. "Q2 2026" — the 13F data quarter
    # When the build read the filings (ISO-8601 UTC). The Holders row is cached up to 24 h,
    # so the chat tool states it, and re-reads when a newer filing exists (finding 3).
    built_at: Optional[str] = None
    # The newest filing date among the insider rows read, and the identities of the rows
    # filed on it (`holders_service._insider_row_identity`; empty when there were too many):
    # what `HoldersService.newer_insider_filing` compares a one-page probe against.
    newest_filed: Optional[str] = None
    newest_filed_ids: List[str] = []
    # The ONE float figure the chat tool states (2026-10-08): the shares-float row this build
    # already fetched, which `shareholder_breakdown.insiders_percent` (100 − free float) is
    # computed from too — so the float, the shares outstanding and the insiders' share in one
    # answer never come from two reads taken at different times. None = not reported (never 0).
    float_shares: Optional[float] = None
    outstanding_shares: Optional[float] = None
    free_float_percent: Optional[float] = None
    float_as_of: Optional[str] = None


# ── Top-level response ───────────────────────────────────────────

class HoldersResponse(BaseModel):
    """
    Full holders response — maps to the iOS HoldersData struct.

    Includes shareholder breakdown, smart money flow (placeholder),
    and recent activities (live).
    """
    symbol: str
    shareholder_breakdown: ShareholderBreakdownSchema = ShareholderBreakdownSchema()
    insider_data: SmartMoneyDataSchema = SmartMoneyDataSchema(tab="Insider")
    hedge_funds_data: SmartMoneyDataSchema = SmartMoneyDataSchema(tab="Institutions")
    congress_data: SmartMoneyDataSchema = SmartMoneyDataSchema(tab="Congress")
    recent_activities: RecentActivitiesSchema = RecentActivitiesSchema()
    # Congress is Pro/Max (2026-09-17). For a Free caller the service substitutes EMPTY
    # but well-formed `congress_data` / `congress_activities` (the iOS DTO requires them
    # non-Optional, so they can never be null on the wire) and raises this pair so the
    # client renders the locked stub rather than "No congress activity data available".
    # Scoped names, not a bare `is_locked`: only one segment is withheld.
    congress_locked: bool = False
    congress_tier_required: Optional[str] = None
    # Chat-only (`check_ownership_filings`, 2026-10-05). EXCLUDED from serialization: the
    # Holders endpoint's JSON is unchanged for every app build, and `model_dump()` (the
    # report's collection cache) drops it too. `holders_service` writes it into the 24h
    # `holders_cache` row explicitly and reads it back through validation.
    ownership_detail: Optional[OwnershipDetailSchema] = Field(default=None, exclude=True)
