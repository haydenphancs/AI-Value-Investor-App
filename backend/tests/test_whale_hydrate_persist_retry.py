"""Whale hydration: retry the idempotent Supabase writes, never the unsafe ones.

Guards the production Sentry issue `APIError: JSON could not be generated` at
`scripts.hydrate_whales in _persist` — Supabase's Cloudflare edge returning an HTML
`520` page mid-hydration.

The interesting part is NOT that retry works; it is **where retry is forbidden**. A
520 says nothing about whether Postgres committed, so a replay is only admissible
when it converges to the same state:

  * snapshot upsert / whales update / raw_hash reset — single idempotent statements.
  * holdings + sector allocations — safe ONLY as whole blocks, because each opens
    with a DELETE that wipes a partial commit. Retrying an individual insert would
    hit `whale_holdings_whale_id_ticker_key` and truncate the holdings.
  * trade groups + trades — still never retried, but for a narrower reason now. Both
    writes are UPSERTs against real unique keys (`uq_whale_trade_groups_whale_date`
    from 077, `uq_whale_trades_group_ticker_action_date` from 143), so a replay
    REPAIRS a partially-written group instead of skipping it forever or duplicating
    every trade. Idempotency makes a replay safe, not free: `retry_idempotent_sync`
    replays from the top of the block it guards, and the per-group try/except already
    isolates failures.

Also covers the latent `NameError` on the ticker-only path (Issue D).
"""
from __future__ import annotations

import logging

import pytest
from postgrest.exceptions import APIError, generate_default_error_message

import scripts.hydrate_whales as hw
from app.services._whale_common import (
    RETURN_OK,
    RETURN_UNAVAILABLE,
    SOURCE_STOCK,
    AnnualReturn,
)


# ── builders ─────────────────────────────────────────────────────────────────

def _gateway_error(status: int = 520) -> APIError:
    class _R:
        status_code = status
        content = b"<!DOCTYPE html><title>520: Web server is returning an unknown error</title>"

    return APIError(generate_default_error_message(_R()))


def _unique_violation() -> APIError:
    return APIError(
        {"message": "duplicate key", "code": "23505", "hint": None, "details": None}
    )


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    """One fluent chain. Records (table, verb) on execute and consults the fault map."""

    def __init__(self, sb, table):
        self._sb = sb
        self._table = table
        self._verb = "select"
        self._payload = None

    def select(self, *a, **k):
        self._verb = "select"
        return self

    def insert(self, payload, *a, **k):
        self._verb = "insert"
        self._payload = payload
        return self

    def upsert(self, payload, *a, **k):
        self._verb = "upsert"
        self._payload = payload
        return self

    def update(self, payload, *a, **k):
        self._verb = "update"
        self._payload = payload
        return self

    def delete(self, *a, **k):
        self._verb = "delete"
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        op = (self._table, self._verb)
        self._sb.ops.append(op)
        if self._payload is not None:
            self._sb.payloads.append((op, self._payload))
        faults = self._sb.faults.get(op)
        if faults:
            exc = faults.pop(0)
            if exc is not None:
                raise exc
        return _Resp(self._sb.returns.get(op, [{"id": f"{self._table}-1"}]))


class _FakeSupabase:
    """Records every (table, verb) and can inject a scripted fault sequence.

    `faults` maps (table, verb) -> list of Exception|None consumed in order, so a
    test can say "fail the 3rd whale_holdings insert" precisely.
    """

    def __init__(self, faults=None, returns=None):
        self.faults = {k: list(v) for k, v in (faults or {}).items()}
        self.returns = returns or {}
        self.ops: list[tuple[str, str]] = []
        self.payloads: list[tuple[tuple[str, str], dict]] = []

    def table(self, name):
        return _Query(self, name)

    def count(self, table, verb):
        return sum(1 for t, v in self.ops if t == table and v == verb)


def _snapshot(n_holdings=3, n_sectors=2, trade_groups=None):
    return {
        "filing_period": "2026-08",
        "total_value": 1_000_000.0,
        "behavior_summary": "steady",
        "sentiment_text": "neutral",
        "raw_hash": "abc123",
        "holdings_data": [
            {"ticker": f"T{i}", "company_name": f"Co {i}", "allocation": 10,
             "change_percent": 1.0}
            for i in range(n_holdings)
        ],
        "sector_data": [
            {"name": f"Sector{i}", "allocation": 50} for i in range(n_sectors)
        ],
        "trade_groups": trade_groups or [],
    }


