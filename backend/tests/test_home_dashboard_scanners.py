"""
Daily Scanners — schema-parity + transform + service tests.

Guards:
1. Schema parity (backend ↔ iOS): the snake_case keys the iOS DTOs decode
   (`ScannerRowDTO` / `ScannerGroupDTO` / `ScannerGroupsDTO` in
   `Models/HomeDashboardModels.swift`) stay pinned to the Pydantic shapes.
2. The pure ranking/filter transforms behave correctly on the messy / outlier
   inputs FMP actually returns — never a wrong rank, never a fabricated row.
3. The service degrades gracefully: per-card failure, build dedup, and the
   timeout guard never fail the whole dashboard.

No network — fake FMP + monkeypatched short interest.
"""

import asyncio
import json
import math

import pytest
from fastapi.encoders import jsonable_encoder

from app.schemas.home_dashboard import (
    HomeDashboardResponse,
    ScannerGroupResponse,
    ScannerGroupsResponse,
    ScannerRowResponse,
)
from app.services import home_dashboard_service as svc
from app.services.home_dashboard_service import (
    HomeDashboardService,
    _SCANNER_CACHE_KEY,
    _finite_float,
    _intraday_sparkline,
    _is_quality_company,
    _movers_from_universe,
    _parse_pct,
    _rvol,
    _short_pct,
    _short_rows,
    _volume_rows,
)


def _profile(symbol, *, mc=1e9, avg=2e6, etf=False, fund=False, **extra):
    p = {"symbol": symbol, "companyName": symbol, "marketCap": mc,
         "averageVolume": avg, "isEtf": etf, "isFund": fund}
    p.update(extra)
    return p


# ── 1. Schema parity ──────────────────────────────────────────────────

_ROW_KEYS = {
    "rank", "symbol", "name", "price", "change_percent", "market_cap",
    "volume_multiple", "short_percent_of_float", "spark",
}
_GROUP_KEYS = {"kind", "gainers", "losers", "entries"}
_GROUPS_KEYS = {"movers", "volume", "shorts"}


def test_scanner_row_keys_match_ios_dto():
    row = ScannerRowResponse(rank=1, symbol="GME", name="GameStop", price=24.0, change_percent=3.2)
    assert set(row.model_dump().keys()) == _ROW_KEYS


def test_scanner_group_and_groups_keys_match_ios_dto():
    g = ScannerGroupResponse(kind="movers")
    assert set(g.model_dump().keys()) == _GROUP_KEYS
    gs = ScannerGroupsResponse()
    assert set(gs.model_dump().keys()) == _GROUPS_KEYS


def test_dashboard_default_scanners_all_null():
    resp = HomeDashboardResponse(market_status_text="Markets Open", market_is_open=True, pulse=[])
    dumped = resp.model_dump()
    assert dumped["scanners"] == {"movers": None, "volume": None, "shorts": None}


def test_dashboard_validates_full_scanner_payload():
    resp = HomeDashboardResponse.model_validate({
        "market_status_text": "Markets Closed",
        "market_is_open": False,
        "pulse": [],
        "scanners": {
            "movers": {"kind": "movers",
                       "gainers": [{"rank": 1, "symbol": "SMCI", "name": "Super Micro",
                                    "price": 58.3, "change_percent": 14.2, "spark": [1.0, 2.0]}],
                       "losers": []},
            "volume": {"kind": "volume",
                       "entries": [{"rank": 1, "symbol": "GME", "name": "GameStop",
                                    "price": 24.0, "change_percent": 3.2, "volume_multiple": 8.4}]},
            "shorts": None,
        },
    })
    assert resp.scanners.movers.gainers[0].symbol == "SMCI"
    assert resp.scanners.volume.entries[0].volume_multiple == 8.4
    assert resp.scanners.shorts is None


# ── 2. Pure transforms ────────────────────────────────────────────────


def test_parse_pct_handles_float_string_percent_parens():
    assert _parse_pct(247.08) == 247.08
    assert _parse_pct("+14.2%") == 14.2
    assert _parse_pct("14.2") == 14.2
    assert _parse_pct("(2.3%)") == -2.3      # accounting-negative
    assert _parse_pct("1,234.5") == 1234.5
    assert _parse_pct(None) is None
    assert _parse_pct("") is None
    assert _parse_pct("junk") is None


