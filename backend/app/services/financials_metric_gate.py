"""Which FMP industries' balance sheets make a liquidity or coverage ratio meaningless.

A bank's or insurer's current ratio, quick ratio and interest coverage say nothing about
its health: deposits and policy reserves are its funding, and interest is its cost of
goods, not a burden on operating earnings. Production rows showed it (2026-10-07): C read
all three red, BAC a 0.54 current ratio as "positive". For these industries the rows are
OMITTED (not shown, not scored) — and, because they are meaningless, their values are kept
out of the Financial Services SECTOR median for these metrics too, so a data vendor or an
exchange compared against its sector is not compared against banks.

One shared source, used by three places that must agree:
  * `health_check_service` (the Financials tab Health Check) and `health_snapshot_service`
    / the Overview fallback card (which rows exist);
  * `industry_benchmark_service` (which companies' values the sector aggregate pools).

Matched on FMP's /stable `industry` string, case- and dash-insensitively ("Banks—Regional"
from the older feed is "Banks - Regional"). An unknown or empty industry keeps every row.
`tests/test_health_check_2026_10_07.py` walks every Financial Services and Real Estate
industry in the universe files, so a new FMP industry name fails the build until someone
decides which side it is on.
"""

from __future__ import annotations

import re
from typing import Any, Optional

#: Benchmark / Health Check metric names (same spelling in both places).
CURRENT_RATIO = "current_ratio"
QUICK_RATIO = "quick_ratio"
INTEREST_COVERAGE = "interest_coverage"
LIQUIDITY_METRICS = frozenset({CURRENT_RATIO, QUICK_RATIO})
GATED_METRICS = frozenset({CURRENT_RATIO, QUICK_RATIO, INTEREST_COVERAGE})

# Current and quick ratio are meaningless for every industry below.
_NO_LIQUIDITY_INDUSTRIES = frozenset({
    # banks
    "banks",
    "banks - diversified",
    "banks - regional",
    # insurers (brokers too: fiduciary funds sit in their current assets and liabilities)
    "insurance - brokers",
    "insurance - diversified",
    "insurance - life",
    "insurance - property & casualty",
    "insurance - reinsurance",
    "insurance - specialty",
    # capital markets: broker-dealers and investment banks
    "financial - capital markets",
    "investment - banking & investment services",
    # asset managers (consolidated funds and insurers; the suffixed groups are mostly funds)
    "asset management",
    "asset management - bonds",
    "asset management - cryptocurrency",
    "asset management - global",
    "asset management - income",
    "asset management - leveraged",
    # lenders: card issuers and consumer/commercial credit, mortgage lenders and REITs.
    # Known cost: Visa and Mastercard sit in "Financial - Credit Services" and lose the rows
    # too — a missing row beats a meaningless verdict on every lender in the group.
    "financial - credit services",
    "financial - mortgages",
    "reit - mortgage",
    # financial holding companies
    "financial - conglomerates",
    "financial - diversified",
})

# Interest coverage is meaningless for the same industries EXCEPT insurance brokers: a
# broker (MMC, AON, AJG) is a fee business that borrows like any other, and its lenders
# watch exactly this ratio (review 2026-10-07: the first gate dropped it for them).
_NO_COVERAGE_INDUSTRIES = _NO_LIQUIDITY_INDUSTRIES - frozenset({"insurance - brokers"})

_DASH_RE = re.compile(r"\s*[-–—]\s*")
_SPACE_RE = re.compile(r"\s+")


def industry_key(industry: Any) -> str:
    """FMP industry name → lower-case, single-spaced, every dash as " - "."""
    if not isinstance(industry, str):
        return ""
    key = _DASH_RE.sub(" - ", industry.strip().lower())
    return _SPACE_RE.sub(" ", key).strip()


def liquidity_ratios_applicable(industry: Optional[str]) -> bool:
    """False when the current and quick ratio mean nothing for this industry."""
    return industry_key(industry) not in _NO_LIQUIDITY_INDUSTRIES


def interest_coverage_applicable(industry: Optional[str]) -> bool:
    """False when interest coverage means nothing for this industry."""
    return industry_key(industry) not in _NO_COVERAGE_INDUSTRIES


def peer_metric_applicable(metric: str, industry: Optional[str]) -> bool:
    """Whether ``metric`` is meaningful for a company in ``industry``. Every metric
    outside `GATED_METRICS` is applicable everywhere."""
    if metric in LIQUIDITY_METRICS:
        return liquidity_ratios_applicable(industry)
    if metric == INTEREST_COVERAGE:
        return interest_coverage_applicable(industry)
    return True
