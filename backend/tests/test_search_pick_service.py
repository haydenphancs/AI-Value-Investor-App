"""
Search picks — `search_pick_service` (the write side of "Trending searches").

The privacy design rests on three things this file pins:
  • one account adds at most ONE to a ticker per 7 ET days (so "≥ 3" means three
    accounts, modulo the documented restart gap), and at most 50 a day overall;
  • nothing that could link a person to a ticker is kept: the de-dup keys are keyed
    digests, and no log line carries the account id;
  • a pick that cannot be verified, or that search would hide, is never counted — and a
    failed write is never retried into a second count.

Hermetic: Supabase and the active-listing directory are patched on the module.
"""

from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.services import search_pick_service as svc
from app.services.search_pick_service import SearchPickService, _PickDedup, validate_pick

DIRECTORY = {"AAPL": "Apple Inc.", "NVDA": "NVIDIA Corporation", "SPY": "SPDR S&P 500 ETF Trust",
             "BRK-B": "Berkshire Hathaway Inc.", "AGNC": "AGNC Investment Corp.",
             "AGNCL": "AGNC Investment Corp.", "FXAIX": "Fidelity 500 Index Fund",
             "HEIFX": "Hennessy Equity and Income Investor", "BTC": "Grayscale Bitcoin Mini Trust"}
ACCOUNT = "3f1c9a2e-7b4d-4c1e-9a8f-0d2b6e5c4a11"
TODAY = date(2026, 9, 26)


# ── validate_pick ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("symbol,typ,expected", [
    ("AAPL", "stock", ("AAPL", "stock")),
    (" aapl ", "STOCK", ("AAPL", "stock")),
    ("aapl", None, ("AAPL", "stock")),
    ("SPY", "etf", ("SPY", "etf")),
    ("BRK-B", "stock", ("BRK-B", "stock")),
    ("BTC", "crypto", ("BTC", "crypto")),
    ("btc", "Crypto", ("BTC", "crypto")),
    ("NOTACOIN", "crypto", "invalid"),
    ("AAPL", "index", "invalid"),
    ("GC", "commodity", "invalid"),
    ("AAPL", "bogus", "invalid"),
    ("", "stock", "invalid"),
    ("   ", "stock", "invalid"),
    ("AAPL;DROP", "stock", "invalid"),
    ("A" * 11, "stock", "invalid"),
    ("-AAPL", "stock", "invalid"),
    ("ＡＡＰＬ", "stock", "invalid"),       # fullwidth look-alike
    ("AAPL\n", "stock", ("AAPL", "stock")),  # stripped
    ("AVGOP", "stock", "invalid"),           # a preferred search would hide
    ("BAC-PE", "stock", "invalid"),
    ("AGNCL", "stock", "invalid"),           # a same-issuer note twin
    ("TWTR", "stock", "invalid"),            # dead: not in the directory
    ("FXAIX", "fund", "invalid"),            # NASDAQ mutual fund, hidden by product decision
    ("HEIFX", "stock", "invalid"),           # ... even when typed as a stock
    (7, "stock", "invalid"),
    ("AAPL", 7, "invalid"),
    (None, None, "invalid"),
])
def test_validation_matrix(symbol, typ, expected):
    assert validate_pick(symbol, typ, DIRECTORY) == expected


def test_a_cold_directory_makes_a_listing_unverifiable_but_not_a_coin():
    assert validate_pick("AAPL", "stock", None) == "unverifiable"
    assert validate_pick("BTC", "crypto", None) == ("BTC", "crypto")


# ── _PickDedup ────────────────────────────────────────────────────────────────

D0 = TODAY.toordinal()


def test_one_count_per_ticker_per_seven_days():
    dedup = _PickDedup(b"k" * 32)
    assert dedup.claim(ACCOUNT, "AAPL", "stock", D0) == "ok"
    assert dedup.claim(ACCOUNT, "AAPL", "stock", D0) == "duplicate"
    assert dedup.claim(ACCOUNT, "AAPL", "stock", D0 + 6) == "duplicate"
    assert dedup.claim(ACCOUNT, "AAPL", "stock", D0 + 7) == "ok"


