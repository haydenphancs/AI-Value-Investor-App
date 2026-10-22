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
* the reason-code sets, `SERIES_CLASS`, and the Money Map pool (owner decision 2);
* Batch 1 (2026-10-15): the members-elect window (owner decision 4: an even year's Election Day through
  January 3 — the edges, 2022 / 2026 / 2028, odd years, a 20-year sweep) and the earnings gates
  (`earnings_revenue_pair`, `clear_surprise` exact at 5%, `statement_currency`, `earnings_period` with
  Delta's and Progressive's live figures, the stale-statement lag and the scale-check contingency); the
  Money Map labels (plan item 2): the live overrides ("UNITED STATES", "Latin America (Segment)", exact
  only), `segment_fold_reason` (1-4 capital codes off `SEGMENT_ACRONYMS_OK`, generic labels exact after
  case and spacing, never a word inside a name) and the strict region list (`region_key`, `is_region_name`,
  `takes_region_article`; never a region by a word inside a label), every new guard mutation-checked; and
  names (plan item 5): the "Nautilus Biotechnolgy" override, a depositary-share NAME refused for the
  insider purpose (AgomAb, `isAdr` False), and `takes_name_article` (only the catalogue's descriptive
  "Louisiana data center venture" takes "the"; a lower-case brand never does).

Hermetic: no network, no Supabase. The roster / registry tests point the module at temp files and
clear its cache (the `_fresh_congress` fixture restores the real state afterwards).
"""

import copy
import json
import logging
import math
import random
import re
from dataclasses import fields, replace
from datetime import date, datetime, timedelta
from pathlib import Path

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
    in `THIRTEEN_F_FILERS` (an entity name, or None for a person) or on the unverified list. Batch 1
    (2026-10-15): a successor filer (`THIRTEEN_F_SUCCESSORS`) is the one entry that is not a registry
    CIK — it is reached only through the registry CIK it succeeds."""
    rows = json.loads(R.WHALE_REGISTRY_PATH.read_text(encoding="utf-8"))
    ciks = {R.cik10(r["cik"]) for r in rows if r.get("data_source") == "13f" and r.get("category") == "investors"}
    assert ciks - set(R.THIRTEEN_F_FILERS) == _UNVERIFIED_13F_FILERS
    assert set(R.THIRTEEN_F_FILERS) <= ciks | set(R.THIRTEEN_F_SUCCESSORS.values())
    assert set(R.THIRTEEN_F_FILERS) - ciks == {"0002026053"}
    assert {c for c, n in R.THIRTEEN_F_FILERS.items() if n is None} == {"0000921669", "0000898382"}
    for cik, name in R.THIRTEEN_F_FILERS.items():
        assert R.cik10(cik) == cik
        if name is not None:
            assert R.filer_name_problem(name) is None and not R.filer_looks_like_person(name), name
    assert R.THIRTEEN_F_FILERS["0001549575"] == "Dalal Street"          # not "Pabrai Investment Funds"


def test_thirteen_f_successors_and_lead_filers_are_well_formed():
    """Batch 1 (2026-10-15): Pershing Square's holdings are filed by CIK 0002026053 ("PERSHING SQUARE
    INC.") since its old CIK 0001336528 filed only a 13F-NT for 2026-Q2; Berkshire is read first. A
    successor is one hop from a registry filer (the registry keeps the old CIK), a verified entity, never
    a registry CIK of its own and never the start of a chain; a lead filer is a registry filer whose (own
    or successor) filer is a verified entity."""
    rows = json.loads(R.WHALE_REGISTRY_PATH.read_text(encoding="utf-8"))
    ciks = {R.cik10(r["cik"]) for r in rows if r.get("data_source") == "13f" and r.get("category") == "investors"}
    assert dict(R.THIRTEEN_F_SUCCESSORS) == {"0001336528": "0002026053"}
    assert R.THIRTEEN_F_FILERS["0002026053"] == "Pershing Square"
    for old, new in R.THIRTEEN_F_SUCCESSORS.items():
        assert R.cik10(old) == old and R.cik10(new) == new and old != new
        assert old in ciks, "a successor replaces a registry filer"
        assert new not in ciks, "the registry now lists the successor itself — retire the successor entry"
        assert R.THIRTEEN_F_FILERS.get(new), "a successor must be a verified entity in THIRTEEN_F_FILERS"
        assert new not in R.THIRTEEN_F_SUCCESSORS, "one hop, never a chain"
    assert R.THIRTEEN_F_LEAD_FILERS == ("0001067983",)
    assert len(set(R.THIRTEEN_F_LEAD_FILERS)) == len(R.THIRTEEN_F_LEAD_FILERS)
    for cik in R.THIRTEEN_F_LEAD_FILERS:
        assert cik in ciks and R.THIRTEEN_F_FILERS.get(R.THIRTEEN_F_SUCCESSORS.get(cik, cik))
    assert "split_unchecked" in R.REJECTION_REASONS


def test_every_successor_has_its_first_full_book():
    """The review of 2026-10-16 (live preview F2): `THIRTEEN_F_SUCCESSOR_SINCE` names, for every successor entry
    and only those, the first quarter whose FULL book the successor filed — a valid "YYYY-Qn" (an entry without
    one would refuse its filer every quarter, `successor_transition`). Pershing Square: 2026-Q2 (its 2026-Q1 book
    held Howard Hughes alone)."""
    assert dict(R.THIRTEEN_F_SUCCESSOR_SINCE) == {"0001336528": "2026-Q2"}
    assert set(R.THIRTEEN_F_SUCCESSOR_SINCE) == set(R.THIRTEEN_F_SUCCESSORS)
    for since in R.THIRTEEN_F_SUCCESSOR_SINCE.values():
        assert R.period_label(*map(int, since.split("-Q"))) == since and R.period_end_of(since)
    assert {"split_in_window", "successor_transition"} <= R.REJECTION_REASONS
    assert {"successor_transition", "THIRTEEN_F_SUCCESSOR_SINCE"} <= set(R.__all__)


@pytest.mark.parametrize("cik, period, refused", [
    ("0001336528", "2026-Q2", True),        # the hand-over quarter: Q2 against the successor's own Q1
    ("0001336528", "2026-Q1", True),
    ("0001336528", "2025-Q4", True),
    ("0001336528", "2026-Q3", False),       # Q3 against Q2: two full books
    ("0001336528", "2027-Q1", False),
    ("0001336528", "2026-Q5", True),        # malformed: fail closed
    ("0001336528", None, True),
    ("0001336528", "", True),
    ("0001067983", "2026-Q2", False),       # no successor: its own filer, never a hand-over
    ("0001067983", "junk", False),
    (None, "2026-Q2", False),
])
def test_successor_transition(cik, period, refused):
    assert R.successor_transition(cik, period) is refused


@pytest.mark.parametrize("since", [None, "", "2026-Q5", "2026Q2", 2026, ["2026-Q2"]])
def test_a_successor_without_a_readable_first_full_book_is_refused_every_quarter(monkeypatch, since):
    monkeypatch.setitem(R.THIRTEEN_F_SUCCESSOR_SINCE, "0001336528", since)
    for period in ("2026-Q2", "2026-Q3", "2030-Q4"):
        assert R.successor_transition("0001336528", period) is True
    monkeypatch.delitem(R.THIRTEEN_F_SUCCESSOR_SINCE, "0001336528")
    assert R.successor_transition("0001336528", "2030-Q4") is True


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


# ── the review of 2026-10-16: no company-name word longer than the video cover can draw ──────────────

_W20, _W21 = "W" * 20, "W" * 21


@pytest.mark.parametrize("raw, ok", [
    (f"{_W20} Inc.", True),                                  # exactly the cap: the cover proves it fits
    (f"{_W21} Inc.", False),                                 # one character more: refused
    (f"Acme {_W21}", False),                                 # any word, not only the first
    ("Supercalifragilisticex Corp.", False),                 # 22 letters
    ("Biopharmaceuticals Holdings Inc.", True),              # 18: the longest word seen live
    ("Sherwin-Williams Company", True),                      # a hyphenated word counts whole: 16
    ("Sherwin-Williams-Xyzabc Co", False),                   # 23 with its hyphens: one word
    ("Taiwan Semiconductor Manufacturing Company", True),    # its override "TSMC"
])
@pytest.mark.parametrize("purpose", R.PROFILE_PURPOSES)
def test_a_name_word_the_cover_cannot_draw_is_refused_at_the_profile_gate(raw, ok, purpose):
    """A word longer than `R.COMPANY_NAME_WORD_MAX_CHARS` overflowed the video cover at render — a CardOverflow
    that skips the whole DAY (the Form 4 and Money Map templates checked no word length). Every purpose refuses
    it as ``company_name_unusable`` at the profile gate, so one candidate is lost instead."""
    got = R.company_from_profile(_profile(companyName=raw), "GME", purpose=purpose)
    if ok:
        assert isinstance(got, R.CompanyRef) and not R.company_name_word_too_long(got.name)
    else:
        assert got == "company_name_unusable"


def test_the_word_cap_is_held_by_every_record_and_never_by_the_matching_helpers():
    """`CompanyRef` refuses such a name itself (a stored fact sheet included), while `display_company_name` and
    `company_name_problem` — which key the 13F share-class tie and the earnings issuer dedupe — still read it:
    a name that cannot be drawn must still MATCH its own other listing. Mutation-checked by hand (2026-10-19):
    the `CompanyRef` clause removed turns the record rows red; the cap moved into `company_name_problem`
    turns the matching rows red."""
    assert R.COMPANY_NAME_WORD_MAX_CHARS == 20
    assert R.CompanyRef("GME", f"{_W20} Co").name == f"{_W20} Co"
    for name in (f"{_W21} Co", "Supercalifragilisticex", f"Acme {_W21}"):
        with pytest.raises(ValueError, match="longer than 20 characters"):
            R.CompanyRef("GME", name)
        d = R.record_to_dict(money_map())                        # a stored fact sheet holding such a name
        d["company"]["name"] = name
        with pytest.raises(ValueError, match="longer than 20 characters"):
            R.record_from_dict(json.loads(json.dumps(d)))
    assert R.display_company_name("Supercalifragilisticex Corp.") == "Supercalifragilisticex"
    assert R.company_name_problem("Supercalifragilisticex") is None
    for junk in (None, 7, b"x" * 30, ["W" * 30]):
        assert R.company_name_word_too_long(junk) is False
    assert R.company_name_word_too_long(f"Tab\t{_W21}") is True          # any whitespace splits a word


