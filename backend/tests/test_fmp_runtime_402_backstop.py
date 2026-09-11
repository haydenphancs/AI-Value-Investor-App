"""Three entitlement-boundary gaps, each the recurring "prediction ≠ oracle" shape.

1. A LIVE 402 the manifest did not predict. `_raise_if_not_entitled` is a prediction
   from `fmp_entitlements`; FMP's answer is the oracle. When the prediction missed, a
   402 fell to `raise_for_status()`, was logged at ERROR on every call and classified
   FMP_UNAVAILABLE (503, "try again shortly", iOS Retry) — a permanent refusal dressed as
   a transient one, with Sentry noise per tap. Now: `FMPNotEntitledException`, one
   WARNING per endpoint naming the symbol, so the manifest can be corrected.

2. `classify_exception` matches class-name SUBSTRINGS. "fmppartialpageexception" does
   not contain "fmpexception", so a lost page of a paginated fetch fell through to
   REPORT_GENERATION_FAILED (502) instead of FMP_UNAVAILABLE.

3. `asset_class._COMMODITY_SYMBOLS` (the SESSION authority) and
   `fmp_entitlements.BLOCKED_COMMODITY_SYMBOLS` (the LICENCE authority) had drifted by
   three codes, so `price_service.get_quote("LBUSD")` skipped the licence short-circuit
   and went out to FMP's `profile`.

Hermetic: the HTTP client is a stub; no network.
"""
from __future__ import annotations

import logging

import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.integrations import fmp as fmp_mod
from app.integrations.fmp import (
    FMPClient,
    FMPNotEntitledException,
    FMPPartialPageException,
    FMPUnavailableException,
)
from app.integrations.fmp_entitlements import BLOCKED_COMMODITY_SYMBOLS, is_blocked_symbol
from app.services.asset_class import _COMMODITY_SYMBOLS


# ── 1. runtime 402 backstop ──────────────────────────────────────────────────

class _Resp:
    def __init__(self, status: int, payload=None):
        self.status_code = status
        self.headers = {}
        self._payload = payload if payload is not None else {"Error Message": "Restricted Endpoint"}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            req = httpx.Request("GET", "https://financialmodelingprep.com/stable/x")
            raise httpx.HTTPStatusError("boom", request=req, response=httpx.Response(self.status_code, request=req))


class _Client:
    def __init__(self, status: int):
        self.status, self.calls = status, 0

    async def get(self, url, params=None):
        self.calls += 1
        return _Resp(self.status)


@pytest.fixture
def client(monkeypatch):
    c = FMPClient()
    FMPClient._unpredicted_402.clear()
    stub = _Client(402)

    async def _get_client():
        return stub

    monkeypatch.setattr(c, "_get_client", _get_client)
    c.request_failures = 0
    yield c, stub
    FMPClient._unpredicted_402.clear()


@pytest.mark.asyncio
async def test_an_unpredicted_402_raises_the_contractual_exception(client):
    c, stub = client
    with pytest.raises(FMPNotEntitledException) as info:
        await c._make_request("profile", {"symbol": "OMUSD"})     # entitled PATH, unmapped symbol
    assert "402" in str(info.value)
    assert stub.calls == 1, "a 402 must not be retried"
    assert not isinstance(info.value, FMPUnavailableException)


@pytest.mark.asyncio
async def test_an_unpredicted_402_is_classified_not_entitled_not_unavailable(client):
    c, _ = client
    try:
        await c._make_request("profile", {"symbol": "OMUSD"})
    except FMPNotEntitledException as e:
        code, status = classify_exception(e)
    assert code is ErrorCode.FMP_NOT_ENTITLED and status == 409


@pytest.mark.asyncio
async def test_an_unpredicted_402_logs_a_warning_once_per_endpoint_naming_the_symbol(client, caplog):
    c, _ = client
    with caplog.at_level(logging.WARNING, logger=fmp_mod.logger.name):
        for _ in range(3):
            with pytest.raises(FMPNotEntitledException):
                await c._make_request("profile", {"symbol": "OMUSD"})
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "402" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert "OMUSD" in warnings[0].getMessage()
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors == [], "a permanent refusal must not page on-call"


