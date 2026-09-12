"""Every asset class must SEND its verdict-badge threshold, never rely on the client's.

There is exactly ONE iOS decoder for this shape — `BenchmarkSummaryDTO` in
`CryptoAPIModels.swift`, whose own comment warns that "a field added here reaches all four
screens at once — and so does a mistake" — and its fallback is `badgeThreshold ?? 5.0`,
crypto's number, chosen because crypto CAGRs are large enough that a sub-5-point gap is
noise.

Commodity's schema omitted the field and documented the opposite ("iOS falls back to 0"),
so gold, silver and palladium — all within a few points of the S&P's CAGR over the ~19-year
window FMP's 5,000-row cap yields — silently lost the "Outperforming / Underperforming
all-time" pill, while an identical gap on a stock or ETF drew it (found 2026-09-12).

A default on one side of a contract and a different default on the other is not a contract.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_IOS = _BACKEND.parent / "frontend" / "ios" / "ios"


def test_the_commodity_schema_sends_a_threshold_and_it_is_zero():
    from app.schemas.commodity import BenchmarkSummaryResponse

    field = BenchmarkSummaryResponse.model_fields.get("badge_threshold")
    assert field is not None, (
        "commodity omits `badge_threshold`, so the shared iOS decoder substitutes CRYPTO's "
        "5.0 and the verdict badge vanishes on any sub-5-point gap"
    )
    assert field.default == 0.0


def test_the_builder_passes_it_explicitly():
    """A schema default is not the same as a value the builder chose — the ETF and stock
    builders both pass 0.0 by hand, and this one must agree with them."""
    src = (_BACKEND / "app" / "services" / "commodity_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "BenchmarkSummaryResponse"
    ]
    assert calls, "no BenchmarkSummaryResponse construction found in commodity_service"
    for call in calls:
        kw = {k.arg for k in call.keywords}
        assert "badge_threshold" in kw, (
            f"commodity_service.py:{call.lineno} builds the benchmark summary without "
            "badge_threshold — the client then uses crypto's 5.0"
        )


@pytest.mark.parametrize("module,cls", [
    ("app.schemas.commodity", "BenchmarkSummaryResponse"),
    ("app.schemas.crypto", "BenchmarkSummaryResponse"),
])
def test_every_schema_carrying_this_shape_declares_the_threshold(module, cls):
    """`etf` and `stock_overview` pass it explicitly at the call site; these two carry it as
    a schema field. Either way it must reach the wire."""
    import importlib

    model = getattr(importlib.import_module(module), cls)
    assert "badge_threshold" in model.model_fields, f"{module}.{cls} cannot send a threshold"


def test_the_ios_fallback_is_still_the_crypto_number():
    """Anti-vacuity AND the reason the fix is shaped this way. If the shared decoder ever
    stops defaulting to 5.0, this file's premise needs rewriting rather than silently
    passing."""
    src = (_IOS / "Models" / "CryptoAPIModels.swift").read_text(encoding="utf-8")
    src = re.sub(r"//.*$", "", src, flags=re.M)
    assert re.search(r"badgeThreshold\s*\?\?\s*5\.0", src), (
        "the shared BenchmarkSummaryDTO fallback changed — re-derive what each asset "
        "class must send"
    )


def test_the_two_explicit_call_sites_still_send_zero():
    """Control: stock and ETF are the reference behaviour commodity was made to match."""
    for rel in ("app/services/etf_service.py", "app/services/stock_overview_service.py"):
        src = (_BACKEND / rel).read_text(encoding="utf-8")
        code = "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())
        assert "badge_threshold=0.0" in code, f"{rel} no longer sends an explicit 0.0"