def _hydrator(sb, monkeypatch):
    h = hw.WhaleHydrator.__new__(hw.WhaleHydrator)  # skip get_supabase()
    h.sb = sb
    # Mirrors WhaleHydrator.__init__. `no_data` is its own bucket so a filer that has
    # gone quiet is not averaged into "unchanged since last run".
    h.stats = {"processed": 0, "skipped": 0, "failed": 0, "errors": 0, "no_data": 0}
    # _maybe_generate_alert is a separate concern (whale_alerts); stub it out.
    monkeypatch.setattr(hw.WhaleHydrator, "_maybe_generate_alert",
                        lambda self, *a, **k: None)
    return h


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    from app.utils import supabase_errors as se
    monkeypatch.setattr(se.time, "sleep", lambda *_a, **_k: None)


_OK_RETURN = AnnualReturn(value=12.5, window_years=5, source=SOURCE_STOCK, status=RETURN_OK)


# ── snapshot upsert ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_upsert_retries_a_520_then_succeeds(monkeypatch):
    sb = _FakeSupabase(faults={("whale_filing_snapshots", "upsert"): [_gateway_error(520), None]})
    h = _hydrator(sb, monkeypatch)

    await h._persist("w1", _snapshot(), _OK_RETURN)

    assert sb.count("whale_filing_snapshots", "upsert") == 2


@pytest.mark.asyncio
async def test_snapshot_retry_success_still_stamps_last_hydrated_at(monkeypatch):
    """The user-visible payoff of the retry.

    `last_hydrated_at` is only written when the snapshot persisted; without it the
    serve path re-runs the whole FMP fan-out on every view of that whale.
    """
    sb = _FakeSupabase(faults={("whale_filing_snapshots", "upsert"): [_gateway_error(520), None]})
    h = _hydrator(sb, monkeypatch)

    await h._persist("w1", _snapshot(), _OK_RETURN)

    whale_updates = [p for op, p in sb.payloads if op == ("whales", "update")]
    assert whale_updates and "last_hydrated_at" in whale_updates[0]


@pytest.mark.asyncio
async def test_snapshot_upsert_does_not_retry_a_23505(monkeypatch):
    sb = _FakeSupabase(faults={("whale_filing_snapshots", "upsert"): [_unique_violation()]})
    h = _hydrator(sb, monkeypatch)

    await h._persist("w1", _snapshot(), _OK_RETURN)

    assert sb.count("whale_filing_snapshots", "upsert") == 1


@pytest.mark.asyncio
async def test_a_520_logs_warning_not_error(monkeypatch, caplog):
    """No Sentry page for an upstream blip (LoggingIntegration fires at ERROR)."""
    sb = _FakeSupabase(faults={("whale_filing_snapshots", "upsert"): [_gateway_error(520)] * 5})
    h = _hydrator(sb, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=hw.logger.name):
        await h._persist("w1", _snapshot(), _OK_RETURN)

    snapshot_logs = [r for r in caplog.records if "Failed to upsert snapshot" in r.getMessage()]
    assert snapshot_logs and all(r.levelno == logging.WARNING for r in snapshot_logs)


@pytest.mark.asyncio
async def test_a_genuine_bug_still_logs_error_with_a_stack(monkeypatch, caplog):
    """Negative control for the demotion."""
    sb = _FakeSupabase(faults={("whale_filing_snapshots", "upsert"): [KeyError("column")]})
    h = _hydrator(sb, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=hw.logger.name):
        await h._persist("w1", _snapshot(), _OK_RETURN)

    hits = [r for r in caplog.records if "Failed to upsert snapshot" in r.getMessage()]
    assert hits and hits[0].levelno == logging.ERROR and hits[0].exc_info is not None


# ── holdings: the block-granularity proof ────────────────────────────────────

@pytest.mark.asyncio
async def test_holdings_retry_replays_from_the_delete_not_mid_loop(monkeypatch):
    """THE idempotency proof.

    Fail the 3rd holdings insert. A correct retry re-runs the whole block, so the
    op sequence must show a SECOND delete before the re-inserts. Resuming mid-loop
    instead would hit whale_holdings_whale_id_ticker_key on the rows that already
    committed and truncate the whale's holdings.
    """
    sb = _FakeSupabase(faults={("whale_holdings", "insert"): [None, None, _gateway_error(520)]})
    h = _hydrator(sb, monkeypatch)

    await h._persist("w1", _snapshot(n_holdings=3), _OK_RETURN)

    holdings_ops = [v for t, v in sb.ops if t == "whale_holdings"]
    assert holdings_ops == [
        "delete", "insert", "insert", "insert",   # attempt 1 — blew up on #3
        "delete", "insert", "insert", "insert",   # attempt 2 — full replay
    ]


