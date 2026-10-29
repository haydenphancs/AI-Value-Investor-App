"""The ONE Telegram pacer + flood back-off (`app/services/marketing/telegram_pacing.py`) and `send_audio`.

Plan 2026-10-22 §1.9: every sender to the owner's review chat — the marketing review sweep, the publish
feed, the digest / run-health reports and the Learn weekly sweep — shares one pacer (an `asyncio.Lock`
around one last-send stamp) and one flood back-off, because Telegram's limits are per CHAT ("In a single
chat, avoid sending more than one message per second"; a 429 closes the chat for every sender).
`review_service._rate_limited_until` / `.SEND_SPACING_SECONDS` keep working by name (forwarded), so the
existing marketing suites run unchanged against the shared state.

Hermetic: Telegram is an `httpx.MockTransport` (conftest blocks the network anyway), the pacer runs on a
FAKE clock whose `sleep` advances it, so "≥ 1.1 s apart" is asserted exactly and nothing really waits.

Mutation-checked by hand (2026-10-22, in-memory mutant copies only — no repo file touched): giving each
sweep its own pacer again (the pre-2026-10-22 `_Pacer`, kept in this file as `_OldPerSweepPacer`) is what
`test_per_sweep_pacers_would_not_space_two_sweeps` shows failing the spacing assertion; dropping the
back-off re-check AFTER the wait in `telegram_pacing.wait_turn` turned
`test_a_back_off_that_starts_during_the_wait_stops_the_waiter` red, and dropping the check on ENTRY
turned `test_a_429_in_one_sweep_sets_the_shared_back_off_and_stops_the_other` red; `pacer = _Pacer()` in
`publish_feed.feed_cycle` and `SharedPacer()` for `digest_service._PACER` each turned the construction scan
red; `time.monotonic() < _rate_limited_until` back in `review_service.review_cycle` turned the bare-name
scan red; deleting the size guard in `send_audio` turned the three size-refusal cases red.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import math
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pytest

from app.api.error_response import ErrorCode, classify_exception
from app.config import settings
from app.integrations import telegram
from app.integrations.telegram import (
    MAX_URL_SEND_BYTES,
    TelegramFileTooLargeError,
    TelegramFloodControlActive,
    TelegramRateLimitException,
    TelegramRequestError,
)
from app.services.marketing import digest_service as ds
from app.services.marketing import publish_feed as feed
from app.services.marketing import review_service as rs
from app.services.marketing import telegram_pacing as tp

_REAL_SLEEP = asyncio.sleep
_BACKEND = Path(__file__).resolve().parents[1]
_BOT_ID = "123456789"
_BOT_SECRET = "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawZ"   # 35 chars, the real shape
TOKEN = f"{_BOT_ID}:{_BOT_SECRET}"
CHAT = 424242
#: A signed Storage URL is a read credential: it must never reach a log line or an exception message.
SIGNED_URL = ("https://example.supabase.co/storage/v1/object/sign/money-moves-media/audio/weekly/"
              "coreweave-0123456789ab.m4a?token=SIGNED-SENTINEL-c0ffee")
EPS = 1e-9


class FakeClock:
    """`time.monotonic` for telegram_pacing; `sleep` advances it and yields to the loop, so concurrent
    sweeps interleave the way they would on real I/O."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.slept: List[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += max(0.0, seconds)
        await _REAL_SLEEP(0)


@pytest.fixture(autouse=True)
def _fresh_pacing(monkeypatch):
    """Every test starts with no back-off, the default spacing and no pacer state from another test."""
    monkeypatch.setattr(tp, "_STATE", None)
    monkeypatch.setattr(tp, "_rate_limited_until", 0.0)
    monkeypatch.setattr(tp, "SEND_SPACING_SECONDS", tp.DEFAULT_SEND_SPACING_SECONDS)


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    # Rebind ONLY telegram_pacing's view of `time` and `asyncio` — the event loop keeps its real clock.
    monkeypatch.setattr(tp, "time", SimpleNamespace(monotonic=fake.monotonic))
    monkeypatch.setattr(tp, "asyncio", SimpleNamespace(sleep=fake.sleep, Lock=asyncio.Lock,
                                                       get_running_loop=asyncio.get_running_loop))
    return fake


