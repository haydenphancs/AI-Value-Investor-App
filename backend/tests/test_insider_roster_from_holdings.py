"""ONE insider roster for the Holders tab, the report and Ask Cay AI (2026-10-08).

The Holders tab's "Top 10 Insiders" sheet and the report's Key Management used the FIRST raw
Form 4 row per person (`FMPClient.get_insider_roster`). For CRWV's Brian Venturo that row was
a Restricted Stock Units line — 984,380 units — while the chat ownership tool said 302,526
Class A shares held directly as of 2026-09-30. `_insider_holdings.roster_from_holdings` now
feeds both builders from the chained balances chat already states:

  * Venturo's rows in EVERY line order → 302,526; never 984,380 (the RSU line) or 368,142
    (the first Class A line of the day);
  * a derivative-only filer, or an indirect-only holder, is absent; an ambiguous day is left
    out of a RANKED list (and unknown — never 0 — where Key Management lists officers);
  * holdings unavailable → an EMPTY sheet, never the raw roster;
  * the iOS-decoded fields stay non-optional floats; the payload version is 6.

Hermetic: fake FMP / Supabase; no network.
"""

from __future__ import annotations

import ast
import inspect
import itertools
import json
import random
import textwrap

import pytest

from app.schemas.holders import InsiderHoldingsSchema, TopInsiderSchema
from app.services import holders_service as hs
from app.services._insider_holdings import (
    ROSTER_BASIS,
    insider_holdings_from_rows,
    roster_from_holdings,
)
from app.services.holders_service import HoldersService

CRWV_CIK = "0001769628"
VENTURO = ("Venturo Brian M", "0002058067", "director, officer: Chief Strategy Officer")
JAIN = ("Jain Sachin", "0002124855", "officer: Chief Operating Officer")
TRUSTEE = ("Trust Holder Jane", "0002999999", "10 percent owner")


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


def _sep30():
    """The 2026-10-02 filing as the feed lists it — NOT in the order it happened."""
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


def _sep18():
    return [
        _row(VENTURO, "2026-09-18", "2026-09-22", tx="C-Conversion", ad="D", shares=62500,
             owned=1515849, own="I", security="Class B Common Stock"),
        _row(VENTURO, "2026-09-18", "2026-09-22", tx="C-Conversion", ad="A", shares=62500, owned=303871),
        _row(VENTURO, "2026-09-18", "2026-09-22", tx="G-Gift", ad="D", shares=62500, owned=241371),
    ]


def _venturo(roster):
    rows = [r for r in roster if "Venturo" in r["display_name"]]
    assert len(rows) == 1, roster
    return rows[0]


def _schema(built):
    built = dict(built)
    built.pop("rows_skipped", None)
    return InsiderHoldingsSchema(**built)


# ── the TestFlight number, whatever the line order ──────────────────────────────────

@pytest.mark.parametrize("prefix", ["none", "before", "after"])
def test_venturo_is_302526_in_every_order_of_his_filing_lines(prefix):
    for perm in itertools.permutations(_sep30()):
        rows = list(perm)
        if prefix == "before":
            rows = _sep18() + rows
        elif prefix == "after":
            rows = rows + _sep18()
        for holdings in (insider_holdings_from_rows(rows),):
            entry = _venturo(roster_from_holdings(holdings))
            assert entry["numberOfShares"] == 302526, [r["securitiesOwned"] for r in rows]
            assert entry["numberOfShares"] not in (984380, 368142, 350751, 173913)
            assert entry["as_of"] == "2026-09-30"
            assert entry["security"] == "Class A Common Stock"


def test_venturo_is_302526_in_random_orders_of_all_his_rows_and_from_the_schema_too():
    rng = random.Random(20261008)
    rows = _sep18() + _sep30()
    for _ in range(300):
        rng.shuffle(rows)
        built = insider_holdings_from_rows(list(rows))
        for holdings in (built, _schema(built)):
            entry = _venturo(roster_from_holdings(holdings))
            assert entry["numberOfShares"] == 302526