def test_a_different_ticker_type_or_account_counts_separately():
    dedup = _PickDedup(b"k" * 32)
    assert dedup.claim(ACCOUNT, "BTC", "crypto", D0) == "ok"
    assert dedup.claim(ACCOUNT, "BTC", "etf", D0) == "ok"
    assert dedup.claim(ACCOUNT, "NVDA", "stock", D0) == "ok"
    assert dedup.claim("another-account", "NVDA", "stock", D0) == "ok"


def test_a_new_process_forgets_everything():
    """Documents the restart gap: a fresh key (a restart) counts the same pick again. The
    device-side de-dup is what normally prevents this."""
    assert _PickDedup(b"a" * 32).claim(ACCOUNT, "AAPL", "stock", D0) == "ok"
    assert _PickDedup(b"b" * 32).claim(ACCOUNT, "AAPL", "stock", D0) == "ok"


def test_the_daily_cap_per_account():
    dedup = _PickDedup(b"k" * 32, per_user_daily_cap=3)
    outcomes = [dedup.claim(ACCOUNT, f"T{i}", "stock", D0) for i in range(5)]
    assert outcomes == ["ok", "ok", "ok", "capped", "capped"]
    assert dedup.claim(ACCOUNT, "T9", "stock", D0 + 1) == "ok", "the cap resets the next day"


def test_a_full_map_refuses_new_picks_rather_than_evicting():
    dedup = _PickDedup(b"k" * 32, max_entries=2)
    assert dedup.claim("a", "X", "stock", D0) == "ok"
    assert dedup.claim("b", "X", "stock", D0) == "ok"
    assert dedup.claim("c", "X", "stock", D0) == "full"


def test_pruning_forgets_old_picks_and_bounds_memory():
    dedup = _PickDedup(b"k" * 32)
    for i in range(10):
        dedup.claim(f"acct-{i}", "AAPL", "stock", D0)
    dedup.claim("late", "AAPL", "stock", D0 + 7)
    assert len(dedup._seen) == 1 and len(dedup._daily) == 1


def test_no_raw_account_id_is_kept():
    dedup = _PickDedup(b"k" * 32)
    dedup.claim(ACCOUNT, "AAPL", "stock", D0)
    for key in [*dedup._seen, *dedup._daily]:
        assert isinstance(key, bytes) and len(key) == 16
        assert ACCOUNT.encode() not in key
    assert all(isinstance(v, int) for v in dedup._seen.values())


# ── record_pick ───────────────────────────────────────────────────────────────

class _Client:
    def __init__(self, fail: Any = None):
        self.calls: List[Dict] = []
        self.fail = fail

    def rpc(self, name, params):
        self.calls.append({"name": name, **params})
        client = self

        class _Q:
            def execute(self):
                if client.fail is not None:
                    raise client.fail
                return SimpleNamespace(data=1)

        return _Q()


def _wire(monkeypatch, client, directory=DIRECTORY):
    monkeypatch.setattr(svc, "get_supabase", lambda: client)
    monkeypatch.setattr(svc.stock_search_service, "current_directory",
                        lambda query_upper="": directory)


