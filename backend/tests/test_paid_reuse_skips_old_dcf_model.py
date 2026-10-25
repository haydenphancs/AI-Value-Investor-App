"""The paid deep door never re-sells a report frozen under an older DCF model.

Owner, 2026-10-25: a Sunday NVDA report (credits_charged 20, generated in 1 s) was a copy of
Friday night's dcf-v1 report — "Not modelled — forecast growth is too extreme" — although the
deployed dcf-v2 values NVDA at $305. `research_service._lookup_shared_cache` reuses any report
completed since the last close (Friday 6 pm ET → Monday 6 pm ET over a weekend), and that buyer
is charged 20 credits on a reuse hit too. So on that path a report whose published estimate came
from another `MODEL_VERSION` is a MISS: same price, today's model.

The owner's other instruction is pinned too: "Leave the free cached path as is". The free
paths (`ticker_report_cache.get_cached_report`, the direct door's legacy read) keep serving such
a report until the close, because a miss there would charge the next opener 20 credits.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.services.dcf_report_gate as gate
import app.services.research_service as rs
import app.services.ticker_report_cache as trc
from app.config import settings
from app.services.dcf_fair_value_service import MODEL_VERSION
from app.services.report_degradation import GROUNDING_FREE_KEY
from app.services.research_service import ResearchService

BACKEND = Path(__file__).resolve().parents[1]
_ABSENT = object()


def _report(block=_ABSENT, *, source: str = "caydex") -> dict:
    """A minimal stored report as the shared lookup reads it back from JSONB."""
    ws: dict = {"dcf_source": source}
    if block is not _ABSENT:
        ws["caydex_fair_value"] = block
    return {"symbol": "NVDA", "quality_score": 70.0, GROUNDING_FREE_KEY: True,
            "wall_street_consensus": ws}


def _block(version) -> dict:
    out = {"symbol": "NVDA", "status": "refused", "refusal_code": "growth_out_of_range"}
    if version is not _ABSENT:
        out["model_version"] = version
    return out


def _chain(rows):
    q = MagicMock()
    for m in ("table", "select", "eq", "gte", "not_", "is_", "order", "limit"):
        getattr(q, m).return_value = q
    q.not_ = q
    q.execute.return_value = MagicMock(data=rows)
    return q


@pytest.fixture
def dcf_on(monkeypatch):
    monkeypatch.setattr(settings, "DCF_ENABLED", True)


# ── the predicate ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload, current", [
    (_report(_block(MODEL_VERSION)), True),
    (_report(_block("dcf-v1")), False),                 # the weekend NVDA copy
    (_report(_block(_ABSENT)), False),                  # unreadable version: fail closed
    (_report(_block(None)), False),
    (_report("not a block"), False),
    (_report(None), True),                              # no estimate published: nothing to compare
    (_report(), True),
    ({"symbol": "NVDA"}, True),                         # no Wall Street section at all
    ("not a report", True),
])
def test_the_model_predicate(dcf_on, payload, current):
    assert gate.report_dcf_model_is_current(payload) is current


def test_the_predicate_is_inert_while_the_estimate_is_off(monkeypatch):
    monkeypatch.setattr(settings, "DCF_ENABLED", False)
    assert gate.report_dcf_model_is_current(_report(_block("dcf-v1"))) is True


# ── the paid deep door's cross-user reuse ───────────────────────────────────

def _lookup(rows):
    svc = object.__new__(ResearchService)
    svc.supabase = _chain(rows)
    return asyncio.run(svc._lookup_shared_cache("NVDA", "warren_buffett"))


_NOW = datetime.now(timezone.utc).isoformat()


def test_an_older_model_report_is_a_miss_on_the_paid_door(dcf_on, caplog):
    with caplog.at_level(logging.INFO, logger=rs.__name__):
        got = _lookup([{"ticker_report_data": _report(_block("dcf-v1")), "completed_at": _NOW}])
    assert got is None
    assert "NVDA/warren_buffett" in caplog.text and "older DCF model" in caplog.text


def test_a_current_model_report_is_still_reused(dcf_on):
    blob = _report(_block(MODEL_VERSION))
    assert _lookup([{"ticker_report_data": blob, "completed_at": _NOW}]) == blob


def test_a_report_without_an_estimate_is_still_reused(dcf_on):
    # Delivered without the estimate after a permanent input refusal: no model to compare,
    # and refusing it would regenerate that ticker on every paid open.
    blob = _report(None)
    assert _lookup([{"ticker_report_data": blob, "completed_at": _NOW}]) == blob


def test_the_older_model_check_is_inert_while_the_estimate_is_off(monkeypatch):
    # A dcf-v1 block on an FMP-source report: the source gate passes with the switch off, so
    # only the predicate's own DCF_ENABLED check keeps this a hit.
    monkeypatch.setattr(settings, "DCF_ENABLED", False)
    blob = _report(_block("dcf-v1"), source="fmp")
    assert _lookup([{"ticker_report_data": blob, "completed_at": _NOW}]) == blob


# ── the free paths are left as they are ─────────────────────────────────────

def test_the_free_cache_still_serves_an_older_model_report(dcf_on, monkeypatch):
    blob = _report(_block("dcf-v1"))
    monkeypatch.setattr(trc, "get_supabase",
                        lambda: _chain([{"ticker_report_data": blob, "cached_at": _NOW}]))
    monkeypatch.setattr(trc, "is_cache_fresh", lambda *_a, **_k: True)
    assert asyncio.run(trc.get_cached_report("NVDA", "warren_buffett")) == blob


def _strip(src: str) -> str:
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


def _code(rel: str) -> str:
    return _strip((BACKEND / rel).read_text())


def _function_body(rel: str, name: str) -> str:
    """Exactly one function's source (AST-bounded: a regex on `def` would run past an
    indented method to the end of the class), comments and docstrings stripped."""
    src = (BACKEND / rel).read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return _strip(ast.get_source_segment(src, node))
    raise AssertionError(f"{name} not found in {rel}")


def test_only_the_paid_lookup_applies_the_model_check():
    assert "report_dcf_model_is_current(" in _function_body(
        "app/services/research_service.py", "_lookup_shared_cache")
    for rel in ("app/services/ticker_report_cache.py", "app/api/v1/endpoints/ticker_report.py"):
        assert "report_dcf_model_is_current" not in _code(rel), rel
