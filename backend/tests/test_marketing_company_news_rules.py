"""`company_news_rules` — the pure layer of Company Weekly (contract D4; owner decisions 2026-10-09).

Pinned here:

* the fail-closed person-name renderer (the contract's table, both input shapes, the casing and
  order rules), `corroborates` with honorifics, a missing given-names list, and the Congress
  block-list (roster + registry politicians; an unreadable roster turns every person slot
  role-only, logged at ERROR);
* `display_company_name`, `canonical_symbol` / `symbol_problem`, every `company_from_profile`
  reason, the exchange-set parity with the 13F builder, `filer_name_problem` (owner decision 5:
  a filer named after a person is allowed);
* ledger keys and source labels, verbatim;
* the record ↔ dict round trip after a JSONB-style key shuffle and numeric re-serialisation, the
  strictness of `record_from_dict`, the fact sheet, and NaN / ±inf refusals everywhere;
* the reason-code sets, `SERIES_CLASS`, and the Money Map pool (owner decision 2).

Hermetic: no network, no Supabase. The roster / registry tests point the module at temp files and
clear its cache (the `_fresh_congress` fixture restores the real state afterwards).
"""

import copy
import json
import logging
import math
import random
from dataclasses import fields, replace
from datetime import date, datetime

import pytest

from app.services.marketing import company_news_rules as R
from app.services.marketing import compliance

UUID = "3f6c1a2e-9b7d-4c1e-8a2b-0d9e8f7a6b5c"


@pytest.fixture
def fresh_congress(monkeypatch):
    """Point the block-list at test files; restore the real one (and its cache) afterwards."""
    R._congress_state.cache_clear()
    yield monkeypatch
    monkeypatch.undo()
    R._congress_state.cache_clear()


# ── sample records (one per series family) ────────────────────────────────────

def co(sym="GME", name="GameStop"):
    return R.CompanyRef(symbol=sym, name=name)


def purchase(sym="GME", name="GameStop", amount=2_500_000.0, role="ceo", person="Ryan Cohen", **kw):
    base = dict(company=co(sym, name), role=role, person_name=person, amount_usd=amount, shares=100_000.0,
                purchases=2, earliest_trade_date=date(2026, 11, 10), latest_trade_date=date(2026, 11, 11),
                filing_dates=(date(2026, 11, 11), date(2026, 11, 12)), holding="direct", amended=False)
    base.update(kw)
    return R.InsiderPurchase(**base)


def insider_week(series="ceo_buys", rows=None):
    rows = rows if rows is not None else (
        purchase(), purchase("FOX", "Fox", 1_000_000.0, person=None, holding="mixed", amended=True))
    return R.InsiderBuysWeek(series=series, window_start=date(2026, 11, 9), window_end=date(2026, 11, 15),
                             rows=rows)


def move(sym="CRWV", name="CoreWeave", kind="newly_reported", **kw):
    base = dict(company=co(sym, name), move=kind, shares=1_000.0, prev_shares=None, value_usd=5_000_000.0,
                listed_on=date(2026, 7, 15))      # inside 2026-Q3: after the previous quarter's end
    base.update(kw)
    return R.ThirteenFMove(**base)


def thirteen_f(**kw):
    base = dict(series="thirteen_f", filer_name="Berkshire Hathaway", filer_cik="0001067983", filer_symbol="BRK-B",
                period="2026-Q3", period_end=date(2026, 9, 30), filed_on=date(2026, 11, 14), amended_on=None,
                total_value_usd=300e9, position_count=45,
                moves=(move(), move("AAPL", "Apple", "decreased", shares=300.0, prev_shares=400.0, listed_on=None,
                                    value_usd=60e9)),
                counts=(("newly_reported", 1), ("decreased", 3)))
    base.update(kw)
    return R.ThirteenFFiling(**base)


def congress(**kw):
    base = dict(series="congress_count", company=co("NVDA", "NVIDIA"), month="2026-11", members=5,
                fetched_on=date(2026, 12, 8))
    base.update(kw)
    return R.CongressCount(**base)


def stake(**kw):
    base = dict(series="company_stakes", stake_id=UUID, investor=co("NVDA", "NVIDIA"), investee_name="Nscale",
                investee=None, kind="private", value_usd=1e9, value_basis="invested", ownership_pct=None,
                as_of=date(2026, 9, 15), verified_on=date(2026, 10, 1), source_title="NVIDIA Form 10-Q",
                background=None, listed_since=None, local_listing=None, is_new=True)
    base.update(kw)
    return R.CompanyStake(**base)


def earnings(**kw):
    base = dict(series="earnings", company=co("COST", "Costco"), report_date=date(2027, 1, 12),
                period_end=date(2026, 11, 30), eps_actual=4.5, eps_estimate=4.2, revenue_actual=66e9,
                revenue_estimate=65e9)
    base.update(kw)
    return R.EarningsReport(**base)


def money_map(**kw):
    base = dict(series="money_map", company=co("AAPL", "Apple"), fiscal_year="2025", period_end=date(2025, 9, 27),
                segments=(R.Segment("iPhone", 200e9), R.Segment("Services", 100e9), R.Segment("Mac", 50e9)),
                other_usd=40e9, eliminations_usd=-1e9, revenue_usd=389e9, gross_profit_usd=180e9,
                operating_profit_usd=120e9, net_income_usd=95e9)
    base.update(kw)
    return R.MoneyMap(**base)


def theme(n=6, **kw):
    syms = ["IONQ", "RGTI", "QBTS", "QUBT", "IBM", "HON", "GOOGL", "MSFT"][:n]
    members = tuple(R.ThemeMember(co(s, f"Company {s}"), "Systems" if i % 2 == 0 else None,
                                  0.6 if i % 2 == 0 else None, "2025" if i % 2 == 0 else None)
                    for i, s in enumerate(syms))
    base = dict(series="theme_explainer", slug="quantum-computing", title="Quantum computing", members=members,
                tickers_as_of=date(2026, 10, 1))
    base.update(kw)
    return R.ThemeExplainer(**base)


SAMPLES = {
    "ceo_buys": insider_week,
    "insider_buys": lambda: insider_week("insider_buys", (purchase(role="cfo"), purchase("FOX", "Fox", 1e6,
                                                                                         role="director",
                                                                                         person=None))),
    "thirteen_f": thirteen_f,
    "congress_count": congress,
    "company_stakes": stake,
    "earnings": earnings,
    "money_map": money_map,
    "theme_explainer": theme,
}


# ── the person-name renderer (contract D4.3) ──────────────────────────────────

@pytest.mark.parametrize("raw,out", [
    ("COHEN RYAN", "Ryan Cohen"),
    ("Sicilia Michael D", "Michael D. Sicilia"),
    ("Smith-Jones Anna", "Anna Smith-Jones"),
    ("ELLISON LAWRENCE JOSEPH", None),       # a full middle word
    ("SMITH JOHN JR", None),                 # a suffix
    ("MCDONALD RONALD", None),               # MC/MAC: title-casing breaks it
    ("O'BRIEN SEAN", None),                  # apostrophe
    ("VAN DER BERG ALICE", None),            # particles, 4 tokens
    ("HUANG JEN HSUN", None),                # third token is not an initial
    ("SCOTT RICK", None),                    # LAST is a given name: order unknowable
    ("MÜLLER HANS", None),                   # accent
    ("BERKSHIRE HATHAWAY INC", None),        # an entity
    # the second shape
    ("Cohen, Ryan", "Ryan Cohen"), ("COHEN, RYAN", "Ryan Cohen"), ("Sicilia, Michael D.", "Michael D. Sicilia"),
    ("SICILIA MICHAEL D.", "Michael D. Sicilia"), ("  COHEN   RYAN  ", "Ryan Cohen"),
    # refusals on shape and alphabet
    ("COHEN", None), ("RYAN COHEN", None), ("COHEN RYAN 3", None), ("COHEN2 RYAN", None),
    ("Cohen, Ryan, Jr", None), ("COHEN, RYAN JOSEPH", None), ("Cohen Ryan,", None), ("COHEN RYAN .", None),
    ("ST. JOHN MARY", None), ("COHEN JEAN-PAUL", None), ("COHEN-SMITH-JONES ANNA", None),
    ("DE ANNA", None), ("JR ANNA", None), ("TRUST ANNA", None), ("FAMILY ANNA", None), ("SMITH-MCKAY ANNA", None),
    ("MACK JOHN", None), ("SMITH JOHN V", None), ("COHEN RY", None), ("COHEN ZQXWV", None), ("AB", None),
    ("X ANNA", None), ("COHEN​RYAN", None),
    # casing: mixed input keeps its case only when every token (and hyphen part) starts upper
    ("cohen ryan", None), ("Cohen ryan", None), ("COHEN Ryan", None), ("Smith-jones Anna", None),
    ("Sicilia Michael d", None),
    (None, None), (5, None), (["COHEN", "RYAN"], None),
])
def test_render_person_name_table(raw, out):
    assert R.render_person_name(raw) == out


def test_render_caps_the_output_and_never_equals_the_company():
    long_last = "Q" * 36
    assert len(f"Anna {long_last}") == 41
    assert R.render_person_name(f"{long_last} ANNA") is None
    assert R.render_person_name(f"{'Q' * 35} ANNA") == "Anna " + "Q" + "q" * 34
    assert R.render_person_name("COHEN RYAN", company="Ryan Cohen") is None
    assert R.render_person_name("COHEN RYAN", company="GameStop") == "Ryan Cohen"


def test_a_missing_given_names_list_renders_every_name_none(monkeypatch):
    monkeypatch.setattr(compliance, "given_names", lambda: frozenset())
    for raw in ("COHEN RYAN", "Sicilia Michael D", "Smith-Jones Anna"):
        assert R.render_person_name(raw) is None


def test_a_missing_given_names_file_renders_every_name_none(monkeypatch, tmp_path):
    monkeypatch.setattr(compliance, "GIVEN_NAMES_PATH", tmp_path / "absent.txt")
    compliance.given_names.cache_clear()
    try:
        assert compliance.given_names() == frozenset()
        assert R.render_person_name("COHEN RYAN") is None
    finally:
        monkeypatch.undo()
        compliance.given_names.cache_clear()
    assert R.render_person_name("COHEN RYAN") == "Ryan Cohen"


@pytest.mark.parametrize("rendered,ceo,ok", [
    ("Ryan Cohen", "Mr. Ryan Cohen", True), ("Ryan Cohen", "Ryan Cohen", True), ("Ryan Cohen", "Dr Ryan Cohen", True),
    ("Ryan Cohen", "Mr. Ryan Cohen Jr.", True), ("Ryan Cohen", "MR. RYAN COHEN", True),
    ("Michael D. Sicilia", "Mr. Michael Sicilia", True), ("Michael D. Sicilia", "Michael D. Sicilia", True),
    ("Anna Smith-Jones", "Ms. Anna Smith-Jones", True), ("Anna Smith-Jones", "Ms. Anna Smith", False),
    ("Ryan Cohen", "Mr. Ryan Smith", False), ("Ryan Cohen", "Mr. Cohen", False), ("Ryan Cohen", "Ryan", False),
    ("Ryan Cohen", "Mr. Bryan Cohenson", False), ("Ryan Cohen", "Sir Ryan Cohen", True),
    ("Ryan Cohen", None, False), (None, "Ryan Cohen", False), ("Ryan", "Ryan Cohen", False), ("Ryan Cohen", 5, False),
])
def test_corroborates(rendered, ceo, ok):
    assert R.corroborates(rendered, ceo) is ok


# ── the Congress block-list ───────────────────────────────────────────────────

