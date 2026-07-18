"""
ChatContextResolver — turns {context_type, reference_id} into a compact,
token-budgeted grounding block for the Cay AI chat prompt.

The iOS client sends only the screen type + a reference id (a ticker,
"TICKER|persona", an article slug, a book curriculum order, ...). This resolver
fetches the ALREADY-CACHED data for that screen from the existing service layer
and returns a text block that ``chat_service`` injects into the Gemini system
instruction, so iOS stops shipping big raw context strings.

Grounding strategy — "prune-then-dump" (one generic serializer, not per-field
curation): a short curated LEAD guarantees the highest-value facts survive the
cap, then ``_flatten_for_grounding`` dumps the WHOLE payload the resolver already
holds (a cached report dict / a Pydantic ``model_dump`` / a bundled article dict)
as compact ``key: value`` text — MINUS the heavy non-semantic keys a text model
can't use (price/chart float series, audio read-along timing arrays, gradients,
urls, embeddings). This grounds the chat on ~all of the screen's data, auto-picks
up new fields, and stays cheap because ``_DUMP_CAP`` bounds every block.

Contract:
  * Never recomputes — only reads existing caches / bundled content.
  * Never raises — any miss / failure degrades to the client-provided context
    (or ``None``) with a ``logger.warning``, so a chat can always proceed.

STOCK is intentionally a no-op here: ``chat_service`` already enriches stock
chats from ``stock_id`` (profit / snapshot / company-profile summaries) + the
iOS current-tab context, so the resolver defers to that path.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Hard bound on how long a single context resolve may take. Cache-only reads
# (report / money-moves) finish well under this; the ETF/CRYPTO/INDEX services
# fall through to a cold recompute — this ceiling stops that from stalling the
# FIRST streamed token; on timeout the chat proceeds ungrounded (never blocked).
_RESOLVE_TIMEOUT_SECONDS = 4.0

# ── Grounding caps ──────────────────────────────────────────────────
_MAX_REPORT_SUMMARY = 800   # executive-summary portion of the report lead
_MAX_REPORT_MODULE = 280    # the price-move narrative / commodity blurb in a lead
_DUMP_CAP = 2800            # the flattened-payload portion (per screen). Lead adds ~0.2–1.2k on top.
_STR_CAP = 400              # any single string field is trimmed to this in the dump

# Keys dropped ANYWHERE in the payload during the dump — heavy, non-semantic data
# a text model can't use (measured: 48–84% of a raw payload). Compared case-
# insensitively, so both snake_case (`chart_data`) and camelCase (`readAlong`,
# `heroGradientColors`, `audioUrl`) forms match.
_DROP_KEYS = frozenset({
    # numeric / chart / price series (raw coordinates)
    "chart_data", "prices", "recent_prices", "recent_price_dates", "timeline_prices",
    "hedge_fund_price_data", "hedge_fund_flow_data", "data_points", "history",
    "dividend_history", "dividends", "growth_chart", "profit_power", "earnings_track_record",
    "news_articles", "news",
    # audio read-along timing arrays + UI cosmetics
    "readalong", "readalongwords", "itemsreadalong", "herogradientcolors",
    "audiourl", "imageurl", "videourl", "logo_url", "logourl", "icon", "heroimage",
    "website", "whitepaper", "url", "uri", "source_url", "sourceurl",
    # embeddings / bulky source lists
    "embedding", "query_embedding", "sources",
})

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


def _num(v: Any) -> Optional[str]:
    """Format a number plainly (comma-grouped, no scientific notation, no trailing zeros).
    Returns None for NaN so it never leaks into the grounding."""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if v != v:  # NaN
            return None
        if v.is_integer():
            return f"{int(v):,}"
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return None


def _flatten_for_grounding(
    payload: Any, max_chars: int, str_cap: int = _STR_CAP, skip_top: Tuple[str, ...] = (),
) -> str:
    """Render a JSON-ish payload (a cached dict / a ``model_dump`` / an article dict) to compact
    ``key: value`` grounding lines. Drops ``_DROP_KEYS`` anywhere in the tree + ``skip_top`` at the top
    level (case-insensitive), truncates long strings to ``str_cap``, caps total output at ``max_chars``,
    and NEVER raises (a bad node is skipped). Lists are inlined (scalars) or walked per-item (dicts),
    both capped at 12 elements so one long array can't blow the budget."""
    lines: List[str] = []
    remaining = [max_chars]
    skip_top_l = {s.lower() for s in skip_top}

    def _scalar(v: Any) -> Optional[str]:
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return None
            return (s[:str_cap] + "…") if len(s) > str_cap else s
        return _num(v)

    def _emit(key: str, s: str) -> bool:
        line = f"{key}: {s}" if key else s
        lines.append(line)
        remaining[0] -= len(line) + 1
        return remaining[0] > 0

    def _walk(o: Any, prefix: str, top: bool = False) -> bool:
        if remaining[0] <= 0:
            return False
        if isinstance(o, dict):
            for k, v in o.items():
                if not isinstance(k, str):
                    continue
                kl = k.lower()
                if kl in _DROP_KEYS or (top and kl in skip_top_l):
                    continue
                if v is None or v == "" or v == [] or v == {}:
                    continue
                key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, (dict, list)):
                    if not _walk(v, key):
                        return False
                else:
                    sv = _scalar(v)
                    if sv is not None and not _emit(key, sv):
                        return False
        elif isinstance(o, list):
            if all(not isinstance(x, (dict, list)) for x in o):   # pure-scalar list → inline
                vals = [s for s in (_scalar(x) for x in o[:12]) if s is not None]
                if vals and not _emit(prefix, ", ".join(vals)):
                    return False
            else:                                                 # mixed / dict list → per item
                for i, item in enumerate(o[:12]):
                    if not _walk(item, f"{prefix}[{i}]"):
                        return False
        else:                                                     # a scalar reached via a list item
            sv = _scalar(o)
            if sv is not None and not _emit(prefix, sv):
                return False
        return True

    try:
        _walk(payload, "", top=True)
    except Exception as e:   # a malformed node must never drop the whole grounding block
        logger.debug("chat_context: flatten stopped early (%s: %s)", type(e).__name__, e)
    return "\n".join(lines)


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
        # the client sends a context string (title/author + the passage) we pass through.
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
            block = await asyncio.wait_for(
                handler(self, reference_id, client_context),
                timeout=_RESOLVE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "chat_context: resolve TIMED OUT (>%.1fs) for %s/%s — likely a cold "
                "detail-cache recompute; proceeding ungrounded",
                _RESOLVE_TIMEOUT_SECONDS, context_type, reference_id,
            )
            return client_context
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

    @staticmethod
    def _as_dict(obj: Any) -> Dict[str, Any]:
        """Best-effort dict view for the dump (a Pydantic model → model_dump; a dict → itself)."""
        if isinstance(obj, dict):
            return obj
        dump = getattr(obj, "model_dump", None)
        if callable(dump):
            try:
                return dump()
            except Exception:
                return {}
        return {}

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

        # LEAD — guarantee the highest-value, most-asked facts survive the cap.
        lead: List[str] = [
            f"The user is viewing the in-depth Cay research report for "
            f"{report.get('company_name') or ticker} ({ticker})."
        ]
        score = report.get("quality_score")
        # 0-100 scale (iOS renders /100). A /10 label told Gemini "72/10", poisoning grounding.
        if isinstance(score, (int, float)):
            lead.append(f"Overall quality score: {score:.0f}/100.")
        pa = report.get("price_action")
        if isinstance(pa, dict):
            narrative = (pa.get("narrative") or "").strip()
            if narrative:
                bits: List[str] = []
                change = pa.get("change_pct")
                if isinstance(change, (int, float)) and change == change:   # `== change` skips NaN
                    window = (pa.get("window_label") or "").strip()
                    bits.append(f"{change:+.1f}%" + (f" over {window}" if window else ""))
                tag = (pa.get("tag") or "").strip()
                if tag:
                    bits.append(tag)
                head = f"Recent price movement ({'; '.join(bits)}): " if bits else "Recent price movement: "
                lead.append(head + _cap(narrative, _MAX_REPORT_MODULE))
        summary = (report.get("executive_summary_text") or "").strip()
        if summary:
            lead.append("Executive summary: " + _cap(summary, _MAX_REPORT_SUMMARY))

        # DUMP — every other section the user can see (thesis, fundamentals, revenue, moat, ownership,
        # Wall Street, macro, critical factors) minus the lead keys + the heavy chart/price arrays.
        dump = _flatten_for_grounding(
            report, _DUMP_CAP,
            skip_top=("symbol", "company_name", "exchange", "agent", "quality_score",
                      "live_date", "price_close_date", "price_action", "executive_summary_text",
                      "disclaimer_text"),
        )
        parts = list(lead)
        if dump:
            parts.append("Full report data the user can see:\n" + dump)
        parts.append(
            "Answer grounded in THIS report; if asked about something it doesn't cover, say so "
            "rather than inventing figures."
        )
        return "\n".join(parts)

    # ── STOCK (no-op — chat_service enriches via stock_id + iOS tab context) ──
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
        lead = (
            f"The user is viewing the ETF detail screen for {detail.name} ({detail.symbol}). "
            f"Price ${detail.current_price:,.2f} ({detail.price_change_percent:+.2f}%)."
        )
        dump = _flatten_for_grounding(
            self._as_dict(detail), _DUMP_CAP,
            skip_top=("symbol", "name", "current_price", "price_change_percent"),
        )
        return lead + ("\nScreen data the user can see:\n" + dump if dump else "")

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
        lead = (
            f"The user is viewing the crypto detail screen for {detail.name} ({detail.symbol}). "
            f"Price ${detail.current_price:,.4f} ({detail.price_change_percent:+.2f}%)."
        )
        dump = _flatten_for_grounding(
            self._as_dict(detail), _DUMP_CAP,
            skip_top=("symbol", "name", "current_price", "price_change_percent"),
        )
        return lead + ("\nScreen data the user can see:\n" + dump if dump else "")

    # ── INDEX ────────────────────────────────────────────────────────
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
        name = (getattr(detail, "index_name", "") or "").strip()
        lead = f"The user is viewing the market/index detail screen for {name or symbol}."
        price = getattr(detail, "current_price", None)
        chg = getattr(detail, "price_change_percent", None)
        if isinstance(price, (int, float)) and isinstance(chg, (int, float)):
            lead += f" Level {price:,.2f} ({chg:+.2f}%)."
        dump = _flatten_for_grounding(
            self._as_dict(detail), _DUMP_CAP,
            skip_top=("symbol", "index_name", "current_price", "price_change_percent"),
        )
        return lead + ("\nScreen data the user can see:\n" + dump if dump else "")

    # ── COMMODITY ────────────────────────────────────────────────────
    async def _resolve_commodity(
        self, reference_id: Optional[str], client_context: Optional[str]
    ) -> Optional[str]:
        # iOS already passes a rich commodity context (price / stats / performance / news). Enrich it
        # with the curated commodity PROFILE (what it is + who produces / consumes it) that the client
        # context lacks — read from the BUNDLED static registry (`_get_meta`, no FMP fetch, honoring
        # the never-recompute contract). Never replaces the client context; degrades to it on any miss.
        symbol = (reference_id or "").strip().upper()
        if not symbol:
            return client_context
        try:
            from app.services.commodity_service import _get_meta
            meta = _get_meta(symbol)
        except Exception as e:
            logger.warning("chat_context: commodity profile lookup failed for %s: %s", symbol, e)
            return client_context
        if not isinstance(meta, dict) or not meta:
            return client_context
        dump = _flatten_for_grounding(meta, _DUMP_CAP, skip_top=("fmp_symbol", "related", "tick_size", "unit"))
        if not dump:
            return client_context
        profile_block = "Commodity profile (what the user is viewing):\n" + dump
        return f"{client_context}\n\n{profile_block}" if client_context else profile_block

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
        lead = [
            f'The user is reading the Money Moves article "{article.get("title", "")}"'
            + (f" by {author_name}" if author_name else "") + "."
        ]
        subtitle = (article.get("subtitle") or "").strip()
        if subtitle:
            lead.append(subtitle)
        # Dump the article MINUS the engagement/cosmetic metadata (so the budget goes to the body +
        # highlights + statistics). The drop set strips the read-along timing arrays + gradients.
        dump = _flatten_for_grounding(
            article, _DUMP_CAP,
            skip_top=("slug", "title", "subtitle", "author", "cardsubtitle", "category",
                      "readtimeminutes", "viewcount", "learnercount", "sortorder", "commentcount",
                      "publisheddaysago", "taglabel", "isfeatured", "hasaudioversion",
                      "audiodurationseconds"),
        )
        parts = list(lead)
        if dump:
            parts.append("Article content the user can see:\n" + dump)
        parts.append("Answer in the context of this article's ideas.")
        return "\n".join(parts)

    # ── JOURNEY_LESSON ───────────────────────────────────────────────
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
        lead = [f'The user is on the Investor Journey lesson "{title}".']
        desc = (get("description", "") or "").strip()
        if desc:
            lead.append(desc)
        # Dump the lesson body (story_content.cards[].text) + metadata; the drop set strips the
        # per-word read-along timing arrays.
        dump = _flatten_for_grounding(
            self._as_dict(lesson), _DUMP_CAP,
            skip_top=("id", "title", "description", "sort_order", "level", "category", "duration_minutes"),
        )
        parts = list(lead)
        if dump:
            parts.append("Lesson content the user can see:\n" + dump)
        parts.append("Answer in the context of this lesson.")
        return "\n".join(parts)


# ── Module-level singleton (matches every other service) ────────────
_resolver: Optional[ChatContextResolver] = None


def get_chat_context_resolver() -> ChatContextResolver:
    global _resolver
    if _resolver is None:
        _resolver = ChatContextResolver()
    return _resolver
