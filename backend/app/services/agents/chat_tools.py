"""Chat function-calling tools for the agentic streaming loop (Phase 2).

Mirrors ``agents/fmp_tools.py``: ``build_chat_tool_declarations()`` returns the ``types.Tool`` list
the model may call mid-stream; ``build_chat_tool_handlers(svc)`` maps each tool name to an async
handler that delegates to the existing ``ChatService`` fetch methods (so the data logic isn't
duplicated). A tool result whose ``widget_type`` is renderable (stock_chart / market_overview)
becomes an inline widget; analyst / sentiment results only inform the model's answer.

Handlers take an svc argument (a ChatService) rather than importing it, to avoid a circular import.
"""

import inspect
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from google.genai import types

from app.config import settings
from app.services._analyst_common import analyst_estimates_available, analyst_section_available
from app.services.chat_security import sanitize_symbol
from app.services.entitlements import congress_holders_unlocked

logger = logging.getLogger(__name__)


def _ticker_tool(name: str, description: str) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=name,
        description=description,
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "ticker": types.Schema(
                    type=types.Type.STRING,
                    description="The stock ticker symbol (e.g. AAPL, TSLA, MSFT).",
                ),
            },
            required=["ticker"],
        ),
    )


# ── Which tools each asset class may call ────────────────────────────────────
#
# SINGLE SOURCE OF TRUTH, shared by both chat paths. The streaming endpoint and
# `ChatService.generate_response` build their `types.Tool` objects separately (a
# long-standing duplication), so without one shared name set they drift silently.
#
# This used to be append-only: every chat — crypto, index, commodity — was offered the three
# EQUITY tools, and `asset_type` could only ever ADD the index tool on top. So on a Bitcoin
# chat the model could call `get_analyst_analysis("BTCUSD")`, and no analyst covers a coin:
# it comes back empty and the answer has to talk around a hole it created itself. Removing a
# tool is the point of this table; adding one is the easy half.
#
# `get_stock_chart_data` is kept for ETF / CRYPTO on purpose — an ETF is quoted through the
# profile path and a coin through CoinGecko, and the resulting card is honest for them
# (`pe_ratio` and `market_cap` are Optional on `StockChartWidget`, and iOS renders P/E only
# when present). COMMODITY does NOT get it: every FMP commodity code (`GCUSD`, `CLUSD`, …)
# sits in `BLOCKED_COMMODITY_SYMBOLS`, so `PriceService.get_quote` answers `{}` before any
# I/O and the handler returns an error dict — the model was declared a tool that could
# never succeed. It called it (the screen symbol is exempt from canonicalisation, so the
# code reached the fetcher unchanged), the failed step rendered in the thinking card, and
# `tool_calls_failed == tool_calls_seen` settled a fully-answered turn as `no_tools`: a
# refund on every commodity price question. The client context already carries the
# commodity's price, so the model loses nothing by not having the tool.
# Granted to EVERY asset class, including `NORMAL` (the global chat with no screen behind
# it). It is the answer to "what's hot today", "why is <sector> lagging" and "how is the
# market doing" — and until it existed there was no sector path outside an index screen, so
# Cay AI told a user its tools only cover individual companies. That was true.
#
# It is NOT `get_market_overview`, which stays index-only: that one runs through
# `index_service.get_index_detail` and recomputes with FMP *and Gemini* on a cold cache —
# the exact stall that forced a 4s timeout on `ChatContextResolver`. This one is a screener
# sweep plus two cache reads.
_MARKET_TOOL = "get_market_snapshot"

# The two per-ticker tools that closed the "why did it move" hole. Both are free at the
# point of call (`explain_price_move`'s metered grounded-search tier was retired 2026-10-02;
# see `chat_market_tools`).
_NEWS_TOOLS = frozenset({"get_ticker_news", "explain_price_move"})

# Who holds a stock, from filings (`chat_ownership_tool`, 2026-10-05): each insider's shares
# after their latest Form 4 transaction, and 13F institutional ownership. TestFlight 1.0 (11):
# in an Updates chat on CRWV, "how many shares does he own now?" (a director who had just
# sold) got "Caydex does not have information on his current total ownership" — no tool or
# grounding carried the post-trade balance every Form 4 line reports. Equity classes only: a
# fund, a coin, an index or a futures contract has no Form 4 filers. The name reads as a
# progress line on its own ("Check ownership filings"): an app build without a label for it
# renders an unknown tool by de-snake-casing its name.
OWNERSHIP_TOOL = "check_ownership_filings"

# A company's reported figures (`chat_financials_tool`, 2026-10-08): Key Stats, statements by
# fiscal year and quarter, margins, the balance sheet's debt and cash, earnings results and
# dates, analysts' revenue/EPS ESTIMATES (licensed; ratings and targets are not), valuation
# multiples and the fair-value model, segments, dividends and splits — read through the SAME
# cache-aside services the Financials and Overview tabs use, never Gemini. The audit behind it:
# that data was cached and licensed, yet chat had no way to reach it, so a NORMAL chat answered
# revenue and EPS from memory. Equity classes only. Shipped app builds with no label for it
# render the de-snake-cased name, "Check company financials" — a true progress line.
FINANCIALS_TOOL = "check_company_financials"
# Its optional `section` — a CLOSED vocabulary, described in the schema (no `enum`, which a
# strict declaration validator can 400 on — the `recency` precedent) and normalised here
# (`normalize_section`). "summary" is the default because the send door allows ONE tool round:
# one call must answer most questions.
FINANCIAL_SECTIONS = (
    "summary", "growth", "margins", "health", "earnings",
    "estimates", "valuation", "segments", "dividends",
)
# The tools that declare a `section` parameter (`tests/test_chat_tool_boundary.py` allows the
# parameter on these alone).
SECTION_TOOLS = frozenset({FINANCIALS_TOOL})
_SECTION_MAX_CHARS = 32
# Fixed text — NEVER an echo of the model's value, which is model output.
_SECTION_NOTE = (
    "That section is not one this tool has, so the summary is shown. Valid sections: "
    + ", ".join(FINANCIAL_SECTIONS) + "."
)
# What a ticker IS (`chat_profile_tool`, 2026-10-08): a company's CEO and key executives,
# headcount, headquarters, listing date and peers; a fund's fee, assets, holdings and sector
# weights; a coin's supply, fully diluted value and rank, plus the market-wide Crypto Fear &
# Greed reading — each read through the screens' own caches (company facts, fund facts, coin
# facts), never Gemini. The audit behind it: "who is the CEO of X", "SPY's expense ratio" and
# "Bitcoin's max supply" were answered from memory, because the only profile chat ever saw was
# the cached row of the stock on screen. Granted to STOCK, NORMAL, ETF and CRYPTO (an index or
# a futures contract has no such profile). It resolves the asset class ITSELF, against the
# screen — so its handler never canonicalises a bare coin first (`resolved_as` says what it
# found). `ChatService._knowledge_rule` adds "current executives" to the company-reported
# tier wherever a class grants it. Shipped app builds with no label for it render the
# de-snake-cased name, "Check asset profile" — a true progress line.
PROFILE_TOOL = "check_asset_profile"
# Its optional `kind` — a CLOSED vocabulary like `section` (described, no schema `enum`;
# normalised by `normalize_profile_kind`, never echoed). Without it the tool resolves a ticker
# against the screen, so a symbol a coin shares with a listed company ("LTC": Litecoin and LTC
# Properties) is the coin everywhere but that company's own screen — and "Who is LTC
# Properties' CEO?" in a general chat could never reach the company. With it, the user's own
# words decide (`ChatService._fetch_asset_profile_data` reads the symbol as that kind).
PROFILE_KINDS = ("company", "fund", "coin")
# The tools that declare a `kind` parameter (`tests/test_chat_tool_boundary.py` allows the
# parameter on these alone).
KIND_TOOLS = frozenset({PROFILE_TOOL})
_KIND_MAX_CHARS = 32
# Accepted spellings → the member. Exact, after strip + lower-case; anything else is ignored.
_KIND_ALIASES: Dict[str, str] = {
    "company": "company", "stock": "company", "equity": "company",
    "fund": "fund", "etf": "fund",
    "coin": "coin", "crypto": "coin", "cryptocurrency": "coin",
}
# Fixed text — NEVER an echo of the model's value, which is model output.
_KIND_NOTE = (
    "That kind is not one this tool has, so the ticker was resolved as usual. Valid kinds: "
    + ", ".join(PROFILE_KINDS) + "."
)
# Caydex's data tools — behind the `CHAT_DATA_TOOLS_ENABLED` kill switch (applied in
# `tools_for_asset_type`, so the declarations, the handler maps and the prompt all follow it).
_DATA_TOOLS = frozenset({FINANCIALS_TOOL, PROFILE_TOOL})