class FakeTelegram:
    """The Bot API behind httpx.MockTransport. `script[method]` = queued answers (status, body)."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        self.script: Dict[str, List[Tuple[int, Any]]] = {}
        self.on_call = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload, dict(request.extensions.get("timeout") or {})))
        if self.on_call is not None:
            self.on_call(method, payload)
        queued = self.script.get(method) or []
        if queued:
            status, body = queued.pop(0)
            return httpx.Response(status, json=body)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(self.calls)}})


@pytest.fixture
def tg(monkeypatch) -> FakeTelegram:
    fake = FakeTelegram()
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(telegram, "_client", httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    return fake


_TG_429 = (429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 30",
                 "parameters": {"retry_after": 30}})


# ── one pacer for every sender ─────────────────────────────────────────────────────────────────


async def _paced_sweep(name: str, n: int, sent: List[Tuple[str, float]], clock: FakeClock, pacer: Any) -> None:
    for _ in range(n):
        await pacer.wait()
        sent.append((name, clock.now))
        await _REAL_SLEEP(0)   # a real send awaits I/O: let the other sweep in


def _gaps(sent: List[Tuple[str, float]]) -> List[float]:
    times = [t for _, t in sent]
    return [b - a for a, b in zip(times, times[1:])]


@pytest.mark.asyncio
async def test_two_concurrent_sweeps_are_spaced_by_the_one_shared_pacer(clock):
    sent: List[Tuple[str, float]] = []
    # Each sweep holds its OWN handle, as review_service / publish_feed / digest do — the state is shared.
    await asyncio.gather(_paced_sweep("review", 3, sent, clock, rs._Pacer()),
                         _paced_sweep("learn", 3, sent, clock, tp.SharedPacer()))
    assert len(sent) == 6 and {n for n, _ in sent} == {"review", "learn"}
    assert [t for _, t in sent] == sorted(t for _, t in sent)
    assert all(g >= tp.SEND_SPACING_SECONDS - EPS for g in _gaps(sent)), _gaps(sent)
    # anti-vacuity: the two sweeps really ran at the same time (interleaved, not one after the other)
    names = [n for n, _ in sent]
    assert names != sorted(names) and names != sorted(names, reverse=True)


class _OldPerSweepPacer:
    """The pre-2026-10-22 `review_service._Pacer`, verbatim in behaviour: one per sweep."""

    def __init__(self, clock: FakeClock) -> None:
        self._last: Optional[float] = None
        self._clock = clock

    async def wait(self) -> None:
        if self._last is not None:
            delay = tp.DEFAULT_SEND_SPACING_SECONDS - (self._clock.monotonic() - self._last)
            if delay > 0:
                await self._clock.sleep(delay)
        self._last = self._clock.monotonic()


@pytest.mark.asyncio
async def test_per_sweep_pacers_would_not_space_two_sweeps(clock):
    """The harness above detects the bug it exists for: two per-sweep pacers send back to back."""
    sent: List[Tuple[str, float]] = []
    await asyncio.gather(_paced_sweep("review", 3, sent, clock, _OldPerSweepPacer(clock)),
                         _paced_sweep("feed", 3, sent, clock, _OldPerSweepPacer(clock)))
    assert min(_gaps(sent)) < tp.SEND_SPACING_SECONDS


@pytest.mark.asyncio
async def test_a_single_sweep_behaves_as_before(clock):
    """First send at once; every later one SEND_SPACING_SECONDS after the previous — the per-sweep
    `_Pacer`'s exact behaviour when nobody else is sending."""
    sent: List[Tuple[str, float]] = []
    await _paced_sweep("review", 4, sent, clock, tp.PACER)
    assert sent[0][1] == 1000.0
    assert len(clock.slept) == 3 and all(0 < s <= tp.SEND_SPACING_SECONDS for s in clock.slept)
    assert all(abs(g - tp.SEND_SPACING_SECONDS) < EPS for g in _gaps(sent))


