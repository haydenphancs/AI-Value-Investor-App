"""`widget_push_service.WidgetPushService.wake` — who gets a WidgetKit wake, when, and what is
written back (spec §3 "Push", D11).

Hermetic: the RPCs go to an in-memory fake that models `widget_push_targets` (liveness flag,
head-ticker / p_all / p_grant_ids match, keyset, 1000-row clamp) and records every
`widget_push_settle` batch; the sender is a fake whose outcomes each test scripts. `settings` is a
per-test proxy patched into the module, and `session_phase` is pinned outside the regular session.

Pins: the switch, APNs, the regular session (which clears pending), scope normalisation,
targeting, interval + ET-day cap from max(local, DB) — so a restart never resets them —, the
per-pass cap (300 targets → 200 sent, 100 pending), pending chunks of ≤ 1000, settle batches of
≤ 100 carrying the exact token sent, the 410 timestamp, dry run, failure paths, cancellation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

import app.services.widget_push_service as wps
from app.services.news_cache_service import MARKET_SCOPE
from app.services.push_service import (
    WIDGET_ACCEPTED,
    WIDGET_BAD,
    WIDGET_GONE,
    WIDGET_NOT_SENT,
    WIDGET_PROVIDER_FAULT,
    WIDGET_RETRY,
    WidgetPushResult,
)
from app.utils.market_hours import ET, SESSION_AFTERHOURS, SESSION_PREMARKET, SESSION_REGULAR

# A fixed instant: Tuesday 2026-11-24 18:00 ET (after hours).
T0 = datetime(2026, 11, 24, 23, 0, tzinfo=timezone.utc).timestamp()
TODAY = "2026-11-24"


class _Settings:
    def __init__(self, base: Any, **overrides: Any) -> None:
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_over", dict(overrides))

    def __getattr__(self, name: str) -> Any:
        over = object.__getattribute__(self, "_over")
        if name in over:
            return over[name]
        return getattr(object.__getattribute__(self, "_base"), name)


def _gid(i: int) -> str:
    return f"00000000-0000-4000-8000-{i:012d}"


def _tok(i: int) -> str:
    return f"{i:064x}"


class _FakeDB:
    """The two push RPCs, modelled on the migration's SQL."""

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.calls: List[tuple] = []
        self.settled: List[List[Dict[str, Any]]] = []
        self.fail: Optional[BaseException] = None
        self.page_limit = 1000

    def add(self, i: int, *, head: Optional[str] = "NVDA", live: bool = True,
            env: str = "production", **extra: Any) -> str:
        gid = _gid(i)
        self.rows[gid] = {
            "grant_id": gid, "user_id": f"user-{i}", "push_token": _tok(i),
            "push_environment": env, "last_head_ticker": head, "push_last_sent_at": None,
            "push_sent_day": None, "push_sent_count": 0, "_live": live, **extra,
        }
        return gid

    def rpc(self, name: str, params: Dict[str, Any]) -> Any:
        self.calls.append((name, params))
        if self.fail is not None:
            raise self.fail
        if name == "widget_push_targets":
            return self._targets(**params)
        if name == "widget_push_settle":
            batch = params["p_outcomes"]
            assert isinstance(batch, list) and len(batch) <= 100
            self.settled.append(batch)
            return {"status": "settled", "skipped": 0, "cleared_bad": 0}
        raise AssertionError(f"unexpected rpc {name}")

    def _targets(self, p_scopes, p_all, p_grant_ids, p_after, p_limit):
        scopes = {s.strip().upper() for s in (p_scopes or [])}
        ids = set(p_grant_ids or [])
        out = []
        for gid in sorted(self.rows):
            row = self.rows[gid]
            if not row["_live"] or row["push_token"] is None:
                continue
            if p_after is not None and gid <= p_after:
                continue
            if not (p_all or gid in ids or (row["last_head_ticker"] in scopes)):
                continue
            out.append({k: v for k, v in row.items() if not k.startswith("_")})
        return out[: min(max(p_limit or 1000, 1), self.page_limit)]

    def targets_calls(self) -> List[Dict[str, Any]]:
        return [p for n, p in self.calls if n == "widget_push_targets"]

    def elements(self) -> List[Dict[str, Any]]:
        return [e for batch in self.settled for e in batch]


