"""Congressional idempotency hash must depend on the MEMBER's filings, nothing else.

The bug this pins, measured in production 2026-08-21:

    02:00:31  Gilbert Cisneros — done in 12.5s
    02:00:45  Josh Gottheimer  — done in 14.8s
    02:00:55  Nancy Pelosi     — done in  7.6s
    02:01:08  Ro Khanna        — done in 11.2s
    02:01:20  Dan Crenshaw     — done in 11.9s

Those five House members took the full `_persist` write path on EVERY sweep — 01:46, 02:00
and 02:15 the same night, and the same five at 17:00 the day before — while every Senate
member correctly logged "Skipping — data unchanged". Ted Cruz's stored hash was
byte-identical across 2026-07 and 2026-08; no House member's ever matched a fresh fetch.

Cause: the hash was `sha256(json.dumps(raw_trades[:50], sort_keys=True))`, and
`house-latest` is a GLOBAL 1000-row feed of every member that the client filters by name.

  * `sort_keys=True` sorts keys INSIDE each dict. It does NOT order the LIST — the name is
    a trap. Any reshuffle changed the hash.
  * `[:50]` took the first 50 *in feed order*, so a disclosure by ANY OTHER member shifted
    the window and changed this member's hash.

The House feed churns far faster than the Senate's, which is exactly why only House
members were affected. It was never a correctness bug — the data written was right — it
was ~60s of blocking writes, ~5x/day, forever, for data that had not changed. And since
these five are the only thing whale hydration writes outside 13F filing season, it was
also the source of the measured 4.12s API latency spikes.

Pure logic — no network. Run via `python -m pytest` from backend/.
"""

import hashlib
import json
import random
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.hydrate_whales import (
    _CONGRESS_HASH_MAX,
    _congressional_raw_hash,
)


def _trade(txn, sym, typ="Purchase", amount="$1,001 - $15,000", owner="Self", **kw):
    """A congressional disclosure in the shape FMP actually returns."""
    base = {
        "transactionDate": txn,
        "disclosureDate": txn,
        "symbol": sym,
        "type": typ,
        "amount": amount,
        "owner": owner,
        "assetDescription": f"{sym} Inc",
        "assetType": "Stock",
        "capitalGainsOver200USD": "False",
        "comment": "",
        "district": "CA11",
        "firstName": "Nancy",
        "lastName": "Pelosi",
        "office": "Nancy Pelosi",
        "senateID": "P000197",
        "link": f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{sym}.pdf",
    }
    base.update(kw)
    return base


def _many(n, start_year=2020):
    """n distinct trades, chronological, enough to exceed the newest-N window."""
    return [
        _trade(f"{start_year + i // 300:04d}-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
               f"SYM{i:03d}")
        for i in range(n)
    ]


# ── The two properties the old hash lacked ───────────────────────────────────────────
def test_feed_order_does_not_change_the_hash():
    """The headline fix. `sort_keys=True` never provided this."""
    trades = _many(80)
    base = _congressional_raw_hash(trades)
    for seed in range(5):
        shuffled = random.Random(seed).sample(trades, len(trades))
        assert _congressional_raw_hash(shuffled) == base, (
            f"feed reshuffle (seed {seed}) changed the hash — the every-sweep rewrite is back"
        )


def test_old_trades_ageing_out_of_the_shared_feed_do_not_change_the_hash():
    """The global feed is capped at 1000 rows. As other members file, a member's OLDEST
    disclosures fall off the end. That is not a change to what they filed."""
    trades = _many(_CONGRESS_HASH_MAX + 30)
    base = _congressional_raw_hash(trades)
    oldest_first = sorted(trades, key=lambda t: t["transactionDate"])
    for dropped in (1, 5, 20, 30):
        assert _congressional_raw_hash(oldest_first[dropped:]) == base, (
            f"dropping {dropped} of the oldest trades changed the hash"
        )


