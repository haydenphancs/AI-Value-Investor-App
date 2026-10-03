"""`GET /admin/industry-dossier` (audit) and `POST /admin/refresh-industry-dossier` (manual run).

Found 2026-10-01 during the PLUG TAM/CAGR investigation:

1. The audit view summarised rows per `source_grain` only. The zero-TAM placeholder ("No
   public data available — FRED/Census unreachable at compute time", `current_tam_b = 0`) is
   stored as `source_grain = 'all_industry'`, so from 2026-07-05, with 138 of 158 rows broken,
   the summary read like a healthy all_industry count and hid the outage.
2. The manual refresh was a bare `asyncio.create_task` (weak reference: GC-able mid-run, and
   its exception retrieved by nobody) that ran OUTSIDE the quarterly chain's day-keyed claim,
   so it could overlap the scheduled phase and pay Phase B's Gemini research twice.

No network, no Supabase: the claim ledger, the dossier service and the Supabase client are
all stubbed at the binding the route actually resolves.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import admin
from app.services import notification_jobs
from app.services import industry_dossier_service as dossier_mod

_ADMIN = {"id": "aaaabbbb-cccc-4ddd-8eee-ffff00001111", "is_admin": True}
_LOGGER = "app.api.v1.endpoints.admin"


# ── Fakes ─────────────────────────────────────────────────────────────────────


class _Query:
    """A postgrest builder stand-in: every chained call returns self; `sb_exec` reads `.table`."""

    def __init__(self, table: str) -> None:
        self.table = table

    def __getattr__(self, _name):
        return lambda *a, **k: self


class _FakeSB:
    def table(self, name: str) -> _Query:
        return _Query(name)


def _install_supabase(monkeypatch, dossier_rows):
    import app.database

    monkeypatch.setattr(app.database, "get_supabase", lambda: _FakeSB())

    async def fake_sb_exec(query):
        if query.table == "industry_dossier":
            return SimpleNamespace(data=dossier_rows)
        # The grounded-override audit is retired (2026-10-02): the route must not read it.
        raise AssertionError(f"unexpected table {query.table!r}")

    monkeypatch.setattr(admin, "sb_exec", fake_sb_exec)


def _row(industry, tam, grain="all_industry", scope="us", sector="Technology"):
    return {
        "industry": industry,
        "sector": sector,
        "current_tam_b": tam,
        "future_tam_b": tam,
        "source_grain": grain,
        "tam_scope": scope,
        "computed_at": "2026-07-05T02:00:00+00:00",
        "source_label": (
            "No public data available — FRED/Census unreachable at compute time"
            if not tam else "BEA GDP"
        ),
    }


class _Ledger:
    """Records claim / finish calls; `granted` and `state` script the claim's answer."""

    def __init__(self, granted=True, state=None):
        self.granted = granted
        self.state = state
        self.claims: list[dict] = []
        self.finishes: list[dict] = []
        self.finished = asyncio.Event()

    def install(self, monkeypatch):
        def claim_scheduled(job, *, timezone_name="UTC", now=None, stale_seconds=None):
            self.claims.append({"job": job, "now": now, "stale_seconds": stale_seconds})
            return self.granted

        def finish_scheduled(job, *, success, items=0, error=None, timezone_name="UTC", now=None):
            self.finishes.append(
                {"job": job, "success": success, "items": items, "error": error, "now": now}
            )

        monkeypatch.setattr(notification_jobs, "claim_scheduled", claim_scheduled)
        monkeypatch.setattr(notification_jobs, "finish_scheduled", finish_scheduled)
        monkeypatch.setattr(notification_jobs, "scheduled_job_state", lambda job: self.state)
        return self


class _Service:
    def __init__(self, result=None, raises=None, gate: asyncio.Event | None = None):
        self.result = result
        self.raises = raises
        self.gate = gate
        self.calls = 0

    async def recompute_all(self, force: bool = False):
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.raises is not None:
            raise self.raises
        return self.result


def _install_service(monkeypatch, service):
    monkeypatch.setattr(dossier_mod, "get_industry_dossier_service", lambda: service)
    return service


async def _drain_admin_tasks():
    tasks = list(admin._admin_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)  # let done-callbacks run


def _body(resp):
    return json.loads(resp.body)