class _FakePush:
    """`send_widget_pushes` with scripted outcomes (default: accepted)."""

    def __init__(self) -> None:
        self.enabled = True
        self.sent: List[List[Dict[str, Any]]] = []
        self.script: Dict[str, tuple] = {}   # grant_id → (status, reason, ts)

    async def send_widget_pushes(self, devices, *, on_result=None, **_kw):
        self.sent.append([dict(d) for d in devices])
        results = []
        for d in devices:
            status, reason, ts = self.script.get(d["grant_id"], (WIDGET_ACCEPTED, "", None))
            r = WidgetPushResult(d["grant_id"], d["push_token"], d["push_environment"],
                                 status, reason, ts)
            results.append(r)
            if on_result is not None:
                on_result(r)
        return results

    def sent_ids(self) -> List[str]:
        return [d["grant_id"] for batch in self.sent for d in batch]


@pytest.fixture
def env(monkeypatch):
    clock = {"t": T0}
    db = _FakeDB()
    push = _FakePush()
    conf = _Settings(
        wps.settings,
        WIDGET_PUSH_ENABLED=True, WIDGET_HOLDINGS_GRANTS_ENABLED=True, PUSH_DRY_RUN=False,
        WIDGET_PUSH_MIN_INTERVAL_SECONDS=3600, WIDGET_PUSH_DAILY_CAP=12,
        WIDGET_PUSH_MAX_SENDS_PER_PASS=200,
    )
    monkeypatch.setattr(wps, "settings", conf)
    monkeypatch.setattr(wps, "session_phase", lambda now=None: SESSION_AFTERHOURS)
    monkeypatch.setattr(wps, "_last_schema_missing_error", 0.0)
    svc = wps.WidgetPushService(push_factory=lambda: push, clock=lambda: clock["t"])
    monkeypatch.setattr(svc, "_rpc_sync", db.rpc)

    class _Env:
        pass

    e = _Env()
    e.svc, e.db, e.push, e.clock, e.monkeypatch = svc, db, push, clock, monkeypatch

    def configure(**kw):
        monkeypatch.setattr(wps, "settings", _Settings(conf, **kw))

    e.configure = configure
    return e


# ── gates before any I/O ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switch_off_does_nothing(env):
    env.configure(WIDGET_PUSH_ENABLED=False)
    env.db.add(1)
    assert await env.svc.wake(["NVDA"], SESSION_AFTERHOURS) == {"skipped": "off"}
    assert env.db.calls == [] and env.push.sent == []


@pytest.mark.asyncio
async def test_grants_off_with_push_on_sends_nothing_and_warns_once(env, caplog):
    """Grants off (an incident, or step 1 of migration 216's teardown) while push stays on: the
    holdings route answers every woken tile 503, so a wake would only spend APNs sends and the
    device's WidgetKit push budget. No read, no send, owed wakes dropped, ONE warning per
    off-period (review 2026-11-27)."""
    env.configure(WIDGET_HOLDINGS_GRANTS_ENABLED=False)
    env.db.add(1)
    env.svc._pending.update({_gid(2): T0, _gid(3): T0})

    def _no_push():
        raise AssertionError("built the APNs sender with grants off")

    env.svc._push_factory = _no_push
    with caplog.at_level(logging.WARNING, logger=wps.logger.name):
        assert await env.svc.wake(["NVDA", MARKET_SCOPE], SESSION_AFTERHOURS) == {
            "skipped": "grants_off", "pending_cleared": 2}
        assert await env.svc.wake(["NVDA"], SESSION_AFTERHOURS) == {
            "skipped": "grants_off", "pending_cleared": 0}
    assert env.db.calls == [] and env.push.sent == [] and env.svc._pending == {}
    warned = [r for r in caplog.records if "WIDGET_HOLDINGS_GRANTS_ENABLED is off" in r.getMessage()]
    assert len(warned) == 1 and warned[0].levelno == logging.WARNING

    # Back on: wakes resume; off again: a fresh off-period warns once more.
    env.svc._push_factory = lambda: env.push
    env.configure(WIDGET_HOLDINGS_GRANTS_ENABLED=True)
    assert (await env.svc.wake(["NVDA"], SESSION_AFTERHOURS))["accepted"] == 1
    env.configure(WIDGET_HOLDINGS_GRANTS_ENABLED=False)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=wps.logger.name):
        await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert sum("WIDGET_HOLDINGS_GRANTS_ENABLED is off" in r.getMessage()
               for r in caplog.records) == 1


