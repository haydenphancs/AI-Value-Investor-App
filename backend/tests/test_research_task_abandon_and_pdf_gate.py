"""`_run_research_task` after the 2026-09-16 hardening.

Two branches the worker used to get wrong:

  * A report DELETED while queued still burned a full agent run — the slot was released
    only after ~17 Gemini / ~20 FMP calls whose completion write was a guaranteed no-op —
    and then the task logged an ERROR (a Sentry event per abandoned report) and ran the
    mark-failed / refund path against a row the DELETE had already refunded.
  * A result dropped because the row had been reconciled or deleted still got a PDF
    rendered and `pdf_status` stamped onto it.

No network: `ResearchService` and `_generate_report_pdf` are replaced at the seams the task
actually uses.
"""

from __future__ import annotations

import logging

import pytest

from app.api.v1.endpoints import research as research_ep
from app.services import research_service as rs


def _wire(monkeypatch, *, outcome):
    """outcome: True / False (delivered flag) or an exception instance to raise."""
    pdf_calls, failed_calls = [], []

    class _Svc:
        def __init__(self):
            pass

        async def generate_report(self, *a, **k):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    monkeypatch.setattr(rs, "ResearchService", _Svc)

    async def _pdf(report_id, user_id):
        pdf_calls.append(report_id)

    async def _claim(report_id, body):
        failed_calls.append((report_id, body.get("error_code")))

    monkeypatch.setattr(research_ep, "_generate_report_pdf", _pdf)
    monkeypatch.setattr(research_ep, "claim_and_mark_failed", _claim)
    return pdf_calls, failed_calls


@pytest.mark.asyncio
async def test_a_delivered_report_gets_its_pdf(monkeypatch):
    pdf, failed = _wire(monkeypatch, outcome=True)
    await research_ep._run_research_task("r1", "AAPL", "warren_buffett", "u1")
    assert pdf == ["r1"] and failed == []


@pytest.mark.asyncio
async def test_a_dropped_result_gets_no_pdf_and_no_refund(monkeypatch, caplog):
    """The row was reconciled (refunded) or deleted before the completion write."""
    pdf, failed = _wire(monkeypatch, outcome=False)
    with caplog.at_level(logging.INFO, logger=research_ep.logger.name):
        await research_ep._run_research_task("r1", "AAPL", "warren_buffett", "u1")
    assert pdf == [] and failed == []
    assert any("no delivery" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_an_abandoned_report_is_logged_at_info_and_touches_nothing(monkeypatch, caplog):
    pdf, failed = _wire(monkeypatch, outcome=rs.ReportAbandonedError("deleted while queued"))
    with caplog.at_level(logging.INFO, logger=research_ep.logger.name):
        await research_ep._run_research_task("r1", "AAPL", "warren_buffett", "u1")
    assert pdf == [] and failed == [], "an abandoned row is already terminal and refunded"
    hits = [r for r in caplog.records if "abandoned" in r.getMessage()]
    assert hits and all(r.levelno == logging.INFO for r in hits), "no ERROR, no Sentry event"


@pytest.mark.asyncio
async def test_a_real_failure_still_marks_failed_with_a_typed_code(monkeypatch):
    """Negative control: the abandon carve-out must not widen."""
    pdf, failed = _wire(monkeypatch, outcome=rs.ReportPipelineTimeoutError("AAPL", "warren_buffett", 600))
    await research_ep._run_research_task("r1", "AAPL", "warren_buffett", "u1")
    assert pdf == []
    assert failed == [("r1", "REPORT_TIMED_OUT")]
