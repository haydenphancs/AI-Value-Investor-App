"""The generalised dispatcher: per-category caps, group masters, quiet-hours deferral.

`test_push_dispatch.py` pins the properties that predate the registry (claim-before-send,
fail-open reads, bounded fan-out). This file pins what the overhaul added, and every
assertion here corresponds to a way the old single-kind dispatcher would have gone wrong
once a second kind existed:

  * one global 3/day cap → an earnings reminder starves a price move;
  * no group master → turning off "Smart Money" silences nothing;
  * ET-only day rolls → a Tokyo user's daily budget resets at 2pm;
  * drop-on-quiet-hours → the notification is simply lost, with no inbox record.

No Supabase, no APNs.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.services import quiet_hours as qh
from app.services.notification_kinds import (
    KIND_EARNINGS_UPCOMING,
    KIND_INSIDER_TRADE,
    KIND_PRICE_ALERT,
    KIND_RESEARCH_COMPLETE,
    KIND_TICKER_MOVE,
    get_kind,
)
from app.services.push_dispatch_service import (
    STATE_DEFERRED,
    STATE_DRY_RUN,
    STATE_NO_DEVICE,
    STATE_SENT,
    PushDispatchService,
    _Recipient,
)

ET = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")
NOON_ET = datetime(2026, 8, 7, 12, 0, tzinfo=ET).astimezone(timezone.utc)
NIGHT_ET = datetime(2026, 8, 7, 23, 30, tzinfo=ET).astimezone(timezone.utc)


class _FakePush:
    def __init__(self, enabled=True, accepted=1):
        self.enabled = enabled
        self._accepted = accepted
        self.calls = []

    async def send_to_user(self, user_id, **kw):
        self.calls.append({"user_id": user_id, **kw})
        return self._accepted


def _svc(push=None):
    svc = object.__new__(PushDispatchService)
    svc._push = push or _FakePush()
    svc.supabase = None
    return svc


def _recipient(**kw):
    kw.setdefault("user_id", "u1")
    kw.setdefault("devices", [{"token": "tok", "environment": "sandbox"}])
    return _Recipient(**kw)


# ── the decision ladder ──────────────────────────────────────────────────────

def test_an_untouched_preference_uses_the_kinds_declared_default():
    svc = _svc()
    on = svc.decide(_recipient(preferences={}), get_kind(KIND_TICKER_MOVE), NOON_ET)
    assert on.send is True and on.reason == "ok"

    # whale_13f ships OFF — 13F data is up to 45 days stale and one filing touches
    # dozens of positions, so it is the easiest signal in the app to over-send.
    off = svc.decide(_recipient(preferences={}), get_kind("whale_13f"), NOON_ET)
    assert off.send is False and off.reason.startswith("preference_off:")


def test_the_group_master_can_silence_a_child_that_is_individually_on():
    """Turning off 'Smart Money' must silence all three children even though each keeps
    its own key. Without the AND, the group toggle would be decorative."""
    svc = _svc()
    kind = get_kind(KIND_INSIDER_TRADE)
    prefs = {"notify_smart_money_insider": True, "notify_smart_money": False}
    d = svc.decide(_recipient(preferences=prefs), kind, NOON_ET)
    assert d.send is False
    assert d.reason == "preference_off:notify_smart_money"


def test_a_child_off_is_honoured_even_when_the_master_is_on():
    svc = _svc()
    prefs = {"notify_smart_money_insider": False, "notify_smart_money": True}
    d = svc.decide(_recipient(preferences=prefs), get_kind(KIND_INSIDER_TRADE), NOON_ET)
    assert d.reason == "preference_off:notify_smart_money_insider"


@pytest.mark.parametrize("stored", ["false", "0", "off", "no", 0])
def test_a_string_typed_off_is_not_truthiness_cast_back_on(stored):
    """`bool("false")` is True. Nothing between here and the database enforces a
    preference's type, so a string-typed toggle from another client or a hand-edited row
    would silently RE-ENABLE a notification the user turned off."""
    svc = _svc()
    d = svc.decide(
        _recipient(preferences={"notify_watchlist_changes": stored}),
        get_kind(KIND_TICKER_MOVE), NOON_ET,
    )
    assert d.send is False


def test_an_unreadable_preference_type_falls_back_to_the_declared_default():
    svc = _svc()
    d = svc.decide(
        _recipient(preferences={"notify_watchlist_changes": {"nested": 1}}),
        get_kind(KIND_TICKER_MOVE), NOON_ET,
    )
    assert d.send is True   # ticker_move defaults ON


# ── per-category caps ────────────────────────────────────────────────────────

def test_the_cap_is_per_category_not_global(monkeypatch):
    """THE point of the refactor. Before this, `alerts_sent_today` counted every row, so
    four earnings reminders would silently consume the whole 3/day budget and the user's
    watchlist price alerts went dark for the rest of the day."""
    monkeypatch.setattr(settings, "PUSH_MAX_ALERTS_PER_USER_PER_DAY", 3)
    svc = _svc()

    # Three earnings notifications already sent today (its cap is 4).
    r = _recipient(category_sent_today=3)
    assert svc.decide(r, get_kind(KIND_EARNINGS_UPCOMING), NOON_ET).send is True

    # The SAME count against the watchlist category (cap 3) is at the ceiling.
    assert svc.decide(r, get_kind(KIND_TICKER_MOVE), NOON_ET).send is False


def test_report_ready_is_never_capped():
    """The user pressed Generate and paid credits seconds ago. Capping the answer to a
    request they just made is indistinguishable from the feature being broken."""
    svc = _svc()
    r = _recipient(category_sent_today=999)
    d = svc.decide(r, get_kind(KIND_RESEARCH_COMPLETE), NOON_ET)
    assert d.send is True


def test_the_cap_reason_names_the_category_and_the_number(monkeypatch):
    """'Why didn't I get my alert?' has to be answerable from the log alone."""
    monkeypatch.setattr(settings, "PUSH_MAX_ALERTS_PER_USER_PER_DAY", 3)
    svc = _svc()
    d = svc.decide(_recipient(category_sent_today=3), get_kind(KIND_TICKER_MOVE), NOON_ET)
    assert d.reason == "cap_reached:watchlist:3"


