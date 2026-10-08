"""The FMP key never reaches a benchmark-universe builder log line (review 2026-10-07, UNI-2).

`FMPClient` puts `apikey=<key>` in the query string. For a 400 / 403 / 404 / Cloudflare 52x
it re-raises the raw `httpx.HTTPStatusError`, whose message IS the request URL, and the
builder wrote that message into the per-industry WARNING, the failures dict and the ERROR
summary that joins them — so one CDN hiccup during a regeneration printed the production key
twice on the owner's terminal (CLAUDE.md invariant 8). `__main__` also configured logging
without the `SecretRedactingFilter` app/main.py installs.

Two layers, each pinned on its own: every exception is scrubbed where it is turned into text
(`_describe`, so the failures dict never holds the key at all), AND the module logger carries
the filter, which also scrubs a traceback logged with `exc_info`.

Hermetic: a fake FMP client raises a real httpx error built from a fake key.
"""
from __future__ import annotations

import zlib
import ast
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest

import scripts.build_benchmark_universe as bu
from app.log_redaction import SecretRedactingFilter

FLOOR = 500_000_000
FAKE_KEY = "TESTKEY0123456789abcdefNOTREAL"
_URL = ("https://financialmodelingprep.com/stable/company-screener?industry=Banks%20-%20"
        "Regional&exchange=NYSE%2CNASDAQ%2CAMEX&isEtf=false&isFund=false&limit=1000"
        f"&apikey={FAKE_KEY}")


def _http_error(status: int = 400, url: str = _URL) -> httpx.HTTPStatusError:
    """The exact exception httpx raises — built by `raise_for_status`, so its message is
    httpx's own (the full URL, key and all)."""
    request = httpx.Request("GET", url)
    try:
        httpx.Response(status, request=request).raise_for_status()
    except httpx.HTTPStatusError as exc:
        assert FAKE_KEY in str(exc)          # the leak this file is about is real
        return exc
    raise AssertionError("raise_for_status did not raise")  # pragma: no cover


def _row(sym: str) -> Dict[str, Any]:
    return {"symbol": sym, "companyName": f"{sym} Corp", "marketCap": 2e9,
            "sector": "Financial Services", "price": 50.0, "volume": 1e6,
            "exchangeShortName": "NASDAQ", "isEtf": False, "isFund": False,
            "isActivelyTrading": True}



def _distinct_ratios(sym: str) -> List[Dict[str, Any]]:
    """A `ratios-ttm` answer unique to `sym`: every listing reports its own statements, so
    the statement-twin pass (2026-10-08) drops nothing these tests did not ask for. The
    twin pass itself is pinned in test_benchmark_universe_builder_twins_2026_10_09.py."""
    seed = zlib.crc32(sym.encode("utf-8")) + 1
    return [{"grossProfitMarginTTM": 0.3 + seed / 2**34, "operatingProfitMarginTTM": 0.1 + seed / 2**35,
             "netProfitMarginTTM": 0.05 + seed / 2**36, "currentRatioTTM": 1.5,
             "debtToEquityRatioTTM": 0.8}]

class _FakeFMP:
    def __init__(self, screener: Dict[str, Any], industries: Any = None):
        self.screener = screener
        self.industries = list(screener) if industries is None else industries

    async def _make_request(self, endpoint: str, params: Optional[Dict[str, Any]] = None):
        if endpoint == "available-industries":
            if isinstance(self.industries, BaseException):
                raise self.industries
            return [{"industry": n} for n in self.industries]
        if endpoint == "ratios-ttm":     # the statement-twin pass: one set per listing
            return _distinct_ratios(params["symbol"])
        spec = self.screener[params["industry"]]
        if isinstance(spec, BaseException):
            raise spec
        return spec

    async def close(self) -> None:  # pragma: no cover — an injected client is not closed
        pass


async def _no_sleep(_delay: float) -> None:  # pragma: no cover — no 429 here
    pass


