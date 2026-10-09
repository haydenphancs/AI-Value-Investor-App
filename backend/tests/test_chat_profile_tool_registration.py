"""`check_asset_profile` wired into Ask Cay AI (2026-10-08): handler → `ChatService.
_fetch_asset_profile_data` → `chat_profile_tool.fetch_asset_profile`, end to end.

Load-bearing:
  * the handler hands the tool the RAW symbol and the screen — never a coin pair — so the
    screen decides: LTC on the LTC Properties (equity) screen is the REIT, in a general chat it
    is Litecoin; BTC on the Grayscale trust's ETF screen is the fund;
  * every result for a symbol carries `resolved_as` — including the tool's outage, timeout,
    not-found and top-level-failure envelopes, which have none of their own;
  * a non-symbol argument is `{"error": "invalid or missing ticker"}` BEFORE any fetch;
  * the tool is declared for STOCK / NORMAL / ETF / CRYPTO only, under its 12 s ceiling.

Hermetic: the company-facts, fund-facts and coin-facts accessors and the peers list are
stubbed at the bindings the tool resolves (function-scoped imports → the source modules).
"""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.services import chat_profile_tool as cpt
from app.services.agents import chat_tools
from app.services.chat_service import ChatService

_INVALID = {"error": "invalid or missing ticker"}
_VENDORS = re.compile(r"fmp|financial ?modeling ?prep|coingecko|\bgemini(?!_)|\bbrave\b|"
                      r"\bgoogle(?!_)", re.IGNORECASE)


def _svc() -> ChatService:
    return ChatService.__new__(ChatService)


def _company(sym, name, **over):
    facts = {"ticker": sym, "available": True, "name": name, "ceo": "Pam Kaur",
             "sector": "Real Estate", "employees": 23,
             "executives": [{"name": "Pam Kaur", "title": "Chief Executive Officer"}],
             "executives_as_of": "2026-10-08T00:00:00+00:00",
             "as_of": "2026-10-08T00:00:00+00:00"}
    facts.update(over)
    return facts


class _Stubs:
    def __init__(self):
        self.facts = {"LTC": _company("LTC", "LTC Properties, Inc."),
                      "AAPL": _company("AAPL", "Apple Inc.", ceo="Timothy D. Cook")}
        self.fund = {"BTC": {"symbol": "BTC", "available": True,
                             "name": "Grayscale Bitcoin Mini Trust ETF",
                             "expense_ratio_percent": 0.15, "as_of": "2026-10-08"}}
        self.coin = {"LTC": {"symbol": "LTC", "available": True, "name": "Litecoin",
                             "coin_status": "ok", "max_supply": 84_000_000},
                     "BTC": {"symbol": "BTC", "available": True, "name": "Bitcoin",
                             "coin_status": "ok", "max_supply": 21_000_000}}
        self.calls = {"facts": [], "fund": [], "coin": []}
        self.raise_facts = None
        self.facts_gate = None

    async def get_company_facts(self, sym):
        self.calls["facts"].append(sym)
        if self.facts_gate is not None:
            await self.facts_gate.wait()
        if self.raise_facts is not None:
            raise self.raise_facts
        return json.loads(json.dumps(self.facts.get(sym, {
            "ticker": sym, "available": False, "not_found": True,
            "error": "no company profile on file for this symbol"})))

    async def get_fund_facts(self, sym):
        self.calls["fund"].append(sym)
        return json.loads(json.dumps(self.fund.get(sym, {
            "symbol": sym, "available": False, "upstream": True,
            "error": "fund data could not be loaded right now"})))

    async def get_coin_facts(self, base):
        self.calls["coin"].append(base)
        value = self.coin.get(base, {"symbol": base, "available": False,
                                     "coin_status": "failed", "upstream": True,
                                     "error": "coin data could not be loaded right now"})
        if isinstance(value, BaseException):
            raise value
        return json.loads(json.dumps(value))


