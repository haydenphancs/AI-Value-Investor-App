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

import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Hard bound on how long a single context resolve may take. Cache-only reads
# (report / money-moves / stock) finish in well under this. The ETF/CRYPTO/INDEX
# services fall through to a full FMP+Gemini recompute on a cold cache — this
# ceiling stops that from stalling the FIRST streamed token; on timeout the chat
# proceeds ungrounded (degraded, never blocked).
_RESOLVE_TIMEOUT_SECONDS = 4.0

# ── Per-source character caps ───────────────────────────────────────
# Keep the injected block small. These bound each source; the total block is at
# most one source's worth (we resolve exactly one screen).
_MAX_REPORT_SUMMARY = 800
_MAX_REPORT_MODULE = 280   # per on-screen module insight (price move, moat, revenue, profile blurb, …)
_MAX_ASSET = 1600          # ETF / crypto / index — holdings / AI snapshots / sector board / allocation / …
_MAX_ARTICLE = 2600        # Money Moves — whole block (title + highlights + body)
_MAX_ARTICLE_BODY = 2000   # the article body portion (sections → plain text)
_MAX_LESSON = 1800         # Investor Journey — whole block (title + description + body)
_MAX_LESSON_BODY = 1600    # the lesson body portion (story cards → plain text)

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
        # quality_score is on a 0-100 scale (persona_scoring clamps to [0,100];
        # iOS renders it as /100). Label it /100 — a /10 label told Gemini the
        # report scored e.g. "72/10", poisoning the grounding.
        if isinstance(score, (int, float)):
            parts.append(f"Overall quality score: {score:.0f}/100.")
        summary = (report.get("executive_summary_text") or "").strip()
        if summary:
            parts.append("Executive summary: " + _cap(summary, _MAX_REPORT_SUMMARY))

        # Recent Price Movement — the on-screen "Insight" that explains WHY the stock moved (the
        # single most common report-chat question, and the reported grounding gap). Special-format
        # it with the % move + the catalyst tag so a "why did it move recently?" answer can cite the
        # real reason instead of restating the raw price numbers.
        pa = report.get("price_action")
        if isinstance(pa, dict):
            narrative = (pa.get("narrative") or "").strip()
            if narrative:
                bits: List[str] = []
                change = pa.get("change_pct")
                window = (pa.get("window_label") or "").strip()
                if isinstance(change, (int, float)) and change == change:   # `== change` skips NaN
                    bits.append(f"{change:+.1f}%" + (f" over {window}" if window else ""))
                tag = (pa.get("tag") or "").strip()
                if tag:
                    bits.append(tag)
                head = f"Recent price movement ({'; '.join(bits)}): " if bits else "Recent price movement: "
                parts.append(head + _cap(narrative, _MAX_REPORT_MODULE))

        # Other visible module insights — each labeled + capped so the chat can ground an answer on
        # ANY section the user sees, not just the price move. Read defensively: a malformed / absent
        # module is skipped, never dropping the rest of the block (the whole resolver runs under one
        # try/except that would otherwise degrade EVERYTHING to ungrounded on a single bad module).
        def _module_line(label: str, key: str, field: str) -> None:
            try:
                blk = report.get(key)
                if isinstance(blk, dict):
                    txt = (blk.get(field) or "").strip()
                    if txt:
                        parts.append(f"{label}: {_cap(txt, _MAX_REPORT_MODULE)}")
            except Exception:  # a single bad module must never nuke the whole grounding block
                pass

        for _label, _key, _field in (
            ("Forward outlook", "revenue_forecast", "insight"),
            ("Earnings track record", "revenue_forecast", "beat_summary"),
            ("Revenue mix", "revenue_engine", "analysis_note"),
            ("Moat", "moat_competition", "competitive_insight"),
            ("Ownership", "key_management", "ownership_insight"),
            ("Wall Street view", "wall_street_consensus", "wall_street_insight"),
        ):
            _module_line(_label, _key, _field)

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
        prof = getattr(detail, "etf_profile", None)
        desc = (getattr(prof, "description", "") or "").strip() if prof else ""
        if desc:
            parts.append("About: " + _cap(desc, _MAX_REPORT_MODULE))
        # Yield & fees — the top ETF question, entirely absent before.
        ny = getattr(detail, "net_yield", None)
        if ny is not None:
            ybits: List[str] = []
            yld = getattr(ny, "dividend_yield", None)
            freq = (getattr(ny, "pay_frequency", "") or "").strip()
            if isinstance(yld, (int, float)):
                ybits.append(f"dividend yield {yld:.2f}%" + (f" (paid {freq.lower()})" if freq else ""))
            exp = getattr(ny, "expense_ratio", None)
            if isinstance(exp, (int, float)):
                ybits.append(f"expense ratio {exp:.2f}%")
            if ybits:
                parts.append("Yield & fees — " + "; ".join(ybits) + ".")
        # Top holdings + sector allocation — the "what's inside it" questions.
        hr = getattr(detail, "holdings_risk", None)
        if hr is not None:
            hold = [
                f"{(getattr(h, 'symbol', '') or getattr(h, 'name', '') or '').strip()} "
                f"{getattr(h, 'weight', 0) or 0:.1f}%".strip()
                for h in (getattr(hr, "top_holdings", None) or [])[:6]
            ]
            hold = [h for h in hold if h and not h.startswith("0.0%")]
            if hold:
                parts.append("Top holdings — " + ", ".join(hold) + ".")
            sec = [
                f"{(getattr(s, 'name', '') or '').strip()} {getattr(s, 'weight', 0) or 0:.1f}%".strip()
                for s in (getattr(hr, "top_sectors", None) or [])[:6]
            ]
            sec = [s for s in sec if s and not s.startswith("0.0%")]
            if sec:
                parts.append("Sector weights — " + ", ".join(sec) + ".")
        stats = [f"{i.label}: {i.value}" for i in (getattr(detail, "key_statistics", None) or [])[:6]]
        if stats:
            parts.append("Key stats — " + "; ".join(stats) + ".")
        perf = [
            f"{(getattr(p, 'label', '') or '').strip()} {getattr(p, 'change_percent', 0) or 0:+.1f}%"
            for p in (getattr(detail, "performance_periods", None) or [])[:5]
        ]
        if perf:
            parts.append("Performance — " + ", ".join(perf) + ".")
        # Asset mix + AUM, concentration risk, dividend schedule, long-run return, volatility, strategy —
        # the rest of the ETF screen a user might ask about.
        aa = getattr(hr, "asset_allocation", None) if hr else None
        if aa is not None:
            mix = [f"{lbl} {v:.0f}%" for lbl, v in (
                ("equities", getattr(aa, "equities", 0)), ("bonds", getattr(aa, "bonds", 0)),
                ("commodities", getattr(aa, "commodities", 0)), ("cash", getattr(aa, "cash", 0)),
            ) if isinstance(v, (int, float)) and v]
            tot = (getattr(aa, "total_assets", "") or "").strip()
            if mix or tot:
                parts.append("Allocation — " + ", ".join(mix) + (f"; AUM {tot}" if tot else "") + ".")
        conc = getattr(hr, "concentration", None) if hr else None
        cinsight = (getattr(conc, "insight", "") or "").strip() if conc else ""
        if cinsight:
            parts.append("Concentration: " + _cap(cinsight, 200))
        ldp = getattr(ny, "last_dividend_payment", None) if ny is not None else None
        dps = (getattr(ldp, "dividend_per_share", "") or "").strip() if ldp is not None else ""
        if dps:
            payd = (getattr(ldp, "pay_date", "") or "").strip()
            parts.append(f"Last dividend {dps}" + (f" paid {payd}" if payd else "") + ".")
        bs = getattr(detail, "benchmark_summary", None)
        ar = getattr(bs, "avg_annual_return", None) if bs is not None else None
        if isinstance(ar, (int, float)):
            spb = getattr(bs, "sp_benchmark", None)
            parts.append(f"Long-run: avg annual return {ar:.1f}%" +
                         (f" vs S&P {spb:.1f}%" if isinstance(spb, (int, float)) else "") + ".")
        ident = getattr(detail, "identity_rating", None)
        vol = (getattr(ident, "volatility_label", "") or "").strip() if ident else ""
        if vol:
            parts.append(f"Volatility: {vol}.")
        strat = getattr(detail, "strategy", None)
        hook = (getattr(strat, "hook", "") or "").strip() if strat else ""
        if hook:
            parts.append(f"Strategy: {hook}")
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
        prof = getattr(detail, "crypto_profile", None)
        desc = (getattr(prof, "description", "") or "").strip() if prof else ""
        if desc:
            parts.append("About: " + _cap(desc, _MAX_REPORT_MODULE))
        # The on-screen AI writeup (Origin & Technology / Tokenomics / Next Big Moves / Risks) — the
        # core analysis the user is reading. Take each category's first paragraphs, capped.
        for snap in (getattr(detail, "snapshots", None) or [])[:4]:
            cat = (getattr(snap, "category", "") or "").strip()
            paras = getattr(snap, "paragraphs", None) or []
            body = " ".join(p.strip() for p in paras if isinstance(p, str) and p.strip())
            if cat and body:
                parts.append(f"{cat}: {_cap(body, 240)}")
        stats: List[str] = []
        for group in (getattr(detail, "key_statistics_groups", None) or []):
            for item in (getattr(group, "statistics", None) or []):
                stats.append(f"{item.label}: {item.value}")
                if len(stats) >= 6:
                    break
            if len(stats) >= 6:
                break
        if stats:
            parts.append("Key stats — " + "; ".join(stats) + ".")
        cbits: List[str] = []
        chain = (getattr(prof, "blockchain", "") or "").strip() if prof else ""
        if chain:
            cbits.append(f"blockchain {chain}")
        consensus = (getattr(prof, "consensus_mechanism", "") or "").strip() if prof else ""
        if consensus:
            cbits.append(f"consensus {consensus}")
        launch = (getattr(prof, "launch_date", "") or "").strip() if prof else ""
        if launch:
            cbits.append(f"launched {launch}")
        if cbits:
            parts.append("Chain — " + ", ".join(cbits) + ".")
        bl = "BTC"
        perf = []
        for p in (getattr(detail, "performance_periods", None) or [])[:5]:
            bl = (getattr(p, "benchmark_label", "") or bl).strip() or bl
            perf.append(f"{(getattr(p, 'label', '') or '').strip()} {getattr(p, 'change_percent', 0) or 0:+.1f}%")
        if perf:
            parts.append(f"Performance (vs {bl}) — " + ", ".join(perf) + ".")
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
        name = (getattr(detail, "index_name", "") or "").strip()
        head = f"The user is viewing the market/index detail screen for {name or symbol}."
        price = getattr(detail, "current_price", None)
        chg = getattr(detail, "price_change_percent", None)
        if isinstance(price, (int, float)) and isinstance(chg, (int, float)):
            head += f" Level {price:,.2f} ({chg:+.2f}%)."
        parts: List[str] = [head]
        prof = getattr(detail, "index_profile", None)
        desc = (getattr(prof, "description", "") or "").strip() if prof else ""
        if desc:
            parts.append("About: " + _cap(desc, _MAX_REPORT_MODULE))
        snap = getattr(detail, "snapshots_data", None)
        val = getattr(snap, "valuation", None) if snap else None
        if val is not None:
            pe = getattr(val, "pe_ratio", None)
            fpe = getattr(val, "forward_pe", None)
            ey = getattr(val, "earnings_yield", None)
            vbits: List[str] = []
            if isinstance(pe, (int, float)) and pe == pe:   # `== pe` skips NaN
                vbits.append(f"P/E {pe:.1f}" + (f" (fwd {fpe:.1f})" if isinstance(fpe, (int, float)) and fpe else ""))
            if isinstance(ey, (int, float)):
                vbits.append(f"earnings yield {ey:.1f}%")
            hpe = getattr(val, "historical_avg_pe", None)
            if isinstance(hpe, (int, float)) and hpe:
                hper = (getattr(val, "historical_period", "") or "").strip()
                vbits.append(f"{hper + ' ' if hper else ''}avg P/E {hpe:.1f}")
            if vbits:
                parts.append("Valuation — " + "; ".join(vbits) + ".")
        # Sector board + macro outlook the user is literally looking at.
        sp = getattr(snap, "sector_performance", None) if snap else None
        sectors = [
            f"{(getattr(s, 'sector', '') or '').strip()} {getattr(s, 'change_percent', 0) or 0:+.1f}%"
            for s in (getattr(sp, "sectors", None) or [])[:8]
        ] if sp else []
        if sectors:
            parts.append("Sectors today — " + ", ".join(sectors) + ".")
        macro = getattr(snap, "macro_forecast", None) if snap else None
        inds = [
            f"{(getattr(m, 'title', '') or '').strip()}: {(getattr(m, 'signal', '') or '').strip()}"
            for m in (getattr(macro, "indicators", None) or [])[:6]
        ] if macro else []
        inds = [i for i in inds if i.strip(": ")]
        if inds:
            parts.append("Macro outlook — " + ", ".join(inds) + ".")
        # How the index is built + its returns — "how many stocks / how is it weighted / how's it done?"
        pbits: List[str] = []
        nc = getattr(prof, "number_of_constituents", None) if prof else None
        if isinstance(nc, int) and nc:
            pbits.append(f"{nc} constituents")
        wm = (getattr(prof, "weighting_methodology", "") or "").strip() if prof else ""
        if wm:
            pbits.append(f"{wm}-weighted")
        provider = (getattr(prof, "index_provider", "") or "").strip() if prof else ""
        if provider:
            pbits.append(f"by {provider}")
        if pbits:
            parts.append("Index — " + ", ".join(pbits) + ".")
        perf = [
            f"{(getattr(p, 'label', '') or '').strip()} {getattr(p, 'change_percent', 0) or 0:+.1f}%"
            for p in (getattr(detail, "performance_periods", None) or [])[:5]
        ]
        if perf:
            parts.append("Performance — " + ", ".join(perf) + ".")
        return _cap(" ".join(parts), _MAX_ASSET)

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
        bits: List[str] = []
        desc = (meta.get("description") or "").strip()
        if desc:
            bits.append(_cap(desc, _MAX_REPORT_MODULE))
        producers = (meta.get("major_producers") or "").strip()
        if producers:
            bits.append(f"Major producers: {producers}.")
        consumers = (meta.get("major_consumers") or "").strip()
        if consumers:
            bits.append(f"Major consumers: {consumers}.")
        tbits: List[str] = []
        cat = (meta.get("category") or "").strip()
        if cat:
            tbits.append(cat)
        exch = (meta.get("exchange") or "").strip()
        if exch:
            tbits.append(f"trades on {exch}")
        hours = (meta.get("trading_hours") or "").strip()
        if hours:
            tbits.append(hours)
        size = (meta.get("contract_size") or "").strip()
        if size:
            tbits.append(f"contract {size}")
        if tbits:
            bits.append("Trading — " + ", ".join(tbits) + ".")
        if not bits:
            return client_context
        profile_block = "Commodity profile — " + " ".join(bits)
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
        parts: List[str] = [
            f'The user is reading the Money Moves article "{article.get("title", "")}"'
            + (f" by {author_name}" if author_name else "") + "."
        ]
        subtitle = (article.get("subtitle") or "").strip()
        if subtitle:
            parts.append(subtitle)
        # keyHighlights items are {icon, title, description} dicts (mirrors the
        # iOS ArticleHighlightDTO), NOT strings — str(dict) would inject a Python
        # dict repr (SF Symbol names + braces) as the article's key points.
        highlights = article.get("keyHighlights") or []
        hl_text: List[str] = []
        for h in highlights[:4]:
            if isinstance(h, dict):
                title = (h.get("title") or "").strip()
                desc = (h.get("description") or "").strip()
                if title and desc:
                    hl_text.append(f"{title} — {desc}")
                elif title or desc:
                    hl_text.append(title or desc)
            elif h:
                hl_text.append(str(h))
        if hl_text:
            parts.append("Key highlights: " + "; ".join(hl_text) + ".")
        # Stat callouts the article surfaces (value + label; trend fields ignored).
        stat_text: List[str] = []
        for s in (article.get("statistics") or [])[:5]:
            if isinstance(s, dict):
                sval = (s.get("value") or "").strip()
                slbl = (s.get("label") or "").strip()
                if sval and slbl:
                    stat_text.append(f"{slbl}: {sval}")
        if stat_text:
            parts.append("Key figures: " + "; ".join(stat_text) + ".")
        # Article BODY — the actual prose the user is reading, so the chat can answer about specific
        # ideas in it (not just the title/highlights). `sections` is opaque JSONB; guard every level.
        # Each block's text is in `text` (paragraph / callout / subheading / quote) or `items`
        # (bulletList); quotes add `attribution`.
        body_parts: List[str] = []
        for section in (article.get("sections") or []):
            if not isinstance(section, dict):
                continue
            stitle = (section.get("title") or "").strip()
            if stitle:
                body_parts.append(stitle + ":")
            for block in (section.get("content") or []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "bulletList":
                    body_parts.extend(
                        i.strip() for i in (block.get("items") or []) if isinstance(i, str) and i.strip()
                    )
                    continue
                txt = (block.get("text") or "").strip()
                if txt:
                    body_parts.append(txt)
                if block.get("type") == "quote":
                    attr = (block.get("attribution") or "").strip()
                    if attr:
                        body_parts.append(f"— {attr}")
        if body_parts:
            parts.append("Article content: " + _cap(" ".join(body_parts), _MAX_ARTICLE_BODY))
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
        # Lesson BODY — the story cards the user is reading, so the chat can answer about the lesson's
        # actual content. `story_content` is Optional (service coerces a non-dict blob → None); each
        # card carries `text` (all 234 cards) + an optional `headline` (title/completion cards only).
        # `text` uses `**bold**` markup — strip it for clean grounding.
        story = get("story_content", None)
        if isinstance(story, dict):
            body_parts: List[str] = []
            for card in (story.get("cards") or []):
                if not isinstance(card, dict):
                    continue
                headline = (card.get("headline") or "").strip()
                if headline:
                    body_parts.append(headline + ":")
                text = (card.get("text") or "").strip().replace("**", "")
                if text:
                    body_parts.append(text)
            if body_parts:
                parts.append("Lesson content: " + _cap(" ".join(body_parts), _MAX_LESSON_BODY))
        parts.append("Answer in the context of this lesson.")
        return _cap(" ".join(parts), _MAX_LESSON)


# ── Module-level singleton (matches every other service) ────────────
_resolver: Optional[ChatContextResolver] = None


def get_chat_context_resolver() -> ChatContextResolver:
    global _resolver
    if _resolver is None:
        _resolver = ChatContextResolver()
    return _resolver
