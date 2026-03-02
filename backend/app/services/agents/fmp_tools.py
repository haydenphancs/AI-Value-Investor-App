"""
FMP Tools for Gemini Function Calling — enables the AI agent to autonomously
request additional financial data during the research phase.

The agent receives base data (profile, quote, income, balance, etc.) upfront.
These tools let it request ADDITIONAL data based on what it discovers — e.g.,
quarterly financials to spot trends, dividend history for yield analysis,
sector data for competitive context.

Uses google.generativeai.protos for Gemini-compatible function declarations.
"""

import logging
from typing import Dict, Any, Callable, Awaitable

import google.generativeai.protos as protos

from app.integrations.fmp import FMPClient

logger = logging.getLogger(__name__)


# ── Gemini Function Declarations ──────────────────────────────────────────────


def build_fmp_tool_declarations() -> protos.Tool:
    """Build Gemini Tool with FMP function declarations for agentic research."""

    return protos.Tool(
        function_declarations=[
            protos.FunctionDeclaration(
                name="fetch_quarterly_financials",
                description=(
                    "Fetch quarterly financial statements to analyze seasonal trends, "
                    "recent quarter performance, or detect acceleration/deceleration "
                    "in revenue and margins."
                ),
                parameters=protos.Schema(
                    type=protos.Type.OBJECT,
                    properties={
                        "ticker": protos.Schema(
                            type=protos.Type.STRING,
                            description="Stock ticker symbol",
                        ),
                        "statement_type": protos.Schema(
                            type=protos.Type.STRING,
                            description="Type of financial statement",
                            enum=["income", "balance_sheet", "cash_flow"],
                        ),
                        "limit": protos.Schema(
                            type=protos.Type.INTEGER,
                            description="Number of quarters (default 8)",
                        ),
                    },
                    required=["ticker", "statement_type"],
                ),
            ),
            protos.FunctionDeclaration(
                name="fetch_dividend_history",
                description=(
                    "Fetch dividend payment history to analyze yield trends, "
                    "payout ratio sustainability, and dividend growth rate."
                ),
                parameters=protos.Schema(
                    type=protos.Type.OBJECT,
                    properties={
                        "ticker": protos.Schema(
                            type=protos.Type.STRING,
                            description="Stock ticker symbol",
                        ),
                        "limit": protos.Schema(
                            type=protos.Type.INTEGER,
                            description="Number of dividend records (default 20)",
                        ),
                    },
                    required=["ticker"],
                ),
            ),
            protos.FunctionDeclaration(
                name="fetch_sector_performance",
                description=(
                    "Fetch current sector performance to contextualize the stock's "
                    "performance relative to its sector and broader market trends."
                ),
                parameters=protos.Schema(
                    type=protos.Type.OBJECT,
                    properties={},
                ),
            ),
            protos.FunctionDeclaration(
                name="fetch_more_news",
                description=(
                    "Fetch additional recent news articles about a company to "
                    "identify catalysts, risks, or sentiment shifts."
                ),
                parameters=protos.Schema(
                    type=protos.Type.OBJECT,
                    properties={
                        "ticker": protos.Schema(
                            type=protos.Type.STRING,
                            description="Stock ticker symbol",
                        ),
                        "limit": protos.Schema(
                            type=protos.Type.INTEGER,
                            description="Number of articles (default 10)",
                        ),
                    },
                    required=["ticker"],
                ),
            ),
            protos.FunctionDeclaration(
                name="fetch_extended_financials",
                description=(
                    "Fetch extended annual financial history (up to 10 years) "
                    "for long-term trend analysis on income, balance sheet, or cash flow."
                ),
                parameters=protos.Schema(
                    type=protos.Type.OBJECT,
                    properties={
                        "ticker": protos.Schema(
                            type=protos.Type.STRING,
                            description="Stock ticker symbol",
                        ),
                        "statement_type": protos.Schema(
                            type=protos.Type.STRING,
                            description="Type of financial statement",
                            enum=["income", "balance_sheet", "cash_flow"],
                        ),
                    },
                    required=["ticker", "statement_type"],
                ),
            ),
            protos.FunctionDeclaration(
                name="research_complete",
                description=(
                    "Signal that you have gathered enough data and are ready to "
                    "produce the final analysis. Call this when you don't need "
                    "any additional financial data."
                ),
                parameters=protos.Schema(
                    type=protos.Type.OBJECT,
                    properties={
                        "summary": protos.Schema(
                            type=protos.Type.STRING,
                            description="Brief summary of key findings from your research",
                        ),
                    },
                    required=["summary"],
                ),
            ),
        ]
    )


# ── Tool Handlers ─────────────────────────────────────────────────────────────


