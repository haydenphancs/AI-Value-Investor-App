"""No company-screener caller may lose a company to FMP's server-side cap filter (2026-10-08).

`marketCapMoreThan` is applied to a server-side cap of FMP's own, and every row where that
cap is null VANISHES — at any floor, 0 included — whatever `marketCap` the row itself
carries. Probed 2026-10-08 on the US sweep: 261 rows of $50M+ by their own cap missing from
the >$50M answer and 666 from the `> 0` one, among them VMRK (Vivmark Residential, the EQR +
AVB merger, $22.5B), VYLR ($48.6B), SKYD (Skydance, $10.1B), LYNX and ADIG.
`scripts/build_benchmark_universe.py` was fixed first
(`test_benchmark_universe_builder_floor_2026_10_09.py`); these are the other four callers:

  * `price_service` universe — Home's movers / Heavy Traffic, the sector and industry
    averages, the widget and every batch quote read it. Companies are now asked for at every
    cap and floored here; ETFs keep the server floor (bandwidth; ETFs are in no ranking);
  * `theme_rotation.load_universe` — an outsider missing from it can never join a theme;
  * Trillion-Dollar Club discovery — the ≥ $900B screen;
  * `scripts/hydrate_hedge_fund_flow.py` — sent `marketCapMoreThan=0`, which hides them too.

Every test drives the REAL `FMPClient.get_company_screener` (kwargs → params) against a fake
`_make_request` that behaves like the screener. Mutation-tested by hand: putting the filter
back at any one call site turns its test red.
"""
from __future__ import annotations

import copy
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import pytest

import app.services.price_service as ps
import scripts.hydrate_hedge_fund_flow as hf
from app.integrations.fmp import FMPClient, FMPUnavailableException
from app.services.price_service import PriceService
from app.services.theme_rotation import sources as theme_sources
from app.services.theme_rotation.sources import CallCounter, ThemeSourceError, load_universe
from app.services.trillion_club import jobs as J

_PAGE_CEILING = 10_000          # FMP serves at most this many rows a call, whatever `limit` says


def _row(sym: str, cap: Any, *, etf: bool = False, exchange: str = "NYSE",
         name: Optional[str] = None, **over: Any) -> Dict[str, Any]:
    row = {"symbol": sym, "companyName": name or f"{sym} Corp", "marketCap": cap,
           "price": 50.0, "volume": 1_000_000, "avgVolume": 1_000_000,
           "exchange": exchange, "exchangeShortName": exchange, "country": "US",
           "sector": "Real Estate", "industry": "REIT - Residential",
           "isEtf": etf, "isFund": False, "isActivelyTrading": True}
    row.update(over)
    return row


def _alpha(i: int) -> str:
    """A distinct 5-letter symbol per index ("ZAAAA", "ZAAAB", …)."""
    out = ""
    for _ in range(4):
        i, r = divmod(i, 26)
        out = chr(ord("A") + r) + out
    return "Z" + out


def _flag(params: Dict[str, Any], key: str) -> Optional[bool]:
    value = params.get(key)
    return None if value is None else str(value).lower() == "true"


class _ScreenerLikeFMP:
    """`_make_request` for the screener as probed on 2026-10-08: a row whose SERVER-SIDE cap
    is null (`hidden`) vanishes whenever `marketCapMoreThan` is sent, at any floor; the rest
    are filtered by their own cap. `isEtf` / `isFund` / `exchange` filter as FMP does, and a
    page holds at most 10,000 rows. Also answers `profile` (recorded, empty) and
    `market-capitalization-batch` (from `batch_caps`)."""

    def __init__(self, rows: Iterable[Any], hidden: Iterable[str] = (),
                 batch_caps: Optional[Dict[str, float]] = None):
        self.rows = list(rows)
        self.hidden = set(hidden)
        self.batch_caps = batch_caps or {}
        self.calls: List[tuple] = []

    async def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None, **_: Any):
        p = dict(params or {})
        self.calls.append((endpoint, p))
        if endpoint == "profile":
            return []
        if endpoint == "market-capitalization-batch":
            return [{"symbol": s, "date": "2026-10-08", "marketCap": self.batch_caps[s]}
                    for s in p["symbols"].split(",") if s in self.batch_caps]
        assert endpoint == "company-screener", endpoint
        out = []
        for r in self.rows:
            if not isinstance(r, dict):
                out.append(r)                       # junk rides along, as it would
                continue
            if "marketCapMoreThan" in p:
                cap = r.get("marketCap")
                if r["symbol"] in self.hidden:
                    continue
                if isinstance(cap, bool) or not isinstance(cap, (int, float)) \
                        or not cap > float(p["marketCapMoreThan"]):
                    continue
            for key, field in (("isEtf", "isEtf"), ("isFund", "isFund"),
                               ("isActivelyTrading", "isActivelyTrading")):
                want = _flag(p, key)
                if want is not None and bool(r.get(field)) != want:
                    break
            else:
                exchanges = p.get("exchange")
                if exchanges and r.get("exchangeShortName") not in str(exchanges).split(","):
                    continue
                out.append(r)
        limit = min(int(p.get("limit", _PAGE_CEILING)), _PAGE_CEILING)
        page = int(p.get("page", 0))
        return copy.deepcopy(out[page * limit:(page + 1) * limit])

    def screener_params(self) -> List[Dict[str, Any]]:
        return [p for e, p in self.calls if e == "company-screener"]

    def profile_symbols(self) -> List[str]:
        return [p["symbol"] for e, p in self.calls if e == "profile"]


