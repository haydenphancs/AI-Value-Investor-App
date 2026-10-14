"""Migration 195's 'legacy' backfill is bounded by the LEDGER, never by a hard-coded instant.

The backfill marks refunded rows 'legacy' (confirmed: the card keeps "Refunded N credits"). That
is right only for rows CHARGED under the old bare-ticker ref, which no replay can re-check. An
UNBOUNDED backfill also confirmed every refund the new code claimed before 195 ran (or before a
re-run) — including refunds that never landed (a SIGKILL between the claim and the RPC, a lost
reply) — and took them out of the retry pass for good, with a false chip and no page.

So the backfill only labels rows created before the first deep-door charge named by the unique
per-report ref (`report_charge_ref`: `TICKER:{report uuid}`), read from credit_transactions —
`now()` when there is none yet — and the retry pass owns everything after it. The regex that
recognises that ref is checked here against the REAL ref builders: it must match every
per-report ref and none of the other refs written with the same `report_charge` reason (the
legacy bare ticker, the legacy direct door's `TICKER:persona`, the current direct door's
`TICKER:persona:{32 hex}`).

Source-level (comments stripped before the structural assertions, testing.md rule 1: the
header names every token checked here). Non-vacuity: the unbounded and the hard-coded shapes
are fed through the same detector and must fail.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest

from app.services.credit_service import (
    chat_turn_ref,
    direct_report_charge_ref,
    report_charge_ref,
)

_MIGRATION = (Path(__file__).resolve().parents[1] / "database" / "migrations"
              / "195_research_reports_refund_outcome.sql")

_BOUND = re.compile(
    r"created_at\s*<\s*COALESCE\s*\(\s*\(\s*SELECT\s+min\s*\(\s*ct\.created_at\s*\)\s+"
    r"FROM\s+public\.credit_transactions\s+ct\s+"
    r"WHERE\s+ct\.reason\s*=\s*'report_charge'\s+"
    r"AND\s+ct\.ref_id\s*~\s*'(?P<pattern>[^']+)'\s*\)\s*,\s*now\s*\(\s*\)\s*\)",
    re.I | re.S,
)

_PERSONAS = ("warren_buffett", "cathie_wood", "peter_lynch", "bill_ackman", "michael_burry")
_TICKERS = ("NVDA", "A", "BRK.B", "BF-B", "nvda", "TSLA")


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _backfill(sql: str) -> str:
    """The body of the one UPDATE that writes 'legacy' (comments already stripped)."""
    hits = re.findall(
        r"UPDATE\s+public\.research_reports\s+SET\s+refund_outcome\s*=\s*'legacy'(.*?);",
        sql, re.I | re.S,
    )
    assert len(hits) == 1, f"expected ONE legacy backfill UPDATE, found {len(hits)}"
    return hits[0]


def _bound_problems(backfill: str) -> list[str]:
    out = []
    if not re.search(r"\bWHERE\s+is_refunded\b", backfill, re.I):
        out.append("the backfill no longer requires is_refunded")
    if not re.search(r"refund_outcome\s+IS\s+NULL", backfill, re.I):
        out.append("the backfill is no longer idempotent (refund_outcome IS NULL)")
    if not _BOUND.search(backfill):
        out.append("the backfill is not bounded by the first per-report charge in the ledger")
    if re.search(r"'\d{4}-\d{2}-\d{2}", backfill) or re.search(r"timestamp(tz)?\s*'", backfill, re.I):
        out.append("the backfill hard-codes an instant — it must read the bound from the ledger")
    return out


def _sql() -> str:
    return _strip_sql_comments(_MIGRATION.read_text(encoding="utf-8"))


def _pattern() -> str:
    m = _BOUND.search(_backfill(_sql()))
    assert m, _bound_problems(_backfill(_sql()))
    return m.group("pattern")


def test_the_backfill_is_bounded_by_the_first_per_report_charge():
    assert _bound_problems(_backfill(_sql())) == []


@pytest.mark.parametrize("mutation", [
    # the original, unbounded backfill
    lambda b: re.sub(r"\s+AND\s+created_at\s*<\s*COALESCE.*", "", b, flags=re.S),
    # a hard-coded cutover instead of the ledger
    lambda b: re.sub(r"AND\s+created_at\s*<\s*COALESCE.*",
                     "AND created_at < '2026-10-13T00:00:00Z'", b, flags=re.S),
    # the right shape on the wrong reason (a chat or refund row would set the bound)
    lambda b: b.replace("'report_charge'", "'chat_charge'"),
])
def test_the_detector_rejects_unbounded_and_hard_coded_backfills(mutation):
    real = _backfill(_sql())
    assert _bound_problems(real) == [], "the real backfill must pass first"
    mutated = mutation(real)
    assert mutated != real, "the mutation did not apply — this test has rotted"
    assert _bound_problems(mutated), "a broken backfill passed the detector — it is vacuous"


def test_the_pattern_uses_only_syntax_postgres_and_python_read_alike():
    """It is checked in Python below and run by Postgres (`~`, an unanchored ARE search, like
    `re.search`). Keep it to the shared subset so the check here means what the SQL does."""
    assert re.fullmatch(r"[\^\$\[\]{}+:\-0-9a-f]+", _pattern()), _pattern()


@pytest.mark.parametrize("ticker", _TICKERS)
def test_the_pattern_matches_every_per_report_charge_ref(ticker):
    pat = re.compile(_pattern())
    for _ in range(25):
        rid = uuid.uuid4()
        assert pat.search(report_charge_ref(ticker, rid)), report_charge_ref(ticker, rid)
        # The builder lower-cases the id, so an upper-cased one still names the same ref.
        assert pat.search(report_charge_ref(ticker, str(rid).upper()))


@pytest.mark.parametrize("ticker", _TICKERS)
def test_the_pattern_matches_no_other_ref_with_the_same_reason(ticker):
    """The direct door charges with reason 'report_charge' too; neither of its ref shapes, nor
    the legacy deep door's bare ticker, may move the bound."""
    pat = re.compile(_pattern())
    symbol = ticker.strip().upper()
    others = [symbol, ticker, f"{symbol}:{uuid.uuid4().hex}", str(uuid.uuid4()),
              chat_turn_ref("sess-1"), chat_turn_ref(str(uuid.uuid4()))]
    for persona in _PERSONAS:
        others += [f"{symbol}:{persona}"]                               # legacy direct door
        others += [direct_report_charge_ref(ticker, persona) for _ in range(5)]  # current
    for ref in others:
        assert not pat.search(ref), f"{ref!r} would set the per-report-ref bound"


def test_the_verify_query_uses_the_same_pattern():
    """The VERIFY block's `unlabelled_bare_ticker_era` must count against the same bound."""
    raw = _MIGRATION.read_text(encoding="utf-8")
    found = re.findall(r"ref_id\s*~\s*'([^']+)'", raw)
    assert len(found) >= 2, "the VERIFY block lost its bound"
    assert set(found) == {_pattern()}


def test_the_service_has_no_hard_coded_cutover():
    """The retry pass recognises a bare-ticker-era row by its answer, not by a constant that
    would have to be moved to the deploy instant by hand."""
    import app.services.research_reconciliation_service as recon

    assert not hasattr(recon, "PER_REPORT_REF_CUTOVER")
