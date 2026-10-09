"""`ETFService.get_fund_facts` — a fund's facts for Ask Cay AI, from the shared fundamentals
section only (never the Gemini strategy hook, never the quote).

Unknown is omitted and NAMED, never 0: a 0 or missing expense ratio is not a free fund, an
unreadable weight is not a 0% weight. Hermetic: `_get_fundamentals` is stubbed.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.services.etf_service import ETFService


def _svc(bundle=None, *, raises=None, gate=None):
    svc = object.__new__(ETFService)
    calls = []

    async def _fund(symbol):
        calls.append(symbol)
        if gate is not None:
            await gate.wait()
        if raises is not None:
            raise raises
        return bundle if bundle is not None else {}

    svc._get_fundamentals = _fund
    return svc, calls


def _bundle(**info_over):
    info = {
        "symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "etfCompany": "SPDR",
        "assetClass": "Equity", "domicile": "US", "expenseRatio": 0.0945,
        "assetsUnderManagement": 571_240_000_000, "navCurrency": "USD",
        "holdingsCount": 503, "inceptionDate": "1993-01-22", "updatedAt": "2026-10-07 06:33:15",
        "sectorsList": [
            {"industry": "Technology", "exposure": 31.2},
            {"industry": "Financial Services", "exposure": "13.5%"},
            {"industry": "Cash & Others", "exposure": 0.3},
            {"industry": "Broken", "exposure": float("nan")},
            {"industry": "Negative", "exposure": -2},
        ],
    }
    info.update(info_over)
    holders = [
        {"asset": "MSFT", "name": "Microsoft Corp", "weightPercentage": 6.9},
        {"asset": "NVDA", "name": "NVIDIA Corp", "weightPercentage": "7.1%"},
        {"asset": "AAPL", "name": "Apple Inc", "weightPercentage": 6.5},
        {"asset": "AAPL", "name": "Apple Inc", "weightPercentage": 6.5},       # duplicate
        {"asset": "XNAN", "name": "Unknown weight", "weightPercentage": float("nan")},
        {"asset": "XBOOL", "name": "Bool weight", "weightPercentage": True},
        {"asset": "XNEG", "name": "Negative weight", "weightPercentage": -1.0},
    ] + [{"asset": f"T{i}", "name": f"Holding {i}", "weightPercentage": 1.0 - i * 0.01}
         for i in range(12)]
    return {"profile": {"companyName": "SPDR S&P 500 ETF Trust", "ipoDate": "1993-01-29"},
            "etf_info": info, "holders": holders, "sector_weights": [], "dividends": []}


@pytest.mark.asyncio
async def test_a_full_bundle_reads_as_labelled_fund_facts():
    svc, calls = _svc(_bundle())
    out = await svc.get_fund_facts("spy")
    assert calls == ["SPY"]
    assert out["available"] is True and out["name"] == "SPDR S&P 500 ETF Trust"
    assert out["issuer"] == "SPDR" and out["inception_date"] == "1993-01-22"
    assert out["expense_ratio_percent"] == 0.0945 and "fund data" in out["expense_ratio_basis"]
    assert out["assets_under_management"] == "USD 571.2B"
    assert out["holdings_count"] == 503
    symbols = [h["symbol"] for h in out["top_holdings"]]
    assert symbols[:3] == ["NVDA", "MSFT", "AAPL"], "sorted by weight, string weights parsed"
    assert symbols.count("AAPL") == 1, "duplicates collapse"
    assert len(out["top_holdings"]) == 10
    assert all("weight_percent" in h for h in out["top_holdings"]), \
        "known weights fill the top ten before unknown ones"
    assert out["sector_weights"][0] == {"sector": "Technology", "weight_percent": 31.2}
    assert [s["sector"] for s in out["sector_weights"]] == ["Technology", "Financial Services"]
    assert out["cash_and_other_percent"] == 0.3, "cash is its own line, never a sector"
    assert out["as_of"] == "2026-10-07"
    assert "unavailable" not in out
    json.dumps(out, allow_nan=False)


@pytest.mark.asyncio
async def test_unknown_weights_keep_their_name_and_lose_their_weight():
    bundle = _bundle()
    bundle["holders"] = [
        {"asset": "XNAN", "name": "Mystery Holding Co", "weightPercentage": float("inf")},
        {"asset": "MSFT", "name": "Microsoft Corp", "weightPercentage": 6.9},
    ]
    svc, _ = _svc(bundle)
    out = await svc.get_fund_facts("SPY")
    assert out["top_holdings"][0] == {"symbol": "MSFT", "name": "Microsoft Corp",
                                      "weight_percent": 6.9}
    assert out["top_holdings"][1] == {"symbol": "XNAN", "name": "Mystery Holding Co"}
    assert out["top_holdings_weight_percent"] == 6.9
    assert any("only the holdings listed with a weight" in n for n in out["notes"])


@pytest.mark.asyncio
@pytest.mark.parametrize("fee", [0, 0.0, None, "n/a", float("nan"), -0.1, True, 250])
async def test_an_unknown_fee_is_named_never_zero(fee):
    svc, _ = _svc(_bundle(expenseRatio=fee, symbol="ZZZZ"))
    out = await svc.get_fund_facts("ZZZZ")      # not in the reference table
    assert "expense_ratio_percent" not in out
    assert "expense ratio" in out["unavailable"]


@pytest.mark.asyncio
async def test_the_reference_table_fallback_is_labelled():
    svc, _ = _svc(_bundle(expenseRatio=None))
    out = await svc.get_fund_facts("SPY")
    assert out["expense_ratio_percent"] == 0.0945
    assert "reference table" in out["expense_ratio_basis"]


@pytest.mark.asyncio
async def test_extreme_assets_and_missing_fields_degrade_honestly():
    svc, _ = _svc(_bundle(assetsUnderManagement=1e15, holdingsCount=None, inceptionDate="",
                          updatedAt=None, sectorsList=[], symbol="ZZZZ"))
    bundle_out = await svc.get_fund_facts("ZZZZ")
    assert bundle_out["assets_under_management"] == "USD 1000.0T"
    assert "number of holdings" in bundle_out["unavailable"]
    assert bundle_out["inception_date"] == "1993-01-29", "the profile's listing date stands in"
    assert "sector weights" in bundle_out["unavailable"]
    assert bundle_out["as_of_note"]


@pytest.mark.asyncio
async def test_an_empty_or_failed_section_is_an_upstream_error():
    svc, _ = _svc({})
    out = await svc.get_fund_facts("ZZZZ")
    assert out["available"] is False and out["upstream"] is True
    svc, _ = _svc(raises=RuntimeError("supabase down"))
    out = await svc.get_fund_facts("ZZZZ")
    assert out["available"] is False and out["upstream"] is True
    assert out["error"] == "fund data could not be loaded right now"


@pytest.mark.asyncio
async def test_a_vendor_exception_never_names_the_vendor_in_the_result(caplog):
    """The exception class stays in the log; the model-facing error is a fixed sentence."""
    from app.integrations.fmp import FMPRateLimitException

    svc, _ = _svc(raises=FMPRateLimitException("429 from upstream"))
    with caplog.at_level("WARNING", logger="app.services.etf_service"):
        out = await svc.get_fund_facts("ZZZZ")
    blob = json.dumps(out).lower()
    assert "fmp" not in blob and "ratelimit" not in blob and "429" not in blob
    assert any("FMPRateLimitException" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_garbage_shapes_never_raise():
    svc, _ = _svc({"etf_info": "x", "holders": {"a": 1}, "profile": None,
                   "sector_weights": None})
    out = await svc.get_fund_facts("SPY")
    assert out["available"] is False
    svc, _ = _svc(["not", "a", "dict"])  # type: ignore[arg-type]
    out = await svc.get_fund_facts("SPY")
    assert out["available"] is False
    for bad in (None, "", "   ", 7):
        out = await svc.get_fund_facts(bad)  # type: ignore[arg-type]
        assert out["available"] is False


@pytest.mark.asyncio
async def test_concurrent_calls_share_one_build_and_get_copies():
    gate = asyncio.Event()
    svc, calls = _svc(_bundle(), gate=gate)
    tasks = [asyncio.ensure_future(svc.get_fund_facts("SPY")) for _ in range(4)]
    await asyncio.sleep(0.01)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert calls == ["SPY"]
    results[0]["top_holdings"].clear()
    assert results[1]["top_holdings"], "each caller gets its own copy"


@pytest.mark.asyncio
async def test_gemini_and_the_detail_build_are_never_reached(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("Gemini must not be reached from fund facts")

    monkeypatch.setattr("app.services.etf_service.get_gemini_client", _boom)
    monkeypatch.setattr("app.integrations.gemini.get_gemini_client", _boom)
    svc, _ = _svc(_bundle())
    for name in ("get_etf_detail", "_build_etf_detail", "_build_strategy",
                 "_generate_hook_text", "_get_quote"):
        async def _never(*_a, _n=name, **_k):
            raise AssertionError(f"{_n} must not be called")
        setattr(svc, name, _never)
    out = await svc.get_fund_facts("SPY")
    assert out["available"] is True


def test_the_method_reads_only_the_fundamentals_section():
    """Source scan (AST — comments and docstrings cannot satisfy it)."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(ETFService.get_fund_facts)))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and isinstance(n.func.value, ast.Name) and n.func.value.id == "self"}
    assert called == {"_get_fundamentals", "_build_asset_allocation"}, called
    # The allocation builder is the screen's pure function: synchronous, no I/O.
    builder = ast.parse(textwrap.dedent(inspect.getsource(ETFService._build_asset_allocation)))
    fn = builder.body[0]
    assert isinstance(fn, ast.FunctionDef), "the builder must stay synchronous"
    assert not any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith))
                   for n in ast.walk(fn))