def _client(fake: _ScreenerLikeFMP) -> FMPClient:
    client = FMPClient()
    client._make_request = fake._make_request          # instance attribute: this client only
    return client


def _no_cap_filter_sent(fake: _ScreenerLikeFMP, *, allow_etf_floor: bool = False) -> None:
    for p in fake.screener_params():
        if allow_etf_floor and _flag(p, "isEtf") is True:
            continue
        assert "marketCapMoreThan" not in p, p


VMRK = _row("VMRK", 22_489_141_120, name="Vivmark Residential")            # server cap null
VYLR = _row("VYLR", 48_565_196_840, name="Vylor Inc.", industry="Agricultural Inputs")
SKYD = _row("SKYD", 10_119_376_000, name="Skydance Corporation", industry="Broadcasting")
HIDDEN = {"VMRK", "VYLR", "SKYD", "HIDETF", "NEWMEGA"}


# ══ 1. price_service: the screener universe ═══════════════════════════════════════════════


@pytest.fixture
def price_world(monkeypatch):
    rows = [
        VMRK, VYLR, SKYD,
        _row("AAPL", 3.0e12, exchange="NASDAQ"),
        _row("FLOOR", ps._UNIVERSE_MIN_MARKET_CAP),                          # exactly the floor
        _row("MICRO", 10_000_000),                                           # a shell
        _row("NOCAP", None), _row("NANCAP", float("nan")), _row("STRCAP", "lots"),
        _row("BOOLCAP", True), _row("NEGCAP", -5e9),
        _row("SPY", 6.0e11, etf=True, exchange="AMEX"),
        _row("TINYETF", 5_000_000, etf=True, exchange="AMEX"),
        _row("HIDETF", 6.38e9, etf=True, exchange="AMEX"),                  # server cap null
        _row("VODL", 3e10, exchange="LSE"),                                  # not a US venue
        _row("GOLDX", 1.2e9, exchange="NASDAQ", isFund=True),
        "junk", None,
    ]
    fake = _ScreenerLikeFMP(rows, hidden=HIDDEN)
    monkeypatch.setattr(ps, "get_fmp_client", lambda: _client(fake))
    monkeypatch.setattr(PriceService, "_select_closes", staticmethod(lambda symbols: []))
    ps._cache.clear()
    yield fake
    ps._cache.clear()


@pytest.mark.asyncio
async def test_the_price_universe_keeps_companies_fmp_hides_behind_its_cap_filter(price_world):
    """Mutation: send `market_cap_more_than` on the company slice again → VMRK, VYLR and SKYD
    are gone, and they could never be a Home mover again."""
    rows = await PriceService()._fetch_universe_pages()
    syms = {r["symbol"] for r in rows}
    assert {"VMRK", "VYLR", "SKYD", "AAPL", "FLOOR", "SPY"} <= syms
    _no_cap_filter_sent(price_world, allow_etf_floor=True)


@pytest.mark.asyncio
async def test_the_floor_is_read_off_each_rows_own_cap(price_world):
    rows = await PriceService()._fetch_universe_pages()
    syms = [r["symbol"] for r in rows]
    for dropped in ("MICRO", "NOCAP", "NANCAP", "STRCAP", "BOOLCAP", "NEGCAP", "TINYETF"):
        assert dropped not in syms, f"{dropped} passed a $50M floor"
    assert "FLOOR" in syms, "a cap exactly at the floor is kept"
    assert "VODL" not in syms and "GOLDX" not in syms, "the exchange / fund filters stay"
    assert len(syms) == len(set(syms)), "the two slices must not overlap"


