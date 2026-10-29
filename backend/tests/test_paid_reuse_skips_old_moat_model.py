"""The paid deep door never re-sells moat pillars scored by an older scorer vintage.

2026-10-28: the moat scorer changed (owner decisions after the JOBY audit — growth off a
near-zero base, P/S with a negative gross margin, industry-only Network Effects, patent-only
Intangible Assets). `research_service._lookup_shared_cache` reuses any report completed since
the last close and charges that buyer 20 credits on the hit, so a report whose
`moat_competition.model_version` is not `SCORER_VERSION` (or is missing: built before the
stamp) is a MISS there — same price, today's pillars. Same split as the DCF gate
(tests/test_paid_reuse_skips_old_dcf_model.py): the free cached paths are left as they are
(owner, 2026-10-25, "Leave the free cached path as is"), because a miss there charges the
next opener 20 credits; their rows age out at the close.
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

import app.services.research_service as rs
import app.services.ticker_report_cache as trc
from app.services.moat_scoring_service import SCORER_VERSION, report_moat_model_is_current
from app.services.report_degradation import GROUNDING_FREE_KEY
from app.services.research_service import ResearchService

BACKEND = Path(__file__).resolve().parents[1]
_ABSENT = object()
_NOW = datetime.now(timezone.utc).isoformat()


def _report(version=_ABSENT, *, moat: bool = True) -> dict:
    blob = {"symbol": "JOBY", "quality_score": 40.0, GROUNDING_FREE_KEY: True,
            # FMP-source Wall Street block: the DCF gates pass whatever DCF_ENABLED is.
            "wall_street_consensus": {"dcf_source": "fmp"}}
    if moat:
        block = {"dimensions": []}
        if version is not _ABSENT:
            block["model_version"] = version
        blob["moat_competition"] = block
    return blob


def _chain(rows):
    q = MagicMock()
    for m in ("table", "select", "eq", "gte", "not_", "is_", "order", "limit"):
        getattr(q, m).return_value = q
    q.not_ = q
    q.execute.return_value = MagicMock(data=rows)
    return q


@pytest.fixture(autouse=True)
def _fmp_dcf(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DCF_ENABLED", False)


@pytest.mark.parametrize("payload, current", [
    (_report(SCORER_VERSION), True),
    (_report("moat_v1.2026-10-22"), False),     # older scorer
    (_report(), False),                          # built before the stamp: fail closed
    (_report(None), False),
    (_report(moat=False), True),                 # no moat block: nothing to compare
    ({"moat_competition": "garbage"}, True),
    ("not a report", True),
])
def test_the_moat_model_predicate(payload, current):
    assert report_moat_model_is_current(payload) is current


def _lookup(rows):
    svc = object.__new__(ResearchService)
    svc.supabase = _chain(rows)
    return asyncio.run(svc._lookup_shared_cache("JOBY", "warren_buffett"))


def test_an_older_scorer_report_is_a_miss_on_the_paid_door(caplog):
    with caplog.at_level(logging.INFO, logger=rs.__name__):
        got = _lookup([{"ticker_report_data": _report("moat_v1.2026-10-22"),
                        "completed_at": _NOW}])
    assert got is None
    assert "JOBY/warren_buffett" in caplog.text and "older scorer" in caplog.text


def test_an_unstamped_report_is_a_miss_on_the_paid_door():
    assert _lookup([{"ticker_report_data": _report(), "completed_at": _NOW}]) is None


def test_a_current_scorer_report_is_still_reused():
    blob = _report(SCORER_VERSION)
    assert _lookup([{"ticker_report_data": blob, "completed_at": _NOW}]) == blob


def test_the_free_cache_still_serves_an_older_scorer_report(monkeypatch):
    blob = _report("moat_v1.2026-10-22")
    monkeypatch.setattr(trc, "get_supabase",
                        lambda: _chain([{"ticker_report_data": blob, "cached_at": _NOW}]))
    monkeypatch.setattr(trc, "is_cache_fresh", lambda *_a, **_k: True)
    assert asyncio.run(trc.get_cached_report("JOBY", "warren_buffett")) == blob


def _strip(src: str) -> str:
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


def _function_body(rel: str, name: str) -> str:
    src = (BACKEND / rel).read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return _strip(ast.get_source_segment(src, node))
    raise AssertionError(f"{name} not found in {rel}")


def test_only_the_paid_lookup_applies_the_moat_model_check():
    assert "report_moat_model_is_current(" in _function_body(
        "app/services/research_service.py", "_lookup_shared_cache")
    for rel in ("app/services/ticker_report_cache.py", "app/api/v1/endpoints/ticker_report.py"):
        assert "report_moat_model_is_current" not in _strip((BACKEND / rel).read_text()), rel
