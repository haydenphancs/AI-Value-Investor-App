"""The deep-research tool results never carry the FMP API key to the model (P7).

FMP authenticates with `?apikey=<KEY>` in the query string, and `FMPClient._make_request_impl`
re-raises a raw `httpx.HTTPStatusError` for every status it does not type (400/403/404/…).
httpx's message for that error IS the request URL, key included. The `fmp_tools` handlers
returned `{"error": str(e)}`, and `ResearchAgent._agentic_research` serialised that into the
`function_response` sent to Gemini — a third party — from which the model could also quote it
into `research_findings`, persisted as `research_reports.full_report` and served back by the
report detail API. The chat door never had this hole (`gemini._run_tool_handler` redacts).

Every test here is hermetic: the "real client" test drives `FMPClient` through an in-process
`httpx.MockTransport`, which opens no socket.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.config import settings
from app.integrations.fmp import EmptyAfterFailure, FMPClient, FMPRateLimitException
from app.services.agents import fmp_tools
from app.services.agents import research_agent as ra
from app.services.agents.persona_config import get_persona_config
from app.services.agents.research_agent import ResearchAgent

KEY = "SECRETKEY123abc"
_URL = f"https://financialmodelingprep.com/stable/income-statement?symbol=AAPL&period=quarter&limit=0&apikey={KEY}"


def _status_error(status: int = 404, url: str = _URL) -> httpx.HTTPStatusError:
    """Built exactly the way `response.raise_for_status()` builds it."""
    request = httpx.Request("GET", url)
    response = httpx.Response(status, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        return e
    raise AssertionError("raise_for_status did not raise")


def _fmp(**raising: BaseException) -> MagicMock:
    fmp = MagicMock()
    fmp.api_key = KEY
    for name, exc in raising.items():
        setattr(fmp, name, AsyncMock(side_effect=exc))
    return fmp


def _clean(obj: Any) -> bool:
    """No key, and no `apikey=` with anything but the redaction marker after it."""
    blob = json.dumps(obj, default=str)
    return KEY not in blob and not re.search(r"(?i)apikey=(?!\*\*\*)", blob)


@pytest.fixture(autouse=True)
def _key_in_settings(monkeypatch):
    monkeypatch.setattr(settings, "FMP_API_KEY", KEY)


def test_the_fixture_error_really_carries_the_key():
    """Anti-vacuity: if httpx ever stopped echoing the URL, every test below would pass on
    an error that never contained the key."""
    assert KEY in str(_status_error()) and "apikey=" in str(_status_error())


# ── every handler that can raise ─────────────────────────────────────────────

_CASES = [
    ("fetch_quarterly_financials", {"ticker": "AAPL", "statement_type": "income", "limit": 0}, "get_income_statement"),
    ("fetch_quarterly_financials", {"ticker": "AAPL", "statement_type": "balance_sheet"}, "get_balance_sheet"),
    ("fetch_quarterly_financials", {"ticker": "AAPL", "statement_type": "cash_flow"}, "get_cash_flow_statement"),
    ("fetch_extended_financials", {"ticker": "AAPL", "statement_type": "income"}, "get_income_statement"),
    ("fetch_extended_financials", {"ticker": "AAPL", "statement_type": "balance_sheet"}, "get_balance_sheet"),
    ("fetch_extended_financials", {"ticker": "AAPL", "statement_type": "cash_flow"}, "get_cash_flow_statement"),
    ("fetch_more_news", {"ticker": "AAPL"}, "get_stock_news"),
    ("fetch_dividend_history", {"ticker": "KO"}, "get_dividend_history"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args,method", _CASES)
async def test_an_httpx_error_never_reaches_the_tool_result(monkeypatch, caplog, tool, args, method):
    monkeypatch.setattr(fmp_tools, "_dividend_history_licensed", lambda: True)
    handlers = fmp_tools.build_tool_handlers(_fmp(**{method: _status_error(404)}))
    with caplog.at_level(logging.WARNING, logger=fmp_tools.__name__):
        out = await handlers[tool](dict(args))
    assert _clean(out), out
    assert out["error"] == "upstream data request failed (HTTP 404)"
    # The log keeps the diagnosis (type + endpoint) without the key — and does not rely on
    # main.py's root filter, which is not installed under pytest or in scripts.
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert tool in logged and "HTTPStatusError" in logged and "income-statement" in logged
    assert KEY not in logged


@pytest.mark.asyncio
async def test_sector_performance_error_is_scrubbed(monkeypatch):
    svc = MagicMock()
    svc.get_sector_performance = AsyncMock(side_effect=_status_error(403))
    monkeypatch.setattr(fmp_tools, "get_market_movers_service", lambda: svc)
    out = await fmp_tools.build_tool_handlers(_fmp())["fetch_sector_performance"]({})
    assert _clean(out) and out["error"] == "upstream data request failed (HTTP 403)"
    assert out["sectors"] == []


@pytest.mark.asyncio
async def test_a_non_status_httpx_error_is_generic_too():
    exc = httpx.TooManyRedirects(f"redirect loop at {_URL}", request=httpx.Request("GET", _URL))
    out = await fmp_tools.build_tool_handlers(_fmp(get_income_statement=exc))["fetch_extended_financials"](
        {"ticker": "AAPL", "statement_type": "income"})
    assert _clean(out) and out["error"] == "upstream data request failed (TooManyRedirects)"


@pytest.mark.asyncio
async def test_the_literal_key_is_scrubbed_from_any_other_message():
    """The regex anchors on `apikey=`; a message carrying the bare key needs the literal pass."""
    exc = RuntimeError(f"upstream said: bad credential {KEY} (and apikey={KEY})")
    out = await fmp_tools.build_tool_handlers(_fmp(get_income_statement=exc))["fetch_extended_financials"](
        {"ticker": "AAPL", "statement_type": "income"})
    assert _clean(out)
    assert out["error"].startswith("upstream said: bad credential ***")


@pytest.mark.asyncio
async def test_a_typed_fmp_message_is_kept_for_the_model():
    """Negative control: redaction must not flatten our own useful, secret-free messages."""
    exc = FMPRateLimitException("FMP rate limit hit on income-statement")
    out = await fmp_tools.build_tool_handlers(_fmp(get_income_statement=exc))["fetch_quarterly_financials"](
        {"ticker": "AAPL", "statement_type": "income"})
    assert out == {"error": "FMP rate limit hit on income-statement", "data": []}


@pytest.mark.asyncio
async def test_a_long_error_is_capped():
    exc = RuntimeError("x" * 5000)
    out = await fmp_tools.build_tool_handlers(_fmp(get_income_statement=exc))["fetch_extended_financials"](
        {"ticker": "AAPL", "statement_type": "income"})
    assert len(out["error"]) == 200


def test_a_short_or_empty_configured_key_does_not_mangle_messages(monkeypatch):
    monkeypatch.setattr(settings, "FMP_API_KEY", "")
    assert fmp_tools.tool_error_message(RuntimeError("boom"), None) == "boom"
    monkeypatch.setattr(settings, "FMP_API_KEY", "o")
    assert fmp_tools.tool_error_message(RuntimeError("boom"), None) == "boom"


@pytest.mark.asyncio
async def test_a_news_outage_reason_is_scrubbed_in_the_log(caplog):
    """`get_stock_news` degrades a failure to `EmptyAfterFailure(f"{type}: {e}")` — the httpx
    URL, key and all — and the handler logged that reason verbatim."""
    fmp = _fmp()
    fmp.get_stock_news = AsyncMock(return_value=EmptyAfterFailure(f"HTTPStatusError: {_status_error()}"))
    with caplog.at_level(logging.WARNING, logger=fmp_tools.__name__):
        out = await fmp_tools.build_tool_handlers(fmp)["fetch_more_news"]({"ticker": "AAPL"})
    assert _clean(out)
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "FAILED" in logged and "apikey=***" in logged and KEY not in logged


# ── the real client: a 4xx FMP does not type goes all the way to the tool result ──

@pytest.mark.asyncio
async def test_the_real_fmp_client_4xx_path_is_scrubbed():
    seen: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(400, json={"Error Message": "Invalid limit"})

    fmp = FMPClient()
    fmp.api_key = KEY
    fmp._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        handlers = fmp_tools.build_tool_handlers(fmp)
        q = await handlers["fetch_quarterly_financials"]({"ticker": "AAPL", "statement_type": "income", "limit": 3})
        x = await handlers["fetch_extended_financials"]({"ticker": "AAPL", "statement_type": "cash_flow"})
    finally:
        await fmp._client.aclose()
    assert seen and all(f"apikey={KEY}" in u for u in seen), "the request really carried the key"
    for out in (q, x):
        assert _clean(out) and out["error"] == "upstream data request failed (HTTP 400)"


# ── through the agentic loop: nothing keyed reaches the function_response ────

class _FC:
    def __init__(self, name: str, args: Dict[str, Any]):
        self.name, self.args = name, args


class _Part:
    def __init__(self, fc=None, text=None):
        self.function_call, self.text, self.thought = fc, text, False


class _Resp:
    def __init__(self, parts):
        self.candidates = [type("C", (), {"content": type("X", (), {"parts": parts})(), "finish_reason": None})()]
        self.usage_metadata = None
        texts = [p.text for p in parts if p.text]
        self.text = "".join(texts) if texts else None


class _Chat:
    def __init__(self, responses):
        self._r, self.sent = list(responses), []

    async def send_message(self, msg):
        self.sent.append(msg)
        return self._r.pop(0)


class _Gem:
    model_name = "gemini-2.5-flash"

    def __init__(self, responses):
        self.chat = _Chat(responses)

    def create_tool_chat(self, **kw):
        return self.chat

    async def generate_text(self, **kw):
        return {"text": "FALLBACK"}


class _Out:
    ticker = "AAPL"
    profile = {"companyName": "Apple Inc."}


def _agent(gem, fmp, monkeypatch, handlers=None) -> ResearchAgent:
    agent = ResearchAgent.__new__(ResearchAgent)
    agent.gemini, agent.fmp = gem, fmp
    agent.persona = get_persona_config("warren_buffett")
    monkeypatch.setattr(ra, "build_fmp_tool_declarations", lambda: object())
    if handlers is not None:
        monkeypatch.setattr(ra, "build_tool_handlers", lambda f: handlers)
    return agent


def _fed_back(chat: _Chat) -> str:
    return " ".join(p.function_response.response["result"] for p in chat.sent[1])


@pytest.mark.asyncio
async def test_the_real_handlers_feed_the_model_no_key(monkeypatch):
    fmp = _fmp(get_income_statement=_status_error(404))
    gem = _Gem([_Resp([_Part(fc=_FC("fetch_quarterly_financials", {"ticker": "AAPL", "statement_type": "income"}))]),
                _Resp([_Part(text="done")])])
    agent = _agent(gem, fmp, monkeypatch)  # the REAL build_tool_handlers
    assert await agent._agentic_research(_Out(), "EVIDENCE") == "done"
    fed = _fed_back(gem.chat)
    assert KEY not in fed and "apikey" not in fed and "HTTP 404" in fed


@pytest.mark.asyncio
async def test_an_error_escaping_a_handler_is_scrubbed_by_the_loop(monkeypatch, caplog):
    """The loop's own generic catch fed `str(e)` to the model too."""
    async def leaky(args):
        raise _status_error(404)

    gem = _Gem([_Resp([_Part(fc=_FC("fetch_more_news", {"ticker": "AAPL"}))]), _Resp([_Part(text="ok")])])
    agent = _agent(gem, _fmp(), monkeypatch, {"fetch_more_news": leaky})
    with caplog.at_level(logging.WARNING, logger=ra.__name__):
        assert await agent._agentic_research(_Out(), "EVIDENCE") == "ok"
    fed = _fed_back(gem.chat)
    assert KEY not in fed and "apikey" not in fed
    assert json.loads(fed) == {"error": "upstream data request failed (HTTP 404)"}
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "fetch_more_news" in logged and KEY not in logged