# ── The placeholder predicate ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [0, 0.0, None, -5, -0.01, float("nan"), float("inf"), float("-inf"), "abc", "", True, False, [], {}],
)
def test_placeholder_values_count_as_placeholders(value):
    assert admin._row_tam_is_placeholder({"current_tam_b": value}) is True


@pytest.mark.parametrize("value", [0.01, 1, 12.5, 4_200.0, "12.5"])
def test_real_tam_values_are_not_placeholders(value):
    assert admin._row_tam_is_placeholder({"current_tam_b": value}) is False


def test_a_row_with_no_tam_column_is_a_placeholder():
    assert admin._row_tam_is_placeholder({"industry": "Semiconductors"}) is True


@pytest.mark.parametrize(
    "value", [None, 0, 0.0, -3.2, 0.01, 1.0, 12.5, float("nan"), float("inf"), "12.5", "0", "NaN"],
)
def test_audit_predicate_agrees_with_the_self_heal_read_path(value):
    """The audit must count exactly the rows `get_or_compute_dossier` treats as a miss —
    otherwise the operator reads "0 placeholders" while every report self-heals live."""
    row = _row("X", value)
    service_view = dossier_mod._is_placeholder(dossier_mod.IndustryDossier.from_db_row(row))
    assert admin._row_tam_is_placeholder(row) is service_view


# ── The audit summary ─────────────────────────────────────────────────────────


def test_audit_counts_the_july_outage_shape():
    """138 placeholders + 20 real rows, all placeholders stored as `all_industry`."""
    rows = [_row(f"Ind {i:03d}", 0) for i in range(138)]
    rows += [_row(f"Real {i:02d}", 100.0 + i, grain="sector") for i in range(10)]
    rows += [_row(f"Broad {i:02d}", 50.0, grain="all_industry") for i in range(4)]
    rows += [_row(f"Global {i:02d}", 900.0, grain="industry", scope="global") for i in range(6)]

    audit = admin._dossier_tam_audit(rows)

    assert audit["tam_placeholder_count"] == 138
    assert audit["tam_placeholder_by_grain"] == {"all_industry": 138}
    assert audit["tam_placeholder_industries"] == sorted(f"Ind {i:03d}" for i in range(138))
    # The four REAL all_industry rows are not placeholders — grain alone cannot tell them apart.
    assert not any(n.startswith("Broad") for n in audit["tam_placeholder_industries"])
    assert audit["global_scope_count"] == 6
    assert audit["global_scope_industries"] == [f"Global {i:02d}" for i in range(6)]


def test_audit_of_an_empty_table_is_all_zero():
    assert admin._dossier_tam_audit([]) == {
        "tam_placeholder_count": 0,
        "tam_placeholder_industries": [],
        "tam_placeholder_by_grain": {},
        "global_scope_count": 0,
        "global_scope_industries": [],
    }


def test_a_global_row_with_a_zero_tam_is_still_a_placeholder():
    audit = admin._dossier_tam_audit([_row("Semiconductors", 0, grain="industry", scope="global")])
    assert audit["tam_placeholder_count"] == 1
    assert audit["global_scope_count"] == 1


def test_a_row_missing_its_industry_name_does_not_break_sorting():
    rows = [_row(None, 0), _row("Banks", 0), {"current_tam_b": None}]
    audit = admin._dossier_tam_audit(rows)
    assert audit["tam_placeholder_count"] == 3
    assert audit["tam_placeholder_industries"] == ["", "", "Banks"]


@pytest.mark.asyncio
async def test_audit_route_adds_the_counts_and_keeps_the_grain_summary(monkeypatch):
    rows = [_row("A", 0), _row("B", 0), _row("C", 120.5, grain="sector"),
            _row("D", 800.0, grain="industry", scope="global")]
    _install_supabase(monkeypatch, rows)

    out = await admin.list_industry_dossier(x_admin_token=None, user=_ADMIN)

    # Back-compat: `summary` is still the per-grain Counter, placeholders included.
    assert out["summary"] == {"all_industry": 2, "sector": 1, "industry": 1}
    assert out["total"] == 4
    assert out["tam_placeholder_count"] == 2
    assert out["tam_placeholder_industries"] == ["A", "B"]
    assert out["tam_placeholder_by_grain"] == {"all_industry": 2}
    assert out["global_scope_count"] == 1
    assert out["global_scope_industries"] == ["D"]   # the operator's check that 188 ran
    assert "last_override_run" not in out
    assert out["rows"] == rows