# ── asset allocation: the screen's own builder, never the raw "Cash & Others" row ─────────

def _lumped(symbol, asset_class, exposure=100):
    bundle = _bundle(symbol=symbol, assetClass=asset_class,
                     sectorsList=[{"industry": "Cash & Others", "exposure": exposure}])
    return bundle


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol,asset_class,bucket", [
    ("TLT", "Fixed Income", "bonds"),
    ("BND", "Bond", "bonds"),
    ("GLD", "Commodity", "commodities"),
    ("IAU", "Gold", "commodities"),
])
async def test_a_lumped_bond_or_gold_fund_is_never_reported_as_all_cash(symbol, asset_class,
                                                                         bucket):
    """FMP lumps a fund it cannot break down into ONE "Cash & Others" row at 100%. The screen
    shows the fund's own asset class with ~5% operating cash; so must the tool — labelled as
    Caydex's estimate."""
    svc, _ = _svc(_lumped(symbol, asset_class))
    out = await svc.get_fund_facts(symbol)
    assert out["cash_and_other_percent"] == 5.0, "never the lump's 100"
    assert out["asset_allocation_percent"] == {bucket: 95.0, "cash": 5.0}
    assert any("Caydex's estimate" in n and "5%" in n for n in out["notes"])
    assert "sector_weights" not in out and "sector weights" in out["unavailable"]
    # Byte-equal to the screen's Holdings & Risk builder for the same rows.
    screen = svc._build_asset_allocation(
        sectors_list=[{"industry": "Cash & Others", "exposure": 100}],
        asset_class=asset_class, total_assets=0.0)
    assert out["cash_and_other_percent"] == screen.cash
    assert out["asset_allocation_percent"][bucket] == getattr(screen, bucket)


