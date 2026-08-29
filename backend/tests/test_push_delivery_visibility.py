"""A push that reached SOME of a user's devices must not look like a clean delivery.

WHY THIS FILE EXISTS. A TestFlight tester reported "Notification about the price move
doesn't work. I don't see any notification on my iphone." The alert's ledger row said
`push_state='sent'` with `sent_at` stamped and `last_error` NULL — a perfect record — and
that was true of every row in the table: `last_error` was NULL on all 57.

The cause was that `send_to_user` returned a bare accepted-COUNT and `_deliver` stamped
`sent` on any non-zero. The account had four registered tokens (one real iPhone, three
simulator tokens from development), so a single simulator accepting was enough to make the
row look perfect while the phone got nothing. Per-device rejections went to
`logger.warning` only, and Railway's log buffer is hours deep — by the time the report
arrived the evidence no longer existed anywhere.

These tests pin the fix: APNs's answer for EVERY device survives into the ledger, so
`push_state='sent' AND last_error IS NOT NULL` is the query that answers "it says sent, why
did my phone not buzz?".
"""

import asyncio

import pytest

from app.config import settings
from app.services.push_service import PushOutcome, PushService
import app.services.push_service as ps
from app.services.push_dispatch_service import (
    STATE_FAILED,
    STATE_SENT,
    PushDispatchService,
    _Recipient,
)
from app.services.notification_kinds import KIND_PRICE_ALERT, get_kind


# ── send_to_user: what APNs said, per device ─────────────────────────────────


def _configure_apns(monkeypatch):
    """Turn `enabled` on the way production does — it is a property over four settings."""
    for name, value in (
        ("APNS_KEY_ID", "KEY123"), ("APNS_TEAM_ID", "TEAM123"),
        ("APNS_AUTH_KEY", "-----PEM-----"), ("APNS_BUNDLE_ID", "com.phan.caydex"),
    ):
        monkeypatch.setattr(settings, name, value)


def _client_returning(statuses):
    """An httpx double that answers each POST with the next (status, reason) in turn."""
    seq = list(statuses)

    class _Resp:
        def __init__(self, status, reason):
            self.status_code = status
            self._reason = reason
            self.text = reason

        def json(self):
            return {"reason": self._reason}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            status, reason = seq.pop(0)
            return _Resp(status, reason)

    return _Client


async def _send(monkeypatch, devices, statuses):
    _configure_apns(monkeypatch)
    monkeypatch.setattr(ps.httpx, "AsyncClient", lambda **kw: _client_returning(statuses)())
    svc = PushService()
    monkeypatch.setattr(svc, "_provider_jwt", lambda: "jwt")
    monkeypatch.setattr(svc, "_prune_token", lambda token: None)
    return await svc.send_to_user("u1", title="t", body="b", devices=devices)


@pytest.mark.asyncio
async def test_a_partial_delivery_reports_which_device_failed(monkeypatch):
    """The exact production shape: one real phone, one stale simulator token."""
    outcome = await _send(
        monkeypatch,
        devices=[
            {"token": "a" * 58 + "390121", "environment": "production"},
            {"token": "b" * 58 + "da4fdb", "environment": "sandbox"},
        ],
        statuses=[(400, "BadDeviceToken"), (200, "")],
    )
    assert outcome.attempted == 2
    assert outcome.accepted == 1
    assert outcome.partial is True, (
        "A send that reached one of two devices must report as PARTIAL — that is the state "
        "that used to be indistinguishable from a clean delivery."
    )
    summary = outcome.summary()
    assert summary and "BadDeviceToken" in summary and "production" in summary, (
        f"the failing device's environment and APNs reason must survive into the ledger; got {summary!r}"
    )