@pytest_asyncio.fixture
async def stubs(monkeypatch):
    s = _Stubs()
    monkeypatch.setattr("app.services.company_facts_service.get_company_facts",
                        s.get_company_facts)
    monkeypatch.setattr("app.services.etf_service.get_etf_service",
                        lambda: SimpleNamespace(get_fund_facts=s.get_fund_facts))
    monkeypatch.setattr("app.services.crypto_service.get_crypto_service",
                        lambda: SimpleNamespace(get_coin_facts=s.get_coin_facts))

    async def _no_peers(sym):
        return {"peers_note": "No peer list is available for this company in Caydex's data."}
    monkeypatch.setattr(cpt, "_peers", _no_peers)
    yield s
    if s.facts_gate is not None:
        s.facts_gate.set()
    if cpt._side_tasks:
        await asyncio.gather(*list(cpt._side_tasks), return_exceptions=True)


def _handler(screen=None, asset=None):
    return chat_tools.build_chat_tool_handlers(
        _svc(), screen_symbol=screen, screen_asset_type=asset)[chat_tools.PROFILE_TOOL]


# ── the screen decides, through the real chain ────────────────────────────────

@pytest.mark.asyncio
async def test_an_equity_screens_ltc_stays_the_reit(stubs):
    out = await _handler("LTC", "STOCK")({"ticker": "ltc"})
    assert stubs.calls == {"facts": ["LTC"], "fund": [], "coin": []}
    assert out["resolved_as"].startswith("LTC Properties, Inc. (LTC) — the listed company")
    assert "not the cryptocurrency" in out["resolved_as"]
    assert out["company"]["ceo"] == "Pam Kaur"


@pytest.mark.asyncio
async def test_a_report_chat_on_ltc_keeps_the_reit(stubs):
    """A TICKER_REPORT session on LTC derives STOCK (`_detect_asset_type`), so the profile tool
    gets the equity screen too — never Litecoin's supply under a REIT's report."""
    asset = ChatService._detect_asset_type("LTC", "TICKER_REPORT")
    out = await _handler("LTC", asset)({"ticker": "LTC"})
    assert stubs.calls["coin"] == [] and stubs.calls["facts"] == ["LTC"]
    assert "the listed company" in out["resolved_as"]


@pytest.mark.asyncio
@pytest.mark.parametrize("screen,asset", [(None, None), (None, "NORMAL"), ("AAPL", "STOCK")])
async def test_a_typed_bare_coin_elsewhere_is_the_coin(stubs, screen, asset):
    out = await _handler(screen, asset)({"ticker": "LTC"})
    assert stubs.calls["coin"] == ["LTC"] and stubs.calls["facts"] == []
    assert out["resolved_as"].startswith("Litecoin (LTC) — the cryptocurrency")


@pytest.mark.asyncio
async def test_the_etf_screens_btc_is_the_fund(stubs):
    out = await _handler("BTC", "ETF")({"ticker": "btc"})
    assert stubs.calls == {"facts": [], "fund": ["BTC"], "coin": []}
    assert out["resolved_as"].startswith("Grayscale Bitcoin Mini Trust ETF (BTC) — an "
                                         "exchange-traded fund")


@pytest.mark.asyncio
async def test_a_crypto_screen_stored_as_the_pair_reads_the_coin(stubs):
    out = await _handler("BTCUSD", "CRYPTO")({"ticker": "BTC"})
    assert stubs.calls["coin"] == ["BTC"]
    assert out["resolved_as"].startswith("Bitcoin (BTC) — the cryptocurrency")


# ── `kind`: the user's words decide what a shared symbol means ────────────────
#
# Without it a symbol a coin shares with a listed company is the coin everywhere but that
# company's own screen, so "Who is LTC Properties' CEO?" in a general chat could never reach the
# REIT (the financials and ownership tools, which never read a coin, did reach it).

@pytest.mark.asyncio
@pytest.mark.parametrize("screen,asset", [(None, None), (None, "NORMAL"), ("AAPL", "STOCK"),
                                          ("LTCUSD", "CRYPTO")])
async def test_kind_company_reaches_the_reit_from_any_screen(stubs, screen, asset):
    out = await _handler(screen, asset)({"ticker": "LTC", "kind": "company"})
    assert stubs.calls == {"facts": ["LTC"], "fund": [], "coin": []}
    assert out["resolved_as"].startswith("LTC Properties, Inc. (LTC) — the listed company")
    assert out["company"]["ceo"] == "Pam Kaur"
    assert "other_meanings_note" not in out, "the user already said which one"


