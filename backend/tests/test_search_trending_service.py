"""
The search-screen chips — `search_trending_service` (read path).

What must hold, and why each matters:
  • nothing under the privacy floor of 3 is ever shown, whatever the database returns;
  • too little live data falls back to ONE honestly labelled "popular" section, never a
    half-empty "Trending" one — and the stocks-only variant falls back on its own numbers;
  • a ticker the search rules hide (a preferred, a dead or renamed listing) never appears
    as trending just because people picked it before it died;
  • the endpoint can never error: a failed list degrades alone, a total failure serves the
    curated lists, and nothing failed is cached for an hour;
  • the module never reads the caller (an AST scan below).

Hermetic: Supabase is a fake patched onto the service module's own bindings.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from app.schemas.search_trending import SearchTrendingResponse
from app.services import search_trending_service as svc
from app.services.search_trending_service import (
    Curated,
    Item,
    SearchTrendingService,
    assemble_sections,
    build_response,
    load_curated,
    normalize_rows,
)

DIRECTORY = {
    "AAPL": "Apple Inc.", "NVDA": "NVIDIA Corporation", "MSFT": "Microsoft Corporation",
    "TSLA": "Tesla, Inc.", "AMZN": "Amazon.com, Inc.", "META": "Meta Platforms, Inc.",
    "GOOGL": "Alphabet Inc.", "GOOG": "Alphabet Inc.", "SPY": "SPDR S&P 500 ETF Trust",
    "PLTR": "Palantir Technologies Inc.", "AMD": "Advanced Micro Devices, Inc.",
    "AVGO": "Broadcom Inc.", "BTC": "Grayscale Bitcoin Mini Trust ETF", "BRK-B": "Berkshire",
    "SOFI": "SoFi Technologies, Inc.", "HOOD": "Robinhood Markets, Inc.",
}


def _it(sym: str, typ: str = "stock", name: str = "") -> Item:
    return Item(symbol=sym, name=name, type=typ)


def _row(ticker: str, typ: str = "stock", n: Any = 5, key: str = "picks", **extra) -> Dict:
    return {"ticker": ticker, "asset_type": typ, key: n, **extra}


def _norm(rows, key="picks", directory=DIRECTORY, blocked=()):
    return normalize_rows(rows, count_key=key, directory=directory, curated_names={},
                          blocked=blocked)


CURATED = Curated(
    [_it("AAPL", name="Apple Inc."), _it("NVDA"), _it("MSFT"), _it("SPY", "etf"), _it("TSLA"),
     _it("AMZN"), _it("BTC", "crypto", "Bitcoin"), _it("GOOGL")],
    [_it("AAPL"), _it("MSFT"), _it("NVDA"), _it("AMZN"), _it("GOOGL"), _it("META"), _it("TSLA"),
     _it("BRK-B")],
    frozenset(),
)


# ── normalize_rows ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("count", [0, 1, 2, 2.99, -5, float("nan"), float("inf") * -1, "7",
                                   None, True, [3], {"n": 3}])
def test_below_floor_or_malformed_counts_never_show(count):
    assert _norm([_row("AAPL", n=count)]) == []


@pytest.mark.parametrize("count", [3, 3.0, 12, 10_000])
def test_counts_at_or_over_the_floor_show(count):
    assert [i.symbol for i in _norm([_row("AAPL", n=count)])] == ["AAPL"]


@pytest.mark.parametrize("row", [
    None, "AAPL", 7, [], {"asset_type": "stock", "picks": 5},
    {"ticker": 7, "asset_type": "stock", "picks": 5},
    {"ticker": "AAPL", "asset_type": None, "picks": 5},
    {"ticker": "   ", "asset_type": "stock", "picks": 5},
    {"ticker": "AAPL", "asset_type": "index", "picks": 5},
    {"ticker": "GC", "asset_type": "commodity", "picks": 5},
])
def test_malformed_rows_are_skipped_not_fatal(row):
    assert _norm([row, _row("NVDA")]) == [_it("NVDA", name="NVIDIA Corporation")]


def test_non_list_payloads_yield_nothing():
    for payload in (None, {}, "rows", 3):
        assert _norm(payload) == []


def test_hidden_listings_never_trend():
    """AVGOP (a converted preferred), BAC-PE (a dash preferred), NASDAQ mutual funds and a
    dead ticker absent from the directory were all picked, but search would never show
    them — so neither may the chips. The funds are IN the directory (the active list
    carries them), so this exercises the fund rule, not liveness."""
    directory = {**DIRECTORY, "JMGRX": "Janus Henderson Enterprise Fund",
                 "HEIFX": "Hennessy Equity and Income Investor"}
    rows = [_row("AVGOP"), _row("BAC-PE"), _row("JMGRX", "fund"), _row("HEIFX"), _row("TWTR"),
            _row("AAPL")]
    assert [i.symbol for i in _norm(rows, directory=directory)] == ["AAPL"]


@pytest.mark.parametrize("ticker", ["SEND ETH TO 0XABC", "BRK.B", "GC=F", "^GSPC", "-AAPL",
                                    "A" * 11, "ＡＡＰＬ"])
def test_a_watchlist_string_of_the_wrong_shape_never_becomes_a_chip(ticker):
    """watchlist_items.ticker is client-writable. With a cold directory (every deploy) the
    liveness rule cannot run — the shape rule still must."""
    assert _norm([_row(ticker, key="adders", n=5)], key="adders", directory=None) == []
    assert _norm([_row(ticker, key="adders", n=5)], key="adders") == []


def test_a_same_issuer_note_twin_never_trends_even_without_a_row_name():
    """The counters carry no name, and the same-issuer rule compares names — so the
    directory's name must be used, or AGNCL (a note beside AGNC) trends."""
    directory = {**DIRECTORY, "AGNC": "AGNC Investment Corp.", "AGNCL": "AGNC Investment Corp."}
    assert [i.symbol for i in _norm([_row("AGNCL"), _row("AGNC")], directory=directory)] == ["AGNC"]


