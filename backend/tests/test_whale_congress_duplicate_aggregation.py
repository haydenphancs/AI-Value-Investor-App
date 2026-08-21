"""A disclosure repeated by the feed must not corrupt the portfolio.

`house-latest` is pulled 30 pages at a time from a feed that is being written to, and it
genuinely returns the same disclosure more than once. Measured against production
2026-08-21:

    Gilbert Cisneros   1091 raw rows -> 1032 distinct   (59 repeats)
    Josh Gottheimer     138 raw rows ->  136 distinct   ( 2 repeats)
    Nancy Pelosi         21 raw rows ->   21 distinct   ( 0 repeats)

`_aggregate_congressional` summed EVERY row into `holdings_accum[symbol]["value"]`, and
holdings are then filtered by `if h["value"] > 0`. So the damage is not merely inflation:

  ⚠️ A SALE disclosed twice can drive a real position to zero and DELETE it.

That is exactly what happened to Josh Gottheimer — one duplicated IFNNY sale cancelled a
genuine $8,000 holding, and the ticker disappeared from his portfolio. He was served 25
positions where 26 were real. A user cannot tell a silently-dropped position from one the
member never held.

Related: the same repeats moved the idempotency hash, which is what kept Cisneros
re-hydrating after the first fix — see test_whale_congress_hash_stability.py. Both now
share ONE definition of "the same disclosure" (`_congress_trade_identity`), so they cannot
disagree.

Pure logic — no network. Run via `python -m pytest` from backend/.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.hydrate_whales import WhaleHydrator
from app.services._whale_common import (
    congress_trade_identity as _congress_trade_identity,
    dedupe_congress_trades as _dedupe_congress_trades,
)


def _h():
    """`_aggregate_congressional` touches no instance state, so skip __init__/Supabase."""
    return object.__new__(WhaleHydrator)


def _t(symbol, typ, amount="$1,001 - $15,000", txn="2026-03-05", disc=None, **kw):
    d = {
        "symbol": symbol,
        "type": typ,
        "amount": amount,
        "transactionDate": txn,
        "disclosureDate": disc or txn,
        "owner": "Self",
        "assetDescription": f"{symbol} Inc",
        "firstName": "Josh",
        "lastName": "Gottheimer",
        "office": "Josh Gottheimer",
    }
    d.update(kw)
    return d


def _by_ticker(holdings):
    return {h["ticker"]: h["value"] for h in holdings}


# ── The production defect, in miniature ──────────────────────────────────────────────
def test_a_duplicated_sale_does_not_delete_a_real_position():
    """The Gottheimer / IFNNY case. This is the one that reached users.

    The position must net POSITIVE when counted correctly, and non-positive when the
    repeat is counted — otherwise `if h["value"] > 0` filters it out in both branches and
    the test passes while proving nothing. (It did exactly that on the first draft; the
    mutation run caught it.)
    """
    clean = [
        _t("IFNNY", "Purchase", txn="2026-03-01"),
        _t("IFNNY", "Purchase", txn="2026-03-05"),
        _t("IFNNY", "Sale", txn="2026-03-12"),
    ]
    duped = clean + [_t("IFNNY", "Sale", txn="2026-03-12")]   # the feed repeats the SALE

    h = _h()
    holdings_clean, _ = h._aggregate_congressional(clean, "2026-08-21")
    holdings_duped, _ = h._aggregate_congressional(duped, "2026-08-21")

    # Precondition: correctly counted, this position is real and visible.
    assert "IFNNY" in _by_ticker(holdings_clean), "test setup no longer nets positive"

    assert "IFNNY" in _by_ticker(holdings_duped), (
        "the duplicated sale DELETED a position the member actually holds"
    )
    assert _by_ticker(holdings_clean) == _by_ticker(holdings_duped)


def test_a_duplicated_purchase_does_not_inflate_a_position():
    clean = [_t("BBIO", "Purchase", amount="$15,001 - $50,000")]
    duped = clean + [_t("BBIO", "Purchase", amount="$15,001 - $50,000")]
    h = _h()
    assert _by_ticker(h._aggregate_congressional(duped, "2026-08-21")[0]) == \
           _by_ticker(h._aggregate_congressional(clean, "2026-08-21")[0])


def test_repeats_do_not_move_the_filing_group_net():
    clean = [_t("AAPL", "Purchase"), _t("MSFT", "Sale")]
    duped = clean + [_t("AAPL", "Purchase"), _t("MSFT", "Sale")]
    h = _h()
    _, g_clean = h._aggregate_congressional(clean, "2026-08-21")
    _, g_duped = h._aggregate_congressional(duped, "2026-08-21")
    assert len(g_clean) == len(g_duped)
    for a, b in zip(g_clean, g_duped):
        assert a.get("net_amount") == b.get("net_amount")
        assert a.get("net_amount_range") == b.get("net_amount_range")


def test_position_count_is_unchanged_by_repeats():
    """The headline symptom: 25 positions served where 26 were real."""
    clean = [_t(sym, "Purchase") for sym in ("AAPL", "MSFT", "NVDA")] + [
        _t("IFNNY", "Purchase", txn="2026-03-01"),
        _t("IFNNY", "Purchase", txn="2026-03-05"),
        _t("IFNNY", "Sale", txn="2026-03-12"),
    ]
    duped = clean + [_t("IFNNY", "Sale", txn="2026-03-12"), _t("AAPL", "Purchase")]
    h = _h()
    n_clean = len(h._aggregate_congressional(clean, "2026-08-21")[0])
    n_duped = len(h._aggregate_congressional(duped, "2026-08-21")[0])
    assert n_clean == 4, "test setup should yield 4 visible positions"
    assert n_duped == n_clean, (
        f"repeats changed the position count: {n_duped} served where {n_clean} are real"
    )


# ── Genuinely distinct trades must still all count ───────────────────────────────────
@pytest.mark.parametrize("field,value", [
    ("amount", "$50,001 - $100,000"),
    ("txn", "2026-04-01"),
    ("owner", "Spouse"),
])
def test_two_similar_but_distinct_trades_are_both_counted(field, value):
    """Dedupe must not swallow a real second trade."""
    a = _t("AAPL", "Purchase")
    kwargs = {"amount": a["amount"], "txn": a["transactionDate"]}
    if field == "owner":
        b = _t("AAPL", "Purchase", owner=value)
    else:
        kwargs[field] = value
        b = _t("AAPL", "Purchase", **{k: v for k, v in kwargs.items()})
    h = _h()
    one, _ = h._aggregate_congressional([a], "2026-08-21")
    two, _ = h._aggregate_congressional([a, b], "2026-08-21")
    assert _by_ticker(two)["AAPL"] > _by_ticker(one)["AAPL"], (
        f"a distinct trade differing in {field} was wrongly deduped away"
    )


def test_same_ticker_same_day_opposite_directions_both_count():
    """A buy and a sell of the same ticker on the same day are NOT duplicates."""
    rows = [_t("AAPL", "Purchase"), _t("AAPL", "Sale")]
    assert len(_dedupe_congress_trades(rows)) == 2


# ── The dedupe helper itself ─────────────────────────────────────────────────────────
def test_dedupe_preserves_first_seen_order():
    rows = [_t("A", "Purchase"), _t("B", "Purchase"), _t("A", "Purchase")]
    out = _dedupe_congress_trades(rows)
    assert [r["symbol"] for r in out] == ["A", "B"]


def test_dedupe_tolerates_malformed_rows():
    rows = [None, "junk", 42, _t("A", "Purchase"), _t("A", "Purchase")]
    out = _dedupe_congress_trades(rows)
    assert len(out) == 1 and out[0]["symbol"] == "A"
    assert _dedupe_congress_trades([]) == []


def test_identity_is_shared_with_the_hash():
    """One definition of 'the same disclosure'. If these ever diverge, the aggregation
    and the idempotency hash disagree about what changed."""
    from app.services._whale_common import CONGRESS_TRADE_FIELDS as _CONGRESS_HASH_FIELDS
    t = _t("AAPL", "Purchase")
    assert _congress_trade_identity(t) == "|".join(
        str(t.get(k) or "") for k in _CONGRESS_HASH_FIELDS
    )


def test_aggregation_actually_dedupes_not_just_the_helper():
    """Guard against the dedupe being dropped from `_aggregate_congressional` while the
    helper stays behind looking used."""
    rows = [_t("AAPL", "Purchase")] * 5
    h = _h()
    holdings, _ = h._aggregate_congressional(rows, "2026-08-21")
    single, _ = h._aggregate_congressional([_t("AAPL", "Purchase")], "2026-08-21")
    assert _by_ticker(holdings) == _by_ticker(single)


# ─────────────────────────────────────────────────────────────────────────────
# THE GUARD: the same property, applied to BOTH implementations.
#
# The two aggregations are deliberately different — one is a flat accumulation, the
# other a chronological walk reconstructing allocations — so their OUTPUTS cannot be
# compared to each other. What can be compared is each one against itself:
#
#     f(rows_with_duplicates) == f(deduped_rows)
#
# That invariant survives their intended divergence, which is what makes it a durable
# guard rather than a snapshot of today's behaviour. This is the fifth recurrence of
# "two copies of whale logic disagreed in production"; the point of these tests is that
# there is not a sixth.
# ─────────────────────────────────────────────────────────────────────────────
def _service():
    """`_aggregate_congressional_trades` touches no instance state either."""
    from app.services.whale_service import WhaleService
    return object.__new__(WhaleService)


def _dup_free_property(fn, rows, duplicated):
    """fn(duplicated) must equal fn(rows) on holdings."""
    a = fn(rows)
    b = fn(duplicated)
    ha = a[0] if isinstance(a, tuple) else a
    hb = b[0] if isinstance(b, tuple) else b
    return _by_ticker(ha), _by_ticker(hb)


@pytest.mark.parametrize("which", ["hydrate", "service"])
def test_both_paths_ignore_duplicate_disclosures(which):
    rows = [
        _t("AAPL", "Purchase", txn="2026-01-05"),
        _t("MSFT", "Purchase", txn="2026-01-06", amount="$50,001 - $100,000"),
        _t("NVDA", "Sale", txn="2026-02-02"),
    ]
    duplicated = rows + [dict(rows[0]), dict(rows[1]), dict(rows[1])]

    if which == "hydrate":
        fn = lambda r: _h()._aggregate_congressional(r, "2026-08-21")
    else:
        fn = lambda r: _service()._aggregate_congressional_trades(r, "2026-08-21")

    clean, duped = _dup_free_property(fn, rows, duplicated)
    assert clean == duped, (
        f"{which} path changed its portfolio when the feed repeated a disclosure"
    )


@pytest.mark.parametrize("which", ["hydrate", "service"])
def test_both_paths_survive_a_duplicated_sale_without_deleting_the_position(which):
    """The Gottheimer/IFNNY shape, applied to whichever path is under test."""
    rows = [
        _t("IFNNY", "Purchase", txn="2026-03-01"),
        _t("IFNNY", "Purchase", txn="2026-03-05"),
        _t("IFNNY", "Sale", txn="2026-03-12"),
    ]
    duplicated = rows + [dict(rows[-1])]

    if which == "hydrate":
        fn = lambda r: _h()._aggregate_congressional(r, "2026-08-21")
    else:
        fn = lambda r: _service()._aggregate_congressional_trades(r, "2026-08-21")

    clean, duped = _dup_free_property(fn, rows, duplicated)
    assert "IFNNY" in clean, f"{which}: test setup no longer nets positive"
    assert "IFNNY" in duped, f"{which}: the duplicated sale DELETED a real position"
    assert clean == duped


def test_both_writers_share_one_identity_function():
    """If either re-implements 'the same disclosure' locally, they can disagree again."""
    import app.services.whale_service as ws
    import scripts.hydrate_whales as hw
    from app.services import _whale_common as wc

    assert hw.dedupe_congress_trades is wc.dedupe_congress_trades
    assert ws.dedupe_congress_trades is wc.dedupe_congress_trades
    assert hw.congressional_raw_hash is wc.congressional_raw_hash
    assert ws.congressional_raw_hash is wc.congressional_raw_hash


def test_both_writers_share_one_trade_summary():
    """The same card rendered in two voices until this was collapsed."""
    import app.services.whale_service as ws
    import scripts.hydrate_whales as hw
    from app.services import _whale_common as wc

    assert hw._generate_trade_summary is wc.generate_trade_summary
    assert ws.generate_trade_summary is wc.generate_trade_summary


def test_hydration_path_can_emit_all_four_trade_types():
    """`New`/`Closed` were unreachable here, which emptied the iOS filter tabs for the
    entire congressional cohort and disabled the `new_positions >= 5` alert."""
    rows = [
        _t("AAPL", "Purchase", txn="2026-01-01"),
        _t("AAPL", "Purchase", txn="2026-02-01"),
        _t("MSFT", "Purchase", txn="2026-01-05"),
        _t("MSFT", "Sale", txn="2026-03-01"),
    ]
    _, groups = _h()._aggregate_congressional(rows, "2026-08-21")
    kinds = {tr["trade_type"] for g in groups for tr in g["trades"]}
    assert {"New", "Increased", "Closed"} <= kinds, f"only got {kinds}"
