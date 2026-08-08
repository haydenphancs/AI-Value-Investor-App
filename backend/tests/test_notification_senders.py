"""Sender SELECTION logic — the half where the bugs actually live.

None of these tests touch FMP, Supabase or APNs. They feed synthetic upstream payloads
to the pure selectors and assert what comes out, because "which rows deserve a
notification" is the decision that is wrong in production while the APNs POST works
fine.

Two failures this file exists to prevent are already on the record in this repo:

  * the `or ""` operator-precedence bug, which turns a date filter into a no-op and
    would announce a fund's entire historical book as this week's activity;
  * a NaN/None arithmetic input silently answering False for every comparison, which
    DISABLES a materiality gate rather than tripping it.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.notification_kinds import KIND_CONGRESS_TRADE, KIND_WHALE_13F
from app.services.notification_senders.earnings_sender import (
    MIN_SURPRISE,
    result_copy,
    select_results,
    select_upcoming,
    surprise_pct,
    upcoming_copy,
)
from app.services.notification_senders.smart_money_sender import (
    MIN_INSIDER_AMOUNT,
    _max_created_at,
    _recent_whale_rows,
    _whale_kind,
    insider_copy,
    notable_insider_trade,
    whale_copy,
)

TODAY = date(2026, 8, 7)
TOMORROW = TODAY + timedelta(days=1)
YESTERDAY = TODAY - timedelta(days=1)


# ══ EARNINGS ═════════════════════════════════════════════════════════════════

def _cal(symbol="AAPL", when=None, time="amc", est=None, act=None):
    row = {"symbol": symbol, "date": (when or TOMORROW).isoformat(), "time": time}
    if est is not None:
        row["epsEstimated"] = est
    if act is not None:
        row["epsActual"] = act
    return row


# ── upcoming ─────────────────────────────────────────────────────────────────

def test_tomorrows_reporters_are_selected():
    picked = select_upcoming([_cal("AAPL"), _cal("MSFT")], TODAY)
    assert {p[0] for p in picked} == {"AAPL", "MSFT"}


def test_todays_reporters_are_NOT_announced_as_upcoming():
    """The job runs after the close, so a today-dated row has already happened. Telling
    someone to expect a print that reported eight hours ago is worse than silence."""
    assert select_upcoming([_cal("AAPL", when=TODAY, time="bmo"),
                            _cal("MSFT", when=TODAY, time="amc")], TODAY) == []


def test_next_weeks_reporters_are_not_upcoming_yet():
    assert select_upcoming([_cal(when=TODAY + timedelta(days=6))], TODAY) == []


def test_a_symbol_listed_twice_produces_one_notification():
    """FMP can carry two rows for one company across a reschedule. Two banners for one
    earnings date is the noise that gets notifications switched off."""
    picked = select_upcoming([_cal("AAPL", time="amc"), _cal("AAPL", time="bmo")], TODAY)
    assert len(picked) == 1


@pytest.mark.parametrize("bad", [
    {"symbol": "AAPL"},                                    # no date at all
    {"symbol": "AAPL", "date": ""},
    {"symbol": "AAPL", "date": "not-a-date"},
    {"symbol": "AAPL", "date": "08/08/2026"},              # non-ISO
    {"symbol": "", "date": "2026-08-08"},                  # no symbol
    {"date": "2026-08-08"},                                # no symbol key
])
def test_unusable_calendar_rows_are_skipped_not_crashed(bad):
    assert select_upcoming([bad], TODAY) == []


def test_a_full_timestamp_date_is_normalised():
    row = {"symbol": "AAPL", "date": f"{TOMORROW.isoformat()}T00:00:00.000Z", "time": "amc"}
    picked = select_upcoming([row], TODAY)
    assert picked and picked[0][1] == TOMORROW.isoformat()


@pytest.mark.parametrize("raw,expect_phrase", [
    ("bmo", "before market open"),
    ("amc", "after market close"),
    ("", None),
    ("--", None),
    ("garbage", None),
])
def test_unknown_timing_tokens_degrade_to_no_phrase(raw, expect_phrase):
    """`timing_sentence` returns None for unspecified so the clause is DROPPED rather
    than defaulted to 'after market close' — a guess here is misinformation."""
    picked = select_upcoming([_cal(time=raw)], TODAY)
    _, body = upcoming_copy("AAPL", picked[0][2])
    if expect_phrase:
        assert expect_phrase in body
    else:
        assert body == "AAPL is scheduled to report tomorrow."


# ── surprise math ────────────────────────────────────────────────────────────

def test_a_clean_beat_is_a_positive_fraction():
    assert surprise_pct({"epsEstimated": 1.00, "epsActual": 1.20}) == pytest.approx(0.20)


def test_a_miss_is_negative():
    assert surprise_pct({"epsEstimated": 1.00, "epsActual": 0.80}) == pytest.approx(-0.20)


def test_a_negative_estimate_uses_the_MAGNITUDE_as_the_denominator():
    """A loss-making company narrowing a -$1.00 estimate to -$0.50 is a 50% BEAT.
    Dividing by the signed estimate would report -50% and invert the verb."""
    assert surprise_pct({"epsEstimated": -1.00, "epsActual": -0.50}) == pytest.approx(0.5)


@pytest.mark.parametrize("est,act", [
    (None, 1.0), (1.0, None), (None, None),
    (float("nan"), 1.0), (1.0, float("nan")),
    (float("inf"), 1.0), (1.0, float("-inf")),
    ("n/a", 1.0), (1.0, "n/a"),
])
def test_a_missing_or_non_finite_side_yields_no_surprise(est, act):
    """None, not 0.0. FMP emits NaN/Infinity JSON tokens on thin names, and NaN silently
    answers False for every comparison — DISABLING the materiality gate rather than
    tripping it."""
    assert surprise_pct({"epsEstimated": est, "epsActual": act}) is None


@pytest.mark.parametrize("est", [0.0, 0.001, -0.001, 0.009])
def test_a_near_zero_estimate_has_no_honest_percentage(est):
    """`est != 0` is not enough: $0.001 vs $0.40 is a 39,900% 'surprise' — arithmetically
    true and completely meaningless."""
    assert surprise_pct({"epsEstimated": est, "epsActual": 0.40}) is None


def test_an_exactly_at_the_floor_estimate_still_divides():
    assert surprise_pct({"epsEstimated": 0.01, "epsActual": 0.02}) == pytest.approx(1.0)


# ── results ──────────────────────────────────────────────────────────────────

def test_a_material_surprise_is_selected():
    picked = select_results([_cal(when=TODAY, est=1.0, act=1.5)], TODAY)
    assert picked and picked[0][0] == "AAPL"


def test_an_in_line_print_is_not_news():
    """Analysts cluster tightly around consensus; a 1% beat is a rounding difference."""
    assert select_results([_cal(when=TODAY, est=1.00, act=1.01)], TODAY) == []


def test_the_materiality_floor_is_inclusive_at_the_boundary():
    below = select_results([_cal(when=TODAY, est=1.0, act=1.0 + MIN_SURPRISE * 0.9)], TODAY)
    at = select_results([_cal(when=TODAY, est=1.0, act=1.0 + MIN_SURPRISE)], TODAY)
    assert below == [] and at != []


def test_yesterdays_after_close_print_is_still_selected():
    """An after-close print lands in the evening and a calendar row can take until the
    next morning to carry epsActual."""
    assert select_results([_cal(when=YESTERDAY, est=1.0, act=1.5)], TODAY) != []


def test_last_weeks_earnings_are_not_re_announced():
    old = _cal(when=TODAY - timedelta(days=6), est=1.0, act=2.0)
    assert select_results([old], TODAY) == []


def test_a_row_with_no_actual_yet_is_not_a_result():
    """Tomorrow's row is in the same response; it must not produce a 'results are out'
    notification just because it has an estimate."""
    assert select_results([_cal(when=TOMORROW, est=1.0)], TODAY) == []


def test_result_copy_states_direction_and_magnitude_without_a_verdict():
    """It does NOT say whether the surprise is good — a beat on a lowered bar is not
    good news, and a 180-character banner is not going to resolve that."""
    title, body = result_copy("NVDA", 0.42)
    assert "NVDA" in title
    assert "beat" in body and "42%" in body
    for directive in ("buy", "sell", "consider", "act now", "don't miss"):
        assert directive not in body.lower()

    _, miss = result_copy("NVDA", -0.30)
    assert "missed" in miss and "30%" in miss


def test_upcoming_copy_is_informational_only():
    _, body = upcoming_copy("AAPL", "after_close")
    for directive in ("buy", "sell", "consider", "position", "don't miss"):
        assert directive not in body.lower()


# ══ INSIDER ══════════════════════════════════════════════════════════════════

CUTOFF = (TODAY - timedelta(days=3)).isoformat()


def _tx(*, filed=None, name="Jane Doe", kind="P-Purchase", shares=1000, price=200.0):
    return {
        "filingDate": (filed or TODAY).isoformat(),
        "reportingName": name,
        "transactionType": kind,
        "securitiesTransacted": shares,
        "price": price,
    }


def test_a_large_informative_buy_is_selected():
    best = notable_insider_trade([_tx()], cutoff=CUTOFF)
    assert best is not None
    filed, name, action, dollars = best
    assert action == "bought" and dollars == pytest.approx(200_000)


def test_a_trade_below_the_dollar_floor_is_ignored():
    below = _tx(shares=1, price=100.0)   # $100
    assert notable_insider_trade([below], cutoff=CUTOFF) is None


def test_the_dollar_floor_is_inclusive():
    at = _tx(shares=1, price=float(MIN_INSIDER_AMOUNT))
    assert notable_insider_trade([at], cutoff=CUTOFF) is not None


@pytest.mark.parametrize("kind", ["M-Exempt", "F-InKind", "A-Award", "G-Gift", "", "???"])
def test_mechanical_transactions_carry_no_sentiment(kind):
    """Option exercises, tax withholding, awards and gifts are not decisions. Same
    filter the Holders tab applies, via the same shared classifier."""
    assert notable_insider_trade([_tx(kind=kind)], cutoff=CUTOFF) is None


def test_one_decision_split_across_forty_rows_becomes_one_notification():
    """A CFO selling in tranches files many rows for one decision. Forty banners for one
    decision is exactly how an app trains people to disable its notifications."""
    rows = [_tx(kind="S-Sale", shares=100, price=200.0) for _ in range(40)]
    best = notable_insider_trade(rows, cutoff=CUTOFF)
    assert best is not None
    assert best[3] == pytest.approx(40 * 100 * 200.0)   # aggregated, not the largest row


def test_an_old_filing_is_outside_the_window():
    assert notable_insider_trade(
        [_tx(filed=TODAY - timedelta(days=30))], cutoff=CUTOFF
    ) is None


def test_a_late_filing_of_an_old_trade_still_fires():
    """Keying on FILING date, not transaction date: a Form 4 for a trade three days ago
    that files today IS new information."""
    row = _tx(filed=TODAY)
    row["transactionDate"] = (TODAY - timedelta(days=3)).isoformat()
    assert notable_insider_trade([row], cutoff=CUTOFF) is not None


@pytest.mark.parametrize("shares,price", [
    (None, 200.0), (1000, None), (0, 200.0), (1000, 0),
    (-1000, 200.0), (1000, -200.0),
    (float("nan"), 200.0), (1000, float("nan")), (float("inf"), 200.0),
    ("many", 200.0), (1000, "cheap"),
])
def test_unusable_amounts_are_skipped_not_multiplied(shares, price):
    assert notable_insider_trade([_tx(shares=shares, price=price)], cutoff=CUTOFF) is None


@pytest.mark.parametrize("payload", [{}, [], None, "rows", [None], ["x"], [[]]])
def test_a_malformed_upstream_response_degrades_to_none(payload):
    assert notable_insider_trade(payload, cutoff=CUTOFF) is None


def test_a_missing_filing_date_is_skipped():
    row = _tx()
    del row["filingDate"]
    assert notable_insider_trade([row], cutoff=CUTOFF) is None


def test_the_biggest_decision_wins_when_several_qualify():
    # FMP sends `reportingName` SURNAME-FIRST ("ELLISON LAWRENCE JOSEPH"), and the
    # shared `normalize_insider_name` flips it to natural order. The fixtures below are
    # written in FMP's shape so the assertion pins the real pipeline rather than a
    # convenient fiction.
    small = _tx(name="Fry Small", shares=1000, price=200.0)                # $200K
    big = _tx(name="Ellison Lawrence", kind="S-Sale", shares=10_000, price=200.0)  # $2M
    best = notable_insider_trade([small, big], cutoff=CUTOFF)
    assert best[1] == "Lawrence Ellison" and best[2] == "sold"


def test_the_insider_name_reaches_the_copy_in_natural_order():
    """A banner reading 'Ellison Lawrence sold…' is the surname-first raw value leaking
    through — the normalizer exists precisely to stop that."""
    best = notable_insider_trade(
        [_tx(name="ELLISON LAWRENCE JOSEPH", shares=10_000, price=200.0)], cutoff=CUTOFF
    )
    _, body = insider_copy("ORCL", best[1], best[2], best[3])
    assert body.startswith("Lawrence Joseph Ellison ")


def test_a_blank_insider_name_degrades_to_a_readable_label():
    best = notable_insider_trade([_tx(name="", shares=10_000, price=200.0)], cutoff=CUTOFF)
    assert best is not None and best[1] == "Insider"


def test_insider_copy_names_who_and_how_much_without_a_recommendation():
    """Insiders sell for tuition and divorces too — the copy must not imply a signal."""
    _, body = insider_copy("NVDA", "Jane Doe", "sold", 2_400_000)
    assert "Jane Doe" in body and "$2.4M" in body
    for directive in ("follow", "consider", "signal", "bearish", "bullish"):
        assert directive not in body.lower()


# ══ WHALE / CONGRESS ═════════════════════════════════════════════════════════

CUTOFF_DATE = (TODAY - timedelta(days=45)).isoformat()


def test_the_backfill_guard_drops_quarter_old_trades():
    """THE trap. First hydration of a new whale inserts hundreds of quarter-old filings
    with a brand-new created_at. Windowing on created_at alone would announce a fund's
    entire historical book as this week's activity — one notification per position."""
    old = {"ticker": "AAPL", "date": "2026-01-15", "created_at": "2026-08-07T00:00:00Z"}
    assert _recent_whale_rows([old], cutoff_date=CUTOFF_DATE) == []


