"""Financial Health snapshot ↔ Health Check parity (owner-approved fixes, 2026-11-14).

The ticker-detail Health Check card and the Financial Health snapshot (Overview card, the
paid report's Health card) must print the same figures for the same company:

1. The snapshot's LOCAL FALLBACK (the Health Check raised) prints FMP's quick ratio,
   (current assets − inventory) / current liabilities — it printed (cash + receivables) /
   current liabilities (ENPH 0.59 vs 1.84) — and its Altman Z at two decimals ("1.5" vs
   "1.48").
2. The fallback's balance-sheet / income / profile legs are fetched ONLY when the Health
   Check raised: a clean build makes no FMP call of its own.
3. The snapshot's 24h row carries the Health Check build's next-earnings stamp and drops on
   the same day the Health Check's row does (`earnings_stamp_passed`); an unknown stamp is
   served, never written, never `degraded`.
4. The Quick Ratio insight uses the Current Ratio's band edges and direction words: ORCL's
   CR and QR, both 1.17 vs 1.53 (−23.5%), read "a little below" and "below".

Hermetic: fake FMP clients, fake Supabase tables, no network.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

import test_health_check_deepcheck as hcd
from app.integrations.fmp import FMPNotEntitledException, FMPRateLimitException
from app.schemas.health_check import HealthCheckMetricSchema, HealthCheckResponse
from app.schemas.stock_overview import SnapshotItemResponse
from app.services import health_check_service as hc
from app.services import health_snapshot_service as hs
from app.services._earnings_common import CALENDAR_UNKNOWN

_FAR_PAST = "2000-01-03"
_FAR_FUTURE = "2099-01-15"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── fakes ────────────────────────────────────────────────────────────────────────────


class _RecordingFMP:
    """Per-method answers (an Exception answer is RAISED); every call is recorded."""

    def __init__(self, answers: Optional[Dict[str, Any]] = None) -> None:
        self.answers = answers or {}
        self.calls: List[Tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            answer = self.answers.get(name, [])
            if isinstance(answer, BaseException):
                raise answer
            return answer

        return _call


class _NoPeers:
    def get_current_benchmarks(self, industry, sector, metrics):
        return {m: None for m in metrics}


class _FakeHC:
    """Stands in for HealthCheckService: `health` is returned (or raised); `stamp` is what
    `next_earnings_for` answers (or raises)."""

    def __init__(self, health: Any, stamp: Any = None) -> None:
        self.health = health
        self.stamp = stamp
        self.stamp_asks: List[Tuple[str, Any]] = []

    async def get_health_check(self, ticker):
        if isinstance(self.health, BaseException):
            raise self.health
        return self.health

    def next_earnings_for(self, ticker, *, served):
        self.stamp_asks.append((ticker, served))
        if isinstance(self.stamp, BaseException):
            raise self.stamp
        return self.stamp


class _FakeHCWithoutStamp:
    def __init__(self, health: Any) -> None:
        self.health = health

    async def get_health_check(self, ticker):
        return self.health


class _Rows:
    """`.table(...).select(...).eq(...).limit(...).execute()` answering `rows`; upserts
    are recorded."""

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rows = rows or []
        self.upserts: List[Dict[str, Any]] = []

    def table(self, name):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def upsert(self, payload, **k):
        self.upserts.append(payload)
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


def _hc_metric(kind, value, comparison=None, status="positive"):
    return HealthCheckMetricSchema(type=kind, value=value, comparison_value=comparison,
                                   gauge_position=0.5, status=status, insight_text="ok")


def _clean_health(degraded: Optional[List[str]] = None) -> HealthCheckResponse:
    return HealthCheckResponse(
        symbol="TEST", overall_rating="good", passed_count=3, total_count=4,
        metrics=[
            _hc_metric("debt_to_equity", 0.5, 0.6),
            _hc_metric("current_ratio", 1.5, 1.4),
            _hc_metric("quick_ratio", 1.2, 1.0),
            _hc_metric("altman_z_score", 4.21, status="positive"),
        ],
        degraded=degraded or [],
    )


def _fresh_snapshot_state(monkeypatch):
    monkeypatch.setattr(hs, "_cache", {})
    monkeypatch.setattr(hs, "_inflight", {})
    monkeypatch.setattr(hs, "_degraded_by_key", {})
    monkeypatch.setattr(hs, "_build_stamp_by_key", {})


def _snapshot_service(monkeypatch, hc_service, fmp=None):
    _fresh_snapshot_state(monkeypatch)
    # Function-local import inside `_compute_with_status`: patch the SOURCE module.
    monkeypatch.setattr(hc, "get_health_check_service", lambda: hc_service)
    monkeypatch.setattr(hs, "get_sector_benchmark_lookup", lambda: _NoPeers())
    svc = hs.HealthSnapshotService.__new__(hs.HealthSnapshotService)
    svc.supabase = None
    svc.fmp = fmp if fmp is not None else _RecordingFMP()
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    return svc


def _spy_persist(monkeypatch, svc) -> List[tuple]:
    """Record the 24h write (ticker, *rest) inline; the service fires it with
    `run_in_executor` and never awaits it."""
    persisted: List[tuple] = []

    def _record(ticker, *rest):
        persisted.append((ticker, *rest))

    monkeypatch.setattr(svc, "_upsert_supabase_cache", _record)
    loop = asyncio.get_running_loop()
    real = loop.run_in_executor

    def _run_in_executor(executor, fn, *args):
        if fn is _record:
            fn(*args)
            done = loop.create_future()
            done.set_result(None)
            return done
        return real(executor, fn, *args)

    monkeypatch.setattr(loop, "run_in_executor", _run_in_executor)
    return persisted


# ── fallback inputs (Technology company, no peers) ───────────────────────────────────

_PROFILE = {"symbol": "TEST", "sector": "Technology", "industry": "Software - Infrastructure",
            "mktCap": 500.0}
# CR = 300 / 200 = 1.50; FMP quick = (300 − 60) / 200 = 1.20; the old (cash +
# receivables) formula = (50 + 40) / 200 = 0.45. Z = 1.292 → "1.29" (1 dp: "1.3").
_BS = {"totalAssets": 1000.0, "totalLiabilities": 600.0, "totalCurrentAssets": 300.0,
       "totalCurrentLiabilities": 200.0, "retainedEarnings": 100.0, "inventory": 60.0,
       "cashAndCashEquivalents": 50.0, "netReceivables": 40.0, "totalDebt": 300.0,
       "totalStockholdersEquity": 400.0}
_QUARTERS = [{"date": d, "operatingIncome": 10.0, "interestExpense": 2.0, "revenue": 100.0,
              "netIncome": 8.0, "ebitda": 12.0, "depreciationAndAmortization": 2.0}
             for d in ("2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31")]


def _fallback_fmp(bs: Optional[Dict[str, Any]] = None, **over) -> _RecordingFMP:
    answers = {
        "get_balance_sheet": [dict(_BS) if bs is None else bs],
        "get_income_statement": [dict(q) for q in _QUARTERS],
        "get_company_profile": dict(_PROFILE),
    }
    answers.update(over)
    return _RecordingFMP(answers)


def _rows_by_key(snap: SnapshotItemResponse) -> Dict[str, Any]:
    return {m.metric_key: m for m in snap.metrics}


# ══════════════════════════════════════════════════════════════════════════════════════
# 2. The fallback legs are lazy
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_clean_health_check_build_makes_no_fmp_call_of_its_own(monkeypatch):
    """Mutation: gathering the three legs beside the health check (the old shape) records
    three calls here."""
    fmp = _RecordingFMP()
    svc = _snapshot_service(monkeypatch, _FakeHC(_clean_health()), fmp=fmp)
    snap, degraded = await svc._compute_with_status("TEST")
    assert fmp.calls == [], f"the happy path fetched {[c[0] for c in fmp.calls]}"
    assert degraded == []
    assert _rows_by_key(snap)["quick_ratio"].value == "1.20"


@pytest.mark.asyncio
async def test_a_partial_health_check_build_is_degraded_with_its_own_reasons_only(monkeypatch):
    fmp = _RecordingFMP()
    svc = _snapshot_service(monkeypatch, _FakeHC(_clean_health(["ratios"])), fmp=fmp)
    _snap, degraded = await svc._compute_with_status("TEST")
    assert degraded == ["health_check:ratios"]
    assert fmp.calls == []


@pytest.mark.asyncio
async def test_a_raised_health_check_fetches_exactly_the_three_fallback_legs(monkeypatch):
    fmp = _fallback_fmp()
    svc = _snapshot_service(monkeypatch, _FakeHC(RuntimeError("hc down")), fmp=fmp)
    _snap, degraded = await svc._compute_with_status("TEST")
    assert sorted((name, args, tuple(sorted(kw.items()))) for name, args, kw in fmp.calls) == [
        ("get_balance_sheet", ("TEST",), (("limit", 1), ("period", "quarter"))),
        ("get_company_profile", ("TEST",), ()),
        ("get_income_statement", ("TEST",), (("limit", 4), ("period", "quarter"))),
    ]
    assert degraded == ["health_check"]


@pytest.mark.asyncio
@pytest.mark.parametrize("hc_error,legs,expected", [
    # Each failing leg is named after "health_check", in leg order.
    (RuntimeError("down"), {"get_balance_sheet": FMPRateLimitException("429")},
     ["health_check", "balance_sheet"]),
    (RuntimeError("down"), {"get_income_statement": RuntimeError("x"),
                            "get_company_profile": FMPRateLimitException("429")},
     ["health_check", "income", "profile"]),
    # A permanent refusal is not degradation, on the health check or on a leg.
    (RuntimeError("down"), {"get_company_profile": FMPNotEntitledException("402")},
     ["health_check"]),
    (FMPNotEntitledException("402"), {}, []),
])
async def test_the_fallback_degraded_list_is_unchanged(monkeypatch, hc_error, legs, expected):
    svc = _snapshot_service(monkeypatch, _FakeHC(hc_error), fmp=_fallback_fmp(**legs))
    _snap, degraded = await svc._compute_with_status("TEST")
    assert degraded == expected


@pytest.mark.asyncio
async def test_a_leg_cancelled_inside_the_gather_degrades_the_build(monkeypatch):
    """A child cancelled inside `gather(return_exceptions=True)` comes back as a
    CancelledError INSTANCE (a BaseException): it is a failed leg, not data."""
    fmp = _fallback_fmp(get_balance_sheet=asyncio.CancelledError())
    svc = _snapshot_service(monkeypatch, _FakeHC(RuntimeError("down")), fmp=fmp)
    snap, degraded = await svc._compute_with_status("TEST")
    assert degraded == ["health_check", "balance_sheet"]
    assert _rows_by_key(snap)["quick_ratio"].value == "—"


# ══════════════════════════════════════════════════════════════════════════════════════
# 1. The fallback prints the Health Check's figures
# ══════════════════════════════════════════════════════════════════════════════════════


async def _fallback_card(monkeypatch, bs: Optional[Dict[str, Any]] = None):
    svc = _snapshot_service(monkeypatch, _FakeHC(RuntimeError("hc down")),
                            fmp=_fallback_fmp(bs=bs))
    snap, _ = await svc._compute_with_status("TEST")
    return _rows_by_key(snap)


@pytest.mark.asyncio
async def test_the_fallback_quick_ratio_is_fmps_current_assets_less_inventory(monkeypatch):
    """Mutation: the old (cash + receivables) / CL formula prints 0.45."""
    rows = await _fallback_card(monkeypatch)
    assert rows["quick_ratio"].value == "1.20"
    assert rows["quick_ratio"].numeric_value == pytest.approx(1.2)
    assert rows["current_ratio"].value == "1.50"


@pytest.mark.asyncio
@pytest.mark.parametrize("inventory_fields", [{"inventory": 0.0}, {}],
                         ids=["inventory_zero", "inventory_missing"])
async def test_no_inventory_makes_the_quick_ratio_the_current_ratio(monkeypatch,
                                                                    inventory_fields):
    bs = {k: v for k, v in _BS.items() if k != "inventory"}
    bs.update(inventory_fields)
    rows = await _fallback_card(monkeypatch, bs)
    assert rows["quick_ratio"].value == rows["current_ratio"].value == "1.50"


@pytest.mark.asyncio
@pytest.mark.parametrize("over,why", [
    ({"totalCurrentLiabilities": 0.0}, "current liabilities 0"),
    ({"totalCurrentLiabilities": -5.0}, "negative current liabilities"),
    ({"totalCurrentLiabilities": None}, "current liabilities missing"),
    ({"totalCurrentAssets": None}, "current assets missing"),
    ({"inventory": 400.0}, "inventory larger than current assets"),
    ({"inventory": -10.0}, "negative inventory"),
    ({"totalCurrentAssets": "n/a"}, "current assets not a number"),
])
async def test_an_unreadable_quick_ratio_is_a_dash_not_a_number(monkeypatch, over, why):
    bs = dict(_BS)
    bs.update(over)
    rows = await _fallback_card(monkeypatch, bs)
    assert rows["quick_ratio"].value == "—", why
    assert rows["quick_ratio"].numeric_value is None and rows["quick_ratio"].score is None


@pytest.mark.asyncio
async def test_all_current_assets_in_inventory_is_a_real_zero(monkeypatch):
    rows = await _fallback_card(monkeypatch, dict(_BS, inventory=300.0))
    assert rows["quick_ratio"].value == "0.00"


@pytest.mark.asyncio
async def test_the_fallback_prints_altman_z_at_two_decimals_like_the_health_check(monkeypatch):
    """Mutation: `f"{z_score}"` with the 1-dp default prints "1.3"."""
    expected = hc._compute_z_score(
        {**_BS}, {"operatingIncome": 40.0, "revenue": 400.0}, 500.0, ndigits=2,
        sector="Technology", industry="Software - Infrastructure",
    )
    assert expected == pytest.approx(1.29) and round(expected, 1) != expected, (
        "anti-vacuity: the fixture must tell 1 dp from 2 dp")
    rows = await _fallback_card(monkeypatch)
    assert rows["altman_z"].value == "1.29"
    assert rows["altman_z"].numeric_value == pytest.approx(1.29)
    # The Health Check's own formatter on the same number.
    assert rows["altman_z"].value == hs._fmt_value("altman_z_score", expected)


@pytest.mark.asyncio
async def test_a_trailing_zero_z_keeps_two_decimals(monkeypatch):
    # Retained earnings 106: Z = 1.292 + 1.4 * 0.006 = 1.3004 → "1.30" (it printed "1.3").
    rows = await _fallback_card(monkeypatch, dict(_BS, retainedEarnings=106.0))
    assert rows["altman_z"].value == "1.30"


# ══════════════════════════════════════════════════════════════════════════════════════
# 3. The earnings-day refresh
# ══════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("stamp,today,passed", [
    (None, "2026-11-14", False),
    ("", "2026-11-14", False),
    ("2026-11-15", "2026-11-14", False),
    ("2026-11-14", "2026-11-14", True),           # report day itself
    ("2026-11-13", "2026-11-14", True),
    ("2026-11-14T00:00:00", "2026-11-14", True),  # a plain string compare kept it alive
    ("2026-11-15T00:00:00", "2026-11-14", False),
    ("soon", "2026-11-14", True),                 # unreadable: rebuild, never unbounded
    (20261120, "2026-11-14", True),
    (CALENDAR_UNKNOWN, "2026-11-14", True),
])
def test_earnings_stamp_passed(stamp, today, passed):
    assert hc.earnings_stamp_passed(stamp, today=today) is passed


def _snapshot_row(version: int = 11, stamp: Any = None, *, with_stamp: bool = True,
                  age: timedelta = timedelta(hours=2)) -> Dict[str, Any]:
    body = SnapshotItemResponse(category="Financial Health", rating=4, metrics=[],
                                computed_at="2026-11-14T09:15:00Z").model_dump()
    body[hs._VERSION_KEY] = version
    if with_stamp:
        body[hs._NEXT_EARNINGS_KEY] = stamp
    return {"response_json": body,
            "cached_at": (datetime.now(timezone.utc) - age).isoformat()}


def _snapshot_reader(rows) -> hs.HealthSnapshotService:
    svc = hs.HealthSnapshotService.__new__(hs.HealthSnapshotService)
    svc.supabase = _Rows(rows)
    return svc


@pytest.mark.parametrize("stamp,served", [
    (None, True),              # no pending announcement: the plain 24h row
    (_FAR_FUTURE, True),       # before its report day
    (_FAR_PAST, False),        # past it
    ("today", False),          # on it
    ("not-a-date", False),
])
def test_a_snapshot_row_past_its_stamp_is_a_miss(stamp, served):
    """Mutation: dropping the stamp check serves the far-past and today rows."""
    if stamp == "today":
        stamp = _today()
    got = _snapshot_reader([_snapshot_row(stamp=stamp)])._check_supabase_cache("TEST")
    assert (got is not None) is served


def test_a_v10_row_is_a_miss_even_with_a_future_stamp():
    """Mutation: leaving the version at 10 serves it."""
    assert hs._SNAPSHOT_PAYLOAD_VERSION == 11
    got = _snapshot_reader([_snapshot_row(version=10, stamp=_FAR_FUTURE)])._check_supabase_cache(
        "TEST")
    assert got is None


def test_a_current_row_without_the_stamp_key_is_a_miss():
    got = _snapshot_reader([_snapshot_row(with_stamp=False)])._check_supabase_cache("TEST")
    assert got is None
    # ...and the stamp key never reaches the response.
    served = _snapshot_reader([_snapshot_row(stamp=None)])._check_supabase_cache("TEST")
    assert served is not None and hs._NEXT_EARNINGS_KEY not in served.model_dump()


def test_the_real_writer_stores_the_stamp_and_the_reader_admits_it():
    snap = SnapshotItemResponse(category="Financial Health", rating=4, metrics=[])
    svc = _snapshot_reader([])
    svc._upsert_supabase_cache("TEST", snap, _FAR_FUTURE)
    (written,) = svc.supabase.upserts
    assert written["response_json"][hs._NEXT_EARNINGS_KEY] == _FAR_FUTURE
    assert written["response_json"][hs._VERSION_KEY] == 11
    svc.supabase = _Rows([{"response_json": dict(written["response_json"]),
                           "cached_at": written["cached_at"]}])
    assert svc._check_supabase_cache("TEST") is not None


def test_the_real_writer_refuses_an_unknown_stamp():
    svc = _snapshot_reader([])
    svc._upsert_supabase_cache(
        "TEST", SnapshotItemResponse(category="Financial Health", rating=4, metrics=[]),
        CALENDAR_UNKNOWN,
    )
    assert svc.supabase.upserts == []


@pytest.mark.parametrize("stamp", [None, _FAR_PAST, "today", _FAR_FUTURE])
def test_the_two_rows_drop_on_the_same_day(monkeypatch, stamp):
    """The Health Check's row and the snapshot's row with the same stamp: both served or
    both rebuilt."""
    monkeypatch.setattr(hc, "_stamp_by_key", {})
    if stamp == "today":
        stamp = _today()
    hc_svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    hc_svc.supabase = _Rows([{
        "response_json": {**_clean_health().model_dump(),
                          "payload_version": hc._HC_PAYLOAD_VERSION},
        "cached_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        "next_earnings_date": stamp,
    }])
    hc_hit = hc_svc._check_supabase_cache("TEST") is not None
    snap_hit = _snapshot_reader([_snapshot_row(stamp=stamp)])._check_supabase_cache(
        "TEST") is not None
    assert hc_hit is snap_hit
    assert hc_hit is (stamp is None or stamp == _FAR_FUTURE)


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", [None, _FAR_FUTURE])
async def test_a_known_stamp_is_written_with_the_build(monkeypatch, stamp):
    fake = _FakeHC(_clean_health(), stamp=stamp)
    svc = _snapshot_service(monkeypatch, fake)
    persisted = _spy_persist(monkeypatch, svc)
    snap, degraded = await svc.get_health_snapshot_with_status("TEST")
    assert degraded == []
    assert persisted == [("TEST", snap, stamp)]
    # It asked about exactly the object the health check served (identity).
    assert len(fake.stamp_asks) == 1 and fake.stamp_asks[0][1] is fake.health


@pytest.mark.asyncio
@pytest.mark.parametrize("fake_factory", [
    lambda h: _FakeHC(h, stamp=CALENDAR_UNKNOWN),
    lambda h: _FakeHC(h, stamp=RuntimeError("stamp read broke")),
    lambda h: _FakeHCWithoutStamp(h),
], ids=["calendar_unknown", "stamp_read_raised", "no_stamp_method"])
async def test_an_unknown_stamp_is_served_not_written_not_degraded(monkeypatch, fake_factory):
    """Mutation: persisting regardless of the stamp writes ("TEST", …) here; adding a
    reason to `degraded` breaks the report collector's use of a complete card."""
    svc = _snapshot_service(monkeypatch, fake_factory(_clean_health()))
    persisted = _spy_persist(monkeypatch, svc)
    snap, degraded = await svc.get_health_snapshot_with_status("TEST")
    assert snap.category == "Financial Health" and snap.rating > 0, "still SERVED"
    assert degraded == [], "the calendar feeds no value: never a degradation"
    assert persisted == []
    # The Tier-1 hit serves the same object with the same (clean) status.
    again, degraded2 = await svc.get_health_snapshot_with_status("TEST")
    assert again is snap and degraded2 == []


