"""Ask Cay AI's ownership tool, `check_ownership_filings`, and the Holders build behind it.

TestFlight 1.0 (11), 2026-10-05, "Grounded on Updates · CRWV": a director's Form 4 sale was
on screen, the user asked "how many shares does he own now?", and Cay AI answered "Caydex
does not have information on Director Brian Venturo's current total ownership". The Form 4
line behind that sale reports the balance he held after it; no chat tool carried it.

Pinned here:
  * the tool answers with the post-trade balance AND its as-of date, and says what it is not;
  * every degraded path is SAID ("could not be loaded"), never answered as "owns nothing";
  * the Holders build computes the holdings from its own rows (issuer CIK, 4/A supersession,
    fail-closed fetch), keeps them in its 24h tier, and never puts them on the wire
    (build 10's Holders JSON is byte-for-byte the same shape);
  * every chat scope with a company in context — ticker, report, Updates — is offered it.

Hermetic: fake FMP / Supabase / holders service; no network.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.integrations.fmp import FMPPartialPageException
from app.schemas.holders import (
    HoldersResponse,
    InsiderHoldingSchema,
    InsiderHoldingsSchema,
    InsiderOwnerSchema,
    InsiderTradeSchema,
    OwnershipDetailSchema,
    ShareholderBreakdownSchema,
    Top10OwnersSchema,
    TopInstitutionSchema,
)
from app.services import chat_ownership_tool as cot
from app.services import holders_service as hs
from app.services.agents import chat_tools
from app.services.holders_service import HoldersService

CRWV_CIK = "0001769628"
_VENTURO = ("Venturo Brian M", "0002058067", "director, officer: Chief Strategy Officer")


def _row(traded, filed, *, tx="S-Sale", ad="D", shares=100, price=0.0, owned=1000, own="D",
         security="Class A Common Stock", form="4", company=CRWV_CIK, who=_VENTURO):
    name, cik, title = who
    return {
        "symbol": "CRWV", "companyCik": company, "reportingName": name, "reportingCik": cik,
        "typeOfOwner": title, "transactionType": tx, "acquisitionOrDisposition": ad,
        "securitiesTransacted": shares, "price": price, "securitiesOwned": owned,
        "directOrIndirect": own, "securityName": security, "formType": form,
        "transactionDate": traded, "filingDate": filed,
    }


def _venturo_rows():
    """His 2026-09-18 and 2026-09-30 filings as the feed lists them (not in time order)."""
    return [
        _row("2026-09-30", "2026-10-02", tx="M-Exempt", ad="D", shares=109380, owned=984380,
             security="Restricted Stock Units"),
        _row("2026-09-30", "2026-10-02", tx="M-Exempt", ad="A", shares=17391, owned=368142),
        _row("2026-09-30", "2026-10-02", tx="M-Exempt", ad="A", shares=109380, owned=350751),
        _row("2026-09-30", "2026-10-02", tx="S-Sale", ad="D", shares=65616, price=87.69,
             owned=302526),
        _row("2026-09-18", "2026-09-22", tx="C-Conversion", ad="D", shares=62500, owned=1515849,
             own="I", security="Class B Common Stock"),
        _row("2026-09-18", "2026-09-22", tx="C-Conversion", ad="A", shares=62500, owned=303871),
        _row("2026-09-18", "2026-09-22", tx="G-Gift", ad="D", shares=62500, owned=241371),
    ]


# ── the Holders build (real `_build_holders`, fake upstreams) ───────────────────────────

class _FMP:
    def __init__(self, insider, *, summary=None, profile=None):
        self.insider, self.summary, self.profile = insider, summary or {}, profile

    async def get_shares_float(self, t): return {"freeFloat": 60.0, "outstandingShares": 4.0e8}
    async def get_institutional_holder(self, t, limit=20): return []
    async def get_institutional_ownership_summary(self, t): return self.summary
    async def get_institutional_ownership_for_quarter(self, t, y, q, strict=False): return None
    async def get_insider_trades_since(self, since, *, symbol=None, page_size=1000,
                                       max_pages=5, transaction_type=None):
        if isinstance(self.insider, BaseException):
            raise self.insider
        return self.insider
    async def get_company_profile(self, t):
        if isinstance(self.profile, BaseException):
            raise self.profile
        return self.profile or {}
    async def get_insider_roster(self, t): return []
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
    """Records upserts; a `holders_cache` read returns `cached` (other tables read empty)."""

    def __init__(self, cached=None):
        self.upserts, self.cached, self._table = [], cached or [], None

    def table(self, name):
        self._table = name
        return self

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def upsert(self, payload, **k):
        self.upserts.append(payload)
        return self

    def execute(self):
        data = self.cached if self._table == "holders_cache" else []

        class _R:
            pass
        r = _R()
        r.data = data
        return r


@pytest.fixture(autouse=True)
def _fresh_holders_tiers(monkeypatch):
    monkeypatch.setattr(hs, "_cache", {})
    monkeypatch.setattr(hs, "_inflight", {})
    monkeypatch.setattr(hs, "_background_tasks", set())


@pytest.fixture(autouse=True)
def _no_side_reads(monkeypatch):
    """The tool's two side reads (short interest through its cache, the company-profile row)
    are stubbed for every test here: hermetic, and answered as 'not available'. Tests that
    exercise them patch these again."""
    async def _no_short(sym):
        return {}

    async def _no_profile(sym):
        return None

    monkeypatch.setattr(cot, "_load_short_interest", _no_short)
    monkeypatch.setattr(cot, "_issuer_profile_flags", _no_profile)
    monkeypatch.setattr(cot, "_side_tasks", set())   # never another test's (or loop's) tasks


def _wired(fmp, supabase=None):
    """A HoldersService on fakes with a COLD 5-minute tier (a second build in one test must
    not be served the first one's entry)."""
    from tests._price_fakes import PriceFromFMPFake
    hs._cache.clear()
    hs._inflight.clear()
    svc = object.__new__(HoldersService)
    svc.fmp, svc.price, svc.corporate_actions = fmp, PriceFromFMPFake(fmp), _CA()
    svc.supabase = supabase or _Supabase()
    return svc


async def _drain():
    await asyncio.gather(*list(hs._background_tasks), return_exceptions=True)


def _holdings(resp):
    detail = resp.ownership_detail
    return None if detail is None else detail.insider_holdings


@pytest.mark.asyncio
async def test_the_holders_build_carries_each_insiders_post_trade_balance():
    other_issuer = _row("2026-09-29", "2026-09-30", tx="P-Purchase", ad="A", shares=1000,
                        owned=250_000_000, company="0000797468",
                        who=("BERKSHIRE HATHAWAY INC", "0001067983", "10 percent owner"))
    fmp = _FMP(_venturo_rows() + [other_issuer], summary={"cik": CRWV_CIK, "ownershipPercent": 40.0})
    resp, degraded = await _wired(fmp).get_holders_with_status("CRWV")
    assert degraded == []
    holdings = _holdings(resp)
    assert holdings is not None and holdings.complete is True
    names = [p.name for p in holdings.insiders]
    assert names == ["Brian M. Venturo"], "the other issuer's row never becomes an insider"
    venturo = holdings.insiders[0]
    direct = [h for h in venturo.holdings if h.held == "direct"]
    assert [(h.shares, h.as_of, h.filed) for h in direct] == [(302526, "2026-09-30", "2026-10-02")]
    indirect = [h for h in venturo.holdings if h.held == "indirect"]
    assert [(h.security, h.shares) for h in indirect] == [("Class B Common Stock", 1515849)]
    assert resp.ownership_detail.institutions_quarter.startswith("Q")


@pytest.mark.asyncio
async def test_a_form_4a_supersedes_its_original_in_the_build():
    original = _row("2026-09-30", "2026-10-02", shares=65616, price=87.69, owned=302526)
    amended = dict(original, formType="4/A", filingDate="2026-10-05",
                   securitiesTransacted=60000, securitiesOwned=308142)
    fmp = _FMP([amended, original], summary={"cik": CRWV_CIK})
    resp, _ = await _wired(fmp).get_holders_with_status("CRWV")
    direct = [h for h in _holdings(resp).insiders[0].holdings if h.held == "direct"]
    assert [(h.shares, h.filed) for h in direct] == [(308142, "2026-10-05")]


@pytest.mark.asyncio
async def test_the_holdings_ride_into_the_24h_tier_and_back_and_a_v2_row_is_refused():
    supabase = _Supabase()
    svc = _wired(_FMP(_venturo_rows(), summary={"cik": CRWV_CIK}), supabase)
    resp, _ = await svc.get_holders_with_status("CRWV")
    await _drain()
    payload = supabase.upserts[0]["response_json"]
    assert payload["payload_version"] == hs._HOLDERS_PAYLOAD_VERSION == 6
    assert payload["ownership_detail"]["insider_holdings"]["insiders"][0]["name"] == "Brian M. Venturo"
    json.dumps(payload, allow_nan=False)  # the row is valid JSON for the jsonb column

    now = datetime.now(timezone.utc).isoformat()
    reader = _wired(_FMP(RuntimeError("must not be called")),
                    _Supabase(cached=[{"response_json": payload, "cached_at": now}]))
    cached = reader._check_supabase_cache("CRWV")
    assert cached is not None
    assert _holdings(cached).insiders[0].holdings[0].shares == 302526

    # 2: no holdings; 3-4: holdings by earlier review rounds' rules; 5: the raw-row Top 10
    for version in (2, 3, 4, 5):
        stale_shape = dict(payload, payload_version=version)
        old = _wired(_FMP([]), _Supabase(cached=[{"response_json": stale_shape, "cached_at": now}]))
        assert old._check_supabase_cache("CRWV") is None, f"a v{version} row is rebuilt, not served"


def test_the_holders_json_every_app_build_decodes_is_unchanged():
    """`ownership_detail` is chat-only: excluded from the endpoint's serialization, so build
    10's `HoldersResponseDTO` sees exactly the keys it always did."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    resp = HoldersResponse(symbol="CRWV", ownership_detail=OwnershipDetailSchema(
        insider_holdings=InsiderHoldingsSchema(), institutions_quarter="Q2 2026"))
    app = FastAPI()

    @app.get("/h", response_model=HoldersResponse)
    async def _h():
        return resp

    body = TestClient(app).get("/h").json()
    assert set(body) == {
        "symbol", "shareholder_breakdown", "insider_data", "hedge_funds_data", "congress_data",
        "recent_activities", "congress_locked", "congress_tier_required",
    }
    assert "ownership_detail" not in resp.model_dump()
    assert "ownership_detail" not in json.loads(resp.model_dump_json())


@pytest.mark.asyncio
async def test_a_failed_issuer_cik_lookup_withholds_the_holdings():
    """Without the issuer CIK the feed cannot be filtered: BRK-B's would read Berkshire's
    stakes in other companies as its insiders' holdings."""
    fmp = _FMP(_venturo_rows(), summary={}, profile=RuntimeError("profile 503"))
    resp, degraded = await _wired(fmp).get_holders_with_status("CRWV")
    assert "Issuer CIK" in degraded
    assert resp.ownership_detail is not None and _holdings(resp) is None


@pytest.mark.asyncio
async def test_no_cik_anywhere_withholds_only_when_the_rows_name_several_issuers():
    mixed = _venturo_rows() + [_row("2026-09-29", "2026-09-30", company="0000797468",
                                    who=("SOMEONE ELSE", "0000000099", "director"))]
    resp, degraded = await _wired(_FMP(mixed, summary={}, profile={})).get_holders_with_status("CRWV")
    assert degraded == [] and _holdings(resp) is None
    resp, _ = await _wired(_FMP(_venturo_rows(), summary={}, profile={})).get_holders_with_status("CRWV")
    assert _holdings(resp) is not None and _holdings(resp).insiders


@pytest.mark.asyncio
async def test_a_failed_insider_fetch_is_unavailable_and_a_lost_page_is_incomplete():
    resp, degraded = await _wired(_FMP(RuntimeError("429"), summary={"cik": CRWV_CIK})).get_holders_with_status("CRWV")
    assert "Insider trading" in degraded and _holdings(resp) is None

    lost = FMPPartialPageException("lost", endpoint="e", pages_total=2, pages_failed=1,
                                   partial=_venturo_rows())
    resp, degraded = await _wired(_FMP(lost, summary={"cik": CRWV_CIK})).get_holders_with_status("CRWV")
    assert degraded == ["Insider trading"]
    holdings = _holdings(resp)
    assert holdings is not None and holdings.complete is False
    assert holdings.insiders[0].holdings[0].shares == 302526


@pytest.mark.asyncio
async def test_a_holdings_defect_never_costs_the_holders_tab_its_build(monkeypatch, caplog):
    def _boom(rows):
        raise ValueError("synthetic defect")
    monkeypatch.setattr(hs, "insider_holdings_from_rows", _boom)
    svc = _wired(_FMP(_venturo_rows(), summary={"cik": CRWV_CIK}))
    resp, degraded = await svc.get_holders_with_status("CRWV")
    assert degraded == []
    assert resp.recent_activities.insider_activities.activities, "the tab's own data is intact"
    assert _holdings(resp) is None
    assert "holdings derivation FAILED" in caplog.text


# ── the tool ────────────────────────────────────────────────────────────────────────

class _Holders:
    def __init__(self, resp=None, degraded=(), exc=None):
        self.resp, self.degraded, self.exc, self.calls = resp, list(degraded), exc, []

    async def get_holders_with_status(self, ticker):
        self.calls.append(ticker)
        if self.exc is not None:
            raise self.exc
        return self.resp, list(self.degraded)


def _breakdown(**kw):
    base = dict(
        insiders_percent=41.2, institutions_percent=38.55, public_other_percent=20.25,
        institutions_source="summary",
        top_10_owners=Top10OwnersSchema(institutions=[
            TopInstitutionSchema(rank=1, name="Vanguard Group Inc", value_in_billions=2.5,
                                 percent_ownership=6.1234),
            TopInstitutionSchema(rank=2, name="Small Fund LP", value_in_billions=0.0421,
                                 percent_ownership=0.004),
        ]),
    )
    base.update(kw)
    return ShareholderBreakdownSchema(**base)


def _venturo_owner(**kw):
    owner = dict(
        name="Brian M. Venturo", role="director, Chief Strategy Officer",
        latest_transaction_date="2026-09-30", latest_filing_date="2026-10-02",
        latest_trades=[InsiderTradeSchema(transaction_type="S-Sale", acquired=False,
                                          shares=65616, average_price=87.69)],
        holdings=[
            InsiderHoldingSchema(security="Class A Common Stock", held="direct",
                                 shares=302526, as_of="2026-09-30", filed="2026-10-02"),
            InsiderHoldingSchema(security="Class B Common Stock", held="indirect",
                                 shares=1515849, as_of="2026-09-18", filed="2026-09-22"),
            InsiderHoldingSchema(security="Class B Common Stock", held="indirect",
                                 shares=4990542, as_of="2026-07-01", reported_earlier=True),
        ],
    )
    owner.update(kw)
    return InsiderOwnerSchema(**owner)


def _response(*, holdings="default", breakdown=None):
    """A Holders build read just now (fresh, so the tool does not probe for newer filings)."""
    if holdings == "default":
        holdings = InsiderHoldingsSchema(covers_filings_since="2025-07-01",
                                         insiders=[_venturo_owner()])
    return HoldersResponse(
        symbol="CRWV", shareholder_breakdown=breakdown or _breakdown(),
        ownership_detail=OwnershipDetailSchema(
            insider_holdings=holdings, institutions_quarter="Q2 2026",
            built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ),
    )


@pytest.fixture
def holders(monkeypatch):
    fake = _Holders(_response())
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_the_tool_answers_the_testflight_question_with_the_balance_and_its_date(holders):
    out = await cot.fetch_ownership("crwv")
    assert holders.calls == ["CRWV"]
    assert "error" not in out and "upstream" not in out
    venturo = out["insiders"]["people"][0]
    assert venturo["name"] == "Brian M. Venturo"
    assert venturo["holdings"][0] == (
        "Class A Common Stock held directly: 302,526 shares as of 2026-09-30")
    # Plain words from the Holders tab's own code table (2026-10-08): an open-market sale,
    # its dollar figure being PROCEEDS — never "the tax".
    assert venturo["latest_transaction"] == (
        "2026-09-30 (filed 2026-10-02): S-Sale: sold 65,616 shares in the open market at an "
        "average $87.69 (about $5.75 million in sale proceeds)")
    assert venturo["holdings"][2].endswith("[earlier figure]")
    assert out["insiders"]["covers_filings_since"] == "2025-07-01"
    how = out["how_to_read"]
    assert "as of" in how and "never present it as a live" in how
    assert "never add holdings up into a total" in how
    assert "never that they own nothing" in how


@pytest.mark.asyncio
async def test_the_institutional_block_carries_the_quarter_and_the_largest_holders(holders):
    inst = (await cot.fetch_ownership("CRWV"))["institutions"]
    assert inst["available"] is True
    assert inst["as_of"] == "13F filings for Q2 2026"
    assert inst["institutions_percent"] == 38.55 and inst["insiders_percent"] == 41.2
    assert inst["public_and_other_percent"] == 20.25
    assert inst["largest_institutions"][0] == (
        "Vanguard Group Inc: 6.12% of shares, worth $2.50 billion at the quarter's end")
    # a tiny stake keeps its precision instead of rounding to 0.00%
    assert inst["largest_institutions"][1].startswith("Small Fund LP: 0.004% of shares, worth $42.1 million")


@pytest.mark.asyncio
@pytest.mark.parametrize("holdings", [None, "no-detail"])
async def test_unloadable_insiders_say_so_and_never_read_as_owning_nothing(monkeypatch, holdings):
    resp = _response(holdings=None)
    if holdings == "no-detail":
        resp = resp.model_copy(update={"ownership_detail": None})
    monkeypatch.setattr(hs, "get_holders_service", lambda: _Holders(resp))
    out = await cot.fetch_ownership("CRWV")
    assert out["insiders"]["available"] is False
    assert "could not be loaded" in out["insiders"]["note"]
    assert "people" not in out["insiders"]
    assert "error" not in out, "institutions still answered: a partial answer, not an outage"


@pytest.mark.asyncio
async def test_an_incomplete_fetch_and_an_empty_period_are_worded_honestly(monkeypatch):
    partial = InsiderHoldingsSchema(complete=False, insiders=[_venturo_owner()])
    monkeypatch.setattr(hs, "get_holders_service", lambda: _Holders(_response(holdings=partial)))
    out = await cot.fetch_ownership("CRWV")
    assert out["insiders"]["complete"] is False and "people may be missing" in out["insiders"]["note"]

    empty = InsiderHoldingsSchema(covers_filings_since="2025-10-01", insiders=[])
    monkeypatch.setattr(hs, "get_holders_service", lambda: _Holders(_response(holdings=empty)))
    out = await cot.fetch_ownership("CRWV")
    assert out["insiders"]["people"] == []
    assert "not the same as insiders owning nothing" in out["insiders"]["note"]


@pytest.mark.asyncio
async def test_nothing_usable_is_an_upstream_failure_the_refund_gate_counts(monkeypatch):
    blank = _breakdown(institutions_unknown=True, institutions_percent=0.0,
                       public_other_percent=0.0, insiders_percent=0.0,
                       top_10_owners=Top10OwnersSchema())
    monkeypatch.setattr(hs, "get_holders_service",
                        lambda: _Holders(_response(holdings=None, breakdown=blank),
                                         degraded=["Insider trading", "Institutional holders"]))
    out = await cot.fetch_ownership("CRWV")
    assert out["error"] and out["upstream"] is True
    assert out["institutions"]["institutions_percent"].startswith("unknown")
    assert out["institutions"]["largest_institutions"] == "could not be loaded right now"


@pytest.mark.asyncio
async def test_a_holders_failure_is_an_upstream_error_with_no_secret(monkeypatch):
    fake = _Holders(exc=RuntimeError("GET https://x/stable/insider?apikey=abcd1234secret failed"))
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    out = await cot.fetch_ownership("CRWV")
    assert out["upstream"] is True and out["available"] is False
    assert "abcd1234secret" not in json.dumps(out)
    assert "do not say there are none" in out["note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["BTCUSD", "^GSPC", "GCUSD", "ETHUSD"])
async def test_a_coin_an_index_or_a_future_is_answered_not_fetched(holders, symbol):
    out = await cot.fetch_ownership(symbol)
    assert out["error"] and not out.get("upstream")
    assert holders.calls == []


@pytest.mark.asyncio
async def test_a_symbol_the_filings_cannot_key_is_answered_and_a_class_share_is_normalised(holders):
    out = await cot.fetch_ownership("TOOLONGX")
    assert out["error"] and not out.get("upstream") and holders.calls == []
    assert (await cot.fetch_ownership(""))["error"] == "no ticker supplied"
    await cot.fetch_ownership("brk.b")
    assert holders.calls == ["BRK-B"]


@pytest.mark.asyncio
async def test_unknown_institutional_figures_are_unknown_never_zero(monkeypatch):
    breakdown = _breakdown(institutions_unknown=True, institutions_percent=0.0,
                           public_other_percent=0.0, institutions_source="unknown")
    monkeypatch.setattr(hs, "get_holders_service",
                        lambda: _Holders(_response(breakdown=breakdown), degraded=["Shares float"]))
    inst = (await cot.fetch_ownership("CRWV"))["institutions"]
    assert inst["institutions_percent"].startswith("unknown")
    assert "insiders_percent" not in inst, "a failed float read leaves a 0.0 placeholder"
    assert "public_and_other_percent" not in inst
    assert inst["available"] is True, "the largest holders still answered"


@pytest.mark.asyncio
async def test_a_fallback_institutional_figure_names_its_basis(monkeypatch):
    breakdown = _breakdown(institutions_source="top_holders_sum")
    monkeypatch.setattr(hs, "get_holders_service", lambda: _Holders(_response(breakdown=breakdown)))
    inst = (await cot.fetch_ownership("CRWV"))["institutions"]
    assert "LOWER BOUND" in inst["institutions_basis"]


@pytest.mark.asyncio
async def test_ambiguous_and_possibly_stale_balances_are_said_so(monkeypatch):
    owner = _venturo_owner(holdings=[
        InsiderHoldingSchema(security="Class A Common Stock", held="direct", shares=None,
                             possible_shares=[241371, 303871], as_of="2026-09-18"),
        InsiderHoldingSchema(security="Class A Common Stock", held="indirect", shares=80,
                             as_of="2026-07-01", changed_after="2026-08-02"),
    ])
    holdings = InsiderHoldingsSchema(insiders=[owner])
    monkeypatch.setattr(hs, "get_holders_service", lambda: _Holders(_response(holdings=holdings)))
    lines = (await cot.fetch_ownership("CRWV"))["insiders"]["people"][0]["holdings"]
    assert lines[0] == ("Class A Common Stock held directly: one of 241,371 or 303,871 shares "
                        "(the filings do not show which line came last that day) as of 2026-09-18")
    # "a transaction", not "a later" one: the flag also covers the balance's own day.
    assert ("a transaction on 2026-08-02 reported no usable balance, so this may not be the "
            "latest figure") in lines[1]


@pytest.mark.asyncio
async def test_the_result_fits_the_tool_cap_and_names_who_was_cut(monkeypatch):
    people = [
        _venturo_owner(name=f"Insider Number {i:02d}", holdings=[
            InsiderHoldingSchema(security="Class B Common Stock", held="indirect",
                                 shares=1_000_000 + j, as_of="2026-09-01")
            for j in range(5)
        ])
        for i in range(25)
    ]
    holdings = InsiderHoldingsSchema(insiders=people)
    monkeypatch.setattr(hs, "get_holders_service", lambda: _Holders(_response(holdings=holdings)))
    out = await cot.fetch_ownership("CRWV")
    from app.config import settings
    cap = int(getattr(settings, "GEMINI_TOOL_RESULT_MAX_CHARS", 8000) or 8000)
    assert len(json.dumps(out, default=str)) <= cap, "the structural pruner must never cut it blind"
    shown = [p["name"] for p in out["insiders"]["people"]]
    cut = out["insiders"]["not_shown"]
    assert shown[0] == "Insider Number 00", "the most recent filer survives"
    assert shown + cut == [f"Insider Number {i:02d}" for i in range(25)]
    assert "latest_transaction" in out["insiders"]["people"][0]


@pytest.mark.asyncio
async def test_no_vendor_or_model_name_reaches_the_model(holders):
    text = json.dumps(await cot.fetch_ownership("CRWV")).lower()
    for word in ("fmp", "financial modeling prep", "gemini", "google", "openai"):
        assert word not in text, word


# ── the registry: who is offered it, and how it is called ──────────────────────────────

def test_every_chat_with_a_company_in_context_is_offered_the_tool():
    from app.services.chat_service import ChatService
    detect = ChatService._detect_asset_type
    cases = {
        ("CRWV", "UPDATES_SCOPE", "CRWV"): True,                # the TestFlight screen
        ("CRWV", "TICKER_REPORT", "CRWV|warren_buffett"): True,  # report chat
        ("CRWV", "STOCK", "CRWV"): True,                        # ticker detail chat
        ("__MARKET__", "UPDATES_SCOPE", "__MARKET__"): True,    # global: any stock may be asked
        ("SPY", "UPDATES_SCOPE", "SPY|ETF"): False,             # a fund files no Form 4s
        ("BTCUSD", "CRYPTO", "BTCUSD"): False,
        ("^GSPC", "INDEX", "^GSPC"): False,
        ("GCUSD", "COMMODITY", "GCUSD"): False,
    }
    for (stock_id, ctx, ref), expected in cases.items():
        granted = chat_tools.OWNERSHIP_TOOL in chat_tools.tools_for_asset_type(detect(stock_id, ctx, ref))
        assert granted is expected, (stock_id, ctx)
    assert chat_tools.OWNERSHIP_TOOL in chat_tools.tools_for_asset_type(None), "global chat"


def test_the_ownership_tool_never_widens_web_search():
    for asset_type in ("STOCK", "NORMAL", None):
        assert chat_tools.WEB_SEARCH_TOOL not in chat_tools.tools_for_asset_type(asset_type)


def test_the_declaration_takes_a_ticker_only():
    decl = [fd for t in chat_tools.build_chat_tool_declarations("STOCK")
            for fd in (t.function_declarations or []) if fd.name == chat_tools.OWNERSHIP_TOOL]
    assert len(decl) == 1
    assert set(decl[0].parameters.properties) == {"ticker"}
    assert list(decl[0].parameters.required) == ["ticker"]


@pytest.mark.parametrize("asset_type,expected", [
    ("STOCK", True), ("NORMAL", True), ("ETF", False), ("CRYPTO", False),
    ("INDEX", False), ("COMMODITY", False),
])
def test_the_prompt_tells_the_model_to_call_it_only_where_it_is_granted(asset_type, expected):
    block = chat_tools.capability_block(chat_tools.tools_for_asset_type(asset_type))
    assert ("OWNERSHIP QUESTIONS" in block) is expected
    assert (chat_tools.OWNERSHIP_TOOL in block) is expected
    if expected:
        assert "never say Caydex has no ownership information without calling it" in block
        assert "never as a live count" in block


def test_the_chip_scope_offers_ownership_questions_only_where_answerable():
    assert "insiders' reported share holdings" in chat_tools.chip_scope_block("STOCK")
    assert "insiders' reported share holdings" not in chat_tools.chip_scope_block("ETF")


@pytest.mark.asyncio
async def test_the_handler_keeps_the_screen_symbol_and_refuses_junk_before_any_fetch():
    class _Svc:
        def __init__(self):
            self._fetch_ownership_data = AsyncMock(return_value={"ok": True})

        @staticmethod
        def _chat_symbol(raw):
            return {"LTC": "LTCUSD"}.get(raw, raw)

    svc = _Svc()
    on_reit = chat_tools.build_chat_tool_handlers(svc, screen_symbol="LTC", screen_asset_type="STOCK")
    await on_reit[chat_tools.OWNERSHIP_TOOL]({"ticker": "LTC"})
    svc._fetch_ownership_data.assert_awaited_with("LTC")   # the REIT, not Litecoin's pair
    await on_reit[chat_tools.OWNERSHIP_TOOL]({"symbol": "AAPL"})  # the mis-keyed spelling
    svc._fetch_ownership_data.assert_awaited_with("AAPL")
    svc._fetch_ownership_data.reset_mock()
    for junk in ({"ticker": ""}, {"ticker": "Apple Inc (AAPL)"}, {}):
        assert await on_reit[chat_tools.OWNERSHIP_TOOL](junk) == {"error": "invalid or missing ticker"}
    svc._fetch_ownership_data.assert_not_awaited()


def test_the_tool_has_a_ceiling_sized_for_a_cold_holders_build():
    from app.integrations.gemini import _TOOL_TIMEOUTS
    assert _TOOL_TIMEOUTS[chat_tools.OWNERSHIP_TOOL] >= 15.0


# ── Adversarial review, 2026-10-07: each finding reproduced by a failing test first ─────
#
# Finding 3: the Holders row is cached up to 24 h and nothing said when the filings were read,
# so right after a new Form 4 — exactly when users ask — the pre-filing balance read as current.

def _iso(hours_ago: float) -> str:
    from datetime import timedelta
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _stamped(owner, *, built_at):
    resp = _response(holdings=InsiderHoldingsSchema(insiders=[owner]))
    detail = resp.ownership_detail.model_copy(update={"built_at": built_at})
    return resp.model_copy(update={"ownership_detail": detail})


_PRE_SALE = _venturo_owner(
    latest_transaction_date="2026-09-18", latest_filing_date="2026-09-22", latest_trades=[],
    holdings=[InsiderHoldingSchema(security="Class A Common Stock", held="direct",
                                   shares=241371, as_of="2026-09-18")])


class _Fresh:
    """A holders service whose cached build may predate a filing; `force_refresh` rebuilds."""

    def __init__(self, cached, fresh=None, *, newer=False, probe_exc=None, fresh_degraded=()):
        self.cached, self.fresh, self.newer, self.probe_exc = cached, fresh, newer, probe_exc
        self.fresh_degraded = list(fresh_degraded)
        self.calls, self.probes = [], 0

    async def get_holders_with_status(self, ticker, *, force_refresh=False):
        self.calls.append((ticker, force_refresh))
        if force_refresh:
            return self.fresh, list(self.fresh_degraded)
        return self.cached, []

    async def newer_insider_filing(self, ticker, detail):
        self.probes += 1
        if self.probe_exc is not None:
            raise self.probe_exc
        return self.newer


@pytest.fixture(autouse=True)
def _fresh_probe_clock(monkeypatch):
    monkeypatch.setattr(cot, "_last_probe", {})
    monkeypatch.setattr(cot, "_fresh_reads", {})
    monkeypatch.setattr(cot, "_fresh_views", {})
    monkeypatch.setattr(cot, "_probe_inflight", {})


@pytest.mark.asyncio
async def test_r3_the_tool_states_when_the_filings_were_read(monkeypatch):
    fake = _Fresh(_stamped(_PRE_SALE, built_at="2026-10-02T08:00:00+00:00"))
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    out = await cot.fetch_ownership("CRWV")
    assert out["filings_checked_at"] == "2026-10-02 08:00 UTC"
    assert "filings_checked_at" in out["how_to_read"]
    assert "filed after it is not included" in out["how_to_read"]


@pytest.mark.asyncio
async def test_r3_a_filing_newer_than_the_cached_build_is_read_before_answering(monkeypatch):
    fresh = _stamped(_venturo_owner(), built_at=_iso(0))
    fake = _Fresh(_stamped(_PRE_SALE, built_at=_iso(10)), fresh, newer=True)
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    out = await cot.fetch_ownership("CRWV")
    assert fake.calls == [("CRWV", False), ("CRWV", True)]
    lines = out["insiders"]["people"][0]["holdings"]
    assert lines[0] == "Class A Common Stock held directly: 302,526 shares as of 2026-09-30"
    assert "freshness" not in out


@pytest.mark.asyncio
async def test_r3_no_newer_filing_no_rebuild_and_a_young_build_is_not_probed(monkeypatch):
    old = _Fresh(_stamped(_PRE_SALE, built_at=_iso(10)), newer=False)
    monkeypatch.setattr(hs, "get_holders_service", lambda: old)
    await cot.fetch_ownership("CRWV")
    assert old.probes == 1 and old.calls == [("CRWV", False)]

    young = _Fresh(_stamped(_PRE_SALE, built_at=_iso(0.05)), newer=True)
    monkeypatch.setattr(hs, "get_holders_service", lambda: young)
    await cot.fetch_ownership("NVDA")
    assert young.probes == 0 and young.calls == [("NVDA", False)]


@pytest.mark.asyncio
async def test_r3_probes_are_rate_limited_per_ticker(monkeypatch):
    fake = _Fresh(_stamped(_PRE_SALE, built_at=_iso(10)), newer=False)
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    await cot.fetch_ownership("CRWV")
    await cot.fetch_ownership("CRWV")
    assert fake.probes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", ["raises", "unknown"])
async def test_r3_a_failed_probe_is_said_and_the_cached_figures_served(monkeypatch, probe):
    fake = _Fresh(_stamped(_PRE_SALE, built_at=_iso(10)), newer=None,
                  probe_exc=RuntimeError("probe 503") if probe == "raises" else None)
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    out = await cot.fetch_ownership("CRWV")
    assert "could not check for newer filings" in out["freshness"]
    assert out["insiders"]["people"][0]["holdings"][0].startswith(
        "Class A Common Stock held directly: 241,371 shares")
    assert "error" not in out


@pytest.mark.asyncio
async def test_r3_a_failed_refresh_keeps_the_cached_figures_and_says_they_predate_a_filing(monkeypatch):
    broken = _response(holdings=None)
    fake = _Fresh(_stamped(_PRE_SALE, built_at=_iso(10)), broken, newer=True,
                  fresh_degraded=["Insider trading"])
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    out = await cot.fetch_ownership("CRWV")
    assert "a newer insider filing exists but could not be loaded" in out["freshness"]
    assert out["insiders"]["available"] is True
    assert "241,371" in out["insiders"]["people"][0]["holdings"][0]


@pytest.mark.asyncio
async def test_r3_the_build_stamps_when_and_what_it_read():
    fmp = _FMP(_venturo_rows(), summary={"cik": CRWV_CIK})
    resp, _ = await _wired(fmp).get_holders_with_status("CRWV")
    detail = resp.ownership_detail
    built = datetime.fromisoformat(detail.built_at)
    assert abs((datetime.now(timezone.utc) - built).total_seconds()) < 60
    assert detail.newest_filed == "2026-10-02"
    assert len(detail.newest_filed_ids) == 4, "the four raw rows filed on the newest date"


class _ProbeFMP(_FMP):
    def __init__(self, page):
        super().__init__([])
        self.page = page

    async def get_insider_trading(self, ticker, limit=100):
        if isinstance(self.page, BaseException):
            raise self.page
        return self.page


@pytest.mark.asyncio
async def test_r3_the_probe_sees_a_new_filing_and_nothing_else():
    from app.integrations.fmp import EmptyAfterFailure

    built = _wired(_FMP(_venturo_rows(), summary={"cik": CRWV_CIK}))
    resp, _ = await built.get_holders_with_status("CRWV")
    detail = resp.ownership_detail
    same_page = _venturo_rows()
    later_day = [_row("2026-10-05", "2026-10-07", shares=10, owned=302516)] + same_page
    same_day_new = [_row("2026-10-01", "2026-10-02", shares=10, owned=302516)] + same_page
    cases = [
        (same_page, False), (later_day, True), (same_day_new, True), ([], False),
        (EmptyAfterFailure("429"), None), (RuntimeError("boom"), None), ({"bad": 1}, None),
    ]
    for page, expected in cases:
        probe = _wired(_ProbeFMP(page))
        assert await probe.newer_insider_filing("CRWV", detail) is expected, page


@pytest.mark.asyncio
async def test_r3_force_refresh_rebuilds_and_a_degraded_refresh_keeps_the_good_entry():
    good_fmp = _FMP(_venturo_rows(), summary={"cik": CRWV_CIK})
    svc = _wired(good_fmp)
    first, _ = await svc.get_holders_with_status("CRWV")
    # A cached hit unless forced:
    svc.fmp.insider = _venturo_rows()[3:]
    again, _ = await svc.get_holders_with_status("CRWV")
    assert again is first
    refreshed, degraded = await svc.get_holders_with_status("CRWV", force_refresh=True)
    assert refreshed is not first and degraded == []
    # A forced rebuild that degrades is returned to its caller but never replaces the
    # 5-minute entry other readers get.
    svc.fmp.insider = RuntimeError("429")
    failed, degraded = await svc.get_holders_with_status("CRWV", force_refresh=True)
    assert "Insider trading" in degraded
    after, degraded_after = await svc.get_holders_with_status("CRWV")
    assert after is refreshed and degraded_after == []


@pytest.mark.asyncio
async def test_r7_everyone_left_out_is_named_and_never_called_filing_less(monkeypatch):
    holdings = InsiderHoldingsSchema(
        insiders=[_venturo_owner()], insiders_not_shown=2,
        insiders_not_shown_names=["Person Twentysix", "Person Twentyseven"],
        inactive_not_shown=1, inactive_not_shown_names=["Robert A. Iger"],
    )
    resp = _response(holdings=holdings)
    resp = resp.model_copy(update={"ownership_detail": resp.ownership_detail.model_copy(
        update={"built_at": _iso(0)})})
    monkeypatch.setattr(hs, "get_holders_service", lambda: _Fresh(resp))
    out = await cot.fetch_ownership("CRWV")
    block = out["insiders"]
    assert block["not_shown"] == ["Person Twentysix", "Person Twentyseven"]
    assert "Robert A. Iger" in block["older_filers_not_shown"]
    assert "their figures were not loaded" in out["how_to_read"]
    assert "more_people_not_shown" not in block


# ── Final review, 2026-10-07: F3 / F4 / F6 reproduced by a failing test first ─────────
#
# Driven through the REAL HoldersService on fakes: a Supabase row built hours ago from the
# pre-sale rows (newest filing 2026-09-22), and a feed that now also holds the 2026-10-02
# Form 4 (302,526 shares after the sale).

_PRE_SALE_ROWS = _venturo_rows()[4:]          # the 2026-09-18 filing only


class _LiveFMP(_FMP):
    """The feed as it stands now; counts the probes (one page) and the full insider reads."""

    def __init__(self, rows, *, senate=None, probe_gate=None, build_gate=None):
        super().__init__(rows, summary={"cik": CRWV_CIK})
        self.senate, self.probe_gate, self.build_gate = senate, probe_gate, build_gate
        self.probes = 0
        self.builds = 0
        self.build_started = asyncio.Event()

    async def get_insider_trading(self, ticker, limit=100):
        self.probes += 1
        if self.probe_gate is not None:
            await self.probe_gate.wait()
        return list(self.insider)

    async def get_insider_trades_since(self, since, **kw):
        self.builds += 1
        self.build_started.set()
        if self.build_gate is not None:
            await self.build_gate.wait()
        return await super().get_insider_trades_since(since, **kw)

    async def get_senate_latest(self, limit=1000):
        if self.senate is not None:
            raise self.senate
        return []


async def _stored_row(rows, *, hours_ago):
    """The `holders_cache` row a build of `rows` wrote `hours_ago`."""
    written = _Supabase()
    await _wired(_FMP(rows, summary={"cik": CRWV_CIK}), written).get_holders_with_status("CRWV")
    await _drain()
    payload = json.loads(json.dumps(written.upserts[0]["response_json"], default=str))
    payload["ownership_detail"]["built_at"] = _iso(hours_ago)
    hs._cache.clear()
    hs._inflight.clear()
    return _Supabase(cached=[{"response_json": payload, "cached_at": _iso(hours_ago)}])


async def _live(monkeypatch, fmp, *, hours_ago=20):
    svc = _wired(fmp, await _stored_row(_PRE_SALE_ROWS, hours_ago=hours_ago))
    monkeypatch.setattr(hs, "get_holders_service", lambda: svc)
    return svc


def _first_holding(out):
    return out["insiders"]["people"][0]["holdings"][0]


@pytest.mark.asyncio
async def test_f3_a_forced_refresh_never_joins_a_read_served_from_the_24h_row(monkeypatch):
    """F3: `force_refresh` joined ANY in-flight read — including one resolved from the 24h
    Supabase row — so the "rebuild" handed back the very build the probe had proven stale."""
    import threading

    fmp = _LiveFMP(_venturo_rows())
    svc = await _live(monkeypatch, fmp)
    entered, release = threading.Event(), threading.Event()
    real_read = svc._check_supabase_cache

    def slow_read(ticker):
        entered.set()
        release.wait(5)
        return real_read(ticker)

    monkeypatch.setattr(svc, "_check_supabase_cache", slow_read)
    reader = asyncio.create_task(svc.get_holders_with_status("CRWV"))
    await asyncio.to_thread(entered.wait, 5)
    forced = asyncio.create_task(svc.get_holders_with_status("CRWV", force_refresh=True))
    await asyncio.sleep(0.05)
    release.set()
    stale, _ = await reader
    fresh, _ = await forced
    assert fmp.builds == 1, "the forced call must rebuild from the filings"
    venturo = fresh.ownership_detail.insider_holdings.insiders[0]
    assert [(h.shares, h.as_of) for h in venturo.holdings if h.held == "direct"] == [
        (302526, "2026-09-30")]
    assert fresh.ownership_detail.built_at > stale.ownership_detail.built_at


@pytest.mark.asyncio
async def test_f3_the_tool_never_takes_a_refresh_that_is_not_newer_as_fresh(monkeypatch):
    """F3, the tool's half: whatever a forced refresh hands back, a build no newer than the
    one the probe proved stale is not fresh — its figures predate the filing."""
    stale_at = _iso(10)
    cached = _stamped(_PRE_SALE, built_at=stale_at)
    same_age = _stamped(_venturo_owner(), built_at=stale_at)
    fake = _Fresh(cached, same_age, newer=True)
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    out = await cot.fetch_ownership("CRWV")
    assert "a newer insider filing exists but could not be loaded" in out.get("freshness", "")
    assert "241,371" in _first_holding(out)


@pytest.mark.asyncio
async def test_f4_a_follow_up_after_a_partly_degraded_rebuild_does_not_go_backwards(monkeypatch):
    """F4: the forced rebuild read the new Form 4 but an unrelated source (the Senate feed)
    failed, so holders kept it out of both tiers. The next question, seconds later, got the
    OLD build plus "a newer insider filing exists but could not be loaded"."""
    fmp = _LiveFMP(_venturo_rows(), senate=RuntimeError("senate 503"))
    await _live(monkeypatch, fmp)
    first = await cot.fetch_ownership("CRWV")
    assert _first_holding(first).startswith("Class A Common Stock held directly: 302,526 shares")
    assert "freshness" not in first
    again = await cot.fetch_ownership("CRWV")
    assert _first_holding(again) == _first_holding(first)
    assert "freshness" not in again
    assert (fmp.probes, fmp.builds) == (1, 1)


@pytest.mark.asyncio
async def test_f4_the_rebuild_is_not_repeated_every_probe_window(monkeypatch):
    """F4: while the unrelated source kept failing, every question after the 5-minute probe
    window probed AND rebuilt the whole Holders payload again, then threw it away."""
    fmp = _LiveFMP(_venturo_rows(), senate=RuntimeError("senate 503"))
    await _live(monkeypatch, fmp)
    await cot.fetch_ownership("CRWV")
    when, *rest = cot._last_probe["CRWV"]
    cot._last_probe["CRWV"] = (when - 3600, *rest)      # the probe window has passed
    out = await cot.fetch_ownership("CRWV")
    assert "302,526" in _first_holding(out) and "freshness" not in out
    assert (fmp.probes, fmp.builds) == (1, 1)


@pytest.mark.asyncio
async def test_f4_concurrent_first_questions_share_one_probe(monkeypatch):
    """F4: nothing deduped between reading the probe memory and writing it — five people
    asking about one ticker at once made five probe calls."""
    gate = asyncio.Event()
    fmp = _LiveFMP(_venturo_rows(), probe_gate=gate)
    await _live(monkeypatch, fmp)
    tasks = [asyncio.create_task(cot.fetch_ownership("CRWV")) for _ in range(5)]
    await asyncio.sleep(0.05)
    gate.set()
    outs = await asyncio.gather(*tasks)
    assert fmp.probes == 1 and fmp.builds == 1
    assert all("302,526" in _first_holding(o) and "freshness" not in o for o in outs)


@pytest.mark.asyncio
async def test_f4_a_question_during_the_rebuild_waits_for_it(monkeypatch):
    """F4: a question arriving while the forced rebuild was still running replayed the probe's
    "newer" as "could not be loaded" over the OLD figures instead of waiting for it."""
    gate = asyncio.Event()
    fmp = _LiveFMP(_venturo_rows(), build_gate=gate)
    await _live(monkeypatch, fmp)
    first = asyncio.create_task(cot.fetch_ownership("CRWV"))
    await asyncio.wait_for(fmp.build_started.wait(), 2)
    later = [asyncio.create_task(cot.fetch_ownership("CRWV")) for _ in range(4)]
    await asyncio.sleep(0.05)
    gate.set()
    outs = await asyncio.gather(first, *later)
    assert all("302,526" in _first_holding(o) and "freshness" not in o for o in outs), [
        (_first_holding(o), o.get("freshness")) for o in outs]
    assert (fmp.probes, fmp.builds) == (1, 1)


def _today(days=0):
    from datetime import timedelta
    return (datetime.now(timezone.utc).date() + timedelta(days=days)).isoformat()


def _corrupt(filed):
    """A row whose filing date no EDGAR filing can carry."""
    return _row("2026-09-29", filed, shares=5, owned=302521)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["2062-08-21", "2026-99-99"])
async def test_f6_an_impossible_filing_date_never_becomes_the_freshness_marker(bad):
    """F6: one row filed '2062-…' (or on a day that does not exist) pinned the build's newest
    filing in the future, and every later probe answered 'nothing newer'."""
    built = _wired(_FMP([_corrupt(bad)] + _venturo_rows(), summary={"cik": CRWV_CIK}))
    resp, _ = await built.get_holders_with_status("CRWV")
    detail = resp.ownership_detail
    assert detail.newest_filed == "2026-10-02"
    assert len(detail.newest_filed_ids) == 4
    new_form4 = _row("2026-10-05", _today(), shares=10, owned=302516)
    probe = _wired(_ProbeFMP([_corrupt(bad), new_form4] + _venturo_rows()))
    assert await probe.newer_insider_filing("CRWV", detail) is True
    unchanged = _wired(_ProbeFMP([_corrupt(bad)] + _venturo_rows()))
    assert await unchanged.newer_insider_filing("CRWV", detail) is False, (
        "the corrupt row itself must not read as a new filing on every probe")


@pytest.mark.asyncio
async def test_f6_a_marker_stored_before_the_bound_heals_on_the_next_probe():
    """A row persisted with a future marker cannot be compared against: the probe says
    'newer' (one rebuild, whose marker is bounded) instead of staying blind for a day."""
    detail = OwnershipDetailSchema(insider_holdings=InsiderHoldingsSchema(),
                                   newest_filed="2062-08-21", newest_filed_ids=["x"])
    probe = _wired(_ProbeFMP(_venturo_rows()))
    assert await probe.newer_insider_filing("CRWV", detail) is True


# ── Final review hardening: every degraded path of the freshness check ──────────────────

def _incomplete(resp):
    detail = resp.ownership_detail
    holdings = detail.insider_holdings.model_copy(update={"complete": False})
    return resp.model_copy(update={"ownership_detail": detail.model_copy(
        update={"insider_holdings": holdings})})


@pytest.mark.asyncio
async def test_hardening_a_remembered_answer_is_replayed_only_for_the_read_it_was_about(monkeypatch):
    import time

    fake = _Fresh(_stamped(_PRE_SALE, built_at=_iso(10)), newer=False)
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    cot._last_probe["CRWV"] = (time.monotonic(), True, _iso(20))   # about ANOTHER read
    out = await cot.fetch_ownership("CRWV")
    assert fake.probes == 1 and "freshness" not in out


@pytest.mark.asyncio
async def test_hardening_a_rebuild_that_lost_a_page_is_retried_after_the_probe_horizon(monkeypatch):
    """A forced read with people possibly missing (a lost page) is kept only until the next
    probe — not for a day — so a later question retries the rebuild."""
    old_at = _iso(10)
    partial = _incomplete(_stamped(_venturo_owner(), built_at=_iso(0)))
    fake = _Fresh(_stamped(_PRE_SALE, built_at=old_at), partial, fresh_degraded=["Insider trading"])
    asked = []

    async def probe(ticker, detail):
        # Like the real probe: only the OLD build predates the new Form 4 — the partial read
        # holds the newest filing, so a probe of IT says "nothing newer".
        asked.append(detail.built_at)
        return detail.built_at == old_at

    fake.newer_insider_filing = probe
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    first = await cot.fetch_ownership("CRWV")
    assert "302,526" in _first_holding(first) and first["insiders"]["complete"] is False
    cot._fresh_reads["CRWV"] = cot._fresh_reads["CRWV"].model_copy(update={"built_at": _iso(31 / 60)})
    when, *rest = cot._last_probe["CRWV"]
    cot._last_probe["CRWV"] = (when - 3600, *rest)
    await cot.fetch_ownership("CRWV")
    assert asked == [old_at, old_at], "the partial read expired: the old build is checked again"
    assert fake.calls.count(("CRWV", True)) == 2, "and the rebuild is retried"


@pytest.mark.asyncio
async def test_hardening_a_kept_read_is_probed_itself_once_it_is_old_enough(monkeypatch):
    fake = _Fresh(_stamped(_PRE_SALE, built_at=_iso(10)), _stamped(_venturo_owner(), built_at=_iso(0)),
                  newer=True)
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    await cot.fetch_ownership("CRWV")
    kept_at = _iso(31 / 60)
    cot._fresh_reads["CRWV"] = cot._fresh_reads["CRWV"].model_copy(update={"built_at": kept_at})
    asked = []

    async def probe(ticker, detail):
        asked.append(detail.built_at)
        return False

    fake.newer_insider_filing = probe
    out = await cot.fetch_ownership("CRWV")
    assert asked == [kept_at], "the newest read is the one checked, not the older Holders build"
    assert "302,526" in _first_holding(out) and "freshness" not in out
    assert fake.calls.count(("CRWV", True)) == 1


@pytest.mark.asyncio
async def test_hardening_the_kept_read_gives_way_once_the_holders_build_catches_up(monkeypatch):
    fake = _Fresh(_stamped(_PRE_SALE, built_at=_iso(10)), _stamped(_venturo_owner(), built_at=_iso(1)),
                  newer=True)
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    await cot.fetch_ownership("CRWV")
    assert "CRWV" in cot._fresh_reads
    fake.cached = _stamped(_venturo_owner(latest_trades=[]), built_at=_iso(0))
    out = await cot.fetch_ownership("CRWV")
    assert "CRWV" not in cot._fresh_reads
    assert out["insiders"]["people"][0]["latest_transaction"] == "2026-09-30 (filed 2026-10-02)"


@pytest.mark.asyncio
async def test_hardening_a_holders_build_without_insiders_still_answers_from_the_kept_read(monkeypatch):
    fake = _Fresh(_stamped(_PRE_SALE, built_at=_iso(10)), _stamped(_venturo_owner(), built_at=_iso(0)),
                  newer=True)
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    await cot.fetch_ownership("CRWV")
    fake.cached = _response(holdings=None)       # the next Holders read could not attribute its rows
    out = await cot.fetch_ownership("CRWV")
    assert out["insiders"]["available"] is True and "302,526" in _first_holding(out)


@pytest.mark.asyncio
async def test_hardening_a_rebuild_for_insiders_never_trades_away_good_institutional_figures(monkeypatch):
    blank = _breakdown(institutions_unknown=True, institutions_percent=0.0,
                       public_other_percent=0.0, top_10_owners=Top10OwnersSchema())
    fresh = _stamped(_venturo_owner(), built_at=_iso(0)).model_copy(
        update={"shareholder_breakdown": blank})
    fake = _Fresh(_stamped(_PRE_SALE, built_at=_iso(10)), fresh, newer=True,
                  fresh_degraded=["Institutional holders", "Inst ownership summary"])
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    out = await cot.fetch_ownership("CRWV")
    assert "302,526" in _first_holding(out)
    inst = out["institutions"]
    assert inst["institutions_percent"] == 38.55
    assert inst["largest_institutions"][0].startswith("Vanguard Group Inc")


@pytest.mark.asyncio
async def test_hardening_a_cancelled_check_never_strands_the_questions_waiting_on_it(monkeypatch):
    gate = asyncio.Event()

    class _Slow(_Fresh):
        async def newer_insider_filing(self, ticker, detail):
            self.probes += 1
            await gate.wait()
            return True

    fake = _Slow(_stamped(_PRE_SALE, built_at=_iso(10)))
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    leader = asyncio.create_task(cot.fetch_ownership("CRWV"))
    await asyncio.sleep(0.05)
    follower = asyncio.create_task(cot.fetch_ownership("CRWV"))
    await asyncio.sleep(0.05)
    assert fake.probes == 1 and "CRWV" in cot._probe_inflight, "the follower waits on the leader"
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    out = await asyncio.wait_for(follower, 2)
    assert "could not check for newer filings" in out["freshness"]
    assert "241,371" in _first_holding(out) and fake.probes == 1
    assert cot._probe_inflight == {}


@pytest.mark.asyncio
async def test_hardening_a_check_that_raises_unexpectedly_still_answers(monkeypatch, caplog):
    fake = _Fresh(_stamped(_PRE_SALE, built_at=_iso(10)), newer=True)

    async def broken(*a, **k):
        raise AttributeError("a defect, not an upstream failure")

    monkeypatch.setattr(cot, "_check_and_rebuild", broken)
    monkeypatch.setattr(hs, "get_holders_service", lambda: fake)
    out = await cot.fetch_ownership("CRWV")
    assert "could not check for newer filings" in out["freshness"]
    assert "241,371" in _first_holding(out)
    assert "freshness check failed for CRWV" in caplog.text and cot._probe_inflight == {}


@pytest.mark.asyncio
async def test_hardening_concurrent_forced_rebuilds_share_one_build(monkeypatch):
    gate = asyncio.Event()
    fmp = _LiveFMP(_venturo_rows(), build_gate=gate)
    svc = await _live(monkeypatch, fmp)
    first = asyncio.create_task(svc.get_holders_with_status("CRWV", force_refresh=True))
    await asyncio.wait_for(fmp.build_started.wait(), 2)
    second = asyncio.create_task(svc.get_holders_with_status("CRWV", force_refresh=True))
    await asyncio.sleep(0.02)
    gate.set()
    (a, _), (b, _) = await asyncio.gather(first, second)
    assert fmp.builds == 1 and a is b
    assert hs._inflight == {}


def test_hardening_the_filing_date_bound_is_today_plus_one():
    latest = hs._latest_filing_day()
    assert hs._filed_day10({"filingDate": _today(1) + " 21:05:00"}, latest) == _today(1)
    assert hs._filed_day10({"filingDate": _today()}, latest) == _today()
    for junk in (_today(2), None, "", 20261002, "2026-02-30", "02/10/2026", "2026-10"):
        assert hs._filed_day10({"filingDate": junk}, latest) == "", junk
    assert hs._insider_feed_marker([]) == (None, [])
    assert hs._insider_feed_marker([{"filingDate": "2062-01-01"}, "junk", None]) == (None, [])


@pytest.mark.asyncio
async def test_hardening_impossible_filing_dates_are_logged_never_dropped_silently(caplog):
    marker = hs._insider_feed_marker([_corrupt("2062-08-21")] + _venturo_rows(), ticker="CRWV")
    assert marker[0] == "2026-10-02"
    assert "[holders-insider-marker] CRWV: 1 insider row(s) carry an impossible filing date" in caplog.text
    built = _wired(_FMP(_venturo_rows(), summary={"cik": CRWV_CIK}))
    detail = (await built.get_holders_with_status("CRWV"))[0].ownership_detail
    probe = _wired(_ProbeFMP([_corrupt("2026-99-99"), {"filingDate": None}] + _venturo_rows()))
    assert await probe.newer_insider_filing("CRWV", detail) is False
    assert "[holders-insider-probe] CRWV: 1 probed row(s) carry an impossible filing date" in caplog.text


@pytest.mark.asyncio
async def test_hardening_a_balance_several_lines_closed_on_reaches_the_model_as_possibly_several(monkeypatch):
    """End to end (the schema must carry `same_balance_lines`, or validation drops it): one
    trust's filer prints the end-of-day balance on both fill lines."""
    rows = [
        _row("2026-09-15", "2026-09-17", shares=6000, owned=490000, own="I", price=50.0,
             security="Class B Common Stock"),
        _row("2026-09-15", "2026-09-17", shares=4000, owned=490000, own="I", price=50.0,
             security="Class B Common Stock"),
    ]
    svc = _wired(_FMP(rows, summary={"cik": CRWV_CIK}))
    monkeypatch.setattr(hs, "get_holders_service", lambda: svc)
    out = await cot.fetch_ownership("CRWV")
    assert _first_holding(out) == (
        "Class B Common Stock held indirectly: 490,000 shares as of 2026-09-15 (2 filing lines "
        "that day ended on this same balance: it may be one holding or up to 2 holdings of this "
        "size)")


@pytest.mark.asyncio
async def test_hardening_holdings_left_out_by_a_cap_are_counted_as_holdings(monkeypatch):
    owner = _venturo_owner(holdings_not_shown=3)
    monkeypatch.setattr(hs, "get_holders_service",
                        lambda: _Holders(_response(holdings=InsiderHoldingsSchema(insiders=[owner]))))
    person = (await cot.fetch_ownership("CRWV"))["insiders"]["people"][0]
    assert person["more_holdings_not_shown"] == 3
    assert "more_indirect_holdings_not_shown" not in person


def test_hardening_the_kept_reads_are_bounded():
    for i in range(cot._FRESH_MEMORY + 6):
        cot._remember_read(f"T{i}", OwnershipDetailSchema(built_at=_iso(0)))
    assert len(cot._fresh_reads) == cot._FRESH_MEMORY
    assert "T0" not in cot._fresh_reads and f"T{cot._FRESH_MEMORY + 5}" in cot._fresh_reads
