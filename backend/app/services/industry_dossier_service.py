"""Industry dossier — pre-computed TAM / CAGR / lifecycle / concentration
for every FMP industry.

The TickerReport "Moat & Competition" section reads this table instead of
calling FRED + Census live per report. A QUARTERLY batch (first Sunday of
Jan/Apr/Jul/Oct, 02:00 UTC — `_run_industry_dossier_job` in main.py) runs
`recompute_all`, which walks every industry in
`backend/data/industry_universe.json` (built by
`backend/scripts/discover_industries.py`), computes a fresh dossier row,
and upserts to the `industry_dossier` Supabase table.

Read path: `get_dossier(industry)` is called from
`ticker_report_data_collector._fetch_dependent` instead of
`industry_tam_service.get_industry_tam`. In-memory tier (5 min TTL) +
Supabase row by `industry`. The returned `IndustryDossier` is shaped
to be a superset of `IndustryTAM` so the existing `_apply_tam_source`
logic keeps working when only TAM fields are read.

Coverage chain — never returns null for a discovered industry:
    1. Census NAICS                → source_grain='industry'
    2. Industry-mapped FRED        → 'industry' only when the series measures
       the industry itself (`FRED_SERIES_MATCHES_INDUSTRY`), else 'sector'
    3. Sector-level FRED           → source_grain='sector'
    4. All-industry FRED USNGSP    → source_grain='all_industry'

The report shows TAM/CAGR only for source_grain='industry' (`_apply_tam_source`
hides the broad stand-ins — owner decision 2026-10-01). iOS renders neither
`source_grain` nor `tam_source_label`; there is no "broader than industry" chip.

Self-heal (2026-10-01): a stored row whose TAM is a zero placeholder (written
when FRED/Census were unreachable — 138 of 158 rows sat that way from 2026-07-05
until the next quarterly run) is treated as a MISS by `get_or_compute_dossier`:
the TAM is computed live and merged over the stored row in memory. The read
path never writes the table — persistence stays with `recompute_all` and its
guards, because a third writer on the request path would race them.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.database import get_supabase
from app.integrations.fmp import FMPClient
from app.services.industry_tam_service import (
    INDUSTRY_TO_CENSUS,
    INDUSTRY_TO_FRED_SERIES,
    IndustryTAM,
    _try_census_tam,
    _try_fred_tam,
    expects_industry_grain,
    fred_mapping_grain,
    fred_tam_for_series,
)
from app.integrations.census import CensusUnavailableException
from app.utils.inflight import fail_shared_future

logger = logging.getLogger(__name__)


class IndustryDossierRecomputeSkipped(RuntimeError):
    """`recompute_all` could not run and wrote NOTHING (empty universe, or no upstream
    credentials). RAISED, never returned as a summary.

    It used to return `{"status": "skipped"}`, and the quarterly chain's
    `_run_claimed_phase` (main.py) marks any phase that RETURNS as settled: the day-keyed
    claim recorded success, the quarter was consumed, and the dossier stayed stale or zero
    until the next quarter — while the read path's in-memory self-heal of zero rows hid it
    from every report. Raising releases the claim unsettled, so the chain retries the phase
    inside its catch-up window and the ledger row carries this message as its `error`.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(f"industry_dossier recompute SKIPPED ({reason}) — {detail}")


# ── Sector-level FRED fallback ─────────────────────────────────────────
#
# When an industry isn't in INDUSTRY_TO_CENSUS or INDUSTRY_TO_FRED_SERIES,
# fall back to the sector's most-representative FRED series. The 11
# canonical sectors come from `sector_benchmark_service.CANONICAL_SECTORS`.
# Real Estate and Utilities don't have a clean single-NAICS FRED series at
# the GDP-by-industry granularity we use elsewhere; they fall through to
# `_ALL_INDUSTRY_FRED_SERIES` (USNGSP) with source_grain='all_industry'.

_SECTOR_TO_FRED_SERIES: Dict[str, str] = {
    "Technology": "USINFONGSP",                      # Information sector
    "Communication Services": "USINFONGSP",          # Telecom + content, same NAICS 51 bucket
    "Healthcare": "USHLTHSOCASSNGSP",                # Health Care & Social Assistance
    "Financial Services": "USFININSNGSP",            # Finance & Insurance
    "Consumer Cyclical": "USRETAILNGSP",             # Retail Trade (largest sub)
    "Industrials": "USMANNGSP",                      # Manufacturing (largest sub of Industrials)
    "Basic Materials": "USMANNGSP",                  # Manufacturing
    "Consumer Defensive": "USFOODDPNGSP",            # Food Services & Drinking Places (partial fit)
    "Energy": "USMINNGSP",                           # Mining, Quarrying, Oil & Gas
    "Real Estate": "USREALNGSP",                     # Real Estate & Rental and Leasing (NAICS 53)
    "Utilities": "USUTILNGSP",                       # Utilities (NAICS 22)
}

