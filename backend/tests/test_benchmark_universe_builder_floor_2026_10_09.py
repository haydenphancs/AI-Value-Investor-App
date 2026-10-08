"""The market-cap floor is applied by the builder, never by FMP (decision D4, 2026-10-08).

`_screener_for_industry` used to send `marketCapMoreThan=<floor>`. FMP applies that filter
to a server-side cap of its own and HIDES every row whose server-side cap is null — even
when the row it would return carries a real one: VMRK (Vivmark Residential, $22.5B), VYLR
($48.6B), SKYD ($10.1B), LYNX, ADIG… were missing from the 2026-10-08 benchmark file. Now:

  * the filter is never sent; the floor is read off each row's own `marketCap`;
  * every industry fits one 5,000-row page, and a later page that repeats a symbol (a moved
    page boundary) fails the build rather than write a truncated industry;
  * a build at another floor than the file it replaces is REFUSED (exit 3) unless
    `--allow-floor-change` — before any FMP call is spent.

Hermetic: `main()` against a fake FMP client that behaves like the real screener.
"""
from __future__ import annotations

import ast
import json
import logging
import math
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pytest

import scripts.build_benchmark_universe as bu

FLOOR = 500_000_000


def _row(sym: str, cap: Any = 2e9, name: Optional[str] = None, **over: Any) -> Dict[str, Any]:
    row = {
        "symbol": sym, "companyName": name or f"{sym} Holdings Corp", "marketCap": cap,
        "sector": "Real Estate", "price": 50.0, "volume": 1_000_000, "avgVolume": 1_000_000,
        "exchange": "NYSE", "exchangeShortName": "NYSE", "country": "US",
        "isEtf": False, "isFund": False, "isActivelyTrading": True,
    }
    row.update(over)
    return row


def _distinct(sym: str) -> List[Dict[str, Any]]:
    seed = zlib.crc32(sym.encode("utf-8")) + 1
    return [{"grossProfitMarginTTM": 0.3 + seed / 2**34, "operatingProfitMarginTTM": 0.1 + seed / 2**35,
             "netProfitMarginTTM": 0.05 + seed / 2**36, "currentRatioTTM": 1.5,
             "debtToEquityRatioTTM": 0.8}]


class _ScreenerLikeFMP:
    """FMP's screener as probed on 2026-10-08: rows whose SERVER-SIDE cap is null (`hidden`)
    vanish whenever `marketCapMoreThan` is sent, whatever cap the row itself carries; the
    rest are filtered by their own cap. Paged by `limit` / `page`."""

    def __init__(self, screener: Dict[str, List[Dict[str, Any]]], hidden: Set[str] = frozenset()):
        self.screener = screener
        self.hidden = set(hidden)
        self.calls: List[tuple] = []

    async def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None):
        self.calls.append((endpoint, dict(params or {})))
        if endpoint == "available-industries":
            return [{"industry": n} for n in self.screener]
        if endpoint == "ratios-ttm":
            return _distinct(params["symbol"])
        assert endpoint == "company-screener", endpoint
        rows = self.screener[params["industry"]]
        if "marketCapMoreThan" in params:
            floor = float(params["marketCapMoreThan"])
            rows = [r for r in rows if r["symbol"] not in self.hidden
                    and isinstance(r.get("marketCap"), (int, float)) and r["marketCap"] > floor]
        limit, page = int(params["limit"]), int(params.get("page", 0))
        return rows[page * limit:(page + 1) * limit]

    async def close(self) -> None:  # pragma: no cover — an injected client is not closed
        pass

    def screener_calls(self) -> List[Dict[str, Any]]:
        return [p for e, p in self.calls if e == "company-screener"]


async def _no_sleep(_delay: float) -> None:
    pass


def _written(out: Path) -> Dict[str, List[str]]:
    return {e["industry"]: e["tickers"]
            for e in json.loads(out.read_text(encoding="utf-8"))["industries"]}


