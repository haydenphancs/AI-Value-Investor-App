"""Round-3 review fix UB3-3 (2026-10-08): the write guards of `build_benchmark_universe`.

`--allow-shrink` used to switch the shrink guard off at ANY size — on the very run (the first
US-only build) the owner is told to pass it. That run is also where an industry FMP answers
with an empty page (a soft failure that reads as "no constituents") hides: its single
WARNING line sits among the industries that really left. Now:

  * `--allow-shrink [PCT]` permits a drop up to PCT% (50 when bare); a larger drop still exits
    3 with nothing written;
  * an industry that held >= 20 operating tickers (no dotted suffix, not a 5-letter X fund
    symbol) and has none now refuses the build (exit 3) unless named with `--allow-missing`.

Hermetic: `main()` against a fake FMP client, and the CLI parser on argument lists.
"""
from __future__ import annotations

import ast
import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import scripts.build_benchmark_universe as bu

FLOOR = 500_000_000


def _row(sym: str, cap: float = 2e9) -> Dict[str, Any]:
    return {
        "symbol": sym, "companyName": f"{sym} Holdings Inc.", "marketCap": cap,
        "sector": "Financial Services", "price": 50.0, "volume": 1e6, "avgVolume": 1e6,
        "exchange": "NYSE", "exchangeShortName": "NYSE", "country": "US",
        "isEtf": False, "isFund": False, "isActivelyTrading": True,
    }


def _syms(prefix: str, n: int) -> List[str]:
    return [f"{prefix}{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(n)]


class _FakeFMP:
    def __init__(self, screener: Dict[str, List[Dict[str, Any]]]):
        self.screener = screener
        self.calls = 0

    async def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None):
        self.calls += 1
        if endpoint == "available-industries":
            return [{"industry": n} for n in self.screener]
        rows = self.screener[params["industry"]]
        limit, page = int(params["limit"]), int(params.get("page", 0))
        return rows[page * limit:(page + 1) * limit]

    async def close(self) -> None:  # pragma: no cover — an injected client is not closed
        pass


async def _no_sleep(_delay: float) -> None:  # pragma: no cover — no 429 here
    pass


def _previous(out: Path, industries: Dict[str, List[str]]) -> str:
    """Write a previous universe; returns its exact text."""
    text = json.dumps({
        "generated_at": "2026-06-24T00:17:55+00:00", "source": "old", "market_cap_floor": FLOOR,
        "industry_count": len(industries),
        "ticker_count": sum(len(t) for t in industries.values()),
        "industries": [{"industry": name, "sector": "Financial Services", "tickers": tickers,
                        "market_caps": {t: 1e9 for t in tickers}}
                       for name, tickers in industries.items()],
    }, indent=2)
    out.write_text(text, encoding="utf-8")
    return text


async def _run(screener: Dict[str, List[Dict[str, Any]]], out: Path, **kw: Any) -> int:
    return await bu.main(FLOOR, output=out, fmp=_FakeFMP(screener), sleep=_no_sleep, **kw)