_SECTOR_FRED_LABELS: Dict[str, str] = {
    "USINFONGSP": "BEA Information sector GDP",
    "USHLTHSOCASSNGSP": "BEA Health Care & Social Assistance GDP",
    "USFININSNGSP": "BEA Finance & Insurance GDP",
    "USRETAILNGSP": "BEA Retail Trade GDP",
    "USMANNGSP": "BEA Manufacturing GDP",
    "USFOODDPNGSP": "BEA Food Services GDP",
    "USMINNGSP": "BEA Mining (oil & gas) GDP",
    "USREALNGSP": "BEA Real Estate & Rental sector GDP",
    "USUTILNGSP": "BEA Utilities sector GDP",
    "USCONSTNGSP": "BEA Construction sector GDP",
}

_ALL_INDUSTRY_FRED_SERIES = "USNGSP"
_ALL_INDUSTRY_FRED_LABEL = "BEA US total GDP (all industries, via FRED)"

# Universe file written by `backend/scripts/discover_industries.py`.
# Single resolver — see `app/services/universe_data.py`. There were FOUR different
# path idioms for this one directory, and the file is FMP-derived so it has to be
# able to move out of the repo (ToS §2.6.1) without a hunt.
from app.services.universe_data import INDUSTRY_UNIVERSE, load_universe, universe_path
from app.utils.supabase_async import sb_exec


# ── Data class ──────────────────────────────────────────────────────────


@dataclass
class IndustryDossier:
    """Pre-computed industry-level facts.

    Shaped as a SUPERSET of `IndustryTAM` (industry_tam_service.IndustryTAM)
    so callers that previously accepted an `IndustryTAM` instance keep
    working. Extra fields:
      - industry / sector: identity (used for the source row + iOS chip)
      - lifecycle_phase / hhi / top1_share_pct / top2_share_pct /
        concentration_label: industry-wide aggregates used to replace the
        focal-ticker peer-derived computation
      - source_grain: 'industry' | 'sector' | 'all_industry' — drives the
        iOS warning chip when fallback was used
    """
    # IndustryTAM-compatible fields (must keep these names + order so
    # downstream `_apply_tam_source` reads them transparently)
    current_tam: float
    future_tam: float
    current_year: str
    future_year: str
    source_label: str
    cagr_5y_pct: Optional[float] = None
    # Dossier-specific
    industry: str = ""
    sector: str = ""
    lifecycle_phase: str = "mature"
    hhi: Optional[float] = None
    top1_share_pct: Optional[float] = None
    top2_share_pct: Optional[float] = None
    concentration_label: Optional[str] = None
    constituent_count: Optional[int] = None
    source_grain: str = "industry"
    # Scope of the TAM figure: 'us' (Census/FRED US-domestic) — the only scope
    # written now. 'global' marked a Gemini grounded-research override (the old
    # Phase B, retired 2026-10-02 with Google Search grounding); such a row is
    # never served (`_withdraw_grounded_tam`) and migration 188 clears it.
    tam_scope: str = "us"

    def to_db_row(self) -> Dict[str, Any]:
        return {
            "industry": self.industry,
            "sector": self.sector,
            "current_tam_b": self.current_tam,
            "future_tam_b": self.future_tam,
            "current_year": self.current_year,
            "future_year": self.future_year,
            "cagr_5y_pct": self.cagr_5y_pct,
            "lifecycle_phase": self.lifecycle_phase,
            "hhi": self.hhi,
            "top1_share_pct": self.top1_share_pct,
            "top2_share_pct": self.top2_share_pct,
            "concentration_label": self.concentration_label,
            "constituent_count": self.constituent_count,
            "source_grain": self.source_grain,
            "source_label": self.source_label,
            "tam_scope": self.tam_scope,
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=8)).isoformat(),
        }

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> "IndustryDossier":
        def _f(key: str) -> Optional[float]:
            v = row.get(key)
            return float(v) if v is not None else None

        return cls(
            current_tam=float(row.get("current_tam_b") or 0.0),
            future_tam=float(row.get("future_tam_b") or 0.0),
            current_year=str(row.get("current_year") or ""),
            future_year=str(row.get("future_year") or ""),
            source_label=str(row.get("source_label") or ""),
            cagr_5y_pct=_f("cagr_5y_pct"),
            industry=str(row.get("industry") or ""),
            sector=str(row.get("sector") or ""),
            lifecycle_phase=str(row.get("lifecycle_phase") or "mature"),
            hhi=_f("hhi"),
            top1_share_pct=_f("top1_share_pct"),
            top2_share_pct=_f("top2_share_pct"),
            concentration_label=row.get("concentration_label"),
            constituent_count=row.get("constituent_count"),
            source_grain=str(row.get("source_grain") or "industry"),
            tam_scope=str(row.get("tam_scope") or "us"),
        )


# ── Classification helpers (kept local so the service has no circular
#    import on ticker_report_data_collector) ────────────────────────────


