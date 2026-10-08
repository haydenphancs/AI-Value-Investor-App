"""What each insider holds after their latest Form 4 transactions (`_insider_holdings`).

TestFlight 1.0 (11), 2026-10-05: in an Updates chat on CRWV, "how many shares does he own
now?" — about a director who had just sold — got "Caydex does not have information on his
current total ownership", although every Form 4 line reports the balance held after it
(`securitiesOwned`). These pin the derivation behind Ask Cay AI's ownership tool.

Rows are shaped like the live CRWV / NVDA rows measured on 2026-10-05 (the Venturo filing,
the trust conversions, the gift between trusts, NVDA's broken trust chain). Pure: no
network, no Supabase.
"""

from __future__ import annotations

import json
import math

import pytest

from app.services._insider_common import prepare_insider_rows
from app.services._insider_holdings import (
    INACTIVE_AFTER_DAYS,
    MAX_CANDIDATES,
    holding_value,
    insider_holdings_from_rows,
)

CRWV_CIK = "0001769628"
VENTURO = ("Venturo Brian M", "0002058067", "director, officer: Chief Strategy Officer")
JAIN = ("Jain Sachin", "0002124855", "officer: Chief Operating Officer")


def _row(who, traded, filed, *, tx="S-Sale", ad="D", shares=100, price=0.0, owned=1000,
         own="D", security="Class A Common Stock", form="4", company=CRWV_CIK):
    name, cik, title = who
    return {
        "symbol": "CRWV", "companyCik": company, "reportingName": name, "reportingCik": cik,
        "typeOfOwner": title, "transactionType": tx, "acquisitionOrDisposition": ad,
        "securitiesTransacted": shares, "price": price, "securitiesOwned": owned,
        "directOrIndirect": own, "securityName": security, "formType": form,
        "transactionDate": traded, "filingDate": filed,
    }


def _venturo_sep30():
    """The 2026-10-02 filing exactly as the feed lists it: NOT in the order it happened."""
    return [
        _row(VENTURO, "2026-09-30", "2026-10-02", tx="M-Exempt", ad="D", shares=109380,
             owned=984380, security="Restricted Stock Units"),
        _row(VENTURO, "2026-09-30", "2026-10-02", tx="M-Exempt", ad="A", shares=17391, owned=368142),
        _row(VENTURO, "2026-09-30", "2026-10-02", tx="M-Exempt", ad="A", shares=109380, owned=350751),
        _row(VENTURO, "2026-09-30", "2026-10-02", tx="S-Sale", ad="D", shares=65616, price=87.69,
             owned=302526),
        _row(VENTURO, "2026-09-30", "2026-10-02", tx="M-Exempt", ad="D", shares=17391,
             owned=173913, security="Restricted Stock Units"),
    ]


def _venturo_sep18():
    return [
        _row(VENTURO, "2026-09-18", "2026-09-22", tx="C-Conversion", ad="D", shares=62500,
             owned=1515849, own="I", security="Class B Common Stock"),
        _row(VENTURO, "2026-09-18", "2026-09-22", tx="C-Conversion", ad="A", shares=62500, owned=303871),
        _row(VENTURO, "2026-09-18", "2026-09-22", tx="G-Gift", ad="D", shares=62500, owned=241371),
    ]


def _person(result, needle):
    matches = [p for p in result["insiders"] if needle.lower() in p["name"].lower()]
    assert len(matches) == 1, [p["name"] for p in result["insiders"]]
    return matches[0]


def _balances(person, held, security=None):
    return [h for h in person["holdings"] if h["held"] == held
            and (security is None or h["security"] == security)]


# ── the TestFlight case ─────────────────────────────────────────────────────────────

def test_the_director_holds_302526_after_the_sale_whatever_order_the_lines_come_in():
    """The vest that happened FIRST (+109,380) is listed SECOND. 'First row' gives 368,142 and
    'last row' gives the RSU balance; the chain gives the balance the filing reports after
    the sale."""
    rows = _venturo_sep18() + _venturo_sep30()
    for ordering in (rows, list(reversed(rows)), rows[3:] + rows[:3]):
        result = insider_holdings_from_rows(ordering)
        venturo = _person(result, "venturo")
        direct = _balances(venturo, "direct")
        assert len(direct) == 1
        assert direct[0]["shares"] == 302526
        assert direct[0]["as_of"] == "2026-09-30" and direct[0]["filed"] == "2026-10-02"
        assert direct[0]["security"] == "Class A Common Stock"
        assert venturo["name"] == "Brian M. Venturo"
        assert venturo["role"] == "director, Chief Strategy Officer"
        assert venturo["latest_transaction_date"] == "2026-09-30"
        trades = {t["transaction_type"]: t for t in venturo["latest_trades"]}
        assert trades["S-Sale"]["shares"] == 65616 and trades["S-Sale"]["acquired"] is False
        assert trades["S-Sale"]["average_price"] == pytest.approx(87.69)
        assert trades["M-Exempt"]["shares"] == 17391 + 109380


def test_rsu_and_option_lines_are_never_holdings():
    result = insider_holdings_from_rows(_venturo_sep30())
    venturo = _person(result, "venturo")
    assert all("Restricted" not in h["security"] for h in venturo["holdings"])
    assert not any(h["shares"] in (984380, 173913) for h in venturo["holdings"])


def test_a_same_day_cycle_at_the_window_start_is_resolved_by_the_next_day():
    """09-18 alone is a cycle (convert +62,500 then gift −62,500, nothing earlier): its end-of-
    day balance is undeterminable — never guessed — until 09-30's first line starts FROM one
    of the candidates."""
    alone = insider_holdings_from_rows([r for r in _venturo_sep18() if r["directOrIndirect"] == "D"])
    direct = _balances(_person(alone, "venturo"), "direct")[0]
    assert direct["shares"] is None
    assert direct["possible_shares"] == [241371, 303871]
    resolved = insider_holdings_from_rows(_venturo_sep18() + _venturo_sep30())
    assert _balances(_person(resolved, "venturo"), "direct")[0]["shares"] == 302526


def test_a_fresh_chain_on_the_first_day_seen_ends_on_its_last_line():
    """No earlier balance in the rows and no cycle: Jain's 08-20 filing (vest +7,608, vest
    +8,038, sell 6,339), listed out of order. The day's chain starts at the line nothing
    else ends on and is followed to its end — never left as three competing balances."""
    rows = [
        _row(JAIN, "2026-08-20", "2026-08-21", tx="M-Exempt", ad="A", shares=8038, owned=163430),
        _row(JAIN, "2026-08-20", "2026-08-21", tx="S-Sale", ad="D", shares=6339, owned=157091,
             price=91.88),
        _row(JAIN, "2026-08-20", "2026-08-21", tx="M-Exempt", ad="A", shares=7608, owned=155392),
    ]
    for ordering in (rows, list(reversed(rows))):
        direct = _balances(_person(insider_holdings_from_rows(ordering), "jain"), "direct")
        assert [(h["shares"], h.get("possible_shares")) for h in direct] == [(157091, None)]


# ── indirect holdings: several, never summed ───────────────────────────────────────────

def _venturo_trusts():
    return [
        # 07-01: two Class B trusts convert (two holdings on one day)
        _row(VENTURO, "2026-07-01", "2026-07-02", tx="C-Conversion", ad="D", shares=61532,
             owned=4990542, own="I", security="Class B Common Stock"),
        _row(VENTURO, "2026-07-01", "2026-07-02", tx="C-Conversion", ad="D", shares=15380,
             owned=2871000, own="I", security="Class B Common Stock"),
        # 08-13: a gift of everything from one trust into a new one (listed receiver first)
        _row(VENTURO, "2026-08-13", "2026-08-17", tx="G-Gift", ad="A", shares=1578349,
             owned=1578349, own="I", security="Class B Common Stock"),
        _row(VENTURO, "2026-08-13", "2026-08-17", tx="G-Gift", ad="D", shares=1578349,
             owned=0, own="I", security="Class B Common Stock"),
    ] + _venturo_sep18()