def test_a_roster_row_carries_the_display_name_role_and_basis():
    entry = _venturo(roster_from_holdings(insider_holdings_from_rows(_sep18() + _sep30())))
    assert entry["display_name"] == entry["owner"] == "Brian M. Venturo"
    # The old roster's shape: the filing's raw role (its "officer:" tag ranks officers in Key
    # Management); the cleaned one beside it.
    assert entry["title"] == entry["typeOfOwner"] == "director, officer: Chief Strategy Officer"
    assert entry["role"] == "director, Chief Strategy Officer"
    assert entry["basis"] == ROSTER_BASIS and "directly" in ROSTER_BASIS


def test_a_holdings_row_stored_without_the_raw_role_falls_back_to_the_cleaned_one():
    built = insider_holdings_from_rows(_sep30())
    for person in built["insiders"]:
        person.pop("type_of_owner")
    entry = _venturo(roster_from_holdings(_schema(built)))
    assert entry["title"] == entry["typeOfOwner"] == "director, Chief Strategy Officer"


def test_key_management_still_ranks_an_officer_by_the_filings_officer_tag():
    """`_role_rank` puts a rank-and-file officer ("officer: VP Sales", 10) above a holder
    with no role it knows (99) — from the RAW role string the roster carries."""
    from app.services.agents.ticker_report_data_collector import _build_key_management

    vp = ("Doe Jane", "0002000001", "officer: VP Sales")
    holder = ("Holder Bob", "0002000002", "10 percent owner")
    rows = [_row(vp, "2026-09-01", "2026-09-02", owned=1_000, tx="P-Purchase", ad="A", shares=10),
            _row(holder, "2026-09-01", "2026-09-02", owned=9_000_000, tx="P-Purchase", ad="A",
                 shares=10)]
    km = _build_key_management(roster_from_holdings(insider_holdings_from_rows(rows),
                                                    include_unknown=True), {})
    assert [o["name"] for o in km["officers"]] == ["Jane Doe", "Bob Holder"]
    assert km["officers"][0]["title"] == "VP Sales"


# ── who is not on it ──────────────────────────────────────────────────────────────────

def test_a_derivative_only_filer_and_an_indirect_only_holder_are_absent():
    rsu_only = [_row(JAIN, "2026-09-30", "2026-10-01", tx="M-Exempt", ad="D", shares=10,
                     owned=5000, security="Restricted Stock Units")]
    trust_only = [_row(TRUSTEE, "2026-09-29", "2026-09-30", tx="G-Gift", ad="D", shares=10,
                       owned=9_000_000, own="I", security="Class B Common Stock")]
    roster = roster_from_holdings(insider_holdings_from_rows(rsu_only + trust_only + _sep30()))
    assert [r["display_name"] for r in roster] == ["Brian M. Venturo"]
    assert all(r["numberOfShares"] != 9_000_000 for r in roster), "indirect never ranks a person"


def test_an_ambiguous_day_is_left_out_of_a_ranked_list_and_unknown_where_listed():
    """09-18 alone is a same-day cycle (241,371 or 303,871): never guessed."""
    alone = [r for r in _sep18() if r["directOrIndirect"] == "D"]
    holdings = insider_holdings_from_rows(alone)
    assert roster_from_holdings(holdings) == []
    listed = roster_from_holdings(holdings, include_unknown=True)
    assert len(listed) == 1 and listed[0]["numberOfShares"] is None


def test_a_holding_sold_to_zero_is_a_reported_zero_not_unknown():
    rows = [_row(JAIN, "2026-08-20", "2026-08-21", shares=500, owned=0, price=10.0)]
    entry = roster_from_holdings(insider_holdings_from_rows(rows))
    assert [e["numberOfShares"] for e in entry] == [0.0]


def test_two_direct_classes_are_never_added_the_newest_wins():
    rows = [
        _row(JAIN, "2026-06-01", "2026-06-02", shares=10, owned=1_000_000,
             security="Class B Common Stock", tx="P-Purchase", ad="A"),
        _row(JAIN, "2026-09-01", "2026-09-02", shares=10, owned=5_000,
             security="Class A Common Stock", tx="P-Purchase", ad="A"),
    ]
    entry = roster_from_holdings(insider_holdings_from_rows(rows))[0]
    assert entry["numberOfShares"] == 5_000 and entry["security"] == "Class A Common Stock"
    assert entry["numberOfShares"] != 1_005_000


