"""F5 (2026-09-11, Railway): a PERMANENT, by-design FMP 402 is not news.

`sp500/dowjones/nasdaq-constituent` are the "Indexes" package, which is not on the Order
Form; `_raise_if_not_entitled` refuses them before any HTTP call. `index_service` used to
call anyway and log two WARNINGs per index per pre-warm cycle (6 per half hour in prod).
Now it consults the manifest first, announces the licence gap ONCE per process at INFO, and
takes the profile fallback. A genuinely NEW failure on an entitled path must still WARN.
"""

from __future__ import annotations

import logging

import pytest

import app.services.index_service as idx
from app.integrations.fmp import FMPClient, FMPNotEntitledException
from app.integrations.fmp_entitlements import INDEX_CONSTITUENT_PATHS, is_entitled


class _NeverCalled:
    async def get_index_constituents(self, symbol):
        raise AssertionError("the constituents endpoint must not be called for an unlicensed package")


def _service(fmp):
    svc = idx.IndexService.__new__(idx.IndexService)
    svc.fmp = fmp
    return svc


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    idx._cache.clear()
    idx._CONSTITUENTS_UNENTITLED_LOGGED.clear()
    monkeypatch.setattr(idx.IndexService, "_tier2_get", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(idx.IndexService, "_tier2_put", staticmethod(lambda *a, **k: None))


def test_the_manifest_still_says_the_constituent_paths_are_unlicensed():
    """If this ever flips (the package was bought), the gate below turns itself off — that is
    the design, but the test above it must be re-read, so pin the premise."""
    assert set(INDEX_CONSTITUENT_PATHS) == {"^GSPC", "^DJI", "^IXIC"}
    assert all(not is_entitled(p) for p in INDEX_CONSTITUENT_PATHS.values())


@pytest.mark.asyncio
async def test_unlicensed_constituents_are_skipped_with_one_info_per_process(caplog):
    svc = _service(_NeverCalled())
    with caplog.at_level(logging.INFO):
        assert await svc._get_constituent_count("^GSPC", fallback=503) == 503
        assert await svc._get_constituent_count("^GSPC", fallback=503) == 503
        assert await svc._get_constituent_count("^DJI", fallback=30) == 30
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warnings, [r.getMessage() for r in warnings]
    infos = [r for r in caplog.records if "outside the FMP licence" in r.getMessage()]
    assert len(infos) == 2, "one INFO per index symbol per process"
    assert "^GSPC" in infos[0].getMessage() and "sp500-constituent" in infos[0].getMessage()


@pytest.mark.asyncio
async def test_a_new_failure_on_an_entitled_path_still_warns(monkeypatch, caplog):
    """Regression visibility: silence is for the KNOWN gap only."""
    monkeypatch.setattr(idx, "is_entitled", lambda p: True)

    class _Boom:
        async def get_index_constituents(self, symbol):
            raise RuntimeError("upstream 500")

    with caplog.at_level(logging.INFO):
        assert await _service(_Boom())._get_constituent_count("^GSPC", fallback=503) == 503
    assert any(r.levelno == logging.WARNING and "RuntimeError" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_an_entitled_count_is_still_cached(monkeypatch):
    monkeypatch.setattr(idx, "is_entitled", lambda p: True)

    class _Rows:
        calls = 0

        async def get_index_constituents(self, symbol):
            type(self).calls += 1
            return [{"symbol": f"S{i}"} for i in range(503)]

    svc = _service(_Rows())
    assert await svc._get_constituent_count("^GSPC", fallback=0) == 503
    assert await svc._get_constituent_count("^GSPC", fallback=0) == 503
    assert _Rows.calls == 1


# ── the second permanent-402 log source: transcript dates ───────────────────────────


@pytest.mark.asyncio
async def test_transcript_dates_entitlement_refusal_is_not_a_warning(monkeypatch, caplog):
    async def _refuse(self, endpoint, params=None, **_):
        raise FMPNotEntitledException("'earning-call-transcript-dates' needs the FMP 'Earnings Call Transcripts' package")

    monkeypatch.setattr(FMPClient, "_make_request", _refuse)
    client = FMPClient.__new__(FMPClient)
    with caplog.at_level(logging.DEBUG, logger="app.integrations.fmp"):
        out = await client.get_earning_call_transcript("AAPL")
    assert out == ""
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_transcript_dates_real_failure_still_warns(monkeypatch, caplog):
    async def _boom(self, endpoint, params=None, **_):
        raise RuntimeError("upstream 503")

    monkeypatch.setattr(FMPClient, "_make_request", _boom)
    client = FMPClient.__new__(FMPClient)
    with caplog.at_level(logging.DEBUG, logger="app.integrations.fmp"):
        assert await client.get_earning_call_transcript("AAPL") == ""
    assert any(r.levelno == logging.WARNING and "transcript-dates failed" in r.getMessage()
               for r in caplog.records)
