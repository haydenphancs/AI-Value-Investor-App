"""Industry dossier — Phase B research overrides.

Runs right after `industry_dossier_service.recompute_all()` (Phase A,
Census/FRED-based) on the quarterly schedule. For a small curated list
of globally-traded industries where Census US-domestic measurements
dramatically undercount the global addressable market (semis, biotech,
pharma, medical devices, autos, defense, internet content), this
service:

  1. Asks Gemini for the current global TAM + 5y forward CAGR with
     instructions to cite ≥2 authoritative sources (SIA, IQVIA,
     McKinsey, Grand View Research, etc.) and return structured JSON.
  2. Runs validation gates on the response (bounds, sources, sanity
     vs Phase A).
  3. Writes accepted overrides to `industry_dossier` (overwrites the
     Phase A row for that industry).
  4. Logs every attempt — accept or reject — to
     `industry_override_audit` (the breadcrumb trail).

The LLM is treated as a research synthesis tool, not a primary source.
The `source_label` in the user-facing dossier cites the underlying
public sources Gemini found (per the project's LLM identity rule —
users never see "AI" / "Gemini" / "Google" in attribution).

Kill switch: `settings.INDUSTRY_OVERRIDE_AI_ENABLED = False` skips this
phase entirely. Each curated industry is logged with
status='skipped_kill_switch' so the audit log shows the run happened.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.database import get_supabase
from app.integrations.gemini import get_gemini_client
from app.services.industry_dossier_service import (
    IndustryDossier,
    classify_lifecycle,
    get_industry_dossier_service,
)

logger = logging.getLogger(__name__)


# ── Curated override list ──────────────────────────────────────────────
#
# Selection criteria (all must be true to add an industry here):
#   1. Census NAICS measures US-domestic activity only, while the FMP
#      industry's companies compete globally.
#   2. Census 2017→2023 CAGR significantly undercounts the industry's
#      actual outlook (a known inflection happened post-Census window).
#   3. ≥2 credible public research sources with numbers consistent
#      within ±30%.
#   4. Industry has ≥20 active tickers OR contains a top-10-market-cap
#      company.
#
# To add a new industry: edit this constant + redeploy. Next quarterly
# job picks it up. Also reachable via the admin endpoint for ad-hoc
# runs without waiting for the quarterly schedule.

CURATED_OVERRIDE_INDUSTRIES: List[Tuple[str, str]] = [
    # Original 5 — Census US-domestic dramatically undercounts global market
    ("Semiconductors", "Technology"),
    ("Biotechnology", "Healthcare"),
    ("Drug Manufacturers - General", "Healthcare"),
    ("Drug Manufacturers - Specialty & Generic", "Healthcare"),
    ("Medical - Devices", "Healthcare"),
    # Additional 4 — same pattern, justified by top-10-mcap or sector inflection
    ("Medical - Instruments & Supplies", "Healthcare"),
    ("Auto - Manufacturers", "Consumer Cyclical"),
    ("Aerospace & Defense", "Industrials"),
    ("Internet Content & Information", "Communication Services"),
]


# ── Validation thresholds ──────────────────────────────────────────────
#
# Bounds chosen to catch obvious hallucinations without rejecting
# legitimate edge cases. Any out-of-bounds value triggers fallback to
# Phase A (Census/FRED stays).

_MIN_TAM_B = 1.0            # under $1B → suspect (most listed industries are bigger)
_MAX_TAM_B = 50_000.0       # over $50T → impossible (>2x US GDP)
_MAX_TAM_MULT = 5.0         # future_tam can't exceed 5x current (would imply >38% CAGR)
_MIN_CAGR_PCT = -10.0       # industries don't shrink faster than this without being "declining"
_MAX_CAGR_PCT = 50.0        # +50% CAGR is the upper bound of any sustained industry growth

# Divergence vs Phase A's existing Census/FRED TAM
_WARN_DIVERGE_RATIO = 3.0   # log warning + still apply
_REJECT_DIVERGE_RATIO = 10.0  # reject; Phase A stays


# ── Prompt template ────────────────────────────────────────────────────
#
# Hybrid prose+JSON format is intentional: the Gemini grounded-search API
# only populates `groundingChunks` (real source URLs) when the response
# contains text that cites those sources inline. A pure-JSON response
# would have searches run but return zero chunks — and we want the actual
# URLs for the audit log, not Gemini self-reporting which can hallucinate.

_RESEARCH_PROMPT = """You are a financial-research analyst. Research the GLOBAL market for: {industry} (parent sector: {sector}).

