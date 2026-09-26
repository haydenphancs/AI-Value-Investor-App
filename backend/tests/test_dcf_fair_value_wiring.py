"""Phase-3 wiring of the Caydex Fair Value Estimate (model dcf-v1) — behind settings.DCF_ENABLED.

Pins that the switch is a clean swap: OFF = exactly the old FMP behaviour; ON = FMP's DCF is not
fetched, our estimate travels in its OWN field (never the `dcf` slot shipped builds label "FMP
discounted-cash-flow model"), cached rows built under the other setting are rebuilt, and the
report prompts describe the estimate as a gap, never a verdict. Hermetic.
"""
from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path
from typing import Any, Dict, List

import pytest

import app.services.dcf_fair_value_service as dcf_mod
import app.services.valuation_snapshot_service as vss
from app.schemas.dcf_fair_value import DcfFairValueResponse
from app.schemas.stock_overview import SnapshotItemResponse
from app.schemas.ticker_report import WallStreetConsensusResponse
from app.services.agents import narrative_prompts as np_
from _price_fakes import PriceFromFMPFake

_COLLECTOR = Path(__file__).resolve().parents[1] / "app" / "services" / "agents" / \
    "ticker_report_data_collector.py"

_OK = DcfFairValueResponse(symbol="AAPL", status="ok", model_version="dcf-v1", fair_value=250.0,
                           range_low=210.0, range_high=290.0, alternative_value=230.0)
_REFUSED = DcfFairValueResponse(symbol="JPM", status="refused", model_version="dcf-v1",
                                refusal_code="financial_company",
                                refusal_reason=dcf_mod.REFUSAL_REASONS["financial_company"])


# ── valuation snapshot (Analysis tab) ────────────────────────────────────────────────────────

class _FMP:
    def __init__(self) -> None:
        self.dcf_calls = 0

    async def get_company_profile(self, t):
        return {"sector": "Technology", "industry": "Software", "marketCap": 1e12,
                "mktCap": 1e12, "price": 100.0}

    async def get_ratios_ttm(self, t):
        return [{"priceToEarningsRatioTTM": 30.0}]

    async def get_key_metrics_ttm(self, t):
        return [{}]

    async def get_income_statement(self, t, period=None, limit=None):
        return [{"ebitda": 1e11}]

    async def get_cash_flow_statement(self, t, period=None, limit=None):
        return [{}]

    async def get_balance_sheet(self, t, period=None, limit=None):
        return [{"totalDebt": 0, "cashAndCashEquivalents": 0}]

    async def get_dcf(self, t):
        self.dcf_calls += 1
        return {"symbol": t, "date": "2026-09-25", "dcf": 135.8}


class _Lookup:
    def get_current_benchmark_values(self, industry, sector, metrics):
        return {}


class _StubDcfService:
    def __init__(self, result: Any) -> None:
        self.result, self.calls = result, 0

    async def get_fair_value(self, ticker: str):
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _RecordingSupabase:
    """No cached row; records every upsert payload."""
    def __init__(self) -> None:
        self.upserts: List[Dict[str, Any]] = []

    def table(self, _):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def upsert(self, row, **_k):
        self.upserts.append(row)
        return self

    def execute(self):
        return type("R", (), {"data": []})()


def _svc(monkeypatch, enabled: bool, stub: "_StubDcfService"):
    monkeypatch.setattr(vss, "get_sector_benchmark_lookup", lambda: _Lookup())
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", enabled)
    # function-scoped import inside caydex_estimate_or_none → patch the SOURCE module
    monkeypatch.setattr(dcf_mod, "get_dcf_fair_value_service", lambda: stub)
    vss._cache.clear()
    vss._inflight.clear()
    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
    svc.fmp = _FMP()
    svc.price = PriceFromFMPFake(svc.fmp)
    svc.supabase = _RecordingSupabase()
    return svc


async def _serve(svc, ticker="AAPL"):
    snap = await svc.get_valuation_snapshot(ticker)
    import asyncio as _a
    await _a.sleep(0.05)                                  # let the persistence executor run
    return snap


@pytest.mark.asyncio
async def test_switch_off_is_exactly_the_old_fmp_behaviour(monkeypatch):
    stub = _StubDcfService(_OK)
    svc = _svc(monkeypatch, False, stub)
    snap = await _serve(svc)
    assert svc.fmp.dcf_calls == 1 and stub.calls == 0
    assert snap.dcf is not None and snap.dcf.value == 135.8
    assert snap.caydex_estimate is None


