"""Max Supply is a THREE-state row, and the third state is the one that shipped wrong.

`"No Cap"` is a claim about a coin's monetary policy. CoinGecko sends JSON `null` for an
uncapped coin — and `market_data` is `{}` when `/coins/{id}` degrades, so `md.get("max_supply")`
collapses both to `None`. The old `_fmt_supply(x) if x else "No Cap"` then published the
degraded case as a FACT, on the same screen whose other columns were rewritten to render "—"
for exactly this reason.

House rule (`price_service.py` invariant #1): an unknown is None, never a fabricated value —
and here "never a fabricated STRING" too.
"""
from __future__ import annotations

import pytest

from app.services import crypto_service as cs
from app.services.crypto_service import _CRYPTO_PROFILES


def _svc(monkeypatch):
    monkeypatch.setattr(cs, "get_supabase", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_fmp_client", lambda: None, raising=True)
    monkeypatch.setattr(cs, "get_coingecko_client", lambda: None, raising=True)
    return cs.CryptoService()


def _max_supply_row(svc, **kw):
    stats = svc._build_supply_stats(
        circulating_supply=19_800_000.0, total_supply=None, max_supply=None,
        fdv=None, market_cap=None, avg_volume=None, symbol="BTC", **kw,
    )
    return next(s for s in stats if s.label == "Max Supply")


def test_a_measured_absence_still_says_no_cap(monkeypatch):
    """ETH really has no cap. The fix must not turn a true fact into an em-dash."""
    svc = _svc(monkeypatch)
    assert _max_supply_row(svc, max_supply_known=True).value == "No Cap"


def test_an_unmeasured_absence_renders_a_dash(monkeypatch):
    """The bug: /coins/{id} degraded, `md` is {}, and the screen claimed 'No Cap'."""
    svc = _svc(monkeypatch)
    assert _max_supply_row(svc, max_supply_known=False).value == "—"


def test_a_known_cap_is_unaffected_by_the_flag(monkeypatch):
    """A real number outranks the flag in both directions — it IS the measurement."""
    svc = _svc(monkeypatch)
    for known in (True, False):
        stats = svc._build_supply_stats(
            circulating_supply=19_800_000.0, total_supply=None,
            max_supply=21_000_000.0, fdv=None, market_cap=None,
            avg_volume=None, symbol="BTC", max_supply_known=known,
        )
        row = next(s for s in stats if s.label == "Max Supply")
        assert row.value not in ("No Cap", "—"), row.value
        assert "21" in row.value


# ── the flag's derivation ────────────────────────────────────────────────────

def _derive(symbol: str, md: dict) -> bool:
    """The production expression, kept in one place so the tests below pin the real rule."""
    return symbol in _CRYPTO_PROFILES or (isinstance(md, dict) and "max_supply" in md)


def test_a_degraded_market_data_is_not_a_measurement():
    assert _derive("NOTCURATED", {}) is False
    assert _derive("NOTCURATED", {"circulating_supply": 1.0}) is False


def test_an_explicit_json_null_IS_a_measurement():
    """`in` rather than truthiness: null is CoinGecko's way of saying 'no cap'."""
    assert _derive("NOTCURATED", {"max_supply": None}) is True


def test_a_curated_profile_is_authoritative_in_both_directions():
    """`_CRYPTO_PROFILES` encodes `"max_supply": None` for the genuinely uncapped coins, so
    a curated symbol is known even when CoinGecko says nothing at all."""
    uncapped = [s for s, p in _CRYPTO_PROFILES.items() if p.get("max_supply") is None]
    capped = [s for s, p in _CRYPTO_PROFILES.items() if p.get("max_supply")]
    assert uncapped and capped, "the fixture assumption about the curated table is stale"
    for sym in (uncapped[0], capped[0]):
        assert _derive(sym, {}) is True


def test_the_derivation_in_the_source_matches_this_one():
    """Guard against the two drifting: the tests above are only meaningful if production
    uses the same rule. Docstring-stripped so the prose cannot satisfy the scan."""
    import ast
    import inspect

    src = inspect.getsource(cs.CryptoService.get_crypto_detail)
    tree = ast.parse(inspect.cleandoc(src))
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]
    body = ast.unparse(fn)
    assert "max_supply_known" in body
    assert "symbol in _CRYPTO_PROFILES" in body
    assert "'max_supply' in md" in body or '"max_supply" in md' in body
    assert "max_supply_known=max_supply_known" in body


@pytest.mark.parametrize("known,expected", [(True, "No Cap"), (False, "—")])
def test_the_flag_survives_the_whole_builder_chain(monkeypatch, known, expected):
    """Through the REAL caller, not straight into `_build_supply_stats`.

    Testing the leaf alone let a mutation that simply stopped passing the argument stay
    green — `_build_key_statistics` is the only thing that calls it, so a broken thread is
    invisible from below. This walks the chain the endpoint actually walks.
    """
    svc = _svc(monkeypatch)
    groups = svc._build_key_statistics(
        price=79_000.0, market_cap=None, volume=None, avg_volume=None,
        day_high=None, day_low=None, year_high=None, year_low=None,
        circulating_supply=19_800_000.0, total_supply=None, max_supply=None,
        fdv=None, symbol="BTC", max_supply_known=known,
    )
    rows = [s for g in groups for s in g.statistics if s.label == "Max Supply"]
    assert len(rows) == 1
    assert rows[0].value == expected