def test_rvol_guards_zero_and_missing():
    assert _rvol(8_400_000, 1_000_000) == 8.4
    assert _rvol(100, 0) is None            # zero avg
    assert _rvol(0, 5) is None              # zero volume
    assert _rvol(None, 5) is None
    assert _rvol(5, "x") is None


def test_short_pct_prefers_computed_then_yahoo():
    assert _short_pct(10_000_000, 40_000_000, None) == 25.0      # computed primary
    assert _short_pct(None, None, 35.7) == 35.7                   # yahoo fallback
    assert _short_pct(0, 0, None) is None                        # neither
    assert _short_pct(5_000_000, 0, 18.0) == 18.0                # float 0 → fall back to yahoo


def test_short_pct_rejects_implausible_over_100():
    # Stale float vs post-reverse-split shares_short → absurd ratio, dropped.
    assert _short_pct(200_000_000, 40_000_000, None) is None     # computes 500% → rejected
    assert _short_pct(200_000_000, 40_000_000, 45.0) == 45.0     # …but a sane yahoo value rescues it
    assert _short_pct(None, None, 435.86) is None                # absurd yahoo value also rejected


def test_is_quality_company_filters_etf_microcap_and_thin():
    assert _is_quality_company(_profile("OK", mc=1e9, avg=2e6)) is True
    assert _is_quality_company(_profile("ETF", mc=39e9, avg=80e6, etf=True)) is False
    assert _is_quality_company(_profile("FUND", mc=1e9, avg=2e6, fund=True)) is False
    assert _is_quality_company(_profile("MICRO", mc=50e6, avg=2e6)) is False     # < $300M
    assert _is_quality_company(_profile("THIN", mc=1e9, avg=100_000)) is False   # < 1M avg vol
    assert _is_quality_company(None) is False
    assert _is_quality_company({}) is False


def test_movers_from_universe_ranks_by_change_and_splits_sign():
    pm = {
        "SMCI": _profile("SMCI", mc=30e9, avg=5e6, price=58.3),
        "FUBO": _profile("FUBO", mc=3e9, avg=10e6, price=9.9),
        "ON":   _profile("ON", mc=35e9, avg=4e6, price=90.0),
        "BE":   _profile("BE", mc=71e9, avg=3e6, price=252.0),
        "FLAT": _profile("FLAT", mc=2e9, avg=2e6, price=10.0),     # 0% → excluded both sides
        "SDOT": _profile("SDOT", mc=21e6, avg=2e6, price=21.0),    # micro-cap → excluded
        "TQQQ": _profile("TQQQ", mc=39e9, avg=80e6, etf=True, price=71.0),  # ETF → excluded
    }
    cm = {"SMCI": 14.2, "FUBO": 22.5, "ON": -23.7, "BE": -18.5,
          "FLAT": 0.0, "SDOT": 247.0, "TQQQ": -2.0}
    gainers = _movers_from_universe(pm, cm, positive=True)
    losers = _movers_from_universe(pm, cm, positive=False)
    # Gainers desc, losers asc; pump/ETF/flat excluded.
    assert [r.symbol for r in gainers] == ["FUBO", "SMCI"]
    assert [r.rank for r in gainers] == [1, 2]
    assert [r.symbol for r in losers] == ["ON", "BE"]
    assert [r.rank for r in losers] == [1, 2]
    assert all(r.change_percent > 0 for r in gainers)
    assert all(r.change_percent < 0 for r in losers)


def test_movers_from_universe_caps_to_requested_rows():
    pm = {f"S{i}": _profile(f"S{i}", price=10.0) for i in range(20)}
    cm = {f"S{i}": float(50 - i) for i in range(20)}   # all positive, descending
    rows = _movers_from_universe(pm, cm, positive=True, rows=10)
    assert len(rows) == 10
    assert rows[0].symbol == "S0" and rows[-1].rank == 10


def test_movers_from_universe_skips_non_finite_change():
    pm = {"NANC": _profile("NANC", price=10.0), "GOOD": _profile("GOOD", price=10.0)}
    cm = {"NANC": float("nan"), "GOOD": -4.0}
    rows = _movers_from_universe(pm, cm, positive=False)
    assert [r.symbol for r in rows] == ["GOOD"]
    assert all(math.isfinite(r.change_percent) for r in rows)