@pytest.mark.asyncio
async def test_push_off_wins_over_grants_off(env):
    env.configure(WIDGET_PUSH_ENABLED=False, WIDGET_HOLDINGS_GRANTS_ENABLED=False)
    assert await env.svc.wake(["NVDA"], SESSION_AFTERHOURS) == {"skipped": "off"}


@pytest.mark.asyncio
async def test_unconfigured_apns_does_nothing_and_warns_once(env, caplog):
    env.push.enabled = False
    env.db.add(1)
    with caplog.at_level(logging.WARNING, logger=wps.logger.name):
        for _ in range(3):
            assert await env.svc.wake(["NVDA"], SESSION_AFTERHOURS) == {
                "skipped": "apns_unconfigured"}
    assert env.db.calls == []
    assert sum("APNs is not configured" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("passed,live", [(SESSION_REGULAR, SESSION_AFTERHOURS),
                                         (SESSION_PREMARKET, SESSION_REGULAR)])
async def test_never_in_the_regular_session_and_pending_is_cleared(env, passed, live):
    env.svc._pending.update({_gid(1): T0, _gid(2): T0})
    env.monkeypatch.setattr(wps, "session_phase", lambda now=None: live)
    result = await env.svc.wake(["NVDA", MARKET_SCOPE], passed)
    assert result == {"skipped": "regular_session", "pending_cleared": 2}
    assert env.svc._pending == {} and env.db.calls == [] and env.push.sent == []


@pytest.mark.asyncio
async def test_nothing_to_do_means_no_io(env):
    assert await env.svc.wake([], SESSION_AFTERHOURS) == {"skipped": "nothing"}
    assert await env.svc.wake(["bad ticker", "X" * 40, 7, None], SESSION_AFTERHOURS) == {
        "skipped": "nothing"}
    # A bare string is one (dropped) scope, never four one-letter tickers.
    assert await env.svc.wake("NVDA", SESSION_AFTERHOURS) == {"skipped": "nothing"}
    assert env.db.calls == []


# ── targeting ────────────────────────────────────────────────────────────────────────────


def test_scope_normalisation():
    tickers, market, dropped = wps.normalise_scopes(
        [" nvda", "NVDA", "brk.b", MARKET_SCOPE, "bad ticker", "", None, 3, "X" * 33, "^GSPC"])
    assert tickers == ["NVDA", "BRK.B", "^GSPC"]
    assert market is True
    assert dropped == 5
    many, _, dropped = wps.normalise_scopes([f"T{i}" for i in range(wps.MAX_SCOPES + 7)])
    assert len(many) == wps.MAX_SCOPES and dropped == 7


@pytest.mark.asyncio
async def test_a_ticker_card_wakes_only_its_head_holders(env):
    env.db.add(1, head="NVDA")
    env.db.add(2, head="AAPL")
    env.db.add(3, head="NVDA", live=False)   # revoked / expired / evicted: never returned
    result = await env.svc.wake(["nvda"], SESSION_AFTERHOURS)
    (call,) = env.db.targets_calls()
    assert call == {"p_scopes": ["NVDA"], "p_all": False, "p_grant_ids": None,
                    "p_after": None, "p_limit": 1000}
    assert env.push.sent_ids() == [_gid(1)]
    assert result["accepted"] == 1 and result["targets"] == 1


@pytest.mark.asyncio
async def test_a_market_card_wakes_everyone_once(env):
    for i in range(1, 5):
        env.db.add(i, head=["NVDA", "AAPL", None, "BTCUSD"][i - 1])
    result = await env.svc.wake([MARKET_SCOPE, "NVDA", "AAPL"], SESSION_AFTERHOURS)
    (call,) = env.db.targets_calls()
    assert call["p_all"] is True and call["p_scopes"] == ["NVDA", "AAPL"]
    assert sorted(env.push.sent_ids()) == [_gid(i) for i in range(1, 5)]  # one wake per device
    assert result["sent"] == 4


@pytest.mark.asyncio
async def test_paging_follows_the_keyset_until_a_short_page(env):
    for i in range(1, 2006):
        env.db.add(i)
    env.configure(WIDGET_PUSH_MAX_SENDS_PER_PASS=2000)
    await env.svc.wake([MARKET_SCOPE], SESSION_AFTERHOURS)
    calls = env.db.targets_calls()
    assert [c["p_after"] for c in calls] == [None, _gid(1000), _gid(2000)]
    assert len(env.push.sent_ids()) == 2000  # the per-pass cap
    assert len(env.svc._pending) == 5


@pytest.mark.asyncio
async def test_a_keyset_that_does_not_advance_stops_the_read(env, caplog):
    env.db.add(1)
    env.db.page_limit = 1000

    def stuck(name, params):
        env.db.calls.append((name, params))
        if name == "widget_push_targets":
            return [dict(env.db.rows[_gid(1)], _live=None)] * 1000
        return {"status": "settled"}

    env.monkeypatch.setattr(env.svc, "_rpc_sync", stuck)
    with caplog.at_level(logging.WARNING, logger=wps.logger.name):
        await env.svc.wake([MARKET_SCOPE], SESSION_AFTERHOURS)
    assert len(env.db.targets_calls()) == 2
    assert any("did not advance" in r.getMessage() for r in caplog.records)
    assert env.push.sent_ids() == [_gid(1)]  # de-duplicated


@pytest.mark.asyncio
async def test_malformed_rows_are_skipped_never_crash(env):
    env.db.add(1)

    def odd(name, params):
        env.db.calls.append((name, params))
        if name == "widget_push_targets":
            good = {k: v for k, v in env.db.rows[_gid(1)].items() if not k.startswith("_")}
            return ["junk", None, {"grant_id": _gid(9)}, {"grant_id": 5, "push_token": "x",
                                                         "push_environment": "sandbox"},
                    good, dict(good)]
        return {"status": "settled"}

    env.monkeypatch.setattr(env.svc, "_rpc_sync", odd)
    result = await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert env.push.sent_ids() == [_gid(1)]
    assert result["malformed"] == 4


@pytest.mark.asyncio
async def test_a_non_list_answer_is_a_failed_read(env):
    env.monkeypatch.setattr(env.svc, "_rpc_sync", lambda n, p: {"oops": True})
    assert await env.svc.wake(["NVDA"], SESSION_AFTERHOURS) == {"error": "targets_read_failed"}


# ── interval, cap, pending ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_second_wake_inside_the_interval_coalesces_then_flushes_once(env):
    env.db.add(1)
    await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert env.push.sent_ids() == [_gid(1)]

    env.clock["t"] += 600
    result = await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert result["coalesced"] == 1 and env.push.sent_ids() == [_gid(1)]
    assert _gid(1) in env.svc._pending

    env.clock["t"] += 3600
    env.db.calls.clear()
    result = await env.svc.wake([], SESSION_AFTERHOURS)   # an empty pass flushes what is owed
    (call,) = env.db.targets_calls()
    assert call["p_grant_ids"] == [_gid(1)] and call["p_scopes"] == [] and call["p_all"] is False
    assert env.push.sent_ids() == [_gid(1), _gid(1)]
    assert env.svc._pending == {}


@pytest.mark.asyncio
async def test_a_pending_grant_that_died_is_never_pushed(env):
    env.db.add(1)
    env.svc._pending[_gid(1)] = T0
    env.db.rows[_gid(1)]["_live"] = False   # signed out of all devices meanwhile
    result = await env.svc.wake([], SESSION_AFTERHOURS)
    assert env.push.sent == [] and env.svc._pending == {}
    assert result["targets"] == 0


@pytest.mark.asyncio
async def test_the_interval_survives_a_restart_through_the_db_stamp(env):
    sent_at = datetime.fromtimestamp(T0 - 600, timezone.utc).isoformat()
    env.db.add(1, push_last_sent_at=sent_at)
    env.db.add(2, push_last_sent_at=datetime.fromtimestamp(T0 - 7200, timezone.utc).isoformat())
    result = await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)   # a fresh process: no local state
    assert env.push.sent_ids() == [_gid(2)]
    assert result["coalesced"] == 1


