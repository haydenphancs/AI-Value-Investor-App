"""The cyclic collector must stay off for the whole test session.

See the rationale block in `backend/conftest.py`. Without it, any of the 26 `ast.parse`-based
source scans can fail at random with `SystemError: AST constructor recursion depth mismatch`.
That failure is intermittent (~1 run in 3) and lands on a DIFFERENT test each time, so it reads
as a real defect in whatever it hits. This test makes the mitigation itself visible: if someone
removes `gc.disable()`, this fails deterministically instead of the suite going flaky.
"""

import gc
from pathlib import Path


def test_gc_is_disabled():
    assert not gc.isenabled(), (
        "the cyclic collector is enabled during tests. backend/conftest.py disables it to stop "
        "GC from reentering CPython's AST recursion counter mid-`ast.parse`, which fails a "
        "random source-scan test with `SystemError: AST constructor recursion depth mismatch`. "
        "Re-enabling it brings back an intermittent, misattributed failure."
    )


def test_the_conftest_still_carries_the_reason():
    """A bare `gc.disable()` with no explanation invites a well-meaning revert."""
    conftest = (Path(__file__).resolve().parents[1] / "conftest.py").read_text(encoding="utf-8")
    assert "gc.disable()" in conftest, "conftest no longer disables gc"
    assert "AST constructor recursion depth mismatch" in conftest, (
        "the conftest disables gc without naming the failure it prevents — restore the rationale"
    )
