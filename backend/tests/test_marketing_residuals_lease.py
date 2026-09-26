"""
Residual (g): OWNER_ALIVE_SECONDS must cover the REAL worst-case life of one generation task.

It was 3 × LEASE_SECONDS = 1896 s, while one generation can legitimately run far longer: every
ledger statement is bounded only by the PostgREST client timeout (120 s), a lease refresh
retries three times, the terminal write retries three times and re-reads, and — with the
semantic judge — a generation makes up to four model calls of 572 s each. At a cap, `_advance`
treats a live owner older than OWNER_ALIVE_SECONDS as wedged and closes the day
`writer_unavailable` under it; the live owner's terminal write is then SUPERSEDED, and a paid,
possibly publishable package is thrown away.

What is pinned here, none of it by trusting the constants:

* `generation_budget` is pure (no FMP, no Supabase client, no Gemini) and its statement bound is
  the timeout the LIVE service-role client actually carries;
* the REAL `_refresh_lease` and `_finish` loops, driven through every outcome sequence on a
  virtual clock where each statement runs to the timeout, never exceed their computed worst
  cases — and reach them (the bounds are tight, not guesses);
* a whole generation driven through the real `_generate` path takes exactly
  `worst_case_generation_seconds()` at its worst, which OWNER_ALIVE_SECONDS covers;
* MUST-PASS twin: at a cap, a live owner as old as a real worst-case generation (older than the
  old 1896-s bound) is left alone; MUST-REJECT twin: one past OWNER_ALIVE_SECONDS is closed.

Hermetic: an in-memory ledger, a recorded `asyncio.sleep` (patched on script_service's own
`asyncio` binding only), no network.
"""

from __future__ import annotations

import ast
import asyncio
import itertools
import json
import logging
import math
import re
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from app.config import Settings
from app.services.marketing import content_pool, selection
from app.services.marketing import generation_budget as gb
from app.services.marketing import run_service as mrs
from app.services.marketing import script_service as ss
from app.services.marketing.run_service import MarketingRunError

_BACKEND = Path(__file__).resolve().parents[1]
S = gb.LEDGER_STATEMENT_SECONDS
KEY = content_pool.eligible_keys()[0]
TEMPLATE = next(iter(selection.TEMPLATES_BY_ID))
RUN_DATE = date(2026, 9, 24)
_BASE = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)


# ── a virtual clock: statements and back-offs cost time, nothing sleeps ───────────────────────


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: List[float] = []

    def now(self) -> datetime:
        return _BASE + timedelta(seconds=self.t)


class _AsyncioWithVirtualSleep:
    """script_service's `asyncio`, with `sleep` charged to the clock. Patched on the module's
    own binding, so nothing else in the process sees it."""

    def __init__(self, clock: _Clock) -> None:
        self._clock = clock

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)

    async def sleep(self, seconds: float, result: Any = None) -> Any:
        self._clock.sleeps.append(float(seconds))
        self._clock.t += float(seconds)
        return result


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(ss, "_now", c.now)
    monkeypatch.setattr(ss, "asyncio", _AsyncioWithVirtualSleep(c))
    return c


# Statement outcomes. Every one but FAST runs to the PostgREST timeout.
RAISE = "raise"      # the statement timed out / errored
NONE = "none"        # it matched nothing
ROW = "row"          # it landed (slowly: after S seconds)
FAST = "fast"        # it landed at once


