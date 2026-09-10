"""Robustness tests for `IndustryBenchmarkService._load_universe`.

Confirmed bug: a single non-numeric market cap ('N/A', '', a CSV-formatted
'3,000,000', None) used to raise ValueError inside the `sorted(...)` generator, and
because `_load_universe` is called OUTSIDE the per-sector try/except, ONE bad value
aborted the ENTIRE recompute (every sector, zero rows). The fix coerces totally,
dropping + warning the bad ticker.

`_load_universe` now goes through `universe_data.load_universe`, the single resolver shared
by all four readers — so the stub patches THAT rather than a per-module `_UNIVERSE_PATH`
constant (there used to be four such constants, with three different path idioms). No file
is touched. The method doesn't use `self`, so a `__new__` instance is enough.
"""

import json

import pytest

from app.services import industry_benchmark_service as ibs
from app.services.industry_benchmark_service import IndustryBenchmarkService


def _svc():
    return IndustryBenchmarkService.__new__(IndustryBenchmarkService)


def _set_universe(monkeypatch, payload):
    """Patch the binding the CALLER uses.

    `industry_benchmark_service` does `from ...universe_data import load_universe` at module
    scope, so the name is bound at import time and patching `universe_data.load_universe`
    would not be seen — see `.claude/rules/testing.md` on module- vs function-scoped imports.
    """
    industries = payload.get("industries", []) if isinstance(payload, dict) else []
    monkeypatch.setattr(ibs, "load_universe", lambda _name: industries)


def test_nonnumeric_market_cap_does_not_abort(monkeypatch):
    _set_universe(monkeypatch, {"industries": [
        {"industry": "Software - Infrastructure", "sector": "Technology",
         "market_caps": {"AAPL": "3000000", "BAD": "N/A", "NULL": None}},
    ]})
    result = _svc()._load_universe()       # must NOT raise (used to raise ValueError)
    assert result, "universe should be non-empty"
    inds = dict(dict(result)["Technology"])
    tickers = [t for t, _ in inds["Software - Infrastructure"]]
    assert "AAPL" in tickers
    assert "BAD" not in tickers and "NULL" not in tickers   # dropped, not crashed


def test_csv_formatted_market_cap_dropped(monkeypatch):
    # float("3,000,000") raises — the comma case from the finding.
    _set_universe(monkeypatch, {"industries": [
        {"industry": "X", "sector": "Tech",
         "market_caps": {"AAPL": "3,000,000", "MSFT": "2000000"}},
    ]})
    result = _svc()._load_universe()
    tickers = [t for t, _ in dict(dict(result)["Tech"])["X"]]
    assert tickers == ["MSFT"]             # AAPL (comma) dropped, MSFT kept


def test_all_bad_caps_drops_industry_but_rest_survive(monkeypatch):
    _set_universe(monkeypatch, {"industries": [
        {"industry": "AllBad", "sector": "Energy",
         "market_caps": {"X": "N/A", "Y": None}},
        {"industry": "Good", "sector": "Energy",
         "market_caps": {"XOM": "500000"}},
    ]})
    result = _svc()._load_universe()
    inds = dict(dict(result)["Energy"])
    assert "Good" in inds                  # survives
    assert "AllBad" not in inds            # no valid caps → industry dropped, not fatal


def test_tickers_sorted_by_cap_desc(monkeypatch):
    _set_universe(monkeypatch, {"industries": [
        {"industry": "X", "sector": "Tech",
         "market_caps": {"SMALL": "100", "BIG": "9000", "MID": "500"}},
    ]})
    result = _svc()._load_universe()
    tickers = [t for t, _ in dict(dict(result)["Tech"])["X"]]
    assert tickers == ["BIG", "MID", "SMALL"]


def test_unknown_sector_and_empty_mcaps_skipped(monkeypatch):
    _set_universe(monkeypatch, {"industries": [
        {"industry": "X", "sector": "Unknown", "market_caps": {"A": "100"}},
        {"industry": "Y", "sector": "Tech", "market_caps": {}},
        {"industry": "", "sector": "Tech", "market_caps": {"A": "100"}},
    ]})
    assert _svc()._load_universe() == []    # all three skipped


def test_a_missing_universe_returns_empty_rather_than_raising(monkeypatch):
    """`load_universe` answers [] on any failure (and logs at ERROR itself), so the caller
    degrades instead of 500ing a request path."""
    monkeypatch.setattr(ibs, "load_universe", lambda _name: [])
    assert _svc()._load_universe() == []


def test_the_shared_loader_swallows_a_read_failure_and_logs_it(tmp_path, monkeypatch, caplog):
    """The resolver's OWN contract, tested where it lives.

    All four readers relied on this behaviour independently before; now there is one place
    it can be wrong. ⚠️ It must LOG — a silently empty universe renders as "no data for this
    industry", which is indistinguishable from a real answer.
    """
    import logging

    from app.services import universe_data as ud

    ud.reset_cache_for_tests()
    monkeypatch.setenv("UNIVERSE_DATA_DIR", str(tmp_path))      # empty dir → local miss
    monkeypatch.setattr(ud, "_download_from_storage", lambda _f: False)

    with caplog.at_level(logging.ERROR, logger="app.services.universe_data"):
        assert ud.load_universe(ud.INDUSTRY_UNIVERSE) == []
    ud.reset_cache_for_tests()


def test_the_shared_loader_reads_a_real_file(tmp_path, monkeypatch):
    import json as _json

    from app.services import universe_data as ud

    ud.reset_cache_for_tests()
    monkeypatch.setenv("UNIVERSE_DATA_DIR", str(tmp_path))
    (tmp_path / ud.INDUSTRY_UNIVERSE).write_text(_json.dumps(
        {"ticker_count": 2, "industries": [{"industry": "Software", "sector": "Technology",
                                            "tickers": ["AAPL"], "market_caps": {"AAPL": 1.0}}]}
    ))
    out = ud.load_universe(ud.INDUSTRY_UNIVERSE)
    assert len(out) == 1 and out[0]["industry"] == "Software"
    ud.reset_cache_for_tests()


def test_every_reader_resolves_to_the_same_directory():
    """The point of the shared resolver: four modules at three different nesting depths
    used to compute this independently (`parents[2]` x3, `parents[3]` in the collector)."""
    from app.services import universe_data as ud

    assert ud.universe_path(ud.BENCHMARK_UNIVERSE).parent == ud.universe_path(ud.INDUSTRY_UNIVERSE).parent
    assert ud.universe_path(ud.BENCHMARK_UNIVERSE).parent.name == "data"
    assert ud.universe_path(ud.BENCHMARK_UNIVERSE).parent.parent.name == "backend"
