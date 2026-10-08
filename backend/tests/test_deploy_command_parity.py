"""The two ways the backend can be started must agree — and must stay single-worker.

`railway.toml` selects `builder = "dockerfile"`, so production runs `Dockerfile`'s CMD and
`Procfile` is never read. They were byte-equivalent by coincidence. The property that
actually matters is written nowhere else: every lifespan loop in `app/main.py` (close
snapshot, pre-warmers, sector jobs, reconciliation, expiry sweep) runs UNCLAIMED and is
safe only because exactly ONE uvicorn worker runs. A `--workers 4` added to either file
would double-run every job the day the other becomes the entrypoint.

The command text is only half of it. With no `--workers` flag uvicorn takes its worker count
from the environment — UVICORN_WORKERS first (its click CLI reads every `UVICORN_<OPTION>`, and
that explicit count wins), else WEB_CONCURRENCY — which lives in Railway's dashboard where no
command test can see it; and a Railway replica count above 1 runs a second copy of the whole
process. Either splits the in-process state the marketing /go counter keeps (its limiter,
ceilings, pending counts and the publisher's early-window clock) and double-runs every loop.
So: uvicorn's own reading of both variables is pinned through its CLI, `app/main.py` logs
"STARTUP: <the variable that decided>=…" at ERROR at boot when it asks for more than one worker,
and no deploy file may set either variable above 1 or `numReplicas` above 1.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import logging
import os
import re
import shlex
import sys
import textwrap
import tomllib
from pathlib import Path
from typing import Any, List

import pytest

_BACKEND = Path(__file__).resolve().parents[1]


def _procfile_cmd() -> str:
    lines = [l for l in (_BACKEND / "Procfile").read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert len(lines) == 1 and lines[0].startswith("web: "), lines
    return lines[0][len("web: "):].strip()


def _dockerfile_cmd() -> str:
    src = (_BACKEND / "Dockerfile").read_text()
    m = re.search(r'^CMD \["sh", "-c", "(.*)"\]\s*$', src, re.M)
    assert m, "Dockerfile CMD is not the sh -c form this test expects"
    return m.group(1)


def test_railway_builds_from_the_dockerfile():
    toml = (_BACKEND / "railway.toml").read_text()
    assert re.search(r'^builder\s*=\s*"dockerfile"', toml, re.M), "Procfile would become the entrypoint"


def test_procfile_and_dockerfile_start_the_same_server():
    proc, dock = _procfile_cmd(), _dockerfile_cmd()
    assert dock.replace("${PORT:-8000}", "$PORT") == proc, (proc, dock)


def test_single_worker_is_pinned():
    for cmd in (_procfile_cmd(), _dockerfile_cmd()):
        assert "--workers" not in cmd and "gunicorn" not in cmd, (
            f"{cmd!r}: the lifespan jobs are unclaimed — more than one worker double-runs them"
        )
        assert cmd.startswith("uvicorn app.main:app")


def test_the_graceful_shutdown_is_bounded():
    """uvicorn drains open connections BEFORE the lifespan shutdown, with no bound by
    default: one live SSE chat stream kept the old instance alive until Railway SIGKILLed
    it, and the lifespan `finally` that releases the durable job claims never ran (the
    claim then sat parked for its stale window — W2 B-1). Both entrypoints bound it."""
    for cmd in (_procfile_cmd(), _dockerfile_cmd()):
        m = re.search(r"--timeout-graceful-shutdown (\d+)", cmd)
        assert m, f"{cmd!r}: no graceful-shutdown bound"
        assert 5 <= int(m.group(1)) <= 60, "must beat Railway's stop grace while draining a normal request"


def test_the_repo_root_railway_toml_cannot_start_a_different_server():
    """A second `railway.toml` sits at the REPO root. Railway resolves a config file from
    the repo root unless the service sets a custom path, so if it is ever the one applied
    its `startCommand` REPLACES the Dockerfile CMD. It carried a stale command without the
    graceful-shutdown bound (and a `/health` check that skips the WeasyPrint probe). It must
    start exactly what the Dockerfile starts."""
    root = _BACKEND.parent / "railway.toml"
    if not root.exists():
        return
    toml = root.read_text()
    m = re.search(r'^startCommand\s*=\s*"(.*)"\s*$', toml, re.M)
    if m:
        assert m.group(1) == _dockerfile_cmd().replace("${PORT:-8000}", "$PORT"), m.group(1)
    hc = re.search(r'^healthcheckPath\s*=\s*"(.*)"\s*$', toml, re.M)
    backend_hc = re.search(r'^healthcheckPath\s*=\s*"(.*)"\s*$', (_BACKEND / "railway.toml").read_text(), re.M)
    if hc:
        assert hc.group(1) == backend_hc.group(1), (hc.group(1), backend_hc.group(1))
    assert "--workers" not in toml


def _healthcheck_timeout(toml_path: Path) -> int:
    m = re.search(r'^healthcheckTimeout\s*=\s*(\d+)\s*$', toml_path.read_text(), re.M)
    assert m, f"{toml_path}: no healthcheckTimeout — Railway's default would apply"
    return int(m.group(1))


def test_the_home_warm_gate_can_never_fail_a_deploy():
    """`/health/pdf` also answers 503 "warming" while the Home boot warm runs, for at most
    HOME_BOOT_WARM_MAX_WAIT_SECONDS (the route enforces that deadline itself). Railway fails
    the deploy if the healthcheck has not passed within healthcheckTimeout, so the gate must
    sit far inside it — 3x headroom covers the boot before the lifespan starts the clock and
    the render after it. Checked against BOTH railway.toml files: either may be the one
    applied (see the repo-root test above)."""
    from app.config import settings

    wait = settings.HOME_BOOT_WARM_MAX_WAIT_SECONDS
    assert isinstance(wait, int) and wait >= 0, wait
    files = [_BACKEND / "railway.toml"]
    root = _BACKEND.parent / "railway.toml"
    if root.exists():
        files.append(root)
    for toml_path in files:
        timeout = _healthcheck_timeout(toml_path)
        assert wait * 3 <= timeout, (
            f"{toml_path}: HOME_BOOT_WARM_MAX_WAIT_SECONDS={wait} leaves too little of "
            f"healthcheckTimeout={timeout} — a slow warm would FAIL the deploy"
        )


# ── the environment half: WEB_CONCURRENCY and replicas ────────────────────────


def _clear_uvicorn_env(monkeypatch) -> None:
    """uvicorn's CLI also reads `UVICORN_<OPTION>` variables (auto_envvar_prefix): none may leak in."""
    for key in [k for k in os.environ if k.startswith("UVICORN_")]:
        monkeypatch.delenv(key)