def test_volume_rows_ranks_by_rvol_desc_drops_below_threshold_etf_and_microcap():
    pm = {
        "GME":  _profile("GME", mc=10e9, avg=10e6, price=24.0, volume=84e6, changePercentage=3.2),    # 8.4x
        "AAPL": _profile("AAPL", mc=4000e9, avg=50e6, price=283.0, volume=50e6, changePercentage=1.0),  # 1.0x → drop
        "FUBO": _profile("FUBO", mc=3e9, avg=10e6, price=9.9, volume=30e6, changePercentage=22.5),    # 3.0x
        "TQQQ": _profile("TQQQ", mc=39e9, avg=20e6, etf=True, price=71.0, volume=80e6, changePercentage=5.0),  # 4.0x but ETF → drop
        "PUMP": _profile("PUMP", mc=50e6, avg=2e6, price=6.0, volume=50e6, changePercentage=200.0),   # 25x but micro → drop
    }
    rows = _volume_rows(pm)
    assert [r.symbol for r in rows] == ["GME", "FUBO"]
    assert rows[0].volume_multiple == 8.4
    assert rows[1].volume_multiple == 3.0
    assert [r.rank for r in rows] == [1, 2]


def test_short_rows_ranks_by_pct_of_float_desc():
    items = [
        {"symbol": "BYND", "name": "Beyond Meat", "price": 5.8, "change_percent": -1.2, "short_percent_of_float": 41.2},
        {"symbol": "CVNA", "name": "Carvana", "price": 244.1, "change_percent": 0.5, "short_percent_of_float": 22.8},
        {"symbol": "NOPE", "name": "NoData", "price": 10.0, "change_percent": 0.0, "short_percent_of_float": None},  # skipped
    ]
    rows = _short_rows(items)
    assert [r.symbol for r in rows] == ["BYND", "CVNA"]
    assert rows[0].short_percent_of_float == 41.2
    assert [r.rank for r in rows] == [1, 2]


# ── 3. Service (fake FMP, no network) ─────────────────────────────────


class _FakeFMP:
    def __init__(self):
        self.gainer_calls = 0
        self.profile_calls = 0

    async def get_biggest_gainers(self):
        self.gainer_calls += 1
        await asyncio.sleep(0)
        return [
            {"symbol": "SMCI", "name": "Super Micro", "price": 58.3, "changesPercentage": 14.2, "exchange": "NASDAQ"},
            {"symbol": "PNNY", "name": "Penny", "price": 0.5, "changesPercentage": 400.0, "exchange": "NASDAQ"},
        ]

    async def get_biggest_losers(self):
        await asyncio.sleep(0)
        return [{"symbol": "WBD", "name": "Warner Bros", "price": 9.1, "changesPercentage": -9.4, "exchange": "NASDAQ"}]

    async def get_most_actives(self):
        await asyncio.sleep(0)
        return [{"symbol": "GME", "name": "GameStop", "price": 24.0, "changesPercentage": 3.2, "exchange": "NYSE"}]

    async def get_company_profiles_batch(self, symbols):
        self.profile_calls += 1
        await asyncio.sleep(0)
        table = {
            "SMCI": _profile("SMCI", mc=30e9, avg=5e6, price=58.3, volume=30e6, changePercentage=14.2),   # 6.0x RVOL
            "WBD":  _profile("WBD", mc=20e9, avg=4e6, price=9.1, volume=8e6, changePercentage=-9.4),      # 2.0x RVOL
            "GME":  _profile("GME", mc=10e9, avg=10e6, price=24.0, volume=84e6, changePercentage=3.2),    # 8.4x RVOL
        }
        return [table[s] for s in symbols if s in table]

    async def get_batch_quotes(self, symbols):
        await asyncio.sleep(0)
        table = {
            "BYND": {"symbol": "BYND", "name": "Beyond Meat", "price": 5.8, "changesPercentage": -1.2},
            "CVNA": {"symbol": "CVNA", "name": "Carvana", "price": 244.1, "changesPercentage": 0.5},
        }
        return [table[s] for s in symbols if s in table]

    async def get_shares_float(self, ticker):
        await asyncio.sleep(0)
        return {"floatShares": 40_000_000}

    # sparkline path
    async def get_intraday_prices(self, ticker, interval="5min", from_date=None, to_date=None):
        await asyncio.sleep(0)
        return [
            {"date": "2026-06-26 10:00:00", "close": 100.0},
            {"date": "2026-06-26 11:00:00", "close": 101.0},
        ]