def test_the_real_block_list_loads_the_roster_and_the_registry():
    assert R.person_names_allowed() is True
    names = R.congress_names()
    assert len(names) > 795
    assert all(len(k.split()) >= 2 for k in names), "a surname alone must never be a key"
    # registry politicians, roster-only members, nicknames, official names, hyphens, accents
    for k in ("nancy pelosi", "ted cruz", "mark kelly", "ro khanna", "alma adams", "andy barr", "garland barr",
              "chuy garcia", "jesus garcia", "jenniffer gonzalez-colon", "jenniffer gonzalez colon",
              "monica de la cruz", "nikki budzinski", "nicole budzinski", "michael conaway", "luis correa"):
        assert k in names, k


@pytest.mark.parametrize("name,hit", [
    ("PELOSI NANCY", True), ("Nancy Pelosi", True), ("Pelosi, Nancy", True), ("KELLY MARK", True),
    ("Mark E. Kelly", True), ("GARCIA JESUS G", True), ("Chuy Garcia", True), ("De La Cruz Monica", True),
    ("GONZALEZ-COLON JENNIFFER", True), ("Ted Cruz Jr", True), ("Alma Adams", True),
    # "LAST FIRST MIDDLE", the usual SEC reporting form: only the ordered-pair loop finds it
    # (review tests:F2 — without it, the renderer's role-only fallback would point at the member)
    ("PELOSI NANCY PATRICIA", True), ("KELLY MARK EDWARD", True), ("Pelosi, Nancy Patricia", True),
    ("Pelosi", False), ("Ryan Cohen", False), ("Kelly", False), ("Mark", False), ("", False), (None, False), (5, False),
])
def test_is_congress_name(name, hit):
    assert R.is_congress_name(name) is hit


def test_congress_name_hits_are_word_bounded_and_never_a_surname_alone():
    assert R.congress_name_hits("A filing by Nancy Pelosi's spouse") == ["nancy pelosi"]
    assert R.congress_name_hits("NANCY PELOSI disclosed") == ["nancy pelosi"]
    assert R.congress_name_hits("Pelosi and Cruz said") == []
    assert R.congress_name_hits("Nancy Pelosiville") == []
    assert R.congress_name_hits("Ted Cruz and Mark Kelly") == ["mark kelly", "ted cruz"]
    assert R.congress_name_hits("") == [] and R.congress_name_hits(None) == []


# ── review round 9: legal names, the surname rule, the roster's age ───────────

#: EDGAR reporting names of members the roster lists only by the name they go by (r9 #0 / #26:
#: the probe named "Joseph Manchin" and "Joshua S. Gottheimer" in captions).
_R9_LEGAL_NAMES = ("MANCHIN JOSEPH", "COTTON THOMAS B", "GOTTHEIMER JOSHUA S", "CRUZ RAFAEL E",
                   "CRUZ RAFAEL EDWARD", "CRUZ RAFAEL", "ROMNEY WILLARD M", "ROMNEY WILLARD", "JOHNSON WILLIAM L",
                   "JOHNSON JAMES MICHAEL", "SCOTT TIMOTHY E", "SCOTT RICHARD L", "TUBERVILLE THOMAS H",
                   "CRENSHAW DANIEL", "KHANNA ROHIT", "MORENO BERNARDO", "HILL JAMES FRENCH", "CORREA JOSE LUIS",
                   "SCHULTZ DEBORAH WASSERMAN", "GOTTHEIMER JOSH", "Joshua S. Gottheimer", "Thomas Cotton",
                   "Gottheimer, Joshua", "MCCONNELL ADDISON M", "ROUNDS MARION MICHAEL", "OWENS CLARENCE B",
                   "THOMPSON BENNIE G", "OHALLERAN THOMAS",
                   # only an initial ties these: the member goes by the middle name, or a legal form
                   # begins with an initial ("J. French Hill", "J. Luis Correa")
                   "KRISHNAMOORTHI SUBRAMANIAN R", "HILL JAMES F", "CORREA JOSE L",
                   # only a prefix ties these ("Mo" Brooks, "Thom" Tillis); two letters with no vowel
                   # are initials or an abbreviation ("TJ" Cox, "Wm." Lacy Clay)
                   "BROOKS MORRIS J", "TILLIS THOMAS R", "COX TERRANCE J", "CLAY WILLIAM")
#: Names the surname rule must NOT take (fixture people of the suites, investors, near misses).
_R9_NOT_MEMBERS = ("SMITH JOHN", "SMITH JOHN JR", "COHEN RYAN", "DOE JANE", "ROE RICHARD", "BROWN DAVID",
                   "TAYLOR EMMA", "LEE ANNA", "BOND JAMES", "SICILIA MICHAEL D", "Warren Buffett", "PELOSI",
                   "Ryan Cohen", "John Smith", "GOTTHEIMER ANNA", "CRUZ MARIA",
                   # a particle is a given name only when it IS one: "da" is not "David" Scott
                   "SCOTT ANA DA SILVA")


@pytest.mark.parametrize("name", _R9_LEGAL_NAMES)
def test_a_members_legal_name_is_a_congress_name(name):
    assert R.is_congress_name(name), name


@pytest.mark.parametrize("name", _R9_NOT_MEMBERS)
def test_the_surname_rule_never_takes_another_first_name(name):
    assert not R.is_congress_name(name), name


def test_a_rendered_legal_name_is_found_in_text_and_a_common_word_is_not():
    assert R.congress_name_hits("The filing names Joshua S. Gottheimer, a director of Fox.") == ["joshua gottheimer"]
    assert R.congress_name_hits("Thomas Cotton and Joseph Manchin said") == ["joseph manchin", "thomas cotton"]
    assert R.congress_name_hits("Rafael Cruz") == ["rafael cruz"]
    # the text form needs capitalised words and a first name related to the member's own
    for clean in ("How will Johnson & Johnson grow?", "will johnson", "Pelosi and Cruz said", "Morgan Stanley",
                  "Charles Schwab", "The Home Depot", "Ryan Cohen, GameStop's chief executive", "Anna Smith-Jones",
                  "John Smith", "Jane Doe"):
        assert R.congress_name_hits(clean) == [], clean


def test_every_roster_member_is_found_under_every_name_of_their_group():
    """Coverage, not examples: for every roster member, "LAST <name>" for every name in the
    nickname group of the name they go by (and their legal alias) is a Congress name."""
    doc = json.loads(R.CONGRESS_ROSTER_PATH.read_text(encoding="utf-8"))
    groups = {n: g for g in R.NICKNAME_GROUPS for n in g}
    missed = []
    for m in doc["members"]:
        first = m["first"].split()[0].strip(".").lower()
        last = m["last"].upper()
        for alt in sorted(groups.get(first, {first}) | set(R.MEMBER_LEGAL_ALIASES.get(f"{first} {m['last'].lower()}", ()))):
            if len(alt) >= 2 and not R.is_congress_name(f"{last} {alt.upper()}"):
                missed.append(f"{last} {alt}")
    assert not missed, missed[:20]


def test_the_nickname_table_is_pinned():
    pairs = ("chuck charles", "bill william", "bob robert", "jim james", "mike michael", "tom thomas", "dick richard",
             "ted edward", "joe joseph", "dan daniel", "dave david", "steve steven stephen", "tim timothy",
             "pat patrick", "pat patricia", "liz elizabeth", "kate katherine", "andy andrew", "tony anthony",
             "greg gregory", "ron ronald", "don donald", "ken kenneth", "jeff jeffrey", "chris christopher",
             "chris christine", "matt matthew", "nick nicholas", "sam samuel", "ben benjamin", "josh joshua",
             "tommy thomas", "bernie bernardo", "ro rohit", "dusty dustin", "jack john", "hank henry",
             # review round 10
             "lizzie elizabeth", "tony antonio", "rudy rudolph")
    for pair in pairs:
        names = pair.split()
        assert any(set(names) <= g for g in R.NICKNAME_GROUPS), pair
    for g in R.NICKNAME_GROUPS:
        assert all(n.isalpha() and n == n.lower() for n in g) and len(g) >= 2, g
    for key, legal in R.MEMBER_LEGAL_ALIASES.items():
        assert len(key.split()) == 2 and key == key.lower() and all(x.isalpha() and x == x.lower() for x in legal)
    for key, legal in {"mitt romney": ("willard",), "ted cruz": ("rafael",), "mike kelly": ("george",),
                       "trey hollingsworth": ("joseph",), "mac thornberry": ("william",)}.items():
        assert R.MEMBER_LEGAL_ALIASES[key] == legal, key


@pytest.mark.parametrize("run_date, fresh, error", [
    (date(2027, 2, 1), True, False), (date(2027, 2, 20), True, False),
    (date(2027, 7, 31), True, False),                                         # 180 days: no warning yet
    (date(2027, 8, 1), True, True),                                           # 181 days: ERROR, still named
    (date(2028, 3, 7), True, True),                                           # 400 days: the last named day
    (date(2028, 3, 8), False, True),                                          # 401 days: role only
    (date(2030, 1, 1), False, True),
])
def test_the_roster_age_is_read_from_the_run_date(fresh_congress, tmp_path, caplog, run_date, fresh, error):
    """r9 #27: a roster fetched 2027-02-01 holding the 120th Congress (``congress_start``
    2027-01-03, so only its age counts until 2029) against fixed run dates — never the clock, so
    these cases never expire."""
    doc = _roster(600)
    doc["fetched_on"] = "2027-02-01"
    doc["congress_start"] = "2027-01-03"
    _point(fresh_congress, tmp_path, doc, _REGISTRY)
    assert R.roster_fetched_on() == date(2027, 2, 1)
    with caplog.at_level(logging.ERROR, logger=R.__name__):
        assert R.roster_fresh_for(run_date) is fresh
    assert any(r.levelno == logging.ERROR and "congress roster" in r.getMessage() for r in caplog.records) is error
    assert (R.ROSTER_WARN_AGE_DAYS, R.ROSTER_MAX_AGE_DAYS) == (180, 400)


@pytest.mark.parametrize("fetched_on, congress_start, run_date, fresh, error", [
    # The shipped roster's dates: fetched 2026-10-09, holding the 119th Congress (sworn in 2025-01-03).
    ("2026-10-09", "2025-01-03", date(2026, 12, 31), True, False),
    ("2026-10-09", "2025-01-03", date(2027, 1, 2), True, False),     # the 119th Congress still sits
    ("2026-10-09", "2025-01-03", date(2027, 1, 3), False, True),     # the 120th is sworn in: NO grace
    ("2026-10-09", "2025-01-03", date(2027, 1, 16), False, True),    # round 10's grace named people here
    ("2026-10-09", "2025-01-03", date(2027, 4, 7), False, True),     # the age alone would still pass
    # rr11 #1: a refresh made inside round 10's grace from a dataset still listing the 119th. Its
    # fetched_on is after January 3, which round 10 took for "knows the new Congress" — until 2028.
    ("2027-01-04", "2025-01-03", date(2027, 1, 20), False, True),
    ("2027-01-04", "2025-01-03", date(2027, 6, 1), False, True),
    # A roster holding the 120th Congress: fresh from the day it is sworn in.
    ("2027-01-03", "2027-01-03", date(2027, 1, 3), True, False),
    ("2027-01-05", "2027-01-03", date(2027, 6, 1), True, False),
    ("2027-12-01", "2027-01-03", date(2028, 1, 20), True, False),    # an even year: no new Congress
    # 2029: the 121st Congress — a roster only 33 days old is still the wrong Congress.
    ("2028-12-01", "2027-01-03", date(2029, 1, 2), True, False),
    ("2028-12-01", "2027-01-03", date(2029, 1, 3), False, True),
    ("2029-01-04", "2029-01-03", date(2029, 3, 1), True, False),
], ids=["dec31", "jan2", "sworn_in", "old_grace", "april", "jan4_refresh_jan20", "jan4_refresh_june",
        "120th_on_the_day", "120th_after", "even_year", "2029_eve", "2029_sworn_in", "121st"])