def _unset_web_concurrency(monkeypatch) -> None:
    if "WEB_CONCURRENCY" in os.environ:
        monkeypatch.delenv("WEB_CONCURRENCY")


def test_uvicorn_reads_web_concurrency_when_the_command_has_no_workers(monkeypatch):
    """The hole `test_single_worker_is_pinned` cannot see: it forbids `--workers` in the command
    text, but uvicorn (0.34: `Config.__init__`) takes the worker count from WEB_CONCURRENCY whenever
    no count was given. `log_config=None` keeps the Config from reconfiguring this process's logging."""
    import uvicorn

    _clear_uvicorn_env(monkeypatch)
    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    assert uvicorn.Config("app.main:app", log_config=None).workers == 3
    # Only an explicit count overrides the variable — and the command may not carry one.
    assert uvicorn.Config("app.main:app", log_config=None, workers=1).workers == 1
    monkeypatch.delenv("WEB_CONCURRENCY")
    assert uvicorn.Config("app.main:app", log_config=None).workers == 1


def _uvicorn_config_for(argv: List[str], monkeypatch) -> Any:
    """The `uvicorn.Config` uvicorn's OWN command line builds for `argv`, captured where `run()` hands
    it to the server — so nothing is bound, imported or served."""
    uvicorn_main = importlib.import_module("uvicorn.main")
    uvicorn_config = importlib.import_module("uvicorn.config")

    class _Captured(Exception):
        def __init__(self, config: Any) -> None:
            super().__init__("captured")
            self.config = config

    class _NoServer:
        def __init__(self, config: Any) -> None:
            raise _Captured(config)

    monkeypatch.setattr(uvicorn_main, "Server", _NoServer)
    # The CLI passes uvicorn's LOGGING_CONFIG, which `configure_logging` would dictConfig into THIS
    # process (and mutate); and `run()` puts `--app-dir` ("") at the front of sys.path.
    monkeypatch.setattr(uvicorn_config.Config, "configure_logging", lambda self: None)
    monkeypatch.setattr(sys, "path", list(sys.path))
    with pytest.raises(_Captured) as caught:
        uvicorn_main.main.main(args=list(argv), prog_name="uvicorn", standalone_mode=False)
    return caught.value.config


