"""A batch row's change belongs to a SESSION; consumers that act on a move must ask which.

`PriceService._change_session` stamps every batch row with the ISO date of the session
its `changePercentage` describes. Display surfaces use the stamp to label a pre-market
"Fri close -4.8%"; the two consumers that ACT on a move — the Updates sweeper's
`ticker_move` alert + catalyst, and the daily `percent_move` price alert — used to read
the raw field and treat yesterday's whole session as today's move.

Production, 2026-09-15 (TestFlight): TER fired "TER -10.4%" at 09:57 ET Monday (read
nine minutes later), then "TER -13.3%" at 04:03 ET TUESDAY — the first pre-market pass,
where the screener still carried Monday's close, so the row's change was Monday's whole
session under Tuesday's per-day dedup key. The cleared alert came back as "×2".

These pin the two helpers and the end-to-end shape of the row `_from_screener` builds.
"""

from datetime import date, datetime, timezone

import pytest

from app.services.price_service import (
    PriceService,
    current_session_quote,
    session_change_percent,
)

# Monday 2026-09-14 closed; these instants are Tuesday 04:03 ET (pre-market, before the
# first print) and Tuesday 09:35 ET (regular session), and Monday 19:00 ET (after hours).
TUE_0403 = datetime(2026, 9, 15, 8, 3, 56, tzinfo=timezone.utc)
TUE_0935 = datetime(2026, 9, 15, 13, 35, 0, tzinfo=timezone.utc)
MON_1900 = datetime(2026, 9, 14, 23, 0, 0, tzinfo=timezone.utc)


def _row(**over):
    base = {
        "symbol": "TER", "price": 371.47, "change": -57.0, "changePercentage": -13.3,
        "changesPercentage": -13.3, "previousClose": 428.47, "changeSession": "2026-09-14",
    }
    base.update(over)
    return base


# ── session_change_percent ────────────────────────────────────────────────────

def test_a_prior_session_change_reads_as_none_pre_market():
    assert session_change_percent(_row(), TUE_0403) is None


def test_the_same_session_change_reads_through_after_hours():
    assert session_change_percent(_row(), MON_1900) == -13.3


def test_a_current_session_change_reads_through():
    assert session_change_percent(_row(changeSession="2026-09-15", changePercentage=-6.1), TUE_0935) == -6.1


@pytest.mark.parametrize("stamp", [None, "", "not-a-date", "2026-13-45", "   "])
def test_a_missing_or_unreadable_stamp_fails_open(stamp):
    """Crypto (a rolling 24h change, no session) and older row shapes carry no usable
    stamp; they keep today's behaviour rather than silently losing every alert."""
    row = _row(changePercentage=5.0)
    if stamp is None:
        row.pop("changeSession")
    else:
        row["changeSession"] = stamp
    assert session_change_percent(row, TUE_0403) == 5.0


def test_a_future_stamp_is_treated_as_current():
    """Clock skew between the ingest and the sweeper is not a stale move."""
    assert session_change_percent(_row(changeSession="2026-09-16"), TUE_0403) == -13.3


def test_a_naive_now_is_read_as_utc():
    """`session_trading_date` reads a naive datetime as UTC (never local), so the same
    input resolves identically on Railway and on a dev machine."""
    naive = datetime(2026, 9, 15, 8, 3, 56)
    assert session_change_percent(_row(), naive) is None


@pytest.mark.parametrize("junk", [None, "TER", 3, ["TER"]])
def test_a_non_dict_quote_is_none(junk):
    assert session_change_percent(junk, TUE_0403) is None


@pytest.mark.parametrize("bad", [None, "nan", float("nan"), float("inf"), "x"])
def test_a_non_finite_change_is_none_even_when_current(bad):
    assert session_change_percent(_row(changeSession="2026-09-15", changePercentage=bad), TUE_0935) is None


def test_a_current_zero_change_is_a_real_zero_not_none():
    """0.0 is a reading (the callers' own `round(cp, 2) == 0.0` gate handles it);
    None means unknown. The two must stay distinct (invariant 1)."""
    assert session_change_percent(_row(changeSession="2026-09-15", changePercentage=0.0), TUE_0935) == 0.0


# ── current_session_quote ─────────────────────────────────────────────────────

def test_a_prior_session_row_is_blanked_but_keeps_its_price_and_stamp():
    row = _row()
    out = current_session_quote(row, TUE_0403)
    assert out is not row, "must be a copy — the batch row is shared with other readers"
    assert out["change"] is None and out["changePercentage"] is None and out["changesPercentage"] is None
    assert out["price"] == 371.47 and out["previousClose"] == 428.47
    assert out["changeSession"] == "2026-09-14"
    assert row["changePercentage"] == -13.3, "the input row must not be mutated"


def test_a_current_session_row_is_returned_as_is():
    row = _row(changeSession="2026-09-15")
    assert current_session_quote(row, TUE_0935) is row


def test_an_unstamped_row_is_returned_as_is():
    row = _row()
    row.pop("changeSession")
    assert current_session_quote(row, TUE_0403) is row


def test_blanking_never_adds_keys_a_row_did_not_have():
    row = {"symbol": "X", "price": 1.0, "changePercentage": 2.0, "changeSession": "2026-09-14"}
    out = current_session_quote(row, TUE_0403)
    assert set(out) == set(row)


# ── the row `_from_screener` builds pre-market really is stamped with the prior day ──

def test_a_pre_market_screener_row_is_stamped_with_the_prior_session_and_neutralised():
    """04:03 ET on the session after a close: the screener price IS that close (no print
    yet), the snapshot holds that close + the one before, so `_pick_denominator` divides
    by the earlier close and the change is the PRIOR session's whole move — stamped with
    its date by `_change_session`. That row must reach the sweeper's gates with no change.

    `_from_screener` judges snapshot freshness on the real clock, so the dates are built
    relative to it: the stored close is the session before the current one.
    """
    from app.utils.market_hours import ET, previous_trading_day, session_trading_date

    today_session = session_trading_date()
    stored = previous_trading_day(today_session)
    snap = {"close": 371.47, "previous_close": 428.47, "trade_date": stored.isoformat()}
    row = PriceService._from_screener({"symbol": "TER", "price": 371.47, "companyName": "Teradyne"}, snap)
    assert row["changeSession"] == stored.isoformat()
    assert round(row["changePercentage"], 1) == -13.3          # the prior session's move, as shipped

    pre_market = datetime(today_session.year, today_session.month, today_session.day, 4, 3, tzinfo=ET)
    assert session_change_percent(row, pre_market) is None       # …and not this session's
    assert current_session_quote(row, pre_market)["changePercentage"] is None
    # The same row read that evening IS that session's move.
    that_evening = datetime(stored.year, stored.month, stored.day, 19, 0, tzinfo=ET)
    assert session_change_percent(row, that_evening) == row["changePercentage"]


def test_the_helper_agrees_with_session_trading_date_on_the_boundary():
    """The stamp is compared against `session_trading_date(now)`, not the calendar
    day: at 03:00 ET Tuesday the session is still Monday's, so Monday's stamp is
    current there and only becomes stale once the Tuesday session opens at 04:00."""
    from app.utils.market_hours import session_trading_date

    tue_0300 = datetime(2026, 9, 15, 7, 0, 0, tzinfo=timezone.utc)
    assert session_trading_date(tue_0300) == date(2026, 9, 14)
    assert session_change_percent(_row(), tue_0300) == -13.3
    assert session_change_percent(_row(), TUE_0403) is None