@pytest.mark.asyncio
async def test_kind_coin_reaches_litecoin_on_the_reits_own_screen(stubs):
    out = await _handler("LTC", "STOCK")({"ticker": "LTC", "kind": "coin"})
    assert stubs.calls == {"facts": [], "fund": [], "coin": ["LTC"]}
    assert out["resolved_as"].startswith("Litecoin (LTC) — the cryptocurrency")
    assert out["coin"]["max_supply"] == 84_000_000


@pytest.mark.asyncio
async def test_kind_fund_reaches_the_trust_in_a_general_chat(stubs):
    out = await _handler()({"ticker": "BTC", "kind": "ETF"})
    assert stubs.calls == {"facts": [], "fund": ["BTC"], "coin": []}
    assert out["resolved_as"].startswith("Grayscale Bitcoin Mini Trust ETF (BTC) — an "
                                         "exchange-traded fund")


@pytest.mark.asyncio
async def test_an_unknown_kind_is_ignored_with_a_fixed_note(stubs):
    out = await _handler("LTC", "STOCK")({"ticker": "LTC", "kind": "reit please"})
    assert stubs.calls["facts"] == ["LTC"] and stubs.calls["coin"] == []
    assert out["kind_note"] == chat_tools._KIND_NOTE
    assert "reit please" not in json.dumps(out)


@pytest.mark.asyncio
async def test_an_outage_under_a_kind_is_looked_up_as_that_kind(stubs):
    stubs.coin["LTC"] = RuntimeError("upstream 503")
    out = await _handler("LTC", "STOCK")({"ticker": "LTC", "kind": "coin"})
    assert out["resolved_as"] == "LTC — looked up as a cryptocurrency"
    assert out["upstream"] is True and "other_meanings_note" not in out


@pytest.mark.asyncio
async def test_a_bare_coin_resolved_without_a_kind_says_how_to_reach_the_company(stubs):
    out = await _handler()({"ticker": "LTC"})
    assert out["resolved_as"].startswith("Litecoin (LTC) — the cryptocurrency")
    note = out["other_meanings_note"]
    assert "listed company or fund" in note and "kind set to company" in note and "LTC" in note
    assert not _VENDORS.search(note)


@pytest.mark.asyncio
async def test_the_reit_on_its_screen_says_how_to_reach_the_coin(stubs):
    out = await _handler("LTC", "STOCK")({"ticker": "LTC"})
    assert out["resolved_as"].startswith("LTC Properties, Inc. (LTC) — the listed company")
    assert "kind set to coin" in out["other_meanings_note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("sym,screen,asset", [("AAPL", None, None), ("LTCUSD", None, None),
                                              ("SPY", "SPY", "ETF"), ("BTCUSD", "BTCUSD", "CRYPTO")])
async def test_an_unshared_symbol_or_a_pair_carries_no_other_meanings_note(stubs, sym, screen,
                                                                          asset):
    out = await _handler(screen, asset)({"ticker": sym})
    assert "other_meanings_note" not in out, out


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [None, "", 7, ["coin"], "fund?", "COIN ", "x" * 5000])
async def test_the_wrapper_ignores_a_kind_that_is_not_a_member(monkeypatch, kind):
    """Belt and braces: the handler normalises, but the wrapper itself acts only on an exact
    member — never on a raw value a future caller might hand it."""
    seen = {}

    async def _capture(ticker, screen_symbol=None, screen_asset_type=None):
        seen.update(screen=screen_symbol, asset=screen_asset_type)
        return {"ticker": ticker, "resolved_as": "x"}
    monkeypatch.setattr(cpt, "fetch_asset_profile", _capture)
    await _svc()._fetch_asset_profile_data("LTC", "AAPL", "STOCK", kind=kind)
    assert seen == {"screen": "AAPL", "asset": "STOCK"}


