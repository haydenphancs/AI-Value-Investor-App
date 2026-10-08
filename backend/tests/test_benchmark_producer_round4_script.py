"""`scripts/recompute_industry_benchmarks.py`, review round 3 (2026-10-07).

  XC-5  `logging.basicConfig(level=INFO)` with no redaction: httpx logs every request URL at
        INFO, and `FMPClient` puts `apikey=<key>` in the query string, so a documented
        validation run printed the live FMP key on every call. `configure_logging` now
        silences httpx/httpcore below WARNING and scrubs every console line (traceback
        included) with `SecretRedactingFilter`, as app/main.py does.
  P3-4  The script ran a full production recompute with no claim, so it could overlap the
        quarterly chain, the weekly TTM job or an admin refresh. A writing run (no
        `--industry`, no `--dry-run`) now takes the same claim, releases it on every path and
        refuses — starting nothing — when it is held.

Hermetic: a fake FMP key, httpx's MockTransport (no socket), a fake claim ledger, a fake (and
once the real, round-2-harnessed) benchmark service.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import io
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest

import app.services.industry_benchmark_service as ibs
import app.services.notification_jobs as nj
from app import main as m
from app.api.v1.endpoints import admin
from app.log_redaction import SecretRedactingFilter
from scripts import recompute_industry_benchmarks as script
from tests.test_benchmark_producer_round2_2026_10_07 import _FMP, _svc, _universe

FAKE_KEY = "TESTKEY0123456789abcdefNOTREAL"
_URL = f"https://financialmodelingprep.com/stable/ratios-ttm?symbol=AAPL&apikey={FAKE_KEY}"


# ═══ XC-5 — the FMP key never reaches the console ═════════════════════════════════════════


@contextlib.contextmanager
def _bare_root():
    """An unconfigured root logger — as at the script's start — for the duration of the
    block, inside the test body (pytest attaches its own capture handlers per PHASE, after
    fixtures ran), restored with setLevel so the loggers' level caches are cleared too."""
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    saved = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    root.handlers = []
    try:
        yield root
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
        for name, level in saved.items():
            logging.getLogger(name).setLevel(level)


