"""
Stock Endpoints — All data from FMP API (no local stocks table).
Frontend: GET /stocks/search, /stocks/{ticker}, /stocks/{ticker}/quote,
          /stocks/{ticker}/overview, /stocks/{ticker}/fundamentals,
          /stocks/{ticker}/chart, /stocks/{ticker}/financials-full,
          /stocks/{ticker}/news
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import asyncio
import logging
import re

from app.integrations.coingecko import SYMBOL_TO_COINGECKO_ID
from app.integrations.fmp import get_fmp_client, FMPClient
from app.integrations.finra_short_interest import get_short_interest
from app.schemas.common import normalize_fmp_response, normalize_fmp_list
from app.api.error_response import (
    ErrorCode,
    error_response_from_exception,
    make_error_response,
    upstream_error_response,
)
from app.schemas.news import (
    MAX_ENRICH_ARTICLE_IDS,
    EnrichNewsResponse,
    TickerNewsFeedResponse,
    news_articles_from_rows,
    news_feed_from_payload,
    sanitize_article_ids,
)
from app.schemas.stock import StockSearchResult
from app.schemas.stock_overview import StockOverviewResponse, StockOverviewCoreResponse
from app.schemas.analyst import AnalystAnalysisResponse
from app.schemas.sentiment import SentimentAnalysisResponse
from app.schemas.technical_analysis import (
    TechnicalAnalysisResponse,
    TechnicalAnalysisDetailResponse,
)
from app.services.stock_overview_service import get_stock_overview_service
from app.services.analyst_service import get_analyst_service
from app.services.earnings_service import get_earnings_service
from app.schemas.earnings import EarningsResponse
from app.schemas.growth import GrowthResponse
from app.services.growth_service import get_growth_service
from app.schemas.profit_power import ProfitPowerResponse
from app.services.profit_power_service import get_profit_power_service
from app.services.sentiment_service import get_sentiment_service
from app.services.technical_analysis_service import get_technical_analysis_service
from app.schemas.revenue_breakdown import RevenueBreakdownResponse
from app.services.revenue_breakdown_service import get_revenue_breakdown_service
from app.schemas.health_check import HealthCheckResponse
from app.services.health_check_service import get_health_check_service
from app.schemas.signal_of_confidence import SignalOfConfidenceResponse
from app.services.signal_of_confidence_service import get_signal_of_confidence_service
from app.schemas.holders import HoldersResponse
from app.services.holders_service import get_holders_service
from app.config import settings
from app.dependencies import get_current_user_or_guest, StandardRateLimit
from app.services.ticker_data_cache import warm_ticker_collection

logger = logging.getLogger(__name__)

router = APIRouter()

# Ticker validation pattern: 1-10 uppercase letters, digits, dots, or hyphens
_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,10}$")

# Strong refs to fire-and-forget prewarm tasks so they aren't GC'd mid-flight
# (un-referenced asyncio tasks can be garbage-collected and cancelled).
_PREWARM_TASKS: set = set()


def _validate_ticker(ticker: str) -> str:
    """Validate and normalise a ticker symbol. Raises 422 on bad input."""
    t = ticker.strip().upper()
    if not t or not _TICKER_RE.match(t):
        raise HTTPException(status_code=422, detail="Invalid ticker symbol")
    return t


@router.post("/{ticker}/prewarm-report")
async def prewarm_report_collection(
    ticker: str,
    user: dict = Depends(get_current_user_or_guest),
    _rate_limit=StandardRateLimit,
):
    """Best-effort: warm the persona-neutral ticker_data_cache for `ticker` so a
    later Generate Analysis skips re-collecting it.

    iOS fires this fire-and-forget when a user opens the detail view. Returns
    202 immediately; the warm runs in the background — freshness-guarded +
    _INFLIGHT-deduped + _WARM_SEMAPHORE-bounded, and NEVER blocks the caller.

    IMPORTANT: the warm runs the FULL persona-NEUTRAL collection — the ~20-call
    FMP fan-out PLUS the persona-neutral grounded precompute, which for a COLD
    ticker makes some Gemini-grounded calls (moat grounding, big-mover price
    catalyst, a shared market-wide geopolitical scan). It skips only the
    per-persona Stage-A/Stage-B work. So this SHIFTS report work earlier (a
    latency win + bounded Gemini spend), it does NOT raise report throughput.
    Guarded against quota drain by StandardRateLimit + a REPORT_PREWARM_MAX_INFLIGHT
    cap on concurrent background warms.
    """
    t = ticker.strip().upper()
    if not _TICKER_RE.match(t):
        # Soft-skip on a best-effort path — never 422 a fire-and-forget warm.
        return JSONResponse(status_code=202, content={"ticker": t, "status": "skipped"})
    if not settings.REPORT_PREWARM_ON_VIEW_ENABLED:
        return JSONResponse(status_code=202, content={"ticker": t, "status": "disabled"})
    # Shed load past a safe in-flight bound so a distinct-cold-ticker burst
    # can't pile up background warms (each of which can spend Gemini quota).
    if len(_PREWARM_TASKS) >= settings.REPORT_PREWARM_MAX_INFLIGHT:
        return JSONResponse(status_code=202, content={"ticker": t, "status": "busy"})

    task = asyncio.create_task(warm_ticker_collection(t))
    _PREWARM_TASKS.add(task)
    task.add_done_callback(_PREWARM_TASKS.discard)
    return JSONResponse(status_code=202, content={"ticker": t, "status": "warming"})


# Major US stock exchanges — used to filter search results.
_US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX"}

# Crypto exchanges returned by FMP
_CRYPTO_EXCHANGES = {"CRYPTO", "CCC", "CC", "CRYPTOCURRENCY"}

# Crypto display names derived from CoinGecko ID map (zero API cost)
_CRYPTO_NAMES: Dict[str, str] = {
    sym: cg_id.replace("-", " ").title()
    for sym, cg_id in SYMBOL_TO_COINGECKO_ID.items()
}
# Override common names that don't title-case well from IDs
_CRYPTO_NAMES.update({
    "BTC": "Bitcoin", "ETH": "Ethereum", "BNB": "BNB",
    "XRP": "XRP", "SOL": "Solana", "DOGE": "Dogecoin",
    "ADA": "Cardano", "TRX": "TRON", "AVAX": "Avalanche",
    "DOT": "Polkadot", "SHIB": "Shiba Inu", "TON": "Toncoin",
    "LINK": "Chainlink", "XLM": "Stellar", "HBAR": "Hedera",
    "BCH": "Bitcoin Cash", "LTC": "Litecoin", "UNI": "Uniswap",
    "NEAR": "NEAR Protocol", "AAVE": "Aave", "PEPE": "Pepe",
    "TAO": "Bittensor", "ICP": "Internet Computer",
    "ETC": "Ethereum Classic", "RENDER": "Render",
    "POL": "Polygon", "MATIC": "Polygon", "APT": "Aptos",
    "MNT": "Mantle", "KAS": "Kaspa", "ATOM": "Cosmos",
    "FIL": "Filecoin", "ARB": "Arbitrum", "VET": "VeChain",
    "FET": "Artificial Superintelligence Alliance",
    "ONDO": "Ondo", "WLD": "Worldcoin", "ALGO": "Algorand",
    "OP": "Optimism", "CRO": "Cronos", "JUP": "Jupiter",
    "BONK": "Bonk", "STX": "Stacks", "INJ": "Injective",
    "SEI": "Sei", "IMX": "Immutable X", "GRT": "The Graph",
    "SUI": "Sui", "THETA": "Theta", "RUNE": "THORChain",
    "FTM": "Fantom", "FLOKI": "Floki", "TIA": "Celestia",
    "PYTH": "Pyth Network", "QNT": "Quant", "ENA": "Ethena",
    "SAND": "The Sandbox", "MANA": "Decentraland",
    "AXS": "Axie Infinity", "GALA": "Gala", "FLOW": "Flow",
    "ENS": "Ethereum Name Service", "CHZ": "Chiliz",
    "PENDLE": "Pendle", "CAKE": "PancakeSwap",
    "EOS": "EOS", "NEO": "Neo", "XTZ": "Tezos",
    "IOTA": "IOTA", "COMP": "Compound", "SNX": "Synthetix",
    "CRV": "Curve DAO", "DYDX": "dYdX", "GMX": "GMX",
    "1INCH": "1inch", "SUSHI": "SushiSwap",
    "WIF": "dogwifhat", "JASMY": "JasmyCoin",
    "TRUMP": "Official Trump", "PI": "Pi Network",
    "HYPE": "Hyperliquid", "VIRTUAL": "Virtuals Protocol",
    "PENGU": "Pudgy Penguins", "XMR": "Monero",
    "DASH": "Dash", "ZEC": "Zcash",
})


def _get_exchange_short_name(item: Dict[str, Any]) -> Optional[str]:
    """
    Extract the short exchange name (NYSE / NASDAQ / AMEX) from an FMP result.

    FMP APIs return exchange info in varying fields depending on the endpoint
    and API version (stable vs legacy):
      - "exchangeShortName" -> short name  (e.g. "NASDAQ")
      - "exchange"          -> may be short or full
                               (e.g. "NASDAQ" or "NASDAQ Global Select Market")

    This helper normalises both variants.
    """
    # Prefer the explicit short-name field
    short = (item.get("exchangeShortName") or "").strip()
    if short:
        return short

    # Fall back to the generic "exchange" field
    exchange = (item.get("exchange") or "").strip()
    if exchange.upper() in _US_EXCHANGES:
        return exchange

    # Check if the full name contains a known US exchange
    upper = exchange.upper()
    for ex in _US_EXCHANGES:
        if ex in upper:
            return ex

    return exchange or None


def _is_us_listed(item: Dict[str, Any]) -> bool:
    """Return True if the FMP result is listed on a US exchange."""
    symbol = item.get("symbol", "")

    # Skip international suffixes (APC.F, AAPL.MX, etc.)
    if "." in symbol:
        return False

    short = _get_exchange_short_name(item)
    return (short or "").upper() in _US_EXCHANGES


def _is_crypto(item: Dict[str, Any]) -> bool:
    """Return True if the FMP result is a cryptocurrency."""
    short = _get_exchange_short_name(item)
    return (short or "").upper() in _CRYPTO_EXCHANGES


# ── Asset-type classification (name-based) ──────────────────────────────
# "ETF"/"ETN"/"Fund" as WHOLE WORDS (singular OR plural — "ETNs", "Funds"). A
# substring test wrongly matched company names, and a substring "trust" matched
# real REITs/banks (see the corporate-entity override below).
_ETF_NAME_RE = re.compile(r"\b(?:etfs?|etns?)\b", re.IGNORECASE)
_FUND_NAME_RE = re.compile(r"\bfunds?\b", re.IGNORECASE)

# Corporate-entity markers (whole word). Their presence means the row is the
# ISSUER'S OWN OPERATING STOCK, not one of its funds — this is what rescues
# Invesco Ltd. (IVZ), The Charles Schwab Corporation (SCHW), and Northern Trust
# Corporation (NTRS) from being mislabeled "etf"/"fund" (via the issuer-brand /
# "trust" keywords below) and silently dropped from the company picker, which
# filters to type == "stock". This override runs BEFORE the issuer-brand check,
# so a brand keyword can never hide the operating company that owns the brand.
_CORP_ENTITY_RE = re.compile(
    r"\b(?:inc|incorporated|corp|corporation|co|company|plc|ltd|limited|"
    r"bancorp|bancshares|holdings?|group|ag|se|sa|nv|llc|lp)\b",
    re.IGNORECASE,
)

# Fund-family brands. Many fund/ETP names carry NO "ETF"/"Fund" word — e.g.
# "Invesco QQQ Trust, Series 1" (QQQ, the 3rd-largest ETF), "Sprott Physical Gold
# Trust", "SPDR Gold Shares", "ProShares Short QQQ", "BlackRock High Yield K".
# These would otherwise fall through to "stock" and pollute the company picker.
# It is SAFE to list brands that DO have a listed operating company (invesco →
# IVZ, schwab → SCHW, blackrock → BLK, sprott → SII, wisdomtree → WT), because
# the corporate-entity override above fires first for the operating company's
# own name ("Invesco Ltd." → stock) — the brand keyword only catches the funds.
_ETF_ISSUER_KEYWORDS = (
    "proshares", "ishares", "spdr", "direxion", "wisdomtree", "vaneck",
    "invesco", "schwab", "vanguard", "blackrock", "fidelity", "pimco",
    "nuveen", "sprott", "grayscale", "graniteshares", "roundhill", "defiance",
    "abrdn", "global x", "first trust", "franklin templeton", "dimensional",
    "janus henderson", "eaton vance",
)


def _get_asset_type(item: Dict[str, Any]) -> Optional[str]:
    """Determine the asset type for a search result. Returns None if it should be excluded.

    Order matters: an explicit ETF/Fund word wins, THEN a corporate-entity marker
    forces "stock" (so an asset-manager's own stock isn't hidden by its brand
    keyword), THEN issuer-brand ETFs without an "ETF" word, THEN indices.
    """
    if _is_crypto(item):
        return "crypto"
    if not _is_us_listed(item):
        return None  # Skip international listings

    name = str(item.get("name") or "")  # coerce: FMP occasionally sends non-str
    name_lower = name.lower()

    # 1) Unambiguous fund/ETF words win outright.
    if _ETF_NAME_RE.search(name):
        return "etf"
    if _FUND_NAME_RE.search(name):
        return "fund"
    # 2) A corporate entity is the operating company's stock — even if a brand
    #    keyword ("invesco", "schwab") or "trust" (banks/REITs) appears in it.
    if _CORP_ENTITY_RE.search(name):
        return "stock"
    # 3) Brand ETFs whose names omit "ETF" (ProShares Short QQQ, SPDR Gold Shares).
    if any(kw in name_lower for kw in _ETF_ISSUER_KEYWORDS):
        return "etf"
    # 4) Bare index products.
    if "index" in name_lower:
        return "fund"
    return "stock"


# ── Secondary-listing de-duplication ────────────────────────────────────
# FMP search returns corporate-action securities alongside the primary listing,
# all sharing the issuer's company name — confusing identical rows, and (worse)
# they classify as "stock" so a user could pick a warrant/unit/right and run the
# company pipeline on it. Two encodings occur:
#   • NASDAQ 5th-letter: when-issued "V" (SNDK→SNDKV), warrant "W" (BGRY→BGRYW),
#     unit "U" (SVNA→SVNAU), right "R" (RFAC→RFACR).
#   • NYSE dash form: "-WT"/"-WS" warrant, "-UN"/"-U" unit, "-RT"/"-R" right,
#     "-WI" when-issued (APCA → APCA-WT / APCA-UN).
# We drop such a row ONLY when its BASE symbol (the row minus the suffix) is ALSO
# present with the SAME normalized name + exchange + type — i.e. it's a redundant
# twin of a listing we already show. This never collapses legitimate dual-class
# shares: GOOGL/GOOG differ by "L" (not a suffix) and BRK-A/BRK-B by "-A"/"-B"
# (not an action suffix); Z/ZG carry distinct names. A standalone V/W/U/R ticker
# with no same-named base (Visa "V", Veritiv "VRTV", Nu "NU", Baidu "BIDU",
# Progressive "PGR") is untouched — its base is a DIFFERENT company.
_SECONDARY_SUFFIXES = ("V", "W", "U", "R")
_DASH_ACTION_SUFFIX_RE = re.compile(r"[.\-](?:wt|ws|un|u|rt|r|wi|w)$", re.IGNORECASE)
# Security-class / corporate-action descriptors FMP appends INCONSISTENTLY across
# a company's securities — e.g. the common is "RF Acquisition Corp II Ordinary
# Shares" while its unit/right rows are just "RF Acquisition Corp II". Stripping
# these so both key to the same company is SAFE: a twin only collapses when it
# ALSO carries a V/W/U/R (or dash-action) suffix, which legitimate dual-class
# shares (GOOGL/GOOG, Zillow "Class A"/"Class C") never do — so the name key is
# only a confirmation, never the sole trigger.
_NAME_DESCRIPTOR_RE = re.compile(
    r"\b(?:units?|warrants?|rights?|when[\s-]?issued|wi"
    r"|ordinary\s+shares?|common\s+stock|common\s+shares?"
    r"|depositary\s+shares?|class\s+[a-z])\b",
    re.IGNORECASE,
)


def _normalize_company_name(name: Optional[str]) -> str:
    """Lowercase, drop trailing corporate-action descriptors + punctuation, and
    collapse whitespace — so a twin ("IB Acquisition Corp. Unit") keys to the
    same company as its base ("IB Acquisition Corp.")."""
    n = (name or "").lower()
    n = _NAME_DESCRIPTOR_RE.sub(" ", n)
    n = re.sub(r"[.,]", " ", n)
    return " ".join(n.split())


def _secondary_base_symbol(sym: str) -> Optional[str]:
    """The primary/base ticker a corporate-action symbol derives from, or None.

    ``APCA-WT``/``APCA-UN`` → ``APCA``; ``SNDKV``/``SVNAU``/``RFACR`` → drop the
    5th letter. Returns None for a symbol with no recognized action suffix.
    """
    m = _DASH_ACTION_SUFFIX_RE.search(sym)
    if m:
        return sym[:m.start()]
    if len(sym) >= 2 and sym[-1] in _SECONDARY_SUFFIXES:
        return sym[:-1]
    return None


def _dedupe_secondary_listings(
    results: List[StockSearchResult],
    keep_symbol: str = "",
) -> List[StockSearchResult]:
    """Remove when-issued / warrant / unit / right twins that duplicate a
    primary listing.

    ``keep_symbol`` (upper-case) is never dropped — protects a ticker the user
    typed verbatim.
    """
    keep = (keep_symbol or "").upper()
    present: Dict[tuple, set] = {}
    for r in results:
        key = (_normalize_company_name(r.name),
               (r.exchange_short_name or "").upper(), r.type)
        present.setdefault(key, set()).add((r.symbol or "").upper())

    deduped: List[StockSearchResult] = []
    for r in results:
        sym = (r.symbol or "").upper()
        base = _secondary_base_symbol(sym) if sym != keep else None
        if base:
            key = (_normalize_company_name(r.name),
                   (r.exchange_short_name or "").upper(), r.type)
            if base in present.get(key, ()):
                continue  # redundant secondary twin of a primary we're showing
        deduped.append(r)
    return deduped


@router.get("/search", response_model=List[StockSearchResult])
async def search_stocks(
    q: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
):
    """Search stocks and crypto by ticker or name. Crypto matched from local map (zero API cost)."""
    fmp = get_fmp_client()
    try:
        # ── Match crypto from hardcoded map (instant, free) ──
        query_upper = q.upper().strip()
        query_lower = q.lower().strip()
        crypto_results: List[StockSearchResult] = []

        # Whitespace-only query (min_length=1 lets " " through): the empty string
        # is a substring of every symbol/name, so the crypto loop below would
        # match the ENTIRE map and we'd fire a blank FMP query. Return empty.
        if not query_upper:
            return []

        # A 1-char query would substring-match a huge fraction of the crypto map
        # (e.g. "a" hits ~60 coins), flooding the results. For a 1-char query,
        # only an EXACT symbol match (e.g. "V") is meaningful. (The old
        # `sym.startswith(query_upper)` clause was redundant — a strict subset of
        # the `query_upper in sym` substring test.)
        for sym, name in _CRYPTO_NAMES.items():
            if len(query_upper) >= 2:
                matched = query_upper in sym or query_lower in name.lower()
            else:
                matched = sym == query_upper
            if matched:
                crypto_results.append(StockSearchResult(
                    symbol=sym,
                    name=name,
                    currency="USD",
                    exchange_short_name="CRYPTO",
                    exchange_full_name="Cryptocurrency",
                    type="crypto",
                ))

        # Exact symbol match goes first
        crypto_results.sort(
            key=lambda r: (0 if r.symbol == query_upper else 1, r.symbol)
        )

        # ── FMP search for stocks/ETFs ──
        raw = await fmp.search_stocks(q, limit=max(limit * 3, 30))

        stock_results: List[StockSearchResult] = []
        seen_stock_symbols: set = set()

        for item in (raw or []):
            # Per-row resilience: a malformed FMP row (non-dict element, or a
            # non-string name that breaks .lower()) must NOT 502 the whole
            # search and lose every other valid result — skip it and continue.
            try:
                if not isinstance(item, dict):
                    continue
                symbol = item.get("symbol") or ""
                if not symbol or symbol in seen_stock_symbols:
                    continue  # empty / duplicate FMP stock symbol

                asset_type = _get_asset_type(item)
                if asset_type is None or asset_type == "crypto":
                    continue  # international, or prefer our own crypto results

                short_name = _get_exchange_short_name(item)
                stock_results.append(StockSearchResult(
                    symbol=symbol,
                    name=str(item.get("name") or ""),
                    currency=item.get("currency"),
                    exchange_short_name=short_name,
                    # FMP /stable rows carry the FULL name in exchangeFullName;
                    # "exchange" is the short code. (stockExchange is legacy.)
                    exchange_full_name=(
                        item.get("exchangeFullName")
                        or item.get("stockExchange")
                        or item.get("exchange")
                    ),
                    type=asset_type,
                ))
                seen_stock_symbols.add(symbol)
            except Exception as row_err:
                logger.warning(
                    f"Skipping malformed search row for q={q!r}: "
                    f"{type(row_err).__name__}: {row_err}"
                )
                continue

        # Drop when-issued / warrant / unit / right twins that duplicate a
        # primary listing (SNDK vs SNDKV). keep_symbol protects a ticker the user
        # typed exactly — if they asked for "SNDKV", show it, don't swap it.
        stock_results = _dedupe_secondary_listings(
            stock_results, keep_symbol=query_upper
        )

        # A real company must NEVER be shadowed by a crypto sharing its ticker:
        # "STX" is Seagate AND Stacks, "SUI" is Sun Communities AND the coin.
        # Drop the crypto duplicate for any symbol we found as a stock so the
        # company (which the picker filters to) is always present.
        stock_symbols = {r.symbol.upper() for r in stock_results}
        crypto_results = [
            c for c in crypto_results if c.symbol.upper() not in stock_symbols
        ]

        # ── Merge: exact-symbol crypto first, then stocks, then other crypto ──
        exact_crypto = [r for r in crypto_results if r.symbol == query_upper]
        other_crypto = [r for r in crypto_results if r.symbol != query_upper]
        combined = exact_crypto + stock_results + other_crypto
        return combined[:limit]

    except Exception as e:
        # Honor the iOS APIErrorResponse contract for known upstream failures
        # (FMP rate-limit / unavailable) instead of a bare generic 502 that iOS
        # can only render as "Server error" — mirrors the sibling detail handlers.
        if (resp := upstream_error_response(e, step="stock_search")) is not None:
            return resp
        logger.error(f"Stock search failed for q={q!r}: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Stock search service unavailable")


@router.get("/{ticker}")
async def get_stock_details(ticker: str):
    """Get detailed company profile from FMP, enriched with ownership & valuation data."""
    ticker = _validate_ticker(ticker)
    fmp = get_fmp_client()
    try:
        # Fetch profile, shares float, analyst estimates, institutional ownership, and short interest in parallel
        results = await asyncio.gather(
            fmp.get_company_profile(ticker),
            fmp.get_shares_float(ticker),
            fmp.get_analyst_estimates(ticker, period="annual", limit=5),
            fmp.get_institutional_ownership_summary(ticker),
            get_short_interest(ticker),
            return_exceptions=True,
        )

        profile = results[0] if not isinstance(results[0], Exception) else {}
        shares_float = results[1] if not isinstance(results[1], Exception) else {}
        analyst_est = results[2] if not isinstance(results[2], Exception) else []
        inst_summary = results[3] if not isinstance(results[3], Exception) else []
        short_data = results[4] if not isinstance(results[4], Exception) else {}

        if not profile:
            raise HTTPException(status_code=404, detail=f"Stock {ticker} not found")

        response = normalize_fmp_response(profile)

        # Ensure iOS-expected field names exist (stable API changed names)
        if "last_dividend" in response and "last_div" not in response:
            response["last_div"] = response["last_dividend"]
        if "average_volume" in response and "vol_avg" not in response:
            response["vol_avg"] = response["average_volume"]

        # Float & insider % from shares-float endpoint
        if isinstance(shares_float, dict) and shares_float:
            float_shares = shares_float.get("floatShares")
            free_float = shares_float.get("freeFloat")
            if float_shares is not None:
                response["float_shares"] = float(float_shares)
            if free_float is not None:
                response["percent_insiders"] = round(100 - float(free_float), 4)

        # Institutional ownership % from ownership summary
        if isinstance(inst_summary, list) and inst_summary:
            inst = inst_summary[0] if isinstance(inst_summary[0], dict) else {}
            own_pct = inst.get("ownershipPercent")
            if own_pct is not None:
                response["percent_institutional"] = float(own_pct)
        elif isinstance(inst_summary, dict) and inst_summary:
            own_pct = inst_summary.get("ownershipPercent")
            if own_pct is not None:
                response["percent_institutional"] = float(own_pct)

        # Short % of Float from Yahoo Finance
        if isinstance(short_data, dict) and short_data.get("short_percent_of_float") is not None:
            response["short_percent_float"] = short_data["short_percent_of_float"]

        # Forward P/E from analyst estimates (nearest future fiscal year)
        price = profile.get("price")
        if analyst_est and isinstance(analyst_est, list) and price and float(price) > 0:
            today_str = datetime.now().date().isoformat()
            # Guard the VALUE, not just a missing key: FMP can return a row with an
            # explicit null date (`{"date": null, ...}`). `e.get("date", "")` yields
            # None there, and `None >= today_str` raises TypeError → the whole
            # /stocks/{ticker} profile 502s despite every other field succeeding.
            future_ests = [
                e for e in analyst_est
                if isinstance(e, dict) and isinstance(e.get("date"), str) and e["date"] >= today_str
            ]
            if future_ests:
                future_ests.sort(key=lambda x: x.get("date") or "")
                fwd_eps = future_ests[0].get("epsAvg") or future_ests[0].get("estimatedEpsAvg")
                if fwd_eps and float(fwd_eps) > 0:
                    response["pe_forward"] = round(float(price) / float(fwd_eps), 2)

        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stock detail failed for {ticker}: {e}", exc_info=True)
        # Surface known upstream failures (FMP rate-limit/unavailable, ticker-not-found)
        # via the structured APIErrorResponse contract iOS decodes; only fall through
        # to a generic 502 for genuinely-unexpected errors.
        if (resp := upstream_error_response(e, ticker=ticker, step="stock_detail")) is not None:
            return resp
        raise HTTPException(status_code=502, detail="Stock data service unavailable")


@router.get("/{ticker}/overview", response_model=StockOverviewResponse)
async def get_stock_overview(
    ticker: str,
    chart_range: str = Query("3M", alias="range", pattern="^(1D|1W|3M|6M|1Y|5Y|ALL)$"),
    interval: Optional[str] = Query(
        None,
        alias="interval",
        pattern="^(1min|5min|15min|30min|1hour|4hour|daily|weekly|monthly|quarterly)$",
    ),
    extended_hours: bool = Query(False, alias="extended_hours"),
):
    """
    Get comprehensive stock overview data for the Overview tab.

    Returns everything the TickerDetailView Overview tab needs in a single call:
    key stats, performance, snapshots, sector info, company profile,
    related tickers, and benchmark summary.
    Set extended_hours=true to include pre-market and after-hours data (intraday only).
    """
    ticker = ticker.upper()
    try:
        service = get_stock_overview_service()
        return await service.get_overview(ticker, chart_range=chart_range, interval=interval, extended_hours=extended_hours)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stock overview failed for {ticker}: {e}", exc_info=True)
        if (resp := upstream_error_response(e, ticker=ticker, step="overview")) is not None:
            return resp
        raise HTTPException(
            status_code=502,
            detail=f"Stock overview service unavailable for {ticker}",
        )


@router.get("/{ticker}/overview/core", response_model=StockOverviewCoreResponse)
async def get_stock_overview_core(
    ticker: str,
    chart_range: str = Query("3M", alias="range", pattern="^(1D|1W|3M|6M|1Y|5Y|ALL)$"),
    interval: Optional[str] = Query(
        None,
        alias="interval",
        pattern="^(1min|5min|15min|30min|1hour|4hour|daily|weekly|monthly|quarterly)$",
    ),
    extended_hours: bool = Query(False, alias="extended_hours"),
):
    """
    Fast first-paint subset of the Overview tab — price + chart + company name.

    Returns in ~0.5s (reuses only the live quote + intraday chart + profile name),
    so the stock detail screen paints the price header + chart instantly. The
    client fires the full `/overview` in parallel; when it lands it supersedes this
    with every Overview section. Public (no auth). Distinct path depth from
    `/overview`, so there is no route-order conflict.
    """
    ticker = ticker.upper()
    try:
        service = get_stock_overview_service()
        return await service.get_overview_core(ticker, chart_range=chart_range, interval=interval, extended_hours=extended_hours)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stock overview core failed for {ticker}: {e}", exc_info=True)
        if (resp := upstream_error_response(e, ticker=ticker, step="overview_core")) is not None:
            return resp
        raise HTTPException(
            status_code=502,
            detail=f"Stock overview core service unavailable for {ticker}",
        )


@router.get("/{ticker}/quote")
async def get_stock_quote(ticker: str):
    """Get real-time stock quote from FMP, enriched with PE/EPS/shares data."""
    ticker = _validate_ticker(ticker)
    fmp = get_fmp_client()
    try:
        results = await asyncio.gather(
            fmp.get_stock_price_quote(ticker),
            fmp.get_income_statement(ticker, period="quarter", limit=4),
            fmp.get_shares_float(ticker),
            fmp.get_company_profile(ticker),
            return_exceptions=True,
        )
        quote = results[0] if not isinstance(results[0], Exception) else {}
        income_q = results[1] if not isinstance(results[1], Exception) else []
        shares_float = results[2] if not isinstance(results[2], Exception) else {}
        profile = results[3] if not isinstance(results[3], Exception) else {}

        if not quote:
            raise HTTPException(status_code=404, detail=f"Quote for {ticker} not found")

        response = normalize_fmp_response(quote)

        # EPS (TTM): sum diluted EPS from last 4 quarterly income statements
        price = quote.get("price")
        if isinstance(income_q, list) and len(income_q) >= 4:
            try:
                ttm_eps = sum(
                    float(q.get("epsDiluted") or q.get("eps") or 0)
                    for q in income_q[:4]
                )
                if ttm_eps > 0:
                    response["eps"] = round(ttm_eps, 2)
                    if price and float(price) > 0:
                        response["pe"] = round(float(price) / ttm_eps, 2)
            except (ValueError, TypeError):
                pass

        if response.get("shares_outstanding") is None and isinstance(shares_float, dict):
            out = shares_float.get("outstandingShares")
            if out is not None:
                response["shares_outstanding"] = float(out)

        if response.get("avg_volume") is None and isinstance(profile, dict):
            avg_vol = profile.get("averageVolume") or profile.get("volAvg")
            if avg_vol is not None:
                response["avg_volume"] = float(avg_vol)

        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Stock quote failed for {ticker}: {e}", exc_info=True)
        if (resp := upstream_error_response(e, ticker=ticker, step="quote")) is not None:
            return resp
        raise HTTPException(status_code=502, detail="Quote service unavailable")


@router.get("/{ticker}/fundamentals")
async def get_stock_fundamentals(ticker: str):
    """Get key financial metrics and ratios from FMP."""
    ticker = _validate_ticker(ticker)
    fmp = get_fmp_client()
    try:
        # return_exceptions=True so ONE failing FMP call degrades that section to
        # [] instead of 502-ing the whole endpoint (metrics + ratios are
        # independent — a caller can still render whichever succeeded).
        metrics, ratios = await asyncio.gather(
            fmp.get_key_metrics(ticker, period="annual", limit=5),
            fmp.get_financial_ratios(ticker, period="annual", limit=5),
            return_exceptions=True,
        )
        if isinstance(metrics, Exception):
            logger.warning(f"Key metrics failed for {ticker}: {type(metrics).__name__}: {metrics}")
            metrics = []
        if isinstance(ratios, Exception):
            logger.warning(f"Financial ratios failed for {ticker}: {type(ratios).__name__}: {ratios}")
            ratios = []
        return {
            "key_metrics": normalize_fmp_list(metrics) if metrics else [],
            "financial_ratios": normalize_fmp_list(ratios) if ratios else [],
        }
    except Exception as e:
        logger.error(f"Fundamentals failed for {ticker}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail="Fundamentals service unavailable")


# ── Date-range helpers for the chart endpoint ──────────────────────

_RANGE_DELTAS = {
    "1D": timedelta(days=5),   # Fetch ~5 calendar days to guarantee 2 trading days
    "1W": timedelta(weeks=1),
    "3M": timedelta(days=90),
    "6M": timedelta(days=180),
    "1Y": timedelta(days=365),
    "5Y": timedelta(days=365 * 5),
}


def _chart_date_range(range_code: str):
    """Return (from_date, to_date) ISO strings for the given range code."""
    today = datetime.utcnow().date()
    to_date = today.isoformat()

    if range_code == "ALL":
        # FMP caps results when from_date is omitted; use explicit old date
        return "1970-01-01", to_date

    delta = _RANGE_DELTAS.get(range_code)
    if delta is None:
        return None, None

    from_date = (today - delta).isoformat()
    return from_date, to_date


# ── Chart endpoint ─────────────────────────────────────────────────

@router.get("/{ticker}/chart")
async def get_stock_chart(
    ticker: str,
    range: str = Query("3M", regex="^(1D|1W|3M|6M|1Y|5Y|ALL)$"),
    interval: Optional[str] = Query(
        None,
        alias="interval",
        pattern="^(1min|5min|15min|30min|1hour|4hour|daily|weekly|monthly|quarterly)$",
    ),
    extended_hours: bool = Query(False, alias="extended_hours"),
):
    """
    Get historical price data for charting.

    Supported ranges: 1D, 1W, 3M, 6M, 1Y, 5Y, ALL.
    Optional interval: 1min, 5min, 15min, 30min, 1hour, 4hour, daily, weekly, monthly, quarterly.
    Defaults: 1D→5min, 1W→1hour, others→daily.
    Set extended_hours=true to include pre-market and after-hours data (intraday only).
    """
    from app.services.chart_helper import fetch_chart_data

    ticker = _validate_ticker(ticker)
    fmp = get_fmp_client()
    try:
        prices = await fetch_chart_data(fmp, ticker, range, interval, extended_hours=extended_hours)
        return {"symbol": ticker.upper(), "prices": prices}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chart data failed for {ticker}: {e}", exc_info=True)
        if (resp := upstream_error_response(e, ticker=ticker, step="chart")) is not None:
            return resp
        raise HTTPException(status_code=502, detail="Chart data service unavailable")


# ── Full financials endpoint ───────────────────────────────────────

@router.get("/{ticker}/financials-full")
async def get_stock_financials_full(ticker: str):
    """
    Get comprehensive financial data for a ticker.

    Returns income statements, balance sheets, cash flow statements (each with
    annual and quarterly periods), plus key metrics, financial ratios, and
    analyst estimates.  All FMP calls are made in parallel for performance.
    """
    ticker = _validate_ticker(ticker)
    fmp = get_fmp_client()

    try:
        # return_exceptions=True so one failing statement (e.g. an FMP 429 on the
        # analyst-estimates call) degrades that section to [] instead of 502-ing
        # the entire financials screen. Each section is independent.
        _raw = await asyncio.gather(
            fmp.get_income_statement(ticker, period="annual", limit=5),
            fmp.get_income_statement(ticker, period="quarter", limit=8),
            fmp.get_balance_sheet(ticker, period="annual", limit=5),
            fmp.get_balance_sheet(ticker, period="quarter", limit=8),
            fmp.get_cash_flow_statement(ticker, period="annual", limit=5),
            fmp.get_cash_flow_statement(ticker, period="quarter", limit=8),
            fmp.get_key_metrics(ticker, period="annual", limit=5),
            fmp.get_financial_ratios(ticker, period="annual", limit=5),
            fmp.get_analyst_estimates(ticker, period="annual", limit=3),
            return_exceptions=True,
        )
        _labels = [
            "income_annual", "income_quarterly", "balance_annual", "balance_quarterly",
            "cashflow_annual", "cashflow_quarterly", "key_metrics", "fin_ratios", "analyst_est",
        ]
        _cleaned = []
        for _lbl, _r in zip(_labels, _raw):
            if isinstance(_r, Exception):
                logger.warning(f"financials-full '{_lbl}' failed for {ticker}: {type(_r).__name__}: {_r}")
                _cleaned.append([])
            else:
                _cleaned.append(_r)
        (
            income_annual, income_quarterly, balance_annual, balance_quarterly,
            cashflow_annual, cashflow_quarterly, key_metrics, fin_ratios, analyst_est,
        ) = _cleaned

        return {
            "symbol": ticker.upper(),
            "income_statement": {
                "annual": normalize_fmp_list(income_annual) if income_annual else [],
                "quarterly": normalize_fmp_list(income_quarterly) if income_quarterly else [],
            },
            "balance_sheet": {
                "annual": normalize_fmp_list(balance_annual) if balance_annual else [],
                "quarterly": normalize_fmp_list(balance_quarterly) if balance_quarterly else [],
            },
            "cash_flow": {
                "annual": normalize_fmp_list(cashflow_annual) if cashflow_annual else [],
                "quarterly": normalize_fmp_list(cashflow_quarterly) if cashflow_quarterly else [],
            },
            "key_metrics": normalize_fmp_list(key_metrics) if key_metrics else [],
            "financial_ratios": normalize_fmp_list(fin_ratios) if fin_ratios else [],
            "analyst_estimates": normalize_fmp_list(analyst_est) if analyst_est else [],
        }

    except Exception as e:
        logger.error(f"Financials-full failed for {ticker}: {e}")
        raise HTTPException(
            status_code=502, detail="Financial data service unavailable"
        )


def _invalid_news_symbol(raw: str) -> JSONResponse:
    """Structured INVALID_INPUT for a malformed news symbol (invariant #3)."""
    return make_error_response(
        ErrorCode.INVALID_INPUT,
        message=f"Invalid ticker for news: {raw[:32]!r}",
        user_message="That symbol isn't valid.",
        details={"ticker": raw[:32]},
    )


@router.get("/{ticker}/news", response_model=TickerNewsFeedResponse)
async def get_stock_news(
    ticker: str,
    limit: int = Query(50, ge=1, le=50),
):
    """
    Get news for a specific ticker (raw + any previously enriched).

    Fetches up to 50 articles from FMP, caches all in Supabase.
    AI enrichment is NOT automatic — use POST /{ticker}/news/enrich
    to enrich specific articles on demand.
    """
    from app.services.news_cache_service import get_news_cache_service

    symbol = ticker.strip().upper()
    if not _TICKER_RE.match(symbol):
        return _invalid_news_symbol(ticker)

    try:
        service = get_news_cache_service()
        feed = await service.get_ticker_news(symbol, limit)
    except Exception as e:
        logger.error(
            f"Stock news failed for {symbol}: {type(e).__name__}: {e}", exc_info=True
        )
        # Surface a known upstream failure (e.g. FMP_RATE_LIMITED) via the structured
        # {error_code,message,user_message} contract (invariant #3); keep the generic
        # 500 only for a truly-unexpected error.
        if (resp := upstream_error_response(e, ticker=symbol, step="news")) is not None:
            return resp
        raise HTTPException(status_code=500, detail="News service unavailable")

    return news_feed_from_payload(feed, ticker=symbol)


@router.post("/{ticker}/news/enrich", response_model=EnrichNewsResponse)
async def enrich_stock_news(
    ticker: str,
    body: Dict[str, Any],
):
    """
    AI-enrich specific news articles on demand.

    Body: { "article_ids": ["uuid1", "uuid2", ...] }

    Only processes articles that haven't been enriched yet.
    Returns the enriched article data.
    """
    from app.services.news_cache_service import get_news_cache_service

    symbol = ticker.strip().upper()
    if not _TICKER_RE.match(symbol):
        return _invalid_news_symbol(ticker)

    raw_ids = body.get("article_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message="article_ids is required (non-empty list)",
            user_message="No articles were requested.",
            details={"ticker": symbol},
        )

    ids = sanitize_article_ids(raw_ids)
    if not ids:
        # Every id was a client-side placeholder — nothing is enrichable yet.
        return EnrichNewsResponse(ticker=symbol, articles=[])
    if len(ids) > MAX_ENRICH_ARTICLE_IDS:
        return make_error_response(
            ErrorCode.INVALID_INPUT,
            message=f"Too many article_ids: {len(ids)} (max {MAX_ENRICH_ARTICLE_IDS})",
            user_message="Too many articles requested at once.",
            details={"ticker": symbol, "count": len(ids)},
        )

    try:
        service = get_news_cache_service()
        enriched = await service.enrich_articles(symbol, ids)
    except Exception as e:
        logger.error(
            f"News enrichment failed for {symbol} ({len(ids)} ids): "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )
        return error_response_from_exception(e, ticker=symbol, step="stock_news_enrich")

    return EnrichNewsResponse(ticker=symbol, articles=news_articles_from_rows(enriched))


# ── Analyst analysis endpoint ─────────────────────────────────────

@router.get("/{ticker}/analyst-analysis", response_model=AnalystAnalysisResponse)
async def get_analyst_analysis(ticker: str):
    """
    Get comprehensive analyst analysis data for a ticker.

    Returns analyst consensus rating, price targets, rating distribution,
    momentum trends, and individual analyst actions (upgrades/downgrades).
    """
    ticker = ticker.upper()
    try:
        service = get_analyst_service()
        return await service.get_analysis(ticker)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Analyst analysis failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Analyst analysis service unavailable for {ticker}",
        )


