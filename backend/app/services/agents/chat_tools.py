"""Chat function-calling tools for the agentic streaming loop (Phase 2).

Mirrors ``agents/fmp_tools.py``: ``build_chat_tool_declarations()`` returns the ``types.Tool`` list
the model may call mid-stream; ``build_chat_tool_handlers(svc)`` maps each tool name to an async
handler that delegates to the existing ``ChatService`` fetch methods (so the data logic isn't
duplicated). A tool result whose ``widget_type`` is renderable (stock_chart / market_overview)
becomes an inline widget; analyst / sentiment results only inform the model's answer.

Handlers take an svc argument (a ChatService) rather than importing it, to avoid a circular import.
"""

from typing import Any, Awaitable, Callable, Dict, List, Optional

from google.genai import types

from app.services._analyst_common import analyst_section_available


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
# `get_stock_chart_data` is kept for ETF / CRYPTO / COMMODITY on purpose — all three are quoted
# by FMP's `/stable/quote` and the resulting card is honest for them (`pe_ratio` and
# `market_cap` are Optional on `StockChartWidget`, and iOS renders P/E only when present).
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
# point of call; `explain_price_move` escalates to a metered web search only for a material
# move it cannot otherwise explain (see `chat_market_tools`).
_NEWS_TOOLS = frozenset({"get_ticker_news", "explain_price_move"})

_STOCK_TOOLSET = frozenset({
    "get_stock_chart_data", "get_analyst_analysis", "get_sentiment_analysis",
}) | _NEWS_TOOLS | {_MARKET_TOOL}

_TOOLS_BY_ASSET_TYPE: Dict[str, frozenset] = {
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
    # A futures contract has neither analyst coverage nor ticker sentiment. It does have
    # news, and the macro backdrop is most of any commodity answer.
    "COMMODITY": frozenset({"get_stock_chart_data", "get_ticker_news", _MARKET_TOOL}),
}


def tools_for_asset_type(asset_type: Optional[str]) -> frozenset:
    """Tool NAMES the given asset class may call.

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
        "DIFFERENT ticker than the current screen (e.g. a comparison)."
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
        "is for THIS ticker specifically, how its industry and the wider market did, recent "
        "headlines, and — for a large unexplained move — a web-researched catalyst with "
        "sources. ALWAYS call this for any 'why is X up/down' question rather than answering "
        "from the price alone."
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
}

TOOL_CAPABILITIES: Dict[str, str] = {
    "get_stock_chart_data": "get_stock_chart_data for a ticker's live quote and 30-day price history",
    "get_analyst_analysis": "get_analyst_analysis for Wall Street ratings, consensus and price targets",
    "get_sentiment_analysis": "get_sentiment_analysis for social and news mood on a ticker",
    "get_ticker_news": "get_ticker_news for recent headlines about a company or coin",
    "explain_price_move": (
        "explain_price_move for why a specific ticker moved TODAY — it returns the actual "
        "cause, how unusual the move is for that ticker, how its industry and the market did, "
        "and for a big unexplained move a web-researched catalyst with sources"
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
}

# Tools that take no arguments / a symbol rather than a ticker.
_NO_ARG_TOOLS = frozenset({_MARKET_TOOL})
_SYMBOL_ARG_TOOLS = frozenset({"get_market_overview"})


def _declaration(name: str) -> types.FunctionDeclaration:
    description = TOOL_DESCRIPTIONS[name]
    if name in _NO_ARG_TOOLS:
        return types.FunctionDeclaration(
            name=name, description=description,
            parameters=types.Schema(type=types.Type.OBJECT, properties={}),
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
    "get_ticker_news", "explain_price_move", _MARKET_TOOL, "get_market_overview",
)


def build_chat_tool_declarations(asset_type: Optional[str] = None) -> List[types.Tool]:
    """The tools the agentic chat may call, filtered to those meaningful for `asset_type`."""
    allowed = tools_for_asset_type(asset_type)
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
    text += (
        "NEVER END A 'WHY' QUESTION WITH 'I DON'T HAVE THAT INFORMATION'. Every such "
        "question gets one of exactly three answers: (a) the actual cause, when a tool "
        "gives you one; (b) that the move is ordinary — say it moved within its normal "
        "range, the everyday up-and-down, and give the number; or (c) that the move is "
        "genuinely large but no single catalyst is visible in today's news — say that "
        "plainly and then give the context you DO have. "
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
    text += (
        "Never supply a reason a tool did not give you, and never pad an answer with a "
        "guess — but never stop at 'I don't know' either. "
    )
    return text


def build_chat_tool_handlers(
    svc: Any,
    screen_symbol: Optional[str] = None,
    screen_asset_type: Optional[str] = None,
) -> Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]]:
    """Map each tool name → async handler delegating to the ChatService fetch methods.

    `screen_symbol` is the session's `stock_id` and `screen_asset_type` the class the screen
    resolved it as (STOCK / ETF / CRYPTO / …). Together they let a handler trust the screen
    over the ticker's spelling — see `_resolve` below.
    """
    screen = (screen_symbol or "").strip().upper()
    # Only an EQUITY screen earns the exemption. A CRYPTO session may store the bare coin
    # ("BTC" — iOS sent that form before 2026-08-20 and `sanitize_symbol` only checks shape),
    # and there the bare form must still canonicalise to the pair, or the chart leg would
    # serve the Grayscale trust's quote beside a Bitcoin base card.
    screen_is_equity = bool(screen) and (screen_asset_type or "").strip().upper() == "STOCK"

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
    def _resolve(args: Dict[str, Any]) -> tuple:
        raw = str(args.get("ticker") or "").strip().upper()
        if screen_is_equity and raw == screen:
            return raw, True
        canon = getattr(svc, "_chat_symbol", None)
        return (canon(raw) if callable(canon) else raw), False

    def _sym(args: Dict[str, Any]) -> str:
        return _resolve(args)[0]

    async def _stock(args: Dict[str, Any]) -> Dict[str, Any]:
        return await svc._fetch_stock_widget_data(_sym(args))

    async def _analyst(args: Dict[str, Any]) -> Dict[str, Any]:
        return await svc._fetch_analyst_data(_sym(args))

    async def _sentiment(args: Dict[str, Any]) -> Dict[str, Any]:
        sym, on_equity = _resolve(args)
        if on_equity:
            return await svc._fetch_sentiment_data(sym, is_crypto=False)
        return await svc._fetch_sentiment_data(sym)

    async def _market(args: Dict[str, Any]) -> Dict[str, Any]:
        return await svc._fetch_market_overview_data((args.get("symbol") or "^GSPC").upper())

    async def _news(args: Dict[str, Any]) -> Dict[str, Any]:
        sym, on_equity = _resolve(args)
        if on_equity:
            return await svc._fetch_ticker_news_data(sym, is_crypto=False)
        return await svc._fetch_ticker_news_data(sym)

    async def _why(args: Dict[str, Any]) -> Dict[str, Any]:
        sym, on_equity = _resolve(args)
        if on_equity:
            return await svc._fetch_price_move_data(sym, is_crypto=False)
        return await svc._fetch_price_move_data(sym)

    async def _snapshot(args: Dict[str, Any]) -> Dict[str, Any]:
        return await svc._fetch_market_snapshot_data()

    return {
        "get_stock_chart_data": _stock,
        "get_analyst_analysis": _analyst,
        "get_sentiment_analysis": _sentiment,
        "get_market_overview": _market,
        "get_ticker_news": _news,
        "explain_price_move": _why,
        _MARKET_TOOL: _snapshot,
    }


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
