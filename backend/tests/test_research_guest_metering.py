"""`POST /research/generate` meters guests, and analytics can never 503.

--- 1. Deep research was completely unmetered for guests ---

`if not is_guest: credit_service.precharge(...)` skipped the charge for signed-out callers,
and the only remaining gate was `StandardRateLimit` at 60/min — on the most expensive
operation in the product (Stage A's 4-round FMP tool calling plus ~15 Stage-B narratives:
~17 Gemini + ~20 FMP calls per run). One install could start a full agent run every second,
indefinitely, for free, by cycling tickers so neither `ticker_report_cache` nor the
`(ticker, persona)` dedup could absorb it.

It also inverted the funnel exactly as `GET /stocks/{ticker}/report` did before its own fix:
signing in took you from UNLIMITED deep research to a metered allowance. Both paths now claim
against the same per-install `guest_report_budget` (migration 106), so a guest's free report is
one allowance across the product rather than one per endpoint.

--- 2. Analytics could 503 and silently destroy the batch ---

`analytics.py` promises in its own docstring that it "can never break the app", but it depended
on `get_current_user_or_guest`, which reads `public.users` and deliberately raises 503 when
that read fails. The iOS `Analytics` actor removes events from its buffer BEFORE the request
and does not re-queue on failure — so a Supabase blip silently destroyed the telemetry that
exists to detect Supabase blips. It now resolves identity from the token alone.

No network / Supabase.
"""
from __future__ import annotations

import json

import pytest

from app.api.error_response import ErrorCode
from app.api.v1.endpoints import research
from app.dependencies import GUEST_USER_ID, get_identity_only_user


class _Budget:
    """Stand-in for the guest budget service. `claimed`: 1 = granted, -1 = cap reached."""

    def __init__(self, claimed=1, raises=False):
        self.claimed, self.raises, self.calls = claimed, raises, []
        self.released = []

    def current_period(self):
        return "2026-08-01"

    def release_report(self, bucket, period):
        self.released.append((bucket, period))
        return 0

    def try_claim_report(self, bucket):
        self.calls.append(bucket)
        if self.raises:
            from app.services.guest_report_budget_service import GuestReportBudgetUnavailable
            raise GuestReportBudgetUnavailable("rpc down")
        return self.claimed


class _Q:
    def __init__(self, count):
        self._count = count

    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def in_(self, *_a, **_k): return self
    def gte(self, *_a, **_k): return self
    def insert(self, *_a, **_k): return self

    def execute(self):
        return type("R", (), {"data": [], "count": self._count})()


class _SB:
    def __init__(self, count=0):
        self._count = count

    def table(self, _n):
        return _Q(self._count)


def _req():
    from app.schemas.research import GenerateResearchRequest
    return GenerateResearchRequest(stock_id="AAPL", investor_persona="warren_buffett")


async def _generate(monkeypatch, budget, user_id=GUEST_USER_ID, supabase=None):
    monkeypatch.setattr(research, "get_guest_report_budget_service", lambda: budget)
    return await research.generate_research_report(
        request=_req(),
        user={"id": user_id},
        supabase=supabase or _SB(),
        x_guest_id="install-A",
        _rate_limit=None,
    )


@pytest.mark.asyncio
async def test_guest_over_the_allowance_is_refused(monkeypatch):
    """THE denial-of-wallet case: past the monthly allowance, no agent run starts."""
    budget = _Budget(claimed=-1)
    resp = await _generate(monkeypatch, budget)
    body = json.loads(resp.body)
    assert body["error_code"] == ErrorCode.INSUFFICIENT_CREDITS.value
    assert body["details"]["guest"] is True
    assert len(budget.calls) == 1, "the allowance must be claimed exactly once per request"


@pytest.mark.asyncio
async def test_the_claim_is_keyed_per_install_not_shared(monkeypatch):
    """A shared key would let one guest exhaust the allowance for every other guest."""
    budget = _Budget(claimed=-1)
    await _generate(monkeypatch, budget)
    assert budget.calls, "the guest budget was never consulted"
    assert budget.calls[0] != GUEST_USER_ID, (
        "claimed against the shared guest sentinel — every install would share one allowance"
    )