def test_without_a_directory_only_the_grammar_rules_apply():
    rows = [_row("AVGOP"), _row("TWTR"), _row("AAPL")]
    assert [i.symbol for i in _norm(rows, directory=None)] == ["TWTR", "AAPL"]


def test_crypto_is_validated_against_the_coin_map_and_named_from_it():
    rows = [_row("BTC", "crypto"), _row("ETHUSD", "crypto"), _row("NOTACOIN", "crypto"),
            _row("BTC", "etf", name="Grayscale Bitcoin Mini Trust ETF")]
    out = _norm(rows)
    assert [(i.symbol, i.type) for i in out] == [("BTC", "crypto"), ("ETH", "crypto"),
                                                ("BTC", "etf")]
    assert out[0].name == "Bitcoin" and out[1].name == "Ethereum"


def test_blocked_symbols_are_removed_everywhere():
    assert _norm([_row("AAPL"), _row("NVDA")], blocked=["aapl"]) == [
        _it("NVDA", name="NVIDIA Corporation")]
    assert assemble_sections([], [], [_it("AAPL"), _it("MSFT")], stocks_only=False,
                             blocked=["AAPL"])[0].items == [_it("MSFT")]


def test_a_row_name_is_never_trusted():
    """watchlist_items.company_name is client-writable (POST /tracking/holdings): the latest
    adder could rename a chip for every user, or give it a debt-shaped name that hides it.
    The directory names it; with no directory entry, the curated file; else nothing."""
    rows = [_row("AAPL", key="adders", n=9, name="Apple - SELL NOW, see evil.example"),
            _row("NVDA", key="adders", n=9, name="NVIDIA Corp 5% Notes due 2030"),
            _row("ZZZZ", key="adders", n=9, name="Injected")]
    out = normalize_rows(rows, count_key="adders", directory={**DIRECTORY, "ZZZZ": ""},
                         curated_names={"ZZZZ": "Curated Z"})
    assert [(i.symbol, i.name) for i in out] == [
        ("AAPL", "Apple Inc."), ("NVDA", "NVIDIA Corporation"), ("ZZZZ", "Curated Z")]
    cold = normalize_rows(rows[:1], count_key="adders", directory=None, curated_names={})
    assert cold[0].name == "", "no directory: the chip shows its symbol, never the row's text"


def test_duplicates_keep_the_first_and_the_order():
    rows = [_row("NVDA", n=9), _row("aapl", n=7), _row("NVDA", n=4)]
    assert [i.symbol for i in _norm(rows)] == ["NVDA", "AAPL"]