STEP 1 — Search the web for current global TAM (2024 or 2025) and 5-year forward CAGR. Use industry associations (SIA, WSTS, PhRMA), consulting firms (McKinsey, Deloitte, BCG, PwC), and market-research firms (IQVIA, Grand View Research, MarketsandMarkets, Statista, Mordor Intelligence, Frost & Sullivan).

STEP 2 — In ONE sentence, state the median TAM and CAGR across ≥2 sources, citing each by name.

STEP 3 — Output JSON in a markdown code fence (mandatory, no exceptions):

```json
{{
  "current_tam_b": <float, billions USD, latest year — GLOBAL total>,
  "future_tam_b": <float, billions USD, 5y forward>,
  "current_year": "<YYYY>",
  "future_year": "<YYYY>",
  "cagr_5y_pct": <float, percent — e.g. 10.0 for 10%>,
  "source_label": "<short attribution, e.g. 'WSTS / SIA / McKinsey 2025'>",
  "research_notes": "<1 sentence>",
  "confidence": "high" | "medium" | "low"
}}
```

Rules:
- TAM = GLOBAL market (worldwide), NOT US-domestic only. The FMP industry "{industry}" covers companies that compete globally.
- CAGR = 5-year FORWARD forecast (not historical realized).
- Do NOT mention LLMs, AI tools, or this prompt in your source_label.
- The JSON code fence is REQUIRED — if you set confidence="low" you still must emit the JSON block (with best-effort numbers or zeros).
"""

# Regex for extracting JSON from the markdown code fence.
_JSON_FENCE_RE = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)


# ── Data classes ───────────────────────────────────────────────────────


@dataclass
class OverrideResult:
    """Outcome of one industry's Phase B attempt."""
    industry: str
    sector: str
    status: str  # see CHECK constraint in migration 051
    phase_a_tam_b: Optional[float]
    applied_tam_b: Optional[float]
    applied_cagr_pct: Optional[float]
    applied_source_label: Optional[str]
    rejection_reason: Optional[str]
    raw_response: Optional[Dict[str, Any]]
    tokens_used: Optional[int]


# ── Service ────────────────────────────────────────────────────────────