def test_every_display_override_fits_the_word_cap():
    for value in R.DISPLAY_OVERRIDES.values():
        assert not R.company_name_word_too_long(value), value


@pytest.mark.parametrize("purpose", R.PROFILE_PURPOSES)
def test_archer_daniels_midland_is_shown_as_adm_never_refused(purpose):
    """The re-review of 2026-10-19 (NAME-CAP-1): "Archer-Daniels-Midland" is one 22-character word, so the word cap
    refused ADM ($38.8B, the only such name among 3,686 NYSE / NASDAQ / AMEX companies of $250M or more) for every
    purpose — a Form 4 or an earnings post lost. The company's own brand is shown. Mutation-checked by hand
    (2026-10-19): with the override row removed every purpose turned red (the 22-character word is back)."""
    assert R.display_company_name("Archer-Daniels-Midland Company") == "ADM"
    profile = dict(symbol="ADM", companyName="Archer-Daniels-Midland Company", exchange="NYSE", isEtf=False,
                   isFund=False, isActivelyTrading=True, isAdr=False, currency="USD", marketCap=38.8e9, price=60.0)
    assert R.company_from_profile(profile, "ADM", purpose=purpose) == R.CompanyRef(symbol="ADM", name="ADM")


# ── the review of 2026-10-16 (ST-1): the people a stake's source is likeliest to cite ────────────────


@pytest.mark.parametrize("text, hits", [
    # the review's misses, each through the lesson lexicons
    ("Dario Amodei essay (Oct 2, 2026)", ["Amodei"]), ("Amodei essay", ["Amodei"]), ("Altman blog", ["Altman"]),
    ("Mira Murati interview", ["Murati"]), ("Ilya Sutskever interview", ["Sutskever"]),
    ("Ellison interview", ["Ellison"]), ("Abel letter", ["Abel"]),
    # the forms a hand-kept title takes
    ("BUFFETT letter", ["BUFFETT"]), ("Buffett's 2025 shareholder letter", ["Buffett"]),
    ("Altman-led round (May 2026)", ["Altman"]), ("Fink and Dimon letters", ["Fink", "Dimon"]),
    ("Masayoshi Son interview", ["Son"]), ("Tim Cook keynote", ["Cook"]),
    # never inside a longer word, never a lower-case word, never a company's own document
    ("Abelson report", []), ("Johnson & Sons annual report", []), ("Cookware maker 10-K", []),
    ("cook book", []), ("son of a filing", []), ("Anthropic Series F announcement", []),
    ("Alphabet 2025 Form 10-K", []), ("Microsoft 8-K (Jan 28, 2026)", []), ("Tankers ltd", []), ("", []),
    (None, []), (7, []), (["Altman"], []),
])
def test_stake_person_surname_hits(text, hits):
    assert R.stake_person_surname_hits(text) == hits


_KNOWN = R.stake_known_words(["NVIDIA", "Meta", "Microsoft", "Eli Lilly", "Berkshire Hathaway", "Scale AI",
                              "Mistral AI", "CoreWeave", "Nebius Group", "Tokio Marine Holdings"])


@pytest.mark.parametrize("text, unknown", [
    # the re-review's probes (ST-1-R): names off the surname list, a first name alone, a surname alone
    ("Alexandr Wang memo (Jun 12, 2025)", ["Alexandr", "Wang"]), ("Wang memo", ["Wang"]),
    ("Jensen keynote (Mar 18, 2026)", ["Jensen"]), ("Elon post on X (Mar 2, 2026)", ["Elon"]),
    ("Mensch interview", ["Mensch"]), ("Intrator letter", ["Intrator"]), ("Volozh interview", ["Volozh"]),
    ("Warren Buffett's 2025 shareholder letter", ["Warren", "Buffett"]), ("WANG memo", ["WANG"]),
    # the catalogue's shapes: a known company, document words, dates, codes, single letters, lower-case words
    ("NVIDIA 10-Q (quarter ended Jul 26, 2026)", []), ("Berkshire Hathaway 2025 shareholder letter", []),
    ("Microsoft, NVIDIA and Anthropic release (Nov 18, 2025)", ["Anthropic"]),     # not a known name here
    ("Mistral AI Series D announcement (Sep 8, 2026)", []), ("Lilly Schedule 13G/A (Jul 24, 2026)", []),
    ("NICO large shareholding report to Japan's FSA (Jul 1, 2026)", []), ("CME Group 8-K Ex. 99.1", ["CME"]),
    ("Tokio Marine Holdings release on NICO share allotment (Mar 23, 2026)", []), ("Meta 10-K (FY2025)", []),
    ("Sony and TSMC joint release, TSMC 6-K (Aug 11, 2026)", ["TSMC", "TSMC"]),    # TSMC: not known here
    ("Korea (KOSDAQ)", []), ("Taiwan Stock Exchange", []), ("China (Shenzhen)", []),
    ("nvidia 10-q", []), ("", []), (None, []), (7, []), (["Wang"], []),
], ids=lambda v: str(v)[:24])
def test_stake_text_unknown_words(text, unknown):
    """The re-review of 2026-10-19 (ST-1-R): a POSITIVE list for the source title and the local listing — the
    surname list is a denylist, and every probe of the first row passed it. Mutation-checked by hand (2026-10-20):
    with every word allowed, the probes turned red."""
    assert R.stake_text_unknown_words(text, _KNOWN) == unknown


def test_every_live_catalogue_source_and_local_listing_passes_the_positive_list():
    """Over-block check against the real catalogue (`data/trillion_club_seed.json`, 132 stakes): with the club's and
    the catalogue's company names known — what the adapter passes — every source title and local listing passes.
    A new source shape that fails names its word in `R.STAKE_SOURCE_WORDS` (or a company of the catalogue)."""
    seed = json.loads((Path(__file__).resolve().parents[1] / "data" / "trillion_club_seed.json")
                      .read_text(encoding="utf-8"))
    known = R.stake_known_words([c["display_name"] for c in seed["companies"]]
                                + [s["investee_name"] for s in seed["stakes"]])
    assert len(seed["stakes"]) >= 100
    for s in seed["stakes"]:
        for field in ("source_title", "local_listing"):
            assert R.stake_text_unknown_words(s.get(field), known) == [], (field, s.get(field))
    assert all(w == w.casefold() for w in R.STAKE_SOURCE_WORDS)
    assert not {n.casefold() for n in R.STAKE_SOURCE_PERSON_SURNAMES} & R.STAKE_SOURCE_WORDS
    # a long hostile text stays linear and bounded
    import time
    t0 = time.perf_counter()
    assert R.stake_text_unknown_words("Wang " * 50_000, known) == ["Wang"] * 80          # capped at 400 characters
    assert time.perf_counter() - t0 < 1.0


def test_the_stake_surname_list_is_well_formed():
    assert {"Amodei", "Altman", "Murati", "Sutskever", "Ellison", "Abel", "Huang", "Musk", "Nadella", "Pichai",
            "Zuckerberg", "Bezos", "Jassy", "Cook", "Son", "Buffett", "Munger", "Dimon", "Fink", "Dalio",
            "Ackman", "Icahn"} <= R.STAKE_SOURCE_PERSON_SURNAMES      # the review's list, all of it
    for s in R.STAKE_SOURCE_PERSON_SURNAMES:
        assert re.fullmatch(r"[A-Z][a-z]{2,}", s), s                  # Title form, ASCII letters, 3+
        assert R.stake_person_surname_hits(f"{s} interview") == [s]
        assert R.stake_person_surname_hits(f"{s.upper()} interview") == [s.upper()]
        assert R.stake_person_surname_hits(f"{s}x interview") == []   # whole words only
    # a long hostile text stays linear (one alternation of literals)
    import time
    t0 = time.perf_counter()
    assert len(R.stake_person_surname_hits("Altman " * 20_000)) == 20_000
    assert R.stake_person_surname_hits("Altmanx" * 20_000) == []
    assert time.perf_counter() - t0 < 2.0


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
    assert R.source_label(theme()) == "company revenue breakdowns; grouping by Caydex"
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
        "internal_error",
        # review of 2026-10-16 (ER-2): every quarterly-statement read of an earnings call failed
        "earnings_statements_unavailable"}
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
                                          "same_person",
                                          # Batch 1 (2026-10-15): earnings refuse the WHOLE report — no
                                          # plausible revenue pair, no clear surprise, a statement that
                                          # disagrees with the quarter or cannot place it
                                          "revenue_implausible", "surprise_unclear", "period_mismatch",
                                          "period_unverified",
                                          # Batch 1 (2026-10-15): a Money Map whose next fiscal year
                                          # has ended (its annual report is due)
                                          "money_map_next_year_due",
                                          # Batch 1 (2026-10-15): a 13F more / fewer shares move of a
                                          # filing whose split lookups the cap trimmed
                                          "split_unchecked",
                                          # review of 2026-10-16: a failed earnings statement read; a REIT
                                          # (or unknown-sector) issuer; an actual and estimate on two bases
                                          "statement_unavailable", "eps_basis_reit", "eps_basis_mixed",
                                          # review of 2026-10-16: a 13F more / fewer move beside a split,
                                          # an unnameable adjustment or a failed lookup in its quarter the
                                          # build did not restate; a successor's hand-over quarter
                                          "split_in_window", "successor_transition",
                                          # owner-delegated (2026-10-19): an EPS above $1,000,000 a share
                                          "eps_implausible",
                                          # the re-review of 2026-10-19 (ST-1-R): a stake source word on no list
                                          "stake_source_unverified",
                                          # the round-2 review of 2026-10-20 (RR2-MM-TEMPLATE-WALL): a Money Map
                                          # loss larger than revenue, refused at the read; a record its template
                                          # refused, reported back and remembered
                                          "money_map_loss_above_revenue", "template_refused"}
    # `revenue_dropped` lost its producer in Batch 1 and stays (append-only: older fact sheets carry it).
    assert "revenue_dropped" in R.REJECTION_REASONS
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


