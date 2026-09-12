"""Silent degradations in the chat service become explicit signals (2026-09-11).

  * `_get_snapshot_summary` gathered five snapshot services with return_exceptions=True
    and silently skipped the failed ones, so "Snapshots for AAPL: Profitability … Growth …"
    read to the model as a complete set — a missing Health vital became "no health
    concerns". It now NAMES what is unavailable and tells the model not to infer.
  * `generate_response` fell back to a tool-less plain-text call with only a WARNING when
    the function-calling round failed. The answer to a stock question with none of its
    live data is materially less than what was charged for; the result now carries
    `degraded="no_tools"` and the endpoint refunds it, like the stream path's shapes.
  * `_deterministic_widget` re-entered `get_index_detail` — the same cold recompute the
    resolver caps at 4 s — with no bound, on the pre-first-token path. Bounded now.

No network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.services.chat_service import ChatService


def _svc() -> ChatService:
    return ChatService.__new__(ChatService)


def _snap(category, rating, metrics):
    return SimpleNamespace(
        category=category, rating=rating,
        metrics=[SimpleNamespace(name=n, value=v) for n, v in metrics],
    )


@pytest.mark.asyncio
async def test_missing_snapshots_are_named_not_dropped(monkeypatch):
    import app.services.profitability_snapshot_service as prof
    import app.services.growth_snapshot_service as gro
    import app.services.valuation_snapshot_service as val
    import app.services.health_snapshot_service as hea
    import app.services.ownership_snapshot_service as own

    def _ok(cat):
        return lambda: SimpleNamespace(**{
            f"get_{cat}_snapshot": AsyncMock(return_value=_snap(cat.title(), 4, [("ROE", "22%")]))
        })

    def _boom(cat):
        return lambda: SimpleNamespace(**{
            f"get_{cat}_snapshot": AsyncMock(side_effect=RuntimeError("upstream 503"))
        })

    monkeypatch.setattr(prof, "get_profitability_snapshot_service", _ok("profitability"))
    monkeypatch.setattr(gro, "get_growth_snapshot_service", _boom("growth"))
    monkeypatch.setattr(val, "get_valuation_snapshot_service", _ok("valuation"))
    monkeypatch.setattr(hea, "get_health_snapshot_service", _boom("health"))
    monkeypatch.setattr(own, "get_ownership_snapshot_service", _ok("ownership"))

    out = await _svc()._get_snapshot_summary("AAPL")
    assert "Profitability: Solid (4/5). ROE: 22%." in out
    assert "Growth, Financial Health snapshots unavailable right now" in out
    assert "do not describe them as absent, weak or unknown" in out


@pytest.mark.asyncio
async def test_all_snapshots_missing_still_tells_the_model(monkeypatch):
    import app.services.profitability_snapshot_service as prof
    import app.services.growth_snapshot_service as gro
    import app.services.valuation_snapshot_service as val
    import app.services.health_snapshot_service as hea
    import app.services.ownership_snapshot_service as own

    def _boom(cat):
        return lambda: SimpleNamespace(**{
            f"get_{cat}_snapshot": AsyncMock(side_effect=RuntimeError("down"))
        })

    for mod, name, cat in (
        (prof, "get_profitability_snapshot_service", "profitability"),
        (gro, "get_growth_snapshot_service", "growth"),
        (val, "get_valuation_snapshot_service", "valuation"),
        (hea, "get_health_snapshot_service", "health"),
        (own, "get_ownership_snapshot_service", "ownership"),
    ):
        monkeypatch.setattr(mod, name, _boom(cat))
    out = await _svc()._get_snapshot_summary("AAPL")
    assert out is not None and "Profitability, Growth, Price, Financial Health, Insiders & Ownership snapshots unavailable" in out


@pytest.mark.asyncio
async def test_widget_fetch_is_bounded_by_the_tool_timeout(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_TOOL_TIMEOUT_SECONDS", 0.05)
    svc = _svc()

    async def _slow(symbol):
        await asyncio.sleep(1.0)
        return {"widget_type": "market_overview"}

    svc._fetch_market_overview_data = _slow
    assert await svc._deterministic_widget("INDEX", "^GSPC", None) is None


@pytest.mark.asyncio
async def test_tool_less_fallback_is_marked_degraded(monkeypatch):
    """The whole generate_response pipeline with every collaborator stubbed, so the ONE
    behaviour under test is: FC round raises → plain-text fallback → `degraded` set."""
    svc = _svc()
    svc.supabase = object()
    svc.fmp = object()

    class _Gem:
        async def generate_with_tools(self, **kw):
            raise RuntimeError("function calling exploded")

        async def generate_text(self, **kw):
            return {"text": "plain answer", "tokens_used": 12}

    svc.gemini = _Gem()
    import app.services.chat_context_resolver as res
    monkeypatch.setattr(res, "get_chat_context_resolver",
                        lambda: SimpleNamespace(resolve=AsyncMock(return_value=None)))
    svc._get_recent_messages = lambda *a, **k: []
    svc._retrieve_context = AsyncMock(return_value=([], []))
    svc._condense_history = AsyncMock(return_value="")
    svc._detect_asset_type = lambda *a, **k: "STOCK"
    svc._get_profit_summary = AsyncMock(return_value=None)
    svc._get_snapshot_summary = AsyncMock(return_value=None)
    svc._get_company_profile_summary = AsyncMock(return_value=None)
    svc._is_deep_dive_request = lambda *a, **k: False
    svc._deterministic_widget = AsyncMock(return_value=None)

    out = await svc.generate_response("sess", "how is AAPL doing?", stock_id="AAPL")
    assert out["content"] == "plain answer"
    assert out["degraded"] == "no_tools"


@pytest.mark.asyncio
async def test_a_successful_tool_round_is_not_marked_degraded(monkeypatch):
    svc = _svc()
    svc.supabase = object()
    svc.fmp = object()

    class _Gem:
        async def generate_with_tools(self, **kw):
            return {"text": "tooled answer", "tokens_used": 40, "tool_results": []}

    svc.gemini = _Gem()
    import app.services.chat_context_resolver as res
    monkeypatch.setattr(res, "get_chat_context_resolver",
                        lambda: SimpleNamespace(resolve=AsyncMock(return_value=None)))
    svc._get_recent_messages = lambda *a, **k: []
    svc._retrieve_context = AsyncMock(return_value=([], []))
    svc._condense_history = AsyncMock(return_value="")
    svc._detect_asset_type = lambda *a, **k: "STOCK"
    svc._get_profit_summary = AsyncMock(return_value=None)
    svc._get_snapshot_summary = AsyncMock(return_value=None)
    svc._get_company_profile_summary = AsyncMock(return_value=None)
    svc._is_deep_dive_request = lambda *a, **k: False
    svc._deterministic_widget = AsyncMock(return_value=None)

    out = await svc.generate_response("sess", "how is AAPL doing?", stock_id="AAPL")
    assert "degraded" not in out


def _stub_generate_response_collaborators(svc, monkeypatch):
    import app.services.chat_context_resolver as res
    monkeypatch.setattr(res, "get_chat_context_resolver",
                        lambda: SimpleNamespace(resolve=AsyncMock(return_value=None)))
    svc.supabase = object()
    svc.fmp = object()
    svc._get_recent_messages = lambda *a, **k: []
    svc._retrieve_context = AsyncMock(return_value=([], []))
    svc._condense_history = AsyncMock(return_value="")
    svc._detect_asset_type = lambda *a, **k: "STOCK"
    svc._get_profit_summary = AsyncMock(return_value=None)
    svc._get_snapshot_summary = AsyncMock(return_value=None)
    svc._get_company_profile_summary = AsyncMock(return_value=None)
    svc._is_deep_dive_request = lambda *a, **k: False
    svc._deterministic_widget = AsyncMock(return_value=None)


@pytest.mark.asyncio
async def test_every_tool_call_failing_marks_the_turn_degraded(monkeypatch):
    """The round SUCCEEDED, but each tool the model called came back as an error (an FMP
    rate limit, a timeout). The answer has none of its live data, exactly like the
    tool-less fallback — and used to be charged as a full turn with no signal."""
    svc = _svc()

    class _Gem:
        async def generate_with_tools(self, **kw):
            return {"text": "answer from memory", "tokens_used": 40, "tool_results": [],
                    "tool_errors": [{"name": "get_stock_chart_data", "error": "FMP rate limit"}]}

    svc.gemini = _Gem()
    _stub_generate_response_collaborators(svc, monkeypatch)
    out = await svc.generate_response("sess", "how is AAPL doing?", stock_id="AAPL")
    assert out["degraded"] == "no_tools"


@pytest.mark.asyncio
async def test_a_partial_tool_failure_is_not_degraded(monkeypatch):
    """One of two tools failing still leaves live data in the answer."""
    svc = _svc()

    class _Gem:
        async def generate_with_tools(self, **kw):
            return {"text": "answer", "tokens_used": 40,
                    "tool_results": [{"widget_type": "stock_chart", "ticker": "AAPL"}],
                    "tool_errors": [{"name": "get_ticker_news", "error": "timed_out"}]}

    svc.gemini = _Gem()
    _stub_generate_response_collaborators(svc, monkeypatch)
    out = await svc.generate_response("sess", "how is AAPL doing?", stock_id="AAPL")
    assert "degraded" not in out


@pytest.mark.asyncio
async def test_the_tool_less_fallback_prompt_claims_no_tools(monkeypatch):
    """The fallback has NO tools attached; a prompt that still says "call explain_price_move
    before answering" invites the model to supply that tool's output from memory."""
    svc = _svc()
    captured = {}

    class _Gem:
        async def generate_with_tools(self, **kw):
            raise RuntimeError("function calling exploded")

        async def generate_text(self, **kw):
            captured.update(kw)
            return {"text": "plain answer", "tokens_used": 12}

    svc.gemini = _Gem()
    _stub_generate_response_collaborators(svc, monkeypatch)
    out = await svc.generate_response("sess", "why did AAPL move today?", stock_id="AAPL")
    assert out["degraded"] == "no_tools"
    from app.services.agents.chat_tools import TOOL_DESCRIPTIONS
    named = {t for t in TOOL_DESCRIPTIONS if t in captured["system_instruction"]}
    assert named == set(), f"the tool-less fallback prompt still claims {sorted(named)}"