@pytest.mark.asyncio
async def test_switch_on_retires_fmp_and_attaches_the_estimate_at_serve_time(monkeypatch):
    stub = _StubDcfService(_OK)
    svc = _svc(monkeypatch, True, stub)
    snap = await _serve(svc)
    assert svc.fmp.dcf_calls == 0, "FMP's DCF must not even be fetched once ours is on"
    assert snap.dcf is None, "our value must never travel in the slot labelled 'FMP model'"
    assert snap.caydex_estimate == _OK
    assert svc.supabase.upserts, "the multiples snapshot itself still persists"
    assert "caydex_estimate" not in svc.supabase.upserts[0]["response_json"], \
        "the estimate must never be frozen into the 24 h snapshot row"
    assert svc.supabase.upserts[0]["response_json"][vss._DCF_SOURCE_KEY] is True


@pytest.mark.asyncio
async def test_a_cache_hit_still_gets_todays_estimate(monkeypatch):
    stub = _StubDcfService(_OK)
    svc = _svc(monkeypatch, True, stub)
    await _serve(svc)
    stub.result = _REFUSED.model_copy(update={"symbol": "AAPL"})
    again = await _serve(svc)                               # tier-1 hit on the snapshot
    assert svc.fmp.dcf_calls == 0 and stub.calls == 2
    assert again.caydex_estimate.status == "refused"


@pytest.mark.asyncio
async def test_a_refusal_is_carried_and_a_failure_degrades_this_serve_only(monkeypatch):
    stub = _StubDcfService(_REFUSED)
    snap = await _serve(_svc(monkeypatch, True, stub))
    assert snap.caydex_estimate.status == "refused"
    failing = _StubDcfService(dcf_mod.DcfInputsUnavailableError("down"))
    svc = _svc(monkeypatch, True, failing)
    snap2 = await _serve(svc)
    assert snap2.caydex_estimate is None and snap2.dcf is None
    assert snap2.metrics, "the rest of the Valuation card must still render"
    failing.result = _OK
    snap3 = await _serve(svc)                               # next serve recovers
    assert snap3.caydex_estimate == _OK


class _Row:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self.payload = payload

    def table(self, _):
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        from datetime import datetime, timezone
        return type("R", (), {"data": [{"response_json": dict(self.payload),
                                        "cached_at": datetime.now(timezone.utc).isoformat()}]})()


def test_cached_rows_built_under_the_other_setting_are_rebuilt(monkeypatch):
    base = SnapshotItemResponse(category="Price", rating=3, metrics=[]).model_dump()
    svc = vss.ValuationSnapshotService.__new__(vss.ValuationSnapshotService)
    fmp_row = {**base, vss._VERSION_KEY: vss._SNAPSHOT_PAYLOAD_VERSION}          # pre-switch row
    ours_row = {**fmp_row, vss._DCF_SOURCE_KEY: True}
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", False)
    svc.supabase = _Row(fmp_row)
    assert svc._check_supabase_cache("AAPL") is not None
    svc.supabase = _Row(ours_row)
    assert svc._check_supabase_cache("AAPL") is None
    monkeypatch.setattr(vss.settings, "DCF_ENABLED", True)
    svc.supabase = _Row(fmp_row)
    assert svc._check_supabase_cache("AAPL") is None, "an FMP-DCF row must not survive the switch"
    svc.supabase = _Row(ours_row)
    assert svc._check_supabase_cache("AAPL") is not None


def test_the_upsert_stamps_the_dcf_source():
    import inspect
    src = inspect.getsource(vss.ValuationSnapshotService._upsert_supabase_cache)
    assert "_DCF_SOURCE_KEY: bool(settings.DCF_ENABLED)" in src


# ── schemas iOS decodes ──────────────────────────────────────────────────────────────────────

def test_schemas_carry_the_estimate_as_an_optional_block():
    ws = {"rating": "hold", "current_price": 200.0, "valuation_status": "fair_value",
          "discount_percent": 0.0, "hedge_fund_price_data": [], "hedge_fund_flow_data": [],
          "momentum_upgrades": 0, "momentum_downgrades": 0}
    assert WallStreetConsensusResponse.model_validate(ws).caydex_fair_value is None
    for est in (_OK, _REFUSED):
        got = WallStreetConsensusResponse.model_validate({**ws, "caydex_fair_value": est.model_dump()})
        assert got.caydex_fair_value == est
    snap = SnapshotItemResponse(category="Price", rating=3, metrics=[],
                                caydex_estimate=_OK.model_dump())
    assert snap.caydex_estimate.fair_value == 250.0