def classify_concentration(top1_pct: float, top2_pct: float, hhi: float) -> str:
    """Mirror of `ticker_report_data_collector._classify_concentration`.

    Kept here so the dossier service has no dependency on the collector
    module (which imports a lot). Update both together if thresholds move.

    Inputs are MARKET-CAP shares (see `_compute_hhi(market_caps)`), not
    market/revenue share — so, like the collector mirror, we never emit
    'monopoly'/'duopoly' (those are share structures). Cap-derived
    concentration tops out at 'oligopoly'.
    """
    if top1_pct > 50.0 or top2_pct > 70.0 or hhi >= 1500.0:
        return "oligopoly"
    return "fragmented"


def classify_lifecycle(cagr_5y_pct: Optional[float], num_constituents: int) -> str:
    """Mirror of `ticker_report_data_collector._classify_lifecycle`."""
    if 0 < num_constituents < 5:
        return "emerging"
    if cagr_5y_pct is None:
        return "mature"
    if cagr_5y_pct > 15.0:
        return "secular_growth"
    if cagr_5y_pct < 0.0:
        return "declining"
    return "mature"


def _compute_hhi(market_caps: List[float]) -> float:
    """HHI on the 0..10000 scale (sum of squared % shares)."""
    total = sum(market_caps)
    if total <= 0:
        return 0.0
    return sum(((c / total) * 100.0) ** 2 for c in market_caps)


# ── Universe file I/O ───────────────────────────────────────────────────


def _load_universe() -> List[Dict[str, Any]]:
    """Load the discovered industry universe.

    Returns a list of {industry, sector, tickers: [...]} entries. Empty
    list when the universe file hasn't been generated yet (first deploy
    before `scripts/discover_industries.py` has run) or could not be
    pulled from Supabase Storage (it is not in git). In that case
    `recompute_all` raises `IndustryDossierRecomputeSkipped` — there's
    nothing to compute, and the quarterly chain must retry, not settle.
    """
    # `load_universe` resolves the path, pulls from Supabase Storage on a local miss, and
    # logs at ERROR with the reason when it cannot. It returns [] rather than raising: this
    # is a request/job path where a missing universe must degrade, not 500.
    return load_universe(INDUSTRY_UNIVERSE)


# ── Self-heal helpers (pure) ───────────────────────────────────────────