@pytest.mark.asyncio
async def test_the_daily_cap_survives_a_restart_and_resets_on_a_new_et_day(env):
    env.db.add(1, push_sent_day=TODAY, push_sent_count=12)
    env.db.add(2, push_sent_day="2026-11-23", push_sent_count=12)   # yesterday's count
    env.db.add(3, push_sent_day=TODAY, push_sent_count=11)
    result = await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert sorted(env.push.sent_ids()) == [_gid(2), _gid(3)]
    assert result["capped"] == 1


@pytest.mark.asyncio
async def test_the_local_count_caps_and_resets_at_et_midnight(env):
    env.configure(WIDGET_PUSH_DAILY_CAP=2, WIDGET_PUSH_MIN_INTERVAL_SECONDS=900)
    env.db.add(1)
    for _ in range(3):
        await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
        env.clock["t"] += 1000
    assert env.push.sent_ids() == [_gid(1)] * 2
    assert _gid(1) not in env.svc._pending   # capped is dropped, never owed

    # 18:50 ET + 6 h → 00:50 ET the next day: a new ET day, a new allowance.
    env.clock["t"] += 6 * 3600
    await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert env.push.sent_ids() == [_gid(1)] * 3


@pytest.mark.asyncio
async def test_a_far_future_db_stamp_holds_one_interval_never_more(env):
    env.db.add(1, push_last_sent_at="2099-01-01T00:00:00+00:00")
    await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert env.push.sent == []
    env.clock["t"] += 3601
    env.db.rows[_gid(1)]["push_last_sent_at"] = None
    await env.svc.wake([], SESSION_AFTERHOURS)   # owed from the first pass
    assert env.push.sent_ids() == [_gid(1)]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["yesterday", "not a date", 5, None])
