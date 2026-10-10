"""`_insider_buys_common` is a COPY of signals_service's CEO Buys pipeline (contract D5).

The copy exists so the marketing adapter never imports signals_service (another session owns it;
a move would couple a Pro card to the marketing engine). This file is the price of copying:

* **parity** — with ``window_start = now.date() - W`` and ``window_end = now.date() + 2`` the copy
  returns signals' buys as a MULTISET (the signals window is ``-2 <= (now - filed).days <= W`` on a
  midnight-UTC filing date, which is exactly that date range at every time of day: 00:00, noon and
  23:59:59 are all checked) and ranks them in signals' order — over a corpus mirroring
  `test_signals_ceo.py`'s cases plus 4/A full and partial restatements, cross-filing duplicates,
  unit errors and NaN / numeric-string numbers;
* **the two pinned divergences** — a bool is never a number, and a missing reference price
  rejects a line when the caller requires one;
* **the role table** — CEO (the shared `is_ceo_role`), CFO (its own exclusions) and director (a
  role FLAG, never a word inside a title).

When signals_service imports this module in its own change, delete the parity half.
"""

from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pytest

import app.services.signals_service as ssvc
from app.services import _insider_buys_common as ib
from app.services._insider_common import officer_title

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _r(**over):
    row = {
        "symbol": "GME",
        "filingDate": "2026-09-20 16:00:00",
        "transactionDate": "2026-09-18",
        "transactionType": "P-Purchase",
        "acquisitionOrDisposition": "A",
        "formType": "4",
        "typeOfOwner": "director, officer: Chief Executive Officer",
        "securityName": "Class A Common Stock",
        "securitiesTransacted": 100_000,
        "price": 25.0,
        "reportingCik": "0001",
        "reportingName": "COHEN RYAN",
        "directOrIndirect": "D",
    }
    row.update(over)
    return row


# ── the corpus: each case is a list of rows extracted together ────────────────

