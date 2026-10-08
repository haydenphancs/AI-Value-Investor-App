"""B6 of the 2026-10-07 benchmark-producer fixes: the universe is read PER RUN, and the manual
triggers run under the scheduled phase's claim.

1. `universe_data.load_universe` memoises a file for the life of the process and prefers its
   disk copy, so a regenerated `benchmark_universe.json` uploaded to the `universe-data`
   bucket was not seen by the quarterly / weekly recompute until a redeploy. Each run now
   fetches the file from Storage itself, falling back (with a WARNING naming the copy used)
   to the previous run's copy, then to the process memo.
2. `POST /admin/refresh-industry-benchmarks` and `POST /admin/refresh-sector-benchmarks`
   started `recompute_all` with NO claim, so a manual run could overlap the quarterly chain's
   own medians phase — two ~47k-call FMP bursts racing on the same rows. They now take
   `JOB_INDUSTRY_BENCHMARK_QUARTERLY` in the request, exactly like `/refresh-industry-dossier`
   (409 / 503 `SYSTEM_BUSY` on refusal), and release it with the outcome.

No network: the Storage client, the claim ledger and the service are stubbed at the binding
the code resolves.
"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

import app.services.industry_benchmark_service as ibs
from app import main as m
from app.api.v1.endpoints import admin
from app.services import notification_jobs

_LOGGER = "app.api.v1.endpoints.admin"


# ── 1. The universe, per run ─────────────────────────────────────────────────────────


def _payload(industries: List[Dict[str, Any]], **extra) -> bytes:
    return json.dumps({"industries": industries, "ticker_count": 1, **extra}).encode()


_V1 = [{"industry": "Software", "sector": "Technology", "market_caps": {"MSFT": 3e12}}]
_V2 = [{"industry": "Banks", "sector": "Financial Services", "market_caps": {"JPM": 6e11}}]


class _Storage:
    """`get_supabase().storage.from_(bucket).download(name)`: answers the current `blob`,
    or raises `error`. Records each (bucket, name)."""

    def __init__(self, blob: Optional[bytes] = None, error: Optional[Exception] = None):
        self.blob, self.error = blob, error
        self.downloads: List[tuple] = []

    def client(self):
        storage = self

        class _Bucket:
            def __init__(self, bucket):
                self.bucket = bucket

            def download(self, name):
                storage.downloads.append((self.bucket, name))
                if storage.error is not None:
                    raise storage.error
                return storage.blob

        return SimpleNamespace(storage=SimpleNamespace(from_=_Bucket))


def _install_storage(monkeypatch, storage: _Storage, memo: Optional[List[Dict[str, Any]]] = None):
    # `_fetch_benchmark_universe` uses the module-level `get_supabase` binding of ibs.
    monkeypatch.setattr(ibs, "get_supabase", storage.client)
    monkeypatch.setattr(ibs, "_last_fetched_universe", None)
    memo_calls: List[str] = []

    def _memo(name):
        memo_calls.append(name)
        return list(memo or [])

    monkeypatch.setattr(ibs, "load_universe", _memo)
    return memo_calls


def _sectors(svc) -> List[str]:
    return [sector for sector, _inds in svc._load_universe()]


def test_an_upload_takes_effect_on_the_next_run_without_a_restart(monkeypatch, caplog):
    storage = _Storage(_payload(_V1, generated_at="2026-06-01"))
    memo_calls = _install_storage(monkeypatch, storage)
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)

    with caplog.at_level(logging.INFO, logger=ibs.logger.name):
        assert _sectors(svc) == ["Technology"]
        storage.blob = _payload(_V2, generated_at="2026-10-08")     # the owner re-uploads
        assert _sectors(svc) == ["Financial Services"]

    assert storage.downloads == [("universe-data", ibs.BENCHMARK_UNIVERSE)] * 2
    assert memo_calls == []                       # the boot-time memo was never consulted
    assert any("generated 2026-10-08" in r.getMessage() for r in caplog.records)


def test_a_failed_fetch_falls_back_to_the_last_runs_copy_then_to_the_memo(monkeypatch, caplog):
    storage = _Storage(_payload(_V2))
    memo_calls = _install_storage(monkeypatch, storage, memo=_V1)
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)

    assert _sectors(svc) == ["Financial Services"]          # run 1 fetched V2
    storage.error = RuntimeError("storage 502")
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        assert _sectors(svc) == ["Financial Services"]      # run 2: V2 again, not the boot V1
    assert memo_calls == []
    msgs = [r.getMessage() for r in caplog.records]
    assert any("could not fetch" in x and "RuntimeError: storage 502" in x for x in msgs)
    assert any("an earlier run fetched (1 industries)" in x for x in msgs)

    monkeypatch.setattr(ibs, "_last_fetched_universe", None)   # e.g. a fresh process
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        assert _sectors(svc) == ["Technology"]               # the memo
    assert memo_calls == [ibs.BENCHMARK_UNIVERSE]
    assert any("this process's cached" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("blob, needle", [
    (b"{not json", "unreadable"),
    (b"", "unreadable"),
    (b"[]", "unreadable"),                                    # a list, not an object
    (json.dumps({"industries": "x"}).encode(), "unreadable"),
    (json.dumps({"tickers": []}).encode(), "unreadable"),
    (_payload([]), "lists no industries"),
    (None, "unreadable"),
])
def test_a_broken_upload_is_refused_loudly_and_the_previous_copy_kept(monkeypatch, caplog, blob, needle):
    storage = _Storage(blob)
    _install_storage(monkeypatch, storage, memo=_V1)
    monkeypatch.setattr(ibs, "_last_fetched_universe", list(_V2))
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)

    with caplog.at_level(logging.WARNING, logger=ibs.logger.name):
        assert _sectors(svc) == ["Financial Services"]
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and needle in errors[0] and "keeps the previous copy" in errors[0]


@pytest.mark.asyncio
async def test_each_scheduled_run_reads_the_universe_again(monkeypatch):
    storage = _Storage(_payload(_V1))
    _install_storage(monkeypatch, storage)
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)
    svc._calendar_quarter_blocked = False
    computed: List[str] = []

    async def compute(sector, *_a, **_k):
        computed.append(sector)
        return 3

    monkeypatch.setattr(svc, "_compute_sector", compute)
    monkeypatch.setattr(svc, "_sector_is_fresh", lambda _s, _h: False)
    await svc.recompute_all(skip_if_fresh_hours=24)
    storage.blob = _payload(_V2)
    await svc.recompute_all(skip_if_fresh_hours=24)
    assert computed == ["Technology", "Financial Services"]
    assert len(storage.downloads) == 2


# ── 2. The manual triggers take the quarterly phase's claim ───────────────────────────


class _Ledger:
    def __init__(self, granted: bool = True, state: Optional[dict] = None):
        self.granted, self.state = granted, state
        self.claims: List[dict] = []
        self.finishes: List[dict] = []

    def install(self, monkeypatch):
        def claim_scheduled(job, *, timezone_name="UTC", now=None, stale_seconds=None):
            self.claims.append({"job": job, "now": now, "stale_seconds": stale_seconds})
            return self.granted

        def finish_scheduled(job, *, success, items=0, error=None, timezone_name="UTC", now=None):
            self.finishes.append({"job": job, "success": success, "items": items,
                                  "error": error, "now": now})

        monkeypatch.setattr(notification_jobs, "claim_scheduled", claim_scheduled)
        monkeypatch.setattr(notification_jobs, "finish_scheduled", finish_scheduled)
        monkeypatch.setattr(notification_jobs, "scheduled_job_state", lambda job: self.state)
        return self


class _Service:
    def __init__(self, result=None, raises=None):
        self.result, self.raises = result, raises
        self.calls: List[Optional[int]] = []

    async def recompute_all(self, *, skip_if_fresh_hours=None, **_k):
        self.calls.append(skip_if_fresh_hours)
        if self.raises is not None:
            raise self.raises
        return self.result


def _install_service(monkeypatch, service):
    # Both routes resolve `get_industry_benchmark_service` from ibs at call time.
    monkeypatch.setattr(ibs, "_industry_benchmark_service", service)
    monkeypatch.setattr(admin, "_authorize_admin", lambda _user, _token: None)
    return service


async def _drain(task_name: str):
    tasks = [t for t in admin._admin_tasks if t.get_name() == task_name]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)


def _body(resp) -> dict:
    return json.loads(resp.body)


_ROUTES = [
    pytest.param(admin.refresh_industry_benchmarks, {"skip_recent_hours": 24}, 24,
                 "admin_refresh_industry_benchmarks", id="industry-benchmarks"),
    pytest.param(admin.refresh_industry_benchmarks, {"skip_recent_hours": 0}, None,
                 "admin_refresh_industry_benchmarks", id="industry-benchmarks-force"),
    pytest.param(admin.refresh_sector_benchmarks, {"backfill": False}, 24,
                 "admin_refresh_sector_benchmarks", id="sector-benchmarks"),
    pytest.param(admin.refresh_sector_benchmarks, {"backfill": True}, None,
                 "admin_refresh_sector_benchmarks", id="sector-benchmarks-backfill"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("route,kwargs,skip,task_name", _ROUTES)
async def test_a_granted_claim_runs_the_recompute_and_settles_the_day(
    monkeypatch, route, kwargs, skip, task_name,
):
    ledger = _Ledger().install(monkeypatch)
    service = _install_service(monkeypatch, _Service(result={"rows_upserted": 4321}))

    resp = await route(x_admin_token=None, user={}, **kwargs)
    assert resp["status"] == "started" and resp["job"] == m.JOB_INDUSTRY_BENCHMARK_QUARTERLY
    assert resp["skip_if_fresh_hours"] == skip
    await _drain(task_name)

    assert ledger.claims == [{"job": m.JOB_INDUSTRY_BENCHMARK_QUARTERLY,
                              "now": ledger.claims[0]["now"],
                              "stale_seconds": m._CHAIN_PHASE_STALE_SECONDS}]
    assert service.calls == [skip]
    (finish,) = ledger.finishes
    assert finish["job"] == m.JOB_INDUSTRY_BENCHMARK_QUARTERLY
    assert finish["success"] is True and finish["items"] == 4321 and finish["error"] is None
    assert finish["now"] == ledger.claims[0]["now"]           # stamped with the claim time


@pytest.mark.asyncio
@pytest.mark.parametrize("state, reason, status", [
    ({"enabled": True, "claim_at": "2026-10-04T04:00:00+00:00", "run_day": None}, "held", 409),
    ({"enabled": True, "claim_at": None, "run_day": "TODAY"}, "already_ran_today", 409),
    ({"enabled": False}, "disabled", 409),
    ({"enabled": True, "claim_at": None, "run_day": "2026-07-05"}, "claim_failed", 409),
    (None, "ledger_unreadable", 503),
])
@pytest.mark.parametrize("route,kwargs", [
    (admin.refresh_industry_benchmarks, {"skip_recent_hours": 24}),
    (admin.refresh_sector_benchmarks, {"backfill": False}),
])
async def test_a_refused_claim_starts_nothing_and_says_why(
    monkeypatch, caplog, route, kwargs, state, reason, status,
):
    if state and state.get("run_day") == "TODAY":
        from datetime import datetime, timezone
        state = {**state, "run_day": datetime.now(timezone.utc).date().isoformat()}
    ledger = _Ledger(granted=False, state=state).install(monkeypatch)
    service = _install_service(monkeypatch, _Service(result={"rows_upserted": 1}))

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        resp = await route(x_admin_token=None, user={}, **kwargs)

    assert resp.status_code == status
    body = _body(resp)
    assert body["error_code"] == "SYSTEM_BUSY"
    assert body["details"]["reason"] == reason
    assert body["details"]["job"] == m.JOB_INDUSTRY_BENCHMARK_QUARTERLY
    assert all(isinstance(v, (str, int, float, bool)) for v in body["details"].values())
    assert body["user_message"] == admin._BENCHMARK_REFUSAL_COPY[reason]
    assert service.calls == [] and ledger.finishes == []
    assert any("REFUSED" in r.getMessage() and reason in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("exc, status, reason", [
    (ibs.IndustryBenchmarkRecomputeSkipped("empty universe", "no file"), "skipped", "empty universe"),
    (ibs.IndustryBenchmarkRecomputeIncomplete("fiscal", ["Energy"], {"rows_upserted": 9}),
     "incomplete", "sectors failed"),
])
async def test_a_typed_refusal_leaves_the_day_open_and_is_logged_once(
    monkeypatch, caplog, exc, status, reason,
):
    ledger = _Ledger().install(monkeypatch)
    _install_service(monkeypatch, _Service(raises=exc))
    loop = asyncio.get_running_loop()
    unhandled: List[Dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, ctx: unhandled.append(ctx))
    try:
        with caplog.at_level(logging.INFO, logger=_LOGGER):
            await admin.refresh_industry_benchmarks(x_admin_token=None, user={}, skip_recent_hours=24)
            (task,) = [t for t in admin._admin_tasks
                       if t.get_name() == "admin_refresh_industry_benchmarks"]
            await asyncio.wait({task})
            await asyncio.sleep(0)
            assert task.result() == {"status": status, "reason": reason, "detail": str(exc)}
            del task
            gc.collect()
    finally:
        loop.set_exception_handler(previous)

    (finish,) = ledger.finishes
    assert finish["success"] is False and finish["error"] == str(exc)
    # A WARNING ties it to the trigger; the service already logged the cause at ERROR.
    assert any("did not settle" in r.getMessage() and r.levelno == logging.WARNING
               for r in caplog.records)
    assert not [r for r in caplog.records if "FAILED" in r.getMessage()]
    assert not [c for c in unhandled if "never retrieved" in str(c.get("message", ""))]


@pytest.mark.asyncio
async def test_an_unexpected_crash_releases_the_claim_and_is_logged_with_its_stack(monkeypatch, caplog):
    ledger = _Ledger().install(monkeypatch)
    _install_service(monkeypatch, _Service(raises=KeyError("bug")))
    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        await admin.refresh_sector_benchmarks(x_admin_token=None, user={}, backfill=False)
        await _drain("admin_refresh_sector_benchmarks")
    (finish,) = ledger.finishes
    assert finish["success"] is False and finish["error"] == "KeyError: 'bug'"
    failed = [r for r in caplog.records if "'admin_refresh_sector_benchmarks' FAILED (KeyError" in r.getMessage()]
    assert len(failed) == 1 and failed[0].exc_info


@pytest.mark.asyncio
async def test_a_task_that_cannot_start_releases_the_claim_at_once(monkeypatch):
    ledger = _Ledger().install(monkeypatch)
    _install_service(monkeypatch, _Service(result={}))

    def boom(coro, name):
        coro.close()
        raise RuntimeError("no loop")

    monkeypatch.setattr(admin, "_spawn_admin_task", boom)
    resp = await admin.refresh_industry_benchmarks(x_admin_token=None, user={}, skip_recent_hours=24)
    assert resp.status_code >= 500
    (finish,) = ledger.finishes
    assert finish["success"] is False and finish["error"] == "manual refresh failed to start"


@pytest.mark.asyncio
async def test_an_empty_universe_through_the_route_is_released_unsettled(monkeypatch, caplog):
    """The twin of the old `test_admin_trigger_logs_the_skip...` row for these routes: the
    REAL service, an empty universe — refused, logged, the claim released, no stray task."""
    ledger = _Ledger().install(monkeypatch)
    svc = ibs.IndustryBenchmarkService.__new__(ibs.IndustryBenchmarkService)
    svc._calendar_quarter_blocked = False
    monkeypatch.setattr(ibs, "_fetch_benchmark_universe", lambda: None)
    monkeypatch.setattr(ibs, "_last_fetched_universe", None)
    monkeypatch.setattr(ibs, "load_universe", lambda _f: [])
    _install_service(monkeypatch, svc)

    with caplog.at_level(logging.WARNING):
        resp = await admin.refresh_industry_benchmarks(x_admin_token=None, user={}, skip_recent_hours=24)
        assert resp["status"] == "started"
        await _drain("admin_refresh_industry_benchmarks")
    (finish,) = ledger.finishes
    assert finish["success"] is False and "SKIPPED (empty universe)" in finish["error"]
    assert any(r.name == ibs.logger.name and "recompute SKIPPED (empty universe)" in r.getMessage()
               for r in caplog.records)