@pytest.mark.asyncio
async def test_a_failure_string_never_contains_a_whole_device_token(monkeypatch):
    """A device token is a credential, and `last_error` is a readable column.

    Six trailing characters is enough to tell one registration from another when reading
    the ledger; the whole token is not needed and must not be stored twice.
    """
    token = "c" * 58 + "390121"
    outcome = await _send(
        monkeypatch,
        devices=[{"token": token, "environment": "production"}],
        statuses=[(400, "BadDeviceToken")],
    )
    summary = outcome.summary() or ""
    assert token not in summary, "the full device token leaked into last_error"
    assert "390121" in summary, "the masked tail should still identify the device"


@pytest.mark.asyncio
async def test_every_device_accepted_leaves_nothing_to_report(monkeypatch):
    """A clean delivery must write NO error, or `last_error IS NOT NULL` stops meaning
    anything and the diagnostic query this whole change exists for becomes noise."""
    outcome = await _send(
        monkeypatch,
        devices=[
            {"token": "a" * 64, "environment": "production"},
            {"token": "b" * 64, "environment": "production"},
        ],
        statuses=[(200, ""), (200, "")],
    )
    assert outcome.accepted == outcome.attempted == 2
    assert outcome.partial is False
    assert outcome.summary() is None


@pytest.mark.asyncio
async def test_a_dead_token_is_both_pruned_and_reported(monkeypatch):
    """410 Unregistered already pruned. It must ALSO be recorded — pruning silently is how
    a user's last device can disappear with nothing explaining why they stopped hearing."""
    _configure_apns(monkeypatch)
    monkeypatch.setattr(
        ps.httpx, "AsyncClient", lambda **kw: _client_returning([(410, "Unregistered")])()
    )
    pruned = []
    svc = PushService()
    monkeypatch.setattr(svc, "_provider_jwt", lambda: "jwt")
    monkeypatch.setattr(svc, "_prune_token", lambda token: pruned.append(token))

    outcome = await svc.send_to_user(
        "u1", title="t", body="b",
        devices=[{"token": "d" * 64, "environment": "production"}],
    )
    assert pruned == ["d" * 64], "a 410 must still prune the dead token"
    assert "Unregistered" in (outcome.summary() or "")


# ── _deliver: the ledger row ─────────────────────────────────────────────────


class _FakePush:
    def __init__(self, outcome):
        self.enabled = True
        self._outcome = outcome

    async def send_to_user(self, user_id, **kw):
        return self._outcome


def _dispatch_with(outcome):
    """A dispatcher whose only live parts are `_deliver` and a captured `mark_state`."""
    svc = object.__new__(PushDispatchService)
    svc._push = _FakePush(outcome)
    svc.supabase = None
    stamped = {}

    def _mark(user_id, dedup_key, state, *, error=None, sent=False):
        stamped.update(
            {"state": state, "error": error, "sent": sent, "dedup_key": dedup_key}
        )

    svc.mark_state = _mark
    return svc, stamped


def _deliver(svc):
    return asyncio.run(
        svc._deliver(
            _Recipient(user_id="u1", devices=[{"token": "x", "environment": "production"}]),
            get_kind(KIND_PRICE_ALERT),
            title="ORCL is above $147.00",
            body="ORCL is trading at $148.21.",
            dedup_key="alert:1",
            route={"route": "ticker", "ticker": "ORCL"},
        )
    )


def test_a_partial_delivery_is_still_sent_but_carries_the_reason():
    """`sent` keeps meaning "at least one device took it".

    Demanding ALL devices would mark a real delivery failed because of one stale simulator
    token, and the inbox row and the daily cap both key off `sent_at`. What changes is that
    the rejection is written to `last_error` ANYWAY, so the row stops lying by omission.
    """
    svc, stamped = _dispatch_with(
        PushOutcome(attempted=2, accepted=1, failures=("production …390121: 400 BadDeviceToken",))
    )
    assert _deliver(svc) is True
    assert stamped["state"] == STATE_SENT
    assert stamped["sent"] is True
    assert stamped["error"] and "BadDeviceToken" in stamped["error"], (
        "A partially delivered push wrote no error — this is the exact bug: the row reads "
        "`sent` with `last_error` NULL while the user's phone got nothing."
    )