def test_one_security_is_one_chip_but_a_coin_and_its_etf_are_two():
    rows = [_row("SPY", "etf", n=9), _row("SPY", "stock", n=4),
            _row("BTC", "crypto", n=5), _row("BTC", "etf", n=4)]
    assert [(i.symbol, i.type) for i in _norm(rows)] == [
        ("SPY", "etf"), ("BTC", "crypto"), ("BTC", "etf")]


# ── assemble_sections ─────────────────────────────────────────────────────────

LIVE8 = [_it(s) for s in ("NVDA", "TSLA", "PLTR", "AMD", "SOFI", "AAPL", "MSFT", "AMZN", "META")]


def test_both_live_lists_are_capped_and_no_popular_is_added():
    out = assemble_sections(LIVE8, LIVE8[:5], CURATED.all_items, stocks_only=False)
    assert [s.kind for s in out] == ["trending_searches", "most_added"]
    assert len(out[0].items) == 8 and len(out[1].items) == 5


@pytest.mark.parametrize("searched,added,kinds", [
    ([], [], ["popular"]),
    (LIVE8, [], ["trending_searches", "popular"]),
    ([], LIVE8, ["popular", "most_added"]),
    (LIVE8[:4], LIVE8[:4], ["popular"]),          # four is too few: one popular, not two
])
def test_fallback_is_one_popular_section_at_the_first_missing_slot(searched, added, kinds):
    out = assemble_sections(searched, added, CURATED.all_items, stocks_only=False)
    assert [s.kind for s in out] == kinds


def test_popular_never_repeats_a_symbol_a_live_section_shows():
    """A live BTC ETF beside Popular's BTC coin would be two chips both reading "BTC"."""
    live = [_it("BTC", "etf"), _it("NVDA"), _it("TSLA"), _it("AMD"), _it("PLTR")]
    out = assemble_sections(live, [], CURATED.all_items, stocks_only=False)
    assert out[1].kind == "popular"
    assert "BTC" not in [i.symbol for i in out[1].items]


def test_popular_excludes_what_a_live_section_already_shows():
    out = assemble_sections(LIVE8[:5], [], CURATED.all_items, stocks_only=False)
    live = {i.symbol for i in out[0].items}
    popular = {i.symbol for i in out[1].items}
    assert out[1].kind == "popular" and not (live & popular) and popular


def test_stocks_only_filters_before_the_threshold():
    """Five live items of which two are not stocks is too few for the company picker, even
    though the all-assets list is live."""
    mixed = [_it("NVDA"), _it("SPY", "etf"), _it("TSLA"), _it("BTC", "crypto"), _it("AMD")]
    assert [s.kind for s in assemble_sections(mixed, [], CURATED.all_items,
                                              stocks_only=False)][0] == "trending_searches"
    out = assemble_sections(mixed, [], CURATED.stock_items, stocks_only=True)
    assert [s.kind for s in out] == ["popular"]
    assert all(i.type == "stock" for s in out for i in s.items)


def test_an_empty_curated_list_leaves_no_empty_section():
    assert assemble_sections([], [], [], stocks_only=False) == []


def test_build_response_carries_no_count_and_both_families():
    resp = build_response(LIVE8, [], CURATED, datetime(2026, 9, 26, 14, tzinfo=timezone.utc))
    body = resp.model_dump()
    assert body["window_days"] == 7 and body["computed_at"].startswith("2026-09-26T14:00")
    assert {s["kind"] for s in body["sections"]} == {"trending_searches", "popular"}
    assert body["stock_sections"][0]["kind"] == "trending_searches"
    flat = json.dumps(body)
    assert "picks" not in flat and "adders" not in flat and "count" not in flat
    assert set(body["sections"][0]["items"][0]) == {"symbol", "name", "type"}


# ── The curated file ──────────────────────────────────────────────────────────

def test_the_shipped_curated_file_is_valid():
    cur = load_curated()
    assert len(cur.all_items) >= 8 and len(cur.stock_items) >= 8
    assert all(i.type == "stock" for i in cur.stock_items)
    syms = [i.symbol for i in cur.all_items + cur.stock_items]
    assert not any("." in s for s in syms), "FMP spells BRK-B, never BRK.B"
    for i in cur.all_items:
        if i.type == "crypto":
            from app.services.crypto_names import CRYPTO_NAMES
            assert i.symbol in CRYPTO_NAMES


def test_a_broken_curated_file_degrades_to_empty(tmp_path, caplog):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with caplog.at_level(logging.ERROR, logger=svc.__name__):
        cur = load_curated(bad)
    assert cur.all_items == [] and cur.stock_items == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)