@pytest.mark.parametrize("bad", [
    None, [], {}, "x", 5, {"insiders": "x"}, {"insiders": [None, 3, "a", {}]},
    {"insiders": [{"name": "", "holdings": []}, {"name": "A", "holdings": "x"}]},
    {"insiders": [{"name": "A", "holdings": [{"held": "direct", "shares": float("nan")}]}]},
    {"insiders": [{"name": "A", "holdings": [{"held": "direct", "shares": True}]}]},
    {"insiders": [{"name": "A", "holdings": [{"held": "direct", "shares": -5}]}]},
])
def test_malformed_input_never_raises_and_never_invents_a_number(bad):
    roster = roster_from_holdings(bad)
    assert isinstance(roster, list)
    assert all(r["numberOfShares"] is None or r["numberOfShares"] >= 0 for r in roster)
    assert roster == []
    assert all(r["numberOfShares"] is None for r in roster_from_holdings(bad, include_unknown=True))


# ── the Holders tab's Top 10 sheet ────────────────────────────────────────────────────

def _svc() -> HoldersService:
    return object.__new__(HoldersService)


def test_the_top_10_sheet_reads_the_roster_and_keeps_the_display_name():
    roster = roster_from_holdings(insider_holdings_from_rows(_sep18() + _sep30()))
    out = HoldersService._build_top_insiders(_svc(), roster, current_price=87.0,
                                             outstanding_shares=4.0e8)
    assert [o.name for o in out] == ["Brian M. Venturo"], "never normalised twice ('M. Venturo Brian')"
    assert out[0].value_in_millions == round(302526 * 87.0 / 1e6, 1)
    assert out[0].percent_ownership == round(302526 / 4.0e8 * 100, 4)
    assert isinstance(out[0].value_in_millions, float) and isinstance(out[0].percent_ownership, float)


@pytest.mark.parametrize("shares", [None, float("nan"), True, -10, 0, "abc"])
def test_the_top_10_sheet_never_lists_an_unknown_or_empty_holding(shares):
    roster = [{"owner": "A B", "display_name": "B A", "title": "director", "numberOfShares": shares},
              {"owner": "C D", "display_name": "D C", "title": "CFO", "numberOfShares": 1000}]
    out = HoldersService._build_top_insiders(_svc(), roster, current_price=10.0, outstanding_shares=1e6)
    assert [o.name for o in out] == ["D C"]


def test_the_top_10_sheet_still_ranks_raw_rows_by_shares():
    roster = [{"owner": "JUNIOR, ANALYST", "numberOfShares": 1_000},
              {"owner": "ELLISON LAWRENCE J", "numberOfShares": 1_100_000_000}]
    out = HoldersService._build_top_insiders(_svc(), roster, current_price=0.0)
    assert out[0].name.endswith("Ellison")


# ── the real build: the RSU line never reaches the sheet ──────────────────────────────

class _FMP:
    def __init__(self, insider, *, summary=None, profile=None, roster=None):
        self.insider, self.summary, self.profile = insider, summary or {}, profile
        self.roster = roster if roster is not None else []

    async def get_shares_float(self, t):
        return {"freeFloat": 60.0, "outstandingShares": 4.0e8, "floatShares": 2.4e8,
                "date": "2026-10-07"}
    async def get_institutional_holder(self, t, limit=20): return []
    async def get_institutional_ownership_summary(self, t): return self.summary
    async def get_institutional_ownership_for_quarter(self, t, y, q, strict=False): return None
    async def get_insider_trades_since(self, since, *, symbol=None, page_size=1000,
                                       max_pages=5, transaction_type=None):
        return self.insider
    async def get_company_profile(self, t):
        if isinstance(self.profile, BaseException):
            raise self.profile
        return self.profile or {}
    async def get_insider_roster(self, t): return self.roster
    async def get_historical_prices(self, t, from_date=None, to_date=None): return []
    async def get_senate_latest(self, limit=1000): return []
    async def get_house_latest(self, limit=1000): return []
    async def get_senate_disclosure(self, t): return []
    async def get_house_disclosure(self, t): return []
    async def get_stock_price_quote(self, t): return {"price": 87.0}


