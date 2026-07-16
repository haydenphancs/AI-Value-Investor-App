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

from google.genai import types

from app.database import get_supabase
from app.integrations.gemini import get_gemini_client
from app.integrations.fmp import get_fmp_client
from app.config import settings
from app.schemas.chat import StockChartWidget, HistoricalDataPoint

logger = logging.getLogger(__name__)

# ── Gemini Function-Calling tool declaration ────────────────────────

_STOCK_CHART_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_stock_chart_data",
            description=(
                "Fetch current stock quote and 30-day historical price data "
                "for a given ticker symbol. Call this tool whenever the user "
                "asks about a specific stock's price, performance, chart, or "
                "whether they should buy/sell a stock."
            ),
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
    ]
)

_ANALYST_ANALYSIS_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_analyst_analysis",
            description=(
                "Fetch Wall Street analyst ratings, consensus, price targets, "
                "and recent upgrade/downgrade actions for a given ticker symbol. "
                "Call this tool when the user asks about analyst opinions, "
                "consensus ratings, price targets, upgrades, downgrades, or "
                "why a stock is rated as a buy or sell."
            ),
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
    ]
)

_SENTIMENT_ANALYSIS_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_sentiment_analysis",
            description=(
                "Fetch market sentiment analysis and mood data for a given ticker symbol. "
                "This includes social media mentions, news sentiment scores, and an overall "
                "0-100 mood gauge. Call this tool when the user asks about market sentiment, "
                "mood, why a stock feels bearish or bullish, social media buzz, or "
                "what people are saying about a stock."
            ),
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
    ]
)