def test_each_indirect_holding_is_listed_with_its_own_date_and_none_is_summed():
    result = insider_holdings_from_rows(_venturo_trusts())
    venturo = _person(result, "venturo")
    class_b = _balances(venturo, "indirect", "Class B Common Stock")
    by_shares = {h["shares"]: h for h in class_b}
    assert set(by_shares) == {1515849, 4990542, 2871000}
    assert by_shares[1515849]["as_of"] == "2026-09-18" and not by_shares[1515849]["reported_earlier"]
    # Last reported before the line's newest indirect transaction: may be separate, may be stale.
    assert by_shares[4990542]["reported_earlier"] and by_shares[2871000]["reported_earlier"]
    assert all(h["shares"] != sum(by_shares) for h in class_b), "never a total"


def test_a_gift_between_two_trusts_is_two_holdings_not_an_ambiguous_day():
    """Receiver 0 → 1,578,349 and giver 1,578,349 → 0 on one day chain both ways; the transfer
    pair says it is two holdings (the giver's history is outside the rows)."""
    gift = _venturo_trusts()[2:4]
    result = insider_holdings_from_rows(gift)
    holding = _balances(_person(result, "venturo"), "indirect")
    assert [h["shares"] for h in holding] == [1578349]
    assert "possible_shares" not in holding[0]


def test_positions_opened_from_nothing_and_sold_down_the_same_day():
    """CRWV 07-01: a trust converted Class B into 61,532 and 15,380 Class A shares and sold
    them down to 80 and 0 across twelve interleaved lines. Every balance-before is some other
    line's balance-after; the chains start at the two positions opened from 0."""
    sales = [  # (shares sold, balance after), feed order
        (13822, 47710), (21118, 26592), (3455, 11925), (15524, 11068), (5279, 6646),
        (7736, 3332), (3880, 2766), (1933, 833), (3252, 80), (813, 20), (20, 0),
    ]
    rows = [
        _row(VENTURO, "2026-07-01", "2026-07-02", tx="C-Conversion", ad="A", shares=61532,
             owned=61532, own="I"),
        _row(VENTURO, "2026-07-01", "2026-07-02", tx="C-Conversion", ad="A", shares=15380,
             owned=15380, own="I"),
    ] + [_row(VENTURO, "2026-07-01", "2026-07-02", shares=s, owned=o, own="I", price=86.0)
         for s, o in sales]
    holding = _balances(_person(insider_holdings_from_rows(rows), "venturo"), "indirect")
    assert [h["shares"] for h in holding] == [80], holding


def test_a_broken_indirect_chain_reports_the_older_figure_as_earlier_never_as_current():
    """NVDA's CEO's main trust: 581,378,470 on 2025-09-15, then rows begin from 521,378,470 —
    a 60M-share move no fetched row records. The old tail is an 'earlier figure'."""
    huang = ("HUANG JEN HSUN", "0001197649", "director, officer: President and CEO")
    rows = [
        _row(huang, "2025-09-15", "2025-09-17", tx="G-Gift", ad="D", shares=100000,
             owned=581378470, own="I", security="Common Stock", company="0001045810"),
        _row(huang, "2025-12-17", "2025-12-19", tx="G-Gift", ad="D", shares=141488,
             owned=521236982, own="I", security="Common Stock", company="0001045810"),
    ]
    holding = _balances(_person(insider_holdings_from_rows(rows), "huang"), "indirect")
    current = [h for h in holding if not h["reported_earlier"]]
    assert [h["shares"] for h in current] == [521236982]
    assert [h["shares"] for h in holding if h["reported_earlier"]] == [581378470]


def test_the_same_balance_on_two_dates_is_one_holding():
    """NVDA's general counsel's trust read 2,687,660 on 08-31 and again on 09-21 (a row the
    feed lacks lies between): listing both would read as two trusts."""
    teter = ("Teter Timothy S.", "0001696841", "officer: EVP, General Counsel")
    rows = [
        _row(teter, "2026-08-31", "2026-09-02", shares=9395, owned=2687660, own="I"),
        _row(teter, "2026-09-21", "2026-09-23", shares=4499, owned=2687660, own="I"),
    ]
    holding = _balances(_person(insider_holdings_from_rows(rows), "teter"), "indirect")
    assert [(h["shares"], h["as_of"]) for h in holding] == [(2687660, "2026-09-21")]


def test_an_emptied_indirect_holding_is_dropped_unless_it_is_all_there_is():
    rows = [
        _row(VENTURO, "2026-08-01", "2026-08-03", shares=500, owned=0, own="I"),
        _row(VENTURO, "2026-08-02", "2026-08-04", tx="G-Gift", ad="A", shares=700, owned=700, own="I"),
    ]
    holding = _balances(_person(insider_holdings_from_rows(rows), "venturo"), "indirect")
    assert [h["shares"] for h in holding] == [700]
    only_zero = _balances(_person(insider_holdings_from_rows(rows[:1]), "venturo"), "indirect")
    assert [h["shares"] for h in only_zero] == [0]


# ── unknown is never 0 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [None, "", "  ", "abc", "-5", "1e9", "1,234", "NaN",
                                 float("nan"), float("inf"), -1, -0.5, True, False, [], {}])
def test_an_unreadable_balance_is_unknown_not_zero(raw):
    assert holding_value(raw) is None


@pytest.mark.parametrize("raw,expected", [(0, 0.0), (302526, 302526.0), ("302526", 302526.0),
                                          (" 12.5 ", 12.5), (1.5, 1.5)])
def test_a_reported_balance_is_kept_and_a_real_zero_is_zero(raw, expected):
    assert holding_value(raw) == expected


@pytest.mark.parametrize("owned", [None, "n/a", -1, float("nan"), True])
def test_a_newest_line_without_a_usable_balance_flags_the_last_known_one(owned):
    rows = [
        _row(JAIN, "2026-08-20", "2026-08-21", shares=6339, owned=157091),
        _row(JAIN, "2026-09-30", "2026-10-02", shares=4, owned=owned),
    ]
    direct = _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct")
    assert len(direct) == 1
    assert direct[0]["shares"] == 157091 and direct[0]["as_of"] == "2026-08-20"
    assert direct[0]["changed_after"] == "2026-09-30"
    assert all(h["shares"] != 0 for h in direct)


def test_a_person_with_no_usable_balance_has_no_holding_rather_than_zero():
    rows = [_row(JAIN, "2026-09-30", "2026-10-02", shares=4, owned=None)]
    person = _person(insider_holdings_from_rows(rows), "jain")
    assert person["holdings"] == []
    assert person["latest_transaction_date"] == "2026-09-30"


def test_an_acquisition_larger_than_the_balance_it_produced_is_a_unit_error():
    rows = [_row(JAIN, "2026-09-30", "2026-10-02", tx="P-Purchase", ad="A", shares=150_000,
                 owned=4_000, price=10.0)]
    assert _person(insider_holdings_from_rows(rows), "jain")["holdings"] == []


def test_a_sold_out_line_reports_zero_because_the_filing_said_zero():
    rows = [_row(JAIN, "2026-09-30", "2026-10-02", shares=500, owned=0)]
    direct = _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct")
    assert [h["shares"] for h in direct] == [0]


# ── junk and malformed rows ─────────────────────────────────────────────────────────────

def test_an_empty_record_is_skipped_not_read_as_holds_zero():
    """Magnetar 2026-08-13 / NVDA's empty Form 3s: no code, no ownership form, no security,
    0 shares, securitiesOwned 0."""
    junk = {"symbol": "CRWV", "companyCik": CRWV_CIK, "reportingName": "Magnetar Financial LLC",
            "reportingCik": "0001352851", "typeOfOwner": "10 percent owner: ", "transactionType": "",
            "acquisitionOrDisposition": "", "securitiesTransacted": 0, "price": 0,
            "securitiesOwned": 0, "directOrIndirect": None, "securityName": "", "formType": "4",
            "transactionDate": "2026-08-13", "filingDate": "2026-08-14"}
    result = insider_holdings_from_rows([junk])
    assert result["insiders"] == [] and result["rows_skipped"] == 1


