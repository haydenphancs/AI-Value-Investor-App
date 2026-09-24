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
    server = TestClient(app)  # no lifespan: its jobs would reach Supabase
    seen = []

    def forward(request: httpx.Request) -> httpx.Response:
        path = request.url.path  # /api/v1/internal/marketing/…
        r = server.request(request.method, path, content=request.content,
                           headers={k: v for k, v in request.headers.items() if k.lower() != "host"})
        seen.append((request.method, path, request.content, r.status_code))
        return httpx.Response(r.status_code, content=r.content, headers={"content-type": "application/json"})

    return worker, ledger, forward, seen


def _client(worker, handler):
    api = worker.BackendClient("http://backend.test", _TOKEN)
    api._client = httpx.Client(base_url="http://backend.test/api/v1/internal/marketing",
                               headers={"X-Marketing-Worker-Token": _TOKEN},
                               transport=httpx.MockTransport(handler))
    return api


def _claimed_run(ledger) -> dict:
    today = mrs.run_date_et(datetime.now(timezone.utc))
    import asyncio

    row, reason = asyncio.run(ledger.claim_run(today, worker_version="t", dry_run=True))
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

    api = _client(worker, lose_the_first_response)
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

    api = _client(worker, lose_once)
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
    api = _client(worker, forward)
    api.update_run(run["id"], status="skipped", finished=True)
    with pytest.raises(worker.WorkerAPIError) as err:
        api.update_run(run["id"], status="failed", finished=True, last_error="zombie")
    assert err.value.status == 409 and err.value.error_code == "MARKETING_RUN_NOT_HELD"
    assert ledger.sb.tables[mrs.RUNS].rows[0]["status"] == "skipped"