def test_att_never_gets_a_money_map_whatever_source_proposes_it():
    """Owner, 2026-10-21: FMP's AT&T segmentation is mislabeled ($24.5B as "Other Capitalized Property
    Plant and Equipment") and its FY2025 broadband / legacy lines look swapped, so AT&T is out of the seed
    AND on the never-list — a Trillion Club or theme source proposing it cannot bring it back."""
    assert "T" not in R.MONEY_MAP_SEED
    assert "T" in R.MONEY_MAP_EXCLUDED_SYMBOLS
    assert "T" not in R.merge_money_map_pool(["T", "t", "AVGO"], ["T"])
    assert "VZ" in R.MONEY_MAP_SEED                     # its neighbour is untouched


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
    # Batch 1 (2026-10-15), live FMP labels: McDonald's capitals, PepsiCo's XBRL suffix — exact only
    ("UNITED STATES", "United States"), (" UNITED STATES ", "United States"), ("United States", "United States"),
    ("UNITED  STATES", "UNITED  STATES"), ("Latin America (Segment)", "Latin America"),
    ("Latin America (segment)", "Latin America (segment)"), ("Europe (Segment)", "Europe (Segment)"),
    # the review of 2026-10-16 (live preview F5): Walmart's labels with the feed's punctuation stripped
    ("Walmart U S", "Walmart U.S."), (" Walmart U S ", "Walmart U.S."), ("Sams Club", "Sam's Club"),
    ("Walmart U.S.", "Walmart U.S."), ("Sam's Club", "Sam's Club"), ("SAMS CLUB", "SAMS CLUB"),
    ("Walmart US", "Walmart US"), ("Sams Clubs", "Sams Clubs"),
    # a company-scoped row never applies without its company (MM-1)
    ("High-Growth Markets", "High-Growth Markets"),
])
def test_segment_display_names_are_exact_overrides(raw, shown):
    assert R.segment_display_name(raw) == shown


@pytest.mark.parametrize("raw, symbol, shown", [
    # the review of 2026-10-16 (MM-1, confirmed on EDGAR): McDonald's FY2025 breakdown carries its 2015-2018
    # label for what it has reported since 2019 as International Operated Markets — for McDonald's ONLY
    ("High-Growth Markets", "MCD", "International Operated Markets"),
    (" High-Growth Markets ", "MCD", "International Operated Markets"),
    ("High-Growth Markets", "mcd", "International Operated Markets"),        # the symbol is canonicalised
    ("High-Growth Markets", " MCD ", "International Operated Markets"),
    ("High-Growth Markets", "YUM", "High-Growth Markets"),                    # another company may use the name
    ("High-Growth Markets", None, "High-Growth Markets"),
    ("High Growth Markets", "MCD", "High Growth Markets"),                    # exact label only
    ("HIGH-GROWTH MARKETS", "MCD", "HIGH-GROWTH MARKETS"),
    # the label-only table still answers for the company's other labels
    ("UNITED STATES", "MCD", "United States"), ("Linked In Corporation", "MSFT", "LinkedIn"),
    # the re-review of 2026-10-19 (checked on EDGAR): PepsiCo's realigned EMEA under the pre-2025 AMESA name, and
    # its 10-K's "International Beverages Franchise" — for PepsiCo ONLY
    ("Africa, Middle East and South Asia", "PEP", "Europe, Middle East and Africa"),
    ("Africa, Middle East and South Asia", "KO", "Africa, Middle East and South Asia"),
    ("Africa, Middle East and South Asia", None, "Africa, Middle East and South Asia"),
    ("International Beverage Franchise", "PEP", "International Beverages Franchise"),
    ("International Beverage Franchise", "KDP", "International Beverage Franchise"),
    ("Latin America (Segment)", "PEP", "Latin America"),                       # the label-only row still answers
    # junk symbols never raise and never match
    ("High-Growth Markets", "", "High-Growth Markets"), ("High-Growth Markets", 7, "High-Growth Markets"),
    ("High-Growth Markets", "MCD-WT", "High-Growth Markets"), ("High-Growth Markets", ["MCD"], "High-Growth Markets"),
    (None, "MCD", None), (7, "MCD", 7),
])
def test_company_scoped_segment_overrides(raw, symbol, shown):
    assert R.segment_display_name(raw, symbol=symbol) == shown


def test_every_company_scoped_override_is_a_valid_unfolded_name_and_never_label_only():
    """Each row is keyed by a canonical symbol and the label exactly as the feed spells it (stripped), shows a
    drawable `Segment` name that is never folded right back — and its label is NEVER also a label-only row,
    which would rename another company's real segment of that name."""
    assert ("MCD", "High-Growth Markets") in R.SEGMENT_SYMBOL_OVERRIDES
    assert ("PEP", "Africa, Middle East and South Asia") in R.SEGMENT_SYMBOL_OVERRIDES
    for (sym, raw), shown in R.SEGMENT_SYMBOL_OVERRIDES.items():
        assert R.canonical_symbol(sym) == sym and raw == raw.strip() and raw != shown
        assert R.Segment(shown, 1.0).name == shown
        assert R.segment_fold_reason(shown) is None, shown
        assert raw not in R.SEGMENT_DISPLAY_OVERRIDES, raw
    assert not R.is_region_name(R.SEGMENT_SYMBOL_OVERRIDES[("MCD", "High-Growth Markets")])  # markets it runs


def test_every_segment_override_is_a_valid_segment_name():
    assert R.SEGMENT_DISPLAY_OVERRIDES
    for raw, shown in R.SEGMENT_DISPLAY_OVERRIDES.items():
        assert raw != shown and raw == raw.strip()
        assert R.Segment(shown, 1.0).name == shown
        assert R.segment_fold_reason(shown) is None, shown     # Batch 1: never folded right back


# ── Batch 1 (2026-10-15, plan item 2): labels that name nothing; the regions ──


@pytest.mark.parametrize("name, reason", [
    # company codes: the WHOLE label 1-4 capitals (Qualcomm's QCT / QTL / QSI, PepsiCo's PBNA / PFNA / IBF)
    ("QCT", "code"), ("QTL", "code"), ("NXE", "code"), ("CMBU", "code"), ("PBNA", "code"), ("IBF", "code"),
    (" QCT ", "code"), ("IT", "code"), ("A", "code"),
    # the allow-list: the company's own public name for the line, and the region names written in capitals
    ("AWS", None), ("EMEA", None), ("APAC", None),
    # the review of 2026-10-16 (MM-2): a country or union a reader knows is a region, never a company code
    ("US", None), ("USA", None), ("UK", None), ("EU", None), ("UAE", None), (" US ", None), ("Us", None),
    ("USX", "code"), ("UKX", "code"), ("EUR", "code"),
    # not the code shape: mixed case, digits, punctuation, 5+ capitals ("UNITED STATES" has its override)
    ("Xbox", None), ("iPad", None), ("IoT", None), ("5G", None), ("U.S.", None), ("R&D", None), ("QCTX", "code"),
    ("QCTXY", None), ("AWS Cloud", None), ("aws", None), ("XBOX", "code"),
    # generic labels, exact after case and spacing
    ("Product", "generic"), ("Products", "generic"), ("PRODUCTS", "generic"), (" products ", "generic"),
    ("Segment", "generic"), ("segments", "generic"), ("Revenue", "generic"), ("Revenues", "generic"),
    ("Sales", "generic"), ("Net sales", "generic"), ("NET  SALES", "generic"), ("Total", "generic"),
    ("TOTAL", "generic"),
    # a word INSIDE a real name is no generic label
    ("Product Revenue", None), ("Server Products And Cloud Services", None), ("Total Gas & Power", None),
    ("Services", None), ("Net Sales Growth", None),
    # not a string: the caller's own gates refuse it
    (None, None), (7, None), (b"QCT", None),
])
def test_segment_fold_reason(name, reason):
    assert R.segment_fold_reason(name) == reason


def test_the_acronym_allow_list_holds_only_live_code_shapes():
    """An allow-list entry that does not match the code shape could never be folded, so it would be dead."""
    for code in R.SEGMENT_ACRONYMS_OK:
        assert R._SEGMENT_CODE_RE.fullmatch(code), code
        assert R.segment_fold_reason(code) is None
    assert "AWS" in R.SEGMENT_ACRONYMS_OK            # Amazon's own name for the line (the plan's must)
    for label in R.GENERIC_SEGMENT_LABELS:
        assert label == " ".join(label.casefold().split()), label      # stored in the compared form


@pytest.mark.parametrize("name, key", [
    ("United States", "united states"), ("UNITED STATES", "united states"), ("  United   States ", "united states"),
    ("Asia-Pacific", "asia pacific"), ("Asia–Pacific", "asia pacific"), ("Europe, Middle East & Africa",
                                                                         "europe middle east and africa"),
    ("Rest of World", "rest of world"), ("Rest of the World", "rest of the world"), ("U.S.", "u s"),
    ("Ｊａｐａｎ", "japan"), (None, ""), (7, ""),
])
def test_region_key(name, key):
    assert R.region_key(name) == key


