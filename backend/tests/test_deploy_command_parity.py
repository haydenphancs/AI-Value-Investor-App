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