@pytest.mark.asyncio
async def test_a_pick_is_one_increment_with_the_et_day(monkeypatch):
    client = _Client()
    _wire(monkeypatch, client)
    service = SearchPickService(_PickDedup(b"k" * 32))
    assert await service.record_pick(ACCOUNT, "aapl", "stock", today=TODAY) == "counted"
    assert client.calls == [{"name": "increment_search_pick", "p_day": "2026-09-26",
                             "p_ticker": "AAPL", "p_asset_type": "stock"}]
    assert await service.record_pick(ACCOUNT, "AAPL", "stock", today=TODAY) == "duplicate"
    assert len(client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("account,symbol,expected", [
    ("", "AAPL", "anonymous"),
    (None, "AAPL", "anonymous"),
    (ACCOUNT, "AVGOP", "invalid"),
    (ACCOUNT, "TWTR", "invalid"),
])
async def test_uncountable_picks_never_reach_the_database(monkeypatch, account, symbol, expected):
    client = _Client()
    _wire(monkeypatch, client)
    service = SearchPickService(_PickDedup(b"k" * 32))
    assert await service.record_pick(account, symbol, "stock", today=TODAY) == expected
    assert client.calls == []


@pytest.mark.asyncio
async def test_a_cold_directory_drops_the_pick(monkeypatch):
    client = _Client()
    _wire(monkeypatch, client, directory=None)
    service = SearchPickService(_PickDedup(b"k" * 32))
    assert await service.record_pick(ACCOUNT, "AAPL", "stock", today=TODAY) == "unverifiable"
    assert client.calls == []


@pytest.mark.asyncio
async def test_one_account_cannot_reach_the_floor_by_varying_the_type(monkeypatch):
    """The SQL sums stock, etf and fund rows of one symbol — so the de-dup must key on the
    same class, or one crafted client alone makes 3 (the review's reproduction)."""
    client = _Client()
    _wire(monkeypatch, client)
    service = SearchPickService(_PickDedup(b"k" * 32))
    outcomes = [await service.record_pick(ACCOUNT, "AAPL", t, today=TODAY)
                for t in ("stock", "etf", "fund", "stock")]
    assert outcomes == ["counted", "duplicate", "duplicate", "duplicate"]
    assert len(client.calls) == 1
    # A coin is its own security: BTC the coin and BTC the ETF still count separately.
    assert await service.record_pick(ACCOUNT, "BTC", "crypto", today=TODAY) == "counted"
    assert await service.record_pick(ACCOUNT, "BTC", "etf", today=TODAY) == "counted"


@pytest.mark.asyncio
async def test_a_failed_write_is_not_retried_into_a_second_count(monkeypatch, caplog):
    client = _Client(fail=RuntimeError("db down"))
    _wire(monkeypatch, client)
    service = SearchPickService(_PickDedup(b"k" * 32))
    with caplog.at_level(logging.DEBUG, logger=svc.__name__):
        assert await service.record_pick(ACCOUNT, "AAPL", "stock", today=TODAY) == "error"
        client.fail = None
        assert await service.record_pick(ACCOUNT, "AAPL", "stock", today=TODAY) == "duplicate"
    assert len(client.calls) == 1, "at most once"
    for record in caplog.records:
        assert ACCOUNT not in record.getMessage(), "never log the account with a ticker"
        assert "AAPL" not in record.getMessage(), (
            "no ticker on the failed-write line: beside the access log's IP it links a person"
        )


@pytest.mark.asyncio
async def test_a_missing_function_is_one_error_log(monkeypatch, caplog):
    client = _Client(fail=RuntimeError("PGRST202 Could not find the function"))
    _wire(monkeypatch, client)
    service = SearchPickService(_PickDedup(b"k" * 32))
    with caplog.at_level(logging.WARNING, logger=svc.__name__):
        for sym in ("AAPL", "NVDA", "SPY"):
            await service.record_pick(ACCOUNT, sym, "etf" if sym == "SPY" else "stock",
                                      today=TODAY)
    assert [r.levelno for r in caplog.records] == [logging.ERROR]


@pytest.mark.asyncio
async def test_an_unexpected_error_before_the_write_never_raises(monkeypatch):
    def boom(query_upper=""):
        raise RuntimeError("directory bug")

    monkeypatch.setattr(svc.stock_search_service, "current_directory", boom)
    service = SearchPickService(_PickDedup(b"k" * 32))
    assert await service.record_pick(ACCOUNT, "AAPL", "stock", today=TODAY) == "error"


def test_the_module_never_logs_an_account_identifier():
    """Source scan: no logger call in this module may format the account."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path(svc.__file__).read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "logger"):
            names = {n.id for arg in node.args for n in ast.walk(arg) if isinstance(n, ast.Name)}
            assert not names & {"account", "user_id"}, ast.unparse(node)