def test_a_clean_delivery_writes_no_error():
    svc, stamped = _dispatch_with(PushOutcome(attempted=1, accepted=1))
    assert _deliver(svc) is True
    assert stamped["state"] == STATE_SENT
    assert stamped["error"] is None


def test_a_total_failure_records_the_reasons_not_a_generic_message():
    """"APNs accepted no device" says nothing a reader can act on."""
    svc, stamped = _dispatch_with(
        PushOutcome(
            attempted=2,
            accepted=0,
            failures=("production …390121: 400 BadDeviceToken", "sandbox …da4fdb: 410 Unregistered"),
        )
    )
    assert _deliver(svc) is False
    assert stamped["state"] == STATE_FAILED
    assert "BadDeviceToken" in stamped["error"] and "Unregistered" in stamped["error"]


# ── the routing default ──────────────────────────────────────────────────────


def test_the_apns_fallback_environment_is_production():
    """A token whose own environment is unknown belongs to a SHIPPED build.

    `device_tokens.environment` always wins, so this default only applies to rows written
    before the client sent one — and those come from TestFlight or the App Store, which are
    production. Defaulting to sandbox sent them to the wrong host, where APNs answers 400
    BadDeviceToken and the push is lost with no user-visible signal.
    """
    import app.config as config

    field = config.Settings.model_fields["APNS_ENV"]
    assert field.default == "production", (
        "APNS_ENV defaults to sandbox again — any token with no recorded environment will "
        "be routed to the sandbox host and silently rejected."
    )


@pytest.mark.asyncio
async def test_a_provider_key_rejection_is_logged_as_an_error_not_a_token_warning(monkeypatch, caplog):
    """403 is a SERVER fault that takes out a whole environment, not a dead device.

    This is the real production failure: an APNs auth key scoped to Sandbox only answered
    200 for three simulator tokens and `403 BadEnvironmentKeyInToken` for the one real
    iPhone. Every ledger row read `sent`, and no TestFlight user received a push for weeks.
    Logged at WARNING alongside ordinary token rejections, it was indistinguishable from
    routine noise.
    """
    import logging

    caplog.set_level(logging.WARNING, logger="app.services.push_service")
    outcome = await _send(
        monkeypatch,
        devices=[{"token": "e" * 64, "environment": "production"}],
        statuses=[(403, "BadEnvironmentKeyInToken")],
    )
    assert outcome.accepted == 0
    assert "BadEnvironmentKeyInToken" in (outcome.summary() or "")

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, (
        "a 403 was not raised to ERROR — it reads as one more flaky token instead of a "
        "configuration fault that silences an entire environment"
    )
    assert "production" in errors[0].getMessage(), (
        "the error must name WHICH environment is dead; that is the whole diagnostic value"
    )


# ── every exit returns a PushOutcome ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_unsignable_provider_key_returns_an_outcome_not_a_bare_int(monkeypatch):
    """The reachable one, and it used to CRASH after the claim row was already written.

    `send_to_user` kept `return 0` on three exits after its signature became
    `-> PushOutcome`. Two are shadowed by earlier checks in `_deliver`; this one is not — it
    fires when all four APNS_* are present and signing still fails, i.e. a mangled
    `APNS_AUTH_KEY` PEM, which is what happens when a `.p8` is pasted into an env var.

    The failure was vicious: `_deliver` called `.summary()` → `AttributeError`, the
    per-recipient guard swallowed it, `mark_state` never ran, and the `(user_id, dedup_key)`
    pair was permanently burned — that notification could never be retried for that user.
    """
    _configure_apns(monkeypatch)
    monkeypatch.setattr(ps.httpx, "AsyncClient", lambda **kw: _client_returning([])())
    svc = PushService()
    monkeypatch.setattr(svc, "_provider_jwt", lambda: None)

    outcome = await svc.send_to_user(
        "u1", title="t", body="b",
        devices=[{"token": "f" * 64, "environment": "production"}],
    )
    assert isinstance(outcome, PushOutcome), f"got {type(outcome).__name__}, not a PushOutcome"
    assert outcome.accepted == 0
    summary = outcome.summary() or ""
    assert "APNS_AUTH_KEY" in summary, (
        f"the failure must name the provider key, not the devices; got {summary!r}"
    )