@pytest.mark.asyncio
async def test_the_sweep_is_two_complete_slices_never_a_paged_walk(price_world):
    await PriceService()._fetch_universe_pages()
    params = price_world.screener_params()
    assert len(params) == 2 and not any("page" in p for p in params), params
    assert sorted(_flag(p, "isEtf") for p in params) == [False, True]
    for p in params:
        assert p["isFund"] == "false" and p["isActivelyTrading"] == "true"
        assert p["exchange"] == "NASDAQ,NYSE,AMEX" and int(p["limit"]) == _PAGE_CEILING
    etf = next(p for p in params if _flag(p, "isEtf"))
    assert int(etf["marketCapMoreThan"]) == ps._UNIVERSE_MIN_MARKET_CAP, (
        "the ETF slice keeps the server floor — all-cap ETFs are 4,107 rows of bandwidth"
    )


@pytest.mark.asyncio
async def test_a_hidden_company_is_priced_from_the_universe_not_the_profile_fallback(price_world):
    quotes = await PriceService().get_quotes(["VMRK", "AAPL"])
    assert quotes["VMRK"]["price"] == 50.0 and quotes["VMRK"]["marketCap"] == VMRK["marketCap"]
    assert price_world.profile_symbols() == [], (
        "VMRK fell through to one /stable/profile call per request — it was missing from "
        "the universe"
    )


@pytest.mark.asyncio
async def test_a_hidden_company_reaches_the_movers_universe(price_world, monkeypatch):
    """The surface that made this matter: Home's movers and Heavy Traffic rank this map."""
    from app.services import market_movers_service as mm

    async def _closes(self):
        return {}

    monkeypatch.setattr(mm.MarketMoversService, "_all_closes", _closes)
    mm._cache.clear()
    try:
        universe = await mm.MarketMoversService().get_universe()
    finally:
        mm._cache.clear()
    assert {"VMRK", "VYLR", "SKYD"} <= set(universe)
    assert universe["SKYD"]["industry"] == "Broadcasting"


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_slice", [False, True])
async def test_an_empty_slice_fails_the_whole_sweep(monkeypatch, caplog, empty_slice):
    """Half a universe would rank movers on half the market and fan every symbol of the
    other half out to /stable/profile — so it is a failure, never an answer."""
    rows = [_row("AAPL", 3e12, exchange="NASDAQ")]
    if not empty_slice:
        rows.append(_row("SPY", 6e11, etf=True, exchange="AMEX"))
    fake = _ScreenerLikeFMP(rows)
    monkeypatch.setattr(ps, "get_fmp_client", lambda: _client(fake))
    if not empty_slice:
        out = await PriceService()._fetch_universe_pages()
        assert {r["symbol"] for r in out} == {"AAPL", "SPY"}
        return
    with caplog.at_level(logging.ERROR, logger="app.services.price_service"):
        with pytest.raises(FMPUnavailableException, match="etfs"):
            await PriceService()._fetch_universe_pages()
    assert any("'etfs'" in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)


@pytest.mark.asyncio
async def test_a_failed_slice_fails_the_whole_sweep(monkeypatch):
    class _HalfDown:
        async def get_company_screener(self, **kw):
            if kw.get("is_etf") is True:
                raise FMPUnavailableException("503 on the ETF slice")
            return [_row("AAPL", 3e12, exchange="NASDAQ")]

    monkeypatch.setattr(ps, "get_fmp_client", lambda: _HalfDown())
    with pytest.raises(FMPUnavailableException, match="ETF slice"):
        await PriceService()._fetch_universe_pages()


@pytest.mark.asyncio
async def test_a_full_slice_page_is_an_error_but_still_served(monkeypatch, caplog):
    """A full page can only cut a slice's tail (those quotes take the profile fallback);
    blanking every equity quote over it would be worse — but it must be loud."""
    companies = [_row(f"C{i:05d}", 1e9) for i in range(_PAGE_CEILING + 5)]
    fake = _ScreenerLikeFMP(companies + [_row("SPY", 6e11, etf=True, exchange="AMEX")])
    monkeypatch.setattr(ps, "get_fmp_client", lambda: _client(fake))
    with caplog.at_level(logging.ERROR, logger="app.services.price_service"):
        rows = await PriceService()._fetch_universe_pages()
    assert len(rows) == _PAGE_CEILING + 1
    assert any("'companies' filled its" in r.getMessage()
               for r in caplog.records if r.levelno == logging.ERROR)


