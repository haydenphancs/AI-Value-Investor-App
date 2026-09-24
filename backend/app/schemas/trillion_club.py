"""
"Trillion-Dollar Club Bets" schemas — the Home section of what the companies worth $1
trillion or more own in other companies (``HomeDashboardResponse.trillion_club``) and the
per-company drill-down served by ``GET /api/v1/home/trillion-club/{slug}``.

Two kinds of data, never mixed up on the wire:

* **13F holdings** (NVIDIA, Alphabet, Amazon, AMD): U.S.-listed positions from the company's
  own SEC Form 13F-HR, as of the quarter end, with quarter-over-quarter SHARE changes. A
  holding that first appears is ``newly_reported`` — never "bought": most Q2 2026 rows were
  IPO conversions (SpaceX listed on 2026-06-12), so ``newly_listed`` says so.
* **Stakes** (every member): hand-kept private / non-U.S. / off-13F stakes and commitments,
  each with its primary source, the date it describes and the date it was last checked.

RAW numbers on the wire (fractions, dollars); iOS formats. Dates are ISO ``YYYY-MM-DD``
strings. Every field iOS may not get is Optional or defaulted, so an already-shipped build
keeps decoding and a failed build degrades to an empty section.

The copy is factual and hand-written — no generated text anywhere in this section.
"""

from typing import List, Optional

from pydantic import BaseModel

# card_kind values ------------------------------------------------------------------------
CARD_THIRTEEN_F = "thirteen_f"        # files a 13F: holdings + changes (+ stakes)
CARD_NO_THIRTEEN_F = "no_thirteen_f"  # U.S. company with no 13F: stakes only
CARD_NON_US = "non_us"                # non-U.S. company: stakes from its own annual report
CARD_WHALE_LINK = "whale_link"        # Berkshire: link to its existing whale profile
CARD_KINDS = (CARD_THIRTEEN_F, CARD_NO_THIRTEEN_F, CARD_NON_US, CARD_WHALE_LINK)

# Share-change outcomes (a holding's `change`) --------------------------------------------
CHANGE_NEWLY_REPORTED = "newly_reported"
CHANGE_NO_LONGER_REPORTED = "no_longer_reported"
CHANGE_INCREASED = "increased"
CHANGE_DECREASED = "decreased"
CHANGE_UNCHANGED = "unchanged"
CHANGE_CORPORATE_ACTION = "corporate_action"
CHANGE_KINDS = (CHANGE_NEWLY_REPORTED, CHANGE_NO_LONGER_REPORTED, CHANGE_INCREASED,
                CHANGE_DECREASED, CHANGE_UNCHANGED, CHANGE_CORPORATE_ACTION)

# How the quarter compares with the previous one -------------------------------------------
COMPARISON_QUARTER = "quarter"          # the previous quarter is the adjacent one
COMPARISON_FIRST_FILING = "first_filing"  # no earlier 13F on file
COMPARISON_GAP = "gap"                  # the previous filing on file is not the adjacent quarter

# Card notices (codes; iOS owns the wording) -----------------------------------------------
NOTICE_LATEST_NOT_IN = "latest_not_in"      # due date + 5 business days passed, no new filing yet
NOTICE_AMENDED = "amended"                  # the quarter includes a 13F-HR/A
NOTICE_FIRST_FILING = "first_filing"        # nothing earlier to compare with
NOTICE_NO_NEWER_FILING = "no_newer_filing"  # two due dates passed with no newer 13F found
NOTICE_KINDS = (NOTICE_LATEST_NOT_IN, NOTICE_AMENDED, NOTICE_FIRST_FILING, NOTICE_NO_NEWER_FILING)

# Stake kinds and value bases ----------------------------------------------------------------
STAKE_KINDS = ("private", "non_us_listed", "us_listed_off_13f", "commitment", "on_13f_note")
VALUE_BASES = ("carrying_value", "fair_value", "invested", "committed_up_to")


class ClubChangeCountsResponse(BaseModel):
    """How many holdings fall in each share-change outcome, vs the previous quarter."""

    newly_reported: int = 0
    increased: int = 0
    decreased: int = 0
    no_longer_reported: int = 0
    unchanged: int = 0
    corporate_action: int = 0


class ClubHoldingResponse(BaseModel):
    """One U.S.-listed position from a 13F (as of the quarter end)."""

    name: str                                   # issuer name ("" never sent; falls back to the symbol)
    symbol: Optional[str] = None                # routable U.S. ticker; None when unresolved
    weight: Optional[float] = None              # FRACTION of the filing's reported value (0.473)
    shares: Optional[float] = None
    value: Optional[float] = None               # dollars, as reported
    change: Optional[str] = None                # one of CHANGE_KINDS; None on a first filing
    newly_listed: bool = False                  # first appears because it newly went public
    is_small: bool = False                      # under 1% of the filing's reported value
    club_member_slug: Optional[str] = None      # the holding is itself a club member (read time)
    sector: Optional[str] = None