def test_the_legacy_env_knob_still_moves_the_watchlist_cap(monkeypatch):
    monkeypatch.setattr(settings, "PUSH_MAX_ALERTS_PER_USER_PER_DAY", 10)
    svc = _svc()
    assert svc.decide(_recipient(category_sent_today=5), get_kind(KIND_TICKER_MOVE), NOON_ET).send


# ── quiet hours ──────────────────────────────────────────────────────────────

_QUIET = {
    qh.PREF_QUIET_ENABLED: True,
    qh.PREF_QUIET_START: "22:00",
    qh.PREF_QUIET_END: "07:00",
    qh.PREF_TIMEZONE: "America/New_York",
}


def test_quiet_hours_defer_rather_than_suppress():
    """A dropped notification is gone; a deferred one still writes its ledger row, so
    the in-app inbox has it immediately and only the buzz waits."""
    svc = _svc()
    d = svc.decide(_recipient(preferences=_QUIET), get_kind(KIND_TICKER_MOVE), NIGHT_ET)
    assert d.send is False
    assert d.reason == "quiet_hours"
    assert d.deliver_after is not None
    assert d.deliver_after.astimezone(ET).hour == 7


def test_outside_the_window_nothing_is_deferred():
    svc = _svc()
    d = svc.decide(_recipient(preferences=_QUIET), get_kind(KIND_TICKER_MOVE), NOON_ET)
    assert d.send is True and d.deliver_after is None


@pytest.mark.parametrize("kind_key", [KIND_RESEARCH_COMPLETE, KIND_PRICE_ALERT])
def test_user_initiated_kinds_ignore_quiet_hours(kind_key):
    """Both answer something the user explicitly asked for. Holding 'your report is
    ready' until 07:00 is worse than not sending it, and a price alert that arrives
    after the move is over is worthless."""
    svc = _svc()
    d = svc.decide(_recipient(preferences=_QUIET), get_kind(kind_key), NIGHT_ET)
    assert d.send is True