CASES = {
    "base": [_r()],
    "malformed": ["x", None, 3, [], _r(typeOfOwner=None), _r(typeOfOwner=5), _r(typeOfOwner=["CEO"]),
                  _r(securityName=None), _r(symbol=None), _r(symbol=123), _r()],
    "non_ceo": [_r(typeOfOwner="director"), _r(typeOfOwner="officer: CFO"), _r(typeOfOwner="10 percent owner"),
                _r(typeOfOwner="director, other: Retired CEO"), _r(typeOfOwner="officer: Deputy CEO")],
    "tx_types": [_r(transactionType=t, reportingCik=str(i)) for i, t in
                 enumerate(["S-Sale", "A-Award", "M-Exempt", "G-Gift", "F-InKind", "", None, 5, "P-Purchase"])],
    "disposition_form5": [_r(acquisitionOrDisposition="D"), _r(formType="5", reportingCik="2"),
                          _r(acquisitionOrDisposition=None, formType=None, reportingCik="3"),
                          _r(acquisitionOrDisposition="", formType="", reportingCik="4"),
                          _r(formType=" 4/a ", reportingCik="5")],
    "security_names": [_r(securityName="Series B Preferred Stock"), _r(securityName="Warrants", reportingCik="2"),
                       _r(securityName="Common Stock", reportingCik="3"),
                       _r(securityName="American Depositary Shares", reportingCik="4"),
                       _r(securityName="Class C Capital Stock", reportingCik="5")],
    "symbols": [_r(symbol=s, reportingCik=str(i)) for i, s in enumerate(
        ["BRK.B", "BRK-B", "BRK.A", "  gme ", "NASDAQ: XYZ", "TOOLONGSYMBOL", "", "--", "N/A", "1ABC",
         "AB CD", "A.B.C", "none", "RZLVW"])],
    "bad_numbers": [_r(**{f: v}, reportingCik=str(i)) for i, (f, v) in enumerate([
        ("securitiesTransacted", 0), ("securitiesTransacted", -5), ("securitiesTransacted", float("nan")),
        ("securitiesTransacted", float("inf")), ("securitiesTransacted", "lots"), ("securitiesTransacted", None),
        ("securitiesTransacted", "nan"), ("securitiesTransacted", "1,000"), ("price", 0), ("price", -1),
        ("price", float("nan")), ("price", "n/a"), ("price", None), ("price", "-inf"),
    ])],
    "numeric_strings": [_r(securitiesTransacted="1000", price="12.5"), _r(securitiesTransacted=" 2000 ", price="1e1",
                                                                           reportingCik="2")],
    "unit_errors": [_r(securitiesTransacted=150_000_000, securitiesOwned=4_000_000),
                    _r(securitiesTransacted=150_000, securitiesOwned=4_000_000, reportingCik="2"),
                    _r(securitiesTransacted=150_000, securitiesOwned=150_000, reportingCik="3"),
                    *[_r(securitiesOwned=o, reportingCik=f"o{i}") for i, o in
                      enumerate([None, 0, "n/a", float("nan"), -1])],
                    _r(securitiesTransacted=1e308, price=1e308, reportingCik="9"),
                    _r(securitiesTransacted=1_000_000_000, price=10.0, reportingCik="10"),
                    _r(symbol="TSLA", securitiesTransacted=2_570_000, price=389.0, reportingCik="11")],
    "dates": [_r(filingDate="2026-08-24", transactionDate="2026-08-22"),
              _r(filingDate="2026-08-23", transactionDate="2026-08-21", reportingCik="2"),
              _r(filingDate="2026-09-25", transactionDate="2026-09-23", reportingCik="3"),
              _r(filingDate="2026-09-26", transactionDate="2026-09-24", reportingCik="4"),
              _r(filingDate="2026-09-20", transactionDate="2026-07-01", reportingCik="5"),
              _r(filingDate="2026-09-20", transactionDate="2026-08-21", reportingCik="6"),
              _r(filingDate="2026-09-20", transactionDate="2026-08-20", reportingCik="7"),
              _r(filingDate="2026-09-20", transactionDate="2026-09-22", reportingCik="8"),
              _r(filingDate="2026-09-20", transactionDate="2026-09-21", reportingCik="9"),
              *[_r(filingDate=b, reportingCik=f"f{i}") for i, b in enumerate([None, "", "N/A", "2026-13-40", 20260920])],
              _r(transactionDate="soon", reportingCik="10"),
              _r(filingDate="2026-09-16", transactionDate="2026-09-16", reportingCik="11"),
              _r(filingDate="2026-09-16T23:59:59", transactionDate="2026-09-15", reportingCik="12"),
              _r(filingDate="2026-09-23 00:00:00", transactionDate="2026-09-22", reportingCik="13")],
    "reporters": [_r(reportingCik=None), _r(reportingCik=None, reportingName="", symbol="FOX"),
                  _r(reportingCik="None", reportingName=None, symbol="UBER"),
                  _r(reportingCik="0000000", reportingName="DOE JANE", symbol="AMD"),
                  _r(reportingCik=1903011.0, symbol="NVDA"), _r(reportingCik="0001903011", symbol="NVDA")],
    "cross_filing": [_r(filingDate="2026-09-19"), _r(filingDate="2026-09-21")],
    "same_filing_fills": [_r(), _r(), _r(securitiesTransacted=1000, price=25.0),
                          _r(securitiesTransacted=2000, price=25.1), _r(securitiesTransacted=3000, price=24.9)],
    "amend_full": [_r(securitiesTransacted=10_000, formType="4", filingDate="2026-09-19"),
                   _r(securitiesTransacted=12_000, formType="4/A", filingDate="2026-09-21")],
    "amend_latest": [_r(securitiesTransacted=10_000, formType="4", filingDate="2026-09-18"),
                     _r(securitiesTransacted=11_000, formType="4/A", filingDate="2026-09-19"),
                     _r(securitiesTransacted=12_000, formType="4/A", filingDate="2026-09-21")],
    "amend_partial_add": [_r(securitiesTransacted=100_000, price=25.0, filingDate="2026-09-19"),
                          _r(securitiesTransacted=50_000, price=26.0, filingDate="2026-09-19"),
                          _r(securitiesTransacted=2_000, price=25.5, filingDate="2026-09-21", formType="4/A")],
    "amend_partial_fix": [_r(securitiesTransacted=100_000, price=25.0, filingDate="2026-09-19"),
                          _r(securitiesTransacted=50_000, price=26.0, filingDate="2026-09-19"),
                          _r(securitiesTransacted=100_000, price=25.1, filingDate="2026-09-21", formType="4/A")],
    "amend_two_partial_lines": [_r(securitiesTransacted=100_000, price=25.0, filingDate="2026-09-19"),
                                _r(securitiesTransacted=50_000, price=26.0, filingDate="2026-09-19"),
                                _r(securitiesTransacted=30_000, price=27.0, filingDate="2026-09-19"),
                                _r(securitiesTransacted=50_000, price=26.5, filingDate="2026-09-21", formType="4/A"),
                                _r(securitiesTransacted=31_000, price=27.0, filingDate="2026-09-21", formType="4/A")],
    "amend_own_day": [_r(transactionDate="2026-09-15", filingDate="2026-09-17", securitiesTransacted=1000),
                      _r(transactionDate="2026-09-18", filingDate="2026-09-19", securitiesTransacted=2000),
                      _r(transactionDate="2026-09-18", filingDate="2026-09-21", securitiesTransacted=2500,
                         formType="4/A")],
    "amend_only": [_r(formType="4/A", filingDate="2026-09-21"), _r(formType="4/A", filingDate="2026-09-21")],
    "holdings": [_r(directOrIndirect="D"), _r(directOrIndirect="I"), _r(directOrIndirect=None),
                 _r(directOrIndirect=" i ")],
    "co_ceos": [_r(reportingCik="1", typeOfOwner="officer: Co-CEO"),
                _r(reportingCik="2", typeOfOwner="officer: Co-Chief Executive Officer", reportingName="ROE JANE")],
    "ranking": [_r(symbol="UBER", securitiesTransacted=100_000, price=100.0, reportingCik="3"),
                _r(symbol="GME", securitiesTransacted=1_870_800, price=25.0, reportingCik="1"),
                _r(symbol="FOX", securitiesTransacted=205_400, price=50.0, reportingCik="2"),
                _r(symbol="AAA", securitiesTransacted=1, price=99_999.99, reportingCik="4"),
                _r(symbol="BBB", securitiesTransacted=1, price=100_000.0, reportingCik="5"),
                _r(symbol="CCC", securitiesTransacted=2000, price=30.0, reportingCik="6"),
                _r(symbol="CCC", securitiesTransacted=2000, price=30.0, reportingCik="7"),
                _r(symbol="ZZZ", filingDate="2026-09-20", reportingCik="8"),
                _r(symbol="YYY", filingDate="2026-09-20", reportingCik="9"),
                _r(symbol="MMM", filingDate="2026-09-22", transactionDate="2026-09-21", reportingCik="10")],
}
ALL_ROWS = [row for rows in CASES.values() for row in rows]
NOWS = [NOW, NOW.replace(hour=0, minute=0, second=0), NOW.replace(hour=23, minute=59, second=59)]


