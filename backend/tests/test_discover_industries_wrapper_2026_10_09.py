"""`scripts/discover_industries.py` is the benchmark builder at floor 0 (decision D4, 2026-10-08).

The old script sent only `industry` + `isActivelyTrading` + `limit=1000`: no exchange, ETF
or fund filter and no paging; a failed industry was written EMPTY, with no shrink guard and
a non-atomic write. The May 2026 file it produced carried VOO / SPY, 877 CAD-priced `.TO`
rows, AT&T's own note and both classes of GOOG / BRK / FOX — and every reader passed them
on (the dossier's HHI, the moat peer averages, the report's competitor candidates).

Pinned here: the wrapper delegates to `build_benchmark_universe.main` at floor 0, writes its
own file, passes every guard flag through, keeps none of the old fetch; and the file it
writes is read correctly by EVERY consumer (`universe_data.load_universe`, the dossier, the
moat benchmark, the report collector). Hermetic: a fake FMP client, Storage stubbed.
"""
from __future__ import annotations

import ast
import json
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

import scripts.build_benchmark_universe as bu
import scripts.discover_industries as di


def _row(sym: str, cap: Any, name: str, sector: str = "Technology", **over: Any) -> Dict[str, Any]:
    row = {"symbol": sym, "companyName": name, "marketCap": cap, "sector": sector,
           "price": 50.0, "volume": 1e6, "avgVolume": 1e6, "exchange": "NASDAQ",
           "exchangeShortName": "NASDAQ", "country": "US", "isEtf": False, "isFund": False,
           "isActivelyTrading": True}
    row.update(over)
    return row


SCREENER = {
    "Software - Infrastructure": [
        _row("MSFT", 3.9e12, "Microsoft Corporation"),
        _row("ORCL", 6.1e11, "Oracle Corporation", exchangeShortName="NYSE"),
        _row("TINY", 4.2e7, "Tiny Software Inc."),                    # a micro cap: kept at 0
        _row("SPY", 6e11, "SPDR S&P 500 ETF Trust", isEtf=True),       # never
        _row("MSFT.TO", 3.9e12, "Microsoft Corporation CDR"),          # never
        _row("ZERO", 0, "Zero Cap Inc."),                              # no usable cap
    ],
    "Internet Content & Information": [
        _row("GOOG", 2.1e12, "Alphabet Inc.", "Communication Services", avgVolume=3e7),
        _row("GOOGL", 2.1e12, "Alphabet Inc.", "Communication Services", avgVolume=2e7),
    ],
}


def _distinct(sym: str) -> List[Dict[str, Any]]:
    seed = zlib.crc32(sym.encode("utf-8")) + 1
    return [{"grossProfitMarginTTM": 0.3 + seed / 2**34, "operatingProfitMarginTTM": 0.1 + seed / 2**35,
             "netProfitMarginTTM": 0.05 + seed / 2**36, "currentRatioTTM": 1.5,
             "debtToEquityRatioTTM": 0.8}]


class _FakeFMP:
    def __init__(self) -> None:
        self.closed = False
        self.calls: List[str] = []

    async def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None):
        self.calls.append(endpoint)
        if endpoint == "available-industries":
            return [{"industry": n} for n in SCREENER]
        if endpoint == "ratios-ttm":
            return _distinct(params["symbol"])
        assert endpoint == "company-screener", endpoint
        assert "marketCapMoreThan" not in params
        return SCREENER[params["industry"]]

    async def close(self) -> None:
        self.closed = True


# ══ the wrapper delegates ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_the_wrapper_is_the_builder_at_floor_zero(monkeypatch):
    fake = AsyncMock(return_value=bu.EXIT_SHRINK_REFUSED)
    monkeypatch.setattr(di.builder, "main", fake)
    code = await di.main(allow_shrink=50, allow_missing=["Asset Management - Bonds"],
                         allow_floor_change=True, skip_twin_scan=True)
    assert code == bu.EXIT_SHRINK_REFUSED                         # the exit code propagates
    fake.assert_awaited_once()
    (floor,), kwargs = fake.await_args
    assert floor == 0 and di.INDUSTRY_UNIVERSE_FLOOR == 0
    assert kwargs == {"output": di._OUTPUT_PATH, "allow_shrink": 50,
                      "allow_missing": ["Asset Management - Bonds"],
                      "allow_floor_change": True, "skip_twin_scan": True}


@pytest.mark.asyncio
async def test_the_defaults_change_nothing(monkeypatch):
    fake = AsyncMock(return_value=0)
    monkeypatch.setattr(di.builder, "main", fake)
    assert await di.main() == 0
    _, kwargs = fake.await_args
    assert kwargs["allow_shrink"] is None and list(kwargs["allow_missing"]) == []
    assert kwargs["allow_floor_change"] is False and kwargs["skip_twin_scan"] is False


def test_it_writes_its_own_file_never_the_benchmark_one():
    assert di._OUTPUT_PATH.name == "industry_universe.json"
    assert di._OUTPUT_PATH.parent == bu._OUTPUT_PATH.parent
    assert di._OUTPUT_PATH != bu._OUTPUT_PATH


