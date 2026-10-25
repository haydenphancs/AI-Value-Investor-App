"""The Caydex Fair Value Estimate on the Analysis tab is Pro/Max (owner decision 2026-10-25).

Owner, quoted: "for Analysis tab, lock caydex fair value estimate for free accounts, it should
show for Pro and Max only", enforced "Server-side (Recommended)" — like the Congress segment of
the Holders tab (`tests/test_holders_entitlement.py`). `stocks.get_stock_overview` resolves the
caller's tier and passes the service's result through
`valuation_snapshot_service.gate_caydex_estimate`, which returns a COPY with the estimate
withheld and `caydex_estimate_locked` raised for a locked plan. The properties that matter, each
driven through the real code:

* the floor fails CLOSED (free, None and any unknown tier are locked; Pro and Max are not);
* only a REAL estimate is locked (owner, 2026-10-25: "Lock only real estimates
  (Recommended)"): status "ok". A refusal ("Not modelled" + its reason), the "temporarily
  unavailable" flag and a Price card with no estimate reach a free account unchanged, so
  upgrading from the lock always reveals a number;
* redaction never mutates the instance the service returns — `/overview` is cached in memory
  per ticker for 120 s and shared by every caller;
* the kill switch (settings.DCF_ENABLED off) leaves the response untouched — no Caydex
  estimate exists then, and FMP's `dcf` row is not part of this gate;
* reports and Cay AI chat stay ungated (owner, 2026-10-25: "Leave reports as is
  (Recommended)") — the gate lives at the route only;
* the paywall row states the same boolean the route enforces, in both copies (backend +
  the Swift bundled fallback).

Hermetic: the overview service and the identity are stubbed; nothing reaches Supabase or FMP.
"""
from __future__ import annotations

import ast
import inspect
import json
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import stocks
from app.config import settings
from app.dependencies import get_current_user, get_current_user_id, get_watchlist_identity
from app.main import app
from app.schemas.dcf_fair_value import DcfFairValueResponse
from app.schemas.stock_overview import (
    DcfEstimateResponse,
    SnapshotItemResponse,
    SnapshotMetricResponse,
    StockOverviewResponse,
)
from app.services import entitlements as ent
from app.services import plan_features as pf
from app.services import valuation_snapshot_service as vss
from app.services.valuation_snapshot_service import gate_caydex_estimate, redact_caydex_estimate

_REPO = Path(__file__).resolve().parents[2]
_ESTIMATE = DcfFairValueResponse(
    symbol="AAPL", status="ok", model_version="dcf-v2", fair_value=250.0,
    range_low=210.0, range_high=290.0, currency="USD",
)
_REFUSED = DcfFairValueResponse(
    symbol="AAPL", status="refused", model_version="dcf-v2", refusal_code="financial_company",
    refusal_reason="Not modelled: banks and insurers are valued on their balance sheet.",
    as_of="2026-10-24",
)


def _price(**over) -> SnapshotItemResponse:
    fields = dict(
        category="Price", rating=3,
        metrics=[SnapshotMetricResponse(name="P/E", value="30.1x", metric_key="pe", score=3)],
        caydex_estimate=_ESTIMATE,
    )
    fields.update(over)
    return SnapshotItemResponse(**fields)


def _card(category: str) -> SnapshotItemResponse:
    return SnapshotItemResponse(
        category=category, rating=4,
        metrics=[SnapshotMetricResponse(name="Gross margin", value="45%")],
    )


def _overview(snapshots=None) -> StockOverviewResponse:
    if snapshots is None:
        snapshots = [_card("Profitability"), _card("Growth"), _price(),
                     _card("Financial Health"), _card("Insiders & Ownership")]
    return StockOverviewResponse.model_validate({
        "symbol": "AAPL", "company_name": "Apple Inc.",
        "current_price": 230.0, "price_change": 1.0, "price_change_percent": 0.4,
        "market_status": {"status": "open"}, "chart_data": [],
        "key_statistics": [], "key_statistics_groups": [], "performance_periods": [],
        "snapshots": snapshots,
        "sector_industry": {"sector": "Technology", "industry": "Consumer Electronics",
                            "sector_performance": 0.5, "industry_rank": "3/10"},
        "company_profile": {"description": "", "ceo": "", "founded": "", "employees": 0,
                            "headquarters": "", "website": ""},
        "related_tickers": [],
    })