def _window(now, days):
    return now.date() - timedelta(days=days), now.date() + timedelta(days=2)


def _mine(rows, now, days, roles=("ceo",)):
    start, end = _window(now, days)
    return ib.extract_insider_buys(rows, window_start=start, window_end=end, roles=roles)


def _theirs(rows, now, days):
    return ssvc._extract_ceo_buys(rows, now=now, window_days=days)


def _multiset(buys, n=11):
    return Counter(tuple(b)[:n] for b in buys)


@pytest.mark.parametrize("case", sorted(CASES))
@pytest.mark.parametrize("now", NOWS, ids=["noon", "00:00", "23:59:59"])
@pytest.mark.parametrize("days", [7, 30])
def test_extraction_equals_signals_as_a_multiset(case, now, days):
    rows = CASES[case]
    mine, theirs = _mine(rows, now, days), _theirs(rows, now, days)
    assert _multiset(mine) == _multiset(theirs)
    assert all(b.role == "ceo" for b in mine)


@pytest.mark.parametrize("now", NOWS, ids=["noon", "00:00", "23:59:59"])
@pytest.mark.parametrize("days", [1, 7, 30])
def test_the_whole_corpus_at_once_and_its_ranking(now, days):
    mine, theirs = _mine(ALL_ROWS, now, days), _theirs(ALL_ROWS, now, days)
    assert theirs, "anti-vacuity: the corpus must produce buys"
    assert _multiset(mine) == _multiset(theirs)
    ranked = ib.rank_buy_symbols(mine, min_dollars=100_000)
    group = ssvc._rank_ceo_buys(theirs, top_n=10_000)
    assert [s for s, _, _ in ranked] == [e.symbol for e in group.entries]
    assert [round(t, 2) for _, t, _ in ranked] == pytest.approx([e.value for e in group.entries])