def build_tool_handlers(fmp: FMPClient) -> Dict[str, Callable[..., Awaitable[Dict[str, Any]]]]:
    """Build async handler functions for each FMP tool."""

    async def fetch_quarterly_financials(args: Dict[str, Any]) -> Dict[str, Any]:
        ticker = args.get("ticker", "").upper()
        statement = args.get("statement_type", "income")
        limit = int(args.get("limit", 8))
        limit = min(limit, 12)  # Cap at 12 quarters

        try:
            if statement == "income":
                data = await fmp.get_income_statement(ticker, "quarter", limit)
            elif statement == "balance_sheet":
                data = await fmp.get_balance_sheet(ticker, "quarter", limit)
            elif statement == "cash_flow":
                data = await fmp.get_cash_flow_statement(ticker, "quarter", limit)
            else:
                return {"error": f"Unknown statement type: {statement}"}

            return _compress_financial_data(data, statement)

        except Exception as e:
            logger.warning(f"Tool fetch_quarterly_financials failed: {e}")
            return {"error": str(e), "data": []}

    async def fetch_dividend_history(args: Dict[str, Any]) -> Dict[str, Any]:
        ticker = args.get("ticker", "").upper()
        limit = int(args.get("limit", 20))
        try:
            data = await fmp.get_dividend_history(ticker, limit)
            return {"dividends": data[:limit]}
        except Exception as e:
            logger.warning(f"Tool fetch_dividend_history failed: {e}")
            return {"error": str(e), "dividends": []}

    async def fetch_sector_performance(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            data = await fmp.get_sector_performance()
            return {"sectors": data}
        except Exception as e:
            logger.warning(f"Tool fetch_sector_performance failed: {e}")
            return {"error": str(e), "sectors": []}

    async def fetch_more_news(args: Dict[str, Any]) -> Dict[str, Any]:
        ticker = args.get("ticker", "").upper()
        limit = int(args.get("limit", 10))
        limit = min(limit, 15)
        try:
            data = await fmp.get_stock_news(ticker, limit)
            # Compress to key fields
            articles = []
            for a in data:
                articles.append({
                    "title": a.get("title", ""),
                    "date": a.get("publishedDate", "")[:10],
                    "text": (a.get("text", "") or "")[:300],
                    "sentiment": a.get("sentiment", ""),
                })
            return {"articles": articles}
        except Exception as e:
            logger.warning(f"Tool fetch_more_news failed: {e}")
            return {"error": str(e), "articles": []}

    async def fetch_extended_financials(args: Dict[str, Any]) -> Dict[str, Any]:
        ticker = args.get("ticker", "").upper()
        statement = args.get("statement_type", "income")
        try:
            if statement == "income":
                data = await fmp.get_income_statement(ticker, "annual", 10)
            elif statement == "balance_sheet":
                data = await fmp.get_balance_sheet(ticker, "annual", 10)
            elif statement == "cash_flow":
                data = await fmp.get_cash_flow_statement(ticker, "annual", 10)
            else:
                return {"error": f"Unknown statement type: {statement}"}
            return _compress_financial_data(data, statement)
        except Exception as e:
            logger.warning(f"Tool fetch_extended_financials failed: {e}")
            return {"error": str(e), "data": []}

    async def research_complete(args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "complete", "summary": args.get("summary", "")}

    return {
        "fetch_quarterly_financials": fetch_quarterly_financials,
        "fetch_dividend_history": fetch_dividend_history,
        "fetch_sector_performance": fetch_sector_performance,
        "fetch_more_news": fetch_more_news,
        "fetch_extended_financials": fetch_extended_financials,
        "research_complete": research_complete,
    }


# ── Data Compression ─────────────────────────────────────────────────────────


def _compress_financial_data(
    data: list, statement_type: str
) -> Dict[str, Any]:
    """Compress financial data to fit within Gemini context limits."""
    if not data:
        return {"data": []}

    # Select key fields based on statement type
    key_fields = {
        "income": [
            "date", "calendarYear", "period", "revenue", "grossProfit",
            "operatingIncome", "netIncome", "epsdiluted", "operatingExpenses",
        ],
        "balance_sheet": [
            "date", "calendarYear", "period", "totalAssets", "totalLiabilities",
            "totalStockholdersEquity", "cashAndCashEquivalents", "totalDebt",
            "netDebt", "totalCurrentAssets", "totalCurrentLiabilities",
            "retainedEarnings",
        ],
        "cash_flow": [
            "date", "calendarYear", "period", "operatingCashFlow",
            "capitalExpenditure", "freeCashFlow", "dividendsPaid",
            "commonStockRepurchased",
        ],
    }

    fields = key_fields.get(statement_type, key_fields["income"])
    compressed = []
    for item in data:
        row = {k: item.get(k) for k in fields if k in item}
        compressed.append(row)

    return {"data": compressed}