# ── argument hardening: model-chosen counts and tickers ──────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected", [
    (0, 1), (-5, 1), (3, 3), (12, 12), (99, 12), (None, 8), ("abc", 8), ("4", 4), (4.0, 4), (True, 8),
])
async def test_quarterly_limit_is_clamped_to_one_through_twelve(raw, expected):
    fmp = _fmp()
    fmp.get_income_statement = AsyncMock(return_value=[])
    args = {"ticker": "AAPL", "statement_type": "income", "limit": raw}
    out = await fmp_tools.build_tool_handlers(fmp)["fetch_quarterly_financials"](args)
    assert out == {"data": []}
    fmp.get_income_statement.assert_awaited_once_with("AAPL", "quarter", expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,expected", [(0, 1), (-1, 1), (99, 15), (None, 10)])
async def test_news_limit_is_clamped(raw, expected):
    fmp = _fmp()
    fmp.get_stock_news = AsyncMock(return_value=[])
    await fmp_tools.build_tool_handlers(fmp)["fetch_more_news"]({"ticker": "aapl", "limit": raw})
    fmp.get_stock_news.assert_awaited_once_with("AAPL", expected)


@pytest.mark.asyncio
async def test_dividend_limit_never_goes_negative(monkeypatch):
    """A negative limit reached FMP and `data[:-3]`, silently dropping rows from the model's view."""
    monkeypatch.setattr(fmp_tools, "_dividend_history_licensed", lambda: True)
    fmp = _fmp()
    fmp.get_dividend_history = AsyncMock(return_value=[{"d": i} for i in range(5)])
    out = await fmp_tools.build_tool_handlers(fmp)["fetch_dividend_history"]({"ticker": "KO", "limit": -3})
    assert out == {"dividends": [{"d": 0}]}
    fmp.get_dividend_history.assert_awaited_once_with("KO", 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,method,args", [
    ("fetch_quarterly_financials", "get_income_statement", {"statement_type": "income"}),
    ("fetch_extended_financials", "get_income_statement", {"statement_type": "income"}),
    ("fetch_more_news", "get_stock_news", {}),
    ("fetch_dividend_history", "get_dividend_history", {}),
])
@pytest.mark.parametrize("ticker", [None, "", "   "])
async def test_an_empty_ticker_is_refused_before_any_fmp_call(monkeypatch, tool, method, args, ticker):
    """For news this is a correctness bug, not a nicety: FMP answers a symbol-less
    `news/stock` with AAPL's feed, which the model would read as this company's news."""
    monkeypatch.setattr(fmp_tools, "_dividend_history_licensed", lambda: True)
    fmp = _fmp()
    setattr(fmp, method, AsyncMock(return_value=[]))
    out = await fmp_tools.build_tool_handlers(fmp)[tool]({**args, "ticker": ticker})
    assert out["error"] == "ticker is required"
    getattr(fmp, method).assert_not_awaited()


@pytest.mark.asyncio
async def test_a_padded_lowercase_ticker_is_normalised():
    fmp = _fmp()
    fmp.get_balance_sheet = AsyncMock(return_value=[])
    await fmp_tools.build_tool_handlers(fmp)["fetch_extended_financials"](
        {"ticker": "  msft ", "statement_type": "balance_sheet"})
    fmp.get_balance_sheet.assert_awaited_once_with("MSFT", "annual", 10)