@pytest.mark.asyncio
async def test_a_kind_with_an_unreadable_symbol_is_dropped_not_raised(monkeypatch):
    seen = {}

    async def _capture(ticker, screen_symbol=None, screen_asset_type=None):
        seen.update(screen=screen_symbol, asset=screen_asset_type)
        return {"error": "no valid ticker supplied"}
    monkeypatch.setattr(cpt, "fetch_asset_profile", _capture)
    out = await _svc()._fetch_asset_profile_data("Apple Inc (AAPL)", None, None, kind="company")
    assert seen == {"screen": None, "asset": None}
    assert out == {"error": "no valid ticker supplied"}


# ── resolved_as on EVERY result for a symbol ──────────────────────────────────

@pytest.mark.asyncio
async def test_a_company_with_no_profile_still_says_what_it_was_looked_up_as(stubs):
    out = await _handler()({"ticker": "ZZZZ"})
    assert out["error"] == "no company profile on file for this symbol"
    assert out["resolved_as"] == "ZZZZ — looked up as a listed company or fund"
    assert "upstream" not in out, "a missing profile is not an outage"


@pytest.mark.asyncio
async def test_a_company_facts_outage_still_says_what_it_was_looked_up_as(stubs):
    stubs.raise_facts = RuntimeError("FMPRateLimitException: 429 for apikey=SECRET")
    out = await _handler("AAPL", "STOCK")({"ticker": "AAPL"})
    assert out["upstream"] is True and out["available"] is False
    assert out["resolved_as"] == "AAPL — looked up as a listed company or fund"
    assert not _VENDORS.search(json.dumps(out)) and "SECRET" not in json.dumps(out)


@pytest.mark.asyncio
async def test_a_coin_outage_still_says_what_it_was_looked_up_as(stubs):
    stubs.coin["LTC"] = RuntimeError("CoinGecko 503")
    out = await _handler()({"ticker": "LTC"})
    assert out["upstream"] is True
    assert out["resolved_as"] == "LTC — looked up as a cryptocurrency"
    assert not _VENDORS.search(json.dumps(out))


@pytest.mark.asyncio
async def test_a_fund_outage_still_says_what_it_was_looked_up_as(stubs):
    out = await _handler("QQQ", "ETF")({"ticker": "QQQ"})
    assert out["upstream"] is True and out["available"] is False
    assert out["resolved_as"] == "QQQ — looked up as a fund"


@pytest.mark.asyncio
async def test_a_slow_company_read_still_says_what_it_was_looked_up_as(stubs, monkeypatch):
    monkeypatch.setattr(cpt, "_FACTS_WAIT", 0.05)
    monkeypatch.setattr(cpt, "_MIN_STEP_WAIT", 0.01)
    stubs.facts_gate = asyncio.Event()
    out = await asyncio.wait_for(_handler()({"ticker": "AAPL"}), 2.0)
    assert out["upstream"] is True and "still loading" in out["error"]
    assert out["resolved_as"] == "AAPL — looked up as a listed company or fund"


@pytest.mark.asyncio
async def test_a_tool_that_fails_outright_still_answers_with_the_ticker_and_resolved_as(
    stubs, monkeypatch,
):
    async def _boom(*a, **k):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(cpt, "_fetch", _boom)
    out = await _handler()({"ticker": "AAPL"})
    assert out["upstream"] is True and out["ticker"] == "AAPL"
    assert out["resolved_as"] == "AAPL — looked up as a listed company or fund"


@pytest.mark.asyncio
@pytest.mark.parametrize("sym,what", [("^GSPC", "an index"), ("GCUSD", "a commodity")])
async def test_an_index_or_commodity_refusal_keeps_the_tools_own_resolved_as(stubs, sym, what):
    """Reachable only if a door forgot its class filter: the tool's answered refusal already
    carries its own `resolved_as`, which the wrapper never overwrites."""
    out = await _handler()({"ticker": sym})
    assert out["resolved_as"] == f"{sym} — {what}"
    assert "upstream" not in out
    assert stubs.calls == {"facts": [], "fund": [], "coin": []}


@pytest.mark.asyncio
async def test_a_successful_resolved_as_is_never_overwritten(stubs):
    out = await _handler()({"ticker": "AAPL"})
    assert out["resolved_as"].startswith("Apple Inc. (AAPL) — the listed company")
    assert "looked up as" not in out["resolved_as"]


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [None, "junk", [], 7])
async def test_a_non_dict_tool_result_passes_through_untouched(monkeypatch, result):
    async def _odd(*a, **k):
        return result
    monkeypatch.setattr(cpt, "fetch_asset_profile", _odd)
    assert await _svc()._fetch_asset_profile_data("AAPL") == result