def _price_of(resp: StockOverviewResponse) -> SnapshotItemResponse:
    [snap] = [s for s in resp.snapshots if s.category == "Price"]
    return snap


@pytest.fixture
def dcf_on(monkeypatch):
    monkeypatch.setattr(settings, "DCF_ENABLED", True)


# ── the floor ────────────────────────────────────────────────────────────────

def test_the_estimate_shares_the_paid_floor():
    """The same frozenset as signals / Congress / whale detail, so the paid surfaces cannot
    drift into "Pro unlocks one, Max another"."""
    assert ent.CAYDEX_FAIR_VALUE_UNLOCKED_TIERS is ent.SIGNALS_UNLOCKED_TIERS
    assert ent.CAYDEX_FAIR_VALUE_UNLOCKED_TIERS == {ent.TIER_PRO, ent.TIER_MAX}


@pytest.mark.parametrize("tier", ["free", "FREE", " free ", "", None, "enterprise", "max", 0, [], {}])
def test_free_and_unknown_tiers_are_locked(tier):
    """Fail CLOSED: an identity dict hardcoding "free", a missing tier and a value from a
    newer build all fold to Free in `normalize_tier`. ("max" is the DISPLAY name; the key is
    "premium", so the literal "max" is unknown and locked.)"""
    assert ent.caydex_fair_value_unlocked(tier) is False
    assert ent.required_tier_for_caydex_fair_value(tier) == ent.TIER_PRO == "pro"


@pytest.mark.parametrize("tier", ["pro", "premium", " Pro ", "PREMIUM"])
def test_pro_and_max_are_unlocked(tier):
    assert ent.caydex_fair_value_unlocked(tier) is True
    assert ent.required_tier_for_caydex_fair_value(tier) is None


# ── the helper ───────────────────────────────────────────────────────────────

def test_a_locked_caller_gets_the_estimate_stripped_and_the_lock_pair(dcf_on):
    resp = _overview()
    out = gate_caydex_estimate(resp, "free")
    price = _price_of(out)
    assert price.caydex_estimate is None
    assert price.caydex_estimate_unavailable is None
    assert price.caydex_estimate_locked is True
    assert price.caydex_estimate_tier_required == "pro"
    # The free half of the card is untouched: the meter's multiples and rating.
    assert price.metrics == _price_of(resp).metrics
    assert price.rating == _price_of(resp).rating
    # The other four cards are the very same objects, with no lock pair of their own.
    for before, after in zip(resp.snapshots, out.snapshots):
        if before.category != "Price":
            assert after is before
            assert after.caydex_estimate_locked is None


# Owner, 2026-10-25: "Lock only real estimates (Recommended)" — a free account sees "Not
# modelled" + the reason on refused stocks (and "temporarily unavailable" when it is), and the
# lock ONLY where there is an actual estimate (status "ok"), so upgrading always reveals a
# number. Each state below holds no number, so it must reach Free as the very same object.

@pytest.mark.parametrize("tier", ["free", None, "platinum"])
def test_a_refusal_reaches_a_free_account_unchanged(dcf_on, tier):
    resp = _overview([_card("Growth"), _price(caydex_estimate=_REFUSED)])
    out = gate_caydex_estimate(resp, tier)
    assert out is resp
    price = _price_of(out)
    assert price.caydex_estimate is _REFUSED
    assert price.caydex_estimate.refusal_reason.startswith("Not modelled")
    assert price.caydex_estimate_locked is None
    assert price.caydex_estimate_tier_required is None