class IndustryOverrideService:

    _instance: Optional["IndustryOverrideService"] = None

    def __init__(self) -> None:
        self._gemini = None  # lazy

    def _get_gemini(self):
        if self._gemini is None:
            self._gemini = get_gemini_client()
        return self._gemini

    async def refresh_all_overrides(
        self,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Walk CURATED_OVERRIDE_INDUSTRIES, call Gemini for each, write
        accepted overrides to industry_dossier, log every attempt to
        industry_override_audit.

        Args:
            dry_run: when True, fetch + validate but don't write to
                either Supabase table. Returns the same summary so
                operators can sanity-check before a real run.
        """
        run_id = str(uuid.uuid4())
        started = time.time()
        results: List[OverrideResult] = []

        if not settings.INDUSTRY_OVERRIDE_AI_ENABLED:
            logger.info(
                "industry_override: kill switch enabled (INDUSTRY_OVERRIDE_AI_ENABLED=false) — "
                "skipping Phase B"
            )
            for industry, sector in CURATED_OVERRIDE_INDUSTRIES:
                results.append(OverrideResult(
                    industry=industry, sector=sector,
                    status="skipped_kill_switch",
                    phase_a_tam_b=None, applied_tam_b=None,
                    applied_cagr_pct=None, applied_source_label=None,
                    rejection_reason="INDUSTRY_OVERRIDE_AI_ENABLED=false",
                    raw_response=None, tokens_used=None,
                ))
            if not dry_run:
                self._write_audit_log(run_id, results)
            return self._summarize(run_id, results, started, dry_run=dry_run)

        # Load all Phase A TAMs upfront so each override has its
        # sanity-baseline without N round-trips.
        phase_a_tams = self._load_phase_a_tams(
            [ind for ind, _ in CURATED_OVERRIDE_INDUSTRIES]
        )

        for industry, sector in CURATED_OVERRIDE_INDUSTRIES:
            phase_a_tam = phase_a_tams.get(industry)
            try:
                result = await self._research_one(industry, sector, phase_a_tam)
            except Exception as exc:
                logger.error(
                    "industry_override: research_one threw for industry=%r: %s",
                    industry, exc, exc_info=True,
                )
                result = OverrideResult(
                    industry=industry, sector=sector,
                    status="gemini_error",
                    phase_a_tam_b=phase_a_tam,
                    applied_tam_b=None, applied_cagr_pct=None,
                    applied_source_label=None,
                    rejection_reason=f"{type(exc).__name__}: {exc}",
                    raw_response=None, tokens_used=None,
                )
            results.append(result)

            if not dry_run and result.status in ("applied", "applied_with_warning"):
                try:
                    self._apply_to_dossier(industry, sector, result)
                except Exception as exc:
                    logger.error(
                        "industry_override: apply_to_dossier failed for %r: %s",
                        industry, exc, exc_info=True,
                    )

        if not dry_run:
            self._write_audit_log(run_id, results)

        return self._summarize(run_id, results, started, dry_run=dry_run)

    async def _research_one(
        self,
        industry: str,
        sector: str,
        phase_a_tam: Optional[float],
    ) -> OverrideResult:
        """One industry: call Gemini with Google Search grounding, parse,
        validate, return result. The real source URLs come from the
        `groundingChunks` Gemini consulted — those are the audit-log
        breadcrumb, not Gemini's self-reported `sources_cited`.
        """
        prompt = _RESEARCH_PROMPT.format(industry=industry, sector=sector)
        gem = self._get_gemini()

        gemini_response = await gem.generate_grounded_research(
            prompt=prompt,
            max_output_tokens=16384,  # tight prose + JSON, but bigger industries need headroom
        )
        tokens = gemini_response.get("tokens_used")
        text = gemini_response.get("text", "") or ""
        grounding_sources = gemini_response.get("grounding_sources") or []
        search_queries = gemini_response.get("search_queries") or []

        # Extract JSON from markdown code fence.
        match = _JSON_FENCE_RE.search(text)
        if not match:
            return OverrideResult(
                industry=industry, sector=sector,
                status="gemini_error",
                phase_a_tam_b=phase_a_tam,
                applied_tam_b=None, applied_cagr_pct=None,
                applied_source_label=None,
                rejection_reason="No ```json``` code fence in response",
                raw_response={
                    "raw_text": text[:1000],
                    "grounding_sources": grounding_sources,
                    "search_queries": search_queries,
                },
                tokens_used=tokens,
            )
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            return OverrideResult(
                industry=industry, sector=sector,
                status="gemini_error",
                phase_a_tam_b=phase_a_tam,
                applied_tam_b=None, applied_cagr_pct=None,
                applied_source_label=None,
                rejection_reason=f"JSON parse failed: {exc}",
                raw_response={
                    "raw_json": match.group(1)[:500],
                    "grounding_sources": grounding_sources,
                    "search_queries": search_queries,
                },
                tokens_used=tokens,
            )

        # Use the grounding URLs as the authoritative source list — more
        # trustworthy than Gemini self-reporting. We inject them into the
        # payload so the validation gate can check them.
        payload["_grounding_sources"] = grounding_sources
        payload["_search_queries"] = search_queries

        validation = self._validate_response(payload, phase_a_tam)
        if validation["status"] != "ok":
            return OverrideResult(
                industry=industry, sector=sector,
                status=validation["status"],
                phase_a_tam_b=phase_a_tam,
                applied_tam_b=None, applied_cagr_pct=None,
                applied_source_label=None,
                rejection_reason=validation["reason"],
                raw_response=payload, tokens_used=tokens,
            )

        tam = float(payload["current_tam_b"])
        future_tam = float(payload["future_tam_b"])
        cagr = float(payload["cagr_5y_pct"])
        applied_status = "applied_with_warning" if validation["warn"] else "applied"

        return OverrideResult(
            industry=industry, sector=sector,
            status=applied_status,
            phase_a_tam_b=phase_a_tam,
            applied_tam_b=tam,
            applied_cagr_pct=cagr,
            applied_source_label=str(payload.get("source_label") or "Research synthesis").strip()[:200],
            rejection_reason=("TAM divergence >3x from Phase A" if validation["warn"] else None),
            raw_response=payload, tokens_used=tokens,
        )

    def _validate_response(
        self,
        payload: Dict[str, Any],
        phase_a_tam: Optional[float],
    ) -> Dict[str, Any]:
        """Run all validation gates. Returns {status, reason, warn}.

        status:
          'ok' → accept (may also have warn=True)
          'rejected_low_confidence' / 'rejected_validation' / 'rejected_sanity' → reject

        warn=True means TAM differs from Phase A by >3x but <=10x. Still
        applied but flagged in the audit log.
        """
        # 1. Confidence
        confidence = str(payload.get("confidence", "")).lower()
        if confidence not in ("high", "medium"):
            return {"status": "rejected_low_confidence",
                    "reason": f"confidence={confidence!r}", "warn": False}

        # 2. Sources cited — prefer grounding URLs (real, from Google Search)
        # over Gemini's self-reported `sources_cited` (can hallucinate).
        # The grounding URLs flow in via `_grounding_sources` from
        # `_research_one`. For tests / older code paths, fall back to
        # the self-reported list.
        grounding = payload.get("_grounding_sources") or []
        valid_grounding = [
            s for s in grounding
            if isinstance(s, dict) and (s.get("uri") or "").strip()
        ]
        valid_count = len(valid_grounding)
        if valid_count == 0:
            # Fallback: self-reported sources (testing / no-grounding mode)
            self_reported = payload.get("sources_cited") or []
            valid_count = len([
                s for s in self_reported
                if isinstance(s, dict) and (s.get("publisher") or "").strip()
            ])
        if valid_count < 2:
            return {"status": "rejected_validation",
                    "reason": f"only {valid_count} valid source(s) (need ≥2 — "
                              f"grounding chunks or self-reported)",
                    "warn": False}

        # 3-5. Numeric bounds
        try:
            current_tam = float(payload["current_tam_b"])
            future_tam = float(payload["future_tam_b"])
            cagr = float(payload["cagr_5y_pct"])
        except (KeyError, TypeError, ValueError) as exc:
            return {"status": "rejected_validation",
                    "reason": f"missing/non-numeric tam/cagr: {exc}", "warn": False}

        if not (_MIN_TAM_B <= current_tam <= _MAX_TAM_B):
            return {"status": "rejected_validation",
                    "reason": f"current_tam_b={current_tam} out of [{_MIN_TAM_B}, {_MAX_TAM_B}]",
                    "warn": False}
        if not (_MIN_TAM_B <= future_tam <= _MAX_TAM_B):
            return {"status": "rejected_validation",
                    "reason": f"future_tam_b={future_tam} out of [{_MIN_TAM_B}, {_MAX_TAM_B}]",
                    "warn": False}
        if future_tam > current_tam * _MAX_TAM_MULT:
            return {"status": "rejected_validation",
                    "reason": f"future_tam {future_tam} > {_MAX_TAM_MULT}x current_tam {current_tam}",
                    "warn": False}
        if not (_MIN_CAGR_PCT <= cagr <= _MAX_CAGR_PCT):
            return {"status": "rejected_validation",
                    "reason": f"cagr_5y_pct={cagr} out of [{_MIN_CAGR_PCT}, {_MAX_CAGR_PCT}]",
                    "warn": False}

        # 6. Years
        try:
            current_year = int(str(payload.get("current_year", "")).strip())
            future_year = int(str(payload.get("future_year", "")).strip())
        except (ValueError, TypeError):
            return {"status": "rejected_validation",
                    "reason": "current_year / future_year not parseable as int",
                    "warn": False}
        if not (2000 <= current_year <= 2099) or not (2000 <= future_year <= 2099):
            return {"status": "rejected_validation",
                    "reason": f"years out of range: current={current_year}, future={future_year}",
                    "warn": False}
        if future_year <= current_year:
            return {"status": "rejected_validation",
                    "reason": f"future_year {future_year} <= current_year {current_year}",
                    "warn": False}

        # 7. Sanity vs Phase A — divergence ratio
        warn = False
        if phase_a_tam and phase_a_tam > 0:
            ratio = max(current_tam / phase_a_tam, phase_a_tam / current_tam)
            if ratio > _REJECT_DIVERGE_RATIO:
                return {"status": "rejected_sanity",
                        "reason": f"TAM divergence {ratio:.1f}x vs Phase A ({phase_a_tam} B); "
                                  f"limit is {_REJECT_DIVERGE_RATIO}x",
                        "warn": False}
            if ratio > _WARN_DIVERGE_RATIO:
                logger.warning(
                    "industry_override: TAM divergence %.1fx (Phase A=%s, Gemini=%s) — applying with warning",
                    ratio, phase_a_tam, current_tam,
                )
                warn = True

        return {"status": "ok", "reason": None, "warn": warn}

    # ── Supabase I/O ──

    def _load_phase_a_tams(self, industries: List[str]) -> Dict[str, float]:
        """Snapshot the current Phase A TAMs for the curated industries.

        Used as the baseline for divergence sanity checks. If Phase A
        hasn't run yet for some reason (first deploy), the dict simply
        doesn't have that industry → divergence check is skipped.
        """
        try:
            sb = get_supabase()
            res = (
                sb.table("industry_dossier")
                .select("industry,current_tam_b")
                .in_("industry", industries)
                .execute()
            )
        except Exception as exc:
            logger.warning("industry_override: failed to load Phase A TAMs: %s", exc)
            return {}
        out: Dict[str, float] = {}
        for row in res.data or []:
            ind = row.get("industry")
            tam = row.get("current_tam_b")
            if ind and isinstance(tam, (int, float)):
                out[ind] = float(tam)
        return out

    def _apply_to_dossier(
        self, industry: str, sector: str, result: OverrideResult,
    ) -> None:
        """Update the industry_dossier row for this industry with the
        research-based TAM/CAGR. Preserves concentration / HHI fields
        from Phase A (those came from FMP screener data).
        """
        if result.applied_tam_b is None:
            return
        raw = result.raw_response or {}
        current_year = str(raw.get("current_year") or datetime.now(timezone.utc).year)
        future_year = str(raw.get("future_year") or (int(current_year) + 5))
        cagr = result.applied_cagr_pct
        future_tam = float(raw.get("future_tam_b") or 0.0)

        # Re-derive lifecycle from the override CAGR using the same
        # classifier the dossier service uses. We don't know constituent
        # count from this path, so pass a high number to skip the
        # 'emerging' branch (CAGR alone drives the classification).
        new_lifecycle = classify_lifecycle(cagr, num_constituents=50)

        update = {
            "current_tam_b": result.applied_tam_b,
            "future_tam_b": future_tam,
            "current_year": current_year,
            "future_year": future_year,
            "cagr_5y_pct": cagr,
            "lifecycle_phase": new_lifecycle,
            "source_grain": "industry",
            "source_label": result.applied_source_label or "Research synthesis",
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            sb = get_supabase()
            existing = (
                sb.table("industry_dossier")
                .select("id")
                .eq("industry", industry)
                .limit(1)
                .execute()
            )
            if existing.data:
                sb.table("industry_dossier").update(update).eq("industry", industry).execute()
            else:
                # Phase A didn't run for this industry — insert a fresh row.
                insert_row = {
                    **update,
                    "industry": industry,
                    "sector": sector,
                    "expires_at": (
                        datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                    ),
                }
                sb.table("industry_dossier").insert(insert_row).execute()
        except Exception as exc:
            logger.error("industry_override: dossier write failed for %s: %s", industry, exc)
            raise

        # Bust the dossier service's in-memory cache so the new row is
        # picked up on the next read.
        get_industry_dossier_service().reset_cache()

    def _write_audit_log(self, run_id: str, results: List[OverrideResult]) -> None:
        """Bulk-insert audit rows."""
        if not results:
            return
        rows = [
            {
                "run_id": run_id,
                "industry": r.industry,
                "sector": r.sector,
                "status": r.status,
                "raw_response": r.raw_response,
                "phase_a_tam_b": r.phase_a_tam_b,
                "applied_tam_b": r.applied_tam_b,
                "applied_cagr_pct": r.applied_cagr_pct,
                "applied_source_label": r.applied_source_label,
                "rejection_reason": r.rejection_reason,
                "tokens_used": r.tokens_used,
            }
            for r in results
        ]
        try:
            sb = get_supabase()
            sb.table("industry_override_audit").insert(rows).execute()
        except Exception as exc:
            logger.error("industry_override: audit log write failed: %s", exc, exc_info=True)

    def _summarize(
        self, run_id: str, results: List[OverrideResult],
        started: float, dry_run: bool,
    ) -> Dict[str, Any]:
        by_status: Dict[str, int] = {}
        for r in results:
            by_status[r.status] = by_status.get(r.status, 0) + 1
        elapsed = round(time.time() - started, 1)
        total_tokens = sum(r.tokens_used or 0 for r in results)
        summary = {
            "run_id": run_id,
            "dry_run": dry_run,
            "elapsed_seconds": elapsed,
            "industries_attempted": len(results),
            "status_counts": by_status,
            "total_tokens_used": total_tokens,
            "results": [
                {
                    "industry": r.industry,
                    "status": r.status,
                    "phase_a_tam_b": r.phase_a_tam_b,
                    "applied_tam_b": r.applied_tam_b,
                    "applied_cagr_pct": r.applied_cagr_pct,
                    "applied_source_label": r.applied_source_label,
                    "rejection_reason": r.rejection_reason,
                }
                for r in results
            ],
        }
        logger.info("industry_override Phase B summary: %s", {
            k: v for k, v in summary.items() if k != "results"
        })
        return summary


_service_singleton: Optional[IndustryOverrideService] = None


def get_industry_override_service() -> IndustryOverrideService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = IndustryOverrideService()
    return _service_singleton
