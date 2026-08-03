"""`POST /research/generate` is ACCOUNT-ONLY, and analytics can never 503.

HISTORY — why this file exists. Deep research was completely unmetered for guests, then was
metered per install against `guest_report_budget` (migration 106). That allowance keyed on
`identity_key(user, x_guest_id)`, a UUID5 of a header the CLIENT chooses, so rotating it bought
a fresh allowance every request and the most expensive call in the product (~17 Gemini + ~20 FMP)
stayed effectively free. Generation now requires an account and is metered by credits, which are
FK-bound to a real `public.users` row and cannot be rotated. The guest-allowance tests are gone
with the branch they covered; `tests/test_research_guest_partition.py` pins the account-only rule.

What remains here is the analytics carve-out, which is unchanged and load-bearing.

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