@pytest.mark.parametrize("tier", ["free", None])
def test_an_outage_flag_reaches_a_free_account_unchanged(dcf_on, tier):
    """"Temporarily unavailable" is not a number either: Free reads the same row as Pro."""
    resp = _overview([_price(caydex_estimate=None, caydex_estimate_unavailable=True)])
    out = gate_caydex_estimate(resp, tier)
    assert out is resp
    price = _price_of(out)
    assert price.caydex_estimate_unavailable is True
    assert price.caydex_estimate_locked is None


@pytest.mark.parametrize("tier", ["free", None])
def test_a_price_card_without_an_estimate_gets_no_lock(dcf_on, tier):
    """A permanent input refusal or the degraded fallback card carries no estimate: a lock
    there would sell an upgrade that shows nothing (review F2, 2026-10-25)."""
    resp = _overview([_card("Profitability"), _price(caydex_estimate=None)])
    out = gate_caydex_estimate(resp, tier)
    assert out is resp
    assert _price_of(out).caydex_estimate is None
    assert _price_of(out).caydex_estimate_locked is None


@pytest.mark.parametrize("state", [
    {"caydex_estimate": _REFUSED},
    {"caydex_estimate": None},
    {"caydex_estimate": None, "caydex_estimate_unavailable": True},
])
def test_redact_itself_passes_a_card_without_a_real_estimate_through(state):
    """The helper holds the rule too, not only the gate's pre-check: a direct caller of
    `redact_caydex_estimate` cannot lock a card that has nothing to reveal."""
    price = _price(**state)
    out = redact_caydex_estimate(_overview([price]), "pro")
    assert out.snapshots[0] is price
    assert out.snapshots[0].caydex_estimate_locked is None


def test_a_real_estimate_elsewhere_is_stripped_while_a_refused_price_card_stays(dcf_on):
    """Fail closed on the stray real estimate; the refusal is still the free account's."""
    refused_price = _price(caydex_estimate=_REFUSED)
    stray = _card("Growth").model_copy(update={"caydex_estimate": _ESTIMATE})
    out = gate_caydex_estimate(_overview([stray, refused_price]), "free")
    assert out.snapshots[0].caydex_estimate is None
    assert out.snapshots[0].caydex_estimate_locked is None
    assert out.snapshots[1] is refused_price


@pytest.mark.parametrize("tier", ["pro", "premium"])
def test_an_unlocked_caller_gets_the_same_object(dcf_on, tier):
    resp = _overview()
    out = gate_caydex_estimate(resp, tier)
    assert out is resp
    assert _price_of(out).caydex_estimate == _ESTIMATE
    assert _price_of(out).caydex_estimate_locked is None


@pytest.mark.parametrize("tier", ["free", "pro", None])
def test_the_kill_switch_leaves_the_response_unchanged(monkeypatch, tier):
    """DCF_ENABLED off: no Caydex estimate exists, and FMP's `dcf` row is not this gate."""
    monkeypatch.setattr(settings, "DCF_ENABLED", False)
    resp = _overview([_price(caydex_estimate=None, dcf=DcfEstimateResponse(value=135.8))])
    out = gate_caydex_estimate(resp, tier)
    assert out is resp
    assert _price_of(out).dcf.value == 135.8
    assert _price_of(out).caydex_estimate_locked is None


def test_redaction_never_mutates_the_cached_instance(dcf_on):
    resp = _overview()
    snapshots_before = list(resp.snapshots)
    price_before = _price_of(resp)
    dump_before = resp.model_dump()

    out = gate_caydex_estimate(resp, "free")

    assert out is not resp
    assert out.snapshots is not resp.snapshots
    assert resp.snapshots == snapshots_before and resp.snapshots[2] is price_before
    assert price_before.caydex_estimate == _ESTIMATE
    assert price_before.caydex_estimate_locked is None
    assert price_before.caydex_estimate_tier_required is None
    assert resp.model_dump() == dump_before
    # ...and the next paying caller of the same cached instance still sees the estimate.
    assert _price_of(gate_caydex_estimate(resp, "pro")).caydex_estimate == _ESTIMATE