def test_a_genuinely_recent_trade_survives_the_guard():
    fresh = {"ticker": "AAPL", "date": TODAY.isoformat(), "created_at": "2026-08-07T00:00:00Z"}
    assert len(_recent_whale_rows([fresh], cutoff_date=CUTOFF_DATE)) == 1


def test_the_guard_is_not_a_no_op():
    """Pins the OPERATOR PRECEDENCE fix. `x.get("date") or "" < cutoff` parses as
    `x.get("date") or ("" < cutoff)` — truthy for ANY non-empty date, i.e. no guard at
    all. It looks like a filter and does nothing. If someone reintroduces that form, the
    mixed batch below comes back with both rows instead of one."""
    rows = [
        {"ticker": "OLD", "date": "2020-01-01"},
        {"ticker": "NEW", "date": TODAY.isoformat()},
    ]
    kept = _recent_whale_rows(rows, cutoff_date=CUTOFF_DATE)
    assert [r["ticker"] for r in kept] == ["NEW"]


def test_a_row_with_no_trade_date_is_kept():
    """created_at is then the only signal available. Dropping it would silently lose
    congressional rows whose transaction date FMP omits."""
    assert len(_recent_whale_rows([{"ticker": "AAPL", "date": None}], cutoff_date=CUTOFF_DATE)) == 1
    assert len(_recent_whale_rows([{"ticker": "AAPL"}], cutoff_date=CUTOFF_DATE)) == 1