def normalize_profile_kind(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    """``(kind, note)`` for the model's `kind` argument: a `PROFILE_KINDS` member or None.
    Only a string of at most 32 characters is read (stripped, lower-cased, an exact
    `_KIND_ALIASES` key). Omitted / blank → (None, None): the screen decides. Anything else →
    (None, a fixed note listing the valid kinds). The raw value is never echoed. Never
    raises."""
    if raw is None:
        return None, None
    if not isinstance(raw, str) or len(raw) > _KIND_MAX_CHARS:
        return None, _KIND_NOTE
    try:
        key = str(raw).strip().lower()
    except Exception:  # noqa: BLE001 — an odd str subclass is refused, never a crash
        return None, _KIND_NOTE
    if not key:
        return None, None
    member = _KIND_ALIASES.get(key)
    return (member, None) if member is not None else (None, _KIND_NOTE)


def normalize_section(raw: Any) -> Tuple[str, Optional[str]]:
    """``(section, note)`` for the model's `section` argument — always a `FINANCIAL_SECTIONS`
    member. Only a string of at most 32 characters is read: stripped, lower-cased, spaces and
    hyphens to underscores, then an EXACT member. Omitted / blank → ("summary", None); anything
    else → ("summary", a fixed note listing the valid sections). The raw value is never echoed
    back. Never raises."""
    if raw is None:
        return "summary", None
    if not isinstance(raw, str) or len(raw) > _SECTION_MAX_CHARS:
        return "summary", _SECTION_NOTE
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if not key:
        return "summary", None
    if key in FINANCIAL_SECTIONS:
        return key, None
    return "summary", _SECTION_NOTE

# Ask Cay AI's live web search (`chat_web_search_service`). The ONE name both files share:
# defined HERE and imported by the service, never the reverse — this module must not pull the
# budget / database code in at import (`tests/test_chat_tool_boundary.py`). It is in NO asset
# class's table below: `tools_for_asset_type(..., web_search=True)` adds it only on a turn the
# service's ONE decision granted a tier (`decide_web_search`: report chat's explicit ask, an
# explicit ask in any chat, or the automatic fallback), so the declaration, the handler map and
# the prompt's capability block all follow that one decision. Its description and capability line
# follow the tier (`web_search_mode`: "explicit" | "news" | "auto", `WEB_SEARCH_MODES`).
WEB_SEARCH_TOOL = "web_search"
#: The declaration / capability variants of the web tool (`web_search_mode`). "explicit" is the
#: registry's own text (`TOOL_DESCRIPTIONS[WEB_SEARCH_TOOL]`); the other two live OUT of the
#: registry so its tool count stays the count of TOOLS (`test_ask_cay_ai_design_page_parity`).
WEB_SEARCH_MODES = ("explicit", "news", "auto")

_STOCK_TOOLSET = frozenset({
    "get_stock_chart_data", "get_analyst_analysis", "get_sentiment_analysis",
}) | _NEWS_TOOLS | {_MARKET_TOOL, OWNERSHIP_TOOL, FINANCIALS_TOOL, PROFILE_TOOL}

_TOOLS_BY_ASSET_TYPE: Dict[str, frozenset] = {
    # Every chat about a company — its ticker screen, its report and its Updates feed all
    # resolve to STOCK (`ChatService._detect_asset_type`) — gets the ownership tool.
    # It also gets the financials tool (a company's own reported figures) and the profile tool
    # (who runs it, where it is based, when it listed, its peers).
    "STOCK": _STOCK_TOOLSET,
    # No screen context: the user may ask about any stock, so keep the full equity set.
    "NORMAL": _STOCK_TOOLSET,
    # A fund has no analyst coverage, but it does have news sentiment and a real quote — and a
    # profile (its fee, assets, holdings and sector weights).
    "ETF": frozenset({"get_stock_chart_data", "get_sentiment_analysis"})
           | _NEWS_TOOLS | {_MARKET_TOOL, PROFILE_TOOL},
    # Sentiment IS meaningful for a coin — `sentiment_service` has a crypto news branch — but
    # only if the caller passes `is_crypto`; see `ChatService._fetch_sentiment_data`.
    # News is routed on the same flag, so a coin gets `news/crypto` rather than an equity
    # query for "BTCUSD" that returns nothing. Its profile is its supply (circulating, total,
    # max), fully diluted value and rank.
    "CRYPTO": frozenset({"get_stock_chart_data", "get_sentiment_analysis"})
              | _NEWS_TOOLS | {_MARKET_TOOL, PROFILE_TOOL},
    # An index has no analyst ratings and no per-symbol social sentiment; it has the
    # market-overview aggregate, which is the tool built for exactly this case — plus the
    # breadth snapshot, which is what "why is the market down" actually needs.
    "INDEX": frozenset({"get_market_overview", _MARKET_TOOL}),
    # A futures contract has neither analyst coverage nor ticker sentiment, and its quote
    # is outside the FMP licence (see the `get_stock_chart_data` note above), so no card
    # tool either. It does have news, and the macro backdrop is most of any commodity answer.
    "COMMODITY": frozenset({"get_ticker_news", _MARKET_TOOL}),
}


def tools_for_asset_type(asset_type: Optional[str], *, web_search: bool = False) -> frozenset:
    """Tool NAMES the given asset class may call.

    `web_search=True` adds report chat's `web_search` tool on top — passed ONLY by a turn whose
    `chat_web_search_service.WebSearchTurn` exists. Defaults to False, so every existing caller
    (the chip scope, the tool-less builds) is unchanged.

    An unknown / missing asset type falls back to the full equity set — the conservative
    direction, since that is exactly what every caller did before this table existed.

    ⚠️ The LICENCE filter is applied on top of the asset-class table, and it has to be here
    rather than in the table because it is a global fact, not a per-asset one. `grades` and
    `price-target-consensus` are 402-blocked (see `analyst_section_available`), so
    `get_analyst_analysis` now returns `HOLD, 0 analysts, $0/$0/$0` for EVERY equity. Offering
    the model a tool whose only possible answer is a fabricated consensus is worse than offering
    no tool: it asserted "Wall Street's consensus on Apple is HOLD with a $0 average price
    target" on a credit-charged turn.
    
    Removing the tool is exactly what the table above already does for ETF / CRYPTO / INDEX /
    COMMODITY, and for the same reason — there is no analyst coverage to fetch. This is that
    case, arrived at from the licence rather than from the asset class.

    The ONE registry — the declarations, the handlers and the prompt's `capability_block` —
    filters through this function, so closing it here closes every door.

    `CHAT_DATA_TOOLS_ENABLED` (Settings, default True) is the kill switch for Caydex's data
    tools (`_DATA_TOOLS`): off, they leave every class at once, read per turn.
    """
    allowed = _TOOLS_BY_ASSET_TYPE.get((asset_type or "").strip().upper(), _STOCK_TOOLSET)
    if not analyst_section_available():
        allowed = allowed - {"get_analyst_analysis"}
    if not settings.CHAT_DATA_TOOLS_ENABLED:
        allowed = allowed - _DATA_TOOLS
    if web_search:
        allowed = allowed | {WEB_SEARCH_TOOL}
    return allowed


# ── The ONE tool registry ─────────────────────────────────────────────────────
#
# Both the FunctionDeclarations the model receives AND the "what you can answer" paragraph
# in the system prompt render from this table, so the prompt can never name a tool the
# asset class was not given. That happened: `_build_system_instruction` unconditionally
# told an INDEX chat it had `explain_price_move` and ORDERED the model to call it — the same
# failure class the macro-lens comment above `chat_specialists` documents — and the
# non-streaming path kept a second, drifting copy of these descriptions in chat_service.py
# (one of which told the model to fetch a chart "or whether they should buy/sell a stock").
#
# `description` is what the model reads when choosing a tool. `capability` is the one
# sentence the system prompt uses to advertise it (no imperative, no duplicate guidance).
TOOL_DESCRIPTIONS: Dict[str, str] = {
    "get_stock_chart_data": (
        "Fetch the current quote + 30-day price history for a ticker. Call when the user asks "
        "about a specific stock's price, performance, chart, or how it's trading — including a "
        "DIFFERENT ticker than the current screen (e.g. a comparison). The result is rendered "
        "to the user as an interactive price-chart card beneath your answer — in a chat "
        "grounded on that same asset the card appears once, under the first answer, and stays "
        "on screen for the rest of the conversation: never say charts are unavailable, and do "
        "not call it twice for the same ticker in one turn."
    ),
    "get_analyst_analysis": (
        "Fetch Wall Street analyst ratings, consensus, price targets, and recent "
        "upgrade/downgrade actions for a ticker. Call when the user asks about analyst opinions, "
        "consensus, or price targets."
    ),
    "get_sentiment_analysis": (
        "Fetch market sentiment for a ticker: social mentions, news sentiment, and a 0-100 mood "
        "gauge. Call when the user asks about sentiment, mood, buzz, or why a stock feels "
        "bullish/bearish."
    ),
    "get_ticker_news": (
        "Fetch the most recent news headlines for a ticker, with key points and publisher. "
        "For a listed company it also returns the company's own latest press releases "
        "(`press_releases`: results, guidance, buybacks, leadership changes and other "
        "announcements, each dated) — the company's own statements, not independent "
        "reporting. Call whenever the user asks what is happening with a company, what the "
        "news is, what the company announced or what guidance it gave, or what is behind a "
        "story — and before saying you do not know why something happened."
    ),
    "explain_price_move": (
        "Explain why a ticker moved TODAY. Returns the identified cause (earnings, analyst "
        "action, company news, a sector-wide move, or an overnight gap), how unusual the move "
        "is for THIS ticker specifically, how its industry and the wider market did, and "
        "recent headlines. ALWAYS call this for any 'why is X up/down' question rather than "
        "answering from the price alone."
    ),
    OWNERSHIP_TOOL: (
        "Look up who owns a company's stock, from its SEC filings: for each insider (officer, "
        "director or 10% owner) the shares they reported holding right AFTER their most "
        "recent transaction — held directly and through trusts or entities, each with its "
        "as-of date — plus their latest purchase or sale in plain words; insiders' open-market "
        "buying and selling over the last 3, 6 and 12 months; the share held by institutions, "
        "the largest institutional holders and how they changed in the latest quarterly 13F "
        "filings; the public float and shares outstanding; short interest with its settlement "
        "date and days to cover; and congressional trading disclosures where the user's plan "
        "includes them. Call it for any question about how many shares someone owns or holds, "
        "what an insider has left after selling or buying, whether insiders or institutions "
        "are buying or selling, how much insiders or institutions own, who the biggest holders "
        "are, the float, short interest, or members of Congress trading the stock. The figures "
        "are as of each filing, not live."
    ),
    FINANCIALS_TOOL: (
        "Look up a company's reported financial figures from Caydex's financial data. Optional "
        "`section` picks the depth: summary (the default: key stats, the latest fiscal year and "
        "quarter, margins, the last earnings result and the next report date, valuation "
        "multiples, and the balance sheet's debt and cash), growth (revenue, EPS, net income, "
        "operating income and free cash flow by fiscal year and quarter, with year-over-year "
        "change), margins, health (debt, cash, liquidity and leverage), earnings (results "
        "against analysts' estimates and the next report date), estimates (analysts' forward "
        "revenue and EPS estimates, when available), valuation (multiples and a fair-value "
        "model estimate), segments (revenue by business segment) or dividends (yield, "
        "per-share history, ex-dividend date and stock splits). Call it before answering any "
        "question about a company's revenue, earnings, EPS, margins, growth, cash flow, debt, "
        "cash, valuation multiples, fair value, dividends, splits, earnings dates or results, "
        "or analysts' revenue or EPS estimates, and never answer those figures from memory. "
        "Every figure comes with its period (fiscal year, quarter or trailing twelve months), "
        "its basis and its currency. It carries no analyst ratings and no price targets. Listed "
        "companies only: not funds, coins, indexes or commodities."
    ),
    PROFILE_TOOL: (
        "Look up what a ticker is, from Caydex's licensed profile data. For a company: its "
        "name, CEO and key executives with their titles, employee count, headquarters, "
        "listing (IPO) date, sector, industry, exchange, website and comparable companies "
        "(peers). For a fund: its issuer, the index it tracks, expense ratio, assets, number "
        "of holdings, top holdings and sector weights. For a coin: its circulating, total and "
        "maximum supply, fully diluted value and market-cap rank, plus the market-wide Crypto "
        "Fear & Greed reading. Call it before answering who runs a company or who its "
        "executives are, how many people it employs, where it is based, when it listed, who "
        "its peers are, a fund's fee, holdings or sector mix, or a coin's supply — and never "
        "answer those from memory. It lists a company's current officers, not its founders "
        "or past leaders. The result says what the ticker resolved to (`resolved_as`): on a "
        "company's or fund's own screen its symbol means that asset, even when a coin shares "
        "it, and elsewhere a symbol a coin shares means the coin — unless the optional `kind` "
        "(company, fund or coin) says which one the user means; set it when the user's words "
        "make that clear, such as LTC Properties versus Litecoin. No prices: the price tool "
        "has those."
    ),
    _MARKET_TOOL: (
        "Fetch how the market is doing TODAY: every sector's daily move, the "
        "leading and lagging industries, the biggest gaining and losing stocks, "
        "and today's market news summary with its cited catalyst — plus official "
        "macro readings, each dated: the fed funds rate, the 10-year Treasury yield "
        "and the 10-year minus 2-year spread, unemployment, CPI and core PCE "
        "inflation year-on-year, the euro, yen and pound exchange rates and the "
        "broad dollar index (not the DXY). Takes no arguments. Call for any "
        "question about sectors, market breadth, what is hot or trending today, "
        "sector rotation, or why the market moved — including when the user names "
        "one sector, such as Basic Materials or Technology — and for any interest "
        "rate, Treasury yield, inflation, unemployment or exchange-rate question. "
        "The VIX and the DXY are not in Caydex data."
    ),
    "get_market_overview": (
        "Fetch overall market valuation (P/E, forward P/E, earnings yield), sector "
        "performance, and macro outlook labels (written outlooks, not measured readings). "
        "For INDEX / broad-market questions, NOT individual stocks."
    ),
    WEB_SEARCH_TOOL: (
        "Search the public web — ONLY for what the user explicitly asked, in this message, to "
        "search for, look up, verify, fact-check or get the latest on. Call it at most once. "
        "`query`: a short search — the company name or ticker, the topic and, if needed, a "
        "year or quarter; never a figure, price or percentage, and nothing about the user. "
        "Optional `recency`: day, week, month or year. Returns third-party pages with their "
        "publisher and date — not Caydex data and not the report's view. This tool is offered "
        "ONLY on a turn where the user explicitly asked for a web search or a check, so call it "
        "for that request — including news requests: the headlines tool may add its licensed "
        "headlines alongside it, never instead of it. Never use this for prices, quotes, price "
        "changes or other market data — exchange rates, the VIX and the DXY included."
    ),
}

TOOL_CAPABILITIES: Dict[str, str] = {
    "get_stock_chart_data": (
        "get_stock_chart_data for a ticker's live quote and 30-day price history — its result "
        "is rendered to the user as an interactive price-chart card beneath your answer (for "
        "the asset this chat is grounded on, the card shown under the first answer stays on "
        "screen and is not repeated), so never say charts are unavailable; describe what the "
        "card shows"
    ),
    "get_analyst_analysis": "get_analyst_analysis for Wall Street ratings, consensus and price targets",
    "get_sentiment_analysis": "get_sentiment_analysis for social and news mood on a ticker",
    "get_ticker_news": (
        "get_ticker_news for recent headlines about a company or coin, and a company's own "
        "latest press releases (its results, guidance and other announcements)"
    ),
    "explain_price_move": (
        "explain_price_move for why a specific ticker moved TODAY — it returns the actual "
        "cause, how unusual the move is for that ticker, how its industry and the market did, "
        "and recent headlines"
    ),
    # The four data tools' lines are short noun lists (final review 2026-10-09): each tool was
    # described three times — its declaration (which already says "call it before answering",
    # with the sections and fields), this line, and a routing paragraph — and the system prompt
    # had grown ~50%. The declaration carries the detail; the routing paragraph carries the rules
    # found nowhere else; the VIX / DXY line lives once, in MACRO READINGS.
    OWNERSHIP_TOOL: (
        "check_ownership_filings for who owns a company's stock — insiders' holdings and "
        "trades, institutional owners, the float, short interest and, on the user's plan, "
        "congressional disclosures"
    ),
    FINANCIALS_TOOL: (
        "check_company_financials for a company's reported figures — growth, margins, cash "
        "flow, debt and cash, valuation, earnings, estimates when available, segments, "
        "dividends and splits"
    ),
    PROFILE_TOOL: (
        "check_asset_profile for a company's executives, size, headquarters, listing date and "
        "peers, a fund's fees and holdings, or a coin's supply"
    ),
    _MARKET_TOOL: (
        "get_market_snapshot for how the market is doing today — sector moves, leaders and "
        "laggards, top gainers and losers and the day's market news — plus dated official "
        "macro readings (rates, Treasury yields, inflation, unemployment and exchange rates)"
    ),
    "get_market_overview": (
        "get_market_overview for the index's valuation (P/E, forward P/E, earnings yield), "
        "sector performance and macro outlook labels"
    ),
    WEB_SEARCH_TOOL: (
        "web_search for third-party web pages on what the user explicitly asked you to look "
        "up, verify or get the latest on in this message — once per question, and never for "
        "prices, quotes or market data (exchange rates, the VIX and the DXY included)"
    ),
}

# ── The web tool's tier variants (kept OUT of the two registries above) ──────────
# "explicit" (an explicit search / verify ask, round 1 forced to the search) is the registry text.
# "news" (a latest-news ask): Caydex's licensed news is forced in round 1 — the company's headline
# tools, or the market snapshot when no company is in view — so the search is offered only for what
# it does not cover; used ONLY when round 1 really is forced to them
# (`chat_web_search_service.web_search_mode(turn, allowed)`), else the "explicit" text. "auto" (the automatic fallback, never forced): only
# after Caydex's data and tools could not answer. The explicit text says the tool is offered only
# on an explicit ask and MUST be called; declared unchanged on the other tiers, it would make the
# model search on nearly every turn that sees it. Every variant keeps the never-for-market-data
# phrases the Privacy Policy states as a model instruction (`test_legal_pages`).
_WEB_QUERY_GUIDE = (
    "`query`: a short search — the company name or ticker, the topic and, if needed, a year or "
    "quarter; never a figure, price or percentage, and nothing about the user. Optional "
    "`recency`: day, week, month or year. Returns third-party pages with their publisher and "
    "date — not Caydex data and not the report's view."
)
_WEB_NEVER_MARKET = (
    "Never use this for prices, quotes, price changes or other market data — exchange rates, "
    "the VIX and the DXY included."
)
WEB_SEARCH_TIER_DESCRIPTIONS: Dict[str, str] = {
    "explicit": TOOL_DESCRIPTIONS[WEB_SEARCH_TOOL],
    "news": (
        "Search the public web — on this turn the user asked for the latest news. Caydex's "
        "licensed headlines come first (the company's own, or the market-wide news when no "
        "company is in view) and were fetched before this tool is offered: call it at most "
        "once, and only for what those headlines and Caydex's data do not cover. "
        + _WEB_QUERY_GUIDE + " " + _WEB_NEVER_MARKET
    ),
    "auto": (
        "Search the public web as a FALLBACK: call it only after Caydex's data and tools could "
        "not answer — for example an event, a lawsuit or regulatory action, a product launch, "
        "what management said, an IPO or economic calendar, a filing's text or a private "
        "company — and at most once. Never call it to restate or check a figure Caydex's data "
        "holds. " + _WEB_QUERY_GUIDE + " " + _WEB_NEVER_MARKET
    ),
}
WEB_SEARCH_TIER_CAPABILITIES: Dict[str, str] = {
    "explicit": TOOL_CAPABILITIES[WEB_SEARCH_TOOL],
    "news": (
        "web_search for third-party web pages after Caydex's licensed headlines, only for what "
        "they do not cover — once per question, and never for prices, quotes or market data "
        "(exchange rates, the VIX and the DXY included)"
    ),
    "auto": (
        "web_search as a fallback for third-party web pages, only when Caydex's data and tools "
        "cannot answer — once per question, never to restate a Caydex figure, and never for "
        "prices, quotes or market data (exchange rates, the VIX and the DXY included)"
    ),
}


def _web_mode(mode: Optional[str]) -> str:
    """A `web_search_mode` normalised to `WEB_SEARCH_MODES`; anything else is "explicit" (the
    registry text, the 2026-10-02 behaviour)."""
    return mode if isinstance(mode, str) and mode in WEB_SEARCH_MODES else "explicit"

# What a ticker tool answers when the model's argument is not a symbol. Fixed text —
# never an echo of the argument, which is model output.
_INVALID_TICKER = {"error": "invalid or missing ticker"}

# Tools that take no arguments / a symbol rather than a ticker.
_NO_ARG_TOOLS = frozenset({_MARKET_TOOL})
_SYMBOL_ARG_TOOLS = frozenset({"get_market_overview"})
# The tools with a closed-vocabulary `section` beside the ticker (`normalize_section`).
_SECTION_ARG_TOOLS = SECTION_TOOLS
# The ONE tool with a free-form argument. The query is model output: the service sanitizes it
# (no figure, no URL, no email, ≤ 16 words) before anything leaves the server, and validates
# `recency` itself — no `enum` in the schema, which a strict declaration validator can 400 on.
_QUERY_ARG_TOOLS = frozenset({WEB_SEARCH_TOOL})


def _declaration(name: str, web_search_mode: Optional[str] = None) -> types.FunctionDeclaration:
    description = (
        WEB_SEARCH_TIER_DESCRIPTIONS[_web_mode(web_search_mode)] if name == WEB_SEARCH_TOOL
        else TOOL_DESCRIPTIONS[name]
    )
    if name in _NO_ARG_TOOLS:
        return types.FunctionDeclaration(
            name=name, description=description,
            parameters=types.Schema(type=types.Type.OBJECT, properties={}),
        )
    if name in _QUERY_ARG_TOOLS:
        return types.FunctionDeclaration(
            name=name, description=description,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "A short web search: the company name or ticker, the topic and, if "
                            "needed, a year or quarter. Never a figure, price or percentage."
                        ),
                    ),
                    "recency": types.Schema(
                        type=types.Type.STRING,
                        description="Optional: how recent the pages must be — day, week, month or year.",
                    ),
                },
                required=["query"],
            ),
        )
    if name in _SECTION_ARG_TOOLS:
        return types.FunctionDeclaration(
            name=name, description=description,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "ticker": types.Schema(
                        type=types.Type.STRING,
                        description="The company's stock ticker symbol (e.g. AAPL, TSLA, MSFT).",
                    ),
                    "section": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Optional: which figures — one of " + ", ".join(FINANCIAL_SECTIONS)
                            + ". Defaults to summary."
                        ),
                    ),
                },
                required=["ticker"],
            ),
        )
    if name in KIND_TOOLS:
        return types.FunctionDeclaration(
            name=name, description=description,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "ticker": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "The ticker symbol of the company, fund or coin (e.g. AAPL, SPY, "
                            "BTC)."
                        ),
                    ),
                    "kind": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "Optional: what the user means by the ticker — one of "
                            + ", ".join(PROFILE_KINDS)
                            + ". Leave it out unless the user's words say which, for a symbol "
                            "a coin shares with a listed company or fund."
                        ),
                    ),
                },
                required=["ticker"],
            ),
        )
    if name in _SYMBOL_ARG_TOOLS:
        return types.FunctionDeclaration(
            name=name, description=description,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "symbol": types.Schema(
                        type=types.Type.STRING,
                        description="The index symbol (e.g. ^GSPC, ^DJI, ^IXIC).",
                    ),
                },
                required=["symbol"],
            ),
        )
    return _ticker_tool(name, description)