def test_a_roster_holding_an_earlier_congress_is_not_fresh(fresh_congress, tmp_path, caplog, fetched_on,
                                                           congress_start, run_date, fresh, error):
    """rr11 #1 / #2 (main-session decision 2026-10-10): freshness is about CONTENT. The roster is
    fresh iff its ``congress_start`` is on or after the latest odd-year January 3 on or before the
    run date (no grace) AND it is at most `ROSTER_MAX_AGE_DAYS` old; when not, the Form 4 series are
    refused (the adapter tests), with an ERROR here."""
    doc = _roster(600)
    doc["fetched_on"] = fetched_on
    doc["congress_start"] = congress_start
    _point(fresh_congress, tmp_path, doc, _REGISTRY)
    assert R.roster_congress_start() == date.fromisoformat(congress_start)
    with caplog.at_level(logging.ERROR, logger=R.__name__):
        assert R.roster_fresh_for(run_date) is fresh
    assert any(r.levelno == logging.ERROR and "congress roster" in r.getMessage() for r in caplog.records) is error
    assert R.person_names_allowed() is True                  # the list itself stays usable
    assert not hasattr(R, "ROSTER_NEW_CONGRESS_GRACE_DAYS"), "round 10's grace is gone"


_MISSING = object()


@pytest.mark.parametrize("congress_start", [
    _MISSING, None, "", "garbage", 20250103, "2025-01-04", "2025-03-01", "2026-01-03", "2024-01-03",
    "2027-01-03",                                            # after its own fetched_on (2026-10-09)
], ids=["absent", "null", "empty", "garbage", "int", "jan4", "march", "even_year", "even_year_old", "future"])
def test_a_roster_without_a_valid_congress_start_is_not_fresh(fresh_congress, tmp_path, caplog, congress_start):
    """rr11: a roster that cannot show which Congress it holds is not fresh (fail closed) — on a run
    date where the same roster with ``congress_start`` 2025-01-03 is (the control below)."""
    doc = _roster(600)
    if congress_start is _MISSING:
        doc.pop("congress_start")
    else:
        doc["congress_start"] = congress_start
    _point(fresh_congress, tmp_path, doc, _REGISTRY)
    with caplog.at_level(logging.ERROR, logger=R.__name__):
        assert R.roster_congress_start() is None
        assert R.roster_fresh_for(date(2026, 11, 16)) is False
    assert any(r.levelno == logging.ERROR and "congress_start" in r.getMessage() for r in caplog.records)
    assert R.person_names_allowed() is True
    R._congress_state.cache_clear()
    _point(fresh_congress, tmp_path, _roster(600), _REGISTRY)            # control: the valid twin
    assert R.roster_fresh_for(date(2026, 11, 16)) is True


@pytest.mark.parametrize("day, start", [
    (date(2027, 1, 2), date(2025, 1, 3)), (date(2027, 1, 3), date(2027, 1, 3)),
    (date(2027, 12, 31), date(2027, 1, 3)), (date(2028, 1, 3), date(2027, 1, 3)),
    (date(2028, 6, 1), date(2027, 1, 3)), (date(2029, 1, 3), date(2029, 1, 3)),
    (date(2026, 10, 9), date(2025, 1, 3)),
])
def test_the_congress_start_is_the_latest_odd_year_january_third(day, start):
    assert R.congress_start_on_or_before(day) == start


#: rr10 #4: Form 4 LEGAL names of roster members the nickname / alias tables missed.
_R10_LEGAL_NAMES = ("FLETCHER ELIZABETH P", "YAKYM RUDOLPH", "KELLY GEORGE J JR", "CARDENAS ANTONIO",
                    "HOLLINGSWORTH JOSEPH A III", "THORNBERRY WILLIAM", "Elizabeth P. Fletcher",
                    "Rudolph Yakym", "Antonio Cardenas")


@pytest.mark.parametrize("name", _R10_LEGAL_NAMES)
def test_a_members_unrelated_legal_name_is_a_congress_name(name):
    assert R.is_congress_name(name), name


def test_a_members_unrelated_legal_name_is_found_in_text():
    assert R.congress_name_hits("Elizabeth P. Fletcher, a director of Acme, disclosed buying.") == [
        "elizabeth fletcher"]
    assert R.congress_name_hits("Antonio Cardenas and Rudolph Yakym") == ["antonio cardenas", "rudolph yakym"]
    # the aliases are the member's own: another Kelly / Hollingsworth is still not blocked by them
    assert not R.is_congress_name("HOLLINGSWORTH MARY") and not R.is_congress_name("YAKYM ANNA")


def test_a_roster_middle_name_is_indexed_as_a_given_name(fresh_congress, tmp_path):
    """rr10 #4: the refresh script now keeps the dataset's ``middle``; a member known by it files
    under it ("ZQXMID ALBERT"), and it is a spelled-out given name — never an initial."""
    member = {"first": "Joseph", "middle": "Albert", "last": "Zqxmid", "current": True}
    _point(fresh_congress, tmp_path, _roster(600, extra=[member]), _REGISTRY)
    assert R.is_congress_name("ZQXMID ALBERT") and R.is_congress_name("ZQXMID JOSEPH")
    assert not R.is_congress_name("ZQXMID ANNA")             # control: an unrelated given name
    assert R.congress_name_hits("Albert Zqxmid joined the board.") == ["albert zqxmid"]
    R._congress_state.cache_clear()
    _point(fresh_congress, tmp_path, _roster(600, extra=[dict(member, middle="")]), _REGISTRY)
    assert not R.is_congress_name("ZQXMID ALBERT")           # control: no middle, no match


@pytest.mark.parametrize("fetched_on", [None, "not-a-date", "", 20261009])
def test_a_roster_without_a_readable_date_names_no_one(fresh_congress, tmp_path, caplog, fetched_on):
    doc = _roster(600)
    if fetched_on is None:
        doc.pop("fetched_on")
    else:
        doc["fetched_on"] = fetched_on
    _point(fresh_congress, tmp_path, doc, _REGISTRY)
    assert R.person_names_allowed() is True                  # the list itself is usable …
    with caplog.at_level(logging.ERROR, logger=R.__name__):
        assert R.roster_fresh_for(date(2026, 11, 16)) is False   # … but its age is unknown
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_a_composed_post_never_names_a_member_filed_under_a_legal_name():
    """r9 #26: the template's own check (`_insider_parts`) drops a row whose rendered person is a
    member under the legal name ("Joshua S. Gottheimer"); a week of only that row is refused."""
    from app.services.marketing import news_templates as NT
    member = purchase("FOX", "Fox", 150_000.0, role="director", person="Joshua S. Gottheimer")
    with pytest.raises(NT.NewsTemplateRefused) as err:
        NT.compose(insider_week("insider_buys", (member,)), run_date=date(2026, 11, 16), store_state="live",
                   allow_x_url=False)
    assert err.value.code == "too_few_rows"


#: Registry 13F investors not (yet) verified on EDGAR: refused as `filer_entity_unknown` until
#: their filer is checked and added to `R.THIRTEEN_F_FILERS` (owner task).
_UNVERIFIED_13F_FILERS = frozenset({"0001489933", "0001352662"})


def test_every_registry_13f_filer_has_a_verified_entity_or_is_listed_unverified():
    """r9 #9 / #23 drift guard: a filer added to the registry fails here until its EDGAR filer is
    in `THIRTEEN_F_FILERS` (an entity name, or None for a person) or on the unverified list."""
    rows = json.loads(R.WHALE_REGISTRY_PATH.read_text(encoding="utf-8"))
    ciks = {R.cik10(r["cik"]) for r in rows if r.get("data_source") == "13f" and r.get("category") == "investors"}
    assert ciks - set(R.THIRTEEN_F_FILERS) == _UNVERIFIED_13F_FILERS
    assert set(R.THIRTEEN_F_FILERS) <= ciks
    assert {c for c, n in R.THIRTEEN_F_FILERS.items() if n is None} == {"0000921669", "0000898382"}
    for cik, name in R.THIRTEEN_F_FILERS.items():
        assert R.cik10(cik) == cik
        if name is not None:
            assert R.filer_name_problem(name) is None and not R.filer_looks_like_person(name), name
    assert R.THIRTEEN_F_FILERS["0001549575"] == "Dalal Street"          # not "Pabrai Investment Funds"


@pytest.mark.parametrize("name, person", [
    ("ICAHN CARL C", True), ("Carl C. Icahn", True), ("COOPERMAN LEON G", True), ("Leon G. Cooperman", True),
    ("Berkshire Hathaway", False), ("Soros Fund Management", False), ("Paulson & Co", False),
    ("Dalal Street", False), ("NVIDIA", False), ("Icahn Enterprises", False), (None, False), ("", False),
])
def test_filer_looks_like_person(name, person):
    assert R.filer_looks_like_person(name) is person


def _roster(n, *, extra=()):
    return {"_about": "test", "fetched_on": "2026-10-09", "congress_start": "2025-01-03",
            "members": [{"first": f"Zqfirst{i}", "last": f"Zqlast{i}", "current": True} for i in range(n)]
            + list(extra)}


def _point(fresh, tmp_path, roster=None, registry=None):
    rpath, wpath = tmp_path / "roster.json", tmp_path / "registry.json"
    if roster is not None:
        rpath.write_text(roster if isinstance(roster, str) else json.dumps(roster), encoding="utf-8")
    if registry is not None:
        wpath.write_text(registry if isinstance(registry, str) else json.dumps(registry), encoding="utf-8")
    fresh.setattr(R, "CONGRESS_ROSTER_PATH", rpath)
    fresh.setattr(R, "WHALE_REGISTRY_PATH", wpath)


_REGISTRY = [{"name": "Nancy Pelosi", "category": "politicians"}, {"name": "Warren Buffett", "category": "investors"}]


def test_a_usable_test_roster_blocks_its_members(fresh_congress, tmp_path):
    _point(fresh_congress, tmp_path, _roster(600, extra=[{"first": "Ryan", "last": "Cohen", "current": False},
                                                         {"first": "Jon", "last": "Van Hollenbeck", "current": True},
                                                         {"first": "Kristofer", "last": "Zqxlake", "current": True}]),
           _REGISTRY)
    # four shared leading letters tie two spellings no group lists ("Kristofer" / "Kristopher")
    assert R.is_congress_name("ZQXLAKE KRISTOPHER") and not R.is_congress_name("ZQXLAKE KRYSTAL")
    assert R.person_names_allowed() is True
    assert R.is_congress_name("COHEN RYAN") and R.is_congress_name("Nancy Pelosi")
    # a multi-word surname filed LAST-first is found only by rotating the words
    assert R.is_congress_name("VAN HOLLENBECK JON") and R.is_congress_name("Van Hollenbeck, Jon")
    # review round 9: the surname rule also finds the particle dropped ("HOLLENBECK JON") or the
    # legal first name ("VAN HOLLENBECK JONATHAN") — never another first name under that surname
    assert R.is_congress_name("HOLLENBECK JON") and R.is_congress_name("VAN HOLLENBECK JONATHAN")
    assert not R.is_congress_name("HOLLENBECK ANNA") and not R.is_congress_name("COHEN ANNA")
    assert not R.is_congress_name("Warren Buffett"), "investors are not members of Congress"
    assert R.render_person_name("COHEN RYAN") == "Ryan Cohen", "the renderer renders; the caller drops the row"