@pytest.mark.parametrize("payload", [None, {}, "rows", [None], ["x"], [1]])
def test_malformed_whale_payloads_degrade_to_empty(payload):
    assert _recent_whale_rows(payload, cutoff_date=CUTOFF_DATE) == []


@pytest.mark.parametrize("source,expected", [
    ("13f", KIND_WHALE_13F),
    ("13F", KIND_WHALE_13F),
    ("congressional_house", KIND_CONGRESS_TRADE),
    ("congressional_senate", KIND_CONGRESS_TRADE),
    ("CONGRESSIONAL_SENATE", KIND_CONGRESS_TRADE),
])
def test_data_source_routes_to_the_right_kind(source, expected):
    assert _whale_kind(source) == expected


@pytest.mark.parametrize("source", ["manual", "", None, "unknown", "13g", 13])
def test_an_unknown_data_source_is_skipped_never_defaulted(source):
    """Defaulting to institutional would be silent (13F ships OFF); defaulting the other
    way would push congress-preference users about something else entirely."""
    assert _whale_kind(source) is None


def test_a_forty_position_filing_names_a_few_tickers_and_counts_the_rest():
    tickers = [f"T{i}" for i in range(40)]
    title, body = whale_copy("Pelosi", "bought", tickers, "$1.5M")
    assert "T0, T1, T2" in title and "+37 more" in title
    assert "$1.5M" in body