# ── Earnings endpoint ────────────────────────────────────────────

@router.get("/{ticker}/earnings", response_model=EarningsResponse)
async def get_earnings(ticker: str):
    """
    Get quarterly earnings data (EPS & Revenue actuals vs estimates),
    price overlay, and next earnings date.
    """
    ticker = ticker.upper()
    try:
        service = get_earnings_service()
        return await service.get_earnings(ticker)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Earnings failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Earnings service unavailable for {ticker}",
        )


# ── Growth endpoint ──────────────────────────────────────────────

@router.get("/{ticker}/growth", response_model=GrowthResponse)
async def get_growth(ticker: str):
    """Get growth data (EPS & Revenue YoY growth with sector comparison)."""
    ticker = ticker.upper()
    try:
        service = get_growth_service()
        return await service.get_growth(ticker)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Growth failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Growth service unavailable for {ticker}",
        )


# ── Profit Power endpoint ────────────────────────────────────────

@router.get("/{ticker}/profit-power", response_model=ProfitPowerResponse)
async def get_profit_power(ticker: str):
    """Get profit power data (margin metrics with sector average net margin)."""
    ticker = ticker.upper()
    try:
        service = get_profit_power_service()
        return await service.get_profit_power(ticker)
    except ValueError as e:
        # Invalid ticker symbol — a 400, matching /revenue-breakdown and
        # /signal-of-confidence rather than masquerading as a 502 outage.
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Profit power failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Profit power service unavailable for {ticker}",
        )