def test_a_member_with_fewer_trades_than_the_window_is_fully_covered():
    """Below the window every trade counts — including the oldest. Losing one IS a change
    for them, and that is the documented trade-off, not an oversight."""
    trades = _many(10)
    assert len(trades) < _CONGRESS_HASH_MAX
    assert _congressional_raw_hash(trades[1:]) != _congressional_raw_hash(trades)


# ── Real changes must still be detected ──────────────────────────────────────────────
@pytest.mark.parametrize("field,value", [
    ("symbol", "ZZZZ"),
    ("type", "Sale"),
    ("amount", "$1,000,001 - $5,000,000"),
    ("owner", "Spouse"),
    ("transactionDate", "2099-01-01"),
    ("disclosureDate", "2099-01-01"),
    ("assetDescription", "Something Else Inc"),
])
def test_a_real_change_still_moves_the_hash(field, value):
    trades = _many(60)
    mutated = [dict(trades[-1], **{field: value})] + trades[:-1]
    assert _congressional_raw_hash(mutated) != _congressional_raw_hash(trades), (
        f"a changed {field} was not detected — a real disclosure would be silently skipped"
    )


def test_a_brand_new_disclosure_moves_the_hash():
    trades = _many(60)
    assert _congressional_raw_hash(
        trades + [_trade("2099-12-31", "NEWCO")]
    ) != _congressional_raw_hash(trades)


def test_link_is_not_hashed():
    """`link` is a PDF URL the Clerk can re-issue; it says nothing about the trade."""
    trades = _many(20)
    relinked = [dict(t, link=t["link"] + "?v=2") for t in trades]
    assert _congressional_raw_hash(relinked) == _congressional_raw_hash(trades)


# ── Degenerate inputs must not throw ─────────────────────────────────────────────────
def test_malformed_rows_are_tolerated():
    assert _congressional_raw_hash([]) == _congressional_raw_hash([])
    # Non-dicts filtered rather than crashing the whole sweep.
    assert _congressional_raw_hash([None, "junk", 42, _trade("2026-01-01", "A")]) == \
           _congressional_raw_hash([_trade("2026-01-01", "A")])
    # Missing / None fields render as "" instead of raising.
    _congressional_raw_hash([{"symbol": None}, {}, {"type": "Purchase"}])


def test_duplicate_rows_are_deterministic():
    t = _trade("2026-01-01", "AAPL")
    assert _congressional_raw_hash([t, dict(t)]) == _congressional_raw_hash([dict(t), t])
    # ...and a duplicate is not silently collapsed into a single entry.
    assert _congressional_raw_hash([t, dict(t)]) != _congressional_raw_hash([t])


def test_is_deterministic_across_calls():
    trades = _many(70)
    assert len({_congressional_raw_hash(trades) for _ in range(10)}) == 1


# ── Regression guard on the source ───────────────────────────────────────────────────
def _congress_processor_source() -> str:
    """`_process_congressional` only, comments and docstrings stripped.

    Stripping matters: `_congressional_raw_hash`'s own docstring quotes the old formula
    verbatim to explain it, so an un-stripped scan of the file would match that prose and
    pass after a revert.
    """
    src = (Path(__file__).resolve().parents[1] / "scripts" / "hydrate_whales.py").read_text()
    start = src.index("    async def _process_congressional(")
    nxt = src.index("\n    async def ", start + 10)
    body = src[start:nxt]
    body = re.sub(r'"""(?:.|\n)*?"""', "", body)
    body = re.sub(r"(?m)^\s*#.*$", "", body)
    return body


def test_processor_uses_the_stable_helper():
    body = _congress_processor_source()
    assert "_congressional_raw_hash(raw_trades)" in body


def test_the_unsorted_first_fifty_formula_has_not_returned():
    body = _congress_processor_source()
    assert "raw_trades[:50]" not in body, (
        "the feed-order-dependent hash is back — House members will rewrite every sweep"
    )
    assert "hashlib.sha256" not in body, (
        "the hash is being computed inline again instead of via the tested helper"
    )