@pytest.mark.parametrize("name, region, article", [
    ("United States", True, True), ("UNITED STATES", True, True), ("Canada", True, False),
    ("Americas", True, True), ("North America", True, False), ("Latin America", True, False),
    ("Europe, Middle East and Africa", True, False), ("EMEA", True, False), ("Asia Pacific", True, False),
    ("Asia-Pacific", True, False), ("Greater China", True, False), ("Japan", True, False),
    ("International", True, False), ("Other International", True, False),
    ("United Kingdom", True, True), ("Middle East and Africa", True, True),
    # the review of 2026-10-16: the acronyms are regions (MM-2), and every name that needs "the" in a sentence
    # takes it (MM-3: "U.S. made up 50% of revenue" was narrated and burned into the captions)
    ("U.S.", True, True), ("US", True, True), ("USA", True, True), ("U.S.A.", True, True), ("UK", True, True),
    ("U.K.", True, True), ("EU", True, True), ("UAE", True, True), ("United States and Canada", True, True),
    ("U.S. and Canada", True, True), ("US & Canada", True, True), ("Rest of the World", True, True),
    ("Rest of World", True, True), ("Rest of Asia Pacific", True, True),
    # never a region by a word inside it (McDonald's markets it runs; PepsiCo's retired AMESA label — shown for
    # PepsiCo as its realigned "Europe, Middle East and Africa" since the re-review of 2026-10-19)
    ("High-Growth Markets", False, False), ("Frito-Lay North America", False, False),
    ("International Operated Markets", False, False), ("Africa, Middle East and South Asia", False, False),
    ("The Americas", False, False), ("U.S. Retail", False, False), ("Walmart U.S.", False, False),
    ("USA Today", False, False), ("Domestic", False, False),
    ("Other", False, False), ("", False, False), (None, False, False), (["United States"], False, False),
])
def test_region_names_are_exact(name, region, article):
    assert R.is_region_name(name) is region
    assert R.takes_region_article(name) is article


def test_region_sets_are_consistent():
    assert R.REGION_ARTICLE <= R.REGION_NAMES
    acronyms = {"emea", "apac", "us", "usa", "uk", "eu", "uae"}       # the region names written as acronyms
    assert acronyms <= R.REGION_NAMES
    for key in R.REGION_NAMES:
        assert R.region_key(key) == key, key                          # stored in the compared form
        # a region name the fold removes would be dead — in capitals too ("ASIA" is code-shaped)
        for form in (key.upper(), key.title()):
            assert R.segment_fold_reason(form) is None, form
    assert {k.upper() for k in acronyms} <= R.SEGMENT_ACRONYMS_OK
    for key in R.REGION_ARTICLE:
        assert R.Segment(key.title(), 1.0)                            # drawable as a segment name


def test_a_region_is_never_folded_even_off_the_allow_list(monkeypatch):
    """The review of 2026-10-16 (MM-2): `segment_fold_reason` reads the region list itself, so a region acronym
    can never fold into the rest even if the allow-list loses it. Mutation-checked by hand (2026-10-19): with the
    `is_region_name` clause removed, "US" folds as a code here."""
    monkeypatch.setattr(R, "SEGMENT_ACRONYMS_OK", frozenset({"AWS"}))
    for label in ("US", "USA", "UK", "EU", "UAE", "EMEA", "APAC"):
        assert R.segment_fold_reason(label) is None, label
    assert R.segment_fold_reason("QCT") == "code"


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
    # A non-common NAME is kept only for "listing" (a 13F holding, a Money Map, a stake's investor, a theme
    # member — an ADR included); Batch 1 (2026-10-15) refuses it for "insider" too, with earnings / common.
    assert R.company_from_profile(_profile(companyName="GameStop Units"), "GME", purpose="listing") == \
        R.CompanyRef("GME", "GameStop Units")
    for purpose in ("insider", "earnings", "common"):
        assert R.company_from_profile(_profile(companyName="GameStop Units"), "GME", purpose=purpose) == \
            "non_common_listing", purpose
    assert R.PROFILE_PURPOSES == ("insider", "earnings", "listing", "common")


#: AgomAb Therapeutics as FMP's profile gave it (the insider_buys preview of 2026-10-12): a depositary-share
#: line by its NAME while `isAdr` is False — the post would have said "disclosed buying … of the company's
#: stock" about American Depositary Shares.
_AGOMAB = dict(symbol="AGMB", exchange="NASDAQ", isAdr=False, marketCap=6.2e8,
               companyName="AgomAb Therapeutics N.V. American Depositary Shares")


def test_an_insider_issuer_whose_line_is_a_depositary_share_is_refused():
    """Batch 1 (2026-10-15, plan item 5): the "insider" purpose refuses a non-common NAME like earnings and
    common do — the only gate for a depositary line FMP does not flag `isAdr`. The listing purpose (a 13F
    holding, a Money Map) still names it, its class tail stripped. Mutation-checked by hand (2026-10-15):
    with "insider" out of the purpose tuple again, the first assertion turned red."""
    assert R.company_from_profile(_profile(**_AGOMAB), "AGMB", purpose="insider") == "non_common_listing"
    assert R.company_from_profile(_profile(**_AGOMAB), "AGMB", purpose="listing") == \
        R.CompanyRef("AGMB", "AgomAb Therapeutics")
    # the order of the gates is unchanged: an ADR flag, a currency and the cap floor are read first
    assert R.company_from_profile(_profile(**dict(_AGOMAB, isAdr=True)), "AGMB", purpose="insider") == "adr"
    assert R.company_from_profile(_profile(**dict(_AGOMAB, marketCap=1.0)), "AGMB", purpose="insider") == \
        "below_cap_floor"
    # an ordinary common-stock issuer still passes, and the reason is a counted rejection
    assert R.company_from_profile(_profile(), "GME", purpose="insider") == R.CompanyRef("GME", "GameStop")
    assert "non_common_listing" in R.REJECTION_REASONS


def test_a_vendor_typo_in_a_company_name_is_shown_as_the_company_spells_itself():
    """FMP's NAUT profile reads "Nautilus Biotechnolgy, Inc." (the SEC's own name is spelled right): a
    `DISPLAY_OVERRIDES` row shows it as "Nautilus Biotechnology" everywhere a profile names it."""
    assert R.display_company_name("Nautilus Biotechnolgy, Inc.") == "Nautilus Biotechnology"
    assert R.display_company_name("NAUTILUS BIOTECHNOLGY INC") == "Nautilus Biotechnology"
    assert R.display_company_name("Nautilus Biotechnology, Inc.") == "Nautilus Biotechnology"   # already right
    naut = _profile(symbol="NAUT", exchange="NASDAQ", companyName="Nautilus Biotechnolgy, Inc.", marketCap=3.0e8)
    for purpose in ("insider", "listing", "common"):          # (earnings' $2B floor is above NAUT's cap)
        assert R.company_from_profile(naut, "NAUT", purpose=purpose) == \
            R.CompanyRef("NAUT", "Nautilus Biotechnology"), purpose


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
    # Batch 1 (2026-10-15): the earnings gates; the scale check is a contingency, OFF.
    assert (R.EARNINGS_CLEAR_SURPRISE, R.EARNINGS_PERIOD_WINDOW_DAYS, R.EARNINGS_PERIOD_MAX_LAG_DAYS,
            R.EARNINGS_PERIOD_REVENUE_TOLERANCE) == (0.05, 120, 90, 0.03)
    assert R.EARNINGS_ALLOW_SCALE_CHECK is False and R.EARNINGS_SCALE_BAND == (0.75, 1.33)


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


def _p(sym, name, cik):
    return {"symbol": sym, "companyName": name, "cik": cik}


@pytest.mark.parametrize("tickers, profiles, n", [
    (["NVDA", "AMD", "AVGO"], None, 3),
    (["NVDA", " nvda ", "AMD"], None, 2),           # upper-cased, stripped strings, as the card dedupes
    (["BRK.B", "BRK-B"], None, 1),                  # one ticker in dot and dash form (one member too)
    (["NVDA", "", "  ", "AMD"], None, 2),           # blanks are not tickers
    (["ARM", "SMH", "NVDA"], None, 3),              # blocked (never profiled) or an ETF: each its own
    ([], None, 0),
    (None, None, None), ("NVDA", None, None), ({"NVDA": 1}, None, None),
    # Review round 15: share classes of ONE issuer count once — the same display name …
    (["GOOG", "GOOGL"], {"GOOG": _p("GOOG", "Alphabet Inc.", 1), "GOOGL": _p("GOOGL", "Alphabet Inc. Class A Common Stock", 2)},
     1),
    # … or the same CIK under another name …
    (["GOOG", "GOOGL"], {"GOOG": _p("GOOG", "Alphabet Inc.", "1652044"),
                         "GOOGL": _p("GOOGL", "Google Inc.", "0001652044")}, 1),
    # … and transitively (A~B by CIK, B~C by name).
    (["AAA", "BBB", "CCC"], {"AAA": _p("AAA", "Acme Corp", 7), "BBB": _p("BBB", "Acme Holdings", 7),
                             "CCC": _p("CCC", "Acme Holdings", 9)}, 1),
    # Another name AND another CIK: two companies.
    (["GOOG", "GOOGL"], {"GOOG": _p("GOOG", "Alphabet Inc.", 1), "GOOGL": _p("GOOGL", "Google Inc.", 2)}, 2),
    # Unusable identity never merges: no profile, a profile for another symbol, an all-zero / bool CIK,
    # an unusable name, profiles that are not a mapping.
    (["GOOG", "GOOGL"], {"GOOG": _p("GOOG", "Alphabet Inc.", 1)}, 2),
    (["GOOG", "GOOGL"], {"GOOG": _p("GOOG", "Alphabet Inc.", 1), "GOOGL": _p("GOOG", "Alphabet Inc.", 1)}, 2),
    (["GOOG", "GOOGL"], {"GOOG": _p("GOOG", None, "0"), "GOOGL": _p("GOOGL", None, "0000000000")}, 2),
    (["GOOG", "GOOGL"], {"GOOG": _p("GOOG", "Inc.", True), "GOOGL": _p("GOOGL", "Inc.", True)}, 2),
    (["GOOG", "GOOGL"], [_p("GOOG", "Alphabet Inc.", 1), _p("GOOGL", "Alphabet Inc.", 1)], 2),
    # A ticker the symbol grammar refuses keeps its own spelling (never merged through a profile).
    (["GOOG", "GOOG.WS"], {"GOOG": _p("GOOG", "Alphabet Inc.", 1)}, 2),
])
def test_theme_company_count_counts_issuers_not_tickers(tickers, profiles, n):
    assert R.theme_company_count(tickers, profiles) == n