def test_quiet_hours_are_evaluated_in_the_USERS_timezone():
    """23:30 ET is 12:30 the next day in Tokyo — the middle of their afternoon."""
    svc = _svc()
    tokyo_prefs = {**_QUIET, qh.PREF_TIMEZONE: "Asia/Tokyo"}
    assert svc.decide(_recipient(preferences=_QUIET), get_kind(KIND_TICKER_MOVE), NIGHT_ET).send is False
    assert svc.decide(_recipient(preferences=tokyo_prefs), get_kind(KIND_TICKER_MOVE), NIGHT_ET).send is True


def test_broken_quiet_hours_math_fails_to_NOT_QUIET(monkeypatch):
    """A bug in the wraparound must not silently queue every notification forever. The
    per-category caps still bound the volume, so failing open here is survivable in a
    way that failing closed is not."""
    def _boom(*a, **k):
        raise RuntimeError("wraparound exploded")

    monkeypatch.setattr(qh, "is_within", _boom)
    svc = _svc()
    d = svc.decide(_recipient(preferences=_QUIET), get_kind(KIND_TICKER_MOVE), NIGHT_ET)
    assert d.send is True


def test_an_unusable_timezone_still_produces_a_decision():
    svc = _svc()
    prefs = {**_QUIET, qh.PREF_TIMEZONE: "Mars/Olympus"}
    d = svc.decide(_recipient(preferences=prefs), get_kind(KIND_TICKER_MOVE), NIGHT_ET)
    # Fell back to ET, where 23:30 IS quiet.
    assert d.reason == "quiet_hours"


# ── delivery ─────────────────────────────────────────────────────────────────

def _wire(svc, *, claims=None, states=None, recipients):
    svc.resolve_recipients = lambda ids, kind, now: {r.user_id: r for r in recipients}
    svc.claim_send = lambda uid, key, **kw: (claims.append((uid, key, kw)) or True) if claims is not None else True
    svc.mark_state = lambda uid, key, state, **kw: (
        states.append((uid, state, kw)) if states is not None else None
    )


@pytest.mark.asyncio
async def test_a_delivered_notification_carries_the_registry_shaping():
    push = _FakePush()
    svc = _svc(push)
    _wire(svc, recipients=[_recipient(preferences={})])

    sent = await svc.notify_users(
        ["u1"], kind=KIND_INSIDER_TRADE, title="T", body="B",
        dedup_key="k", route={"ticker": "NVDA"}, now=NOON_ET,
    )
    assert sent == 1
    call = push.calls[0]
    # `passive` lets iOS batch for battery — right for a Form 4, wrong for a price alert.
    assert call["interruption_level"] == "passive"
    assert call["thread_id"] == "smart_money"
    # The kind travels in the payload so the iOS router can pick a destination.
    assert call["data"]["kind"] == KIND_INSIDER_TRADE
    assert call["data"]["ticker"] == "NVDA"
    # Badge is server-computed: incrementing client-side drifts the moment a
    # notification is delivered and never opened.
    assert call["badge"] == 1
    # Tokens are passed through from the batched read, not re-fetched per recipient.
    assert call["devices"] == [{"token": "tok", "environment": "sandbox"}]


@pytest.mark.asyncio
async def test_the_badge_reflects_existing_unread_plus_this_one():
    push = _FakePush()
    svc = _svc(push)
    _wire(svc, recipients=[_recipient(preferences={}, unread=4)])
    await svc.notify_users(["u1"], kind=KIND_TICKER_MOVE, title="T", body="B",
                           dedup_key="k", now=NOON_ET)
    assert push.calls[0]["badge"] == 5