@pytest.mark.asyncio
async def test_holdings_does_not_retry_a_23505(monkeypatch):
    """A unique violation means a row is already there — replaying cannot help."""
    sb = _FakeSupabase(faults={("whale_holdings", "insert"): [_unique_violation()]})
    h = _hydrator(sb, monkeypatch)

    await h._persist("w1", _snapshot(n_holdings=3), _OK_RETURN)

    assert [v for t, v in sb.ops if t == "whale_holdings"] == ["delete", "insert"]


@pytest.mark.asyncio
async def test_sector_allocations_also_replay_from_the_delete(monkeypatch):
    # This table has NO unique key, so per-statement retry would silently duplicate.
    sb = _FakeSupabase(faults={("whale_sector_allocations", "insert"): [None, _gateway_error(520)]})
    h = _hydrator(sb, monkeypatch)

    await h._persist("w1", _snapshot(n_sectors=2), _OK_RETURN)

    ops = [v for t, v in sb.ops if t == "whale_sector_allocations"]
    assert ops == ["delete", "insert", "insert", "delete", "insert", "insert"]


# ── trade groups: the "unsafe site must NOT get retry" control ───────────────

@pytest.mark.asyncio
async def test_trade_groups_are_never_retried(monkeypatch):
    """The most important negative control in this file.

    The write is now an UPSERT against `uq_whale_trade_groups_whale_date` (077), so a
    replay repairs rather than skips or duplicates — but it is still deliberately NOT
    wrapped in a retry helper. `retry_idempotent_sync` replays from the top of the block
    it guards, and the enclosing per-group try/except already provides isolation;
    idempotency makes a replay SAFE, not free. So a 520 here is still reported and
    dropped, never replayed.
    """
    groups = [{"date": "2026-08-01", "trade_count": 2, "net_action": "BUY",
               "net_amount": 100.0, "summary": "s", "insights": [], "trades": []}]
    sb = _FakeSupabase(
        faults={("whale_trade_groups", "upsert"): [_gateway_error(520)] * 5},
        returns={("whale_trade_groups", "select"): []},
    )
    h = _hydrator(sb, monkeypatch)

    await h._persist("w1", _snapshot(trade_groups=groups), _OK_RETURN)

    assert sb.count("whale_trade_groups", "upsert") == 1  # exactly one attempt
    # And it must NOT have fallen back to the old check-then-act insert.
    assert sb.count("whale_trade_groups", "insert") == 0


@pytest.mark.asyncio
async def test_trade_group_520_logs_warning_not_error(monkeypatch, caplog):
    groups = [{"date": "2026-08-01", "trade_count": 2, "net_action": "BUY",
               "net_amount": 100.0, "summary": "s", "insights": [], "trades": []}]
    sb = _FakeSupabase(
        faults={("whale_trade_groups", "upsert"): [_gateway_error(520)]},
        returns={("whale_trade_groups", "select"): []},
    )
    h = _hydrator(sb, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=hw.logger.name):
        await h._persist("w1", _snapshot(trade_groups=groups), _OK_RETURN)

    hits = [r for r in caplog.records if "Failed to sync trade group" in r.getMessage()]
    assert hits and all(r.levelno == logging.WARNING for r in hits)


@pytest.mark.asyncio
async def test_a_failed_denorm_write_still_resets_raw_hash(monkeypatch):
    """The existing self-heal must survive the refactor.

    Without the reset, the next run matches raw_hash, skips as "data unchanged",
    and the partially-written tables are never repaired.
    """
    sb = _FakeSupabase(faults={("whale_holdings", "insert"): [_unique_violation()]})
    h = _hydrator(sb, monkeypatch)

    await h._persist("w1", _snapshot(), _OK_RETURN)

    resets = [p for op, p in sb.payloads
              if op == ("whale_filing_snapshots", "update") and "raw_hash" in p]
    assert resets and resets[0]["raw_hash"] is None


# ── Issue D: the ticker-only NameError ───────────────────────────────────────

