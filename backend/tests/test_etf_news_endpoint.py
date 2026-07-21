"""
ETF news endpoints — the mechanical mirror of the stock/crypto news endpoints,
so ETF news now comes from the SAME shared `ticker_news_cache` and gets AI
enrichment (previously it was a direct FMP fetch baked into the detail payload,
uncached and un-enriched).

These exercise the wiring + input validation WITHOUT network: the invalid-symbol
and placeholder-id paths return before the service (and FMP/Gemini) are touched.
"""

from __future__ import annotations

import json

import pytest
from fastapi.responses import JSONResponse

from app.api.v1.endpoints import etfs
from app.schemas.news import EnrichNewsResponse, TickerNewsFeedResponse


def test_etf_news_routes_are_registered():
    paths = {r.path for r in etfs.router.routes}
    assert "/{symbol}/news" in paths
    assert "/{symbol}/news/enrich" in paths


def test_etf_news_endpoints_reuse_the_shared_response_shapes():
    # No new DTO — ETF decodes the exact same shapes iOS already decodes for
    # stocks/crypto, so there is no new decode contract to drift.
    def _resp_model(path, method):
        for r in etfs.router.routes:
            if r.path == path and method in r.methods:
                return r.response_model
        return None

    assert _resp_model("/{symbol}/news", "GET") is TickerNewsFeedResponse
    assert _resp_model("/{symbol}/news/enrich", "POST") is EnrichNewsResponse


@pytest.mark.asyncio
async def test_get_etf_news_rejects_a_malformed_symbol_without_network():
    resp = await etfs.get_etf_news("bad!!symbol", limit=50)
    assert isinstance(resp, JSONResponse)
    body = json.loads(resp.body)
    assert body["error_code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_enrich_etf_news_rejects_a_malformed_symbol_without_network():
    resp = await etfs.enrich_etf_news("bad!!", {"article_ids": ["x"]})
    assert isinstance(resp, JSONResponse)
    assert json.loads(resp.body)["error_code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_enrich_etf_news_requires_a_non_empty_id_list():
    resp = await etfs.enrich_etf_news("SPY", {"article_ids": []})
    assert isinstance(resp, JSONResponse)
    assert json.loads(resp.body)["error_code"] == "INVALID_INPUT"


@pytest.mark.asyncio
async def test_enrich_etf_news_short_circuits_placeholder_ids_without_network():
    # Every id is a client-side placeholder → nothing enrichable → empty result,
    # and crucially the news/Gemini service is never called.
    resp = await etfs.enrich_etf_news("SPY", {"article_ids": ["temp_1", "raw_2", "sample_3"]})
    assert isinstance(resp, EnrichNewsResponse)
    assert resp.ticker == "SPY"
    assert resp.articles == []