async def test_an_unreadable_db_stamp_never_blocks_a_send(env, bad):
    env.db.add(1, push_last_sent_at=bad, push_sent_count=True, push_sent_day=TODAY)
    await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert env.push.sent_ids() == [_gid(1)]


@pytest.mark.asyncio
async def test_300_targets_send_200_and_keep_100_pending(env):
    for i in range(1, 301):
        env.db.add(i)
    result = await env.svc.wake([MARKET_SCOPE], SESSION_AFTERHOURS)
    assert len(env.push.sent_ids()) == 200
    assert result["deferred"] == 100 and len(env.svc._pending) == 100
    assert set(env.svc._pending) == {_gid(i) for i in range(201, 301)}
    # Settled in batches of at most 100 (the RPC refuses more).
    assert all(len(b) <= 100 for b in env.db.settled)
    assert len(env.db.elements()) == 200

    # The next pass sends the owed hundred first, even with no new card.
    env.clock["t"] += 300
    await env.svc.wake([], SESSION_AFTERHOURS)
    assert len(env.push.sent_ids()) == 300
    assert env.svc._pending == {}


@pytest.mark.asyncio
async def test_owed_wakes_go_before_this_passes_new_ones(env):
    env.configure(WIDGET_PUSH_MAX_SENDS_PER_PASS=10)
    for i in range(1, 31):
        env.db.add(i, head="NVDA" if i <= 20 else "AAPL")
    env.svc._pending.update({_gid(i): T0 - 100 for i in range(21, 31)})
    await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert sorted(env.push.sent_ids()) == [_gid(i) for i in range(21, 31)]


@pytest.mark.asyncio
async def test_pending_ids_are_read_in_chunks_of_1000(env):
    env.svc._pending.update({_gid(i): T0 for i in range(2500)})
    await env.svc.wake([], SESSION_AFTERHOURS)
    sizes = [len(c["p_grant_ids"]) for c in env.db.targets_calls()]
    assert sizes == [1000, 1000, 500]
    assert env.svc._pending == {}   # none of them is live in the fake


@pytest.mark.asyncio
async def test_a_market_wake_answers_for_pending_without_extra_reads(env):
    env.db.add(1)
    env.svc._pending.update({_gid(1): T0, _gid(9): T0})   # 9 is not live
    await env.svc.wake([MARKET_SCOPE], SESSION_AFTERHOURS)
    assert len(env.db.targets_calls()) == 1
    assert env.push.sent_ids() == [_gid(1)] and env.svc._pending == {}


@pytest.mark.asyncio
async def test_stale_pending_marks_expire_after_six_hours(env):
    env.svc._pending[_gid(1)] = T0 - 7 * 3600
    env.db.add(1)
    assert await env.svc.wake([], SESSION_AFTERHOURS) == {"skipped": "nothing"}
    assert env.db.calls == []


