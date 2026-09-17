"""Institutions 100.0% / Public 0.0% must never be laundered from an implausible 13F
aggregate (TestFlight E5, build 1.0 (7), AAPL on 2026-09-03; its real figure is ~66%).

`_build_shareholder_breakdown` used to take `ownershipPercent` verbatim and clamp it with
`min(100, …)`. Now every candidate passes a PHYSICAL plausibility gate (>100%, or a total
above 100% beside an insider block too small to be the double-counted party, is rejected)
and walks a numeric-first fallback chain; when nothing survives the figure is UNKNOWN —
0.0 placeholders + `institutions_unknown` (the wire floats stay non-Optional for shipped
builds) — and Public/Other is NOT fabricated as `100 − insiders`.

Math tests over plain dicts, a wiring test through the real `get_holders`, and the two
24h caches that could otherwise re-serve the old number for a day.
"""
from __future__ import annotations

import inspect
import math

import pytest

from app.integrations.fmp import FMPClient, FMPRateLimitException
from app.services import holders_service as hs
from app.services import ownership_snapshot_service as oss
from app.services.holders_service import (
    HoldersService,
    _institutional_pct_plausible,
    _resolve_institutional_pct,
)
from app.schemas.holders import HoldersResponse


def _svc() -> HoldersService:
    return object.__new__(HoldersService)


def _breakdown(profile, summary, holders=None, prior=None):
    return HoldersService._build_shareholder_breakdown(
        _svc(), profile=profile, inst_holders=holders or [], insider_roster=[],
        current_price=10.0, inst_summary=summary, prior_summary=prior, ticker="T",
    )


# ── the gate ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pct, insiders, ok", [
    (133.2, 0.1, False),     # the AAPL shape, before the clamp
    (100.0, 0.1, False),     # exactly 100 beside a tiny insider block
    (99.95, 0.1, False),     # 99.95 + 0.1 > 100 with insiders < 5
    (98.0, 20.0, True),      # a real overlap: the insider block is a 13F filer
    (78.35, 65.0, True),     # PLCE
    (66.55, 0.17, True),     # AAPL, correct
    (95.0, 0.5, True),       # a heavily-institutional small cap
    (0.0, 0.1, False),
    (-3.0, 0.1, False),
    (float("nan"), 0.1, False),
    (float("inf"), 0.1, False),
    (None, 0.1, False),
])
def test_plausibility_is_a_physical_bound_not_a_magic_ceiling(pct, insiders, ok):
    assert _institutional_pct_plausible(pct, insiders) is ok


# ── the fallback chain ───────────────────────────────────────────────────────

def test_implausible_aggregate_is_not_laundered_to_100():
    out = _breakdown({"freeFloat": 99.9}, {"ownershipPercent": 133.2})
    assert out.institutions_percent == 0.0
    assert out.public_other_percent == 0.0, "Public must not be fabricated as 100 − insiders"
    assert out.institutions_unknown is True
    assert out.institutions_source == "unknown"
    assert out.insiders_percent == pytest.approx(0.1, abs=0.05)


def test_exactly_100_with_a_tiny_insider_block_is_implausible():
    out = _breakdown({"freeFloat": 99.9}, {"ownershipPercent": 100.0})
    assert out.institutions_percent != 100.0 and out.institutions_unknown is True


def test_implausible_aggregate_recomputes_from_13f_shares():
    out = _breakdown(
        {"freeFloat": 99.83, "outstandingShares": 15.0e9},
        {"ownershipPercent": 133.2, "numberOf13Fshares": 9.3e9},
    )
    assert out.institutions_percent == pytest.approx(62.0, abs=0.1)
    assert out.institutions_source == "recomputed" and out.institutions_unknown is False
    assert out.public_other_percent == pytest.approx(100 - 0.17 - 62.0, abs=0.2)


def test_recompute_is_also_rejected_when_13f_shares_are_inflated():
    out = _breakdown(
        {"freeFloat": 99.83, "outstandingShares": 15.0e9},
        {"ownershipPercent": 133.2, "numberOf13Fshares": 20.0e9},
    )
    assert out.institutions_source != "recomputed"
    assert out.institutions_percent != pytest.approx(133.3, abs=0.5)