class _CA:
    async def get_split_rows(self, *a, **k): return []
    async def has_unclassified_adjustment(self, *a, **k): return False


class _Supabase:
    def __init__(self):
        self.upserts = []

    def table(self, name): return self
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def upsert(self, payload, **k):
        self.upserts.append(payload)
        return self

    def execute(self):
        class _R:
            data = []
        return _R()


@pytest.fixture(autouse=True)
def _fresh_tiers(monkeypatch):
    monkeypatch.setattr(hs, "_cache", {})
    monkeypatch.setattr(hs, "_inflight", {})
    monkeypatch.setattr(hs, "_background_tasks", set())


def _wired(fmp):
    from tests._price_fakes import PriceFromFMPFake
    svc = object.__new__(HoldersService)
    svc.fmp, svc.price, svc.corporate_actions = fmp, PriceFromFMPFake(fmp), _CA()
    svc.supabase = _Supabase()
    return svc


# The old roster's first row for Venturo: the RSU line.
_RAW_ROSTER = [{"owner": "Venturo Brian M", "title": "director", "numberOfShares": 984380,
                "companyCik": CRWV_CIK}]


@pytest.mark.asyncio
async def test_the_build_ranks_venturo_at_302526_never_the_rsu_line():
    fmp = _FMP(_sep18() + _sep30(), summary={"cik": CRWV_CIK}, roster=_RAW_ROSTER)
    resp, degraded = await _wired(fmp).get_holders_with_status("CRWV")
    assert degraded == []
    top = resp.shareholder_breakdown.top_10_owners.insiders
    assert [t.name for t in top] == ["Brian M. Venturo"]
    assert top[0].value_in_millions == round(302526 * 87.0 / 1e6, 1)
    assert top[0].value_in_millions != round(984380 * 87.0 / 1e6, 1)
    assert top[0].percent_ownership == round(302526 / 4.0e8 * 100, 4)
    # the wire shape iOS decodes: every field present, non-optional, the same type
    body = json.loads(resp.model_dump_json())
    row = body["shareholder_breakdown"]["top_10_owners"]["insiders"][0]
    assert set(row) == set(TopInsiderSchema.model_fields)
    assert isinstance(row["value_in_millions"], float) and isinstance(row["percent_ownership"], float)
    # one float source carried for chat
    detail = resp.ownership_detail
    assert (detail.float_shares, detail.outstanding_shares, detail.free_float_percent,
            detail.float_as_of) == (2.4e8, 4.0e8, 60.0, "2026-10-07")


@pytest.mark.asyncio
async def test_holdings_unavailable_gives_an_empty_sheet_never_the_raw_roster():
    fmp = _FMP(_sep18() + _sep30(), summary={}, profile=RuntimeError("profile 503"),
               roster=_RAW_ROSTER)
    resp, degraded = await _wired(fmp).get_holders_with_status("CRWV")
    assert "Issuer CIK" in degraded and resp.ownership_detail.insider_holdings is None
    assert resp.shareholder_breakdown.top_10_owners.insiders == []


def test_the_payload_version_is_6():
    assert hs._HOLDERS_PAYLOAD_VERSION == 6


# ── source guards: neither builder reads the raw roster for holdings ──────────────────

