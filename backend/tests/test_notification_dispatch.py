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

import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.push_service import PushOutcome

from app.config import settings
from app.services import quiet_hours as qh
from app.services.notification_senders import earnings_live_sender
from app.services.notification_kinds import (
    KIND_EARNINGS_LIVE,
    KIND_EARNINGS_RESULT,
    KIND_EARNINGS_UPCOMING,
    KIND_INSIDER_TRADE,
    KIND_PRICE_ALERT,
    KIND_RESEARCH_COMPLETE,
    KIND_TICKER_MOVE,
    NOTIFICATION_KINDS,
    category_cap,
    get_kind,
)
from app.services.push_dispatch_service import (
    STATE_DEFERRED,
    STATE_DRY_RUN,
    STATE_FAILED,
    STATE_NO_DEVICE,
    STATE_PENDING,
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
        # Mirrors `PushService.send_to_user`'s `PushOutcome` return, INCLUDING its `enabled`
        # check — see the note on the double in test_push_dispatch.py.
        if not self.enabled:
            return PushOutcome(
                attempted=0, accepted=0,
                failures=("APNs is not configured on this server",),
            )
        return PushOutcome(attempted=self._accepted, accepted=self._accepted)


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


# ── the night hold ───────────────────────────────────────────────────────────
#
# Owner decision 2026-11-05, "Hold until 7 AM". An earnings result used to go out only from the
# 16:00 ET run; sent live, a fill FMP folds at 02:30 ET would buzz a default-settings watcher in
# bed. So these two kinds keep the default night for anyone who never set Quiet Hours, on that
# user's own clock. Everything else about quiet hours stays: the row is parked and the inbox has
# it now. A toggle the user did flip to off is their answer, and no other kind changes.

NIGHT_KINDS = (KIND_EARNINGS_RESULT, KIND_EARNINGS_LIVE)
NY_ONLY = {qh.PREF_TIMEZONE: "America/New_York"}
SMALL_HOURS_ET = datetime(2026, 10, 30, 2, 30, tzinfo=ET).astimezone(timezone.utc)


def _et(hour, minute=0):
    return datetime(2026, 10, 30, hour, minute, tzinfo=ET).astimezone(timezone.utc)


def test_earnings_results_wait_for_morning_when_quiet_hours_were_never_set():
    svc = _svc()
    night = datetime(2026, 10, 30, 2, 30, tzinfo=ET).astimezone(timezone.utc)
    noon = datetime(2026, 10, 30, 12, 0, tzinfo=ET).astimezone(timezone.utc)
    for key in ("earnings_result", "earnings_live"):
        d = svc.decide(_recipient(preferences={qh.PREF_TIMEZONE: "America/New_York"}), get_kind(key), night)
        assert (d.send, d.reason) == (False, "quiet_hours")
        assert d.deliver_after.astimezone(ET).hour == 7
    # The user's own clock: 15:30 in Tokyo is daytime.
    tokyo = {qh.PREF_TIMEZONE: "Asia/Tokyo"}
    assert svc.decide(_recipient(preferences=tokyo), get_kind("earnings_result"), night).send is True
    # An explicit Quiet Hours off is honoured.
    off = {qh.PREF_QUIET_ENABLED: False}
    assert svc.decide(_recipient(preferences=off), get_kind("earnings_result"), night).send is True
    # Other kinds and daytime are unchanged.
    assert svc.decide(_recipient(preferences={}), get_kind(KIND_TICKER_MOVE), night).send is True
    assert svc.decide(_recipient(preferences={}), get_kind("earnings_result"), noon).send is True


@pytest.mark.parametrize("key", NIGHT_KINDS)
@pytest.mark.parametrize("hour,minute,held", [
    (21, 59, False), (22, 0, True), (6, 59, True), (7, 0, False),
])
def test_the_hold_runs_from_22_00_up_to_but_not_including_07_00(key, hour, minute, held):
    """Half-open, like every quiet window: a result landing at exactly 07:00 goes out."""
    d = _svc().decide(_recipient(preferences=NY_ONLY), get_kind(key), _et(hour, minute))
    assert d.send is (not held)
    assert (d.deliver_after is not None) is held


@pytest.mark.parametrize("zone", [
    "Asia/Tokyo", "Europe/Berlin", "Asia/Kolkata", "Australia/Sydney", "America/Denver",
])
def test_the_hold_ends_at_7_on_the_users_own_clock(zone):
    tz = ZoneInfo(zone)
    late = datetime(2026, 10, 29, 23, 30, tzinfo=tz).astimezone(timezone.utc)
    d = _svc().decide(
        _recipient(preferences={qh.PREF_TIMEZONE: zone}), get_kind(KIND_EARNINGS_RESULT), late,
    )
    wake = d.deliver_after.astimezone(tz)
    assert d.reason == "quiet_hours"
    assert (wake.date(), wake.hour, wake.minute) == (date(2026, 10, 30), 7, 0)


def test_the_hold_keeps_the_wall_clock_across_a_daylight_saving_change():
    """Clocks go back at 02:00 on Nov 1 2026 in New York, so that night is 10 hours long."""
    late = datetime(2026, 10, 31, 22, 30, tzinfo=ET).astimezone(timezone.utc)
    d = _svc().decide(_recipient(preferences=NY_ONLY), get_kind(KIND_EARNINGS_LIVE), late)
    assert d.deliver_after == datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    assert d.deliver_after.astimezone(ET).hour == 7


@pytest.mark.parametrize("prefs", [
    {}, {qh.PREF_TIMEZONE: "Mars/Olympus"}, {qh.PREF_TIMEZONE: ""}, {qh.PREF_TIMEZONE: 5},
], ids=["absent", "unknown", "blank", "wrong_type"])
def test_with_no_usable_timezone_the_hold_runs_on_ET(prefs):
    d = _svc().decide(_recipient(preferences=prefs), get_kind(KIND_EARNINGS_RESULT), SMALL_HOURS_ET)
    assert d.reason == "quiet_hours"
    assert d.deliver_after == datetime(2026, 10, 30, 11, 0, tzinfo=timezone.utc)


def test_a_failed_preference_read_errs_toward_a_late_buzz():
    """The read failed, so nothing says this user ever turned Quiet Hours off. A hold costs them
    a few hours and the flush re-reads their settings; a wrong buzz wakes them."""
    svc = _svc()
    svc._preferences_bulk = lambda ids: {"u1": None}
    svc._devices_bulk = lambda ids: {}
    svc.unread_counts_bulk = lambda ids: {}
    svc._category_counts_bulk = lambda ids, category, cutoffs: {}
    kind = get_kind(KIND_EARNINGS_RESULT)

    recipient = svc.resolve_recipients(["u1"], kind, SMALL_HOURS_ET)["u1"]
    assert recipient.preferences_known is False
    d = svc.decide(recipient, kind, SMALL_HOURS_ET)
    assert d.reason == "quiet_hours"
    assert d.deliver_after == datetime(2026, 10, 30, 11, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("key", NIGHT_KINDS)
@pytest.mark.parametrize("hour,minute", [(7, 0), (12, 0), (16, 5), (21, 59)])
def test_a_failed_read_in_ets_daytime_waits_for_the_flush_instead_of_buzzing(key, hour, minute):
    """ET's day is someone's night: 16:05 ET is 22:05 in Berlin and 06:05 in Tokyo. With no blob
    nothing says which clock this user is on, so the row is parked for the next flush, which reads
    the settings again and judges it on the user's own clock: a minute late at worst, never a buzz
    at 22:05 in Berlin."""
    now = _et(hour, minute)
    d = _svc().decide(_recipient(preferences={}, preferences_known=False), get_kind(key), now)
    assert (d.send, d.reason, d.deliver_after) == (False, "preferences_unread", now)


@pytest.mark.parametrize("key", NIGHT_KINDS)
def test_a_failed_read_in_ets_own_night_still_waits_for_ets_morning(key):
    """Parked for the flush instead, a row would go out at 23:31 in New York the moment a second
    read failed too. So ET's night still parks it to ET's 07:00, where a good read holds it again
    for anyone further west."""
    d = _svc().decide(_recipient(preferences={}, preferences_known=False), get_kind(key), _et(23, 30))
    assert (d.reason, d.deliver_after) == (
        "quiet_hours", datetime(2026, 10, 31, 11, 0, tzinfo=timezone.utc))


@pytest.mark.parametrize("key", sorted(set(NOTIFICATION_KINDS) - set(NIGHT_KINDS)))
def test_a_failed_read_changes_nothing_for_any_other_kind(key):
    svc, kind = _svc(), get_kind(key)
    for now in (SMALL_HOURS_ET, NOON_ET, _et(23, 30)):
        unread = svc.decide(_recipient(preferences={}, preferences_known=False), kind, now)
        assert unread == svc.decide(_recipient(preferences={}), kind, now)


@pytest.mark.parametrize("key", NIGHT_KINDS)
@pytest.mark.parametrize("stored", [False, "false", "off", "0", 0])
def test_an_explicit_quiet_hours_off_is_honoured_however_it_is_stored(key, stored):
    prefs = {**NY_ONLY, qh.PREF_QUIET_ENABLED: stored}
    d = _svc().decide(_recipient(preferences=prefs), get_kind(key), SMALL_HOURS_ET)
    assert (d.send, d.reason, d.deliver_after) == (True, "ok", None)


@pytest.mark.parametrize("stored", [None, [], {"on": False}])
def test_a_null_or_unreadable_toggle_is_no_answer_so_the_hold_stays(stored):
    """A null or a nested value states nothing, so it means what an absent key means, the same
    fallback `_coerce_toggle` gives every other toggle."""
    prefs = {**NY_ONLY, qh.PREF_QUIET_ENABLED: stored}
    d = _svc().decide(_recipient(preferences=prefs), get_kind(KIND_EARNINGS_RESULT), SMALL_HOURS_ET)
    assert d.reason == "quiet_hours"


def test_the_users_own_window_replaces_the_hold():
    prefs = {
        **NY_ONLY, qh.PREF_QUIET_ENABLED: True,
        qh.PREF_QUIET_START: "23:00", qh.PREF_QUIET_END: "06:00",
    }
    svc, kind = _svc(), get_kind(KIND_EARNINGS_RESULT)
    assert svc.decide(_recipient(preferences=prefs), kind, _et(22, 30)).send is True
    assert svc.decide(_recipient(preferences=prefs), kind, _et(6, 30)).send is True
    d = svc.decide(_recipient(preferences=prefs), kind, SMALL_HOURS_ET)
    assert d.deliver_after.astimezone(ET).hour == 6


def test_a_window_the_user_switched_on_and_broke_is_not_replaced_by_the_hold():
    """Start equal to end reads as off, and the settings screen says so. The toggle was touched,
    so the hold, which is only for people who never touched it, does not apply."""
    prefs = {
        **NY_ONLY, qh.PREF_QUIET_ENABLED: True,
        qh.PREF_QUIET_START: "22:00", qh.PREF_QUIET_END: "22:00",
    }
    d = _svc().decide(_recipient(preferences=prefs), get_kind(KIND_EARNINGS_RESULT), SMALL_HOURS_ET)
    assert (d.send, d.reason) == (True, "ok")


def test_hours_stored_without_the_toggle_do_not_move_the_hold():
    prefs = {**NY_ONLY, qh.PREF_QUIET_START: "23:30", qh.PREF_QUIET_END: "05:00"}
    svc, kind = _svc(), get_kind(KIND_EARNINGS_RESULT)
    assert svc.decide(_recipient(preferences=prefs), kind, _et(22, 30)).reason == "quiet_hours"
    d = svc.decide(_recipient(preferences=prefs), kind, _et(5, 30))
    assert d.deliver_after == datetime(2026, 10, 30, 11, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("key", NIGHT_KINDS)
def test_a_toggle_or_the_cap_still_decides_first_and_parks_nothing(key):
    """A held row would use the user's dedup slot for something they will never be told: at 02:30
    the wake is 07:00 the same day, whose budget is already spent. A failed read is parked only
    once the cap has passed it, for the same reason."""
    svc, kind = _svc(), get_kind(key)
    for preference in (kind.preference_key, kind.master_preference_key):
        d = svc.decide(_recipient(preferences={preference: False}), kind, SMALL_HOURS_ET)
        assert (d.reason, d.deliver_after) == (f"preference_off:{preference}", None)
    d = svc.decide(_recipient(category_sent_today=4), kind, SMALL_HOURS_ET)
    assert (d.reason, d.deliver_after) == ("cap_reached:earnings:4", None)
    unread = _recipient(preferences={}, preferences_known=False, category_sent_today=4)
    d = svc.decide(unread, kind, NOON_ET)
    assert (d.reason, d.deliver_after) == ("cap_reached:earnings:4", None)


@pytest.mark.parametrize("key", NIGHT_KINDS)
@pytest.mark.parametrize("hour,minute", [(22, 0), (23, 30), (23, 59)])
def test_a_hold_past_midnight_leaves_the_cap_to_the_day_it_wakes_on(key, hour, minute):
    """Today's four are spent, but this row buzzes at 07:00 tomorrow, against tomorrow's budget,
    and the flush applies that one. Judged on today's, a result landing at 23:30 was dropped with
    no inbox row while the same result at 00:30 went out at 07:00."""
    d = _svc().decide(
        _recipient(preferences=NY_ONLY, category_sent_today=4), get_kind(key), _et(hour, minute))
    assert (d.send, d.reason) == (False, "quiet_hours")
    assert d.deliver_after == datetime(2026, 10, 31, 11, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("key", NIGHT_KINDS)
def test_a_users_own_window_past_midnight_leaves_the_cap_to_its_wake_too(key):
    prefs = {**NY_ONLY, qh.PREF_QUIET_ENABLED: True,
             qh.PREF_QUIET_START: "21:00", qh.PREF_QUIET_END: "06:00"}
    d = _svc().decide(_recipient(preferences=prefs, category_sent_today=9), get_kind(key), _et(21, 30))
    assert (d.reason, d.deliver_after) == (
        "quiet_hours", datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc))


# Which day a hold wakes on is read off the user's own calendar, the day their cap counts. These
# are the cases where ET's calendar, or a hold's length, would give another answer.


@pytest.mark.parametrize("key", NIGHT_KINDS)
@pytest.mark.parametrize("zone", ["Asia/Tokyo", "Asia/Kolkata"])
def test_a_hold_past_the_users_midnight_is_tomorrows_even_on_the_same_et_date(key, zone):
    """22:30 in Tokyo is 09:30 in New York, and the 07:00 wake is still that ET date. On ET's
    calendar it would meet today's spent four and get no row; it buzzes tomorrow on theirs."""
    tz = ZoneInfo(zone)
    late = datetime(2026, 10, 29, 22, 30, tzinfo=tz).astimezone(timezone.utc)
    d = _svc().decide(_recipient(preferences={qh.PREF_TIMEZONE: zone}, category_sent_today=4),
                      get_kind(key), late)
    wake = d.deliver_after.astimezone(tz)
    assert d.reason == "quiet_hours"
    assert (wake.date(), wake.hour, wake.minute) == (date(2026, 10, 30), 7, 0)


@pytest.mark.parametrize("key", NIGHT_KINDS)
def test_a_hold_that_wakes_on_the_users_same_day_meets_that_days_cap(key):
    """00:30 in Berlin is 19:30 in New York, and the 07:00 wake falls on the next ET date. It is
    the same Berlin day, whose four are spent, so it gets no row, as at 02:30 in New York."""
    berlin = ZoneInfo("Europe/Berlin")
    late = datetime(2026, 10, 30, 0, 30, tzinfo=berlin).astimezone(timezone.utc)
    d = _svc().decide(
        _recipient(preferences={qh.PREF_TIMEZONE: "Europe/Berlin"}, category_sent_today=4),
        get_kind(key), late)
    assert (d.reason, d.deliver_after) == ("cap_reached:earnings:4", None)


@pytest.mark.parametrize("key", NIGHT_KINDS)
def test_a_short_window_across_midnight_still_wakes_on_the_next_day(key):
    """23:30 to 03:00 is a hold of three and a half hours, and it ends tomorrow all the same."""
    prefs = {**NY_ONLY, qh.PREF_QUIET_ENABLED: True,
             qh.PREF_QUIET_START: "23:00", qh.PREF_QUIET_END: "03:00"}
    d = _svc().decide(_recipient(preferences=prefs, category_sent_today=4), get_kind(key), _et(23, 30))
    assert (d.reason, d.deliver_after) == (
        "quiet_hours", datetime(2026, 10, 31, 7, 0, tzinfo=timezone.utc))


@pytest.mark.parametrize("key", NIGHT_KINDS)
def test_a_long_hold_inside_one_day_meets_that_days_cap(key):
    """00:45 to 09:00 is eight hours and a quarter, all of it on one New York day."""
    prefs = {**NY_ONLY, qh.PREF_QUIET_ENABLED: True,
             qh.PREF_QUIET_START: "00:30", qh.PREF_QUIET_END: "09:00"}
    d = _svc().decide(_recipient(preferences=prefs, category_sent_today=4), get_kind(key), _et(0, 45))
    assert (d.reason, d.deliver_after) == ("cap_reached:earnings:4", None)


_CAPPED_QUIET_KINDS = sorted(
    k for k, kind in NOTIFICATION_KINDS.items()
    if k not in NIGHT_KINDS and kind.respects_quiet_hours
    and category_cap(kind.category, settings.PUSH_MAX_ALERTS_PER_USER_PER_DAY) is not None
)


@pytest.mark.parametrize("key", _CAPPED_QUIET_KINDS)
def test_any_other_kind_held_past_midnight_still_meets_the_landing_days_cap(key):
    """The wake's cap belongs to the night-hold kinds. Every other kind keeps the order it shipped
    with: a user at the cap gets no row, whatever their own quiet hours say."""
    kind = get_kind(key)
    cap = category_cap(kind.category, settings.PUSH_MAX_ALERTS_PER_USER_PER_DAY)
    prefs = {**_QUIET, kind.preference_key: True}
    d = _svc().decide(_recipient(preferences=prefs, category_sent_today=cap), kind, _et(23, 30))
    assert (d.reason, d.deliver_after) == (f"cap_reached:{kind.category}:{cap}", None)


def test_broken_hold_math_fails_to_NOT_QUIET(monkeypatch):
    """The rule for all of step 3: a bug must not queue every result forever."""
    def _boom(*a, **k):
        raise RuntimeError("wraparound exploded")

    monkeypatch.setattr(qh, "is_within", _boom)
    d = _svc().decide(_recipient(preferences=NY_ONLY), get_kind(KIND_EARNINGS_LIVE), SMALL_HOURS_ET)
    assert (d.send, d.reason) == (True, "ok")


@pytest.mark.parametrize("key", sorted(set(NOTIFICATION_KINDS) - set(NIGHT_KINDS)))
@pytest.mark.parametrize("now", [SMALL_HOURS_ET, _et(23, 30), NOON_ET], ids=["0230", "2330", "noon"])
def test_every_other_kind_treats_an_untouched_toggle_as_never_quiet(key, now):
    """The hold belongs to the two result kinds. A kind that ships off is opted in here so that
    the decision reaches the quiet-hours step."""
    svc, kind = _svc(), get_kind(key)
    prefs = {kind.preference_key: True}
    untouched = svc.decide(_recipient(preferences=prefs), kind, now)
    said_off = svc.decide(_recipient(preferences={**prefs, qh.PREF_QUIET_ENABLED: False}), kind, now)
    assert (untouched.send, untouched.reason, untouched.deliver_after) == (True, "ok", None)
    assert untouched == said_off


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
async def test_with_apns_unconfigured_the_row_is_still_claimed_and_marked_failed():
    """⚠️ THIS REVERSES AN EARLIER DECISION. Do not flip it back without reading why.

    This test used to assert `claims == []`, on the reasoning that claiming while APNs is
    down burns the dedup slot and permanently suppresses a notification that was never
    sent. That reasoning is real, but it trades the wrong way: it protects a RE-SEND that is
    rarely worth anything (a market event is stale hours later) at the cost of the
    `notification_events` row, which is worth something always.

    With the early return in place, a missing APNS_* value produced no row at all — the
    in-app inbox was empty, the calling job logged a healthy "delivered 0/0", and the only
    trace was a `logger.debug` invisible at the default INFO level. The inbox exists
    precisely so a notification survives a failed push.

    So: claim, write the row, and mark it `failed` with the reason. The user sees the alert
    in the app; `last_error` says why their phone never buzzed.
    """
    svc = _svc(_FakePush(enabled=False))
    claims, states = [], []
    _wire(svc, claims=claims, states=states, recipients=[_recipient(preferences={})])
    assert await svc.notify_users(["u1"], kind=KIND_TICKER_MOVE, title="T", body="B",
                                  dedup_key="k", now=NOON_ET) == 0
    assert len(claims) == 1, "the inbox row must be written even when no push can go out"
    assert states and states[0][1] == STATE_FAILED
    assert "APNs" in (states[0][2].get("error") or ""), (
        f"the row must say WHY it failed; got {states[0][2]!r}"
    )


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
@pytest.mark.parametrize("key", NIGHT_KINDS)
async def test_a_held_result_is_parked_at_once_so_the_inbox_has_it(key):
    push = _FakePush()
    svc = _svc(push)
    claims = []
    _wire(svc, claims=claims, recipients=[_recipient(preferences=NY_ONLY)])

    sent = await svc.notify_users(["u1"], kind=key, title="T", body="B",
                                  dedup_key="k", now=SMALL_HOURS_ET)
    assert sent == 0 and push.calls == []
    ((_uid, _key, claim),) = claims
    assert (claim["kind"], claim["push_state"]) == (key, STATE_DEFERRED)
    assert claim["deliver_after"] == datetime(2026, 10, 30, 11, 0, tzinfo=timezone.utc)


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
async def test_a_long_quiet_window_defers_rather_than_dropping():
    """§11.5 promises "DEFER, never drop" — and a legal quiet window used to break it.

    Nothing bounds how long a quiet window may be: `resolve_window` only rejects
    `start == end`, `sanitize_preferences` keeps any scalar, and the iOS pickers are free
    form. So 20:00 → 10:00 is 14 hours, and staleness was measured from `claimed_at` against
    a 12-hour cap — every quiet-hours-respecting notification parked at 20:01 was marked
    `failed` at flush. The user set an ordinary preference and silently stopped getting
    alerts.

    This row was parked 14 hours ago and came due 1 minute ago. It must SEND.
    """
    push = _FakePush()
    svc = _svc(push)
    parked = datetime.now(timezone.utc) - timedelta(hours=14)
    due = datetime.now(timezone.utc) - timedelta(minutes=1)
    svc._claim_due = lambda limit: [{
        "user_id": "u1", "dedup_key": "k", "kind": KIND_TICKER_MOVE,
        "title": "NVDA", "body": "moved 8%", "route": {"ticker": "NVDA"},
        "claimed_at": parked.isoformat(),
        "deliver_after": due.isoformat(),
    }]
    svc._devices_bulk = lambda ids: {"u1": [{"token": "t", "environment": "sandbox"}]}
    svc.unread_counts_bulk = lambda ids: {"u1": 1}
    svc.mark_state = lambda *a, **k: None

    stats = await svc.flush_deferred()
    assert stats["stale"] == 0, "a row that came due one minute ago is not stale"
    assert stats["sent"] == 1
    assert push.calls[0]["title"] == "NVDA"


@pytest.mark.asyncio
async def test_a_row_stuck_long_past_its_due_time_still_fails():
    """The staleness bound has to survive the fix above, or a genuinely stuck row buzzes
    someone at breakfast about yesterday afternoon."""
    push = _FakePush()
    svc = _svc(push)
    parked = datetime.now(timezone.utc) - timedelta(hours=40)
    due = datetime.now(timezone.utc) - timedelta(hours=20)   # 20h LATE, not 20h old
    svc._claim_due = lambda limit: [{
        "user_id": "u1", "dedup_key": "k", "kind": KIND_TICKER_MOVE,
        "title": "T", "body": "B", "route": {},
        "claimed_at": parked.isoformat(),
        "deliver_after": due.isoformat(),
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
    # The badge is `unread + 1`: what the icon should read AFTER this notification lands.
    #
    # ⚠️ This assertion has flipped TWICE, and the second flip was the bug. It read `== 3`,
    # was changed to `== 2` on the premise that "a deferred row is INSERTED unread at defer
    # time, so the count read here ALREADY includes it" — and that premise is false.
    # `unread_counts_bulk` counts `push_state = 'sent'` rows only (a `deferred` row was
    # never shown and must not badge the icon), and `claim_due_notifications` flips the row
    # to `pending`, never `sent`, so at flush time it is STILL excluded. `== 2` therefore
    # pinned a badge that left out the very notification it announced: a new user's first
    # deferred alert went out with `aps.badge: 0`, which iOS treats as CLEAR.
    #
    # The stub's `{"u1": 2}` means "2 delivered-and-unread rows, and this one is NOT among
    # them". The per-batch increment for a second row to the same user is pinned in
    # `test_flush_badge_*` below.
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
    # `_category_counts_bulk`, NOT `alerts_sent_today`: the latter has no production
    # caller any more, so stubbing it here would leave this test controlling nothing
    # (svc.supabase is None, so the real read fails open to 0 and the assertion below
    # would pass for the wrong reason).
    svc._category_counts_bulk = lambda ids, category, cutoffs: {uid: 0 for uid in ids}
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
    # `_category_counts_bulk`, NOT `alerts_sent_today`: the latter has no production
    # caller any more, so stubbing it here would leave this test controlling nothing
    # (svc.supabase is None, so the real read fails open to 0 and the assertion below
    # would pass for the wrong reason).
    svc._category_counts_bulk = lambda ids, category, cutoffs: {uid: 0 for uid in ids}
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
    # `_category_counts_bulk`, NOT `alerts_sent_today`: the latter has no production
    # caller any more, so stubbing it here would leave this test controlling nothing
    # (svc.supabase is None, so the real read fails open to 0 and the assertion below
    # would pass for the wrong reason).
    svc._category_counts_bulk = lambda ids, category, cutoffs: {uid: 0 for uid in ids}
    svc.mark_state = lambda *a, **k: None

    stats = await svc.flush_deferred()
    assert stats["sent"] == 1, "a flushed row must not be re-deferred"


@pytest.mark.asyncio
@pytest.mark.parametrize("key", sorted(set(NOTIFICATION_KINDS) - set(NIGHT_KINDS)))
async def test_every_other_kind_is_flushed_word_for_word_with_no_second_hold(key, monkeypatch):
    """The wake's two extra questions belong to the night-hold kinds. A row of any other kind,
    still inside the user's window on a good read and on its first hand-out, with a route naming
    a report, goes out as it was written."""
    def _never(*args, **kwargs):
        raise AssertionError(f"{key} must not reach this")

    kind = get_kind(key)
    push = _FakePush()
    svc = _svc(push)
    svc._claim_due = lambda limit: [{
        "user_id": "u1", "dedup_key": "k", "kind": key, "title": "T", "body": "B",
        "route": {"ticker": "INTC", "report_date": "2026-10-29"}, "attempts": 1,
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }]
    svc._devices_bulk = lambda ids: {"u1": [{"token": "t", "environment": "sandbox"}]}
    svc.unread_counts_bulk = lambda ids: {"u1": 0}
    svc._preferences_bulk = lambda ids: {"u1": {
        kind.preference_key: True,
        qh.PREF_QUIET_ENABLED: True,
        qh.PREF_QUIET_START: "00:01",
        qh.PREF_QUIET_END: "23:59",
        qh.PREF_TIMEZONE: "America/New_York",
    }}
    svc._category_counts_bulk = lambda ids, category, cutoffs: {uid: 0 for uid in ids}
    svc.mark_state = lambda *a, **k: None
    svc._hold_again = _never
    svc._rewrite_text = _never
    monkeypatch.setattr(earnings_live_sender, "copy_at_wake", _never)

    stats = await svc.flush_deferred()

    assert (stats["sent"], stats["requeued"], stats["failed"]) == (1, 0, 0)
    assert (push.calls[0]["title"], push.calls[0]["body"]) == ("T", "B")


def _flush_clock(monkeypatch, instant: datetime) -> datetime:
    """The flush reads its own clock, so a test of who is in their night pins it."""
    import app.services.push_dispatch_service as pds

    class _Moved(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz or timezone.utc)

    monkeypatch.setattr(pds, "datetime", _Moved)
    return instant


def _never(*args, **kwargs):
    raise AssertionError("this row must not reach that step")


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts,hold_written,read_ok,held", [
    (1, True, True, True),
    (2, True, True, False),
    (1, False, True, False),
    (1, True, False, False),
], ids=["first_wake", "spent", "hold_not_written", "read_failed"])
async def test_a_night_row_judged_at_the_flush_meets_the_cap_of_the_day_it_goes_out_on(
        monkeypatch, attempts, hold_written, read_ok, held):
    """23:30 in New York is the user's night, with the wake tomorrow, so `decide` leaves the cap to
    that wake: a row on its first hand-out is held for it, today's four spent or not. A row the
    flush does not hold again goes out now instead, so today's budget is the one it would spend:
    one that has had its second hold, one whose hold could not be written, and one whose read
    failed (an empty blob reads as ET's night, which is no reason to hold it)."""
    now = _flush_clock(monkeypatch, _et(23, 30))
    push = _FakePush()
    svc = _svc(push)
    svc._claim_due = lambda limit: [{
        "user_id": "u1", "dedup_key": "k", "kind": KIND_EARNINGS_RESULT, "title": "T", "body": "B",
        "route": {}, "attempts": attempts, "deliver_after": (now - timedelta(minutes=1)).isoformat(),
    }]
    svc._devices_bulk = lambda ids: {"u1": [{"token": "t", "environment": "sandbox"}]}
    svc.unread_counts_bulk = lambda ids: {"u1": 0}
    svc._preferences_bulk = lambda ids: {"u1": dict(NY_ONLY) if read_ok else None}
    svc._category_counts_bulk = lambda ids, category, cutoffs: {uid: 4 for uid in ids}
    holds, states = [], []
    svc._hold_again = lambda uid, key, at: holds.append(at) or hold_written
    svc.mark_state = lambda uid, key, state, **kw: states.append((state, kw.get("error")))

    stats = await svc.flush_deferred()

    wake = datetime(2026, 10, 31, 11, 0, tzinfo=timezone.utc)
    assert push.calls == []
    if held:
        assert (stats["requeued"], stats["suppressed"]) == (1, 0)
        assert holds == [wake] and states == []
    else:
        assert (stats["requeued"], stats["suppressed"]) == (0, 1)
        assert holds == ([] if hold_written else [wake])
        assert states == [(STATE_FAILED, "suppressed on flush: cap_reached:earnings:4")]


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [1, 2, 3], ids=["first_wake", "second", "last"])
@pytest.mark.parametrize("key", [KIND_TICKER_MOVE, KIND_EARNINGS_RESULT, KIND_EARNINGS_LIVE])
async def test_a_held_row_read_in_the_inbox_is_ended_unbuzzed_at_its_wake(monkeypatch, key, attempts):
    """The inbox marks a held row read when the user opens it. At its wake they have what the buzz
    would tell them: no buzz, no second hold, no new text, no count read, and nothing charged to the
    day's cap. On any hand-out: a failed read at the landing or a figure being confirmed gives a
    night-hold row later ones. 02:30 is still the night, so a first one would be held once more."""
    now = _flush_clock(monkeypatch, SMALL_HOURS_ET)
    push = _FakePush()
    svc = _svc(push)
    svc._claim_due = lambda limit: [{
        "user_id": "u1", "dedup_key": "k", "kind": key, "title": "T", "body": "B",
        "route": {"ticker": "INTC", "report_date": "2026-10-29"}, "attempts": attempts,
        "deliver_after": (now - timedelta(minutes=1)).isoformat(),
        "read_at": (now - timedelta(hours=3)).isoformat(),
    }]
    svc._devices_bulk = lambda ids: {"u1": [{"token": "t", "environment": "sandbox"}]}
    svc.unread_counts_bulk = lambda ids: {"u1": 0}
    svc._preferences_bulk = lambda ids: {"u1": dict(NY_ONLY)}
    svc._category_counts_bulk = _never
    svc._hold_again = _never
    svc._rewrite_text = _never
    monkeypatch.setattr(earnings_live_sender, "copy_at_wake", _never)
    states = []
    svc.mark_state = lambda uid, k, state, **kw: states.append((state, kw.get("error"), kw.get("sent")))

    stats = await svc.flush_deferred()

    assert push.calls == []
    assert (stats["suppressed"], stats["sent"], stats["requeued"], stats["failed"]) == (1, 0, 0, 0)
    assert states == [(STATE_FAILED, "suppressed on flush: read in the app before its wake", False)]


def _turned_off_at_the_wake(monkeypatch, key, *, off):
    """One held row of `key` on its first hand-out at noon, its user's `off` toggle now False."""
    now = _flush_clock(monkeypatch, _et(12, 0))
    push = _FakePush()
    svc = _svc(push)
    svc._claim_due = lambda limit: [{
        "user_id": "u1", "dedup_key": "k", "kind": key, "title": "T", "body": "B",
        "route": {"ticker": "INTC", "report_date": "2026-10-29"}, "attempts": 1,
        "deliver_after": (now - timedelta(minutes=1)).isoformat(),
    }]
    svc._devices_bulk = lambda ids: {"u1": [{"token": "t", "environment": "sandbox"}]}
    svc.unread_counts_bulk = lambda ids: {"u1": 0}
    svc._preferences_bulk = lambda ids: {"u1": {**NY_ONLY, off: False}}
    svc._category_counts_bulk = lambda ids, category, cutoffs: {uid: 0 for uid in ids}
    states = []
    svc.mark_state = lambda uid, k, state, **kw: states.append((state, kw.get("error")))
    return svc, push, states


@pytest.mark.asyncio
@pytest.mark.parametrize("key", NIGHT_KINDS)
@pytest.mark.parametrize("which", ["own", "master"])
async def test_a_night_row_its_user_turned_off_gives_its_key_back(monkeypatch, key, which):
    """The two night-hold kinds and the daily pass share one key per report. A row the user's
    settings turn off at its wake was claimed on a guess (a failed read at the landing) or before
    they changed them, and ended there it kept that key from the kind or the pass they do want. It
    is deleted instead: nothing was buzzed, so nothing can be buzzed twice."""
    kind = get_kind(key)
    off = kind.preference_key if which == "own" else kind.master_preference_key
    svc, push, states = _turned_off_at_the_wake(monkeypatch, key, off=off)
    released = []
    svc._release = lambda uid, k: released.append((uid, k)) or True
    monkeypatch.setattr(earnings_live_sender, "copy_at_wake", _never)

    stats = await svc.flush_deferred()

    assert push.calls == [] and states == [] and released == [("u1", "k")]
    assert (stats["released"], stats["suppressed"], stats["sent"]) == (1, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", NIGHT_KINDS)
async def test_a_row_that_cannot_be_given_back_is_ended_as_before(monkeypatch, key):
    svc, push, states = _turned_off_at_the_wake(monkeypatch, key, off=get_kind(key).preference_key)
    svc._release = lambda uid, k: False
    monkeypatch.setattr(earnings_live_sender, "copy_at_wake", lambda *args: None)

    stats = await svc.flush_deferred()

    assert push.calls == [] and (stats["released"], stats["suppressed"]) == (0, 1)
    assert states == [(STATE_FAILED, f"suppressed on flush: preference_off:{get_kind(key).preference_key}")]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", sorted(k for k, kind in NOTIFICATION_KINDS.items()
                                       if k not in NIGHT_KINDS and kind.respects_quiet_hours))
async def test_any_other_kind_turned_off_at_its_wake_keeps_its_row(monkeypatch, key):
    """No other kind shares its key, so its row stays as the record of what was decided."""
    svc, push, states = _turned_off_at_the_wake(monkeypatch, key, off=get_kind(key).preference_key)
    svc._release = _never

    stats = await svc.flush_deferred()

    assert push.calls == [] and (stats["released"], stats["suppressed"]) == (0, 1)
    assert states == [(STATE_FAILED, f"suppressed on flush: preference_off:{get_kind(key).preference_key}")]


class _DeleteRecorder:
    """`notification_events` for `_release`: the filters a DELETE carries, and what it answers."""

    def __init__(self, answer=None, error=None):
        self.filters, self.answer, self.error = [], answer, error

    def table(self, name):
        assert name == "notification_events"
        return self

    def delete(self):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def execute(self):
        if self.error is not None:
            raise self.error
        return type("_R", (), {"data": self.answer})()


def test_a_release_deletes_only_the_row_this_flush_holds():
    """Scoped to the user and the key, and to `pending`: the state the claim RPC gave the row this
    flush is handling, so the delete can never remove a row that has gone out."""
    svc = _svc()
    svc.supabase = _DeleteRecorder(answer=[{"user_id": "u1", "dedup_key": "k"}])
    assert svc._release("u1", "k") is True
    assert sorted(svc.supabase.filters) == [
        ("dedup_key", "k"), ("push_state", STATE_PENDING), ("user_id", "u1")]


@pytest.mark.parametrize("answer,error", [([], None), (None, RuntimeError("520 from the edge"))],
                         ids=["nothing_deleted", "delete_failed"])
def test_a_release_that_removed_nothing_says_so_and_answers_false(caplog, answer, error):
    svc = _svc()
    svc.supabase = _DeleteRecorder(answer=answer, error=error)
    with caplog.at_level(logging.WARNING):
        assert svc._release("u1", "k") is False
    (record,) = [r for r in caplog.records if "user=u1" in r.getMessage()]
    assert "key=k" in record.getMessage() and record.levelno == logging.WARNING


# ── the flush charges the cap against the RIGHT clock ────────────────────────
#
# §11.3 names three clocks that must never be interchanged. The per-category daily cap rolls
# at the USER's midnight (`notify_timezone`); ET is the dedup/ops clock.
#
# `dispatch` got this right at the claim path. `flush_deferred` did NOT: it called
# `alerts_sent_today`, which anchors on ET midnight — directly under a comment claiming it used
# "the user's own day boundary, not ET". For a Tokyo user the ET boundary lands at 14:00 local,
# so alerts already sent that morning were charged to the previous day and the cap over-sent.
# That is the "a Tokyo user's daily budget resets at 2pm" failure in this file's own docstring,
# still live on the flush path.
#
# These assert the CUTOFF and the CATEGORY the counter is asked for, rather than a delivery
# outcome, because `flush_deferred` reads `datetime.now(timezone.utc)` internally — an outcome
# test would depend on the wall clock at the moment the suite runs.


def _deferred_row(kind, key="k", user="u1"):
    return {
        "user_id": user, "dedup_key": key, "kind": kind,
        "title": "T", "body": "B", "route": {},
        "claimed_at": datetime.now(timezone.utc).isoformat(),
    }


def _flush_svc(push, rows, preferences):
    svc = _svc(push)
    svc._claim_due = lambda limit: rows
    svc._devices_bulk = lambda ids: {"u1": [{"token": "t", "environment": "sandbox"}]}
    svc.unread_counts_bulk = lambda ids: {"u1": 0}
    svc._preferences_bulk = lambda ids: {"u1": preferences}
    svc.mark_state = lambda *a, **k: None
    return svc


@pytest.mark.asyncio
async def test_the_flush_charges_the_cap_against_the_users_own_midnight():
    seen = {}

    def _counts(ids, category, cutoffs):
        seen.update(cutoffs)
        return {uid: 0 for uid in ids}

    svc = _flush_svc(
        _FakePush(),
        [_deferred_row(KIND_TICKER_MOVE)],
        {qh.PREF_TIMEZONE: "Asia/Tokyo"},
    )
    svc._category_counts_bulk = _counts

    await svc.flush_deferred()

    assert "u1" in seen, "the flush never asked for a per-user cutoff"
    now = datetime.now(timezone.utc)
    tokyo_midnight = qh.local_day_start_utc(now, TOKYO)
    et_midnight = qh.local_day_start_utc(now, ET)
    # Non-vacuity: if the two clocks agreed right now, this test would prove nothing.
    assert tokyo_midnight != et_midnight, "pick a moment where the two day-starts differ"
    assert seen["u1"] == tokyo_midnight, (
        f"cap charged against {seen['u1']} — the user's midnight is {tokyo_midnight}"
    )


@pytest.mark.asyncio
async def test_the_flush_counts_each_category_separately():
    """The budget cache was keyed on the USER alone while the value is per-CATEGORY, so a
    second category reused the first one's count — an earnings row judged against the
    watchlist budget."""
    asked = []

    def _counts(ids, category, cutoffs):
        asked.append(category)
        return {uid: 0 for uid in ids}

    svc = _flush_svc(
        _FakePush(),
        [
            _deferred_row(KIND_TICKER_MOVE, key="a"),        # watchlist
            _deferred_row(KIND_EARNINGS_UPCOMING, key="b"),  # earnings
        ],
        {},
    )
    svc._category_counts_bulk = _counts

    await svc.flush_deferred()

    assert sorted(asked) == ["earnings", "watchlist"], (
        f"each category needs its own count; asked for {asked}"
    )


@pytest.mark.asyncio
async def test_the_flush_asks_once_per_user_and_category_not_once_per_row():
    """The cache still has to work: three watchlist rows are ONE count read, then charged
    locally as the batch delivers."""
    asked = []

    def _counts(ids, category, cutoffs):
        asked.append(category)
        return {uid: 0 for uid in ids}

    svc = _flush_svc(
        _FakePush(),
        [_deferred_row(KIND_TICKER_MOVE, key=f"k{i}") for i in range(3)],
        {},
    )
    svc._category_counts_bulk = _counts

    await svc.flush_deferred()

    assert asked == ["watchlist"], f"one read per (user, category) expected; got {asked}"


# ── the dispatch loop's pace ─────────────────────────────────────────────────


class _LoopStopped(BaseException):
    """Ends the 24/7 dispatch loop, which outlives every `Exception`."""


async def _dispatch_loop_sleeps(monkeypatch, passes):
    """Run the real `_run_notification_dispatch_loop` over `passes`, each one flush's stats or the
    error it raises, and return every pause it took, the start-up stagger first."""
    import asyncio
    from types import SimpleNamespace

    import app.main as main_mod
    import app.services.push_dispatch_service as pds

    left = iter(passes)

    class _Flush:
        async def flush_deferred(self):
            stats = next(left, None)
            if stats is None:
                raise _LoopStopped
            if isinstance(stats, Exception):
                raise stats
            return stats

    slept = []

    async def _sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(pds, "get_push_dispatch_service", lambda: _Flush())
    monkeypatch.setattr(main_mod, "asyncio",
                        SimpleNamespace(sleep=_sleep, CancelledError=asyncio.CancelledError))
    with pytest.raises(_LoopStopped):
        await main_mod._run_notification_dispatch_loop()
    return slept


@pytest.mark.asyncio
async def test_a_full_batch_that_settled_rows_is_followed_at_once_not_a_minute_later(monkeypatch):
    """A 07:00 wake after an earnings night holds every default user's results in that time zone,
    all due at once. One batch, then a minute's wait: about 75 rows a minute, so a few thousand
    rows made 07:00 into 08:00 for the last of them. A batch that came back full and settled rows
    says more are due now, so the next one starts at once."""
    import app.main as main_mod

    batch = settings.NOTIFICATION_DISPATCH_BATCH
    interval = max(settings.NOTIFICATION_DISPATCH_INTERVAL_SECONDS, 10)
    slept = await _dispatch_loop_sleeps(monkeypatch, [
        {"claimed": batch, "sent": batch - 3, "requeued": 3},
        {"claimed": batch, "sent": batch, "requeued": 0},
        {"claimed": batch - 1, "sent": batch - 1, "requeued": 0},
    ])
    pause = main_mod._FLUSH_DRAIN_PAUSE_SECONDS
    assert slept == [45, pause, pause, interval]
    assert 0 < pause < interval


@pytest.mark.asyncio
@pytest.mark.parametrize("stats", [
    {"claimed": 100, "requeued": 100},
    {"claimed": 0, "requeued": 0},
    {"claimed": 99, "sent": 99, "requeued": 0},
    {},
], ids=["a_full_batch_all_put_back", "nothing_due", "the_last_of_the_queue", "no_counts"])
async def test_a_batch_that_settled_nothing_or_emptied_the_queue_waits_the_interval(
        monkeypatch, stats):
    """A full batch put back whole, every row held again or retried after an error, settled
    nothing: taken again at once, an error that keeps failing would spend a row's tries in
    seconds. A short batch emptied the queue."""
    monkeypatch.setattr(settings, "NOTIFICATION_DISPATCH_BATCH", 100)
    interval = max(settings.NOTIFICATION_DISPATCH_INTERVAL_SECONDS, 10)
    assert await _dispatch_loop_sleeps(monkeypatch, [stats]) == [45, interval]


@pytest.mark.asyncio
async def test_a_failed_flush_waits_the_interval_and_says_so(monkeypatch, caplog):
    """Even right after a pass that drained a full batch: a failure says nothing about what is
    due, and taken again at once it would fail, with an ERROR and its stack, every second."""
    import app.main as main_mod

    batch = settings.NOTIFICATION_DISPATCH_BATCH
    interval = max(settings.NOTIFICATION_DISPATCH_INTERVAL_SECONDS, 10)
    with caplog.at_level(logging.ERROR, logger=main_mod.__name__):
        slept = await _dispatch_loop_sleeps(monkeypatch, [
            {"claimed": batch, "sent": batch, "requeued": 0},
            RuntimeError("claim RPC exploded"),
            RuntimeError("claim RPC exploded"),
        ])
    assert slept == [45, main_mod._FLUSH_DRAIN_PAUSE_SECONDS, interval, interval]
    failures = [r for r in caplog.records if "claim RPC exploded" in r.getMessage()]
    assert len(failures) == 2 and all(r.exc_info for r in failures)