def test_a_response_without_a_price_card_is_unchanged(dcf_on):
    resp = _overview([_card("Profitability"), _card("Growth")])
    assert gate_caydex_estimate(resp, "free") is resp
    assert gate_caydex_estimate(_overview([]), "free").snapshots == []


def test_an_estimate_on_another_card_is_stripped_without_a_lock_card(dcf_on):
    """None carries one today; if a future card did, Free must still not receive it."""
    stray = _card("Growth").model_copy(update={"caydex_estimate": _ESTIMATE})
    out = gate_caydex_estimate(_overview([stray]), "free")
    assert out.snapshots[0].caydex_estimate is None
    assert out.snapshots[0].caydex_estimate_locked is None


def test_a_non_overview_value_passes_through(dcf_on):
    """An error JSONResponse from the route's own fallback must not be touched."""
    sentinel = object()
    assert gate_caydex_estimate(sentinel, "free") is sentinel


def test_the_wire_keys_are_snake_case(dcf_on):
    wire = json.loads(gate_caydex_estimate(_overview(), "free").model_dump_json())
    price = [s for s in wire["snapshots"] if s["category"] == "Price"][0]
    assert price["caydex_estimate_locked"] is True
    assert price["caydex_estimate_tier_required"] == "pro"
    assert price["caydex_estimate"] is None
    assert "caydexEstimateLocked" not in json.dumps(wire)


def test_the_new_fields_default_to_none():
    snap = SnapshotItemResponse(category="Price", rating=0, metrics=[])
    assert snap.caydex_estimate_locked is None
    assert snap.caydex_estimate_tier_required is None
    # An unversioned row (written before these keys existed) rehydrates UNLOCKED.
    old = _price().model_dump()
    old.pop("caydex_estimate_locked"); old.pop("caydex_estimate_tier_required")
    assert SnapshotItemResponse(**old).caydex_estimate_locked is None


def test_the_lock_pair_is_never_stored_in_snapshot_cache():
    """A stored lock would lock every PAID caller of that row for 24 h."""
    captured = {}

    class _Table:
        def upsert(self, payload, on_conflict=None):
            captured.update(payload)
            return self

        def execute(self):
            return None

    class _Supabase:
        def table(self, name):
            assert name == "snapshot_cache"
            return _Table()

    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
    svc.supabase = _Supabase()
    locked = redact_caydex_estimate(_overview([_price()]), "pro").snapshots[0]
    assert locked.caydex_estimate_locked is True
    svc._upsert_supabase_cache("AAPL", locked)
    stored = captured["response_json"]
    for key in ("caydex_estimate", "caydex_estimate_unavailable",
                "caydex_estimate_locked", "caydex_estimate_tier_required"):
        assert key not in stored, key


# ── route ────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    logging.disable(logging.CRITICAL)
    try:
        yield TestClient(app)   # no context manager: the lifespan reaches Supabase
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture
def overview_fake(monkeypatch):
    resp = _overview()

    class _Svc:
        async def get_overview(self, ticker, **_kw):
            assert ticker == "AAPL"
            return resp

    monkeypatch.setattr(stocks, "get_stock_overview_service", lambda: _Svc())
    return resp


def _get_as(client, tier):
    # The stocks router carries a router-level `get_current_user_id` guard AND the route's own
    # `get_watchlist_identity` (→ `get_current_user`); both must be satisfied.
    app.dependency_overrides[get_current_user_id] = lambda: "u-1"
    app.dependency_overrides[get_current_user] = lambda: {"id": "u-1", "tier": tier}
    try:
        return client.get("/api/v1/stocks/aapl/overview")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_current_user_id, None)


@pytest.mark.parametrize("tier, locked", [
    ("free", True), (None, True), ("platinum", True), ("pro", False), ("premium", False),
])
def test_the_route_gates_by_the_callers_tier(client, overview_fake, dcf_on, tier, locked):
    r = _get_as(client, tier)
    assert r.status_code == 200, r.text
    price = [s for s in r.json()["snapshots"] if s["category"] == "Price"][0]
    if locked:
        assert price["caydex_estimate"] is None
        assert price["caydex_estimate_locked"] is True
        assert price["caydex_estimate_tier_required"] == "pro"
    else:
        assert price["caydex_estimate"]["fair_value"] == 250.0
        assert price["caydex_estimate_locked"] is None
        assert price["caydex_estimate_tier_required"] is None
    # A Free read never modified the instance the next caller is served.
    assert _price_of(overview_fake).caydex_estimate == _ESTIMATE
    assert _price_of(overview_fake).caydex_estimate_locked is None