def test_a_holdings_line_without_a_transaction_is_a_balance():
    """A Form 3 (or a restated 'By Trust' line) carries a balance and no transaction."""
    nora = ("NORA JOHNSON SUZANNE M", "0001000001", "director")
    row = _row(nora, "2026-07-13", "2026-07-15", tx="", ad="", shares=0, owned=2496, own="I",
               security="Common Stock", form="3", company="0001045810")
    holding = _balances(_person(insider_holdings_from_rows([row]), "johnson"), "indirect")
    assert [(h["shares"], h["as_of"]) for h in holding] == [(2496, "2026-07-13")]


def test_a_line_without_an_ownership_form_is_never_a_balance_but_flags_older_ones():
    rows = [
        _row(JAIN, "2026-08-20", "2026-08-21", shares=10, owned=5000),
        _row(JAIN, "2026-09-02", "2026-09-03", shares=10, owned=4990, own=None),
    ]
    direct = _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct")
    assert [h["shares"] for h in direct] == [5000]
    assert direct[0]["changed_after"] == "2026-09-02"


@pytest.mark.parametrize("bad", [None, 7, "rows", {"a": 1}])
def test_a_non_list_input_is_an_empty_result(bad):
    assert insider_holdings_from_rows(bad) == {
        "covers_filings_since": None, "insiders": [], "insiders_not_shown": 0,
        "insiders_not_shown_names": [], "inactive_not_shown": 0,
        "inactive_not_shown_names": [], "rows_skipped": 0,
    }


def test_garbage_rows_are_skipped_and_counted():
    good = _row(JAIN, "2026-09-30", "2026-10-02", shares=4, owned=157095)
    no_reporter = dict(good, reportingName="", reportingCik=None)
    no_date = dict(good, transactionDate=None, filingDate=None)
    result = insider_holdings_from_rows([good, "x", 5, None, no_reporter, no_date])
    assert [p["name"] for p in result["insiders"]] == ["Sachin Jain"]
    assert result["rows_skipped"] == 5


def test_an_impossible_trade_date_falls_back_to_the_filing_date():
    row = _row(JAIN, "2026-02-30", "2026-03-02", shares=4, owned=157095)
    direct = _balances(_person(insider_holdings_from_rows([row]), "jain"), "direct")
    assert direct[0]["as_of"] == "2026-03-02"


def test_a_cycle_with_many_balances_keeps_the_extremes_within_the_cap():
    rows = []
    balances = list(range(1000, 1000 + 12 * 10, 10))
    # A closed loop: each line's balance-before is the previous line's balance-after.
    for i, after in enumerate(balances):
        before = balances[i - 1]
        change = after - before
        rows.append(_row(JAIN, "2026-09-30", "2026-10-02", tx="J-Other",
                         ad="A" if change > 0 else "D", shares=abs(change), owned=after))
    direct = _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct")[0]
    assert direct["shares"] is None
    assert len(direct["possible_shares"]) == MAX_CANDIDATES
    assert min(direct["possible_shares"]) == min(balances)
    assert max(direct["possible_shares"]) == max(balances)


# ── people: names, order, staleness ───────────────────────────────────────────────────

def test_an_entity_name_is_not_reordered_like_a_person():
    magnetar = ("Magnetar Financial LLC", "0001352851", "10 percent owner: ")
    row = _row(magnetar, "2026-08-14", "2026-08-14", shares=10, owned=16293578, own="I")
    person = _person(insider_holdings_from_rows([row]), "magnetar")
    assert person["name"] == "Magnetar Financial LLC"
    assert person["role"] == "10 percent owner"


def test_people_come_most_recent_first_and_stale_filers_are_counted_out():
    old = ("IGER ROBERT A", "0001000002", "director")
    rows = [
        _row(JAIN, "2026-09-30", "2026-10-02", shares=4, owned=157095),
        _row(VENTURO, "2026-08-20", "2026-08-21", shares=10, owned=241371),
        _row(old, "2019-02-01", "2019-02-05", shares=10, owned=5000, company=CRWV_CIK),
    ]
    result = insider_holdings_from_rows(rows)
    assert [p["name"] for p in result["insiders"]] == ["Sachin Jain", "Brian M. Venturo"]
    assert result["inactive_not_shown"] == 1
    assert result["covers_filings_since"] == "2019-02-05"
    # The boundary itself is kept. (Its filing date moves with it: a trade dated after its
    # own filing is a typo the parser replaces with the filing date — finding 4 below.)
    edge = dict(rows[2], transactionDate="2024-10-01", filingDate="2024-10-03")
    assert INACTIVE_AFTER_DAYS == 730
    kept = insider_holdings_from_rows([rows[0], edge])
    assert len(kept["insiders"]) == 2 and kept["inactive_not_shown"] == 0


def test_max_people_caps_and_counts():
    rows = [_row((f"PERSON{i} A", f"00000000{i:02d}", "director"), "2026-09-01",
                 "2026-09-02", shares=1, owned=100 + i) for i in range(30)]
    result = insider_holdings_from_rows(rows, max_people=25)
    assert len(result["insiders"]) == 25 and result["insiders_not_shown"] == 5


def test_thousands_of_holdings_stay_near_linear():
    """It runs inside every Holders build. The first version rescanned every pending row and
    tail per placed row: 2,500 independent holdings over five days took ~61 s. Indexed by
    balance it takes ~20 ms; the bound here is generous so a slow runner never flakes."""
    import time

    fund = ("FUND LP", "0000000777", "10 percent owner")
    rows = [_row(fund, f"2026-09-{1 + i % 5:02d}", f"2026-09-{1 + i % 5:02d}", shares=1000 + i,
                 owned=3_000_000 + i * 104729, own="I")
            for i in range(2500)]
    chain, balance = [], 10_000_000
    for i in range(1000):
        chain.append(_row(JAIN, "2026-09-30", "2026-10-02", shares=7 + i, owned=balance - 7 - i))
        balance -= 7 + i
    started = time.perf_counter()
    result = insider_holdings_from_rows(rows + list(reversed(chain)))
    assert time.perf_counter() - started < 3.0
    assert _balances(_person(result, "jain"), "direct")[0]["shares"] == balance


