"""The probe must be able to FAIL. It always returned 0, so "run it before submitting"
could pass while the report said anything at all.

Three checks, and they are not interchangeable:

  * **CONTROL lapse (exit 2).** `funds/disclosure`, `earning-call-transcript` and
    `esg-disclosures` belong to no purchased package. If one answers 200, FMP's enforcement
    has lapsed — and then EVERY row in the run is uninformative, because a 200 no longer
    proves entitlement. Note a naive "count the blocked paths" check reads a lapse as an
    IMPROVEMENT (the count goes down), which is exactly backwards.
  * **Newly blocked (exit 1).** A feature just died.
  * **No longer blocked (exit 1).** Diffed, not counted: swapping one blocked path for
    another leaves the count identical.

Offline — `_gate_verdict` is pure, so this never touches FMP.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "_fmp_probe",
    Path(__file__).resolve().parents[1] / "scripts" / "fmp_entitlement_probe.py",
)
probe = importlib.util.module_from_spec(_SPEC)
sys.modules["_fmp_probe"] = probe
_SPEC.loader.exec_module(probe)


@pytest.fixture
def baseline(monkeypatch):
    rows = [
        ("CONTROL", "funds/disclosure VTSAX", 402),
        ("CONTROL", "earning-call-transcript AAPL", 402),
        ("CONTROL", "esg-disclosures AAPL", 402),
        ("QUOTE", "quote AAPL", 402),
        ("PHASE3", "grades AAPL", 402),
    ]
    monkeypatch.setattr(probe, "_baseline", lambda: rows, raising=True)
    return rows


def test_an_exact_match_passes(baseline, capsys):
    assert probe._gate_verdict(list(baseline)) == 0
    assert "GATE PASSED" in capsys.readouterr().out


def test_a_lapsed_control_exits_2_and_says_the_run_is_uninformative(baseline, capsys):
    """The most important case, and the one a count-based check gets backwards."""
    live = [r for r in baseline if r[1] != "esg-disclosures AAPL"]
    assert probe._gate_verdict(live) == 2
    out = capsys.readouterr().out
    assert "ENFORCEMENT HAS LAPSED" in out
    assert "esg-disclosures AAPL" in out
    assert "uninformative" in out


def test_all_three_controls_lapsing_is_still_exit_2(baseline):
    live = [r for r in baseline if r[0] != "CONTROL"]
    assert probe._gate_verdict(live) == 2


def test_a_newly_blocked_path_exits_1(baseline, capsys):
    live = list(baseline) + [("PHASE3", "analyst-estimates AAPL", 402)]
    assert probe._gate_verdict(live) == 1
    out = capsys.readouterr().out
    assert "NEWLY BLOCKED" in out and "analyst-estimates" in out


def test_a_path_that_stops_being_blocked_exits_1(baseline, capsys):
    live = [r for r in baseline if r[1] != "grades AAPL"]
    assert probe._gate_verdict(live) == 1
    out = capsys.readouterr().out
    assert "NO LONGER BLOCKED" in out and "grades" in out


def test_a_swap_is_caught_even_though_the_count_is_identical(baseline, capsys):
    """Why this diffs instead of counting."""
    live = [r for r in baseline if r[1] != "grades AAPL"]
    live.append(("PHASE3", "analyst-estimates AAPL", 402))
    assert len(live) == len(baseline)
    assert probe._gate_verdict(live) == 1
    out = capsys.readouterr().out
    assert "NEWLY BLOCKED" in out and "NO LONGER BLOCKED" in out


def test_the_control_check_runs_before_the_diff(baseline, capsys):
    """A lapse must report AS a lapse (exit 2), not as ordinary drift (exit 1) — the
    remediation is completely different: re-run on another date vs update the manifest."""
    live = [r for r in baseline if r[1] != "esg-disclosures AAPL"]
    live.append(("PHASE3", "analyst-estimates AAPL", 402))   # drift as well
    assert probe._gate_verdict(live) == 2
    assert "ENFORCEMENT HAS LAPSED" in capsys.readouterr().out


# ── the committed baseline itself ────────────────────────────────────────────

def test_the_committed_baseline_exists_and_holds_the_19_blocked_paths():
    """The number the handoff writes down as the pass condition, now encoded rather than
    prose. Measured live 2026-09-10: 19 blocked, 0 empty/error."""
    rows = probe._baseline()
    assert len(rows) == 19, f"expected 19 blocked paths, baseline has {len(rows)}"
    controls = [r for r in rows if r[0] == "CONTROL"]
    assert len(controls) == 3, "the control group must be in the baseline as blocked"


def test_every_baseline_entry_is_a_real_refusal_status():
    for group, label, status in probe._baseline():
        assert status in (401, 402, 403), (group, label, status)