def _fresh_service(monkeypatch, *, short_pct=33.0, short_delay=0.0):
    HomeDashboardService._scanner_cache.clear()
    HomeDashboardService._scanner_inflight.clear()
    HomeDashboardService._float_cache.clear()
    s = HomeDashboardService()
    s.fmp = _FakeFMP()  # type: ignore[assignment]

    async def fake_si(ticker):
        if short_delay:
            await asyncio.sleep(short_delay)
        # Only a couple of names carry data; rest return {} → skipped.
        data = {"BYND": 7_000_000, "CVNA": 9_000_000}
        if ticker in data:
            return {"shares_short": data[ticker]}  # no precomputed % → computed from float
        return {}

    monkeypatch.setattr(svc, "get_short_interest", fake_si)
    monkeypatch.setattr(svc, "_load_short_universe", lambda: ["BYND", "CVNA", "ZZZZ"])
    return s


@pytest.mark.asyncio
async def test_build_scanner_groups_movers_volume_shorts(monkeypatch):
    s = _fresh_service(monkeypatch)
    groups = await s.get_scanners()

    # Movers: ranked from the quality universe by % change. Penny PNNY ($0.5)
    # dropped. Gainers desc = SMCI (+14.2), then GME (+3.2, a most-active that
    # backfills with a real name). Losers asc = WBD (−9.4).
    assert groups.movers is not None
    assert [r.symbol for r in groups.movers.gainers] == ["SMCI", "GME"]
    assert [r.symbol for r in groups.movers.losers] == ["WBD"]
    # rank-1 gainer carries a sparkline; deeper rows don't.
    assert groups.movers.gainers[0].spark == [100.0, 101.0]
    assert groups.movers.gainers[1].spark == []

    # Volume: names clearing the 2.0x RVOL floor, ranked desc
    # (GME 8.4x, SMCI 6.0x, WBD 2.0x). Mega-cap ~1x exclusion is covered by the
    # pure _volume_rows test.
    assert groups.volume is not None
    assert [r.symbol for r in groups.volume.entries] == ["GME", "SMCI", "WBD"]
    assert [r.volume_multiple for r in groups.volume.entries] == [8.4, 6.0, 2.0]

    # Shorts: BYND (7M/40M=17.5%) + CVNA (9M/40M=22.5%), ranked desc.
    assert groups.shorts is not None
    assert [r.symbol for r in groups.shorts.entries] == ["CVNA", "BYND"]
    assert groups.shorts.entries[0].short_percent_of_float == 22.5


@pytest.mark.asyncio
async def test_scanners_dedup_to_single_build(monkeypatch):
    s = _fresh_service(monkeypatch)
    a, b = await asyncio.gather(s.get_scanners(), s.get_scanners())
    assert a is b                      # same cached object
    assert s.fmp.gainer_calls == 1     # one build, not two


@pytest.mark.asyncio
async def test_scanner_cache_hit_skips_rebuild(monkeypatch):
    s = _fresh_service(monkeypatch)
    await s.get_scanners()
    calls = s.fmp.gainer_calls
    await s.get_scanners()             # within TTL
    assert s.fmp.gainer_calls == calls


@pytest.mark.asyncio
async def test_one_card_failure_does_not_kill_others(monkeypatch):
    s = _fresh_service(monkeypatch)

    async def boom(ticker):
        raise RuntimeError("FINRA down")

    monkeypatch.setattr(svc, "get_short_interest", boom)
    groups = await s.get_scanners()
    assert groups.movers is not None and groups.volume is not None
    assert groups.shorts is None       # shorts failed, others survive


@pytest.mark.asyncio
async def test_timeout_guard_returns_empty_without_failing_dashboard(monkeypatch):
    # Short build (0.3s) but an even shorter guard timeout (0.05s) → guard gives up.
    s = _fresh_service(monkeypatch, short_delay=0.3)
    monkeypatch.setattr(svc, "_SCANNER_BUILD_TIMEOUT_SECONDS", 0.05)
    groups = await s._get_scanners_guarded()
    assert isinstance(groups, ScannerGroupsResponse)
    assert groups.movers is None and groups.volume is None and groups.shorts is None
    await asyncio.sleep(0.5)  # let the shielded background build finish/clean up