@pytest.mark.asyncio
async def test_a_disabled_service_returns_an_outcome_naming_the_misconfiguration(monkeypatch):
    """`enabled` is false when any APNS_* is unset. The caller records the reason."""
    monkeypatch.setattr(settings, "APNS_KEY_ID", None)
    svc = PushService()
    outcome = await svc.send_to_user(
        "u1", title="t", body="b", devices=[{"token": "g" * 64, "environment": "production"}]
    )
    assert isinstance(outcome, PushOutcome)
    assert outcome.accepted == 0
    assert "not configured" in (outcome.summary() or "")


@pytest.mark.asyncio
async def test_no_devices_is_not_reported_as_a_failure(monkeypatch):
    """`no_device` is a legitimate state, not an error. Writing a failure string for every
    tokenless user would fill `last_error` with noise and blunt the diagnostic query."""
    _configure_apns(monkeypatch)
    svc = PushService()
    monkeypatch.setattr(svc, "_provider_jwt", lambda: "jwt")
    outcome = await svc.send_to_user("u1", title="t", body="b", devices=[])
    assert isinstance(outcome, PushOutcome)
    assert outcome.attempted == 0 and outcome.summary() is None


def test_an_unconfigured_apns_is_logged_as_an_error_not_a_debug_line():
    """`logger.debug` is invisible at the default INFO level.

    That single line was the only trace that every notification in the system was being
    dropped. Combined with the early return it replaced, a misconfigured deploy produced no
    inbox row, no log, and a calling job that reported "delivered 0/0" — which is what let a
    month of undelivered notifications pass unnoticed.
    """
    import inspect
    from app.services.push_dispatch_service import PushDispatchService

    src = inspect.getsource(PushDispatchService._notify_users_inner)
    code = "\n".join(
        "" if line.strip().startswith("#") else line for line in src.splitlines()
    )
    assert "APNs is NOT CONFIGURED" in code
    marker = code.index("APNs is NOT CONFIGURED")
    assert "logger.error" in code[max(0, marker - 300):marker], (
        "the unconfigured-APNs message is no longer logged at ERROR"
    )


# ── apns-expiration ──────────────────────────────────────────────────────────


def _headers_from(monkeypatch, **send_kwargs):
    """Capture the headers of a single APNs POST."""
    _configure_apns(monkeypatch)
    captured = {}

    class _Resp:
        status_code = 200
        text = ""
        def json(self): return {}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured.update(headers or {})
            return _Resp()

    monkeypatch.setattr(ps.httpx, "AsyncClient", lambda **kw: _Client())
    svc = PushService()
    monkeypatch.setattr(svc, "_provider_jwt", lambda: "jwt")
    asyncio.run(svc.send_to_user(
        "u1", title="t", body="b",
        devices=[{"token": "z" * 64, "environment": "production"}],
        **send_kwargs,
    ))
    return captured


def test_no_expiration_means_the_header_is_ABSENT_not_zero(monkeypatch):
    """THE TRAP. `apns-expiration: 0` tells APNs to attempt delivery once and never store
    the notification — the strictest possible expiry, not the absence of one. Sending 0
    where "no expiry" was meant would silently drop every push to a device that happened
    to be offline for a second, including the report a user paid 20 credits for."""
    headers = _headers_from(monkeypatch, expiration_hours=None)
    assert "apns-expiration" not in headers


