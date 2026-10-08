"""Chat function-calling tools for the agentic streaming loop (Phase 2).

Mirrors ``agents/fmp_tools.py``: ``build_chat_tool_declarations()`` returns the ``types.Tool`` list
the model may call mid-stream; ``build_chat_tool_handlers(svc)`` maps each tool name to an async
handler that delegates to the existing ``ChatService`` fetch methods (so the data logic isn't
duplicated). A tool result whose ``widget_type`` is renderable (stock_chart / market_overview)
becomes an inline widget; analyst / sentiment results only inform the model's answer.

Handlers take an svc argument (a ChatService) rather than importing it, to avoid a circular import.
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from google.genai import types

from app.services._analyst_common import analyst_section_available
from app.services.chat_security import sanitize_symbol

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

# Report chat's live web search (`chat_web_search_service`). The ONE name both files share:
# defined HERE and imported by the service, never the reverse — this module must not pull the
# budget / database code in at import (`tests/test_chat_tool_boundary.py`). It is in NO asset
# class's table below: `tools_for_asset_type(..., web_search=True)` adds it only on a turn the
# service's gate opened (REPORT session, TICKER_REPORT screen, the switch, a key, a signed-in
# caller and an explicit ask), so the declaration, the handler map and the prompt's capability
# block all follow that one decision.
WEB_SEARCH_TOOL = "web_search"

_STOCK_TOOLSET = frozenset({
    "get_stock_chart_data", "get_analyst_analysis", "get_sentiment_analysis",
}) | _NEWS_TOOLS | {_MARKET_TOOL, OWNERSHIP_TOOL}

_TOOLS_BY_ASSET_TYPE: Dict[str, frozenset] = {
    # Every chat about a company — its ticker screen, its report and its Updates feed all
    # resolve to STOCK (`ChatService._detect_asset_type`) — gets the ownership tool.
    "STOCK": _STOCK_TOOLSET,
    # No screen context: the user may ask about any stock, so keep the full equity set.
    "NORMAL": _STOCK_TOOLSET,
    # A fund has no analyst coverage, but it does have news sentiment and a real quote.
    "ETF": frozenset({"get_stock_chart_data", "get_sentiment_analysis"})
           | _NEWS_TOOLS | {_MARKET_TOOL},
    # Sentiment IS meaningful for a coin — `sentiment_service` has a crypto news branch — but
    # only if the caller passes `is_crypto`; see `ChatService._fetch_sentiment_data`.
    # News is routed on the same flag, so a coin gets `news/crypto` rather than an equity
    # query for "BTCUSD" that returns nothing.
    "CRYPTO": frozenset({"get_stock_chart_data", "get_sentiment_analysis"})
              | _NEWS_TOOLS | {_MARKET_TOOL},
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
    """
    allowed = _TOOLS_BY_ASSET_TYPE.get((asset_type or "").strip().upper(), _STOCK_TOOLSET)
    if not analyst_section_available():
        allowed = allowed - {"get_analyst_analysis"}
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
        "Call whenever the user asks what is happening with a company, what the news is, or "
        "what is behind a story — and before saying you do not know why something happened."
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
        "as-of date — plus their latest purchase or sale, and the share held by institutions "
        "with the largest institutional holders from the latest quarterly 13F filings. Call it "
        "for any question about how many shares someone owns or holds, what an insider has "
        "left after selling or buying, how much insiders or institutions own, or who the "
        "biggest holders are. The figures are as of each filing, not live."
    ),
    _MARKET_TOOL: (
        "Fetch how the market is doing TODAY: every sector's daily move, the "
        "leading and lagging industries, the biggest gaining and losing stocks, "
        "and today's market news summary with its cited catalyst. Takes no "
        "arguments. Call for any question about sectors, market breadth, what is "
        "hot or trending today, sector rotation, or why the market moved — "
        "including when the user names one sector, such as Basic Materials or "
        "Technology."
    ),
    "get_market_overview": (
        "Fetch overall market valuation (P/E, forward P/E, earnings yield), sector "
        "performance, and macro indicators. For INDEX / broad-market questions, NOT "
        "individual stocks."
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
        "changes or other market data."
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
    "get_ticker_news": "get_ticker_news for recent headlines about a company or coin",
    "explain_price_move": (
        "explain_price_move for why a specific ticker moved TODAY — it returns the actual "
        "cause, how unusual the move is for that ticker, how its industry and the market did, "
        "and recent headlines"
    ),
    OWNERSHIP_TOOL: (
        "check_ownership_filings for who owns a company's stock — each insider's shares held "
        "after their latest reported transaction, as of that filing, and the institutional "
        "ownership with the largest holders"
    ),
    _MARKET_TOOL: (
        "get_market_snapshot for how the market itself is doing today — every sector's move, "
        "leading and lagging industries, the day's biggest gainers and losers, and today's "
        "market news summary"
    ),
    "get_market_overview": (
        "get_market_overview for the index's valuation (P/E, forward P/E, earnings yield), "
        "sector performance and macro indicators"
    ),
    WEB_SEARCH_TOOL: (
        "web_search for third-party web pages on what the user explicitly asked you to look "
        "up, verify or get the latest on in this message — once per question, and never for "
        "prices, quotes or market data"
    ),
}