# ── The service: cache, dedup, degradation ────────────────────────────────────

class _FakeRPC:
    def __init__(self, calls: List[str], result: Any):
        self._calls, self._result = calls, result

    def execute(self):
        if isinstance(self._result, BaseException):
            raise self._result
        return SimpleNamespace(data=self._result)


class _FakeClient:
    def __init__(self, searched: Any, added: Any):
        self.calls: List[str] = []
        self.results = {"get_search_trending": searched, "get_most_added_tickers": added}
        self.params: Dict[str, Dict] = {}

    def rpc(self, name, params):
        self.calls.append(name)
        self.params[name] = params
        return _FakeRPC(self.calls, self.results[name])


def _service(monkeypatch, searched, added, directory=DIRECTORY) -> tuple:
    client = _FakeClient(searched, added)
    monkeypatch.setattr(svc, "get_supabase", lambda: client)
    monkeypatch.setattr(svc.stock_search_service, "current_directory",
                        lambda query_upper="": directory)
    return SearchTrendingService(curated=CURATED), client


SEARCHED_ROWS = [_row(s, n=9 - i) for i, s in enumerate(["NVDA", "TSLA", "PLTR", "AMD", "SOFI", "AAPL"])]
ADDED_ROWS = [_row(s, key="adders", n=4) for s in ["AVGO", "HOOD", "NVDA", "MSFT", "META"]]


@pytest.mark.asyncio
async def test_live_lists_and_the_rpc_parameters(monkeypatch):
    service, client = _service(monkeypatch, SEARCHED_ROWS, ADDED_ROWS)
    resp = await service.get_trending()
    assert [s.kind for s in resp.sections] == ["trending_searches", "most_added"]
    assert [i.symbol for i in resp.sections[0].items][:3] == ["NVDA", "TSLA", "PLTR"]
    p = client.params["get_search_trending"]
    assert p["p_min_picks"] == 3 and p["p_limit"] == 40
    a = client.params["get_most_added_tickers"]
    assert a["p_min_users"] == 3 and a["p_min_account_age_hours"] == 24
    assert datetime.fromisoformat(a["p_since"]).utcoffset() is not None, "an ET-aware instant"


@pytest.mark.asyncio
async def test_a_fresh_answer_is_served_from_memory(monkeypatch):
    service, client = _service(monkeypatch, SEARCHED_ROWS, ADDED_ROWS)
    first = await service.get_trending()
    second = await service.get_trending()
    assert first is second and len(client.calls) == 2


@pytest.mark.asyncio
async def test_concurrent_first_callers_share_one_computation(monkeypatch):
    service, client = _service(monkeypatch, SEARCHED_ROWS, ADDED_ROWS)
    results = await asyncio.gather(*(service.get_trending() for _ in range(10)))
    assert len(client.calls) == 2
    assert all(r is results[0] for r in results)


@pytest.mark.asyncio
async def test_one_failed_list_degrades_alone(monkeypatch, caplog):
    service, client = _service(monkeypatch, RuntimeError("db down"), ADDED_ROWS)
    with caplog.at_level(logging.WARNING, logger=svc.__name__):
        resp = await service.get_trending()
    assert [s.kind for s in resp.sections] == ["popular", "most_added"]
    assert service._cache[1] == svc._DEGRADED_TTL_SECONDS, "a degraded answer is cached briefly"


@pytest.mark.asyncio
async def test_a_failed_list_serves_its_last_good_copy(monkeypatch):
    service, client = _service(monkeypatch, SEARCHED_ROWS, ADDED_ROWS)
    await service.get_trending()
    client.results["get_search_trending"] = RuntimeError("db down")
    service._cache = None
    resp = await service.get_trending()
    assert resp.sections[0].kind == "trending_searches", "stale beats popular within a day"
    # ... but not after a day.
    ts, items = service._last_good["get_search_trending"]
    service._last_good["get_search_trending"] = (ts - svc._STALE_MAX_SECONDS - 1, items)
    service._cache = None
    resp = await service.get_trending()
    assert resp.sections[0].kind == "popular"