def test_the_route_serves_a_free_account_the_refusal_reason(client, monkeypatch, dcf_on):
    resp = _overview([_price(caydex_estimate=_REFUSED)])

    class _Svc:
        async def get_overview(self, ticker, **_kw):
            return resp

    monkeypatch.setattr(stocks, "get_stock_overview_service", lambda: _Svc())
    r = _get_as(client, "free")
    assert r.status_code == 200, r.text
    price = [s for s in r.json()["snapshots"] if s["category"] == "Price"][0]
    assert price["caydex_estimate"]["status"] == "refused"
    assert price["caydex_estimate"]["refusal_reason"] == _REFUSED.refusal_reason
    assert price["caydex_estimate_locked"] is None


def test_the_route_with_the_kill_switch_off_serves_free_unchanged(client, overview_fake, monkeypatch):
    monkeypatch.setattr(settings, "DCF_ENABLED", False)
    price = [s for s in _get_as(client, "free").json()["snapshots"] if s["category"] == "Price"][0]
    assert price["caydex_estimate_locked"] is None


def test_the_route_signature_carries_the_identity():
    params = inspect.signature(stocks.get_stock_overview).parameters
    assert params["user"].default.dependency is get_watchlist_identity


def _gate_applied(source: str) -> bool:
    """True when `get_stock_overview` (brace-bounded by the AST, comments gone) returns
    `gate_caydex_estimate(<the get_overview result>, user.get("tier"))`."""
    tree = ast.parse(source)
    [fn] = [n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_stock_overview"]
    overview_names = {
        t.id
        for n in ast.walk(fn) if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Await) and isinstance(n.value.value, ast.Call)
        and isinstance(n.value.value.func, ast.Attribute)
        and n.value.value.func.attr == "get_overview"
        for t in n.targets if isinstance(t, ast.Name)
    }
    for n in ast.walk(fn):
        if not (isinstance(n, ast.Return) and isinstance(n.value, ast.Call)):
            continue
        call = n.value
        if not (isinstance(call.func, ast.Name) and call.func.id == "gate_caydex_estimate"):
            continue
        if len(call.args) != 2:
            continue
        first, second = call.args
        if not (isinstance(first, ast.Name) and first.id in overview_names):
            continue
        if (isinstance(second, ast.Call) and isinstance(second.func, ast.Attribute)
                and second.func.attr == "get" and isinstance(second.func.value, ast.Name)
                and second.func.value.id == "user" and second.args
                and isinstance(second.args[0], ast.Constant) and second.args[0].value == "tier"):
            return True
    return False


def test_the_route_applies_the_gate_to_what_the_service_returned():
    source = inspect.getsource(stocks)
    assert _gate_applied(source)
    # Anti-vacuity: the scan fails when the gate is bypassed (the pre-change return line).
    bypassed = source.replace(
        "return gate_caydex_estimate(overview, user.get(\"tier\"))", "return overview", 1,
    )
    assert bypassed != source
    assert not _gate_applied(bypassed)


def test_reports_and_chat_stay_ungated():
    """Owner, 2026-10-25: "Leave reports as is (Recommended)": AI research reports (free to
    open from the shared cache, otherwise 20 credits) and Cay AI chat still carry the
    estimate on every tier. The gate must live at the /overview route only — never in the
    valuation service's serve path or the overview service, which both of those read."""
    from app.services import chat_financials_tool, stock_overview_service
    from app.services.agents import ticker_report_data_collector
    for mod in (chat_financials_tool, stock_overview_service, ticker_report_data_collector):
        tree = ast.parse(inspect.getsource(mod))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert not {"gate_caydex_estimate", "redact_caydex_estimate"} & names, mod.__name__
    service_src = inspect.getsource(vss.ValuationSnapshotService)
    assert "gate_caydex_estimate(" not in service_src
    assert "redact_caydex_estimate(" not in service_src