RESIDENTIAL = [
    _row("VMRK", 22_489_141_120, "Vivmark Residential"),           # server cap null
    _row("MAA", 17_259_417_861, "Mid-America Apartment Communities, Inc."),
    _row("UDR", 15_730_232_160, "UDR, Inc."),
    _row("NXR", 155_501_325, "Nexus Residential Inc."),             # under $500M
]


# ══ the server-side cap filter is gone ══════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("floor,expected", [
    (FLOOR, ["MAA", "UDR", "VMRK"]),
    (0, ["MAA", "NXR", "UDR", "VMRK"]),
])
async def test_a_row_fmp_hides_behind_its_cap_filter_survives(tmp_path, floor, expected):
    """VMRK-shaped: hidden by the screener whenever `marketCapMoreThan` is sent. Mutation:
    send the filter again → VMRK missing at both floors."""
    fmp = _ScreenerLikeFMP({"REIT - Residential": RESIDENTIAL}, hidden={"VMRK"})
    out = tmp_path / "u.json"
    assert await bu.main(floor, output=out, fmp=fmp, sleep=_no_sleep) == bu.EXIT_OK
    assert _written(out) == {"REIT - Residential": expected}
    for params in fmp.screener_calls():
        assert not any(k.lower().startswith("marketcap") for k in params), params


@pytest.mark.asyncio
async def test_a_hidden_row_under_the_floor_is_still_dropped(tmp_path, caplog):
    rows = RESIDENTIAL + [_row("VYLT", 420_000_000, "Vylt Residential Corp")]
    fmp = _ScreenerLikeFMP({"REIT - Residential": rows}, hidden={"VMRK", "VYLT"})
    out = tmp_path / "u.json"
    with caplog.at_level(logging.INFO, logger=bu.__name__):
        assert await bu.main(FLOOR, output=out, fmp=fmp, sleep=_no_sleep) == bu.EXIT_OK
    assert "VYLT" not in _written(out)["REIT - Residential"]
    (line,) = [r for r in caplog.records if "below_floor row(s)" in r.getMessage()]
    assert line.levelno == logging.INFO
    assert "applied here" in line.getMessage()


@pytest.mark.parametrize("cap,floor,reason", [
    (0, 0, "bad_market_cap"), (None, 0, "bad_market_cap"), (math.nan, 0, "bad_market_cap"),
    ("1e9", 0, "bad_market_cap"), (True, 0, "bad_market_cap"), (1, 0, None),
    (FLOOR - 1, FLOOR, "below_floor"), (FLOOR, FLOOR, None),
])
def test_the_client_side_floor(cap, floor, reason):
    assert bu._drop_reason(_row("ABC", cap), floor) == reason


def test_floor_drops_are_info_now():
    assert "below_floor" not in bu._UNEXPECTED_DROP_REASONS
    assert "bad_market_cap" not in bu._UNEXPECTED_DROP_REASONS
    assert {"below_floor", "bad_market_cap"} <= set(bu._EXPECTED_DROP_NOTES)
    assert not hasattr(bu, "_BELOW_FLOOR_ALARM_PERCENT")


# ══ paging: every industry on one page, never truncated ═════════════════════════════════


def test_a_page_holds_every_us_industry():
    """Biotechnology was 607 rows at every cap (probe 2026-10-08); FMP serves up to 10,000
    rows a call. A 1,000-row page would put the next ~400 growth on two pages."""
    assert bu._SCREENER_PAGE_LIMIT == 5000 and bu._SCREENER_PAGE_LIMIT <= 10_000