async def _real_httpx_request() -> None:
    """A real httpx client sending a request whose URL carries the key: httpx itself logs
    `HTTP Request: GET <url> "HTTP/1.1 200 OK"` at INFO (no socket: MockTransport)."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    async with httpx.AsyncClient(transport=transport) as client:
        await client.get(_URL)


@pytest.mark.asyncio
async def test_the_console_never_shows_the_key():
    stream = io.StringIO()
    with _bare_root():
        script.configure_logging(stream=stream)
        await _real_httpx_request()                                    # httpx's INFO line
        logging.getLogger("app.integrations.fmp").warning("FMP 503 for %s", _URL)
        try:
            raise httpx.ConnectError(f"GET {_URL} failed")
        except httpx.ConnectError:
            logging.getLogger("recompute_industry_benchmarks").exception("The recompute crashed")

    out = stream.getvalue()
    assert FAKE_KEY not in out
    assert "HTTP Request" not in out                                   # httpx below WARNING
    assert out.count("apikey=***") >= 2                                # still diagnosable
    assert "Traceback (most recent call last)" in out                  # the stack is kept
    assert "FMP 503 for https://financialmodelingprep.com/stable/ratios-ttm" in out


@pytest.mark.asyncio
async def test_a_handler_that_already_exists_is_scrubbed_too(caplog, monkeypatch):
    """basicConfig does nothing when the root already has a handler (here caplog's); the
    filter must still land on it. caplog records what the console would print."""
    monkeypatch.setattr(caplog.handler, "filters", [])                 # restored after
    caplog.set_level(logging.INFO)
    with _bare_root() as root:
        root.handlers = [caplog.handler]
        script.configure_logging()
        await _real_httpx_request()
        logging.getLogger("app.integrations.fmp").warning("FMP 503 for %s", _URL)
        script.configure_logging()                                      # idempotent
        assert sum(isinstance(f, SecretRedactingFilter) for f in caplog.handler.filters) == 1
    assert FAKE_KEY not in caplog.text and "apikey=***" in caplog.text
    assert not [r for r in caplog.records if r.name == "httpx"]


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _main_block(tree: ast.Module) -> ast.If:
    return next(n for n in tree.body if isinstance(n, ast.If)
                and isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name) and n.test.left.id == "__name__")


def test_configure_logging_filters_after_basic_config_and_main_calls_it_first():
    """Source half, brace-bound to the function and the `__main__` block (AST: comments
    are gone, so prose naming the fix cannot satisfy it)."""
    tree = ast.parse(Path(script.__file__).read_text(encoding="utf-8"))
    fn = _function(tree, "configure_logging")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    basic = min(c.lineno for c in calls
                if isinstance(c.func, ast.Attribute) and c.func.attr == "basicConfig")
    adds = [c for c in calls if isinstance(c.func, ast.Attribute) and c.func.attr == "addFilter"
            and c.args and isinstance(c.args[0], ast.Call)
            and getattr(c.args[0].func, "id", None) == "SecretRedactingFilter"]
    assert adds and basic < min(c.lineno for c in adds)
    loops = [n for n in ast.walk(fn) if isinstance(n, ast.For)
             and "getLogger().handlers" in ast.unparse(n.iter)]
    assert loops and any(c in list(ast.walk(loop)) for loop in loops for c in adds)
    quiet = ast.unparse(fn)
    assert "'httpx'" in quiet and "'httpcore'" in quiet and "logging.WARNING" in quiet

    block = _main_block(tree)
    configure = [n.lineno for n in ast.walk(block) if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "configure_logging"]
    runs = [n.lineno for n in ast.walk(block) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "run"]
    assert configure and runs and min(configure) < min(runs)
    # …and no second basicConfig in `__main__` that could run before it.
    assert not [n for n in ast.walk(block) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute) and n.func.attr == "basicConfig"]


# ═══ P3-4 — a writing run takes the job's claim ══════════════════════════════════════════


def _args(**overrides: Any) -> argparse.Namespace:
    base = dict(skip_recent_hours=24, sector=None, industry=None, dry_run=False, ttm=False,
                universe=None)
    base.update(overrides)
    return argparse.Namespace(**base)


class _Ledger:
    """`notification_jobs` claim / finish / state, in memory, logging the order of events."""

    def __init__(self, events: List[str], grant: bool = True, state: Optional[dict] = None):
        self.events = events
        self.grant = grant
        self.state = state
        self.claims: List[Dict[str, Any]] = []
        self.finishes: List[Dict[str, Any]] = []

    def claim(self, job, *, timezone_name="UTC", now=None, stale_seconds=None):
        self.events.append("claim")
        self.claims.append({"job": job, "now": now, "stale_seconds": stale_seconds})
        return self.grant

    def finish(self, job, *, success, items=0, error=None, timezone_name="UTC", now=None):
        self.events.append("finish")
        self.finishes.append({"job": job, "success": success, "items": items, "error": error,
                              "now": now})

    def read_state(self, job):
        return self.state


class _Service:
    def __init__(self, events: List[str], outcome: Any) -> None:
        self.events = events
        self.outcome = outcome
        self.calls: List[tuple] = []

    async def _run(self, mode: str, kw: Dict[str, Any]) -> Any:
        self.events.append("run")
        self.calls.append((mode, kw))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def recompute_all(self, **kw):
        return await self._run("fiscal", kw)

    async def recompute_all_ttm(self, **kw):
        return await self._run("ttm", kw)


@pytest.fixture
def wired(monkeypatch):
    """(install, events): `install(outcome, grant=True, state=None)` wires a fake ledger and
    service into the script and returns (ledger, service)."""
    events: List[str] = []
    monkeypatch.delenv("UNIVERSE_DATA_DIR", raising=False)

    def install(outcome: Any = None, *, grant: bool = True, state: Optional[dict] = None):
        ledger = _Ledger(events, grant=grant, state=state)
        monkeypatch.setattr(nj, "claim_scheduled", ledger.claim)
        monkeypatch.setattr(nj, "finish_scheduled", ledger.finish)
        monkeypatch.setattr(nj, "scheduled_job_state", ledger.read_state)
        service = _Service(events, {"rows_upserted": 377} if outcome is None else outcome)
        monkeypatch.setattr(script, "get_industry_benchmark_service", lambda: service)
        return ledger, service

    return install, events


@pytest.mark.asyncio
@pytest.mark.parametrize("ttm, job", [(False, m.JOB_INDUSTRY_BENCHMARK_QUARTERLY),
                                      (True, m.JOB_TTM_BENCHMARK_WEEKLY)])
async def test_a_full_sweep_runs_under_the_scheduled_jobs_claim(wired, ttm, job):
    install, events = wired
    ledger, service = install()
    assert await script.main(_args(ttm=ttm, skip_recent_hours=0)) == script.EXIT_OK
    assert events == ["claim", "run", "finish"]                       # claimed BEFORE it ran
    (claim,) = ledger.claims
    assert claim["job"] == job and claim["stale_seconds"] == m._CHAIN_PHASE_STALE_SECONDS
    (finish,) = ledger.finishes
    assert finish == {"job": job, "success": True, "items": 377, "error": None,
                      "now": claim["now"]}                            # stamped with the claim
    assert service.calls[0][0] == ("ttm" if ttm else "fiscal")
    assert service.calls[0][1]["sectors"] is None and service.calls[0][1]["skip_if_fresh_hours"] is None


@pytest.mark.asyncio
async def test_a_sector_run_holds_the_claim_but_never_settles_the_day(wired, caplog):
    install, events = wired
    ledger, service = install()
    with caplog.at_level(logging.INFO, logger=script.logger.name):
        assert await script.main(_args(sector="Technology")) == script.EXIT_OK
    assert events == ["claim", "run", "finish"]
    (finish,) = ledger.finishes
    assert finish["success"] is False and finish["items"] == 377
    assert "--sector Technology" in finish["error"] and "not a full sweep" in finish["error"]
    assert service.calls[0][1]["sectors"] == ["Technology"]


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    {"industry": ["Asset Management"]},                 # validation path: industry rows only
    {"dry_run": True},                                  # writes nothing
    {"dry_run": True, "ttm": True},
    {"industry": ["Banks - Regional"], "ttm": True},
])
async def test_validation_runs_take_no_claim(wired, overrides):
    install, events = wired
    ledger, service = install()
    assert await script.main(_args(**overrides)) == script.EXIT_OK
    assert events == ["run"] and ledger.claims == [] and ledger.finishes == []


_TODAY = datetime.now(timezone.utc).date().isoformat()


@pytest.mark.asyncio
@pytest.mark.parametrize("state, reason", [
    ({"claim_at": "2026-10-08T04:00:00+00:00", "run_day": "2026-07-05", "enabled": True}, "held"),
    ({"claim_at": None, "run_day": _TODAY, "enabled": True}, "already_ran_today"),
    ({"claim_at": None, "run_day": None, "enabled": False}, "disabled"),
    (None, "ledger_unreadable"),
    ({"claim_at": None, "run_day": "2026-07-05", "enabled": True}, "claim_failed"),
])
async def test_a_refused_claim_starts_nothing_and_says_why(wired, caplog, state, reason):
    install, events = wired
    ledger, service = install(grant=False, state=state)
    with caplog.at_level(logging.ERROR, logger=script.logger.name):
        assert await script.main(_args(skip_recent_hours=0)) == script.EXIT_CLAIM_REFUSED
    assert events == ["claim"] and service.calls == [] and ledger.finishes == []
    (line,) = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert f"REFUSED ({reason})" in line and m.JOB_INDUSTRY_BENCHMARK_QUARTERLY in line
    assert script._REFUSAL_COPY[reason] in line


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [
    ibs.IndustryBenchmarkRecomputeIncomplete("fiscal", ["Energy"], {"rows_upserted": 9}),
    ibs.IndustryBenchmarkRecomputeIncomplete("fiscal", [], {"rows_upserted": 9},
                                             lossy_sectors=["Technology"]),
    ibs.IndustryBenchmarkRecomputeSkipped("nothing written", "FMP refused the whole run"),
])
async def test_an_incomplete_run_releases_the_claim_unsettled(wired, caplog, exc):
    install, events = wired
    ledger, _ = install(exc)
    with caplog.at_level(logging.WARNING, logger=script.logger.name):
        assert await script.main(_args()) == script.EXIT_INCOMPLETE
    assert events == ["claim", "run", "finish"]
    (finish,) = ledger.finishes
    assert finish["success"] is False and finish["error"] == str(exc)
    assert any("released UNSETTLED" in r.getMessage() and exc.reason in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_a_crash_releases_the_claim_and_never_records_the_key(wired, caplog):
    install, events = wired
    ledger, _ = install(httpx.ConnectError(f"GET {_URL} failed"))
    with caplog.at_level(logging.ERROR, logger=script.logger.name):
        assert await script.main(_args()) == script.EXIT_FAILED
    assert events == ["claim", "run", "finish"]
    (finish,) = ledger.finishes
    assert finish["success"] is False and finish["error"].startswith("ConnectError: GET ")
    assert FAKE_KEY not in finish["error"] and "apikey=***" in finish["error"]
    (crash,) = [r for r in caplog.records if r.getMessage() == "The recompute crashed"]
    assert crash.exc_info is not None                                 # logged WITH the stack


@pytest.mark.asyncio
async def test_an_interrupted_run_still_releases_the_claim(wired):
    install, events = wired
    ledger, _ = install(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await script.main(_args())
    (finish,) = ledger.finishes
    assert finish["success"] is False and finish["error"] == "interrupted (cancelled)"


@pytest.mark.asyncio
async def test_a_run_that_returns_no_summary_does_not_settle(wired):
    install, _ = wired
    ledger, _ = install(["not", "a", "summary"])
    assert await script.main(_args()) == script.EXIT_FAILED
    (finish,) = ledger.finishes
    assert finish["success"] is False and "returned list, not a summary" in finish["error"]


@pytest.mark.asyncio
async def test_the_real_sweep_under_the_claim(monkeypatch, wired):
    """The real `recompute_all` (round-2 harness): a complete sweep settles the day with its
    row count; a sector lost to an outage (P3-1) leaves it open with the service's reason."""
    install, _ = wired
    ledger, _ = install()
    groups = {"Technology": {"Software - Application": [f"S{i}" for i in range(5)],
                             "Computer Hardware": [f"H{i}" for i in range(5)]},
              "Energy": {"Oil & Gas E&P": [f"E{i}" for i in range(5)]}}
    _svc(monkeypatch, _FMP(), _universe(groups))   # installs the service singleton
    monkeypatch.setattr(script, "get_industry_benchmark_service", ibs.get_industry_benchmark_service)
    assert await script.main(_args(skip_recent_hours=0)) == script.EXIT_OK
    (settled,) = ledger.finishes
    assert settled["success"] is True and settled["items"] > 0

    _svc(monkeypatch, _FMP(down={f"S{i}" for i in range(5)}), _universe(groups))
    assert await script.main(_args(skip_recent_hours=0)) == script.EXIT_INCOMPLETE
    assert ledger.finishes[-1]["success"] is False
    assert "lost too many companies to FMP fetch failures: Technology" in ledger.finishes[-1]["error"]


# ═══ Pins: the script's copies agree with app.main / the admin route ══════════════════════


def test_the_scripts_job_keys_and_stale_window_are_mains():
    assert script.JOB_FISCAL == m.JOB_INDUSTRY_BENCHMARK_QUARTERLY
    assert script.JOB_TTM == m.JOB_TTM_BENCHMARK_WEEKLY
    assert script.CLAIM_STALE_SECONDS == m._CHAIN_PHASE_STALE_SECONDS


@pytest.mark.parametrize("state", [
    None,
    {"enabled": False, "run_day": _TODAY, "claim_at": "x"},
    {"enabled": True, "run_day": _TODAY, "claim_at": "x"},
    {"enabled": True, "run_day": "2026-01-04", "claim_at": "2026-10-08T04:00:00+00:00"},
    {"enabled": True, "run_day": None, "claim_at": None},
    {"run_day": f"{_TODAY}T00:00:00+00:00"},
    {},
])
def test_the_refusal_reasons_are_the_admin_routes(state):
    assert script.claim_refusal_reason(state, _TODAY) == admin._dossier_claim_refusal(state, _TODAY)
    assert script.claim_refusal_reason(state, _TODAY) in script._REFUSAL_COPY
