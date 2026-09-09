"""Outlier inputs to the two crypto chart-row builders.

Both take a list of upstream rows and map it to chart points. On the FMP branch that list
is RAW upstream JSON (`raw.get("historical", [])`), so a malformed payload can carry a
non-dict element — and `p.get(...)` on a bare string/None raises AttributeError, which
escapes the builder and 500s the ENTIRE crypto detail screen rather than costing one bar.

`_build_related_cryptos` in the same module already carries this guard, with the comment
"one non-dict element must not crash the whole response". These two did not.

Also pinned here: the finite/positive filter and the sub-penny ladder, because the whole
point of dropping a bad row is that the GOOD rows still arrive intact.
"""

from __future__ import annotations

import json
import math

import pytest

from app.services.crypto_service import CryptoService

NAN, INF = float("nan"), float("inf")

# Every shape a malformed upstream payload has actually produced or plausibly can.
JUNK_ROWS = [
    None,
    5,
    "not-a-row",
    ["nested", "list"],
    {},                      # dict but empty
    {"close": None},
    {"close": NAN},
    {"close": INF},
    {"close": -INF},
    {"close": 0},
    {"close": -1.0},
    {"close": "abc"},
]

GOOD_ROW = {"date": "2026-09-08", "open": 1.0, "high": 2.0, "low": 0.5,
            "close": 1.5, "volume": 100.0}
SUB_PENNY = {"date": "2026-09-08", "open": None, "high": None, "low": None,
             "close": 5.39e-06, "volume": 1.0}


def _svc() -> CryptoService:
    return object.__new__(CryptoService)


# ── neither builder may raise on junk ────────────────────────────────────────

def test_chart_rows_from_survives_every_junk_row():
    out = CryptoService._chart_rows_from(list(JUNK_ROWS))
    assert out == [], "no junk row should yield a chart point"


def test_extract_chart_data_survives_every_junk_row():
    out = _svc()._extract_chart_data(list(JUNK_ROWS), "3M")
    assert out == []


@pytest.mark.parametrize("bad", JUNK_ROWS)
def test_one_bad_row_does_not_destroy_the_good_ones(bad):
    """The actual contract: a malformed element costs ONE bar, not the whole chart."""
    rows = [bad, GOOD_ROW, bad, SUB_PENNY]
    out = CryptoService._chart_rows_from(rows)
    assert len(out) == 2, f"a {type(bad).__name__} row took good rows with it: {out}"
    assert out[0]["close"] == 1.5
    # Sub-penny must survive: round(x, 2) would collapse SHIB-class prices to 0.0.
    assert out[1]["close"] > 0


def test_extract_chart_data_keeps_good_rows_beside_junk():
    out = _svc()._extract_chart_data([None, GOOD_ROW, "x", SUB_PENNY], "3M")
    assert [r["close"] for r in out] == [1.5, pytest.approx(5.39e-06, rel=1e-3)]


# ── nothing non-finite may reach the wire ────────────────────────────────────

@pytest.mark.parametrize("builder", ["rows_from", "extract"])
def test_output_is_always_json_serializable_under_allow_nan_false(builder):
    """FastAPI's encoder uses allow_nan=False; a NaN anywhere is a hard 500."""
    rows = list(JUNK_ROWS) + [
        GOOD_ROW,
        {"date": "2026-09-08", "open": NAN, "high": INF, "low": -INF,
         "close": 2.0, "volume": NAN},
    ]
    out = (CryptoService._chart_rows_from(rows) if builder == "rows_from"
           else _svc()._extract_chart_data(rows, "3M"))
    json.dumps(out, allow_nan=False)
    for r in out:
        for k, v in r.items():
            if isinstance(v, float):
                assert math.isfinite(v), f"{k}={v} is not finite"


def test_a_non_finite_ohlc_does_not_drop_a_row_with_a_good_close():
    """open/high/low are Optional on iOS, so they degrade to None — the bar still draws."""
    row = {"date": "2026-09-08", "open": NAN, "high": INF, "low": -INF,
           "close": 2.0, "volume": NAN}
    out = CryptoService._chart_rows_from([row])
    assert len(out) == 1 and out[0]["close"] == 2.0
    assert out[0]["open"] is None and out[0]["high"] is None and out[0]["volume"] is None


# ── empty / None inputs ──────────────────────────────────────────────────────

@pytest.mark.parametrize("empty", [None, [], ()])
def test_empty_input_yields_an_empty_chart_not_an_error(empty):
    assert CryptoService._chart_rows_from(empty) == []


def test_extract_chart_data_handles_empty():
    assert _svc()._extract_chart_data([], "3M") == []
    assert _svc()._extract_chart_data(None, "3M") == []