# ── Batch 1 (2026-10-15): the members-elect window (owner decision 4) ─────────

@pytest.mark.parametrize("day, inside", [
    (date(2026, 11, 2), False), (date(2026, 11, 3), True),        # Election Day 2026: Tue Nov 3
    (date(2026, 12, 31), True), (date(2027, 1, 1), True),
    (date(2027, 1, 3), True), (date(2027, 1, 4), False),          # the new Congress is sworn in on Jan 3
    (date(2022, 11, 7), False), (date(2022, 11, 8), True),        # Nov 1, 2022 was a Tuesday
    (date(2028, 11, 6), False), (date(2028, 11, 7), True),        # Nov 1, 2028 is a Wednesday
    (date(2027, 11, 2), False), (date(2027, 12, 31), False),      # an odd year: no federal general election
    (date(2028, 1, 2), False),                                    # Jan 1-3 after an odd year
    (date(2026, 10, 15), False), (date(2026, 6, 30), False),
])
def test_the_members_elect_window(day, inside):
    assert R.members_elect_window(day) is inside


def test_election_day_is_the_tuesday_after_the_first_monday_of_november_for_twenty_years():
    known = {2020: date(2020, 11, 3), 2022: date(2022, 11, 8), 2024: date(2024, 11, 5), 2026: date(2026, 11, 3),
             2028: date(2028, 11, 7), 2030: date(2030, 11, 5), 2032: date(2032, 11, 2)}
    for year in range(2020, 2040):
        e = R.general_election_day(year)
        assert e.weekday() == 1 and date(year, 11, 2) <= e <= date(year, 11, 8), (year, e)
        first_monday = e - timedelta(days=1)
        assert first_monday.weekday() == 0 and first_monday.day <= 7
        if year in known:
            assert e == known[year]
        # the window opens ON Election Day of an even year, never the day before, never in an odd year
        assert R.members_elect_window(e) is (year % 2 == 0)
        assert R.members_elect_window(e - timedelta(days=1)) is False
        assert R.members_elect_window(date(year + 1, 1, 3)) is (year % 2 == 0)
        assert R.members_elect_window(date(year + 1, 1, 4)) is False


@pytest.mark.parametrize("bad", [datetime(2026, 11, 16, 12, 0), "2026-11-16", None, 20261116])
def test_the_window_reads_a_date_never_a_datetime_or_text(bad):
    with pytest.raises(ValueError):
        R.members_elect_window(bad)


@pytest.mark.parametrize("bad", [True, 2026.0, "2026", None])
def test_election_day_needs_an_int_year(bad):
    with pytest.raises(ValueError):
        R.general_election_day(bad)


@pytest.mark.parametrize("run_date, role_only", [
    # review of 2026-10-16 (ME-1): a run's posts can go out on the run day, the next ET day, and (an Upload-Post
    # resend of an unacknowledged job, ≤ 20 h after the first send) the day after — so the window is judged over
    # the run date and the two days after it. Election Day 2026 is Tue Nov 3.
    (date(2026, 10, 31), False),                 # its posts go out Oct 31 .. Nov 2: before the window
    (date(2026, 11, 1), True), (date(2026, 11, 2), True),       # a post of theirs can go out on Election Day
    (date(2026, 11, 3), True), (date(2026, 12, 31), True),
    (date(2027, 1, 3), True),                    # the last day of the window itself
    (date(2027, 1, 4), False),                   # the new Congress is sworn in (Jan 3): its posts are after
    (date(2028, 11, 4), False), (date(2028, 11, 5), True),      # Election Day 2028 is Tue Nov 7
    (date(2027, 10, 31), False), (date(2027, 11, 1), False),    # an odd year: no federal general election
    (date(2027, 12, 30), False),                 # Jan 1 2028 follows an ODD year: no window
    (date(2026, 12, 30), True), (date(2027, 1, 2), True),
])
def test_the_members_elect_check_covers_every_day_a_runs_posts_can_go_out(run_date, role_only):
    """Mutation-checked by hand (2026-10-16): judging the run date alone (`members_elect_window(run_date)`)
    turned the Nov 1 / Nov 2 and 2028-11-05 rows red."""
    assert R.MEMBERS_ELECT_PUBLISH_DAYS == 2
    assert R.members_elect_for_run(run_date) is role_only
    span = [run_date + timedelta(days=i) for i in range(R.MEMBERS_ELECT_PUBLISH_DAYS + 1)]
    assert role_only is any(R.members_elect_window(d) for d in span)        # the two ends decide every day


def test_the_run_check_reads_the_window_at_call_time(monkeypatch):
    """The helper calls `members_elect_window` through the module at call time: the suites that switch the
    window off (or on) switch the run check with it — one switch, no second path that could disagree."""
    monkeypatch.setattr(R, "members_elect_window", lambda day: False)
    assert R.members_elect_for_run(date(2026, 11, 2)) is False
    monkeypatch.setattr(R, "members_elect_window", lambda day: True)
    assert R.members_elect_for_run(date(2026, 6, 30)) is True


@pytest.mark.parametrize("bad", [datetime(2026, 11, 2, 12, 0), "2026-11-02", None, 20261102])
def test_the_run_check_reads_a_date_never_a_datetime_or_text(bad):
    with pytest.raises(ValueError):
        R.members_elect_for_run(bad)


# ── Batch 1 (2026-10-15): the earnings gates ──────────────────────────────────

@pytest.mark.parametrize("pairs, out", [
    ([(1.1e9, 1.0e9)], (1.1e9, 1.0e9)),
    ([(1.1e9, 1.0e9), (1.1e9, 1.0e9)], (1.1e9, 1.0e9)),               # every row agrees
    ([(1.5e9, 1.0e9)], (1.5e9, 1.0e9)), ([(0.5e9, 1.0e9)], (0.5e9, 1.0e9)),   # the band, inclusive
    ([(1.51e9, 1.0e9)], None), ([(0.49e9, 1.0e9)], None),
    ([(7.16e9, 22.6e9)], None),                                        # Progressive's one-month row
    ([(673e6, 24e6)], None),                                           # Nurix's one-off revenue
    ([(1.1e9, 1.0e9), (1.2e9, 1.0e9)], None),                          # rows that disagree
    ([(1.1e9, 1.0e9), (None, None)], None),                            # a row without revenue beside one with it
    ([(None, None)], None), ([(1.1e9, None)], None), ([(None, 1.0e9)], None),
    ([(0.0, 1.0e9)], None), ([(1.0e9, 0.0)], None), ([(-1.0e9, 1.0e9)], None), ([(1.0e9, -1.0e9)], None),
    ([(NAN, 1.0e9)], None), ([(1.0e9, INF)], None), ([(True, 1.0e9)], None), ([("1e9", 1.0e9)], None),
    ([(10 ** 400, 1.0e9)], None),
    ([], None), (None, None), ("x", None), ([[1.1e9, 1.0e9]], None), ([(1.1e9,)], None),
])
def test_earnings_revenue_pair(pairs, out):
    assert R.earnings_revenue_pair(pairs) == out


@pytest.mark.parametrize("actual, estimate, clear", [
    (1.05, 1.00, True), (1.0499, 1.00, False), (0.95, 1.00, True), (0.9501, 1.00, False),
    (0.21, 0.20, True), (0.2099, 0.20, False),          # exact on the figures: the float quotient is 0.0499…
    (-0.95, -1.00, True), (-0.951, -1.00, False), (4.73, -0.60, True), (1.00, 1.00, False),
    (1.72, 1.77, False),                                 # Delta's live pair (2.8%)
    (1.08, 4.35, True),
    (1.0, 0.0, False), (NAN, 1.0, False), (1.0, INF, False), (True, 1.0, False), ("1.1", 1.0, False),
    (None, 1.0, False), (10 ** 400, 1.0, False),
    # review of 2026-10-16 (ER-1): clear as written but NOT as printed (two places, half-up — what the post
    # shows): "$0.10 vs $0.10", "$0.14 vs $0.14", "-$0.10 vs -$0.10" and "$0.30 vs $0.29" (3.4%)
    (0.095, 0.10, False), (0.1449, 0.1376, False), (-0.095, -0.10, False), (0.3049, 0.2895, False),
    # ... and clear as printed but not as written: 4.99% stays refused ("$1.05 vs $1.00" on the post)
    (1.0499, 1.00, False), (0.2099, 0.20, False),
    # clear both ways, at the printed edge: "$0.21 vs $0.20" (5.0%), "$0.105" prints "$0.11" vs "$0.10"
    (0.105, 0.10, True), (-0.105, -0.10, True), (0.315, 0.30, True),
    # an estimate that prints "$0.00" is never a base (the record gates refuse it first)
    (0.004, 0.001, False),
])
def test_clear_surprise(actual, estimate, clear):
    assert R.clear_surprise(actual, estimate) is clear


def test_the_printed_eps_is_the_templates_eps_image():
    """`R.printed_eps` (the clear-surprise gate's printed figure) is exactly the figure `news_templates.eps_image`
    writes — half-up on the magnitude, the sign put back — over a sweep of edges and random figures. A drift
    would judge one figure and print another."""
    import random

    from app.services.marketing import news_templates as T

    rng = random.Random(20261016)
    values = [0.095, -0.095, 0.105, -0.105, 0.005, -0.005, 0.0049, 1.005, 2.675, -2.675, 0.1449, 0.1376, 4.35,
              1234.565, 0.0, 12.0, 0.2895, 0.3049]
    values += [round(rng.uniform(-50, 50), rng.choice((2, 3, 4))) for _ in range(2000)]
    for v in values:
        p = R.printed_eps(v)
        shown = T.eps_image(v)
        assert shown == (f"-${abs(p):,.2f}" if p < 0 else f"${abs(p):,.2f}"), (v, p, shown)
    assert R.EPS_PRINT_PLACES == 2