def test_implausible_aggregate_falls_back_to_last_quarter_on_the_row_then_the_prior_summary():
    on_row = _breakdown({"freeFloat": 99.9}, {"ownershipPercent": 133.2, "lastOwnershipPercent": 61.4})
    assert on_row.institutions_percent == pytest.approx(61.4, abs=0.1)
    assert on_row.institutions_source == "last_quarter"
    prior = _breakdown({"freeFloat": 99.9}, {"ownershipPercent": 133.2}, prior={"ownershipPercent": 63.9})
    assert prior.institutions_percent == pytest.approx(63.9, abs=0.1)
    assert prior.institutions_source == "last_quarter"
    absent = _breakdown({"freeFloat": 99.9}, {"ownershipPercent": 133.2}, prior={"nothing": 1})
    assert absent.institutions_unknown is True


def test_top_holder_sum_is_a_last_resort_lower_bound():
    holders = [{"ownership": 8.0}, {"ownership": 6.5}, {"percentOfSharesHeld": 5.1}, {"ownership": "n/a"}]
    out = _breakdown({"freeFloat": 99.9}, {"ownershipPercent": 133.2}, holders=holders)
    assert out.institutions_percent == pytest.approx(19.6, abs=0.1)
    assert out.institutions_source == "top_holders_sum"


def test_overlap_case_is_still_reported_unclamped():
    """PLCE: freeFloat 34.956 → insiders 65.0, ownershipPercent 78.35 — a REAL overlap."""
    out = _breakdown({"freeFloat": 34.956}, {"ownershipPercent": 78.3514})
    assert out.institutions_percent == pytest.approx(78.4, abs=0.1)
    assert out.institutions_source == "summary" and out.institutions_unknown is False
    assert out.public_other_percent == 0.0


@pytest.mark.parametrize("raw", [float("nan"), "n/a", None, -12.0, float("inf")])
def test_garbage_ownership_percent_is_unknown_not_100(raw):
    out = _breakdown({"freeFloat": 99.9}, {"ownershipPercent": raw})
    assert out.institutions_unknown is True
    for v in (out.insiders_percent, out.institutions_percent, out.public_other_percent):
        assert math.isfinite(v) and 0.0 <= v <= 100.0


def test_empty_summary_and_empty_holders_is_flagged_unknown():
    out = _breakdown({"freeFloat": 99.9}, {}, holders=[])
    assert out.institutions_percent == 0.0 and out.institutions_unknown is True


def test_slices_are_never_negative_and_never_exactly_100_from_an_over_100_input():
    for free_float, own in [(0.0, 0.0), (100.0, 0.0), (150.0, 200.0), (-5.0, -5.0), (99.9, 100.0)]:
        out = _breakdown({"freeFloat": free_float}, {"ownershipPercent": own})
        for v in (out.insiders_percent, out.institutions_percent, out.public_other_percent):
            assert v >= 0.0 and math.isfinite(v)
        if own > 100:
            assert out.institutions_percent != 100.0


def test_resolver_tolerates_non_dict_inputs():
    assert _resolve_institutional_pct("x", 0.1, 0.0, ["y", None], prior_summary="z") == (None, "unknown")


# ── wiring: the real get_holders path ───────────────────────────────────────

class _FMP:
    """Every upstream `_build_holders` fans out to, with an implausible summary."""

    def __init__(self, summary, prior=None):
        self.summary, self.prior = summary, prior
        self.prior_calls = []

    async def get_shares_float(self, t): return {"freeFloat": 99.83, "outstandingShares": 15.0e9}
    async def get_institutional_holder(self, t, limit=20): return [{"ownership": 8.0, "marketValue": 1.0, "investorName": "A"}]
    async def get_institutional_ownership_summary(self, t): return self.summary
    async def get_institutional_ownership_for_quarter(self, t, y, q, strict=False):
        self.prior_calls.append((y, q)); return self.prior
    async def get_insider_trading(self, t, since_date=None): return []
    async def get_insider_roster(self, t): return []
    async def get_historical_prices(self, t, from_date=None, to_date=None): return []
    async def get_senate_latest(self, limit=1000): return []
    async def get_house_latest(self, limit=1000): return []
    async def get_senate_disclosure(self, t): return []
    async def get_house_disclosure(self, t): return []
    async def get_stock_price_quote(self, t): return {"price": 330.0}