@pytest.mark.asyncio
async def test_a_user_with_no_device_still_gets_an_inbox_row():
    """A signed-in user who denied the iOS permission has no token. That is not an
    error, and the ledger row is the whole point of writing it before delivery."""
    push = _FakePush()
    svc = _svc(push)
    states = []
    _wire(svc, states=states, recipients=[_recipient(preferences={}, devices=[])])

    sent = await svc.notify_users(["u1"], kind=KIND_TICKER_MOVE, title="T", body="B",
                                  dedup_key="k", now=NOON_ET)
    assert sent == 0
    assert push.calls == []
    assert states == [("u1", STATE_NO_DEVICE, {"sent": False})]


@pytest.mark.asyncio
async def test_dry_run_exercises_everything_except_the_apns_post(monkeypatch):
    """The verification backbone: with no Apple configuration and no device, a sender is
    still fully exercisable and its ledger row is the evidence."""
    monkeypatch.setattr(settings, "PUSH_DRY_RUN", True)
    push = _FakePush(enabled=False)     # APNs deliberately NOT configured
    svc = _svc(push)
    claims, states = [], []
    _wire(svc, claims=claims, states=states, recipients=[_recipient(preferences={})])

    sent = await svc.notify_users(["u1"], kind=KIND_TICKER_MOVE, title="T", body="B",
                                  dedup_key="k", now=NOON_ET)
    assert sent == 0                        # nothing was really delivered
    assert push.calls == []                 # ...and APNs was never touched
    assert len(claims) == 1                 # but the claim DID happen
    assert states == [("u1", STATE_DRY_RUN, {"sent": False})]


@pytest.mark.asyncio
async def test_with_apns_unconfigured_and_no_dry_run_nothing_is_claimed():
    """The state before the APNs key is set. Claiming here would burn dedup slots for
    notifications that were never sent, permanently suppressing them."""
    svc = _svc(_FakePush(enabled=False))
    claims = []
    _wire(svc, claims=claims, recipients=[_recipient(preferences={})])
    assert await svc.notify_users(["u1"], kind=KIND_TICKER_MOVE, title="T", body="B",
                                  dedup_key="k", now=NOON_ET) == 0
    assert claims == []


@pytest.mark.asyncio
async def test_a_deferred_notification_is_claimed_and_parked_not_sent():
    """Claim-first makes the deferral idempotent: a second trigger of the same event
    finds the row already claimed instead of queueing a duplicate."""
    push = _FakePush()
    svc = _svc(push)
    claims = []
    _wire(svc, claims=claims, recipients=[_recipient(preferences=_QUIET)])

    sent = await svc.notify_users(["u1"], kind=KIND_TICKER_MOVE, title="T", body="B",
                                  dedup_key="k", now=NIGHT_ET)
    assert sent == 0
    assert push.calls == []
    assert len(claims) == 1
    assert claims[0][2]["push_state"] == STATE_DEFERRED
    assert claims[0][2]["deliver_after"] is not None


@pytest.mark.asyncio
async def test_a_suppressed_notification_never_burns_the_dedup_slot(monkeypatch):
    """Preference-off and cap-reached must NOT claim. Claiming for someone we will not
    message consumes that key and silently suppresses a LATER alert they did want."""
    monkeypatch.setattr(settings, "PUSH_MAX_ALERTS_PER_USER_PER_DAY", 1)
    svc = _svc()
    claims = []
    _wire(svc, claims=claims, recipients=[
        _recipient(user_id="off", preferences={"notify_watchlist_changes": False}),
        _recipient(user_id="capped", preferences={}, category_sent_today=9),
    ])
    await svc.notify_users(["off", "capped"], kind=KIND_TICKER_MOVE, title="T",
                           body="B", dedup_key="k", now=NOON_ET)
    assert claims == []


@pytest.mark.asyncio
async def test_a_per_user_dedup_key_callable_is_supported():
    """A price alert's key is its own row id; a market event's is shared. One primitive
    has to serve both."""
    svc = _svc()
    claims = []
    _wire(svc, claims=claims, recipients=[
        _recipient(user_id="a", preferences={}), _recipient(user_id="b", preferences={}),
    ])
    await svc.notify_users(["a", "b"], kind=KIND_TICKER_MOVE, title="T", body="B",
                           dedup_key=lambda uid: f"pa:{uid}", now=NOON_ET)
    assert [c[1] for c in claims] == ["pa:a", "pa:b"]