# Stable order for the declarations and the prompt paragraph (dict order is insertion
# order, but say so rather than rely on it).
_TOOL_ORDER = (
    "get_stock_chart_data", "get_analyst_analysis", "get_sentiment_analysis",
    "get_ticker_news", "explain_price_move", FINANCIALS_TOOL, OWNERSHIP_TOOL, PROFILE_TOOL,
    _MARKET_TOOL, "get_market_overview", WEB_SEARCH_TOOL,
)


def build_chat_tool_declarations(
    asset_type: Optional[str] = None, *, web_search: bool = False,
    web_search_mode: Optional[str] = None,
) -> List[types.Tool]:
    """The tools the agentic chat may call, filtered to those meaningful for `asset_type`
    (plus `web_search` only when the turn's gate opened — see `tools_for_asset_type` — with the
    description of its tier, `web_search_mode`)."""
    allowed = tools_for_asset_type(asset_type, web_search=web_search)
    decls = [_declaration(name, web_search_mode) for name in _TOOL_ORDER if name in allowed]
    # An empty `function_declarations` list is not a valid Tool — return no tools at all.
    return [types.Tool(function_declarations=decls)] if decls else []


def _round_job_cap() -> int:
    """`CHAT_TOOL_ROUND_MAX_JOBS` for the prompt — the same setting the round planner reads;
    its declared default when the value is not an int in 1..16 (never a bool)."""
    raw = settings.CHAT_TOOL_ROUND_MAX_JOBS
    if isinstance(raw, int) and not isinstance(raw, bool) and 1 <= raw <= 16:
        return raw
    return 8