@pytest.mark.parametrize("entrypoint", ["Dockerfile", "Procfile"])
def test_the_production_command_takes_its_worker_count_from_web_concurrency(monkeypatch, entrypoint):
    """The exact production command, parsed by uvicorn's own CLI: with no `--workers` in it, the worker
    count is whatever WEB_CONCURRENCY says (1 when it is unset)."""
    cmd = (_dockerfile_cmd().replace("${PORT:-8000}", "8000") if entrypoint == "Dockerfile"
           else _procfile_cmd().replace("$PORT", "8000"))
    argv = shlex.split(cmd)
    assert argv[:2] == ["uvicorn", "app.main:app"], argv
    _clear_uvicorn_env(monkeypatch)

    monkeypatch.setenv("WEB_CONCURRENCY", "3")
    config = _uvicorn_config_for(argv[1:], monkeypatch)
    # The real command was parsed (sentinels), and the variable picked the worker count.
    assert config.app == "app.main:app" and config.port == 8000 and config.timeout_graceful_shutdown == 30
    assert config.workers == 3

    monkeypatch.delenv("WEB_CONCURRENCY")
    assert _uvicorn_config_for(argv[1:], monkeypatch).workers == 1


#: What the boot line must explain when the variable asks for more than one worker, and when it is junk.
_MORE_WORKERS_WHY = ("every lifespan loop runs unclaimed in each", "publish clock")
_JUNK_WHY = ("is not a number",)


@pytest.mark.parametrize("environ, named, why", [
    ({}, None, ()),
    ({"OTHER": "4"}, None, ()),
    ({"WEB_CONCURRENCY": ""}, None, ()),
    ({"WEB_CONCURRENCY": "   "}, None, ()),
    ({"WEB_CONCURRENCY": "1"}, None, ()),
    ({"WEB_CONCURRENCY": " 1 "}, None, ()),
    ({"WEB_CONCURRENCY": "0"}, None, ()),
    ({"WEB_CONCURRENCY": "-1"}, None, ()),                  # uvicorn runs one process for any count <= 1
    ({"WEB_CONCURRENCY": "2"}, "WEB_CONCURRENCY=2 ", _MORE_WORKERS_WHY),
    ({"WEB_CONCURRENCY": " 2 "}, "WEB_CONCURRENCY=2 ", _MORE_WORKERS_WHY),
    ({"WEB_CONCURRENCY": "+2"}, "WEB_CONCURRENCY=2 ", _MORE_WORKERS_WHY),
    ({"WEB_CONCURRENCY": "16"}, "WEB_CONCURRENCY=16 ", _MORE_WORKERS_WHY),
    ({"WEB_CONCURRENCY": "abc"}, "WEB_CONCURRENCY='abc' ", _JUNK_WHY),
    ({"WEB_CONCURRENCY": "2.5"}, "WEB_CONCURRENCY='2.5' ", _JUNK_WHY),
], ids=["no-environ", "other-variable", "empty", "blank", "1", "1-padded", "0", "minus-1", "2", "2-padded", "plus-2",
        "16", "abc", "2.5"])