class _Ledger:
    """`MarketingRunService`'s script surface, in memory. Each statement advances the clock by
    its cost; refresh and terminal UPDATEs follow a scripted outcome list."""

    def __init__(self, clock: Optional[_Clock], row: Dict[str, Any], run: Dict[str, Any], *,
                 refresh: Optional[List[str]] = None, terminal: Optional[List[str]] = None) -> None:
        self.clock, self.row, self.run = clock, row, run
        self.refresh = list(refresh or [])
        self.terminal = list(terminal or [])
        self.log: List[str] = []

    def _cost(self, kind: str, outcome: str = ROW) -> None:
        self.log.append(kind)
        if self.clock is not None and outcome != FAST:
            self.clock.t += S

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        self._cost("get_run")
        return dict(self.run)

    async def get_script(self, run_id: str) -> Optional[Dict[str, Any]]:
        self._cost("get_script")
        return dict(self.row)

    def _matches(self, expect: Dict[str, Any]) -> bool:
        for col, val in expect.items():
            cur = self.row.get(col)
            if col == "lease_until":
                cur = ss._ts_key(cur)
            if val is None and cur is not None:
                return False
            if val is not None and cur != val:
                return False
        return True

    async def update_script_where(self, run_id: str, patch: Dict[str, Any], *,
                                  expect: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if set(patch) == {"lease_until"}:
            kind, outcome = "refresh", (self.refresh.pop(0) if self.refresh else FAST)
        elif "generation_id" in patch:
            kind, outcome = "acquire", ROW
        elif "generations" in expect:  # the cap-close CAS fences on the observed count
            kind, outcome = "finalize", ROW
        else:
            kind, outcome = "terminal", (self.terminal.pop(0) if self.terminal else ROW)
        self._cost(kind, outcome)
        if outcome == RAISE:
            raise MarketingRunError(f"update_script failed (run_id={run_id}): {kind} statement timed out")
        if outcome == NONE or not self._matches(expect):
            return None
        self.row.update(patch)
        return dict(self.row)


def _row(**fields: Any) -> Dict[str, Any]:
    return {"run_id": "run-1", "status": ss.SELECTED, "source_ref": KEY, "template_id": TEMPLATE,
            "generations": 0, "content_rejections": 0, "violations": [], "fact_sheet": {},
            "tokens_used": 0, "generation_id": None, "lease_until": None, "retry_not_before": None,
            **fields}


_NONCE = "0f" * 16
#: The caller-claim fence (rules marketing.md §2): every kick names the claim it holds.
_CLAIM = mrs.CallerClaim(1, _NONCE)


def _run(**fields: Any) -> Dict[str, Any]:
    return {"id": "run-1", "run_date": RUN_DATE.isoformat(), "status": "in_progress",
            "source_ref": KEY, "attempts": 1, "metadata": {"claim_nonce": _NONCE}, **fields}


# ── generation_budget: pure, and its statement bound is the live client's timeout ────────────


def test_generation_budget_is_pure():
    code = ("import json, sys\nimport app.services.marketing.generation_budget\n"
            "print(json.dumps(sorted(sys.modules)))")
    out = subprocess.run([sys.executable, "-c", code], cwd=_BACKEND, capture_output=True,
                         text=True, timeout=120, check=True)
    loaded = set(json.loads(out.stdout.strip().splitlines()[-1]))
    assert "app.services.marketing.generation_budget" in loaded  # anti-vacuity
    ours = sorted(m for m in loaded if m == "app" or m.startswith("app."))
    assert ours == ["app", "app.services", "app.services.marketing",
                    "app.services.marketing.generation_budget"], ours
    assert not [m for m in loaded if "fmp" in m.lower()]
    assert not [m for m in loaded if m.startswith(("supabase", "google", "gotrue"))]


def test_the_statement_bound_is_the_timeout_the_live_service_role_client_carries():
    """`app/database.py` builds the client with supabase-py's defaults and
    `_force_http1_on_postgrest` copies the timeout onto its HTTP/1.1 session. Build one the same
    way (no network: `create_client` does no I/O) and read what it will actually wait."""
    from postgrest.constants import DEFAULT_POSTGREST_CLIENT_TIMEOUT
    from supabase import create_client
    from supabase.lib.client_options import SyncClientOptions

    from app import database

    assert gb.LEDGER_STATEMENT_SECONDS == float(DEFAULT_POSTGREST_CLIENT_TIMEOUT)
    assert SyncClientOptions().postgrest_client_timeout == DEFAULT_POSTGREST_CLIENT_TIMEOUT
    dummy_key = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                 "eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2lnbmF0dXJl")
    client = create_client("https://abcdefghijklmnopqrst.supabase.co", dummy_key)
    try:
        database._force_http1_on_postgrest(client)
        timeout = client.postgrest.session.timeout
        phases = (timeout.connect, timeout.read, timeout.write, timeout.pool)
        assert all(p is not None and p <= gb.LEDGER_STATEMENT_SECONDS for p in phases), phases
        assert timeout.read == gb.LEDGER_STATEMENT_SECONDS  # the bound IS the timeout, not a guess
    finally:
        client.postgrest.session.close()


