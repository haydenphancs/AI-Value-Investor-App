"""Trillion-Dollar Club Bets — the read path (`app/services/trillion_club_service.py`).

Hermetic: an in-memory fake Supabase stands in for the three club tables + `whales`, and
`sb_exec` runs the fake synchronously (or parks on a gate, for the concurrency tests). No
FMP, no network.

What is pinned, and why each matters:
  * caching — 10-min TTL, one build for N concurrent callers, joiners shielded, a cancelled
    leader never strands a joiner, a read error is NEVER cached as an empty section;
  * the guard — never raises, serves the last good group, hides the section rather than
    serve a day-old picture;
  * what is shown — unpublished companies and secondary / malformed / banned-word stakes
    never reach the wire; force_in / force_out win at read time; stale membership (> 7 days)
    hides the whole section; the club tag is computed from the CURRENT registry;
  * the gate — a locked detail leaks no holding outside the top 3 + changed rows in ANY
    field, and never mutates the shared cached object;
  * the endpoint + the Home branch — the contract codes, and that neither can raise.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse

import app.services.trillion_club_service as tcs
from app.config import settings
from app.schemas.trillion_club import (
    ClubChangeResponse,
    ClubHistoryPointResponse,
    ClubHoldingResponse,
    ClubMemberBriefResponse,
    ClubStakeResponse,
    TrillionClubCompanyResponse,
    TrillionClubDetailResponse,
    TrillionClubGroupResponse,
)
from app.services.trillion_club_service import (
    REDACTION_POLICY,
    TrillionClubBuildCancelled,
    TrillionClubService,
    contains_banned_copy,
    redact_trillion_club_detail,
    stake_problem,
)

TODAY = date(2026, 9, 24)
NOW = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
CHECKED = "2026-09-24T11:00:05.123456+00:00"

NVDA_CIK = "0001045810"
GOOGL_CIK = "0001652044"
BRK_CIK = "0001067983"
AMD_CIK = "0000002488"


# ── fake Supabase ────────────────────────────────────────────────────────────────────


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.filters = []
        self._order = None
        self._limit = None
        self._cols = "*"

    def select(self, cols="*"):
        self._cols = cols
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def in_(self, col, vals):
        vals = list(vals)
        # PostgREST renders `in.()` for an empty list — a 400 in production. The service
        # must short-circuit instead of sending it.
        assert vals, f"empty in_() on {self.table}.{col}"
        self.filters.append(lambda r, c=col, v=vals: r.get(c) in v)
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        self.db.reads[self.table] = self.db.reads.get(self.table, 0) + 1
        failure = self.db.fail.get(self.table)
        if failure is not None:
            raise failure
        rows = [copy.deepcopy(r) for r in self.db.tables.get(self.table, []) if all(f(r) for f in self.filters)]
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: (r.get(col) is None, r.get(col) or 0), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        if self._cols != "*":
            keep = [c.strip() for c in self._cols.split(",")]
            rows = [{k: r.get(k) for k in keep} for r in rows]
        return SimpleNamespace(data=rows)


class _FakeDB:
    def __init__(self, tables):
        self.tables = tables
        self.reads = {}
        self.fail = {}

    def table(self, name):
        return _Query(self, name)

    @property
    def total_reads(self):
        return sum(self.reads.values())


# ── fixture data (a small, realistic club) ───────────────────────────────────────────


def _company(slug, name, *, card_kind="no_thirteen_f", cap=None, symbol=None, aliases=(),
             ciks=(), use_13f=False, cap_source="fmp_us", mode="auto", is_member=True,
             published=True, checked=CHECKED, link_whale=False, manual_cap=None, **extra):
    row = {
        "slug": slug, "display_name": name, "ciks": list(ciks), "card_kind": card_kind,
        "use_13f": use_13f, "cap_symbol": symbol, "symbol_aliases": list(aliases),
        "detail_symbol": symbol, "logo_symbol": symbol, "home_country": "US",
        "cap_source": cap_source, "manual_cap_usd": manual_cap,
        "manual_cap_as_of": "2026-09-19" if manual_cap else None,
        "membership_mode": mode, "is_member": is_member, "last_market_cap": cap,
        "last_cap_date": "2026-09-23" if cap else None, "membership_checked_at": checked,
        "link_whale": link_whale, "published": published, "reviewed_on": "2026-09-20",
    }
    row.update(extra)
    return row


def _stake(company, investee, *, kind="private", material=True, symbol=None, pct=None,
           value=None, basis=None, verified="2026-09-01", sort=0, **extra):
    row = {
        "id": f"{company}-{investee}", "company_slug": company, "kind": kind,
        "investee_name": investee, "investee_cusip": None, "investee_us_symbol": symbol,
        "local_listing": None, "ownership_pct": pct, "ownership_basis": None,
        "disclosed_value_usd": value, "value_basis": basis, "as_of": "2026-06-30",
        "source_title": f"{company} 10-K (FY2026)", "source_url": "https://www.sec.gov/x",
        "source_confidence": "primary", "material": material, "tied_to_deal": False,
        "listed_since": None, "background": None, "verified_on": verified,
        "published": True, "sort_order": sort,
    }
    row.update(extra)
    return row


def _holding(cusip, symbol, name, value, weight, *, routable=True, is_small=None):
    return {
        "cusip": cusip, "symbol": symbol, "name": name, "title_of_class": "COM",
        "shares": value / 100.0, "value": value, "weight": weight,
        "is_small": weight < 0.01 if is_small is None else is_small, "sector": "Technology",
        "ipo_date": None, "exchange": "NASDAQ", "routable": routable,
    }


def _nvda_q2():
    holdings = [
        _holding("458140100", "INTC", "Intel Corp", 29.99e9, 0.4727),
        _holding("84615Q103", "SPCX", "Space Exploration Technologies", 20.9e9, 0.33),
        _holding("21873S108", "CRWV", "CoreWeave Inc", 4.4e9, 0.07),
        _holding("ZZDA00001", "ZZDA", "Zedd Alpha Corp", 3.0e9, 0.047),
        _holding("ZZUA00001", "ZZUA", "Umbra Unchanged A", 2.5e9, 0.039),
        _holding("007903107", "AMD", "Advanced Micro Devices", 1.5e9, 0.024),
        _holding("ZZUB00001", "ZZUB", "Umbra Unchanged B", 0.9e9, 0.014),
        _holding("29765A101", None, "Ethos Technologies", 0.24e9, 0.004, routable=False),
    ]
    changes = {
        "comparison": "quarter", "prev_period": "2026-Q1",
        "counts": {"newly_reported": 1, "increased": 0, "decreased": 1,
                   "no_longer_reported": 1, "unchanged": 5, "corporate_action": 0},
        "rows": [
            {"cusip": "84615Q103", "symbol": "SPCX", "name": "Space Exploration Technologies",
             "change": "newly_reported", "newly_listed": True, "shares": 2.09e8,
             "prev_shares": None, "share_change": None, "value": 20.9e9, "weight": 0.33},
            {"cusip": "ZZDA00001", "symbol": "ZZDA", "name": "Zedd Alpha Corp",
             "change": "decreased", "newly_listed": False, "shares": 3.0e7,
             "prev_shares": 4.0e7, "share_change": -1.0e7, "value": 3.0e9, "weight": 0.047},
            {"cusip": "ZZGN00001", "symbol": "ZZGN", "name": "Gone Holdings Inc",
             "change": "no_longer_reported", "newly_listed": False, "shares": None,
             "prev_shares": 5.0e6, "share_change": None, "value": None, "weight": None},
        ],
    }
    return {
        "cik": NVDA_CIK, "period": "2026-Q2", "period_end": "2026-06-30",
        "filed_on": "2026-08-14", "amended_on": None, "accessions": ["0001045810-26-000065"],
        "total_value": 63.44e9, "position_count": 8, "holdings": holdings, "changes": changes,
        "excluded_rows": 0, "unresolved": {}, "raw_hash": "h1", "build_status": "complete",
        "source": "fmp", "built_at": "2026-09-24T11:00:00+00:00",
    }


def _history_row(cik, period, end, total, count):
    return {
        "cik": cik, "period": period, "period_end": end, "filed_on": end, "amended_on": None,
        "accessions": ["a"], "total_value": total, "position_count": count, "holdings": [],
        "changes": {"comparison": "first_filing", "prev_period": None, "counts": {}, "rows": []},
        "excluded_rows": 0, "unresolved": {}, "raw_hash": "x", "build_status": "complete",
        "source": "fmp",
    }


def _googl_q2():
    return {
        "cik": GOOGL_CIK, "period": "2026-Q2", "period_end": "2026-06-30",
        "filed_on": "2026-08-07", "amended_on": "2026-09-01",
        "accessions": ["0001652044-26-000001", "0001652044-26-000002"],
        "total_value": 99.08e9, "position_count": 3,
        "holdings": [
            _holding("84615Q103", "SPCX", "Space Exploration Technologies", 94.18e9, 0.9505),
            _holding("084670702", "BRK.B", "Berkshire Hathaway Inc", 3.0e9, 0.0303),
            _holding("29765A101", "LIFE", "Ethos Technologies", 1.9e9, 0.0192),
        ],
        "changes": {"comparison": "quarter", "prev_period": "2026-Q1",
                    "counts": {"newly_reported": 1, "unchanged": 2}, "rows": [
                        {"cusip": "84615Q103", "symbol": "SPCX", "name": "Space Exploration Technologies",
                         "change": "newly_reported", "newly_listed": True, "shares": 5.5e8,
                         "value": 94.18e9, "weight": 0.9505}]},
        "excluded_rows": 0, "unresolved": {}, "raw_hash": "g1", "build_status": "complete",
        "source": "fmp",
    }


def _tables():
    return {
        "trillion_club_companies": [
            _company("nvidia", "NVIDIA", card_kind="thirteen_f", cap=4.5e12, symbol="NVDA",
                     ciks=[NVDA_CIK], use_13f=True),
            _company("microsoft", "Microsoft", cap=3.8e12, symbol="MSFT", ciks=["0000789019"]),
            _company("alphabet", "Alphabet", card_kind="thirteen_f", cap=3.0e12, symbol="GOOGL",
                     aliases=["GOOG"], ciks=[GOOGL_CIK], use_13f=True),
            _company("broadcom", "Broadcom", cap=1.6e12, symbol="AVGO"),
            _company("tsmc", "TSMC", card_kind="non_us", cap=1.5e12, symbol="TSM", cap_source="fmp_adr"),
            _company("tesla", "Tesla", cap=1.4e12, symbol="TSLA"),
            _company("samsung", "Samsung Electronics", card_kind="non_us", cap_source="manual",
                     manual_cap=1.37e12, mode="force_in", is_member=False, symbol=None,
                     checked=None, manual_cap_source_url="https://example.com/cap"),
            _company("spacex", "SpaceX", cap=1.2e12, symbol="SPCX"),
            _company("berkshire", "Berkshire Hathaway", card_kind="whale_link", cap=1.05e12,
                     symbol="BRK-B", aliases=["BRK-A"], ciks=[BRK_CIK], link_whale=True),
            _company("micron", "Micron", cap=1.01e12, symbol="MU"),
            # Not members: a force_out (with a stale is_member=True), an auto non-member,
            # and an unpublished watch-list row.
            _company("sk-hynix", "SK hynix", card_kind="non_us", cap_source="manual",
                     manual_cap=0.99e12, mode="force_out", is_member=True, symbol=None,
                     checked=None, manual_cap_source_url="https://example.com/cap"),
            _company("amd", "AMD", card_kind="thirteen_f", cap=1.0e12, symbol="AMD",
                     ciks=[AMD_CIK], use_13f=True, is_member=False),
            _company("jpmorgan", "JPMorgan Chase", cap=0.9e12, symbol="JPM", published=False),
        ],
        "trillion_club_stakes": [
            _stake("microsoft", "OpenAI Group PBC", pct=25.0, sort=1),
            _stake("microsoft", "G42", value=1.5e9, basis="invested", sort=2),
            _stake("microsoft", "Anthropic", kind="commitment", material=False,
                   value=5e9, basis="committed_up_to", sort=3),
            _stake("nvidia", "Intel", kind="on_13f_note", symbol="INTC", tied_to_deal=True),
            _stake("tsmc", "Vanguard International Semiconductor", kind="non_us_listed", pct=27.6,
                   local_listing="Taiwan (5347.TWO)"),
            _stake("tesla", "SpaceX", value=2.0e9, basis="invested", symbol="SPCX"),
            _stake("samsung", "Corning Inc", kind="us_listed_off_13f", pct=7.9, symbol="GLW"),
            _stake("berkshire", "Mitsubishi Corp", kind="non_us_listed", pct=10.8,
                   verified="2026-03-01", local_listing="Tokyo (8058.T)"),
            _stake("micron", "Anthropic", material=False),
            # Must never reach the wire: news-only, unpublished, and a non-member's stake.
            _stake("alphabet", "Anthropic", pct=14.0, source_confidence="secondary", published=False),
            _stake("alphabet", "Unpublished Co", published=False),
            _stake("sk-hynix", "Some Stake", pct=5.0),
        ],
        "trillion_club_filings": [
            _nvda_q2(),
            _history_row(NVDA_CIK, "2026-Q1", "2026-03-31", 50e9, 7),
            _history_row(NVDA_CIK, "2025-Q4", "2025-12-31", 40e9, 6),
            _googl_q2(),
            _history_row(AMD_CIK, "2026-Q2", "2026-06-30", 5e9, 4),
        ],
        "whales": [{"id": "whale-brk-uuid", "cik": BRK_CIK, "name": "Warren Buffett"}],
    }


# ── harness ──────────────────────────────────────────────────────────────────────────


class _Gate:
    """Parks every sb_exec call until opened — the window a concurrency test needs."""

    def __init__(self):
        self.open = asyncio.Event()
        self.entered = asyncio.Event()
        self.enabled = False
        self.calls = 0


@pytest.fixture
def db(monkeypatch):
    fake = _FakeDB(_tables())
    gate = _Gate()

    async def _sb_exec(query):
        if gate.enabled:
            gate.calls += 1
            gate.entered.set()
            await gate.open.wait()
        return query.execute()

    monkeypatch.setattr(tcs, "get_supabase", lambda: fake)
    monkeypatch.setattr(tcs, "sb_exec", _sb_exec)
    monkeypatch.setattr(tcs, "_today_et", lambda: TODAY)
    monkeypatch.setattr(tcs, "_now_utc", lambda: NOW)
    monkeypatch.setattr(settings, "TRILLION_CLUB_ENABLED", True)
    TrillionClubService._group_cache.clear()
    TrillionClubService._detail_cache.clear()
    TrillionClubService._inflight.clear()
    TrillionClubService._invalidated_at = 0.0
    fake.gate = gate
    yield fake
    TrillionClubService._group_cache.clear()
    TrillionClubService._detail_cache.clear()
    TrillionClubService._inflight.clear()
    TrillionClubService._invalidated_at = 0.0


def _svc():
    return TrillionClubService()


def _slugs(group):
    return [c.slug for c in group.companies]


def _card(group, slug):
    return next(c for c in group.companies if c.slug == slug)


# ── the group: what is shown, in what order ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_group_orders_cards_by_market_cap_and_lists_the_rest(db):
    group = await _svc().get_group()
    # Largest first; samsung's owner-entered cap sorts with the rest.
    assert _slugs(group) == ["nvidia", "microsoft", "alphabet", "tsmc", "tesla", "samsung", "berkshire"]
    # Members with nothing material to show: named, never carded, same order rule.
    assert [(m.slug, m.name) for m in group.also_in_club] == [
        ("broadcom", "Broadcom"), ("spacex", "SpaceX"), ("micron", "Micron"),
    ]
    TrillionClubGroupResponse.model_validate(group.model_dump())


@pytest.mark.asyncio
async def test_non_members_unpublished_and_force_out_never_appear(db):
    group = await _svc().get_group()
    everything = _slugs(group) + [m.slug for m in group.also_in_club]
    assert "jpmorgan" not in everything          # published = false
    assert "sk-hynix" not in everything          # force_out beats a stale is_member=true
    assert "amd" not in everything               # auto, not (yet) a member
    assert "samsung" in everything               # force_in beats is_member=false
    assert "Some Stake" not in group.model_dump_json()


@pytest.mark.asyncio
async def test_a_thirteen_f_card_carries_its_quarter(db):
    card = _card(await _svc().get_group(), "nvidia")
    assert card.card_kind == "thirteen_f"
    assert (card.period, card.period_end, card.filed_on) == ("2026-Q2", "2026-06-30", "2026-08-14")
    assert card.next_due == "2026-11-16"         # Q3 2026: Nov 14 is a Saturday
    assert card.position_count == 8 and card.total_value == 63.44e9
    assert [h.symbol for h in card.top_holdings] == ["INTC", "SPCX", "CRWV"]
    spcx = card.top_holdings[1]
    assert spcx.change == "newly_reported" and spcx.newly_listed is True
    assert card.top_holdings[0].change == "unchanged"
    assert card.change_counts.newly_reported == 1 and card.change_counts.unchanged == 5
    assert (card.comparison, card.prev_period, card.notice) == ("quarter", "2026-Q1", None)
    assert card.market_cap == 4.5e12 and card.market_cap_as_of == "2026-09-23"
    # The Home card lists material stakes but never an on_13f_note (NVIDIA's only material
    # stake here, Intel): a note is about one 13F holding and lives in the detail beside it.
    assert card.stakes == [] and card.stake_count == 1


@pytest.mark.asyncio
async def test_card_stakes_are_material_only_and_ordered(db):
    card = _card(await _svc().get_group(), "microsoft")
    assert card.card_kind == "no_thirteen_f" and card.period is None
    assert [s.investee_name for s in card.stakes] == ["OpenAI Group PBC", "G42"]
    openai = card.stakes[0]
    assert openai.ownership_pct == 25.0 and openai.source_url.startswith("https://")
    assert openai.is_stale is False


@pytest.mark.asyncio
async def test_manual_cap_is_flagged_and_dated(db):
    card = _card(await _svc().get_group(), "samsung")
    assert card.cap_is_manual is True
    assert card.market_cap == 1.37e12 and card.market_cap_as_of == "2026-09-19"


@pytest.mark.asyncio
async def test_whale_link_card_resolves_the_profile_by_cik(db):
    card = _card(await _svc().get_group(), "berkshire")
    assert card.card_kind == "whale_link" and card.whale_id == "whale-brk-uuid"
    assert card.period is None and card.top_holdings == []   # its 13F is never duplicated here
    # verified_on 2026-03-01 is 207 days before TODAY → flagged for re-checking.
    assert card.stakes[0].is_stale is True


@pytest.mark.asyncio
async def test_whale_link_with_no_whale_row_and_no_stakes_gets_no_card(db, caplog):
    db.tables["whales"] = []
    db.tables["trillion_club_stakes"] = [
        s for s in db.tables["trillion_club_stakes"] if s["company_slug"] != "berkshire"
    ]
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        group = await _svc().get_group()
    assert "berkshire" not in _slugs(group)
    assert "berkshire" in [m.slug for m in group.also_in_club]
    assert "no whales row has CIK 0001067983" in caplog.text


@pytest.mark.asyncio
async def test_an_unpadded_whale_cik_still_resolves(db):
    db.tables["whales"] = [{"id": "whale-brk-uuid", "cik": "1067983"}]
    assert _card(await _svc().get_group(), "berkshire").whale_id == "whale-brk-uuid"


@pytest.mark.asyncio
async def test_a_filer_without_a_stored_filing_but_with_a_material_stake_keeps_a_card(db):
    db.tables["trillion_club_filings"] = [
        f for f in db.tables["trillion_club_filings"] if f["cik"] != NVDA_CIK
    ]
    note = next(r for r in db.tables["trillion_club_stakes"]
                if r["company_slug"] == "nvidia" and r["investee_name"] == "Intel")
    db.tables["trillion_club_stakes"].append({
        **note, "id": "private-1", "kind": "private", "investee_name": "Private companies",
        "investee_cusip": None, "investee_us_symbol": None, "tied_to_deal": False})
    card = _card(await _svc().get_group(), "nvidia")
    assert card.card_kind == "thirteen_f" and card.period is None and card.top_holdings == []
    # The private stake earns the card; the Intel note never reaches a Home card.
    assert [s.investee_name for s in card.stakes] == ["Private companies"]


@pytest.mark.asyncio
async def test_a_filer_whose_only_material_stake_is_a_note_gets_no_card_without_a_filing(db):
    db.tables["trillion_club_filings"] = [
        f for f in db.tables["trillion_club_filings"] if f["cik"] != NVDA_CIK
    ]
    group = await _svc().get_group()
    assert "nvidia" not in [c.slug for c in group.companies]
    assert "nvidia" in [m.slug for m in group.also_in_club]


@pytest.mark.asyncio
async def test_a_member_with_nothing_to_show_is_listed_not_carded(db):
    db.tables["trillion_club_stakes"] = []
    db.tables["trillion_club_filings"] = []
    db.tables["whales"] = []
    group = await _svc().get_group()
    assert group.companies == []
    assert len(group.also_in_club) == 10


@pytest.mark.asyncio
async def test_missing_market_cap_sinks_to_the_end(db):
    for row in db.tables["trillion_club_companies"]:
        if row["slug"] == "nvidia":
            row["last_market_cap"] = None
    group = await _svc().get_group()
    assert _slugs(group)[-1] == "nvidia"
    assert _card(group, "nvidia").market_cap is None


# ── read-time club tag ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_club_tag_is_computed_from_the_current_registry(db):
    group = await _svc().get_group()
    nvda = _card(group, "nvidia")
    assert nvda.top_holdings[1].club_member_slug == "spacex"
    assert nvda.top_holdings[0].club_member_slug is None       # Intel is not a member
    googl = _card(group, "alphabet")
    # "BRK.B" on the 13F matches the registry's "BRK-B".
    brk = next(h for h in googl.top_holdings if h.name == "Berkshire Hathaway Inc")
    assert brk.club_member_slug == "berkshire"
    # A stake's routable symbol is tagged too.
    assert _card(group, "tesla").stakes[0].club_member_slug == "spacex"


@pytest.mark.asyncio
async def test_club_tag_flips_with_membership_and_no_rebuild(db):
    svc = _svc()
    detail = await svc.get_detail("nvidia")
    amd = next(h for h in detail.holdings if h.symbol == "AMD")
    assert amd.club_member_slug is None
    filings_before = copy.deepcopy(db.tables["trillion_club_filings"])

    # AMD joins (the daily job writes is_member) — the 13F snapshot is NOT rebuilt.
    for row in db.tables["trillion_club_companies"]:
        if row["slug"] == "amd":
            row["is_member"] = True
    tcs.invalidate()
    detail = await svc.get_detail("nvidia")
    amd = next(h for h in detail.holdings if h.symbol == "AMD")
    assert amd.club_member_slug == "amd"
    assert db.tables["trillion_club_filings"] == filings_before


@pytest.mark.asyncio
async def test_a_company_is_never_tagged_inside_its_own_card(db):
    db.tables["trillion_club_filings"][0]["holdings"].append(
        _holding("67066G104", "NVDA", "NVIDIA Corp", 0.1e9, 0.001)
    )
    detail = await _svc().get_detail("nvidia")
    assert next(h for h in detail.holdings if h.symbol == "NVDA").club_member_slug is None


# ── stale membership + the feature flag ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_membership_older_than_seven_days_hides_the_section(db, caplog):
    stale = (NOW - timedelta(days=7, minutes=1)).isoformat()
    for row in db.tables["trillion_club_companies"]:
        if row["cap_source"] != "manual":
            row["membership_checked_at"] = stale
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        group = await _svc().get_group()
    assert group == TrillionClubGroupResponse()
    assert "section hidden" in caplog.text and "more than 7 days" in caplog.text
    assert await _svc().get_detail("nvidia") is None


@pytest.mark.asyncio
async def test_one_fresh_fmp_company_keeps_the_section(db):
    for row in db.tables["trillion_club_companies"]:
        if row["cap_source"] != "manual":
            row["membership_checked_at"] = (NOW - timedelta(days=30)).isoformat()
    db.tables["trillion_club_companies"][0]["membership_checked_at"] = (NOW - timedelta(days=6)).isoformat()
    assert _slugs(await _svc().get_group())[0] == "nvidia"


@pytest.mark.asyncio
@pytest.mark.parametrize("checked", [None, "", "not a timestamp"])
async def test_never_checked_membership_hides_the_section(db, checked):
    for row in db.tables["trillion_club_companies"]:
        row["membership_checked_at"] = checked
    assert await _svc().get_group() == TrillionClubGroupResponse()


@pytest.mark.asyncio
async def test_hand_sized_companies_alone_cannot_vouch_for_membership(db):
    """The job only refreshes FMP-sized companies; a registry of hand-sized ones has no
    freshness signal at all, so it must not show."""
    db.tables["trillion_club_companies"] = [
        r for r in db.tables["trillion_club_companies"] if r["cap_source"] == "manual"
    ]
    assert await _svc().get_group() == TrillionClubGroupResponse()


@pytest.mark.asyncio
async def test_a_z_suffixed_timestamp_parses(db):
    for row in db.tables["trillion_club_companies"]:
        row["membership_checked_at"] = "2026-09-24T11:00:05Z"
    assert _slugs(await _svc().get_group())


@pytest.mark.asyncio
async def test_flag_off_means_no_reads_at_all(db, monkeypatch):
    monkeypatch.setattr(settings, "TRILLION_CLUB_ENABLED", False)
    svc = _svc()
    assert await svc.get_group() == TrillionClubGroupResponse()
    assert await svc.get_group_guarded() == TrillionClubGroupResponse()
    assert await svc.get_detail("nvidia") is None
    assert db.total_reads == 0


@pytest.mark.asyncio
async def test_flag_off_ignores_a_previously_cached_group(db, monkeypatch):
    svc = _svc()
    assert (await svc.get_group()).companies
    monkeypatch.setattr(settings, "TRILLION_CLUB_ENABLED", False)
    assert await svc.get_group_guarded() == TrillionClubGroupResponse()


# ── caching ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_group_is_cached_for_the_ttl(db):
    svc = _svc()
    first = await svc.get_group()
    reads = db.total_reads
    assert await svc.get_group() is first
    assert db.total_reads == reads, "a fresh cache hit must not touch Supabase"

    stored_at, group = TrillionClubService._group_cache[tcs._GROUP_KEY]
    TrillionClubService._group_cache[tcs._GROUP_KEY] = (stored_at - tcs._CACHE_TTL_SECONDS - 1, group)
    await svc.get_group()
    assert db.total_reads > reads, "an expired entry must rebuild"


@pytest.mark.asyncio
async def test_invalidate_forces_a_rebuild_but_keeps_the_fallback(db):
    svc = _svc()
    await svc.get_group()
    await svc.get_detail("nvidia")
    tcs.invalidate()
    assert TrillionClubService._detail_cache == {}
    assert tcs._GROUP_KEY in TrillionClubService._group_cache   # kept for the guard
    reads = db.total_reads
    await svc.get_group()
    assert db.total_reads > reads


@pytest.mark.asyncio
async def test_a_build_that_straddles_invalidate_is_not_served_as_fresh(db):
    """The jobs write, then invalidate. A build that READ before the write but finished
    after the invalidate holds pre-write data: it may stand in as the guard's fallback, but
    the next request must rebuild rather than serve it for ten minutes."""
    db.gate.enabled = True
    svc = _svc()
    build = asyncio.create_task(svc.get_group())
    await asyncio.wait_for(db.gate.entered.wait(), 2)
    tcs.invalidate()                       # the job wrote while this build was reading
    db.gate.open.set()
    await asyncio.wait_for(build, 2)
    db.gate.enabled = False
    reads = db.total_reads
    await svc.get_group()
    assert db.total_reads > reads


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", ["2026-09-24 11:00:05+00", "2026-09-24T11:00:05.123456789+00:00"])
async def test_postgres_timestamp_shapes_parse(db, stamp):
    for row in db.tables["trillion_club_companies"]:
        row["membership_checked_at"] = stamp
    assert _slugs(await _svc().get_group())


@pytest.mark.asyncio
async def test_a_read_error_raises_and_is_never_cached(db):
    svc = _svc()
    db.fail["trillion_club_stakes"] = RuntimeError("postgrest 503")
    with pytest.raises(RuntimeError, match="postgrest 503"):
        await svc.get_group()
    assert TrillionClubService._group_cache == {}
    assert TrillionClubService._inflight == {}

    db.fail.clear()
    assert _slugs(await svc.get_group())[0] == "nvidia"


@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["trillion_club_companies", "trillion_club_stakes",
                                   "trillion_club_filings", "whales"])
async def test_every_read_propagates_its_error(db, table):
    db.fail[table] = RuntimeError(f"{table} down")
    with pytest.raises(RuntimeError, match="down"):
        await _svc().get_group()
    assert TrillionClubService._group_cache == {}


@pytest.mark.asyncio
async def test_detail_read_error_raises_and_is_not_cached(db):
    db.fail["trillion_club_filings"] = RuntimeError("filings down")
    with pytest.raises(RuntimeError):
        await _svc().get_detail("nvidia")
    assert TrillionClubService._detail_cache == {}


@pytest.mark.asyncio
async def test_unknown_detail_is_not_cached(db):
    svc = _svc()
    assert await svc.get_detail("not-a-member") is None
    assert TrillionClubService._detail_cache == {}


# ── _inflight: dedup, shielded joins, cancellation ──────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_build(db):
    db.gate.enabled = True
    svc = _svc()
    tasks = [asyncio.create_task(svc.get_group()) for _ in range(5)]
    await asyncio.wait_for(db.gate.entered.wait(), 2)
    await asyncio.sleep(0.01)
    assert db.gate.calls == 1, "five callers must park on ONE build, not start five"
    db.gate.open.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), 2)
    assert all(r is results[0] for r in results)
    assert db.reads["trillion_club_companies"] == 1


@pytest.mark.asyncio
async def test_a_cancelled_joiner_does_not_break_the_leader_or_the_other_joiners(db):
    db.gate.enabled = True
    svc = _svc()
    leader = asyncio.create_task(svc.get_group())
    await asyncio.wait_for(db.gate.entered.wait(), 2)
    quitter = asyncio.create_task(svc.get_group())
    patient = asyncio.create_task(svc.get_group())
    await asyncio.sleep(0.01)
    quitter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await quitter
    db.gate.open.set()
    group = await asyncio.wait_for(leader, 2)
    assert _slugs(group)[0] == "nvidia"
    # Unshielded, the quitter's cancellation would have cancelled the SHARED future and
    # this caller — who never gave up — would get a CancelledError instead of the group.
    assert await asyncio.wait_for(patient, 2) is group


@pytest.mark.asyncio
async def test_a_cancelled_leader_never_strands_its_joiner(db):
    db.gate.enabled = True
    svc = _svc()
    leader = asyncio.create_task(svc.get_group())
    await asyncio.wait_for(db.gate.entered.wait(), 2)
    joiner = asyncio.create_task(svc.get_group())
    await asyncio.sleep(0.01)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    with pytest.raises(TrillionClubBuildCancelled):
        await asyncio.wait_for(joiner, 2)
    assert TrillionClubService._inflight == {}
    assert TrillionClubService._group_cache == {}


@pytest.mark.asyncio
async def test_guard_times_out_without_cancelling_the_shared_build(db, monkeypatch):
    monkeypatch.setattr(tcs, "_GROUP_TIMEOUT_SECONDS", 0.05)
    db.gate.enabled = True
    svc = _svc()
    assert await svc.get_group_guarded() == TrillionClubGroupResponse()   # no cache yet
    db.gate.open.set()
    for _ in range(100):
        if tcs._GROUP_KEY in TrillionClubService._group_cache:
            break
        await asyncio.sleep(0.01)
    # The shielded build finished in the background and warmed the cache.
    assert _slugs(TrillionClubService._group_cache[tcs._GROUP_KEY][1])[0] == "nvidia"


# ── the guard ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_guard_serves_the_last_good_group_on_a_read_error(db, caplog):
    svc = _svc()
    good = await svc.get_group()
    tcs.invalidate()
    db.fail["trillion_club_companies"] = RuntimeError("db down")
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        served = await svc.get_group_guarded()
    assert served is good
    assert "serving last good group" in caplog.text and "RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_guard_returns_empty_when_there_is_no_good_group(db):
    db.fail["trillion_club_companies"] = RuntimeError("db down")
    assert await _svc().get_group_guarded() == TrillionClubGroupResponse()


@pytest.mark.asyncio
async def test_guard_will_not_serve_a_day_old_group(db):
    svc = _svc()
    good = await svc.get_group()
    TrillionClubService._group_cache[tcs._GROUP_KEY] = (
        time.time() - tcs._GROUP_FALLBACK_MAX_AGE_SECONDS - 1, good,
    )
    db.fail["trillion_club_companies"] = RuntimeError("db down")
    assert await svc.get_group_guarded() == TrillionClubGroupResponse()


@pytest.mark.asyncio
async def test_guard_never_raises_even_on_a_parser_bug(db, monkeypatch):
    def _boom(_row):
        raise KeyError("unexpected")

    monkeypatch.setattr(tcs, "_parse_company", _boom)
    assert await _svc().get_group_guarded() == TrillionClubGroupResponse()


# ── stakes: the runtime validator ────────────────────────────────────────────────────


def _valid_stake(**over):
    row = _stake("microsoft", "OpenAI Group PBC", pct=25.0)
    row.update(over)
    return row


def test_a_valid_stake_passes():
    assert stake_problem(_valid_stake()) is None
    assert stake_problem(_valid_stake(kind="commitment", value=5e9, value_basis="committed_up_to",
                                      disclosed_value_usd=5e9)) is None


@pytest.mark.parametrize("over, reason", [
    ({"source_url": None}, "source_url"),
    ({"source_url": "http://www.sec.gov/x"}, "source_url"),
    ({"source_url": "https://www.sec.gov/ x"}, "source_url"),
    ({"source_title": "  "}, "source_title"),
    ({"as_of": None}, "as_of"),
    ({"as_of": "2026-13-40"}, "as_of"),
    ({"verified_on": "yesterday"}, "verified_on"),
    ({"listed_since": "June"}, "listed_since"),
    ({"source_confidence": "secondary"}, "primary"),
    ({"published": False}, "published"),
    ({"kind": "rumour"}, "kind"),
    ({"investee_name": ""}, "investee_name"),
    ({"investee_name": "x" * 61}, "investee_name"),
    ({"ownership_pct": float("nan")}, "ownership_pct"),
    ({"ownership_pct": 0}, "ownership_pct"),
    ({"ownership_pct": 100.5}, "ownership_pct"),
    ({"ownership_pct": True}, "ownership_pct"),
    ({"disclosed_value_usd": float("inf"), "value_basis": "invested"}, "disclosed_value_usd"),
    ({"disclosed_value_usd": -5.0, "value_basis": "invested"}, "disclosed_value_usd"),
    ({"disclosed_value_usd": 1e9, "value_basis": None}, "value_basis"),
    ({"value_basis": "worth"}, "value_basis"),
    ({"kind": "commitment", "value_basis": "carrying_value", "disclosed_value_usd": 1e9}, "commitment"),
    ({"background": "x" * 91}, "background"),
    ({"background": "NVIDIA-backed startup listed in June."}, "banned"),
    ({"background": "A conviction position since 2024."}, "banned"),
    ({"investee_name": "Smart Money Partners"}, "banned"),
    ({"ownership_basis": "Loaded up in Q2"}, "banned"),
])
def test_a_malformed_stake_is_refused(over, reason):
    problem = stake_problem(_valid_stake(**over))
    assert problem is not None and reason in problem, problem


@pytest.mark.parametrize("column", ["as_of", "verified_on"])
def test_a_future_date_is_refused_when_today_is_known(column):
    tomorrow = (TODAY + timedelta(days=1)).isoformat()
    later = (TODAY + timedelta(days=2)).isoformat()
    assert stake_problem(_valid_stake(**{column: tomorrow}), TODAY) is None   # ET/UTC slack
    problem = stake_problem(_valid_stake(**{column: later}), TODAY)
    assert problem == f"{column} is in the future"
    assert stake_problem(_valid_stake(**{column: later})) is None             # pure form: no clock


@pytest.mark.asyncio
async def test_a_refused_stake_is_dropped_loudly_and_the_rest_survive(db, caplog):
    for row in db.tables["trillion_club_stakes"]:
        if row["investee_name"] == "G42":
            row["source_url"] = None
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        card = _card(await _svc().get_group(), "microsoft")
    assert [s.investee_name for s in card.stakes] == ["OpenAI Group PBC"]
    assert "stake dropped (company=microsoft investee='G42'" in caplog.text


@pytest.mark.asyncio
async def test_a_secondary_row_is_refused_even_if_the_query_returned_it(db, monkeypatch):
    """The query filters `source_confidence = primary`; the validator is the second lock."""
    row = _stake("microsoft", "Rumoured Co", source_confidence="secondary")
    stake = tcs._parse_stake(row, TODAY)
    assert stake is None


@pytest.mark.parametrize("text", ["Alphabet", "Holdings", "Photonics", "Hotel Group",
                                  "Betamax", "follow-on offering", "Copyright notice",
                                  "Bet-free", None, 42])
def test_banned_copy_does_not_fire_on_ordinary_words(text):
    if text == "Bet-free":
        assert contains_banned_copy(text)          # "Bet" as a word IS banned outside the title
        return
    assert not contains_banned_copy(text)


@pytest.mark.parametrize("text", ["bullish", "BEARISH on chips", "loaded up", "smart money",
                                  "our top picks", "a vote of confidence", "an endorsement",
                                  "hot stock", "secret stake", "hidden gem", "NVIDIA-backed",
                                  "copy their trades", "follow this investor", "mirror the fund",
                                  "big bets"])
def test_banned_copy_fires_on_banned_wording(text):
    assert contains_banned_copy(text)


@pytest.mark.asyncio
async def test_stake_staleness_boundary(db):
    fresh = tcs._parse_stake(_valid_stake(verified_on=(TODAY - timedelta(days=120)).isoformat()), TODAY)
    stale = tcs._parse_stake(_valid_stake(verified_on=(TODAY - timedelta(days=121)).isoformat()), TODAY)
    assert fresh.response.is_stale is False and stale.response.is_stale is True


# ── holdings / changes parsing on malformed filings ─────────────────────────────────


@pytest.mark.asyncio
async def test_malformed_holdings_degrade_row_by_row(db, caplog):
    filing = db.tables["trillion_club_filings"][0]
    filing["holdings"] = [
        _holding("458140100", "INTC", "Intel Corp", 29.99e9, 0.4727),
        _holding("458140100", "INTC", "Intel Corp (dup)", 1.0, 0.0),          # duplicate CUSIP
        {"cusip": "BAD000001", "symbol": "NANV", "name": "Nan Value Co", "value": float("nan"),
         "weight": float("nan"), "shares": float("inf"), "routable": True},
        {"cusip": "BAD000002", "symbol": "HEVY", "name": "Heavy Co", "value": 1e9, "weight": 7.5,
         "routable": True},                                                   # weight > 1
        {"cusip": None, "symbol": None, "name": None},                        # nothing to show
        {"cusip": "BAD000003", "symbol": "nosym", "name": None, "value": 5e8,
         "weight": 0.008, "routable": False},                                 # name ← symbol
        "not a dict",
    ]
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        detail = await _svc().get_detail("nvidia")
    names = [h.name for h in detail.holdings]
    assert names.count("Intel Corp") == 1 and "Intel Corp (dup)" not in names
    nan = next(h for h in detail.holdings if h.name == "Nan Value Co")
    assert nan.value is None and nan.weight is None and nan.shares is None
    assert next(h for h in detail.holdings if h.name == "Heavy Co").weight is None
    nosym = next(h for h in detail.holdings if h.name == "NOSYM")
    assert nosym.symbol is None                     # not routable → no symbol on the wire
    assert "repeats holding 458140100" in caplog.text
    json.loads(detail.model_dump_json())            # never NaN on the wire


@pytest.mark.asyncio
@pytest.mark.parametrize("holdings, changes", [
    ("[{\"cusip\": \"458140100\", \"symbol\": \"INTC\", \"name\": \"Intel Corp\", \"value\": 1.0, \"weight\": 1.0, \"routable\": true}]",
     "{\"comparison\": \"first_filing\", \"rows\": []}"),
    ("not json", "not json"),
    ({"a": 1}, ["a list"]),
    (None, None),
])
async def test_jsonb_columns_of_the_wrong_shape_degrade(db, holdings, changes):
    filing = db.tables["trillion_club_filings"][0]
    filing["holdings"], filing["changes"] = holdings, changes
    detail = await _svc().get_detail("nvidia")
    assert detail is not None
    TrillionClubDetailResponse.model_validate(detail.model_dump())


@pytest.mark.asyncio
async def test_unknown_change_kinds_are_dropped_and_unchanged_never_sent(db):
    filing = db.tables["trillion_club_filings"][0]
    filing["changes"]["rows"] += [
        {"cusip": "ZZUA00001", "symbol": "ZZUA", "name": "Umbra Unchanged A", "change": "unchanged"},
        {"cusip": "ZZUB00001", "symbol": "ZZUB", "name": "Umbra Unchanged B", "change": "bought"},
    ]
    detail = await _svc().get_detail("nvidia")
    assert {c.change for c in detail.changes} == {"newly_reported", "decreased", "no_longer_reported"}
    assert [c.change for c in detail.changes] == ["newly_reported", "decreased", "no_longer_reported"]


@pytest.mark.asyncio
async def test_first_filing_has_no_changes_and_the_notice(db):
    filing = db.tables["trillion_club_filings"][0]
    filing["changes"] = {"comparison": "first_filing", "prev_period": None,
                         "counts": {k: 0 for k in ("newly_reported", "unchanged")}, "rows": []}
    detail = await _svc().get_detail("nvidia")
    assert detail.changes == []
    assert all(h.change is None for h in detail.holdings)
    assert detail.company.notice == "first_filing"
    assert detail.company.comparison == "first_filing"
    # Nothing was compared, so there are no counts (all-zero counts would be a claim).
    assert detail.company.change_counts is None


@pytest.mark.asyncio
@pytest.mark.parametrize("counts", [None, {}, "7", [1, 2]])
async def test_missing_counts_are_unknown_not_zero(db, counts, caplog):
    """"vs Q1: 0 newly reported" would be a claim about the filing; unknown is None."""
    db.tables["trillion_club_filings"][0]["changes"]["counts"] = counts
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        card = _card(await _svc().get_group(), "nvidia")
    assert card.change_counts is None
    assert card.comparison == "quarter"
    assert "no counts" in caplog.text


@pytest.mark.asyncio
async def test_bad_counts_become_zero(db):
    db.tables["trillion_club_filings"][0]["changes"]["counts"] = {
        "newly_reported": -3, "increased": "7", "decreased": 2.5, "unchanged": True,
    }
    counts = (await _svc().get_detail("nvidia")).company.change_counts
    assert counts.model_dump() == {k: 0 for k in counts.model_dump()}


# ── notices (real 13F calendar from app.services.trillion_club.rules) ───────────────


@pytest.mark.asyncio
async def test_amended_quarter_carries_the_notice(db):
    card = _card(await _svc().get_group(), "alphabet")
    assert card.notice == "amended" and card.amended_on == "2026-09-01"


@pytest.mark.asyncio
@pytest.mark.parametrize("today, notice", [
    (date(2026, 11, 20), None),                 # Q3 due Nov 16, still inside the grace
    (date(2026, 12, 1), "latest_not_in"),       # past due + 5 business days
    (date(2027, 3, 15), "no_newer_filing"),     # Q4 (Feb 16, 2027) missed as well
])
async def test_staleness_notices_follow_the_calendar(db, monkeypatch, today, notice):
    monkeypatch.setattr(tcs, "_today_et", lambda: today)
    card = _card(await _svc().get_group(), "nvidia")
    assert card.notice == notice


@pytest.mark.asyncio
async def test_staleness_outranks_amended(db, monkeypatch):
    monkeypatch.setattr(tcs, "_today_et", lambda: date(2026, 12, 1))
    assert _card(await _svc().get_group(), "alphabet").notice == "latest_not_in"


@pytest.mark.asyncio
async def test_a_broken_calendar_costs_two_fields_not_the_card(db, monkeypatch, caplog):
    def _broken():
        raise ImportError("rules module missing")

    monkeypatch.setattr(tcs, "_rules", _broken)
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        group = await _svc().get_group()
    googl = _card(group, "alphabet")
    assert googl.next_due is None and googl.notice == "amended"
    assert googl.top_holdings
    assert "13F calendar unavailable for alphabet 2026-Q2" in caplog.text


# ── the detail ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detail_has_everything_for_a_pro_caller(db):
    detail = await _svc().get_detail("nvidia")
    assert detail.is_locked is False and detail.tier_required is None
    assert [h.symbol for h in detail.holdings] == [
        "INTC", "SPCX", "CRWV", "ZZDA", "ZZUA", "AMD", "ZZUB", None,
    ]
    assert detail.holdings[-1].name == "Ethos Technologies" and detail.holdings[-1].is_small is True
    # The no-longer-reported row has no current holding, and the stored 2026-Q1 row lists no
    # holdings to vouch for its symbol → listed by name, never a link (see the tests below).
    assert [(c.symbol, c.change) for c in detail.changes] == [
        ("SPCX", "newly_reported"), ("ZZDA", "decreased"), (None, "no_longer_reported"),
    ]
    gone = detail.changes[-1]
    assert gone.shares is None and gone.prev_shares == 5.0e6
    assert [(p.period, p.total_value) for p in detail.history] == [("2026-Q1", 50e9), ("2025-Q4", 40e9)]
    assert "nvidia" not in [m.slug for m in detail.other_members]
    assert [m.slug for m in detail.other_members][:2] == ["microsoft", "alphabet"]
    TrillionClubDetailResponse.model_validate(detail.model_dump())


@pytest.mark.asyncio
async def test_detail_shows_non_material_stakes_the_card_leaves_out(db):
    detail = await _svc().get_detail("microsoft")
    assert [s.investee_name for s in detail.stakes] == ["OpenAI Group PBC", "G42", "Anthropic"]
    assert [s.investee_name for s in detail.company.stakes] == ["OpenAI Group PBC", "G42"]
    micron = await _svc().get_detail("micron")        # "Also in the club" still has a detail
    assert [s.investee_name for s in micron.stakes] == ["Anthropic"]
    assert micron.company.stakes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["amd", "sk-hynix", "jpmorgan", "nope", "NVIDIA", "nvidia\n",
                                  "", "a" * 41, None])
async def test_detail_is_none_for_anything_but_a_published_member(db, slug):
    assert await _svc().get_detail(slug) is None


@pytest.mark.asyncio
async def test_history_dedupes_periods_across_ciks(db):
    for row in db.tables["trillion_club_companies"]:
        if row["slug"] == "nvidia":
            row["ciks"] = [NVDA_CIK, "0009999999"]
    db.tables["trillion_club_filings"].append(_history_row("0009999999", "2026-Q1", "2026-03-31", 1.0, 1))
    detail = await _svc().get_detail("nvidia")
    assert [p.period for p in detail.history] == ["2026-Q1", "2025-Q4"]


@pytest.mark.asyncio
async def test_a_malformed_filing_key_is_skipped(db, caplog):
    db.tables["trillion_club_filings"].append(
        {**_history_row(NVDA_CIK, "2027-Q9", "2027-12-31", 1.0, 1)}
    )
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        detail = await _svc().get_detail("nvidia")
    assert detail.company.period == "2026-Q2"
    assert "malformed key" in caplog.text


# ── redaction: the gate ──────────────────────────────────────────────────────────────

# Holdings a Free caller must never see: outside the top 3 AND unchanged this quarter.
_HIDDEN_SYMBOLS = ('"ZZUA"', '"AMD"', '"ZZUB"')
_HIDDEN_NAMES = ('"Umbra Unchanged A"', '"Advanced Micro Devices"', '"Umbra Unchanged B"',
                 '"Ethos Technologies"')


@pytest.mark.asyncio
async def test_locked_detail_leaks_nothing_outside_top3_and_changed_rows(db):
    detail = await _svc().get_detail("nvidia")
    unlocked = detail.model_dump_json()
    # Non-vacuity: every hidden token IS in the unlocked payload.
    for token in _HIDDEN_SYMBOLS + _HIDDEN_NAMES:
        assert token in unlocked, token

    locked = redact_trillion_club_detail(detail, "pro")
    wire = locked.model_dump_json()
    for token in _HIDDEN_SYMBOLS + _HIDDEN_NAMES:
        assert token not in wire, f"{token} leaked into a locked payload"
    # What Free keeps.
    assert [h.symbol for h in locked.holdings] == ["INTC", "SPCX", "CRWV"]
    assert [c.symbol for c in locked.changes] == ["SPCX", "ZZDA", None]
    assert [s.investee_name for s in locked.stakes] == ["Intel"]
    assert locked.history == []
    assert locked.is_locked is True and locked.tier_required == "pro"
    assert locked.locked_holdings_count == 5
    assert [h.symbol for h in locked.company.top_holdings] == ["INTC", "SPCX", "CRWV"]
    TrillionClubDetailResponse.model_validate(json.loads(wire))


@pytest.mark.asyncio
async def test_locked_detail_scan_covers_every_string_field(db):
    """Walk every string in the locked payload rather than trusting the JSON substring
    check alone: no leaf equals a hidden symbol or name, whatever field it sits in."""
    locked = redact_trillion_club_detail(await _svc().get_detail("nvidia"), "pro")
    hidden = {t.strip('"') for t in _HIDDEN_SYMBOLS + _HIDDEN_NAMES}

    def leaves(node):
        if isinstance(node, dict):
            for v in node.values():
                yield from leaves(v)
        elif isinstance(node, list):
            for v in node:
                yield from leaves(v)
        else:
            yield node

    assert not hidden & {v for v in leaves(locked.model_dump()) if isinstance(v, str)}


@pytest.mark.asyncio
async def test_redaction_never_mutates_the_cached_detail(db):
    svc = _svc()
    detail = await svc.get_detail("nvidia")
    before = detail.model_dump()
    redact_trillion_club_detail(detail, "pro")
    assert detail.model_dump() == before
    assert (await svc.get_detail("nvidia")).model_dump() == before   # the cache is intact


def test_redaction_decides_every_detail_field():
    """A field added to TrillionClubDetailResponse must get an explicit redaction decision
    here — otherwise it silently rides through the gate."""
    assert set(REDACTION_POLICY) == set(TrillionClubDetailResponse.model_fields)


def test_redaction_does_not_edit_the_input_even_where_it_filters():
    """The locked copy filters `company.top_holdings`; the input's list must be untouched.
    (A fixture where the filter is a no-op could not tell a copy from an in-place edit.)"""
    holdings = [ClubHoldingResponse(name=f"H{i}", symbol=f"H{i}") for i in range(5)]
    detail = TrillionClubDetailResponse(
        company=TrillionClubCompanyResponse(slug="x", name="X", card_kind="thirteen_f",
                                            top_holdings=holdings[:4]),
        holdings=holdings,
        history=[ClubHistoryPointResponse(period="2026-Q1", period_end="2026-03-31")],
    )
    before = detail.model_dump()
    locked = redact_trillion_club_detail(detail, "pro")
    assert [h.symbol for h in locked.company.top_holdings] == ["H0", "H1", "H2"]
    assert '"H3"' not in locked.model_dump_json()
    assert detail.model_dump() == before


def test_redaction_of_a_detail_with_three_or_fewer_holdings():
    company = TrillionClubCompanyResponse(slug="x", name="X", card_kind="no_thirteen_f")
    detail = TrillionClubDetailResponse(
        company=company,
        history=[ClubHistoryPointResponse(period="2026-Q1", period_end="2026-03-31")],
        stakes=[ClubStakeResponse(investee_name="A", kind="private", as_of="2026-06-30",
                                  source_title="t", source_url="https://x", verified_on="2026-09-01")],
        other_members=[ClubMemberBriefResponse(slug="y", name="Y")],
    )
    locked = redact_trillion_club_detail(detail, "pro")
    assert locked.locked_holdings_count == 0 and locked.holdings == [] and locked.history == []
    assert locked.is_locked is True and len(locked.stakes) == 1 and len(locked.other_members) == 1
    assert len(detail.history) == 1


def test_redaction_drops_an_unchanged_row_that_slipped_into_changes():
    detail = TrillionClubDetailResponse(
        company=TrillionClubCompanyResponse(slug="x", name="X", card_kind="thirteen_f"),
        holdings=[ClubHoldingResponse(name=f"H{i}", symbol=f"H{i}") for i in range(5)],
        changes=[ClubChangeResponse(name="H4", symbol="H4", change="unchanged")],
    )
    locked = redact_trillion_club_detail(detail, "pro")
    assert locked.changes == [] and '"H4"' not in locked.model_dump_json()


# ── entitlements ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tier, unlocked", [
    ("pro", True), ("premium", True), ("PRO ", True),
    ("free", False), (None, False), ("", False), ("platinum", False), (7, False),
])
def test_detail_unlock_falls_closed(tier, unlocked):
    from app.services import entitlements as ent

    assert ent.trillion_club_detail_unlocked(tier) is unlocked
    assert ent.required_tier_for_trillion_club_detail(tier) == (None if unlocked else "pro")


def test_detail_unlock_shares_the_whale_floor():
    from app.services import entitlements as ent

    assert ent.TRILLION_CLUB_DETAIL_UNLOCKED_TIERS is ent.SIGNALS_UNLOCKED_TIERS


# ── the endpoint ─────────────────────────────────────────────────────────────────────


async def _call(slug, tier="free"):
    from app.api.v1.endpoints import home as home_ep

    return await home_ep.get_trillion_club_detail(slug=slug, user={"id": "u-1", "tier": tier})


def _body(resp):
    assert isinstance(resp, JSONResponse)
    body = json.loads(bytes(resp.body))
    assert {"error_code", "message", "user_message", "action", "details"} <= body.keys()
    for value in body["details"].values():
        assert isinstance(value, (str, int, float, bool))
    return resp.status_code, body


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["NVIDIA", "nvidia\n", "a" * 41, "", "nv idia", "../x", "nvidia;"])
async def test_endpoint_rejects_a_malformed_slug(db, slug):
    status, body = _body(await _call(slug))
    assert status == 400 and body["error_code"] == "INVALID_INPUT"
    assert db.total_reads == 0


@pytest.mark.asyncio
async def test_endpoint_unknown_slug_is_the_new_404(db):
    status, body = _body(await _call("not-a-member"))
    assert status == 404 and body["error_code"] == "TRILLION_CLUB_COMPANY_NOT_FOUND"
    assert body["details"] == {"slug": "not-a-member"}
    assert body["user_message"] and "Something went wrong" not in body["user_message"]


@pytest.mark.asyncio
async def test_endpoint_with_the_feature_off_is_404_and_reads_nothing(db, monkeypatch):
    monkeypatch.setattr(settings, "TRILLION_CLUB_ENABLED", False)
    status, body = _body(await _call("nvidia"))
    assert status == 404 and body["error_code"] == "TRILLION_CLUB_COMPANY_NOT_FOUND"
    assert db.total_reads == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["free", None, "platinum", ""])
async def test_endpoint_locks_free_and_unknown_tiers(db, tier):
    resp = await _call("nvidia", tier=tier)
    assert isinstance(resp, TrillionClubDetailResponse)
    assert resp.is_locked is True and resp.tier_required == "pro"
    assert len(resp.holdings) == 3 and resp.history == []
    assert '"ZZUA"' not in resp.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["pro", "premium"])
async def test_endpoint_unlocks_paid_tiers(db, tier):
    resp = await _call("nvidia", tier=tier)
    assert resp.is_locked is False and len(resp.holdings) == 8 and len(resp.history) == 2


@pytest.mark.asyncio
async def test_endpoint_free_then_pro_share_one_cache_without_bleed(db):
    await _call("nvidia", tier="free")
    pro = await _call("nvidia", tier="pro")
    assert pro.is_locked is False and len(pro.holdings) == 8


@pytest.mark.asyncio
async def test_endpoint_turns_a_read_error_into_the_contract_not_a_raise(db):
    db.fail["trillion_club_companies"] = RuntimeError("db down")
    status, body = _body(await _call("nvidia"))
    # Its own retryable code — the generic mapping said "The report failed to generate".
    assert status == 503
    assert body["error_code"] == "TRILLION_CLUB_UNAVAILABLE"
    assert body["action"] == "retry"
    assert "report" not in body["user_message"].lower()
    assert body["details"]["slug"] == "nvidia"
    assert "db down" not in body["message"]           # internal text is not echoed


def test_endpoint_takes_the_users_row_identity():
    import inspect

    from app.api.v1.endpoints import home as home_ep
    from app.dependencies import get_watchlist_identity

    deps = [p.default.dependency for p in inspect.signature(home_ep.get_trillion_club_detail).parameters.values()
            if hasattr(p.default, "dependency")]
    assert deps == [get_watchlist_identity]


# ── the Home dashboard branch ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dashboard_branch_never_raises(monkeypatch):
    import app.services.trillion_club_service as mod
    from app.services.home_dashboard_service import HomeDashboardService

    class _Exploding:
        async def get_group_guarded(self):
            raise RuntimeError("guard broke its promise")

    monkeypatch.setattr(mod, "get_trillion_club_service", lambda: _Exploding())
    svc = HomeDashboardService.__new__(HomeDashboardService)   # no FMP client needed
    assert await svc._get_trillion_club_guarded() == TrillionClubGroupResponse()


@pytest.mark.asyncio
async def test_dashboard_branch_passes_the_group_through(db):
    from app.services.home_dashboard_service import HomeDashboardService

    svc = HomeDashboardService.__new__(HomeDashboardService)
    group = await svc._get_trillion_club_guarded()
    assert _slugs(group)[0] == "nvidia"


# ══ Hardening pass 2026-09-24: review findings + adversarial regressions ═════════════
# (the adversarial file, test_trillion_club_adv_service.py, carries the renamed
# test_regression_* for the defects its own probes found; these pin the review findings and
# the edges around each fix)


def _company_row(db, slug):
    return next(r for r in db.tables["trillion_club_companies"] if r["slug"] == slug)


def _everything(group):
    return _slugs(group) + [m.slug for m in group.also_in_club]


# ── correctness-stale-member-kept-per-company ────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_member_whose_own_check_is_stale_is_left_out_while_the_rest_show(db, caplog):
    _company_row(db, "tesla")["membership_checked_at"] = (NOW - timedelta(days=30)).isoformat()
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        group = await _svc().get_group()
    assert "tesla" not in _everything(group)                    # no card, not "also in"
    assert _slugs(group)[0] == "nvidia"                         # the section still shows
    assert "tesla left out of the club" in caplog.text and "more than 7 days" in caplog.text
    assert await _svc().get_detail("tesla") is None             # → 404, same as the group
    others = [m.slug for m in (await _svc().get_detail("nvidia")).other_members]
    assert "tesla" not in others


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp, kept", [
    (NOW - timedelta(days=7), True),                            # exactly 7 days: still fresh
    (NOW - timedelta(days=7, microseconds=1), False),
    (NOW + timedelta(minutes=5), True),                         # clock skew allowance
    (NOW + timedelta(minutes=5, seconds=1), False),             # a future stamp vouches nothing
    (None, False),
    ("not a timestamp", False),
])
async def test_an_fmp_members_own_stamp_boundaries(db, stamp, kept):
    _company_row(db, "tesla")["membership_checked_at"] = (
        stamp.isoformat() if isinstance(stamp, datetime) else stamp
    )
    assert ("tesla" in _slugs(await _svc().get_group())) is kept


@pytest.mark.asyncio
async def test_a_force_in_fmp_member_is_judged_by_its_own_stamp_too(db):
    """force_in decides membership, but an FMP-sized row still has FMP data the job must
    refresh; a frozen one is left out like any other."""
    row = _company_row(db, "tesla")
    row.update(membership_mode="force_in", is_member=False,
               membership_checked_at=(NOW - timedelta(days=8)).isoformat())
    assert "tesla" not in _everything(await _svc().get_group())
    row["membership_checked_at"] = CHECKED
    tcs.invalidate()
    assert "tesla" in _slugs(await _svc().get_group())


@pytest.mark.asyncio
@pytest.mark.parametrize("as_of, kept", [
    ("2026-09-24", True),                                       # today
    ("2026-09-25", True),                                       # tomorrow: ET vs UTC slack
    ("2026-09-26", False),                                      # a typo'd future date
    ((TODAY - timedelta(days=45)).isoformat(), True),
    ((TODAY - timedelta(days=46)).isoformat(), False),
    (None, False),
    ("not a date", False),
])
async def test_a_hand_sized_member_is_judged_by_its_manual_cap_date(db, as_of, kept):
    row = _company_row(db, "samsung")
    assert row["cap_source"] == "manual" and row["membership_checked_at"] is None
    row["manual_cap_as_of"] = as_of
    group = await _svc().get_group()
    assert ("samsung" in _slugs(group)) is kept
    assert "samsung" not in [m.slug for m in group.also_in_club]
    assert (await _svc().get_detail("samsung") is not None) is kept


def test_the_manual_cap_horizon_is_the_jobs_own_recheck_horizon():
    from app.services.trillion_club import jobs

    assert tcs._MANUAL_CAP_STALE_AFTER_DAYS == jobs.MANUAL_CAP_WARN_DAYS


@pytest.mark.asyncio
async def test_a_future_stamp_cannot_vouch_for_a_dead_job(db, caplog):
    for row in db.tables["trillion_club_companies"]:
        if row["cap_source"] != "manual":
            row["membership_checked_at"] = (NOW - timedelta(days=30)).isoformat()
    _company_row(db, "micron")["membership_checked_at"] = "2031-09-24T11:00:00+00:00"
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        assert await _svc().get_group() == TrillionClubGroupResponse()
    assert "micron membership_checked_at 2031-09-24T11:00:00+00:00 is in the future" in caplog.text


# ── correctness-next-due-in-the-past / contract-next-due-in-the-past ─────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("today, next_due", [
    (date(2026, 9, 24), "2026-11-16"),
    (date(2026, 11, 16), "2026-11-16"),       # the due date itself is still "due by"
    (date(2026, 11, 17), None),               # the day after: never a past date, and never
    (date(2027, 3, 15), None),                # rolled to a later quarter's deadline
])
async def test_next_due_is_never_in_the_past(db, monkeypatch, today, next_due):
    monkeypatch.setattr(tcs, "_today_et", lambda: today)
    card = _card(await _svc().get_group(), "nvidia")
    assert card.next_due == next_due


def test_next_due_is_the_following_quarters_deadline_only_while_it_is_ahead():
    """For three cards over three years of days: the deadline of the quarter right after the
    card's period while it is on or after today, else nothing — never a past date and never
    a LATER quarter's deadline (that read as "nothing due before then" while a filing was
    overdue)."""
    from app.services.trillion_club import rules

    for key in [(2026, 2), (2019, 3), (2029, 4)]:
        expected_due = rules.sec_13f_due_date(*tcs._next_quarter(*key))
        day = date(2026, 1, 1)
        while day <= date(2028, 12, 31):
            got = tcs._next_due_on_or_after(rules, key, day)
            assert got == (expected_due if expected_due >= day else None), (key, day, got)
            day += timedelta(days=1)


# ── correctness-more-stakes-count ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stake_count_counts_every_published_stake_not_only_the_carded_ones(db):
    group = await _svc().get_group()
    microsoft = _card(group, "microsoft")
    assert len(microsoft.stakes) == 2 and microsoft.stake_count == 3   # Anthropic: not material
    assert _card(group, "nvidia").stake_count == 1
    assert _card(group, "alphabet").stake_count == 0                  # its rows are unservable
    detail = await _svc().get_detail("microsoft")
    assert detail.company.stake_count == len(detail.stakes) == 3
    micron = await _svc().get_detail("micron")
    assert micron.company.stakes == [] and micron.company.stake_count == 1


@pytest.mark.asyncio
async def test_a_refused_stake_is_not_counted(db):
    for row in db.tables["trillion_club_stakes"]:
        if row["investee_name"] == "G42":
            row["source_url"] = "https:///no-host"
    assert _card(await _svc().get_group(), "microsoft").stake_count == 2


# ── contract-history-lock-with-nothing-behind-it ─────────────────────────────────────


@pytest.mark.asyncio
async def test_locked_history_count_says_how_many_earlier_quarters_were_withheld(db):
    pro = await _call("nvidia", tier="pro")
    assert len(pro.history) == 2 and pro.locked_history_count == 0
    free = await _call("nvidia", tier="free")
    assert free.history == [] and free.locked_history_count == 2
    # A filer with no earlier quarter on file, and a company with no 13F: nothing behind the
    # lock, so iOS must not sell an empty History.
    for slug in ("alphabet", "microsoft"):
        locked = await _call(slug, tier="free")
        assert locked.is_locked is True and locked.locked_history_count == 0, slug
        assert (await _call(slug, tier="pro")).history == []


# ── contract-locked-notes-name-withheld-holdings ─────────────────────────────────────

_NOTE_BASE = dict(kind="on_13f_note", material=True)


def _add_notes(db):
    """Four notes about NVIDIA's own 13F holdings, each naming its holding differently."""
    db.tables["trillion_club_stakes"] += [
        # Withheld holdings (outside the top 3, unchanged): by CUSIP (lower-case, no symbol,
        # a name unlike the holding's), by symbol (lower-case), and the NON-routable one by
        # CUSIP (its holding has no symbol on the wire at all).
        _stake("nvidia", "Stake Note Alpha", investee_cusip="zzua00001", **_NOTE_BASE),
        _stake("nvidia", "Stake Note Bravo", symbol="zzub", **_NOTE_BASE),
        _stake("nvidia", "Stake Note Delta", investee_cusip="29765A101", **_NOTE_BASE),
        # A CHANGED holding outside the top 3: its identity is free in `changes`, so it stays.
        _stake("nvidia", "Stake Note Charlie", symbol="ZZDA", **_NOTE_BASE),
    ]