# ── report prompts: a gap, never a verdict ───────────────────────────────────────────────────

def _ws(**kw):
    # discount_percent deliberately DISAGREES with the true gap (price 200 vs estimate 250 = 20 %
    # below), so a line that copied it instead of computing price ÷ estimate would fail.
    return {"current_price": 200.0, "valuation_status": "deep_undervalued", "dcf_measured": True,
            "discount_percent": 7.0, **kw}


def test_the_estimate_line_is_a_neutral_gap():
    line = np_._caydex_estimate_line(_ws(caydex_fair_value=_OK.model_dump()))
    assert "$250.00" in line and "$210.00–$290.00" in line
    assert "the current price is 20% below the estimate" in line
    for word in ("undervalued", "overvalued", "cheap", "target"):
        assert word not in line.lower()
    assert np_._caydex_estimate_line(_ws(caydex_fair_value=_REFUSED.model_dump())) is None
    assert np_._caydex_estimate_line(_ws()) is None


def test_prompts_use_the_estimate_and_the_wording_rule_only_when_it_exists():
    persona = __import__("app.services.agents.persona_config", fromlist=["x"])
    p = next(iter(persona.PERSONA_CONFIGS.values())) if hasattr(persona, "PERSONA_CONFIGS") \
        else persona.get_persona_config("buffett")
    with_est = np_._institutional_flow_insight_prompt(p, "none", _ws(caydex_fair_value=_OK.model_dump()))
    assert "Caydex Fair Value Estimate" in with_est and np_._CAYDEX_ESTIMATE_RULE in with_est
    assert "deep undervalued" not in with_est
    without = np_._institutional_flow_insight_prompt(p, "none", _ws())
    assert np_._CAYDEX_ESTIMATE_RULE not in without and "Caydex Fair Value Estimate" not in without
    refused = np_._institutional_flow_insight_prompt(p, "none", _ws(caydex_fair_value=_REFUSED.model_dump()))
    assert refused == without, "a refusal must leave the old prompt byte-identical"


# ── collector: the FMP DCF is fetched only while the switch is off ───────────────────────────

def _code(path: Path) -> str:
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(path.read_text()).readline):
        if tok.type != tokenize.COMMENT:
            out.append(tok.string)
    return " ".join(out)


def test_collector_swaps_the_fetch_and_the_fair_value_source():
    code = re.sub(r"\s+", " ", _code(_COLLECTOR))
    assert re.search(
        r'\( "caydex_dcf" , _caydex_dcf \( ticker \) , None \) if settings \. DCF_ENABLED '
        r'else \( "dcf" , self \. fmp \. get_dcf \( ticker \) , \{ \} \)', code)
    assert re.search(r'if settings \. DCF_ENABLED : est = out \. caydex_dcf', code)
    assert re.search(r'getattr \( est , "status" , None \) == "ok" else None', code)
    assert re.search(
        r'if settings \. DCF_ENABLED and out \. caydex_dcf is not None : '
        r'out \. wall_street_consensus_partial \[ "caydex_fair_value" \] = '
        r'out \. caydex_dcf \. model_dump \( \)', code)


# ── Stage A / Stage B evidence (review round C6/C19) ─────────────────────────────────────────

def _evidence(monkeypatch, enabled: bool, estimate):
    from app.services.agents import ticker_report_data_collector as col
    monkeypatch.setattr(col.settings, "DCF_ENABLED", enabled)
    out = col.CollectedTickerData(ticker="AAPL", persona_key="buffett")
    out.profile = {"price": 200.0, "companyName": "Apple"}
    out.quote = {"price": 200.0}
    out.computed = {"fair_value": 250.0, "upside_pct": 25.0}
    out.caydex_dcf = estimate
    return col.build_financial_context(out)


def test_evidence_quotes_the_published_estimate_with_its_rule(monkeypatch):
    ev = _evidence(monkeypatch, True, _OK)
    assert "Caydex Fair Value Estimate (a DCF model estimate, shown on this card): $250.00" in ev
    assert "range $210.00–$290.00" in ev and "20% below the estimate" in ev
    assert np_._CAYDEX_ESTIMATE_RULE in ev
    assert "DCF Upside" not in ev and "DCF Fair Value" not in ev