def capability_block(allowed: frozenset, *, web_search_mode: Optional[str] = None) -> str:
    """The system-prompt paragraph that names ONLY the tools this chat actually has.

    Two rules ride on it and are conditioned the same way:
      * "WHEN THE USER ASKS WHY, CALL A TOOL" names `explain_price_move` only where that tool
        exists (it does not on INDEX or COMMODITY), and the sector/market half names
        `get_market_snapshot` only where it exists (every class today).
      * "NEVER END A WHY QUESTION WITH I DON'T KNOW" is unconditional — it is about the SHAPE
        of the answer — but the `bottom_line` hint is attached only when the tool that
        returns it is granted.

    Returns "" when no tool is granted, so a tool-less chat is told nothing false.
    """
    names = [n for n in _TOOL_ORDER if n in allowed]
    if not names:
        return ""
    lines = "; ".join(
        WEB_SEARCH_TIER_CAPABILITIES[_web_mode(web_search_mode)] if n == WEB_SEARCH_TOOL
        else TOOL_CAPABILITIES[n]
        for n in names
    )
    text = (
        "WHAT YOU CAN ANSWER. You are not limited to a single company's price. You have: "
        + lines + ". "
    )
    if len(names) > 1:
        # A round's calls run CONCURRENTLY (`gemini._gather_tool_calls`) and the send door
        # allows a single round, so one step with every tool beats a chain of steps. The number
        # is the round's job cap — a call past it is refused (`too_many_tool_calls`).
        text += (
            "When a question needs several tools, request them together in one step (at most "
            f"{_round_job_cap()}) rather than one after another. "
        )
    has_why = "explain_price_move" in allowed
    has_snapshot = _MARKET_TOOL in allowed
    has_news = "get_ticker_news" in allowed
    if has_why or has_snapshot or has_news:
        text += "WHEN THE USER ASKS 'WHY', CALL A TOOL BEFORE ANSWERING. "
        if has_why:
            text += (
                "'Why is X down today?' means call explain_price_move — never restate the price, "
                "the change and the volume back to the user and stop, because that answers a "
                "different question than the one asked. "
            )
        elif has_news:
            text += (
                "'Why is X down today?' means call get_ticker_news first — never restate the "
                "price, the change and the volume back to the user and stop. "
            )
        if has_snapshot:
            text += (
                "'Why is <sector> lagging?', 'what's hot today?' and 'what topics are hot?' mean "
                "call get_market_snapshot; it covers every sector by name, so you can answer "
                "sector questions and must not say you only handle individual stocks. "
            )
        # The tools stamp WHICH session their percentages describe (`session` on a move,
        # `as_of_session.word` on the snapshot). Pre-market Monday every number is
        # Friday's close-to-close move; a model told nothing else says "today".
        text += (
            "The tools tell you WHICH SESSION a change belongs to (`session`, "
            "`as_of_session.word`, `session_date`): use that word — 'on Fri', not 'today' "
            "— whenever it is not 'today', because pre-market the numbers are still the "
            "previous session's. "
        )
    text += (
        "NEVER END A 'WHY' QUESTION WITH 'I DON'T HAVE THAT INFORMATION'. Every such "
        "question gets one of exactly three answers: (a) the actual cause, when a tool "
        "gives you one; (b) that the move is ordinary — say it moved within its normal "
        "range, the everyday up-and-down, and give the number; or (c) that the move is "
        "genuinely large but no single catalyst is visible in that session's news — say "
        "that plainly, name the session, and then give the context you DO have. "
    )
    if has_why:
        text += (
            "The explain_price_move tool returns a `bottom_line` written for exactly this; "
            "use it rather than declining. "
        )
    text += (
        "THIS APPLIES TO SECTORS, INDUSTRIES, COMMODITIES AND THEMES TOO, not only tickers. "
    )
    if has_snapshot:
        text += (
            "get_market_snapshot lists every sector and every industry that moved, so for "
            "'why is copper down' or 'what's happening in semiconductors' name the move, "
            "compare it with its sector and the market, and use the market news summary for "
            "the wider driver. "
        )
    # "reason" here is the CAUSE of a price move — the WHY-question rule above. Written as
    # "a reason a tool did not give you", the model generalised it to every fact a tool
    # did not return and answered "Who maintains DOGE?" with "Caydex does not have
    # information on who maintains DOGE" (TestFlight 2026-09-16, E5). Background facts
    # are the model's own to answer (`ChatService._KNOWLEDGE_RULE`); the guard is on
    # invented CAUSES, and on padding, only.
    text += (
        "Never invent a CAUSE for a price move that a tool did not give you, and never pad "
        "an answer with a guess — but never stop at 'I don't know' either. "
    )
    if FINANCIALS_TOOL in allowed:
        # Company-reported figures used to be answered from memory in a NORMAL chat — no tool
        # carried them. "Analysts' estimates" is named only while that dataset is licensed.
        estimates = (
            ", analysts' revenue and EPS estimates" if analyst_estimates_available() else ""
        )
        text += (
            "FINANCIAL FIGURES — revenue, EPS, margins, cash flow, debt and cash, valuation "
            "multiples, dividends, splits, earnings dates and results"
            + estimates
            + " — mean call check_company_financials (the summary first, a section for depth); "
            "give each figure with its period, basis and currency, and never answer them from "
            "memory. "
        )
    if OWNERSHIP_TOOL in allowed:
        # The TestFlight 1.0 (11) dead end: "how many shares does he own now?" was answered
        # "Caydex does not have information on his current total ownership" — with no tool
        # that could have said otherwise.
        text += (
            "OWNERSHIP QUESTIONS — how many shares an insider owns or has left after a sale or "
            "purchase, how much insiders or institutions own, who the biggest holders are, "
            "whether insiders or institutions are buying or selling, the float, short interest, "
            "or congressional trading — mean call check_ownership_filings before answering; "
            "never say Caydex has no ownership information without calling it. Give every "
            "holding with its as-of filing date, never as a live count, and keep shares held "
            "directly and through trusts or entities as separate figures. A sale's dollar "
            "figure is its proceeds; shares withheld for taxes are not a sale. When the result "
            "says congressional disclosures are locked, say they are on Caydex Pro and name "
            "no member and no trade. "
        )
    if PROFILE_TOOL in allowed:
        # "Who is the CEO of X", "SPY's expense ratio", "Bitcoin's max supply" were answered
        # from memory: no tool carried a profile for any ticker but the one on screen.
        # The "not in Caydex's data" clause is scoped to CURRENT officers and profile figures:
        # written as "a role it does not list", it reached founders, creators and maintainers —
        # roles no profile lists — and revived the TestFlight E5 refusal ("Who maintains DOGE?"
        # → "Caydex has no information"), against WHAT YOU KNOW's background tier.
        # Its "facts take precedence over memory" sentence is gone: CAYDEX DATA FIRST says it in
        # every prompt (final review 2026-10-09). The rules found only here stay.
        text += (
            "COMPANY, FUND AND COIN FACTS — who runs a company or its executives, its employees, "
            "headquarters, listing date or peers, a fund's expense ratio, holdings or sector "
            "weights, or a coin's supply — mean call check_asset_profile before answering. A "
            "current officer (the CEO, the CFO or another executive) or a profile figure it does "
            "not list is not in Caydex's data, so say that rather than naming a person or giving "
            "a figure from memory. Founders, creators, maintainers and past leaders are "
            "background, not profile data: answer them from general knowledge, hedged where they "
            "could have changed. Read the `resolved_as` line to know which asset the facts "
            "describe. "
        )
    if has_news:
        # The licensed answer to "what guidance did X give": the company's own releases ride
        # with the headlines (`press_releases`). A release is the issuer's statement.
        text += (
            "COMPANY ANNOUNCEMENTS — what a listed company announced, reported or guided to — "
            "mean call get_ticker_news and read its press_releases: attribute each to the "
            "company with its date ('the company said on Oct 1, 2026 …'), as the company's own "
            "statement, never as independent reporting or as Caydex's view. "
        )
    if has_snapshot:
        # Macro readings (FRED, public-domain series) ride in the snapshot, dated. FX is market
        # data: the only answer is that block. The VIX and the DXY are not in our data at all.
        text += (
            "MACRO READINGS — an interest rate, a Treasury yield, inflation, unemployment or an "
            "exchange rate — mean call get_market_snapshot and give its dated macro reading "
            "('as of <date>'), never a figure from memory. The VIX and the DXY are not in "
            "Caydex data: say so plainly rather than estimating them. "
        )
        if "get_market_overview" in allowed:
            text += (
                "get_market_overview's macro indicators are written outlook labels, not "
                "measured readings: for a rate, an inflation figure or an exchange rate, use "
                "the snapshot's dated readings. "
            )
    return text


