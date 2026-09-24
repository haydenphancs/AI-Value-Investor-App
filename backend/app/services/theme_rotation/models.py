"""Data shapes for the monthly Emerging Frontiers theme rotation. Pure — no I/O.

The rotation decides, once a month, which stocks belong in each Home theme card
(`trending_themes.tickers`). Owner decisions (2026-09-23) that shape everything here:

* Every theme is re-scored monthly; the change fraction is a CEILING (≤ 30% of the list),
  not a target — most months change 0-3 names, and relevance always wins over freshness.
* Relevance decides membership; recent price performance is a small tie-breaker (15%).
* A removed stock may come back as soon as it ranks back in.

The pipeline: `sources` loads licensed data → `scoring` turns each candidate into a
0-100 score and checks eligibility floors → `llm_gate` confirms a newcomer's own
description is on-theme (it can only BLOCK) → `rotation.plan_rotation` applies the
buffer / strike / cap rules → `service` records every decision and publishes atomically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Tuple


class Action(str, Enum):
    KEPT = "kept"
    ADDED = "added"
    RETURNED = "returned"
    REMOVED = "removed"
    DEFERRED = "deferred"
    REJECTED = "rejected"
    BENCH = "bench"


class Reason(str, Enum):
    # kept
    STILL_ON_THEME = "still_on_theme"
    ANCHOR = "anchor"
    PINNED = "pinned"
    FIRST_STRIKE = "first_strike"            # ranked low this month; one more month to confirm
    TENURE_PROTECTED = "tenure_protected"    # joined too recently to rotate out
    KEPT_FOR_SIZE = "kept_for_size"          # would have left, but no stronger stock can take its seat
    # added / returned
    ENTERED_TOP_RANKS = "entered_top_ranks"
    RETURNED_TOP_RANKS = "returned_top_ranks"
    REFILL = "refill"                        # a slot opened (forced removal) and must be filled
    # removed
    BLOCKED = "blocked"                      # editor override
    DELISTED = "delisted"                    # no longer actively trading
    BELOW_FLOORS = "below_floors"            # size / liquidity / price fell below the minimums
    OUTRANKED = "outranked"                  # two months below the keep zone
    OFF_THEME = "off_theme"                  # description judged not on theme, two months running
    # deferred
    CHANGE_CAP = "change_cap"
    # rejected (newcomers only)
    FAILS_FLOORS = "fails_floors"
    NOT_ON_THEME = "not_on_theme"
    FIT_UNVERIFIED = "fit_unverified"        # the relevance check failed or was not run
    WEAK_EVIDENCE = "weak_evidence"          # only "adjacent" without the data to back it
    NO_DATA = "no_data"                      # price history unavailable → cannot be judged
    IPO_SEASONING = "ipo_seasoning"
    NOT_US_LISTED = "not_us_listed"
    # bench (eligible outsider, not added)
    RANKED_BELOW_ENTRY = "ranked_below_entry"
    NO_OPEN_SLOT = "no_open_slot"


class Fit(str, Enum):
    CORE = "core"
    ADJACENT = "adjacent"
    NOT_RELATED = "not_related"


ADDABLE_FITS = frozenset({Fit.CORE.value, Fit.ADJACENT.value})


@dataclass(frozen=True)
class ThemeDefinition:
    slug: str
    label: str                               # what the theme is, in words (prompts, logs)
    seed_etfs: Tuple[str, ...]
    industries: FrozenSet[str]               # exact FMP `industry` strings
    segment_keywords: Tuple[str, ...]        # lower-case substrings of revenue-segment names
    description_keywords: Tuple[str, ...]    # lower-case phrases in the company description
    small_cap: bool = False
    anchors: int = 3

    @property
    def min_market_cap(self) -> float:
        return 500e6 if self.small_cap else 2e9

    # Members face LOWER bars than newcomers — the index-provider buffer. Global X keeps an
    # existing member down to 40% of the entry market cap (BUG: $200M new / $80M existing);
    # the first live preview (2026-09-23) would otherwise have evicted Rapid7 ($0.87B) from
    # Cyber Wars and two robotics pure plays the owner had curated.
    @property
    def incumbent_min_market_cap(self) -> float:
        return 0.4 * self.min_market_cap

    @property
    def min_adtv(self) -> float:
        return 10e6 if self.small_cap else 20e6

    @property
    def incumbent_min_adtv(self) -> float:
        return 0.5 * self.min_adtv


@dataclass(frozen=True)
class Candidate:
    """Everything the scoring needs to know about one stock for one theme.

    `None` always means UNKNOWN (the source had nothing or failed), never zero — an
    unknown is scored neutrally for a member and not at all for a newcomer.
    """
    ticker: str
    name: str = ""
    is_member: bool = False
    market_cap: Optional[float] = None
    price: Optional[float] = None
    exchange: Optional[str] = None
    actively_trading: Optional[bool] = None
    industry: Optional[str] = None
    description: str = ""
    # Theme share of revenue from product segments whose names match the theme's keywords,
    # 0..1. None = no segment data (common for foreign filers and small caps).
    segment_share: Optional[float] = None
    keyword_hits: int = 0                    # DISTINCT description keywords matched
    etf_holders: int = 0                     # how many of the theme's seed ETFs hold it
    etf_max_weight: float = 0.0              # its largest weight (%) in any seed ETF
    ret_3m: Optional[float] = None           # fractional total return, e.g. 0.12
    ret_6m: Optional[float] = None
    adtv_6m: Optional[float] = None          # average daily traded value, $
    session_coverage: Optional[float] = None # share of expected sessions with a bar, 0..1
    sessions_listed: Optional[int] = None    # bars since listing when listed < ~6 months
    history_blocked: bool = False            # price history unavailable/unlicensed
    fit: Optional[str] = None                # Fit value, or None = not checked / check failed
    fit_band: Optional[str] = None           # the check's revenue-share band, e.g. "over_50"


@dataclass(frozen=True)
class ScoreBreakdown:
    total: float
    exposure_pts: float
    etf_pts: float
    market_pts: float
    size_pts: float
    exposure: float                          # 0..1 as scored
    exposure_source: str                     # segments | industry | description | unknown

    def as_parts(self) -> Dict[str, object]:
        return {
            "exposure_pts": self.exposure_pts, "etf_pts": self.etf_pts,
            "market_pts": self.market_pts, "size_pts": self.size_pts,
            "exposure": self.exposure, "exposure_source": self.exposure_source,
        }


@dataclass(frozen=True)
class MemberHistory:
    """What published LIVE runs recorded about a ticker in one theme (dry runs never count).

    `tenure_months` None = the ticker was a member before the rotation kept records, which
    counts as seasoned.
    """
    tenure_months: Optional[int] = None
    struck_last_month: bool = False
    flips_6m: int = 0                        # joins + departures in the last 6 published runs
    removed_recently: bool = False           # removed in the last 6 published runs → "returned"


@dataclass(frozen=True)
class RotationConfig:
    min_size: int = 12
    max_size: int = 24
    keep_rank_fraction: float = 1.25         # members ranked within 1.25·N stay
    entry_rank_fraction: float = 0.75        # outsiders must rank within 0.75·N to enter
    max_change_fraction: float = 0.30        # CEILING on replaced slots per month
    anchors: int = 3
    min_tenure_months: int = 2
    decisive_rank_multiple: float = 2.0      # ranked beyond 2·N: one strike is enough
    pingpong_flips: int = 3
    pingpong_penalty: float = 5.0
    fund_consensus: int = 2                  # seed ETFs holding a member that shield it from
                                             # an AI-only "off theme" strike


@dataclass(frozen=True)
class Decision:
    ticker: str
    action: Action
    reason: Reason
    score: Optional[float] = None
    rank: Optional[int] = None
    was_member: bool = False
    strike: bool = False
    score_parts: Dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ThemePlan:
    slug: str
    before: List[str]
    after: List[str]
    decisions: List[Decision]
    added: List[str]
    returned: List[str]
    removed: List[str]
    deferred: List[str]
    shortfall: bool
    change_cap: int

    @property
    def change_count(self) -> int:
        """Replaced slots: one out + one in is ONE change (the owner's "20-30%")."""
        return max(len(self.added) + len(self.returned), len(self.removed))

    @property
    def changed(self) -> bool:
        return self.before != self.after


@dataclass(frozen=True)
class RunContext:
    run_month: date
    mode: str                                # live | dry_run | preview
