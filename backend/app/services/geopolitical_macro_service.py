"""Web-search-grounded scan of REAL current geopolitical / macro-shock events
(wars, trade wars, oil shocks, pandemics) for the Macro module.

The Macro module's geopolitical/regulatory factors used to be emitted UNGROUNDED
by Stage A (the AI's training-knowledge guess) — they rendered "Data unavailable"
with a meaningless impact %. This service instead grounds them in CURRENT web
sources via Gemini search (the same engine behind price_catalyst / competitor_
intel / moat_intel).

These events are MARKET-WIDE (identical for every ticker) and PERSISTENT (a war
runs for months), so this is ONE shared, on-demand scan with a long (~7-day)
cache — not a per-ticker call and not a busy daily job. Design vs price_catalyst:
  * keyed on a single market-wide ``scope='global'`` instead of per-ticker,
  * stale-while-revalidate — a stale list is served instantly while a refresh
    runs in the background, so no report ever waits on the grounded call,
  * keep-last-good — a refresh that returns empty/errors NEVER wipes the list
    (a one-off "miss" can't drop an ongoing war).

Citations are persisted (cache + audit + the report payload) for a future
report-detail PDF; they are NOT shown in the report view.
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

_SCOPE = "global"

# Tier-1 in-memory mirror of the shared row (Supabase is the source of truth).
_MEM_TTL_SECONDS = 3600
_mem: Optional[tuple[float, List[Dict[str, Any]]]] = None
_inflight: Dict[str, asyncio.Future] = {}

# Rows computed before this are treated as a cache miss (prompt/schema bumps).
GEOPOLITICAL_SCHEMA_FLOOR = datetime(2026, 6, 6, 0, 0, 0, tzinfo=timezone.utc)

# Model fallback chain — flash gets 503-stormed; flash-lite / pro are separate
# capacity pools. Deduped, primary first.
_MODEL_CHAIN: List[str] = list(dict.fromkeys(
    [settings.GEMINI_MODEL, "gemini-2.5-flash-lite", "gemini-2.5-pro"]
))
_RETRIES_PER_MODEL = 2
_RETRY_BASE_SECONDS = 2.0

_GROUNDED_JSON_FENCE_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)

# severity → impact, kept consistent with the deterministic factors
# (impact = severity ÷ 5). The UI no longer shows impact, but the schema
# requires the field and it keeps sort/threshold logic coherent.
_SEV_IMPACT = {"low": 0.2, "elevated": 0.4, "high": 0.6, "severe": 0.8, "critical": 1.0}
_VALID_SEVERITY = {"elevated", "high", "severe", "critical"}  # "low" is silenced
_VALID_TREND = {"improving", "stable", "worsening"}
# Categories the iOS MacroRiskCategory enum renders; unknown → "geopolitical".
_VALID_CATEGORY = {
    "geopolitical", "tariffs", "regulation", "energy",
    "supply_chain", "inflation", "interest_rates", "currency",
}
# risk_group → sector-β lookup groups (see _MACRO_SENSITIVITY_BY_SECTOR).
_VALID_RISK_GROUP = {
    "geopolitical", "regulation", "oil", "fx", "inflation", "rates",
    "manufacturing", "credit", "tariffs", "supply_chain",
}

_PROMPT = """You are a geopolitical and macro risk analyst. Using CURRENT web sources, list the major geopolitical / macro-shock events RIGHT NOW that materially move global equity markets:
- active wars and armed conflicts,
- trade wars, tariffs, export controls, sanctions,
- energy / oil supply shocks,
- pandemics or major public-health shocks,
- major political, fiscal, or sovereign-debt shocks.

Only include events genuinely material to markets TODAY. Skip minor or stale items. Use "geopolitical" for wars/conflicts/sanctions (even when they affect oil); "tariffs" for trade actions; "regulation" for regulatory/antitrust; "energy" only for a pure energy-supply event.