@pytest.mark.asyncio
@pytest.mark.parametrize("asset_class", ["Equity", "Currency", None, 7, ""])
async def test_a_lump_on_a_fund_without_a_rule_is_declined_not_shown_as_cash(asset_class):
    svc, _ = _svc(_lumped("ZZZZ", asset_class))
    out = await svc.get_fund_facts("ZZZZ")
    assert "asset_allocation_percent" not in out
    assert "cash_and_other_percent" not in out
    assert "asset allocation" in out["unavailable"]
    assert any("cannot be stated" in n for n in out["notes"])


@pytest.mark.asyncio
async def test_an_equity_fund_keeps_its_real_cash_line_and_string_weights_parse():
    svc, _ = _svc(_bundle(sectorsList=[{"industry": "Technology", "exposure": 60},
                                       {"industry": "Cash & Others", "exposure": "3.5%"},
                                       "garbage-row", None]))
    out = await svc.get_fund_facts("SPY")
    assert out["cash_and_other_percent"] == 3.5
    assert out["asset_allocation_percent"] == {"equities": 96.5, "cash": 3.5}
    assert not any("estimate" in n for n in out.get("notes", []))


@pytest.mark.asyncio
async def test_a_cash_row_without_other_is_a_sector_as_on_the_screen():
    """The screen matches "cash" AND "other"; a bare "Cash" label is shown as a sector there."""
    svc, _ = _svc(_bundle(sectorsList=[{"industry": "Technology", "exposure": 90},
                                       {"industry": "Cash", "exposure": 2}]))
    out = await svc.get_fund_facts("SPY")
    assert {"sector": "Cash", "weight_percent": 2.0} in out["sector_weights"]
    assert out["asset_allocation_percent"] == {"equities": 100.0}
    assert "cash_and_other_percent" not in out