@pytest.mark.asyncio
async def test_a_cold_directory_is_degraded_and_never_becomes_last_good(monkeypatch):
    """After every deploy the directory is cold: the dead-listing and same-issuer rules
    cannot run, so that answer is cached for a minute, not an hour, and a filtered copy
    from before beats it."""
    service, client = _service(monkeypatch, SEARCHED_ROWS, ADDED_ROWS, directory=None)
    resp = await service.get_trending()
    assert service._cache[1] == svc._DEGRADED_TTL_SECONDS
    assert service._last_good == {}, "an unfiltered pass must not be remembered"
    assert resp.sections, "still answers (grammar rules only)"

    # A filtered copy exists → it wins over a later cold pass.
    monkeypatch.setattr(svc.stock_search_service, "current_directory",
                        lambda query_upper="": DIRECTORY)
    service._cache = None
    warm = await service.get_trending()
    monkeypatch.setattr(svc.stock_search_service, "current_directory",
                        lambda query_upper="": None)
    client.results["get_search_trending"] = [_row("TWTR", n=50)] + SEARCHED_ROWS
    service._cache = None
    cold = await service.get_trending()
    assert cold.sections[0].items == warm.sections[0].items
    assert "TWTR" not in [i.symbol for s in cold.sections for i in s.items]


@pytest.mark.asyncio
async def test_a_missing_function_logs_error_once(monkeypatch, caplog):
    missing = RuntimeError("{'code': 'PGRST202', 'message': 'Could not find the function'}")
    service, client = _service(monkeypatch, missing, missing)
    with caplog.at_level(logging.WARNING, logger=svc.__name__):
        for _ in range(3):
            service._cache = None
            resp = await service.get_trending()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 2, "one ERROR per missing function, not per request"
    assert [s.kind for s in resp.sections] == ["popular"]


@pytest.mark.asyncio
async def test_a_total_failure_serves_the_curated_lists_and_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("no client")

    monkeypatch.setattr(svc, "get_supabase", boom)
    service = SearchTrendingService(curated=CURATED)
    resp = await service.get_trending()
    assert isinstance(resp, SearchTrendingResponse)
    assert [s.kind for s in resp.sections] == ["popular"]
    assert [s.kind for s in resp.stock_sections] == ["popular"]
    assert service._cache[1] == svc._DEGRADED_TTL_SECONDS
    assert service._inflight is None


@pytest.mark.asyncio
async def test_a_cancelled_computation_does_not_strand_its_joiners(monkeypatch):
    gate = asyncio.Event()

    async def slow_compute(today):
        await gate.wait()
        raise AssertionError("unreachable")

    service = SearchTrendingService(curated=CURATED)
    monkeypatch.setattr(service, "_compute", slow_compute)
    leader = asyncio.create_task(service.get_trending())
    await asyncio.sleep(0)
    joiner = asyncio.create_task(service.get_trending())
    await asyncio.sleep(0)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(joiner, 1)
    assert service._inflight is None


def test_the_et_day_rolls_at_et_midnight(monkeypatch):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 27, 3, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(svc, "datetime", _Clock)
    assert svc._today_et() == date(2026, 9, 26), "03:30 UTC is still the 26th in New York"


def test_sweep_deletes_before_the_retention_cutoff(monkeypatch):
    seen = {}

    class _Q:
        def delete(self):
            return self

        def lt(self, col, value):
            seen["lt"] = (col, value)
            return self

        def execute(self):
            return SimpleNamespace(data=[{}, {}])

    monkeypatch.setattr(svc, "get_supabase", lambda: SimpleNamespace(table=lambda name: _Q()))
    assert SearchTrendingService(curated=CURATED).sweep_expired(date(2026, 9, 26)) == 2
    assert seen["lt"] == ("day", "2026-09-12")


def test_sweep_failure_is_logged_not_raised(monkeypatch, caplog):
    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr(svc, "get_supabase", boom)
    with caplog.at_level(logging.WARNING, logger=svc.__name__):
        assert SearchTrendingService(curated=CURATED).sweep_expired() == 0
    assert caplog.records


# ── Impersonality ─────────────────────────────────────────────────────────────

_BANNED_NAMES = {"user_id", "user", "current_user", "get_current_user", "get_current_user_id",
                 "identity_key", "x_guest_id", "account"}


def test_the_read_module_never_touches_a_caller_identity():
    """The lists are the same for everyone. A user identifier appearing in this module is
    how a per-user list — or a log line linking a person to a ticker — would start."""
    src = Path(svc.__file__).read_text()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name for a in node.names)
    assert not (names & _BANNED_NAMES), names & _BANNED_NAMES