@pytest.mark.asyncio
async def test_a_sweep_starting_after_another_waits_only_the_rest_of_the_gap(clock):
    """The feed's first message used to follow the review sweep's last one at once."""
    sent: List[Tuple[str, float]] = []
    await _paced_sweep("review", 2, sent, clock, tp.PACER)
    clock.now += 0.4                                    # the review sweep's stamp writes take 0.4 s
    await _paced_sweep("feed", 1, sent, clock, tp.PACER)
    assert _gaps(sent)[-1] == pytest.approx(tp.SEND_SPACING_SECONDS)
    assert clock.slept[-1] == pytest.approx(tp.SEND_SPACING_SECONDS - 0.4)
    clock.now += 5.0                                    # long after: no wait at all
    before = len(clock.slept)
    await _paced_sweep("digest", 1, sent, clock, tp.PACER)
    assert len(clock.slept) == before


@pytest.mark.asyncio
async def test_zero_spacing_never_sleeps(clock, monkeypatch):
    monkeypatch.setattr(rs, "SEND_SPACING_SECONDS", 0.0)      # what the marketing suites do
    sent: List[Tuple[str, float]] = []
    await asyncio.gather(_paced_sweep("a", 3, sent, clock, tp.PACER), _paced_sweep("b", 3, sent, clock, tp.PACER))
    assert len(sent) == 6 and clock.slept == []


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", [float("nan"), float("inf"), -1.0, True, "1.1", None])
async def test_a_junk_spacing_set_on_the_module_keeps_the_default_spacing_and_logs(clock, monkeypatch, caplog, junk):
    monkeypatch.setattr(tp, "SEND_SPACING_SECONDS", junk)
    caplog.set_level(logging.ERROR, logger=tp.__name__)
    sent: List[Tuple[str, float]] = []
    await _paced_sweep("review", 2, sent, clock, tp.PACER)
    assert _gaps(sent) == [pytest.approx(tp.DEFAULT_SEND_SPACING_SECONDS)]
    assert any("SEND_SPACING_SECONDS is unusable" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_cancelled_wait_takes_no_slot_and_frees_the_lock(clock):
    await tp.PACER.wait()                               # slot 1 at t=1000
    first = clock.now
    waiter = asyncio.ensure_future(tp.PACER.wait())     # will sleep for the rest of the gap
    await _REAL_SLEEP(0)
    assert clock.slept, "the second wait never started sleeping"
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    state = tp._STATE
    assert state is not None and state.last_send == first and not state.lock.locked()
    await asyncio.wait_for(tp.PACER.wait(), timeout=1)  # the next sender is not stuck behind it


# ── the flood back-off ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_429_in_one_sweep_sets_the_shared_back_off_and_stops_the_other(clock, tg):
    """Sweep B's send is answered 429 (retry_after 30): B records it and stops. Sweep A, already
    running, is refused by the pacer at its next send — no request — and stops through the SAME
    `except TelegramRateLimitException` every sweep has. One back-off, readable by the old name."""
    tg.script["sendMessage"] = [(200, {"ok": True, "result": {"message_id": 1}}), _TG_429]
    stopped: Dict[str, BaseException] = {}
    sent: List[Tuple[str, float]] = []

    async def sweep(name: str, n: int) -> None:
        try:
            for i in range(n):
                await tp.PACER.wait()
                await telegram.send_message(CHAT, f"{name} {i}")
                sent.append((name, clock.now))
        except TelegramRateLimitException as e:        # the handler every sweep already has
            tp.note_rate_limit(e.retry_after)
            stopped[name] = e

    await asyncio.gather(sweep("review", 5), sweep("learn", 5))
    assert [m for m, _, _ in tg.calls] == ["sendMessage", "sendMessage"]     # nothing after the 429
    assert len(sent) == 1 and set(stopped) == {"review", "learn"}
    by_type = {type(e) for e in stopped.values()}
    assert by_type == {TelegramRateLimitException, TelegramFloodControlActive}
    refused = next(e for e in stopped.values() if isinstance(e, TelegramFloodControlActive))
    assert refused.retry_after is not None and refused.retry_after <= 30 and refused.method == "paced_send"
    # The back-off: 30 s from the 429, which landed at the second slot (t = 1001.1) or — when the first
    # sweep was already waiting out its next gap — one gap later; never shortened by the refused sweep's
    # own note (which may round the seconds left up by < 1 s).
    second_slot = 1000.0 + tp.SEND_SPACING_SECONDS
    assert second_slot + 30 - EPS <= tp.rate_limited_until() <= second_slot + tp.SEND_SPACING_SECONDS + 30 + 1 + EPS
    assert rs._rate_limited_until == tp.rate_limited_until()              # one back-off, by its old name
    # …and while it is in force no sender sends at all; once it has passed, the next one goes.
    with pytest.raises(TelegramFloodControlActive):
        await tp.PACER.wait()
    clock.now = tp.rate_limited_until() + 0.01
    await tp.PACER.wait()


@pytest.mark.asyncio
async def test_a_back_off_that_starts_during_the_wait_stops_the_waiter(clock, monkeypatch):
    await tp.PACER.wait()
    real_sleep = clock.sleep

    async def sleep_while_another_sender_gets_a_429(seconds: float) -> None:
        await real_sleep(seconds)
        tp.note_rate_limit(12)

    monkeypatch.setattr(tp.asyncio, "sleep", sleep_while_another_sender_gets_a_429)
    stamp = tp._STATE.last_send
    with pytest.raises(TelegramFloodControlActive) as info:
        await tp.PACER.wait()
    assert info.value.retry_after == 12 and tp._STATE.last_send == stamp   # refused: no slot taken


@pytest.mark.asyncio
async def test_the_review_sweep_stops_on_another_senders_back_off_mid_sweep(clock, monkeypatch, tg):
    """The REAL `review_cycle` outer handler: a back-off set by another sender between two runs stops
    the sweep at its next send (rate_limited), and its note never SHORTENS that back-off."""
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_REVIEW_CHAT_ID", CHAT)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", "s3cret_Webhook-token")
    monkeypatch.setattr(settings, "MARKETING_PUBLIC_BASE_URL", "https://caydexinvest.com")
    monkeypatch.setattr(settings, "MARKETING_REVIEW_BUNDLES", False)
    monkeypatch.setattr(rs, "get_marketing_run_service", lambda: object())

    async def two_runs(_svc: Any) -> List[Dict[str, Any]]:
        return [{"id": "p1", "run_id": "r1"}, {"id": "p2", "run_id": "r2"}]

    runs: List[str] = []

    async def fake_review_run(_svc, chat_id, run_id, posts, pacer, counters) -> None:
        if run_id == "r2":
            tp.note_rate_limit(600)        # the Learn sweep's 429, between the review sweep's two runs
        await pacer.wait()
        await telegram.send_message(chat_id, run_id)
        runs.append(run_id)
        counters["sent"] += 1

    monkeypatch.setattr(rs, "_pending_unnotified", two_runs)
    monkeypatch.setattr(rs, "_review_run", fake_review_run)
    counters = await rs.review_cycle()
    until = tp.rate_limited_until()
    assert runs == ["r1"] and counters["rate_limited"] == 1 and counters["sent"] == 1
    assert len(tg.calls) == 1
    assert until >= 1000.0 + 600 - EPS                  # the 600 s back-off was not cut short
    assert (await rs.review_cycle())["rate_limited"] == 1 and len(tg.calls) == 1   # held off up front


