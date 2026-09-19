"""F24-3: a laptop must not consume PRODUCTION's notification queue under "dry run".

`RUN_NOTIFICATION_JOBS_LOCALLY` spawned `notification_dispatch`, `notification_senders` and
`price_alerts` against the Supabase in backend/.env — production — with `PUSH_DRY_RUN`
forced on as the safety. Dry-run replaces only the APNs POST: `flush_deferred` still
claims real users' due quiet-hours rows and stamps them `dry_run` (terminal; Railway never
re-claims them), the senders take the once-per-ET-day `claimed_job` and mark it done, and
the price-alert loop deactivates every fired one-shot rule before its dry-run push. A
developer following the docstring at 16:05 ET silently ate the day's earnings
notifications for every production user.

The lifespan now refuses those three loops in local dev unless
`NOTIFICATION_JOBS_LOCALLY_DB_IS_NOT_PROD` asserts the database is not production. The
tests drive the REAL `lifespan` with the loops replaced by recorders — nothing here reaches
a network (the conftest guard would say so).
"""

from __future__ import annotations

import ast
import inspect
import logging
import re

import pytest

import app.main as main_mod
import app.services.price_alert_service as pas
import app.services.universe_data as ud
from app.config import settings

_LOOPS = ("notification_dispatch", "notification_senders", "price_alerts")


@pytest.fixture
def lifespan_harness(monkeypatch):
    """Stub every side effect of the local-dev lifespan branch; record which of the three
    notification loops were actually started."""
    started: list[str] = []

    async def _no_db():
        return False

    monkeypatch.setattr(main_mod, "check_supabase_health", _no_db)
    monkeypatch.setattr(ud, "verify_universe_files_present", lambda: {})

    def _recorder(name):
        # Records at coroutine CREATION — `_spawn` is what we are proving, and the
        # `async with` body below never yields, so a task would be cancelled before it
        # ever ran its first line.
        async def _inert():
            return None

        def _loop():
            started.append(name)
            return _inert()
        return _loop

    monkeypatch.setattr(main_mod, "_run_notification_dispatch_loop", _recorder("notification_dispatch"))
    monkeypatch.setattr(main_mod, "_run_scheduled_notification_senders", _recorder("notification_senders"))
    monkeypatch.setattr(pas, "run_price_alert_loop", _recorder("price_alerts"))
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "PUSH_DRY_RUN", False)
    monkeypatch.setattr(settings, "RUN_NOTIFICATION_JOBS_LOCALLY", False)
    monkeypatch.setattr(settings, "NOTIFICATION_JOBS_LOCALLY_DB_IS_NOT_PROD", False)
    return started


async def _run_lifespan():
    async with main_mod.lifespan(main_mod.app):
        pass


@pytest.mark.asyncio
async def test_local_opt_in_without_the_non_prod_assertion_spawns_nothing(lifespan_harness, caplog):
    settings.RUN_NOTIFICATION_JOBS_LOCALLY = True
    with caplog.at_level(logging.ERROR, logger="app.main"):
        await _run_lifespan()
    assert lifespan_harness == [], (
        f"the claim-bearing loops were started against production: {lifespan_harness}"
    )
    refusals = [r for r in caplog.records if "REFUSING to start the notification loops" in r.getMessage()]
    assert refusals and refusals[0].levelno == logging.ERROR, "the refusal must be loud"
    assert "NOTIFICATION_JOBS_LOCALLY_DB_IS_NOT_PROD" in refusals[0].getMessage()


@pytest.mark.asyncio
async def test_local_opt_in_with_the_assertion_spawns_all_three_and_forces_dry_run(lifespan_harness):
    settings.RUN_NOTIFICATION_JOBS_LOCALLY = True
    settings.NOTIFICATION_JOBS_LOCALLY_DB_IS_NOT_PROD = True
    settings.PUSH_DRY_RUN = True
    await _run_lifespan()
    assert sorted(lifespan_harness) == sorted(_LOOPS)
    assert settings.PUSH_DRY_RUN is True, "dry-run must still hold on a laptop"


