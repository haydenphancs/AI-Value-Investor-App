"""`EmptyAfterFailure.reason` must never carry the FMP API key.

`get_stock_news` / `get_crypto_news` degrade a non-quota failure to an `EmptyAfterFailure`
whose `reason` was `f"{type(e).__name__}: {e}"`. For an httpx HTTPStatusError that message is
the request URL — `…&apikey=<KEY>` — and the reason is read by consumers that never pass
through the logging redaction filter (chat grounding, the research agent's tool results).
Found by the 2026-09-25 sweep (P7 follow-up).
"""
from __future__ import annotations

import httpx
import pytest

from app.integrations import fmp


def test_the_reason_is_redacted_at_construction():
    leaky = ("HTTPStatusError: Client error '404 Not Found' for url "
             "'https://financialmodelingprep.com/stable/news/stock?symbols=AAPL&apikey=SECRETKEY123'")
    out = fmp.EmptyAfterFailure(leaky)
    assert "SECRETKEY123" not in out.reason
    assert "apikey=" in out.reason and "404" in out.reason, "diagnostics must survive"
    assert list(out) == [] and out.fetch_failed is True


@pytest.mark.asyncio
async def test_a_failed_news_fetch_does_not_carry_the_key(monkeypatch):
    client = fmp.FMPClient.__new__(fmp.FMPClient)

    async def _boom(path, params=None, **_k):
        req = httpx.Request("GET", f"https://financialmodelingprep.com/stable/{path}?apikey=SECRETKEY123")
        raise httpx.HTTPStatusError(f"Client error for url '{req.url}'", request=req,
                                    response=httpx.Response(404, request=req))

    monkeypatch.setattr(client, "_make_request", _boom, raising=True)
    out = await client.get_stock_news("AAPL")
    assert getattr(out, "fetch_failed", False) is True
    assert "SECRETKEY123" not in out.reason