Respond with ONLY a ```json code block:
```json
{{
  "factors": [
    {{
      "category": "geopolitical|tariffs|regulation|energy|supply_chain|inflation|interest_rates",
      "title": "<3-5 word event name, e.g. 'Russia-Ukraine War', 'US-China Tariffs'>",
      "description": "<one factual sentence: what is happening + why it matters to markets, <=22 words>",
      "severity": "elevated|high|severe|critical",
      "trend": "improving|stable|worsening",
      "risk_group": "geopolitical|tariffs|regulation|oil|fx|inflation|rates|supply_chain"
    }}
  ]
}}
```
List 2-6 factors, most-material first. If there are NO material geopolitical/macro shocks right now, return an empty "factors" array. Cite real, current sources."""


def _mem_get() -> Optional[List[Dict[str, Any]]]:
    global _mem
    if _mem is None:
        return None
    ts, value = _mem
    if time.time() - ts > _MEM_TTL_SECONDS:
        _mem = None
        return None
    return value


def _mem_set(value: List[Dict[str, Any]]) -> None:
    global _mem
    _mem = (time.time(), value)


class GeopoliticalMacroService:
    """Market-wide grounded scan of current geopolitical / macro-shock events."""

    def __init__(self) -> None:
        self._gemini = None  # lazy

    def _get_gemini(self):
        if self._gemini is None:
            self._gemini = get_gemini_client()
        return self._gemini

    # ── Public entry ───────────────────────────────────────────────────

    async def get_geopolitical_factors(
        self, *, force_refresh: bool = False, run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the current grounded geopolitical factor list (market-wide).

        Never raises and never blocks on the grounded call when a usable list
        already exists: a stale list is served immediately while a background
        refresh runs. Returns [] only when there is genuinely nothing cached
        and a cold scan finds nothing (or grounding is disabled).
        """
        if not force_refresh:
            mem = _mem_get()
            if mem is not None:
                return mem

        cached = await asyncio.to_thread(self._read_cache)
        if cached is not None and not force_refresh:
            _mem_set(cached["factors"])
            if cached["fresh"]:
                return cached["factors"]
            # Stale → serve immediately, refresh in the background.
            self._get_or_start_refresh(run_id, cached["factors"])
            return cached["factors"]

        # Cold (no usable row) or forced → await a shared refresh.
        previous = cached["factors"] if cached else []
        try:
            return await self._get_or_start_refresh(run_id, previous)
        except Exception as exc:
            logger.warning("geopolitical_macro: refresh await failed: %s", exc)
            return previous  # keep-last-good

    def _get_or_start_refresh(
        self, run_id: Optional[str], previous: List[Dict[str, Any]],
    ) -> "asyncio.Future":
        """Single shared refresh task — concurrent callers reuse it."""
        fut = _inflight.get(_SCOPE)
        if fut is not None:
            return fut
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._refresh(run_id, previous))
        _inflight[_SCOPE] = task
        task.add_done_callback(lambda _t: _inflight.pop(_SCOPE, None))
        return task

    # ── Refresh ────────────────────────────────────────────────────────

    async def _refresh(
        self, run_id: Optional[str], previous: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        this_run = run_id or str(uuid.uuid4())

        if not getattr(settings, "GEOPOLITICAL_INTEL_AI_ENABLED", True):
            await asyncio.to_thread(
                self._write_audit, this_run, status="skipped_kill_switch",
                factors=[], raw_response=None, search_queries=[],
                tokens_used=None, model_version=None,
            )
            return previous

        try:
            result = await self._do_grounded()
        except Exception as exc:
            logger.exception("geopolitical_macro: grounded call errored: %s", exc)
            result = {"status": "gemini_error", "raw_response": {"error": str(exc)[:300]}}

        status = result["status"]
        factors = result.get("factors") or []

        # keep-last-good: an empty/errored refresh must not wipe a good list.
        audit_status = status
        if status in ("gemini_error", "no_factors") and previous:
            audit_status = "kept_last_good"
        await asyncio.to_thread(
            self._write_audit, this_run, status=audit_status, factors=factors,
            raw_response=result.get("raw_response"),
            search_queries=result.get("search_queries", []),
            tokens_used=result.get("tokens_used"),
            model_version=result.get("model_version"),
        )

        if status == "applied" and factors:
            await asyncio.to_thread(
                self._write_cache, factors, result.get("model_version"),
            )
            _mem_set(factors)
            return factors

        # Genuinely calm (grounded OK, no events) AND nothing prior → cache empty
        # so we don't re-scan every request for the TTL window.
        if status == "no_factors" and not previous:
            await asyncio.to_thread(
                self._write_cache, [], result.get("model_version"),
            )
            _mem_set([])
            return []

        # Otherwise keep-last-good (don't touch the cache → next request retries).
        _mem_set(previous)
        return previous

    async def _grounded_with_fallback(self, prompt: str) -> Optional[Dict[str, Any]]:
        gem = self._get_gemini()
        for model in _MODEL_CHAIN:
            for attempt in range(_RETRIES_PER_MODEL):
                try:
                    return await gem.generate_grounded_research(
                        prompt=prompt, model_name=model,
                        temperature=0.0, max_output_tokens=8192,
                    )
                except Exception as exc:
                    last = attempt == _RETRIES_PER_MODEL - 1
                    logger.warning(
                        "geopolitical_macro: grounded call failed "
                        "(model=%s attempt=%d): %s", model, attempt, exc,
                    )
                    if last:
                        break
                    await asyncio.sleep(_RETRY_BASE_SECONDS * (2 ** attempt))
        return None

    async def _do_grounded(self) -> Dict[str, Any]:
        resp = await self._grounded_with_fallback(_PROMPT)
        if not resp:
            return {"status": "gemini_error", "raw_response": {"error": "all models failed"}}

        text = resp.get("text", "") or ""
        grounding = resp.get("grounding_sources") or []
        base = {
            "raw_response": {"raw_text": text[:2000], "grounding_sources": grounding},
            "search_queries": resp.get("search_queries") or [],
            "tokens_used": resp.get("tokens_used"),
            "model_version": resp.get("model"),
        }

        match = _GROUNDED_JSON_FENCE_RE.search(text)
        if not match:
            return {**base, "status": "gemini_error"}
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return {**base, "status": "gemini_error"}

        # Hallucination guard: only trust factors backed by real citations.
        if not grounding:
            return {**base, "status": "no_factors", "factors": []}

        factors = self._parse_factors(payload.get("factors") or [], grounding)
        if not factors:
            return {**base, "status": "no_factors", "factors": []}
        return {**base, "status": "applied", "factors": factors}

    def _parse_factors(
        self, raw_factors: Any, grounding: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Validate + normalize each grounded factor into the macro-pipeline
        shape. `sources` is the scan-level citation list (shared across the
        factors from this run) — persisted for the future PDF, not shown in UI."""
        if not isinstance(raw_factors, list):
            return []
        out: List[Dict[str, Any]] = []
        for rf in raw_factors:
            if not isinstance(rf, dict):
                continue
            title = (rf.get("title") or "").strip()
            desc = (rf.get("description") or "").strip()
            if not title or not desc:
                continue
            sev = (rf.get("severity") or "").strip().lower()
            if sev not in _VALID_SEVERITY:  # skip "low" / invalid
                continue
            trend = (rf.get("trend") or "stable").strip().lower()
            if trend not in _VALID_TREND:
                trend = "stable"
            cat = (rf.get("category") or "geopolitical").strip().lower().replace(" ", "_")
            if cat not in _VALID_CATEGORY:
                cat = "geopolitical"
            rg = (rf.get("risk_group") or "geopolitical").strip().lower()
            if rg not in _VALID_RISK_GROUP:
                rg = "geopolitical"
            out.append({
                "category": cat,
                "title": title[:80],
                "impact": _SEV_IMPACT[sev],
                "description": desc[:200],
                "trend": trend,
                "severity": sev,
                "sources": grounding,        # public — kept for the PDF
                "_risk_group": rg,           # internal — stripped before response
                "_source": "grounded",       # internal — stripped before response
            })
        return out[:6]

    # ── Supabase I/O ───────────────────────────────────────────────────

    def _read_cache(self) -> Optional[Dict[str, Any]]:
        try:
            sb = get_supabase()
            res = (
                sb.table("geopolitical_macro_cache")
                .select("factors,computed_at,expires_at")
                .eq("scope", _SCOPE)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.warning("geopolitical_macro: cache read failed: %s", exc)
            return None

        rows = res.data or []
        if not rows:
            return None
        row = rows[0]
        computed_at, expires_at = row.get("computed_at"), row.get("expires_at")
        if not computed_at or not expires_at:
            return None
        try:
            comp = datetime.fromisoformat(computed_at.replace("Z", "+00:00"))
            exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        if comp < GEOPOLITICAL_SCHEMA_FLOOR:
            return None  # schema bump → force a re-scan
        return {"factors": row.get("factors") or [], "fresh": exp > datetime.now(timezone.utc)}

    def _write_cache(
        self, factors: List[Dict[str, Any]], model_version: Optional[str],
    ) -> None:
        now = datetime.now(timezone.utc)
        ttl_days = getattr(settings, "GEOPOLITICAL_CACHE_TTL_DAYS", 7)
        row = {
            "scope": _SCOPE,
            "factors": factors,
            "model_version": model_version,
            "computed_at": now.isoformat(),
            "expires_at": (now + timedelta(days=ttl_days)).isoformat(),
        }
        try:
            sb = get_supabase()
            sb.table("geopolitical_macro_cache").upsert(row).execute()
        except Exception as exc:
            logger.warning("geopolitical_macro: cache write failed: %s", exc)

    def _write_audit(
        self, run_id: str, *, status: str, factors: List[Dict[str, Any]],
        raw_response: Optional[Dict[str, Any]], search_queries: List[str],
        tokens_used: Optional[int], model_version: Optional[str],
    ) -> None:
        row = {
            "run_id": run_id,
            "status": status,
            "factor_count": len(factors or []),
            "factors": factors or [],
            "raw_response": raw_response,
            "search_queries": search_queries or [],
            "tokens_used": tokens_used,
            "model_version": model_version,
        }
        try:
            sb = get_supabase()
            sb.table("geopolitical_macro_audit").insert(row).execute()
        except Exception as exc:
            logger.warning("geopolitical_macro: audit write failed: %s", exc)


_service_singleton: Optional[GeopoliticalMacroService] = None


def get_geopolitical_macro_service() -> GeopoliticalMacroService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = GeopoliticalMacroService()
    return _service_singleton
