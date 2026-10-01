"""No service may read the legacy fiscal-keyed 'quarterly' benchmark rows.

Migration 184 moved the quarterly sector/industry benchmarks to period_type
'calendar_quarter' (keyed by the calendar quarter a period ENDS in); migration 185
deletes the legacy 'quarterly' rows. The legacy rows were keyed "<fiscal quarter
number>'<end-date year>", which joined Microsoft's fiscal Q1 (Jul-Sep) to its peers'
Jan-Mar. A reader still asking for 'quarterly' would serve those wrong peer values until
185 runs, and nothing at all after it, with no error anywhere.

This is the guard migration 185's header refers to. It reads the AST of every module
under app/services (so a comment or docstring mentioning "quarterly" cannot satisfy or
trip it) and fails on any benchmark-lookup call, direct or via asyncio.to_thread, that
passes the string literal "quarterly".
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"

_LOOKUP_METHODS = frozenset({
    "get_benchmarks",
    "get_benchmark_values",
    "get_sector_benchmarks",
    "get_sector_benchmarks_with_n",
})


def _method_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_lookup_call(call: ast.Call) -> bool:
    if _method_name(call.func) in _LOOKUP_METHODS:
        return True
    # asyncio.to_thread(lookup.get_benchmarks, ...)
    if _method_name(call.func) == "to_thread" and call.args:
        return _method_name(call.args[0]) in _LOOKUP_METHODS
    return False


def _passes_legacy_quarterly(call: ast.Call) -> bool:
    values = list(call.args) + [kw.value for kw in call.keywords]
    return any(isinstance(v, ast.Constant) and v.value == "quarterly" for v in values)


def _offending_calls(source: str) -> list:
    tree = ast.parse(source)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_lookup_call(node) and _passes_legacy_quarterly(node)
    ]


def _service_files():
    return sorted(p for p in _SERVICES.rglob("*.py") if "__pycache__" not in p.parts)


@pytest.mark.parametrize("path", _service_files(), ids=lambda p: str(p.relative_to(_SERVICES)))
def test_no_service_reads_legacy_quarterly_benchmarks(path):
    offending = _offending_calls(path.read_text())
    assert not offending, (
        f"{path.relative_to(_SERVICES.parent.parent)} reads period_type 'quarterly' at line(s) "
        f"{offending}; read CALENDAR_QUARTER_PERIOD_TYPE and join with "
        f"period_labels.calendar_quarter_label"
    )


# ── The guard itself must not be vacuous ────────────────────────────────────


@pytest.mark.parametrize(
    "snippet",
    [
        'lookup.get_benchmarks(industry, sector, metrics, "quarterly")',
        'await asyncio.to_thread(lookup.get_benchmark_values, i, s, m, "quarterly")',
        'lookup.get_sector_benchmarks(sector, metrics, period_type="quarterly")',
        'self._lookup.get_sector_benchmarks_with_n(sector, metrics, period_type="quarterly")',
    ],
)
def test_the_guard_catches_every_call_shape(snippet):
    assert _offending_calls(snippet), snippet


@pytest.mark.parametrize(
    "snippet",
    [
        'lookup.get_benchmarks(industry, sector, metrics, CALENDAR_QUARTER_PERIOD_TYPE)',
        'lookup.get_benchmarks(industry, sector, metrics, "annual")',
        '# lookup.get_benchmarks(industry, sector, metrics, "quarterly")',
        'x = "quarterly"  # an unrelated label',
        'labels = ("annual", "quarterly")',
    ],
)
def test_the_guard_ignores_comments_and_other_uses(snippet):
    assert not _offending_calls(snippet), snippet


def test_the_guard_actually_scans_the_readers():
    names = {p.name for p in _service_files()}
    assert {"growth_service.py", "profit_power_service.py"} <= names
    assert any(p.name == "ticker_report_data_collector.py" for p in _service_files())