def test_the_web_concurrency_problem_table(environ, named, why):
    from app.main import _web_concurrency_problem

    problem = _web_concurrency_problem(environ)
    if named is None:
        assert problem is None, problem
    else:
        assert isinstance(problem, str) and problem.startswith(named), problem
        assert "unset it on the Railway web service" in problem and "ONE uvicorn worker" in problem
        assert [w for w in why if w not in problem] == [], problem


def test_a_huge_junk_web_concurrency_is_named_in_a_short_line():
    """The value is someone's typo, echoed into a log line: capped, never the whole thing."""
    from app.main import _web_concurrency_problem

    problem = _web_concurrency_problem({"WEB_CONCURRENCY": "x" * 10_000})
    assert isinstance(problem, str) and len(problem) < 300, len(problem or "")
    assert problem.startswith("WEB_CONCURRENCY='xxxxxxxxxxxx' ") and "x" * 13 not in problem


def test_a_huge_numeric_web_concurrency_is_named_in_a_short_line_too():
    """The same cap for a typo made of digits: uvicorn would take it as a worker count, and the line that
    says so must still be one short line (Python parses up to 4300 digits by default)."""
    from app.main import _web_concurrency_problem

    problem = _web_concurrency_problem({"WEB_CONCURRENCY": "9" * 400})
    assert isinstance(problem, str) and problem.startswith("WEB_CONCURRENCY=")
    assert "unset it on the Railway web service" in problem
    assert len(problem) < 300 and "9" * 13 not in problem, len(problem)


def _cli_workers(environ: dict, monkeypatch) -> Any:
    """The worker count uvicorn's OWN command line starts the production command with when the
    environment holds exactly `environ` of the worker variables (every other `UVICORN_*` and
    WEB_CONCURRENCY cleared), or None when uvicorn refuses to start (a click BadParameter, or Config's
    ValueError on WEB_CONCURRENCY)."""
    import click

    _clear_uvicorn_env(monkeypatch)
    _unset_web_concurrency(monkeypatch)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    argv = shlex.split(_dockerfile_cmd().replace("${PORT:-8000}", "8000"))
    try:
        return _uvicorn_config_for(argv[1:], monkeypatch).workers
    except (click.ClickException, ValueError):
        return None


@pytest.mark.parametrize("variable", ["WEB_CONCURRENCY", "UVICORN_WORKERS"])
@pytest.mark.parametrize("value", ["0", "1", " 1 ", "\t1\n", "-1", "-7", "2", " 3 ", "+2", "16", "1_0",
                                   "٢", "abc", "2.5", "0x2", "1e3", "", "   "])
def test_the_boot_check_flags_exactly_what_makes_uvicorn_start_more_than_one_worker(monkeypatch, variable, value):
    """Agreement with uvicorn's own command line, value by value and for BOTH variables: an ERROR exactly
    when uvicorn would start more than one worker (`1_0` and an Arabic-Indic `٢` are integers to both),
    naming the variable. A value uvicorn refuses (it never starts, so the lifespan never runs) is still
    named, unless it is blank. Review 2026-10-07: the check used to read WEB_CONCURRENCY only, and
    `UVICORN_WORKERS=4` started four workers with no line at all."""
    from app.main import _web_concurrency_problem

    workers = _cli_workers({variable: value}, monkeypatch)
    problem = _web_concurrency_problem({variable: value})
    if workers is None:
        assert (problem is None) is (not value.strip()), (value, problem)
    else:
        assert (problem is not None) is (workers > 1), (value, workers, problem)
    if problem is not None:
        assert problem.startswith(f"{variable}="), problem


