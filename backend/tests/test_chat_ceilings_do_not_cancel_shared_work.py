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