def test_the_core_route_carries_no_snapshots():
    """/overview/core is the instant first paint and stays ungated: it has no snapshot."""
    from app.schemas.stock_overview import StockOverviewCoreResponse
    assert "snapshots" not in StockOverviewCoreResponse.model_fields


# ── paywall row ──────────────────────────────────────────────────────────────

def _row(tier):
    rows = pf.features_for_tier(tier, monthly_credits=1200, report_cost=20, chat_cost=1)
    [row] = [r for r in rows if r["key"] == pf.KEY_CAYDEX_FAIR_VALUE]
    return row


@pytest.mark.parametrize("tier", ent.TIER_ORDER)
def test_the_paywall_row_states_the_enforced_gate(tier):
    row = _row(tier)
    assert row["included"] is ent.caydex_fair_value_unlocked(tier)
    assert row["title"] == "Caydex Fair Value Estimate"
    assert row["accent"] in pf.ACCENTS
    assert row["group"] == pf.GROUP_PLAN


# The owner-approved copy (2026-10-25), byte for byte. The paid line never says "every
# stock" (the model refuses about half of US large caps); the locked line says "there" (the
# Analysis tab), because reports and chat still carry the estimate on every tier.
_PAID_DETAIL = ("A DCF model's fair-value range and estimate on the Analysis tab, wherever "
                "the model can value the company.")
_FREE_DETAIL = ("Free keeps the valuation meter, multiples and peer comparisons on the "
                "Analysis tab. The Caydex Fair Value Estimate there is paid.")


@pytest.mark.parametrize("tier", ent.TIER_ORDER)
def test_the_paywall_copy_is_the_owner_approved_text(tier):
    row = _row(tier)
    expected = _PAID_DETAIL if ent.caydex_fair_value_unlocked(tier) else _FREE_DETAIL
    assert row["detail"] == expected
    assert "every stock" not in row["detail"]


# dcf-methodology-v2.md §5: an estimate from a model, never a verdict, a target or a claim.
_BANNED = re.compile(
    r"\b(undervalued|overvalued|buy|sell|cheap|bargain|price target|target|accura\w*|"
    r"guarantee\w*|upside|downside)\b", re.I,
)


@pytest.mark.parametrize("tier", ent.TIER_ORDER)
def test_the_paywall_copy_makes_no_valuation_claim(tier):
    row = _row(tier)
    assert not _BANNED.search(f"{row['title']} {row['detail']}"), row


def _swift_code_only(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    return "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))


def _fallback_row_text() -> str:
    code = _swift_code_only(
        (_REPO / "frontend/ios/ios/Models/SubscriptionModels.swift").read_text(encoding="utf-8"))
    start = code.index("static func bundled(")
    key = code.index('key: "caydex_fair_value"', start)
    opens = code.rindex("PlanFeature(", start, key)
    depth, i = 0, opens + len("PlanFeature")
    while True:
        depth += {"(": 1, ")": -1}.get(code[i], 0)
        i += 1
        if depth == 0:
            return code[opens:i]


def test_the_swift_fallback_row_matches_the_backend_copy():
    row = _fallback_row_text()
    m = re.search(r'detail:\s*paid\s*\?\s*"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"', row, re.S)
    assert m, row
    assert m.group(1) == _row(ent.TIER_PRO)["detail"] == _PAID_DETAIL
    assert m.group(2) == _row(ent.TIER_FREE)["detail"] == _FREE_DETAIL
    assert 'title: "Caydex Fair Value Estimate"' in row
    assert "included: paid" in row
    assert f'accentKey: "{_row(ent.TIER_PRO)["accent"]}"' in row
    assert f'validatedSymbol("{_row(ent.TIER_PRO)["icon"]}")' in row