@pytest.mark.parametrize("environ, named", [
    ({"UVICORN_WORKERS": "3", "WEB_CONCURRENCY": "1"}, "UVICORN_WORKERS=3 "),
    ({"UVICORN_WORKERS": "1", "WEB_CONCURRENCY": "4"}, None),            # the CLI's explicit count wins
    ({"UVICORN_WORKERS": "0", "WEB_CONCURRENCY": "4"}, None),            # even 0 (Config: `workers or 1`)
    ({"UVICORN_WORKERS": "", "WEB_CONCURRENCY": "4"}, "WEB_CONCURRENCY=4 "),   # empty: click ignores it
    ({"UVICORN_WORKERS": "2", "WEB_CONCURRENCY": "abc"}, "UVICORN_WORKERS=2 "),
    ({"UVICORN_WORKERS": "1", "WEB_CONCURRENCY": "abc"}, None),          # WEB_CONCURRENCY is never parsed
], ids=["uvicorn-3", "uvicorn-1-wins", "uvicorn-0-wins", "uvicorn-empty", "uvicorn-2-junk-web", "uvicorn-1-junk-web"])
def test_uvicorn_workers_decides_before_web_concurrency(monkeypatch, environ, named):
    """Both variables at once: the check follows uvicorn's precedence and names the variable that
    decided, exactly as uvicorn's own CLI resolves the count."""
    from app.main import _web_concurrency_problem

    workers = _cli_workers(environ, monkeypatch)
    problem = _web_concurrency_problem(environ)
    assert workers is not None and (problem is not None) is (workers > 1), (environ, workers, problem)
    if named is None:
        assert problem is None, problem
    else:
        assert problem.startswith(named) and "unset it on the Railway web service" in problem, problem


def test_the_lifespan_checks_web_concurrency_at_boot_in_every_environment():
    """AST of `lifespan` (comments and docstrings cannot satisfy it): `problem =
    _web_concurrency_problem(os.environ)` is a TOP-LEVEL statement of the function — so it runs in local
    dev and in production alike — before the `yield`, and an `if problem:` after it logs exactly
    `logger.error("STARTUP: %s", problem)`."""
    import app.main as main_mod

    tree = ast.parse(textwrap.dedent(inspect.getsource(main_mod.lifespan)))
    fn = tree.body[0]
    assert isinstance(fn, ast.AsyncFunctionDef) and fn.name == "lifespan"
    body = fn.body
    yield_at = next(i for i, stmt in enumerate(body) if any(isinstance(n, ast.Yield) for n in ast.walk(stmt)))
    every_call = [ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Call)
                  and isinstance(n.func, ast.Name) and n.func.id == "_web_concurrency_problem"]
    assert every_call == ["_web_concurrency_problem(os.environ)"], every_call
    assigns = [(i, s) for i, s in enumerate(body) if isinstance(s, ast.Assign) and len(s.targets) == 1
               and isinstance(s.targets[0], ast.Name) and ast.unparse(s.value) == every_call[0]]
    assert len(assigns) == 1, "the check is not a top-level statement of the lifespan"
    at, assign = assigns[0]
    name = assign.targets[0].id
    assert at < yield_at, "the check runs at shutdown, not at boot"
    guards = [s for s in body[at + 1:yield_at] if isinstance(s, ast.If) and ast.unparse(s.test) == name]
    assert len(guards) == 1, f"no top-level `if {name}:` between the check and the yield"
    assert [ast.unparse(s) for s in guards[0].body] == [f"logger.error('STARTUP: %s', {name})"]
    assert guards[0].orelse == []


@pytest.mark.asyncio
@pytest.mark.parametrize("variable", ["WEB_CONCURRENCY", "UVICORN_WORKERS"])
@pytest.mark.parametrize("value, logged", [("2", True), ("8", True), ("abc", True), ("1", False), ("0", False),
                                           ("", False), (None, False)])
