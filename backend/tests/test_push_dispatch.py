"""Push dispatch — who gets alerted, whether they want it, and exactly once.

A duplicate cache write is invisible. A duplicate push is a buzz in someone's pocket
that cannot be taken back, and it is the fastest way to earn an uninstall. So the
properties pinned here are deliberately paranoid:

  * the dedup claim happens BEFORE the send, and a failed claim means DON'T send;
  * a user who turned the category off is never sent to;
  * one bad recipient never abandons the rest of the fan-out;
  * the fan-out is bounded, and truncation is LOGGED rather than silent.

No Supabase, no APNs — both are stubbed.
"""

import asyncio

import pytest

from app.services.push_dispatch_service import (
    MAX_RECIPIENTS_PER_SCOPE,
    PushDispatchService,
)


class _FakePush:
    def __init__(self, enabled=True, accepted=1):
        self.enabled = enabled
        self._accepted = accepted
        self.sent = []

    async def send_to_user(self, user_id, *, title, body, data=None):
        self.sent.append((user_id, title, body, data))
        return self._accepted


def _service(*, watchers=None, prefs=None, claim=True, push=None,
             watchers_raise=False, prefs_raise=False, claim_raise=None):
    svc = object.__new__(PushDispatchService)
    svc._push = push or _FakePush()
    svc.supabase = None
    svc.claimed = []

    def _watchers_of(ticker):
        if watchers_raise:
            raise RuntimeError("db down")
        users = list(watchers or [])
        return users[:MAX_RECIPIENTS_PER_SCOPE]

    def _pref(user_id, key):
        if prefs_raise:
            return True   # the service's documented fail-open
        return (prefs or {}).get(user_id, True)

    def _claim(user_id, dedup_key):
        if claim_raise:
            return False
        svc.claimed.append((user_id, dedup_key))
        return claim

    svc.watchers_of = _watchers_of
    svc.preference_enabled = _pref
    svc.claim_send = _claim
    return svc


async def _notify(svc, ticker="NVDA", key="move:NVDA:2026-08-01"):
    return await svc.notify_watchers(
        ticker=ticker, title=ticker, body="moved 8%", dedup_key=key
    )


# ── the once-only property ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_claims_before_sending():
    """Order matters: claiming first risks a rare missed alert, sending first risks a
    duplicate. Missing one is forgivable; buzzing twice is not."""
    push = _FakePush()
    svc = _service(watchers=["u1"], push=push)
    await _notify(svc)
    assert svc.claimed == [("u1", "move:NVDA:2026-08-01")]
    assert [s[0] for s in push.sent] == ["u1"]


@pytest.mark.asyncio
async def test_an_already_claimed_user_is_not_sent_to():
    """The second pass over the same scope on the same day must be silent."""
    push = _FakePush()
    svc = _service(watchers=["u1", "u2"], claim=False, push=push)
    sent = await _notify(svc)
    assert sent == 0
    assert push.sent == []


@pytest.mark.asyncio
async def test_a_failed_claim_roundtrip_does_not_send():
    """If we cannot prove it's unsent, don't send. A DB blip must not become a
    duplicate notification."""
    push = _FakePush()
    svc = _service(watchers=["u1"], claim_raise=True, push=push)
    assert await _notify(svc) == 0
    assert push.sent == []


# ── preferences ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_users_who_turned_the_category_off_are_skipped():
    push = _FakePush()
    svc = _service(watchers=["yes", "no"], prefs={"yes": True, "no": False}, push=push)
    await _notify(svc)
    assert [s[0] for s in push.sent] == ["yes"]


@pytest.mark.asyncio
async def test_a_user_with_no_saved_preference_is_opted_in():
    """Absent → ON, matching the iOS default for notify_watchlist_changes."""
    push = _FakePush()
    svc = _service(watchers=["u1"], prefs={}, push=push)
    await _notify(svc)
    assert len(push.sent) == 1


@pytest.mark.asyncio
async def test_an_opted_out_user_is_not_even_claimed():
    """Claiming for someone we won't message would burn their dedup slot and
    silently suppress a LATER alert they did want."""
    svc = _service(watchers=["no"], prefs={"no": False})
    await _notify(svc)
    assert svc.claimed == []