class _CA:
    async def get_split_rows(self, *a, **k): return []


class _FakeSupabase:
    def __init__(self, row=None): self.row, self.upserts = row, []
    def table(self, _): return self
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def upsert(self, payload, **k): self.upserts.append(payload); return self
    def execute(self):
        class _R: pass
        r = _R(); r.data = [self.row] if self.row else []
        return r


def _wired(fmp, supabase=None) -> HoldersService:
    from tests._price_fakes import PriceFromFMPFake
    hs._cache.clear(); hs._inflight.clear()
    svc = object.__new__(HoldersService)
    svc.fmp = fmp
    svc.price = PriceFromFMPFake(fmp)
    svc.corporate_actions = _CA()
    svc.supabase = supabase or _FakeSupabase()
    return svc


@pytest.mark.asyncio
async def test_get_holders_flags_an_implausible_aggregate_and_fetches_the_prior_quarter_only_then():
    fmp = _FMP(summary={"ownershipPercent": 133.2, "numberOf13Fshares": 25.0e9}, prior={"ownershipPercent": 63.9})
    resp = await _wired(fmp).get_holders("AAPL")
    b = resp.shareholder_breakdown
    assert b.institutions_percent == pytest.approx(63.9, abs=0.1)
    assert b.institutions_source == "last_quarter" and b.institutions_unknown is False
    # The 8-quarter Institutions history fetches the prior quarter once regardless; the
    # gate's own fetch is the SECOND call for that tuple.
    from app.utils.period_labels import latest_filed_13f_quarter
    y, q = latest_filed_13f_quarter()
    prior = (y, q - 1) if q > 1 else (y - 1, 4)
    assert fmp.prior_calls.count(prior) == 2

    fine = _FMP(summary={"ownershipPercent": 66.55})
    resp = await _wired(fine).get_holders("AAPL")
    assert resp.shareholder_breakdown.institutions_percent == pytest.approx(66.6, abs=0.1)
    assert fine.prior_calls.count(prior) == 1, "the gate's prior-quarter fetch happens only when it trips"


async def _settle(sb):
    import asyncio
    for _ in range(50):                    # the upsert is dispatched in the background
        if sb.upserts:
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_get_holders_lands_on_the_top_holder_lower_bound_and_stamps_the_version():
    fmp = _FMP(summary={"ownershipPercent": 133.2}, prior=None)
    sb = _FakeSupabase()
    resp = await _wired(fmp, sb).get_holders("AAPL")
    b = resp.shareholder_breakdown
    # the top-holder sum (8.0) IS plausible, so the chain lands there rather than unknown
    assert b.institutions_source == "top_holders_sum" and b.institutions_percent == 8.0
    await _settle(sb)
    assert sb.upserts and sb.upserts[0]["response_json"]["payload_version"] == hs._HOLDERS_PAYLOAD_VERSION


@pytest.mark.asyncio
async def test_get_holders_reads_unknown_when_every_rung_fails_and_persists_the_flag():
    """No plausible summary, no prior row, NO top holders at all → unknown, and the persisted
    row carries the flag so a cached read renders "—" too."""
    class _NoHolders(_FMP):
        async def get_institutional_holder(self, t, limit=20):
            return []

    fmp = _NoHolders(summary={"ownershipPercent": 133.2}, prior=None)
    sb = _FakeSupabase()
    resp = await _wired(fmp, sb).get_holders("AAPL")
    b = resp.shareholder_breakdown
    assert b.institutions_unknown is True and b.institutions_source == "unknown"
    assert b.institutions_percent == 0.0 and b.public_other_percent == 0.0
    await _settle(sb)
    assert sb.upserts, "an honest unknown from a healthy upstream IS a result and may be cached"
    assert sb.upserts[0]["response_json"]["shareholder_breakdown"]["institutions_unknown"] is True