@pytest.fixture(params=["with_logger_filter", "explicit_scrub_only"])
def layer(request, monkeypatch):
    """Run each test with the module logger's filter AND with it removed, so the explicit
    `_describe` scrub is proved on its own, not only behind the filter."""
    if request.param == "explicit_scrub_only":
        monkeypatch.setattr(bu.logger, "filters", [])
    return request.param


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 403, 404, 520, 524])
async def test_a_failed_industry_never_logs_the_key(tmp_path, caplog, layer, status):
    fmp = _FakeFMP({"Asset Management": [_row("TROW")], "Banks - Regional": _http_error(status)})
    with caplog.at_level(logging.DEBUG, logger=bu.__name__):
        code = await bu.main(FLOOR, output=tmp_path / "u.json", fmp=fmp, sleep=_no_sleep)
    assert code == bu.EXIT_BUILD_FAILED
    assert FAKE_KEY not in caplog.text
    # Still diagnosable from the log alone: which industry, which error, which status.
    summary = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(summary) == 1 and "1 of 2 industries FAILED" in summary[0]
    assert "Banks - Regional (HTTPStatusError:" in summary[0]
    assert str(status) in summary[0] and "apikey=***" in summary[0]
    warning = [r.getMessage() for r in caplog.records
               if r.levelno == logging.WARNING and "FAILED" in r.getMessage()]
    assert len(warning) == 1 and "apikey=***" in warning[0]


@pytest.mark.asyncio
async def test_a_failed_industry_list_never_logs_the_key(tmp_path, caplog, layer):
    fmp = _FakeFMP({}, industries=_http_error(403, _URL.replace("company-screener",
                                                                  "available-industries")))
    with caplog.at_level(logging.DEBUG, logger=bu.__name__):
        code = await bu.main(FLOOR, output=tmp_path / "u.json", fmp=fmp, sleep=_no_sleep)
    assert code == bu.EXIT_BUILD_FAILED
    assert FAKE_KEY not in caplog.text
    assert "could not list FMP industries (HTTPStatusError:" in caplog.text
    assert "apikey=***" in caplog.text


@pytest.mark.asyncio
async def test_an_unexpected_error_keeps_its_stack_but_not_the_key(tmp_path, caplog):
    """A bug (anything outside `_EXPECTED_FAILURES`) is logged WITH its traceback, and the
    traceback's last line repeats the message — the logger filter scrubs that too."""
    boom = RuntimeError(f"wrapped: GET {_URL} failed")
    fmp = _FakeFMP({"Asset Management": [_row("TROW")], "Banks - Regional": boom})
    with caplog.at_level(logging.DEBUG, logger=bu.__name__):
        code = await bu.main(FLOOR, output=tmp_path / "u.json", fmp=fmp, sleep=_no_sleep)
    assert code == bu.EXIT_BUILD_FAILED
    assert "Traceback (most recent call last)" in caplog.text     # the stack is kept
    assert FAKE_KEY not in caplog.text


def test_describe_scrubs_and_keeps_the_type_and_status():
    text = bu._describe(_http_error(404))
    assert FAKE_KEY not in text
    assert text.startswith("HTTPStatusError: Client error '404 Not Found'")
    assert "apikey=***" in text


@pytest.mark.parametrize("message", [
    f"apikey={FAKE_KEY}", f"{{'apikey': '{FAKE_KEY}'}}", f"&apikey={FAKE_KEY}&x=1",
])
def test_describe_scrubs_every_shape_the_key_takes(message):
    assert FAKE_KEY not in bu._describe(ValueError(message))


def test_the_module_logger_carries_the_redacting_filter():
    assert any(isinstance(f, SecretRedactingFilter) for f in bu.logger.filters)


def _main_block() -> ast.If:
    tree = ast.parse(Path(bu.__file__).read_text(encoding="utf-8"))   # comments dropped
    return next(n for n in tree.body if isinstance(n, ast.If)
                and isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__")


def test_main_filters_the_root_handlers_after_basic_config():
    """As app/main.py does, so the FMP client's own lines and any other module's are
    scrubbed on the console too. Brace-bound to the `__main__` block, comments stripped."""
    block = _main_block()
    calls = [n for n in ast.walk(block) if isinstance(n, ast.Call)]

    def _line_of(predicate) -> int:
        lines = [n.lineno for n in calls if predicate(n)]
        assert lines, "call not found in the __main__ block"
        return min(lines)

    basic = _line_of(lambda c: isinstance(c.func, ast.Attribute) and c.func.attr == "basicConfig")
    add_filter = _line_of(
        lambda c: isinstance(c.func, ast.Attribute) and c.func.attr == "addFilter"
        and c.args and isinstance(c.args[0], ast.Call)
        and getattr(c.args[0].func, "id", None) == "SecretRedactingFilter"
    )
    assert basic < add_filter
    loops = [n for n in ast.walk(block) if isinstance(n, ast.For)
             and "getLogger().handlers" in ast.unparse(n.iter)]
    assert loops and any(add_filter in {c.lineno for c in ast.walk(loop)
                                        if isinstance(c, ast.Call)} for loop in loops)