_MARKET_OVERVIEW_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="get_market_overview",
            description=(
                "Fetch current market valuation (P/E ratio, forward P/E, earnings yield), "
                "sector performance (all 11 sectors with daily change), and macroeconomic "
                "indicators. Call this tool when the user asks about the overall market, "
                "market deep dive, sector rotation, market valuation, or macro outlook. "
                "This is for INDEX analysis only, not individual stocks."
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
        history = self._get_recent_messages(session_id, limit=20)

        # Step 2: RAG context + conversation memory — independent, so run concurrently to shave a
        # serial LLM round-trip off time-to-first-token.
        (chunks, citations), conversation_block = await asyncio.gather(
            self._retrieve_context(user_message, stock_id, history),
            self._condense_history(history),
        )

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
        prompt = self._build_prompt(user_message, conversation_block, chunks)

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

        # No tool widget (text-only question, or the FC round failed and degraded to plain text
        # above) → fall back to the deterministic screen-scoped widget, so an asset-detail chat
        # keeps its inline chart on this non-streaming path too (matching prepare_stream_generation).
        if widget is None:
            widget = await self._deterministic_widget(asset_type, stock_id, reference_id)

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

        history = self._get_recent_messages(session_id, limit=20)

        # RAG context + conversation memory — independent, run concurrently (same as generate_response).
        (chunks, citations), conversation_block = await asyncio.gather(
            self._retrieve_context(user_message, stock_id, history),
            self._condense_history(history),
        )

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
        )
        prompt = self._build_prompt(user_message, conversation_block, chunks)
        widget = await self._deterministic_widget(asset_type, stock_id, reference_id)
        sources = self._build_sources(context_type, reference_id, citations)

        return {
            "prompt": prompt,
            "system_instruction": system_instruction,
            "citations": citations if citations else None,
            "widget": widget,
            "sources": sources if sources else None,
            "asset_type": asset_type,
        }

    async def stream_synthesis(self, prep, user_message, route, tools, tool_handlers):
        """Cross-domain multi-agent: run each specialist's agentic answer in PARALLEL (non-streamed),
        then STREAM a synthesized answer that merges their perspectives.

        Yields the same (kind, payload) events the endpoint consumes: ("thought"|"answer", str) plus
        ("widget", dict) for each specialist's renderable widget (the endpoint dedups). Bounded
        (max_rounds=2 per specialist, ≤3 specialists from the router). Degrades to a single general
        agentic stream if every specialist fails, so the user always gets a reply."""
        from app.services.agents.chat_specialists import apply_specialist, get_specialist
        from app.services.agents.chat_tools import widget_from_tool_result

        keys = route["specialists"]
        # Progress note into the thinking card while the specialists work (no answer tokens yet).
        yield "thought", f"Consulting the {', '.join(route['labels'])} perspectives, then synthesizing…"

        async def _run(key: str):
            sys = apply_specialist(prep["system_instruction"], key)
            texts, wgts = [], []
            try:
                async for kind, payload in self.gemini.stream_agentic(
                    prep["prompt"], tools=tools, tool_handlers=tool_handlers,
                    system_instruction=sys, max_rounds=2,
                ):
                    if kind == "answer":
                        texts.append(payload)
                    elif kind == "tool":
                        w = widget_from_tool_result(payload.get("result"))
                        if w is not None:
                            wgts.append(w)
            except Exception as e:
                logger.warning("Synthesis specialist %s failed: %s: %s", key, type(e).__name__, e)
            return {"label": get_specialist(key).label, "answer": "".join(texts).strip(), "widgets": wgts}

        results = await asyncio.gather(*[_run(k) for k in keys], return_exceptions=True)
        results = [r for r in results if isinstance(r, dict) and r.get("answer")]

        # Emit each specialist's widgets (the endpoint dedups against the base + across specialists).
        for r in results:
            for w in r["widgets"]:
                yield "widget", w

        if not results:
            # Every specialist failed → a single general agentic answer so the turn still completes.
            async for ev in self.gemini.stream_agentic(
                prep["prompt"], tools=tools, tool_handlers=tool_handlers,
                system_instruction=prep["system_instruction"],
            ):
                yield ev
            return

        # Synthesize: stream ONE unified answer (no tools — the data's already gathered).
        perspectives = "\n\n".join(f"[{r['label']} view]\n{r['answer'][:1200]}" for r in results)
        synth_prompt = (
            f"USER QUESTION:\n{user_message}\n\n"
            f"You considered these analyst perspectives:\n\n{perspectives}\n\n"
            "Write ONE concise, unified answer that INTEGRATES the perspectives above — do NOT list "
            "them separately and do NOT mention 'perspectives'/'specialists'/'views'. Lead with the "
            "direct answer, then the 2-3 points that matter most across the lenses. Follow the STYLE rules."
        )
        # If the merge itself fails (e.g. the quota circuit opened between the specialists finishing
        # and this call), degrade to the already-computed specialist answer instead of throwing away
        # real work — the endpoint would otherwise fall back to another Gemini call and error out.
        merge_yielded = False
        try:
            async for kind, text in self.gemini.stream_text(
                synth_prompt, system_instruction=prep["system_instruction"],
            ):
                if kind == "answer" and text:
                    merge_yielded = True
                yield kind, text
        except Exception as e:
            logger.warning("Synthesis merge failed (%s: %s) — using the top specialist answer",
                           type(e).__name__, e)
        # Salvage the already-computed specialist work whenever the merge produced NO answer text —
        # whether it RAISED, or completed cleanly with only thoughts / a safety-filtered / empty
        # answer (e.g. MAX_TOKENS spent during thinking). Without covering the clean-but-empty case,
        # stream_synthesis would yield nothing → the endpoint sees empty content, raises "empty
        # stream result", and burns a THIRD full generate_response (non-synthesized), discarding both
        # specialist answers. The merge_yielded guard still prevents a double answer when partial
        # text already streamed. No Gemini call needed — the answer is already in hand.
        if not merge_yielded:
            yield "answer", results[0]["answer"]

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

    # RAG chunk source_type → the human "source" pill label. Absent/unknown → "SEC filing"
    # (the filing-only stock path, whose chunks carry no source_type).
    _RAG_SOURCE_TYPE_LABEL = {"book": "Book", "article": "Article", "filing": "SEC filing"}

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

        # RAG citations → one pill per distinct source. Label by the chunk's source_type
        # (book / article / filing) instead of a hardcoded "SEC filing", so once the RAG
        # corpus is ingested a book/article chunk isn't mis-attributed to a filing. Absent
        # source_type (the filing-only stock path) still labels "SEC filing".
        if citations:
            seen: set = set()
            for c in citations:
                if not isinstance(c, dict):
                    continue
                section = (c.get("source") or "").strip()
                detail = (c.get("source_label") or "").strip() or section
                key = detail.lower()
                if not detail or key in seen or key == "document":
                    continue
                seen.add(key)
                label = cls._RAG_SOURCE_TYPE_LABEL.get(
                    (c.get("source_type") or "").strip().lower(), "SEC filing"
                )
                sources.append({"label": label, "detail": detail})
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
            # Dedup case-insensitively, preserving order: the model can echo the same question twice,
            # and duplicate chips collide the iOS `ForEach(id: \.self)` (a dropped row + a warning)
            # besides being poor UX.
            out: List[str] = []
            seen: set = set()
            for s in raw:
                if not isinstance(s, str):
                    continue
                t = s.strip()
                if t and t.lower() not in seen:
                    seen.add(t.lower())
                    out.append(t)
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
            # Guard the None / missing-snapshots case cleanly (mirrors the resolver's INDEX branch) so
            # a cold/failed index fetch degrades to "no widget" via a legible error dict rather than a
            # noisy AttributeError on `detail.snapshots_data.valuation`.
            if not detail or not getattr(detail, "snapshots_data", None):
                return {"error": f"No index detail available for {symbol}"}

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
    def _get_valuation_level(pe: Optional[float]) -> str:
        # A missing / non-positive / NaN P/E means "no earnings data" (e.g. the index
        # sector-benchmark fallback returned 0 — or round(nan) — on a thin or failed recompute) —
        # that is NOT cheap. Guard first so it never renders as a real band. `pe != pe` catches NaN,
        # which slips past every `<` comparison below and would otherwise fall through to the
        # most-expensive "Overheated" band — the exact inverse of the truth.
        if pe is None or pe != pe or pe <= 0:
            return "Unknown"
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

    # ── RAG retrieval (Phase 4: query-rewrite → RETRIEVAL_QUERY embed → wider search → LLM-rerank) ──

    _REWRITE_PRONOUNS = frozenset({
        "it", "its", "that", "this", "they", "them", "those", "these", "their", "there", "here",
    })

    @classmethod
    def _needs_rewrite(cls, user_message: str) -> bool:
        """Cheap heuristic: only rewrite a message that looks context-dependent (a short fragment, or
        one carrying pronouns/ellipsis), so standalone questions skip the extra LLM call."""
        m = (user_message or "").strip()
        if len(m) < 15:
            return True
        words = {w.strip(".,!?;:'\"()").lower() for w in m.split()}
        return bool(words & cls._REWRITE_PRONOUNS)

    async def _rewrite_query(self, user_message: str, history: List[Dict]) -> str:
        """Resolve a follow-up into a standalone search query using recent turns (cheap flash-lite).
        Skips the call when the message isn't context-dependent. Never raises → the original message."""
        if not history or not self._needs_rewrite(user_message):
            return user_message
        try:
            convo = "\n".join(
                f"{'User' if m.get('role') == 'user' else 'Assistant'}: {(m.get('content') or '')[:200]}"
                for m in history[-4:]
            )
            prompt = (
                "Rewrite the user's LATEST question into a short, standalone search query for a "
                "document search — resolve pronouns/ellipsis using the conversation, keep it "
                "keyword-rich, and do NOT answer it.\n\n"
                f"CONVERSATION:\n{convo}\n\nLATEST QUESTION: {user_message}\n\nStandalone search query:"
            )
            res = await self.gemini.generate_text(prompt, model_name="gemini-2.5-flash-lite")
            rewritten = (res.get("text") or "").strip().strip('"').strip()
            return rewritten if 0 < len(rewritten) <= 400 else user_message
        except Exception as e:
            logger.warning("Query rewrite failed (%s: %s) — using original", type(e).__name__, e)
            return user_message

    async def _rerank_chunks(self, query: str, chunks: List[Dict], top_k: int) -> List[Dict]:
        """LLM-rerank candidate chunks by relevance to `query`, keeping `top_k` (cheap flash-lite).
        Never raises → returns the first `top_k` in vector order on any failure."""
        if len(chunks) <= top_k:
            return chunks
        try:
            listing = "\n".join(
                f"[{i}] {(c.get('chunk_text') or '')[:280]}" for i, c in enumerate(chunks)
            )
            prompt = (
                f"QUERY: {query}\n\nPASSAGES:\n{listing}\n\n"
                f"Return the indices of the {top_k} passages MOST relevant to answering the query, "
                'best first, as JSON: {"indices": [numbers]}.'
            )
            res = await self.gemini.generate_json(prompt, model_name="gemini-2.5-flash-lite")
            data = json.loads((res.get("text") or "{}") or "{}")
            picked: List[Dict] = []
            seen: set = set()
            for i in (data.get("indices") or []):
                if isinstance(i, int) and 0 <= i < len(chunks) and i not in seen:
                    seen.add(i)
                    picked.append(chunks[i])
                    if len(picked) >= top_k:
                        break
            # Backfill from vector order if the model returned too few valid indices.
            for i, c in enumerate(chunks):
                if len(picked) >= top_k:
                    break
                if i not in seen:
                    picked.append(c)
            return picked[:top_k]
        except Exception as e:
            logger.warning("Chunk rerank failed (%s: %s) — using vector order", type(e).__name__, e)
            return chunks[:top_k]

    async def _retrieve_context(
        self, user_message: str, stock_id: Optional[str], history: List[Dict],
    ) -> Tuple[List[Dict], List[Dict]]:
        """Chat RAG retrieval: (query-rewrite) → RETRIEVAL_QUERY embed → wider vector search →
        (LLM-rerank) → top-K, plus the citations built from the surviving chunks. Never raises → ([], [])."""
        chunks: List[Dict] = []
        citations: List[Dict] = []
        try:
            query = user_message
            if settings.CHAT_QUERY_REWRITE_ENABLED:
                query = await self._rewrite_query(user_message, history)
            query_embedding = await self.gemini.generate_embedding(
                query, model_name="models/gemini-embedding-001", task_type="RETRIEVAL_QUERY",
            )
            top_k = settings.RAG_TOP_K_RESULTS
            rerank = settings.CHAT_RERANK_ENABLED
            match_count = settings.RAG_RERANK_CANDIDATES if rerank else top_k
            if stock_id:
                candidates = self._search_filing_chunks(query_embedding, stock_id, match_count)
            else:
                candidates = self._search_all_chunks(query_embedding, match_count)
            if rerank and len(candidates) > top_k:
                chunks = await self._rerank_chunks(query, candidates, top_k)
            else:
                chunks = candidates[:top_k]
            for i, chunk in enumerate(chunks):
                # `(x or default)` — a nullable section_title / present-but-null chunk_text
                # would make `.get(k, default)[:200]` slice a None (TypeError). Belt-and-suspenders
                # for the RAG-ingest path (chunk_text is NOT NULL today; section_title is nullable).
                citations.append({
                    "index": i + 1,
                    "source": chunk.get("section_title") or "Document",
                    "source_type": chunk.get("source_type"),
                    "source_label": chunk.get("source_label"),
                    "text": (chunk.get("chunk_text") or "")[:200],
                })
        except Exception as e:
            logger.warning("RAG retrieval failed, proceeding without context: %s", e)
        return chunks, citations

    def _search_filing_chunks(
        self, embedding: List[float], ticker: str, match_count: Optional[int] = None
    ) -> List[Dict]:
        try:
            result = self.supabase.rpc("search_filing_chunks", {
                "query_embedding": embedding,
                "match_threshold": settings.VECTOR_SIMILARITY_THRESHOLD,
                "match_count": match_count or settings.RAG_TOP_K_RESULTS,
                "filter_ticker": ticker.upper(),
            }).execute()
            return result.data or []
        except Exception as e:
            logger.warning(f"Filing chunk search failed: {e}")
            return []

    def _search_all_chunks(self, embedding: List[float], match_count: Optional[int] = None) -> List[Dict]:
        try:
            result = self.supabase.rpc("search_all_chunks", {
                "query_embedding": embedding,
                "match_threshold": settings.VECTOR_SIMILARITY_THRESHOLD,
                "match_count": match_count or settings.RAG_TOP_K_RESULTS,
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
        # Commodity symbols FIRST — the FMP USD-suffixed codes (GCUSD/CLUSD/SIUSD/…) would
        # otherwise be swallowed by the generic endswith("USD") crypto heuristic below and
        # mis-voiced as crypto. No symbol collides between the commodity and crypto sets.
        if sid in {
            "GCUSD", "SIUSD", "CLUSD", "NGUSD", "PLUSD", "HGUSD",
            "ZSUSD", "ZCUSD", "ZUSD", "LBUSD", "OJUSD", "KCUSD",
            "SBUSD", "CTUSD", "CCUSD",
            "GOLD", "SILVER", "OIL", "NATGAS", "PLATINUM", "COPPER",
        }:
            return "COMMODITY"
        # Common crypto suffixes
        if sid.endswith("USD") or sid.endswith("USDT") or sid in {
            "BTC", "ETH", "SOL", "ADA", "DOT", "AVAX", "MATIC", "LINK",
            "XRP", "DOGE", "SHIB", "UNI", "AAVE", "LTC", "BCH", "ATOM",
        }:
            return "CRYPTO"
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
            "user explicitly asks for more. Use plain, conversational language. "
            "NEVER give a personal buy/sell/hold directive (don't say 'you should buy/sell') — "
            "explain the tradeoffs and let the user decide. Keep the required "
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

        return base

    # ── Conversation memory (Phase 5: rolling summary for long chats) ──────────

    _RECENT_TURNS = 6  # last N messages kept verbatim; older ones roll into a summary

    @staticmethod
    def _fmt_turns(msgs: List[Dict], cap: int = 500) -> str:
        return "\n".join(
            f"{'User' if m.get('role') == 'user' else 'Assistant'}: {(m.get('content') or '')[:cap]}"
            for m in msgs
        )

    async def _condense_history(self, history: List[Dict]) -> str:
        """Build the conversation block for the prompt. Short chats → recent turns verbatim. Long
        chats → a rolling SUMMARY of the older turns + the last few verbatim, so early context
        (tickers, goals, numbers) isn't dropped by simple truncation. Never raises → recent-only."""
        if not history:
            return ""
        recent = history[-self._RECENT_TURNS:]
        older = history[:-self._RECENT_TURNS]
        if not older:
            return f"CONVERSATION HISTORY:\n{self._fmt_turns(recent)}"
        summary = ""
        try:
            prompt = (
                "Summarize the earlier part of this conversation in 3-5 short bullet points — keep "
                "the user's goals and any specifics (tickers, numbers, preferences) so it can ground "
                "later answers. No preamble.\n\n" + self._fmt_turns(older, cap=400)
            )
            res = await self.gemini.generate_text(prompt, model_name="gemini-2.5-flash-lite")
            summary = (res.get("text") or "").strip()
        except Exception as e:
            logger.warning("History condense failed (%s: %s) — recent turns only", type(e).__name__, e)
        if summary:
            return (
                f"EARLIER CONVERSATION (summary):\n{summary}\n\n"
                f"RECENT MESSAGES:\n{self._fmt_turns(recent)}"
            )
        return f"CONVERSATION HISTORY:\n{self._fmt_turns(recent)}"

    @staticmethod
    def _build_prompt(
        user_message: str, conversation_block: str, chunks: List[Dict],
    ) -> str:
        parts = []

        if chunks:
            # `(x or "")` not `.get(k, "")`: a chunk row can carry a present-but-NULL chunk_text
            # once the RAG corpus is ingested, and `str.join` on a None raises — and this call is
            # OUTSIDE any try/except, so it would abort the whole prompt build (→ error frame).
            context_text = "\n\n---\n\n".join(
                (c.get("chunk_text") or "") for c in chunks[:5]
            )
            parts.append(f"RELEVANT CONTEXT:\n{context_text}\n\n---\n")

        if conversation_block:
            parts.append(f"{conversation_block}\n\n---\n")

        parts.append(f"USER MESSAGE:\n{user_message}")

        if chunks:
            parts.append(
                "\nAnswer directly and concisely. Cite the context with [1], [2], etc. only "
                "where it backs a specific claim."
            )

        return "\n".join(parts)