# ── Health Check endpoint ────────────────────────────────────────

@router.get("/{ticker}/health-check", response_model=HealthCheckResponse)
async def get_health_check(ticker: str):
    """Get health check data (financial ratio analysis vs sector benchmarks)."""
    ticker = ticker.upper()
    try:
        service = get_health_check_service()
        return await service.get_health_check(ticker)
    except ValueError as e:
        # Invalid ticker symbol — a 400, matching /revenue-breakdown and
        # /signal-of-confidence rather than masquerading as a 502 outage.
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Health check failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Health check service unavailable for {ticker}",
        )


# ── Revenue breakdown endpoint ───────────────────────────────────

@router.get("/{ticker}/revenue-breakdown", response_model=RevenueBreakdownResponse)
async def get_revenue_breakdown(ticker: str):
    """
    Get revenue breakdown showing how the company makes money.

    Returns product-segment revenue sources plus cost of sales,
    operating expenses, and tax — the iOS "How [TICKER] Makes Money" section.
    """
    try:
        service = get_revenue_breakdown_service()
        return await service.get_revenue_breakdown(ticker)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Revenue breakdown failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Revenue breakdown service unavailable for {ticker}",
        )


# ── Signal of Confidence endpoint ────────────────────────────────

@router.get("/{ticker}/signal-of-confidence", response_model=SignalOfConfidenceResponse)
async def get_signal_of_confidence(ticker: str):
    """
    Get signal of confidence data (dividends, buybacks, shares outstanding).

    Returns per-quarter shareholder yield data plus a trailing-12-month summary
    and optional dividend info — the iOS "Signal of Confidence" section.
    """
    try:
        service = get_signal_of_confidence_service()
        return await service.get_signal_of_confidence(ticker)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Signal of confidence failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Signal of confidence service unavailable for {ticker}",
        )