@pytest.mark.asyncio
async def test_a_transient_failure_on_the_prior_quarter_rescue_is_served_but_not_persisted():
    """A 429 on the rescue rung leaves the figure at a lower bound; honest to SERVE, not
    to PIN for 24h — a 5-minute rebuild may recover the real number. The rung is opt-in
    strict: the 8-quarter history gather still folds the same exception to None."""
    from app.utils.period_labels import latest_filed_13f_quarter
    y, q = latest_filed_13f_quarter()
    prior = (y, q - 1) if q > 1 else (y - 1, 4)

    class _RescueDown(_FMP):
        async def get_institutional_ownership_for_quarter(self, t, year, quarter, strict=False):
            self.prior_calls.append((year, quarter))
            if (year, quarter) == prior and strict:
                raise FMPRateLimitException("slow down")
            return None

    fmp = _RescueDown(summary={"ownershipPercent": 133.2})
    sb = _FakeSupabase()
    resp = await _wired(fmp, sb).get_holders("AAPL")
    assert resp.shareholder_breakdown.institutions_source == "top_holders_sum"
    await _settle(sb)
    assert sb.upserts == [], "a degraded build was written to holders_cache"


@pytest.mark.asyncio
async def test_a_legitimate_missing_prior_row_still_persists():
    """`None` from FMP (no row for that quarter) is NOT a failure and must not turn every
    read into a 5-minute rebuild loop."""
    fmp = _FMP(summary={"ownershipPercent": 133.2}, prior=None)
    sb = _FakeSupabase()
    await _wired(fmp, sb).get_holders("AAPL")
    await _settle(sb)
    assert sb.upserts


# ── the two 24h caches ───────────────────────────────────────────────────────

def test_an_unversioned_holders_row_is_refused():
    from datetime import datetime, timezone
    stale = HoldersResponse(symbol="AAPL").model_dump()
    stale["shareholder_breakdown"]["institutions_percent"] = 100.0
    sb = _FakeSupabase(row={"response_json": stale, "cached_at": datetime.now(timezone.utc).isoformat()})
    svc = object.__new__(HoldersService); svc.supabase = sb
    assert svc._check_supabase_cache("AAPL") is None


def test_a_current_holders_row_is_served():
    from datetime import datetime, timezone
    fresh = {**HoldersResponse(symbol="AAPL").model_dump(), "payload_version": hs._HOLDERS_PAYLOAD_VERSION}
    sb = _FakeSupabase(row={"response_json": fresh, "cached_at": datetime.now(timezone.utc).isoformat()})
    svc = object.__new__(HoldersService); svc.supabase = sb
    assert svc._check_supabase_cache("AAPL") is not None


def test_an_unversioned_ownership_snapshot_row_is_refused_and_a_current_one_served():
    from datetime import datetime, timezone
    from app.schemas.stock_overview import SnapshotItemResponse
    base = SnapshotItemResponse(category="Insiders & Ownership", rating=3, metrics=[]).model_dump()
    now = datetime.now(timezone.utc).isoformat()
    svc = object.__new__(oss.OwnershipSnapshotService)
    svc.supabase = _FakeSupabase(row={"response_json": base, "cached_at": now})
    assert svc._check_supabase_cache("AAPL") is None
    svc.supabase = _FakeSupabase(row={"response_json": {**base, oss._VERSION_KEY: oss._SNAPSHOT_PAYLOAD_VERSION}, "cached_at": now})
    assert svc._check_supabase_cache("AAPL") is not None
    svc.supabase = _FakeSupabase()
    svc._upsert_supabase_cache("AAPL", SnapshotItemResponse(**base))
    assert svc.supabase.upserts[0]["response_json"][oss._VERSION_KEY] == oss._SNAPSHOT_PAYLOAD_VERSION