@pytest.mark.asyncio
@pytest.mark.parametrize("exposure", [float("nan"), float("inf"), True, -3, 250, "n/a"])
async def test_an_unreadable_cash_line_declines_the_allocation_never_zero_cash(exposure):
    svc, _ = _svc(_bundle(sectorsList=[{"industry": "Technology", "exposure": 90},
                                       {"industry": "Cash & Others", "exposure": exposure}]))
    out = await svc.get_fund_facts("SPY")
    assert "asset_allocation_percent" not in out and "cash_and_other_percent" not in out
    assert "asset allocation" in out["unavailable"]
    assert out["sector_weights"] == [{"sector": "Technology", "weight_percent": 90.0}]
    json.dumps(out, allow_nan=False)


@pytest.mark.asyncio
async def test_no_sector_list_is_inferred_from_the_class_and_labelled():
    svc, _ = _svc(_bundle(sectorsList=[], assetClass="Fixed Income", symbol="ZZZZ"))
    out = await svc.get_fund_facts("ZZZZ")
    assert out["asset_allocation_percent"] == {"bonds": 100.0}
    assert any("inferred from the asset class" in n for n in out["notes"])


@pytest.mark.asyncio
async def test_a_raising_allocation_builder_drops_only_the_allocation(monkeypatch):
    def _boom(self, **_k):
        raise RuntimeError("builder exploded")

    monkeypatch.setattr(ETFService, "_build_asset_allocation", _boom)
    svc, _ = _svc(_bundle())
    out = await svc.get_fund_facts("SPY")
    assert out["available"] is True and out["holdings_count"] == 503
    assert "asset allocation" in out["unavailable"]


def test_no_error_text_in_fund_facts_is_built_from_an_exception():
    """AST, def-bound to get_fund_facts: an "error" value is never an f-string or a class
    name (a vendor's exception class would reach the model)."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(ETFService.get_fund_facts)))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "error" and (
                        isinstance(value, ast.JoinedStr) or any(
                            isinstance(n, ast.Attribute) and n.attr == "__name__"
                            for n in ast.walk(value))):
                    hits.append(ast.unparse(value))
    assert hits == []


# ── final review 2026-10-09: an unconfirmed NAV currency is never assumed USD ──


@pytest.mark.asyncio
@pytest.mark.parametrize("nav", [None, "", "us$", "USDX", "N/A", 7])
async def test_an_unconfirmed_nav_currency_prints_no_usd(nav):
    out = await _svc(_bundle(navCurrency=nav))[0].get_fund_facts("SPY")
    assert out["assets_under_management"] == "571.2B"
    assert "USD" not in out["assets_under_management"]
    assert out["assets_under_management_currency"].startswith("not confirmed")


@pytest.mark.asyncio
@pytest.mark.parametrize("nav,expected", [("USD", "USD 571.2B"), ("eur", "EUR 571.2B"), (" gbp ", "GBP 571.2B")])
async def test_a_stated_nav_currency_keeps_its_code(nav, expected):
    out = await _svc(_bundle(navCurrency=nav))[0].get_fund_facts("SPY")
    assert out["assets_under_management"] == expected
    assert "assets_under_management_currency" not in out