# ── Holders endpoint ─────────────────────────────────────────────

@router.get("/{ticker}/holders", response_model=HoldersResponse)
async def get_holders(ticker: str):
    """
    Get shareholder breakdown, smart money flow, and recent activities.

    Returns ownership distribution (insiders/institutions/public),
    top 10 owners, recent institutional and insider trading activity —
    the iOS "Holders" tab.
    """
    try:
        service = get_holders_service()
        return await service.get_holders(ticker)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Holders failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Holders service unavailable for {ticker}",
        )


# ── Sentiment analysis endpoint ──────────────────────────────────

@router.get("/{ticker}/sentiment", response_model=SentimentAnalysisResponse)
async def get_sentiment_analysis(ticker: str):
    """
    Get sentiment analysis / market mood data for a ticker.

    Aggregates AI-analyzed news sentiment and social media sentiment
    to produce a 0-100 mood score with 24H and 7D breakdowns.
    """
    ticker = ticker.upper()
    try:
        service = get_sentiment_service()
        return await service.get_sentiment(ticker)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Sentiment analysis failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Sentiment analysis service unavailable for {ticker}",
        )


# ── Technical analysis endpoints ──────────────────────────────

@router.get("/{ticker}/technical-analysis", response_model=TechnicalAnalysisResponse)
async def get_technical_analysis(ticker: str):
    """
    Get technical analysis gauge data for a ticker.

    Computes 18 technical indicators (10 moving averages + 8 oscillators)
    on both daily and weekly timeframes, producing a 0-1 gauge value
    and signal (Strong Sell to Strong Buy).
    """
    ticker = ticker.upper()
    try:
        service = get_technical_analysis_service()
        return await service.get_analysis(ticker)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Technical analysis failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Technical analysis service unavailable for {ticker}",
        )


