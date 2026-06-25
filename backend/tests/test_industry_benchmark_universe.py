"""Robustness tests for `IndustryBenchmarkService._load_universe`.

Confirmed bug: a single non-numeric market cap ('N/A', '', a CSV-formatted
'3,000,000', None) used to raise ValueError inside the `sorted(...)` generator, and
because `_load_universe` is called OUTSIDE the per-sector try/except, ONE bad value
aborted the ENTIRE recompute (every sector, zero rows). The fix coerces totally,
dropping + warning the bad ticker.

`_load_universe` reads `_UNIVERSE_PATH.read_text()` — monkeypatched here so no file
is touched. The method doesn't use `self`, so a `__new__` instance is enough.
"""

import json

import pytest

from app.services import industry_benchmark_service as ibs
from app.services.industry_benchmark_service import IndustryBenchmarkService


class _FakePath:
    def __init__(self, text):
        self._text = text

    def read_text(self):
        return self._text


def _svc():
    return IndustryBenchmarkService.__new__(IndustryBenchmarkService)


def _set_universe(monkeypatch, payload):
    monkeypatch.setattr(ibs, "_UNIVERSE_PATH", _FakePath(json.dumps(payload)))


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


def test_unreadable_universe_file_returns_empty(monkeypatch):
    class _BadPath:
        def read_text(self):
            raise FileNotFoundError("missing")

    monkeypatch.setattr(ibs, "_UNIVERSE_PATH", _BadPath())
    assert _svc()._load_universe() == []    # caught + logged, not raised