# ── settle ───────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_settle_carries_the_exact_token_sent_and_the_410_timestamp(env):
    for i in range(1, 7):
        env.db.add(i)
    env.push.script = {
        _gid(1): (WIDGET_ACCEPTED, "", None),
        _gid(2): (WIDGET_GONE, "410 Unregistered", 1764000000123),
        _gid(3): (WIDGET_BAD, "400 BadDeviceToken", None),
        _gid(4): (WIDGET_RETRY, "503 ServiceUnavailable", None),
        _gid(5): (WIDGET_PROVIDER_FAULT, "403 InvalidProviderToken", None),
        _gid(6): (WIDGET_NOT_SENT, "environment_stopped: provider", None),
    }
    result = await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    by_gid = {e["grant_id"]: e for e in env.db.elements()}
    assert set(by_gid) == {_gid(1), _gid(2), _gid(3), _gid(4)}   # not_sent writes nothing
    for gid, element in by_gid.items():
        assert element["token"] == env.db.rows[gid]["push_token"]
    assert by_gid[_gid(1)] == {"grant_id": _gid(1), "token": _tok(1), "outcome": "accepted",
                               "apns_ts": None, "count": True}
    assert by_gid[_gid(2)]["outcome"] == "gone" and by_gid[_gid(2)]["apns_ts"] == 1764000000123
    assert by_gid[_gid(3)] == {"grant_id": _gid(3), "token": _tok(3), "outcome": "bad",
                               "apns_ts": None, "error": "400 BadDeviceToken"}
    assert by_gid[_gid(4)]["outcome"] == "retry"
    assert result["accepted"] == 1 and result["gone"] == 1 and result["bad"] == 1
    assert result["retry"] == 1 and result["not_sent"] == 2
    # Local settle: accepted + bad count and stamp; retry / not_sent stay owed; gone is dropped.
    assert env.svc._count == {_gid(1): 1, _gid(3): 1}
    assert set(env.svc._pending) == {_gid(4), _gid(5), _gid(6)}


def test_settle_element_rules():
    r = WidgetPushResult(_gid(1), _tok(1), "sandbox", WIDGET_NOT_SENT, "invalid_device: x")
    assert wps.settle_outcome(r) is None
    assert wps.settle_outcome(r._replace(status=WIDGET_PROVIDER_FAULT)) is None
    assert wps.settle_outcome(r._replace(grant_id="")) is None
    assert wps.settle_outcome(r._replace(status="weird")) is None
    long = r._replace(status=WIDGET_RETRY, reason="x" * 500)
    assert len(wps.settle_outcome(long)["error"]) == 200
    gone_no_ts = r._replace(status=WIDGET_GONE, reason="410 Unregistered")
    assert wps.settle_outcome(gone_no_ts)["apns_ts"] is None
    assert wps.settle_outcome(r._replace(status=WIDGET_ACCEPTED), count=False)["count"] is False


@pytest.mark.asyncio
async def test_a_429_waits_an_interval_but_stays_owed(env):
    env.db.add(1)
    env.push.script = {_gid(1): (WIDGET_RETRY, "429 TooManyRequests", None)}
    await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert _gid(1) in env.svc._pending
    env.push.script = {}
    env.clock["t"] += 300
    await env.svc.wake([], SESSION_AFTERHOURS)
    assert len(env.push.sent_ids()) == 1   # inside the interval
    env.clock["t"] += 3600
    await env.svc.wake([], SESSION_AFTERHOURS)
    assert len(env.push.sent_ids()) == 2


def test_the_settler_batches_at_100_and_flushes_once_a_second():
    async def run():
        written: List[int] = []

        async def write(batch):
            written.append(len(batch))

        now = {"t": 0.0}
        settler = wps._Settler(write, clock=lambda: now["t"])
        for i in range(250):
            settler.add({"i": i})
        await settler.drain()
        assert written == [100, 100, 50]

        written.clear()
        settler = wps._Settler(write, clock=lambda: now["t"])
        for i in range(30):
            now["t"] += 0.1
            settler.add({"i": i})
        await settler.drain()
        assert sum(written) == 30 and all(n <= 11 for n in written) and len(written) >= 3

    asyncio.run(run())