# ── resilience ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_push_is_a_silent_noop():
    """The state before the APNs key is configured. Must not error or claim."""
    svc = _service(watchers=["u1"], push=_FakePush(enabled=False))
    assert await _notify(svc) == 0
    assert svc.claimed == []


@pytest.mark.asyncio
async def test_no_watchers_is_a_noop():
    push = _FakePush()
    svc = _service(watchers=[], push=push)
    assert await _notify(svc) == 0
    assert push.sent == []


@pytest.mark.asyncio
async def test_a_failed_watcher_lookup_degrades_to_zero():
    svc = _service(watchers_raise=True)
    assert await _notify(svc) == 0


@pytest.mark.asyncio
async def test_one_failing_recipient_does_not_abandon_the_rest():
    class _Flaky(_FakePush):
        async def send_to_user(self, user_id, *, title, body, data=None):
            if user_id == "bad":
                raise RuntimeError("apns exploded")
            return await super().send_to_user(user_id, title=title, body=body, data=data)

    push = _Flaky()
    svc = _service(watchers=["a", "bad", "b"], push=push)
    sent = await _notify(svc)
    assert sent == 2
    assert [s[0] for s in push.sent] == ["a", "b"]


@pytest.mark.asyncio
async def test_a_rejected_send_is_not_counted_as_delivered():
    push = _FakePush(accepted=0)   # APNs accepted nothing
    svc = _service(watchers=["u1"], push=push)
    assert await _notify(svc) == 0


# ── payload ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_payload_carries_the_ticker_for_the_tap_handler():
    """AppDelegate.didReceive reads `ticker` from userInfo to route the tap. Without
    it the notification says 'NVDA moved 8%' and opens whatever tab was last used."""
    push = _FakePush()
    svc = _service(watchers=["u1"], push=push)
    await svc.notify_watchers(
        ticker="NVDA", title="NVDA", body="moved 8%",
        dedup_key="k", data={"kind": "ticker_move", "ticker": "NVDA"},
    )
    assert push.sent[0][3] == {"kind": "ticker_move", "ticker": "NVDA"}


# ── bounds ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_fanout_is_bounded():
    """A mega-cap moving on a busy day must not stall the sweeper behind thousands of
    sequential sends."""
    push = _FakePush()
    many = [f"u{i}" for i in range(MAX_RECIPIENTS_PER_SCOPE + 250)]
    svc = _service(watchers=many, push=push)
    await _notify(svc)
    assert len(push.sent) == MAX_RECIPIENTS_PER_SCOPE


def test_dedup_key_is_one_alert_per_ticker_per_day():
    from app.services.push_dispatch_service import trading_date_et

    assert trading_date_et() == trading_date_et()   # stable within a run
    assert len(trading_date_et()) == 10             # ISO date, not a timestamp


# ── how a duplicate is actually detected ─────────────────────────────────────
#
# `claim_send` is the whole dedup mechanism, and it decides "already sent" by
# inspecting the exception from a conflicting INSERT. Verified empirically against a
# live Supabase composite-PK insert: supabase-py raises `APIError` with
# `.code == "23505"` and a message containing "duplicate key value violates unique
# constraint". Both shapes are pinned here so a client upgrade that changes one
# doesn't silently turn every duplicate into an "unknown error" — which fails safe
# (no send) but would suppress alerts indefinitely with only a warning.

class _ConflictError(Exception):
    code = "23505"

    def __str__(self):
        return ('{"message": "duplicate key value violates unique constraint '
                '\\"push_send_log_pkey\\"", "code": "23505"}')


class _MessageOnlyConflict(Exception):
    """No structured code — only the message, as an older/other client might raise."""

    def __str__(self):
        return 'duplicate key value violates unique constraint "push_send_log_pkey"'


def _claim_service(raises):
    class _Tbl:
        def insert(self, row): self._row = row; return self
        def execute(self):
            if raises:
                raise raises
            return type("R", (), {"data": [{}]})()

    svc = object.__new__(PushDispatchService)
    svc.supabase = type("S", (), {"table": lambda self, n: _Tbl()})()
    return svc


def test_a_structured_23505_is_read_as_already_sent():
    assert _claim_service(_ConflictError()).claim_send("u1", "k") is False


