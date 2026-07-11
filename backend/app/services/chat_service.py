"""
Chat Service — RAG pipeline using Supabase pgvector + Gemini.

Supports *Rich Media Chat*: when the user asks about a specific stock,
Gemini may invoke the ``get_stock_chart_data`` function-calling tool.
The service then fetches real-time quote + historical prices from FMP
and returns a structured ``StockChartWidget`` alongside Gemini's text
analysis so the SwiftUI frontend can render a native chart widget.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List, Tuple

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


_MARKET_OVERVIEW_TOOL = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="get_market_overview",
            description=(
                "Fetch current market valuation (P/E ratio, forward P/E, earnings yield), "
                "sector performance (all 11 sectors with daily change), and macroeconomic "
                "indicators. Call this tool when the user asks about the overall market, "
                "market deep dive, sector rotation, market valuation, or macro outlook. "
                "This is for INDEX analysis only, not individual stocks."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "symbol": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="The index symbol (e.g. ^GSPC, ^DJI, ^IXIC).",
                    ),
                },
                required=["symbol"],
            ),
        )
    ]
)


class ReasoningStreamSplitter:
    """Split a streamed chat generation into a reasoning preamble and the answer.

    The streaming system instruction asks the model to write a short reasoning preamble, then a
    line ``===ANSWER===``, then the answer. Feed each stream delta to ``feed()``; it returns
    ``(reasoning_chunks, answer_chunk)`` to emit as SSE frames. Robust: if the separator never
    appears within ``cap`` chars (the model ignored the format), everything is treated as the
    answer so the answer is never lost.
    """

    MARKER = "===ANSWER==="

    def __init__(self, cap: int = 800):
        self._cap = cap
        self._buf = ""
        self._in_answer = False
        self.reasoning = ""
        self.answer = ""

    def feed(self, delta: str) -> Tuple[List[str], str]:
        if self._in_answer:
            self.answer += delta
            return [], delta
        self._buf += delta
        idx = self._buf.find(self.MARKER)
        if idx != -1:
            reasoning_part = self._buf[:idx].strip()
            after = self._buf[idx + len(self.MARKER):].lstrip("\n")
            self._buf = ""
            self._in_answer = True
            self.reasoning = reasoning_part
            self.answer += after
            return ([reasoning_part] if reasoning_part else []), after
        if len(self._buf) > self._cap:
            # No COMPLETE separator within the cap (the model isn't using the format, or the reasoning
            # is unusually long). Emit the buffer as answer content, but hold back a marker-length tail
            # so a '===ANSWER===' that straddles THIS flush boundary can still complete on the next
            # delta and be stripped — never leaking the literal marker into the answer. Stay in detect
            # mode so a late separator is still honored (the checked-above find() guarantees no complete
            # marker is inside the flushed prefix).
            keep = len(self.MARKER)
            emitted = self._buf[:-keep]
            self._buf = self._buf[-keep:]
            self.answer += emitted
            return [], emitted
        return [], ""

    def finish(self) -> Tuple[List[str], str]:
        """Flush at stream end: if still detecting, the buffer IS the answer (never lose it)."""
        if not self._in_answer and self._buf:
            emitted = self._buf
            self._buf = ""
            self.answer += emitted
            return [], emitted
        return [], ""


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
        context: Optional[str] = None,
        context_type: Optional[str] = None,
        reference_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate AI response with RAG context retrieval and optional
        rich-media stock chart widget via Gemini Function Calling.

        When ``context_type`` + ``reference_id`` are supplied, the screen's
        already-cached data (report / ETF / crypto / article / ...) is fetched
        server-side and used as the grounding block — so iOS no longer ships a
        big raw context string. Falls back to any client-sent ``context`` (BOOK,
        legacy) or none on a miss.
        """
        # Screen-aware grounding (never raises; degrades to client context/None).
        from app.services.chat_context_resolver import get_chat_context_resolver
        context = await get_chat_context_resolver().resolve(
            context_type, reference_id, client_context=context,
        )

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
        # Detect asset type from stock_id
        asset_type = self._detect_asset_type(stock_id) if stock_id else "NORMAL"

        # Enrich with live data — only for stocks (other types use client_context)
        profit_summary = None
        snapshot_summary = None
        company_profile_summary = None
        is_stock = asset_type == "STOCK"
        if stock_id and is_stock:
            profit_summary, snapshot_summary, company_profile_summary = await asyncio.gather(
                self._get_profit_summary(stock_id),
                self._get_snapshot_summary(stock_id),
                self._get_company_profile_summary(stock_id),
            )

        # Check Market Deep Dive cache for index/ETF/crypto/commodity
        cached_report = None
        is_deep_dive = self._is_deep_dive_request(is_stock, stock_id, user_message)
        if is_deep_dive and context:
            cached_report = self._check_deep_dive_cache(stock_id, context)

        system_instruction = self._build_system_instruction(
            session_type, stock_id, profit_summary=profit_summary,
            snapshot_summary=snapshot_summary,
            company_profile_summary=company_profile_summary,
            client_context=context,
            asset_type=asset_type,
        )
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

        async def _handle_market_overview_tool(args: Dict[str, Any]) -> Dict[str, Any]:
            """Called when Gemini decides it needs market overview data."""
            symbol = args.get("symbol", "^GSPC").upper()
            return await self._fetch_market_overview_data(symbol)

        # Return cached deep dive if available (zero Gemini cost)
        if cached_report:
            logger.info(f"Deep dive cache HIT for {stock_id}")
            return {
                "content": cached_report,
                "citations": citations if citations else None,
                "tokens_used": 0,
            }

        # Select tools based on asset type
        tools = [_STOCK_CHART_TOOL, _ANALYST_ANALYSIS_TOOL, _SENTIMENT_ANALYSIS_TOOL]
        handlers = {
            "get_stock_chart_data": _handle_stock_tool,
            "get_analyst_analysis": _handle_analyst_tool,
            "get_sentiment_analysis": _handle_sentiment_tool,
        }
        if asset_type == "INDEX":
            tools.append(_MARKET_OVERVIEW_TOOL)
            handlers["get_market_overview"] = _handle_market_overview_tool

        try:
            response = await self.gemini.generate_with_tools(
                prompt=prompt,
                tools=tools,
                tool_handlers=handlers,
                system_instruction=system_instruction,
            )

            # If the tool was invoked, extract the widget payload
            tool_results = response.get("tool_results", [])
            if tool_results:
                raw = tool_results[0]
                if raw and raw.get("widget_type") in ("stock_chart", "market_overview"):
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

        ai_text = response["text"]

        # Cache deep dive reports for 24 hours
        if is_deep_dive and context and stock_id and len(ai_text) > 100:
            self._upsert_deep_dive_cache(stock_id, context, ai_text)

        result: Dict[str, Any] = {
            "content": ai_text,
            "citations": citations if citations else None,
            "tokens_used": response.get("tokens_used"),
        }
        if widget:
            result["widget"] = widget

        return result

    # ── Streaming prep (SSE path) ───────────────────────────────────
    async def prepare_stream_generation(
        self,
        session_id: str,
        user_message: str,
        session_type: str = "NORMAL",
        stock_id: Optional[str] = None,
        context: Optional[str] = None,
        context_type: Optional[str] = None,
        reference_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build everything a STREAMED response needs, WITHOUT calling Gemini.

        Function-calling can't stream, so instead of letting Gemini pick a tool
        we (a) resolve the screen's grounding block, (b) build the same system
        instruction + prompt as ``generate_response``, and (c) fetch any inline
        widget deterministically by id. The endpoint then streams the prose via
        ``gemini.stream_text`` and attaches this widget/citations in the terminal
        ``done`` event.

        Returns ``{prompt, system_instruction, citations, widget}``.
        """
        # Screen-aware grounding (never raises).
        from app.services.chat_context_resolver import get_chat_context_resolver
        context = await get_chat_context_resolver().resolve(
            context_type, reference_id, client_context=context,
        )

        history = self._get_recent_messages(session_id, limit=10)

        # RAG context (same as generate_response, best-effort).
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
            logger.warning(f"RAG retrieval failed (stream), proceeding without context: {e}")

        asset_type = self._detect_asset_type(stock_id) if stock_id else "NORMAL"

        # Stock enrichment (only for STOCK — other types are grounded by the resolver).
        profit_summary = snapshot_summary = company_profile_summary = None
        if stock_id and asset_type == "STOCK":
            profit_summary, snapshot_summary, company_profile_summary = await asyncio.gather(
                self._get_profit_summary(stock_id),
                self._get_snapshot_summary(stock_id),
                self._get_company_profile_summary(stock_id),
            )

        system_instruction = self._build_system_instruction(
            session_type, stock_id, profit_summary=profit_summary,
            snapshot_summary=snapshot_summary,
            company_profile_summary=company_profile_summary,
            client_context=context, asset_type=asset_type,
            reasoning=True,   # streamed reasoning preamble (split from the answer in the endpoint)
        )
        prompt = self._build_prompt(user_message, history, chunks, reasoning=True)
        widget = await self._deterministic_widget(asset_type, stock_id, reference_id)
        sources = self._build_sources(context_type, reference_id, citations)

        return {
            "prompt": prompt,
            "system_instruction": system_instruction,
            "citations": citations if citations else None,
            "widget": widget,
            "sources": sources if sources else None,
        }

    # Screen context_type → the human "source" label shown in the thinking card.
    # Mirrors the ChatContextResolver branches; identity-safe (server-authored strings).
    _CONTEXT_SOURCE_LABEL = {
        "TICKER_REPORT": "Cay research report",
        "STOCK": "Company financials",
        "ETF": "ETF profile",
        "CRYPTO": "Crypto profile",
        "INDEX": "Index data",
        "COMMODITY": "Commodity data",
        "MONEY_MOVES_ARTICLE": "Money Moves article",
        "JOURNEY_LESSON": "Investor Journey lesson",
        "BOOK": "Book",
    }
    # context_types whose reference_id is a user-readable ticker (vs. a slug/order id).
    _TICKER_CONTEXTS = {"TICKER_REPORT", "STOCK", "ETF", "CRYPTO", "INDEX", "COMMODITY"}

    @classmethod
    def _build_sources(
        cls,
        context_type: Optional[str],
        reference_id: Optional[str],
        citations: Optional[List[Dict]],
    ) -> List[Dict[str, Any]]:
        """Build the small "sources" list for the thinking card from the grounding we
        already resolved: one pill for the screen context + one per distinct SEC-filing
        section surfaced by RAG. No web/URL sources — this is our cached grounding only.
        Never raises; returns [] when there's nothing to show."""
        sources: List[Dict[str, Any]] = []
        ctype = (context_type or "").strip().upper()
        label = cls._CONTEXT_SOURCE_LABEL.get(ctype)
        if label:
            detail = None
            ref = (reference_id or "").strip()
            if ref and ctype in cls._TICKER_CONTEXTS:
                detail = ref.split("|")[0].strip().upper() or None
            sources.append({"label": label, "detail": detail})

        # RAG citations → distinct SEC-filing sections (skip the generic "Document" fallback).
        if citations:
            seen: set = set()
            for c in citations:
                if not isinstance(c, dict):
                    continue
                section = (c.get("source") or "").strip()
                key = section.lower()
                if not section or key in seen or key == "document":
                    continue
                seen.add(key)
                sources.append({"label": "SEC filing", "detail": section})
                if len(sources) >= 6:  # keep the card compact
                    break

        return sources

    async def generate_followup_suggestions(
        self,
        user_message: str,
        answer: str,
        context_type: Optional[str] = None,
        reference_id: Optional[str] = None,
    ) -> List[str]:
        """Best-effort: 2 short follow-up questions the user might ask next, phrased as the
        USER would. Identity-guarded — reuses the Cay AI system instruction so the model can
        never leak "Gemini/LLM/language model" into a suggestion. Never raises: on any failure
        (quota, timeout, bad JSON) returns [] so the answer + card are unaffected."""
        try:
            system = self._build_system_instruction("NORMAL", None)
            prompt = (
                "Given this question-and-answer, propose EXACTLY 2 short follow-up questions "
                "the user is likely to ask next. Rules: each under 60 characters; specific to "
                "the topic just discussed; phrased in first person as the user would type it; "
                "no numbering, no quotes.\n\n"
                f"USER ASKED:\n{user_message}\n\n"
                f"CAY AI ANSWERED:\n{answer[:1500]}\n\n"
                'Return ONLY JSON of the form {"suggestions": ["...", "..."]}.'
            )
            result = await self.gemini.generate_json(prompt, system_instruction=system)
            data = json.loads(result.get("text") or "{}")
            raw = data.get("suggestions") or []
            out = [s.strip() for s in raw if isinstance(s, str) and s.strip()]
            return out[:2]
        except Exception as e:
            logger.warning(
                "Follow-up suggestions failed (%s: %s) — skipping", type(e).__name__, e
            )
            return []

    async def _deterministic_widget(
        self, asset_type: str, stock_id: Optional[str], reference_id: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Fetch the inline widget up-front by symbol (no Gemini tool round-trip),
        so the streamed path keeps the rich stock-chart / market-overview widget.
        Never raises — a failure just means no widget."""
        try:
            symbol = (stock_id or reference_id or "").split("|")[0].strip().upper()
            if not symbol:
                return None
            if asset_type == "STOCK":
                raw = await self._fetch_stock_widget_data(symbol)
                if raw and raw.get("widget_type") == "stock_chart":
                    return raw
            elif asset_type == "INDEX":
                raw = await self._fetch_market_overview_data(symbol)
                if raw and raw.get("widget_type") == "market_overview":
                    return raw
        except Exception as e:
            logger.warning(
                f"Deterministic widget fetch failed ({asset_type}/{stock_id}/{reference_id}): {e}"
            )
        return None

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

            historical_data = self._normalize_historical(hist_raw)

            # FMP's /stable/quote returns avgVolume=0 (documented elsewhere in the codebase). Fall
            # back to the company profile's averageVolume (what every other service uses), then to
            # the mean of the daily volumes we already fetched — so the card never shows "0".
            avg_volume = int(quote.get("avgVolume") or 0)
            if avg_volume <= 0:
                try:
                    profile = await self.fmp.get_company_profile(ticker)
                    if profile:
                        avg_volume = int(profile.get("averageVolume") or profile.get("volAvg") or 0)
                except Exception as e:
                    logger.warning("avg_volume profile fallback failed for %s: %s", ticker, e)
            if avg_volume <= 0 and historical_data:
                vols = [d["volume"] for d in historical_data if d.get("volume")]
                if vols:
                    avg_volume = int(sum(vols) / len(vols))

            # Is the US session open right now? Drives the card's "Live"/"Closed" dot (clock-based,
            # DST-aware — reuses the home-dashboard helper).
            from app.services.home_dashboard_service import _market_status
            is_market_open = _market_status()[1]

            return self._build_stock_widget(
                ticker, quote, historical_data, avg_volume, is_market_open
            )

        except Exception as e:
            logger.error(f"FMP stock widget fetch failed for {ticker}: {e}")
            return {"error": str(e)}

    # FMP fields arrive as present-but-null for halted / thinly-traded / pre-market / newly-listed
    # tickers. `dict.get(k, 0)` only substitutes on an ABSENT key, so int(None) — or a None fed into
    # a non-Optional float field — would abort the WHOLE widget (caught above → no chart at all).
    # These two pure helpers coerce with the `or 0` idiom every sibling FMP service already uses,
    # and are unit-tested directly (no network) for the null/malformed-row outliers.
    @staticmethod
    def _normalize_historical(hist_raw: Any) -> List[Dict[str, Any]]:
        """FMP EOD history → sorted, null-safe OHLCV rows. Handles the /stable bare-LIST shape, the
        legacy ``{"historical": [...]}`` dict shape, None, and non-dict / null-field rows (a single
        bad day is coerced/skipped, never aborting the chart)."""
        if isinstance(hist_raw, list):
            hist_list = hist_raw
        elif isinstance(hist_raw, dict):
            hist_list = hist_raw.get("historical", [])
        else:
            hist_list = []
        rows: List[Dict[str, Any]] = []
        for day in sorted(
            (d for d in hist_list if isinstance(d, dict)),
            key=lambda d: d.get("date") or "",
        ):
            rows.append({
                "date": day.get("date") or "",
                "open": day.get("open") or 0,
                "high": day.get("high") or 0,
                "low": day.get("low") or 0,
                "close": day.get("close") or 0,
                "volume": int(day.get("volume") or 0),
            })
        return rows

    @staticmethod
    def _build_stock_widget(
        ticker: str,
        quote: Dict[str, Any],
        historical_data: List[Dict[str, Any]],
        avg_volume: int,
        is_market_open: Optional[bool],
    ) -> Dict[str, Any]:
        """Build the StockChartWidget payload from a raw FMP quote + normalized history. Null-coerces
        the REQUIRED numeric fields (`or 0`) so a null price/change/volume degrades to 0 instead of
        raising a Pydantic ValidationError that drops the entire card; the genuinely-optional fields
        (market_cap / pe / year hi-lo) stay None when absent."""
        widget = StockChartWidget(
            ticker=ticker,
            company_name=quote.get("name") or ticker,
            current_price=quote.get("price") or 0,
            change=quote.get("change") or 0,
            change_percent=quote.get("changesPercentage") or 0,
            day_high=quote.get("dayHigh") or 0,
            day_low=quote.get("dayLow") or 0,
            volume=int(quote.get("volume") or 0),
            avg_volume=avg_volume,
            market_cap=quote.get("marketCap"),
            pe_ratio=quote.get("pe"),
            year_high=quote.get("yearHigh"),
            year_low=quote.get("yearLow"),
            is_market_open=is_market_open,
            historical_data=[HistoricalDataPoint(**d) for d in historical_data],
        )
        return widget.model_dump()

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

    async def _fetch_market_overview_data(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch market valuation, sector performance, and macro indicators
        for the market overview widget. Uses cached index detail data.
        """
        try:
            from app.services.index_service import get_index_service
            from app.schemas.chat import MarketOverviewWidget, MarketOverviewSector, MarketOverviewMacroItem

            service = get_index_service()
            # Fetch the full index detail (will use Supabase cache if available)
            detail = await service.get_index_detail(symbol)

            val = detail.snapshots_data.valuation
            sp = detail.snapshots_data.sector_performance
            macro = detail.snapshots_data.macro_forecast

            sectors = [
                MarketOverviewSector(sector=s.sector, change_percent=s.change_percent)
                for s in sp.sectors
            ]
            advancing = sum(1 for s in sp.sectors if s.change_percent >= 0)
            macro_items = [
                MarketOverviewMacroItem(title=m.title, signal=m.signal)
                for m in macro.indicators
            ]

            widget = MarketOverviewWidget(
                pe_ratio=val.pe_ratio,
                forward_pe=val.forward_pe,
                valuation_level=self._get_valuation_level(val.pe_ratio),
                earnings_yield=val.earnings_yield,
                historical_avg_pe=val.historical_avg_pe,
                sectors=sectors,
                advancing=advancing,
                declining=len(sectors) - advancing,
                macro_indicators=macro_items,
            )
            return widget.model_dump()
        except Exception as e:
            logger.error(f"Market overview fetch failed for {symbol}: {e}")
            return {"error": str(e)}

    @staticmethod
    def _get_valuation_level(pe: float) -> str:
        if pe < 18:
            return "Bargain"
        elif pe < 24:
            return "Fair Value"
        elif pe < 30:
            return "Expensive"
        else:
            return "Overheated"

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

    async def _get_profit_summary(self, ticker: str) -> Optional[str]:
        """Fetch cached profit power data and format a compact summary string."""
        try:
            from app.services.profit_power_service import get_profit_power_service
            service = get_profit_power_service()
            data = await service.get_profit_power(ticker)
            if not data.annual:
                return None
            latest = data.annual[-1]
            parts = [f"Latest annual margins for {ticker} ({latest.period}):"]
            if latest.gross_margin is not None:
                parts.append(f"Gross {latest.gross_margin:.1f}%")
            if latest.operating_margin is not None:
                parts.append(f"Operating {latest.operating_margin:.1f}%")
            if latest.net_margin is not None:
                parts.append(f"Net {latest.net_margin:.1f}%")
            if latest.fcf_margin is not None:
                parts.append(f"FCF {latest.fcf_margin:.1f}%")
            if latest.sector_average_net_margin is not None:
                parts.append(f"Sector avg net margin {latest.sector_average_net_margin:.1f}%")
            return ", ".join(parts[:1]) + " " + ", ".join(parts[1:]) + "."
        except Exception as e:
            logger.warning(f"Profit summary fetch failed for {ticker}: {e}")
            return None

    async def _get_snapshot_summary(self, ticker: str) -> Optional[str]:
        """Fetch all 5 cached snapshots and format compact summary strings."""
        try:
            from app.services.profitability_snapshot_service import get_profitability_snapshot_service
            from app.services.growth_snapshot_service import get_growth_snapshot_service
            from app.services.valuation_snapshot_service import get_valuation_snapshot_service
            from app.services.health_snapshot_service import get_health_snapshot_service
            from app.services.ownership_snapshot_service import get_ownership_snapshot_service

            results = await asyncio.gather(
                get_profitability_snapshot_service().get_profitability_snapshot(ticker),
                get_growth_snapshot_service().get_growth_snapshot(ticker),
                get_valuation_snapshot_service().get_valuation_snapshot(ticker),
                get_health_snapshot_service().get_health_snapshot(ticker),
                get_ownership_snapshot_service().get_ownership_snapshot(ticker),
                return_exceptions=True,
            )

            rating_labels = {5: "High", 4: "Solid", 3: "Moderate", 2: "Soft", 1: "Low"}
            parts = []

            for snap in results:
                if isinstance(snap, Exception):
                    continue
                metrics_str = ", ".join(f"{m.name}: {m.value}" for m in snap.metrics)
                label = rating_labels.get(snap.rating, "Unknown")
                parts.append(f"{snap.category}: {label} ({snap.rating}/5). {metrics_str}.")

            return f"Snapshots for {ticker}: " + " ".join(parts) if parts else None
        except Exception as e:
            logger.warning(f"Snapshot summary fetch failed for {ticker}: {e}")
            return None

    async def _get_company_profile_summary(self, ticker: str) -> Optional[str]:
        """Fetch cached company profile and format as context string for AI."""
        try:
            from app.services.stock_overview_service import get_stock_overview_service
            service = get_stock_overview_service()
            profile = service.get_cached_company_profile(ticker)

            # Fallback: lightweight FMP fetch if cache is empty
            if not profile:
                raw = await self.fmp.get_company_profile(ticker)
                if raw:
                    profile = {
                        "description": raw.get("description", ""),
                        "ceo": raw.get("ceo", "N/A"),
                        "sector": raw.get("sector", "N/A"),
                        "industry": raw.get("industry", "N/A"),
                        "employees": raw.get("fullTimeEmployees") or raw.get("employees", 0),
                        "headquarters": f"{raw.get('city', '')}, {raw.get('state', '')}".strip(", "),
                        "founded": raw.get("ipoDate", "N/A"),
                    }
            if not profile:
                return None

            parts = [f"Company Profile for {ticker}:"]
            desc = profile.get("description", "")
            if desc:
                if len(desc) > 500:
                    desc = desc[:500] + "..."
                parts.append(f"Description: {desc}")
            if profile.get("ceo"):
                parts.append(f"CEO: {profile['ceo']}")
            if profile.get("sector"):
                parts.append(f"Sector: {profile['sector']}")
            if profile.get("industry"):
                parts.append(f"Industry: {profile['industry']}")
            if profile.get("employees"):
                emp = profile["employees"]
                parts.append(f"Employees: {emp:,}" if isinstance(emp, int) else f"Employees: {emp}")
            if profile.get("headquarters"):
                parts.append(f"HQ: {profile['headquarters']}")
            if profile.get("founded"):
                parts.append(f"IPO Date: {profile['founded']}")
            perf = profile.get("sector_performance")
            if perf and perf != 0.0:
                parts.append(f"Sector Performance: {perf}%")
            rank = profile.get("industry_rank")
            if rank and rank != "--":
                parts.append(f"Industry Rank: {rank}")
            return " | ".join(parts)
        except Exception as e:
            logger.warning(f"Company profile summary failed for {ticker}: {e}")
            return None

    # ── Asset type detection ─────────────────────────────────────────

    @staticmethod
    def _detect_asset_type(stock_id: str) -> str:
        """Detect asset type from the symbol format."""
        if not stock_id:
            return "NORMAL"
        sid = stock_id.upper()
        if sid.startswith("^"):
            return "INDEX"
        # Common crypto suffixes
        if sid.endswith("USD") or sid.endswith("USDT") or sid in {
            "BTC", "ETH", "SOL", "ADA", "DOT", "AVAX", "MATIC", "LINK",
            "XRP", "DOGE", "SHIB", "UNI", "AAVE", "LTC", "BCH", "ATOM",
        }:
            return "CRYPTO"
        # Common commodity symbols
        if sid in {
            "GCUSD", "SIUSD", "CLUSD", "NGUSD", "PLUSD", "HGUSD",
            "ZSUSD", "ZCUSD", "ZUSD", "LBUSD", "OJUSD", "KCUSD",
            "SBUSD", "CTUSD", "CCUSD",
            "GOLD", "SILVER", "OIL", "NATGAS", "PLATINUM", "COPPER",
        }:
            return "COMMODITY"
        return "STOCK"

    # ── Deep dive cache ───────────────────────────────────────────

    _DEEP_DIVE_TTL_HOURS = 24

    @staticmethod
    def _is_deep_dive_request(is_stock: bool, stock_id: Optional[str], user_message: str) -> bool:
        """Whether to route this message through the Market Deep Dive cache. That cache is for the
        canned NON-stock request (index / ETF / crypto / commodity) and is keyed by (symbol, context)
        — NOT by the message. The parentheses are load-bearing: without them Python's `and`/`or`
        precedence lets 'deep analysis' / 'market deep dive' fire for ANY chat, which on a stock chat
        serves a stale, message-agnostic cached report answering a different question."""
        if is_stock or not stock_id:
            return False
        msg = user_message.lower()
        return any(kw in msg for kw in ("deep dive", "deep analysis", "market deep dive"))

    def _check_deep_dive_cache(self, symbol: str, context: str) -> Optional[str]:
        """Check Supabase market_deep_dive_cache (24h TTL)."""
        ctx_hash = hashlib.md5(context.encode()).hexdigest()[:16]
        try:
            row = (
                self.supabase.table("market_deep_dive_cache")
                .select("report_markdown, cached_at")
                .eq("symbol", symbol.upper())
                .eq("context_hash", ctx_hash)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None
            entry = row.data[0]
            cached_at = datetime.fromisoformat(
                entry["cached_at"].replace("Z", "+00:00")
            )
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=self._DEEP_DIVE_TTL_HOURS):
                return None
            logger.info(f"Deep dive cache HIT for {symbol} (age={age})")
            return entry["report_markdown"]
        except Exception as e:
            logger.warning(f"Deep dive cache check failed: {e}")
            return None

    def _upsert_deep_dive_cache(self, symbol: str, context: str, report: str) -> None:
        """Cache deep dive report in Supabase (24h TTL)."""
        ctx_hash = hashlib.md5(context.encode()).hexdigest()[:16]
        try:
            self.supabase.table("market_deep_dive_cache").upsert(
                {
                    "symbol": symbol.upper(),
                    "context_hash": ctx_hash,
                    "report_markdown": report,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="symbol,context_hash",
            ).execute()
            logger.info(f"Deep dive cached for {symbol} (24h TTL)")
        except Exception as e:
            logger.warning(f"Deep dive cache upsert failed: {e}")

    # ── System instruction builder ────────────────────────────────

    # Asset-specific persona extensions
    # Persona = a short analyst VOICE only. No mandatory ##-section scaffolds — chat answers stay
    # concise (see the brevity directive in _build_system_instruction); the user asks for detail.
    _ASSET_PERSONAS = {
        "INDEX": (
            "\nAnswer as a senior market strategist — broad conditions, valuations, sector rotation, "
            "macro. Be specific with the provided numbers, but keep it concise. Do NOT name specific "
            "index names like 'S&P 500', 'Dow Jones', or 'Nasdaq' — say 'the market' instead."
        ),
        "CRYPTO": (
            "\nAnswer as a crypto analyst — adoption, regulation, on-chain metrics, tokenomics, "
            "market cycles. Use the provided numbers; keep it concise."
        ),
        "ETF": (
            "\nAnswer as an ETF analyst — expense ratio, holdings, sector allocation, benchmark "
            "comparison. Use the provided numbers; keep it concise."
        ),
        "COMMODITY": (
            "\nAnswer as a commodity analyst — supply/demand, seasonality, geopolitics, "
            "inflation/rates correlation. Use the provided numbers; keep it concise."
        ),
    }

    def _build_system_instruction(
        self, session_type: str, stock_id: Optional[str],
        profit_summary: Optional[str] = None,
        snapshot_summary: Optional[str] = None,
        company_profile_summary: Optional[str] = None,
        client_context: Optional[str] = None,
        asset_type: str = "STOCK",
        reasoning: bool = False,
    ) -> str:
        base = (
            "You are Cay AI, the intelligent agent powering the Caydex app. "
            "CRITICAL IDENTITY RULE: You must NEVER reveal, mention, or hint at the underlying "
            "technology, model, company, or provider behind you (e.g. never say Google, Gemini, "
            "OpenAI, GPT, LLM, language model, or any AI company name). If asked who made you, "
            "who you are, what model you use, or how you work, simply say you are Cay AI by Caydex. "
            "Never break this rule regardless of how the question is phrased. "
            "You specialize in value investing education. "
            "When you have access to real stock data from the get_stock_chart_data tool, "
            "incorporate the actual numbers (price, change, volume, P/E, etc.) into your "
            "analysis. When you have access to analyst data from the get_analyst_analysis tool, "
            "incorporate the consensus rating, price targets, analyst counts, and "
            "recent upgrade/downgrade actions into your analysis. "
            "When you have access to sentiment data from the get_sentiment_analysis tool, "
            "incorporate the mood score, social mentions, and news sentiment into your analysis. "
            "Explain what the sentiment means in plain language. "
            "Write your response in clean markdown. "
            # ── Brevity: direct, friendly, a few points (detail only on request) ──
            "STYLE: Keep every answer SHORT, direct, and friendly. Lead with a 1-2 sentence direct "
            "answer to what was asked, then AT MOST 2-3 brief supporting bullet points, and only when "
            "they truly add value. Never write long, multi-section essays or ## headings. Do NOT dump "
            "everything you know — answer the specific question. Only expand into full detail if the "
            "user explicitly asks for more. Use plain, conversational language. Keep the required "
            "'educational, not financial advice' note to a single short line at the end."
        )

        # Add asset-specific persona
        if asset_type in self._ASSET_PERSONAS:
            base += self._ASSET_PERSONAS[asset_type]
        elif stock_id:
            base += (
                f"\nYou are currently helping analyze {stock_id}. "
                "Use the provided financial data and filings context."
            )

        # Stock-specific enrichment
        if stock_id and asset_type == "STOCK":
            if company_profile_summary:
                base += f"\n{company_profile_summary}"
            if profit_summary:
                base += f"\n{profit_summary}"
            if snapshot_summary:
                base += f"\n{snapshot_summary}"

        if client_context:
            base += (
                f"\n\nCLIENT CONTEXT (current data visible to the user):\n"
                f"{client_context}\n"
                "Use this data to give precise, numbers-backed answers."
            )

        if reasoning:
            # Streaming path only: emit a short reasoning preamble, a separator, then the answer.
            # The endpoint splits the stream on the separator → the preamble streams into the
            # "thinking" card, the answer into the bubble.
            base += (
                "\n\nOUTPUT FORMAT — reason first, then answer:\n"
                "1) First write 1-3 SHORT sentences of your reasoning about THIS specific question "
                "(what data/considerations matter, how you'll approach it). Reason AS Cay AI — never "
                "mention being an AI, a model, or any provider.\n"
                "2) Then output a line containing EXACTLY ===ANSWER=== and nothing else.\n"
                "3) Then write your concise answer, following the STYLE rules above.\n"
                "Never use the text ===ANSWER=== anywhere except as that single separator line."
            )
        return base

    def _build_prompt(
        self, user_message: str, history: List[Dict], chunks: List[Dict],
        reasoning: bool = False,
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
                "\nAnswer directly and concisely. Cite the context with [1], [2], etc. only "
                "where it backs a specific claim."
            )

        if reasoning:
            # Format goes in the PROMPT (attended more reliably than the system instruction). The
            # worked example markedly improves compliance for gemini-flash, which otherwise skips
            # straight to the answer.
            parts.append(
                "\n\nHOW TO FORMAT YOUR REPLY — MANDATORY, do this for EVERY reply:\n"
                "1) Start with 1-2 short sentences of your reasoning — how you're thinking about this "
                "question. 2) Then a line that is EXACTLY: ===ANSWER=== 3) Then your concise answer.\n"
                "You MUST include the ===ANSWER=== line and MUST NOT skip the reasoning.\n\n"
                "Example reply:\n"
                "ROE is net income over shareholder equity, so a negative equity base can make a "
                "loss-making company show a positive ROE. Let me check SNDK's equity.\n"
                "===ANSWER===\n"
                "SNDK's ROE looks high because its shareholder equity is negative...\n\n"
                "Now answer the user's actual question in that format."
            )

        return "\n".join(parts)
