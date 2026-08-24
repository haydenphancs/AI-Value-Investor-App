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
_STOCK_TOOLSET = frozenset({
    "get_stock_chart_data", "get_analyst_analysis", "get_sentiment_analysis",
})

_TOOLS_BY_ASSET_TYPE: Dict[str, frozenset] = {
    "STOCK": _STOCK_TOOLSET,
    # No screen context: the user may ask about any stock, so keep the full equity set.
    "NORMAL": _STOCK_TOOLSET,
    # A fund has no analyst coverage, but it does have news sentiment and a real quote.
    "ETF": frozenset({"get_stock_chart_data", "get_sentiment_analysis"}),
    # Sentiment IS meaningful for a coin — `sentiment_service` has a crypto news branch — but
    # only if the caller passes `is_crypto`; see `ChatService._fetch_sentiment_data`.
    "CRYPTO": frozenset({"get_stock_chart_data", "get_sentiment_analysis"}),
    # An index has no analyst ratings and no per-symbol social sentiment; it has the
    # market-overview aggregate, which is the tool built for exactly this case.
    "INDEX": frozenset({"get_market_overview"}),
    # A futures contract has neither analyst coverage nor ticker sentiment.
    "COMMODITY": frozenset({"get_stock_chart_data"}),
}


def tools_for_asset_type(asset_type: Optional[str]) -> frozenset:
    """Tool NAMES the given asset class may call.

    An unknown / missing asset type falls back to the full equity set — the conservative
    direction, since that is exactly what every caller did before this table existed.
    """
    return _TOOLS_BY_ASSET_TYPE.get((asset_type or "").strip().upper(), _STOCK_TOOLSET)


def build_chat_tool_declarations(asset_type: Optional[str] = None) -> List[types.Tool]:
    """The tools the agentic chat may call, filtered to those meaningful for `asset_type`."""
    allowed = tools_for_asset_type(asset_type)
    decls = [
        _ticker_tool(
            "get_stock_chart_data",
            "Fetch the current quote + 30-day price history for a ticker. Call when the user asks "
            "about a specific stock's price, performance, chart, or how it's trading — including a "
            "DIFFERENT ticker than the current screen (e.g. a comparison).",
        ),
        _ticker_tool(
            "get_analyst_analysis",
            "Fetch Wall Street analyst ratings, consensus, price targets, and recent "
            "upgrade/downgrade actions for a ticker. Call when the user asks about analyst opinions, "
            "consensus, or price targets.",
        ),
        _ticker_tool(
            "get_sentiment_analysis",
            "Fetch market sentiment for a ticker: social mentions, news sentiment, and a 0-100 mood "
            "gauge. Call when the user asks about sentiment, mood, buzz, or why a stock feels "
            "bullish/bearish.",
        ),
    ]
    if "get_market_overview" in allowed:
        decls.append(
            types.FunctionDeclaration(
                name="get_market_overview",
                description=(
                    "Fetch overall market valuation (P/E, forward P/E, earnings yield), sector "
                    "performance, and macro indicators. For INDEX / broad-market questions, NOT "
                    "individual stocks."
                ),
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
        )
    decls = [d for d in decls if d.name in allowed]
    # An empty `function_declarations` list is not a valid Tool — return no tools at all.
    return [types.Tool(function_declarations=decls)] if decls else []


def build_chat_tool_handlers(
    svc: Any,
) -> Dict[str, Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]]:
    """Map each tool name → async handler delegating to the ChatService fetch methods."""

    async def _stock(args: Dict[str, Any]) -> Dict[str, Any]:
        return await svc._fetch_stock_widget_data((args.get("ticker") or "").upper())

    async def _analyst(args: Dict[str, Any]) -> Dict[str, Any]:
        return await svc._fetch_analyst_data((args.get("ticker") or "").upper())

    async def _sentiment(args: Dict[str, Any]) -> Dict[str, Any]:
        return await svc._fetch_sentiment_data((args.get("ticker") or "").upper())

    async def _market(args: Dict[str, Any]) -> Dict[str, Any]:
        return await svc._fetch_market_overview_data((args.get("symbol") or "^GSPC").upper())

    return {
        "get_stock_chart_data": _stock,
        "get_analyst_analysis": _analyst,
        "get_sentiment_analysis": _sentiment,
        "get_market_overview": _market,
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