# ══ 2. theme rotation ══════════════════════════════════════════════════════════════════════


def _theme_rows(n: int, cap: float = 1e9) -> List[Dict[str, Any]]:
    return [_row(f"T{i:05d}", cap) for i in range(n)]


@pytest.mark.asyncio
async def test_a_theme_outsider_fmp_hides_behind_its_cap_filter_is_in_the_universe():
    """Mutation: send `market_cap_more_than=SCREENER_MIN_MARKET_CAP` again → VYLR, SKYD and
    VMRK are missing, so `_candidate_pool` could never pick them for a theme."""
    fake = _ScreenerLikeFMP(_theme_rows(theme_sources.MIN_UNIVERSE_ROWS) + [VYLR, SKYD, VMRK],
                            hidden=HIDDEN)
    universe = await load_universe(_client(fake), CallCounter())
    assert {"VYLR", "SKYD", "VMRK"} <= set(universe)
    _no_cap_filter_sent(fake)
    assert all(_flag(p, "isEtf") is False and _flag(p, "isFund") is False
               for p in fake.screener_params())


@pytest.mark.asyncio
async def test_the_theme_floor_is_the_rows_own_cap_and_sub_floor_rows_do_not_count():
    floor = theme_sources.SCREENER_MIN_MARKET_CAP
    under = [_row("SMALL", floor - 1), _row("NOCAP", None), _row("NANCAP", float("nan")),
             _row("STRCAP", "big"), _row("BOOLCAP", True)]
    fake = _ScreenerLikeFMP(_theme_rows(theme_sources.MIN_UNIVERSE_ROWS - 1) + under
                            + [_row("ATFLOOR", floor)])
    universe = await load_universe(_client(fake), CallCounter())
    assert "ATFLOOR" in universe
    assert not {"SMALL", "NOCAP", "NANCAP", "STRCAP", "BOOLCAP"} & set(universe)
    # 1,999 + ATFLOOR = 2,000 counted; the five under-floor rows are not.
    fake.rows.remove(fake.rows[-1])
    with pytest.raises(ThemeSourceError, match=f"< {theme_sources.MIN_UNIVERSE_ROWS}"):
        await load_universe(_client(fake), CallCounter())


# ══ 3. Trillion-Dollar Club discovery ══════════════════════════════════════════════════════


class _Store:
    def __init__(self):
        self.inserted: List[Dict[str, Any]] = []

    async def insert_discovered(self, rows):
        self.inserted.extend(rows)
        return [r["slug"] for r in rows]


T = 1e12


@pytest.mark.asyncio
async def test_discovery_finds_a_mega_cap_fmp_hides_behind_its_cap_filter():
    """A just-listed mega-cap is exactly the shape with no server-side cap yet. Mutation:
    send `market_cap_more_than=DISCOVERY_MIN_CAP_USD` again → NEWMEGA is never screened."""
    rows = [_row("NVDA", 5.5 * T, exchange="NASDAQ"),
            _row("NEWMEGA", 1.4 * T, name="New Mega Inc."),                 # server cap null
            _row("LOWCO", 0.5 * T), VMRK,
            _row("NANCO", float("nan")), _row("NOCAPCO", None)]
    fake = _ScreenerLikeFMP(rows, hidden=HIDDEN, batch_caps={"NEWMEGA": 1.35 * T})
    store = _Store()
    summary = J._new_summary("weekly", datetime(2026, 10, 9, 12, tzinfo=timezone.utc))
    registry = [{"slug": "nvidia", "cap_symbol": "NVDA"}]
    await J._discovery_stage([], registry, _client(fake), store, summary)

    stage = summary["discovery"]
    assert summary["ok"] is True and stage["ok"] is True, summary["failures"]
    assert stage["candidates"] == ["NEWMEGA"]
    assert [r["cap_symbol"] for r in store.inserted] == ["NEWMEGA"]
    assert store.inserted[0]["published"] is False
    _no_cap_filter_sent(fake)
    (params,) = fake.screener_params()
    assert int(params["limit"]) == J.DISCOVERY_SCREEN_LIMIT == _PAGE_CEILING
    assert params["exchange"] == "NASDAQ,NYSE" and params["isEtf"] == "false"