def test_evidence_for_a_refusal_and_with_the_switch_off(monkeypatch):
    ev = _evidence(monkeypatch, True, _REFUSED)
    assert "Caydex Fair Value Estimate: not published" in ev and "DCF Upside" not in ev
    off = _evidence(monkeypatch, False, None)
    assert "DCF Fair Value: $250.00" in off and "Caydex" not in off


def test_no_persona_is_told_to_compute_its_own_per_share_value():
    """Hard rule 3: reports quote the published estimate. Review round C23 found Ackman and Burry
    still told to 'estimate' an intrinsic value and to 'pass' on a margin-of-safety rule."""
    from app.services.agents import persona_config as pc
    for key in pc._PERSONA_REGISTRY:
        prompt = pc.get_persona_config(key).system_prompt
        body = prompt.split("ADVICE BOUNDARY")[0]
        assert not re.search(r"(?im)^\s*-\s*(Estimate|Calculate|Compute)\b[^\n]*intrinsic value", body), key
        assert "If not, you pass" not in body, key
        assert "Intrinsic value estimate using" not in body, key
    assert "never state a different per-share fair value" in pc.ADVICE_BOUNDARY


# ── rollout switches (review round C24 / L8) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shadow_mode_records_in_the_background_and_shows_nothing(monkeypatch):
    stub = _StubDcfService(_OK)
    svc = _svc(monkeypatch, False, stub)
    monkeypatch.setattr(vss.settings, "DCF_SHADOW", True)
    snap = await _serve(svc)
    assert snap.caydex_estimate is None, "shadow mode must show the estimate to nobody"
    assert snap.dcf is not None and svc.fmp.dcf_calls == 1, "FMP's DCF stays in place"
    assert stub.calls == 1, "the estimate is computed (and so recorded) in the background"


@pytest.mark.asyncio
async def test_shadow_off_computes_nothing(monkeypatch):
    stub = _StubDcfService(_OK)
    svc = _svc(monkeypatch, False, stub)
    monkeypatch.setattr(vss.settings, "DCF_SHADOW", False)
    await _serve(svc)
    assert stub.calls == 0


@pytest.mark.asyncio
async def test_the_service_persists_in_shadow_mode(monkeypatch):
    monkeypatch.setattr(dcf_mod.settings, "DCF_ENABLED", False)
    monkeypatch.setattr(dcf_mod.settings, "DCF_SHADOW", True)
    import inspect
    src = inspect.getsource(dcf_mod.DcfFairValueService.get_fair_value)
    assert "persist = bool(settings.DCF_ENABLED or settings.DCF_SHADOW)" in src
    assert src.count("if persist:") == 2


@pytest.mark.asyncio
async def test_the_kill_switch_drops_a_stored_estimate_from_served_reports(monkeypatch):
    from app.services.agents import ticker_report_data_collector as col
    payload = {"ticker": "AAPL", "wall_street_consensus": {"rating": "hold",
               "caydex_fair_value": _OK.model_dump()}}
    monkeypatch.setattr(col.settings, "DCF_ENABLED", False)
    served = await col.patch_wall_street_consensus_live(payload, "AAPL")
    assert served["wall_street_consensus"]["caydex_fair_value"] is None
    assert payload["wall_street_consensus"]["caydex_fair_value"], "the stored row is untouched"
    monkeypatch.setattr(col.settings, "DCF_ENABLED", True)
    assert await col.patch_wall_street_consensus_live(payload, "AAPL") is payload


def test_prompts_ask_for_a_synthesis_not_a_verdict_when_the_estimate_is_shown():
    from app.services.agents import persona_config as pc
    p = pc.get_persona_config("buffett")
    with_est = np_._institutional_flow_insight_prompt(p, "none", _ws(caydex_fair_value=_OK.model_dump()))
    assert "Give the synthesis that ties them together" in with_est
    assert "Give the verdict" not in with_est
    without = np_._institutional_flow_insight_prompt(p, "none", _ws())
    assert "Give the verdict that ties them together" in without       # flag-off prompt unchanged
    digest = np_._digest_wall_street({"wall_street_consensus": _ws(caydex_fair_value=_OK.model_dump())})
    joined = " ".join(digest)
    assert "not a verdict" in joined and "deep_undervalued" not in joined


# ── report-cache source gate + kill switch on every serve path (fix-round review) ────────────