def test_note_rate_limit_never_shortens_and_defaults_a_missing_retry_after(clock):
    assert tp.note_rate_limit(None) == pytest.approx(tp.DEFAULT_RETRY_AFTER_SECONDS)
    assert tp.rate_limited_until() == pytest.approx(1000.0 + 60)
    tp.note_rate_limit(5)                                # a shorter answer does not reopen the chat
    assert tp.rate_limited_until() == pytest.approx(1060.0)
    tp.note_rate_limit(120)
    assert tp.rate_limited_until() == pytest.approx(1120.0)
    assert tp.is_rate_limited() and tp.seconds_left() == pytest.approx(120.0)
    clock.now = 1120.0
    assert not tp.is_rate_limited() and tp.seconds_left() == 0.0


@pytest.mark.parametrize("junk, expected", [
    (True, 60), (False, 60), (-1, 60), (float("nan"), 60), (float("-inf"), 60), ("30", 60), ([30], 60),
    (float("inf"), 60), (0, 0), (2.5, 2.5), (10 ** 9, tp.MAX_RETRY_AFTER_SECONDS),
])
def test_note_rate_limit_with_outliers(clock, caplog, junk, expected):
    caplog.set_level(logging.WARNING, logger=tp.__name__)
    assert tp.note_rate_limit(junk) == pytest.approx(expected)
    assert math.isfinite(tp.rate_limited_until())
    warned = any("unusable retry_after" in r.getMessage() for r in caplog.records)
    assert warned == (expected == 60)


