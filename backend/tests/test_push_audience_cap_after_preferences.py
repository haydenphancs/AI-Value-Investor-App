"""The per-scope recipient cap is applied AFTER the preference filter, with a rotating cut.

F17-7: `followers_of_whale` / `watchers_of` read `MAX_RECIPIENTS_PER_SCOPE + 1` rows ordered
by user id and dropped the rest BEFORE anyone looked at a preference. On a whale with 600
followers of whom 40 had `whale_13f` ON, the 500 lowest uuids were taken (~33 opted in) and
the opted-in follower whose uuid sorted 501st never received any 13F alert on ANY filing,
forever, while the server logged a WARNING nobody sees. Same shape for a ticker past 500
watchers. The selectors now page the whole audience, and `_notify_users_inner` filters on
the toggle + master BEFORE capping the survivors — sorted by a hash of (uid, event key) so
the excluded tail rotates instead of starving the same users every time.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services import push_dispatch_service as pds
from app.services.notification_kinds import get_kind
from app.services.push_dispatch_service import MAX_RECIPIENTS_PER_SCOPE, PushDispatchService

NOW = datetime(2026, 9, 17, 16, 0, tzinfo=timezone.utc)


def _svc(prefs_by_user):
    svc = object.__new__(PushDispatchService)
    svc.supabase = None
    svc._push = None
    svc._preferences_bulk = lambda ids: {u: prefs_by_user.get(u, {}) for u in ids}
    return svc


def test_opted_in_users_past_the_raw_cap_are_no_longer_starved():
    kind = get_kind("whale_13f")                      # ships OFF: only explicit ON qualifies
    users = [f"{i:04d}-user" for i in range(600)]
    # Forty opted in, spread across the whole uuid range — including the tail the old
    # raw cap discarded.
    opted = users[::15]
    assert len(opted) == 40 and opted[-1] > users[MAX_RECIPIENTS_PER_SCOPE]
    prefs = {u: {kind.preference_key: True, kind.master_preference_key: True} for u in opted}
    svc = _svc(prefs)
    kept = svc._cap_after_preferences(users, kind, "whale:gates:13f:2026-09-17", NOW)
    assert sorted(kept) == sorted(opted), "every opted-in follower is notified"


def test_survivors_are_capped_with_a_rotating_cut():
    kind = get_kind("ticker_move")                    # ships ON
    users = [f"{i:04d}-user" for i in range(MAX_RECIPIENTS_PER_SCOPE + 200)]
    svc = _svc({})
    a = set(svc._cap_after_preferences(users, kind, "AAPL:move:2026-09-17", NOW))
    b = set(svc._cap_after_preferences(users, kind, "AAPL:move:2026-09-18", NOW))
    assert len(a) == len(b) == MAX_RECIPIENTS_PER_SCOPE
    assert a != b, "the same tail was excluded on both events — the cut is not rotating"
    # And a callable dedup key contributes its shape too.
    c = set(svc._cap_after_preferences(users, kind, lambda uid: f"AAPL:move:x:{uid}", NOW))
    assert len(c) == MAX_RECIPIENTS_PER_SCOPE


def test_a_failed_preference_read_keeps_the_declared_default():
    kind = get_kind("ticker_move")                    # default ON
    users = [f"{i:04d}-user" for i in range(MAX_RECIPIENTS_PER_SCOPE + 1)]
    svc = object.__new__(PushDispatchService)
    svc.supabase = None
    svc._push = None
    svc._preferences_bulk = lambda ids: {u: None for u in ids}   # the read FAILED
    kept = svc._cap_after_preferences(users, kind, "k", NOW)
    assert len(kept) == MAX_RECIPIENTS_PER_SCOPE
    off = get_kind("whale_13f")                       # default OFF
    assert svc._cap_after_preferences(users, off, "k", NOW) == []


def test_the_selectors_page_the_whole_audience_and_no_longer_cap():
    """Source pin: the cap must not creep back into the selectors — that is where it
    starved users before anyone read a preference."""
    import inspect

    for fn in (PushDispatchService.watchers_of, PushDispatchService.followers_of_whale):
        src = "\n".join(l for l in inspect.getsource(fn).splitlines() if not l.strip().startswith("#"))
        assert "fetch_all_rows(" in src, fn.__name__
        assert "MAX_RECIPIENTS_PER_SCOPE" not in src, fn.__name__
        assert ".limit(" not in src, fn.__name__
    inner = inspect.getsource(PushDispatchService._notify_users_inner)
    assert "self._cap_after_preferences" in inner
    assert pds.MAX_AUDIENCE_SCAN_PAGES >= 10


@pytest.mark.asyncio
async def test_notify_users_inner_reaches_the_capped_survivors_not_the_raw_head(monkeypatch):
    """End to end through the fan-out: 600 followers, the opted-in ones at the TAIL."""
    kind = get_kind("whale_13f")
    users = [f"{i:04d}-user" for i in range(600)]
    opted = users[-40:]
    prefs = {u: {kind.preference_key: True, kind.master_preference_key: True} for u in opted}
    svc = _svc(prefs)
    monkeypatch.setattr(svc, "resolve_route_asset_type", lambda *a, **k: None)
    seen: list = []

    def _resolve(ids, nkind, now):
        seen.extend(ids)
        return {}
    monkeypatch.setattr(svc, "resolve_recipients", _resolve)
    monkeypatch.setattr(pds.settings, "PUSH_DRY_RUN", True)

    class _Push:
        enabled = False
    svc._push = _Push()

    # Stop before any claim: `decide` on an empty `_Recipient` (no prefs) suppresses the
    # OFF-by-default kind, so nothing is claimed — we only care WHO reached resolve.
    sent = await svc._notify_users_inner(
        users, kind="whale_13f", title="t", body="b", dedup_key="whale:x",
        route=None, collapse_id=None, now=NOW,
    )
    assert sent == 0
    assert sorted(seen) == sorted(opted), "the tail opted-in followers never reached the fan-out"