@pytest.mark.asyncio
async def test_the_not_entitled_fallback_keeps_the_plain_24h_row(monkeypatch):
    """A permanent 402 on the health check: the fallback IS the answer and no Health Check
    row exists to drop on report day — written with no bound (None)."""
    svc = _snapshot_service(monkeypatch, _FakeHC(FMPNotEntitledException("402")),
                            fmp=_fallback_fmp())
    persisted = _spy_persist(monkeypatch, svc)
    snap, degraded = await svc.get_health_snapshot_with_status("TEST")
    assert degraded == [] and persisted == [("TEST", snap, None)]


@pytest.mark.asyncio
async def test_a_transient_fallback_is_neither_written_nor_stamped(monkeypatch):
    svc = _snapshot_service(monkeypatch, _FakeHC(RuntimeError("down")), fmp=_fallback_fmp())
    persisted = _spy_persist(monkeypatch, svc)
    _snap, degraded = await svc.get_health_snapshot_with_status("TEST")
    assert degraded == ["health_check"] and persisted == []
    assert hs._build_stamp_by_key == {}, "the getter takes its note back"


# ── the Health Check side: every served object has its stamp noted ─────────────────


def _fresh_hc(monkeypatch, *, rows=None) -> hc.HealthCheckService:
    monkeypatch.setattr(hc, "_cache", {})
    monkeypatch.setattr(hc, "_inflight", {})
    monkeypatch.setattr(hc, "_stamp_by_key", {})
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    svc.supabase = _Rows(rows or [])
    monkeypatch.setattr(svc, "_upsert_supabase_cache_safe", lambda *a: None)
    return svc