def test_the_report_gate_helpers(monkeypatch):
    from app.services import dcf_report_gate as gate
    caydex_report = {"wall_street_consensus": {"dcf_source": "caydex", "caydex_fair_value": _OK.model_dump()}}
    fmp_report = {"wall_street_consensus": {"dcf_source": "fmp"}}
    legacy = {"wall_street_consensus": {"rating": "hold"}}
    monkeypatch.setattr(gate.settings, "DCF_ENABLED", True)
    assert gate.report_dcf_source_matches(caydex_report)
    assert not gate.report_dcf_source_matches(fmp_report) and not gate.report_dcf_source_matches(legacy)
    assert gate.strip_caydex_if_disabled(caydex_report) is caydex_report
    monkeypatch.setattr(gate.settings, "DCF_ENABLED", False)
    assert not gate.report_dcf_source_matches(caydex_report)
    assert gate.report_dcf_source_matches(fmp_report) and gate.report_dcf_source_matches(legacy)
    stripped = gate.strip_caydex_if_disabled(caydex_report)
    assert stripped["wall_street_consensus"]["caydex_fair_value"] is None
    assert caydex_report["wall_street_consensus"]["caydex_fair_value"], "never mutates the stored row"


def _code_of(rel: str) -> str:
    path = Path(__file__).resolve().parents[1] / rel
    return re.sub(r"\s+", " ", _code(path))


def _function_body(rel: str, anchor: str) -> str:
    """Exact source of one top-level function (nested helpers such as `_query` included), with
    comments removed so an explanatory comment cannot satisfy the assertion."""
    import ast as _ast
    src = (Path(__file__).resolve().parents[1] / rel).read_text()
    name = anchor.split("def ", 1)[1].strip()
    node = next(n for n in _ast.walk(_ast.parse(src))
                if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and n.name == name)
    seg = _ast.get_source_segment(src, node) or ""
    return "\n".join(re.sub(r"#.*$", "", line) for line in seg.splitlines())


def test_every_report_cache_lookup_applies_the_source_gate():
    for rel, anchor in (
        ("app/services/ticker_report_cache.py", "async def get_cached_report"),
        ("app/services/research_service.py", "async def _lookup_shared_cache"),
        ("app/api/v1/endpoints/ticker_report.py", "async def _check_legacy_report_cache"),
    ):
        assert "report_dcf_source_matches(" in _function_body(rel, anchor), rel


def test_every_stored_report_serve_path_applies_the_kill_switch():
    ep = _code_of("app/api/v1/endpoints/ticker_report.py")
    validate = ep[ep.index("def _validate_report"):]
    assert validate.index("strip_caydex_if_disabled ( report )") < validate.index("TickerReportResponse ( ** report )")
    chat = _code_of("app/services/chat_context_resolver.py")
    resolve = chat[chat.index("async def _resolve_ticker_report"):]
    assert resolve.index("strip_caydex_if_disabled ( report )") < resolve.index("_without_unmeasured_guidance")
    col = _code_of("app/services/agents/ticker_report_data_collector.py")
    hook = col[col.index("async def patch_wall_street_consensus_live"):]
    assert "return strip_caydex_if_disabled ( payload )" in hook[:2000]
    assert 'out . wall_street_consensus_partial [ "dcf_source" ] = current_dcf_source ( )' in col


@pytest.mark.asyncio
async def test_shadow_persistence_is_behavioural(monkeypatch):
    """(off, off) persists nothing; shadow and enabled both persist."""
    import asyncio as _a
    for enabled, shadow, expect in ((False, False, 0), (False, True, 2), (True, False, 2)):
        monkeypatch.setattr(dcf_mod.settings, "DCF_ENABLED", enabled)
        monkeypatch.setattr(dcf_mod.settings, "DCF_SHADOW", shadow)
        dcf_mod._cache.clear()
        dcf_mod._failed_at.clear()
        svc = dcf_mod.DcfFairValueService.__new__(dcf_mod.DcfFairValueService)
        calls: List[str] = []

        async def _compute(ticker):
            return _OK, {"price": 1.0}

        svc._compute = _compute
        svc._read_stored = lambda *a, **k: calls.append("read") or None
        svc._write_stored = lambda *a, **k: calls.append("write")
        svc._append_history = lambda *a, **k: calls.append("history")
        await svc.get_fair_value("AAPL")
        await _a.sleep(0.05)
        assert len([c for c in calls if c in ("write", "history")]) == expect, (enabled, shadow, calls)