@pytest.mark.parametrize("actual, estimate, clear", [
    (1e26, 9e25, True), (1e26, 9.9e25, False), (1e300, 1e299, True), (-1e300, -1e299, True),
    (1.7976931348623157e308, 1e308, True), (1e308, 1.7976931348623157e308, True), (10 ** 30, 9 * 10 ** 29, True),
    (5e-324, 0.1, True), (0.1, 5e-324, False),
])
def test_the_eps_gates_are_exact_and_never_raise_on_an_extreme_figure(actual, estimate, clear):
    """Hardening of the ER-1 printed-figure gate (review of 2026-10-16): `printed_eps` quantized in the default
    28-digit Decimal context, which cannot hold a figure of 1e26 or more — `clear_surprise` raised
    `decimal.InvalidOperation`, and the adapter turns any exception into ``internal_error`` for the whole earnings
    series. Both now compute in `R.EPS_DECIMAL_PRECISION` digits: exact for every finite float, never raising, and
    leaving the caller's Decimal context as it found it. Mutation-checked by hand (2026-10-16): with ``ctx.prec =
    28`` every row from 1e26 up raised (red)."""
    import decimal

    before = decimal.getcontext().prec
    assert R.clear_surprise(actual, estimate) is clear
    for v in (actual, estimate):
        p = R.printed_eps(v)
        assert p.as_tuple().exponent == -R.EPS_PRINT_PLACES
        assert abs(p - decimal.Decimal(repr(float(v)))) <= decimal.Decimal("0.005"), v
    assert decimal.getcontext().prec == before
    assert R.EPS_DECIMAL_PRECISION >= len(str(int(1.7976931348623157e308))) + R.EPS_PRINT_PLACES + 2


@pytest.mark.parametrize("rows, code", [
    ([{"date": "2026-12-31", "reportedCurrency": "USD"}], "USD"),
    ([{"date": "2026-12-31", "reportedCurrency": " usd "}], "USD"),
    ([{"date": "2026-09-30", "reportedCurrency": "USD"}, {"date": "2026-12-31", "reportedCurrency": "EUR"}], "EUR"),
    ([{"date": "2026-12-31", "reportedCurrency": "USD"}, {"date": "2026-12-31", "reportedCurrency": "USD"}], "USD"),
    ([{"date": "2026-12-31", "reportedCurrency": "USD"}, {"date": "2026-12-31", "reportedCurrency": "CAD"}], None),
    ([{"date": "2026-12-31"}], None), ([{"date": "2026-12-31", "reportedCurrency": ""}], None),
    ([{"date": "2026-12-31", "reportedCurrency": 840}], None),
    ([{"reportedCurrency": "USD"}], None),                               # no period end to rank it by
    ([{"date": "zzzz-99-99", "reportedCurrency": "EUR"}, {"date": "2026-12-31", "reportedCurrency": "USD"}], "USD"),
    ([], None), (None, None), ({"x": 1}, None), ([7, "x"], None),
])
def test_statement_currency_is_the_newest_readable_periods(rows, code):
    assert R.statement_currency(rows) == code


_REPORT = date(2026, 10, 9)


def _q(*quarters):
    return [{"date": d, "revenue": rev, "reportedCurrency": "USD"} for d, rev in quarters]


#: The quarter before Delta's Sep 30 quarter (92 days earlier): every case below that is not about the
#: quarter's SHAPE carries it, so the newest row is a quarter (review of 2026-10-16, ER-4).
_JUN = ("2026-06-30", 16_650_000_000)


@pytest.mark.parametrize("rows, actual, out", [
    # Delta (live, 2026-10-15): the statement's $20.186B vs the calendar's $20.2B → the quarter ended Sep 30
    (_q(("2026-09-30", 20_186_000_000), _JUN), 20.2e9, (date(2026, 9, 30), None)),
    # the order of the rows does not matter
    (_q(_JUN, ("2026-09-30", 20_186_000_000)), 20.2e9, (date(2026, 9, 30), None)),
    # within 3%, both ways, inclusive; past it, a mismatch
    (_q(("2026-09-30", 20_806_000_000), _JUN), 20.2e9, (date(2026, 9, 30), None)),
    (_q(("2026-09-30", 19_594_000_000), _JUN), 20.2e9, (date(2026, 9, 30), None)),
    (_q(("2026-09-30", 20_807_000_000), _JUN), 20.2e9, (None, "period_mismatch")),
    # Progressive's one-month row against its quarter's statement
    (_q(("2026-09-30", 23_180_000_000), _JUN), 7.16e9, (None, "period_mismatch")),
    # the newest row decides — an older matching row in the window is not the reported quarter
    (_q(("2026-09-30", 30_000_000_000), ("2026-06-30", 20_200_000_000)), 20.2e9, (None, "period_mismatch")),
    # the reported quarter is missing: the newest row ended 101 days before the report (the quarter before)
    (_q(("2026-06-30", 20_200_000_000), ("2026-03-31", 19_000_000_000)), 20.2e9, (None, "period_unverified")),
    # exactly 90 days before is still the reported quarter (its own span is 91 days); 91 is not
    (_q(("2026-07-11", 20_200_000_000), ("2026-04-11", 19_000_000_000)), 20.2e9, (date(2026, 7, 11), None)),
    (_q(("2026-07-10", 20_200_000_000), ("2026-04-10", 19_000_000_000)), 20.2e9, (None, "period_unverified")),
    # nothing in [report - 120, report); a period ending on (or after) the report day
    (_q(("2026-06-10", 20_200_000_000), ("2026-03-10", 19_000_000_000)), 20.2e9, (None, "period_unverified")),
    (_q(("2026-10-09", 20_200_000_000), ("2026-09-30", 20_186_000_000)), 20.2e9, (None, "period_unverified")),
    (_q(("2026-12-31", 20_200_000_000), ("2026-09-30", 20_186_000_000)), 20.2e9, (None, "period_unverified")),
    # rows on the newest period must all agree
    (_q(("2026-09-30", 20_186_000_000), ("2026-09-30", 20_190_000_000), _JUN), 20.2e9, (date(2026, 9, 30), None)),
    (_q(("2026-09-30", 20_186_000_000), ("2026-09-30", 25_000_000_000), _JUN), 20.2e9, (None, "period_mismatch")),
    # unreadable figures and rows
    (_q(("2026-09-30", None), _JUN), 20.2e9, (None, "period_unverified")),
    (_q(("2026-09-30", "20186000000"), _JUN), 20.2e9, (None, "period_unverified")),
    (_q(("2026-09-30", 0), _JUN), 20.2e9, (None, "period_unverified")),
    (_q(("2026-09-30", -5), _JUN), 20.2e9, (None, "period_unverified")),
    (_q(("2026-09-30", True), _JUN), 20.2e9, (None, "period_unverified")),
    (_q(("2026-09-30", NAN), _JUN), 20.2e9, (None, "period_unverified")),
    (_q(("2026-09-30", 10 ** 400), _JUN), 20.2e9, (None, "period_unverified")),
    (_q(("2026-09-3", 20_186_000_000), _JUN), 20.2e9, (None, "period_unverified")),     # only Jun 30 dated: stale
    (_q((datetime(2026, 9, 30, 0, 0), 20_186_000_000), _JUN), 20.2e9, (None, "period_unverified")),
    (_q((date(2026, 9, 30), 20_186_000_000), (date(2026, 6, 30), 16_650_000_000)), 20.2e9, (date(2026, 9, 30), None)),
    ([], 20.2e9, (None, "period_unverified")), (None, 20.2e9, (None, "period_unverified")),
    ([7, None, "x", {"date": None}], 20.2e9, (None, "period_unverified")),
    # a calendar revenue that is no figure
    (_q(("2026-09-30", 20_186_000_000), _JUN), None, (None, "period_unverified")),
    (_q(("2026-09-30", 20_186_000_000), _JUN), 0.0, (None, "period_unverified")),
    (_q(("2026-09-30", 20_186_000_000), _JUN), NAN, (None, "period_unverified")),
])
def test_earnings_period(rows, actual, out):
    assert R.earnings_period(rows, report_date=_REPORT, revenue_actual=actual) == out


@pytest.mark.parametrize("rows, report, actual, out", [
    # ER-4 (a): a semiannual reporter whose FMP "quarters" are HALF-YEARS: the calendar's H1 revenue matches the
    # H1 row within 3%, and the post would say "the quarter that ended June 30" about six months of figures
    (_q(("2026-06-30", 25.0e9), ("2025-12-31", 26.1e9)), date(2026, 8, 18), 25.2e9, (None, "period_unverified")),
    (_q(("2026-06-30", 25.0e9), ("2025-12-31", 26.1e9), ("2025-06-30", 24.0e9)), date(2026, 8, 18), 25.2e9,
     (None, "period_unverified")),
    # ER-4 (b): a 12-week quarter ending Jun 13, reported Sep 10 — 89 days on, inside the 90-day lag, but its
    # NEXT 12-week quarter (ending Sep 5) had ended by then: the row may be the quarter before the reported one
    (_q(("2026-06-13", 10.0e9), ("2026-03-21", 9.9e9)), date(2026, 9, 10), 10.1e9, (None, "period_unverified")),
    # ... the same company reporting that quarter promptly is placed
    (_q(("2026-06-13", 10.0e9), ("2026-03-21", 9.9e9)), date(2026, 6, 25), 10.1e9, (date(2026, 6, 13), None)),
    # a report exactly one span after its row (lag == span) is refused; one day less is kept
    (_q(("2026-07-11", 20.2e9), ("2026-04-12", 19.0e9)), date(2026, 10, 9), 20.2e9, (None, "period_unverified")),
    (_q(("2026-07-11", 20.2e9), ("2026-04-11", 19.0e9)), date(2026, 10, 9), 20.2e9, (date(2026, 7, 11), None)),
    # a single row: no older period, so nothing shows it is a quarter
    (_q(("2026-09-30", 20_186_000_000)), date(2026, 10, 9), 20.2e9, (None, "period_unverified")),
    # two rows on ONE date and nothing older: still a single period
    (_q(("2026-09-30", 20_186_000_000), ("2026-09-30", 20_190_000_000)), date(2026, 10, 9), 20.2e9,
     (None, "period_unverified")),
    # the span's edges, inclusive: 77 and 119 days are quarters; 76 and 120 are not
    (_q(("2026-09-30", 20.2e9), ("2026-07-15", 19.0e9)), date(2026, 10, 9), 20.2e9, (date(2026, 9, 30), None)),
    (_q(("2026-09-30", 20.2e9), ("2026-07-16", 19.0e9)), date(2026, 10, 9), 20.2e9, (None, "period_unverified")),
    (_q(("2026-09-30", 20.2e9), ("2026-06-03", 19.0e9)), date(2026, 10, 9), 20.2e9, (date(2026, 9, 30), None)),
    (_q(("2026-09-30", 20.2e9), ("2026-06-02", 19.0e9)), date(2026, 10, 9), 20.2e9, (None, "period_unverified")),
    # a 16-week first quarter (Kroger-like, 112 days) reported 27 days on is a quarter
    (_q(("2026-05-24", 45.0e9), ("2026-02-01", 34.0e9)), date(2026, 6, 20), 45.1e9, (date(2026, 5, 24), None)),
    # the next-older row may sit OUTSIDE the 120-day window (it only shows the span); an unreadable one is no row
    (_q(("2026-07-11", 20.2e9), ("2026-04-11", 19.0e9)), date(2026, 10, 9), 20.2e9, (date(2026, 7, 11), None)),
    (_q(("2026-09-30", 20.2e9), ("2026-06-3x", 19.0e9)), date(2026, 10, 9), 20.2e9, (None, "period_unverified")),
    (_q(("2026-09-30", 20.2e9)) + [{"date": None, "revenue": 1.0}, 7], date(2026, 10, 9), 20.2e9,
     (None, "period_unverified")),
], ids=["half_year", "half_years", "twelve_week_lag", "twelve_week_prompt", "lag_equals_span", "lag_under_span",
        "single_row", "one_date_twice", "span_77", "span_76", "span_119", "span_120", "sixteen_weeks",
        "older_outside_window", "older_unreadable", "older_junk"])