@pytest.mark.asyncio
async def test_audit_route_survives_malformed_rows(monkeypatch):
    rows = [_row("A", "garbage"), "not-a-dict", None, _row("B", float("nan")), _row("C", 10.0)]
    _install_supabase(monkeypatch, rows)

    out = await admin.list_industry_dossier(x_admin_token=None, user=_ADMIN)

    assert out["total"] == 3  # the two non-dict rows are dropped, not a 500
    assert out["tam_placeholder_count"] == 2
    assert out["tam_placeholder_industries"] == ["A", "B"]


@pytest.mark.asyncio
async def test_audit_route_failure_is_a_structured_error_body(monkeypatch):
    import app.database

    def boom():
        raise RuntimeError("supabase unreachable")

    monkeypatch.setattr(app.database, "get_supabase", boom)
    resp = await admin.list_industry_dossier(x_admin_token=None, user=_ADMIN)
    body = _body(resp)
    assert resp.status_code >= 500
    assert {"error_code", "message", "user_message", "action", "details"} <= set(body)
    assert body["details"]["step"] == "list_industry_dossier"


@pytest.mark.asyncio
async def test_audit_route_auth_is_unchanged(monkeypatch):
    def must_not_read():
        raise AssertionError("read before authorization")

    import app.database

    monkeypatch.setattr(app.database, "get_supabase", must_not_read)
    with pytest.raises(HTTPException) as ei:
        await admin.list_industry_dossier(x_admin_token=None, user=None)
    assert ei.value.status_code == 401
    with pytest.raises(HTTPException) as ei:
        await admin.list_industry_dossier(
            x_admin_token=None, user={"id": _ADMIN["id"], "is_admin": False},
        )
    assert ei.value.status_code == 403


# ── The manual refresh: claim, strong reference, release ─────────────────────


@pytest.mark.asyncio
async def test_refresh_takes_the_quarterly_claim_and_settles_it_on_success(monkeypatch):
    from app.main import _CHAIN_PHASE_STALE_SECONDS, JOB_INDUSTRY_DOSSIER_QUARTERLY

    ledger = _Ledger().install(monkeypatch)
    service = _install_service(
        monkeypatch, _Service(result={"status": "ok", "rows_upserted": 158}),
    )

    out = await admin.refresh_industry_dossier(x_admin_token=None, user=_ADMIN)
    assert out["status"] == "started"
    assert out["job"] == JOB_INDUSTRY_DOSSIER_QUARTERLY

    # The SAME claim the scheduled phase takes, with the chain's stale window.
    assert ledger.claims == [{
        "job": JOB_INDUSTRY_DOSSIER_QUARTERLY,
        "now": ledger.claims[0]["now"],
        "stale_seconds": _CHAIN_PHASE_STALE_SECONDS,
    }]
    await _drain_admin_tasks()

    assert service.calls == 1
    assert ledger.finishes == [{
        "job": JOB_INDUSTRY_DOSSIER_QUARTERLY,
        "success": True,
        "items": 158,
        "error": None,
        "now": ledger.claims[0]["now"],  # released with the CLAIM's stamp, like the manager
    }]
    assert out["claimed_at"] == ledger.claims[0]["now"].isoformat()


@pytest.mark.asyncio
async def test_refresh_holds_a_strong_reference_until_the_task_finishes(monkeypatch):
    _Ledger().install(monkeypatch)
    gate = asyncio.Event()
    _install_service(monkeypatch, _Service(result={"status": "ok", "rows_upserted": 1}, gate=gate))

    await admin.refresh_industry_dossier(x_admin_token=None, user=_ADMIN)
    held = [t for t in admin._admin_tasks if t.get_name() == "admin_refresh_industry_dossier"]
    assert len(held) == 1 and not held[0].done()

    gate.set()
    await _drain_admin_tasks()
    assert held[0] not in admin._admin_tasks  # released once done, so the set cannot grow


@pytest.mark.parametrize(
    "result",
    [
        {"status": "ok", "rows_upserted": 0},
        {"status": "ok"},
        {"status": "ok", "rows_upserted": True},
        {"status": "weird", "rows_upserted": 5},
        None,
        "ok",
    ],
)
@pytest.mark.asyncio
async def test_a_run_that_wrote_nothing_leaves_the_day_open(monkeypatch, result):
    """Returned, but upserted nothing (pre-read failed / every upsert failed) or answered in
    an unexpected shape: the claim is released UNSETTLED with a reason, so a same-day re-run
    — and, on a quarter-start Sunday, the scheduled phase — is still allowed."""
    ledger = _Ledger().install(monkeypatch)
    _install_service(monkeypatch, _Service(result=result))

    await admin.refresh_industry_dossier(x_admin_token=None, user=_ADMIN)
    await _drain_admin_tasks()

    (fin,) = ledger.finishes
    assert fin["success"] is False
    assert fin["items"] == (5 if result == {"status": "weird", "rows_upserted": 5} else 0)
    assert fin["error"] and "manual refresh returned" in fin["error"]