def _finite_positive(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 0


def _is_placeholder(d: "IndustryDossier") -> bool:
    """A stored row carries no usable TAM — the "no public data" placeholder
    (TAM 0) or anything non-finite. Decided on the NUMBER, never the label
    text, so a reworded placeholder cannot slip through."""
    return not _finite_positive(d.current_tam)


def _usable_live(d: Optional[Any]) -> bool:
    """A live compute is worth showing only with a complete, positive pair —
    a one-sided pair renders "$0B → $X"."""
    return d is not None and _finite_positive(d.current_tam) and _finite_positive(d.future_tam)


def _finite_or_none(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _fred_is_configured() -> bool:
    from app.integrations.fred import get_fred_client  # noqa: PLC0415 — keeps FRED out of import time

    try:
        return bool(get_fred_client().is_configured)
    except Exception as exc:
        logger.warning("industry_dossier: could not read FRED configuration: %s: %s", type(exc).__name__, exc)
        return False


GROUNDED_TAM_SCOPE = "global"


def _withdraw_grounded_tam(d: "IndustryDossier") -> "IndustryDossier":
    """A row whose TAM came from Gemini grounded research (`tam_scope='global'`, the
    retired Phase B) with that TAM withdrawn: a zero placeholder, so the read path's
    self-heal serves a live Census/FRED figure in its place. The Grounding with Google
    Search terms forbid storing a grounded result and showing it to every user, so not
    even its lifecycle (derived from the grounded CAGR) survives. The CONCENTRATION side
    (universe market caps) was never grounded and is kept. Any other row is unchanged.
    """
    if d.tam_scope != GROUNDED_TAM_SCOPE:
        return d
    return dataclasses.replace(
        d,
        current_tam=0.0, future_tam=0.0, current_year="", future_year="",
        cagr_5y_pct=None, lifecycle_phase="mature", source_grain="all_industry",
        source_label="Research figure withdrawn — pending recompute", tam_scope="us",
    )


def _merge_live_tam(stored: "IndustryDossier", live: "IndustryDossier") -> "IndustryDossier":
    """Lay a live-computed TAM over a stored placeholder row.

    TAM-side fields come from `live`; the industry-wide CONCENTRATION fields
    (HHI, top shares, label, constituent count — computed from the universe's
    market caps, independent of FRED/Census) stay from `stored`. A live compute
    runs with no constituents, so taking its concentration would flip a
    measured "oligopoly" to the "fragmented" default. `replace` returns a new
    object: the cached stored row is never mutated.
    """
    cagr = _finite_or_none(live.cagr_5y_pct)
    return dataclasses.replace(
        stored,
        current_tam=live.current_tam,
        future_tam=live.future_tam,
        current_year=live.current_year,
        future_year=live.future_year,
        source_label=live.source_label,
        cagr_5y_pct=cagr,
        source_grain=live.source_grain,
        tam_scope="us",  # Phase A sources (Census/FRED) are US-domestic
        lifecycle_phase=classify_lifecycle(cagr, stored.constituent_count or 0),
        sector=stored.sector or live.sector,
    )


# ── Service ─────────────────────────────────────────────────────────────


class IndustryDossierService:
    """Two-tier cached access to `industry_dossier`.

    Read path (in-memory → Supabase) is hot; backed by the weekly
    `recompute_all` write path that does the upstream FRED / Census /
    FMP work in batch.
    """

    _instance: Optional["IndustryDossierService"] = None

    # Class-level in-memory cache shared across instances (singleton
    # use, but keep it on the class so test fixtures can reset it).
    _cache: Dict[str, tuple[float, IndustryDossier]] = {}
    _CACHE_TTL_SECONDS = 300  # 5 min

    # Self-heal state (see the module docstring).
    #   _live_inflight  — one live compute per (industry, sector) at a time.
    #   _heal_failed_at — negative memo: a live compute that failed or came back
    #                     unusable is not retried for _CACHE_TTL_SECONDS, so a
    #                     FRED outage does not cost every report a slow retry.
    #   _generation     — bumped by reset_cache(); a heal that started before a
    #                     recompute/override reset must not re-cache its stale
    #                     result over the freshly written row.
    _live_inflight: Dict[tuple[str, str], "asyncio.Future"] = {}
    _heal_failed_at: Dict[str, tuple[float, bool]] = {}  # industry → (when, transient)
    _heal_logged: set = set()
    _generation: int = 0
    # A live compute walks up to 3 FRED series (+4 Census probes for a mapped
    # NAICS), each with a 30 s HTTP timeout. A report must not wait on that.
    _LIVE_COMPUTE_TIMEOUT_SECONDS = 8.0

    def __init__(self) -> None:
        self._fmp: Optional[FMPClient] = None

    @classmethod
    def reset_cache(cls) -> None:
        # Called from recompute_all (and admin routes, some inside
        # asyncio.to_thread) — plain dict/int ops only. `_live_inflight`
        # is deliberately NOT cleared: dropping a live future would strand the
        # callers awaiting it.
        cls._cache.clear()
        cls._heal_failed_at.clear()
        # So a heal that is still needed AFTER a recompute logs again — that line
        # is the signal that the recompute did not write a real row.
        cls._heal_logged.clear()
        cls._generation += 1

    def _get_fmp(self) -> FMPClient:
        if self._fmp is None:
            self._fmp = FMPClient()
        return self._fmp

    # ── Read path ──

    async def get_dossier(self, industry: Optional[str]) -> Optional[IndustryDossier]:
        """Pure read: in-memory → Supabase. Returns None on miss.

        Most callers should use `get_or_compute_dossier` instead — it
        falls back to a live FRED/Census compute when the quarterly batch
        hasn't covered an industry yet, or left a zero placeholder.
        """
        dossier, _ok = await self._read_dossier(industry)
        return dossier

    async def _read_dossier(
        self, industry: Optional[str],
    ) -> tuple[Optional[IndustryDossier], bool]:
        """(row, read_ok). `read_ok` is False only when Supabase itself failed,
        so the caller can tell "no row" from "could not look" — the two need
        different handling (see `get_or_compute_dossier`)."""
        if not industry:
            return None, True

        # 1. in-memory
        entry = self._cache.get(industry)
        if entry and (time.time() - entry[0]) < self._CACHE_TTL_SECONDS:
            return entry[1], True

        # 2. Supabase
        generation = type(self)._generation
        try:
            sb = get_supabase()
            res = (
                (await sb_exec(
                    sb.table("industry_dossier")
                    .select("*")
                    .eq("industry", industry)
                    .limit(1)
                ))
            )
            rows = res.data or []
        except Exception as exc:
            logger.warning(
                "industry_dossier read failed for %r: %s: %s",
                industry, type(exc).__name__, exc,
            )
            return None, False

        if not rows:
            return None, True

        dossier = _withdraw_grounded_tam(IndustryDossier.from_db_row(rows[0]))
        # A recompute/override that reset the cache while this read was in its
        # worker thread may have written a newer row than the one we hold.
        if type(self)._generation == generation:
            self._cache[industry] = (time.time(), dossier)
        return dossier, True

    async def get_or_compute_dossier(
        self,
        industry: Optional[str],
        sector: Optional[str] = None,
    ) -> Optional[IndustryDossier]:
        """`get_or_compute_dossier_with_status` without the transient flag."""
        dossier, _transient = await self.get_or_compute_dossier_with_status(industry, sector)
        return dossier

    async def get_or_compute_dossier_with_status(
        self,
        industry: Optional[str],
        sector: Optional[str] = None,
    ) -> tuple[Optional[IndustryDossier], bool]:
        """Read path with an on-the-fly fallback. Returns (dossier, transient).

        - A stored row with a real Census/FRED TAM is returned untouched — no live
          call. A grounded global row reads as a placeholder (`_withdraw_grounded_tam`).
        - A stored ZERO PLACEHOLDER, or no row at all, triggers a live compute
          (the same 4-tier chain the quarterly job uses), bounded by
          `_LIVE_COMPUTE_TIMEOUT_SECONDS` and shared per (industry, sector).
          A usable result is merged over the stored row in memory (concentration
          kept — `_merge_live_tam`) and memoized for 5 min. It is NEVER written
          to Supabase: the quarterly `recompute_all` owns persistence and its
          guards, and a third writer on the request path would race them.
        - A failed / timed-out / unusable live compute leaves the placeholder
          (TAM 0 → the report shows "—") and is not retried for 5 min.
        - A Supabase READ failure returns None with no live compute: we cannot
          tell what the stored row holds, and a guess would be baked into the
          close-aligned report caches.

        `transient` is True when the answer is a momentary hole rather than the
        industry's real state — a Supabase read failure, a live compute that
        raised / timed out / was cancelled, or every FRED tier failing while
        FRED is configured. The report collector records it on
        `degraded_sections`, so that report is delivered but never shared-cached
        for the rest of the close cycle (the next caller retries).

        Sector comes from the FMP profile and feeds the tier-3 sector-FRED
        fallback; the stored row's sector wins when it has one.
        """
        if not industry:
            return None, False

        generation = type(self)._generation
        stored, read_ok = await self._read_dossier(industry)
        if not read_ok:
            return None, True
        if stored is not None and not _is_placeholder(stored):
            return stored, False

        memo = self._heal_failed_at.get(industry)
        if memo is not None and (time.time() - memo[0]) < self._CACHE_TTL_SECONDS:
            return stored, memo[1]

        live_sector = (stored.sector if stored is not None and stored.sector else None) or sector or "Unknown"
        live, transient = await self._shared_live_compute(industry, live_sector)
        if not _usable_live(live):
            return stored, transient  # placeholder (or None) — the leader logged + memoized

        if stored is not None:
            result = _merge_live_tam(stored, live)
            if industry not in self._heal_logged:
                self._heal_logged.add(industry)
                logger.warning(
                    "industry_dossier self-heal: stored row for %r is a zero placeholder "
                    "(%r) — serving a live figure in memory: TAM %.1fB→%.1fB (%s→%s), "
                    "CAGR %s, grain=%s, label=%r. The next quarterly recompute should "
                    "persist a real row; if this line keeps appearing after one, it did not.",
                    industry, stored.source_label, result.current_tam, result.future_tam,
                    result.current_year, result.future_year, result.cagr_5y_pct,
                    result.source_grain, result.source_label,
                )
        else:
            # No row at all (industry outside the universe file). The live compute
            # ran with no constituents, so it has no concentration to offer — None,
            # never the "fragmented" default, which would override the focal
            # ticker's peer-derived concentration in `_apply_tam_source`.
            result = dataclasses.replace(live, concentration_label=None)
            logger.info(
                "industry_dossier miss for %r (sector=%r) — serving a live figure "
                "(TAM %.1fB, grain=%s); not persisted",
                industry, live_sector, result.current_tam, result.source_grain,
            )

        # Skip the memo when a recompute/override reset the cache since this call
        # started (including during the Supabase read) — their freshly written row
        # must win the next read.
        if type(self)._generation == generation:
            self._cache[industry] = (time.time(), result)
        return result, False

    async def _shared_live_compute(
        self, industry: str, sector: str,
    ) -> tuple[Optional[IndustryDossier], bool]:
        """One bounded live `_compute_one` per (industry, sector) at a time.
        Returns (live, transient).

        Never raises an ordinary exception: failure, timeout and an unusable
        result all come back as None-or-unusable, logged and negatively memoized
        by the LEADER. Joiners share the leader's result; if the leader was
        cancelled they get (None, transient) without touching the memo (a
        cancelled request says nothing about FRED). Only the merge is per
        caller, so a joiner is never handed a row built from someone else's
        stored row.
        """
        key = (industry, sector)
        inflight = self._live_inflight.get(key)
        if inflight is not None:
            try:
                return await asyncio.shield(inflight)
            except Exception:
                return None, True

        loop = asyncio.get_running_loop()
        fut: "asyncio.Future" = loop.create_future()
        self._live_inflight[key] = fut
        try:
            live: Optional[IndustryDossier]
            transient = False
            try:
                live = await asyncio.wait_for(
                    self._compute_one(
                        industry=industry, sector=sector, tickers=[], caps_by_ticker={},
                    ),
                    timeout=self._LIVE_COMPUTE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "industry_dossier live compute TIMED OUT for %r (sector=%r) after "
                    "%.0fs — keeping the stored placeholder; retry after %ds",
                    industry, sector, self._LIVE_COMPUTE_TIMEOUT_SECONDS,
                    self._CACHE_TTL_SECONDS,
                )
                live, transient = None, True
            except Exception as exc:
                logger.warning(
                    "industry_dossier live compute FAILED for %r (sector=%r): %s: %s — "
                    "keeping the stored placeholder; retry after %ds",
                    industry, sector, type(exc).__name__, exc, self._CACHE_TTL_SECONDS,
                    exc_info=True,
                )
                live, transient = None, True

            if not _usable_live(live):
                if live is not None:
                    # `_compute_one` synthesizes a TAM-0 placeholder only when every
                    # FRED tier failed. With FRED configured that is an outage
                    # (transient); unconfigured, it is permanent — and treating it as
                    # transient would keep every such report out of the shared caches.
                    if not _finite_positive(live.current_tam):
                        transient = _fred_is_configured()
                    logger.warning(
                        "industry_dossier live compute for %r (sector=%r) gave no usable "
                        "TAM (current=%r future=%r label=%r) — keeping the stored "
                        "placeholder; retry after %ds",
                        industry, sector, live.current_tam, live.future_tam,
                        live.source_label, self._CACHE_TTL_SECONDS,
                    )
                self._heal_failed_at[industry] = (time.time(), transient)
            outcome = (live, transient)
            if not fut.done():
                fut.set_result(outcome)
            return outcome
        except BaseException as exc:
            # Anything that escaped the handlers above (a cancel, or a bug in the
            # logging/usability code): resolve the shared future so no joiner
            # hangs, then re-raise on our own frame.
            fail_shared_future(
                fut,
                exc if isinstance(exc, Exception)
                else RuntimeError(f"industry_dossier live compute for {industry!r} was cancelled"),
            )
            raise
        finally:
            self._live_inflight.pop(key, None)

    # ── Write path (weekly batch) ──

    async def recompute_all(self, force: bool = False) -> Dict[str, Any]:
        """Walk every industry in the universe file and upsert a fresh
        dossier row for each.

        Market caps are read from the universe file itself (written by
        `discover_industries.py` at quarterly cadence). This avoids
        hitting FMP rate limits on the 9K+ tickers in the universe and
        keeps the weekly job fast and side-effect-free w.r.t. FMP. Caps
        go quarterly-stale at most — fine for relative-share HHI.

        `force` is accepted for parity with the sector_benchmarks job's
        signature but currently has no freshness gate (this job only
        fires weekly from main.py — there's nothing to skip).

        Raises `IndustryDossierRecomputeSkipped` (after an ERROR log) when the
        run cannot start — an empty universe or no upstream credentials — so the
        quarterly chain's claim stays UNSETTLED and the phase is retried. Nothing
        is written on that path; the previous snapshot stays in place.
        """
        universe = _load_universe()
        if not universe:
            # `load_universe` already logged the Storage/parse reason at ERROR; this line
            # names the consequence for the dossier.
            exc = IndustryDossierRecomputeSkipped(
                "empty universe",
                "industry_universe.json could not be loaded (it is not in git: the Supabase "
                "Storage download failed, or discover_industries.py never published it). "
                "Nothing was written; the previous snapshot is left untouched.",
            )
            logger.error("%s", exc)
            raise exc

        # ⚠️ REFUSE TO RUN WITH NO UPSTREAM CREDENTIALS.
        #
        # Phase A's TAM comes from Census (AIES / Economic Census) with a FRED fallback. With
        # NEITHER configured, every industry resolves to the "No public data available —
        # FRED/Census unreachable at compute time" placeholder with `current_tam_b = 0` — and
        # the upsert below then OVERWRITES every existing good row with it.
        #
        # That is not hypothetical: it already happened in production. A live query on
        # 2026-08-07 found 138 of 158 rows zeroed with exactly that source label, because
        # FRED_API_KEY and CENSUS_API_KEY are set in `backend/.env` but were never set on
        # Railway. Every industry stays at zero until someone notices the Moat TAM row has silently vanished app-wide.
        #
        # A quarterly job that destroys data when a credential is missing is worse than one
        # that does not run: skipping leaves the last good snapshot in place, which is stale
        # at worst. Loud, and greppable.
        from app.integrations.census import get_census_client  # noqa: PLC0415
        from app.integrations.fred import get_fred_client      # noqa: PLC0415

        if not get_fred_client().is_configured and not get_census_client().is_configured:
            exc = IndustryDossierRecomputeSkipped(
                "no upstream credentials",
                "neither FRED_API_KEY nor CENSUS_API_KEY is configured, so every industry "
                "would resolve to a zero-TAM placeholder and OVERWRITE the existing rows. "
                "Set the keys and re-run; the previous snapshot is left untouched.",
            )
            logger.error("%s", exc)
            raise exc

        started = time.time()

        # Compute a dossier per industry — caps come from the universe
        # file's pre-captured `market_caps` (per-industry) dict.
        dossiers: List[IndustryDossier] = []
        for entry in universe:
            industry = entry.get("industry")
            sector = entry.get("sector")
            tickers = entry.get("tickers") or []
            caps_by_ticker = entry.get("market_caps") or {}
            if not industry or not sector:
                continue
            try:
                dossier = await self._compute_one(industry, sector, tickers, caps_by_ticker)
                dossiers.append(dossier)
            except CensusUnavailableException as exc:
                # Transient: keep the stored row rather than persist a broader
                # fallback for a quarter. The read path heals a zero row.
                logger.warning(
                    "dossier compute skipped for industry=%r sector=%r — Census "
                    "unavailable (%s); the stored row is kept",
                    industry, sector, exc,
                )
            except Exception as exc:
                logger.error(
                    "dossier compute failed for industry=%r sector=%r: %s",
                    industry, sector, exc, exc_info=True,
                )

        # 3. Upsert in chunks. Supabase's batch upsert supports several
        # hundred rows per call; 100 is a safe ceiling.
        rows_upserted = 0
        if dossiers:
            sb = get_supabase()
            rows = [d.to_db_row() for d in dossiers]

            # PER-ROW GUARD: never replace a real TAM with a zero placeholder.
            #
            # The credential check at the top of this method covers "nothing is configured".
            # This covers the narrower case that still destroys data: one upstream configured,
            # but a specific industry resolving to the placeholder anyway — an AIES gap, a FRED
            # series that stopped publishing, a transient upstream failure during the run.
            # Upserting that over a row with a real number turns a temporary lookup miss into
            # permanent data loss, and the next run has nothing to restore from.
            #
            # Skipping leaves the previous value in place, which is stale at worst — and stale
            # is what the `computed_at` column is for.
            try:
                existing = (
                    (await sb_exec(
                        sb.table("industry_dossier")
                        .select("industry, current_tam_b, tam_scope, source_grain")
                    ))
                )
                # A GROUNDED global row (`tam_scope='global'`, the retired Phase B) is
                # not a real figure to protect: it must be REPLACED by this run, even by
                # a placeholder (Grounding with Google Search terms; migration 188).
                # Counting it here would let the zero-guard below keep it for a quarter.
                has_real_tam = {
                    r["industry"] for r in (existing.data or [])
                    if (r.get("current_tam_b") or 0) > 0
                    and r.get("tam_scope") != GROUNDED_TAM_SCOPE
                }
                # Rows that already hold an INDUSTRY-grain real figure. A run that
                # resolves one of them to a broader source for an industry that is
                # MAPPED to an industry-grain source (Census NAICS, or a FRED series
                # in FRED_SERIES_MATCHES_INDUSTRY) hit a transient miss on that
                # source — writing it would hide the card's TAM for a quarter.
                industry_grain_rows = {
                    r["industry"] for r in (existing.data or [])
                    if (r.get("current_tam_b") or 0) > 0
                    and r.get("source_grain") == "industry"
                    and r.get("tam_scope") != GROUNDED_TAM_SCOPE
                }
            except Exception as exc:
                # Fail SAFE: if we cannot tell which rows are good, do not risk clobbering
                # them. An empty set would make the filter below a no-op.
                logger.error(
                    "industry_dossier pre-read failed (%s) — skipping the upsert rather than "
                    "risking an overwrite of good rows", exc, exc_info=True,
                )
                has_real_tam = None
                industry_grain_rows = set()

            if has_real_tam is None:
                rows = []
            else:
                kept, skipped, kept_grain = [], [], []
                for row in rows:
                    ind = row["industry"]
                    would_zero = (row.get("current_tam_b") or 0) <= 0
                    downgrades = (
                        row.get("source_grain") != "industry"
                        and ind in industry_grain_rows
                        and expects_industry_grain(ind)
                    )
                    if would_zero and ind in has_real_tam:
                        skipped.append(ind)
                    elif downgrades:
                        kept_grain.append(ind)
                    else:
                        kept.append(row)
                if kept_grain:
                    logger.warning(
                        "industry_dossier: kept the EXISTING industry-level TAM for %d "
                        "industr%s because this run fell back to a broader source (%s)",
                        len(kept_grain), "y" if len(kept_grain) == 1 else "ies",
                        ", ".join(sorted(kept_grain)),
                    )
                if skipped:
                    logger.warning(
                        "industry_dossier: kept the EXISTING TAM for %d industr%s because "
                        "this run resolved them to a zero placeholder (%s%s)",
                        len(skipped), "y" if len(skipped) == 1 else "ies",
                        ", ".join(sorted(skipped)[:8]),
                        "…" if len(skipped) > 8 else "",
                    )
                rows = kept

            for batch in _chunked(rows, 100):
                try:
                    (await sb_exec(
                        sb.table("industry_dossier").upsert(
                        batch, on_conflict="industry"
                        )
                    ))
                    rows_upserted += len(batch)
                except Exception as exc:
                    logger.error("industry_dossier upsert failed: %s", exc, exc_info=True)

        # 4. Reset the in-memory tier so the freshly-upserted rows are
        # read on the next request.
        self.reset_cache()

        elapsed = time.time() - started
        result = {
            "status": "ok",
            "universe_size": len(universe),
            "rows_upserted": rows_upserted,
            "elapsed_seconds": round(elapsed, 1),
        }
        logger.info("industry_dossier recompute: %s", result)
        return result

    async def _compute_one(
        self,
        industry: str,
        sector: str,
        tickers: List[str],
        caps_by_ticker: Dict[str, float],
    ) -> IndustryDossier:
        """Resolve TAM/CAGR/lifecycle/concentration for one industry.

        Fallback chain — always returns a populated dossier (no nulls):
          1. industry-specific Census (NAICS)        source_grain='industry'
          2. industry-mapped FRED                    'industry' when the series
             measures the industry itself (FRED_SERIES_MATCHES_INDUSTRY), else
             'sector' — most of these map onto a whole 2-digit NAICS sector
          3. sector-level FRED                       source_grain='sector'
          4. all-industry USNGSP                     source_grain='all_industry'

        Concentration / HHI / lifecycle derive from the universe-supplied
        constituent tickers' market caps (refreshed live by `recompute_all`).
        """
        # ── Tier 1 + 2: industry-level (Census or FRED) ──
        tam_proxy: Optional[IndustryTAM] = None
        source_grain = "industry"

        if industry in INDUSTRY_TO_CENSUS:
            tam_proxy = await _try_census_tam(industry)
        if tam_proxy is None and industry in INDUSTRY_TO_FRED_SERIES:
            tam_proxy = await _try_fred_tam(industry)
            if tam_proxy is not None:
                source_grain = fred_mapping_grain(industry)

        # ── Tier 3: sector-level FRED ──
        if tam_proxy is None:
            sector_series = _SECTOR_TO_FRED_SERIES.get(sector)
            if sector_series:
                label = _SECTOR_FRED_LABELS.get(sector_series, f"BEA {sector_series} (via FRED)")
                tam_proxy = await fred_tam_for_series(
                    sector_series,
                    source_label=f"{label} — broader than {industry}",
                )
                if tam_proxy is not None:
                    source_grain = "sector"

        # ── Tier 4: all-industry USNGSP ──
        if tam_proxy is None:
            tam_proxy = await fred_tam_for_series(
                _ALL_INDUSTRY_FRED_SERIES,
                source_label=_ALL_INDUSTRY_FRED_LABEL,
            )
            if tam_proxy is not None:
                source_grain = "all_industry"

        if tam_proxy is None:
            # Every fallback failed (FRED API down + no env key). Synthesize
            # a "data unavailable" placeholder (TAM 0 → the report shows "—").
            # recompute_all never lets it overwrite a real row, and the read
            # path treats a stored placeholder as a miss (self-heal).
            now_year = str(datetime.now(timezone.utc).year)
            tam_proxy = IndustryTAM(
                current_tam=0.0,
                future_tam=0.0,
                current_year=now_year,
                future_year=str(int(now_year) + 5),
                source_label="No public data available — FRED/Census unreachable at compute time",
                cagr_5y_pct=None,
            )
            source_grain = "all_industry"

        # ── Concentration from S&P 500 constituents in this industry ──
        caps = [
            caps_by_ticker[sym.upper()]
            for sym in tickers
            if sym and sym.upper() in caps_by_ticker
        ]
        hhi: Optional[float] = None
        top1_share_pct: Optional[float] = None
        top2_share_pct: Optional[float] = None
        concentration_label: Optional[str] = None
        constituent_count: Optional[int] = len(caps) if caps else None

        if len(caps) >= 3:
            caps_sorted = sorted(caps, reverse=True)
            total = sum(caps_sorted)
            top1_share_pct = round((caps_sorted[0] / total) * 100.0, 2)
            top2_share_pct = round(((caps_sorted[0] + caps_sorted[1]) / total) * 100.0, 2)
            hhi = round(_compute_hhi(caps_sorted), 2)
            concentration_label = classify_concentration(
                top1_share_pct, top2_share_pct, hhi
            )
        else:
            # Too few public players for HHI to be informative. Default
            # to "fragmented" rather than null so the iOS UI shows
            # something — fabricating "monopoly" from 1 public ticker
            # would be misleading (the industry has private competitors
            # we can't see).
            concentration_label = "fragmented"

        lifecycle = classify_lifecycle(tam_proxy.cagr_5y_pct, len(caps))

        return IndustryDossier(
            current_tam=tam_proxy.current_tam,
            future_tam=tam_proxy.future_tam,
            current_year=tam_proxy.current_year,
            future_year=tam_proxy.future_year,
            source_label=tam_proxy.source_label,
            cagr_5y_pct=tam_proxy.cagr_5y_pct,
            industry=industry,
            sector=sector,
            lifecycle_phase=lifecycle,
            hhi=hhi,
            top1_share_pct=top1_share_pct,
            top2_share_pct=top2_share_pct,
            concentration_label=concentration_label,
            constituent_count=constituent_count,
            source_grain=source_grain,
        )


def _chunked(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


_service_singleton: Optional[IndustryDossierService] = None


def get_industry_dossier_service() -> IndustryDossierService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = IndustryDossierService()
    return _service_singleton