def _function_node(module, name):
    tree = ast.parse(textwrap.dedent(inspect.getsource(module)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"guard is stale — {name} moved")


def _calls(node, callee):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if fname == callee:
                out.append(n)
    return out


def _names_in(call):
    return {n.id for arg in list(call.args) + [k.value for k in call.keywords]
            for n in ast.walk(arg) if isinstance(n, ast.Name)} | {
        n.attr for arg in list(call.args) + [k.value for k in call.keywords]
        for n in ast.walk(arg) if isinstance(n, ast.Attribute)}


def test_the_holders_breakdown_is_fed_from_the_holdings_roster():
    build = _function_node(hs, "_build_holders")
    calls = _calls(build, "_build_shareholder_breakdown")
    assert len(calls) == 1, "anti-vacuity: the breakdown is built once in the build"
    names = _names_in(calls[0])
    assert "insider_roster" not in names and "_issuer_roster" not in names, names
    assert "top_insiders" in names
    roster_calls = _calls(_function_node(hs, "_top_insider_roster"), "roster_from_holdings")
    assert roster_calls, "the Top 10 roster derives from the chained holdings"


def test_the_report_key_management_is_fed_from_the_holdings_roster():
    from app.services.agents import ticker_report_data_collector as col
    sections = _function_node(col, "_build_sections")
    calls = _calls(sections, "_build_key_management")
    assert len(calls) == 1, "anti-vacuity"
    first = calls[0].args[0]
    assert isinstance(first, ast.Call) and getattr(first.func, "id", None) == "_key_management_roster"
    names = _names_in(calls[0])
    assert "issuer_roster" not in names and "insider_roster" not in names, names
    helper = _function_node(col, "_key_management_roster")
    assert _calls(helper, "roster_from_holdings") and _calls(helper, "insider_holdings_from_rows")
    assert not _calls(helper, "issuer_roster")


@pytest.mark.parametrize("lot_day", ["2026-09-25", "2026-10-01"])
def test_a_separately_reported_lot_never_replaces_the_traded_holding(lot_day):
    """A Form 4 'Common Stock … D' line with no transaction is a LOT listed beside the traded
    holding (`_direct_entries`); older or newer, it is never the person's ranked number."""
    rows = _sep30() + [_row(VENTURO, lot_day, lot_day, tx="", ad="", shares=0, owned=5000)]
    holdings = insider_holdings_from_rows(rows)
    direct = [h for h in holdings["insiders"][0]["holdings"] if h["held"] == "direct"]
    assert sorted(h["shares"] for h in direct) == [5000, 302526], "anti-vacuity: two direct entries"
    assert _venturo(roster_from_holdings(holdings))["numberOfShares"] == 302526


# ── review fixes (2026-10-08): a possibly-stale balance, indirect-only and unlisted filers ──

CFO = ("Roe Jane", "0002000003", "officer: Chief Financial Officer")
CEO = ("Doe John", "0002000004", "director, officer: Chief Executive Officer")


def _stale_cfo_rows():
    """50,000 after a 09-10 sale, then a 40,000-share sale on 09-20 that printed NO balance."""
    return [_row(CFO, "2026-09-10", "2026-09-11", shares=1000, owned=50000, price=10.0),
            _row(CFO, "2026-09-20", "2026-09-22", shares=40000, owned=None, price=10.0)]


def test_a_balance_a_later_no_balance_trade_may_have_changed_is_never_ranked():
    holdings = insider_holdings_from_rows(_stale_cfo_rows())
    entry = holdings["insiders"][0]["holdings"][0]
    assert entry["shares"] == 50000 and entry["changed_after"] == "2026-09-20", "anti-vacuity"
    assert roster_from_holdings(holdings) == [], "the pre-sale 50,000 is not the current holding"
    listed = roster_from_holdings(holdings, include_unknown=True)
    assert [(r["display_name"], r["numberOfShares"]) for r in listed] == [("Jane Roe", None)]
    for schema in (_schema(holdings),):
        assert roster_from_holdings(schema) == []
        assert roster_from_holdings(schema, include_unknown=True)[0]["numberOfShares"] is None


def test_a_later_balance_resolves_the_staleness():
    rows = _stale_cfo_rows() + [_row(CFO, "2026-09-25", "2026-09-26", shares=100, owned=9900,
                                     price=10.0)]
    entry = roster_from_holdings(insider_holdings_from_rows(rows))
    assert [e["numberOfShares"] for e in entry] == [9900.0]


@pytest.mark.parametrize("changed_after,as_of,stale", [
    ("2026-09-20", "2026-09-10", True),
    ("2026-09-10", "2026-09-10", True),       # the same day: the day's lines have no order
    ("2026-09-01", "2026-09-10", False),      # before the balance (never built so): not stale
    (None, "2026-09-10", False), ("", "2026-09-10", False), (20260920, "2026-09-10", False),
    ("2026-09-20", None, True), ("2026-09-20", "", True),
])
def test_possibly_stale_reads_only_a_real_later_date(changed_after, as_of, stale):
    holdings = {"insiders": [{"name": "Jane Roe", "role": "CFO", "holdings": [
        {"held": "direct", "security": "Common Stock", "shares": 50000.0, "as_of": as_of,
         "changed_after": changed_after}]}]}
    ranked = roster_from_holdings(holdings)
    assert (ranked == []) is stale
    assert roster_from_holdings(holdings, include_unknown=True)[0]["numberOfShares"] == (
        None if stale else 50000.0)


def test_an_indirect_only_holder_is_unknown_where_listed_and_absent_where_ranked():
    rows = [_row(CEO, "2026-09-12", "2026-09-13", shares=10, owned=2_000_000, own="I",
                 security="Class A Common Stock"),
            _row(CFO, "2026-09-10", "2026-09-11", shares=1000, owned=50000, price=10.0)]
    holdings = insider_holdings_from_rows(rows)
    assert [r["display_name"] for r in roster_from_holdings(holdings)] == ["Jane Roe"]
    listed = {r["display_name"]: r for r in roster_from_holdings(holdings, include_unknown=True)}
    assert listed["John Doe"]["numberOfShares"] is None, "never the 2,000,000 held indirectly"
    assert listed["John Doe"]["security"] is None and listed["John Doe"]["as_of"] is None
    assert listed["Jane Roe"]["numberOfShares"] == 50000.0


def test_key_management_keeps_an_indirect_only_ceo_first_with_an_unknown_count():
    """The reviewer's scratch case: the CEO holds 2,000,000 only through a trust, the CFO
    50,000 directly. Key Management used to show only the CFO."""
    from app.services.agents.ticker_report_data_collector import _build_key_management

    rows = [_row(CEO, "2026-09-12", "2026-09-13", shares=10, owned=2_000_000, own="I"),
            _row(CFO, "2026-09-10", "2026-09-11", shares=1000, owned=50000, price=10.0)]
    km = _build_key_management(roster_from_holdings(insider_holdings_from_rows(rows),
                                                    include_unknown=True),
                               {"ceo": "John Doe"}, current_price=10.0, shares_outstanding=1e8)
    officers = [(o["name"], o["title"], o["ownership"]) for o in km["officers"]]
    assert officers == [("John Doe", "director, Chief Executive Officer", "—"),
                        ("Jane Roe", "Chief Financial Officer", "50K")], officers
    assert km["officers"][0]["ownership_value"] == "—" and km["officers"][0]["percent_owned"] is None


def test_key_management_shows_a_possibly_stale_balance_as_unknown():
    from app.services.agents.ticker_report_data_collector import _build_key_management

    km = _build_key_management(roster_from_holdings(insider_holdings_from_rows(_stale_cfo_rows()),
                                                    include_unknown=True),
                               {}, current_price=10.0, shares_outstanding=1e8)
    assert km["officers"][0]["name"] == "Jane Roe"
    assert km["officers"][0]["ownership"] == "—", "never the pre-sale 50K"


def test_the_top_10_sheet_never_ranks_a_possibly_stale_or_indirect_only_holder():
    rows = _stale_cfo_rows() + [_row(CEO, "2026-09-12", "2026-09-13", shares=10,
                                     owned=2_000_000, own="I")] + _sep30()
    out = HoldersService._build_top_insiders(_svc(), roster_from_holdings(
        insider_holdings_from_rows(rows)), current_price=10.0, outstanding_shares=1e8)
    assert [o.name for o in out] == ["Brian M. Venturo"]


# ── `unlisted_filers`: an officer with only option / RSU lines in the window ──────────

from app.services._insider_holdings import roster_name_key, unlisted_filers  # noqa: E402


def test_an_rsu_only_ceo_is_listed_with_an_unknown_count_never_the_rsu_figure():
    raw = _sep30() + [_row(CEO, "2026-09-15", "2026-09-16", tx="A-Award", ad="A", shares=500000,
                           owned=500000, security="Restricted Stock Units")]
    listed = unlisted_filers(raw, ["Brian M. Venturo"], since="2026-01-01")
    assert [(r["display_name"], r["numberOfShares"], r["title"]) for r in listed] == [
        ("John Doe", None, "director, officer: Chief Executive Officer")]
    assert listed[0]["role"] == "director, Chief Executive Officer"


def test_unlisted_filers_skips_names_already_listed_in_any_shape_and_old_filers():
    raw = [_row(("VENTURO BRIAN M", "0002058067", "director"), "2026-09-30", "2026-10-02",
                security="Restricted Stock Units"),
           _row(CEO, "2024-03-01", "2024-03-02", security="Stock Option")]
    assert unlisted_filers(raw, ["Brian M. Venturo"], since="2025-10-08") == []
    assert roster_name_key("Brian M. Venturo") == roster_name_key("VENTURO BRIAN M") \
        == roster_name_key("Venturo, Brian")


def test_unlisted_filers_takes_each_persons_newest_row_and_orders_newest_first():
    raw = [_row(CEO, "2026-03-01", "2026-03-02", security="Stock Option",
                ),
           _row(("Doe John", "0002000004", "director, officer: President and CEO"), "2026-09-01",
                "2026-09-02", security="Stock Option"),
           _row(JAIN, "2026-05-01", "2026-05-02", security="Restricted Stock Units")]
    listed = unlisted_filers(raw, [], since="2026-01-01")
    assert [r["display_name"] for r in listed] == ["John Doe", "Sachin Jain"]
    assert listed[0]["title"] == "director, officer: President and CEO"


@pytest.mark.parametrize("raw", [
    None, "x", 5, {}, [None, 3, "a", {}],
    [{"reportingName": None, "reportingCik": None, "transactionDate": "2026-09-01"}],
    [{"reportingName": "Jane Roe", "transactionDate": "not a date", "filingDate": None}],
    [{"reportingName": "Jane Roe", "transactionDate": 20260901}],
])
def test_unlisted_filers_never_raises_on_malformed_rows(raw):
    assert unlisted_filers(raw, None, since="2026-01-01") == []


def test_unlisted_filers_falls_back_to_the_filing_date():
    raw = [{"reportingName": "Roe Jane", "reportingCik": "2000003", "typeOfOwner": "officer: CFO",
            "transactionDate": "2026-02-30", "filingDate": "2026-09-02",
            "securityName": "Stock Option"}]
    listed = unlisted_filers(raw, [], since="2026-09-01")
    assert [(r["display_name"], r["role"]) for r in listed] == [("Jane Roe", "CFO")]


class _KMOut:
    def __init__(self, raw, holders_response=None):
        self.ticker = "CRWV"
        self.holders_response = holders_response
        self.insider_unavailable = False
        self.insider_trades = raw


def _today_iso(days_back=0):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=days_back)).date().isoformat()