@pytest.mark.asyncio
async def test_dashboard_still_builds_when_scanners_timeout(monkeypatch):
    s = _fresh_service(monkeypatch, short_delay=0.3)
    monkeypatch.setattr(svc, "_SCANNER_BUILD_TIMEOUT_SECONDS", 0.05)

    # Stub the pulse path so the dashboard build is otherwise trivial.
    async def fake_pulse(cfg):
        return None
    s._fetch_pulse_item = fake_pulse  # type: ignore[assignment]

    resp = await s.get_dashboard()
    assert isinstance(resp, HomeDashboardResponse)
    # Scanners not ready (timed out) and nothing cached yet → empty groups, but
    # the dashboard still builds and ships.
    assert resp.scanners.movers is None
    await asyncio.sleep(0.5)  # let the shielded background build finish/clean up


# ── 4. Hardening regressions (adversarial review) ─────────────────────
# Non-finite (NaN/inf) inputs are the dangerous class: they pass naive guards,
# scramble rankings, and serialize as the non-standard JSON tokens NaN/Infinity
# that 500 the endpoint or crash the iOS decode of the WHOLE dashboard.


def test_finite_float_rejects_non_finite_and_garbage():
    assert _finite_float("5") == 5.0
    assert _finite_float(3.2) == 3.2
    for bad in ["nan", "inf", "-inf", "Infinity", float("nan"), float("inf"),
                float("-inf"), None, "x", object()]:
        assert _finite_float(bad) is None, bad


def test_parse_pct_rejects_non_finite_strings_and_floats():
    for bad in ["nan", "NaN", "inf", "-inf", "Infinity", "(nan%)", "+inf%",
                float("nan"), float("inf"), float("-inf"), True, False]:
        assert _parse_pct(bad) is None, bad
    # Sanity: real values still parse.
    assert _parse_pct("+14.2%") == 14.2
    assert _parse_pct("(2.3%)") == -2.3


def test_rvol_rejects_non_finite_and_overflow():
    assert _rvol(float("nan"), 1e6) is None
    assert _rvol(float("inf"), 1e6) is None
    assert _rvol(3e6, float("nan")) is None
    assert _rvol("nan", 1e6) is None
    assert _rvol("inf", 1e6) is None
    assert _rvol(1e308, 1e-308) is None        # overflow ratio → inf → None
    assert _rvol(8_400_000, 1_000_000) == 8.4  # real value survives


def test_is_quality_company_rejects_nan_marketcap_and_avgvol():
    assert _is_quality_company({"marketCap": float("nan"), "averageVolume": 2e6, "isEtf": False}) is False
    assert _is_quality_company({"marketCap": 1e9, "averageVolume": float("inf"), "isEtf": False}) is False
    assert _is_quality_company(_profile("OK", mc=1e9, avg=2e6)) is True


def test_intraday_sparkline_drops_infinite_close():
    # One finite close left → < 2 → [] (never a list containing inf).
    assert _intraday_sparkline([
        {"date": "2026-06-26 10:00:00", "close": float("inf")},
        {"date": "2026-06-26 11:00:00", "close": 200.0},
    ]) == []
    out = _intraday_sparkline([
        {"date": "2026-06-26 10:00:00", "close": 100.0},
        {"date": "2026-06-26 10:05:00", "close": float("inf")},
        {"date": "2026-06-26 10:10:00", "close": 102.0},
    ])
    assert all(math.isfinite(c) for c in out)


def test_movers_from_universe_drops_nan_price():
    # change_map only ever holds finite values (it's built via _parse_pct), but
    # a NaN price on a profile must still drop the row, not emit NaN.
    pm = {
        "NANP": _profile("NANP", price=float("nan")),
        "GOOD": _profile("GOOD", price=10.0),
    }
    cm = {"NANP": 5.0, "GOOD": 8.0}
    rows = _movers_from_universe(pm, cm, positive=True)
    assert [r.symbol for r in rows] == ["GOOD"]
    assert all(math.isfinite(r.price) and math.isfinite(r.change_percent) for r in rows)


def test_volume_rows_drops_nan_volume_not_ranked_first():
    pm = {
        "GOOD": _profile("GOOD", price=10.0, volume=30e6, avg=10e6, changePercentage=3.0),  # 3x
        "NANV": _profile("NANV", price=10.0, volume=float("nan"), avg=10e6, changePercentage=3.0),
        "INFV": _profile("INFV", price=10.0, volume=float("inf"), avg=10e6, changePercentage=3.0),
    }
    rows = _volume_rows(pm)
    assert [r.symbol for r in rows] == ["GOOD"]  # nan/inf excluded, not rank #1
    assert all(math.isfinite(r.volume_multiple) for r in rows)