@pytest.mark.asyncio
async def test_a_402_is_still_counted_as_a_request_failure(client):
    """`request_failures` feeds the health signal; a refusal after the guard passed IS a
    failed upstream call, unlike a pre-flight refusal (pinned elsewhere)."""
    c, _ = client
    with pytest.raises(FMPNotEntitledException):
        await c._make_request("profile", {"symbol": "OMUSD"})
    assert c.request_failures == 1


# ── 2. classification substring miss ─────────────────────────────────────────

def test_a_partial_page_exception_is_classified_as_fmp_unavailable():
    exc = FMPPartialPageException(
        "senate-latest: 1/4 pages failed; 300 row(s) arrived but the set is INCOMPLETE",
        endpoint="senate-latest", pages_total=4, pages_failed=1,
    )
    assert isinstance(exc, FMPUnavailableException), "precondition: it IS an unavailability"
    code, status = classify_exception(exc)
    assert code is ErrorCode.FMP_UNAVAILABLE, code
    assert status == 502


def test_the_substring_rule_still_covers_the_base_classes():
    assert classify_exception(FMPUnavailableException("x"))[0] is ErrorCode.FMP_UNAVAILABLE
    assert classify_exception(FMPNotEntitledException("x"))[0] is ErrorCode.FMP_NOT_ENTITLED


# ── 3. the two commodity authorities agree ───────────────────────────────────

def test_every_commodity_the_classifier_knows_is_blocked_by_the_licence_guard():
    drift = sorted(_COMMODITY_SYMBOLS - BLOCKED_COMMODITY_SYMBOLS)
    assert drift == [], f"commodity codes the classifier calls 'commodity' but FMP is asked for: {drift}"


@pytest.mark.parametrize("sym", sorted(_COMMODITY_SYMBOLS))
def test_every_commodity_code_is_a_blocked_symbol(sym):
    assert is_blocked_symbol(sym) is True


@pytest.mark.asyncio
async def test_a_five_char_commodity_code_never_reaches_fmp_profile(monkeypatch):
    """The user-visible consequence: `get_quote` short-circuits to {} like GCUSD.

    `get_quote` resolves the client through `price_service.get_fmp_client` at call time,
    so that binding is what gets patched. The AAPL control proves the patch is live —
    without it a swallowed `NetworkCallInTests` would make this pass for the wrong reason.
    """
    from app.services import price_service as ps

    class _FMP:
        def __init__(self):
            self.calls = []

        async def get_company_profile(self, symbol):
            self.calls.append(symbol)
            return [{"symbol": symbol, "price": 1.0, "change": 0.1, "changePercentage": 1.0}]

    fake = _FMP()
    monkeypatch.setattr(ps, "get_fmp_client", lambda: fake)
    svc = ps.PriceService()
    ps._cache.clear()

    assert (await svc.get_quote("AAPL")).get("price") == 1.0     # control: patch is live
    assert fake.calls == ["AAPL"]

    assert await svc.get_quote("LBUSD") == {}
    assert fake.calls == ["AAPL"], "LBUSD went out to FMP"


@pytest.mark.asyncio
async def test_a_second_unpredicted_symbol_on_the_same_path_is_still_named(client, caplog):
    """Keyed per (path, symbol): the first refused symbol must not silence the next."""
    c, _ = client
    with caplog.at_level(logging.WARNING, logger=fmp_mod.logger.name):
        for sym in ("OMUSD", "OMUSD", "NEWCOMMODITYUSD"):
            with pytest.raises(FMPNotEntitledException):
                await c._make_request("profile", {"symbol": sym})
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "402" in r.getMessage()]
    assert len(warnings) == 2, warnings
    assert "OMUSD" in warnings[0] and "NEWCOMMODITYUSD" in warnings[1]