def test_key_management_adds_an_rsu_only_ceo_and_never_a_foreign_issuers_filer():
    from app.services._insider_common import prepare_insider_rows
    from app.services.agents.ticker_report_data_collector import (
        _build_key_management,
        _key_management_roster,
    )

    day, filed = _today_iso(10), _today_iso(8)
    raw = [_row(CFO, day, filed, shares=1000, owned=50000, price=10.0),
           _row(CEO, day, filed, tx="A-Award", ad="A", shares=900000, owned=900000,
                security="Stock Option"),
           _row(("Other Corp Holder", "0002999998", "officer: Chief Executive Officer"), day,
                filed, security="Stock Option", company="0000797468")]
    prepared, _ = prepare_insider_rows(raw, CRWV_CIK)
    roster = _key_management_roster(_KMOut(raw), prepared, CRWV_CIK)
    by_name = {r["display_name"]: r["numberOfShares"] for r in roster}
    assert by_name == {"Jane Roe": 50000.0, "John Doe": None}, by_name
    km = _build_key_management(roster, {"ceo": "John Doe"}, current_price=10.0,
                               shares_outstanding=1e8)
    assert [(o["name"], o["ownership"]) for o in km["officers"]] == [
        ("John Doe", "—"), ("Jane Roe", "50K")]
    # From the holders object too: the same supplement.
    from app.schemas.holders import HoldersResponse, OwnershipDetailSchema
    resp = HoldersResponse(symbol="CRWV", ownership_detail=OwnershipDetailSchema(
        insider_holdings=_schema(insider_holdings_from_rows(prepared))))
    assert {r["display_name"] for r in _key_management_roster(_KMOut(raw, resp), prepared,
                                                              CRWV_CIK)} == {"Jane Roe", "John Doe"}