def _errors(caplog) -> List[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


# ── a bounded --allow-shrink ────────────────────────────────────────────────────────


@pytest.mark.parametrize("value,limit", [
    (None, None), (False, None), (True, 50.0), (50, 50.0), (1, 1.0), (100, 100.0),
    (37.5, 37.5),
])
def test_shrink_limit(value, limit):
    assert bu._shrink_limit(value) == limit


@pytest.mark.parametrize("value", [0, -1, 101, 1e9, math.nan, math.inf, -math.inf, "50", [50]])
def test_a_bad_shrink_override_is_a_caller_bug_never_unbounded(value):
    """Mutation: let a bad value read as "allowed" and the old unbounded switch is back."""
    with pytest.raises(ValueError):
        bu._shrink_limit(value)


@pytest.mark.asyncio
async def test_a_bad_override_fails_before_any_fmp_call(tmp_path):
    fmp = _FakeFMP({"Asset Management": [_row("TROW")]})
    with pytest.raises(ValueError):
        await bu.main(FLOOR, output=tmp_path / "u.json", fmp=fmp, sleep=_no_sleep,
                      allow_shrink=250)
    assert fmp.calls == 0 and not (tmp_path / "u.json").exists()


@pytest.mark.asyncio
async def test_the_bare_flag_refuses_a_drop_past_its_bound(tmp_path, caplog):
    """UB3-3: 20 → 3 is 85% — the old switch wrote it; the bounded one refuses (exit 3)."""
    out = tmp_path / "u.json"
    before = _previous(out, {"Asset Management": _syms("P", 20)})
    rows = [_row(s) for s in _syms("N", 3)]
    with caplog.at_level(logging.ERROR, logger=bu.__name__):
        code = await _run({"Asset Management": rows}, out, allow_shrink=True)
    assert code == bu.EXIT_SHRINK_REFUSED
    assert out.read_text(encoding="utf-8") == before
    assert not out.with_suffix(".json.part").exists()
    (msg,) = _errors(caplog)
    assert "REFUSED" in msg and "85.0%" in msg and "20 → 3" in msg and "50%" in msg


@pytest.mark.asyncio
@pytest.mark.parametrize("allow,code", [(84, bu.EXIT_SHRINK_REFUSED), (85, bu.EXIT_OK),
                                        (100, bu.EXIT_OK)])
async def test_an_explicit_percentage_is_the_bound(tmp_path, allow, code):
    out = tmp_path / "u.json"
    _previous(out, {"Asset Management": _syms("P", 20)})
    rows = [_row(s) for s in _syms("N", 3)]
    assert await _run({"Asset Management": rows}, out, allow_shrink=allow) == code


@pytest.mark.parametrize("previous,new,limit,allowed", [
    (100, 50, 50, True), (100, 49, 50, False),           # exactly the bound passes
    (5704, 3100, 50, True),                               # the expected first US-only build
    (100, 92, 5, True),                                   # a PCT under 10 changes nothing
    (100, 89, 5, False),
])
def test_shrink_bound_boundaries(previous, new, limit, allowed):
    assert bu._shrink_allowed(previous, new, allow_shrink=limit) is allowed


# ── an industry that went missing ───────────────────────────────────────────────────


def _two_industry_previous(out: Path, banks: int = 25) -> str:
    return _previous(out, {"Asset Management": _syms("P", 30),
                           "Banks - Regional": _syms("B", banks)})


def _screener_without_banks() -> Dict[str, List[Dict[str, Any]]]:
    """The soft failure: the screener answers Banks - Regional with an empty page."""
    return {"Asset Management": [_row(s) for s in _syms("N", 30)], "Banks - Regional": []}


@pytest.mark.asyncio
async def test_a_large_industry_with_no_constituents_now_refuses_the_build(tmp_path, caplog):
    """UB3-3's scenario: the 55 → 30 drop (45%) passes the bare --allow-shrink, but a real
    industry came back empty. Mutation: drop the missing-industry check → exit 0, written."""
    out = tmp_path / "u.json"
    before = _two_industry_previous(out)
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        code = await _run(_screener_without_banks(), out, allow_shrink=True)
    assert code == bu.EXIT_SHRINK_REFUSED
    assert out.read_text(encoding="utf-8") == before
    (msg,) = _errors(caplog)
    assert "Banks - Regional (25)" in msg
    assert '--allow-missing "Banks - Regional"' in msg      # the exact flag to add
    assert "allowed by --allow-shrink 50" in caplog.text     # the shrink itself was fine


@pytest.mark.asyncio
@pytest.mark.parametrize("named", [["Banks - Regional"], ["  banks - REGIONAL  "],
                                   "Banks - Regional"])
async def test_a_named_industry_may_leave(tmp_path, caplog, named):
    """Matched case- and space-insensitively; a bare string is ONE industry, not letters."""
    out = tmp_path / "u.json"
    _two_industry_previous(out)
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        code = await _run(_screener_without_banks(), out, allow_shrink=True,
                          allow_missing=named)
    assert code == bu.EXIT_OK
    assert json.loads(out.read_text(encoding="utf-8"))["ticker_count"] == 30
    assert "industry='Banks - Regional' (25 operating tickers before) has none now — " \
           "allowed by --allow-missing" in caplog.text


@pytest.mark.asyncio
async def test_both_refusals_are_reported_on_one_run(tmp_path, caplog):
    out = tmp_path / "u.json"
    _two_industry_previous(out)
    screener = {"Asset Management": [_row(s) for s in _syms("N", 5)], "Banks - Regional": []}
    with caplog.at_level(logging.ERROR, logger=bu.__name__):
        code = await _run(screener, out)
    assert code == bu.EXIT_SHRINK_REFUSED
    errors = _errors(caplog)
    assert len(errors) == 2
    assert any("ticker count drops" in e for e in errors)
    assert any("Banks - Regional (25)" in e for e in errors)


@pytest.mark.parametrize("tickers,blocks", [
    (_syms("B", 20), True),                               # the threshold itself
    (_syms("B", 19), False),                              # under it: WARNING only
    # 20 tickers, but 5 dotted and 2 fund-shaped: 13 operating ones.
    (_syms("B", 13) + [f"X{i}.TO" for i in range(5)] + ["ABCDX", "VFIAX"], False),
    (_syms("B", 20) + [None, 7, "", "  "], True),         # junk entries are not counted
])
def test_only_operating_tickers_count_toward_the_threshold(tickers, blocks):
    previous = [{"industry": "Banks - Regional", "tickers": tickers}]
    assert bu._missing_industries_allowed(previous, []) is (not blocks)


@pytest.mark.parametrize("previous", [
    [None, 3, "x", []],
    [{"industry": None, "tickers": _syms("B", 30)}],
    [{"industry": "   ", "tickers": _syms("B", 30)}],
    [{"industry": ["Banks"], "tickers": _syms("B", 30)}],
    [{"industry": "Banks - Regional", "tickers": "ABC"}],
    [{"industry": "Banks - Regional"}],
])
def test_a_malformed_previous_entry_never_blocks_or_raises(previous):
    assert bu._missing_industries_allowed(previous, []) is True
    bu._log_comparison(previous, [])                     # the comparison log survives it too


def test_an_industry_still_present_is_never_missing():
    previous = [{"industry": "Banks - Regional", "tickers": _syms("B", 300)}]
    new = [{"industry": "Banks - Regional", "sector": "Financial Services",
            "tickers": ["FITB"], "market_caps": {"FITB": 2e10}}]
    assert bu._missing_industries_allowed(previous, new) is True


def test_a_duplicate_previous_industry_counts_its_largest_entry(caplog):
    previous = [{"industry": "Banks - Regional", "tickers": _syms("B", 3)},
                {"industry": "Banks - Regional", "tickers": _syms("C", 40)}]
    with caplog.at_level(logging.ERROR, logger=bu.__name__):
        assert bu._missing_industries_allowed(previous, []) is False
    (msg,) = _errors(caplog)
    assert "Banks - Regional (40)" in msg and msg.count("Banks - Regional (") == 1


def test_an_unused_allow_missing_name_is_reported(caplog):
    """A typo must not pass silently — it would leave the real industry blocking."""
    previous = [{"industry": "Banks - Regional", "tickers": _syms("B", 30)}]
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        ok = bu._missing_industries_allowed(previous, [], ["Banks - Regionl"])
    assert ok is False
    assert "did not go missing" in caplog.text and "banks - regionl" in caplog.text


@pytest.mark.asyncio
async def test_allow_missing_none_is_no_override(tmp_path):
    out = tmp_path / "u.json"
    _two_industry_previous(out)
    assert await _run(_screener_without_banks(), out, allow_shrink=True,
                      allow_missing=None) == bu.EXIT_SHRINK_REFUSED


@pytest.mark.asyncio
async def test_allow_missing_without_a_previous_file_is_noted(tmp_path, caplog):
    out = tmp_path / "u.json"
    with caplog.at_level(logging.WARNING, logger=bu.__name__):
        code = await _run({"Asset Management": [_row("TROW")]}, out,
                          allow_missing=["Banks - Regional"])
    assert code == bu.EXIT_OK
    assert "--allow-missing given but there is no previous file" in caplog.text


# ── the CLI ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("argv,shrink,missing", [
    ([], None, []),
    (["--allow-shrink"], 50, []),
    (["--allow-shrink", "30"], 30, []),
    (["--allow-shrink", "--allow-missing", "Banks - Regional"], 50, ["Banks - Regional"]),
    (["--allow-missing", "A", "--allow-missing", "B - C"], None, ["A", "B - C"]),
])
def test_cli_parsing(argv, shrink, missing):
    args = bu._build_parser().parse_args(argv)
    assert args.allow_shrink == shrink and args.allow_missing == missing
    assert args.floor == bu._DEFAULT_FLOOR


@pytest.mark.parametrize("value", ["0", "101", "-5", "abc", "50.5", "nan"])
def test_cli_rejects_a_bad_percentage(value, capsys):
    with pytest.raises(SystemExit) as exc:
        bu._build_parser().parse_args(["--allow-shrink", value])
    assert exc.value.code == 2


def test_the_main_block_passes_both_overrides_to_main():
    """Brace-bound to the `__main__` block (comments dropped by the AST): a flag parsed but
    never passed on would be a silent no-op."""
    tree = ast.parse(Path(bu.__file__).read_text(encoding="utf-8"))
    block = next(n for n in tree.body if isinstance(n, ast.If)
                 and isinstance(n.test, ast.Compare)
                 and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__")
    calls = [n for n in ast.walk(block) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "main"]
    assert len(calls) == 1
    keywords = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert keywords["allow_shrink"] == "args.allow_shrink"
    assert keywords["allow_missing"] == "args.allow_missing"
