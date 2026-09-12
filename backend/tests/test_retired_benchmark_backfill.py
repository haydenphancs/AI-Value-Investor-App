"""`compute_all_benchmarks` is the sector-median corruption path (55 hardcoded tickers over
~5,700-company rows); `app/main.py` says it "must stay unscheduled" and the admin route was
re-pointed away from it. Nothing under `scripts/` may import it, and the retired backfill
script must refuse to run rather than vanish into "command not found"."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_SCRIPTS = _BACKEND / "scripts"


def _imports_or_calls(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == name for a in node.names):
            return True
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
        if isinstance(node, ast.Name) and node.id == name:
            return True
    return False


def test_no_script_reaches_compute_all_benchmarks():
    offenders = [p.name for p in sorted(_SCRIPTS.glob("*.py")) if _imports_or_calls(p, "compute_all_benchmarks")]
    assert not offenders, f"scripts still driving the corruption path: {offenders}"
    # Anti-vacuity: the detector sees an import, an attribute call and a bare call.
    import tempfile
    for snippet in (
        "from app.services.sector_benchmark_service import compute_all_benchmarks\n",
        "await service.compute_all_benchmarks(force=True)\n",
        "compute_all_benchmarks()\n",
    ):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write("async def f(service):\n    " + snippet if snippet.startswith("await") else snippet)
        assert _imports_or_calls(Path(fh.name), "compute_all_benchmarks"), snippet
    assert len(list(_SCRIPTS.glob("*.py"))) > 10, "the scripts glob found almost nothing"


def test_the_retired_backfill_script_refuses_to_run():
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.backfill_sector_benchmarks"],
        cwd=_BACKEND, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 2, proc.stderr
    assert "retired" in proc.stderr and "refresh-sector-benchmarks" in proc.stderr
