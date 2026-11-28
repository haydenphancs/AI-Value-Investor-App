"""`PushService.send_widget_pushes` — the WidgetKit wake sender (spec §3 "Push", D11/D12).

Hermetic: every request goes through an injected `httpx.AsyncClient(transport=MockTransport)`,
the provider JWT is a constant, and `settings` is a per-test proxy patched into the module (so the
suite does not depend on which WIDGET_* fields `config.py` declares today).

Pins:
* the exact wire shape — topic, push type, expiration 0, NO priority, the byte-exact body, each
  token to its OWN environment's host, 5 s per request;
* the outcome table, and that per-DEVICE answers stay per-device: one poisoned token among 50
  costs exactly that device (the review's attack: any account uploads its ordinary alert token);
* the environment stops: 403 / a request-shape topic error / the first 3 devices ALL answering
  DeviceTokenNotForTopic stop only THAT environment, and the probe's answers are never "bad";
* the per-environment circuit after 5 retryable answers in a row;
* pacing at WIDGET_PUSH_SENDS_PER_SECOND;
* no token in any log line, `_prune_token` and `device_tokens` never touched.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx
import pytest

import app.services.push_service as push_mod
from app.services.push_service import (
    WIDGET_ACCEPTED,
    WIDGET_BAD,
    WIDGET_GONE,
    WIDGET_NOT_SENT,
    WIDGET_PROVIDER_FAULT,
    WIDGET_PUSH_BODY,
    WIDGET_PUSH_CIRCUIT_AFTER,
    WIDGET_PUSH_CONCURRENCY,
    WIDGET_RETRY,
    PushService,
    WidgetPushPacer,
    classify_widget_push_response,
    widget_push_topic,
)

PROD_HOST = "api.push.apple.com"
SANDBOX_HOST = "api.sandbox.push.apple.com"


class _Settings:
    """The real settings with per-test overrides; never validates, never writes the singleton."""

    def __init__(self, base: Any, **overrides: Any) -> None:
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_over", dict(overrides))

    def __getattr__(self, name: str) -> Any:
        over = object.__getattribute__(self, "_over")
        if name in over:
            return over[name]
        return getattr(object.__getattribute__(self, "_base"), name)


class _NoDB:
    def __getattr__(self, name: str) -> Any:  # any Supabase use is a bug on this path
        raise AssertionError(f"the widget sender touched Supabase ({name})")


class _NoWaitPacer(WidgetPushPacer):
    def __init__(self) -> None:
        super().__init__(100)

    async def wait(self) -> None:
        return None


def _token(i: int, prefix: str = "a") -> str:
    # 64 lower-case hex characters, distinct per i.
    return (prefix + f"{i:063x}")[:64]


def _devices(n: int, env: str = "production", prefix: str = "a") -> List[Dict[str, str]]:
    return [
        {"grant_id": f"00000000-0000-4000-8000-{i:012d}" if prefix == "a"
         else f"00000000-0000-4000-9000-{i:012d}",
         "push_token": _token(i, prefix), "push_environment": env}
        for i in range(n)
    ]


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(push_mod, "settings", _Settings(
        push_mod.settings,
        APNS_KEY_ID="KEYID12345", APNS_TEAM_ID="TEAM123456", APNS_AUTH_KEY="pem",
        APNS_BUNDLE_ID="com.phan.caydex", APNS_ENV="production",
        WIDGET_PUSH_SENDS_PER_SECOND=20,
    ))
    monkeypatch.setattr(push_mod, "get_supabase", lambda: _NoDB())
    pruned: List[str] = []
    monkeypatch.setattr(PushService, "_prune_token", lambda self, token: pruned.append(token))
    service = PushService()
    monkeypatch.setattr(service, "_provider_jwt", lambda: "provider.jwt")
    service.pruned = pruned  # type: ignore[attr-defined]
    return service


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _yielding(handler):
    """The same answers, but each request suspends first, so the ten workers genuinely overlap
    (a plain MockTransport answers synchronously and serialises them)."""

    async def _handler(request):
        await asyncio.sleep(0.001)
        return handler(request)

    return _handler


@pytest.fixture(params=["serial", "overlapping"])
def wrap(request):
    return (lambda h: h) if request.param == "serial" else _yielding


def _answer(status: int, reason: Optional[str] = None, **extra: Any) -> httpx.Response:
    if status == 200:
        return httpx.Response(200)
    body = {"reason": reason} if reason else {}
    body.update(extra)
    return httpx.Response(status, json=body)


async def _send(svc: PushService, devices, handler, **kw) -> List[Any]:
    async with _client(handler) as client:
        return await svc.send_widget_pushes(devices, client=client,
                                            pacer=kw.pop("pacer", _NoWaitPacer()), **kw)


# ── the wire shape ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_headers_body_path_and_each_token_to_its_own_host(svc):
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    devices = _devices(1, "sandbox") + _devices(1, "production", prefix="b")
    results = await _send(svc, devices, handler)

    assert [r.status for r in results] == [WIDGET_ACCEPTED, WIDGET_ACCEPTED]
    by_host = {r.url.host: r for r in seen}
    # The server default is production; the sandbox token still goes to the sandbox host.
    assert set(by_host) == {SANDBOX_HOST, PROD_HOST}
    for request, device in ((by_host[SANDBOX_HOST], devices[0]), (by_host[PROD_HOST], devices[1])):
        assert request.method == "POST"
        assert request.url.path == f"/3/device/{device['push_token']}"
        assert request.content == WIDGET_PUSH_BODY == b'{"aps":{"content-changed":true}}'
        h = request.headers
        assert h["apns-push-type"] == "widgets"
        assert h["apns-topic"] == "com.phan.caydex.push-type.widgets"
        assert "CaydexWidgets" not in h["apns-topic"]
        assert h["apns-expiration"] == "0"
        assert "apns-priority" not in h and "apns-collapse-id" not in h
        assert h["authorization"] == "bearer provider.jwt"
        assert json.loads(request.content) == {"aps": {"content-changed": True}}
        # 5 s per request, even on an injected client with its own default.
        assert request.extensions["timeout"]["read"] == 5.0
    assert widget_push_topic() == "com.phan.caydex.push-type.widgets"


# ── the outcome table ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status,reason,expected", [
    (200, "", WIDGET_ACCEPTED),
    (410, "Unregistered", WIDGET_GONE),
    (410, "ExpiredToken", WIDGET_GONE),
    (400, "BadDeviceToken", WIDGET_BAD),
    (400, "DeviceTokenNotForTopic", WIDGET_BAD),
    (403, "InvalidProviderToken", WIDGET_PROVIDER_FAULT),
    (403, "", WIDGET_PROVIDER_FAULT),
    (400, "BadTopic", WIDGET_PROVIDER_FAULT),
    (400, "TopicDisallowed", WIDGET_PROVIDER_FAULT),
    (400, "MissingTopic", WIDGET_PROVIDER_FAULT),
    (400, "InvalidPushType", WIDGET_PROVIDER_FAULT),
    (429, "TooManyRequests", WIDGET_RETRY),
    (500, "InternalServerError", WIDGET_RETRY),
    (503, "ServiceUnavailable", WIDGET_RETRY),
    # Unnamed answers never clear a token and never stop an environment on a guess.
    (400, "BadPriority", WIDGET_RETRY),
    (413, "PayloadTooLarge", WIDGET_RETRY),
    (404, "", WIDGET_RETRY),
])
def test_classification_table(status, reason, expected):
    assert classify_widget_push_response(status, reason) == expected


@pytest.mark.asyncio
async def test_gone_carries_apns_timestamp_and_bad_is_per_device(svc):
    devices = _devices(3)
    answers = {
        devices[0]["push_token"]: _answer(410, "Unregistered", timestamp=1764000000123),
        devices[1]["push_token"]: _answer(400, "BadDeviceToken"),
        devices[2]["push_token"]: _answer(200),
    }

    def handler(request):
        return answers[request.url.path.rsplit("/", 1)[1]]

    results = await _send(svc, devices, handler)
    assert [r.status for r in results] == [WIDGET_GONE, WIDGET_BAD, WIDGET_ACCEPTED]
    assert results[0].apns_timestamp_ms == 1764000000123
    assert results[0].reason == "410 Unregistered"
    assert results[1].reason == "400 BadDeviceToken" and results[1].apns_timestamp_ms is None
    # The exact stored token rides on every result (the settle keys on it).
    assert [r.push_token for r in results] == [d["push_token"] for d in devices]
    assert svc.pruned == []  # never `_prune_token`, never device_tokens


@pytest.mark.parametrize("raw", [True, "abc", -5, 0, 1.5, None, "12"])
def test_odd_410_timestamps_are_never_invented(raw):
    from app.services.push_service import _apns_timestamp_ms

    expected = 12 if raw == "12" else None
    assert _apns_timestamp_ms({"timestamp": raw}) == expected
    assert _apns_timestamp_ms("not a dict") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("position", [0, 1, 2, 25, 49])
async def test_one_poisoned_token_among_50_costs_only_that_device(svc, position, wrap):
    """The review's attack: an account uploads its ordinary alert token (valid, wrong topic). It
    must never stop the environment — wherever it sits, including inside the topic probe."""
    devices = _devices(50)
    poisoned = devices[position]["push_token"]

    def handler(request):
        if request.url.path.endswith(poisoned):
            return _answer(400, "DeviceTokenNotForTopic")
        return _answer(200)

    results = await _send(svc, devices, wrap(handler))
    statuses = [r.status for r in results]
    assert statuses.count(WIDGET_ACCEPTED) == 49
    assert results[position].status == WIDGET_BAD
    assert results[position].reason == "400 DeviceTokenNotForTopic"


@pytest.mark.asyncio
async def test_a_403_stops_only_its_own_environment(svc, caplog, wrap):
    prod = _devices(40, "production")
    sandbox = _devices(10, "sandbox", prefix="b")
    calls = {"prod": 0, "sandbox": 0}

    def handler(request):
        if request.url.host == PROD_HOST:
            calls["prod"] += 1
            return _answer(403, "InvalidProviderToken")
        calls["sandbox"] += 1
        return _answer(200)

    with caplog.at_level(logging.INFO, logger=push_mod.logger.name):
        results = await _send(svc, prod + sandbox, wrap(handler))

    prod_results, sandbox_results = results[:40], results[40:]
    assert all(r.status == WIDGET_ACCEPTED for r in sandbox_results)
    assert calls["sandbox"] == 10
    # Only what was already in flight reached APNs; everything after the stop is not_sent.
    assert calls["prod"] <= WIDGET_PUSH_CONCURRENCY
    assert {r.status for r in prod_results} <= {WIDGET_PROVIDER_FAULT, WIDGET_NOT_SENT}
    assert sum(r.status == WIDGET_NOT_SENT for r in prod_results) >= 40 - WIDGET_PUSH_CONCURRENCY
    assert all("environment_stopped" in r.reason for r in prod_results
               if r.status == WIDGET_NOT_SENT)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "production" in errors[0].getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["BadTopic", "TopicDisallowed", "MissingTopic",
                                    "InvalidPushType"])
async def test_a_request_shape_topic_error_stops_the_environment(svc, reason, caplog):
    devices = _devices(30)
    calls = []

    def handler(request):
        calls.append(request)
        return _answer(400, reason)

    with caplog.at_level(logging.ERROR, logger=push_mod.logger.name):
        results = await _send(svc, devices, handler)
    assert len(calls) <= WIDGET_PUSH_CONCURRENCY
    assert WIDGET_BAD not in {r.status for r in results}
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 1


@pytest.mark.asyncio
async def test_first_three_devices_all_not_for_topic_stop_the_environment_never_bad(
        svc, caplog, wrap):
    """A wrong topic (or no push capability on the widget App ID) makes EVERY device answer
    DeviceTokenNotForTopic. None of them may be settled as `bad` — after 24 h that would clear
    every valid token for our own mistake."""
    prod = _devices(40, "production")
    sandbox = _devices(5, "sandbox", prefix="b")
    calls = {"prod": 0}

    def handler(request):
        if request.url.host == PROD_HOST:
            calls["prod"] += 1
            return _answer(400, "DeviceTokenNotForTopic")
        return _answer(200)

    seen = []
    with caplog.at_level(logging.ERROR, logger=push_mod.logger.name):
        results = await _send(svc, prod + sandbox, wrap(handler), on_result=seen.append)

    prod_results = results[:40]
    assert WIDGET_BAD not in {r.status for r in prod_results}
    assert sum(r.status == WIDGET_PROVIDER_FAULT for r in prod_results) >= 3
    assert calls["prod"] <= WIDGET_PUSH_CONCURRENCY + 3
    assert all(r.status == WIDGET_ACCEPTED for r in results[40:])
    # The callback saw each device exactly once, and never a provisional "bad".
    assert len(seen) == len(results)
    assert WIDGET_BAD not in {r.status for r in seen if r.environment == "production"}
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "DeviceTokenNotForTopic" in errors[0].getMessage()


@pytest.mark.asyncio
async def test_a_probe_with_one_good_answer_never_trips(svc):
    devices = _devices(3)

    def handler(request):
        if request.url.path.endswith(devices[1]["push_token"]):
            return _answer(200)
        return _answer(400, "DeviceTokenNotForTopic")

    results = await _send(svc, devices, handler)
    assert [r.status for r in results] == [WIDGET_BAD, WIDGET_ACCEPTED, WIDGET_BAD]


@pytest.mark.asyncio
async def test_two_not_for_topic_devices_alone_are_per_device_bad(svc):
    """Fewer devices than the probe: nothing can be concluded about the topic."""
    devices = _devices(2)
    results = await _send(svc, devices, lambda r: _answer(400, "DeviceTokenNotForTopic"))
    assert [r.status for r in results] == [WIDGET_BAD, WIDGET_BAD]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["503", "429", "transport"])
async def test_the_circuit_stops_an_environment_after_five_retryable_answers(svc, failure, wrap):
    prod = _devices(40)
    sandbox = _devices(5, "sandbox", prefix="b")
    calls = {"prod": 0}

    def handler(request):
        if request.url.host == SANDBOX_HOST:
            return _answer(200)
        calls["prod"] += 1
        if failure == "transport":
            raise httpx.ConnectError("boom", request=request)
        return _answer(int(failure), "ServiceUnavailable" if failure == "503" else "TooManyRequests")

    results = await _send(svc, prod + sandbox, wrap(handler))
    prod_results = results[:40]
    assert sum(r.status == WIDGET_RETRY for r in prod_results) >= WIDGET_PUSH_CIRCUIT_AFTER
    assert calls["prod"] <= WIDGET_PUSH_CIRCUIT_AFTER + WIDGET_PUSH_CONCURRENCY
    assert sum(r.status == WIDGET_NOT_SENT for r in prod_results) >= (
        40 - WIDGET_PUSH_CIRCUIT_AFTER - WIDGET_PUSH_CONCURRENCY)
    assert all(r.status == WIDGET_ACCEPTED for r in results[40:])


@pytest.mark.asyncio
async def test_a_success_resets_the_circuit(svc, monkeypatch):
    """Retries that are not consecutive never trip it: 4 failures, a success, 4 failures."""
    devices = _devices(9)
    order = iter([503, 503, 503, 503, 200, 503, 503, 503, 503])
    lock_free_answers = {d["push_token"]: next(order) for d in devices}

    def handler(request):
        return _answer(lock_free_answers[request.url.path.rsplit("/", 1)[1]], "x")

    # One at a time, so "consecutive" is the input order.
    monkeypatch.setattr(push_mod, "WIDGET_PUSH_CONCURRENCY", 1)
    async with _client(handler) as client:
        results = await svc.send_widget_pushes(devices, client=client, pacer=_NoWaitPacer())
    assert WIDGET_NOT_SENT not in {r.status for r in results}
    assert [r.status for r in results].count(WIDGET_RETRY) == 8


@pytest.mark.asyncio
async def test_five_in_a_row_trip_the_circuit_exactly(svc, monkeypatch):
    devices = _devices(8)
    monkeypatch.setattr(push_mod, "WIDGET_PUSH_CONCURRENCY", 1)
    async with _client(lambda r: _answer(503, "ServiceUnavailable")) as client:
        results = await svc.send_widget_pushes(devices, client=client, pacer=_NoWaitPacer())
    assert [r.status for r in results] == [WIDGET_RETRY] * 5 + [WIDGET_NOT_SENT] * 3


# ── pacing ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sends_are_paced_at_the_configured_rate(svc):
    slots: List[float] = []

    async def _sleep(delay: float) -> None:
        slots.append(delay)

    pacer = WidgetPushPacer(20, clock=lambda: 100.0, sleep=_sleep)
    devices = _devices(50)
    results = await _send(svc, devices, lambda r: _answer(200), pacer=pacer)
    assert [r.status for r in results].count(WIDGET_ACCEPTED) == 50
    # The first start is immediate; every later start is reserved 1/20 s after the previous one.
    assert len(slots) == 49
    assert sorted(slots) == pytest.approx([i * 0.05 for i in range(1, 50)])


@pytest.mark.asyncio
async def test_the_default_pacer_reads_the_setting(svc, monkeypatch):
    rates: List[float] = []

    class _Recording(WidgetPushPacer):
        def __init__(self, per_second, **kw):
            rates.append(per_second)
            super().__init__(per_second, **kw)

        async def wait(self):
            return None

    monkeypatch.setattr(push_mod, "WidgetPushPacer", _Recording)
    monkeypatch.setattr(push_mod, "settings", _Settings(push_mod.settings,
                                                         WIDGET_PUSH_SENDS_PER_SECOND=7))
    async with _client(lambda r: _answer(200)) as client:
        await svc.send_widget_pushes(_devices(2), client=client)
    assert rates == [7]


def test_the_pacer_never_bursts_after_an_idle_gap():
    """A slot in the past is re-anchored at `now`, so an idle pass never 'saves up' a burst."""
    now = {"t": 0.0}
    slept: List[float] = []

    async def _sleep(d):
        slept.append(d)

    pacer = WidgetPushPacer(10, clock=lambda: now["t"], sleep=_sleep)

    async def run():
        await pacer.wait()
        await pacer.wait()
        now["t"] = 50.0
        await pacer.wait()
        await pacer.wait()

    asyncio.run(run())
    assert slept == pytest.approx([0.1, 0.1])


# ── refusals before any I/O ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invalid_devices_never_reach_a_url(svc):
    good = _devices(1)[0]
    bad_devices = [
        {"grant_id": "g1", "push_token": "ab/../../3/device/x", "push_environment": "production"},
        {"grant_id": "g2", "push_token": "AB" * 32, "push_environment": "production"},
        {"grant_id": "g3", "push_token": "a" * 63, "push_environment": "production"},
        {"grant_id": "g4", "push_token": "ab" * 16, "push_environment": "prod"},
        {"grant_id": "", "push_token": "cd" * 16, "push_environment": "sandbox"},
        {"grant_id": "g6", "push_token": None, "push_environment": "sandbox"},
        "not a mapping",
        dict(good),  # a duplicate token in one call
    ]
    calls: List[str] = []

    def handler(request):
        calls.append(request.url.path)
        return _answer(200)

    results = await _send(svc, [good] + bad_devices, handler)
    assert calls == [f"/3/device/{good['push_token']}"]
    assert results[0].status == WIDGET_ACCEPTED
    assert all(r.status == WIDGET_NOT_SENT and r.reason.startswith("invalid_device")
               for r in results[1:])
    assert len(results) == 1 + len(bad_devices)


@pytest.mark.asyncio
async def test_a_failed_provider_jwt_sends_nothing(svc, monkeypatch):
    monkeypatch.setattr(svc, "_provider_jwt", lambda: None)
    calls: List[Any] = []
    results = await _send(svc, _devices(3), lambda r: calls.append(r) or _answer(200))
    assert calls == []
    assert {(r.status, r.reason) for r in results} == {(WIDGET_NOT_SENT, "provider_jwt_failed")}


@pytest.mark.asyncio
async def test_unconfigured_apns_sends_nothing(svc, monkeypatch):
    monkeypatch.setattr(push_mod, "settings", _Settings(push_mod.settings, APNS_KEY_ID=None))
    calls: List[Any] = []
    results = await _send(svc, _devices(3), lambda r: calls.append(r) or _answer(200))
    assert calls == []
    assert {(r.status, r.reason) for r in results} == {(WIDGET_NOT_SENT, "apns_unconfigured")}


@pytest.mark.asyncio
async def test_empty_input_is_a_no_op(svc):
    assert await _send(svc, [], lambda r: _answer(200)) == []


# ── the callback ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_raising_callback_never_stops_the_sends(svc):
    seen: List[Any] = []

    def on_result(result):
        seen.append(result)
        raise RuntimeError("bookkeeping bug")

    results = await _send(svc, _devices(12), lambda r: _answer(200), on_result=on_result)
    assert len(seen) == 12
    assert [r.status for r in results].count(WIDGET_ACCEPTED) == 12


# ── never a token in a log line ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_token_is_ever_logged_in_full(svc, caplog):
    devices = _devices(8) + _devices(4, "sandbox", prefix="b")
    devices.append({"grant_id": "g-odd", "push_token": "f" * 63, "push_environment": "sandbox"})
    answers = [_answer(410, "Unregistered", timestamp=5), _answer(400, "BadDeviceToken"),
               _answer(503, "ServiceUnavailable"), _answer(429, "TooManyRequests"),
               _answer(400, "DeviceTokenNotForTopic"), _answer(200), None, _answer(403, "Forbidden")]

    def handler(request):
        token = request.url.path.rsplit("/", 1)[1]
        index = [d["push_token"] for d in devices].index(token)
        answer = answers[index % len(answers)]
        if answer is None:
            raise httpx.ReadTimeout("slow", request=request)
        return answer

    with caplog.at_level(logging.DEBUG):
        await _send(svc, devices, handler)
    # OUR log lines (`app.*`). httpx's own INFO line is pinned by the next test.
    text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app."))
    assert text  # something was logged
    for device in devices:
        assert device["push_token"] not in text
        assert device["push_token"][:-6] not in text  # nothing beyond the last six characters


@pytest.mark.asyncio
async def test_httpx_request_lines_are_scrubbed_by_the_production_filter(svc):
    """httpx logs every answered request at INFO — `HTTP Request: POST
    https://api.push.apple.com/3/device/<token> "HTTP/2 200 OK"` — and `LOG_LEVEL` is INFO in
    production, so the device token reaches Railway's log (and a Sentry breadcrumb) unless the
    root handlers' `SecretRedactingFilter` scrubs the `/3/device/` path. The test above ignores
    that logger; this one proves its lines are scrubbed on the production path (review
    2026-11-27: the comment that claimed a `/3/device/` pattern cited one that did not exist)."""
    from app.log_redaction import SecretRedactingFilter

    devices = _devices(6) + _devices(3, "sandbox", prefix="b")
    answers = [_answer(200), _answer(410, "Unregistered", timestamp=5),
               _answer(400, "BadDeviceToken")]

    def handler(request):
        token = request.url.path.rsplit("/", 1)[1]
        return answers[[d["push_token"] for d in devices].index(token) % len(answers)]

    # The RAW records, from a handler of our own on the httpx logger, NOT caplog. Once app.main
    # has been imported (by another test file in a full run), the ROOT handlers'
    # SecretRedactingFilter rewrites the same record object before caplog's root handler sees
    # it, so caplog would see a pre-scrubbed line and the not-vacuous check below would fail for
    # the wrong reason (2026-11-27 full-suite run). Same capture as test_log_redaction's
    # APNs-path test: our handler, propagation off for the duration, restored after.
    captured: List[logging.LogRecord] = []

    class _Grab(logging.Handler):
        def emit(self, record):
            captured.append(record)

    log = logging.getLogger("httpx")
    grab, old_level, old_propagate = _Grab(level=logging.INFO), log.level, log.propagate
    log.addHandler(grab)
    log.setLevel(logging.INFO)
    log.propagate = False
    try:
        await _send(svc, devices, handler)
    finally:
        log.removeHandler(grab)
        log.setLevel(old_level)
        log.propagate = old_propagate
    records = [r for r in captured if r.name.startswith("httpx")]
    raw = "\n".join(r.getMessage() for r in records)
    # Not vacuous: httpx DID print the tokens, so the scrub below has something to remove.
    assert "/3/device/" in raw and any(d["push_token"] in raw for d in devices)

    scrub = SecretRedactingFilter()
    scrubbed: List[str] = []
    for record in records:
        copy = logging.makeLogRecord(record.__dict__)   # the root handlers' filter, on a copy
        assert scrub.filter(copy) is True
        scrubbed.append(copy.getMessage())
    httpx_text = "\n".join(scrubbed)
    assert httpx_text.count("/3/device/") == len(records)   # the line itself survives
    for device in devices:
        assert device["push_token"][:-6] not in httpx_text, (
            "an APNs device token reaches the log in full — log_redaction.redact_secrets needs "
            "the `/3/device/<token>` path pattern"
        )


def test_queued_and_result_reprs_are_scrubbed_in_sentry_frames():
    """A Sentry frame stores locals as reprs. The sender's queue items and results are
    NamedTuples, so the token reads `push_token='…'`, which the frame scrub redacts — a bare
    tuple would print the token unlabelled."""
    from app.log_redaction import _redact_secret_fields
    from app.services.push_service import WidgetPushResult, _QueuedWidgetPush

    token = _token(7)
    for value in (_QueuedWidgetPush(0, "g", token, "sandbox"),
                  WidgetPushResult("g", token, "sandbox", WIDGET_ACCEPTED),
                  [{"grant_id": "g", "push_token": token, "push_environment": "sandbox"}],
                  [{"grant_id": "g", "token": token, "outcome": "accepted"}]):
        assert token not in _redact_secret_fields(repr(value))