@pytest.mark.asyncio
async def test_an_unset_push_dry_run_is_forced_on_while_an_explicit_false_is_honoured(
    lifespan_harness, monkeypatch,
):
    """`PUSH_DRY_RUN` is a bool whose default is False, so the old `is not False` test
    could not tell "unset" (the normal laptop state) from an explicit opt-out — and with
    the variable simply absent, dry-run was NOT forced: real users' phones from a developer
    machine, the exact outcome the comment says is prevented. `model_fields_set` names
    what a source actually provided; only an explicit `PUSH_DRY_RUN=false` opts back in."""
    settings.RUN_NOTIFICATION_JOBS_LOCALLY = True
    settings.NOTIFICATION_JOBS_LOCALLY_DB_IS_NOT_PROD = True
    # Unset: the field is at its default and NOT in model_fields_set.
    settings.PUSH_DRY_RUN = False
    # `model_fields_set` is a read-only view over `__pydantic_fields_set__`.
    monkeypatch.setattr(settings, "__pydantic_fields_set__",
                        set(settings.model_fields_set) - {"PUSH_DRY_RUN"})
    await _run_lifespan()
    assert settings.PUSH_DRY_RUN is True, "an unset PUSH_DRY_RUN must be forced on locally"
    # Explicit opt-out: provided by a source, and false.
    lifespan_harness.clear()
    settings.PUSH_DRY_RUN = False
    monkeypatch.setattr(settings, "__pydantic_fields_set__",
                        set(settings.model_fields_set) | {"PUSH_DRY_RUN"})
    await _run_lifespan()
    assert settings.PUSH_DRY_RUN is False, "an explicit PUSH_DRY_RUN=false is the deliberate opt-in"


@pytest.mark.asyncio
async def test_the_assertion_alone_does_not_opt_in(lifespan_harness):
    """Boundary: the flag asserts the target, it does not start anything by itself."""
    settings.NOTIFICATION_JOBS_LOCALLY_DB_IS_NOT_PROD = True
    await _run_lifespan()
    assert lifespan_harness == []


@pytest.mark.asyncio
async def test_local_dev_default_spawns_nothing(lifespan_harness):
    await _run_lifespan()
    assert lifespan_harness == []


@pytest.mark.asyncio
async def test_an_explicit_push_dry_run_false_is_still_refused_without_the_assertion(lifespan_harness):
    """The two knobs are independent: turning dry-run OFF on purpose (a real device test)
    must not sneak past the production-queue refusal."""
    settings.RUN_NOTIFICATION_JOBS_LOCALLY = True
    settings.PUSH_DRY_RUN = False
    await _run_lifespan()
    assert lifespan_harness == []


def _lifespan_code() -> str:
    src = inspect.getsource(main_mod.lifespan)
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())


def test_production_is_untouched_by_the_local_gate():
    """Railway must keep spawning the loops with no flag at all. The refusal lives INSIDE
    the `if is_local_dev:` branch and only ever narrows `run_notification_jobs`."""
    code = _lifespan_code()
    assert "run_notification_jobs = (not is_local_dev) or settings.RUN_NOTIFICATION_JOBS_LOCALLY" in code
    tree = ast.parse(inspect.cleandoc(inspect.getsource(main_mod.lifespan)))
    # Every reference to the assertion flag sits under an `if is_local_dev:` test.
    flag_nodes = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "NOTIFICATION_JOBS_LOCALLY_DB_IS_NOT_PROD"
    ]
    assert flag_nodes, "the non-prod assertion is not consulted"
    guarded = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "is_local_dev":
            guarded.extend(
                n for n in ast.walk(node)
                if isinstance(n, ast.Attribute) and n.attr == "NOTIFICATION_JOBS_LOCALLY_DB_IS_NOT_PROD"
            )
    assert len(guarded) == len(flag_nodes), "the refusal leaked outside the local-dev branch"
    assert "run_notification_jobs = False" in code


def test_the_docstring_no_longer_calls_dry_run_sufficient():
    """The old comment told developers to 'pair with PUSH_DRY_RUN=true' as the safety."""
    src = inspect.getsource(main_mod.lifespan)
    assert "Pair with PUSH_DRY_RUN=true to run the full pipeline" not in src
    assert "NOTIFICATION_JOBS_LOCALLY_DB_IS_NOT_PROD" in src


def test_the_flag_is_declared_fail_closed():
    assert settings.model_fields["NOTIFICATION_JOBS_LOCALLY_DB_IS_NOT_PROD"].default is False
