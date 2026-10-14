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
        # Truthy: the claim WON. A falsy answer now sends the worker to `_fail_unclaimed_row`
        # (a Supabase re-read) — exercised by its own tests below, never by accident here.
        return True

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


# ── 2026-10-12: a claim that did not land — `_fail_unclaimed_row` ─────────────────────
#
# `generate_report` no longer pre-stamps status='failed' (that made 'failed' visible before
# the refund). The claim (`claim_and_mark_failed`) now writes 'failed' + is_refunded in one
# UPDATE — but it filters on credits_charged > 0, so an UNCHARGED row it cannot match would
# otherwise sit 'processing' forever, and a CHARGED row whose claim did not land must NOT be
# marked failed without its refund.


class _RowStore:
    """A one-row fake of the research_reports table: records every UPDATE it applies."""

    def __init__(self, row):
        self.row = dict(row)
        self.updates: list = []

    def table(self, _name):
        store = self

        class _Q:
            def __init__(self):
                self.op, self.payload, self.filters = None, None, []

            def select(self, *_a, **_k):
                self.op = "select"
                return self

            def update(self, payload):
                self.op, self.payload = "update", payload
                return self

            def eq(self, col, val):
                self.filters.append(lambda r: r.get(col) == val)
                return self

            def in_(self, col, vals):
                self.filters.append(lambda r: r.get(col) in vals)
                return self

            def lte(self, col, val):
                self.filters.append(lambda r: r.get(col) is not None and r.get(col) <= val)
                return self

            def limit(self, _n):
                return self

            def execute(self):
                match = all(f(store.row) for f in self.filters)

                class _R:
                    data = [dict(store.row)] if match else []
                if self.op == "update" and match:
                    store.updates.append(dict(self.payload))
                    store.row.update(self.payload)
                return _R()

        return _Q()


def _wire_unclaimed(monkeypatch, row):
    store = _RowStore(row)
    pdf, failed = _wire(monkeypatch, outcome=RuntimeError("gemini down"))

    async def _lost_claim(report_id, body):
        failed.append((report_id, body.get("error_code")))
        return False

    monkeypatch.setattr(research_ep, "claim_and_mark_failed", _lost_claim)
    monkeypatch.setattr("app.database.get_supabase", lambda: store)
    return store, failed


@pytest.mark.asyncio
async def test_an_unclaimed_uncharged_row_is_marked_failed_by_the_worker(monkeypatch):
    store, failed = _wire_unclaimed(
        monkeypatch, {"id": "r1", "status": "processing", "is_refunded": False, "credits_charged": 0})
    await research_ep._run_research_task("r1", "AAPL", "warren_buffett", "u1")
    assert failed and store.row["status"] == "failed"
    assert len(store.updates) == 1 and store.updates[0]["status"] == "failed"
    assert "error_message" in store.updates[0], "the structured error must ride along"


@pytest.mark.asyncio
async def test_an_unclaimed_charged_row_is_left_for_the_sweep(monkeypatch, caplog):
    store, _failed = _wire_unclaimed(
        monkeypatch, {"id": "r1", "status": "processing", "is_refunded": False, "credits_charged": 20})
    with caplog.at_level(logging.WARNING, logger=research_ep.logger.name):
        await research_ep._run_research_task("r1", "AAPL", "warren_buffett", "u1")
    assert store.updates == [], "'failed' written with no refund behind it"
    assert store.row["status"] == "processing"
    assert any("reconciliation sweep" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "deleted"])
async def test_a_terminal_unclaimed_row_is_untouched(monkeypatch, status):
    store, _failed = _wire_unclaimed(
        monkeypatch, {"id": "r1", "status": status, "is_refunded": True, "credits_charged": 0})
    await research_ep._run_research_task("r1", "AAPL", "warren_buffett", "u1")
    assert store.updates == []


@pytest.mark.asyncio
async def test_a_reread_failure_is_logged_and_writes_nothing(monkeypatch, caplog):
    class _Down:
        def table(self, _name):
            raise RuntimeError("supabase down")

    _wire(monkeypatch, outcome=RuntimeError("gemini down"))

    async def _lost_claim(report_id, body):
        return False

    monkeypatch.setattr(research_ep, "claim_and_mark_failed", _lost_claim)
    monkeypatch.setattr("app.database.get_supabase", lambda: _Down())
    with caplog.at_level(logging.WARNING, logger=research_ep.logger.name):
        await research_ep._run_research_task("r1", "AAPL", "warren_buffett", "u1")
    assert any("re-read after an unclaimed failure failed" in r.getMessage() for r in caplog.records)