@pytest.mark.parametrize("roster", [
    None,                                     # missing (the file is now part of the repo)
    "{not json",                              # unreadable
    json.dumps({"members": "nope"}),          # wrong shape
    json.dumps(["a", "b"]),                   # wrong top level
    _roster(R.ROSTER_MIN_MEMBERS - 1),        # truncated
])
def test_an_unusable_roster_turns_every_person_slot_role_only(fresh_congress, tmp_path, caplog, roster):
    _point(fresh_congress, tmp_path, roster, _REGISTRY)
    with caplog.at_level(logging.ERROR, logger=R.__name__):
        assert R.person_names_allowed() is False
        assert R.render_person_name("COHEN RYAN") is None
        assert R.render_person_name("Sicilia Michael D") is None
    assert any(rec.levelno == logging.ERROR and "role-only" in rec.getMessage() for rec in caplog.records)
    assert R.is_congress_name("Nancy Pelosi"), "the registry politicians still block"


@pytest.mark.parametrize("registry", [None, "{bad", json.dumps([{"name": "W B", "category": "investors"}])])
def test_an_unusable_registry_also_fails_closed(fresh_congress, tmp_path, caplog, registry):
    _point(fresh_congress, tmp_path, _roster(600), registry)
    with caplog.at_level(logging.ERROR, logger=R.__name__):
        assert R.person_names_allowed() is False
        assert R.render_person_name("COHEN RYAN") is None
    assert any(rec.levelno == logging.ERROR for rec in caplog.records)


def test_the_roster_minimum_equals_the_refresh_scripts():
    from scripts import refresh_congress_roster as script
    assert script.ROSTER_MIN_MEMBERS == R.ROSTER_MIN_MEMBERS


# ── companies and symbols ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,out", [
    ("Apple Inc.", "Apple"), ("Microsoft Corporation", "Microsoft"), ("NVIDIA Corporation", "NVIDIA"),
    ("GameStop Corp.", "GameStop"), ("Amazon.com, Inc.", "Amazon"), ("Meta Platforms, Inc.", "Meta"),
    ("Costco Wholesale Corporation", "Costco"), ("The Coca-Cola Company", "Coca-Cola"),
    ("Deere & Company", "Deere & Company"), ("Johnson & Johnson", "Johnson & Johnson"), ("AT&T Inc.", "AT&T"),
    ("Merck & Co., Inc.", "Merck"), ("Paulson & Co", "Paulson & Co"), ("Brown & Co.", "Brown & Co."),
    ("McDonald's Corporation", "McDonald's"), ("McDonald’s Corporation", "McDonald's"),
    ("Yum! Brands, Inc.", "Yum Brands"), ("QUALCOMM Incorporated", "Qualcomm"), ("NIKE, Inc.", "Nike"),
    ("CoreWeave, Inc. Class A Common Stock", "CoreWeave"), ("Example Holdings Ltd. plc", "Example Holdings"),
    ("Example Inc. Ltd. plc", "Example Inc."), ("Banco Ejemplo S.A.", "Banco Ejemplo"), ("ASML Holding N.V.", "ASML Holding"),
    ("Energy Transfer LP", "Energy Transfer"), ("Some Firm, L.L.C.", "Some Firm"), ("Fox Corporation", "Fox"),
    ("  GameStop   Corp.  ", "GameStop"), ("Eli Lilly and Company", "Eli Lilly"),
    # refusals
    ("Hot Topic, Inc.", None), ("Signal Hill Acquisition Corp", None), ("Victoria's Secret & Co.", None),
    ("Will Group Inc.", None), ("Alphabet Inc. (Class A)", None), ("Kraft Heinz/The", None),
    ("A Very Long Company Name That Never Ends Inc.", None), ("Inc.", None), ("", None), ("123 456", None),
    ("Société Générale", None), ("Tab\tCorp", "Tab"), (None, None), (7, None),
])
def test_display_company_name(raw, out):
    assert R.display_company_name(raw) == out


def test_hostile_inputs_are_refused_fast():
    import time
    t0 = time.perf_counter()
    assert R.display_company_name("x" + "," * 50_000 + "y") is None
    assert R.display_company_name("Acme" + ", " * 90 + "Inc.") == "Acme"     # separators fold into the suffix
    assert R.display_company_name("Acme" + ", " * 100 + "Inc.") is None      # over the raw cap
    assert R.render_person_name("A" * 10_000) is None
    assert R.filer_name_problem("Q" * 100_000) == "filer_name_unusable"
    assert R.company_name_problem("Hot " * 50_000) == "company_name_unusable"
    assert not R.is_congress_name("nancy " * 20_000)
    assert R.congress_name_hits("word " * 20_000) == []
    assert R.corroborates("Ryan Cohen", "mr. " + "x " * 50_000) is False
    assert time.perf_counter() - t0 < 5.0


def test_display_overrides_are_folded_keys_with_valid_values():
    for key, value in R.DISPLAY_OVERRIDES.items():
        assert key == compliance.fold(key), key
        assert R.company_name_problem(value) is None, value


@pytest.mark.parametrize("raw,canon,problem", [
    ("BRK.B", "BRK-B", None), ("brk.b", "BRK-B", None), (" gme ", "GME", None), ("GOOGL", "GOOGL", None),
    ("BRK-A", "BRK-A", None), ("F", "F", None), ("RZLVW", None, "warrant_unit_right"),
    ("ABCDU", None, "warrant_unit_right"), ("ABCDR", None, "warrant_unit_right"), ("ABCDQ", None, "warrant_unit_right"),
    ("ABCDZ", None, "warrant_unit_right"), ("ABCW", "ABCW", None), ("BRK-D", None, "symbol_grammar"),
    ("ABCDEF", None, "symbol_grammar"), ("A1", None, "symbol_grammar"), ("PSH.L", None, "symbol_grammar"),
    ("", None, "symbol_grammar"), (None, None, "symbol_grammar"), (123, None, "symbol_grammar"),
    ("NASDAQ:X", None, "symbol_grammar"),
])
def test_canonical_symbol_and_symbol_problem(raw, canon, problem):
    assert R.canonical_symbol(raw) == canon
    assert R.symbol_problem(raw) == problem


def _profile(**over):
    p = {"symbol": "GME", "exchange": "NYSE", "isEtf": False, "isFund": False, "isActivelyTrading": True,
         "isAdr": False, "currency": "USD", "marketCap": 1.1e10, "companyName": "GameStop Corp.",
         "price": 91919.19, "ceo": "Mr. ZQXCEO Cohen", "image": "https://example.invalid/x.png",
         "description": "ZQXDESC", "defaultImage": False}
    p.update(over)
    return p


@pytest.mark.parametrize("purpose", R.PROFILE_PURPOSES)
def test_company_from_profile_accepts_a_clean_profile_and_carries_nothing_else(purpose):
    got = R.company_from_profile(_profile(), "GME", purpose=purpose)
    assert got == R.CompanyRef("GME", "GameStop")
    assert [f.name for f in fields(got)] == ["symbol", "name"]
    assert R.company_from_profile(_profile(symbol="BRK.B", companyName="Berkshire Hathaway Inc."), "BRK-B",
                                  purpose=purpose) == R.CompanyRef("BRK-B", "Berkshire Hathaway")


@pytest.mark.parametrize("profile,symbol,purpose,reason", [
    (_profile(), "RZLVW", "listing", "warrant_unit_right"),
    (_profile(), "GME!", "listing", "symbol_grammar"),
    (_profile(symbol="AMC"), "GME", "listing", "profile_missing"),
    (None, "GME", "listing", "profile_missing"),
    ([], "GME", "insider", "profile_missing"),
    (_profile(symbol=None), "GME", "listing", "profile_missing"),
    (_profile(exchange="OTC"), "GME", "listing", "not_major_exchange"),
    (_profile(exchange=None), "GME", "listing", "not_major_exchange"),
    (_profile(exchange="LSE"), "GME", "insider", "not_major_exchange"),
    (_profile(isEtf=True), "GME", "listing", "etf_or_fund"),
    (_profile(isFund=True), "GME", "insider", "etf_or_fund"),
    (_profile(isEtf="true"), "GME", "listing", "etf_or_fund"),
    (_profile(isActivelyTrading=False), "GME", "listing", "inactive"),
    (_profile(isActivelyTrading="false"), "GME", "earnings", "inactive"),
    (_profile(isAdr=True), "GME", "insider", "adr"),
    (_profile(isAdr=1), "GME", "earnings", "adr"),
    (_profile(currency="EUR"), "GME", "insider", "not_usd"),
    (_profile(currency=None), "GME", "earnings", "not_usd"),
    (_profile(marketCap=2.4e8), "GME", "insider", "below_cap_floor"),
    (_profile(marketCap=1.9e9), "GME", "earnings", "below_cap_floor"),
    (_profile(marketCap=None), "GME", "insider", "below_cap_floor"),
    (_profile(marketCap=float("nan")), "GME", "insider", "below_cap_floor"),
    (_profile(marketCap=float("inf")), "GME", "earnings", "below_cap_floor"),
    (_profile(marketCap=True), "GME", "insider", "below_cap_floor"),
    (_profile(marketCap="1e12"), "GME", "insider", "below_cap_floor"),
    (_profile(companyName=None), "GME", "listing", "company_name_unusable"),
    (_profile(companyName="Société Générale"), "GME", "listing", "company_name_unusable"),
    (_profile(companyName="Hot Topic, Inc."), "GME", "listing", "company_name_banned_word"),
])
def test_company_from_profile_reasons(profile, symbol, purpose, reason):
    assert R.company_from_profile(profile, symbol, purpose=purpose) == reason
    assert reason in R.REJECTION_REASONS


def test_listing_purpose_skips_the_insider_and_earnings_gates():
    p = _profile(isAdr=True, currency="EUR", marketCap=None, exchange="nasdaq", isActivelyTrading=None)
    assert isinstance(R.company_from_profile(p, "GME", purpose="listing"), R.CompanyRef)
    assert R.company_from_profile(_profile(marketCap=1e9), "GME", purpose="insider") == R.CompanyRef("GME", "GameStop")
    assert R.company_from_profile(_profile(marketCap=2e9), "GME", purpose="earnings") == R.CompanyRef("GME", "GameStop")
    with pytest.raises(ValueError):
        R.company_from_profile(_profile(), "GME", purpose="bets")


def test_exchange_set_equals_the_13f_builders():
    from app.services.trillion_club import builder
    assert R.MAJOR_US_EXCHANGES == frozenset(builder.US_EXCHANGES)


@pytest.mark.parametrize("name,problem", [
    ("", "filer_entity_unknown"), (None, "filer_entity_unknown"), ("   ", "filer_entity_unknown"),
    ("Unknown", "filer_entity_unknown"), ("N/A", "filer_entity_unknown"), (5, "filer_entity_unknown"),
    ("Soros Fund Management", None), ("Icahn Enterprises", None), ("Pabrai Investment Funds", None),
    ("Gates Foundation Trust", None), ("Fisher Investments", None), ("Paulson & Co", None), ("GMO", None),
    ("Berkshire Hathaway", None), ("D1 Capital Partners", None),
    ("X" * 61, "filer_name_unusable"), ("Hot Hands Capital", "filer_name_unusable"),
    ("Fund/Partners", "filer_name_unusable"), (" Padded Capital", "filer_name_unusable"),
    ("Société Capital", "filer_name_unusable"), ("Will Capital", "filer_name_unusable"),
])
def test_filer_name_problem(name, problem):
    assert R.filer_name_problem(name) == problem


def test_every_registry_13f_investor_filer_is_allowed():
    """Owner decision 5: entity names with a person's surname are allowed — never refused for it."""
    rows = json.loads((compliance.DATA_DIR / "whale_registry.json").read_text(encoding="utf-8"))
    firms = [r["firm_name"] for r in rows if r.get("category") == "investors" and r.get("data_source") == "13f"]
    assert len(firms) >= 20
    assert [f for f in firms if R.filer_name_problem(f) is not None] == []


def test_cik10():
    assert R.cik10("1067983") == "0001067983" == R.cik10(1067983) == R.cik10("0001067983")
    for bad in (None, True, 1067983.0, "", "0", "0000000000", "12345678901", "12a", " ", "１２３"):
        assert R.cik10(bad) is None, bad