@pytest.mark.asyncio
async def test_skipped_is_a_warning_releases_unsettled_and_leaks_no_task_exception(
    monkeypatch, caplog,
):
    """`recompute_all` raises `IndustryDossierRecomputeSkipped` when it cannot run (no
    universe / no upstream credentials). The service already logged it at ERROR, so the
    route logs WARNING once, releases the claim with success=False and the reason, and the
    task ends normally — no second ERROR from the done-callback."""
    ledger = _Ledger().install(monkeypatch)
    exc = dossier_mod.IndustryDossierRecomputeSkipped("no upstream credentials", "keys unset")
    _install_service(monkeypatch, _Service(raises=exc))

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        await admin.refresh_industry_dossier(x_admin_token=None, user=_ADMIN)
        (task,) = [t for t in admin._admin_tasks if t.get_name() == "admin_refresh_industry_dossier"]
        await _drain_admin_tasks()

    assert task.exception() is None
    assert task.result()["status"] == "skipped"
    (fin,) = ledger.finishes
    assert fin["success"] is False and fin["error"] == str(exc)
    admin_records = [r for r in caplog.records if r.name == _LOGGER]
    assert not [r for r in admin_records if r.levelno >= logging.ERROR]
    assert any(
        r.levelno == logging.WARNING and "did not run" in r.getMessage() for r in admin_records
    )


@pytest.mark.asyncio
async def test_a_crash_releases_unsettled_and_is_logged_with_its_stack(monkeypatch, caplog):
    ledger = _Ledger().install(monkeypatch)
    _install_service(monkeypatch, _Service(raises=RuntimeError("boom")))

    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        await admin.refresh_industry_dossier(x_admin_token=None, user=_ADMIN)
        await _drain_admin_tasks()

    (fin,) = ledger.finishes
    assert fin["success"] is False and fin["error"] == "RuntimeError: boom"
    errors = [r for r in caplog.records if r.name == _LOGGER and r.levelno >= logging.ERROR]
    assert len(errors) == 1
    assert "admin_refresh_industry_dossier" in errors[0].getMessage()
    assert errors[0].exc_info is not None


@pytest.mark.asyncio
async def test_cancellation_still_releases_the_claim(monkeypatch):
    """A redeploy cancels the task mid-run: the release must still happen (shielded) and say
    so, or the quarterly chain waits out the 3-hour stale window."""
    ledger = _Ledger().install(monkeypatch)
    _install_service(monkeypatch, _Service(result={"status": "ok"}, gate=asyncio.Event()))

    await admin.refresh_industry_dossier(x_admin_token=None, user=_ADMIN)
    (task,) = [t for t in admin._admin_tasks if t.get_name() == "admin_refresh_industry_dossier"]
    await asyncio.sleep(0)
    task.cancel()
    await _drain_admin_tasks()
    for _ in range(50):  # the shielded to_thread release finishes on its own
        if ledger.finishes:
            break
        await asyncio.sleep(0.01)

    assert task.cancelled()
    (fin,) = ledger.finishes
    assert fin["success"] is False and fin["error"] == "cancelled (shutdown)"


@pytest.mark.asyncio
async def test_a_failure_to_start_releases_the_claim(monkeypatch):
    ledger = _Ledger().install(monkeypatch)
    service = _install_service(monkeypatch, _Service(result={"status": "ok", "rows_upserted": 1}))

    def explode(coro, name):
        coro.close()  # never scheduled — avoid the "never awaited" warning
        raise RuntimeError("loop refused the task")

    monkeypatch.setattr(admin, "_spawn_admin_task", explode)
    resp = await admin.refresh_industry_dossier(x_admin_token=None, user=_ADMIN)

    assert resp.status_code >= 500
    assert _body(resp)["details"]["step"] == "refresh_industry_dossier"
    assert service.calls == 0
    (fin,) = ledger.finishes
    assert fin["success"] is False and fin["error"] == "manual refresh failed to start"