def _hc_row(stamp):
    return {"response_json": {**_clean_health().model_dump(),
                              "payload_version": hc._HC_PAYLOAD_VERSION},
            "cached_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "next_earnings_date": stamp}


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", [_FAR_FUTURE, None, ""])
async def test_a_tier2_read_notes_the_rows_stamp(monkeypatch, stamp):
    """Mutation: not noting it in `_check_supabase_cache` reads CALENDAR_UNKNOWN."""
    svc = _fresh_hc(monkeypatch, rows=[_hc_row(stamp)])
    resp = await svc.get_health_check("TEST")
    assert svc.next_earnings_for("TEST", served=resp) == (stamp or None)
    # Tier-1 hit: the same object, the same stamp.
    assert await svc.get_health_check("TEST") is resp
    assert svc.next_earnings_for("test", served=resp) == (stamp or None)


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", [_FAR_FUTURE, None, CALENDAR_UNKNOWN])
async def test_a_fresh_build_notes_its_stamp(monkeypatch, stamp):
    svc = _fresh_hc(monkeypatch)
    built = _clean_health()

    async def _build(ticker):
        return hc._HcBuild(response=built, next_earnings=stamp)

    monkeypatch.setattr(svc, "_build_health_check_full", _build)
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    resp = await svc.get_health_check("TEST")
    assert resp is built
    got = svc.next_earnings_for("TEST", served=resp)
    assert got is stamp if not isinstance(stamp, str) else got == stamp


@pytest.mark.asyncio
async def test_the_stamp_is_for_the_served_object_only(monkeypatch):
    svc = _fresh_hc(monkeypatch, rows=[_hc_row(_FAR_FUTURE)])
    resp = await svc.get_health_check("TEST")
    assert svc.next_earnings_for("TEST", served=_clean_health()) is CALENDAR_UNKNOWN
    assert svc.next_earnings_for("OTHER", served=resp) is CALENDAR_UNKNOWN


@pytest.mark.asyncio
async def test_an_in_flight_joiner_reads_the_leaders_stamp(monkeypatch):
    svc = _fresh_hc(monkeypatch)
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda ticker: None)
    release = asyncio.Event()
    built = _clean_health()

    async def _slow_build(ticker):
        await release.wait()
        return hc._HcBuild(response=built, next_earnings=_FAR_FUTURE)

    monkeypatch.setattr(svc, "_build_health_check_full", _slow_build)
    leader = asyncio.ensure_future(svc.get_health_check("TEST"))
    for _ in range(200):
        if "health_check:TEST" in hc._inflight:
            break
        await asyncio.sleep(0.005)
    joiner = asyncio.ensure_future(svc.get_health_check("TEST"))
    await asyncio.sleep(0.05)
    release.set()
    a, b = await asyncio.gather(leader, joiner)
    assert a is b is built
    assert svc.next_earnings_for("TEST", served=b) == _FAR_FUTURE