# What a ticker tool answers when the model's argument is not a symbol. Fixed text —
# never an echo of the argument, which is model output.
_INVALID_TICKER = {"error": "invalid or missing ticker"}

# Tools that take no arguments / a symbol rather than a ticker.
_NO_ARG_TOOLS = frozenset({_MARKET_TOOL})
_SYMBOL_ARG_TOOLS = frozenset({"get_market_overview"})
# The ONE tool with a free-form argument. The query is model output: the service sanitizes it
# (no figure, no URL, no email, ≤ 16 words) before anything leaves the server, and validates
# `recency` itself — no `enum` in the schema, which a strict declaration validator can 400 on.
_QUERY_ARG_TOOLS = frozenset({WEB_SEARCH_TOOL})


def _declaration(name: str) -> types.FunctionDeclaration:
    description = TOOL_DESCRIPTIONS[name]
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
    "get_ticker_news", "explain_price_move", OWNERSHIP_TOOL, _MARKET_TOOL,
    "get_market_overview", WEB_SEARCH_TOOL,
)


def build_chat_tool_declarations(
    asset_type: Optional[str] = None, *, web_search: bool = False,
) -> List[types.Tool]:
    """The tools the agentic chat may call, filtered to those meaningful for `asset_type`
    (plus `web_search` only when the turn's gate opened — see `tools_for_asset_type`)."""
    allowed = tools_for_asset_type(asset_type, web_search=web_search)
    decls = [_declaration(name) for name in _TOOL_ORDER if name in allowed]
    # An empty `function_declarations` list is not a valid Tool — return no tools at all.
    return [types.Tool(function_declarations=decls)] if decls else []


def capability_block(allowed: frozenset) -> str:
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
    lines = "; ".join(TOOL_CAPABILITIES[n] for n in names)
    text = (
        "WHAT YOU CAN ANSWER. You are not limited to a single company's price. You have: "
        + lines + ". "
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
    if OWNERSHIP_TOOL in allowed:
        # The TestFlight 1.0 (11) dead end: "how many shares does he own now?" was answered
        # "Caydex does not have information on his current total ownership" — with no tool
        # that could have said otherwise.
        text += (
            "OWNERSHIP QUESTIONS — how many shares an insider owns or has left after a sale or "
            "purchase, how much insiders or institutions own, who the biggest holders are — "
            "mean call check_ownership_filings before answering; never say Caydex has no "
            "ownership information without calling it. Give every holding with its as-of "
            "filing date, never as a live count, and keep shares held directly and through "
            "trusts or entities as separate figures. "
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
    OWNERSHIP_TOOL: "who owns it — insiders' reported share holdings and institutional ownership",
    "get_market_snapshot": "how the market and its sectors are doing",
    "get_market_overview": "how the market and its sectors are doing, the index's level, valuation and breadth",
    "get_sentiment_analysis": "the mood in news and social chatter",
    "get_analyst_analysis": "analyst ratings and consensus",
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
    data_clauses = [_CHIP_SCOPE_BY_TOOL[name] for name in _TOOL_ORDER if name in allowed and name in _CHIP_SCOPE_BY_TOOL]
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


def build_chat_tool_handlers(
    svc: Any,
    screen_symbol: Optional[str] = None,
    screen_asset_type: Optional[str] = None,
    user_id: Optional[str] = None,
    web_turn: Any = None,
) -> Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]]:
    """Map each tool name → async handler delegating to the ChatService fetch methods.

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

    async def _ownership(args: Dict[str, Any]) -> Dict[str, Any]:
        # The screen exemption matters here too: on LTC Properties' screen "LTC" is the REIT,
        # never canonicalised to Litecoin's pair (which the tool would then refuse).
        sym, _ = _resolve(args)
        if sym is None:
            return _invalid(args)
        return await svc._fetch_ownership_data(sym)

    handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
        "get_stock_chart_data": _stock,
        "get_analyst_analysis": _analyst,
        "get_sentiment_analysis": _sentiment,
        "get_market_overview": _market,
        "get_ticker_news": _news,
        "explain_price_move": _why,
        OWNERSHIP_TOOL: _ownership,
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