@pytest.mark.asyncio
async def test_notify_users_never_raises_even_when_the_registry_lookup_fails():
    """It is called from inside sender jobs and the sweeper's generation path, where an
    escape would mark successful work as failed."""
    svc = _svc()
    assert await svc.notify_users(["u1"], kind="not_a_registered_kind", title="T",
                                  body="B", dedup_key="k") == 0


@pytest.mark.asyncio
async def test_an_empty_audience_is_a_silent_noop():
    svc = _svc()
    assert await svc.notify_users([], kind=KIND_TICKER_MOVE, title="T", body="B",
                                  dedup_key="k") == 0


@pytest.mark.asyncio
async def test_duplicate_user_ids_are_collapsed():
    """The smart-money sender unions two audiences (ticker watchers + whale followers).
    A user in both must get ONE notification, and the union is de-duplicated here rather
    than trusted to the dedup claim."""
    push = _FakePush()
    svc = _svc(push)
    _wire(svc, recipients=[_recipient(user_id="u1", preferences={})])
    sent = await svc.notify_users(["u1", "u1", "u1"], kind=KIND_TICKER_MOVE, title="T",
                                  body="B", dedup_key="k", now=NOON_ET)
    assert sent == 1
    assert len(push.calls) == 1


# ── collapse-id ──────────────────────────────────────────────────────────────

async def _capture_apns(monkeypatch, **send_kwargs) -> dict:
    """Drive the real `PushService.send_to_user` and capture the APNs request.

    `enabled` is a property computed from the four APNS_* settings, so it is turned on
    the way production does it rather than by patching the property object.
    """
    import app.services.push_service as ps
    from app.services.push_service import PushService

    for name, value in (
        ("APNS_KEY_ID", "KEY123"), ("APNS_TEAM_ID", "TEAM123"),
        ("APNS_AUTH_KEY", "-----PEM-----"), ("APNS_BUNDLE_ID", "com.phan.caydex"),
    ):
        monkeypatch.setattr(settings, name, value)

    captured: dict = {}

    class _Resp:
        status_code = 200
        text = ""

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _Resp()

    monkeypatch.setattr(ps.httpx, "AsyncClient", lambda **kw: _Client())

    svc = PushService()
    monkeypatch.setattr(svc, "_provider_jwt", lambda: "jwt")
    await svc.send_to_user(
        "u1", title="t", body="b",
        devices=[{"token": "tok", "environment": "sandbox"}],
        **send_kwargs,
    )
    return captured


@pytest.mark.asyncio
async def test_a_long_collapse_id_is_truncated_to_apns_64_byte_limit(monkeypatch):
    """APNs 400s on a longer apns-collapse-id, which would drop the whole send. Measured
    in BYTES, not characters — a multi-byte company name slips past a len() check."""
    captured = await _capture_apns(
        monkeypatch,
        collapse_id="ü" * 100,           # 200 bytes
        interruption_level="passive",
        thread_id="smart_money",
    )
    cid = captured["headers"]["apns-collapse-id"]
    assert len(cid.encode("utf-8")) <= 64
    # A truncation that split a multi-byte sequence would leave a replacement char.
    assert "�" not in cid
    # passive => batchable delivery priority
    assert captured["headers"]["apns-priority"] == "5"
    assert captured["json"]["aps"]["interruption-level"] == "passive"
    assert captured["json"]["aps"]["thread-id"] == "smart_money"


@pytest.mark.asyncio
async def test_a_non_passive_kind_keeps_immediate_delivery_priority(monkeypatch):
    captured = await _capture_apns(
        monkeypatch, interruption_level="time-sensitive", thread_id="price_alert",
    )
    assert captured["headers"]["apns-priority"] == "10"