@pytest.mark.asyncio
async def test_a_failed_settle_never_raises(env, caplog):
    env.db.add(1)

    def flaky(name, params):
        env.db.calls.append((name, params))
        if name == "widget_push_settle":
            raise RuntimeError("pool exhausted")
        return env.db._targets(**params)

    env.monkeypatch.setattr(env.svc, "_rpc_sync", flaky)
    with caplog.at_level(logging.WARNING, logger=wps.logger.name):
        result = await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert result["accepted"] == 1
    assert any("settle of 1 outcome" in r.getMessage() for r in caplog.records)
    assert env.svc._count == {_gid(1): 1}   # the local throttle still holds


# ── dry run ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_sends_nothing_and_stamps_without_counting(env, caplog):
    env.configure(PUSH_DRY_RUN=True)
    env.db.add(1)
    env.svc._pending[_gid(1)] = T0
    with caplog.at_level(logging.INFO, logger=wps.logger.name):
        result = await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert env.push.sent == []
    assert env.db.elements() == [{"grant_id": _gid(1), "token": _tok(1), "outcome": "accepted",
                                  "apns_ts": None, "count": False}]
    assert env.svc._count == {} and env.svc._pending == {}
    assert result["dry_run"] is True
    assert any("DRY RUN" in r.getMessage() for r in caplog.records)
    # Stamped: the next pass inside the interval does not re-read it as due.
    env.clock["t"] += 300
    result = await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert result["coalesced"] == 1


# ── failure paths ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_targets_read_sends_nothing_and_keeps_pending(env, caplog):
    env.svc._pending[_gid(5)] = T0
    env.db.fail = RuntimeError("connection reset")
    with caplog.at_level(logging.WARNING, logger=wps.logger.name):
        result = await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert result == {"error": "targets_read_failed"}
    assert env.push.sent == [] and _gid(5) in env.svc._pending
    assert any("targets read failed" in r.getMessage() for r in caplog.records)


class _PgrstError(Exception):
    def __init__(self, code):
        super().__init__(f"code {code}")
        self.code = code


@pytest.mark.asyncio
async def test_a_missing_migration_is_a_throttled_error(env, caplog):
    env.db.fail = _PgrstError("PGRST202")
    with caplog.at_level(logging.WARNING, logger=wps.logger.name):
        for _ in range(3):
            assert await env.svc.wake(["NVDA"], SESSION_AFTERHOURS) == {"error": "schema_missing"}
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "216_widget_holdings_grants" in errors[0].getMessage()
    assert env.push.sent == []


@pytest.mark.asyncio
async def test_a_bug_anywhere_is_caught_and_logged(env, caplog):
    def boom():
        raise RuntimeError("push construction failed")

    env.svc._push_factory = boom
    with caplog.at_level(logging.WARNING, logger=wps.logger.name):
        assert await env.svc.wake(["NVDA"], SESSION_AFTERHOURS) == {"error": "RuntimeError"}
    assert any("wake failed" in r.getMessage() for r in caplog.records)
    assert env.svc._running is False


@pytest.mark.asyncio
async def test_a_wake_still_running_skips_the_next_one(env):
    env.svc._running = True
    env.db.add(1)
    assert await env.svc.wake(["NVDA"], SESSION_AFTERHOURS) == {"skipped": "busy"}
    assert env.db.calls == []


@pytest.mark.asyncio
async def test_cancellation_propagates_settles_what_was_sent_and_keeps_the_rest(env):
    for i in range(1, 6):
        env.db.add(i)

    order: List[str] = []

    async def cut_short(devices, *, on_result=None, **_kw):
        order.extend(d["grant_id"] for d in devices)
        for d in devices[:2]:
            on_result(WidgetPushResult(d["grant_id"], d["push_token"], d["push_environment"],
                                       WIDGET_ACCEPTED))
        raise asyncio.CancelledError

    env.push.send_widget_pushes = cut_short
    with pytest.raises(asyncio.CancelledError):
        await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    await asyncio.sleep(0.05)   # the abandoned settle runs on its own
    assert [e["grant_id"] for e in env.db.elements()] == order[:2]
    assert set(env.svc._pending) == set(order[2:]) and len(order) == 5
    assert env.svc._running is False