class ClubChangeResponse(BaseModel):
    """One share change vs the previous quarter. `unchanged` rows are never sent."""

    name: str
    symbol: Optional[str] = None
    change: str                                 # one of CHANGE_KINDS except "unchanged"
    newly_listed: bool = False
    shares: Optional[float] = None              # this quarter (None when no longer reported)
    prev_shares: Optional[float] = None         # previous quarter, split-restated
    share_change: Optional[float] = None
    value: Optional[float] = None
    weight: Optional[float] = None


class ClubStakeResponse(BaseModel):
    """A hand-kept stake outside the 13F, with its primary source."""

    investee_name: str
    kind: str                                   # one of STAKE_KINDS
    symbol: Optional[str] = None                # only when routable to the stock detail screen
    local_listing: Optional[str] = None         # display only, e.g. "Taiwan"
    ownership_pct: Optional[float] = None       # PERCENT (25.0 = 25%), as the source states it
    ownership_basis: Optional[str] = None
    disclosed_value: Optional[float] = None     # dollars, as disclosed by the owning company
    value_basis: Optional[str] = None           # one of VALUE_BASES
    as_of: str                                  # the date the figure describes
    source_title: str                           # e.g. "Microsoft 10-K (FY2026)"
    source_url: str
    tied_to_deal: bool = False
    listed_since: Optional[str] = None
    background: Optional[str] = None            # ≤ 90 chars, past tense, hand-written
    verified_on: str                            # when the owner last checked the source
    is_stale: bool = False                      # verified_on older than 120 days
    club_member_slug: Optional[str] = None      # the investee is itself a club member (read time)


class ClubMemberBriefResponse(BaseModel):
    """A club member named by slug + name. In the group's `also_in_club` it is a member
    WITHOUT a card (nothing material to show); in a detail's `other_members` it is every
    OTHER published member, with or without a card."""

    slug: str
    name: str


class ClubHistoryPointResponse(BaseModel):
    """One earlier quarter of a 13F filer (Pro)."""

    period: str                                 # "2026-Q1"
    period_end: str
    total_value: Optional[float] = None
    position_count: Optional[int] = None


class TrillionClubCompanyResponse(BaseModel):
    """One company card (also the header of its detail screen)."""

    slug: str
    name: str
    card_kind: str                              # one of CARD_KINDS
    logo_symbol: Optional[str] = None
    detail_symbol: Optional[str] = None         # routable U.S. ticker for the company itself
    market_cap: Optional[float] = None          # dollars, a dated close (or the owner's figure)
    market_cap_as_of: Optional[str] = None
    cap_is_manual: bool = False                 # entered by the owner from a cited source
    # 13F cards only ------------------------------------------------------------------
    period: Optional[str] = None                # "2026-Q2"
    period_end: Optional[str] = None            # holdings as of this date
    filed_on: Optional[str] = None
    amended_on: Optional[str] = None
    next_due: Optional[str] = None              # the next 13F's legal due date
    position_count: Optional[int] = None
    total_value: Optional[float] = None         # dollars reported on the 13F (U.S.-listed stock)
    top_holdings: List[ClubHoldingResponse] = []
    change_counts: Optional[ClubChangeCountsResponse] = None
    comparison: Optional[str] = None            # COMPARISON_* value
    prev_period: Optional[str] = None
    notice: Optional[str] = None                # one of NOTICE_KINDS
    # Every card --------------------------------------------------------------------------
    stakes: List[ClubStakeResponse] = []        # material stakes only on the Home card
    # Every published stake of the company (the card shows only the material ones), so the
    # card can say "+N more in the details" truthfully.
    stake_count: Optional[int] = None
    whale_id: Optional[str] = None              # whale_link cards: the whale profile to open
    reviewed_on: Optional[str] = None


class TrillionClubGroupResponse(BaseModel):
    """The Home section. Empty (and hidden by iOS) when the feature is off, the data is
    unreadable, or membership has not been refreshed for over a week."""

    companies: List[TrillionClubCompanyResponse] = []   # largest market cap first
    also_in_club: List[ClubMemberBriefResponse] = []    # members without a card


class TrillionClubDetailResponse(BaseModel):
    """A company's drill-down. Free: top 3 holdings, the changes and every stake. Pro/Max:
    every holding and the earlier quarters (`is_locked` false)."""

    company: TrillionClubCompanyResponse
    holdings: List[ClubHoldingResponse] = []
    changes: List[ClubChangeResponse] = []      # never "unchanged" rows
    stakes: List[ClubStakeResponse] = []        # all published stakes (not only material)
    history: List[ClubHistoryPointResponse] = []
    is_locked: bool = False
    tier_required: Optional[str] = None
    locked_holdings_count: int = 0
    # Earlier quarters withheld from a Free caller. 0 means there is nothing behind the
    # lock, so iOS shows no History paywall for a filer with no earlier quarter.
    locked_history_count: int = 0
    other_members: List[ClubMemberBriefResponse] = []