def _ticker_only_hydrator(sb, monkeypatch, result=_OK_RETURN):
    h = _hydrator(sb, monkeypatch)
    # Mirrors WhaleHydrator.__init__. `no_data` is its own bucket so a filer that has
    # gone quiet is not averaged into "unchanged since last run".
    h.stats = {"processed": 0, "skipped": 0, "failed": 0, "errors": 0, "no_data": 0}

    async def _no_data(self, *a, **k):
        return None

    async def _return(self, *a, **k):
        return result

    monkeypatch.setattr(hw.WhaleHydrator, "_process_13f", _no_data)
    monkeypatch.setattr(hw.WhaleHydrator, "_compute_ytd_return", _return)
    return h


_WHALE = {
    "id": "w1", "name": "ARK Innovation", "fmp_name": "ARK",
    "data_source": "13f", "associated_ticker": "ARKK", "cik": "0001",
}


@pytest.mark.asyncio
async def test_ticker_only_success_is_counted_as_processed(monkeypatch, caplog):
    """Fails before the fix with skipped == 1.

    The log line referenced `ytd_return` / `return_label`, neither bound in scope.
    The NameError fired AFTER the UPDATE committed, was caught by the handler, and
    reported the successful write as "Ticker-only update failed".
    """
    sb = _FakeSupabase()
    h = _ticker_only_hydrator(sb, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=hw.logger.name):
        await h._hydrate_one(dict(_WHALE))

    assert h.stats["processed"] == 1
    assert h.stats["skipped"] == 0
    assert not [r for r in caplog.records if "Ticker-only update failed" in r.getMessage()]


@pytest.mark.asyncio
async def test_ticker_only_writes_the_same_label_it_logs(monkeypatch, caplog):
    """The write and the log now share one `label`, so they cannot disagree."""
    sb = _FakeSupabase()
    h = _ticker_only_hydrator(sb, monkeypatch)

    with caplog.at_level(logging.INFO, logger=hw.logger.name):
        await h._hydrate_one(dict(_WHALE))

    written = [p for op, p in sb.payloads if op == ("whales", "update")][0]
    logged = [r.getMessage() for r in caplog.records if "ticker-only return updated" in r.getMessage()]
    assert logged and written["return_label"] in logged[0]
    assert str(written["ytd_return"]) in logged[0]


@pytest.mark.asyncio
async def test_ticker_only_db_failure_is_still_counted_as_skipped(monkeypatch, caplog):
    """Negative control: a REAL write failure must not be counted as processed."""
    sb = _FakeSupabase(faults={("whales", "update"): [KeyError("return_label")] * 5})
    h = _ticker_only_hydrator(sb, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=hw.logger.name):
        await h._hydrate_one(dict(_WHALE))

    assert h.stats["processed"] == 0
    assert h.stats["skipped"] == 1
    hits = [r for r in caplog.records if "Ticker-only update failed" in r.getMessage()]
    assert hits and hits[0].levelno == logging.ERROR


@pytest.mark.asyncio
async def test_ticker_only_skips_when_the_return_is_not_ok(monkeypatch):
    unavailable = AnnualReturn(value=None, window_years=None, source="",
                               status=RETURN_UNAVAILABLE)
    sb = _FakeSupabase()
    h = _ticker_only_hydrator(sb, monkeypatch, result=unavailable)

    await h._hydrate_one(dict(_WHALE))

    assert h.stats["skipped"] == 1
    assert sb.count("whales", "update") == 0

# ── The partial-index trap (migrations 143 → 146) ─────────────────────────────
#
# MEASURED IN PRODUCTION, 2026-08-19, one hydration cycle on Railway: 12 × 42P10 fallback
# warnings followed by 24 × 23505 "Failed to sync trade group".
#
# 143's unique index shipped PARTIAL (`WHERE trade_group_id IS NOT NULL`), and PostgreSQL
# only infers a partial index when the statement also supplies the predicate — which
# PostgREST's bare `on_conflict=` column list cannot express. So the upsert raised 42P10, the
# fallback insert hit the very index that could not be inferred (23505), and the exception
# propagated out of the caller's per-trade loop, aborting every REMAINING trade in the group.
# Migration 146 drops the predicate; this code path is the belt to that braces.


def _no_inferable_index() -> APIError:
    return APIError({
        "message": "there is no unique or exclusion constraint matching the "
                   "ON CONFLICT specification",
        "code": "42P10", "hint": None, "details": None,
    })


