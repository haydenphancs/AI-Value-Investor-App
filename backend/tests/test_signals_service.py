"""
App-Exclusive Signals — aggregation math + service-degradation tests.

Guards (mirrors testing.md — pure inputs inline, no network / Supabase):
1. The three pure aggregators (`_aggregate_congress` / `_aggregate_whale` /
   `_aggregate_earnings`) rank correctly and degrade honestly on the messy /
   outlier inputs FMP + the whale registry actually produce — distinct-member and
   distinct-CIK dedup, disclosure windowing, surprise thresholds/caps, signed
   misses, missing/zero/NaN fields, class-share folding — never a wrong count,
   never a fabricated card.
2. The service build degrades per-branch (one source failing → that card None,
   the others render) and never caches an all-empty (failed) build.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from app.schemas.home_dashboard import (
    SignalGroupResponse,
    SignalRowResponse,
    SignalsGroupResponse,
)
from app.services import signals_service as ssvc
from app.services.signals_service import (
    _aggregate_congress,
    _aggregate_whale,
    _aggregate_earnings,
)

# Fixed "now" so disclosure windowing is deterministic. 14-day window → on/after 2026-06-16.
NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


# ── 1. Congress aggregation ────────────────────────────────────────────


def test_congress_counts_distinct_members_and_ignores_sales():
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-25",
         "firstName": "Jane", "lastName": "Doe", "assetDescription": "NVIDIA Corp"},
        # SAME member buys NVDA again → must count ONCE.
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-26",
         "firstName": "Jane", "lastName": "Doe"},
        {"symbol": "NVDA", "type": "purchase", "disclosureDate": "2026-06-24",
         "firstName": "John", "lastName": "Smith"},
        {"symbol": "MSFT", "type": "Purchase", "disclosureDate": "2026-06-20",
         "firstName": "Al", "lastName": "Gore"},
    ]
    house = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-23",
         "firstName": "Bob", "lastName": "Roe"},
        # A SALE must be ignored entirely.
        {"symbol": "AAPL", "type": "Sale", "disclosureDate": "2026-06-25",
         "firstName": "X", "lastName": "Y"},
    ]
    g = _aggregate_congress(senate, house, now=NOW)
    assert g is not None and g.kind == "congress"
    assert [(r.symbol, r.value) for r in g.entries] == [("NVDA", 3.0), ("MSFT", 1.0)]
    assert g.entries[0].name == "NVIDIA Corp"        # first non-empty assetDescription
    assert g.as_of_date == "2026-06-26"              # latest disclosure among counted


def test_congress_all_sales_returns_none():
    senate = [{"symbol": "NVDA", "type": "Sale", "disclosureDate": "2026-06-25",
               "lastName": "Doe"}]
    assert _aggregate_congress(senate, [], now=NOW) is None


def test_congress_top_below_min_members_returns_none():
    # Only one member bought anything → no honest "most-bought" headline.
    senate = [{"symbol": "MSFT", "type": "Purchase", "disclosureDate": "2026-06-20",
               "lastName": "Gore"}]
    assert _aggregate_congress(senate, [], now=NOW) is None


def test_congress_folds_class_share_symbol_variants():
    senate = [
        {"symbol": "BRK.B", "type": "Purchase", "disclosureDate": "2026-06-25", "lastName": "A"},
        {"symbol": "BRK-B", "type": "Purchase", "disclosureDate": "2026-06-24", "lastName": "B"},
    ]
    g = _aggregate_congress(senate, [], now=NOW)
    assert g is not None
    assert g.entries[0].symbol == "BRK-B" and g.entries[0].value == 2.0


def test_congress_windows_out_stale_disclosures():
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-25", "lastName": "A"},
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-26", "lastName": "B"},
        {"symbol": "OLD", "type": "Purchase", "disclosureDate": "2026-01-01", "lastName": "C"},
        {"symbol": "OLD", "type": "Purchase", "disclosureDate": "2026-01-02", "lastName": "D"},
    ]
    g = _aggregate_congress(senate, [], now=NOW)
    assert g is not None
    assert [r.symbol for r in g.entries] == ["NVDA"]   # OLD (January) excluded by window


def test_congress_falls_back_to_all_rows_when_no_date_parses():
    # Degenerate feed: no parseable dates → keep buys so the card stays alive; as_of None.
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "lastName": "A"},
        {"symbol": "NVDA", "type": "Purchase", "lastName": "B"},
    ]
    g = _aggregate_congress(senate, [], now=NOW)
    assert g is not None and g.entries[0].value == 2.0 and g.as_of_date is None


def test_congress_identifies_member_by_office_when_name_missing():
    senate = [
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-25", "office": "Jane Doe"},
        {"symbol": "NVDA", "type": "Purchase", "disclosureDate": "2026-06-24", "office": "John Smith"},
    ]
    g = _aggregate_congress(senate, [], now=NOW)
    assert g is not None and g.entries[0].value == 2.0


def test_congress_empty_inputs_return_none():
    assert _aggregate_congress([], [], now=NOW) is None
    assert _aggregate_congress("garbage", None, now=NOW) is None


# ── 2. Whale aggregation (distinct CIK) ────────────────────────────────


def test_whale_dedups_person_and_fund_sharing_a_cik():
    # w1 (person) and w2 (their fund) share ONE CIK → count as one fund, not two.
    cik_map = {"w1": "CIK1", "w2": "CIK1", "w3": "CIK2"}
    rows = [
        {"whale_id": "w1", "ticker": "NVDA", "change_percent": 2.0, "company_name": "NVIDIA"},
        {"whale_id": "w2", "ticker": "NVDA", "change_percent": 1.5},
        {"whale_id": "w3", "ticker": "NVDA", "change_percent": 0.5},
    ]
    g = _aggregate_whale(rows, cik_map)
    assert g is not None and g.kind == "whale"
    assert g.entries[0].symbol == "NVDA" and g.entries[0].value == 2.0   # {CIK1, CIK2}
    assert g.entries[0].name == "NVIDIA"


def test_whale_registry_of_25_with_6_shared_pairs_yields_19_funds():
    # Mirrors the real registry: 6 person↔fund pairs share a CIK, 13 singles → 19 distinct.
    cik_map = {}
    for i in range(6):
        cik_map[f"p{i}a"] = f"PAIR{i}"
        cik_map[f"p{i}b"] = f"PAIR{i}"
    for i in range(13):
        cik_map[f"s{i}"] = f"SOLO{i}"
    assert len(cik_map) == 25
    rows = [{"whale_id": wid, "ticker": "NVDA", "change_percent": 1.0} for wid in cik_map]
    g = _aggregate_whale(rows, cik_map)
    assert g is not None and g.entries[0].value == 19.0


def test_whale_null_cik_whales_stay_distinct():
    # Sentinel keys (as the service builds for null-cik whales) must NOT collapse.
    cik_map = {"w1": "nocik:w1", "w2": "nocik:w2"}
    rows = [
        {"whale_id": "w1", "ticker": "NVDA", "change_percent": 1.0},
        {"whale_id": "w2", "ticker": "NVDA", "change_percent": 1.0},
    ]
    g = _aggregate_whale(rows, cik_map)
    assert g is not None and g.entries[0].value == 2.0


def test_whale_excludes_non_positive_change_and_non_registry_whales():
    cik_map = {"w1": "C1", "w2": "C2", "w3": "C3", "w4": "C4"}
    rows = [
        {"whale_id": "w1", "ticker": "NVDA", "change_percent": 1.0},
        {"whale_id": "w4", "ticker": "NVDA", "change_percent": 3.0},
        {"whale_id": "w2", "ticker": "NVDA", "change_percent": 0},      # not adding
        {"whale_id": "w3", "ticker": "NVDA", "change_percent": -2.0},   # trimming
        {"whale_id": "wX", "ticker": "NVDA", "change_percent": 5.0},    # not a 13F whale
    ]
    g = _aggregate_whale(rows, cik_map)
    assert g is not None and g.entries[0].value == 2.0   # {C1, C4}


def test_whale_top_below_min_funds_returns_none():
    cik_map = {"w1": "C1"}
    rows = [{"whale_id": "w1", "ticker": "NVDA", "change_percent": 1.0}]
    assert _aggregate_whale(rows, cik_map) is None


def test_whale_empty_returns_none_and_passes_through_as_of():
    assert _aggregate_whale([], {"w1": "C1"}) is None
    cik_map = {"w1": "C1", "w2": "C2"}
    rows = [
        {"whale_id": "w1", "ticker": "NVDA", "change_percent": 1.0},
        {"whale_id": "w2", "ticker": "NVDA", "change_percent": 1.0},
    ]
    g = _aggregate_whale(rows, cik_map, as_of="2026-03-31")
    assert g is not None and g.as_of_date == "2026-03-31"


# ── 3. Earnings aggregation ────────────────────────────────────────────


def test_earnings_ranks_by_magnitude_a_miss_can_lead_a_beat():
    cal = [
        {"symbol": "AVGO", "epsActual": 1.22, "epsEstimated": 1.0, "date": "2026-06-27"},  # +22%
        {"symbol": "XYZ", "epsActual": 0.75, "epsEstimated": 1.0, "date": "2026-06-26"},   # -25%
        {"symbol": "SMALL", "epsActual": 1.05, "epsEstimated": 1.0, "date": "2026-06-25"}, # +5% (below floor)
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and g.kind == "earnings"
    assert [(r.symbol, r.value) for r in g.entries] == [("XYZ", -25.0), ("AVGO", 22.0)]
    assert g.as_of_date == "2026-06-27"


def test_earnings_skips_missing_and_zero_estimates():
    cal = [
        {"symbol": "A", "epsActual": 1.2},                                    # no estimate → skip
        {"symbol": "Z", "epsActual": 1.2, "epsEstimated": 0.0},               # est 0 → surprise None → skip
        {"symbol": "B", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-27"},  # +50%
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and [r.symbol for r in g.entries] == ["B"]


def test_earnings_caps_penny_eps_blowups():
    cal = [
        {"symbol": "PENNY", "epsActual": 0.5, "epsEstimated": 0.01, "date": "2026-06-27"},  # 4900% → capped out
        {"symbol": "B", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-26"},       # +50%
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and [r.symbol for r in g.entries] == ["B"]


def test_earnings_accepts_legacy_field_names():
    cal = [{"symbol": "L", "eps": 1.4, "epsEstimate": 1.0, "date": "2026-06-27"}]  # +40% via legacy keys
    g = _aggregate_earnings(cal)
    assert g is not None and g.entries[0].symbol == "L" and g.entries[0].value == 40.0


def test_earnings_dedups_symbol_keeping_larger_magnitude():
    cal = [
        {"symbol": "D", "epsActual": 1.15, "epsEstimated": 1.0, "date": "2026-06-20"},  # +15%
        {"symbol": "D", "epsActual": 0.60, "epsEstimated": 1.0, "date": "2026-06-27"},  # -40% (bigger)
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and len(g.entries) == 1
    assert g.entries[0].value == -40.0


def test_earnings_rejects_nan_actuals_and_returns_none_when_nothing_clears():
    cal = [
        {"symbol": "N", "epsActual": float("nan"), "epsEstimated": 1.0},   # NaN → skip
        {"symbol": "B", "epsActual": 1.5, "epsEstimated": 1.0, "date": "2026-06-27"},
    ]
    g = _aggregate_earnings(cal)
    assert g is not None and [r.symbol for r in g.entries] == ["B"]
    # Nothing above the 10% floor → honest empty.
    assert _aggregate_earnings([{"symbol": "F", "epsActual": 1.02, "epsEstimated": 1.0}]) is None
    assert _aggregate_earnings([]) is None


# ── 4. Service degradation / dedup / caching ───────────────────────────


@pytest.mark.asyncio
async def test_build_degrades_per_branch():
    s = ssvc.SignalsService()

    async def boom():
        raise RuntimeError("congress feed down")

    async def whale_ok():
        return SignalGroupResponse(
            kind="whale",
            entries=[SignalRowResponse(rank=1, symbol="MSFT", name="", value=3.0)],
        )

    async def earnings_none():
        return None

    s._build_congress = boom          # type: ignore[assignment]
    s._build_whale = whale_ok         # type: ignore[assignment]
    s._build_earnings = earnings_none # type: ignore[assignment]

    result = await s._build()
    assert result.congress is None                       # raised → degraded, not fatal
    assert result.whale is not None and result.whale.entries[0].symbol == "MSFT"
    assert result.earnings is None


@pytest.mark.asyncio
async def test_all_none_build_is_not_cached(monkeypatch):
    ssvc.SignalsService._cache.clear()
    ssvc.SignalsService._inflight.clear()
    s = ssvc.SignalsService()
    monkeypatch.setattr(s, "_read_supabase_cache", lambda: None)

    async def empty_build():
        return SignalsGroupResponse()

    monkeypatch.setattr(s, "_build", empty_build)

    r = await s.get_signals()
    assert r.congress is None and r.whale is None and r.earnings is None
    # A transient triple-failure must NOT be pinned → the next request retries.
    assert ssvc._SIGNALS_CACHE_KEY not in ssvc.SignalsService._cache


@pytest.mark.asyncio
async def test_get_signals_dedups_concurrent_cold_builds(monkeypatch):
    ssvc.SignalsService._cache.clear()
    ssvc.SignalsService._inflight.clear()
    s = ssvc.SignalsService()
    monkeypatch.setattr(s, "_read_supabase_cache", lambda: None)
    monkeypatch.setattr(s, "_write_supabase_cache", lambda result: None)

    calls = {"n": 0}

    async def counting_build():
        calls["n"] += 1
        await asyncio.sleep(0.01)  # a window for the 2nd caller to join
        return SignalsGroupResponse(
            congress=SignalGroupResponse(
                kind="congress",
                entries=[SignalRowResponse(rank=1, symbol="NVDA", name="", value=3.0)],
            )
        )

    monkeypatch.setattr(s, "_build", counting_build)

    a, b = await asyncio.gather(s.get_signals(), s.get_signals())
    assert calls["n"] == 1  # in-flight dedup → ONE build for two concurrent opens
    assert a.congress.entries[0].symbol == "NVDA"
    assert b.congress.entries[0].symbol == "NVDA"
