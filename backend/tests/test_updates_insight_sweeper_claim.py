"""
The sweeper's claim path — the AUTHORITATIVE half of the per-scope daily cap.

The cap is enforced twice: advisorily in the pure gate (`updates_materiality.
decide`) and authoritatively in Postgres by `claim_updates_insight_scope`, whose
`p_daily_cap` argument is supplied here. Nothing covered that argument, so
raising `PER_SCOPE_DAILY_CAP_MARKET` to 16 in the gate while `_claim` still sent
a flat 6 shipped with a fully green suite: the gate admitted `__MARKET__` at
6/16, the RPC matched no row, `_run` returned False with nothing logged and no
state row written, and the scope re-tripped every 5 minutes forever while the
one diagnostic that explained the freeze (`last_skip_reason = 'daily_cap'`)
vanished.

These tests exist so the two ceilings can never silently diverge again.
"""

from datetime import datetime, timezone

import pytest

from app.services.updates_insight_sweeper import InsightSweeper, MARKET_SCOPE
from app.services.updates_materiality import (
    PER_SCOPE_ATTEMPT_CAP,
    PER_SCOPE_DAILY_CAP,
    PER_SCOPE_DAILY_CAP_MARKET,
    daily_cap_for,
)

NOW = datetime(2026, 7, 21, 18, 0, tzinfo=timezone.utc)


class _RPCResult:
    def __init__(self, data):
        self.data = data


class _RecordingRPC:
    """Captures every rpc() payload and returns a scripted grant."""

    def __init__(self, granted=True):
        self.calls = []
        self.granted = granted

    def rpc(self, name, params):
        self.calls.append((name, params))
        outer = self

        class _Exec:
            def execute(self_inner):
                return _RPCResult(outer.granted)

        return _Exec()

    @property
    def last_params(self):
        return self.calls[-1][1]


class _StubSweeper(InsightSweeper):
    """InsightSweeper with no network clients (see .claude/rules/testing.md)."""

    def __init__(self, supabase):
        self.supabase = supabase
        self.fmp = None
        self.news = None
        self.insights = None


@pytest.fixture
def rpc():
    return _RecordingRPC()


def test_market_scope_claims_against_the_market_cap(rpc):
    sweeper = _StubSweeper(rpc)
    assert sweeper._claim(MARKET_SCOPE, NOW, is_market_scope=True) is True
    assert rpc.calls[0][0] == "claim_updates_insight_scope"
    assert rpc.last_params["p_daily_cap"] == PER_SCOPE_DAILY_CAP_MARKET
    assert rpc.last_params["p_scope"] == MARKET_SCOPE


def test_ticker_scope_claims_against_the_ticker_cap(rpc):
    sweeper = _StubSweeper(rpc)
    assert sweeper._claim("AAPL", NOW, is_market_scope=False) is True
    assert rpc.last_params["p_daily_cap"] == PER_SCOPE_DAILY_CAP


@pytest.mark.parametrize("is_market", [True, False])
def test_the_claim_cap_is_exactly_what_the_gate_used(rpc, is_market):
    """The load-bearing invariant: both ceilings come from one function.

    If these ever diverge, the gate says GENERATE and the database says no,
    which is invisible from the state row.
    """
    sweeper = _StubSweeper(rpc)
    sweeper._claim("X", NOW, is_market_scope=is_market)
    assert rpc.last_params["p_daily_cap"] == daily_cap_for(is_market)


def test_claim_sends_the_attempt_cap_and_a_fresh_aware_timestamp(rpc):
    sweeper = _StubSweeper(rpc)
    before = datetime.now(timezone.utc)
    sweeper._claim("AAPL", NOW, is_market_scope=False)
    after = datetime.now(timezone.utc)
    assert rpc.last_params["p_attempt_cap"] == PER_SCOPE_ATTEMPT_CAP
    # p_now is stamped + stale-evaluated with a FRESH real-time UTC instant, NOT
    # the passed sweep-start `now`: reusing the stale start time made a just-taken
    # claim look already-stale to a second instance → duplicate paid generation.
    # Still tz-aware so Postgres keys the ET trading day off a real UTC instant.
    p_now = rpc.last_params["p_now"]
    assert p_now.endswith("+00:00")
    parsed = datetime.fromisoformat(p_now)
    assert before <= parsed <= after        # fresh, NOT the fixed sweep-start NOW
    assert parsed != NOW                     # explicitly not the passed timestamp


@pytest.mark.parametrize("payload,expected", [
    (True, True),
    (False, False),
    ([True], True),
    ([False], False),
    ([], False),          # RETURNING matched no row
    (None, False),        # RPC returned NULL
])
def test_claim_normalizes_every_postgrest_return_shape(payload, expected):
    # PostgREST wraps scalar RETURNS in a list in some versions and not others.
    # Treating a bare [] as truthy would double-bill a Gemini call.
    sweeper = _StubSweeper(_RecordingRPC(granted=payload))
    assert sweeper._claim("AAPL", NOW, is_market_scope=False) is expected


def test_claim_fails_closed_when_the_rpc_raises():
    class _Boom:
        def rpc(self, name, params):
            raise RuntimeError("relation does not exist")

    sweeper = _StubSweeper(_Boom())
    # Fail CLOSED: without a claim we might double-bill across instances.
    # Skipping costs at most one 5-minute cycle.
    assert sweeper._claim("AAPL", NOW, is_market_scope=False) is False


def test_a_denied_claim_is_logged_not_swallowed(rpc, caplog):
    """A denial on the paid path must never be silent — it is the difference
    between 'capped as designed' and 'the two caps disagree', and no state row
    is written on this path to record it."""
    sweeper = _StubSweeper(_RecordingRPC(granted=False))
    with caplog.at_level("INFO", logger="app.services.updates_insight_sweeper"):
        assert sweeper._claim(MARKET_SCOPE, NOW, is_market_scope=True) is False
    # caplog.text renders the %-args, so this asserts the FORMATTED line — the
    # scope and the cap that bound must both be greppable without a repro.
    assert MARKET_SCOPE in caplog.text
    assert f"daily_cap={PER_SCOPE_DAILY_CAP_MARKET}" in caplog.text


def test_a_granted_claim_logs_nothing(rpc):
    """The denial log must not become per-cycle noise on the happy path."""
    sweeper = _StubSweeper(rpc)
    assert sweeper._claim("AAPL", NOW, is_market_scope=False) is True