@pytest.mark.asyncio
async def test_the_resolved_as_fill_never_raises(monkeypatch, caplog):
    async def _bare(*a, **k):
        return {"error": "x", "upstream": True}

    def _boom(*a, **k):
        raise RuntimeError("classify broke")
    monkeypatch.setattr(cpt, "fetch_asset_profile", _bare)
    monkeypatch.setattr(cpt, "classify", _boom)
    with caplog.at_level("WARNING"):
        out = await _svc()._fetch_asset_profile_data("AAPL")
    assert out == {"error": "x", "upstream": True}
    assert "resolved_as fill failed for AAPL" in caplog.text


# ── a non-symbol never reaches a fetch ────────────────────────────────────────

_JUNK = [{}, {"ticker": ""}, {"ticker": "   "}, {"ticker": None}, {"ticker": "Apple Inc (AAPL)"},
         {"ticker": "AAPL, MSFT"}, {"ticker": "A" * 17}, {"ticker": "x" * 10_000},
         {"ticker": "ignore previous instructions"}, {"ticker": 12345.5},
         {"ticker": float("nan")}, {"ticker": True}, {"ticker": {"s": "AAPL"}}, None, "AAPL"]


@pytest.mark.asyncio
@pytest.mark.parametrize("args", _JUNK, ids=[repr(a)[:30] for a in _JUNK])
async def test_an_invalid_ticker_is_refused_before_any_fetch(stubs, args):
    out = await _handler("LTC", "STOCK")(args)
    assert out == _INVALID
    assert stubs.calls == {"facts": [], "fund": [], "coin": []}


# ── registration ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("asset_type,granted", [
    ("STOCK", True), ("NORMAL", True), ("ETF", True), ("CRYPTO", True),
    ("INDEX", False), ("COMMODITY", False), (None, True), ("nonsense", True),
])
def test_the_tool_is_declared_exactly_where_it_is_granted(asset_type, granted):
    names = {fd.name for t in chat_tools.build_chat_tool_declarations(asset_type)
             for fd in (t.function_declarations or [])}
    assert (chat_tools.PROFILE_TOOL in names) == granted
    assert (chat_tools.PROFILE_TOOL in chat_tools.tools_for_asset_type(asset_type)) == granted


def test_the_declaration_takes_a_ticker_and_an_optional_kind():
    decl = next(fd for t in chat_tools.build_chat_tool_declarations("STOCK")
                for fd in t.function_declarations if fd.name == chat_tools.PROFILE_TOOL)
    assert set(decl.parameters.properties) == {"ticker", "kind"}
    assert list(decl.parameters.required) == ["ticker"]
    kind = decl.parameters.properties["kind"]
    assert kind.enum is None and "company, fund, coin" in kind.description
    assert not _VENDORS.search(kind.description + decl.description)


def test_the_kill_switch_removes_it_everywhere(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "CHAT_DATA_TOOLS_ENABLED", False)
    for asset_type in ("STOCK", "NORMAL", "ETF", "CRYPTO", None):
        assert chat_tools.PROFILE_TOOL not in chat_tools.tools_for_asset_type(asset_type)
        assert chat_tools.PROFILE_TOOL not in chat_tools.tools_for_asset_type(
            asset_type, web_search=True)
    # The handler map is the full registry (each door filters it by the same grant).
    assert chat_tools.PROFILE_TOOL in chat_tools.build_chat_tool_handlers(_svc())


def test_its_ceiling_covers_the_tools_own_deadline():
    """The tool answers "still loading" at its own deadline — before the handler ceiling turns
    the turn into a bare `timed_out`."""
    from app.integrations.gemini import _TOOL_TIMEOUTS
    assert _TOOL_TIMEOUTS[chat_tools.PROFILE_TOOL] == 12.0
    assert cpt._TOTAL_WAIT + cpt._MIN_STEP_WAIT < _TOOL_TIMEOUTS[chat_tools.PROFILE_TOOL]