def test_anchors_and_digits_are_ascii_and_whole_string():
    """`$` matches before a trailing newline and `\\d` matches other scripts' digits: neither may
    reach a ledger key, a symbol or a reason code."""
    for bad in ("2026-Q3\n", "\u0662\u0660\u0662\u0666-Q3", "2026-Q3 "):
        with pytest.raises(ValueError):
            R.period_end_of(bad)
    for bad in ("2026-11\n", "\uff12\uff10\uff12\uff16-11"):
        with pytest.raises(ValueError):
            R.month_end_of(bad)
    with pytest.raises(ValueError):
        money_map(fiscal_year="\uff12\uff10\uff12\uff15")
    with pytest.raises(ValueError):
        thirteen_f(period="2026-Q3\n")
    for bad in ("gme\u00df", "gm\u0131", "GME\nX", "\uff27\uff2d\uff25"):
        assert R.canonical_symbol(bad) is None, bad
    assert R.canonical_symbol(" GME\n") == "GME"            # surrounding whitespace is noise
    with pytest.raises(ValueError):
        R.fact_sheet(money_map(), rejections={"ok\n": 1}, selection={})
    assert R.company_name_problem("Apple\n") == "company_name_unusable"


def test_insider_window_and_period_helpers():
    assert R.insider_window(date(2026, 11, 16)) == (date(2026, 11, 9), date(2026, 11, 15))
    with pytest.raises(ValueError):
        R.insider_window(datetime(2026, 11, 16))
    assert R.period_label(2026, 3) == "2026-Q3"
    assert R.period_end_of("2026-Q1") == date(2026, 3, 31) and R.period_end_of("2026-Q4") == date(2026, 12, 31)
    assert R.month_end_of("2028-02") == date(2028, 2, 29)
    for bad in ("2026-Q5", "2026Q1", None, "26-Q1"):
        with pytest.raises(ValueError):
            R.period_end_of(bad)
    for bad in ("2026-13", "2026-1", None):
        with pytest.raises(ValueError):
            R.month_end_of(bad)


# ── ledger keys and source labels ─────────────────────────────────────────────

def test_ledger_keys_are_pinned():
    assert R.ledger_key(insider_week()) == "news:ceo_buys:2026-11-09"
    assert R.ledger_key(SAMPLES["insider_buys"]()) == "news:insider_buys:2026-11-09"
    assert R.ledger_key(thirteen_f()) == "news:thirteen_f:0001067983:2026-Q3"
    assert R.ledger_key(congress()) == "news:congress_count:2026-11"
    assert R.ledger_key(stake()) == f"news:company_stakes:{UUID}"
    assert R.ledger_key(earnings()) == "news:earnings:COST:2027-01-12"
    assert R.ledger_key(money_map()) == "news:money_map:AAPL:2025"
    assert R.ledger_key(theme()) == "news:theme_explainer:quantum-computing:2026-10-01"
    for make in SAMPLES.values():
        assert R.ledger_key(make()).startswith(R.LEDGER_PREFIX)
    with pytest.raises(ValueError):
        R.ledger_key(co())


def test_source_labels_are_pinned_and_never_name_the_vendor():
    assert R.source_label(insider_week()) == "SEC Form 4 filings"
    assert R.source_label(SAMPLES["insider_buys"]()) == "SEC Form 4 filings"
    assert R.source_label(thirteen_f()) == "SEC Form 13F"
    assert R.source_label(thirteen_f(amended_on=date(2026, 11, 20))) == "SEC Form 13F-HR/A"
    assert R.source_label(congress()) == "congressional periodic transaction reports"
    assert R.source_label(stake()) == "NVIDIA Form 10-Q"
    assert R.source_label(earnings()) == "company results and analyst consensus"
    assert R.source_label(money_map()) == "company financial statements"
    assert R.source_label(theme()) == "company segment reporting; grouping by Caydex"
    import re
    vendor = re.compile(r"fmp|financial\s*modeling\s*prep|financialmodelingprep", re.I)
    assert not [v for v in R.SOURCE_LABELS.values() if vendor.search(v)]
    with pytest.raises(ValueError):
        R.source_label(co())


# ── round trip, strictness, fact sheet ────────────────────────────────────────

def _shuffle(obj, rng):
    """JSONB-style read-back: keys reordered at every level, numbers re-serialised (an integral
    float comes back as an int and vice versa)."""
    if isinstance(obj, dict):
        items = list(obj.items())
        rng.shuffle(items)
        return {k: _shuffle(v, rng) for k, v in items}
    if isinstance(obj, list):
        return [_shuffle(x, rng) for x in obj]
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, int) and not isinstance(obj, bool):
        return float(obj)
    return obj


@pytest.mark.parametrize("series", sorted(SAMPLES))
def test_record_round_trip_survives_a_jsonb_shuffle(series):
    rec = SAMPLES[series]()
    d = R.record_to_dict(rec)
    assert d["schema"] == 1 and d["series"] == series
    text = json.dumps(d, allow_nan=False)
    for seed in range(5):
        back = R.record_from_dict(_shuffle(json.loads(text), random.Random(seed)))
        assert back == rec
        assert json.dumps(R.record_to_dict(back), sort_keys=True) == json.dumps(d, sort_keys=True)
    assert R.ledger_key(R.record_from_dict(json.loads(text))) == R.ledger_key(rec)


@pytest.mark.parametrize("series", sorted(SAMPLES))
def test_fact_sheet_shape_and_round_trip(series):
    rec = SAMPLES[series]()
    selection = {"plan": "mon", "chain": ["ceo_buys", "lesson"], "trail": [{"series": "ceo_buys", "outcome": "chosen"}]}
    sheet = R.fact_sheet(rec, rejections={"already_posted": 2, "below_cap_floor": 1}, selection=selection)
    assert set(sheet) == {"schema", "series", "content_class", "source_ref", "record", "rejections", "selection"}
    assert sheet["schema"] == 1 and sheet["series"] == series
    assert sheet["content_class"] == R.SERIES_CLASS[series]
    assert sheet["source_ref"] == R.ledger_key(rec)
    assert sheet["rejections"] == {"already_posted": 2, "below_cap_floor": 1}
    stored = _shuffle(json.loads(json.dumps(sheet)), random.Random(7))
    stored["schema"] = 1
    stored["rejections"] = sheet["rejections"]
    assert R.record_from_fact_sheet(stored) == rec


def test_fact_sheet_refusals():
    rec = money_map()
    good = R.fact_sheet(rec, rejections={}, selection={})
    for key, value in (("source_ref", "news:money_map:AAPL:2024"), ("content_class", "C"), ("series", "earnings"),
                       ("schema", 2), ("schema", True), ("rejections", {"x": -1}), ("selection", [])):
        bad = copy.deepcopy(good)
        bad[key] = value
        with pytest.raises(ValueError):
            R.record_from_fact_sheet(bad)
    extra = dict(copy.deepcopy(good), surprise=1)
    with pytest.raises(ValueError):
        R.record_from_fact_sheet(extra)
    missing = {k: v for k, v in good.items() if k != "selection"}
    with pytest.raises(ValueError):
        R.record_from_fact_sheet(missing)
    for rej in ({"Bad Key": 1}, {"ok": True}, {"ok": 1.5}, {"ok": -1}, {1: 1}, ["x"]):
        with pytest.raises(ValueError):
            R.fact_sheet(rec, rejections=rej, selection={})
    for sel in ({"x": float("nan")}, {"x": date(2026, 1, 1)}, ["x"]):
        with pytest.raises(ValueError):
            R.fact_sheet(rec, rejections={}, selection=sel)
    with pytest.raises(ValueError):
        R.fact_sheet(co(), rejections={}, selection={})


def _mutations(d):
    yield "unknown top key", dict(d, extra=1)
    yield "missing top key", {k: v for k, v in d.items() if k != "company"}
    yield "schema 2", dict(d, schema=2)
    yield "schema bool", dict(d, schema=True)
    yield "schema missing", {k: v for k, v in d.items() if k != "schema"}
    yield "unknown series", dict(d, series="bets")
    yield "nested unknown key", dict(d, company=dict(d["company"], logo=None))
    yield "nested missing key", dict(d, company={"symbol": "AAPL"})
    yield "slashed date", dict(d, period_end="2025/09/27")
    yield "datetime string", dict(d, period_end="2025-09-27T00:00:00")
    yield "bool number", dict(d, revenue_usd=True)
    yield "string number", dict(d, revenue_usd="389000000000")
    yield "nan", dict(d, net_income_usd=float("nan"))
    yield "inf", dict(d, net_income_usd=float("inf"))
    yield "segments not a list", dict(d, segments={"name": "x"})
    yield "segment extra key", dict(d, segments=[dict(s, share=0.5) for s in d["segments"]])
    yield "not an object", ["money_map"]


def test_record_from_dict_is_strict():
    d = R.record_to_dict(money_map())
    for what, bad in _mutations(d):
        with pytest.raises(ValueError):
            R.record_from_dict(bad)
        assert what  # every mutation is named


def test_counts_and_int_fields_decode_strictly():
    d = R.record_to_dict(thirteen_f())
    assert d["counts"] == [["newly_reported", 1], ["decreased", 3]]
    with pytest.raises(ValueError):
        R.record_from_dict(dict(d, counts=[["newly_reported", 1.5]]))
    with pytest.raises(ValueError):
        R.record_from_dict(dict(d, counts=[["newly_reported"]]))
    with pytest.raises(ValueError):
        R.record_from_dict(dict(d, position_count=True))
    assert R.record_from_dict(dict(d, position_count=45.0)).position_count == 45


def test_record_to_dict_refuses_non_records_and_floats_are_normalised():
    with pytest.raises(ValueError):
        R.record_to_dict(co())
    with pytest.raises(ValueError):
        R.record_to_dict({"series": "money_map"})
    p = purchase(amount=100_000, shares=5)
    assert isinstance(p.amount_usd, float) and isinstance(p.shares, float)
    assert json.dumps(R.record_to_dict(insider_week(rows=(p,))))  # strict JSON


# ── records validate themselves ───────────────────────────────────────────────

NAN, INF = float("nan"), float("inf")


