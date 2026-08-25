"""Backend↔iOS contract for the credit history statement, plus the paging contract.

`CreditHistoryResponse` is decoded by `CreditTransactionDTO` / `CreditHistoryDTO` in
`frontend/ios/ios/Models/CreditHistoryModels.swift`. Swift's JSONDecoder is strict, so a
renamed or newly-non-optional field here is a decode CRASH on a money screen — not a
missing row. These tests pin the shape before the app does.

The paging half is here rather than in a separate file because the cursor IS part of the
wire contract: a stalled or repeating cursor makes the client re-request forever.

No Supabase, no network — the client is faked.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.credit_history import (
    CreditHistoryResponse,
    CreditTransactionResponse,
)
from app.services.credit_history_service import (
    DEFAULT_PAGE,
    MAX_PAGE,
    CreditHistoryService,
    CreditHistoryUnavailable,
    describe_transaction,
)

# Keys the iOS decoder reads. A rename on either side breaks the screen.
_ITEM_KEYS = {
    "id", "created_at", "delta", "kind", "title", "subtitle",
    "pool_note", "is_reversed", "reason",
}
_ENVELOPE_KEYS = {"items", "next_cursor"}


# ── fake Supabase ────────────────────────────────────────────────────────────


class _FakeQuery:
    """Order-INDEPENDENT, because PostgREST is.

    `postgrest-py` serializes every filter and the limit into one query string, so the
    order the builder methods are called in does not affect the result — Postgres applies
    WHERE before LIMIT regardless. An eager `limit()` in this fake would truncate before a
    later `.lt()` filter ran and report a paging bug that cannot happen in production.
    So the limit is deferred to `execute()`, like the real thing.
    """

    def __init__(self, rows, log):
        self._rows = rows
        self._log = log
        self._limit = None

    def select(self, *a, **k):
        self._log.append(("select", a))
        return self

    def eq(self, col, val):
        self._log.append(("eq", col, val))
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def in_(self, col, vals):
        self._log.append(("in_", col, list(vals)))
        wanted = {str(v) for v in vals}
        self._rows = [r for r in self._rows if str(r.get(col)) in wanted]
        return self

    def lt(self, col, val):
        self._log.append(("lt", col, val))
        self._rows = [r for r in self._rows if int(r.get(col)) < int(val)]
        return self

    def order(self, col, desc=False):
        self._log.append(("order", col, desc))
        self._rows = sorted(self._rows, key=lambda r: r.get(col) or 0, reverse=desc)
        return self

    def limit(self, n):
        self._log.append(("limit", n))
        self._limit = n
        return self

    def execute(self):
        rows = self._rows if self._limit is None else self._rows[: self._limit]
        return type("R", (), {"data": list(rows)})()


class _FakeSupabase:
    def __init__(self, tables, fail_on=()):
        self.tables = tables
        self.fail_on = set(fail_on)
        self.log = []

    def table(self, name):
        self.log.append(("table", name))
        if name in self.fail_on:
            raise RuntimeError(f"simulated outage reading {name}")
        return _FakeQuery(list(self.tables.get(name, [])), self.log)


def _service(tables, fail_on=()):
    svc = CreditHistoryService.__new__(CreditHistoryService)  # skip get_supabase()
    svc.supabase = _FakeSupabase(tables, fail_on=fail_on)
    return svc


def _ledger_row(row_id, **over):
    row = {
        "id": row_id,
        "user_id": "u1",
        "delta": -1,
        "reason": "chat_charge",
        "ref_id": f"sess-{row_id}:abcd",
        "created_at": datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc),
        "granted_delta": -1,
        "purchased_delta": 0,
    }
    row.update(over)
    return row


# ── shape ────────────────────────────────────────────────────────────────────


def test_worst_case_row_still_validates_as_the_response_model():
    """Every nullable column NULL, an unknown split, an unmapped reason, no timestamp."""
    described = describe_transaction(
        {
            "id": 1,
            "delta": -20,
            "reason": None,
            "ref_id": None,
            "created_at": None,
            "granted_delta": None,
            "purchased_delta": None,
        }
    )
    payload = CreditHistoryResponse(items=[described], next_cursor=None).model_dump()
    revalidated = CreditHistoryResponse.model_validate(payload)
    assert len(revalidated.items) == 1


def test_item_keys_are_exactly_what_ios_decodes():
    keys = set(CreditTransactionResponse(id="1").model_dump().keys())
    assert keys == _ITEM_KEYS, (
        "iOS CreditTransactionDTO decodes these keys; a rename here is a decode crash"
    )


def test_envelope_keys_are_exactly_what_ios_decodes():
    assert set(CreditHistoryResponse().model_dump().keys()) == _ENVELOPE_KEYS


def test_non_optional_fields_survive_an_empty_construction():
    """`id` is the only required field; everything else must default so an older iOS
    build keeps decoding when a new column is added server-side."""
    item = CreditTransactionResponse(id="1")
    assert item.delta == 0 and item.kind == "other" and item.is_reversed is False
    assert item.subtitle is None and item.pool_note is None


def test_empty_response_is_a_valid_page_not_a_null():
    empty = CreditHistoryResponse()
    assert empty.items == [] and empty.next_cursor is None


def test_timestamps_cross_the_wire_as_strings_never_datetime():
    described = describe_transaction(_ledger_row(1))
    assert isinstance(described.created_at, str)
    assert not isinstance(described.created_at, datetime)


def test_id_and_cursor_are_strings_so_bigint_width_never_matters():
    described = describe_transaction(_ledger_row(9223372036854775807))
    assert isinstance(described.id, str)
    svc = _service({"credit_transactions": [_ledger_row(i) for i in range(1, 6)]})
    page = svc.list_for_user("u1", limit=2)
    assert isinstance(page.next_cursor, str)


# ── paging ───────────────────────────────────────────────────────────────────


def test_page_is_newest_first_and_cursor_points_at_the_last_row():
    rows = [_ledger_row(i) for i in range(1, 11)]
    page = _service({"credit_transactions": rows}).list_for_user("u1", limit=3)
    assert [i.id for i in page.items] == ["10", "9", "8"]
    assert page.next_cursor == "8"


def test_the_cursor_walks_the_whole_ledger_without_repeating_or_skipping():
    rows = [_ledger_row(i) for i in range(1, 11)]
    svc = _service({"credit_transactions": rows})
    seen, cursor = [], None
    for _ in range(10):
        page = svc.list_for_user("u1", limit=3, before=cursor)
        seen.extend(i.id for i in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == [str(i) for i in range(10, 0, -1)]
    assert len(seen) == len(set(seen)), "a page boundary repeated a row"


def test_last_page_reports_no_cursor():
    rows = [_ledger_row(i) for i in range(1, 4)]
    page = _service({"credit_transactions": rows}).list_for_user("u1", limit=3)
    assert len(page.items) == 3
    assert page.next_cursor is None, "exactly-full last page must not advertise another"


def test_limit_is_clamped_rather_than_rejected():
    rows = [_ledger_row(i) for i in range(1, 200)]
    svc = _service({"credit_transactions": rows})
    assert len(svc.list_for_user("u1", limit=99999).items) == MAX_PAGE
    assert len(svc.list_for_user("u1", limit=0).items) == 1
    assert len(svc.list_for_user("u1", limit=-5).items) == 1
    assert len(svc.list_for_user("u1", limit=None).items) == DEFAULT_PAGE


def test_a_junk_cursor_is_ignored_rather_than_500ing():
    rows = [_ledger_row(i) for i in range(1, 6)]
    page = _service({"credit_transactions": rows}).list_for_user("u1", before="not-a-number")
    assert len(page.items) == 5


def test_rows_are_scoped_to_the_caller():
    """The service-role key bypasses RLS, so this in-code filter is the wall."""
    rows = [_ledger_row(1), _ledger_row(2, user_id="someone-else")]
    page = _service({"credit_transactions": rows}).list_for_user("u1")
    assert [i.id for i in page.items] == ["1"]


def test_a_malformed_row_is_skipped_without_stalling_the_cursor():
    """A skipped row must not become the cursor — that would re-request forever."""
    rows = [_ledger_row(i) for i in range(1, 6)]
    rows[-1]["id"] = None  # id=5 is unusable; it sorts last so it would be the cursor
    page = _service({"credit_transactions": rows}).list_for_user("u1", limit=2)
    assert page.next_cursor is not None
    assert all(i.id for i in page.items)


# ── failure contract ─────────────────────────────────────────────────────────


def test_a_read_failure_raises_rather_than_serving_an_empty_page():
    """An empty statement and a broken statement look identical to a user — and this is
    the screen someone opens when they already believe their credits are wrong."""
    svc = _service({"credit_transactions": []}, fail_on={"credit_transactions"})
    with pytest.raises(CreditHistoryUnavailable):
        svc.list_for_user("u1")


def test_enrichment_failures_degrade_the_row_but_never_the_page():
    """The reversal probe and the pack-name lookup are decoration on top of the ledger."""
    rows = [
        _ledger_row(1, delta=-20, reason="report_charge", ref_id="ORCL"),
        _ledger_row(2, delta=540, reason="pack_purchase", ref_id="2000000812345678",
                    granted_delta=0, purchased_delta=540),
    ]
    svc = _service(
        {"credit_transactions": rows},
        fail_on={"credit_purchases", "credit_packs"},
    )
    page = svc.list_for_user("u1")
    assert len(page.items) == 2
    pack = next(i for i in page.items if i.reason == "pack_purchase")
    assert pack.subtitle is None
    assert "2000000812345678" not in (pack.title + (pack.subtitle or ""))


# ── enrichment correctness ───────────────────────────────────────────────────


def test_a_reversed_debit_is_flagged_and_an_unreversed_one_is_not():
    charge_a = _ledger_row(1, delta=-1, reason="chat_charge")
    charge_b = _ledger_row(2, delta=-1, reason="chat_charge")
    refund = _ledger_row(3, delta=1, reason="chat_cache_hit", reverses_id=1)
    page = _service({"credit_transactions": [charge_a, charge_b, refund]}).list_for_user("u1")
    by_id = {i.id: i for i in page.items}
    assert by_id["1"].is_reversed is True
    assert by_id["2"].is_reversed is False
    assert by_id["3"].is_reversed is False, "a credit is not itself reversed"


def test_pack_rows_resolve_their_display_name_through_credit_purchases():
    rows = [_ledger_row(1, delta=540, reason="pack_purchase", ref_id="2000000812345678",
                        granted_delta=0, purchased_delta=540)]
    svc = _service({
        "credit_transactions": rows,
        "credit_purchases": [
            # `user_id` is present because the lookup is scoped to the caller, the same
            # IDOR rule every other query in this codebase follows.
            {"user_id": "u1", "transaction_id": "2000000812345678",
             "product_id": "com.phan.caydex.credits.power"},
            # Another account's purchase carrying the SAME transaction id must not leak a
            # name into this user's statement.
            {"user_id": "someone-else", "transaction_id": "2000000812345678",
             "product_id": "com.phan.caydex.credits.mega"},
        ],
        "credit_packs": [
            {"product_id": "com.phan.caydex.credits.power", "display_name": "Power"}
        ],
    })
    item = svc.list_for_user("u1").items[0]
    assert item.subtitle == "Power"
    assert item.pool_note == "Never expires"


def test_enrichment_never_reads_another_users_rows():
    """Both enrichment queries filter on ids; without the user scope they would be an IDOR.

    The service-role key bypasses RLS, so the in-code filter is the only wall.
    """
    charge = _ledger_row(1, delta=-1, reason="chat_charge")
    # Another account's refund pointing at OUR debit id must not mark it reversed.
    foreign_refund = _ledger_row(2, delta=1, reason="chat_refund",
                                 user_id="someone-else", reverses_id=1)
    page = _service({"credit_transactions": [charge, foreign_refund]}).list_for_user("u1")
    assert [i.id for i in page.items] == ["1"]
    assert page.items[0].is_reversed is False


def test_no_pack_lookup_happens_when_the_page_has_no_pack_rows():
    """Two extra queries per page is fine; two extra queries per page for nothing is not."""
    svc = _service({"credit_transactions": [_ledger_row(1)]})
    svc.list_for_user("u1")
    touched = {name for kind, name, *_ in svc.supabase.log if kind == "table"}
    assert "credit_purchases" not in touched
    assert "credit_packs" not in touched


def test_no_reversal_probe_happens_when_the_page_has_no_debits():
    rows = [_ledger_row(1, delta=100, reason="monthly_reset", granted_delta=100)]
    svc = _service({"credit_transactions": rows})
    svc.list_for_user("u1")
    assert not any(kind == "in_" for kind, *_ in svc.supabase.log)
