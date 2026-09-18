"""A chat ceiling abandons the chat's WAIT; it must not cancel the work it was waiting on.

`_run_tool_handler` (8 s default, 20/30/75 s per tool) and `_deterministic_widget` (8 s)
wrapped the fetch in `asyncio.wait_for`, whose expiry CANCELS the awaited coroutine. A market
tool is usually the leader of a shared in-flight build (`get_index_detail`,
`price:universe`), and the leader's CancelledError arm settled the shared future with an error
for every joiner — one chat turn's ceiling failed the index screen and the widget batch.
"""
import asyncio

import pytest

from app.integrations import gemini as gem
from app.services import chat_service as cs


@pytest.mark.asyncio
async def test_a_tool_ceiling_returns_timed_out_but_lets_the_work_finish(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "CHAT_TOOL_TIMEOUT_SECONDS", 0.05)
    state = {"cancelled": False, "done": False}

    async def slow(args):
        try:
            await asyncio.sleep(0.2)
            state["done"] = True
            return {"ok": True}
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    out = await gem._run_tool_handler("get_ticker_news", slow, {})
    assert out.get("error") == "timed_out"
    await asyncio.sleep(0.3)
    assert state["done"] is True and state["cancelled"] is False, state


@pytest.mark.asyncio
async def test_the_widget_ceiling_does_not_cancel_the_shared_index_build(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "CHAT_TOOL_TIMEOUT_SECONDS", 0.05)
    svc = cs.ChatService.__new__(cs.ChatService)
    state = {"cancelled": False, "done": False}

    async def slow(symbol):
        try:
            await asyncio.sleep(0.2)
            state["done"] = True
            return {"widget_type": "market_overview"}
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
    svc._fetch_market_overview_data = slow
    got = await svc._deterministic_widget("INDEX", "^GSPC", None)
    assert got is None                      # this turn gives up on the widget…
    await asyncio.sleep(0.3)
    assert state["done"] is True and state["cancelled"] is False   # …the build completes for joiners


@pytest.mark.asyncio
async def test_the_resolver_ceiling_does_not_cancel_the_shared_detail_build(monkeypatch):
    """The third unshielded ceiling: `ChatContextResolver.resolve`'s 4 s `wait_for`."""
    import app.services.chat_context_resolver as ccr
    import app.services.etf_service as es
    monkeypatch.setattr(ccr, "_RESOLVE_TIMEOUT_SECONDS", 0.05)
    state = {"cancelled": False, "done": False}

    class _SlowSvc:
        async def get_etf_detail(self, symbol):
            try:
                await asyncio.sleep(0.2)
                state["done"] = True
                return None
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise
    monkeypatch.setattr(es, "get_etf_service", lambda: _SlowSvc())
    r = ccr.ChatContextResolver()
    assert await r.resolve("ETF", "SPY", "client ctx") == "client ctx"
    await asyncio.sleep(0.3)
    assert state["done"] is True and state["cancelled"] is False, state


# ── the streamed turn has a wall-clock budget (2026-09-17) ──

@pytest.mark.asyncio
async def test_the_keepalive_loop_abandons_a_stalled_stream_at_the_turn_budget(monkeypatch):
    """Keepalives reset iOS's idle timeout forever; without a deadline a stalled turn held
    the user for the SUM of every inner ceiling."""
    from app.api.v1.endpoints import chat as chat_mod
    from app.config import settings
    import time
    monkeypatch.setattr(settings, "CHAT_STREAM_KEEPALIVE_SECONDS", 0.02)

    async def stalled():
        yield "answer", "partial"
        await asyncio.sleep(10)
        yield "answer", "never"

    events = []
    with pytest.raises(gem.GeminiTimeoutError, match="CHAT_STREAM_BUDGET_SECONDS"):
        async for ev in chat_mod._with_keepalive(stalled(), deadline=time.monotonic() + 0.15):
            events.append(ev)
    assert ("answer", "partial") in events
    assert events.count(("keepalive", None)) >= 1, "it kept the client alive until the deadline"
    assert ("answer", "never") not in events


@pytest.mark.asyncio
async def test_without_a_deadline_the_keepalive_loop_is_unchanged():
    from app.api.v1.endpoints import chat as chat_mod

    async def quick():
        yield "answer", "a"
        yield "answer", "b"
    events = [ev async for ev in chat_mod._with_keepalive(quick())]
    assert events == [("answer", "a"), ("answer", "b")]
