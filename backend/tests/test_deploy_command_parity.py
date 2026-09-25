"""The two ways the backend can be started must agree — and must stay single-worker.

`railway.toml` selects `builder = "dockerfile"`, so production runs `Dockerfile`'s CMD and
`Procfile` is never read. They were byte-equivalent by coincidence. The property that
actually matters is written nowhere else: every lifespan loop in `app/main.py` (close
snapshot, pre-warmers, sector jobs, reconciliation, expiry sweep) runs UNCLAIMED and is
safe only because exactly ONE uvicorn worker runs. A `--workers 4` added to either file
would double-run every job the day the other becomes the entrypoint.
"""

from __future__ import annotations

import re
from pathlib import Path

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
