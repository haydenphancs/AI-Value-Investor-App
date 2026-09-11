"""The Crypto Fear & Greed gauge never paints a reading nobody measured.

`compute_fear_greed_summary([])` returned value 50 / "Neutral" for current, 7d and 30d —
byte-identical to a real neutral reading — and `get_fear_greed_index` cached an empty
upstream answer for 15 minutes and returned `[]` on a cold-cache failure. So one
Alternative.me blip painted a confident "Neutral 50" gauge on every crypto screen for a
quarter of an hour. The endpoint now answers 502 (iOS hides the gauge), the empty answer
is never cached, and a malformed entry is skipped rather than 502-ing a good set.
"""
from __future__ import annotations

import asyncio

import pytest

from app.integrations import alternative_me as am


@pytest.fixture(autouse=True)
def _cold_cache():
    am._cache, am._cache_ts = None, 0.0
    yield
    am._cache, am._cache_ts = None, 0.0


def test_an_empty_set_is_refused_not_summarised_as_neutral():
    with pytest.raises(am.FearGreedUnavailableException):
        am.compute_fear_greed_summary([])


def test_a_malformed_entry_is_skipped_not_fatal():
    out = am.compute_fear_greed_summary([
        {"value": "not-a-number", "value_classification": "?", "timestamp": "1"},
        {"value": "61", "value_classification": "Greed", "timestamp": "2"},
    ])
    assert out["value"] == 61 and out["classification"] == "Greed"
    assert len(out["history"]) == 1


def test_all_malformed_is_refused():
    with pytest.raises(am.FearGreedUnavailableException):
        am.compute_fear_greed_summary([{"value": None}, {"value": "abc"}, {"value": 150}])


def test_averages_are_over_what_exists_not_padded_with_50():
    out = am.compute_fear_greed_summary([{"value": "80", "timestamp": "1"}, {"value": "70", "timestamp": "2"}])
    assert out["value_7d"] == 75 and out["value_30d"] == 75


class _Resp:
    def __init__(self, payload=None, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        return self.resp


@pytest.mark.asyncio
async def test_an_empty_upstream_answer_is_not_cached_and_raises(monkeypatch):
    monkeypatch.setattr(am.httpx, "AsyncClient", lambda timeout=None: _Client(_Resp({"data": []})))
    with pytest.raises(am.FearGreedUnavailableException):
        await am.get_fear_greed_index(limit=30)
    assert am._cache is None, "an empty answer was pinned for the TTL"


@pytest.mark.asyncio
async def test_a_cold_cache_failure_raises_instead_of_returning_nothing(monkeypatch):
    monkeypatch.setattr(am.httpx, "AsyncClient", lambda timeout=None: _Client(_Resp(status=503)))
    with pytest.raises(am.FearGreedUnavailableException):
        await am.get_fear_greed_index(limit=30)


@pytest.mark.asyncio
async def test_a_warm_cache_still_serves_through_a_failure(monkeypatch):
    am._cache, am._cache_ts = [{"value": "55", "value_classification": "Greed", "timestamp": "1"}], 0.0
    monkeypatch.setattr(am.httpx, "AsyncClient", lambda timeout=None: _Client(_Resp(status=503)))
    assert (await am.get_fear_greed_index(limit=30))[0]["value"] == "55"


@pytest.mark.asyncio
async def test_the_endpoint_answers_502_not_a_fabricated_reading(monkeypatch):
    from fastapi import HTTPException
    import app.api.v1.endpoints.crypto as ep

    monkeypatch.setattr(am.httpx, "AsyncClient", lambda timeout=None: _Client(_Resp({"data": []})))
    with pytest.raises(HTTPException) as info:
        await ep.get_crypto_fear_greed()
    assert info.value.status_code == 502