async def test_the_boot_logs_one_error_when_web_concurrency_asks_for_more_workers(monkeypatch, caplog, variable,
                                                                                  value, logged):
    """The REAL lifespan, booted and shut down in local-dev mode with every side effect stubbed (the
    harness of tests/test_lifespan_local_notification_jobs.py): one ERROR line at boot, non-fatal."""
    import app.main as main_mod
    import app.services.universe_data as ud

    async def _no_db() -> bool:
        return False

    monkeypatch.setattr(main_mod, "check_supabase_health", _no_db)
    monkeypatch.setattr(ud, "verify_universe_files_present", lambda: {})
    monkeypatch.setattr(main_mod.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(main_mod.settings, "RUN_NOTIFICATION_JOBS_LOCALLY", False)
    _clear_uvicorn_env(monkeypatch)
    _unset_web_concurrency(monkeypatch)
    if value is not None:
        monkeypatch.setenv(variable, value)
    entered = False
    with caplog.at_level(logging.INFO, logger="app.main"):
        async with main_mod.lifespan(main_mod.app):
            entered = True                      # the boot went on: the ERROR is not fatal
    assert entered
    errors = [r.getMessage() for r in caplog.records
              if r.name == "app.main" and r.levelno == logging.ERROR and "uvicorn worker" in r.getMessage()]
    if logged:
        assert len(errors) == 1 and errors[0].startswith(f"STARTUP: {variable}="), errors
        assert "unset it on the Railway web service" in errors[0]
    else:
        assert errors == []


#: Every file that can start or scale the WEB service. The repo-root railway.toml is the one Railway
#: would apply without a custom config path (see the test above); a railway.json would be read too.
_DEPLOY_FILES = ("backend/Dockerfile", "backend/Procfile", "backend/railway.toml", "railway.toml",
                 "backend/railway.json", "railway.json")
_REQUIRED_DEPLOY_FILES = ("backend/Dockerfile", "backend/Procfile", "backend/railway.toml")
#: Text a file must still hold once its comment lines are gone (the strip must not eat the file).
_SENTINELS = {"backend/Dockerfile": "CMD [", "backend/Procfile": "web: uvicorn", "backend/railway.toml": "builder"}


def _uncommented(text: str) -> str:
    """The text without its comment lines (Dockerfile, Procfile and TOML all comment with `#`)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _set_above_one(text: str, name: str) -> List[str]:
    """Every mention of `name` that does not provably set it to an integer <= 1 — `ENV NAME=1`,
    `ENV NAME 1`, `NAME=0 uvicorn …`, `numReplicas = 1`, `"numReplicas": 1`. A mention this cannot read
    (an expansion, a non-integer) is reported too: unreadable is not a pass. `unset NAME` is fine."""
    problems: List[str] = []
    for m in re.finditer(rf"\b{re.escape(name)}\b", text):
        if re.search(r"\bunset[ \t]+$", text[:m.start()]):
            continue
        value = re.match(r"""["']?[ \t]*(?:=|:|[ \t])[ \t]*["']?(\d+)["']?(?![\w.])""", text[m.end():])
        if value and int(value.group(1)) <= 1:
            continue
        problems.append(text[max(0, m.start() - 30):m.end() + 30].strip())
    return problems


def _key_values(doc: Any, key: str) -> List[Any]:
    """Every value stored under `key` anywhere in a parsed TOML / JSON document."""
    if isinstance(doc, dict):
        return [v for k, v in doc.items() if k == key] + [x for v in doc.values() for x in _key_values(v, key)]
    if isinstance(doc, list):
        return [x for v in doc for x in _key_values(v, key)]
    return []


def test_no_deploy_file_sets_web_concurrency_or_extra_replicas():
    repo = _BACKEND.parent
    for rel in _REQUIRED_DEPLOY_FILES:
        assert (repo / rel).exists(), rel
    scanned = []
    for rel in _DEPLOY_FILES:
        path = repo / rel
        if not path.exists():
            continue
        raw = path.read_text()
        text = _uncommented(raw)
        if rel in _SENTINELS:
            assert _SENTINELS[rel] in text, f"{rel}: the comment strip removed the file's content"
        for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "numReplicas"):
            assert _set_above_one(text, name) == [], f"{rel}: {name} {_set_above_one(text, name)}"
        if rel.endswith((".toml", ".json")):
            doc = tomllib.loads(raw) if rel.endswith(".toml") else json.loads(raw)
            for replicas in _key_values(doc, "numReplicas"):
                assert type(replicas) is int and replicas <= 1, f"{rel}: numReplicas = {replicas!r}"
            commands = [c for c in _key_values(doc, "startCommand") if isinstance(c, str)]
            for cmd in commands:
                for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
                    assert _set_above_one(cmd, name) == [], f"{rel}: startCommand {cmd!r}"
        scanned.append(rel)
    assert {"backend/Dockerfile", "backend/Procfile", "backend/railway.toml"} <= set(scanned)


def test_the_deploy_file_scan_catches_what_it_is_meant_to_catch():
    """Anti-vacuity on the scan itself: every way of asking for more than one worker or replica is
    reported; one, zero, an unset and a comment are not."""
    for text in ("ENV WEB_CONCURRENCY=4", "ENV WEB_CONCURRENCY 2", "ARG WEB_CONCURRENCY=3",
                 "web: WEB_CONCURRENCY=3 uvicorn app.main:app --host 0.0.0.0 --port $PORT",
                 'startCommand = "WEB_CONCURRENCY=2 uvicorn app.main:app"',
                 'CMD ["sh", "-c", "WEB_CONCURRENCY=10 uvicorn app.main:app"]',
                 "uvicorn app.main:app --workers ${WEB_CONCURRENCY:-4}", "ENV WEB_CONCURRENCY=1.5"):
        assert _set_above_one(_uncommented(text), "WEB_CONCURRENCY"), text
    for text in ("ENV WEB_CONCURRENCY=1", "ENV WEB_CONCURRENCY 0", "web: WEB_CONCURRENCY=1 uvicorn app.main:app",
                 "unset WEB_CONCURRENCY", "# keep WEB_CONCURRENCY=4 out of here", "   # WEB_CONCURRENCY=8",
                 "ENV MY_WEB_CONCURRENCY=4"):
        assert _set_above_one(_uncommented(text), "WEB_CONCURRENCY") == [], text
    for text in ("ENV UVICORN_WORKERS=4", "web: UVICORN_WORKERS=2 uvicorn app.main:app --port $PORT",
                 'startCommand = "UVICORN_WORKERS=3 uvicorn app.main:app"'):
        assert _set_above_one(_uncommented(text), "UVICORN_WORKERS"), text
    for text in ("ENV UVICORN_WORKERS=1", "unset UVICORN_WORKERS", "# UVICORN_WORKERS=4"):
        assert _set_above_one(_uncommented(text), "UVICORN_WORKERS") == [], text
    for text in ("[deploy]\nnumReplicas = 2", '{"deploy": {"numReplicas": 3}}',
                 '[deploy.multiRegionConfig."us-west2"]\nnumReplicas = 2'):
        assert _set_above_one(text, "numReplicas"), text
    assert _set_above_one("[deploy]\nnumReplicas = 1", "numReplicas") == []
    assert _key_values(tomllib.loads('[deploy.multiRegionConfig."us-west2"]\nnumReplicas = 3'), "numReplicas") == [3]
    assert _key_values(json.loads('{"deploy": {"numReplicas": 2, "startCommand": "x"}}'), "numReplicas") == [2]