@pytest.mark.parametrize("make", [
    lambda: co("GME", "GameStop\n"), lambda: co("GME", "Game​Stop"), lambda: co("RZLVW", "Razor"),
    lambda: co("gme", "GameStop"), lambda: co("GME", "Hot Topic"), lambda: co("GME", "G"),
    lambda: purchase(amount=99_999.99), lambda: purchase(amount=NAN), lambda: purchase(amount=INF),
    lambda: purchase(amount=-INF), lambda: purchase(amount=True), lambda: purchase(amount="100000"),
    lambda: purchase(amount=6e9), lambda: purchase(shares=0), lambda: purchase(purchases=0),
    lambda: purchase(purchases=True), lambda: purchase(role="chair"), lambda: purchase(person="ryan cohen"),
    lambda: purchase(person="Ryan"), lambda: purchase(person="Ryan  Cohen"), lambda: purchase(person=" Ryan Cohen"),
    lambda: purchase(person="Ryan O'Cohen"), lambda: purchase(name="Ryan Cohen", person="Ryan Cohen"),
    lambda: purchase(earliest_trade_date=date(2026, 11, 12), latest_trade_date=date(2026, 11, 11)),
    lambda: purchase(filing_dates=(date(2026, 11, 12), date(2026, 11, 11))),
    lambda: purchase(filing_dates=(date(2026, 11, 12), date(2026, 11, 12))),
    lambda: purchase(filing_dates=[date(2026, 11, 12)]), lambda: purchase(filing_dates=()),
    lambda: purchase(filing_dates=(datetime(2026, 11, 12),)),
    lambda: purchase(latest_trade_date=date(2026, 11, 14)),
    lambda: purchase(earliest_trade_date=date(2026, 10, 1)),
    lambda: purchase(earliest_trade_date=datetime(2026, 11, 10)), lambda: purchase(holding="joint"),
    lambda: purchase(amended=1),
    lambda: insider_week(rows=()), lambda: insider_week(rows=tuple(purchase(f"S{c}", f"Co {c}") for c in "ABCDEF")),
    lambda: insider_week(rows=(purchase(), purchase())),
    lambda: insider_week(rows=(purchase("FOX", "Fox", 1e6), purchase())),
    lambda: insider_week(rows=(purchase(role="cfo"),)),
    lambda: insider_week("insider_buys", rows=(purchase(),)),
    lambda: insider_week(rows=(purchase(filing_dates=(date(2026, 11, 16),), latest_trade_date=date(2026, 11, 16)),)),
    lambda: insider_week(rows=[purchase()]),
    lambda: R.InsiderBuysWeek("ceo_buys", date(2026, 11, 15), date(2026, 11, 9), (purchase(),)),
    lambda: R.InsiderBuysWeek("ceo_buys", date(2026, 10, 1), date(2026, 11, 15), (purchase(),)),
    lambda: move(kind="newly_reported", prev_shares=10.0), lambda: move(kind="increased", listed_on=None, shares=5.0,
                                                                         prev_shares=10.0),
    lambda: move(kind="decreased", listed_on=None, shares=10.0, prev_shares=10.0),
    lambda: move(kind="increased", listed_on=None, shares=None, prev_shares=10.0),
    lambda: move(kind="no_longer_reported", listed_on=None, shares=5.0), lambda: move(kind="decreased"),
    lambda: move(value_usd=NAN), lambda: move(shares=-1.0), lambda: move(kind="corporate_action", listed_on=None),
    lambda: thirteen_f(period_end=date(2026, 9, 29)), lambda: thirteen_f(period="2026-Q5"),
    lambda: thirteen_f(filed_on=date(2026, 9, 30)), lambda: thirteen_f(amended_on=date(2026, 11, 1)),
    lambda: thirteen_f(filer_cik="1067983"), lambda: thirteen_f(filer_cik="0000000000"),
    lambda: thirteen_f(filer_name=""), lambda: thirteen_f(filer_name="Hot Hands Capital"),
    lambda: thirteen_f(filer_symbol="PSH-L"), lambda: thirteen_f(total_value_usd=1e6),
    lambda: thirteen_f(total_value_usd=INF), lambda: thirteen_f(position_count=0), lambda: thirteen_f(moves=()),
    lambda: thirteen_f(moves=(move(), move())), lambda: thirteen_f(counts=(("decreased", 3), ("newly_reported", 1))),
    lambda: thirteen_f(counts=(("newly_reported", 1),)), lambda: thirteen_f(counts=(("newly_reported", 0),
                                                                                    ("decreased", 3))),
    lambda: thirteen_f(counts=(("newly_reported", 1), ("decreased", -1))),
    lambda: thirteen_f(counts=(("bought", 1), ("decreased", 3))), lambda: thirteen_f(counts=[("newly_reported", 1)]),
    lambda: thirteen_f(counts=(("newly_reported", True), ("decreased", 3))),
    lambda: congress(members=1), lambda: congress(members=True), lambda: congress(month="2026-13"),
    lambda: congress(fetched_on=date(2026, 11, 30)), lambda: congress(series="congress"),
    lambda: stake(stake_id="not-a-uuid"), lambda: stake(stake_id=UUID.upper()), lambda: stake(value_usd=None,
                                                                                             value_basis=None),
    lambda: stake(value_basis=None), lambda: stake(value_basis="worth"), lambda: stake(kind="rumour"),
    lambda: stake(investee_name="Various (undisclosed)"), lambda: stake(investee_name="Investee not named"),
    lambda: stake(ownership_pct=0.0), lambda: stake(ownership_pct=101.0), lambda: stake(as_of=date(2026, 10, 2)),
    lambda: stake(value_usd=NAN), lambda: stake(is_new="yes"), lambda: stake(background=""),
    lambda: earnings(eps_estimate=0.05), lambda: earnings(eps_actual=NAN), lambda: earnings(revenue_actual=None),
    lambda: earnings(revenue_actual=200e9), lambda: earnings(revenue_actual=10e9), lambda: earnings(revenue_estimate=0.0),
    lambda: earnings(period_end=date(2027, 2, 1)),
    lambda: money_map(segments=(R.Segment("iPhone", 389e9),)), lambda: money_map(revenue_usd=400e9),
    lambda: money_map(segments=(R.Segment("iPhone", 200e9), R.Segment("IPHONE", 100e9), R.Segment("Mac", 50e9))),
    lambda: money_map(gross_profit_usd=390e9, revenue_usd=389e9), lambda: money_map(operating_profit_usd=190e9),
    lambda: money_map(net_income_usd=400e9), lambda: money_map(eliminations_usd=1e9, other_usd=38e9),
    lambda: money_map(other_usd=-1e9, eliminations_usd=40e9), lambda: money_map(fiscal_year="FY2025"),
    lambda: money_map(net_income_usd=INF), lambda: R.Segment("", 1.0), lambda: R.Segment("Hot picks", 1.0),
    lambda: R.Segment("Services", 0.0),
    lambda: theme(5), lambda: theme(members=theme().members[:5] + (theme().members[0],)),
    lambda: theme(slug="Quantum Computing"), lambda: theme(title="Hot quantum"), lambda: theme(title="Breaking AI"),
    lambda: R.ThemeMember(co(), None, 0.5, None), lambda: R.ThemeMember(co(), "Systems", 1.5, None),
    lambda: R.ThemeMember(co(), "Systems", 0.5, "FY25"),
])
def test_invalid_records_raise(make):
    with pytest.raises(ValueError):
        make()


@pytest.mark.parametrize("series", sorted(SAMPLES))
def test_samples_are_valid_and_hashable(series):
    rec = SAMPLES[series]()
    assert rec.series == series
    hash(rec)
    with pytest.raises(Exception):
        rec.series = "x"  # frozen


def test_records_hold_tuples_never_lists_and_have_slots():
    for t in R.ALL_RECORD_TYPES:
        assert hasattr(t, "__slots__"), t.__name__
        assert t.__dataclass_params__.frozen, t.__name__


def test_boundaries_that_must_pass():
    purchase(amount=100_000.0)
    purchase(amount=R.INSIDER_MAX_AMOUNT_USD)
    purchase(earliest_trade_date=date(2026, 10, 12))                       # exactly the 30-day lag
    purchase(latest_trade_date=date(2026, 11, 13))                         # filing + 1 day
    insider_week(rows=tuple(purchase(f"S{c}", f"Co {c}", 1e6 - i) for i, c in enumerate("ABCDE")))
    earnings(revenue_actual=97.5e9, revenue_estimate=65e9)                  # ratio 1.5
    earnings(revenue_actual=32.5e9, revenue_estimate=65e9)                  # ratio 0.5
    earnings(eps_estimate=-0.10, eps_actual=-0.3)
    congress(members=2, fetched_on=date(2026, 12, 1))
    stake(value_usd=None, value_basis=None, ownership_pct=12.5)
    money_map(other_usd=None, eliminations_usd=None, segments=(R.Segment("A", 200e9), R.Segment("B", 189e9)))
    money_map(revenue_usd=389e9 * 1.004)                                    # inside 0.5 %
    money_map(gross_profit_usd=None, operating_profit_usd=None, net_income_usd=-5e9)
    theme(8)
    thirteen_f(amended_on=date(2026, 11, 14), counts=(("newly_reported", 1), ("decreased", 1)))


# ── reason codes, classes, series ─────────────────────────────────────────────

def test_reason_sets_are_pinned():
    assert R.UNAVAILABLE_REASONS == {
        "insider_feed_unavailable", "insider_feed_empty", "earnings_calendar_unavailable",
        "earnings_calendar_truncated", "profiles_unavailable", "club_unavailable", "stakes_truncated",
        "thirteen_f_unavailable", "congress_feed_unavailable", "congress_window_uncovered",
        "congress_feed_unordered", "revenue_unavailable", "themes_unavailable", "budget_exhausted",
        "internal_error"}
    assert R.SKIP_REASONS == {
        "ceo_none_qualified", "insider_none_qualified", "earnings_none_qualified", "stakes_feature_off",
        "stakes_none_new", "stakes_none_unposted", "thirteen_f_off_season", "thirteen_f_none_qualified",
        "congress_not_due", "congress_none_qualified", "money_map_none_qualified", "theme_none_qualified",
        # Drop 2b (2026-10-10): no stake passed the gates (vs every passing one already posted)
        "stakes_none_qualified"}
    must = {"already_posted", "profile_missing", "not_major_exchange", "etf_or_fund", "adr", "not_usd", "inactive",
            "below_cap_floor", "company_name_unusable", "company_name_banned_word", "symbol_grammar",
            "warrant_unit_right", "price_reference_missing", "price_implausible", "over_cap_share",
            "below_dollar_floor", "ambiguous_ceo", "eps_estimate_too_small", "eps_digit_shift", "eps_gap_implausible",
            "revenue_dropped", "stake_invalid", "stake_stale", "stake_too_old", "stake_no_figure", "stake_aggregate",
            "filer_entity_unknown", "filer_named_after_person", "filer_not_filed", "filer_unavailable",
            "book_too_large", "degraded_build", "non_comparable", "move_value_exceeds_total", "move_too_small",
            "move_unknown_listing", "move_not_routable", "exchange_type", "option_asset", "member_unidentifiable",
            "money_map_degraded", "segments_thin", "sector_excluded", "non_usd_reporter", "revenue_mismatch",
            "net_income_mismatch", "money_map_stale", "money_map_inconsistent", "theme_stale", "theme_title_unusable",
            "theme_too_large", "theme_members_thin", "theme_facts_thin"}
    assert must <= R.REJECTION_REASONS
    assert R.REJECTION_REASONS - must == {"record_invalid", "congress_name", "filer_name_unusable",
                                          "money_map_excluded",
                                          # review round 1 (2026-10-09)
                                          "reporter_is_entity", "issuer_mismatch", "issuer_unverified",
                                          "share_class_overlap",
                                          # review round 2 (2026-10-09)
                                          "amendment_check_failed",
                                          # review round 7 (2026-10-09): THE amendment rule, role
                                          # words, the 13F materiality floor, a mostly-"Other" map
                                          "amended_filing", "role_uncertain", "move_immaterial",
                                          "money_map_mostly_other",
                                          # Drop 2b (2026-10-10): a non-common line by its profile
                                          # name; a club investor with no US listing; a Congress
                                          # count the identity fields cannot settle; an in-month
                                          # purchase of the company under no usable symbol
                                          "non_common_listing", "investor_unlisted", "member_ambiguous",
                                          "unmapped_purchase", "asset_uncertain",
                                          # review round 9 (2026-10-10): a partial Form 4 person; a
                                          # 13F move beside option / note rows of its issuer; a 13F
                                          # filed by a natural person; a Congress count beside a
                                          # ticker-less purchase naming the company another way
                                          "partial_person", "move_has_options", "filer_is_person",
                                          "count_uncertain",
                                          # review round 10 (2026-10-10): one Form 4 person on two
                                          # issuers is one row of the week
                                          "same_person"}
    # Round 7 replaced rounds 1-6's amendment matchers with one person-level rule: their codes
    # have no producer left, so they are gone (a stored fact sheet only checks a code's shape).
    assert not {"amendment_unmatched", "amendment_partial", "amended_after_window",
                "amendment_recoded"} & R.REJECTION_REASONS


