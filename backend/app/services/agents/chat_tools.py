"""Chat function-calling tools for the agentic streaming loop (Phase 2).

Mirrors ``agents/fmp_tools.py``: ``build_chat_tool_declarations()`` returns the ``types.Tool`` list
the model may call mid-stream; ``build_chat_tool_handlers(svc)`` maps each tool name to an async
handler that delegates to the existing ``ChatService`` fetch methods (so the data logic isn't
duplicated). A tool result whose ``widget_type`` is renderable (stock_chart / market_overview)
becomes an inline widget; analyst / sentiment results only inform the model's answer.

Handlers take an svc argument (a ChatService) rather than importing it, to avoid a circular import.
"""

from typing import Any, Awaitable, Callable, Dict, List

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


def build_chat_tool_declarations(include_market_overview: bool = False) -> List[types.Tool]:
    """The tools the agentic chat may call. ``include_market_overview`` adds the index tool
    (only relevant for INDEX/market chats, matching the legacy asset-type gating)."""
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
    if include_market_overview:
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
    return [types.Tool(function_declarations=decls)]


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
