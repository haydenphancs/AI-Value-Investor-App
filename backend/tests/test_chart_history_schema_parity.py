"""Backend ↔ iOS contract for the chart bars (plan B2 / B4).

Three responses carry the one bar shape iOS `StockPricePoint` decodes:

  * `GET /{asset}/{symbol}/chart/history` → `ChartHistoryResponse`
    `{symbol, interval, bars: [bar], has_more, settled_through?}`
  * `GET /stocks/{ticker}/chart` and `GET /crypto/{symbol}/chart` → `ChartPricesResponse`
    `{symbol, prices: [bar]}` (iOS `StockChartResponse`)

with bar = `{date, open?, high?, low?, close, volume?}`. A renamed key or a field turned
optional on one side is a decode crash in production, so this pins both sides: the
Pydantic dump, and the Swift declarations (brace-bound, comments stripped).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas.chart_history import ChartBar, ChartHistoryResponse, ChartPricesResponse

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_BAR_KEYS = {"date", "open", "high", "low", "close", "volume"}


def _strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _swift_struct(name: str):
    """``{property: type}`` of one Swift struct, or None when no file declares it."""
    for path in _IOS.rglob("*.swift"):
        src = _strip_comments(path.read_text(encoding="utf-8"))
        m = re.search(r"\bstruct\s+" + re.escape(name) + r"\b[^{]*\{", src)
        if not m:
            continue
        depth, i = 1, m.end()
        while i < len(src) and depth:
            depth += {"{": 1, "}": -1}.get(src[i], 0)
            i += 1
        body = src[m.end(): i - 1]
        # Top-level stored properties only: drop nested braces (computed vars, funcs).
        flat, depth = [], 0
        for ch in body:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif depth == 0:
                flat.append(ch)
        props = {}
        for pm in re.finditer(r"\blet\s+(\w+)\s*:\s*([\w\[\]?.]+)", "".join(flat)):
            props[pm.group(1)] = pm.group(2)
        return props
    return None


# ── backend shapes ──────────────────────────────────────────────────────────

def test_a_history_page_dumps_exactly_the_documented_keys():
    page = ChartHistoryResponse.model_validate({
        "symbol": "INTC", "interval": "weekly", "has_more": True,
        "settled_through": "2026-10-16",
        "bars": [{"date": "1980-03-21", "open": None, "high": None, "low": None,
                  "close": 0.18, "volume": None}],
    })
    dumped = page.model_dump()
    assert set(dumped) == {"symbol", "interval", "bars", "has_more", "settled_through",
                           "reached_start"}
    assert set(dumped["bars"][0]) == _BAR_KEYS


def test_the_worst_case_page_still_validates():
    """Empty bars, no settled date (crypto), every optional field None."""
    page = ChartHistoryResponse.model_validate(
        {"symbol": "BTC", "interval": "1hour", "bars": [], "has_more": False}
    )
    assert page.model_dump() == {"symbol": "BTC", "interval": "1hour", "bars": [],
                                 "has_more": False, "settled_through": None,
                                 "reached_start": False}
    bar = ChartBar.model_validate({"date": "2026-10-16 10:30:00", "close": 1.5})
    assert bar.model_dump() == {"date": "2026-10-16 10:30:00", "open": None, "high": None,
                                "low": None, "close": 1.5, "volume": None}


def test_a_bar_without_a_close_is_refused_at_the_boundary():
    """iOS declares `close: Double` (non-optional): one bar without it fails the whole
    decode, so the schema refuses it rather than ship it."""
    with pytest.raises(Exception):
        ChartBar.model_validate({"date": "2026-10-16", "open": 1.0})


def test_the_range_chart_response_dumps_symbol_and_prices():
    resp = ChartPricesResponse.model_validate({"symbol": "BTC", "prices": [
        {"date": "2026-10-16", "close": 67000.0, "volume": 1.0}]})
    dumped = resp.model_dump()
    assert set(dumped) == {"symbol", "prices"}
    assert set(dumped["prices"][0]) == _BAR_KEYS


# ── the Swift side ──────────────────────────────────────────────────────────

def test_ios_stock_chart_response_matches_the_range_chart_schema():
    resp = _swift_struct("StockChartResponse")
    assert resp is not None, "StockChartResponse disappeared from the iOS tree"
    assert resp == {"symbol": "String", "prices": "[StockPricePoint]"}, resp


def test_ios_stock_price_point_matches_the_bar_schema():
    point = _swift_struct("StockPricePoint")
    assert point is not None, "StockPricePoint disappeared from the iOS tree"
    assert point == {"date": "String", "close": "Double", "open": "Double?",
                     "high": "Double?", "low": "Double?", "volume": "Double?"}, point
    # Required on BOTH sides, optional on both sides — nothing in between.
    for name, field in ChartBar.model_fields.items():
        assert field.is_required() == (not point[name].endswith("?")), name


def test_ios_chart_history_response_matches_when_it_exists():
    """The iOS DTO is written by the iOS workstream against this contract. Until it lands
    this half cannot run; once it does, it must match key for key."""
    dto = _swift_struct("ChartHistoryResponse")
    if dto is None:
        pytest.skip("iOS ChartHistoryResponse not in the tree yet (iOS workstream)")
    assert dto == {"symbol": "String", "interval": "String", "bars": "[StockPricePoint]",
                   "hasMore": "Bool", "settledThrough": "String?",
                   "reachedStart": "Bool?"}, dto
    # The camelCase names must map onto the wire's snake_case keys.
    src = next(
        _strip_comments(p.read_text(encoding="utf-8"))
        for p in _IOS.rglob("*.swift")
        if re.search(r"\bstruct\s+ChartHistoryResponse\b", p.read_text(encoding="utf-8"))
    )
    keys = src[src.index("struct ChartHistoryResponse"):]
    keys = keys[: keys.index("}") + 1]
    assert re.search(r'case\s+hasMore\s*=\s*"has_more"', keys), "hasMore must decode has_more"
    assert re.search(r'case\s+settledThrough\s*=\s*"settled_through"', keys)
    assert re.search(r'case\s+reachedStart\s*=\s*"reached_start"', keys)
    # The backend side of the same contract: has_more is required, settled_through is not.
    assert ChartHistoryResponse.model_fields["has_more"].is_required()
    assert not ChartHistoryResponse.model_fields["settled_through"].is_required()
    # reached_start defaults to False on the wire; iOS decodes it optional and reads a
    # missing key as False (never relax the indicator floor on a guess).
    assert not ChartHistoryResponse.model_fields["reached_start"].is_required()
    assert ChartHistoryResponse.model_fields["reached_start"].default is False