# ── Refusals ──────────────────────────────────────────────────────────────────


def _today():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()


@pytest.mark.parametrize(
    "state, reason, status",
    [
        ({"enabled": True, "run_day": None, "claim_at": "2026-10-01T02:00:05+00:00"}, "held", 409),
        ({"enabled": True, "run_day": "TODAY", "claim_at": None}, "already_ran_today", 409),
        ({"enabled": False, "run_day": None, "claim_at": None}, "disabled", 409),
        ({"enabled": True, "run_day": "2026-07-05", "claim_at": None}, "claim_failed", 409),
        (None, "ledger_unreadable", 503),
    ],
)
@pytest.mark.asyncio
async def test_a_refused_claim_starts_nothing_and_says_why(monkeypatch, state, reason, status):
    if state and state.get("run_day") == "TODAY":
        state = {**state, "run_day": _today()}
    ledger = _Ledger(granted=False, state=state).install(monkeypatch)
    service = _install_service(monkeypatch, _Service(result={"status": "ok", "rows_upserted": 1}))
    before = set(admin._admin_tasks)

    resp = await admin.refresh_industry_dossier(x_admin_token=None, user=_ADMIN)

    assert resp.status_code == status
    body = _body(resp)
    assert body["error_code"] == "SYSTEM_BUSY"
    assert body["details"]["reason"] == reason
    assert body["user_message"] == admin._DOSSIER_REFUSAL_COPY[reason]
    # `details` must be flat scalars (iOS AnyCodable) — and never a None.
    assert all(isinstance(v, (str, int, float, bool)) for v in body["details"].values())
    assert set(admin._admin_tasks) == before
    assert service.calls == 0
    assert ledger.finishes == []  # nothing claimed, so nothing to release


@pytest.mark.parametrize(
    "state, expected",
    [
        (None, "ledger_unreadable"),
        ({}, "claim_failed"),
        ({"enabled": False, "run_day": "TODAY", "claim_at": "x"}, "disabled"),
        ({"enabled": True, "run_day": "TODAY", "claim_at": "x"}, "already_ran_today"),
        ({"enabled": True, "run_day": "TODAYT00:00:00", "claim_at": None}, "already_ran_today"),
        ({"enabled": True, "run_day": "2026-01-04", "claim_at": "2026-10-01T02:00:00Z"}, "held"),
        ({"enabled": True, "run_day": None, "claim_at": ""}, "claim_failed"),
    ],
)
def test_refusal_reason_matrix(state, expected):
    today = "2026-10-04"
    if state:
        state = {k: (v.replace("TODAY", today) if isinstance(v, str) else v) for k, v in state.items()}
    assert admin._dossier_claim_refusal(state, today) == expected


@pytest.mark.parametrize("user, status", [(None, 401), ({"id": "u-1", "is_admin": False}, 403)])
@pytest.mark.asyncio
async def test_refresh_auth_is_unchanged_and_runs_before_the_claim(monkeypatch, user, status):
    ledger = _Ledger().install(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        await admin.refresh_industry_dossier(x_admin_token=None, user=user)
    assert ei.value.status_code == status
    assert ledger.claims == []


def test_every_refusal_reason_has_operator_copy():
    reasons = {"held", "already_ran_today", "disabled", "claim_failed", "ledger_unreadable"}
    assert set(admin._DOSSIER_REFUSAL_COPY) == reasons
    assert all(v.strip() for v in admin._DOSSIER_REFUSAL_COPY.values())


# ── No route may go back to a bare create_task ────────────────────────────────


def test_admin_routes_spawn_background_work_only_through_the_strong_ref_helper():
    """`asyncio.create_task` keeps a WEAK reference. Every call in admin.py must sit inside
    `_spawn_admin_task`, which holds the handle and logs how the task ended."""
    src = Path(admin.__file__).read_text()
    tree = ast.parse(src)
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"create_task", "ensure_future"}
                and fn.name != "_spawn_admin_task"
            ):
                offenders.append(f"{fn.name}:{node.lineno}")
    assert offenders == []
    # Mutation check of the guard itself: the helper really does call create_task.
    helper = next(
        f for f in ast.walk(tree)
        if isinstance(f, ast.FunctionDef) and f.name == "_spawn_admin_task"
    )
    assert any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "create_task"
        for n in ast.walk(helper)
    )