def test_response_with_nan_row_input_serializes_clean():
    # A non-finite change must be dropped so the assembled dashboard serializes to
    # STANDARD JSON (json.dumps allow_nan=False raises on any non-finite — i.e. the
    # wire never carries NaN/Infinity).
    pm = {"BAD": _profile("BAD", price=10.0), "GOOD": _profile("GOOD", price=10.0)}
    cm = {"BAD": float("inf"), "GOOD": 8.0}
    rows = _movers_from_universe(pm, cm, positive=True)
    assert [r.symbol for r in rows] == ["GOOD"]
    resp = HomeDashboardResponse(
        market_status_text="Markets Open", market_is_open=True, pulse=[],
        scanners=ScannerGroupsResponse(movers=ScannerGroupResponse(kind="movers", gainers=rows)),
    )
    encoded = json.dumps(jsonable_encoder(resp), allow_nan=False)  # raises if any NaN/inf
    assert "NaN" not in encoded and "Infinity" not in encoded


def test_short_pct_rejects_non_finite():
    assert _short_pct(float("nan"), 1e6, None) is None
    assert _short_pct(1e6, float("inf"), None) is None
    assert _short_pct(None, None, float("nan")) is None


@pytest.mark.asyncio
async def test_get_scanners_cancellation_does_not_hang_joiner(monkeypatch):
    """A leader build cancelled mid-flight (shutdown) must SETTLE the in-flight
    future so a parked joiner wakes instead of hanging forever."""
    s = _fresh_service(monkeypatch)
    HomeDashboardService._scanner_cache.clear()
    HomeDashboardService._scanner_inflight.clear()

    started = asyncio.Event()

    async def slow_build():
        started.set()
        await asyncio.sleep(5)
        return ScannerGroupsResponse()

    s._build_scanner_groups = slow_build  # type: ignore[assignment]

    leader = asyncio.create_task(s.get_scanners())
    await started.wait()                 # inflight future installed, build running
    joiner = asyncio.create_task(s.get_scanners())
    await asyncio.sleep(0)               # let joiner park on the inflight future
    leader.cancel()

    done, _pending = await asyncio.wait({joiner}, timeout=1.0)
    assert joiner in done, "joiner hung — in-flight future was left unresolved"
    assert _SCANNER_CACHE_KEY not in HomeDashboardService._scanner_inflight
    with pytest.raises(BaseException):   # consume the propagated cancellation
        joiner.result()


@pytest.mark.asyncio
async def test_scanners_surface_on_next_request_after_timeout(monkeypatch):
    """Cold first build times out (empty), but the shielded bg build warms the
    20-min scanner cache; the 2nd dashboard request must surface the warm
    scanners — proving the empty result was NOT pinned in the pulse cache."""
    s = _fresh_service(monkeypatch, short_delay=0.2)
    HomeDashboardService._cache.clear()
    HomeDashboardService._inflight.clear()
    HomeDashboardService._scanner_cache.clear()
    HomeDashboardService._scanner_inflight.clear()
    monkeypatch.setattr(svc, "_SCANNER_BUILD_TIMEOUT_SECONDS", 0.05)

    async def fake_pulse(cfg):
        return None
    s._fetch_pulse_item = fake_pulse  # type: ignore[assignment]

    r1 = await s.get_dashboard()
    assert r1.scanners.movers is None      # timed out, nothing cached yet
    await asyncio.sleep(0.4)               # bg build finishes, warms _scanner_cache
    r2 = await s.get_dashboard()
    assert r2.scanners.movers is not None  # surfaced, not pinned empty for 5 min


@pytest.mark.asyncio
async def test_cached_float_does_not_pin_none_on_transient_failure(monkeypatch):
    s = _fresh_service(monkeypatch)
    HomeDashboardService._float_cache.clear()
    calls = {"n": 0}

    async def flaky_float(ticker):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("FMP down")
        return {"floatShares": 5e7}

    s.fmp.get_shares_float = flaky_float  # type: ignore[assignment]
    assert await s._cached_float("XYZ") is None    # first call failed
    assert await s._cached_float("XYZ") == 5e7      # retried — None was NOT pinned