def test_key_management_withholds_unlisted_filers_when_issuers_cannot_be_told_apart(caplog):
    from app.services.agents.ticker_report_data_collector import _key_management_roster

    day = _today_iso(5)
    mine = [_row(CFO, day, day, shares=1000, owned=50000, price=10.0)]
    raw = mine + [_row(CEO, day, day, security="Stock Option", company="0000797468")]
    from app.schemas.holders import HoldersResponse, OwnershipDetailSchema
    resp = HoldersResponse(symbol="CRWV", ownership_detail=OwnershipDetailSchema(
        insider_holdings=_schema(insider_holdings_from_rows(mine))))
    roster = _key_management_roster(_KMOut(raw, resp), mine, None)
    assert [r["display_name"] for r in roster] == ["Jane Roe"]
    assert "filers outside the holdings roster withheld" in caplog.text


def test_key_management_without_raw_rows_adds_nothing():
    from app.services.agents.ticker_report_data_collector import _km_unlisted

    for raw in (None, [], "x", 5):
        assert _km_unlisted(_KMOut(raw), [], CRWV_CIK) == []


# ── final review 2026-10-09: a blank Top 10 Insiders sheet is logged, and named honestly ──


@pytest.mark.asyncio
async def test_a_withheld_roster_logs_one_greppable_empty_sheet_line(caplog):
    import logging
    fmp = _FMP(_sep18() + _sep30(), summary={}, profile=RuntimeError("profile 503"),
               roster=_RAW_ROSTER)
    with caplog.at_level(logging.WARNING, logger=hs.logger.name):
        resp, _degraded = await _wired(fmp).get_holders_with_status("CRWV")
    assert resp.shareholder_breakdown.top_10_owners.insiders == []
    lines = [r.getMessage() for r in caplog.records if "[holders-top-insiders]" in r.getMessage()]
    assert any("ranked sheet empty (reason=holdings_unavailable)" in l for l in lines), lines


def test_an_all_indirect_roster_logs_the_no_direct_figure_reason(caplog, monkeypatch):
    import logging
    from types import SimpleNamespace

    def fake_roster(holdings, include_unknown=False):
        return [{"name": "Founder Trust"}] if include_unknown else []
    monkeypatch.setattr(hs, "roster_from_holdings", fake_roster)
    with caplog.at_level(logging.WARNING, logger=hs.logger.name):
        out = hs.HoldersService._top_insider_roster("XYZ", SimpleNamespace(insider_holdings=object()))
    assert out == []
    assert "ranked sheet empty (reason=no_direct_figure)" in caplog.text


def test_a_failed_holdings_derivation_says_the_sheet_is_empty_too():
    """The old line said "withheld from chat; the Holders tab itself is unaffected" — false once
    the Top 10 Insiders sheet ranked from these holdings."""
    import inspect
    src = inspect.getsource(hs.HoldersService._ownership_detail)
    assert "the Holders tab itself is unaffected" not in src
    assert "chat AND the Top 10 Insiders sheet is empty" in src