@pytest.mark.asyncio
async def test_the_widget_is_taken_from_any_tool_result(monkeypatch):
    """gemini-2.5 emits several calls in one turn; `tool_results[0]` was the news result
    and the chart card was dropped."""
    svc = _svc()

    class _Gem:
        async def generate_with_tools(self, **kw):
            return {"text": "answer", "tokens_used": 40, "tool_errors": [],
                    "tool_results": [{"articles": []},
                                     {"widget_type": "stock_chart", "ticker": "AAPL", "prices": []}]}

    svc.gemini = _Gem()
    _stub_generate_response_collaborators(svc, monkeypatch)
    out = await svc.generate_response("sess", "chart AAPL", stock_id="AAPL")
    widget = out.get("widget") or (out.get("rich_content") or {}).get("widget")
    assert widget and widget.get("widget_type") == "stock_chart", out


async def _prep(svc, monkeypatch, *, context_type, snapshot=None, profile=None, resolved=None):
    import app.services.chat_context_resolver as res
    monkeypatch.setattr(res, "get_chat_context_resolver",
                        lambda: SimpleNamespace(resolve=AsyncMock(return_value=resolved)))
    svc.supabase = object()
    svc.fmp = object()
    svc._get_recent_messages = lambda *a, **k: []
    svc._retrieve_context = AsyncMock(return_value=([], []))
    svc._condense_history = AsyncMock(return_value="")
    svc._detect_asset_type = lambda *a, **k: "STOCK"
    svc._get_profit_summary = AsyncMock(return_value=None)
    svc._get_snapshot_summary = AsyncMock(return_value=snapshot)
    svc._get_company_profile_summary = AsyncMock(return_value=profile)
    svc._is_deep_dive_request = lambda *a, **k: False
    svc._deterministic_widget = AsyncMock(return_value=None)
    return await svc.prepare_stream_generation(
        "sess", "how is AAPL doing?", stock_id="AAPL",
        context_type=context_type, reference_id="AAPL",
    )


