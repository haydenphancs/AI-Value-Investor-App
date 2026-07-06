"""
ChatContextResolver — turns {context_type, reference_id} into a compact,
token-budgeted grounding block for the Cay AI chat prompt.

The iOS client sends only the screen type + a reference id (a ticker,
"TICKER|persona", an article slug, a book curriculum order, ...). This resolver
fetches the ALREADY-CACHED data for that screen from the existing service layer
and returns a short text block that ``chat_service`` injects into the Gemini
system instruction. The whole point is that iOS stops shipping big raw context
strings — so every branch is token-budgeted.

Contract:
  * Never recomputes — only reads existing caches / bundled content.
  * Never raises — any miss / failure degrades to the client-provided context
    (or ``None``) with a ``logger.warning`` carrying context_type + reference_id,
    so a chat can always proceed (ungrounded at worst).

STOCK is intentionally a no-op here: ``chat_service`` already enriches stock
chats from ``stock_id`` (profit / snapshot / company-profile summaries), so the
resolver defers to that path rather than duplicating those fetches.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Per-source character caps ───────────────────────────────────────
# Keep the injected block small. These bound each source; the total block is at
# most one source's worth (we resolve exactly one screen).
_MAX_REPORT_SUMMARY = 800
_MAX_ARTICLE = 700
_MAX_ASSET = 700
_MAX_LESSON = 600

# agent_tag → full persona key, so a reference_id built from EITHER form
# ("AAPL|buffett" or "AAPL|warren_buffett") resolves to the same cache row.
_AGENT_TAG_TO_KEY = {
    "buffett": "warren_buffett",
    "wood": "cathie_wood",
    "lynch": "peter_lynch",
    "ackman": "bill_ackman",
    "burry": "michael_burry",
}
_DEFAULT_PERSONA = "warren_buffett"

# Treated as "no grounding needed" — fall through to any client context.
_NO_CONTEXT = {"", "NONE", "GENERAL", "NORMAL"}


def _cap(text: str, limit: int) -> str:
    """Trim to `limit` chars on a word boundary, adding an ellipsis if cut."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"