def test_whale_copy_is_informational():
    _, body = whale_copy("Bridgewater", "sold", ["AAPL"], "$50M")
    for directive in ("follow", "copy", "consider", "you should"):
        assert directive not in body.lower()


# ── the ingest cursor ────────────────────────────────────────────────────────

def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_the_cursor_advances_past_rows_the_guard_REJECTED():
    """Deliberately advanced past rejected rows: they were evaluated and correctly
    declined, so re-reading them tomorrow is pure waste. Advancing only past ACCEPTED
    rows would make a quiet week re-scan the same backfill forever."""
    fallback = _dt("2026-08-01T00:00:00Z")
    rows = [
        {"created_at": "2026-08-07T10:00:00Z", "date": "2020-01-01"},   # rejected
        {"created_at": "2026-08-07T09:00:00Z", "date": "2026-08-07"},   # kept
    ]
    assert _max_created_at(rows, fallback) == _dt("2026-08-07T10:00:00Z")


def test_the_cursor_never_goes_backwards():
    fallback = _dt("2026-08-07T00:00:00Z")
    rows = [{"created_at": "2026-01-01T00:00:00Z"}]
    assert _max_created_at(rows, fallback) == fallback


@pytest.mark.parametrize("rows", [
    [], None, "rows", [{}], [{"created_at": ""}], [{"created_at": "garbage"}], [None],
])
def test_an_unusable_cursor_input_falls_back(rows):
    fallback = _dt("2026-08-07T00:00:00Z")
    assert _max_created_at(rows, fallback) == fallback


def test_a_naive_timestamp_is_read_as_utc():
    """Supabase can return a naive stamp. Comparing naive to aware raises TypeError, so
    the whole pass would die on one row."""
    fallback = _dt("2026-08-01T00:00:00Z")
    got = _max_created_at([{"created_at": "2026-08-07T10:00:00"}], fallback)
    assert got == datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
