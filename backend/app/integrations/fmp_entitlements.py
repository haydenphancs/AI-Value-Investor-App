"""FMP entitlement manifest — what the signed Order Form actually grants.

WHY THIS EXISTS
---------------
The Caydex FMP Enterprise Order Form buys **9 named Data Packages**. Anything outside
them is not licensed, and as of 2026-09-03 FMP **enforces** that: a call to an
unpurchased endpoint returns ``402`` with the body
``Restricted Endpoint: This endpoint is not available under your current subscription``.

Before enforcement, every path returned 200 regardless of entitlement, so "it works"
was worthless as evidence and the boundary had to be inferred from FMP's product names.
It no longer does. **Every entry below was verified by a live probe**, not inferred —
see ``scratchpad/fmp_enforcement_map.py``, which is re-runnable and is now the oracle.

Two traps this module encodes, both of which cost real debugging time:

1. **Doc slugs are not endpoint paths.** ``docs/stable/profile-symbol`` is served at
   ``/stable/profile``; ``peers`` -> ``/stable/stock-peers``; ``market-cap`` ->
   ``/stable/market-capitalization``; ``quote-change`` -> ``/stable/stock-price-change``.
   The names in the Order Form are FMP's *product* names and match neither.
2. **Some blocking is per-SYMBOL, not per-endpoint.** ``historical-price-eod/full`` is
   purchased and answers 200 for ``AAPL`` and ``SHOP.TO`` — and 402 for ``^GSPC``,
   ``GCUSD``, ``BTCUSD`` and ``EURUSD``. Index, commodity, crypto and FX are dead at the
   symbol level, so there is no endpoint to re-point; those asset classes need another
   source entirely. See ``BLOCKED_SYMBOL_PREFIXES`` / ``is_blocked_symbol``.

RE-ENABLING A DATASET LATER
---------------------------
Nothing here is deleted, and no calling code was removed. A dataset we have not bought
is **hidden**, not gone: the wrapper method stays in ``fmp.py``, the service that calls
it stays, and the only thing that changes is that ``_make_request`` refuses the call
early with :class:`FMPNotEntitledException` instead of paying for a 402.

So buying a package later is **one line**: add its name to :data:`PURCHASED_PACKAGES`.
Every path mapped to it in :data:`PACKAGE_OF` comes back on immediately — no code
change, no re-plumbing, no migration. That is the whole point of driving this from a
package set rather than a hand-maintained path allowlist.

This module is DATA ONLY — no imports from the rest of the app, no logic beyond pure
predicates — so ``tests/test_fmp_entitlement_parity.py`` can read it without dragging
in the service layer.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Optional

# --------------------------------------------------------------------------------------
# THE SWITCH. Everything else in this module is a lookup table; this is the only state.
# --------------------------------------------------------------------------------------

#: The 9 Data Packages on the signed Order Form, plus two groups FMP serves without
#: naming them. **To turn a dataset back on after buying it, add its name here.**
PURCHASED_PACKAGES: FrozenSet[str] = frozenset({
    "1 Fundamentals",
    "2 Earnings Calendar",
    "3 Company Information",
    "4 ETF",
    "6 Institutional Ownership",
    "7 Historical and Intraday",
    "8 Analyst Estimates",
    "9 Market News",
    "10 Insider & Senate",
    # Named in NO package, yet verified 200 under enforcement. FMP publishes symbol and
    # name search at "Full Global Coverage" on its FREE tier — it treats lookup as
    # baseline infrastructure. Deliberate product decision to keep using these.
    "Search & Directory (unnamed, serves 200)",
    "unnamed, serves 200",
})

#: Datasets FMP sells that we have NOT bought. Each name is exactly what to add to
#: PURCHASED_PACKAGES to light the feature back up.
UNPURCHASED_PACKAGES: FrozenSet[str] = frozenset({
    "Real-time Market Data",     # quote / batch-quote family — live prices
    "Market Performance",        # movers + sector/industry snapshots
    "Market Calendar",           # dividends, splits, IPO calendar
    "Analyst Ratings & Price Targets",
    "Indexes",                   # ^GSPC / ^IXIC / ^DJI and the constituent lists
    "Commodities",
    "Crypto",
    "Mutual Funds Holdings",
    "Earnings Call Transcripts",
    "ESG",
})


# --------------------------------------------------------------------------------------
# path -> the FMP package that grants it. Covers purchased AND unpurchased paths, so a
# single lookup answers "is this on?" and "what would turn it on?".
# --------------------------------------------------------------------------------------

PACKAGE_OF: Dict[str, str] = {
    # -- 1. Fundamentals -----------------------------------------------------------
    "income-statement": "1 Fundamentals",
    "balance-sheet-statement": "1 Fundamentals",
    "cash-flow-statement": "1 Fundamentals",
    "ratios": "1 Fundamentals",
    "ratios-ttm": "1 Fundamentals",
    "key-metrics": "1 Fundamentals",
    "key-metrics-ttm": "1 Fundamentals",
    "financial-growth": "1 Fundamentals",
    "financial-scores": "1 Fundamentals",
    "revenue-product-segmentation": "1 Fundamentals",
    "revenue-geographic-segmentation": "1 Fundamentals",
    "owner-earnings": "1 Fundamentals",
    "enterprise-values": "1 Fundamentals",
    "financial-reports-dates": "1 Fundamentals",
    # -- 2. Earnings Calendar ------------------------------------------------------
    # NOTE: this package is only 2 of FMP's 9 Calendar rows. `dividends`, `splits`,
    # `dividends-calendar` and `ipos-calendar` are NOT included — see BLOCKED_PATHS.
    "earnings": "2 Earnings Calendar",
    "earnings-calendar": "2 Earnings Calendar",
    # -- 3. Company Information ----------------------------------------------------
    "profile": "3 Company Information",
    "stock-peers": "3 Company Information",
    "shares-float": "3 Company Information",
    "historical-market-capitalization": "3 Company Information",
    "market-capitalization": "3 Company Information",
    "market-capitalization-batch": "3 Company Information",
    "key-executives": "3 Company Information",
    "employee-count": "3 Company Information",
    "delisted-companies": "3 Company Information",
    "mergers-acquisitions-latest": "3 Company Information",
    "exchange-market-hours": "3 Company Information",
    "all-exchange-market-hours": "3 Company Information",
    # -- 4. ETF --------------------------------------------------------------------
    "etf/info": "4 ETF",
    "etf/holdings": "4 ETF",
    "etf/sector-weightings": "4 ETF",
    "etf/country-weightings": "4 ETF",
    "etf/asset-exposure": "4 ETF",
    # -- 6. Institutional Ownership (Form 13F) -------------------------------------
    "institutional-ownership/dates": "6 Institutional Ownership",
    "institutional-ownership/extract": "6 Institutional Ownership",
    "institutional-ownership/extract-analytics/holder": "6 Institutional Ownership",
    "institutional-ownership/holder-industry-breakdown": "6 Institutional Ownership",
    "institutional-ownership/holder-performance-summary": "6 Institutional Ownership",
    "institutional-ownership/symbol-positions-summary": "6 Institutional Ownership",
    "institutional-ownership/industry-summary": "6 Institutional Ownership",
    # -- 7. Historical and Intraday ------------------------------------------------
    # Entitled for equities and international symbols ONLY — see BLOCKED_SYMBOL_PREFIXES.
    # Whole-market OHLCV for one session, 65,690 rows in one call. Verified 200.
    # ⚠️ Serves ^GSPC / GCUSD / BTCUSD / EURUSD even though the per-symbol endpoint
    # 402s them — callers MUST filter with is_blocked_symbol().
    "batch-eod": "7 Historical and Intraday",
    "historical-price-eod/full": "7 Historical and Intraday",
    "historical-price-eod/light": "7 Historical and Intraday",
    "historical-price-eod/non-split-adjusted": "7 Historical and Intraday",
    "historical-price-eod/dividend-adjusted": "7 Historical and Intraday",
    "historical-chart/1min": "7 Historical and Intraday",
    "historical-chart/5min": "7 Historical and Intraday",
    "historical-chart/15min": "7 Historical and Intraday",
    "historical-chart/30min": "7 Historical and Intraday",
    "historical-chart/1hour": "7 Historical and Intraday",
    "historical-chart/4hour": "7 Historical and Intraday",
    # -- 8. Analyst Estimates ------------------------------------------------------
    # ONE endpoint, not the 8-endpoint Analyst section. `grades` and `price-target-*`
    # are NOT included — see BLOCKED_PATHS.
    "analyst-estimates": "8 Analyst Estimates",
    # -- 9. Market News ------------------------------------------------------------
    "news/stock": "9 Market News",
    "news/general-latest": "9 Market News",
    "news/crypto": "9 Market News",
    "news/press-releases": "9 Market News",
    # -- 10. Insider Trading & Senate Disclosure -----------------------------------
    "insider-trading/search": "10 Insider & Senate",
    "insider-trading/statistics": "10 Insider & Senate",
    "acquisition-of-beneficial-ownership": "10 Insider & Senate",
    "senate-latest": "10 Insider & Senate",
    "house-latest": "10 Insider & Senate",
    "senate-trades": "10 Insider & Senate",
    "house-trades": "10 Insider & Senate",
    # -- Search & Directory --------------------------------------------------------
    # Named in NO package, yet verified 200 under enforcement. FMP publishes symbol and
    # name search at "Full Global Coverage" on its FREE tier, i.e. it treats lookup as
    # baseline infrastructure. Deliberate product decision to keep using these.
    "search-symbol": "Search & Directory (unnamed, serves 200)",
    "search-name": "Search & Directory (unnamed, serves 200)",
    "search-cik": "Search & Directory (unnamed, serves 200)",
    "search-isin": "Search & Directory (unnamed, serves 200)",
    "company-screener": "Search & Directory (unnamed, serves 200)",
    "stock-list": "Search & Directory (unnamed, serves 200)",
    "available-industries": "Search & Directory (unnamed, serves 200)",
    "available-sectors": "Search & Directory (unnamed, serves 200)",
    # -- Valuation / scoring (unnamed but serving 200) ------------------------------
    "ratings-snapshot": "unnamed, serves 200",
    "ratings-historical": "unnamed, serves 200",
    "discounted-cash-flow": "unnamed, serves 200",
    "levered-discounted-cash-flow": "unnamed, serves 200",
}


# --------------------------------------------------------------------------------------
# Paths we call (or once called) that belong to a package we have NOT bought.
# Mapped to the package that WOULD grant them, so re-enabling is a one-line change.
# --------------------------------------------------------------------------------------

PACKAGE_OF.update({
    # -- Real-time Market Data -----------------------------------------------------
    "quote": "Real-time Market Data",
    "batch-quote": "Real-time Market Data",
    "batch-quote-short": "Real-time Market Data",
    "aftermarket-quote": "Real-time Market Data",
    "stock-price-change": "Real-time Market Data",
    # -- Market Performance --------------------------------------------------------
    "biggest-gainers": "Market Performance",
    "biggest-losers": "Market Performance",
    "most-actives": "Market Performance",
    "sector-performance-snapshot": "Market Performance",
    "industry-performance-snapshot": "Market Performance",
    "sector-pe-snapshot": "Market Performance",
    "industry-pe-snapshot": "Market Performance",
    "historical-sector-performance": "Market Performance",
    # -- Market Calendar (package 2 buys only 2 of its 9 rows) ---------------------
    "dividends": "Market Calendar",
    "splits": "Market Calendar",
    "dividends-calendar": "Market Calendar",
    "ipos-calendar": "Market Calendar",
    # -- Analyst Ratings & Price Targets (package 8 buys only analyst-estimates) ---
    "grades": "Analyst Ratings & Price Targets",
    "grades-historical": "Analyst Ratings & Price Targets",
    "grades-consensus": "Analyst Ratings & Price Targets",
    "grades-news": "Analyst Ratings & Price Targets",
    "grades-latest-news": "Analyst Ratings & Price Targets",
    "price-target-consensus": "Analyst Ratings & Price Targets",
    "price-target-summary": "Analyst Ratings & Price Targets",
    "price-target-news": "Analyst Ratings & Price Targets",
    "price-target-latest-news": "Analyst Ratings & Price Targets",
    # -- Indexes -------------------------------------------------------------------
    "sp500-constituent": "Indexes",
    "dowjones-constituent": "Indexes",
    "nasdaq-constituent": "Indexes",
    # -- Other unbought datasets ---------------------------------------------------
    "earning-call-transcript": "Earnings Call Transcripts",
    "earning-call-transcript-dates": "Earnings Call Transcripts",
    "funds/disclosure": "Mutual Funds Holdings",
    "funds/disclosure-holders-search": "Mutual Funds Holdings",
    "esg-disclosures": "ESG",
})


#: What to use instead, per blocked path. Surfaced in the exception message so the fix
#: is visible at the failure site rather than buried in a plan document.
SUBSTITUTION: Dict[str, str] = {
    "quote": "profile (carries a live price) or historical-chart/1min",
    "batch-quote": "company-screener for breadth; market-capitalization-batch for caps",
    "batch-quote-short": "company-screener",
    "stock-price-change": "compute from historical-price-eod/full",
    "biggest-gainers": "company-screener + a stored previous-close snapshot",
    "biggest-losers": "company-screener + a stored previous-close snapshot",
    "most-actives": "company-screener, ranked by volume or volume/avgVolume",
    "sector-performance-snapshot": "group company-screener rows by sector",
    "industry-performance-snapshot": "group company-screener rows by industry",
    "dividends": "ratios-ttm dividend fields + profile.lastDividend (TTM only)",
    "splits": "historical-price-eod/non-split-adjusted vs /full — the ratio is the split",
    "grades": "no entitled substitute — render an honest empty state",
    "price-target-consensus": "no entitled substitute — render an honest empty state",
    "sp500-constituent": "backend/data/benchmark_universe.json",
    "earning-call-transcript": "no entitled substitute — omit the section",
}


def is_entitled(path: str) -> bool:
    """True when ``path`` belongs to a package on :data:`PURCHASED_PACKAGES`."""
    return PACKAGE_OF.get(normalize_path(path)) in PURCHASED_PACKAGES


#: Paths a purchased package grants. Derived — never hand-maintained.
ENTITLED_PATHS: FrozenSet[str] = frozenset(
    p for p, pkg in PACKAGE_OF.items() if pkg in PURCHASED_PACKAGES
)

#: Paths we know of that no purchased package grants. Also derived, so the two sets
#: can never drift apart or overlap.
BLOCKED_PATHS: Dict[str, str] = {
    p: pkg for p, pkg in PACKAGE_OF.items() if pkg not in PURCHASED_PACKAGES
}



# Retired v3/v4 paths: 404, NOT an entitlement problem. Kept so a 404 is never
# misdiagnosed as a licence issue. The first two have entitled stable replacements.
RETIRED_PATHS: Dict[str, str] = {
    "senate-disclosure": "404. Replaced by senate-trades (entitled).",
    "house-disclosure": "404. Replaced by house-trades (entitled).",
    "sec_filings": "404. No caller.",
    "company-outlook": "404. No caller.",
    "social-sentiments/change": "404. No caller.",
    "social-sentiments/historical": "404. No caller.",
    "stock-news-sentiments-rss-feed": "404. No caller.",
}


# --------------------------------------------------------------------------------------
# Symbol-level blocking — applies even to endpoints we DO own.
# --------------------------------------------------------------------------------------

# Index tickers all start with "^" (^GSPC, ^IXIC, ^DJI, ^VIX, ^TNX).
BLOCKED_SYMBOL_PREFIXES: FrozenSet[str] = frozenset({"^"})

# FMP commodity codes. All end in "USD", which is also the crypto-pair shape, so the
# commodity set is enumerated explicitly and crypto is caught by the suffix rule below.
BLOCKED_COMMODITY_SYMBOLS: FrozenSet[str] = frozenset({
    "GCUSD", "SIUSD", "CLUSD", "NGUSD", "HGUSD", "PLUSD", "PAUSD",
    "ZWUSD", "ZCUSD", "ZSUSD", "KCUSD", "SBUSD", "CCUSD", "CTUSD",
})

# Crypto pairs (BTCUSD, ETHUSD, ...) and FX pairs (EURUSD, USDJPY, ...) are both blocked.
# Anything ending in a major fiat code that is not a known equity is unavailable.
BLOCKED_SYMBOL_SUFFIXES: FrozenSet[str] = frozenset({"USD", "JPY", "EUR", "GBP", "CNY"})

# Endpoints where a symbol is looked up as market data, so symbol-level blocking bites.
# Deliberately narrow: `profile` accepts an index symbol and simply returns [], and
# `news/crypto` is entitled Market News, so neither belongs here.
SYMBOL_SENSITIVE_PREFIXES = ("historical-price-eod", "historical-chart")


def is_blocked_symbol(symbol: Optional[str]) -> bool:
    """True when this symbol's market data is outside the licence.

    Covers indices (``^GSPC``), FMP commodity codes (``GCUSD``), crypto pairs
    (``BTCUSD``) and FX pairs (``EURUSD``) — all verified 402 on endpoints that are
    otherwise entitled. US and international equities are fine: the packages carry
    "60+ Global Exchanges" and ``SHOP.TO`` is verified 200.
    """
    if not symbol:
        return False
    s = symbol.strip().upper()
    if not s:
        return False
    if any(s.startswith(p) for p in BLOCKED_SYMBOL_PREFIXES):
        return True
    if s in BLOCKED_COMMODITY_SYMBOLS:
        return True
    # A 6-char pair like BTCUSD / EURUSD. Guard on length so a real equity whose
    # ticker merely ends in "USD" is not swept up.
    if len(s) >= 6 and any(s.endswith(x) for x in BLOCKED_SYMBOL_SUFFIXES):
        return True
    return False


def normalize_path(endpoint: str) -> str:
    """Strip query/leading slash and collapse the two templated path families.

    ``historical-chart/{interval}`` is formatted at the call site, so the concrete
    path (``historical-chart/5min``) is what reaches ``_make_request``. Both the
    template and the concrete form must resolve to the same manifest key.
    """
    p = (endpoint or "").split("?", 1)[0].strip().strip("/")
    if p.startswith("historical-chart/"):
        interval = p.split("/", 1)[1]
        if interval.startswith("{"):
            return "historical-chart/5min"  # representative member of the family
    return p


def entitlement_error(path: str) -> Optional[str]:
    """A human-readable reason when ``path`` is unusable, else ``None``.

    The message names the package that would unlock it and the entitled substitute, so
    whoever hits this at 2am can act on it without opening a plan document.
    """
    p = normalize_path(path)
    if p in BLOCKED_PATHS:
        package = BLOCKED_PATHS[p]
        msg = (
            f"{p!r} needs the FMP '{package}' package, which is not on the Order Form "
            f"(FMP answers 402 Restricted Endpoint)."
        )
        sub = SUBSTITUTION.get(p)
        if sub:
            msg += f" Use instead: {sub}."
        msg += (
            f" To re-enable after buying it, add '{package}' to PURCHASED_PACKAGES in "
            f"app/integrations/fmp_entitlements.py — nothing else changes."
        )
        return msg
    if p in RETIRED_PATHS:
        return f"{p!r} is a retired FMP path (404): {RETIRED_PATHS[p]}"
    return None