@pytest.mark.asyncio
async def test_a_full_discovery_page_is_an_error_but_the_stage_still_runs(caplog):
    rows = [_row("NEWMEGA", 1.4 * T)] + [_row(f"S{i:05d}", 1e9) for i in range(_PAGE_CEILING)]
    fake = _ScreenerLikeFMP(rows, hidden=HIDDEN, batch_caps={"NEWMEGA": 1.35 * T})
    store = _Store()
    summary = J._new_summary("weekly", datetime(2026, 10, 9, 12, tzinfo=timezone.utc))
    with caplog.at_level(logging.ERROR, logger="app.services.trillion_club.jobs"):
        await J._discovery_stage([], [], _client(fake), store, summary)
    assert any("filled its" in r.getMessage() for r in caplog.records
               if r.levelno == logging.ERROR)
    assert summary["discovery"]["candidates"] == ["NEWMEGA"]


# ══ 4. scripts/hydrate_hedge_fund_flow.py ══════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_hedge_fund_flow_pool_keeps_rows_fmp_hides_even_at_a_zero_floor():
    """`marketCapMoreThan=0` hid 666 US listings on 2026-10-08. Mutation: send it again →
    VMRK, VYLR and SKYD drop out of the cap-ranked hydration universe."""
    rows = [VMRK, VYLR, SKYD, _row("AAPL", 3e12, exchange="NASDAQ"),
            _row("SPY", 6e11, etf=True, exchange="AMEX")]
    fake = _ScreenerLikeFMP(rows, hidden=HIDDEN)
    caps = await hf._screener_stock_caps(_client(fake))
    assert {"VMRK", "VYLR", "SKYD", "AAPL"} <= set(caps) and "SPY" not in caps
    assert caps["VMRK"] == VMRK["marketCap"]
    _no_cap_filter_sent(fake)
    (params,) = fake.screener_params()
    assert int(params["limit"]) <= _PAGE_CEILING


@pytest.mark.asyncio
async def test_a_garbage_cap_ranks_last_instead_of_scrambling_the_ranking():
    rows = [_row("AAPL", 3e12, exchange="NASDAQ"), _row("NANCO", float("nan")),
            _row("INFCO", float("inf")), _row("STRCO", "12"), _row("BOOLC", True),
            _row("NOCAP", None), VMRK, "junk", None]
    caps = await hf._screener_stock_caps(_client(_ScreenerLikeFMP(rows)))
    for sym in ("NANCO", "INFCO", "STRCO", "BOOLC", "NOCAP"):
        assert caps[sym] == 0.0, sym
    assert all(math.isfinite(v) for v in caps.values())
    ranked = [s for s, _ in sorted(caps.items(), key=lambda kv: kv[1], reverse=True)]
    assert ranked[:2] == ["AAPL", "VMRK"]


@pytest.mark.asyncio
async def test_a_full_hedge_fund_flow_page_is_logged(caplog):
    # Letters only: the script keeps symbols matching `holders_service._TICKER_RE`.
    rows = [_row(_alpha(i), 1e9) for i in range(_PAGE_CEILING + 3)]
    with caplog.at_level(logging.WARNING, logger="hydrate_hedge_fund_flow"):
        caps = await hf._screener_stock_caps(_client(_ScreenerLikeFMP(rows)))
    assert len(caps) == _PAGE_CEILING
    assert any("filled its" in r.getMessage() for r in caplog.records)


# ══ 5. no caller sends the filter on a company sweep any more ══════════════════════════════


def test_only_the_price_etf_slice_still_sends_a_server_cap_filter():
    """Source scan (comments and docstrings stripped): `market_cap_more_than=` /
    `"marketCapMoreThan"` appear in `app/` only in the FMP wrapper and the price universe's
    ETF slice. A new company sweep must floor on the rows' own cap instead."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in sorted(list((root / "app").rglob("*.py")) + list((root / "scripts").rglob("*.py"))):
        rel = path.relative_to(root).as_posix()
        if rel in ("app/integrations/fmp.py", "scripts/fmp_entitlement_probe.py"):
            continue                    # the wrapper itself; the probe tests the endpoint
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            hit = None
            if isinstance(node, ast.keyword) and node.arg == "market_cap_more_than":
                hit = node
            elif isinstance(node, ast.Constant) and node.value in ("marketCapMoreThan",
                                                                    "market_cap_more_than"):
                hit = node
            if hit is not None:
                offenders.append((rel, getattr(hit, "lineno", None) or node.lineno))
    assert offenders == [("app/services/price_service.py", offenders[0][1])], offenders
    etf_slice = ps._UNIVERSE_SLICES[1][1]
    assert etf_slice == {"is_etf": True, "market_cap_more_than": ps._UNIVERSE_MIN_MARKET_CAP}
    assert "market_cap_more_than" not in ps._UNIVERSE_SLICES[0][1]