async def _background_settled(timeout: float = 5.0) -> None:
    """Wait for the abandoned settle writes (`wps._BACKGROUND`) to finish."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while wps._BACKGROUND and loop.time() < deadline:
        await asyncio.sleep(0.01)
    assert not wps._BACKGROUND, "abandoned settle writes never finished"


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, True])
async def test_a_cut_while_draining_the_writes_still_settles_every_batch(env, dry_run):
    """The sweeper's budget cancels the wake right after the sends returned, while the settle
    writes drain. Every flushed batch must still reach `widget_push_settle` — the DB interval
    stamp, the ET-day count and the 410 clears survive a restart only through it. A `gather`
    there cancelled its children: the last batch's task never started and was lost
    (review 2026-11-27)."""
    env.configure(PUSH_DRY_RUN=dry_run)
    for i in range(1, 151):
        env.db.add(i)

    original = wps._Settler.drain

    async def drain_then_cut(self):
        # The cancellation lands on the wake's first suspension inside `drain`, after its final
        # flush scheduled the last batch — exactly where the pass budget cuts in production.
        asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
        await original(self)

    env.monkeypatch.setattr(wps._Settler, "drain", drain_then_cut)
    with pytest.raises(asyncio.CancelledError):
        await env.svc.wake([MARKET_SCOPE], SESSION_AFTERHOURS)
    await _background_settled()

    expected = sorted(_gid(i) for i in range(1, 151))
    if not dry_run:
        assert sorted(env.push.sent_ids()) == expected
    assert sorted(e["grant_id"] for e in env.db.elements()) == expected
    assert [len(b) for b in env.db.settled] == [100, 50]
    assert all(e["count"] is (not dry_run) for e in env.db.elements())
    assert env.svc._running is False


def test_the_settler_keeps_its_writes_running_when_its_drain_is_cancelled():
    """`_Settler` alone: cancel `drain` one tick in — every batch is still written."""
    async def run():
        written: List[int] = []
        gate = asyncio.Event()

        async def write(batch):
            await gate.wait()
            written.append(len(batch))

        settler = wps._Settler(write, clock=lambda: 0.0)
        for i in range(130):
            settler.add({"i": i})
        drain = asyncio.ensure_future(settler.drain())
        await asyncio.sleep(0)
        drain.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drain
        gate.set()
        await _background_settled()
        assert sorted(written) == [30, 100]

    asyncio.run(run())


@pytest.mark.asyncio
async def test_a_sender_that_raises_still_settles_what_it_reported(env):
    env.db.add(1)
    env.db.add(2)

    order: List[str] = []

    async def half(devices, *, on_result=None, **_kw):
        order.extend(d["grant_id"] for d in devices)
        d = devices[0]
        on_result(WidgetPushResult(d["grant_id"], d["push_token"], d["push_environment"],
                                   WIDGET_ACCEPTED))
        raise RuntimeError("sender bug")

    env.push.send_widget_pushes = half
    result = await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    assert result["accepted"] == 1
    assert [e["grant_id"] for e in env.db.elements()] == order[:1]
    assert set(env.svc._pending) == set(order[1:]) and len(order) == 2


@pytest.mark.asyncio
async def test_the_send_order_is_not_the_grant_order(env):
    """The sender's topic probe reads each environment's first three devices; a fixed grant_id
    order would let the owner of the three lowest ids stop an environment every pass."""
    for i in range(1, 61):
        env.db.add(i)
    await env.svc.wake([MARKET_SCOPE], SESSION_AFTERHOURS)
    sent = env.push.sent_ids()
    assert sorted(sent) == [_gid(i) for i in range(1, 61)]
    assert sent != sorted(sent)


@pytest.mark.asyncio
async def test_one_info_summary_per_wake(env, caplog):
    env.db.add(1)
    with caplog.at_level(logging.INFO, logger=wps.logger.name):
        result = await env.svc.wake(["NVDA"], SESSION_AFTERHOURS)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("widget push: phase=")]
    assert len(lines) == 1
    assert "targets=1 due=1 sent=1 accepted=1" in lines[0]
    assert result["phase"] == SESSION_AFTERHOURS


def test_the_singleton():
    assert wps.get_widget_push_service() is wps.get_widget_push_service()


def test_et_day_boundary():
    # 23:59 ET and 00:01 ET are different days, whatever the server's own timezone.
    before = datetime(2026, 11, 24, 23, 59, tzinfo=ET).timestamp()
    after = before + 120
    assert wps._et_day(before) == "2026-11-24" and wps._et_day(after) == "2026-11-25"