@pytest.mark.asyncio
async def test_a_stock_screen_is_grounded_by_its_live_enrichment(monkeypatch):
    prep = await _prep(_svc(), monkeypatch, context_type="STOCK",
                       profile="Apple Inc. designs consumer electronics.")
    assert prep["grounded"] is True and prep["sources"], prep["sources"]


@pytest.mark.asyncio
async def test_a_report_pill_is_not_earned_by_stock_enrichment(monkeypatch):
    """A TICKER_REPORT chat whose report never resolved falls through to the SAME live
    enrichment a STOCK chat gets. "Cay research report · AAPL" must not be earned by a
    company profile — the pill says the answer used the report, and it did not."""
    prep = await _prep(_svc(), monkeypatch, context_type="TICKER_REPORT",
                       profile="Apple Inc. designs consumer electronics.")
    assert prep["grounded"] is False and not prep["sources"], prep["sources"]


@pytest.mark.asyncio
async def test_the_all_missing_snapshot_marker_is_not_grounding(monkeypatch):
    """The marker is text FOR the model ("do not describe them as absent"); nothing
    arrived, so nothing was used."""
    marker = ("Snapshots for AAPL: Profitability, Growth, Price, Financial Health, "
              "Insiders & Ownership snapshots unavailable right now — do not describe "
              "them as absent, weak or unknown.")
    prep = await _prep(_svc(), monkeypatch, context_type="STOCK", snapshot=marker)
    assert prep["grounded"] is False and not prep["sources"]
    # One present category rates itself "(N/5)." and IS grounding.
    prep = await _prep(_svc(), monkeypatch, context_type="STOCK",
                       snapshot="Snapshots for AAPL: Profitability: Solid (4/5). ROE: 22%.")
    assert prep["grounded"] is True


@pytest.mark.asyncio
async def test_a_resolver_that_appends_keeps_the_replayed_label(monkeypatch):
    """COMMODITY appends a static bundled profile to the CLIENT string; on a history reopen
    the price figures in that string are still the persisted snapshot, so the "replayed —
    may be out of date" framing must survive. Only a REPLACED block is live."""
    import app.services.chat_context_resolver as res
    svc = _svc()
    client = "GCUSD · $2,410 · +0.4%"
    monkeypatch.setattr(res, "get_chat_context_resolver", lambda: SimpleNamespace(
        resolve=AsyncMock(return_value=client + "\n\nGold: a monetary metal…")))
    ctx, server_grounded, replayed = await svc._resolve_grounding(
        "COMMODITY", "GCUSD", client, None, True)
    assert server_grounded is True and replayed is True
    # A resolver that REBUILT the block (ETF/CRYPTO/INDEX) is live.
    monkeypatch.setattr(res, "get_chat_context_resolver", lambda: SimpleNamespace(
        resolve=AsyncMock(return_value="SPY · live block")))
    ctx, server_grounded, replayed = await svc._resolve_grounding("ETF", "SPY", client, None, True)
    assert server_grounded is True and replayed is False
