"""
Ticker COLLECTION cache — the persona-NEUTRAL data layer (24h TTL, Supabase).

`TickerReportDataCollector.collect()` produces a `CollectedTickerData` that is
identical for every persona: persona_key only labels `meta.agent` and drives the
per-persona score, and BOTH happen AFTER collection. This caches that collection
by TICKER so personas 2..N — and later users on the same ticker — skip the
~20-call FMP fan-out + deterministic assembly and only re-run the genuinely
per-persona layer (scoring + Stage B narratives). It sits BELOW the
per-(ticker,persona) `ticker_report_cache`: a different-persona request misses
the report cache, then HITS this collection cache.

Design (intentional deviation from the usual two-tier in-memory+Supabase norm):
  - Supabase `ticker_data_cache` (24h + shared CACHE_SCHEMA_FLOOR) is the cache.
    Reads deserialize a FRESH object every time, so there is NO shared mutable
    live object across personas/requests (these objects are large and re-read by
    assemble_report — a fresh copy is the safe choice). A warm hit is one DB
    round-trip (~tens of ms) versus seconds of FMP fan-out.
  - A module-level `_inflight` map collapses concurrent first-callers for the
    same ticker into a single fetch (thundering-herd guard).

FAIL-SAFE: serialization, deserialization, and DB calls NEVER raise. They log and
degrade to a cache MISS (a fresh collect), so a serialization imperfection can
only slow a request, never corrupt a report. Reconstruction is additionally
post-validated (profile + computed present) before it's trusted.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.database import get_supabase
# Shared close-aligned freshness (schema floor + trading-close cycle) so this
# collection cache refreshes on the SAME boundary as ticker_report_cache —
# critical, else a post-close report regen would reuse stale historical here.
from app.services.ticker_report_cache import is_cache_fresh

# Typed objects carried on CollectedTickerData, reconstructed on read.
from app.schemas.analyst import AnalystAnalysisResponse
from app.schemas.holders import HoldersResponse
from app.schemas.revenue_breakdown import RevenueBreakdownResponse
from app.schemas.signal_of_confidence import SignalOfConfidenceResponse
from app.schemas.earnings import EarningsResponse
from app.schemas.stock_overview import SnapshotItemResponse
from app.services.sector_aggregates_service import SectorAggregates
from app.services.industry_tam_service import IndustryTAM

logger = logging.getLogger(__name__)

CACHE_TTL_HOURS = 24
TABLE_NAME = "ticker_data_cache"

# Pydantic fields → model class (reconstructed via model_validate; model_dump
# (mode="json") handles any nested dates/enums on write).
_PYDANTIC_FIELDS: Dict[str, Any] = {
    "analyst_analysis": AnalystAnalysisResponse,
    "holders_response": HoldersResponse,
    "revenue_breakdown": RevenueBreakdownResponse,
    "signal_of_confidence": SignalOfConfidenceResponse,
    "earnings": EarningsResponse,
    "snap_profitability": SnapshotItemResponse,
    "snap_health": SnapshotItemResponse,
    "snap_growth": SnapshotItemResponse,
    "snap_valuation": SnapshotItemResponse,
}

# Flat dataclass fields → (class, [datetime field names needing ISO round-trip]).
_DATACLASS_FIELDS: Dict[str, Any] = {
    "sector_aggregates": (SectorAggregates, ["computed_at"]),
    "industry_tam": (IndustryTAM, []),
}

# Concurrent-fetch dedup, keyed by ticker (module-level so it spans collector
# instances, which are created per ResearchAgent run).
_INFLIGHT: Dict[str, "asyncio.Future"] = {}


# ── Serialization (fail-safe) ───────────────────────────────────────


def _serialize_computed(computed: Any) -> Any:
    """`computed` is a plain dict EXCEPT `recent_price_dates` (List[date]).
    Convert those dates to ISO strings; everything else is already JSON-safe."""
    if not isinstance(computed, dict):
        return computed
    out = dict(computed)
    rpd = out.get("recent_price_dates")
    if isinstance(rpd, list):
        out["recent_price_dates"] = [
            d.isoformat() if isinstance(d, (date, datetime)) else d for d in rpd
        ]
    return out


def _deserialize_computed(computed: Any) -> Any:
    if not isinstance(computed, dict):
        return computed
    out = dict(computed)
    rpd = out.get("recent_price_dates")
    if isinstance(rpd, list):
        parsed = []
        for s in rpd:
            try:
                parsed.append(date.fromisoformat(s) if isinstance(s, str) else s)
            except ValueError:
                parsed.append(s)
        out["recent_price_dates"] = parsed
    return out


def _serialize(out: Any) -> Optional[Dict[str, Any]]:
    """CollectedTickerData → JSON-clean dict, or None if it can't be made clean
    (in which case we simply don't cache — never raise)."""
    try:
        result: Dict[str, Any] = {}
        for f in dataclasses.fields(out):
            name = f.name
            val = getattr(out, name)
            if val is None:
                result[name] = None
            elif name in _PYDANTIC_FIELDS:
                result[name] = val.model_dump(mode="json")
            elif name in _DATACLASS_FIELDS:
                _, dt_fields = _DATACLASS_FIELDS[name]
                d = dataclasses.asdict(val)
                for k in dt_fields:
                    if isinstance(d.get(k), (datetime, date)):
                        d[k] = d[k].isoformat()
                result[name] = d
            elif name == "computed":
                result[name] = _serialize_computed(val)
            else:
                result[name] = val
        # Guarantee JSON-clean WITHOUT default=str (which would silently stringify
        # an unexpected type and round-trip wrong). A raise here → don't cache.
        json.dumps(result)
        return result
    except Exception as e:
        logger.warning(
            "ticker_data_cache serialize failed: %s: %s", type(e).__name__, e
        )
        return None


def _deserialize(data: Dict[str, Any], field_names: set) -> Optional[Any]:
    """JSON dict → CollectedTickerData, or None on any failure / incomplete
    reconstruction (→ treated as a cache miss). `field_names` is the live set of
    dataclass field names so a removed field in stale data degrades to a miss."""
    try:
        from app.services.agents.ticker_report_data_collector import (
            CollectedTickerData,
        )

        kwargs: Dict[str, Any] = {}
        for name, val in data.items():
            if name not in field_names:
                continue  # field no longer on the dataclass → skip (fail-safe)
            if val is None:
                kwargs[name] = None
            elif name in _PYDANTIC_FIELDS:
                kwargs[name] = _PYDANTIC_FIELDS[name].model_validate(val)
            elif name in _DATACLASS_FIELDS:
                cls, dt_fields = _DATACLASS_FIELDS[name]
                d = dict(val)
                for k in dt_fields:
                    if isinstance(d.get(k), str):
                        d[k] = datetime.fromisoformat(d[k])
                kwargs[name] = cls(**d)
            elif name == "computed":
                kwargs[name] = _deserialize_computed(val)
            else:
                kwargs[name] = val

        out = CollectedTickerData(**kwargs)
        # Trust only a structurally-complete reconstruction.
        if not out.profile or not out.computed:
            logger.warning(
                "ticker_data_cache deserialize incomplete (missing profile/"
                "computed) — treating as miss"
            )
            return None
        return out
    except Exception as e:
        logger.warning(
            "ticker_data_cache deserialize failed: %s: %s", type(e).__name__, e
        )
        return None


# ── Supabase read / write (fail-safe) ───────────────────────────────


async def get_cached_collection(ticker: str) -> Optional[Any]:
    """Return a FRESH persona-neutral CollectedTickerData for `ticker` if a
    cache row is < 24h old and on/after the schema floor, else None."""
    ticker = ticker.upper().strip()

    def _query() -> Optional[Dict[str, Any]]:
        try:
            supabase = get_supabase()
            row = (
                supabase.table(TABLE_NAME)
                .select("collected_data, cached_at")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None
            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None
            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            if not is_cache_fresh(cached_at):
                logger.info("ticker_data_cache STALE/PRE-FLOOR for %s", ticker)
                return None
            data = entry.get("collected_data")
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.warning(
                "ticker_data_cache read failed for %s: %s: %s",
                ticker, type(e).__name__, e,
            )
            return None

    data = await asyncio.to_thread(_query)
    if data is None:
        return None

    # Deserialize off the event loop too — it touches Pydantic validation which
    # can be non-trivial for big payloads.
    from app.services.agents.ticker_report_data_collector import CollectedTickerData
    field_names = {f.name for f in dataclasses.fields(CollectedTickerData)}
    out = await asyncio.to_thread(_deserialize, data, field_names)
    if out is not None:
        logger.info("ticker_data_cache HIT for %s", ticker)
    return out


async def store_collection(ticker: str, out: Any) -> None:
    """Write/refresh the cache row. Fire-and-forget: failures are logged, never
    raised, and a serialization failure simply skips the write."""
    ticker = ticker.upper().strip()
    payload = await asyncio.to_thread(_serialize, out)
    if payload is None:
        return

    def _upsert() -> None:
        try:
            get_supabase().table(TABLE_NAME).upsert(
                {
                    "ticker": ticker,
                    "collected_data": payload,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker",
            ).execute()
            logger.info("ticker_data_cache UPSERTED for %s", ticker)
        except Exception as e:
            logger.warning(
                "ticker_data_cache upsert failed for %s: %s: %s",
                ticker, type(e).__name__, e,
            )

    await asyncio.to_thread(_upsert)


# ── Cache-or-collect with in-flight dedup ───────────────────────────


async def get_or_collect(ticker: str, fetch_fresh) -> Any:
    """Return the persona-neutral CollectedTickerData for `ticker` from cache, or
    run `fetch_fresh()` (async callable → CollectedTickerData), store it, and
    return it. Concurrent first-callers for the same ticker share one fetch.

    The returned object is persona-NEUTRAL; the caller applies its persona."""
    ticker = ticker.upper().strip()

    cached = await get_cached_collection(ticker)
    if cached is not None:
        return cached

    inflight = _INFLIGHT.get(ticker)
    if inflight is not None:
        return await inflight  # a concurrent caller is already fetching this ticker

    loop = asyncio.get_running_loop()
    fut: "asyncio.Future" = loop.create_future()
    _INFLIGHT[ticker] = fut
    try:
        out = await fetch_fresh()
        await store_collection(ticker, out)
        if not fut.done():
            fut.set_result(out)
        return out
    except Exception as e:
        if not fut.done():
            fut.set_exception(e)
        raise
    finally:
        _INFLIGHT.pop(ticker, None)