def test_the_result_is_json_safe():
    result = insider_holdings_from_rows(_venturo_trusts() + _venturo_sep30())
    text = json.dumps(result, allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text
    for person in result["insiders"]:
        for h in person["holdings"]:
            assert h["shares"] is None or math.isfinite(h["shares"])


# ── the shared pipeline: issuer CIK + 4/A supersession happen BEFORE this ───────────────

def test_other_issuers_rows_never_become_holdings_after_the_shared_pipeline():
    """BRK-B's feed carries Berkshire's 10%-owner filings at OTHER issuers."""
    rows = [
        _row(JAIN, "2026-09-30", "2026-10-02", shares=4, owned=157095),
        _row(("BERKSHIRE HATHAWAY INC", "0001067983", "10 percent owner"), "2026-09-29",
             "2026-09-30", tx="P-Purchase", ad="A", shares=1000, owned=250_000_000,
             company="0000797468", own="I", security="Common Stock"),
    ]
    prepared, dropped = prepare_insider_rows(rows, CRWV_CIK)
    assert dropped == {"797468": 1}
    names = [p["name"] for p in insider_holdings_from_rows(prepared)["insiders"]]
    assert names == ["Sachin Jain"]


def test_a_form_4a_restatement_replaces_the_original_balance():
    """The original reported 13,596 sold → 50,000 held; the 4/A corrected it to 12,180 sold →
    51,416 held. Through the shared pipeline only the amendment's balance survives."""
    original = _row(JAIN, "2026-06-10", "2026-06-12", shares=13596, owned=50000, price=20.0)
    amended = dict(original, formType="4/A", filingDate="2026-06-20", securitiesTransacted=12180,
                   securitiesOwned=51416)
    prepared, _ = prepare_insider_rows([amended, original], CRWV_CIK)
    direct = _balances(_person(insider_holdings_from_rows(prepared), "jain"), "direct")
    assert [(h["shares"], h["filed"]) for h in direct] == [(51416, "2026-06-20")]


# ── Adversarial review, 2026-10-07: each finding reproduced by a failing test first ─────

def _cycle_day(day, filed, *, s_first):
    """An officer exercises and sells the same 10,000 shares on one day (a 10b5-1 month):
    each line's balance-before is the other's balance-after, so the rows alone cannot say
    whether the day ended on 50,000 or 60,000."""
    m = _row(JAIN, day, filed, tx="M-Exempt", ad="A", shares=10000, owned=60000)
    s = _row(JAIN, day, filed, tx="S-Sale", ad="D", shares=10000, owned=50000, price=20.0)
    return [s, m] if s_first else [m, s]


@pytest.mark.parametrize("s_first", [True, False])
def test_r1_a_repeated_cycle_stays_undetermined_whatever_the_feed_order(s_first):
    """Finding 1: the second identical day used to resolve the first day's ambiguity by
    whichever line the feed listed first — 60,000 or 50,000, never hedged."""
    rows = (_cycle_day("2026-08-01", "2026-08-03", s_first=s_first)
            + _cycle_day("2026-09-01", "2026-09-03", s_first=s_first))
    direct = _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct")
    assert len(direct) == 1
    assert direct[0]["shares"] is None, direct
    assert direct[0]["possible_shares"] == [50000, 60000]
    assert direct[0]["as_of"] == "2026-09-01"


def test_r1_one_continuing_candidate_still_resolves_the_cycle():
    """The guard must not over-hedge: a later day that starts from only ONE of the
    candidates names the balance."""
    rows = _cycle_day("2026-08-01", "2026-08-03", s_first=True) + [
        _row(JAIN, "2026-09-01", "2026-09-03", shares=1000, owned=49000, price=20.0)]
    direct = _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct")
    assert [(h["shares"], h.get("possible_shares")) for h in direct] == [(49000, None)]


@pytest.mark.parametrize("break_it", ["owned_none", "no_ownership_form"])
def test_r2_a_same_day_line_with_no_usable_balance_flags_the_figure(break_it):
    """Finding 2: Venturo's 09-30 sale line with a null balance (or no direct/indirect flag)
    left 368,142 — the PRE-sale figure — unflagged beside that same day's sale."""
    sale = _row(VENTURO, "2026-09-30", "2026-10-02", tx="S-Sale", ad="D", shares=65616,
                price=87.69, owned=302526)
    if break_it == "owned_none":
        sale["securitiesOwned"] = None
    else:
        sale["directOrIndirect"] = None
    rows = [
        _row(VENTURO, "2026-09-18", "2026-09-22", tx="G-Gift", ad="D", shares=62500, owned=241371),
        _row(VENTURO, "2026-09-30", "2026-10-02", tx="M-Exempt", ad="A", shares=17391, owned=368142),
        _row(VENTURO, "2026-09-30", "2026-10-02", tx="M-Exempt", ad="A", shares=109380, owned=350751),
        sale,
    ]
    direct = _balances(_person(insider_holdings_from_rows(rows), "venturo"), "direct")
    assert len(direct) == 1
    assert direct[0]["changed_after"] == "2026-09-30", direct


def test_r4_a_future_trade_date_typo_does_not_hide_every_other_insider():
    """Finding 4: one row typed 2062 for 2026 made every real filer 'over two years older'."""
    rows = [
        _row(VENTURO, "2026-09-30", "2026-10-02", shares=65616, owned=302526, price=87.69),
        _row(JAIN, "2062-08-20", "2026-08-21", shares=6339, owned=157091, price=91.88),
    ]
    result = insider_holdings_from_rows(rows)
    names = [p["name"] for p in result["insiders"]]
    assert names == ["Brian M. Venturo", "Sachin Jain"], result
    assert result["inactive_not_shown"] == 0
    jain = _person(result, "jain")
    # A Form 4 cannot report a trade after its own filing: the filing date stands in.
    assert jain["latest_transaction_date"] == "2026-08-21"
    assert _balances(jain, "direct")[0]["as_of"] == "2026-08-21"


def test_r4_a_future_trade_date_with_no_filing_date_is_not_a_balance():
    from datetime import date as _date

    rows = [
        _row(VENTURO, "2026-09-30", "2026-10-02", shares=65616, owned=302526),
        dict(_row(JAIN, "2062-08-20", "2026-08-21", shares=6339, owned=157091), filingDate=None),
    ]
    result = insider_holdings_from_rows(rows, today=_date(2026, 10, 7))
    assert [p["name"] for p in result["insiders"]] == ["Brian M. Venturo"]
    assert result["rows_skipped"] == 1 and result["inactive_not_shown"] == 0


def test_r5_series_lines_are_separate_holdings():
    """Finding 5: 'Series A' and 'Series C' Common Stock (the Liberty-style trackers) are
    different securities; they were one line, read as 'one of 50,000 or 90,000'."""
    rows = [
        _row(JAIN, "2026-09-30", "2026-10-02", tx="P-Purchase", ad="A", shares=1000,
             owned=50000, security="Series A Common Stock", price=10.0),
        _row(JAIN, "2026-09-30", "2026-10-02", tx="P-Purchase", ad="A", shares=1000,
             owned=90000, security="Series C Common Stock", price=10.0),
    ]
    direct = _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct")
    assert sorted((h["security"], h["shares"]) for h in direct) == [
        ("Series A Common Stock", 50000), ("Series C Common Stock", 90000)]
    assert all("possible_shares" not in h for h in direct)


def test_r5_an_older_series_line_is_not_dropped():
    rows = [
        _row(JAIN, "2026-08-01", "2026-08-03", tx="P-Purchase", ad="A", shares=1000,
             owned=50000, security="Series A Common Stock", price=10.0),
        _row(JAIN, "2026-09-30", "2026-10-02", tx="P-Purchase", ad="A", shares=1000,
             owned=90000, security="Series C Common Stock", price=10.0),
    ]
    direct = _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct")
    assert sorted(h["shares"] for h in direct) == [50000, 90000]


def _lot(day, filed, owned):
    """A separately reported direct lot: a 'Common Stock … D' line with no transaction."""
    return _row(JAIN, day, filed, tx="", ad="", shares=0, owned=owned)


def test_r6_a_separately_reported_direct_lot_is_a_holding_not_an_ambiguity():
    """Finding 6: a no-transaction 'D' line beside a sale read as 'one of 2,500 or 499,000
    shares (several same-day transactions could not be put in order)'."""
    rows = [
        _row(JAIN, "2026-09-15", "2026-09-16", shares=1000, owned=500000, price=20.0),
        _row(JAIN, "2026-09-30", "2026-10-02", shares=1000, owned=499000, price=20.0),
        _lot("2026-09-30", "2026-10-02", 2500),
    ]
    direct = _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct")
    assert all("possible_shares" not in h for h in direct), direct
    assert sorted((h["shares"], h["as_of"]) for h in direct) == [
        (2500, "2026-09-30"), (499000, "2026-09-30")]


def test_r6_an_older_direct_lot_is_kept_as_an_earlier_figure():
    rows = [
        _lot("2026-09-15", "2026-09-16", 2500),
        _row(JAIN, "2026-09-15", "2026-09-16", shares=1000, owned=500000, price=20.0),
        _row(JAIN, "2026-09-30", "2026-10-02", shares=1000, owned=499000, price=20.0),
    ]
    direct = _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct")
    by_shares = {h["shares"]: h for h in direct}
    assert set(by_shares) == {499000, 2500}, direct
    assert by_shares[499000]["reported_earlier"] is False
    assert by_shares[2500]["reported_earlier"] is True


def test_r6_a_form_3_starting_position_is_not_a_separate_lot():
    """AAPL's Ben Borders (live feed, 2026-10-05): his Form 3 states 39,130 shares on 01-02;
    his later Form 4 chain starts from 39,162 (an unreported 32-share change between), so the
    two never link. A Form 3 states where a position STARTED — not a lot reported beside it —
    so it is an older state of the traded holding, never an 'earlier figure' entry."""
    rows = [
        _row(JAIN, "2026-01-02", "2026-01-02", tx="", ad="", shares=0, owned=39130, form="3"),
        _row(JAIN, "2026-04-15", "2026-04-17", tx="M-Exempt", ad="A", shares=1717, owned=40879),
        _row(JAIN, "2026-04-15", "2026-04-17", tx="F-InKind", ad="D", shares=892, owned=39987,
             price=266.43),
        _row(JAIN, "2026-05-08", "2026-05-12", shares=1274, owned=38713, price=290.0),
    ]
    direct = _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct")
    assert [(h["shares"], h["reported_earlier"]) for h in direct] == [(38713, False)], direct


def test_r7_people_cut_by_the_cap_are_named():
    """Finding 7: the people beyond MAX_PEOPLE were only counted, so a question about one
    of them got 'no filing of theirs was found'."""
    rows = [_row((f"PERSON{i:02d} A", f"00000000{i:02d}", "director"), f"2026-09-{1 + i % 28:02d}",
                 "2026-09-30", shares=1, owned=100 + i) for i in range(30)]
    result = insider_holdings_from_rows(rows, max_people=25)
    shown = {p["name"] for p in result["insiders"]}
    cut = result["insiders_not_shown_names"]
    assert len(cut) == 5 and not shown & set(cut)
    assert shown | set(cut) == {f"A. Person{i:02d}" for i in range(30)}


def test_r7_stale_filers_are_named_too():
    old = ("IGER ROBERT A", "0001000002", "director")
    rows = [
        _row(JAIN, "2026-09-30", "2026-10-02", shares=4, owned=157095),
        _row(old, "2019-02-01", "2019-02-05", shares=10, owned=5000),
    ]
    result = insider_holdings_from_rows(rows)
    assert result["inactive_not_shown"] == 1
    assert result["inactive_not_shown_names"] == ["Robert A. Iger"]


# ── Final review, 2026-10-07: F1 / F2 / F5 reproduced by a failing test first ─────────

def _direct_view(rows):
    return sorted((h["shares"], h["as_of"], h["reported_earlier"], tuple(h.get("possible_shares") or ()))
                  for h in _balances(_person(insider_holdings_from_rows(rows), "jain"), "direct"))


def _main_and_lot():
    """Day 1: the main direct holding sells down to 5,000; the same Form 4 lists a separate
    2,500-share direct lot (a no-transaction 'D' line — an ESPP or restricted-stock lot)."""
    return [
        _row(JAIN, "2026-09-15", "2026-09-16", shares=1000, owned=5000, price=20.0),
        _lot("2026-09-15", "2026-09-16", 2500),
    ]


def test_f1_a_lot_that_later_trades_stays_beside_the_main_holding():
    """F1: the lot's first trade (500 shares withheld) used to turn it into 'the traded
    holding', and the main 5,000 vanished — 'held directly: 2,000 shares'. The 5,000 stays;
    last reported before the line's newest transaction, it carries the earlier-figure hedge
    (the rows cannot say whether it is a separate holding or a stale one)."""
    rows = _main_and_lot() + [
        _row(JAIN, "2026-09-20", "2026-09-22", tx="F-InKind", ad="D", shares=500, owned=2000,
             price=20.0),
    ]
    assert _direct_view(rows) == [(2000, "2026-09-20", False, ()), (5000, "2026-09-15", True, ())]


def test_f1_a_restated_main_line_beside_a_lot_trade_is_not_a_fake_ambiguity():
    """…and with the day-2 Form 4 also restating the main 5,000, the two used to merge into
    'one of 2,000 or 5,000 shares'."""
    rows = _main_and_lot() + [
        _lot("2026-09-20", "2026-09-22", 5000),
        _row(JAIN, "2026-09-20", "2026-09-22", tx="F-InKind", ad="D", shares=500, owned=2000,
             price=20.0),
    ]
    assert _direct_view(rows) == [(2000, "2026-09-20", False, ()), (5000, "2026-09-20", False, ())]


def test_f1_a_direct_chain_opened_from_zero_keeps_the_older_holding_as_an_earlier_figure():
    """F1, from-zero half: a new direct position opened from 0 (a conversion sold down to 80)
    used to erase the 302,526 held directly the day before. It cannot say whether that
    holding moved elsewhere unrecorded or still stands — so it stays, as an earlier figure."""
    rows = [
        _row(JAIN, "2026-09-30", "2026-10-02", shares=65616, owned=302526, price=87.69),
        _row(JAIN, "2026-10-01", "2026-10-02", tx="C-Conversion", ad="A", shares=61532,
             owned=61532),
        _row(JAIN, "2026-10-01", "2026-10-02", shares=61452, owned=80, price=86.0),
    ]
    assert _direct_view(rows) == [(80, "2026-10-01", False, ()), (302526, "2026-09-30", True, ())]


def test_f1_guard_a_chain_broken_at_a_nonzero_balance_still_drops_its_stale_state():
    """Unchanged: a newest chain that starts from an UNSEEN non-zero balance continues the same
    holding past a row the feed lacks — the older figure is its stale state, not shown."""
    rows = [
        _row(JAIN, "2026-09-15", "2026-09-16", shares=1000, owned=5000, price=20.0),
        _row(JAIN, "2026-09-30", "2026-10-02", shares=1000, owned=3000, price=20.0),  # from 4,000
    ]
    assert _direct_view(rows) == [(3000, "2026-09-30", False, ())]


def _trust(day, filed, *, tx, ad, shares, owned, price=0.0):
    return _row(JAIN, day, filed, tx=tx, ad=ad, shares=shares, owned=owned, own="I", price=price)


def test_f2_twin_trusts_with_equal_balances_are_two_holdings():
    """F2: two trusts each gifted 500,000 shares, each selling 10,000 — identically funded,
    identically traded. The balance de-duplication used to keep ONE 490,000."""
    rows = [
        _trust("2026-03-02", "2026-03-04", tx="G-Gift", ad="A", shares=500000, owned=500000),
        _trust("2026-03-02", "2026-03-04", tx="G-Gift", ad="A", shares=500000, owned=500000),
        _trust("2026-09-15", "2026-09-17", tx="S-Sale", ad="D", shares=10000, owned=490000, price=50.0),
        _trust("2026-09-15", "2026-09-17", tx="S-Sale", ad="D", shares=10000, owned=490000, price=50.0),
    ]
    holding = _balances(_person(insider_holdings_from_rows(rows), "jain"), "indirect")
    assert [(h["shares"], h["as_of"]) for h in holding] == [(490000, "2026-09-15")] * 2
    gift_day = _balances(_person(insider_holdings_from_rows(rows[:2]), "jain"), "indirect")
    assert [h["shares"] for h in gift_day] == [500000, 500000]


def test_f2_guard_one_holding_repeating_its_end_balance_stays_one_entry_and_says_so():
    """The merge F2 must not undo: one trust whose filer prints the END-of-day balance on
    every fill line (6,000 and 4,000 sold, both lines '490,000'). It stays one entry — and,
    since the rows cannot prove it is one holding, the entry says the balance closed two
    lines that day."""
    rows = [
        _trust("2026-09-15", "2026-09-17", tx="S-Sale", ad="D", shares=6000, owned=490000, price=50.0),
        _trust("2026-09-15", "2026-09-17", tx="S-Sale", ad="D", shares=4000, owned=490000, price=50.0),
    ]
    holding = _balances(_person(insider_holdings_from_rows(rows), "jain"), "indirect")
    assert [(h["shares"], h.get("same_balance_lines")) for h in holding] == [(490000, 2)]


def test_f2_guard_the_same_balance_on_two_dates_still_merges_quietly():
    rows = [
        _trust("2026-08-31", "2026-09-02", tx="S-Sale", ad="D", shares=9395, owned=2687660, price=50.0),
        _trust("2026-09-21", "2026-09-23", tx="S-Sale", ad="D", shares=4499, owned=2687660, price=50.0),
    ]
    holding = _balances(_person(insider_holdings_from_rows(rows), "jain"), "indirect")
    assert [(h["shares"], h["as_of"], h.get("same_balance_lines")) for h in holding] == [
        (2687660, "2026-09-21", None)]


def test_f5_a_late_filed_old_trade_is_not_a_two_years_stale_filer():
    """F5: the 'older filers' cut read the TRADE date while the tool says 'last filing'. A
    Form 4 filed 2026-09-15 for a 2016-09-10 trade (filed late, or 2016 typed for 2026) hid a
    current director's balance as 'most likely no longer an insider'."""
    bob = ("BETA BOB", "0001000003", "director")
    rows = [
        _row(VENTURO, "2026-09-30", "2026-10-02", shares=65616, owned=302526, price=87.69),
        _row(bob, "2016-09-10", "2026-09-15", shares=1000, owned=120000, price=20.0),
    ]
    result = insider_holdings_from_rows(rows)
    assert result["inactive_not_shown"] == 0 and result["inactive_not_shown_names"] == []
    assert _balances(_person(result, "beta"), "direct")[0]["shares"] == 120000


def test_f5_guard_a_filer_whose_last_FILING_is_years_old_is_still_cut():
    old = ("IGER ROBERT A", "0001000002", "director")
    rows = [
        _row(JAIN, "2026-09-30", "2026-10-02", shares=4, owned=157095),
        _row(old, "2019-02-01", "2019-02-05", shares=10, owned=5000),
    ]
    assert insider_holdings_from_rows(rows)["inactive_not_shown_names"] == ["Robert A. Iger"]


# ── Final review hardening: the F1 / F2 / F5 rules at their edges ───────────────────────

def _lot_trade():
    return _main_and_lot() + [
        _row(JAIN, "2026-09-20", "2026-09-22", tx="F-InKind", ad="D", shares=500, owned=2000,
             price=20.0),
    ]


def test_hardening_empty_and_single_row_inputs():
    empty = insider_holdings_from_rows([])
    assert (empty["insiders"], empty["inactive_not_shown"], empty["covers_filings_since"]) == (
        [], 0, None)
    one = insider_holdings_from_rows([_row(JAIN, "2026-09-30", "2026-10-02", shares=10, owned=157091)])
    jain = _person(one, "jain")
    assert [(h["held"], h["shares"], h["reported_earlier"]) for h in jain["holdings"]] == [
        ("direct", 157091, False)]
    assert jain["holdings_not_shown"] == 0 and one["inactive_not_shown"] == 0


def test_hardening_an_exact_duplicate_line_never_doubles_a_direct_figure():
    sale = _row(JAIN, "2026-09-30", "2026-10-02", shares=1000, owned=499000, price=20.0)
    rows = [_row(JAIN, "2026-09-15", "2026-09-16", shares=1000, owned=500000, price=20.0),
            sale, dict(sale)]
    assert _direct_view(rows) == [(499000, "2026-09-30", False, ())]


def test_f1_hardening_the_lot_rules_hold_whatever_the_feed_order():
    expected = [(2000, "2026-09-20", False, ()), (5000, "2026-09-15", True, ())]
    assert _direct_view(_lot_trade()) == _direct_view(list(reversed(_lot_trade()))) == expected


def test_f1_hardening_a_lot_sold_to_zero_adds_nothing_beside_the_main_holding():
    rows = _main_and_lot() + [_row(JAIN, "2026-09-20", "2026-09-22", shares=2500, owned=0, price=20.0)]
    assert _direct_view(rows) == [(5000, "2026-09-15", True, ())]


def test_f1_hardening_an_exercise_opened_and_sold_from_zero_never_reads_as_holding_nothing():
    """Some filers report each exercised lot from 0 (exercise 0 → 1,000, sale → 0). That day's
    0 is the lot's, not the person's — it used to be the only direct figure: 'held directly:
    0 shares' beside a 302,526-share holding."""
    rows = [
        _row(JAIN, "2026-09-30", "2026-10-02", shares=1000, owned=302526, price=87.0),
        _row(JAIN, "2026-10-01", "2026-10-03", tx="M-Exempt", ad="A", shares=1000, owned=1000),
        _row(JAIN, "2026-10-01", "2026-10-03", shares=1000, owned=0, price=88.0),
    ]
    assert _direct_view(rows) == [(302526, "2026-09-30", True, ())]


def test_f1_hardening_a_lone_position_opened_and_closed_from_zero_still_reads_zero():
    rows = [
        _row(JAIN, "2026-10-01", "2026-10-03", tx="M-Exempt", ad="A", shares=1000, owned=1000),
        _row(JAIN, "2026-10-01", "2026-10-03", shares=1000, owned=0, price=88.0),
    ]
    assert _direct_view(rows) == [(0, "2026-10-01", False, ())]


def test_f1_hardening_the_walk_back_stops_at_the_first_chain_with_unseen_history():
    """Back from the newest chain, through positions opened from 0 (one emptied, so not
    listed), to the first chain that started from a balance no row explains — which may
    continue anything older, so nothing older is listed."""
    rows = [
        _row(JAIN, "2026-09-01", "2026-09-02", shares=100, owned=400000, price=80.0),
        _row(JAIN, "2026-09-20", "2026-09-22", shares=100, owned=302526, price=80.0),
        _row(JAIN, "2026-09-25", "2026-09-26", tx="M-Exempt", ad="A", shares=500, owned=500),
        _row(JAIN, "2026-09-25", "2026-09-26", shares=500, owned=0, price=81.0),
        _row(JAIN, "2026-10-01", "2026-10-02", tx="C-Conversion", ad="A", shares=61532, owned=61532),
        _row(JAIN, "2026-10-01", "2026-10-02", shares=61452, owned=80, price=86.0),
    ]
    assert _direct_view(rows) == [(80, "2026-10-01", False, ()), (302526, "2026-09-20", True, ())]


def test_f1_hardening_a_repeated_lot_size_is_listed_once():
    """OKTA's director (live feed) converted 65,000 Class A shares from 0 on several dates. The
    walk back lists each earlier position — but a figure repeating a newer one's balance is
    that holding again (or the same lot size), as on the indirect line: listed once."""
    rows = [_row(JAIN, day, day, tx="C-Conversion", ad="A", shares=65000, owned=65000)
            for day in ("2026-01-24", "2026-02-05", "2026-02-13")]
    rows.append(_row(JAIN, "2026-06-17", "2026-06-19", tx="M-Exempt", ad="A", shares=7123, owned=7123))
    assert _direct_view(rows) == [(7123, "2026-06-17", False, ()), (65000, "2026-02-13", True, ())]


def test_f1_hardening_direct_figures_beyond_the_cap_are_counted_as_holdings():
    """A filer who reports every exercised lot from 0 and holds each: all are listed, the cap
    shows six, and the rest are COUNTED — as holdings, not as 'indirect holdings'."""
    rows = [_row(JAIN, f"2026-09-{d:02d}", f"2026-09-{d + 1:02d}", tx="M-Exempt", ad="A",
                 shares=100 + d, owned=100 + d) for d in range(1, 10)]
    jain = _person(insider_holdings_from_rows(rows), "jain")
    direct = _balances(jain, "direct")
    assert len(direct) == 6 and jain["holdings_not_shown"] == 3
    assert direct[0]["shares"] == 109 and not direct[0]["reported_earlier"]
    assert all(h["reported_earlier"] for h in direct[1:])
    assert "indirect_holdings_not_shown" not in jain


def test_f2_hardening_identical_holdings_lines_with_no_history_say_how_many_lines_they_are():
    """Two holdings lines restated at 25,000 on one day with nothing before them — one trust
    restated twice, or two trusts: one entry that says it stands for two lines."""
    rows = [_trust("2026-09-15", "2026-09-17", tx="", ad="", shares=0, owned=25000)] * 2
    holding = _balances(_person(insider_holdings_from_rows(rows), "jain"), "indirect")
    assert [(h["shares"], h.get("same_balance_lines")) for h in holding] == [(25000, 2)]


def test_f2_hardening_a_known_trust_and_an_unexplained_line_on_its_balance_fold_into_one():
    """A trust with history ends on 490,000; a line opened that day from a balance no row
    explains also reports 490,000 — one entry, never two trusts asserted, flagged two lines."""
    rows = [
        _trust("2026-09-01", "2026-09-02", tx="S-Sale", ad="D", shares=5000, owned=500000, price=50.0),
        _trust("2026-09-15", "2026-09-17", tx="S-Sale", ad="D", shares=10000, owned=490000, price=50.0),
        _trust("2026-09-15", "2026-09-17", tx="S-Sale", ad="D", shares=4000, owned=490000, price=50.0),
    ]
    holding = _balances(_person(insider_holdings_from_rows(rows), "jain"), "indirect")
    assert [(h["shares"], h["as_of"], h.get("same_balance_lines")) for h in holding] == [
        (490000, "2026-09-15", 2)]


def test_f2_hardening_twin_trusts_whatever_the_feed_order():
    rows = [
        _trust("2026-03-02", "2026-03-04", tx="G-Gift", ad="A", shares=500000, owned=500000),
        _trust("2026-03-02", "2026-03-04", tx="G-Gift", ad="A", shares=500000, owned=500000),
        _trust("2026-09-15", "2026-09-17", tx="S-Sale", ad="D", shares=10000, owned=490000, price=50.0),
        _trust("2026-09-15", "2026-09-17", tx="S-Sale", ad="D", shares=10000, owned=490000, price=50.0),
    ]
    for order in (rows, list(reversed(rows)), rows[2:] + rows[:2]):
        holding = _balances(_person(insider_holdings_from_rows(order), "jain"), "indirect")
        assert [(h["shares"], h.get("same_balance_lines")) for h in holding] == [(490000, None)] * 2


@pytest.mark.parametrize("days,stale", [(730, False), (731, True)])
def test_f5_hardening_the_cut_reads_filing_dates_at_its_boundary(days, stale):
    from datetime import date as _date, timedelta

    filed = (_date(2026, 10, 2) - timedelta(days=days)).isoformat()
    rows = [
        _row(VENTURO, "2026-09-30", "2026-10-02", shares=10, owned=302526),
        _row(("IGER ROBERT A", "0001000002", "director"), "2019-02-01", filed, shares=10, owned=5000),
    ]
    result = insider_holdings_from_rows(rows, today=_date(2026, 10, 7))
    assert (result["inactive_not_shown_names"] == ["Robert A. Iger"]) is stale


def test_f5_hardening_an_impossible_filing_date_falls_back_to_the_trade_date():
    """A corrupt filing date ('2062-…', or a day that does not exist) is never what a person
    is judged on — as the newest 'filing' it would make everyone else read as stale."""
    from datetime import date as _date

    rows = [
        _row(VENTURO, "2026-09-30", "2026-10-02", shares=10, owned=302526),
        _row(JAIN, "2026-09-29", "2062-09-30", shares=10, owned=157091),
        _row(("IGER ROBERT A", "0001000002", "director"), "2019-02-01", "2026-99-99", shares=10,
             owned=5000),
    ]
    result = insider_holdings_from_rows(rows, today=_date(2026, 10, 7))
    assert [p["name"] for p in result["insiders"]] == ["Brian M. Venturo", "Sachin Jain"]
    assert result["inactive_not_shown_names"] == ["Robert A. Iger"]


# ── Round-5 review, 2026-10-07: R5-1 / R5-2 / R5-3 reproduced by a failing test first ────

def test_r5_1_a_from_zero_holding_sold_to_zero_days_later_still_reads_zero_now():
    """R5-1 (COIN's CAO, live feed): her direct chain opened from 0 and runs through quarterly
    vest-and-sell cycles; the 2026-08-24 sale left 0. That 0 is her reported current figure —
    F1's round dropped it and showed a 2025 figure in its place."""
    rows = [
        _row(JAIN, "2025-02-24", "2025-02-26", shares=400, owned=2598, price=200.0),
        _row(JAIN, "2025-05-20", "2025-05-22", tx="M-Exempt", ad="A", shares=628, owned=628),
        _row(JAIN, "2025-05-22", "2025-05-23", shares=628, owned=0, price=210.0),
        _row(JAIN, "2026-08-20", "2026-08-22", tx="M-Exempt", ad="A", shares=1321, owned=1321),
        _row(JAIN, "2026-08-20", "2026-08-22", tx="M-Exempt", ad="A", shares=1214, owned=2535),
        _row(JAIN, "2026-08-24", "2026-08-26", shares=2535, owned=0, price=300.0),
    ]
    assert _direct_view(rows) == [(0, "2026-08-24", False, ()), (2598, "2025-02-24", True, ())]


def test_r5_1_awards_gifted_away_every_quarter_read_zero_held_directly_now():
    """R5-1 (TWLO director, live feed): each quarter an award 0 → N is gifted to her trust the
    same day, the chain running through 0 since 2024. Her direct holding now is the reported
    0, not a 2021 figure in its place."""
    rows = [_row(JAIN, "2021-12-03", "2021-12-07", tx="G-Gift", ad="D", shares=697, owned=4212)]
    for day, n in (("2024-12-15", 732), ("2025-03-15", 618), ("2026-09-15", 337)):
        rows += [_row(JAIN, day, day, tx="A-Award", ad="A", shares=n, owned=n),
                 _row(JAIN, day, day, tx="G-Gift", ad="D", shares=n, owned=0)]
    assert _direct_view(rows) == [(0, "2026-09-15", False, ()), (4212, "2021-12-03", True, ())]


def test_r5_1_a_holding_built_from_zero_over_months_then_gifted_reads_zero_now():
    """R5-1 (WFC officer, live feed): a chain opened from 0 in February, vested up to 67,966
    and was gifted whole to a trust in July. The 0 is current; December's figure is earlier."""
    rows = [
        _row(JAIN, "2025-12-05", "2025-12-09", tx="M-Exempt", ad="A", shares=1000, owned=1407),
        _row(JAIN, "2026-02-05", "2026-02-09", tx="M-Exempt", ad="A", shares=15212, owned=15212),
        _row(JAIN, "2026-03-05", "2026-03-09", tx="M-Exempt", ad="A", shares=52754, owned=67966),
        _row(JAIN, "2026-07-15", "2026-07-17", tx="G-Gift", ad="D", shares=67966, owned=0),
    ]
    assert _direct_view(rows) == [(0, "2026-07-15", False, ()), (1407, "2025-12-05", True, ())]


def _segal(awards):
    """HOOD's director (live feed): a running holding of 14,953 on 2025-03-31, then quarterly
    awards, each reported from 0."""
    days = ("2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30")
    sizes = (209, 137, 176, 288, 243, 216)
    rows = [_row(JAIN, "2025-03-31", "2025-04-02", tx="A-Award", ad="A", shares=524, owned=14953)]
    for day, n in list(zip(days, sizes))[len(days) - awards:]:
        rows.append(_row(JAIN, day, day, tx="A-Award", ad="A", shares=n, owned=n))
    jain = _person(insider_holdings_from_rows(rows), "jain")
    return [(h["shares"], h["reported_earlier"]) for h in _balances(jain, "direct")], jain["holdings_not_shown"]


def test_r5_2_the_cap_keeps_the_running_holding_behind_six_quarterly_awards():
    """R5-2: six awards from 0 after the 14,953 running holding filled the cap with the awards,
    and the 14,953 — the figure the walk back exists to keep — was cut. The OLDEST award is
    trimmed (and counted) instead."""
    assert _segal(6) == ([(216, False), (243, True), (288, True), (176, True), (137, True),
                          (14953, True)], 1)


def test_r5_3_a_same_day_exercise_sold_to_zero_is_not_a_fake_one_of():
    """R5-3: a main sale ending 302,526 and, the same day, an exercise 0 → 1,000 sold back to 0.
    That 0 is the exercised lot's — it read 'one of 0 or 302,526'."""
    rows = [
        _row(JAIN, "2026-10-01", "2026-10-03", shares=1000, owned=302526, price=87.0),
        _row(JAIN, "2026-10-01", "2026-10-03", tx="M-Exempt", ad="A", shares=1000, owned=1000),
        _row(JAIN, "2026-10-01", "2026-10-03", shares=1000, owned=0, price=88.0),
    ]
    for order in (rows, list(reversed(rows)), rows[1:] + rows[:1]):
        assert _direct_view(order) == [(302526, "2026-10-01", False, ())]


def test_r5_3_an_award_lot_gifted_into_the_main_holding_reads_the_main_holding():
    """R5-3 (MGRX's CEO, live market feed): a 2,000,000-share award lot (from 0) was gifted out
    to 0 and into his main holding (1,305,000 → 3,305,000) the same day. It read 'one of 0 or
    3,305,000 shares (the filings do not show which line came last that day)'."""
    rows = [
        _row(JAIN, "2025-09-30", "2026-10-06", tx="G-Gift", ad="A", shares=500000, owned=1305000),
        _row(JAIN, "2026-07-28", "2026-10-06", tx="A-Award", ad="A", shares=2000000, owned=2000000),
        _row(JAIN, "2026-08-11", "2026-10-06", tx="G-Gift", ad="A", shares=2000000, owned=3305000),
        _row(JAIN, "2026-08-11", "2026-10-06", tx="G-Gift", ad="D", shares=2000000, owned=0),
    ]
    assert _direct_view(rows) == [(3305000, "2026-08-11", False, ())]


# ── Round 5, must-keep: the shapes the three touched rules must NOT change ──────────────

def test_r5_must_keep_a_multi_day_from_zero_holding_still_held_lists_the_older_figure():
    """R5-1 changed only an EMPTIED from-zero newest day. A from-zero chain that ran across
    days and is still held keeps F1's walk back: the older holding beside it, as earlier."""
    rows = [
        _row(JAIN, "2026-09-15", "2026-09-17", shares=65616, owned=302526, price=87.69),
        _row(JAIN, "2026-09-25", "2026-09-26", tx="C-Conversion", ad="A", shares=61532, owned=61532),
        _row(JAIN, "2026-10-01", "2026-10-02", shares=61452, owned=80, price=86.0),
    ]
    assert _direct_view(rows) == [(80, "2026-10-01", False, ()), (302526, "2026-09-15", True, ())]


def test_r5_must_keep_the_anchor_inside_the_cap_keeps_the_walk_order_and_counts_nothing():
    """R5-2 changed only an anchor pushed OUT of the cap: with five awards it fits, untouched."""
    assert _segal(5) == ([(216, False), (243, True), (288, True), (176, True), (137, True),
                          (14953, True)], 0)


def test_r5_must_keep_lots_past_the_cap_are_counted_and_the_running_holding_listed_once():
    """R5-2 moves the running holding into the cap only when the cap cut it. Inside the cap —
    here second, with separately reported lots after it — it is listed once, never twice,
    and the lot past the cap is counted."""
    rows = [_row(JAIN, "2025-03-31", "2025-04-02", tx="A-Award", ad="A", shares=524, owned=14953),
            _row(JAIN, "2026-09-30", "2026-09-30", tx="A-Award", ad="A", shares=216, owned=216)]
    rows += [_lot(f"2026-0{m}-10", f"2026-0{m}-12", 1000 * m) for m in range(1, 6)]
    jain = _person(insider_holdings_from_rows(rows), "jain")
    shares = [h["shares"] for h in _balances(jain, "direct")]
    assert shares == [216, 14953, 5000, 4000, 3000, 2000] and jain["holdings_not_shown"] == 1


def test_r5_must_keep_two_chains_with_unseen_history_ending_the_same_day_stay_one_of():
    """R5-3 filters only an EMPTIED from-zero position. Two chains that each start from a
    balance no row explains and end the same day are the documented ambiguity (a chain broken
    by a missing line): still 'one of'."""
    rows = [
        _row(JAIN, "2026-10-01", "2026-10-03", shares=1000, owned=4000, price=20.0),
        _row(JAIN, "2026-10-01", "2026-10-03", shares=1000, owned=6000, price=20.0),
    ]
    assert _direct_view(rows) == [(None, "2026-10-01", False, (4000, 6000))]


def test_r5_must_keep_a_held_same_day_lot_beside_the_main_holding_stays_hedged():
    """A conversion from 0 sold down to 80 (still held) beside the main sale the same day:
    two live figures the rows cannot separate stay offered together — the hedge, not a new
    rule (R5-3 drops only a position emptied to 0)."""
    rows = [
        _row(JAIN, "2026-10-01", "2026-10-03", shares=1000, owned=302526, price=87.0),
        _row(JAIN, "2026-10-01", "2026-10-03", tx="C-Conversion", ad="A", shares=61532, owned=61532),
        _row(JAIN, "2026-10-01", "2026-10-03", shares=61452, owned=80, price=86.0),
    ]
    assert _direct_view(rows) == [(None, "2026-10-01", False, (80, 302526))]


def test_r5_must_keep_the_main_holdings_own_reported_zero_is_never_filtered():
    """R5-3 drops only a position that OPENED from 0 and emptied. The main holding sold out
    the same day another chain (history unseen) ends keeps its reported 0 — offered with the
    other figure, hedged, as before. (Sold to 0 and re-acquired from 0 is ONE chain: below.)"""
    rows = [
        _row(JAIN, "2026-09-15", "2026-09-17", shares=1000, owned=5000, price=20.0),
        _row(JAIN, "2026-10-01", "2026-10-03", shares=5000, owned=0, price=21.0),
        _row(JAIN, "2026-10-01", "2026-10-03", shares=1000, owned=6000, price=21.0),
    ]
    assert _direct_view(rows) == [(None, "2026-10-01", False, (0, 6000))]
    rebought = rows[:2] + [
        _row(JAIN, "2026-10-01", "2026-10-03", tx="M-Exempt", ad="A", shares=1000, owned=1000)]
    assert _direct_view(rebought) == [(1000, "2026-10-01", False, ())]


def test_r5_3_an_emptied_lot_among_same_day_lots_from_zero_offers_no_zero():
    """R5-3's reach (KO-style, every exercise reported from 0): two lots still held and one sold
    back to 0 on one day. The emptied lot's 0 is not offered as the person's figure."""
    rows = [
        _row(JAIN, "2026-10-01", "2026-10-03", tx="M-Exempt", ad="A", shares=40754, owned=40754),
        _row(JAIN, "2026-10-01", "2026-10-03", tx="M-Exempt", ad="A", shares=41365, owned=41365),
        _row(JAIN, "2026-10-01", "2026-10-03", tx="M-Exempt", ad="A", shares=1000, owned=1000),
        _row(JAIN, "2026-10-01", "2026-10-03", shares=1000, owned=0, price=88.0),
    ]
    assert _direct_view(rows) == [(None, "2026-10-01", False, (40754, 41365))]