def test_an_expiration_is_an_absolute_epoch_in_the_future(monkeypatch):
    """A DURATION in the header would be read as a timestamp in 1970 — i.e. already
    expired — so the conversion is the whole point of the parameter."""
    import time as _time

    before = int(_time.time())
    headers = _headers_from(monkeypatch, expiration_hours=4)
    value = int(headers["apns-expiration"])
    assert before + 4 * 3600 <= value <= before + 4 * 3600 + 30, (
        f"expected an epoch ~4h out, got {value} (now={before})"
    )


def test_a_non_positive_expiration_is_omitted_rather_than_sent(monkeypatch):
    """Belt and braces for a caller that bypasses the registry's own validation. The
    "0 means omit, never send 0" invariant has to hold at the boundary too."""
    assert "apns-expiration" not in _headers_from(monkeypatch, expiration_hours=0)
    assert "apns-expiration" not in _headers_from(monkeypatch, expiration_hours=-3)


def test_the_delivery_passes_the_kinds_own_expiration(monkeypatch):
    """Wiring check: `_deliver` must read it off the registry rather than leaving the
    default, or every kind's carefully chosen window is dead configuration."""
    from app.services.notification_kinds import KIND_RESEARCH_COMPLETE

    captured = {}

    class _RecordingPush:
        enabled = True
        async def send_to_user(self, user_id, **kw):
            captured.update(kw)
            return PushOutcome(attempted=1, accepted=1)

    svc = object.__new__(PushDispatchService)
    svc._push = _RecordingPush()
    svc.supabase = None
    svc.mark_state = lambda *a, **k: None

    asyncio.run(svc._deliver(
        _Recipient(user_id="u1", devices=[{"token": "x", "environment": "production"}]),
        get_kind(KIND_PRICE_ALERT),
        title="t", body="b", dedup_key="alert:1", route={"ticker": "ORCL"},
    ))
    assert captured["expiration_hours"] == get_kind(KIND_PRICE_ALERT).expiration_hours

    asyncio.run(svc._deliver(
        _Recipient(user_id="u1", devices=[{"token": "x", "environment": "production"}]),
        get_kind(KIND_RESEARCH_COMPLETE),
        title="t", body="b", dedup_key="report:1", route={"ticker": "ORCL"},
    ))
    assert captured["expiration_hours"] is None, (
        "the report the user paid for was given an expiry — it must never be discarded"
    )


def test_the_payload_carries_the_dedup_key(monkeypatch):
    """What makes "Mark as Read" possible at all.

    That action is registered on all six categories, so every notification offers it, and
    it did nothing — the payload named no row. `route` carries a ticker and a tab; this is
    the other half of the `(user_id, dedup_key)` unique index.
    """
    captured = {}

    class _RecordingPush:
        enabled = True
        async def send_to_user(self, user_id, **kw):
            captured.update(kw)
            return PushOutcome(attempted=1, accepted=1)

    svc = object.__new__(PushDispatchService)
    svc._push = _RecordingPush()
    svc.supabase = None
    svc.mark_state = lambda *a, **k: None
    asyncio.run(svc._deliver(
        _Recipient(user_id="u1", devices=[{"token": "x", "environment": "production"}]),
        get_kind(KIND_PRICE_ALERT),
        title="t", body="b", dedup_key="alert:42", route={"ticker": "ORCL"},
    ))
    assert captured["data"]["dedup_key"] == "alert:42"
    # A flat scalar, like every other route value — iOS `AnyCodable` yields "" for
    # anything nested (auth.md §3).
    assert isinstance(captured["data"]["dedup_key"], str)


def test_the_module_docstring_does_not_claim_push_is_unwired():
    """It opened with "NOT wired to any trigger yet — future events (research-complete,
    price alerts) will call…" while TEN kinds shipped through it. That is the first thing
    anyone debugging a delivery reads, and it points them away from the code that ran."""
    import app.services.push_service as module

    doc = module.__doc__ or ""
    assert "NOT wired to any trigger" not in doc
    assert "push_dispatch_service" in doc, (
        "the docstring no longer says which layer decides WHO gets a notification, which "
        "is the question a reader arrives with"
    )