@pytest.mark.asyncio
async def test_shorts_computes_from_float_when_precomputed_zero(monkeypatch):
    """A Yahoo short% of 0 (falsy-but-present) must NOT suppress the FINRA/float
    compute path."""
    s = _fresh_service(monkeypatch)
    HomeDashboardService._float_cache.clear()

    async def si(ticker):
        if ticker == "AAA":
            return {"shares_short": 8_000_000, "short_percent_of_float": 0}
        return {}

    monkeypatch.setattr(svc, "get_short_interest", si)
    monkeypatch.setattr(svc, "_load_short_universe", lambda: ["AAA"])
    group = await s._build_shorts()
    assert group is not None
    assert group.entries[0].symbol == "AAA"
    assert group.entries[0].short_percent_of_float == 20.0  # 8M / 40M * 100


def test_load_short_universe_dedups_and_normalizes(tmp_path, monkeypatch):
    p = tmp_path / "u.json"
    p.write_text('{"tickers": ["AAA", "bbb", "AAA", "BBB", "  ccc  ", ""]}')
    monkeypatch.setattr(svc, "_SHORT_UNIVERSE_PATH", p)
    assert svc._load_short_universe() == ["AAA", "BBB", "CCC"]


@pytest.mark.asyncio
async def test_volume_universe_not_starved_of_most_actives(monkeypatch):
    """Round-robin interleave: even with a long gainers list, most-actives
    (the RVOL backbone) still make it into the quoted universe under the cap."""
    s = _fresh_service(monkeypatch)
    monkeypatch.setattr(svc, "_UNIVERSE_CAP", 6)

    async def many_gainers():
        return [{"symbol": f"G{i}", "name": f"G{i}", "price": 10.0,
                 "changesPercentage": 50 - i, "exchange": "NASDAQ"} for i in range(20)]

    async def one_active():
        return [{"symbol": "MEGA", "name": "Mega", "price": 100.0,
                 "changesPercentage": 1.0, "exchange": "NASDAQ"}]

    profiled = {}

    async def profiles(symbols):
        return [_profile(s_, price=10.0, volume=30e6, avg=10e6, changePercentage=1.0)
                for s_ in symbols]

    s.fmp.get_biggest_gainers = many_gainers  # type: ignore[assignment]
    s.fmp.get_biggest_losers = one_active      # reuse as a 1-item list  # type: ignore[assignment]
    s.fmp.get_most_actives = one_active        # type: ignore[assignment]
    s.fmp.get_company_profiles_batch = profiles  # type: ignore[assignment]

    movers, volume = await s._build_movers_and_volume()
    # MEGA (the most-active) must be present in the RVOL card despite 20 gainers
    # ahead of it — round-robin guarantees it under the cap of 6.
    assert volume is not None
    assert "MEGA" in {r.symbol for r in volume.entries}


@pytest.mark.asyncio
async def test_movers_profile_fetch_chunks_past_the_50_cap(monkeypatch):
    """`get_company_profiles_batch` hard-caps at 50 symbols/call. The service must
    CHUNK so a >50 universe is fully profiled — otherwise the tail (where
    most-actives' down names land after the round-robin) is silently dropped and
    Top Losers is starved of real names. Regression for that exact bug."""
    s = _fresh_service(monkeypatch)
    monkeypatch.setattr(svc, "_UNIVERSE_CAP", 90)

    # 60 down most-actives; A59 is the most-negative and lands PAST position 50.
    actives = [
        {"symbol": f"A{i}", "name": f"A{i}", "price": 10.0,
         "changesPercentage": -float(i + 1), "exchange": "NYSE"}
        for i in range(60)
    ]

    async def _actives():
        return actives

    async def _empty():
        return []

    # Mirror the REAL 50-per-call cap so the test fails without chunking.
    async def _capped_profiles(symbols):
        return [_profile(sym, price=10.0, mc=1e9, avg=2e6) for sym in symbols[:50]]

    s.fmp.get_biggest_gainers = _empty       # type: ignore[assignment]
    s.fmp.get_biggest_losers = _empty        # type: ignore[assignment]
    s.fmp.get_most_actives = _actives        # type: ignore[assignment]
    s.fmp.get_company_profiles_batch = _capped_profiles  # type: ignore[assignment]

    movers, _volume = await s._build_movers_and_volume()
    assert movers is not None
    syms = {r.symbol for r in movers.losers}
    # A59/A58/… land past index 50; they'd be dropped without chunking.
    assert "A59" in syms and "A55" in syms
    assert len(movers.losers) == 10  # the 10 most-negative, fully profiled