def test_nothing_in_app_overrides_the_postgrest_timeout():
    """A `postgrest_client_timeout=` anywhere, or options on the service-role `create_client`,
    would move the real bound away from LEDGER_STATEMENT_SECONDS without anything noticing."""
    hits = []
    for path in sorted((_BACKEND / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "postgrest_client_timeout":
                hits.append(f"{path.relative_to(_BACKEND)}:{node.value.lineno}")
    assert hits == []
    tree = ast.parse((_BACKEND / "app" / "database.py").read_text(encoding="utf-8"))
    get_sb = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "get_supabase")
    calls = [n for n in ast.walk(get_sb) if isinstance(n, ast.Call)
             and getattr(n.func, "id", getattr(n.func, "attr", "")) == "create_client"]
    assert len(calls) == 1
    assert sorted(k.arg for k in calls[0].keywords) == ["supabase_key", "supabase_url"]


# ── the per-step worst cases, driven through the REAL loops ────────────────────────────────────


def test_linear_backoff_total_edges():
    assert ss._linear_backoff_total(0, 1.0) == 0.0
    assert ss._linear_backoff_total(1, 1.0) == 0.0
    assert ss._linear_backoff_total(3, 0.5) == 1.5
    assert ss._linear_backoff_total(3, 1.0) == 3.0
    assert ss._linear_backoff_total(-2, 1.0) == 0.0


@pytest.mark.asyncio
async def test_the_real_refresh_loop_never_exceeds_its_worst_case_and_reaches_it(clock):
    """Every sequence of refresh outcomes the loop can consume. A slow landing (after S) leaves
    632 - 120 = 512 s < 572 s of lease, so the loop retries it like an error — the worst case is
    three full statements plus both back-offs."""
    observed = []
    # One outcome MORE than the loop may consume: a loop that grew an attempt would take it.
    for seq in itertools.product((RAISE, ROW, NONE, FAST), repeat=ss._REFRESH_ATTEMPTS + 1):
        clock.t, clock.sleeps = 0.0, []
        gen = str(uuid.uuid4())
        ledger = _Ledger(clock, _row(status=ss.GENERATING, generation_id=gen), _run(), refresh=list(seq))
        svc = ss.MarketingScriptService(ledger, writer=object())
        svc._leases[gen] = clock.now() + timedelta(seconds=ss.LEASE_SECONDS)
        try:
            await svc._refresh_lease("run-1", gen)
        except ss.LeaseLost:
            pass
        assert ledger.log.count("refresh") <= ss._REFRESH_ATTEMPTS
        observed.append((clock.t, seq))
    worst = max(t for t, _ in observed)
    assert all(t <= ss.worst_case_refresh_seconds() + 1e-9 for t, _ in observed)
    assert worst == pytest.approx(ss.worst_case_refresh_seconds())  # tight: the bound is reached
    if S == 120.0:
        assert worst == 361.5


@pytest.mark.asyncio
async def test_the_real_terminal_write_never_exceeds_its_worst_case_and_reaches_it(clock):
    """Every outcome sequence of `_finish`. The worst is two errors, then a retry that matches
    nothing — which re-reads the row to see whether the first write landed."""
    observed = []
    for seq in itertools.product((RAISE, NONE, ROW), repeat=ss._FINISH_ATTEMPTS + 1):
        clock.t, clock.sleeps = 0.0, []
        gen = str(uuid.uuid4())
        ledger = _Ledger(clock, _row(status=ss.GENERATING, generation_id=gen), _run(), terminal=list(seq))
        svc = ss.MarketingScriptService(ledger, writer=object())
        outcome = await svc._finish("run-1", gen, {"status": ss.SELECTED, "last_error": "x",
                                                    "content_rejections": 1})
        assert outcome in (ss.WRITTEN, ss.SUPERSEDED, ss.LOST)
        assert ledger.log.count("terminal") <= ss._FINISH_ATTEMPTS
        assert ledger.log.count("get_script") <= 1
        observed.append((clock.t, seq))
    worst = max(t for t, _ in observed)
    assert all(t <= ss.worst_case_terminal_write_seconds() + 1e-9 for t, _ in observed)
    assert worst == pytest.approx(ss.worst_case_terminal_write_seconds())
    if S == 120.0:
        assert worst == 483.0


# ── a whole generation on the real code path ───────────────────────────────────────────────


def _worst_writer(clock: _Clock, calls: List[str]):
    """Makes MODEL_CALLS_PER_GENERATION calls, each behind `before_call`, each at the worst
    case — and ignores a False from `before_call` (the contract allows the call when nothing
    publishable is in hand), so every refresh and every call happens."""

    async def writer(item, template, run_date, *, generation_id, allow_x_url, judge_mode, before_call):
        for _ in range(gb.MODEL_CALLS_PER_GENERATION):
            calls.append("before_call")
            await before_call()
            calls.append("model")
            clock.t += ss.worst_case_model_call_seconds()
        return SimpleNamespace(status="rejected", package=None, violations=[{"code": "judge_x"}],
                               tokens_used=0, model="m", prompt_version="p")

    return writer


@pytest.mark.asyncio
async def test_a_whole_worst_case_generation_fits_inside_owner_alive(clock, caplog):
    """The REAL `_generate`: acquire, the run-date read (the row carries none), four refreshes
    that each time out three times, four worst-case model calls, and a terminal write that times
    out twice then matches nothing and re-reads. Its virtual life is exactly
    `worst_case_generation_seconds()` — and the old OWNER_ALIVE_SECONDS (3 × the lease) did not
    cover it."""
    refreshes = [RAISE] * (ss._REFRESH_ATTEMPTS * gb.MODEL_CALLS_PER_GENERATION)
    terminal = [RAISE] * (ss._FINISH_ATTEMPTS - 1) + [NONE]
    ledger = _Ledger(clock, _row(), _run(), refresh=refreshes, terminal=terminal)
    calls: List[str] = []
    svc = ss.MarketingScriptService(ledger, writer=_worst_writer(clock, calls))
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc._generate("run-1")
    assert calls == ["before_call", "model"] * gb.MODEL_CALLS_PER_GENERATION
    assert ledger.log == (["get_script", "acquire", "get_run"]
                          + ["refresh"] * ss._REFRESH_ATTEMPTS * gb.MODEL_CALLS_PER_GENERATION
                          + ["terminal"] * ss._FINISH_ATTEMPTS + ["get_script"])
    assert ledger.refresh == [] and ledger.terminal == []  # every scripted outcome was consumed
    assert clock.t == pytest.approx(ss.worst_case_generation_seconds())
    assert clock.t + ss.LEASE_MARGIN_SECONDS <= ss.OWNER_ALIVE_SECONDS
    assert clock.t > 3 * ss.LEASE_SECONDS, "sentinel: the old bound really was too short"
    if S == 120.0 and ss.worst_case_model_call_seconds() == 572.0:
        assert clock.t == 4577.0 and ss.OWNER_ALIVE_SECONDS == 4637


# ── the twins: at a cap, the owner's age decides ───────────────────────────────────────────


def _at_the_cap(age_seconds: float):
    """The last allowed generation (failure cap) is running in THIS process with a lapsed
    lease, spawned `age_seconds` ago."""
    gen = str(uuid.uuid4())
    lapsed = datetime.now(timezone.utc) - timedelta(seconds=5)
    row = _row(status=ss.GENERATING, generation_id=gen, generations=ss.MAX_WRITER_FAILURES,
               lease_until=ss._iso(lapsed))
    assert ss._cap_verdict(row) == ss.REASON_WRITER_UNAVAILABLE, "sentinel: at the cap"
    ledger = _Ledger(None, row, _run())
    svc = ss.MarketingScriptService(ledger, writer=object())
    svc._running.add("run-1")
    svc._spawned_at["run-1"] = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return svc, ledger


@pytest.mark.parametrize("age", [
    3 * ss.LEASE_SECONDS + 1,                    # just past the OLD bound (1897 s)
    ss.worst_case_generation_seconds(),          # a live owner at the real worst case
    ss.OWNER_ALIVE_SECONDS - 5,
], ids=["past_old_bound", "real_worst_case", "just_inside"])
@pytest.mark.asyncio
async def test_must_pass_a_live_owner_at_the_cap_keeps_the_day(age, caplog):
    svc, ledger = _at_the_cap(age)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        state = await svc.kick("run-1", claim=_CLAIM)
    assert state["status"] == ss.GENERATING
    assert ledger.row["status"] == ss.GENERATING, "the live owner's package must still be writable"
    assert not [k for k in ledger.log if k not in ("get_run", "get_script")], ledger.log  # no write
    msgs = [r.getMessage() for r in caplog.records]
    assert any("owner is alive in this process" in m for m in msgs)
    assert not any("treating it as wedged" in m for m in msgs)


@pytest.mark.asyncio
async def test_must_reject_an_owner_past_owner_alive_at_the_cap_closes_the_day(caplog):
    svc, ledger = _at_the_cap(ss.OWNER_ALIVE_SECONDS + 1)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick("run-1", claim=_CLAIM)
    assert state["status"] == ss.REJECTED and state["reason"] == ss.REASON_WRITER_UNAVAILABLE
    assert ledger.row["status"] == ss.REJECTED
    assert ledger.log.count("finalize") == 1
    assert any("treating it as wedged" in r.getMessage() for r in caplog.records)


# ── the per-run bound, and the wedge verdict against the worker's cadence ──────────────────────


def test_the_per_run_model_call_bound_is_28():
    assert ss.MAX_MODEL_CALLS_PER_RUN == 28
    assert ss.MAX_MODEL_CALLS_PER_RUN == (
        (ss.MAX_GENERATIONS + ss.MAX_WRITER_FAILURES - 1) * gb.MODEL_CALLS_PER_GENERATION
    )


def _cron_period_seconds() -> int:
    toml = (_BACKEND / "marketing" / "railway.toml").read_text(encoding="utf-8")
    m = re.search(r'^cronSchedule\s*=\s*"([^"]+)"', toml, re.M)
    assert m, "the worker's cronSchedule is gone from marketing/railway.toml"
    minute, *rest = m.group(1).split()
    assert minute.isdigit() and rest == ["*"] * 4, f"not hourly any more: {m.group(1)!r}"
    return 3600


def test_the_wedge_verdict_still_lands_while_the_run_has_claim_attempts_left():
    """OWNER_ALIVE_SECONDS grew from 1896 s to ~77 min. A genuinely wedged owner at a cap is now
    closed that much later, so the verdict must still land within the run's claim attempts —
    a generation spawned on the first hourly tick is closed by a later tick of the same run."""
    period = _cron_period_seconds()
    attempts = Settings.model_fields["MARKETING_MAX_RUN_ATTEMPTS"].default
    ticks_to_verdict = math.ceil(ss.OWNER_ALIVE_SECONDS / period)
    assert 1 <= ticks_to_verdict < attempts, (ss.OWNER_ALIVE_SECONDS, attempts)