@pytest.mark.asyncio
@pytest.mark.parametrize("calendar,expected_stamp,written", [
    ([{"symbol": "TEST", "date": _FAR_FUTURE}], _FAR_FUTURE, True),
    ([], None, True),
    (FMPRateLimitException("429"), CALENDAR_UNKNOWN, False),
])
async def test_the_snapshot_stores_the_real_health_checks_stamp(
    monkeypatch, calendar, expected_stamp, written,
):
    """End to end over the REAL Health Check getter and build: the snapshot's row carries
    the stamp that the Health Check's own row carries (or is not written at all)."""
    hc_mod, hc_svc = hcd._service(monkeypatch, hcd._answers(calendar=calendar))
    monkeypatch.setattr(hc, "_stamp_by_key", {})
    monkeypatch.setattr(hc_svc, "_check_supabase_cache", lambda ticker: None)
    hc_rows: List[tuple] = []
    monkeypatch.setattr(hc_svc, "_upsert_supabase_cache_safe",
                        lambda t, r, stamp, *rest: hc_rows.append((t, stamp)))
    svc = _snapshot_service(monkeypatch, hc_svc)
    persisted = _spy_persist(monkeypatch, svc)
    snap, degraded = await svc.get_health_snapshot_with_status("TEST")
    assert degraded == []
    if written:
        assert persisted == [("TEST", snap, expected_stamp)]
        # The Health Check's own write is fire-and-forget (a worker thread): wait for it.
        for _ in range(400):
            if hc_rows:
                break
            await asyncio.sleep(0.005)
        assert hc_rows == [("TEST", expected_stamp)], "both rows carry the same stamp"
    else:
        assert persisted == []
        await asyncio.sleep(0.05)
        assert hc_rows == [], "neither row is written without a report-day bound"


