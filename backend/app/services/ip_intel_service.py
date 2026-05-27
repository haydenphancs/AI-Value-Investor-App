"""IP Intel — orchestrates USPTO patent + OpenFDA approval lookups
for the Intangible Assets pillar of moat scoring (Phase 3C).

Architecture (mirrors competitor_intel + moat_intel):
  * Two-tier cache: in-memory dict (5 min) + Supabase ip_intel_cache
    table (180-day TTL). IP data changes slowly.
  * `_inflight` dedup so a thundering herd doesn't trigger duplicate
    USPTO / FDA calls per process.
  * Industry routing: USPTO patent lookup runs for every ticker that
    has a company name. FDA approval lookup only runs when the sector
    is Healthcare or the industry name contains "Pharma" / "Biotech" /
    "Medical" — for everything else, FDA approvals are zero and we
    shouldn't waste the API call.
  * Audit log per fetch attempt → `ip_intel_audit` table.
  * On any single source failing (USPTO returns no_api_key; OpenFDA 429),
    the other source's data still lands in the cache — "applied_partial".
  * `refresh_top_tickers()` is the entry point chained into the
    existing quarterly background job in main.py.

Per-ticker focal value:
    patents_per_employee = uspto_recent_5y_count / fullTimeEmployees
The recent-5y window is preferred over total because total includes
expired patents that no longer block competitors.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.openfda import (
    OpenFDAException,
    OpenFDARateLimitException,
    get_openfda_client,
)
from app.integrations.uspto import (
    USPTOException,
    USPTORateLimitException,
    current_year,
    get_uspto_client,
)

logger = logging.getLogger(__name__)


# ── Config ─────────────────────────────────────────────────────────────

_CACHE_TTL_DAYS = 180        # IP changes slowly — half-year freshness
_MEM_TTL_SECONDS = 300       # 5-min in-memory dedup
_BATCH_TOP_N = 500
_BATCH_CONCURRENCY = 5
_RECENT_PATENT_YEARS = 5

_HEALTHCARE_SECTORS = {"Healthcare"}
_PHARMA_INDUSTRY_KEYWORDS = ("pharma", "biotech", "drug", "medical")

# Schema floor — bump when extraction logic changes meaningfully so
# stale cached rows fall through to fresh fetches.
IP_INTEL_SCHEMA_FLOOR = datetime(2026, 5, 26, 0, 0, 0, tzinfo=timezone.utc)


# ── In-memory tier 1 cache + inflight dedup ────────────────────────────

_mem_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_inflight: Dict[str, asyncio.Future] = {}


def _mem_get(ticker: str) -> Optional[Dict[str, Any]]:
    entry = _mem_cache.get(ticker)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _MEM_TTL_SECONDS:
        del _mem_cache[ticker]
        return None
    return value


def _mem_set(ticker: str, value: Dict[str, Any]) -> None:
    _mem_cache[ticker] = (time.time(), value)


def _is_pharma(profile: Dict[str, Any]) -> bool:
    sector = (profile.get("sector") or "").strip()
    industry = (profile.get("industry") or "").strip().lower()
    if sector in _HEALTHCARE_SECTORS:
        return True
    return any(kw in industry for kw in _PHARMA_INDUSTRY_KEYWORDS)


_CORP_SUFFIX_RE = re.compile(
    r"[,\s]+("
    r"Inc\.?|Incorporated|Corp\.?|Corporation|"
    r"Co\.?|Company|Ltd\.?|Limited|"
    r"LLC|L\.L\.C\.|PLC|plc|N\.V\.|NV|"
    r"S\.A\.|SA|AG|GmbH|Holdings?|Group"
    r")\.?$",
    re.IGNORECASE,
)
_LEADING_ARTICLE_RE = re.compile(r"^(The|the)\s+")
_DOTCOM_SUFFIX_RE = re.compile(r"\.com$", re.IGNORECASE)

# Per-ticker overrides for the small set of names where the FMP
# companyName + suffix-strip normalizer mismatches the legal entity
# USPTO actually files patents under. Loaded once per process. The
# map is intentionally tiny — most tickers work without it; only add
# entries here after a live USPTO probe confirms a >2x improvement.
_ALIAS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "uspto_assignee_aliases.json"
)
_ASSIGNEE_ALIASES: Optional[Dict[str, str]] = None


def _load_assignee_aliases() -> Dict[str, str]:
    global _ASSIGNEE_ALIASES
    if _ASSIGNEE_ALIASES is None:
        try:
            data = json.loads(_ALIAS_PATH.read_text())
            _ASSIGNEE_ALIASES = data.get("aliases") or {}
        except Exception as exc:
            logger.warning(
                "ip_intel: failed to load USPTO alias map %s: %s",
                _ALIAS_PATH, exc,
            )
            _ASSIGNEE_ALIASES = {}
    return _ASSIGNEE_ALIASES


def _normalize_assignee(name: str, ticker: Optional[str] = None) -> str:
    """Canonicalize a company name for USPTO/FDA search.

    Order of preference:
      1. Per-ticker alias map (curated edge cases — MRNA→ModernaTX etc).
      2. Strip leading "The " article and trailing ".com" / corporate
         suffix (Inc., Corporation, Ltd., LLC, Holdings, etc.) and
         orphaned `&`/`+`/`,`.

    The suffix strip exists because USPTO assignee records often list
    the operating subsidiary rather than the listed holding company —
    e.g. FMP returns "Oracle Corporation" but USPTO has the bulk of
    the IP under "Oracle International Corporation" / "Oracle America,
    Inc.". A bare prefix match catches both. Repeats once so chains
    like "Holdings, Inc." collapse cleanly.
    """
    if ticker:
        alias = _load_assignee_aliases().get(ticker.upper())
        if alias:
            return alias
    if not isinstance(name, str):
        return ""
    cleaned = _LEADING_ARTICLE_RE.sub("", name.strip())
    # Two passes: corporate-suffix strip exposes a trailing ".com" that
    # was hidden behind ", Inc." (e.g. "Amazon.com, Inc." → "Amazon.com"
    # → "Amazon"), so re-run the .com strip after each suffix removal.
    for _ in range(3):
        new = _CORP_SUFFIX_RE.sub("", cleaned).rstrip(", &+").strip()
        new = _DOTCOM_SUFFIX_RE.sub("", new)
        if new == cleaned or not new:
            break
        cleaned = new
    return cleaned


# ── Service ────────────────────────────────────────────────────────────


class IPIntelService:

    def __init__(self) -> None:
        self._uspto = None
        self._openfda = None

    def _get_uspto(self):
        if self._uspto is None:
            self._uspto = get_uspto_client()
        return self._uspto

    def _get_openfda(self):
        if self._openfda is None:
            self._openfda = get_openfda_client()
        return self._openfda

    # ── Public API ──────────────────────────────────────────────────

    async def get_ip_intel(
        self,
        ticker: str,
        profile: Dict[str, Any],
        *,
        force_refresh: bool = False,
        run_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Returns the per-ticker IP payload (patents + FDA) or None
        when both sources resolve empty.

        Payload shape:
            {
              "patents_total":          Optional[int],
              "patents_recent_5y":      Optional[int],
              "patents_per_employee":   Optional[float],  # recent_5y / fullTimeEmployees
              "fda_active_approvals":   Optional[int],
              "fda_total_approvals":    Optional[int],
              "source_labels":          List[str],         # ["USPTO", "OpenFDA"]
              "assignee_queried":       str,
            }
        """
        focal = (ticker or "").strip().upper()
        if not focal:
            return None

        # Tier 1: in-memory.
        if not force_refresh:
            cached = _mem_get(focal)
            if cached is not None:
                return cached

        # Tier 2: Supabase.
        if not force_refresh:
            db_cached = await asyncio.to_thread(self._read_cache, focal)
            if db_cached is not None:
                _mem_set(focal, db_cached)
                return db_cached

        # Inflight dedup.
        if focal in _inflight:
            try:
                return await _inflight[focal]
            except Exception:
                return None

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[focal] = future

        try:
            payload = await self._fetch_one(focal, profile, run_id=run_id)
            if payload:
                await asyncio.to_thread(self._write_cache, focal, payload)
                _mem_set(focal, payload)
                future.set_result(payload)
                return payload
            future.set_result(None)
            return None
        except Exception as exc:
            logger.exception(
                "ip_intel: unhandled error for %s: %s", focal, exc,
            )
            future.set_exception(exc)
            return None
        finally:
            _inflight.pop(focal, None)
            if not future.done():
                future.set_result(None)

    async def refresh_top_tickers(
        self, top_n: int = _BATCH_TOP_N,
    ) -> Dict[str, Any]:
        """Quarterly batch entry. Reads the top-N most-watchlisted
        tickers and refreshes each. Mirrors competitor_intel_service.
        """
        run_id = str(uuid.uuid4())
        started = time.time()
        tickers = await asyncio.to_thread(self._load_top_watchlist_tickers, top_n)
        if not tickers:
            return {
                "run_id": run_id, "ran": 0, "applied": 0,
                "still_failing": 0, "elapsed_seconds": 0,
            }

        logger.info(
            "ip_intel: quarterly batch starting — run_id=%s, tickers=%d",
            run_id, len(tickers),
        )

        sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

        async def _one(t: str) -> Tuple[str, bool]:
            async with sem:
                profile = await self._safe_fetch_profile(t)
                if profile is None:
                    return (t, False)
                payload = await self.get_ip_intel(
                    t, profile, force_refresh=True, run_id=run_id,
                )
                return (t, bool(payload))

        results = await asyncio.gather(
            *[_one(t) for t in tickers], return_exceptions=True,
        )
        applied = sum(
            1 for r in results
            if isinstance(r, tuple) and r[1]
        )
        still_failing = len(tickers) - applied
        summary = {
            "run_id": run_id,
            "ran": len(tickers),
            "applied": applied,
            "still_failing": still_failing,
            "elapsed_seconds": round(time.time() - started, 1),
        }
        logger.info("ip_intel quarterly batch summary: %s", summary)
        return summary

    # ── Internal: per-ticker fetch ────────────────────────────────────

    async def _fetch_one(
        self,
        ticker: str,
        profile: Dict[str, Any],
        *,
        run_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        company_name = profile.get("companyName") or ticker
        assignee = _normalize_assignee(company_name, ticker=ticker)
        sponsor = assignee   # FDA sponsor naming usually mirrors USPTO assignee
        run_id_to_use = run_id or str(uuid.uuid4())

        # USPTO — always attempted (most companies have at least a few
        # patents; gracefully no-ops when USPTO_API_KEY missing).
        uspto_total: Optional[int] = None
        uspto_recent: Optional[int] = None
        uspto_error: Optional[str] = None
        try:
            recent_year_threshold = current_year() - _RECENT_PATENT_YEARS
            uspto_data = await self._get_uspto().get_patents_for_assignee(
                assignee, since_year=recent_year_threshold,
            )
            uspto_recent = int(uspto_data.get("total_hits") or 0)
            uspto_error = uspto_data.get("error")
            # For "total since the dawn of time", a separate call could
            # be made — but the recent-5y window is the primary signal
            # for moat scoring (older patents may have expired). Skip
            # the second call to stay under rate limit budget.
            uspto_total = uspto_recent  # placeholder; treats recent as proxy for "active IP"
        except USPTORateLimitException:
            uspto_error = "rate_limit"
        except USPTOException as exc:
            uspto_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            uspto_error = f"unexpected: {exc}"

        # FDA — only for pharma-adjacent tickers.
        fda_active: Optional[int] = None
        fda_total: Optional[int] = None
        fda_error: Optional[str] = None
        if _is_pharma(profile):
            try:
                fda_data = await self._get_openfda().get_drug_approvals(sponsor)
                fda_active = int(fda_data.get("active_count") or 0)
                fda_total = int(fda_data.get("total_hits") or 0)
                fda_error = fda_data.get("error")
            except OpenFDARateLimitException:
                fda_error = "rate_limit"
            except OpenFDAException as exc:
                fda_error = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                fda_error = f"unexpected: {exc}"

        # Per-employee normalization for the patents driver.
        employees = profile.get("fullTimeEmployees")
        try:
            employees = int(employees) if employees else 0
        except (TypeError, ValueError):
            employees = 0
        patents_per_emp: Optional[float] = None
        if uspto_recent is not None and uspto_recent > 0 and employees > 0:
            patents_per_emp = round(uspto_recent / employees, 4)

        # Decide status for the audit row.
        if (uspto_recent or 0) == 0 and (fda_active or 0) == 0:
            status = "rejected_no_data"
            error_detail = uspto_error or fda_error or "both_sources_empty"
            payload: Optional[Dict[str, Any]] = None
        else:
            status = "applied"
            if uspto_error or fda_error:
                status = "applied_partial"
            error_detail = uspto_error or fda_error
            sources = []
            if (uspto_recent or 0) > 0:
                sources.append("USPTO")
            if (fda_active or 0) > 0:
                sources.append("OpenFDA")
            payload = {
                "patents_total": uspto_total,
                "patents_recent_5y": uspto_recent,
                "patents_per_employee": patents_per_emp,
                "fda_active_approvals": fda_active,
                "fda_total_approvals": fda_total,
                "source_labels": sources,
                "assignee_queried": assignee,
                "employees_at_fetch": employees,
            }

        await asyncio.to_thread(
            self._write_audit_row,
            run_id_to_use, ticker, status, payload,
            uspto_total, uspto_recent, fda_active,
            assignee, sponsor, error_detail,
        )

        return payload

    async def _safe_fetch_profile(self, ticker: str) -> Optional[Dict[str, Any]]:
        try:
            from app.integrations.fmp import get_fmp_client
            return await get_fmp_client().get_company_profile(ticker)
        except Exception as exc:
            logger.warning(
                "ip_intel: profile fetch failed for %s: %s", ticker, exc,
            )
            return None

    # ── Supabase I/O ──────────────────────────────────────────────────

    def _read_cache(self, ticker: str) -> Optional[Dict[str, Any]]:
        try:
            sb = get_supabase()
            res = (
                sb.table("ip_intel_cache")
                .select("payload,computed_at,expires_at")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            logger.warning("ip_intel: cache read failed for %s: %s", ticker, exc)
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
        if exp_dt <= datetime.now(timezone.utc):
            return None
        if comp_dt < IP_INTEL_SCHEMA_FLOOR:
            return None
        payload = row.get("payload")
        if not isinstance(payload, dict) or not payload:
            return None
        return payload

    def _write_cache(self, ticker: str, payload: Dict[str, Any]) -> None:
        if not payload:
            return
        now = datetime.now(timezone.utc)
        row = {
            "ticker": ticker,
            "payload": payload,
            "source_labels": payload.get("source_labels") or [],
            "computed_at": now.isoformat(),
            "expires_at": (now + timedelta(days=_CACHE_TTL_DAYS)).isoformat(),
        }
        try:
            sb = get_supabase()
            sb.table("ip_intel_cache").upsert(row).execute()
        except Exception as exc:
            logger.warning("ip_intel: cache write failed for %s: %s", ticker, exc)

    def _write_audit_row(
        self,
        run_id: str,
        ticker: str,
        status: str,
        payload: Optional[Dict[str, Any]],
        uspto_total: Optional[int],
        uspto_recent: Optional[int],
        fda_active: Optional[int],
        assignee: str,
        sponsor: str,
        error_detail: Optional[str],
    ) -> None:
        row = {
            "run_id": run_id,
            "ticker": ticker,
            "status": status,
            "payload": payload,
            "uspto_total": uspto_total,
            "uspto_recent_5y": uspto_recent,
            "fda_active": fda_active,
            "assignee_name": assignee,
            "sponsor_name": sponsor,
            "error_detail": error_detail,
        }
        try:
            sb = get_supabase()
            sb.table("ip_intel_audit").insert(row).execute()
        except Exception as exc:
            logger.warning(
                "ip_intel: audit write failed for %s: %s", ticker, exc,
            )

    def _load_top_watchlist_tickers(self, limit: int) -> List[str]:
        try:
            sb = get_supabase()
            res = (
                sb.table("watchlist_items")
                .select("ticker")
                .limit(50_000)
                .execute()
            )
        except Exception as exc:
            logger.warning("ip_intel: watchlist read failed: %s", exc)
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


_service_singleton: Optional[IPIntelService] = None


def get_ip_intel_service() -> IPIntelService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = IPIntelService()
    return _service_singleton


# Sub-score helpers — used by moat_scoring_service.

def patents_per_employee_to_sub_score(
    patents_per_employee: Optional[float],
) -> Optional[float]:
    """Map patents-per-employee (5y window) to a 0-10 Intangible Assets
    sub-score using practitioner-defensible thresholds:
        >= 1.0   → 9.5 (elite — IBM, Qualcomm, Samsung tier)
        >= 0.5   → 8.5
        >= 0.2   → 7.0
        >= 0.05  → 5.5
        >  0     → 4.0 (some IP)
        == 0     → None (no signal; don't contribute)
    """
    if patents_per_employee is None or patents_per_employee <= 0:
        return None
    if patents_per_employee >= 1.0:
        return 9.5
    if patents_per_employee >= 0.5:
        return 8.5
    if patents_per_employee >= 0.2:
        return 7.0
    if patents_per_employee >= 0.05:
        return 5.5
    return 4.0


def fda_approvals_to_sub_score(
    fda_active_approvals: Optional[int],
) -> Optional[float]:
    """Map FDA active drug approvals to a 0-10 Intangible Assets
    sub-score. Higher = larger marketed drug portfolio = more
    regulatory moat:
        >= 50  → 9.5
        >= 20  → 8.5
        >= 10  → 7.5
        >=  5  → 6.5
        >=  1  → 5.5
        == 0   → None (signal absent)
    """
    if fda_active_approvals is None or fda_active_approvals <= 0:
        return None
    if fda_active_approvals >= 50:
        return 9.5
    if fda_active_approvals >= 20:
        return 8.5
    if fda_active_approvals >= 10:
        return 7.5
    if fda_active_approvals >= 5:
        return 6.5
    return 5.5