def test_reason_sets_are_disjoint_well_formed_and_renamed():
    sets = (R.UNAVAILABLE_REASONS, R.SKIP_REASONS, R.REJECTION_REASONS)
    for a in range(3):
        for b in range(a + 1, 3):
            assert not (sets[a] & sets[b]), (sets[a] & sets[b])
    every = set().union(*sets)
    assert all(R._REASON_RE.match(c) for c in every)
    assert not [c for c in every if c.startswith("bets_")], "bets_* were renamed stakes_*"
    assert "already_posted" not in R.SKIP_REASONS


def test_every_gate_returns_a_known_reason():
    reasons = {R.symbol_problem(x) for x in ("RZLVW", "??", None, "GME")}
    reasons |= {R.filer_name_problem(x) for x in ("", "X" * 70, "Fine Capital")}
    reasons |= {R.company_name_problem(x) for x in ("", "Hot Topic", "GameStop")}
    reasons.discard(None)
    assert reasons <= R.REJECTION_REASONS, reasons - R.REJECTION_REASONS


def test_series_registries_agree():
    assert R.SERIES_CLASS == {
        "ceo_buys": "C", "insider_buys": "C", "thirteen_f": "C", "congress_count": "C",
        "company_stakes": "F", "earnings": "F", "money_map": "F", "theme_explainer": "F"}
    assert tuple(R.SERIES_CLASS) == R.NEWS_SERIES == tuple(R.RECORD_TYPE_BY_SERIES)
    assert set(R.SOURCE_LABELS) == set(R.NEWS_SERIES) - {"company_stakes"}
    assert set(SAMPLES) == set(R.NEWS_SERIES)
    for series, cls in R.RECORD_TYPE_BY_SERIES.items():
        assert cls in R.RECORD_TYPES
    assert set(R.RECORD_TYPES) == set(R.RECORD_TYPE_BY_SERIES.values())


def test_series_class_equals_selection_series():
    from app.services.marketing import selection
    series = getattr(selection, "SERIES", None)
    if series is None:
        pytest.skip("selection.SERIES is not built yet (contract D3, another implementer); pinned literally above")
    assert {s.id: s.content_class for s in series} == R.SERIES_CLASS
    assert tuple(s.id for s in series) == R.NEWS_SERIES


def test_stake_vocabularies_come_from_the_club_schema():
    from app.schemas import trillion_club as tc
    assert R.STAKE_KINDS is tc.STAKE_KINDS and R.VALUE_BASES is tc.VALUE_BASES


# ── the Money Map pool (owner decision 2) ─────────────────────────────────────

def test_money_map_seed_hygiene():
    seed = R.MONEY_MAP_SEED
    assert len(seed) >= 30 and len(set(seed)) == len(seed)
    assert all(R.canonical_symbol(s) == s for s in seed)
    assert not set(seed) & R.MONEY_MAP_EXCLUDED_SYMBOLS
    # no bank, insurer, REIT or real-estate company ever leads the rotation
    for banned in ("JPM", "BAC", "UNH", "BRK-B", "V", "MA", "AMT", "PLD", "O"):
        assert banned not in seed


def test_merge_money_map_pool_order_dedupe_and_exclusions():
    pool = R.merge_money_map_pool(["NVDA", "AVGO", "brk.b", "JPM", "RZLVW", None, 5], ["IONQ", "avgo", "UNH"])
    assert pool[:len(R.MONEY_MAP_SEED)] == R.MONEY_MAP_SEED
    assert pool[len(R.MONEY_MAP_SEED):] == ("AVGO", "IONQ")
    assert R.merge_money_map_pool() == R.MONEY_MAP_SEED
    assert R.merge_money_map_pool(None) == R.MONEY_MAP_SEED


def test_module_reads_no_vendor_fields():
    """The gates read a profile's cap inside and never return it; nothing in the module names a
    price, chart or logo URL field of a record."""
    banned_tokens = {"price", "prices", "close", "open", "high", "low", "chart", "history", "volume", "beta", "cap",
                     "dcf", "avatar", "photo", "image", "url", "link", "first", "last", "firstname", "lastname",
                     "office", "senator", "representative", "district", "owner", "party", "state", "chamber",
                     "change", "changes", "percent", "pct", "ceo", "description"}
    exempt = {"ownership_pct", "top_segment_share"}
    for t in R.ALL_RECORD_TYPES:
        for f in fields(t):
            if f.name in exempt:
                continue
            assert not set(f.name.split("_")) & banned_tokens, (t.__name__, f.name)


# ── review round 1 (2026-10-09) ───────────────────────────────────────────────

@pytest.mark.parametrize("raw, entity", [
    ("STARBOARD VALUE LP", True), ("TRIAN FUND MANAGEMENT, L.P.", True), ("BAKER BROS. ADVISORS LP", True),
    ("ELLIOTT INVESTMENT MANAGEMENT L P", True), ("STARBOARD VALUE L P", True), ("BLUE HARBOR L L C", True),
    ("Some Holdings, L.L.C.", True), ("ValueAct Capital Master Fund", True),
    ("CARLYLE GROUP INC", True), ("SMITH FAMILY TRUST", True), ("ESTATE OF JOHN DOE", True), ("Acme S.A.", True),
    ("Icahn Enterprises L.P.", True), ("Pershing Square GP", True), ("Doe Ventures", True), ("X Corporation", True),
    ("", True), ("   ", True), (None, True), (7, True), ("A" * 201, True),
    ("COHEN RYAN", False), ("Sicilia Michael D", False), ("SMITH JOHN JR", False), ("Smith-Jones Anna", False),
    ("ELLISON LAWRENCE JOSEPH", False), ("MÜLLER HANS", False), ("O'BRIEN SEAN", False), ("Lee Anna", False),
])
def test_is_entity_reporter(raw, entity):
    """A fund reporting as a director (review data:F2) is never published as a person — named or
    role-only. The renderer's style refusals (JR, a full middle name, accents) stay role-only."""
    assert R.is_entity_reporter(raw) is entity


@pytest.mark.parametrize("raw, out", [
    ("C3.ai, Inc.", "C3 AI"), ("BigBear.ai Holdings, Inc.", "BigBear AI"), ("1-800-FLOWERS.COM, Inc.", "1-800-Flowers"),
    ("Amazon.com, Inc.", "Amazon"),
    # any other domain-shaped name is refused where names are made (no bare link in a post)
    ("monday.com Ltd.", None), ("Booking.com", None), ("Cars.com Inc.", None), ("Example.io Corp", None),
    # abbreviations are not links
    ("J.M. Smucker Company", "J.M. Smucker Company"), ("U.S. Bancorp", "U.S. Bancorp"), ("H.B. Fuller Company", "H.B. Fuller Company"),
])
def test_domain_shaped_company_names_are_overridden_or_refused(raw, out):
    """compliance:F3 — "C3.ai" passed every template check; X then refused the post at publish
    and Threads/Facebook/LinkedIn autolinked a third-party domain in our body."""
    assert R.display_company_name(raw) == out
    if out is None:
        assert R.company_from_profile({"symbol": "ABC", "companyName": raw, "exchange": "NYSE"}, "ABC",
                                      purpose="listing") == "company_name_unusable"


def test_domain_shaped_names_fail_every_name_gate():
    for name in ("C3.ai", "Booking.com", "1-800-FLOWERS.COM"):
        assert R.company_name_problem(name) == "company_name_unusable"
        with pytest.raises(ValueError):
            R.CompanyRef(symbol="ABC", name=name)
    assert R.filer_name_problem("Example.com Capital") == "filer_name_unusable"
    assert R.filer_name_problem("Example Capital.io") == "filer_name_unusable"
    assert R.filer_name_problem("J.P. Example & Co.") is None


# ── review round 2 (2026-10-09): the 13F listing date sits inside the diffed quarter ──


def test_previous_period_end_of_every_quarter():
    assert [R.previous_period_end_of(f"2026-Q{q}") for q in (1, 2, 3, 4)] == [
        date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30), date(2026, 9, 30)]
    for bad in ("2026-Q5", "2026Q1", None):
        with pytest.raises(ValueError):
            R.previous_period_end_of(bad)


@pytest.mark.parametrize("listed_on, ok", [
    (date(2026, 6, 30), False),     # the previous quarter's last day: on last quarter's book
    (date(2026, 7, 1), True),       # the first day of the quarter
    (date(2026, 9, 30), True),      # the quarter's last day
    (date(2026, 10, 1), False),     # after the quarter: cannot be a holding AT its end
    (date(2025, 3, 28), False),     # 18 months before the previous quarter (the old fixture)
    (date(2015, 1, 5), False),
])
def test_a_newly_listed_move_must_be_listed_inside_the_quarter(listed_on, ok):
    """lens 1: "CoreWeave first appears in this filing; it was listed in March 2025" is true only
    for a listing after the previous quarter's end (the diff is adjacent-quarter only) and no
    later than this quarter's end. The record refuses anything else — a hand-built or older fact
    sheet cannot carry the false sentence past it."""
    build = lambda: thirteen_f(moves=(move(listed_on=listed_on),), counts=(("newly_reported", 1),))  # noqa: E731
    if ok:
        assert build().moves[0].listed_on == listed_on
    else:
        with pytest.raises(ValueError, match="listed_on"):
            build()
    # A newly reported move that is NOT newly listed carries no date at all, and is unaffected.
    assert thirteen_f(moves=(move(listed_on=None),), counts=(("newly_reported", 1),)).moves[0].listed_on is None


def test_a_stored_fact_sheet_with_an_out_of_quarter_listing_is_refused_on_read():
    rec = thirteen_f()
    d = R.record_to_dict(rec)
    assert R.record_from_dict(json.loads(json.dumps(d))) == rec
    d["moves"][0]["listed_on"] = "2025-03-28"
    with pytest.raises(ValueError, match="listed_on"):
        R.record_from_dict(json.loads(json.dumps(d)))


# ── review round 7: Money Map segment labels as the company writes them (live L3) ──


@pytest.mark.parametrize("raw, shown", [
    ("Linked In Corporation", "LinkedIn"), ("LinkedIn Corporation", "LinkedIn"), ("Linked In", "LinkedIn"),
    ("XBOX", "Xbox"), ("Service", "Services"), (" XBOX ", "Xbox"),
    # EXACT match only — never a casing rule: other spellings and every other label are untouched.
    ("Xbox", "Xbox"), ("xbox", "xbox"), ("SERVICE", "SERVICE"), ("Services", "Services"),
    ("linked in corporation", "linked in corporation"), ("iPhone", "iPhone"), ("AWS", "AWS"),
    ("Server Products And Cloud Services", "Server Products And Cloud Services"),
    (None, None), (7, 7),
])
def test_segment_display_names_are_exact_overrides(raw, shown):
    assert R.segment_display_name(raw) == shown


def test_every_segment_override_is_a_valid_segment_name():
    assert R.SEGMENT_DISPLAY_OVERRIDES
    for raw, shown in R.SEGMENT_DISPLAY_OVERRIDES.items():
        assert raw != shown and raw == raw.strip()
        assert R.Segment(shown, 1.0).name == shown


# ── review round 8: an exit's previous-quarter value; a leading "The" ─────────

def _exit(**kw):
    return move("OXY", "Occidental", "no_longer_reported", shares=None, prev_shares=2_000_000.0, value_usd=None,
                listed_on=None, **kw)


def test_an_exit_carries_its_previous_value_and_nothing_else_does():
    assert _exit(prev_value_usd=3e9).prev_value_usd == 3e9
    assert _exit(prev_value_usd=0).prev_value_usd == 0.0 and isinstance(_exit(prev_value_usd=0).prev_value_usd, float)
    assert _exit().prev_value_usd is None                               # optional, defaulted (additive field)
    assert move().prev_value_usd is None
    for bad in (lambda: move(prev_value_usd=1e6),                       # a newly reported move
                lambda: move("AAPL", "Apple", "increased", shares=2.0, prev_shares=1.0, listed_on=None,
                             prev_value_usd=1e6),
                lambda: move("AAPL", "Apple", "decreased", shares=1.0, prev_shares=2.0, listed_on=None,
                             prev_value_usd=1e6),
                lambda: _exit(prev_value_usd=-1.0), lambda: _exit(prev_value_usd=NAN),
                lambda: _exit(prev_value_usd=INF), lambda: _exit(prev_value_usd=True),
                lambda: _exit(prev_value_usd="3000000000")):
        with pytest.raises(ValueError):
            bad()


