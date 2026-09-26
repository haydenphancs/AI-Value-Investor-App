"""
End to end: the worker's retried PATCH against the real endpoint and the real ledger.

`BackendClient._call` (backend/marketing/main.py) retries every call after a transport error or a
502/503/504 and documents every call as "idempotent by contract". The round-1 worker fence
(`update_run(worker=True)`) broke that for terminal writes: when the first attempt COMMITTED but
its response was lost, the retry found the run already closed and got 409 MARKETING_RUN_NOT_HELD,
and the worker logged ERROR "could not record deferral/failure" for a write that had landed.

The real worker client talks to the real FastAPI app through an httpx MockTransport; the real
`MarketingRunService` runs over the in-memory PostgREST fake of test_marketing_run_service.py.
Only the transport is doctored: it forwards the first terminal PATCH (so the server commits it)
and then raises ReadTimeout, exactly a response lost after commit. Hermetic.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import marketing_internal as mod
from app.config import settings
from app.main import app
from app.services.marketing import run_service as mrs
from test_marketing_run_service import FakeSupabase

_SCRIPT = Path(__file__).resolve().parents[1] / "marketing" / "main.py"
_TOKEN = "replay-test-token"


def _load_worker():
    spec = importlib.util.spec_from_file_location("marketing_worker_replay_under_test", _SCRIPT)
    worker = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = worker
    spec.loader.exec_module(worker)  # type: ignore[union-attr]
    return worker


@pytest.fixture
def world(monkeypatch):
    worker = _load_worker()
    monkeypatch.setattr(worker.time, "sleep", lambda _s: None)  # no real retry back-off
    monkeypatch.setattr(settings, "MARKETING_WORKER_TOKEN", _TOKEN)
    monkeypatch.setattr(mrs.settings, "MARKETING_RUN_STALE_SECONDS", 2700)
    monkeypatch.setattr(mrs.settings, "MARKETING_MAX_RUN_ATTEMPTS", 6)
    ledger = mrs.MarketingRunService(supabase=FakeSupabase())
    monkeypatch.setattr(mod, "get_marketing_run_service", lambda: ledger)
    from app.services.marketing import script_service as _ss

    async def _no_writer(*_a, **_k):  # the kick routes must never reach the model here
        raise AssertionError("writer called")

    scripts = _ss.MarketingScriptService(ledger, writer=_no_writer)
    monkeypatch.setattr(mod, "get_marketing_script_service", lambda: scripts)
    server = TestClient(app)  # no lifespan: its jobs would reach Supabase
    seen = []

    def forward(request: httpx.Request) -> httpx.Response:
        path = request.url.path  # /api/v1/internal/marketing/…
        r = server.request(request.method, path, content=request.content,
                           headers={k: v for k, v in request.headers.items() if k.lower() != "host"})
        seen.append((request.method, path, request.content, r.status_code))
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})

    return worker, ledger, forward, seen


_NONCE = "feedfacefeedfacefeedfacefeedface"


def _client(worker, handler, run=None):
    """The REAL worker client over the real app. With `run`, it holds that run's claim exactly
    as `main()` does after claiming (`BackendClient.hold` → `X-Marketing-Claim`)."""
    api = worker.BackendClient("http://backend.test", _TOKEN)
    api._client = httpx.Client(base_url="http://backend.test/api/v1/internal/marketing",
                               headers={"X-Marketing-Worker-Token": _TOKEN},
                               transport=httpx.MockTransport(handler))
    if run is not None:
        api.hold(run, run["metadata"]["claim_nonce"])
    return api


def _claimed_run(ledger, nonce: str = _NONCE) -> dict:
    today = mrs.run_date_et(datetime.now(timezone.utc))
    import asyncio

    row, reason = asyncio.run(ledger.claim_run(today, worker_version="t", dry_run=True,
                                               claim_nonce=nonce))
    assert reason == mrs.CLAIMED
    return row


@pytest.mark.parametrize("fields", [
    {"status": "failed", "finished": True, "last_error": "deferred: poll budget spent"},  # the deferral
    {"status": "skipped", "finished": True, "metadata": {"skip_reason": "rest_day"}},     # _close_skipped
    {"status": "media_ready", "finished": True},
])
def test_a_terminal_patch_whose_response_was_lost_is_not_refused_on_retry(world, fields, caplog):
    worker, ledger, forward, seen = world
    run = _claimed_run(ledger)
    lost = {"done": False}

    def lose_the_first_response(request: httpx.Request) -> httpx.Response:
        response = forward(request)
        if request.method == "PATCH" and not lost["done"]:
            lost["done"] = True  # the server committed; the worker never hears back
            raise httpx.ReadTimeout("response lost after commit", request=request)
        return response

    api = _client(worker, lose_the_first_response, run)
    with caplog.at_level(logging.INFO):
        body = api.update_run(run["id"], **fields)
    assert body["status"] == fields["status"]
    assert [code for method, _p, _c, code in seen if method == "PATCH"] == [200, 200]
    stored = ledger.sb.tables[mrs.RUNS].rows[0]
    assert stored["status"] == fields["status"]
    assert any("is a replay" in r.getMessage() for r in caplog.records)


def test_the_worker_deferral_path_logs_no_false_error_after_a_lost_response(world, caplog):
    """The worker's own deferral branch (main.py): its ERROR 'could not record deferral' fired for
    a write that had landed. Driven through `BackendClient` exactly as `main()` calls it."""
    worker, ledger, forward, seen = world
    run = _claimed_run(ledger)
    lost = {"done": False}

    def lose_once(request):
        response = forward(request)
        if request.method == "PATCH" and not lost["done"]:
            lost["done"] = True
            raise httpx.ReadTimeout("response lost after commit", request=request)
        return response

    api = _client(worker, lose_once, run)
    errors = []
    try:
        api.update_run(run["id"], status="failed", finished=True, last_error="deferred: x")
    except worker.WorkerAPIError as e:  # what main() would log at ERROR
        errors.append(e)
    assert errors == []
    assert ledger.sb.tables[mrs.RUNS].rows[0]["status"] == "failed"


def test_a_real_state_conflict_is_still_409_not_held(world):
    worker, ledger, forward, _seen = world
    run = _claimed_run(ledger)
    api = _client(worker, forward, run)
    api.update_run(run["id"], status="skipped", finished=True)
    with pytest.raises(worker.WorkerAPIError) as err:
        api.update_run(run["id"], status="failed", finished=True, last_error="zombie")
    assert err.value.status == 409 and err.value.error_code == "MARKETING_RUN_NOT_HELD"
    assert ledger.sb.tables[mrs.RUNS].rows[0]["status"] == "skipped"



# ── the caller-claim fence, end to end (rules marketing.md §2) ─────────────────


def _reclaimed_by_another(ledger, run) -> dict:
    """What a second tick does after the first went stale: re-claim with ITS nonce (attempts+1)."""
    import asyncio

    row = ledger.sb.tables[mrs.RUNS].rows[0]
    row["started_at"] = row["updated_at"] = "2000-01-01T00:00:00+00:00"   # the first tick went quiet
    today = mrs.run_date_et(datetime.now(timezone.utc))
    again, reason = asyncio.run(ledger.claim_run(today, worker_version="t2", dry_run=True,
                                                 claim_nonce="c0ffeec0ffeec0ffeec0ffeec0ffee00"))
    assert reason == mrs.CLAIMED and again["attempts"] == run["attempts"] + 1
    return again


@pytest.mark.parametrize("call", ["checkpoint", "terminal", "register", "kick", "posts", "assets"])
def test_a_zombie_tick_is_refused_on_every_route_once_its_run_was_re_claimed(world, call):
    """The old fence compared the row with ITSELF (the attempts it had just read), so a zombie
    whose read came after the re-claim passed it and wrote over the new holder. Now each route
    compares the CALLER's claim: the zombie gets 409 MARKETING_RUN_NOT_HELD and nothing moves."""
    worker, ledger, forward, _seen = world
    run = _claimed_run(ledger)
    zombie = _client(worker, forward, run)
    newer = _reclaimed_by_another(ledger, run)
    before = dict(ledger.sb.tables[mrs.RUNS].rows[0])
    act = {
        "checkpoint": lambda: zombie.update_run(run["id"], stage="selected"),
        "terminal": lambda: zombie.update_run(run["id"], status="failed", finished=True, last_error="z"),
        "register": lambda: zombie.register_asset(run["id"], kind="manifest", ext="json",
                                                  sha256="a" * 64, bytes=10),
        "kick": lambda: zombie.kick_script(run["id"]),
        "posts": lambda: zombie.create_posts(run["id"], [{"platform": "x", "format": "text"}]),
        "assets": lambda: zombie.list_assets(run["id"]),
    }[call]
    with pytest.raises(worker.WorkerAPIError) as err:
        act()
    assert err.value.status == 409 and err.value.error_code == "MARKETING_RUN_NOT_HELD", err.value
    after = ledger.sb.tables[mrs.RUNS].rows[0]
    assert after["attempts"] == newer["attempts"] and after["metadata"]["claim_nonce"] == newer["metadata"]["claim_nonce"]
    assert after["status"] == before["status"] and after["stage"] == before["stage"]
    assert after["updated_at"] == before["updated_at"], "the zombie bumped the new holder's liveness"
    assert not ledger.sb.tables[mrs.ASSETS].rows


def test_the_new_holder_is_not_refused(world):
    worker, ledger, forward, _seen = world
    run = _claimed_run(ledger)
    newer = _reclaimed_by_another(ledger, run)
    api = _client(worker, forward, newer)
    assert api.update_run(newer["id"], stage="selected")["stage"] == "selected"


def test_a_call_without_a_claim_is_a_422_contract_breach(world):
    worker, ledger, forward, _seen = world
    run = _claimed_run(ledger)
    api = _client(worker, forward)          # never called hold()
    with pytest.raises(worker.WorkerAPIError) as err:
        api.update_run(run["id"], stage="selected")
    assert err.value.status == 422 and err.value.error_code == "MARKETING_REQUEST_INVALID"


def test_a_claim_for_the_same_attempt_but_another_nonce_is_refused(world):
    """Attempts alone collide across runs and after a manual reset; the nonce tells them apart."""
    worker, ledger, forward, _seen = world
    run = _claimed_run(ledger)
    impostor = _client(worker, forward, {**run, "metadata": {"claim_nonce": "1" * 32}})
    impostor.hold(run, "1" * 32)
    with pytest.raises(worker.WorkerAPIError) as err:
        impostor.update_run(run["id"], stage="selected")
    assert err.value.status == 409