@pytest.mark.asyncio
async def test_budget_outage_fails_OPEN(monkeypatch):
    """Consistent with the report path: a Supabase blip must not wall users out of the
    headline feature — the rate limit and concurrency caps still bound the damage."""
    budget = _Budget(raises=True)
    resp = await _generate(monkeypatch, budget)
    body = json.loads(resp.body) if hasattr(resp, "body") else {}
    assert body.get("error_code") != ErrorCode.INSUFFICIENT_CREDITS.value, (
        "a budget-service outage must not be reported to the user as 'out of credits'"
    )


@pytest.mark.asyncio
async def test_signed_in_users_do_not_touch_the_guest_budget(monkeypatch):
    """The guest allowance is for guests; an account is metered by credits."""
    budget = _Budget(claimed=1)
    from unittest.mock import MagicMock

    credit = MagicMock()
    credit.precharge.return_value = 100
    # Replace the CLASS, not just the instance: the handler reads the class attribute
    # `CreditService.DEEP_RESEARCH_COST` as well as calling `CreditService()`.
    credit_cls = MagicMock(return_value=credit)
    credit_cls.DEEP_RESEARCH_COST = 20
    monkeypatch.setattr(research, "CreditService", credit_cls)

    await _generate(monkeypatch, budget, user_id="real-user-1")
    assert budget.calls == [], "a signed-in user consumed the guest allowance"
    credit.precharge.assert_called_once()


@pytest.mark.asyncio
async def test_persona_validation_still_precedes_any_claim(monkeypatch):
    """A caller error must never burn the allowance."""
    budget = _Budget(claimed=1)
    monkeypatch.setattr(research, "get_guest_report_budget_service", lambda: budget)
    from app.schemas.research import GenerateResearchRequest
    resp = await research.generate_research_report(
        request=GenerateResearchRequest(stock_id="AAPL", investor_persona="not_a_persona"),
        user={"id": GUEST_USER_ID}, supabase=_SB(), x_guest_id="install-A", _rate_limit=None,
    )
    assert json.loads(resp.body)["error_code"] == ErrorCode.INVALID_PERSONA.value
    assert budget.calls == [], "an invalid persona consumed the guest allowance"


# ---------------------------------------------------------------------------
# Analytics identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_identity_only_user_never_touches_the_database():
    """It takes no supabase argument at all — the 503 path is structurally unreachable."""
    import inspect
    params = inspect.signature(get_identity_only_user).parameters
    assert "supabase" not in params, (
        "the analytics identity still depends on a database read, so a Supabase blip can "
        "503 the ingest endpoint and destroy the client's already-drained batch"
    )


@pytest.mark.asyncio
async def test_identity_only_user_degrades_to_guest_on_a_bad_token():
    out = await get_identity_only_user(authorization="Bearer not.a.jwt")
    assert out["id"] == GUEST_USER_ID


@pytest.mark.asyncio
async def test_identity_only_user_resolves_a_real_token():
    from app.core.security import create_access_token
    token = create_access_token({"sub": "user-123", "email": "a@b.c"})
    out = await get_identity_only_user(authorization=f"Bearer {token}")
    assert out["id"] == "user-123"


@pytest.mark.asyncio
async def test_identity_only_user_rejects_a_refresh_token():
    """The same exchange-only guard the data endpoints apply."""
    from app.core.security import create_refresh_token
    token = create_refresh_token({"sub": "user-123"})
    out = await get_identity_only_user(authorization=f"Bearer {token}")
    assert out["id"] == GUEST_USER_ID


def test_analytics_uses_the_non_raising_identity():
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "analytics.py"
    ).read_text(encoding="utf-8")
    assert "get_identity_only_user" in src
    assert "Depends(get_current_user_or_guest)" not in src, (
        "analytics still depends on the raising identity"
    )
