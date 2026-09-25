"""The 13F "latest filed quarter" floor — one rule, two copies, pinned together.

A 13F row's `whale_trades.date` is the QUARTER END its filing describes (FMP
`institutional-ownership/dates`), never the day it was filed, and the filing is due 45
days after that quarter ends. Two surfaces gate 13F rows on it: the smart-money push
(`smart_money_sender._recent_whale_rows`) and the Tracking "Whales Bought/Sold this week"
card (`tracking_service._get_whale_trade_alerts`). Both used a day window on that
quarter-end date and so dropped real filings (C13/C14, 2026-09-25): the push lost every
deadline-day filer, the card lost EVERY 13F row.

Each module carries its own `_thirteen_f_floor` (the natural shared home,
`app/utils/period_labels.py` next to `latest_filed_13f_quarter`, was outside that change's
scope). This file makes the two impossible to drift apart, and states the rule as a
property over every day rather than a handful of examples.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.services import tracking_service as tsvc
from app.services.notification_senders import smart_money_sender as sm

_ENDS = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _quarter(d: date):
    return d.year, (d.month - 1) // 3 + 1


def _shift(year: int, q: int, by: int):
    idx = year * 4 + (q - 1) + by
    return idx // 4, idx % 4 + 1


def _end(year: int, q: int) -> date:
    return date(year, *_ENDS[q])


def _every_day(start=date(2024, 12, 20), days=3 * 366):
    return (start + timedelta(days=i) for i in range(days))


def test_the_two_copies_agree_on_every_day():
    diffs = [d for d in _every_day() if sm._thirteen_f_floor(d) != tsvc._thirteen_f_floor(d)]
    assert not diffs, f"the push and the Tracking card disagree on {diffs[:3]}"


def test_the_latest_filed_quarter_is_always_in_and_the_one_before_always_out():
    for d in _every_day():
        floor = sm._thirteen_f_floor(d)
        prev_y, prev_q = _shift(*_quarter(d), -1)
        older_y, older_q = _shift(*_quarter(d), -2)
        prev_end, older_end = _end(prev_y, prev_q), _end(older_y, older_q)
        assert prev_end.isoformat() >= floor, f"{d}: the latest filed quarter ({prev_end}) is dropped"
        # The hydrators' fallback date (`{year}-{q*3:02d}-30`) must be in too.
        fallback = date(prev_y, prev_q * 3, 30).isoformat()
        assert fallback >= floor, f"{d}: the fallback date {fallback} is dropped"
        assert older_end.isoformat() < floor, f"{d}: an older quarter ({older_end}) is kept"


def test_the_sender_derives_the_same_floor_from_its_cutoff():
    """`_recent_whale_rows` receives only `cutoff_date` (run date − 45 days) and derives the
    run date from it; a 13F row exactly on the floor is kept, the day before is not."""
    for d in _every_day(days=400):
        cutoff = (d - timedelta(days=sm.WHALE_TRADE_MAX_AGE_DAYS)).isoformat()
        floor = date.fromisoformat(sm._thirteen_f_floor(d))
        on = {"ticker": "ON", "date": floor.isoformat()}
        before = {"ticker": "BEFORE", "date": (floor - timedelta(days=1)).isoformat()}
        kept = sm._recent_whale_rows([on, before], cutoff_date=cutoff)
        assert [r["ticker"] for r in kept] == ["ON"], d