def test_the_corpus_is_not_vacuous():
    """Every interesting branch is exercised: amendments replace, duplicates collapse, the
    window edges bite, and several symbols rank."""
    theirs = _theirs(ALL_ROWS, NOW, 30)
    forms = {b.form_type for b in theirs}
    assert {"4", "4/A"} <= forms
    assert len({b.symbol for b in theirs}) >= 8
    assert len(_theirs(CASES["cross_filing"], NOW, 30)) == 1
    assert sorted(b.shares for b in _theirs(CASES["amend_partial_add"], NOW, 30)) == [2_000, 50_000, 100_000]
    assert len(_theirs(CASES["dates"], NOW, 7)) < len(_theirs(CASES["dates"], NOW, 30))


def test_rank_matches_signals_floor_and_ties():
    buys = _mine(CASES["ranking"], NOW, 30)
    ranked = ib.rank_buy_symbols(buys, min_dollars=100_000)
    syms = [s for s, _, _ in ranked]
    assert syms[:3] == ["GME", "FOX", "UBER"]
    assert "AAA" not in syms and "BBB" in syms and "CCC" in syms
    assert syms.index("MMM") < syms.index("YYY") < syms.index("ZZZ")
    assert ib.rank_buy_symbols([], min_dollars=1) == []
    assert ib.rank_buy_symbols(buys, min_dollars=1e12) == []


# ── the window is explicit ────────────────────────────────────────────────────

def test_window_edges_are_inclusive_and_dates_only():
    start, end = date(2026, 9, 14), date(2026, 9, 20)
    rows = [_r(filingDate=d.isoformat(), transactionDate=d.isoformat(), reportingCik=str(i))
            for i, d in enumerate([start - timedelta(days=1), start, end, end + timedelta(days=1)])]
    got = ib.extract_insider_buys(rows, window_start=start, window_end=end, roles=("ceo",))
    assert sorted(b.filing_date for b in got) == ["2026-09-14", "2026-09-20"]


@pytest.mark.parametrize("kwargs", [
    {"window_start": datetime(2026, 9, 14), "window_end": date(2026, 9, 20), "roles": ("ceo",)},
    {"window_start": date(2026, 9, 21), "window_end": date(2026, 9, 20), "roles": ("ceo",)},
    {"window_start": "2026-09-14", "window_end": date(2026, 9, 20), "roles": ("ceo",)},
    {"window_start": date(2026, 9, 14), "window_end": date(2026, 9, 20), "roles": ()},
    {"window_start": date(2026, 9, 14), "window_end": date(2026, 9, 20), "roles": ["ceo"]},
    {"window_start": date(2026, 9, 14), "window_end": date(2026, 9, 20), "roles": ("boss",)},
])
def test_bad_arguments_raise(kwargs):
    with pytest.raises(ValueError):
        ib.extract_insider_buys([_r()], **kwargs)


@pytest.mark.parametrize("rows", [None, "rows", {"a": 1}, 3, []])
def test_non_list_input_is_empty(rows):
    assert ib.extract_insider_buys(rows, window_start=date(2026, 9, 1), window_end=date(2026, 9, 30),
                                   roles=("ceo",)) == []


# ── pinned divergences ────────────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["securitiesTransacted", "price"])
def test_divergence_a_bool_is_never_a_number(field):
    row = _r(**{field: True}, price=150_000.0) if field == "securitiesTransacted" else _r(price=True)
    assert len(_theirs([row], NOW, 30)) == 1, "signals reads True as 1.0 (the divergence is real)"
    assert _mine([row], NOW, 30) == []


def test_divergence_a_bool_holding_is_not_a_holding():
    row = _r(securitiesOwned=True)
    assert _theirs([row], NOW, 30) == []          # signals: 100,000 shares > 1.0 owned → unit error
    assert len(_mine([row], NOW, 30)) == 1        # here: no usable holding → cannot judge → keep