# What a follow-up CHIP may ask — prose only (no tool identifiers: the chip prompt runs
# with `tools_granted=False`, and `test_chat_capability_block` pins that such an
# instruction names no tool). Read by `ChatService.generate_followup_suggestions` and
# mirrored, deterministically, by `chat_chip_filter` — the prompt is the first line of
# defence, the filter the one that cannot be talked out of it.
#
# Built from the GRANTED tool set, never from a fixed list: an INDEX chat has no
# sentiment or quote tool and a COMMODITY chat has no chart, so a scope that advertised
# "the mood in news and social chatter" there proposed exactly the dead-end chip this
# block exists to prevent (review finding, 2026-09-19).
_CHIP_SCOPE_BY_TOOL: Dict[str, str] = {
    "get_stock_chart_data": "the live price, today's change, volume and market cap",
    "get_ticker_news": "recent news",
    "explain_price_move": "why the price moved",
    FINANCIALS_TOOL: (
        "reported financial figures — revenue, earnings, margins, cash flow, debt and cash, "
        "valuation multiples, earnings dates and results, revenue by segment, dividends and "
        "splits"
    ),
    OWNERSHIP_TOOL: (
        "who owns it — insiders' reported share holdings and recent buying or selling, "
        "institutional ownership and changes, the float and short interest"
    ),
    # The class-neutral wording (a general chat may ask about any of the three); a chat on one
    # kind of asset gets its own clause (`_PROFILE_CHIP_SCOPE_BY_ASSET_TYPE`), so a coin chat is
    # never steered to "who is the CEO".
    PROFILE_TOOL: (
        "its key facts — a company's CEO and executives, headcount, headquarters, listing "
        "date and peers; a fund's expense ratio, holdings and sector weights; a coin's supply"
    ),
    "get_market_snapshot": (
        "how the market and its sectors are doing, and the latest official macro readings "
        "(interest rates, inflation, unemployment, exchange rates)"
    ),
    "get_market_overview": "how the market and its sectors are doing, the index's level, valuation and breadth",
    "get_sentiment_analysis": "the mood in news and social chatter",
    "get_analyst_analysis": "analyst ratings and consensus",
}
_PROFILE_CHIP_SCOPE_BY_ASSET_TYPE: Dict[str, str] = {
    "STOCK": "its key facts — the CEO and executives, headcount, headquarters, listing date and peers",
    "ETF": "the fund's key facts — its expense ratio, assets, top holdings and sector weights",
    "CRYPTO": "the coin's supply — circulating, total and maximum — and its fully diluted value",
}
_CHIP_SCOPE_ALWAYS = (
    "what the asset is, its history, who created or maintains it and how it works; "
    "where and how it can be bought (as availability, not advice); and the outlook framed "
    "as scenarios, drivers and risks"
)
_CHIP_SCOPE_BY_ASSET_TYPE: Dict[str, str] = {
    "STOCK": "the company's fundamentals, margins, growth, valuation multiples and financial history; ",
    "NORMAL": "a company's fundamentals, margins, growth, valuation multiples and financial history; ",
    "ETF": "the fund's expense ratio, holdings, sector mix and benchmark comparison; ",
    "CRYPTO": "the coin's tokenomics, supply schedule, adoption, consensus and on-chain activity; ",
    "INDEX": "the index's constituents, breadth, sector rotation and valuation; the level and today's move shown on screen; ",
    "COMMODITY": "supply and demand, seasonality, geopolitics and the rates/inflation link; the price and move shown on screen; ",
}
_CHIP_FORBIDDEN = (
    "NEVER propose: whether to buy, sell, hold or trade it ('should I…'); whether it suits "
    "the user personally; a price prediction, price target or forecast ('will it go up', "
    "'what will the price be'); portfolio sizing"
)
_CHIP_FORBIDDEN_ANALYST = "; analyst ratings, consensus or upgrades/downgrades"
_CHIP_FORBIDDEN_TAIL = ". Cay AI declines those, and a chip the answer declines is a dead end."
# A Learn chat (a book, a Money Moves article, a Journey lesson) is about an IDEA, not an
# asset: the STOCK scope steered its chips to price and fundamentals on a lesson page.
_LEARN_CONTEXT_TYPES = frozenset({"BOOK", "MONEY_MOVES_ARTICLE", "JOURNEY_LESSON"})
# A chat opened from the Updates tab is about the NEWS first — what is moving the subject and
# how the tone has shifted — so its chips lead with that before the asset scope.
_CHIP_SCOPE_UPDATES = (
    "the latest news and what is driving it, the Insight card's points, and how the news "
    "tone has shifted over recent days; "
)
_CHIP_SCOPE_LEARN = (
    "ANSWERABLE SCOPE — propose ONLY questions Cay AI can answer, which are about: the "
    "idea just discussed — what it means, how it works, how an investor applies it, a "
    "worked example, the common mistakes, how it connects to related concepts, and where "
    "the reader could go next in the material. "
    + _CHIP_FORBIDDEN + _CHIP_FORBIDDEN_ANALYST + _CHIP_FORBIDDEN_TAIL + " "
)