def test_the_placed_row_must_be_a_quarter(rows, report, actual, out):
    """Review of 2026-10-16 (ER-4 / money_map_copy ER-1): `earnings_period` placed the NEWEST row in the window
    without asking whether it covered a quarter — a half-year row, or a row a whole quarter older than the report,
    became "the quarter that ended …". The row must sit `R.EARNINGS_QUARTER_SPAN_DAYS` after the company's
    previous period end, and the report must come less than that span after it. Mutation-checked by hand
    (2026-10-16): dropping the span bounds turned half_year / half_years / span_76 / span_120 red, dropping the
    lag-vs-span clause turned twelve_week_lag / lag_equals_span red, and a missing older row read as a quarter
    turned single_row / one_date_twice / older_unreadable / older_junk red."""
    assert R.EARNINGS_QUARTER_SPAN_DAYS == (77, 119)
    assert R.earnings_period(rows, report_date=report, revenue_actual=actual) == out


@pytest.mark.parametrize("stale, out", [
    (17_000_000_000, (None, None)),                     # ×1.19 a quarter's scale: kept without a period
    (15_200_000_000, (None, None)), (26_900_000_000, (None, None)),   # ×1.33 and ×0.75, inclusive-ish
    (60_000_000_000, (None, "period_unverified")),      # ×0.34: a month
    (10_000_000_000, (None, "period_unverified")),      # ×2.02: a half-year
])
def test_the_scale_check_contingency_never_sets_a_period(monkeypatch, stale, out):
    # the quarter before (the statement has not caught up), with ITS quarter before: a quarter-shaped row
    rows = _q(("2026-06-30", stale), ("2026-03-31", 15_000_000_000))
    assert R.earnings_period(rows, report_date=_REPORT, revenue_actual=20.2e9) == (None, "period_unverified")
    monkeypatch.setattr(R, "EARNINGS_ALLOW_SCALE_CHECK", True)
    assert R.earnings_period(rows, report_date=_REPORT, revenue_actual=20.2e9) == out
    # review of 2026-10-16: under the contingency too, a row that is not quarter-shaped (a single row, a
    # half-year span) is never "a quarter's scale"
    for shapeless in (_q(("2026-06-30", stale)), _q(("2026-06-30", stale), ("2025-12-31", 15_000_000_000))):
        assert R.earnings_period(shapeless, report_date=_REPORT, revenue_actual=20.2e9) == (None, "period_unverified")
    # a reported quarter (inside the lag) that disagrees is still a mismatch under the contingency
    assert R.earnings_period(_q(("2026-09-30", stale), _JUN), report_date=_REPORT, revenue_actual=20.2e9) == (
        None, "period_mismatch")


@pytest.mark.parametrize("bad", [datetime(2026, 10, 9, 9, 0), "2026-10-09", None])
def test_earnings_period_needs_a_report_date(bad):
    with pytest.raises(ValueError):
        R.earnings_period([], report_date=bad, revenue_actual=1.0)


# ── review of 2026-10-16: the EPS basis (live preview F1) ─────────────────────


@pytest.mark.parametrize("profile, problem", [
    ({"sector": "Real Estate", "industry": "REIT - Industrial"}, "eps_basis_reit"),      # Prologis
    ({"sector": " Real Estate ", "industry": "Real Estate - Services"}, "eps_basis_reit"),  # CBRE: over-block
    ({"sector": "Financial Services", "industry": "REIT - Mortgage"}, "eps_basis_reit"),   # a mortgage REIT
    ({"sector": "Financial Services", "industry": "reit - specialty"}, "eps_basis_reit"),  # any case
    # an unknown sector cannot be shown not to be a REIT (fail closed, as the Money Map sector gate)
    ({"sector": None, "industry": "Software"}, "eps_basis_reit"), ({"sector": "", "industry": "x"}, "eps_basis_reit"),
    ({"sector": "  ", "industry": "x"}, "eps_basis_reit"), ({"sector": 7}, "eps_basis_reit"), ({}, "eps_basis_reit"),
    ({"sector": "x" * 201}, "eps_basis_reit"), (None, "eps_basis_reit"), ("Real Estate", "eps_basis_reit"),
    # kept: an ordinary sector; a missing or junk industry alone decides nothing; "Reinsurance" is no REIT
    ({"sector": "Technology", "industry": "Software - Infrastructure"}, None),
    ({"sector": "Financial Services", "industry": "Asset Management"}, None),               # BlackRock
    ({"sector": "Healthcare", "industry": None}, None), ({"sector": "Industrials", "industry": 5}, None),
    ({"sector": "Financial Services", "industry": "Insurance - Reinsurance"}, None),
    ({"sector": "Financial Services", "industry": "Reinsurance"}, None),
])
def test_earnings_sector_problem(profile, problem):
    """Mutation-checked by hand (2026-10-16): without the industry prefix the mortgage-REIT rows went green as
    kept (red here); answering None for an unreadable sector turned the fail-closed rows red."""
    assert R.earnings_sector_problem(profile) == problem
    assert R.EARNINGS_EXCLUDED_SECTORS == {"Real Estate"} and R.EARNINGS_EXCLUDED_INDUSTRY_PREFIX == "REIT"


def _stmt(day="2026-09-30", **eps):
    return {"date": day, "revenue": 2_387_518_000, "reportedCurrency": "USD", **eps}


@pytest.mark.parametrize("rows, actual, estimate, mixed", [
    # Prologis (live, 2026-10-15 calendar): actual 1.60 (Core-FFO scale, 1.86x GAAP), estimate 0.777 (0.90x)
    ([_stmt(epsDiluted=0.86, eps=0.86)], 1.60, 0.777, True),
    # BlackRock: 15.93 (1.507x GAAP 10.57) beside 14.32 (1.35x): the estimate is off the GAAP scale too — kept
    ([_stmt(epsDiluted=10.57)], 15.93, 14.32, False),
    # the held records of the live preview: actual within [0.67, 1.5] of GAAP — kept whatever the estimate
    ([_stmt(epsDiluted=4.56)], 4.69, 4.40, False),                          # UNH
    ([_stmt(epsDiluted=2.85)], 2.85, 2.60, False),                          # C
    ([_stmt(epsDiluted=1.81)], 1.82, 1.55, False),                          # AA
    ([_stmt(epsDiluted=9.04)], 9.18, 8.10, False),                          # TRV
    ([_stmt(epsDiluted=1.91)], 2.09, 1.80, False),                          # CMC
    ([_stmt(epsDiluted=5.67)], 4.85, 4.40, False),                          # a PGR-like adjusted actual (0.855x)
    # the band's edges, inclusive (exact on the figures): 0.67x and 1.5x are inside; just past them is not
    ([_stmt(epsDiluted=1.00)], 1.50, 1.00, False), ([_stmt(epsDiluted=1.00)], 1.5001, 1.00, True),
    ([_stmt(epsDiluted=1.00)], 0.67, 1.00, False), ([_stmt(epsDiluted=1.00)], 0.6699, 1.00, True),
    # the estimate's tolerance, inclusive: ±25% of GAAP is "on its scale"; past it the gate does not apply
    ([_stmt(epsDiluted=1.00)], 2.00, 1.25, True), ([_stmt(epsDiluted=1.00)], 2.00, 1.2501, False),
    ([_stmt(epsDiluted=1.00)], 2.00, 0.75, True), ([_stmt(epsDiluted=1.00)], 2.00, 0.7499, False),
    ([_stmt(epsDiluted=1.00)], -0.50, 0.95, True),                          # a negative actual, GAAP-scale estimate
    # never when the statement EPS is missing, not a number or not positive (a loss quarter): no basis to judge
    ([_stmt()], 1.60, 0.777, False), ([_stmt(epsDiluted=None, eps=None)], 1.60, 0.777, False),
    ([_stmt(epsDiluted="0.86")], 1.60, 0.777, False), ([_stmt(epsDiluted=0.0, eps=0.86)], 1.60, 0.777, False),
    ([_stmt(epsDiluted=-0.86)], 1.60, -0.777, False), ([_stmt(epsDiluted=NAN)], 1.60, 0.777, False),
    ([_stmt(epsDiluted=True)], 1.60, 0.777, False), ([_stmt(epsDiluted=10 ** 400)], 1.60, 0.777, False),
    # `eps` stands in only when `epsDiluted` is absent or not a number (presence, not truthiness)
    ([_stmt(eps=0.86)], 1.60, 0.777, True), ([_stmt(epsDiluted="n/a", eps=0.86)], 1.60, 0.777, True),
    # only the PLACED quarter's row(s) count — an older row, another date, a row without a date is not read
    ([_stmt(day="2026-06-30", epsDiluted=0.86), _stmt(epsDiluted=1.55)], 1.60, 0.777, False),
    ([_stmt(day="2026-06-30", epsDiluted=0.86)], 1.60, 0.777, False),
    ([{"revenue": 1.0, "epsDiluted": 0.86}, 7, None], 1.60, 0.777, False),
    # two rows on the placed date: any one that shows the mix refuses (fail closed)
    ([_stmt(epsDiluted=1.55), _stmt(epsDiluted=0.86)], 1.60, 0.777, True),
    # junk around the figures
    ([], 1.60, 0.777, False), (None, 1.60, 0.777, False), ([_stmt(epsDiluted=0.86)], None, 0.777, False),
    ([_stmt(epsDiluted=0.86)], 1.60, NAN, False), ([_stmt(epsDiluted=0.86)], True, 0.777, False),
], ids=["prologis", "blackrock", "unh", "c", "aa", "trv", "cmc", "pgr_like", "band_hi_edge", "band_hi_past",
        "band_lo_edge", "band_lo_past", "tol_hi_edge", "tol_hi_past", "tol_lo_edge", "tol_lo_past", "negative_actual",
        "no_eps", "none_eps", "string_eps", "zero_diluted", "loss_quarter", "nan_eps", "bool_eps", "huge_eps",
        "basic_eps", "basic_eps_fallback", "another_quarter", "not_the_placed_quarter", "undated", "two_rows",
        "no_rows", "rows_none", "no_actual", "nan_estimate", "bool_actual"])