def test_price_plausible_matches_signals_without_a_required_reference():
    cases = [(25.0, 24.0), (2.6, 25.0), (249, 25.0), (2.4, 25.0), (260, 25.0), (25.0, None), (25.0, 0),
             (25.0, -5), (25.0, float("nan")), (25.0, float("inf")), (25.0, "x"), (None, 25.0), (0, 25.0),
             (-1, 25.0), (float("nan"), 25.0), (float("inf"), 25.0), ("25", "24")]
    for row, ref in cases:
        assert ib.price_plausible(row, ref, require_reference=False) == ssvc._ceo_price_plausible(row, ref), (row, ref)


def test_divergence_a_missing_reference_rejects_when_required():
    for ref in (None, 0, -5, float("nan"), float("inf"), "x", True):
        assert ib.price_plausible(25.0, ref, require_reference=True) is False, ref
    assert ib.price_plausible(25.0, 24.0, require_reference=True) is True
    assert ib.price_plausible(2.4, 25.0, require_reference=True) is False
    assert ib.price_plausible(True, 1.0, require_reference=False) is False      # bool row price
    assert ib.price_plausible(26.0, 25.0, band=1.01, require_reference=True) is False


def test_require_reference_has_no_default():
    with pytest.raises(TypeError):
        ib.price_plausible(25.0, 24.0)  # type: ignore[call-arg]


def test_the_copied_constants_equal_signals():
    assert ib.MAX_FILING_LAG_DAYS == ssvc._CEO_MAX_FILING_LAG_DAYS
    assert ib.MAX_ROW_DOLLARS == ssvc._CEO_MAX_ROW_DOLLARS
    assert ib.PRICE_BAND == ssvc._CEO_PRICE_BAND
    assert ib.SYMBOL_RE.pattern == ssvc._CEO_SYMBOL_RE.pattern
    assert ib.BAD_SYMBOLS == frozenset(ssvc._BAD_SYMBOLS)
    assert list(ib.InsiderBuy._fields[:11]) == list(ssvc._CeoBuy._fields)
    assert ib.InsiderBuy._fields[11:] == ("role", "company_cik")


@pytest.mark.parametrize("raw, cik", [
    ("0001326380", "1326380"), (1326380, "1326380"), ("1326380", "1326380"), (" 0001326380 ", "1326380"),
    (None, None), ("", None), ("0000000000", None), ("ZQX", None), (True, None), (float("nan"), None),
])
def test_the_issuer_cik_rides_along_and_changes_nothing(raw, cik):
    """Appended after the compared fields (review data:F5): the line's issuer CIK, normalised;
    extraction, supersession and ranking never read it."""
    rows = [_r(companyCik=raw), _r(companyCik=raw, symbol="AAA", reportingCik="9")]
    got = _mine(rows, NOW, 30)
    assert sorted((b.symbol, b.company_cik) for b in got) == [("AAA", cik), ("GME", cik)]
    plain = _mine([{k: v for k, v in r.items() if k != "companyCik"} for r in rows], NOW, 30)
    assert _multiset(got) == _multiset(plain) and all(b.company_cik is None for b in plain)