@pytest.mark.asyncio
async def test_a_sandbox_token_is_routed_to_the_sandbox_host(monkeypatch):
    """A DEBUG build mints a sandbox token; sending it to the production host yields
    BadDeviceToken for the whole fleet of dev devices."""
    captured = await _capture_apns(monkeypatch, interruption_level="active")
    assert captured["url"].startswith("https://api.sandbox.push.apple.com/3/device/")


@pytest.mark.asyncio
async def test_omitting_the_shaping_arguments_produces_the_original_payload(monkeypatch):
    """Byte-compatibility for any caller that has not moved onto the registry."""
    captured = await _capture_apns(monkeypatch)
    assert captured["json"]["aps"] == {
        "alert": {"title": "t", "body": "b"}, "sound": "default",
    }
    assert "apns-collapse-id" not in captured["headers"]


@pytest.mark.asyncio
async def test_a_zero_badge_is_sent_because_it_CLEARS_the_badge(monkeypatch):
    """`badge: 0` is meaningful, so the guard tests for None rather than falsiness."""
    captured = await _capture_apns(monkeypatch, badge=0)
    assert captured["json"]["aps"]["badge"] == 0


# ── the flush loop ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_row_deferred_past_the_max_window_is_failed_not_sent(monkeypatch):
    """A 14-hour-late 'AAPL moved 8%' is misinformation, not a notification. The INBOX
    row survives, so the information is not lost — only the buzz."""
    monkeypatch.setattr(settings, "NOTIFICATION_MAX_DEFER_HOURS", 12)
    push = _FakePush()
    svc = _svc(push)
    stale = (datetime.now(timezone.utc) - timedelta(hours=20)).isoformat()
    svc._claim_due = lambda limit: [{
        "user_id": "u1", "dedup_key": "k", "kind": KIND_TICKER_MOVE,
        "title": "T", "body": "B", "route": {}, "claimed_at": stale,
    }]
    svc._devices_bulk = lambda ids: {"u1": [{"token": "t", "environment": "sandbox"}]}
    svc.unread_counts_bulk = lambda ids: {"u1": 0}
    states = []
    svc.mark_state = lambda uid, key, state, **kw: states.append((state, kw.get("error")))

    stats = await svc.flush_deferred()
    assert stats["stale"] == 1 and stats["sent"] == 0
    assert push.calls == []
    assert states[0][0] == "failed" and "stale" in states[0][1]


@pytest.mark.asyncio
async def test_a_fresh_deferred_row_is_delivered_with_its_stored_payload():
    push = _FakePush()
    svc = _svc(push)
    svc._claim_due = lambda limit: [{
        "user_id": "u1", "dedup_key": "k", "kind": KIND_TICKER_MOVE,
        "title": "NVDA", "body": "moved 8%", "route": {"ticker": "NVDA"},
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }]
    svc._devices_bulk = lambda ids: {"u1": [{"token": "t", "environment": "sandbox"}]}
    svc.unread_counts_bulk = lambda ids: {"u1": 2}
    svc.mark_state = lambda *a, **k: None

    stats = await svc.flush_deferred()
    assert stats["sent"] == 1
    assert push.calls[0]["title"] == "NVDA"
    assert push.calls[0]["data"]["ticker"] == "NVDA"
    assert push.calls[0]["badge"] == 3


@pytest.mark.asyncio
async def test_a_parked_row_whose_kind_was_removed_fails_loudly():
    """Guessing a preference key here would buzz a user who had opted out."""
    push = _FakePush()
    svc = _svc(push)
    svc._claim_due = lambda limit: [{
        "user_id": "u1", "dedup_key": "k", "kind": "deleted_kind",
        "title": "T", "body": "B", "route": {},
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }]
    svc._devices_bulk = lambda ids: {"u1": []}
    svc.unread_counts_bulk = lambda ids: {"u1": 0}
    states = []
    svc.mark_state = lambda uid, key, state, **kw: states.append((state, kw.get("error")))

    stats = await svc.flush_deferred()
    assert stats["failed"] == 1 and push.calls == []
    assert "unknown kind" in states[0][1]


@pytest.mark.asyncio
async def test_an_empty_flush_is_cheap_and_silent():
    svc = _svc()
    svc._claim_due = lambda limit: []
    assert (await svc.flush_deferred())["claimed"] == 0