@pytest.mark.asyncio
async def test_a_row_leaving_between_two_pages_can_never_shorten_an_industry(tmp_path, caplog):
    """The 2026-10-09 review's case: BBB leaves the listing after page 0 is read, so page
    1 starts one row later and DDD is on neither page — nothing repeats. Paging is gone: the
    full first page is refused and no second page is ever asked for. Mutation: page on a
    full page (the old loop) → written without DDD."""
    rows = [_row(s) for s in ("AAA", "BBB", "CCC", "DDD", "EEE")]
    after = [r for r in rows if r["symbol"] != "BBB"]

    class _Leaving(_ScreenerLikeFMP):
        async def _make_request(self, endpoint, params=None):
            if endpoint == "company-screener" and params.get("page") == "1":
                self.calls.append((endpoint, dict(params)))
                return after[3:6]                    # [EEE]: DDD fell between the pages
            return await super()._make_request(endpoint, params)

    fmp = _Leaving({"REIT - Residential": rows})
    out = tmp_path / "u.json"
    with caplog.at_level(logging.ERROR, logger=bu.__name__):
        assert await bu.main(FLOOR, output=out, fmp=fmp, sleep=_no_sleep,
                             page_limit=3) == bu.EXIT_BUILD_FAILED
    assert not out.exists()
    assert [p.get("page") for p in fmp.screener_calls()] == [None]
    assert "TruncatedIndustryError" in caplog.text


@pytest.mark.asyncio
async def test_a_truncated_industry_still_refuses_with_every_cap_returned(tmp_path, caplog):
    rows = [_row(f"A{chr(65 + i // 26)}{chr(65 + i % 26)}") for i in range(12)]
    fmp = _ScreenerLikeFMP({"Biotechnology": rows})
    out = tmp_path / "u.json"
    with caplog.at_level(logging.ERROR, logger=bu.__name__):
        assert await bu.main(0, output=out, fmp=fmp, sleep=_no_sleep,
                             page_limit=3) == bu.EXIT_BUILD_FAILED
    assert not out.exists() and "TruncatedIndustryError" in caplog.text


@pytest.mark.asyncio
async def test_an_industry_under_the_page_is_read_in_one_call(tmp_path):
    rows = [_row(s) for s in ("AAA", "BBB", "CCC", "DDD")]
    fmp = _ScreenerLikeFMP({"REIT - Residential": rows})
    out = tmp_path / "u.json"
    assert await bu.main(FLOOR, output=out, fmp=fmp, sleep=_no_sleep, page_limit=5) == bu.EXIT_OK
    assert _written(out) == {"REIT - Residential": ["AAA", "BBB", "CCC", "DDD"]}
    assert len(fmp.screener_calls()) == 1 and "page" not in fmp.screener_calls()[0]


def test_paging_is_gone():
    """No page cap to raise and no `page` parameter: one call, or a refusal."""
    assert not hasattr(bu, "_SCREENER_MAX_PAGES")
    import inspect
    assert "max_pages" not in inspect.signature(bu.main).parameters
    assert "max_pages" not in inspect.signature(bu._screener_for_industry).parameters


# ══ the floor-change guard ═════════════════════════════════════════════════════════════


def _previous(out: Path, **extra: Any) -> str:
    tickers = ["MAA", "UDR", "VMRK"]
    payload = {"generated_at": "2026-10-08T19:30:00+00:00", "source": "old",
               "industry_count": 1, "ticker_count": 3,
               "industries": [{"industry": "REIT - Residential", "sector": "Real Estate",
                               "tickers": tickers, "market_caps": {t: 1e10 for t in tickers}}]}
    payload.update(extra)
    text = json.dumps(payload, indent=2)
    out.write_text(text, encoding="utf-8")
    return text


