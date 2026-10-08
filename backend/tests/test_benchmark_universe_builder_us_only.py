"""The benchmark universe is US-listed operating companies only (owner decision 2026-10-07).

The 2026-06-24 `benchmark_universe.json` was built with no fund / ETF / exchange filter and
a silent `limit=1000`: it carried ~1,275 open-end mutual funds and ETFs, 615 `.TO` listings
(147 of them a second copy of a US ticker), and an 'Asset Management' list cut at exactly
1,000 rows (TROW, BEN, IVZ… missing). A failed industry request was logged and skipped,
so the file could be written with whole industries absent.

Pinned here, against a fake FMP client (hermetic — nothing reaches FMP):
  * the screener is ASKED for US-listed, fund-free, ETF-free, actively-trading rows;
  * every row is re-checked anyway (a filter FMP ignores looks like a clean answer);
  * an industry is paged, never truncated; a full page of repeats fails loudly;
  * any failed request fails the build and writes nothing; a 429 is retried first;
  * a > 10% shrink is refused unless `--allow-shrink` (bounded: 50% bare);
  * the output format is unchanged, round-tripped through the real reader.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import pytest

import scripts.build_benchmark_universe as bu
from app.integrations.fmp import FMPRateLimitException, FMPUnavailableException

FLOOR = 500_000_000


def _row(sym: str, cap: Any = 2e9, sector: str = "Financial Services", **over: Any) -> Dict[str, Any]:
    """A /stable company-screener row (field names as FMP returns them)."""
    row = {
        "symbol": sym, "companyName": f"{sym} Corp", "marketCap": cap, "sector": sector,
        "industry": "Asset Management", "beta": 1.1, "price": 50.0, "volume": 1_000_000,
        "exchange": "NASDAQ Global Select", "exchangeShortName": "NASDAQ", "country": "US",
        "isEtf": False, "isFund": False, "isActivelyTrading": True,
    }
    row.update(over)
    return row


Spec = Union[List[Any], BaseException, Callable[[Dict[str, Any]], Any]]


class _FakeFMP:
    """`_make_request` double: industries from a list, screener rows per industry.

    A list spec is paged by the request's own `limit` / `page`, exactly as the screener
    pages; a callable sees the params; an exception instance is raised.
    """

    def __init__(self, screener: Dict[str, Spec], industries: Any = None):
        self.screener = screener
        self.industries = list(screener) if industries is None else industries
        self.calls: List[tuple] = []
        self.closed = False

    async def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        self.calls.append((endpoint, dict(params or {})))
        if endpoint == "available-industries":
            if isinstance(self.industries, BaseException):
                raise self.industries
            if isinstance(self.industries, list):
                return [{"industry": n} for n in self.industries]
            return self.industries
        assert endpoint == "company-screener", endpoint
        spec = self.screener[params["industry"]]
        if isinstance(spec, BaseException):
            raise spec
        if callable(spec):
            return spec(params)
        limit, page = int(params["limit"]), int(params.get("page", 0))
        return spec[page * limit:(page + 1) * limit]

    async def close(self) -> None:
        self.closed = True

    def screener_calls(self, industry: Optional[str] = None) -> List[Dict[str, Any]]:
        return [p for e, p in self.calls
                if e == "company-screener" and (industry is None or p["industry"] == industry)]


class _Sleep:
    def __init__(self) -> None:
        self.delays: List[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


async def _run(fmp: _FakeFMP, out: Path, **kw: Any) -> int:
    kw.setdefault("sleep", _Sleep())
    return await bu.main(FLOOR, output=out, fmp=fmp, **kw)


def _written(out: Path) -> Dict[str, Any]:
    return json.loads(out.read_text(encoding="utf-8"))


def _tickers(out: Path) -> List[str]:
    return sorted(t for e in _written(out)["industries"] for t in e["tickers"])


def _previous_file(out: Path, n: int) -> str:
    """A previous universe holding `n` tickers; returns its exact text."""
    tickers = [f"P{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(n)]
    text = json.dumps({
        "generated_at": "2026-06-24T00:17:55+00:00", "source": "old", "market_cap_floor": FLOOR,
        "industry_count": 1, "ticker_count": n,
        "industries": [{"industry": "Asset Management", "sector": "Financial Services",
                        "tickers": tickers, "market_caps": {t: 1e9 for t in tickers}}],
    }, indent=2)
    out.write_text(text, encoding="utf-8")
    return text


# ── what the screener is asked for ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_screener_is_asked_for_us_listed_operating_companies(tmp_path):
    fmp = _FakeFMP({"Asset Management": [_row("TROW")]})
    assert await _run(fmp, tmp_path / "u.json") == bu.EXIT_OK
    (params,) = fmp.screener_calls()
    assert params["industry"] == "Asset Management"
    assert params["isEtf"] == "false" and params["isFund"] == "false"
    assert params["isActivelyTrading"] == "true"
    assert set(params["exchange"].split(",")) == {"NYSE", "NASDAQ", "AMEX"}
    assert params["marketCapMoreThan"] == str(FLOOR)
    # Domicile is NOT the filter: a US-listed foreign issuer (TSM, ASML) is a US listing.
    assert "country" not in params
    assert "page" not in params  # page 0 is the default, as `get_company_screener` sends it


# ── the defensive row checks ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_funds_and_etfs_are_dropped_even_when_the_server_filter_is_ignored(tmp_path, caplog):
    rows = [
        _row("TROW"),
        _row("GOLDX", isFund=True),              # open-end mutual fund
        _row("SPY", isEtf=True),
        _row("VFIAX", isFund="true"),            # a string flag still counts
        _row("QQQ", isEtf="TRUE"),
        _row("OLD", isActivelyTrading=False),
    ]
    out = tmp_path / "u.json"
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        assert await _run(_FakeFMP({"Asset Management": rows}), out) == bu.EXIT_OK
    assert _tickers(out) == ["TROW"]
    text = caplog.text
    assert "fund" in text and "etf" in text and "not honoured" in text, text


@pytest.mark.asyncio
async def test_foreign_listings_are_dropped_and_us_share_classes_kept(tmp_path):
    rows = [
        _row("NTR.TO", exchangeShortName="TSX", exchange="Toronto Stock Exchange"),
        _row("SHOP.TO"),                         # dotted suffix alone is enough
        _row("VOD.L"),
        _row("BRK.B"),                           # FMP spells US classes with a dash
        _row("BRK-A", exchangeShortName="NYSE"),
        _row("BRK-B", exchangeShortName="NYSE"),
        _row("BF-B", exchangeShortName="NYSE"),
        _row("MKC-V", exchangeShortName="NYSE"),
        _row("NTR", exchangeShortName="NYSE"),
    ]
    out = tmp_path / "u.json"
    assert await _run(_FakeFMP({"Agricultural Inputs": rows}), out) == bu.EXIT_OK
    assert _tickers(out) == ["BF-B", "BRK-A", "BRK-B", "MKC-V", "NTR"]


@pytest.mark.parametrize("sym", ["SHOP.TO", "VOD.L", "RY.V", "BRK.B", "7203.T"])
def test_a_dotted_symbol_is_a_foreign_listing(sym):
    """Named `foreign_suffix`, not just any malformed symbol: that reason is logged at
    WARNING as a server-side filter not honoured (the symbol-shape check alone would
    drop it as an INFO-level preferred/unit)."""
    assert bu._drop_reason(_row(sym), FLOOR) == "foreign_suffix"
    assert "foreign_suffix" in bu._UNEXPECTED_DROP_REASONS
    assert "not_common_share" not in bu._UNEXPECTED_DROP_REASONS


@pytest.mark.asyncio
async def test_foreign_rows_are_logged_at_warning(tmp_path, caplog):
    out = tmp_path / "u.json"
    rows = [_row("NTR"), _row("NTR.TO"), _row("EP-PC")]
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await _run(_FakeFMP({"Agricultural Inputs": rows}), out) == bu.EXIT_OK
    by_reason = {r.getMessage().split(" row(s)")[0].rsplit(" ", 1)[-1]: r.levelno
                 for r in caplog.records if " row(s) of " in r.getMessage()}
    assert by_reason == {"foreign_suffix": logging.WARNING, "not_common_share": logging.INFO}


def _drop_log_levels(caplog) -> dict:
    return {r.getMessage().split(" row(s)")[0].rsplit(" ", 1)[-1]: r.levelno
            for r in caplog.records if " row(s) of " in r.getMessage()}


@pytest.mark.asyncio
async def test_a_few_rows_just_under_the_floor_are_drift_not_an_alarm(tmp_path, caplog):
    """The June file held 13 rows at $364M-$499M under a $500M floor: FMP filters on one
    cap and reports another. Dropped, but not reported as an ignored filter."""
    rows = [_row(f"K{chr(65 + i // 26)}{chr(65 + i % 26)}") for i in range(30)]
    rows += [_row("IHRT", cap=466.9e6), _row("ARQQ", cap=364.1e6)]
    out = tmp_path / "u.json"
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await _run(_FakeFMP({"Broadcasting": rows}), out) == bu.EXIT_OK
    assert "IHRT" not in _tickers(out) and len(_tickers(out)) == 30
    assert _drop_log_levels(caplog) == {"below_floor": logging.INFO}
    assert "not honoured" not in caplog.text


@pytest.mark.asyncio
async def test_mass_sub_floor_rows_mean_the_floor_filter_was_ignored(tmp_path, caplog):
    rows = [_row("BIG", cap=9e9)] + [_row(f"M{chr(65 + i)}", cap=5e6) for i in range(5)]
    out = tmp_path / "u.json"
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await _run(_FakeFMP({"Shell Companies": rows}), out) == bu.EXIT_OK
    assert _tickers(out) == ["BIG"]
    assert _drop_log_levels(caplog) == {"below_floor": logging.WARNING}
    assert "not honoured" in caplog.text


@pytest.mark.parametrize("short,long_form,kept", [
    ("NASDAQ", "NASDAQ Global Select", True),
    ("NYSE", "New York Stock Exchange", True),
    ("AMEX", "NYSE American", True),
    ("", "NASDAQ Global Market", True),          # long form only
    ("", "New York Stock Exchange", True),
    ("", "", True),                               # no exchange: the server filter gated it
    ("TSX", "Toronto Stock Exchange", False),
    ("LSE", "London Stock Exchange", False),
    ("OTC", "Other OTC", False),
    ("", "Toronto Stock Exchange", False),
    ("NASDAQX", "", False),                       # a prefix match needs a word boundary
])
def test_exchange_check(short, long_form, kept):
    reason = bu._drop_reason(_row("ABC", exchangeShortName=short, exchange=long_form), FLOOR)
    assert (reason is None) is kept, reason


@pytest.mark.parametrize("sym,kept", [
    ("A", True), ("GOOGL", True), ("BRK-B", True), ("brk-b", True), (" bf-b ", True),
    ("EP-PC", False), ("MER-PK", False), ("SEAL-PB", False),    # preferred series
    ("ABCDEF", False), ("AB1", False), ("XYZ-WT", False), ("ABC-", False),
])
def test_only_common_share_symbols_are_kept(sym, kept):
    reason = bu._drop_reason(_row(sym), FLOOR)
    assert (reason is None) is kept, reason
    if not kept:
        assert reason == "not_common_share"


@pytest.mark.parametrize("cap,reason", [
    (None, "bad_market_cap"), ("N/A", "bad_market_cap"), ("3,000,000,000", "bad_market_cap"),
    (True, "bad_market_cap"), (math.nan, "bad_market_cap"), (math.inf, "bad_market_cap"),
    (-math.inf, "bad_market_cap"), (0, "bad_market_cap"), (-5e9, "bad_market_cap"),
    (FLOOR - 1, "below_floor"), (FLOOR, None), (3e12, None), (2_000_000_000, None),
])
def test_market_cap_outliers(cap, reason):
    assert bu._drop_reason(_row("ABC", cap=cap), FLOOR) == reason


@pytest.mark.parametrize("row", [None, "AAPL", 7, [], {}, {"symbol": None}, {"symbol": "  "},
                                 {"symbol": 42}])
def test_malformed_rows_are_dropped_not_raised(row):
    assert bu._drop_reason(row, FLOOR) == "malformed"


@pytest.mark.asyncio
async def test_kept_universe_has_finite_caps_one_row_per_symbol_and_the_modal_sector(tmp_path):
    rows = [
        _row("AAA", cap=5e9, sector="Financial Services"),
        _row("aaa", cap=6e9, sector="Financial Services"),   # same symbol, other case
        _row("BBB", cap=3e9, sector="Financial Services"),
        _row("CCC", cap=4e9, sector="Industrials"),
        _row("DDD", cap=math.nan),
    ]
    out = tmp_path / "u.json"
    assert await _run(_FakeFMP({"Asset Management": rows}), out) == bu.EXIT_OK
    (entry,) = _written(out)["industries"]
    assert entry["tickers"] == ["AAA", "BBB", "CCC"]
    assert entry["market_caps"]["AAA"] == 5e9            # the first row wins, no double vote
    assert entry["sector"] == "Financial Services"
    assert all(math.isfinite(c) and c > 0 for c in entry["market_caps"].values())


@pytest.mark.asyncio
async def test_a_fund_fmp_did_not_flag_is_kept_but_named(tmp_path, caplog):
    """NASDAQ's fifth-letter X = mutual fund. FMP's flag is the authority, so the row is
    kept — but the owner sees it before uploading."""
    out = tmp_path / "u.json"
    rows = [_row("TROW"), _row("ABCDX")]
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        assert await _run(_FakeFMP({"Asset Management": rows}), out) == bu.EXIT_OK
    assert "ABCDX" in _tickers(out)
    assert "ABCDX" in caplog.text and "fifth-letter" in caplog.text


# ── paging: never a silent truncation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_full_page_is_paged_not_truncated(tmp_path, caplog):
    syms = [f"AM{chr(65 + i)}" for i in range(7)]
    fmp = _FakeFMP({"Asset Management": [_row(s) for s in syms]})
    out = tmp_path / "u.json"
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        assert await _run(fmp, out, page_limit=3, max_pages=5) == bu.EXIT_OK
    assert _tickers(out) == sorted(syms)
    pages = [p.get("page") for p in fmp.screener_calls()]
    assert pages == [None, "1", "2"]
    assert "needed 3 screener pages" in caplog.text


@pytest.mark.asyncio
async def test_an_exact_multiple_of_the_page_ends_on_the_empty_page(tmp_path):
    syms = [f"AM{chr(65 + i)}" for i in range(6)]
    fmp = _FakeFMP({"Asset Management": [_row(s) for s in syms]})
    out = tmp_path / "u.json"
    assert await _run(fmp, out, page_limit=3, max_pages=5) == bu.EXIT_OK
    assert _tickers(out) == sorted(syms)
    assert len(fmp.screener_calls()) == 3


@pytest.mark.asyncio
async def test_an_industry_still_full_at_the_page_cap_fails_and_writes_nothing(tmp_path, caplog):
    """The 2026-06-24 'Asset Management' was exactly 1,000 rows: cut, not complete."""
    out = tmp_path / "u.json"
    before = _previous_file(out, 3)
    fmp = _FakeFMP({
        "Asset Management": [_row(f"AM{chr(65 + i)}") for i in range(5)],
        "Banks - Regional": [_row("FITB")],
    })
    with caplog.at_level(logging.ERROR, logger=bu.__name__):
        assert await _run(fmp, out, page_limit=2, max_pages=2) == bu.EXIT_BUILD_FAILED
    assert out.read_text(encoding="utf-8") == before
    assert "Asset Management" in caplog.text and "TruncatedIndustryError" in caplog.text


@pytest.mark.asyncio
async def test_a_screener_that_ignores_page_fails_loudly(tmp_path, caplog):
    page0 = [_row("AMA"), _row("AMB")]
    fmp = _FakeFMP({"Asset Management": lambda params: list(page0)})
    out = tmp_path / "u.json"
    with caplog.at_level(logging.ERROR, logger=bu.__name__):
        assert await _run(fmp, out, page_limit=2, max_pages=5) == bu.EXIT_BUILD_FAILED
    assert not out.exists()
    assert "not honouring `page`" in caplog.text
    assert len(fmp.screener_calls()) == 2      # stopped at the first repeat, not the cap


# ── failures fail the build ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    FMPUnavailableException("HTTP 503 after 4 attempts"),
    RuntimeError("unexpected"),
    lambda params: {"Error Message": "Limit Reach"},     # a 200 that is not rows
    lambda params: None,
])
async def test_a_failed_industry_fails_the_build_and_writes_nothing(tmp_path, caplog, bad):
    out = tmp_path / "u.json"
    before = _previous_file(out, 2)
    fmp = _FakeFMP({"Asset Management": [_row("TROW"), _row("BEN")], "Banks - Regional": bad})
    with caplog.at_level(logging.ERROR, logger=bu.__name__):
        assert await _run(fmp, out) == bu.EXIT_BUILD_FAILED
    assert out.read_text(encoding="utf-8") == before
    assert not out.with_suffix(".json.part").exists()
    assert "1 of 2 industries FAILED" in caplog.text and "Banks - Regional" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("industries", [
    FMPUnavailableException("down"), [], {"Error Message": "x"}, None, ["  "],
])
async def test_no_industry_list_fails_the_build(tmp_path, industries):
    fmp = _FakeFMP({}, industries=industries)
    out = tmp_path / "u.json"
    assert await _run(fmp, out) == bu.EXIT_BUILD_FAILED
    assert not out.exists() and fmp.screener_calls() == []


@pytest.mark.asyncio
async def test_a_rate_limit_is_retried_before_the_industry_counts_as_failed(tmp_path):
    answers: List[Any] = [FMPRateLimitException("429", retry_after="2"),
                          FMPRateLimitException("429"), [_row("TROW")]]

    def _screener(params):
        nxt = answers.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return nxt

    sleep = _Sleep()
    out = tmp_path / "u.json"
    assert await _run(_FakeFMP({"Asset Management": _screener}), out, sleep=sleep) == bu.EXIT_OK
    assert _tickers(out) == ["TROW"]
    # The server's Retry-After first, then the default schedule.
    assert sleep.delays == [2.0, bu._RATE_LIMIT_BACKOFF_SECONDS[1]]


@pytest.mark.asyncio
async def test_an_exhausted_rate_limit_fails_the_build(tmp_path):
    def _always_429(params):
        raise FMPRateLimitException("429")

    sleep = _Sleep()
    out = tmp_path / "u.json"
    assert await _run(_FakeFMP({"Asset Management": _always_429}), out,
                      sleep=sleep) == bu.EXIT_BUILD_FAILED
    assert sleep.delays == list(bu._RATE_LIMIT_BACKOFF_SECONDS)
    assert not out.exists()


@pytest.mark.parametrize("retry_after,expected", [
    ("2", 2.0), ("0", 5.0), ("-3", 5.0), ("abc", 5.0), (None, 5.0), ("nan", 5.0),
    ("inf", 5.0), ("1e9", bu._MAX_RETRY_AFTER_SECONDS),
])
def test_retry_after_parsing(retry_after, expected):
    assert bu._retry_delay(FMPRateLimitException("429", retry_after=retry_after), 5.0) == expected


@pytest.mark.asyncio
async def test_every_row_dropped_is_never_written_even_with_allow_shrink(tmp_path):
    out = tmp_path / "u.json"
    fmp = _FakeFMP({"Asset Management": [_row("SPY", isEtf=True)], "Shell Companies": []})
    assert await _run(fmp, out, allow_shrink=True) == bu.EXIT_BUILD_FAILED
    assert not out.exists()


# ── the shrink guard ────────────────────────────────────────────────────────────────


def _rows(n: int) -> List[Dict[str, Any]]:
    return [_row(f"N{chr(65 + i // 26)}{chr(65 + i % 26)}") for i in range(n)]


@pytest.mark.asyncio
async def test_a_shrink_over_ten_percent_is_refused_without_the_flag(tmp_path, caplog):
    out = tmp_path / "u.json"
    before = _previous_file(out, 20)
    with caplog.at_level(logging.ERROR, logger=bu.__name__):
        code = await _run(_FakeFMP({"Asset Management": _rows(17)}), out)
    assert code == bu.EXIT_SHRINK_REFUSED
    assert out.read_text(encoding="utf-8") == before
    assert "REFUSED" in caplog.text and "--allow-shrink" in caplog.text and "20 → 17" in caplog.text


@pytest.mark.asyncio
async def test_allow_shrink_writes_the_smaller_universe(tmp_path, caplog):
    """The bare flag (`True`) permits up to `_DEFAULT_ALLOW_SHRINK_PERCENT` (50%): 20 → 12
    is 40%. Bounded since round 3 (UB3-3) — tests/test_benchmark_universe_builder_round4_
    guards.py pins the refusal past the bound."""
    out = tmp_path / "u.json"
    _previous_file(out, 20)
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        code = await _run(_FakeFMP({"Asset Management": _rows(12)}), out, allow_shrink=True)
    assert code == bu.EXIT_OK
    assert _written(out)["ticker_count"] == 12
    assert "allowed by --allow-shrink 50" in caplog.text


@pytest.mark.parametrize("previous,new,allowed,with_flag", [
    (10, 9, True, True),            # exactly 10%: "more than 10%" is the bar
    (10, 8, False, True),
    (5704, 5134, True, True),       # 9.99%
    (5704, 5133, False, True),      # 10.01%
    (10, 5, False, True),           # exactly 50%: the bare flag's bound
    (10, 4, False, False),          # 60%: refused even with the bare flag (UB3-3)
    (5704, 2852, False, True),      # 50.00%
    (5704, 2851, False, False),     # 50.02%
    (20, 25, True, True),           # growth is always fine
    (0, 3, True, True),             # an empty baseline cannot shrink
])
def test_shrink_boundary(previous, new, allowed, with_flag):
    assert bu._shrink_allowed(previous, new, allow_shrink=False) is allowed
    assert bu._shrink_allowed(previous, new, allow_shrink=None) is allowed
    assert bu._shrink_allowed(previous, new, allow_shrink=True) is with_flag
    assert bu._shrink_allowed(previous, new, allow_shrink=50) is with_flag


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "{not json", json.dumps({"industries": "x"}),
                                     json.dumps([1, 2])])
async def test_no_readable_baseline_builds_with_a_warning(tmp_path, caplog, content):
    out = tmp_path / "u.json"
    if content is not None:
        out.write_text(content, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        assert await _run(_FakeFMP({"Asset Management": _rows(2)}), out) == bu.EXIT_OK
    assert _written(out)["ticker_count"] == 2
    assert "shrink guard has no baseline" in caplog.text


# ── the output format is unchanged ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_output_format_is_unchanged_and_the_real_reader_loads_it(tmp_path, monkeypatch):
    from app.services import industry_benchmark_service as ibs
    from app.services import universe_data as ud
    from app.services.industry_benchmark_service import IndustryBenchmarkService

    # Hermetic, as every sibling benchmark test: a recompute run asks Storage for a FRESH
    # copy first (`_fetch_benchmark_universe`), then the copy an earlier run fetched. Both
    # legs stubbed to "none", so the reader under test is the file just written — not the
    # production bucket (this test used to reach it, and passed only because the conftest
    # network guard blocked the call and the reader fell back), and not another test's copy.
    fetches: List[int] = []
    monkeypatch.setattr(ibs, "_fetch_benchmark_universe", lambda: fetches.append(1))  # → None
    monkeypatch.setattr(ibs, "_last_fetched_universe", None)

    def _no_download(name):
        raise AssertionError(f"universe_data tried Storage for {name}: the file just written "
                             "should have been read from disk")

    monkeypatch.setattr(ud, "_download_from_storage", _no_download)

    out = tmp_path / ud.BENCHMARK_UNIVERSE
    fmp = _FakeFMP({
        "Asset Management": [_row("TROW", cap=2.5e10), _row("BEN", cap=1.1e10)],
        "Banks - Regional": [_row("FITB", cap=2.8e10, exchangeShortName="NASDAQ")],
        "Shell Companies": [],
    })
    assert await _run(fmp, out) == bu.EXIT_OK
    assert fmp.closed is False                 # an injected client is the caller's to close
    assert not out.with_suffix(".json.part").exists()

    payload = _written(out)
    assert set(payload) == {"generated_at", "source", "market_cap_floor", "industry_count",
                            "ticker_count", "industries"}
    assert payload["market_cap_floor"] == FLOOR
    assert payload["industry_count"] == 2 and payload["ticker_count"] == 3
    for entry in payload["industries"]:
        assert set(entry) == {"industry", "sector", "tickers", "market_caps"}
        assert entry["tickers"] == sorted(entry["market_caps"])
        assert all(isinstance(c, float) for c in entry["market_caps"].values())
    assert [e["industry"] for e in payload["industries"]] == ["Asset Management",
                                                               "Banks - Regional"]

    monkeypatch.setenv(ud._ENV_DIR, str(tmp_path))
    ud.reset_cache_for_tests()
    try:
        svc = IndustryBenchmarkService.__new__(IndustryBenchmarkService)
        loaded = svc._load_universe()
    finally:
        ud.reset_cache_for_tests()
    assert fetches == [1]       # the stubbed seam IS the one the reader goes through
    assert loaded == [("Financial Services", [
        ("Asset Management", [("TROW", 2.5e10), ("BEN", 1.1e10)]),
        ("Banks - Regional", [("FITB", 2.8e10)]),
    ])]