class ChatContextResolver:
    """Dispatches {context_type, reference_id} → compact grounding text."""

    async def resolve(
        self,
        context_type: Optional[str],
        reference_id: Optional[str],
        client_context: Optional[str] = None,
    ) -> Optional[str]:
        if not context_type:
            return client_context
        ctype = context_type.strip().upper()
        if ctype in _NO_CONTEXT:
            return client_context
        # BOOK has no backend text (book content is bundled in the iOS app), so
        # the client sends a small context string we pass through verbatim.
        if ctype == "BOOK":
            return client_context

        handler = self._dispatch().get(ctype)
        if handler is None:
            logger.warning(
                "chat_context: unknown context_type=%s (ref=%s) — using client context",
                context_type, reference_id,
            )
            return client_context

        try:
            block = await handler(self, reference_id, client_context)
        except Exception as e:
            logger.warning(
                "chat_context: resolve failed for %s/%s: %s: %s — degrading to client context",
                context_type, reference_id, type(e).__name__, e,
            )
            return client_context
        return block or client_context

    # ── Dispatch table ──────────────────────────────────────────────
    @classmethod
    def _dispatch(cls):
        return {
            "TICKER_REPORT": cls._resolve_ticker_report,
            "STOCK": cls._resolve_stock,
            "ETF": cls._resolve_etf,
            "CRYPTO": cls._resolve_crypto,
            "INDEX": cls._resolve_index,
            "COMMODITY": cls._resolve_commodity,
            "MONEY_MOVES_ARTICLE": cls._resolve_money_move,
            "JOURNEY_LESSON": cls._resolve_journey_lesson,
        }

    # ── TICKER_REPORT ────────────────────────────────────────────────
    async def _resolve_ticker_report(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        if not reference_id:
            return None
        ticker, _, persona = reference_id.partition("|")
        ticker = ticker.strip().upper()
        persona = (persona or "").strip().lower()
        persona = _AGENT_TAG_TO_KEY.get(persona, persona) or _DEFAULT_PERSONA
        if not ticker:
            return None

        from app.services import ticker_report_cache

        report = await ticker_report_cache.get_cached_report(ticker, persona)
        if not report:
            logger.info(
                "chat_context: no cached report for %s/%s (chat proceeds ungrounded)",
                ticker, persona,
            )
            return None

        parts: List[str] = [
            f"The user is viewing the in-depth Cay research report for "
            f"{report.get('company_name') or ticker} ({ticker})."
        ]
        score = report.get("quality_score")
        if isinstance(score, (int, float)):
            parts.append(f"Overall quality score: {score:.1f}/10.")
        summary = (report.get("executive_summary_text") or "").strip()
        if summary:
            parts.append("Executive summary: " + _cap(summary, _MAX_REPORT_SUMMARY))
        thesis = report.get("core_thesis") or {}
        bull = thesis.get("bull_case") or []
        bear = thesis.get("bear_case") or []
        if bull:
            parts.append("Top bull point: " + str(bull[0]))
        if bear:
            parts.append("Top bear point: " + str(bear[0]))
        parts.append(
            "Answer questions grounded in THIS report; if asked about something "
            "the report doesn't cover, say so rather than inventing figures."
        )
        return " ".join(parts)

    # ── STOCK (no-op — chat_service enriches via stock_id) ───────────
    async def _resolve_stock(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        return None

    # ── ETF ──────────────────────────────────────────────────────────
    async def _resolve_etf(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        symbol = (reference_id or "").strip().upper()
        if not symbol:
            return None
        from app.services.etf_service import get_etf_service

        detail = await get_etf_service().get_etf_detail(symbol)
        if not detail:
            return None
        parts: List[str] = [
            f"The user is viewing the ETF detail screen for {detail.name} ({detail.symbol}). "
            f"Price ${detail.current_price:,.2f} ({detail.price_change_percent:+.2f}%)."
        ]
        stats = [f"{i.label}: {i.value}" for i in (detail.key_statistics or [])[:6]]
        if stats:
            parts.append("Key stats — " + "; ".join(stats) + ".")
        prof = getattr(detail, "etf_profile", None)
        idx = getattr(prof, "index_tracked", None) if prof else None
        if idx:
            parts.append(f"Tracks: {idx}.")
        return _cap(" ".join(parts), _MAX_ASSET)

    # ── CRYPTO ────────────────────────────────────────────────────────
    async def _resolve_crypto(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        symbol = (reference_id or "").strip().upper()
        if not symbol:
            return None
        from app.services.crypto_service import get_crypto_service

        detail = await get_crypto_service().get_crypto_detail(symbol)
        if not detail:
            return None
        parts: List[str] = [
            f"The user is viewing the crypto detail screen for {detail.name} ({detail.symbol}). "
            f"Price ${detail.current_price:,.4f} ({detail.price_change_percent:+.2f}%)."
        ]
        stats: List[str] = []
        for group in (detail.key_statistics_groups or []):
            for item in group.statistics:
                stats.append(f"{item.label}: {item.value}")
                if len(stats) >= 6:
                    break
            if len(stats) >= 6:
                break
        if stats:
            parts.append("Key stats — " + "; ".join(stats) + ".")
        prof = getattr(detail, "crypto_profile", None)
        chain = getattr(prof, "blockchain", None) if prof else None
        if chain:
            parts.append(f"Blockchain: {chain}.")
        return _cap(" ".join(parts), _MAX_ASSET)

    # ── INDEX (best-effort; reuses index snapshots) ──────────────────
    async def _resolve_index(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        symbol = (reference_id or "").strip().upper()
        if not symbol:
            return None
        from app.services.index_service import get_index_service

        detail = await get_index_service().get_index_detail(symbol)
        if not detail:
            return None
        parts: List[str] = ["The user is viewing the market/index detail screen."]
        val = getattr(getattr(detail, "snapshots_data", None), "valuation", None)
        if val is not None:
            pe = getattr(val, "pe_ratio", None)
            fpe = getattr(val, "forward_pe", None)
            ey = getattr(val, "earnings_yield", None)
            if pe is not None:
                parts.append(f"Market P/E {pe:.1f}" + (f" (fwd {fpe:.1f})." if fpe else "."))
            if ey is not None:
                parts.append(f"Earnings yield {ey:.1f}%.")
        return _cap(" ".join(parts), _MAX_ASSET)

    # ── COMMODITY (fast-follow — pass through client context) ────────
    async def _resolve_commodity(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        return client_context

    # ── MONEY_MOVES_ARTICLE ──────────────────────────────────────────
    async def _resolve_money_move(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        slug = (reference_id or "").strip()
        if not slug:
            return None
        from app.services.money_moves_content_service import get_money_moves_content_service

        resp = await get_money_moves_content_service().get_money_moves()
        article = next(
            (a for a in (resp.articles or []) if isinstance(a, dict) and a.get("slug") == slug),
            None,
        )
        if not article:
            logger.info("chat_context: money move slug=%s not found", slug)
            return None
        author = article.get("author") or {}
        author_name = author.get("name") if isinstance(author, dict) else str(author or "")
        parts: List[str] = [
            f'The user is reading the Money Moves article "{article.get("title", "")}"'
            + (f" by {author_name}" if author_name else "") + "."
        ]
        subtitle = (article.get("subtitle") or "").strip()
        if subtitle:
            parts.append(subtitle)
        highlights = article.get("keyHighlights") or []
        hl_text = [str(h) for h in highlights[:4] if h]
        if hl_text:
            parts.append("Key highlights: " + "; ".join(hl_text) + ".")
        parts.append("Answer in the context of this article's ideas.")
        return _cap(" ".join(parts), _MAX_ARTICLE)

    # ── JOURNEY_LESSON (fast-follow) ─────────────────────────────────
    async def _resolve_journey_lesson(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        ref = (reference_id or "").strip()
        if not ref:
            return None
        from app.services.journey_content_service import get_journey_content_service

        resp = await get_journey_content_service().get_journey()
        lessons = getattr(resp, "lessons", None) or []

        def _match(lesson: Any) -> bool:
            get = lesson.get if isinstance(lesson, dict) else lambda k, d=None: getattr(lesson, k, d)
            return str(get("id", "")) == ref or str(get("title", "")) == ref

        lesson = next((l for l in lessons if _match(l)), None)
        if not lesson:
            return None
        get = lesson.get if isinstance(lesson, dict) else lambda k, d=None: getattr(lesson, k, d)
        title = get("title", "") or ""
        desc = (get("description", "") or "").strip()
        parts = [f'The user is on the Investor Journey lesson "{title}".']
        if desc:
            parts.append(desc)
        parts.append("Answer in the context of this lesson.")
        return _cap(" ".join(parts), _MAX_LESSON)


# ── Module-level singleton (matches every other service) ────────────
_resolver: Optional[ChatContextResolver] = None


def get_chat_context_resolver() -> ChatContextResolver:
    global _resolver
    if _resolver is None:
        _resolver = ChatContextResolver()
    return _resolver