@pytest.mark.asyncio
async def test_the_flush_re_checks_the_daily_cap(monkeypatch):
    """THE back door. `sent_at` is stamped only on real delivery, so a deferred row was
    never charged against the cap. Five alerts parked at 22:30 during an after-hours
    selloff would ALL fire at 07:00 — the exact 'ten notifications in one morning'
    failure the cap exists to prevent, arriving through the flush instead of the claim.
    """
    monkeypatch.setattr(settings, "PUSH_MAX_ALERTS_PER_USER_PER_DAY", 2)
    push = _FakePush()
    svc = _svc(push)
    fresh = datetime.now(timezone.utc).isoformat()
    svc._claim_due = lambda limit: [
        {"user_id": "u1", "dedup_key": f"k{i}", "kind": KIND_TICKER_MOVE,
         "title": "T", "body": "B", "route": {}, "claimed_at": fresh}
        for i in range(5)
    ]
    svc._devices_bulk = lambda ids: {"u1": [{"token": "t", "environment": "sandbox"}]}
    svc.unread_counts_bulk = lambda ids: {"u1": 0}
    svc._preferences_bulk = lambda ids: {"u1": {}}
    svc.alerts_sent_today = lambda uid, category=None: 0
    states = []
    svc.mark_state = lambda uid, key, state, **kw: states.append((state, kw.get("error")))

    stats = await svc.flush_deferred()
    assert stats["sent"] == 2, "the cap must bound a single flush batch"
    assert stats["suppressed"] == 3
    assert len(push.calls) == 2
    assert all("cap_reached" in (e or "") for s, e in states if s == "failed")


@pytest.mark.asyncio
async def test_the_flush_re_checks_the_preference():
    """A user who turns the category off during their quiet hours must not be buzzed
    when the window ends — the decision that parked the row is hours old."""
    push = _FakePush()
    svc = _svc(push)
    svc._claim_due = lambda limit: [{
        "user_id": "u1", "dedup_key": "k", "kind": KIND_TICKER_MOVE,
        "title": "T", "body": "B", "route": {},
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }]
    svc._devices_bulk = lambda ids: {"u1": [{"token": "t", "environment": "sandbox"}]}
    svc.unread_counts_bulk = lambda ids: {"u1": 0}
    svc._preferences_bulk = lambda ids: {"u1": {"notify_watchlist_changes": False}}
    svc.alerts_sent_today = lambda uid, category=None: 0
    states = []
    svc.mark_state = lambda uid, key, state, **kw: states.append((state, kw.get("error")))

    stats = await svc.flush_deferred()
    assert stats["sent"] == 0 and stats["suppressed"] == 1
    assert push.calls == []
    assert "preference_off" in states[0][1]


@pytest.mark.asyncio
async def test_the_flush_does_NOT_re_check_quiet_hours():
    """Re-asking would re-defer forever on a window that spans the flush. The row is
    being flushed precisely because its window ended."""
    push = _FakePush()
    svc = _svc(push)
    svc._claim_due = lambda limit: [{
        "user_id": "u1", "dedup_key": "k", "kind": KIND_TICKER_MOVE,
        "title": "T", "body": "B", "route": {},
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }]
    svc._devices_bulk = lambda ids: {"u1": [{"token": "t", "environment": "sandbox"}]}
    svc.unread_counts_bulk = lambda ids: {"u1": 0}
    # A window covering essentially the whole day — "still quiet" by any reading.
    svc._preferences_bulk = lambda ids: {"u1": {
        qh.PREF_QUIET_ENABLED: True,
        qh.PREF_QUIET_START: "00:01",
        qh.PREF_QUIET_END: "23:59",
        qh.PREF_TIMEZONE: "America/New_York",
    }}
    svc.alerts_sent_today = lambda uid, category=None: 0
    svc.mark_state = lambda *a, **k: None

    stats = await svc.flush_deferred()
    assert stats["sent"] == 1, "a flushed row must not be re-deferred"
