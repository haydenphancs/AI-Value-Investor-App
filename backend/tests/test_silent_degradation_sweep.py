"""CLAUDE.md: "Never swallow silently … silent degradation is the hardest bug to find."

Four sites broke that rule in ways the 2026-09-12 pass confirmed (F00, F50, F15, G90):

* `GET /admin/industry-benchmarks-status` turned every read failure into `0` / `null` with
  no log — byte-identical to a wiped `sector_benchmarks` table, inviting an operator to
  re-trigger a 1-3 hour throttled FMP recompute against a table that was fine.
* `hydrate_whales` swallowed a failed `whale_profile_cache` invalidation, leaving the STALE
  assembled profile serving as though the hydration never ran.
* `_on_background_task_done` logged a deliberate one-shot as "exited without an error" at
  WARNING on every boot — training everyone to ignore the one line that matters when a
  real loop dies.
* `verify_api_key` compared against exactly ONE arbitrary key (so a second configured key
  never authenticated) and raised `StopIteration` on an empty set.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import app.main as main_mod
from app.core.security import verify_api_key


# ── F00 ─────────────────────────────────────────────────────────────────────────────


class _Boom:
    def table(self, _n):
        return self

    def __getattr__(self, _n):
        return lambda *a, **k: self

    def execute(self):
        raise RuntimeError("Error 520: ")


@pytest.mark.asyncio
async def test_an_unreadable_benchmark_count_is_unknown_not_zero(monkeypatch, caplog):
    import app.api.v1.endpoints.admin as admin

    monkeypatch.setattr("app.database.get_supabase", lambda: _Boom())
    with caplog.at_level(logging.WARNING, logger="app.api.v1.endpoints.admin"):
        out = await admin.industry_benchmarks_status(
            x_admin_token=None, user={"id": "u1", "is_admin": True}
        )
    assert out["total_rows"] is None and out["industry_rows"] is None, (
        "a failed count rendered as 0 — indistinguishable from an empty table"
    )
    assert out["latest_computed_at"] is None
    assert out["degraded"] is True
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("industry-benchmarks-status" in m and "RuntimeError" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_a_healthy_read_is_not_marked_degraded(monkeypatch):
    import app.api.v1.endpoints.admin as admin

    class _OK:
        def table(self, _n):
            return self

        def __getattr__(self, _n):
            return lambda *a, **k: self

        def execute(self):
            return SimpleNamespace(count=5704, data=[{"computed_at": "2026-09-12"}])

    monkeypatch.setattr("app.database.get_supabase", lambda: _OK())
    out = await admin.industry_benchmarks_status(
        x_admin_token=None, user={"id": "u1", "is_admin": True}
    )
    assert out["degraded"] is False and out["total_rows"] == 5704


# ── F50 ─────────────────────────────────────────────────────────────────────────────


def test_a_failed_profile_cache_invalidation_is_logged():
    import re
    from pathlib import Path

    src = Path("scripts/hydrate_whales.py").read_text(encoding="utf-8")
    i = src.index('sb.table("whale_profile_cache").delete()')
    window = src[i:i + 900]
    assert "pass  # Table may not exist yet" not in window
    assert "logger.warning(" in window and "whale_id" in window, (
        "a failed invalidation leaves the stale assembled profile serving, silently"
    )


# ── F15 ─────────────────────────────────────────────────────────────────────────────


def test_a_one_shot_task_is_not_reported_as_a_dead_loop():
    assert "run_whale_profile_pre_warmer" in main_mod._ONE_SHOT_TASKS
    import inspect
    src = inspect.getsource(main_mod)
    i = src.index("def _on_background_task_done")
    body = src[i:i + 2000]
    assert "_ONE_SHOT_TASKS" in body, (
        "every boot logged a deliberate one-shot as 'exited without an error' at WARNING, "
        "which is how a REAL loop death gets lost"
    )
    # …and a genuine loop returning normally must still WARN.
    assert 'logger.warning("Background task %r exited without an error"' in body


# ── G90 ─────────────────────────────────────────────────────────────────────────────


def test_every_configured_api_key_authenticates():
    keys = {"alpha-key", "beta-key", "gamma-key"}
    for k in keys:
        assert verify_api_key(k, keys) is True, (
            f"{k!r} did not authenticate — only one arbitrary member of the set was compared"
        )


def test_an_unknown_key_is_rejected():
    assert verify_api_key("nope", {"alpha-key", "beta-key"}) is False


@pytest.mark.parametrize("api_key,keys", [
    ("", {"alpha"}), ("alpha", set()), ("", set()), (None, {"alpha"}),
])
def test_empty_inputs_return_false_instead_of_raising(api_key, keys):
    """`next(iter(set()))` raised StopIteration, which inside an async frame surfaces as an
    unrelated RuntimeError rather than 'no keys configured'."""
    assert verify_api_key(api_key, keys) is False