def chip_scope_block(asset_type: Optional[str], context_type: Optional[str] = None) -> str:
    """The ANSWERABLE SCOPE paragraph for the follow-up chip prompt.

    The chip generator used to know nothing about what the chat could answer, so it
    offered "where can I buy DOGE?" and "Who maintains DOGE?" and the next turn declined
    both (TestFlight 2026-09-16, E3). Prose only — see the module comment above. The
    data clauses follow the tools the asset class is actually granted (`tools_for_asset_type`,
    licence-aware), so the scope never promises a number the answer cannot fetch; a Learn
    context gets the concept scope instead of an asset one.
    """
    ctx = (context_type or "").strip().upper()
    if ctx in _LEARN_CONTEXT_TYPES:
        return _CHIP_SCOPE_LEARN
    key = (asset_type or "NORMAL").strip().upper()
    specific = _CHIP_SCOPE_BY_ASSET_TYPE.get(key, _CHIP_SCOPE_BY_ASSET_TYPE["NORMAL"])
    if ctx == "UPDATES_SCOPE":
        specific = _CHIP_SCOPE_UPDATES + specific
    allowed = tools_for_asset_type(key)

    def _clause(name: str) -> str:
        if name == PROFILE_TOOL:
            return _PROFILE_CHIP_SCOPE_BY_ASSET_TYPE.get(key, _CHIP_SCOPE_BY_TOOL[name])
        return _CHIP_SCOPE_BY_TOOL[name]

    data_clauses = [_clause(name) for name in _TOOL_ORDER if name in allowed and name in _CHIP_SCOPE_BY_TOOL]
    data = "; ".join(data_clauses)
    forbidden = _CHIP_FORBIDDEN + ("" if "get_analyst_analysis" in allowed else _CHIP_FORBIDDEN_ANALYST) + _CHIP_FORBIDDEN_TAIL
    return (
        "ANSWERABLE SCOPE — propose ONLY questions Cay AI can answer, which are about: "
        + specific + (data + "; " if data else "") + _CHIP_SCOPE_ALWAYS + ". " + forbidden + " "
    )