@pytest.mark.parametrize("value, error", [
    (True, TypeError), ("1", TypeError), (None, TypeError), (float("nan"), ValueError),
    (float("inf"), ValueError), (float("-inf"), ValueError),
])
def test_the_back_off_refuses_a_value_that_would_silently_disable_or_freeze_it(value, error):
    with pytest.raises(error):
        rs._rate_limited_until = value
    with pytest.raises(error):
        tp.set_rate_limited_until(value)
    assert tp.rate_limited_until() == 0.0


def test_the_old_names_forward_to_the_shared_state(monkeypatch):
    # set through the old name → read through the new one, and back
    rs._rate_limited_until = 123.5
    assert tp.rate_limited_until() == 123.5 and rs._rate_limited_until == 123.5
    tp.set_rate_limited_until(0.0)                       # plain overwrite: a reset to 0 is allowed
    assert rs._rate_limited_until == 0.0
    monkeypatch.setattr(rs, "SEND_SPACING_SECONDS", 0.25)
    assert tp.SEND_SPACING_SECONDS == 0.25 and rs.SEND_SPACING_SECONDS == 0.25
    # no stale copy in the module dict: every read goes through the forwarding property
    assert "_rate_limited_until" not in vars(rs) and "SEND_SPACING_SECONDS" not in vars(rs)
    for bad, error in ((True, TypeError), ("1", TypeError), (-0.1, ValueError), (float("nan"), ValueError)):
        with pytest.raises(error):
            rs.SEND_SPACING_SECONDS = bad
    assert tp.SEND_SPACING_SECONDS == 0.25


def test_monkeypatching_the_old_names_restores_the_shared_state():
    mp = pytest.MonkeyPatch()
    mp.setattr(rs, "_rate_limited_until", time.monotonic() + 600)
    mp.setattr(rs, "SEND_SPACING_SECONDS", 0.0)
    assert tp.is_rate_limited() and tp.SEND_SPACING_SECONDS == 0.0
    mp.undo()
    assert tp.rate_limited_until() == 0.0 and tp.SEND_SPACING_SECONDS == tp.DEFAULT_SEND_SPACING_SECONDS


def test_every_sender_holds_the_one_pacer():
    assert rs._Pacer is tp.SharedPacer and feed._Pacer is tp.SharedPacer
    assert ds._PACER is tp.PACER
    assert isinstance(tp.PACER, tp.SharedPacer)


def _module_tree(rel: str) -> ast.Module:
    return ast.parse((_BACKEND / rel).read_text(encoding="utf-8"))


_SENDERS = ("app/services/marketing/review_service.py", "app/services/marketing/publish_feed.py",
            "app/services/marketing/digest_service.py",
            # the Trillion Club stake finder's notes, proposals and digest (plan 2026-10-28, `z:` taps)
            "app/services/trillion_club/stake_review.py")


