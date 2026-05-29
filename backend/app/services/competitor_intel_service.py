"""Competitor intel — revenue-mix-aware peer selection (Phase 2).

The TickerReport Moat & Competitors section's peer source is FMP's
`/stock-peers` augmented from `data/industry_universe.json` (Phase 1).
That deterministic path is structurally too narrow because FMP's
`industry` field reflects a company's PRIMARY classification, not its
revenue-mix overlap with another company. For Oracle, the real
competitors — Microsoft, Amazon (AWS), Salesforce, SAP, IBM, Adobe,
Google (GCP), Broadcom (post-VMware) — span 4-5 different FMP
industries.

Phase 2: ask Gemini, with Google Search grounding, to identify
competitors based on overlapping revenue mix. Validate every returned
ticker against FMP `/profile` (no unknown / fabricated symbols). Trim
to 7 only when Gemini returns more than 7 — below that, take the full
4-6 list as-is (Gemini's grounded research uses 10-Ks and earnings
calls; a $10B niche rival with verifiable revenue overlap is a real
competitor, regardless of mkt-cap delta to the focal).

Architecture mirrors industry_override_service (Phase B of the dossier
pipeline):
  * Same quarterly schedule (first Sunday Jan/Apr/Jul/Oct 02:00 UTC),
    chained after industry_dossier_service.recompute_all() in main.py.
  * Same audit-log discipline — every Gemini extraction leaves a row
    in competitor_intel_audit, with raw response, suggested tickers,
    validated survivors, rejections, tokens, model version.
  * Same anti-fabrication guardrail — no ticker survives without an
    FMP profile resolution + positive mktCap.

In addition (because Phase 2 has an on-demand per-request path that
Phase B doesn't): two-tier cache (in-memory dict + Supabase
competitor_intel_cache) + `_inflight` dedup so a thundering herd of
concurrent ticker-report requests doesn't trigger duplicate Gemini
calls for the same ticker.

Cache TTL ≈ 100 days — competitors are stable quarter-over-quarter,
and the quarterly batch on the first Sunday of each quarter overwrites
the row before it expires.

Kill switch: `settings.COMPETITOR_INTEL_AI_ENABLED = False` skips the
Gemini call. Callers see None → fall back to the Phase 1 deterministic
peer-augmentation path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.integrations.gemini import get_gemini_client

logger = logging.getLogger(__name__)


# ── Configuration constants ────────────────────────────────────────────

_COMPETITOR_MAX_N = 7              # iOS renders up to 7 competitor rows
_CACHE_TTL_DAYS = 100              # one quarter + ~7 day safety margin
_IN_MEM_TTL_SECONDS = 300          # 5-minute in-memory dedup tier
_BATCH_TOP_N = 500                 # top-N watchlisted tickers per quarterly run
_BATCH_CONCURRENCY = 5             # concurrent Gemini calls during batch
_GEMINI_MAX_OUTPUT_TOKENS = 8192


# Schema floor — bump when prompt logic / validation rules change in a
# way that makes pre-existing cached rows semantically stale. Rows with
# `computed_at < COMPETITOR_INTEL_SCHEMA_FLOOR` are treated as cache miss
# even if their `expires_at` is in the future.
COMPETITOR_INTEL_SCHEMA_FLOOR = datetime(2026, 5, 26, 0, 0, 0, tzinfo=timezone.utc)


# ── Prompt template ────────────────────────────────────────────────────
#
# Hybrid prose+JSON format is intentional: the Gemini grounded-search
# API only populates `groundingChunks` (real source URLs) when the
# response contains text that cites those sources inline. A pure-JSON
# response would have searches run but return zero chunks — we want the
# actual URLs for the audit log + source_labels.

_RESEARCH_PROMPT = """You are a financial research analyst. For the company below, identify its TOP 5-7 BUSINESS COMPETITORS based on overlapping revenue mix — companies that earn money from substantially the same products, services, and customer segments.