def test_a_42P10_falls_back_and_tolerates_a_row_that_is_already_there():
    """23505 on the fallback means the row is ALREADY in the state the upsert wanted.
    That is the fallback succeeding; raising turned a no-op into a failed group."""
    sb = _FakeSupabase(faults={
        ("whale_trades", "upsert"): [_no_inferable_index()],
        ("whale_trades", "insert"): [_unique_violation()],
    })

    hw.WhaleHydrator._upsert_trade(sb, "w1", "tg1", {
        "ticker": "IBM", "action": "SOLD", "trade_type": "Sell",
        "amount": 1.0, "date": "2025-08-12",
    })

    assert sb.count("whale_trades", "upsert") == 1
    assert sb.count("whale_trades", "insert") == 1


def test_the_fallback_insert_still_raises_a_genuine_failure():
    """Tolerating 23505 must not become tolerating everything."""
    sb = _FakeSupabase(faults={
        ("whale_trades", "upsert"): [_no_inferable_index()],
        ("whale_trades", "insert"): [_gateway_error(520)],
    })

    with pytest.raises(APIError):
        hw.WhaleHydrator._upsert_trade(sb, "w1", "tg1", {
            "ticker": "IBM", "action": "SOLD", "trade_type": "Sell",
            "amount": 1.0, "date": "2025-08-12",
        })


def test_a_non_42P10_upsert_error_is_not_downgraded_to_an_insert():
    """Only an unusable conflict target justifies the fallback. A 520 must propagate, or a
    transient edge failure would silently become a duplicate-prone plain insert."""
    sb = _FakeSupabase(faults={("whale_trades", "upsert"): [_gateway_error(520)]})

    with pytest.raises(APIError):
        hw.WhaleHydrator._upsert_trade(sb, "w1", "tg1", {
            "ticker": "IBM", "action": "SOLD", "trade_type": "Sell",
            "amount": 1.0, "date": "2025-08-12",
        })

    assert sb.count("whale_trades", "insert") == 0


@pytest.mark.asyncio
async def test_one_duplicate_trade_does_not_abort_the_rest_of_the_group(monkeypatch, caplog):
    """THE production symptom, end to end.

    `_upsert_trade` is called in a loop inside the caller's `try`, so a raise on trade #1
    skipped trades #2..N and logged "Failed to sync trade group". A repair run could
    therefore never top up a partially written group — the exact behaviour migration 143
    was written to deliver.
    """
    trades = [
        {"ticker": "IBM", "action": "SOLD", "trade_type": "Sell", "amount": 1.0,
         "date": "2025-08-12"},
        {"ticker": "MSFT", "action": "BOUGHT", "trade_type": "Buy", "amount": 2.0,
         "date": "2025-08-12"},
    ]
    groups = [{"date": "2026-08-01", "trade_count": 2, "net_action": "BUY",
               "net_amount": 100.0, "summary": "s", "insights": [], "trades": trades}]
    # Every trade takes the worst path: unusable conflict target, then already-present.
    sb = _FakeSupabase(faults={
        ("whale_trades", "upsert"): [_no_inferable_index()] * 2,
        ("whale_trades", "insert"): [_unique_violation()] * 2,
    })
    h = _hydrator(sb, monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=hw.logger.name):
        await h._persist("w1", _snapshot(trade_groups=groups), _OK_RETURN)

    assert sb.count("whale_trades", "upsert") == 2, (
        "the second trade was never attempted — one duplicate aborted the group"
    )
    assert not [r for r in caplog.records if "Failed to sync trade group" in r.getMessage()], (
        "an already-present trade was reported as a failed group"
    )


def test_the_fallback_warning_does_not_misdiagnose_an_applied_migration(caplog):
    """The original message asserted 'migration 143 not applied' — which was FALSE in
    production and sent every reader hunting for an unapplied migration."""
    sb = _FakeSupabase(faults={
        ("whale_trades", "upsert"): [_no_inferable_index()],
        ("whale_trades", "insert"): [_unique_violation()],
    })

    with caplog.at_level(logging.DEBUG, logger=hw.logger.name):
        hw.WhaleHydrator._upsert_trade(sb, "w1", "tg1", {
            "ticker": "IBM", "action": "SOLD", "trade_type": "Sell",
            "amount": 1.0, "date": "2025-08-12",
        })

    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "PARTIAL" in msg or "partial" in msg, (
        "the warning does not mention the partial-index cause, which is the one that "
        "actually shipped"
    )