# ── roles ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,role", [
    # CEO: the shared rule, and it wins over CFO and director
    ("officer: Chief Executive Officer", "ceo"), ("director, officer: President and CEO", "ceo"),
    ("officer: Co-CEO", "ceo"), ("officer: CEO/CFO", "ceo"), ("officer: Interim CEO & CFO", "ceo"),
    ("director, officer, other: President & CEO", "ceo"),
    # CFO
    ("officer: Chief Financial Officer", "cfo"), ("officer: CFO", "cfo"), ("director, officer: EVP & CFO", "cfo"),
    ("officer: Senior Vice President and Chief Financial Officer", "cfo"),
    ("officer: EVP, Chief Financial Officer", "cfo"), ("officer: Interim CFO", "cfo"),
    ("officer: CFO and Treasurer", "cfo"), ("officer: CFO, Treasurer and Secretary", "cfo"),
    ("officer: Chief Financial Officer of the Company", "cfo"), ("director, officer, other: CFO", "cfo"),
    ("officer: Chief Financial & Operating Officer", "cfo"),
    # not the sitting issuer CFO
    ("officer: Former CFO", None), ("officer: Retired Chief Financial Officer", None),
    ("officer: CFO-Elect", None), ("officer: Ex-CFO", None), ("officer: Deputy CFO", None),
    ("officer: Vice CFO", None), ("officer: Assistant CFO", None), ("officer: Division CFO", None),
    ("officer: Segment Chief Financial Officer", None), ("officer: Chief of Staff to the CFO", None),
    ("officer: CFO, Consumer Banking", None), ("officer: CFO - Europe", None),
    ("officer: CFO of Subsidiary Bank", None), ("officer: CFO Europe", None),
    ("officer: Chief Financial Officer Asia Pacific", None), ("officer: Spouse of CFO", None),
    ("10 percent owner, other: Chief Financial Officer", None),
    # a former CFO who sits on the board is a director
    ("director, officer: Former CFO", "director"), ("director, other: CFO", "director"),
    # director: a role FLAG only
    ("director", "director"), ("Director", "director"), ("director, 10 percent owner", "director"),
    ("director, officer: COO", "director"), ("director, other: Retired CEO", "director"),
    ("10 percent owner, director", "director"),
    ("officer: VP, Director of Sales", None), ("officer: Director of Finance", None),
    ("officer: VP, Director", None), ("officer: Senior VP, other: director", None),
    ("officer: Chief Operating Officer", None), ("10 percent owner", None), ("other: director", None),
    ("", None), (None, None), (5, None), (["director"], None),
    # Review rounds 5-6 tied incoming / designate(d) / future / successor to the CEO / CFO phrase
    # in the shared rules; round 7 restored the committed rule (and its plain CFO analogue) and
    # moved the question to the Company Weekly adapter, which refuses ANY transitional word in a
    # CEO / CFO row's raw role text (`role_uncertain`). The rows below hold at the restored rule:
    # a sitting officer with a transitional SECOND title keeps the role.
    ("officer: CFO and CEO-Designate", "cfo"), ("officer: CFO, Successor Trustee", "cfo"),
    ("officer: EVP, CFO & Future Chair", "cfo"),
    ("officer: President, CEO and Incoming Chairman", "ceo"),
    ("officer: Chief Executive Officer and Chairman-Designate", "ceo"),
])
def test_insider_role_table(title, role):
    assert ib.insider_role(title) == role


def test_the_cfo_not_sitting_rule_is_the_plain_analogue_of_the_ceo_one():
    """Review round 7: `_CFO_NOT_SITTING_RE` is exactly the committed CEO rule with "ceo" → "cfo"
    (no tied incoming / designate / future / successor clauses); the Company Weekly adapter's
    `role_uncertain` gate is where a transitional word anywhere refuses a CFO."""
    from app.services import _insider_common as ic
    assert ib._CFO_NOT_SITTING_RE.pattern == ic._NOT_SITTING_RE.pattern.replace("ceo", "cfo")
    assert ib._CFO_NOT_SITTING_RE.flags == ic._NOT_SITTING_RE.flags


def test_officer_title_alias_is_public_and_total():
    assert officer_title("director, officer: Chief Financial Officer") == "Chief Financial Officer"
    assert officer_title("director, officer, other: President & CEO") == "President & CEO"
    assert officer_title("director") == ""
    for bad in (None, 5, ["officer: CFO"], b"officer: CFO"):
        assert officer_title(bad) == ""


def test_cfo_and_director_roles_extract_with_their_role():
    rows = [_r(reportingCik="1", typeOfOwner="officer: Chief Financial Officer", symbol="AAA"),
            _r(reportingCik="2", typeOfOwner="director", symbol="BBB"),
            _r(reportingCik="3", typeOfOwner="director, officer: CEO", symbol="CCC"),
            _r(reportingCik="4", typeOfOwner="officer: VP, Director of Sales", symbol="DDD"),
            _r(reportingCik="5", typeOfOwner="officer: Deputy CFO", symbol="EEE")]
    got = _mine(rows, NOW, 30, roles=("cfo", "director"))
    assert sorted((b.symbol, b.role) for b in got) == [("AAA", "cfo"), ("BBB", "director")]
    only_ceo = _mine(rows, NOW, 30, roles=("ceo",))
    assert [(b.symbol, b.role) for b in only_ceo] == [("CCC", "ceo")]
    every = _mine(rows, NOW, 30, roles=("ceo", "cfo", "director"))
    assert sorted(b.symbol for b in every) == ["AAA", "BBB", "CCC"]


def test_the_module_is_fmp_free_and_signals_free():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(ib))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    app_imports = {m for m in imported if m.startswith("app.")}
    assert app_imports == {"app.services._insider_common"}, app_imports