@pytest.mark.asyncio
async def test_floor_zero_aimed_at_the_500m_file_refuses_before_any_call(tmp_path, caplog):
    """`--floor 0` without `--output` would overwrite benchmark_universe.json with the
    floor-0 industry file — growth the shrink guard never refuses. Mutation: drop the guard
    → written."""
    out = tmp_path / "benchmark_universe.json"
    before = _previous(out, market_cap_floor=FLOOR)
    fmp = _ScreenerLikeFMP({"REIT - Residential": RESIDENTIAL})
    with caplog.at_level(logging.ERROR, logger=bu.__name__):
        assert await bu.main(0, output=out, fmp=fmp, sleep=_no_sleep) == bu.EXIT_SHRINK_REFUSED
    assert out.read_text(encoding="utf-8") == before
    assert fmp.calls == []                                 # refused before ~3,000 calls
    (err,) = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "REFUSED" in err.getMessage() and "--allow-floor-change" in err.getMessage()


@pytest.mark.asyncio
async def test_allow_floor_change_writes_and_warns(tmp_path, caplog):
    out = tmp_path / "u.json"
    _previous(out, market_cap_floor=FLOOR)
    fmp = _ScreenerLikeFMP({"REIT - Residential": RESIDENTIAL})
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        assert await bu.main(0, output=out, fmp=fmp, sleep=_no_sleep,
                             allow_floor_change=True) == bu.EXIT_OK
    assert json.loads(out.read_text())["market_cap_floor"] == 0
    assert "allowed by --allow-floor-change" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,warns", [
    ({}, "carries no market_cap_floor"),                   # the old discover_industries file
    ({"market_cap_floor": None}, "unreadable"),
    ({"market_cap_floor": "500000000"}, "unreadable"),
    ({"market_cap_floor": True}, "unreadable"),
    ({"market_cap_floor": float("nan")}, "unreadable"),
    ({"market_cap_floor": [0]}, "unreadable"),
])
async def test_a_floor_that_cannot_be_compared_warns_and_builds(tmp_path, caplog, extra, warns):
    out = tmp_path / "u.json"
    _previous(out, **extra)
    fmp = _ScreenerLikeFMP({"REIT - Residential": RESIDENTIAL})
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        assert await bu.main(0, output=out, fmp=fmp, sleep=_no_sleep) == bu.EXIT_OK
    (line,) = [r for r in caplog.records if warns in r.getMessage()]
    assert line.levelno == logging.WARNING


@pytest.mark.parametrize("previous,floor,allowed", [
    (FLOOR, FLOOR, True), (5e8, FLOOR, True), (0, 0, True), (0.0, 0, True),
    (FLOOR, 0, False), (0, FLOOR, False), (FLOOR, 1_000_000_000, False), (FLOOR, FLOOR + 1, False),
])
def test_the_floor_comparison(previous, floor, allowed):
    assert bu._floor_change_allowed({"market_cap_floor": previous}, floor) is allowed
    assert bu._floor_change_allowed({"market_cap_floor": previous}, floor, True) is True


@pytest.mark.asyncio
async def test_no_previous_file_means_no_floor_check(tmp_path):
    fmp = _ScreenerLikeFMP({"REIT - Residential": RESIDENTIAL})
    out = tmp_path / "u.json"
    assert await bu.main(0, output=out, fmp=fmp, sleep=_no_sleep) == bu.EXIT_OK


def test_the_cli_flag_reaches_main():
    assert bu._build_parser().parse_args(["--allow-floor-change"]).allow_floor_change is True
    tree = ast.parse(Path(bu.__file__).read_text(encoding="utf-8"))
    block = next(n for n in tree.body if isinstance(n, ast.If)
                 and isinstance(n.test, ast.Compare)
                 and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__")
    (call,) = [n for n in ast.walk(block) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Name) and n.func.id == "main"]
    assert {k.arg: ast.unparse(k.value) for k in call.keywords}["allow_floor_change"] == \
        "args.allow_floor_change"


def test_the_source_line_no_longer_claims_the_server_filter():
    """The payload's `source` describes how the file was built."""
    src = Path(bu.__file__).read_text(encoding="utf-8")
    main_src = ast.get_source_segment(src, next(
        n for n in ast.parse(src).body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "main"))
    assert "marketCapMoreThan" not in main_src