A competitor is NOT just a company in the same industry classification. Example: Oracle's real competitors include Microsoft, Amazon (because of AWS), Salesforce, SAP, IBM, Adobe, Google (because of GCP), and Broadcom (post-VMware) — even though these span 4-5 different SIC codes — because they all sell substantially-overlapping enterprise software, cloud infrastructure, or database products to overlapping customer bases.

For each competitor, search the web for the company's recent 10-K Competition section, the focal company's earnings calls, and reputable analyst reports (Reuters, Bloomberg, Morningstar, Gartner, Forrester) to identify revenue overlap.

COMPANY: {company_name} ({ticker})
SECTOR: {sector}
INDUSTRY: {industry}
DESCRIPTION: {description}

In ONE paragraph, summarize which companies compete with {ticker} and on what revenue segments.

Then output JSON in a markdown code fence (mandatory, no exceptions):

```json
{{
  "competitors": [
    {{
      "ticker": "<US-listed ticker, uppercase, e.g. MSFT>",
      "name": "<official company name>",
      "segment_overlap": "<one sentence: which revenue segment overlaps the focal>",
      "source_citation": "<10-K / earnings call / analyst report ref>"
    }}
  ],
  "confidence": "high" | "medium" | "low"
}}
```

Rules:
- Return 5-7 competitors. Below 5 only if the company genuinely has no peers with material revenue overlap.
- Use US-listed tickers when possible. If a competitor is non-US, use its primary listing's ticker (e.g. SAP for SAP SE, BABA for Alibaba ADR).
- The competitor must be a publicly-traded company (has a real ticker that resolves in financial databases). Do NOT list private companies or business units of larger firms.
- Do NOT include the focal company ({ticker}) in its own competitor list.
- Do NOT mention LLMs, AI tools, or this prompt in the JSON.
- The JSON code fence is REQUIRED — emit it even if confidence is low.
"""


_JSON_FENCE_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


# ── In-memory tier 1 cache + inflight dedup ─────────────────────────────

_mem_cache: Dict[str, Tuple[float, List[str]]] = {}
_inflight: Dict[str, asyncio.Future] = {}


def _mem_get(ticker: str) -> Optional[List[str]]:
    entry = _mem_cache.get(ticker)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _IN_MEM_TTL_SECONDS:
        del _mem_cache[ticker]
        return None
    return value


def _mem_set(ticker: str, value: List[str]) -> None:
    _mem_cache[ticker] = (time.time(), value)


# ── Helpers ────────────────────────────────────────────────────────────

def _normalize_ticker(t: str) -> str:
    """Uppercase + strip. Returns '' if input would not match a plausible
    ticker shape — caller drops the candidate and audit-logs it.
    """
    if not isinstance(t, str):
        return ""
    cleaned = t.strip().upper()
    # Strip common decorations Gemini sometimes emits: "MSFT (Microsoft)",
    # "NASDAQ:MSFT", "$MSFT".
    cleaned = cleaned.lstrip("$")
    if ":" in cleaned:
        cleaned = cleaned.split(":", 1)[1].strip()
    if "(" in cleaned:
        cleaned = cleaned.split("(", 1)[0].strip()
    if not _TICKER_RE.match(cleaned):
        return ""
    return cleaned


def _derive_source_label(grounding_sources: List[Dict[str, Any]]) -> List[str]:
    """De-duplicate publisher names from the grounded-search response,
    capitalize, return up to 4. Mirrors the helper at
    industry_override_service._derive_source_label but returns a list
    (the cache column is TEXT[]) rather than a single joined string.
    """
    seen: List[str] = []
    for s in grounding_sources or []:
        if not isinstance(s, dict):
            continue
        pub = str(s.get("publisher") or "").strip()
        if not pub:
            continue
        pretty = pub[:1].upper() + pub[1:]
        if pretty not in seen:
            seen.append(pretty)
    return seen[:4]


# ── Data class ─────────────────────────────────────────────────────────


@dataclass
class CompetitorResult:
    """Outcome of one ticker's Phase 2 extraction. Populated regardless
    of success — written to the audit table by `_write_audit_row`.
    """
    ticker: str
    status: str  # see CHECK constraint in migration 054
    suggested_tickers: List[str] = field(default_factory=list)
    validated_tickers: List[str] = field(default_factory=list)
    rejected: List[Dict[str, str]] = field(default_factory=list)
    source_labels: List[str] = field(default_factory=list)
    raw_response: Optional[Dict[str, Any]] = None
    tokens_used: Optional[int] = None
    model_version: Optional[str] = None


# ── Service ────────────────────────────────────────────────────────────


class CompetitorIntelService:

    def __init__(self) -> None:
        self._gemini = None  # lazy
        self._fmp = None     # lazy

    def _get_gemini(self):
        if self._gemini is None:
            self._gemini = get_gemini_client()
        return self._gemini

    def _get_fmp(self):
        if self._fmp is None:
            self._fmp = get_fmp_client()
        return self._fmp

    # ── Public API ──────────────────────────────────────────────────

    async def get_competitors(
        self,
        ticker: str,
        profile: Dict[str, Any],
        *,
        force_refresh: bool = False,
        run_id: Optional[str] = None,
    ) -> Optional[List[str]]:
        """Returns up to 7 validated competitor tickers (sorted by mktCap
        desc). Returns None on hard failure — caller falls back to the
        deterministic Phase 1 path.

        Two-tier cache + in-flight dedup. Honors the kill switch
        (`COMPETITOR_INTEL_AI_ENABLED=False` → returns None without a
        Gemini call but still writes an audit row).
        """
        focal = _normalize_ticker(ticker)
        if not focal:
            logger.warning("competitor_intel: invalid ticker %r", ticker)
            return None

        # ── Tier 1: in-memory ──
        if not force_refresh:
            cached = _mem_get(focal)
            if cached is not None:
                return cached

        # ── Tier 2: Supabase ──
        if not force_refresh:
            db_cached = await asyncio.to_thread(self._read_cache, focal)
            if db_cached is not None:
                _mem_set(focal, db_cached)
                return db_cached

        cache_key = focal

        # ── Inflight dedup ──
        if cache_key in _inflight:
            try:
                return await _inflight[cache_key]
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            # ── Kill switch ──
            if not getattr(settings, "COMPETITOR_INTEL_AI_ENABLED", True):
                result = CompetitorResult(
                    ticker=focal, status="skipped_kill_switch",
                )
                await asyncio.to_thread(
                    self._write_audit_row, run_id or str(uuid.uuid4()), result,
                )
                future.set_result(None)
                return None

            # ── Gemini extraction + validation ──
            result = await self._extract_and_validate(focal, profile)
            this_run_id = run_id or str(uuid.uuid4())
            await asyncio.to_thread(self._write_audit_row, this_run_id, result)

            if result.status in ("applied", "applied_with_rejections"):
                await asyncio.to_thread(
                    self._write_cache, focal, result.validated_tickers,
                    result.source_labels, result.model_version,
                )
                _mem_set(focal, result.validated_tickers)
                future.set_result(result.validated_tickers)
                return result.validated_tickers

            future.set_result(None)
            return None
        except Exception as exc:
            logger.exception(
                "competitor_intel: unhandled error for %s: %s", focal, exc,
            )
            future.set_exception(exc)
            return None
        finally:
            _inflight.pop(cache_key, None)
            # Safety belt — if the future was never resolved (producer was
            # cancelled mid-flight, e.g. client disconnected and FastAPI
            # cancelled the handler), tell awaiting callers there's no
            # result so they fall back to the Phase-1 deterministic path
            # instead of hanging on an orphaned future forever.
            if not future.done():
                future.set_result(None)

    async def refresh_top_tickers(
        self,
        top_n: int = _BATCH_TOP_N,
    ) -> Dict[str, Any]:
        """Quarterly batch: refresh the top-N most-watchlisted tickers.
        Run Pass 1 over all of them, then Pass 2 retries any that hit
        `gemini_error` or `rejected_no_validated`.

        Returns a one-line-loggable summary dict.
        """
        run_id = str(uuid.uuid4())
        started = time.time()

        if not getattr(settings, "COMPETITOR_INTEL_AI_ENABLED", True):
            logger.info(
                "competitor_intel: kill switch enabled — skipping quarterly batch"
            )
            return {
                "run_id": run_id, "skipped": True, "ran": 0,
                "applied": 0, "applied_after_retry": 0,
                "still_failing": 0, "total_tokens": 0,
                "elapsed_seconds": 0,
            }

        tickers = await asyncio.to_thread(self._load_top_watchlist_tickers, top_n)
        if not tickers:
            logger.warning("competitor_intel: no tickers from watchlist; skipping batch")
            return {
                "run_id": run_id, "ran": 0, "applied": 0,
                "applied_after_retry": 0, "still_failing": 0,
                "total_tokens": 0, "elapsed_seconds": 0,
            }

        logger.info(
            "competitor_intel: quarterly batch starting — run_id=%s, tickers=%d",
            run_id, len(tickers),
        )

        sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

        async def _run_one(t: str) -> Tuple[str, Optional[List[str]]]:
            async with sem:
                # We need the focal profile for the prompt — fetch it as
                # part of this work unit so the orchestrator stays simple.
                profile = await self._safe_fetch_profile(t)
                if profile is None:
                    return (t, None)
                companies = await self.get_competitors(
                    t, profile, force_refresh=True, run_id=run_id,
                )
                return (t, companies)

        pass1_results = await asyncio.gather(
            *[_run_one(t) for t in tickers], return_exceptions=True,
        )

        applied_pass1 = sum(
            1 for r in pass1_results
            if isinstance(r, tuple) and r[1] is not None and len(r[1]) > 0
        )

        # ── Pass 2: retry the zeros ──
        # Anything that came back None in pass 1 gets one more shot.
        # Transient Gemini hiccups and grounding misses are the target.
        failed_tickers = [
            r[0] for r in pass1_results
            if isinstance(r, tuple) and (r[1] is None or len(r[1]) == 0)
        ]
        applied_after_retry = 0
        if failed_tickers:
            logger.info(
                "competitor_intel: pass 2 retrying %d zero-result tickers",
                len(failed_tickers),
            )
            pass2_results = await asyncio.gather(
                *[_run_one(t) for t in failed_tickers], return_exceptions=True,
            )
            applied_after_retry = sum(
                1 for r in pass2_results
                if isinstance(r, tuple) and r[1] is not None and len(r[1]) > 0
            )

        still_failing = len(failed_tickers) - applied_after_retry
        total_tokens = await asyncio.to_thread(self._sum_tokens_for_run, run_id)

        summary = {
            "run_id": run_id,
            "ran": len(tickers),
            "applied": applied_pass1,
            "applied_after_retry": applied_after_retry,
            "still_failing": still_failing,
            "total_tokens": total_tokens,
            "elapsed_seconds": round(time.time() - started, 1),
        }
        logger.info("competitor_intel quarterly batch summary: %s", summary)
        return summary

    # ── Internal: extraction + validation ─────────────────────────────

    async def _extract_and_validate(
        self,
        ticker: str,
        profile: Dict[str, Any],
    ) -> CompetitorResult:
        """One Gemini call + FMP validation. Always returns a
        CompetitorResult (never raises); errors go into the result's
        status / rejection_reason for audit-log fidelity.
        """
        company_name = (profile or {}).get("companyName") or ticker
        sector = (profile or {}).get("sector") or "Unknown"
        industry = (profile or {}).get("industry") or "Unknown"
        description = ((profile or {}).get("description") or "")[:600]

        prompt = _RESEARCH_PROMPT.format(
            ticker=ticker,
            company_name=company_name,
            sector=sector,
            industry=industry,
            description=description,
        )

        gem = self._get_gemini()
        try:
            gemini_response = await gem.generate_grounded_research(
                prompt=prompt,
                max_output_tokens=_GEMINI_MAX_OUTPUT_TOKENS,
            )
        except Exception as exc:
            return CompetitorResult(
                ticker=ticker, status="gemini_error",
                raw_response={"error": f"{type(exc).__name__}: {exc}"},
            )

        text = gemini_response.get("text", "") or ""
        grounding = gemini_response.get("grounding_sources") or []
        search_queries = gemini_response.get("search_queries") or []
        tokens = gemini_response.get("tokens_used")
        model_version = gemini_response.get("model")

        # JSON code-fence extraction.
        match = _JSON_FENCE_RE.search(text)
        if not match:
            return CompetitorResult(
                ticker=ticker, status="gemini_error",
                raw_response={
                    "raw_text": text[:1500],
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                    "error": "no ```json``` code fence",
                },
                tokens_used=tokens, model_version=model_version,
            )

        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            return CompetitorResult(
                ticker=ticker, status="gemini_error",
                raw_response={
                    "raw_json": match.group(1)[:1500],
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                    "error": f"json parse: {exc}",
                },
                tokens_used=tokens, model_version=model_version,
            )

        suggested_raw = payload.get("competitors") or []
        if not isinstance(suggested_raw, list):
            return CompetitorResult(
                ticker=ticker, status="gemini_error",
                raw_response={
                    "payload": payload,
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                    "error": "'competitors' is not a list",
                },
                tokens_used=tokens, model_version=model_version,
            )

        # Normalize suggested tickers + drop self / blanks / shape junk.
        suggested: List[str] = []
        rejected: List[Dict[str, str]] = []
        for entry in suggested_raw:
            if not isinstance(entry, dict):
                continue
            raw_t = entry.get("ticker") or ""
            norm = _normalize_ticker(raw_t)
            if not norm:
                rejected.append({"ticker": str(raw_t), "reason": "bad_ticker_shape"})
                continue
            if norm == ticker:
                rejected.append({"ticker": norm, "reason": "is_focal"})
                continue
            if norm in suggested:
                continue  # Gemini occasionally repeats; silently dedupe
            suggested.append(norm)

        if not suggested:
            return CompetitorResult(
                ticker=ticker, status="rejected_no_validated",
                suggested_tickers=[],
                rejected=rejected,
                source_labels=_derive_source_label(grounding),
                raw_response={
                    "payload": payload,
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                },
                tokens_used=tokens, model_version=model_version,
            )

        # FMP validation — every survivor must resolve to a profile with
        # positive mktCap. No $27.3B floor here; trust Gemini's
        # authoritative selection for revenue-mix-aware competitors.
        validated, validation_rejected = await self._fmp_validate(suggested, ticker)
        rejected.extend(validation_rejected)

        # Preserve Gemini's order — earlier-suggested competitors are
        # more central per the grounded research (revenue-mix overlap is
        # the prompt's primary criterion). Position in the input list
        # IS the priority signal; do NOT re-sort by mktCap or we erase
        # the rank that downstream scoring needs. `validated` is already
        # in Gemini order because `_fmp_validate` iterates `candidates`
        # (suggested list) and appends in order.
        #
        # When Gemini returns more than `_COMPETITOR_MAX_N`, keep the
        # top-N by Gemini rank and drop the tail with a clear rejection
        # reason so the audit row makes the trimming decision traceable.
        trimmed_off: List[Dict[str, str]] = []
        if len(validated) > _COMPETITOR_MAX_N:
            kept = validated[:_COMPETITOR_MAX_N]
            dropped = validated[_COMPETITOR_MAX_N:]
            validated = kept
            for d in dropped:
                trimmed_off.append({
                    "ticker": d["ticker"],
                    "reason": f"trimmed_to_{_COMPETITOR_MAX_N}_by_gemini_rank",
                })

        rejected.extend(trimmed_off)
        survivor_tickers = [v["ticker"] for v in validated]

        if not survivor_tickers:
            return CompetitorResult(
                ticker=ticker, status="rejected_no_validated",
                suggested_tickers=suggested,
                rejected=rejected,
                source_labels=_derive_source_label(grounding),
                raw_response={
                    "payload": payload,
                    "grounding_sources": grounding,
                    "search_queries": search_queries,
                },
                tokens_used=tokens, model_version=model_version,
            )

        status = "applied" if not rejected else "applied_with_rejections"
        return CompetitorResult(
            ticker=ticker, status=status,
            suggested_tickers=suggested,
            validated_tickers=survivor_tickers,
            rejected=rejected,
            source_labels=_derive_source_label(grounding),
            raw_response={
                "payload": payload,
                "grounding_sources": grounding,
                "search_queries": search_queries,
            },
            tokens_used=tokens, model_version=model_version,
        )

    async def _fmp_validate(
        self,
        candidates: List[str],
        focal: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
        """For each candidate ticker, fetch FMP profile and check
        mktCap > 0. Returns (validated, rejected). Validated entries are
        dicts with `ticker` + `mkt_cap` (for downstream sort/trim).
        """
        if not candidates:
            return [], []

        fmp = self._get_fmp()
        try:
            profiles = await fmp.get_company_profiles_batch(candidates)
        except Exception as exc:
            logger.warning(
                "competitor_intel: get_company_profiles_batch failed: %s", exc,
            )
            # Fall back to per-ticker fetches; this still gives us partial
            # results rather than rejecting the whole batch.
            profiles = []
            for c in candidates:
                try:
                    p = await fmp.get_company_profile(c)
                    if p:
                        profiles.append(p)
                except Exception:
                    pass

        by_symbol: Dict[str, Dict[str, Any]] = {}
        for p in profiles or []:
            if not isinstance(p, dict):
                continue
            sym = (p.get("symbol") or "").upper()
            if sym:
                by_symbol[sym] = p

        validated: List[Dict[str, Any]] = []
        rejected: List[Dict[str, str]] = []
        for t in candidates:
            profile = by_symbol.get(t)
            if profile is None:
                rejected.append({"ticker": t, "reason": "rejected_unknown_ticker"})
                continue
            try:
                mkt_cap = float(profile.get("mktCap") or 0.0)
            except (TypeError, ValueError):
                mkt_cap = 0.0
            if mkt_cap <= 0:
                rejected.append({"ticker": t, "reason": "rejected_no_mktcap"})
                continue
            validated.append({"ticker": t, "mkt_cap": mkt_cap})

        return validated, rejected

    async def _safe_fetch_profile(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Used by the batch path to fetch the focal profile. Swallows
        errors and returns None so one bad ticker doesn't blow up the
        batch.
        """
        try:
            fmp = self._get_fmp()
            return await fmp.get_company_profile(ticker)
        except Exception as exc:
            logger.warning(
                "competitor_intel: profile fetch failed for %s: %s", ticker, exc,
            )
            return None

    # ── Supabase I/O ──────────────────────────────────────────────────

    def _read_cache(self, ticker: str) -> Optional[List[str]]:
        """Synchronous Supabase read (called via asyncio.to_thread)."""
        try:
            sb = get_supabase()
            res = (
                sb.table("competitor_intel_cache")
                .select("competitor_tickers,computed_at,expires_at")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.warning("competitor_intel: cache read failed for %s: %s", ticker, exc)
            return None

        rows = res.data or []
        if not rows:
            return None
        row = rows[0]
        expires_at = row.get("expires_at")
        computed_at = row.get("computed_at")
        if not expires_at or not computed_at:
            return None
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            comp_dt = datetime.fromisoformat(computed_at.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
        now = datetime.now(timezone.utc)
        if exp_dt <= now:
            return None
        if comp_dt < COMPETITOR_INTEL_SCHEMA_FLOOR:
            return None
        tickers = row.get("competitor_tickers") or []
        if not isinstance(tickers, list) or not tickers:
            return None
        return [str(t) for t in tickers]

    def _write_cache(
        self,
        ticker: str,
        validated: List[str],
        source_labels: List[str],
        model_version: Optional[str],
    ) -> None:
        """Synchronous upsert (called via asyncio.to_thread)."""
        if not validated:
            return
        now = datetime.now(timezone.utc)
        row = {
            "ticker": ticker,
            "competitor_tickers": validated,
            "source_labels": source_labels or [],
            "computed_at": now.isoformat(),
            "expires_at": (now + timedelta(days=_CACHE_TTL_DAYS)).isoformat(),
            "model_version": model_version,
        }
        try:
            sb = get_supabase()
            sb.table("competitor_intel_cache").upsert(row).execute()
        except Exception as exc:
            logger.warning("competitor_intel: cache write failed for %s: %s", ticker, exc)

    def _write_audit_row(self, run_id: str, result: CompetitorResult) -> None:
        """Synchronous audit log insert (called via asyncio.to_thread).
        Never raises — audit-log failures should not propagate to user
        requests.
        """
        row = {
            "run_id": run_id,
            "ticker": result.ticker,
            "status": result.status,
            "raw_response": result.raw_response,
            "suggested_tickers": result.suggested_tickers,
            "validated_tickers": result.validated_tickers,
            "rejected": result.rejected,
            "source_labels": result.source_labels,
            "tokens_used": result.tokens_used,
            "model_version": result.model_version,
        }
        try:
            sb = get_supabase()
            sb.table("competitor_intel_audit").insert(row).execute()
        except Exception as exc:
            logger.warning(
                "competitor_intel: audit log write failed for %s: %s",
                result.ticker, exc,
            )

    def _sum_tokens_for_run(self, run_id: str) -> int:
        try:
            sb = get_supabase()
            res = (
                sb.table("competitor_intel_audit")
                .select("tokens_used")
                .eq("run_id", run_id)
                .execute()
            )
        except Exception:
            return 0
        total = 0
        for row in res.data or []:
            t = row.get("tokens_used")
            if isinstance(t, (int, float)):
                total += int(t)
        return total

    def _load_top_watchlist_tickers(self, limit: int) -> List[str]:
        """Top-N tickers across `watchlist_items` by user-occurrence
        count. Uses the Supabase SDK's group-by via a rpc call would be
        ideal, but a plain SELECT + Python aggregation is enough for
        ~10k watchlist rows.
        """
        try:
            sb = get_supabase()
            res = (
                sb.table("watchlist_items")
                .select("ticker")
                .limit(50_000)
                .execute()
            )
        except Exception as exc:
            logger.warning(
                "competitor_intel: failed to read watchlist_items: %s", exc,
            )
            return []
        counts: Dict[str, int] = {}
        for row in res.data or []:
            t = (row.get("ticker") or "").upper().strip()
            if not t:
                continue
            counts[t] = counts.get(t, 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [t for t, _ in ranked[:limit]]


# ── Singleton ──────────────────────────────────────────────────────────


_service_singleton: Optional[CompetitorIntelService] = None


def get_competitor_intel_service() -> CompetitorIntelService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = CompetitorIntelService()
    return _service_singleton


# Convenience module-level callable for the data collector.
async def get_competitors(
    ticker: str,
    profile: Dict[str, Any],
    *,
    force_refresh: bool = False,
) -> Optional[List[str]]:
    return await get_competitor_intel_service().get_competitors(
        ticker, profile, force_refresh=force_refresh,
    )
