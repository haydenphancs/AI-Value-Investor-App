"""Web-search-grounded "why did the stock move" for BIG price moves.

The cheap FMP-news keyword catalyst (in `_build_price_action`) was measured
(scripts/eval_price_catalyst.py) at 43% coverage / 35% precision and lost
0-55 head-to-head vs Gemini web-search. So for moves the section already
flags as big (|z| >= 1, decided in `_build_price_action`), the *reason* now
comes from a Gemini web-search-grounded call here.

Mirrors the proven grounded-service pattern in `moat_scoring_service.py`:
  in-memory tier (5 min) → Supabase `price_catalyst_cache` (24h) → grounded
  call (with `_inflight` dedup) → write-through + append-only audit row.

Guarantees:
  * Gated by the caller — only invoked for big moves, so most reports never
    trigger a paid search.
  * Hallucination guard — a specific catalyst is only returned when the
    grounded response carries real source citations; otherwise it degrades to
    "no clear catalyst" (never fabricates a driver).
  * Resilient — retries with backoff and falls back across models
    (2.5-flash → flash-lite → pro) on 503/UNAVAILABLE (learned from the eval).
  * Citations are persisted to `price_catalyst_audit` for a future
    report-detail PDF; they are NOT surfaced in the report view.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.database import get_supabase
from app.integrations.gemini import get_gemini_client

logger = logging.getLogger(__name__)

# Tier-1 in-memory cache + inflight dedup (module scope → shared across reports).
_MEM_TTL_SECONDS = 300
_mem_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}
_inflight: Dict[str, asyncio.Future] = {}

# Rows computed before this are treated as a cache miss (prompt/schema bumps).
PRICE_CATALYST_SCHEMA_FLOOR = datetime(2026, 6, 3, 0, 0, 0, tzinfo=timezone.utc)

# Model fallback chain — the eval showed 2.5-flash gets 503-stormed; flash-lite
# and pro are separate capacity pools. Deduped, primary first.
_MODEL_CHAIN: List[str] = list(dict.fromkeys(
    [settings.GEMINI_MODEL, "gemini-2.5-flash-lite", "gemini-2.5-pro"]
))
_RETRIES_PER_MODEL = 2
_RETRY_BASE_SECONDS = 2.0

_GROUNDED_JSON_FENCE_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)
_NO_CATALYST_LABELS = {"no clear catalyst", "none", "n/a", "no catalyst", "unclear"}

_PROMPT = """You are a financial research analyst. {ticker} moved {change_pct:+.1f}% over {window}.

Using CURRENT web sources, identify the SINGLE most important reason for THIS specific move. Prefer company-specific drivers (earnings, guidance, M&A, FDA/regulatory action, a major contract/customer, a capital raise, an executive change, a product launch, a short-seller report) over generic commentary.