@pytest.mark.asyncio
async def test_ownership_snapshot_renders_unknown_as_a_dash_and_scores_it_neutral(monkeypatch):
    from app.schemas.holders import ShareholderBreakdownSchema

    class _Holders:
        async def get_holders(self, ticker):
            return HoldersResponse(
                symbol="AAPL",
                shareholder_breakdown=ShareholderBreakdownSchema(
                    insiders_percent=0.2, institutions_percent=0.0, public_other_percent=0.0,
                    institutions_unknown=True, institutions_source="unknown",
                ),
            )

    monkeypatch.setattr(hs, "get_holders_service", lambda: _Holders())
    svc = object.__new__(oss.OwnershipSnapshotService)
    seen = {}
    real_rating = oss.OwnershipSnapshotService._compute_rating

    def spy(self_, insider_pct, inst_pct, *a, **k):
        seen["inst_pct"] = inst_pct
        return real_rating(self_, insider_pct, inst_pct, *a, **k)

    monkeypatch.setattr(oss.OwnershipSnapshotService, "_compute_rating", spy)
    out = await svc._compute("AAPL")
    by_name = {m.name: m.value for m in out.metrics}
    assert by_name["Institutional Ownership"] == "—"
    assert by_name["Public & Other"] == "—"
    # the rating must see UNKNOWN (None), not the 0.0 placeholder (which scores "<10%")
    assert seen["inst_pct"] is None
    neutral = real_rating(svc, 0.2, None, 0.0, True, 0.0, True)
    as_zero = real_rating(svc, 0.2, 0.0, 0.0, True, 0.0, True)
    assert neutral != as_zero and out.rating == neutral


# ── the summary fetch itself ────────────────────────────────────────────────

def test_summary_quarter_is_deadline_aware():
    """The summary asks FMP for the newest quarter whose 13F deadline has PASSED — asserted
    on the request parameters, not on the source text."""
    import asyncio
    from app.utils.period_labels import latest_filed_13f_quarter

    seen = []

    async def fake(self, endpoint, params=None, **kw):
        seen.append((endpoint, dict(params or {})))
        return [{"ownershipPercent": 66.5}]

    orig = FMPClient._make_request
    FMPClient._make_request = fake
    try:
        asyncio.run(FMPClient.__new__(FMPClient).get_institutional_ownership_summary("AAPL"))
    finally:
        FMPClient._make_request = orig
    y, q = latest_filed_13f_quarter()
    assert seen[0][0] == "institutional-ownership/symbol-positions-summary"
    assert (seen[0][1]["year"], seen[0][1]["quarter"]) == (y, q)


def test_summary_reraises_rate_limit_but_folds_404():
    import asyncio
    import httpx

    client = FMPClient.__new__(FMPClient)

    async def boom(self, endpoint, params=None, **kw):
        raise FMPRateLimitException("slow down")

    orig = FMPClient._make_request
    FMPClient._make_request = boom
    try:
        with pytest.raises(FMPRateLimitException):
            asyncio.run(client.get_institutional_ownership_summary("AAPL"))

        async def not_found(self, endpoint, params=None, **kw):
            resp = httpx.Response(404, request=httpx.Request("GET", "https://x"))
            raise httpx.HTTPStatusError("nf", request=resp.request, response=resp)

        FMPClient._make_request = not_found
        assert asyncio.run(client.get_institutional_ownership_summary("AAPL")) == {}
    finally:
        FMPClient._make_request = orig


def test_the_detail_card_omits_an_implausible_percent_and_keeps_a_plausible_one():
    from app.api.v1.endpoints.stocks import _plausible_percent_institutional as f
    assert f([{"ownershipPercent": 133.2}], 0.17) is None
    assert f({"ownershipPercent": 100.0}, 0.1) is None
    assert f([{"ownershipPercent": 66.55}], 0.17) == 66.55
    assert f({"ownershipPercent": 78.35}, 65.0) == 78.35          # a real overlap
    assert f([{"ownershipPercent": "n/a"}], 0.1) is None
    assert f([], 0.1) is None and f({}, None) is None and f(None, 0.1) is None


def test_the_detail_route_calls_the_gate_not_the_raw_field():
    """AST, not text: the route body must CALL the helper and must not assign the raw
    `ownershipPercent` to `percent_institutional` anywhere."""
    import ast, inspect
    from app.api.v1.endpoints import stocks
    src = inspect.getsource(stocks.get_stock_details)
    tree = ast.parse(src)
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_plausible_percent_institutional" in calls
    raw_reads = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value == "ownershipPercent"]
    assert not raw_reads, "the route reads ownershipPercent directly again"