@pytest.mark.asyncio
async def test_locked_detail_drops_notes_naming_a_withheld_holding(db):
    _add_notes(db)
    detail = await _svc().get_detail("nvidia")
    everyone = ["Intel", "Stake Note Alpha", "Stake Note Bravo", "Stake Note Charlie", "Stake Note Delta"]
    assert [s.investee_name for s in detail.stakes] == everyone            # Pro: all of them
    assert detail.company.stakes == []           # notes never ride on the card itself
    assert detail.company.stake_count == 5

    locked = redact_trillion_club_detail(detail, "pro")
    assert [s.investee_name for s in locked.stakes] == ["Intel", "Stake Note Charlie"]
    assert locked.company.stakes == []
    assert locked.company.stake_count == 2
    # The redaction is a copy: the cached Pro detail still has every note.
    assert [s.investee_name for s in detail.stakes] == everyone


@pytest.mark.asyncio
async def test_locked_detail_leaks_no_withheld_holding_through_any_field(db):
    """Every string leaf of the locked payload, substring match — holdings, changes, stakes,
    the company card and its stakes, other_members, everything."""
    _add_notes(db)
    detail = await _svc().get_detail("nvidia")
    locked = redact_trillion_club_detail(detail, "pro")
    hidden = {t.strip('"') for t in _HIDDEN_SYMBOLS + _HIDDEN_NAMES} | {
        "ZZUA00001", "zzua00001", "zzub", "Stake Note Alpha", "Stake Note Bravo", "Stake Note Delta",
    }

    def leaves(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from leaves(value)
        elif isinstance(node, list):
            for value in node:
                yield from leaves(value)
        else:
            yield node

    strings = [v for v in leaves(locked.model_dump(mode="json")) if isinstance(v, str)]
    for token in hidden:
        leaks = [s for s in strings if token in s]
        assert not leaks, f"{token!r} leaked into a locked payload: {leaks}"
    # Non-vacuity: the unlocked payload carries every one of them except the raw CUSIPs.
    unlocked = detail.model_dump_json()
    for token in hidden - {"ZZUA00001", "zzua00001", "zzub"}:
        assert token in unlocked, token


@pytest.mark.asyncio
async def test_the_note_link_never_reaches_the_wire(db):
    _add_notes(db)
    detail = await _svc().get_detail("nvidia")
    note = next(s for s in detail.stakes if s.investee_name == "Stake Note Delta")
    assert note._names_holding == ("Ethos Technologies", None)             # resolved by CUSIP
    wire = json.loads(detail.model_dump_json())
    assert all(set(s) == set(ClubStakeResponse.model_fields) for s in wire["stakes"])
    assert "_names_holding" not in detail.model_dump_json()
    ClubStakeResponse.model_validate(wire["stakes"][0])


def test_redaction_matches_a_note_by_its_symbol_when_it_has_no_build_link():
    """A detail built outside the service (or a future builder) has no private link; the
    wire symbol, normalised, still identifies the holding."""
    def stake(name, kind, symbol):
        return ClubStakeResponse(investee_name=name, kind=kind, symbol=symbol, as_of="2026-06-30",
                                 source_title="t", source_url="https://www.sec.gov/x",
                                 verified_on="2026-09-01")

    holdings = [ClubHoldingResponse(name=f"H{i}", symbol=f"H{i}.B", change="unchanged") for i in range(5)]
    holdings[4].change = "increased"                                         # changed: stays free
    detail = TrillionClubDetailResponse(
        company=TrillionClubCompanyResponse(slug="x", name="X", card_kind="thirteen_f",
                                            stakes=[stake("n3", "on_13f_note", "h3-b")], stake_count=4),
        holdings=holdings,
        stakes=[stake("n0", "on_13f_note", "H0.B"), stake("n3", "on_13f_note", "h3-b"),
                stake("n4", "on_13f_note", "H4.B"), stake("p3", "private", "H3.B")],
    )
    locked = redact_trillion_club_detail(detail, "pro")
    assert [s.investee_name for s in locked.stakes] == ["n0", "n4", "p3"]    # only the note goes
    assert locked.company.stakes == [] and locked.company.stake_count == 3


# ── contract-whale-link-explainer-without-profile ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_whale_link_without_a_whales_row_keeps_its_card_with_no_profile_link(db, caplog):
    db.tables["whales"] = []
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        card = _card(await _svc().get_group(), "berkshire")
    assert card.card_kind == "whale_link" and card.whale_id is None
    assert [s.investee_name for s in card.stakes] == ["Mitsubishi Corp"]
    assert "no whales row has CIK 0001067983" in caplog.text
    assert "whale_id sent as null" in caplog.text
    assert (await _svc().get_detail("berkshire")).company.whale_id is None


# ── contract-other-members-mislabelled (semantics KEPT, pinned) ─────────────────────


@pytest.mark.asyncio
async def test_other_members_is_every_other_published_member_carded_or_not(db):
    """Deliberately NOT the Home group's no-card `also_in_club`: the detail lists every other
    member. (The iOS sheet's heading must say so — see the lead's report.)"""
    group = await _svc().get_group()
    detail = await _svc().get_detail("nvidia")
    assert {m.slug for m in detail.other_members} == set(_everything(group)) - {"nvidia"}


# ── compliance-runtime-copy-guard-weaker-than-seed ───────────────────────────────────


def test_the_service_uses_the_shared_copy_rules():
    from app.services.trillion_club import copy_rules

    assert tcs.contains_banned_copy is copy_rules.contains_banned_copy
    assert tcs.contains_forecast is copy_rules.contains_forecast
    assert tcs._STAKE_TEXT_FIELDS is copy_rules.STAKE_TEXT_FIELDS
    assert not hasattr(tcs, "_BANNED_COPY"), "a second copy of the patterns is how they drifted"


@pytest.mark.parametrize("over, reason", [
    ({"background": "Tesla bought SpaceX shares in March 2026."}, "banned"),
    ({"background": "The stake is worth $3.0B today."}, "banned"),
    ({"investee_name": "Endorsed Partners LLC"}, "banned"),
    ({"background": "Follow Berkshire into Japan's trading houses."}, "banned"),
    ({"background": "Tesla will likely sell after the lock-up ends."}, "forecast"),
    ({"background": "Microsoft expects to raise the stake in 2027."}, "forecast"),
])
def test_the_runtime_validator_refuses_what_the_seed_refuses(over, reason):
    """Studio edits reach the screen through this validator only."""
    problem = stake_problem(_valid_stake(**over), TODAY)
    assert problem is not None and reason in problem, problem


@pytest.mark.parametrize("over", [
    {"background": "Follow-on offering closed in Jan 2026."},     # a noun, not an instruction
    {"investee_name": "Mirror Biologics"},
    {"source_title": "Form 8-K: plans to acquire (filed 2026-05-01)"},  # forecast: background only
    {"investee_name": "Will Group"},
])
def test_the_runtime_validator_still_serves_nouns_and_titles(over):
    """At read time a false positive silently DROPS a real stake."""
    assert stake_problem(_valid_stake(**over), TODAY) is None


# ── the source link and the stake symbol ─────────────────────────────────────────────


@pytest.mark.parametrize("url, ok", [
    ("https://www.sec.gov/x", True),
    ("https://www.sec.gov:443/x?a=1#b", True),
    ("https://www.sec.gov./x", True),                            # a fully qualified name
    ("https://user:pw@www.sec.gov/x", False),                     # userinfo
    ("https://localhost/x", False),                               # no dotted host
    ("https://.gov/x", False),
    ("https://[::1/x", False),                                    # urlsplit raises
    ("https://", False),
])
def test_the_source_link_needs_a_real_host(url, ok):
    assert (stake_problem(_valid_stake(source_url=url), TODAY) is None) is ok


@pytest.mark.parametrize("raw, shipped", [
    ("glw", "GLW"), ("BRK.B", "BRK.B"), ("BRK-B", "BRK-B"), (" nok ", "NOK"),
    ("ＧＬＷ", None),                                            # full-width letters
    ("ﬀ", None),                                                 # upper() would make "FF"
    ("TOOLONGX", None), ("9ABC", None), ("N/A", None), ("BRK B", None),
])
def test_a_stake_symbol_is_shape_checked(raw, shipped, caplog):
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        stake = tcs._parse_stake(_valid_stake(investee_us_symbol=raw), TODAY)
    assert stake is not None and stake.response.symbol == shipped
    assert (stake.raw_symbol is None) is (shipped is None)
    assert ("malformed investee_us_symbol" in caplog.text) is (shipped is None)


# ── resilience: a malformed JSONB value degrades its row, never the section ──────────


def test_counts_and_ciks_refuse_numbers_ios_or_python_cannot_hold():
    assert tcs._count(2 ** 53 - 1) == 2 ** 53 - 1
    assert tcs._count(2 ** 53) is None and tcs._count(float(2 ** 60)) is None
    assert tcs._finite(10 ** 400) is None and tcs._positive(-(10 ** 400)) is None
    assert tcs._normalise_cik(10 ** 5000) is None                 # str() would raise
    assert tcs._normalise_cik(1067983) == "0001067983"


@pytest.mark.asyncio
@pytest.mark.parametrize("column, value, logged", [
    ("card_kind", ["non_us"], "unknown card_kind"),
    ("cap_source", {"source": "fmp_us"}, "unknown cap_source"),
    ("membership_mode", ["auto"], "unknown membership_mode"),
])
async def test_an_unhashable_registry_value_skips_that_company_only(db, caplog, column, value, logged):
    _company_row(db, "micron")[column] = value
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        group = await _svc().get_group()
    assert _slugs(group)[0] == "nvidia"
    assert "micron" not in _everything(group)
    assert f"company micron skipped — {logged}" in caplog.text


# ── the gone row's symbol: what may vouch for it ─────────────────────────────────────


def _gone_symbol(detail):
    return next(c for c in detail.changes if c.change == "no_longer_reported").symbol


@pytest.mark.asyncio
@pytest.mark.parametrize("prev_routable, expected", [(True, "ZZGN"), (False, None)])
async def test_the_stored_previous_quarter_vouches_for_a_gone_rows_symbol(db, prev_routable, expected):
    prev = db.tables["trillion_club_filings"][1]
    assert (prev["cik"], prev["period"]) == (NVDA_CIK, "2026-Q1")
    prev["holdings"] = [_holding("ZZGN00001", "ZZGN", "Gone Holdings Inc", 5e8, 1.0, routable=prev_routable)]
    detail = await _svc().get_detail("nvidia")
    assert _gone_symbol(detail) == expected
    # The latest and the previous full rows come back in ONE round trip (index + rows).
    assert db.reads["trillion_club_filings"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("prev_period", ["2026-Q1", None])
async def test_only_the_adjacent_quarter_may_vouch(db, prev_period):
    """2026-Q1 not stored, 2025-Q4 is (and lists the symbol as routable): not the quarter the
    row was compared with, so nothing vouches — also when the filing does not name its
    previous period (so only the adjacency rule, not the prev_period cross-check, decides)."""
    db.tables["trillion_club_filings"] = [f for f in db.tables["trillion_club_filings"]
                                          if (f["cik"], f["period"]) != (NVDA_CIK, "2026-Q1")]
    db.tables["trillion_club_filings"][0]["changes"]["prev_period"] = prev_period
    older = next(f for f in db.tables["trillion_club_filings"] if f["period"] == "2025-Q4")
    older["holdings"] = [_holding("ZZGN00001", "ZZGN", "Gone Holdings Inc", 5e8, 1.0)]
    assert _gone_symbol(await _svc().get_detail("nvidia")) is None


@pytest.mark.asyncio
async def test_a_previous_quarter_the_filing_does_not_compare_with_is_ignored(db, caplog):
    db.tables["trillion_club_filings"][1]["holdings"] = [
        _holding("ZZGN00001", "ZZGN", "Gone Holdings Inc", 5e8, 1.0)]
    db.tables["trillion_club_filings"][0]["changes"]["prev_period"] = "2025-Q4"   # contradicts
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        detail = await _svc().get_detail("nvidia")
    assert _gone_symbol(detail) is None
    assert "compares with 2025-Q4, not the stored 2026-Q1" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("stamped, prev_routable, expected", [
    (True, False, "ZZGN"), (False, True, None), ("yes", True, "ZZGN"),   # non-bool: not a stamp
])
async def test_a_routable_flag_on_the_change_row_is_authoritative(db, stamped, prev_routable, expected):
    db.tables["trillion_club_filings"][1]["holdings"] = [
        _holding("ZZGN00001", "ZZGN", "Gone Holdings Inc", 5e8, 1.0, routable=prev_routable)]
    row = next(r for r in db.tables["trillion_club_filings"][0]["changes"]["rows"]
               if r["change"] == "no_longer_reported")
    row["routable"] = stamped
    assert _gone_symbol(await _svc().get_detail("nvidia")) == expected


# ── the guard's orphaned build ───────────────────────────────────────────────────────


async def _drain_inflight():
    for _ in range(100):
        if not TrillionClubService._inflight:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_guard_reports_a_build_that_fails_after_it_stopped_waiting(db, monkeypatch, caplog):
    monkeypatch.setattr(tcs, "_GROUP_TIMEOUT_SECONDS", 0.05)
    db.gate.enabled = True
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        assert await _svc().get_group_guarded() == TrillionClubGroupResponse()
        db.fail["trillion_club_companies"] = RuntimeError("late failure")
        db.gate.open.set()
        await _drain_inflight()
    assert "failed after the Home guard stopped waiting (RuntimeError: late failure)" in caplog.text


@pytest.mark.asyncio
async def test_guard_reports_an_in_time_failure_exactly_once(db, caplog):
    db.fail["trillion_club_companies"] = RuntimeError("db down now")
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        assert await _svc().get_group_guarded() == TrillionClubGroupResponse()
        await asyncio.sleep(0.01)
    assert caplog.text.count("db down now") == 1
    assert "stopped waiting" not in caplog.text


@pytest.mark.parametrize("tier, locked", [("pro", False), ("free", True)])
def test_the_route_serializes_through_fastapi_without_the_private_link(db, tier, locked):
    """The detail's stakes are `_ClubStake` instances (a private attribute on top of the wire
    model). Through FastAPI's real response serialisation the wire must be exactly
    ClubStakeResponse — no extra key, no serialiser warning — Pro and Free alike."""
    import warnings

    from fastapi.testclient import TestClient

    from app.dependencies import get_current_user_id, get_watchlist_identity
    from app.main import app

    _add_notes(db)
    app.dependency_overrides[get_current_user_id] = lambda: "u-1"
    app.dependency_overrides[get_watchlist_identity] = lambda: {"id": "u-1", "tier": tier}
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            resp = TestClient(app).get("/api/v1/home/trillion-club/nvidia")
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)
        app.dependency_overrides.pop(get_watchlist_identity, None)
    assert resp.status_code == 200, resp.text
    assert not [w for w in caught if "serializ" in str(w.message).lower()], [str(w.message) for w in caught]
    body = resp.json()
    assert "_names_holding" not in resp.text and "names_holding" not in resp.text
    for stake in body["stakes"] + body["company"]["stakes"]:
        assert set(stake) == set(ClubStakeResponse.model_fields)
    assert body["is_locked"] is locked
    assert len(body["stakes"]) == (2 if locked else 5)
    assert body["locked_history_count"] == (2 if locked else 0)
    TrillionClubDetailResponse.model_validate(body)