# ══════════════════════════════════════════════════════════════════════════════════════
# 4. The Quick Ratio insight reads like the Current Ratio's
# ══════════════════════════════════════════════════════════════════════════════════════

_GRID = [-30, -24, -15, -5, 0, 7, 15, 40, 80]
_EDGES = [-40, -25, -10, 10, 30, 75]


@pytest.mark.parametrize("pct", _GRID + _EDGES + [e + d for e in _EDGES for d in (-0.01, 0.01)])
def test_cr_and_qr_give_the_same_direction_word(pct):
    """Mutation: the old QR bands give "below" at −24 and −15, and "above" at 7."""
    _cr_text, cr_label, cr_dir = hc._generate_cr_insight(pct, 1.0, 1.0, "sector")
    qr_text, qr_label, qr_dir = hc._generate_qr_insight(pct, 1.0, 1.0, "sector")
    assert qr_dir == cr_dir and qr_label == cr_label, (pct, cr_dir, qr_dir)
    # Quick-asset nouns, never the current ratio's wording.
    assert qr_text != _cr_text
    assert "quick" in qr_text.lower() or "cash" in qr_text.lower(), qr_text


@pytest.mark.parametrize("pct", [x / 2 for x in range(-120, 241)])
def test_the_qr_direction_word_agrees_with_its_status(pct):
    status = hc._determine_status("quick_ratio", pct, False)
    _t, _l, direction = hc._generate_qr_insight(pct, 1.0, 1.0, "industry")
    if status == "positive":
        assert direction in ("above", "well above"), (pct, direction)
    elif status == "negative":
        assert direction in ("below", "well below"), (pct, direction)
    else:
        assert direction in ("near industry average.", "a little below"), (pct, direction)