def _is_profiled_index(symbol: str) -> bool:
    """True only for an index `index_service` has a profile for (`^GSPC`, `^IXIC`, `^DJI`).

    Lazy import: this module is imported by the Gemini declaration path and must not pull
    the index service (and its datastore client) in at module load.
    """
    from app.services.index_service import _INDEX_PROFILES

    return symbol in _INDEX_PROFILES


def _accepts_keyword(fn: Any, name: str) -> bool:
    """Whether `fn` can be called with keyword `name` (or takes ``**kwargs``)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


def build_chat_tool_handlers(
    svc: Any,
    screen_symbol: Optional[str] = None,
    screen_asset_type: Optional[str] = None,
    user_id: Optional[str] = None,
    web_turn: Any = None,
    user_tier: Optional[str] = None,
) -> Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]]:
    """Map each tool name → async handler delegating to the ChatService fetch methods.

    `user_tier` is the caller's plan (`user["tier"]`). Only the ownership tool reads it, and
    only to unlock congressional disclosures: None — the default every caller that does not
    pass it gets — "free" and anything unrecognised stay LOCKED
    (`entitlements.congress_holders_unlocked` fails closed), so no member's name or trade
    reaches a Free caller's answer.

    `web_turn` is the turn's `chat_web_search_service.WebSearchTurn`, or None. Only when it
    exists does the map gain the `web_search` handler — every round, specialist and the
    stream→non-stream fallback that shares this turn shares its ONE search. `web_search` is the
    only tool that spends a paid search: `explain_price_move`'s grounded third tier was retired
    on 2026-10-02, so the `web_escalation=False` its handler still passes on a web turn is an
    accepted no-op (kept so a paid tier added there later stays off on a turn that already
    carries the user's search).

    `screen_symbol` is the session's `stock_id` and `screen_asset_type` the class the screen
    resolved it as (STOCK / ETF / CRYPTO / …). Together they let a handler trust the screen
    over the ticker's spelling — see `_resolve` below. `user_id` is forwarded to
    `explain_price_move`, which accepts and ignores it today; the `web_search` handler meters
    its spend through `web_turn.user_id` instead.
    """
    screen = (screen_symbol or "").strip().upper()
    # Every NON-crypto screen earns the exemption — STOCK, and also ETF / INDEX / COMMODITY,
    # whose detail views open the session with their own symbol: the Grayscale Bitcoin and
    # Ethereum Mini Trusts are listed ETFs whose tickers are "BTC" and "ETH", and on THEIR
    # screen the model naming "ETH" means the trust on screen, not Ether. A CRYPTO session
    # is the one class that must NOT be exempt: it may store the bare coin ("BTC" — iOS sent
    # that form before 2026-08-20 and `sanitize_symbol` only checks shape), and there the
    # bare form must still canonicalise to the pair, or the chart leg would serve the
    # Grayscale trust's quote beside a Bitcoin base card.
    screen_is_equity = bool(screen) and (screen_asset_type or "").strip().upper() != "CRYPTO"

    # `ChatService._chat_symbol` resolves a bare coin ticker ("BTC") to the pair the data
    # path prices ("BTCUSD"); without it the card served the Grayscale ETF's quote under a
    # 24/7 "Live" dot. EXCEPT when the model names the screen's own symbol on an equity
    # screen: on the LTC Properties (NYSE: LTC) detail screen the model calls
    # get_stock_chart_data("LTC") and means the REIT the user is looking at, not Litecoin —
    # the screen resolved the asset class already, and canonicalising it here re-created
    # the bare-coin collision that migration 160 fixed (BTC/ETH trusts, XRP, LTC, BCH, ATOM).
    # A DIFFERENT ticker than the screen's is a user-typed one and keeps chat's "bare coin =
    # the coin" rule. Falls back to a plain upper-case for a service double that lacks
    # `_chat_symbol`.
    #
    # Returns (symbol, on_equity_screen). The flag matters beyond the symbol: the news,
    # sentiment and price-move fetchers each re-derive `is_crypto` from the TICKER with
    # bare coins included, so "LTC" would still route to the crypto branch — the exemption
    # has to travel with the call as an explicit `is_crypto=False`.
    #
    # ⚠️ THE ARGUMENT IS MODEL OUTPUT, and it used to reach every fetcher unchecked. A
    # blank ticker went all the way to `fmp.get_stock_news(ticker="")`, which OMITS the
    # symbol filter — FMP then serves its default APPLE feed, ~1,000 rows of which were
    # persisted under `ticker=""` and read back as the named company's sentiment. A
    # mis-keyed call (`{"symbol": "TSLA"}`, the key the market tool uses) did the same.
    # `sanitize_symbol` is the closed-vocabulary gate `stock_id` already goes through;
    # it accepts every legitimate spelling (AAPL, BRK.B, BRK-B, ^GSPC, BTC, BTCUSD,
    # GCUSD) and returns None for '', 'Apple Inc (AAPL)', 'AAPL, MSFT' and anything
    # over 16 chars. A None here is answered with `_INVALID_TICKER` BEFORE any fetch —
    # the same `{"error": …}` shape `_run_tool_handler` counts as a failed tool, so a
    # turn whose only tool was mis-called settles as degraded rather than charged for
    # Apple's sentiment. `symbol` is accepted as a fallback key for the ticker tools
    # because that is the mis-keying the model actually produces.
    def _resolve(args: Dict[str, Any]) -> tuple:
        raw = sanitize_symbol(args.get("ticker") or args.get("symbol"))
        if raw is None:
            return None, False
        if screen_is_equity and raw == screen:
            return raw, True
        canon = getattr(svc, "_chat_symbol", None)
        return (canon(raw) if callable(canon) else raw), False

    def _invalid(args: Dict[str, Any]) -> Dict[str, Any]:
        logger.warning(
            "chat tool: refusing a non-symbol ticker argument (keys=%s, len=%d)",
            sorted(args) if isinstance(args, dict) else type(args).__name__,
            len(str((args or {}).get("ticker") or (args or {}).get("symbol") or ""))
            if isinstance(args, dict) else 0,
        )
        return dict(_INVALID_TICKER)

    async def _stock(args: Dict[str, Any]) -> Dict[str, Any]:
        sym, _ = _resolve(args)
        if sym is None:
            return _invalid(args)
        return await svc._fetch_stock_widget_data(sym)

    async def _analyst(args: Dict[str, Any]) -> Dict[str, Any]:
        sym, _ = _resolve(args)
        if sym is None:
            return _invalid(args)
        return await svc._fetch_analyst_data(sym)

    async def _sentiment(args: Dict[str, Any]) -> Dict[str, Any]:
        sym, on_equity = _resolve(args)
        if sym is None:
            return _invalid(args)
        if on_equity:
            return await svc._fetch_sentiment_data(sym, is_crypto=False)
        return await svc._fetch_sentiment_data(sym)

    screen_is_index = (
        bool(screen) and (screen_asset_type or "").strip().upper() == "INDEX"
        and screen.startswith("^")
    )

    async def _market(args: Dict[str, Any]) -> Dict[str, Any]:
        # An OMITTED symbol takes the screen's own index on an INDEX screen, else the
        # declared default; a PRESENT but non-symbol one ("S&P 500", "the market") is an
        # error rather than silently the S&P — the model may have meant the Dow.
        #
        # Only a PROFILED index is accepted. `sanitize_symbol` checks shape, so "AAPL"
        # passed — and on the ^GSPC screen ("how does the market compare with Apple?")
        # the model's only per-symbol tool ran the FULL index pipeline for AAPL: its
        # profile quote, its entire daily history in pages, a Gemini story generation,
        # and `index_cache` / `index_macro_forecast_cache` rows persisted under 'AAPL'.
        # The result was the S&P's global valuation with no symbol on it, narrated as
        # Apple's on a charged turn — repeatable per turn for any symbol the model invents.
        # `ticker` is read as the mis-keyed fallback, mirroring `_resolve`.
        raw = (args or {}).get("symbol")
        if raw is None or not str(raw).strip():
            raw = (args or {}).get("ticker")
        if raw is None or not str(raw).strip():
            symbol = screen if screen_is_index else "^GSPC"
        else:
            symbol = sanitize_symbol(raw)
            if symbol is None or not _is_profiled_index(symbol):
                return _invalid({"symbol": raw})
        return await svc._fetch_market_overview_data(symbol)

    async def _news(args: Dict[str, Any]) -> Dict[str, Any]:
        sym, on_equity = _resolve(args)
        if sym is None:
            return _invalid(args)
        if on_equity:
            return await svc._fetch_ticker_news_data(sym, is_crypto=False)
        return await svc._fetch_ticker_news_data(sym)

    async def _why(args: Dict[str, Any]) -> Dict[str, Any]:
        sym, on_equity = _resolve(args)
        if sym is None:
            return _invalid(args)
        if web_turn is not None:
            # This turn already carries the user's own web search. The price-move ladder is free
            # today (its paid tier was retired), so `web_escalation=False` is a no-op — kept so a
            # paid tier added there later can never buy a SECOND search on a web turn.
            kw: Dict[str, Any] = {"is_crypto": False} if on_equity else {}
            return await svc._fetch_price_move_data(
                sym, user_id=user_id, web_escalation=False, **kw)
        if on_equity:
            return await svc._fetch_price_move_data(sym, is_crypto=False, user_id=user_id)
        return await svc._fetch_price_move_data(sym, user_id=user_id)

    async def _snapshot(args: Dict[str, Any]) -> Dict[str, Any]:
        return await svc._fetch_market_snapshot_data()

    def _resolve_equity(args: Any) -> Optional[str]:
        # The filings and the statements exist only for listed securities, so the symbol is
        # NEVER canonicalised to a coin pair — on any screen: "LTC" is LTC Properties (the
        # tools would refuse Litecoin's "LTCUSD" outright), "BTC" the listed Bitcoin trust.
        # Each tool says what it resolved to (`resolved_as`). Same closed-vocabulary gate as
        # `_resolve`, with the same mis-keyed `symbol` fallback.
        a = args if isinstance(args, dict) else {}
        return sanitize_symbol(a.get("ticker") or a.get("symbol"))

    async def _financials(args: Dict[str, Any]) -> Dict[str, Any]:
        a = args if isinstance(args, dict) else {}
        sym = _resolve_equity(a)
        if sym is None:
            return _invalid(a)
        # Normalised BEFORE the service is called: the service only ever sees a member of
        # `FINANCIAL_SECTIONS`, and the model's own value never reaches the result.
        section, note = normalize_section(a.get("section"))
        result = await svc._fetch_financials_data(sym, section)
        if note and isinstance(result, dict):
            result = {**result, "section_note": note}
        return result

    async def _ownership(args: Dict[str, Any]) -> Dict[str, Any]:
        sym = _resolve_equity(args)
        if sym is None:
            return _invalid(args if isinstance(args, dict) else {})
        fetch = svc._fetch_ownership_data
        if congress_holders_unlocked(user_tier):
            if _accepts_keyword(fetch, "user_tier"):
                # A plan that includes congressional disclosures: the tier travels with it.
                return await fetch(sym, user_tier=user_tier)
            # A fetch that cannot take the tier serves the locked default: fail CLOSED.
            logger.warning(
                "chat tool check_ownership_filings: the ownership fetch takes no user_tier — "
                "serving the locked default to a %s caller", user_tier,
            )
        # Every other tier — None included — is the locked default of the fetch itself.
        return await fetch(sym)

    async def _profile(args: Dict[str, Any]) -> Dict[str, Any]:
        a = args if isinstance(args, dict) else {}
        # The same closed-vocabulary gate, and NO coin canonicalisation: the profile tool
        # classifies the symbol ITSELF against the screen (company / fund / coin) — on the LTC
        # Properties screen "LTC" is the REIT, on the Grayscale trust's ETF screen "BTC" is the
        # fund, in a general chat a bare "BTC" is Bitcoin — and says which (`resolved_as`).
        # Turning "LTC" into "LTCUSD" here would decide the class before the screen is read.
        sym = _resolve_equity(a)
        if sym is None:
            return _invalid(a)
        # Normalised BEFORE the service is called: the service only ever sees a member of
        # `PROFILE_KINDS` (or None), and the model's own value never reaches the result.
        kind, note = normalize_profile_kind(a.get("kind"))
        result = await svc._fetch_asset_profile_data(
            sym, screen_symbol=screen or None, screen_asset_type=screen_asset_type, kind=kind,
        )
        if note and isinstance(result, dict):
            result = {**result, "kind_note": note}
        return result

    handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
        "get_stock_chart_data": _stock,
        "get_analyst_analysis": _analyst,
        "get_sentiment_analysis": _sentiment,
        "get_market_overview": _market,
        "get_ticker_news": _news,
        "explain_price_move": _why,
        OWNERSHIP_TOOL: _ownership,
        FINANCIALS_TOOL: _financials,
        PROFILE_TOOL: _profile,
        _MARKET_TOOL: _snapshot,
    }
    if web_turn is not None:
        async def _web(args: Dict[str, Any]) -> Dict[str, Any]:
            # Lazy import: this module is loaded by the declaration path and must not pull the
            # budget service and its datastore client in at import.
            from app.services.chat_web_search_service import run_web_search

            a = args if isinstance(args, dict) else {}
            return await run_web_search(web_turn, a.get("query"), a.get("recency"))

        handlers[WEB_SEARCH_TOOL] = _web
    return handlers


# Tool results with these widget_types render as inline widgets; others only inform the answer.
_RENDERABLE_WIDGET_TYPES = {"stock_chart", "market_overview"}


def widget_from_tool_result(result: Any) -> Any:
    """Return the tool result if it's a renderable widget payload (has a known widget_type), else None."""
    if isinstance(result, dict) and result.get("widget_type") in _RENDERABLE_WIDGET_TYPES:
        return result
    return None


def widget_key(widget: Dict[str, Any]) -> str:
    """Dedup key for a widget so a tool-fetched chart doesn't duplicate the deterministic base one."""
    wt = widget.get("widget_type", "")
    ident = widget.get("ticker") or widget.get("symbol") or ""
    return f"{wt}:{str(ident).upper()}"