def _code_only(path: Path) -> str:
    """The module's code with every docstring stripped (comments are dropped by the AST)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_none_of_the_old_fetch_or_write_is_left():
    """Mutation (by hand, once): restore the old `_screener_for_industry` → red."""
    code = _code_only(Path(di.__file__))
    for banned in ("_make_request", "company-screener", "available-industries", "write_text",
                   "json.dumps", "FMPClient", "def _aggregate", "def _screener_for_industry",
                   "def _list_industries"):
        assert banned not in code, banned
    assert "builder.main(" in code


@pytest.mark.parametrize("argv,expected", [
    ([], dict(allow_shrink=None, allow_missing=[], allow_floor_change=False,
              skip_twin_scan=False)),
    (["--allow-shrink", "--allow-missing", "A", "--allow-missing", "B - C",
      "--allow-floor-change", "--skip-twin-scan"],
     dict(allow_shrink=50, allow_missing=["A", "B - C"], allow_floor_change=True,
          skip_twin_scan=True)),
    (["--allow-shrink", "40"], dict(allow_shrink=40)),
])
def test_cli(argv, expected):
    args = di._build_parser().parse_args(argv)
    for key, value in expected.items():
        assert getattr(args, key) == value, key
    assert args.output == di._OUTPUT_PATH


@pytest.mark.parametrize("value", ["0", "101", "abc"])
def test_cli_rejects_a_bad_percentage(value):
    with pytest.raises(SystemExit):
        di._build_parser().parse_args(["--allow-shrink", value])


def test_the_main_block_passes_every_flag_and_scrubs_the_console():
    tree = ast.parse(Path(di.__file__).read_text(encoding="utf-8"))
    block = next(n for n in tree.body if isinstance(n, ast.If)
                 and isinstance(n.test, ast.Compare)
                 and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__")
    (call,) = [n for n in ast.walk(block) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Name) and n.func.id == "main"]
    keywords = {k.arg: ast.unparse(k.value) for k in call.keywords}
    assert keywords == {"output": "args.output", "allow_shrink": "args.allow_shrink",
                        "allow_missing": "args.allow_missing",
                        "allow_floor_change": "args.allow_floor_change",
                        "skip_twin_scan": "args.skip_twin_scan"}
    filters = [n for n in ast.walk(block) if isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute) and n.func.attr == "addFilter"]
    assert filters and "SecretRedactingFilter" in ast.unparse(filters[0])


# ══ the file it writes, read by every consumer ══════════════════════════════════════════


@pytest.fixture
def built(tmp_path, monkeypatch):
    """Run the REAL wrapper → builder against the fake client into a scratch dir that
    `universe_data` reads (Storage stubbed out: the file just written must be the one read)."""
    from app.services import universe_data as ud

    fake = _FakeFMP()
    monkeypatch.setattr(bu, "FMPClient", lambda: fake)
    monkeypatch.setattr(bu, "_TWIN_CALLS_PER_SECOND", 1e6)       # no real pacing waits
    out = tmp_path / ud.INDUSTRY_UNIVERSE

    def _no_download(name):
        raise AssertionError(f"universe_data tried Storage for {name}")

    monkeypatch.setattr(ud, "_download_from_storage", _no_download)
    monkeypatch.setenv(ud._ENV_DIR, str(tmp_path))
    ud.reset_cache_for_tests()
    import asyncio
    code = asyncio.run(di.main(output=out))
    yield code, out, fake
    ud.reset_cache_for_tests()


def test_the_payload_keeps_every_key_the_old_file_had(built):
    code, out, fake = built
    assert code == bu.EXIT_OK and fake.closed is True             # the builder's own client
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert set(payload) == {"generated_at", "source", "market_cap_floor", "industry_count",
                            "ticker_count", "industries"}
    assert payload["market_cap_floor"] == 0
    assert payload["industry_count"] == 2 and payload["ticker_count"] == 4
    for entry in payload["industries"]:
        assert set(entry) == {"industry", "sector", "tickers", "market_caps"}
        assert entry["tickers"] == sorted(entry["market_caps"])
        assert all(isinstance(c, float) and c > 0 for c in entry["market_caps"].values())
    assert {e["industry"]: e["tickers"] for e in payload["industries"]} == {
        "Software - Infrastructure": ["MSFT", "ORCL", "TINY"],      # micro cap kept, no ETF/CDR
        "Internet Content & Information": ["GOOG"],                  # one class per issuer
    }


def test_every_consumer_reads_it(built, monkeypatch):
    from app.services import industry_dossier_service as dossier
    from app.services import industry_moat_benchmark_service as moat
    from app.services import universe_data as ud
    from app.services.agents import ticker_report_data_collector as collector

    code, _, _ = built
    assert code == bu.EXIT_OK
    industries = ud.load_universe(ud.INDUSTRY_UNIVERSE)
    assert len(industries) == 2

    by_name = {e["industry"]: e for e in dossier._load_universe()}
    soft = by_name["Software - Infrastructure"]
    assert soft["sector"] == "Technology" and soft["tickers"] == ["MSFT", "ORCL", "TINY"]
    assert soft["market_caps"]["MSFT"] == 3.9e12

    assert dict(moat._load_universe_industries()) == {
        "Internet Content & Information": [("GOOG", 2.1e12)],
        "Software - Infrastructure": [("MSFT", 3.9e12), ("ORCL", 6.1e11), ("TINY", 4.2e7)],
    }

    monkeypatch.setattr(collector, "_INDUSTRY_PEERS_CACHE", {})
    assert collector._industry_universe_peers("Software - Infrastructure", {"MSFT"}) == \
        ["ORCL", "TINY"]