def test_the_orcl_gap_reads_alike_on_both_rows():
    pct = (1.17 - 1.53) / 1.53 * 100          # −23.5%
    _cr = hc._generate_cr_insight(pct, 1.17, 1.53, "industry")
    qr = hc._generate_qr_insight(pct, 1.17, 1.53, "industry")
    assert _cr[1:] == qr[1:] == ("24%", "a little below")
    assert qr[0] == "industry average. Adequate but tight quick-asset cushion."


@pytest.mark.asyncio
async def test_the_real_build_renders_orcls_cr_and_qr_alike(monkeypatch):
    bench = dict(hcd._BENCH, current_ratio=1.53, quick_ratio=1.53)
    ratios = [dict(hcd._RATIOS, currentRatioTTM=1.17, quickRatioTTM=1.17)]
    _hc, svc = hcd._service(monkeypatch, hcd._answers(ratios=ratios), bench=bench,
                            level="industry")
    resp, _next = await svc._build_health_check("TEST")
    cr = next(m for m in resp.metrics if m.type == "current_ratio")
    qr = next(m for m in resp.metrics if m.type == "quick_ratio")
    assert (cr.status, cr.highlighted_value, cr.highlighted_label) == (
        qr.status, qr.highlighted_value, qr.highlighted_label) == (
        "neutral", "24%", "a little below")
    assert hcd._rendered(qr) == (
        "24% a little below industry average. Adequate but tight quick-asset cushion.")


def test_a_v10_health_check_row_is_a_miss(monkeypatch):
    """The insight text is stored in the row. Mutation: leaving the version at 10."""
    monkeypatch.setattr(hc, "_stamp_by_key", {})
    assert hc._HC_PAYLOAD_VERSION == 11
    svc = hc.HealthCheckService.__new__(hc.HealthCheckService)
    for version, served in ((10, False), (11, True)):
        row = _hc_row(None)
        row["response_json"]["payload_version"] = version
        svc.supabase = _Rows([row])
        assert (svc._check_supabase_cache("TEST") is not None) is served, version