def test_a_filing_with_a_valued_exit_round_trips_and_its_value_is_not_bounded_by_the_total():
    rec = thirteen_f(moves=(move(), _exit(prev_value_usd=400e9)),     # the previous book can be larger
                     counts=(("newly_reported", 1), ("no_longer_reported", 1)))
    d = R.record_to_dict(rec)
    assert d["moves"][1]["prev_value_usd"] == 400e9 and d["moves"][0]["prev_value_usd"] is None
    text = json.dumps(d, allow_nan=False)
    for seed in range(3):
        assert R.record_from_dict(_shuffle(json.loads(text), random.Random(seed))) == rec
    sheet = R.fact_sheet(rec, rejections={}, selection={})
    assert R.record_from_fact_sheet(json.loads(json.dumps(sheet))) == rec


def test_a_sheet_stored_before_prev_value_usd_still_reads_and_strictness_holds():
    rec = thirteen_f(moves=(move(), _exit(prev_value_usd=3e9)),
                     counts=(("newly_reported", 1), ("no_longer_reported", 1)))
    d = json.loads(json.dumps(R.record_to_dict(rec)))
    for m in d["moves"]:
        del m["prev_value_usd"]                                         # the pre-round-8 shape
    back = R.record_from_dict(d)
    assert [m.prev_value_usd for m in back.moves] == [None, None]
    with pytest.raises(ValueError):                                     # an unknown key is still refused
        R.record_from_dict(dict(d, moves=[dict(d["moves"][0], prev_value=1.0), d["moves"][1]]))
    with pytest.raises(ValueError):                                     # a field with no default is required
        R.record_from_dict(dict(d, moves=[{k: v for k, v in d["moves"][0].items() if k != "listed_on"},
                                          d["moves"][1]]))
    with pytest.raises(ValueError):                                     # and the value is still validated
        R.record_from_dict(dict(d, moves=[dict(d["moves"][0], prev_value_usd=5.0), d["moves"][1]]))


@pytest.mark.parametrize("raw, shown", [
    ("The Kroger Co.", "Kroger"), ("The Charles Schwab Corporation", "Charles Schwab"),
    ("The Allstate Corporation", "Allstate"), ("The Progressive Corporation", "Progressive"),
    ("The AES Corporation", "AES"), ("The Goldman Sachs Group, Inc.", "Goldman Sachs"),
    ("The PNC Financial Services Group, Inc.", "PNC"), ("The Sherwin-Williams Company", "Sherwin-Williams"),
    ("The Kraft Heinz Company", "Kraft Heinz"), ("The Clorox Company", "Clorox"),
    ("The TJX Companies, Inc.", "TJX"), ("The Travelers Companies, Inc.", "Travelers"),
    # never a generic rule: a company whose own name starts with "The" keeps it
    ("The Trade Desk, Inc.", "The Trade Desk"), ("The Cigna Group", "The Cigna Group"),
    ("The Southern Company", "The Southern Company"),
])
def test_a_leading_the_is_dropped_only_by_an_exact_override(raw, shown):
    assert R.display_company_name(raw) == shown


# ── Drop 2b (2026-10-10) ──────────────────────────────────────────────────────

@pytest.mark.parametrize("profile, purpose, reason", [
    # "common" = a US common stock: no ADR, USD, no non-common name — but no cap floor.
    (_profile(isAdr=True), "common", "adr"),
    (_profile(currency="EUR"), "common", "not_usd"),
    (_profile(companyName="GameStop Corp. Warrants"), "common", "non_common_listing"),
    (_profile(companyName="GameStop 8.00% Series A Preferred"), "common", "non_common_listing"),
    (_profile(companyName="GameStop Units"), "earnings", "non_common_listing"),
    (_profile(companyName="GameStop Rights"), "earnings", "non_common_listing"),
    (_profile(companyName="GameStop American Depositary Shares"), "earnings", "non_common_listing"),
    (_profile(companyName="GameStop Senior Notes due 2030"), "common", "non_common_listing"),
    (_profile(isEtf=True), "common", "etf_or_fund"),
    (_profile(exchange="OTC"), "common", "not_major_exchange"),
])
def test_the_common_purpose_and_the_non_common_name(profile, purpose, reason):
    assert R.company_from_profile(profile, "GME", purpose=purpose) == reason
    assert reason in R.REJECTION_REASONS


def test_the_common_purpose_has_no_cap_floor_and_listing_keeps_adrs():
    assert R.company_from_profile(_profile(marketCap=None), "GME", purpose="common") == R.CompanyRef("GME", "GameStop")
    assert R.company_from_profile(_profile(marketCap=1.0), "GME", purpose="common") == R.CompanyRef("GME", "GameStop")
    # A non-common NAME is refused only for earnings / common: the 2a purposes are unchanged.
    for purpose in ("listing", "insider"):
        assert R.company_from_profile(_profile(companyName="GameStop Units"), "GME", purpose=purpose) == \
            R.CompanyRef("GME", "GameStop Units")
    assert R.PROFILE_PURPOSES == ("insider", "earnings", "listing", "common")


@pytest.mark.parametrize("name, hit", [
    ("XYZ Corp Warrants", True), ("XYZ Warrant", True), ("Energy Transfer LP Common Units", True),
    ("XYZ Rights", True), ("XYZ 6.5% Pfd", True), ("XYZ Preferred Stock", True), ("XYZ Notes due 2030", True),
    ("XYZ Debentures", True), ("XYZ American Depositary Shares", True),
    ("United Rentals, Inc.", False), ("Apple Inc.", False), ("Notable Labs", False), ("Rightside Group", False),
    (None, False), (5, False), ("x" * 201, False),
])
def test_non_common_name(name, hit):
    assert R.non_common_name(name) is hit


def test_tsmc_is_shown_by_its_short_name():
    assert R.display_company_name("Taiwan Semiconductor Manufacturing Company Limited") == "TSMC"
    assert R.company_from_profile(_profile(symbol="TSM", companyName="Taiwan Semiconductor Manufacturing Company "
                                                                     "Limited", isAdr=True),
                                  "TSM", purpose="listing") == R.CompanyRef("TSM", "TSMC")


@pytest.mark.parametrize("text, hi, ok", [
    ("NVIDIA 10-Q (quarter ended Jul 26, 2026)", 120, True),
    ("Nscale Form S-1 (Sep 18, 2026)", 120, True),
    ("Taiwan", 40, True),
    ("", 40, False), (" Taiwan", 40, False), ("Taiwan ", 40, False), ("Two  spaces", 40, False),
    ("x" * 121, 120, False), (None, 40, False), (5, 40, False),
    ("See https://www.sec.gov/x", 120, False), ("www.twse.com.tw", 40, False), ("Reuters.com report", 120, False),
    ("ir@example.com", 120, False), ("Follow @caydex", 120, False), ("FMP data", 120, False), ("Financial Modeling Prep", 120, False),
    ("line one\nline two", 120, False), ("Breaking: a deal", 120, False),
])
def test_free_text_ok(text, hi, ok):
    assert R.free_text_ok(text, hi=hi) is ok


@pytest.mark.parametrize("title, problem", [
    ("AI Chips", None), ("Quantum Computing", None), ("Space & Defense", None),
    ("", "theme_title_unusable"), ("A", "theme_title_unusable"), ("x" * 41, "theme_title_unusable"),
    ("Données", "theme_title_unusable"), ("Signal Hill", "theme_title_unusable"),
    ("Breaking AI", "theme_title_unusable"), ("ai.com", "theme_title_unusable"), (None, "theme_title_unusable"),
    ("123", "theme_title_unusable"), (" AI", "theme_title_unusable"),
])
def test_theme_title_problem(title, problem):
    assert R.theme_title_problem(title) == problem


def test_the_2b_constants_are_pinned():
    # Main-session decision (1), 2026-10-09: catalogue stakes may be posted.
    assert R.STAKES_INCLUDE_CATALOGUE is True
    assert (R.STAKE_STALE_DAYS, R.STAKE_MAX_AGE_DAYS, R.STAKE_MAX_VALUE_USD) == (120, 3 * 365, 1e12)
    assert (R.STAKE_SOURCE_TITLE_MAX, R.STAKE_BACKGROUND_MAX, R.STAKE_LOCAL_LISTING_MAX) == (120, 90, 40)
    assert (R.THEME_MIN_FACTS, R.THEME_STALE_DAYS, R.THEME_MEMBERS) == (4, 70, (6, 24))
    assert R.COMPANY_WEEKLY_LAUNCH == date(2026, 11, 16)
    assert R.REVENUE_RATIO_BAND == (0.5, 1.5) and R.EPS_MIN_ABS_ESTIMATE == 0.10


# ── the shared contract (2026-10-10): `ThemeExplainer.theme_size` ─────────────

def test_theme_size_is_the_last_field_optional_and_at_least_the_member_count():
    assert [f.name for f in fields(R.ThemeExplainer)][-1] == "theme_size"
    assert theme().theme_size is None                                    # additive, defaulted
    assert theme(theme_size=6).theme_size == 6                           # == len(members): nothing dropped
    assert theme(theme_size=8).theme_size == 8                           # the gates dropped two
    assert theme(theme_size=R.THEME_SIZE_MAX).theme_size == R.THEME_SIZE_MAX
    for bad in (5, 0, -1, R.THEME_SIZE_MAX + 1, True, 7.0, "7", NAN):
        with pytest.raises(ValueError):
            theme(theme_size=bad)
    with pytest.raises(ValueError):                                     # checked against THIS record's members
        theme(8, theme_size=7)


def test_theme_size_round_trips_and_an_older_sheet_still_reads():
    rec = theme(theme_size=9)
    d = R.record_to_dict(rec)
    assert d["theme_size"] == 9
    text = json.dumps(d, allow_nan=False)
    for seed in range(3):                                               # 9 comes back as 9.0 on some reads
        assert R.record_from_dict(_shuffle(json.loads(text), random.Random(seed))) == rec
    sheet = R.fact_sheet(rec, rejections={"not_major_exchange": 1}, selection={})
    assert R.record_from_fact_sheet(json.loads(json.dumps(sheet))) == rec
    old = {k: v for k, v in json.loads(text).items() if k != "theme_size"}   # stored before the field
    assert R.record_from_dict(old) == theme()
    assert R.record_from_dict(old).theme_size is None
    with pytest.raises(ValueError):                                     # still validated on the way back
        R.record_from_dict(dict(old, theme_size=5))
    with pytest.raises(ValueError):                                     # and still strict on unknown keys
        R.record_from_dict(dict(old, theme_count=9))


@pytest.mark.parametrize("tickers, n", [
    (["NVDA", "AMD", "AVGO"], 3),
    (["NVDA", " nvda ", "AMD"], 2),                 # the card dedupes upper-cased, stripped strings
    (["BRK.B", "BRK-B"], 2),                        # … but not the dot/dash spelling (one member here)
    (["NVDA", "", "  ", "AMD"], 2),                 # blanks are not tickers
    (["ARM", "SMH", "NVDA"], 3),                    # blocked or ETF: still on the card
    ([], 0),
    (None, None), ("NVDA", None), ({"NVDA": 1}, None),
])
def test_theme_ticker_count_is_the_cards_count(tickers, n):
    assert R.theme_ticker_count(tickers) == n