@router.get("/{ticker}/chart-events")
async def get_chart_events(ticker: str):
    """
    Get earnings dates for chart markers.

    Returns list of dates (yyyy-MM-dd) so the frontend can render
    "E" (earnings) markers on the price chart.
    """
    ticker = ticker.upper()
    fmp = get_fmp_client()
    try:
        earnings_dates = await fmp.get_historical_earnings_dates(ticker)

        logger.info(
            f"Chart events for {ticker}: {len(earnings_dates)} earnings"
        )
        return {
            "earnings_dates": earnings_dates,
            "dividend_dates": [],  # Kept for backward compatibility
        }
    except Exception as e:
        logger.error(f"Chart events failed for {ticker}: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Chart events service unavailable for {ticker}",
        )


@router.get(
    "/{ticker}/technical-analysis/detail",
    response_model=TechnicalAnalysisDetailResponse,
)
async def get_technical_analysis_detail(ticker: str):
    """
    Get detailed technical analysis breakdown for a ticker.

    Returns individual indicator values and signals, pivot points,
    volume analysis, Fibonacci retracement, and support/resistance levels.
    """
    ticker = ticker.upper()
    try:
        service = get_technical_analysis_service()
        return await service.get_analysis_detail(ticker)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Technical analysis detail failed for {ticker}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=502,
            detail=f"Technical analysis detail service unavailable for {ticker}",
        )
