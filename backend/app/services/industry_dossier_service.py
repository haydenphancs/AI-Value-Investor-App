"""Industry dossier — pre-computed TAM / CAGR / lifecycle / concentration
for every FMP industry.

Today the TickerReport "Moat & Competition" section calls FRED + Census
*live* per report. This service replaces that hot-path latency with a
weekly batch: every Sunday 2 AM local, `recompute_all` walks every
industry in `backend/data/industry_universe.json` (built by
`backend/scripts/discover_industries.py`), computes a fresh dossier
row, and upserts to the `industry_dossier` Supabase table.

Read path: `get_dossier(industry)` is called from
`ticker_report_data_collector._fetch_dependent` instead of
`industry_tam_service.get_industry_tam`. In-memory tier (5 min TTL) +
Supabase row by `industry`. The returned `IndustryDossier` is shaped
to be a superset of `IndustryTAM` so the existing `_apply_tam_source`
logic keeps working when only TAM fields are read.

Coverage chain — never returns null for a discovered industry:
    1. Census 4-digit NAICS    → source_grain='industry'
    2. Industry-specific FRED  → source_grain='industry'
    3. Sector-level FRED       → source_grain='sector'
    4. All-industry FRED USNGSP → source_grain='all_industry'

iOS shows a "⚠ Broader than industry" chip when source_grain != 'industry'.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
    fred_tam_for_series,
)

logger = logging.getLogger(__name__)


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
_UNIVERSE_PATH = Path(__file__).resolve().parents[2] / "data" / "industry_universe.json"


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
    # Scope of the TAM figure: 'us' (Census/FRED US-domestic, the Phase A
    # default) or 'global' (Gemini grounded research via industry_override_
    # service, Phase B). Lets the report explicitly label US vs Global.
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
    before `scripts/discover_industries.py` has run). In that case
    `recompute_all` logs a warning and bails — there's nothing to do
    until discovery runs.
    """
    if not _UNIVERSE_PATH.exists():
        logger.warning(
            "industry_universe.json not found at %s — run "
            "`python backend/scripts/discover_industries.py` first",
            _UNIVERSE_PATH,
        )
        return []
    try:
        data = json.loads(_UNIVERSE_PATH.read_text())
        return data.get("industries", []) or []
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Failed to load industry universe: %s", exc)
        return []


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

    def __init__(self) -> None:
        self._fmp: Optional[FMPClient] = None

    @classmethod
    def reset_cache(cls) -> None:
        cls._cache.clear()

    def _get_fmp(self) -> FMPClient:
        if self._fmp is None:
            self._fmp = FMPClient()
        return self._fmp

    # ── Read path ──

    async def get_dossier(self, industry: Optional[str]) -> Optional[IndustryDossier]:
        """Pure read: in-memory → Supabase. Returns None on miss.

        Most callers should use `get_or_compute_dossier` instead — it
        falls back to a live FRED/Census compute when the weekly batch
        hasn't covered an industry yet, so every ticker gets data.
        """
        if not industry:
            return None

        # 1. in-memory
        entry = self._cache.get(industry)
        if entry and (time.time() - entry[0]) < self._CACHE_TTL_SECONDS:
            return entry[1]

        # 2. Supabase
        try:
            sb = get_supabase()
            res = (
                sb.table("industry_dossier")
                .select("*")
                .eq("industry", industry)
                .limit(1)
                .execute()
            )
            rows = res.data or []
        except Exception as exc:
            logger.warning("industry_dossier read failed for %r: %s", industry, exc)
            return None

        if not rows:
            return None

        dossier = IndustryDossier.from_db_row(rows[0])
        self._cache[industry] = (time.time(), dossier)
        return dossier

    async def get_or_compute_dossier(
        self,
        industry: Optional[str],
        sector: Optional[str] = None,
    ) -> Optional[IndustryDossier]:
        """Read path with on-the-fly fallback — GUARANTEES coverage.

        If the dossier exists (in-memory or Supabase), return it.
        Otherwise compute one live using the same 4-tier fallback chain
        the weekly job uses (Census → industry-FRED → sector-FRED →
        all-industry USNGSP). The live-computed dossier is memoized for
        5 minutes so concurrent reports for the same industry don't
        re-hit FRED.

        Sector is taken from the FMP profile and is needed for tier-3
        sector-level FRED fallback. When None, we skip tier 3 and fall
        through directly to the all-industry tier.

        Returns None only when `industry` itself is empty/None — every
        non-empty industry string resolves to at least an all-industry
        proxy.
        """
        if not industry:
            return None

        cached = await self.get_dossier(industry)
        if cached is not None:
            return cached

        # Miss — compute one live. No constituents available in the read
        # path (we don't want to fan out FMP screener calls inline), so
        # HHI/concentration stay null on the live-computed dossier; the
        # focal-ticker's peer-derived concentration in
        # `_build_market_dynamics` still fills that in.
        logger.info(
            "industry_dossier miss for %r (sector=%r) — computing live; "
            "next weekly job will persist it",
            industry, sector,
        )
        dossier = await self._compute_one(
            industry=industry,
            sector=sector or "Unknown",
            tickers=[],
            caps_by_ticker={},
        )
        # Memoize so the same industry across concurrent reports doesn't
        # hammer FRED a second time within the in-memory TTL.
        self._cache[industry] = (time.time(), dossier)
        return dossier

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
        """
        universe = _load_universe()
        if not universe:
            return {"status": "skipped", "reason": "empty universe"}

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
            for batch in _chunked(rows, 100):
                try:
                    sb.table("industry_dossier").upsert(
                        batch, on_conflict="industry"
                    ).execute()
                    rows_upserted += len(batch)
                except Exception as exc:
                    logger.error("industry_dossier upsert failed: %s", exc, exc_info=True)

        # 4. Reset the in-memory tier so the freshly-upserted rows are
        # read on the next request.
        self.reset_cache()

        phase_a_elapsed = time.time() - started

        # Phase B — AI-driven research overrides for the curated
        # globally-traded industries (semis, biotech, pharma, etc.).
        # Lazy import so test paths that don't exercise overrides
        # (persona scoring etc.) don't pull in the Gemini integration.
        phase_b_summary: Optional[Dict[str, Any]] = None
        try:
            from app.services.industry_override_service import (
                get_industry_override_service,
            )
            phase_b_summary = await get_industry_override_service().refresh_all_overrides()
        except Exception as exc:
            logger.error(
                "industry_dossier: Phase B (overrides) failed: %s — Phase A values stay",
                exc, exc_info=True,
            )

        elapsed = time.time() - started
        result = {
            "status": "ok",
            "universe_size": len(universe),
            "rows_upserted": rows_upserted,
            "phase_a_elapsed_seconds": round(phase_a_elapsed, 1),
            "phase_b_summary": phase_b_summary,
            "elapsed_seconds": round(elapsed, 1),
        }
        logger.info("industry_dossier recompute (Phase A + B): %s", {
            k: v for k, v in result.items() if k != "phase_b_summary"
        })
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
          2. industry-specific FRED                  source_grain='industry'
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
            # a "data unavailable" placeholder so the row still upserts —
            # the user sees the iOS warning chip + the source_label tells
            # them what happened.
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
