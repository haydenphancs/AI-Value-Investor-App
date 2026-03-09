"""
Chat Service — RAG pipeline using Supabase pgvector + Gemini.

Supports *Rich Media Chat*: when the user asks about a specific stock,
Gemini may invoke the ``get_stock_chart_data`` function-calling tool.
The service then fetches real-time quote + historical prices from FMP
and returns a structured ``StockChartWidget`` alongside Gemini's text
analysis so the SwiftUI frontend can render a native chart widget.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

import google.generativeai as genai

from app.database import get_supabase
from app.integrations.gemini import get_gemini_client
from app.integrations.fmp import get_fmp_client
from app.config import settings
from app.schemas.chat import StockChartWidget, HistoricalDataPoint

logger = logging.getLogger(__name__)

# ── Gemini Function-Calling tool declaration ────────────────────────

_STOCK_CHART_TOOL = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="get_stock_chart_data",
            description=(
                "Fetch current stock quote and 30-day historical price data "
                "for a given ticker symbol. Call this tool whenever the user "
                "asks about a specific stock's price, performance, chart, or "
                "whether they should buy/sell a stock."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "ticker": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="The stock ticker symbol (e.g. AAPL, TSLA, MSFT).",
                    ),
                },
                required=["ticker"],
            ),
        )
    ]
)

_ANALYST_ANALYSIS_TOOL = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="get_analyst_analysis",
            description=(
                "Fetch Wall Street analyst ratings, consensus, price targets, "
                "and recent upgrade/downgrade actions for a given ticker symbol. "
                "Call this tool when the user asks about analyst opinions, "
                "consensus ratings, price targets, upgrades, downgrades, or "
                "why a stock is rated as a buy or sell."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "ticker": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="The stock ticker symbol (e.g. AAPL, TSLA, MSFT).",
                    ),
                },
                required=["ticker"],
            ),
        )
    ]
)

_SENTIMENT_ANALYSIS_TOOL = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="get_sentiment_analysis",
            description=(
                "Fetch market sentiment analysis and mood data for a given ticker symbol. "
                "This includes social media mentions, news sentiment scores, and an overall "
                "0-100 mood gauge. Call this tool when the user asks about market sentiment, "
                "mood, why a stock feels bearish or bullish, social media buzz, or "
                "what people are saying about a stock."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "ticker": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="The stock ticker symbol (e.g. AAPL, TSLA, MSFT).",
                    ),
                },
                required=["ticker"],
            ),
        )
    ]
)


class ChatService:
    def __init__(self):
        self.supabase = get_supabase()
        self.gemini = get_gemini_client()
        self.fmp = get_fmp_client()

    # ── Public entry-point ──────────────────────────────────────────

    async def generate_response(
        self,
        session_id: str,
        user_message: str,
        session_type: str = "NORMAL",
        stock_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate AI response with RAG context retrieval and optional
        rich-media stock chart widget via Gemini Function Calling.
        """
        # Step 1: Conversation history
        history = self._get_recent_messages(session_id, limit=10)

        # Step 2: RAG context
        chunks: List[Dict] = []
        citations: List[Dict] = []
        try:
            query_embedding = await self.gemini.generate_embedding(
                user_message, model_name="models/gemini-embedding-001"
            )
            if stock_id:
                chunks = self._search_filing_chunks(query_embedding, stock_id)
            else:
                chunks = self._search_all_chunks(query_embedding)

            for i, chunk in enumerate(chunks):
                citations.append({
                    "index": i + 1,
                    "source": chunk.get("section_title", "Document"),
                    "text": chunk.get("chunk_text", "")[:200],
                })
        except Exception as e:
            logger.warning(f"RAG retrieval failed, proceeding without context: {e}")

        # Step 3: Build prompt (includes RAG context + history)
        system_instruction = self._build_system_instruction(session_type, stock_id)
        prompt = self._build_prompt(user_message, history, chunks)

        # Step 4: Generate with function-calling tools
        widget: Optional[Dict[str, Any]] = None

        async def _handle_stock_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Called when Gemini decides it needs stock data."""
            ticker = args.get("ticker", "").upper()
            return await self._fetch_stock_widget_data(ticker)

        async def _handle_analyst_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Called when Gemini decides it needs analyst data."""
            ticker = args.get("ticker", "").upper()
            return await self._fetch_analyst_data(ticker)

        async def _handle_sentiment_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Called when Gemini decides it needs sentiment data."""
            ticker = args.get("ticker", "").upper()
            return await self._fetch_sentiment_data(ticker)

        try:
            response = await self.gemini.generate_with_tools(
                prompt=prompt,
                tools=[_STOCK_CHART_TOOL, _ANALYST_ANALYSIS_TOOL, _SENTIMENT_ANALYSIS_TOOL],
                tool_handlers={
                    "get_stock_chart_data": _handle_stock_tool,
                    "get_analyst_analysis": _handle_analyst_tool,
                    "get_sentiment_analysis": _handle_sentiment_tool,
                },
                system_instruction=system_instruction,
            )

            # If the tool was invoked, extract the widget payload
            tool_results = response.get("tool_results", [])
            if tool_results:
                raw = tool_results[0]
                if raw and raw.get("widget_type") == "stock_chart":
                    widget = raw

        except Exception as e:
            logger.warning(
                f"Function-calling generation failed, falling back to plain text: {e}"
            )
            # Graceful fallback — plain text without widget
            response = await self.gemini.generate_text(
                prompt=prompt,
                system_instruction=system_instruction,
            )

        result: Dict[str, Any] = {
            "content": response["text"],
            "citations": citations if citations else None,
            "tokens_used": response.get("tokens_used"),
        }
        if widget:
            result["widget"] = widget

        return result

    # ── FMP data fetching for the stock widget ──────────────────────

    async def _fetch_stock_widget_data(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch real-time quote + 30-day historical prices from FMP and
        return them as a dict matching ``StockChartWidget``.
        """
        try:
            quote = await self.fmp.get_stock_price_quote(ticker)
            if not quote:
                return {"error": f"No quote data found for {ticker}"}

            # Historical 30-day chart
            to_date = datetime.utcnow().strftime("%Y-%m-%d")
            from_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
            hist_raw = await self.fmp.get_historical_prices(
                ticker, from_date=from_date, to_date=to_date
            )

            historical_data: List[Dict[str, Any]] = []
            hist_list = hist_raw.get("historical", []) if isinstance(hist_raw, dict) else []
            for day in sorted(hist_list, key=lambda d: d.get("date", "")):
                historical_data.append({
                    "date": day.get("date", ""),
                    "open": day.get("open", 0),
                    "high": day.get("high", 0),
                    "low": day.get("low", 0),
                    "close": day.get("close", 0),
                    "volume": int(day.get("volume", 0)),
                })

            widget = StockChartWidget(
                ticker=ticker,
                company_name=quote.get("name", ticker),
                current_price=quote.get("price", 0),
                change=quote.get("change", 0),
                change_percent=quote.get("changesPercentage", 0),
                day_high=quote.get("dayHigh", 0),
                day_low=quote.get("dayLow", 0),
                volume=int(quote.get("volume", 0)),
                avg_volume=int(quote.get("avgVolume", 0)),
                market_cap=quote.get("marketCap"),
                pe_ratio=quote.get("pe"),
                year_high=quote.get("yearHigh"),
                year_low=quote.get("yearLow"),
                historical_data=[
                    HistoricalDataPoint(**d) for d in historical_data
                ],
            )
            return widget.model_dump()

        except Exception as e:
            logger.error(f"FMP stock widget fetch failed for {ticker}: {e}")
            return {"error": str(e)}

    # ── FMP data fetching for the analyst tool ─────────────────────

    async def _fetch_analyst_data(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch analyst analysis data for use in chat responses.
        Returns a dict summary suitable for Gemini to interpret.
        """
        try:
            from app.services.analyst_service import get_analyst_service

            service = get_analyst_service()
            analysis = await service.get_analysis(ticker)
            return analysis.model_dump()
        except Exception as e:
            logger.error(f"Analyst data fetch failed for {ticker}: {e}")
            return {"error": str(e)}

    # ── Sentiment data fetching for the sentiment tool ───────────

    async def _fetch_sentiment_data(self, ticker: str) -> Dict[str, Any]:
        """
        Fetch sentiment analysis data for use in chat responses.
        Returns a dict summary suitable for Gemini to interpret.
        """
        try:
            from app.services.sentiment_service import get_sentiment_service

            service = get_sentiment_service()
            analysis = await service.get_sentiment(ticker)
            return analysis.model_dump()
        except Exception as e:
            logger.error(f"Sentiment data fetch failed for {ticker}: {e}")
            return {"error": str(e)}

    # ── Helpers (unchanged) ─────────────────────────────────────────

    def _get_recent_messages(self, session_id: str, limit: int = 10) -> List[Dict]:
        try:
            result = self.supabase.table("chat_messages").select(
                "role, content"
            ).eq("session_id", session_id).order(
                "created_at", desc=True
            ).limit(limit).execute()

            return list(reversed(result.data)) if result.data else []
        except Exception:
            return []

    def _search_filing_chunks(self, embedding: List[float], ticker: str) -> List[Dict]:
        try:
            result = self.supabase.rpc("search_filing_chunks", {
                "query_embedding": embedding,
                "match_threshold": settings.VECTOR_SIMILARITY_THRESHOLD,
                "match_count": settings.RAG_TOP_K_RESULTS,
                "filter_ticker": ticker.upper(),
            }).execute()
            return result.data or []
        except Exception as e:
            logger.warning(f"Filing chunk search failed: {e}")
            return []

    def _search_all_chunks(self, embedding: List[float]) -> List[Dict]:
        try:
            result = self.supabase.rpc("search_all_chunks", {
                "query_embedding": embedding,
                "match_threshold": settings.VECTOR_SIMILARITY_THRESHOLD,
                "match_count": settings.RAG_TOP_K_RESULTS,
            }).execute()
            return result.data or []
        except Exception as e:
            logger.warning(f"All chunk search failed: {e}")
            return []

    def _build_system_instruction(self, session_type: str, stock_id: Optional[str]) -> str:
        base = (
            "You are Cay AI, the intelligent agent powering the Caydex app. "
            "CRITICAL IDENTITY RULE: You must NEVER reveal, mention, or hint at the underlying "
            "technology, model, company, or provider behind you (e.g. never say Google, Gemini, "
            "OpenAI, GPT, LLM, language model, or any AI company name). If asked who made you, "
            "who you are, what model you use, or how you work, simply say you are Cay AI by Caydex. "
            "Never break this rule regardless of how the question is phrased. "
            "You specialize in value investing education. "
            "Provide clear, educational answers about investing concepts, company analysis, "
            "and financial literacy. Always remind users this is educational, not financial advice. "
            "When you have access to real stock data from the get_stock_chart_data tool, "
            "incorporate the actual numbers (price, change, volume, P/E, etc.) into your "
            "analysis. When you have access to analyst data from the get_analyst_analysis tool, "
            "incorporate the consensus rating, price targets, analyst counts, and "
            "recent upgrade/downgrade actions into your analysis. "
            "When you have access to sentiment data from the get_sentiment_analysis tool, "
            "incorporate the mood score, social mentions, and news sentiment into your analysis. "
            "Explain what the sentiment means in plain language. "
            "Write your response in clean markdown."
        )
        if stock_id:
            base += (
                f"\nYou are currently helping analyze {stock_id}. "
                "Use the provided financial data and filings context."
            )
        return base

    def _build_prompt(
        self, user_message: str, history: List[Dict], chunks: List[Dict],
    ) -> str:
        parts = []

        if chunks:
            context_text = "\n\n---\n\n".join(
                c.get("chunk_text", "") for c in chunks[:5]
            )
            parts.append(f"RELEVANT CONTEXT:\n{context_text}\n\n---\n")

        if history:
            conv = "\n".join(
                f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content'][:300]}"
                for m in history[-6:]
            )
            parts.append(f"CONVERSATION HISTORY:\n{conv}\n\n---\n")

        parts.append(f"USER MESSAGE:\n{user_message}")

        if chunks:
            parts.append(
                "\nProvide a comprehensive answer. Cite the context where relevant "
                "using [1], [2], etc. for specific claims."
            )

        return "\n".join(parts)