Respond with ONLY a ```json code block:
```json
{{
  "catalyst_tag": "<2-4 word label, e.g. 'Q1 Earnings Beat', 'Raised Guidance', 'FDA Approval', 'Acquisition', 'Capital Raise', 'Analyst Downgrade', 'Sector Selloff'>",
  "reason": "<one factual sentence naming the specific driver>"
}}
```
If there is NO clear company-specific catalyst (the move is broad-market or sector-wide), set "catalyst_tag" to "No Clear Catalyst" and say so in "reason". Cite real, current sources."""


# ── In-memory tier ─────────────────────────────────────────────────────


def _mem_get(ticker: str) -> Optional[Dict[str, Any]]:
    entry = _mem_cache.get(ticker)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _MEM_TTL_SECONDS:
        _mem_cache.pop(ticker, None)
        return None
    return value


def _mem_set(ticker: str, value: Dict[str, Any]) -> None:
    _mem_cache[ticker] = (time.time(), value)


class PriceCatalystService:
    """Grounded web-search reason-finder for big price moves."""

    def __init__(self) -> None:
        self._gemini = None  # lazy

    def _get_gemini(self):
        if self._gemini is None:
            self._gemini = get_gemini_client()
        return self._gemini

    # ── Public entry ───────────────────────────────────────────────────

    async def get_catalyst(
        self,
        ticker: str,
        change_pct: float,
        window_label: str,
        *,
        force_refresh: bool = False,
        run_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return {tag, reason, sources} for a big move, or None on hard
        failure (caller then keeps the deterministic FMP catalyst fallback).

        A "no clear catalyst" outcome is NOT a failure — it returns
        {tag: None, reason: <broad-market explanation>, sources: []} so the
        section trusts the web-search over an FMP keyword guess.
        """
        focal = (ticker or "").strip().upper()
        if not focal:
            return None

        if not force_refresh:
            cached = _mem_get(focal)
            if cached is not None:
                return cached
            db_cached = await asyncio.to_thread(self._read_cache, focal)
            if db_cached is not None:
                _mem_set(focal, db_cached)
                return db_cached

        if focal in _inflight:
            try:
                return await _inflight[focal]
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[focal] = future
        this_run_id = run_id or str(uuid.uuid4())

        try:
            if not getattr(settings, "PRICE_CATALYST_AI_ENABLED", True):
                await asyncio.to_thread(
                    self._write_audit, this_run_id, focal, change_pct, window_label,
                    status="skipped_kill_switch", tag=None, reason=None,
                    sources=[], raw_response=None, search_queries=[],
                    tokens_used=None, model_version=None,
                )
                future.set_result(None)
                return None

            result = await self._do_grounded(focal, change_pct, window_label)
            await asyncio.to_thread(
                self._write_audit, this_run_id, focal, change_pct, window_label,
                status=result["status"], tag=result.get("tag"),
                reason=result.get("reason"), sources=result.get("sources", []),
                raw_response=result.get("raw_response"),
                search_queries=result.get("search_queries", []),
                tokens_used=result.get("tokens_used"),
                model_version=result.get("model_version"),
            )

            if result["status"] == "gemini_error":
                future.set_result(None)
                return None

            served = {
                "tag": result.get("tag"),
                "reason": result.get("reason") or "",
                "sources": result.get("sources", []),
            }
            await asyncio.to_thread(
                self._write_cache, focal, served, result.get("model_version"),
            )
            _mem_set(focal, served)
            future.set_result(served)
            return served
        except Exception as exc:
            logger.exception(
                "price_catalyst: unhandled error for %s: %s", focal, exc,
            )
            if not future.done():
                future.set_exception(exc)
            return None
        finally:
            _inflight.pop(focal, None)
            if not future.done():
                future.set_result(None)

    # ── Grounded call ──────────────────────────────────────────────────

    async def _grounded_with_fallback(self, prompt: str) -> Optional[Dict[str, Any]]:
        """Try each model in the chain with backoff; return the first success
        or None if all are exhausted (503 storms, quota, etc.)."""
        gem = self._get_gemini()
        for model in _MODEL_CHAIN:
            for attempt in range(_RETRIES_PER_MODEL):
                try:
                    return await gem.generate_grounded_research(
                        prompt=prompt, model_name=model, max_output_tokens=8192,
                    )
                except Exception as exc:  # 503/UNAVAILABLE, transient errors
                    last = attempt == _RETRIES_PER_MODEL - 1
                    logger.warning(
                        "price_catalyst: grounded call failed (model=%s attempt=%d): %s",
                        model, attempt, exc,
                    )
                    if last:
                        break  # move to next model
                    await asyncio.sleep(_RETRY_BASE_SECONDS * (2 ** attempt))
        return None

    async def _do_grounded(
        self, ticker: str, change_pct: float, window_label: str,
    ) -> Dict[str, Any]:
        prompt = _PROMPT.format(
            ticker=ticker, change_pct=change_pct,
            window=(window_label or "the recent window").lower(),
        )
        resp = await self._grounded_with_fallback(prompt)
        if not resp:
            return {"status": "gemini_error", "raw_response": {"error": "all models failed"}}

        text = resp.get("text", "") or ""
        grounding = resp.get("grounding_sources") or []
        search_queries = resp.get("search_queries") or []
        tokens = resp.get("tokens_used")
        model_version = resp.get("model")

        base = {
            "raw_response": {"raw_text": text[:1500], "grounding_sources": grounding},
            "search_queries": search_queries,
            "tokens_used": tokens,
            "model_version": model_version,
        }

        match = _GROUNDED_JSON_FENCE_RE.search(text)
        if not match:
            return {**base, "status": "gemini_error"}
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {**base, "status": "gemini_error"}

        tag = (payload.get("catalyst_tag") or "").strip()
        reason = (payload.get("reason") or "").strip()

        # Hallucination guard: only attribute a specific catalyst when the
        # answer is specific AND backed by real source citations.
        specific = (
            tag
            and tag.lower() not in _NO_CATALYST_LABELS
            and len(grounding) > 0
        )
        if specific:
            return {
                **base, "status": "applied", "tag": tag, "reason": reason,
                "sources": grounding,
            }
        return {
            **base, "status": "no_catalyst", "tag": None,
            "reason": reason or "No single company-specific catalyst — broad market or sector move.",
            "sources": [],
        }

    # ── Supabase I/O ───────────────────────────────────────────────────

    def _read_cache(self, ticker: str) -> Optional[Dict[str, Any]]:
        try:
            sb = get_supabase()
            res = (
                sb.table("price_catalyst_cache")
                .select("tag,reason,sources,computed_at,expires_at")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.warning("price_catalyst: cache read failed for %s: %s", ticker, exc)
            return None

        rows = res.data or []
        if not rows:
            return None
        row = rows[0]
        expires_at, computed_at = row.get("expires_at"), row.get("computed_at")
        if not expires_at or not computed_at:
            return None
        try:
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            comp = datetime.fromisoformat(computed_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        if exp <= datetime.now(timezone.utc) or comp < PRICE_CATALYST_SCHEMA_FLOOR:
            return None
        return {
            "tag": row.get("tag"),
            "reason": row.get("reason") or "",
            "sources": row.get("sources") or [],
        }

    def _write_cache(
        self, ticker: str, served: Dict[str, Any], model_version: Optional[str],
    ) -> None:
        now = datetime.now(timezone.utc)
        ttl_hours = getattr(settings, "PRICE_CATALYST_CACHE_TTL_HOURS", 24)
        row = {
            "ticker": ticker,
            "tag": served.get("tag"),
            "reason": served.get("reason"),
            "sources": served.get("sources") or [],
            "model_version": model_version,
            "computed_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=ttl_hours)).isoformat(),
        }
        try:
            sb = get_supabase()
            sb.table("price_catalyst_cache").upsert(row).execute()
        except Exception as exc:
            logger.warning("price_catalyst: cache write failed for %s: %s", ticker, exc)

    def _write_audit(
        self, run_id: str, ticker: str, change_pct: float, window_label: str, *,
        status: str, tag: Optional[str], reason: Optional[str],
        sources: List[Dict[str, Any]], raw_response: Optional[Dict[str, Any]],
        search_queries: List[str], tokens_used: Optional[int],
        model_version: Optional[str],
    ) -> None:
        row = {
            "run_id": run_id,
            "ticker": ticker,
            "status": status,
            "change_pct": change_pct,
            "window_label": window_label,
            "tag": tag,
            "reason": reason,
            "sources": sources or [],
            "raw_response": raw_response,
            "search_queries": search_queries or [],
            "tokens_used": tokens_used,
            "model_version": model_version,
        }
        try:
            sb = get_supabase()
            sb.table("price_catalyst_audit").insert(row).execute()
        except Exception as exc:
            logger.warning("price_catalyst: audit write failed for %s: %s", ticker, exc)


_service_singleton: Optional[PriceCatalystService] = None


def get_price_catalyst_service() -> PriceCatalystService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = PriceCatalystService()
    return _service_singleton