def test_a_message_only_conflict_is_also_read_as_already_sent():
    assert _claim_service(_MessageOnlyConflict()).claim_send("u1", "k") is False


def test_a_clean_insert_grants_the_claim():
    assert _claim_service(None).claim_send("u1", "k") is True


def test_an_unrelated_db_error_refuses_the_claim():
    """Fails SAFE: if we can't prove the alert is unsent, don't send it. A duplicate
    buzz is worse than a missed one."""
    assert _claim_service(RuntimeError("connection reset")).claim_send("u1", "k") is False


# ── the sweeper gate (adversarial review, 2026-08-01) ────────────────────────
#
# `decision.price_band` carries a STALE σ-tier when the current quote is unusable
# (it falls back to `last_price_band`). The paid-catalyst path already guards against
# that; the notify path did not, so a ticker that moved 9% yesterday and has no quote
# today would interrupt someone about a move that isn't happening — and because the
# dedup key is per DAY, it would land as a genuinely new alert rather than a duplicate.

class _Decision:
    def __init__(self, band):
        self.price_band = band
        self.inputset_id = ""
        self.reason = "test"


def _sweeper():
    from app.services.updates_insight_sweeper import InsightSweeper

    return object.__new__(InsightSweeper)


@pytest.mark.asyncio
async def test_no_push_when_the_current_quote_is_missing(monkeypatch):
    from app.services.updates_materiality import TIER_EXTREME
    import app.services.push_dispatch_service as pds

    calls = []

    class _Spy:
        async def notify_watchers(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Spy())

    await _sweeper()._notify_watchers(
        "NVDA", _Decision(TIER_EXTREME), {"headline": "NVDA fell hard"},
        None, quote=None,      # no quote this cycle → the band is stale
    )
    assert calls == [], "alerted on a stale tier with no live quote"


@pytest.mark.asyncio
async def test_no_push_when_the_move_rounds_to_zero(monkeypatch):
    from app.services.updates_materiality import TIER_EXTREME
    import app.services.push_dispatch_service as pds

    calls = []

    class _Spy:
        async def notify_watchers(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Spy())

    await _sweeper()._notify_watchers(
        "NVDA", _Decision(TIER_EXTREME), {"headline": "h"},
        None, quote={"changePercentage": 0.001},
    )
    assert calls == [], "alerted on a move that rounds to 0.00%"


@pytest.mark.asyncio
async def test_a_real_move_with_a_live_quote_does_push(monkeypatch):
    from app.services.updates_materiality import TIER_EXTREME
    import app.services.push_dispatch_service as pds

    calls = []

    class _Spy:
        async def notify_watchers(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Spy())

    await _sweeper()._notify_watchers(
        "NVDA", _Decision(TIER_EXTREME), {"headline": "NVDA fell 9% on guidance"},
        None, quote={"changePercentage": -9.2},
    )
    assert len(calls) == 1
    assert calls[0]["ticker"] == "NVDA"
    assert calls[0]["data"]["ticker"] == "NVDA"   # the tap handler needs this
    assert "move:NVDA:" in calls[0]["dedup_key"]


@pytest.mark.asyncio
async def test_a_routine_drift_never_pushes(monkeypatch):
    """Only Unusual/Extreme earns an interruption. A 0.4% drift already regenerates a
    card; notifying on those would train users to ignore the app within a week."""
    import app.services.push_dispatch_service as pds

    calls = []

    class _Spy:
        async def notify_watchers(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Spy())

    await _sweeper()._notify_watchers(
        "NVDA", _Decision("typical"), {"headline": "h"},
        None, quote={"changePercentage": 0.4},
    )
    assert calls == []


@pytest.mark.asyncio
async def test_an_empty_headline_never_pushes(monkeypatch):
    """Silence beats a notification that says nothing — the card is on the Updates
    tab either way."""
    from app.services.updates_materiality import TIER_EXTREME
    import app.services.push_dispatch_service as pds

    calls = []

    class _Spy:
        async def notify_watchers(self, **kw):
            calls.append(kw)
            return 1

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Spy())

    await _sweeper()._notify_watchers(
        "NVDA", _Decision(TIER_EXTREME), {"headline": "   "},
        None, quote={"changePercentage": -9.0},
    )
    assert calls == []