def _pacer_constructions(tree: ast.Module) -> List[int]:
    """Lines that BUILD a pacer (`_Pacer()`, `SharedPacer()`, `x.SharedPacer()`) — AST, so a comment or
    a docstring naming one never counts."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name in {"_Pacer", "SharedPacer"}:
                found.append(node.lineno)
    return found


def test_no_sender_builds_its_own_pacer_any_more():
    assert {rel: _pacer_constructions(_module_tree(rel)) for rel in _SENDERS} == {rel: [] for rel in _SENDERS}
    # anti-vacuity: the scan sees the shape it bans
    assert _pacer_constructions(ast.parse("pacer = _Pacer()\nother = telegram_pacing.SharedPacer()\n")) == [1, 2]


def _bare_moved_names(tree: ast.Module) -> List[Tuple[str, int]]:
    """Bare uses of the two forwarded names inside review_service OUTSIDE its module class: a bare
    read or a `global` write would bypass the property and act on a dead module global."""
    skip = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "_ReviewServiceModule":
            skip = {id(n) for n in ast.walk(node)}
    hits = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.Name) and node.id in {"_rate_limited_until", "SEND_SPACING_SECONDS"}:
            hits.append((node.id, node.lineno))
        if isinstance(node, ast.Global) and {"_rate_limited_until", "SEND_SPACING_SECONDS"} & set(node.names):
            hits.append(("global", node.lineno))
    return hits


def test_review_service_never_uses_the_forwarded_names_bare():
    assert _bare_moved_names(_module_tree("app/services/marketing/review_service.py")) == []
    # anti-vacuity: the scan sees both shapes it bans
    sample = "def f():\n    global _rate_limited_until\n    return _rate_limited_until + SEND_SPACING_SECONDS\n"
    assert sorted(_bare_moved_names(ast.parse(sample))) == [
        ("SEND_SPACING_SECONDS", 3), ("_rate_limited_until", 3), ("global", 2)]


def test_each_event_loop_gets_its_own_pacer_and_lock():
    """An asyncio.Lock binds to the first loop that contends for it; a module-level one would raise
    "bound to a different event loop" in the next loop (the next test, a script's second asyncio.run).
    The per-loop state never does, and a stamp from a loop that no longer runs is not waited on.
    Real clock, tiny spacing: the whole test takes well under a second."""

    async def contended(n: int) -> Tuple[float, Any]:
        t0 = time.monotonic()
        await asyncio.gather(*(tp.PACER.wait() for _ in range(n)))   # n waiters: the lock is contended
        return time.monotonic() - t0, tp._STATE

    async def single() -> float:
        t0 = time.monotonic()
        await tp.PACER.wait()
        return time.monotonic() - t0

    loop1, loop2 = asyncio.new_event_loop(), asyncio.new_event_loop()
    try:
        tp.set_send_spacing(0.02)
        elapsed1, state1 = loop1.run_until_complete(contended(3))
        # A huge spacing now: had loop 1's stamp carried over, loop 2's first send would wait 5 s.
        tp.set_send_spacing(5.0)
        first_in_loop2 = loop2.run_until_complete(single())
        tp.set_send_spacing(0.02)
        elapsed2, state2 = loop2.run_until_complete(contended(3))   # contended again: no RuntimeError
    finally:
        loop1.close()
        loop2.close()
    assert state1 is not state2 and state1.loop is loop1 and state2.loop is loop2
    assert first_in_loop2 < 1.0
    # in each loop the waiters after the first wait one gap each (≥ 2 gaps of 0.02 s), far under a second
    assert 0.03 <= elapsed1 < 1.0 and 0.03 <= elapsed2 < 1.0


# ── the Trillion Club stake finder is one more sender to the same chat (plan 2026-10-28) ────────


def _stake_bot_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_REVIEW_CHAT_ID", CHAT)
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_WEBHOOK_SECRET", "s3cret_Webhook-token")
    monkeypatch.setattr(settings, "MARKETING_PUBLIC_BASE_URL", "https://caydexinvest.com")


_STAKE_FINDING = {"id": "5b0c7a7e-3c1f-4e0a-9d6c-2f1e8b7a6c5d", "company_slug": "nvidia", "member_name": "NVIDIA",
                  "accession": "0001045810-26-000123", "reason": "plain_13dg",
                  "source_url": "https://www.sec.gov/Archives/edgar/data/1045810/x.htm"}


@pytest.mark.asyncio
async def test_the_stake_finders_sends_are_spaced_with_every_other_sender(clock, tg, monkeypatch):
    from datetime import date

    from app.services.trillion_club import stake_review as sr

    _stake_bot_configured(monkeypatch)
    sent: List[Tuple[str, float]] = []
    tg.on_call = lambda method, payload: sent.append(("stakes", clock.now))
    digest = [f"heads-up {i}: " + "x" * 800 for i in range(12)]
    parts = len(sr.digest_parts(digest, today=date(2026, 10, 28))[0])
    assert parts >= 3                                              # several sends, not one
    ok, _ = await asyncio.gather(sr.TelegramStakeNotifier().send_digest(digest),
                                 _paced_sweep("review", 3, sent, clock, rs._Pacer()))
    assert ok is True and [n for n, _ in sent].count("stakes") == parts and len(sent) == parts + 3
    times = sorted(t for _, t in sent)
    assert all(b - a >= tp.SEND_SPACING_SECONDS - EPS for a, b in zip(times, times[1:])), times


@pytest.mark.asyncio
async def test_a_429_to_the_stake_finder_closes_the_chat_for_everyone_and_back(clock, tg, monkeypatch):
    from app.services.trillion_club import stake_review as sr

    _stake_bot_configured(monkeypatch)
    tg.script["sendMessage"] = [_TG_429]
    notifier = sr.TelegramStakeNotifier()
    assert await notifier.send_auto_note(_STAKE_FINDING, {"investee_name": "Nebius Group N.V."}) is None
    assert tp.is_rate_limited() and rs._rate_limited_until == tp.rate_limited_until()   # every sender sees it
    with pytest.raises(TelegramFloodControlActive):
        await tp.PACER.wait()                                                          # the Learn / marketing side
    clock.now = tp.rate_limited_until() + 0.01
    tp.note_rate_limit(120)                                                            # another sender's 429 now
    assert await notifier.send_digest(["a line"]) is False and len(tg.calls) == 1      # refused, no request


# ── classification of the two new Telegram classes ─────────────────────────────────────────────


def test_the_new_telegram_classes_classify_like_their_family():
    too_large = TelegramFileTooLargeError("too large", method="sendAudio")
    gate = TelegramFloodControlActive("not sent", method="paced_send", retry_after=7)
    assert isinstance(too_large, TelegramRequestError)          # callers' "send a link instead" path
    assert isinstance(gate, TelegramRateLimitException)         # every sweep's "stop" path
    assert classify_exception(too_large) == (ErrorCode.MARKETING_REVIEW_BOT_UNAVAILABLE, 502)   # permanent
    assert classify_exception(gate) == (ErrorCode.MARKETING_REVIEW_BOT_UNAVAILABLE, 503)        # retry later
    # unchanged for the existing classes
    assert classify_exception(telegram.TelegramRequestError("x", method="m"))[1] == 502
    assert classify_exception(telegram.TelegramRateLimitException("HTTP 429", method="m"))[1] == 503


# ── send_audio ─────────────────────────────────────────────────────────────────────────────────


def _no_secret_anywhere(texts: List[str]) -> None:
    for text in texts:
        assert _BOT_SECRET not in text and "SIGNED-SENTINEL" not in text, text


@pytest.mark.asyncio
async def test_send_audio_payload_shape(tg):
    envelope = await telegram.send_audio(CHAT, SIGNED_URL, caption="Deep Dive · 2026-W43", title="CoreWeave",
                                         performer="Caydex", duration=612, size_bytes=9_000_000)
    assert envelope["ok"] is True
    ((method, payload, timeout),) = tg.calls
    assert method == "sendAudio"
    assert payload == {"chat_id": CHAT, "audio": SIGNED_URL, "caption": "Deep Dive · 2026-W43",
                       "duration": 612, "performer": "Caydex", "title": "CoreWeave"}
    assert "parse_mode" not in payload and "disable_notification" not in payload
    assert timeout.get("read") == 60.0                       # Telegram fetches the file before answering


@pytest.mark.asyncio
async def test_send_audio_minimal_and_silent(tg):
    await telegram.send_audio(CHAT, SIGNED_URL, caption="", title="", performer=None, disable_notification=True)
    ((_, payload, _),) = tg.calls
    assert payload == {"chat_id": CHAT, "audio": SIGNED_URL, "disable_notification": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [MAX_URL_SEND_BYTES + 1, 25e6, 10 ** 12])
async def test_send_audio_refuses_a_known_size_over_20_mb_without_a_request(tg, caplog, size):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(TelegramFileTooLargeError) as info:
        await telegram.send_audio(CHAT, SIGNED_URL, size_bytes=size)
    assert tg.calls == []
    assert info.value.method == "sendAudio" and "20 MB" in str(info.value)
    _no_secret_anywhere([str(info.value), repr(info.value)] + [r.getMessage() for r in caplog.records])


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [MAX_URL_SEND_BYTES, 0, 8_600_000, 8_600_000.0, None])
async def test_send_audio_sends_a_size_at_or_under_the_limit_or_unknown(tg, caplog, size):
    caplog.set_level(logging.WARNING, logger=telegram.__name__)
    await telegram.send_audio(CHAT, SIGNED_URL, size_bytes=size)
    assert [m for m, _, _ in tg.calls] == ["sendAudio"]
    assert not [r for r in caplog.records if "size_bytes" in r.getMessage()]


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", [True, False, -1, float("nan"), float("inf"), "30000000", [1]])
async def test_send_audio_treats_a_malformed_size_as_unknown_and_says_so(tg, caplog, junk):
    """A malformed size (it comes from a ledger row) neither refuses nor crashes: the audio goes, and
    Telegram's own refusal is the backstop."""
    caplog.set_level(logging.WARNING, logger=telegram.__name__)
    await telegram.send_audio(CHAT, SIGNED_URL, size_bytes=junk)
    assert [m for m, _, _ in tg.calls] == ["sendAudio"]
    assert any("unusable size_bytes" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("duration, sent", [
    (612.6, 613), (612.4, 612), (0, 0), (True, None), (-5, None), (float("nan"), None),
    (float("inf"), None), ("600", None), (None, None),
])
async def test_send_audio_duration_is_whole_seconds_or_left_out(tg, caplog, duration, sent):
    caplog.set_level(logging.WARNING, logger=telegram.__name__)
    await telegram.send_audio(CHAT, SIGNED_URL, duration=duration)
    ((_, payload, _),) = tg.calls
    assert payload.get("duration") == sent and ("duration" in payload) == (sent is not None)
    if sent is None and duration is not None:
        assert any("unusable duration" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [123, b"bytes", ["CoreWeave"]])
async def test_send_audio_leaves_out_a_non_string_title_or_performer(tg, caplog, value):
    caplog.set_level(logging.WARNING, logger=telegram.__name__)
    await telegram.send_audio(CHAT, SIGNED_URL, title=value, performer=value)
    ((_, payload, _),) = tg.calls
    assert "title" not in payload and "performer" not in payload
    assert len([r for r in caplog.records if "not a string" in r.getMessage()]) == 2


@pytest.mark.asyncio
async def test_send_audio_answers_are_typed_and_never_carry_the_token_or_the_signed_url(tg, caplog):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.INFO, logger="httpx")
    tg.script["sendAudio"] = [_TG_429, (400, {"ok": False, "description": "Bad Request: wrong file"}),
                              (502, {"ok": False})]
    with pytest.raises(TelegramRateLimitException) as limited:
        await telegram.send_audio(CHAT, SIGNED_URL)
    assert limited.value.retry_after == 30 and not isinstance(limited.value, TelegramFloodControlActive)
    with pytest.raises(TelegramRequestError) as refused:
        await telegram.send_audio(CHAT, SIGNED_URL)
    with pytest.raises(telegram.TelegramUnavailableException) as down:
        await telegram.send_audio(CHAT, SIGNED_URL)
    texts = [str(e.value) for e in (limited, refused, down)] + [r.getMessage() for r in caplog.records]
    _no_secret_anywhere(texts)
    assert any(f"api.telegram.org/bot{_BOT_ID}:***/sendAudio" in m for m in texts)   # httpx did log — redacted


@pytest.mark.asyncio
async def test_send_audio_without_a_token_makes_no_request(tg, monkeypatch):
    monkeypatch.setattr(settings, "MARKETING_TELEGRAM_BOT_TOKEN", None)
    with pytest.raises(telegram.TelegramNotConfiguredException):
        await telegram.send_audio(CHAT, SIGNED_URL)
    assert tg.calls == []