def test_earnings_basis_mixed(rows, actual, estimate, mixed):
    """Mutation-checked by hand (2026-10-16): without the estimate clause BlackRock (and every pgr-like / tol_*_past
    row) turned red; with the band check removed UNH..CMC turned red; reading `eps` before `epsDiluted` turned
    zero_diluted red."""
    assert R.EARNINGS_BASIS_ACTUAL_BAND == (0.67, 1.5) and R.EARNINGS_BASIS_ESTIMATE_TOLERANCE == 0.25
    assert R.earnings_basis_mixed(rows, period_end=date(2026, 9, 30), actual=actual, estimate=estimate) is mixed


def test_the_basis_check_needs_a_period_date():
    rows = [_stmt(epsDiluted=0.86)]
    for bad in ("2026-09-30", datetime(2026, 9, 30), None):
        assert R.earnings_basis_mixed(rows, period_end=bad, actual=1.60, estimate=0.777) is False


# ── review of 2026-10-16: NASDAQ preferred lines (live preview F3) ────────────


def test_the_nasdaq_preferred_shape_is_the_search_services_own():
    """Parity: the rule is COPIED from `stock_search_service._NASDAQ_PREFERRED_RE` (that module loads the FMP
    client; the rules stay pure) — the two patterns are one."""
    from app.services import stock_search_service

    assert R.NASDAQ_PREFERRED_SYMBOL_RE.pattern == stock_search_service._NASDAQ_PREFERRED_RE.pattern
    assert R.NASDAQ_PREFERRED_SYMBOL_RE.flags == stock_search_service._NASDAQ_PREFERRED_RE.flags


@pytest.mark.parametrize("sym, preferred", [
    ("WAFDP", True), ("HBANP", True), ("HBANM", True), ("AGNCO", True), ("AGNCN", True), ("ACGLO", True),
    ("SLMBP", True), ("FITBO", True), ("NTRSO", True),
    # four letters (SNAP, RAMP, NTBP), other fifth letters (a class, GOOGL's L, FITBI's I), dashes: not this rule
    ("SNAP", False), ("RAMP", False), ("NTBP", False), ("GOOGL", False), ("FITBI", False), ("HBANL", False),
    ("LBRDA", False), ("FWONK", False), ("BRK-B", False), ("WAFD", False), ("P", False), ("ABCDEP", False),
    ("wafdp", False), (None, False), (5, False),
])
def test_nasdaq_preferred_symbol(sym, preferred):
    assert R.nasdaq_preferred_symbol(sym) is preferred


@pytest.mark.parametrize("purpose, refused", [("earnings", True), ("insider", True), ("common", True),
                                              ("listing", False)])
def test_a_plain_named_nasdaq_preferred_line_is_never_a_common_stock(purpose, refused):
    """WaFd's preferred line WAFDP (live, 2026-10-15): FMP profiles it as "WaFd, Inc.", NASDAQ, no ADR flag, a
    $2.19B cap — the name gate passed it as the company, and its calendar row carried 0.76 against the common's
    0.85. The earnings, insider and common purposes refuse it (`non_common_listing`); "listing" (a 13F holding,
    a theme member) is named by its listing and keeps it. Mutation-checked by hand (2026-10-16): without the
    symbol clause in `company_from_profile` every refused row turned red."""
    profile = dict(symbol="WAFDP", companyName="WaFd, Inc.", exchange="NASDAQ", isEtf=False, isFund=False,
                   isActivelyTrading=True, isAdr=False, currency="USD", marketCap=2.188e9 * 2, price=20.0)
    got = R.company_from_profile(profile, "WAFDP", purpose=purpose)
    assert (got == "non_common_listing") is refused
    if not refused:
        assert got == R.CompanyRef(symbol="WAFDP", name="WaFd")
    # the common itself, and a 5-letter class share, pass every purpose
    for sym in ("WAFD", "GOOGL", "LBRDK"):
        assert isinstance(R.company_from_profile(dict(profile, symbol=sym), sym, purpose=purpose), R.CompanyRef)


def test_a_figure_too_large_for_a_float_is_no_number_and_never_raises():
    assert R._is_num(10 ** 400) is False and R._is_num(10 ** 15) is True
    with pytest.raises(ValueError):
        earnings(revenue_actual=10 ** 400)


@pytest.mark.parametrize("actual, estimate, valid", [
    (1_000_000.0, 900_000.0, True),             # exactly the ceiling: kept
    (-1_000_000.0, -900_000.0, True),
    (7_089.43, 8_259.23, True),                 # Berkshire Hathaway Class A, 2026-03-02 (live): far inside it
    (20_000.0, 18_000.0, True),
    (1_000_000.01, 900_000.0, False),           # a cent above it
    (900_000.0, 1_000_000.5, False),            # the estimate above it
    (-1_500_000.0, -1_200_000.0, False),        # the magnitude, whatever the sign
    (1e20, 1.1e20, False), (1e300, 1e299, False),
], ids=["ceiling", "neg_ceiling", "brk_a", "20k", "cent_above", "estimate_above", "negative", "1e20", "1e300"])
def test_the_eps_ceiling_holds_the_record_too(actual, estimate, valid):
    """The owner-delegated ceiling (2026-10-19): a per-share figure above $1,000,000 is a feed error. The record
    refuses it too, so a stored fact sheet is held to the rule — never only the adapter's row gate.
    Mutation-checked by hand (2026-10-19): the record loop removed turns every refused row red."""
    assert R.EPS_MAX_ABS == 1_000_000.0 and "eps_implausible" in R.REJECTION_REASONS
    if valid:
        assert earnings(eps_actual=actual, eps_estimate=estimate).eps_actual == actual
    else:
        with pytest.raises(ValueError, match="above 1,000,000 a share"):
            earnings(eps_actual=actual, eps_estimate=estimate)


# ── Batch 1 (2026-10-15, plan item 5): a descriptive stake name takes "the" ─────────────────────────


@pytest.mark.parametrize("name, article", [
    ("Louisiana data center venture", True),               # the catalogue's Meta row
    ("Stateline Power joint venture", True), ("Meta data center campus", True), ("Texas solar farms", True),
    ("Data center venture", True),
    # a company's own name: capitalised, or styled in lower case on purpose — never an article
    ("Louisiana Data Center Venture", False), ("Iroquois Gas Transmission System", False), ("SK hynix", False),
    ("bluebird bio", False), ("Mitsui & Co.", False), ("Nscale", False), ("Anthropic", False),
    ("Berkadia Commercial Mortgage", False), ("Globalstar Licensee LLC", False), ("ONE store", False),
    # one word, already opened by "the", a hyphen, a digit, punctuation: none is a head noun here
    ("venture", False), ("The Louisiana data center venture", False), ("the Louisiana venture", False),
    ("Louisiana data-center", False), ("Project venture2", False), ("Louisiana venture.", False),
    # not a name at all, or too long to be one
    (None, False), ("", False), ("   ", False), (5, False), (["a venture"], False), ("a " * 150 + "venture", False),
])
def test_takes_name_article(name, article):
    assert R.takes_name_article(name) is article


def test_only_the_descriptive_catalogue_name_takes_the_article():
    """Over-block check against the real catalogue (`data/trillion_club_seed.json`): of every investee name
    the stake records may carry, exactly the descriptive one takes "the" — no company name does. (The head-noun
    list is what keeps a lower-case brand out: mutation-checked by hand, 2026-10-15 — answering True for ANY
    lower-case last word turned the "SK hynix", "bluebird bio" and "ONE store" rows of `test_takes_name_article`
    red.)"""
    seed = json.loads((Path(__file__).resolve().parents[1] / "data" / "trillion_club_seed.json")
                      .read_text(encoding="utf-8"))
    names = {s["investee_name"] for s in seed["stakes"]}
    assert len(names) >= 100
    assert {n for n in names if R.takes_name_article(n)} == {"Louisiana data center venture"}
    assert all(h == h.lower() and h.isalpha() for h in R.DESCRIPTIVE_NAME_HEADS)
