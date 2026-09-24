"""Adversarial tests — Trillion-Dollar Club Bets READ path.

Scope: ``app/services/trillion_club_service.py``, ``GET /api/v1/home/trillion-club/{slug}``,
the Home dashboard gather branch, the Free redaction, the error codes and the entitlement.

Written independently of ``test_trillion_club_service.py`` (own fake, own fixtures) to hunt
the inputs that file did not picture: boundary clocks, malformed JSONB, tier strings,
cross-role contract drift (what the builder REALLY writes vs what the service assumes).

Hermetic: an in-memory fake Supabase; ``sb_exec`` runs it synchronously or parks on a gate.

Tests named ``test_regression_*`` were ``test_BUG_*``: each exposed a real defect, was left
failing until the source was fixed (2026-09-24), and now pins the fix — its docstring says
what it was, where, and what changed.
"""

from __future__ import annotations

import asyncio
import copy
import gc
import json
import logging
import math
import random
import re
import sys
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.responses import JSONResponse

import app.services.trillion_club_service as tcs
from app.config import settings
from app.schemas.trillion_club import TrillionClubDetailResponse, TrillionClubGroupResponse
from app.services.trillion_club_service import (
    TrillionClubService,
    redact_trillion_club_detail,
    stake_problem,
)

TODAY = date(2026, 9, 24)
NOW = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
CHECKED = "2026-09-24T11:00:00+00:00"

NVDA = "0001045810"
GOOGL = "0001652044"
BRK = "0001067983"
AMD = "0000002488"


# ── fake Supabase ────────────────────────────────────────────────────────────────────


class _Q:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.order_by, self.lim, self.cols = [], None, None, "*"

    def select(self, cols="*"):
        self.cols = cols
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def in_(self, col, vals):
        vals = list(vals)
        assert vals, f"empty in_() on {self.table}.{col} — PostgREST 400s on `in.()`"
        self.filters.append(lambda r, c=col, v=vals: r.get(c) in v)
        return self

    def order(self, col, desc=False):
        self.order_by = (col, desc)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def execute(self):
        self.db.reads[self.table] = self.db.reads.get(self.table, 0) + 1
        failure = self.db.fail.get(self.table)
        if failure is None and self.db.fail_if is not None:
            failure = self.db.fail_if(self.table, self.cols)
        if failure is not None:
            raise failure
        rows = [copy.deepcopy(r) for r in self.db.tables.get(self.table, [])
                if all(f(r) for f in self.filters)]
        if self.order_by:
            col, desc = self.order_by
            rows.sort(key=lambda r: (r.get(col) is None, str(r.get(col))), reverse=desc)
        if self.lim is not None:
            rows = rows[: self.lim]
        if self.cols != "*":
            keep = [c.strip() for c in self.cols.split(",")]
            rows = [{k: r.get(k) for k in keep} for r in rows]
        return SimpleNamespace(data=rows)


class _DB:
    def __init__(self, tables):
        self.tables = tables
        self.reads = {}
        self.fail = {}
        self.fail_if = None

    def table(self, name):
        return _Q(self, name)

    @property
    def total_reads(self):
        return sum(self.reads.values())


# ── fixture data ─────────────────────────────────────────────────────────────────────


def _co(slug, name, *, card_kind="no_thirteen_f", cap=None, symbol=None, aliases=(), ciks=(),
        use_13f=False, cap_source="fmp_us", mode="auto", is_member=True, published=True,
        checked=CHECKED, link_whale=False, manual_cap=None, detail_symbol="__same__", **extra):
    row = {
        "slug": slug, "display_name": name, "ciks": list(ciks), "card_kind": card_kind,
        "use_13f": use_13f, "cap_symbol": symbol, "symbol_aliases": list(aliases),
        "detail_symbol": symbol if detail_symbol == "__same__" else detail_symbol,
        "logo_symbol": symbol, "home_country": "US", "cap_source": cap_source,
        "manual_cap_usd": manual_cap, "manual_cap_as_of": "2026-09-19" if manual_cap else None,
        "membership_mode": mode, "is_member": is_member, "last_market_cap": cap,
        "last_cap_date": "2026-09-23" if cap else None, "membership_checked_at": checked,
        "link_whale": link_whale, "published": published, "reviewed_on": "2026-09-20",
    }
    row.update(extra)
    return row


def _st(company, investee, *, kind="private", material=True, symbol=None, pct=None, value=None,
        basis=None, verified="2026-09-01", sort=0, **extra):
    row = {
        "id": f"{company}:{investee}", "company_slug": company, "kind": kind,
        "investee_name": investee, "investee_cusip": None, "investee_us_symbol": symbol,
        "local_listing": None, "ownership_pct": pct, "ownership_basis": None,
        "disclosed_value_usd": value, "value_basis": basis, "as_of": "2026-06-30",
        "source_title": f"{company} annual report", "source_url": "https://www.sec.gov/x",
        "source_confidence": "primary", "material": material, "tied_to_deal": False,
        "listed_since": None, "background": None, "verified_on": verified, "published": True,
        "sort_order": sort,
    }
    row.update(extra)
    return row


def _h(cusip, symbol, name, value, weight, *, routable=True):
    return {"cusip": cusip, "symbol": symbol, "name": name, "title_of_class": "COM",
            "shares": value / 100.0, "value": value, "weight": weight, "is_small": weight < 0.01,
            "sector": "Technology", "ipo_date": None, "exchange": "NASDAQ", "routable": routable}


def _nvda_q2():
    return {
        "cik": NVDA, "period": "2026-Q2", "period_end": "2026-06-30", "filed_on": "2026-08-14",
        "amended_on": None, "accessions": ["0001045810-26-000065"], "total_value": 63.0e9,
        "position_count": 8,
        "holdings": [
            _h("458140100", "INTC", "Intel Corp", 30.0e9, 0.47),
            _h("84615Q103", "SPCX", "Space Exploration Technologies", 21.0e9, 0.33),
            _h("21873S108", "CRWV", "CoreWeave Inc", 4.4e9, 0.07),
            _h("ZZDA00001", "ZZDA", "Zedd Alpha Corp", 3.0e9, 0.047),
            _h("ZZUA00001", "ZZUA", "Umbra Unchanged A", 2.5e9, 0.039),
            _h("007903107", "AMD", "Advanced Micro Devices", 1.5e9, 0.024),
            _h("ZZUB00001", "ZZUB", "Umbra Unchanged B", 0.9e9, 0.014),
            _h("29765A101", None, "Ethos Technologies", 0.24e9, 0.004, routable=False),
        ],
        "changes": {
            "comparison": "quarter", "prev_period": "2026-Q1",
            "counts": {"newly_reported": 1, "increased": 0, "decreased": 1,
                       "no_longer_reported": 1, "unchanged": 5, "corporate_action": 0},
            "rows": [
                {"cusip": "84615Q103", "symbol": "SPCX", "name": "Space Exploration Technologies",
                 "change": "newly_reported", "newly_listed": True, "shares": 2.1e8,
                 "prev_shares": None, "share_change": None, "value": 21.0e9, "weight": 0.33},
                {"cusip": "ZZDA00001", "symbol": "ZZDA", "name": "Zedd Alpha Corp",
                 "change": "decreased", "newly_listed": False, "shares": 3.0e7,
                 "prev_shares": 4.0e7, "share_change": -1.0e7, "value": 3.0e9, "weight": 0.047},
                {"cusip": "ZZGN00001", "symbol": "ZZGN", "name": "Gone Holdings Inc",
                 "change": "no_longer_reported", "newly_listed": False, "shares": None,
                 "prev_shares": 5.0e6, "share_change": None, "value": None, "weight": None},
            ],
        },
        "excluded_rows": 0, "unresolved": {}, "raw_hash": "n2", "build_status": "complete",
        "source": "fmp",
    }


def _nvda_q1():
    # The PREVIOUS quarter as its own build stored it. "Gone Holdings" was filed under a
    # symbol whose profile was NOT on a U.S. exchange → routable false.
    holdings = [
        _h("458140100", "INTC", "Intel Corp", 25.0e9, 0.5),
        _h("ZZDA00001", "ZZDA", "Zedd Alpha Corp", 4.0e9, 0.08),
        _h("ZZGN00001", "ZZGN", "Gone Holdings Inc", 0.5e9, 0.01, routable=False),
    ]
    return {
        "cik": NVDA, "period": "2026-Q1", "period_end": "2026-03-31", "filed_on": "2026-05-14",
        "amended_on": None, "accessions": ["a1"], "total_value": 50e9, "position_count": 7,
        "holdings": holdings,
        "changes": {"comparison": "first_filing", "prev_period": None, "counts": {}, "rows": []},
        "excluded_rows": 0, "unresolved": {}, "raw_hash": "n1", "build_status": "complete",
        "source": "fmp",
    }


def _hist(cik, period, end, total, count):
    return {"cik": cik, "period": period, "period_end": end, "filed_on": end, "amended_on": None,
            "accessions": ["h"], "total_value": total, "position_count": count, "holdings": [],
            "changes": {"comparison": "first_filing", "prev_period": None, "counts": {}, "rows": []},
            "excluded_rows": 0, "unresolved": {}, "raw_hash": "h", "build_status": "complete",
            "source": "fmp"}


def _googl_q2():
    return {
        "cik": GOOGL, "period": "2026-Q2", "period_end": "2026-06-30", "filed_on": "2026-08-07",
        "amended_on": "2026-09-01", "accessions": ["g1", "g2"], "total_value": 99e9,
        "position_count": 2,
        "holdings": [_h("84615Q103", "SPCX", "Space Exploration Technologies", 94e9, 0.95),
                     _h("084670702", "BRK.B", "Berkshire Hathaway Inc", 5e9, 0.05)],
        "changes": {"comparison": "quarter", "prev_period": "2026-Q1",
                    "counts": {"newly_reported": 1, "unchanged": 1},
                    "rows": [{"cusip": "84615Q103", "symbol": "SPCX",
                              "name": "Space Exploration Technologies", "change": "newly_reported",
                              "newly_listed": True, "shares": 5e8, "value": 94e9, "weight": 0.95}]},
        "excluded_rows": 0, "unresolved": {}, "raw_hash": "g", "build_status": "complete",
        "source": "fmp",
    }


def _tables():
    return {
        "trillion_club_companies": [
            _co("nvidia", "NVIDIA", card_kind="thirteen_f", cap=4.5e12, symbol="NVDA", ciks=[NVDA],
                use_13f=True),
            _co("microsoft", "Microsoft", cap=3.8e12, symbol="MSFT"),
            _co("alphabet", "Alphabet", card_kind="thirteen_f", cap=3.0e12, symbol="GOOGL",
                aliases=["GOOG"], ciks=[GOOGL], use_13f=True),
            _co("tesla", "Tesla", cap=1.4e12, symbol="TSLA"),
            _co("samsung", "Samsung Electronics", card_kind="non_us", cap_source="manual",
                manual_cap=1.37e12, mode="force_in", is_member=False, symbol=None, checked=None),
            _co("spacex", "SpaceX", cap=1.2e12, symbol="SPCX"),
            _co("berkshire", "Berkshire Hathaway", card_kind="whale_link", cap=1.05e12,
                symbol="BRK-B", aliases=["BRK-A"], ciks=[BRK], link_whale=True),
            _co("micron", "Micron", cap=1.01e12, symbol="MU"),
            _co("amd", "AMD", card_kind="thirteen_f", cap=0.99e12, symbol="AMD", ciks=[AMD],
                use_13f=True, is_member=False),
            _co("jpmorgan", "JPMorgan Chase", cap=0.9e12, symbol="JPM", published=False),
        ],
        "trillion_club_stakes": [
            _st("microsoft", "OpenAI Group PBC", pct=25.0, sort=1),
            _st("microsoft", "G42", value=1.5e9, basis="invested", sort=2),
            _st("microsoft", "Anthropic", kind="commitment", material=False, value=5e9,
                basis="committed_up_to", sort=3),
            _st("nvidia", "Intel", kind="on_13f_note", symbol="INTC"),
            _st("tesla", "SpaceX", value=2.0e9, basis="invested", symbol="SPCX"),
            _st("samsung", "Corning Inc", kind="us_listed_off_13f", pct=7.9, symbol="GLW"),
            _st("berkshire", "Mitsubishi Corp", kind="non_us_listed", pct=10.8),
            _st("micron", "Anthropic", material=False),
        ],
        "trillion_club_filings": [
            _nvda_q2(), _nvda_q1(), _hist(NVDA, "2025-Q4", "2025-12-31", 40e9, 6), _googl_q2(),
            _hist(AMD, "2026-Q2", "2026-06-30", 5e9, 4),
        ],
        "whales": [{"id": "whale-brk", "cik": BRK}],
    }


class _Gate:
    def __init__(self):
        self.open = asyncio.Event()
        self.entered = asyncio.Event()
        self.enabled = False
        self.calls = 0


def _reset():
    TrillionClubService._group_cache.clear()
    TrillionClubService._detail_cache.clear()
    TrillionClubService._inflight.clear()
    TrillionClubService._invalidated_at = 0.0


@pytest.fixture
def db(monkeypatch):
    fake = _DB(_tables())
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
    _reset()
    fake.gate = gate
    yield fake
    _reset()


def _svc():
    return TrillionClubService()


def _row(db, slug):
    return next(r for r in db.tables["trillion_club_companies"] if r["slug"] == slug)


def _slugs(group):
    return [c.slug for c in group.companies]


def _also(group):
    return [m.slug for m in group.also_in_club]


def _card(group, slug):
    return next(c for c in group.companies if c.slug == slug)


def _leaves(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _leaves(v)
    elif isinstance(node, list):
        for v in node:
            yield from _leaves(v)
    else:
        yield node


def _strict_json(model) -> str:
    """What Starlette's JSONResponse does: allow_nan=False — a NaN is a 500."""
    return json.dumps(model.model_dump(mode="json"), allow_nan=False)


async def _call(slug, tier="free"):
    from app.api.v1.endpoints import home as home_ep

    return await home_ep.get_trillion_club_detail(slug=slug, user={"id": "u-1", "tier": tier})


def _err(resp):
    assert isinstance(resp, JSONResponse), f"expected an error body, got {type(resp).__name__}"
    body = json.loads(bytes(resp.body))
    assert set(body) == {"error_code", "message", "user_message", "action", "details"}
    for v in body["details"].values():
        assert isinstance(v, (str, int, float, bool)), "details must be flat scalars (iOS AnyCodable)"
    return resp.status_code, body


# ══ 1. Redaction: nothing outside top-3 + changed rows, anywhere ═════════════════════


def _holding_tokens(detail):
    """Every name / symbol a holding or change row puts on the wire."""
    out = set()
    for h in detail.holdings:
        out.add(h.name)
        if h.symbol:
            out.add(h.symbol)
    return out


def _allowed_tokens(detail):
    out = set()
    for h in detail.holdings[:3]:
        out.add(h.name)
        if h.symbol:
            out.add(h.symbol)
    for c in detail.changes:
        if c.change != "unchanged":
            out.add(c.name)
            if c.symbol:
                out.add(c.symbol)
    return out


@pytest.mark.asyncio
async def test_locked_payload_carries_no_hidden_holding_token_in_any_string_leaf(db):
    detail = await _svc().get_detail("nvidia")
    hidden = _holding_tokens(detail) - _allowed_tokens(detail)
    # Non-vacuity: four holdings are outside the top 3 AND unchanged.
    assert hidden >= {"Umbra Unchanged A", "ZZUA", "Advanced Micro Devices", "AMD",
                      "Umbra Unchanged B", "ZZUB", "Ethos Technologies"}
    locked = redact_trillion_club_detail(detail, "pro")
    strings = [v for v in _leaves(locked.model_dump(mode="json")) if isinstance(v, str)]
    for token in hidden:
        # Substring, not equality: a token inside a longer string is still a leak.
        leaks = [s for s in strings if token in s]
        assert not leaks, f"{token!r} leaked into the locked payload: {leaks}"
    # The Home card's own top_holdings obey the same cut.
    assert [h.model_dump() for h in locked.company.top_holdings] == \
        [h.model_dump() for h in locked.holdings]
    assert locked.locked_holdings_count == len(detail.holdings) - 3


@pytest.mark.asyncio
async def test_locked_payload_hides_a_club_member_holding_outside_the_top3(db):
    """AMD joins the club. Its holding (outside the top 3, unchanged) is now TAGGED — the tag
    must vanish with the row; AMD may appear only as an other_members entry."""
    _row(db, "amd")["is_member"] = True
    detail = await _svc().get_detail("nvidia")
    amd = next(h for h in detail.holdings if h.symbol == "AMD")
    assert amd.club_member_slug == "amd"                        # non-vacuity
    locked = redact_trillion_club_detail(detail, "pro")
    dumped = locked.model_dump(mode="json")
    assert all(h["club_member_slug"] != "amd" for h in dumped["holdings"])
    assert all(h["club_member_slug"] != "amd" for h in dumped["company"]["top_holdings"])
    assert "Advanced Micro Devices" not in json.dumps(dumped)
    assert "amd" in [m["slug"] for m in dumped["other_members"]]


@pytest.mark.asyncio
async def test_redaction_output_is_never_aliased_to_the_shared_cached_detail(db):
    """Deep copy, not just a new envelope: mutating the locked copy (as a later middleware or
    a careless caller might) must not reach the object every Pro caller is served."""
    svc = _svc()
    cached = await svc.get_detail("microsoft")
    cached_nv = await svc.get_detail("nvidia")
    before = (cached.model_dump(), cached_nv.model_dump())
    for detail in (cached, cached_nv):
        locked = redact_trillion_club_detail(detail, "pro")
        locked.company.name = "MUTATED"
        for coll in (locked.holdings, locked.changes, locked.company.top_holdings):
            for item in coll:
                item.name = "MUTATED"
        for s in locked.stakes + locked.company.stakes:
            s.investee_name = "MUTATED"
        for m in locked.other_members:
            m.name = "MUTATED"
    assert (cached.model_dump(), cached_nv.model_dump()) == before
    pro = await _call("nvidia", tier="pro")
    assert "MUTATED" not in pro.model_dump_json()


@pytest.mark.asyncio
async def test_free_then_pro_then_free_never_bleed_through_the_endpoint(db):
    free1 = await _call("nvidia", tier="free")
    pro = await _call("nvidia", tier="pro")
    free2 = await _call("nvidia", tier=None)
    assert free1.is_locked and free2.is_locked and not pro.is_locked
    assert len(pro.holdings) == 8 and len(pro.history) == 2
    assert free1.model_dump() == free2.model_dump()
    assert db.reads["trillion_club_companies"] == 1           # one shared cached build


# ══ 2. Tier strings ══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("tier, unlocked", [
    ("pro", True), ("PRO", True), (" Pro\t", True), ("premium", True), ("PREMIUM\n", True),
    (None, False), ("", False), ("   ", False), ("free", False), ("FREE", False),
    ("max", False), ("enterprise", False), ("pro_trial", False), ("pro plus", False),
    (0, False), (1, False), (True, False), (["pro"], False), ({"tier": "pro"}, False),
])
async def test_endpoint_tier_matrix_falls_closed(db, tier, unlocked):
    resp = await _call("nvidia", tier=tier)
    assert isinstance(resp, TrillionClubDetailResponse)
    assert resp.is_locked is (not unlocked)
    assert resp.tier_required == (None if unlocked else "pro")
    assert len(resp.holdings) == (8 if unlocked else 3)
    assert (len(resp.history) > 0) is unlocked


# ══ 3. Feature flag off ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_flag_off_serves_nothing_and_reads_nothing_even_with_warm_caches(db, monkeypatch):
    from app.services.home_dashboard_service import HomeDashboardService

    svc = _svc()
    assert (await svc.get_group()).companies
    assert await svc.get_detail("nvidia") is not None
    monkeypatch.setattr(settings, "TRILLION_CLUB_ENABLED", False)
    db.reads.clear()
    dash = HomeDashboardService.__new__(HomeDashboardService)
    assert await dash._get_trillion_club_guarded() == TrillionClubGroupResponse()
    assert await svc.get_group() == TrillionClubGroupResponse()
    assert await svc.get_detail("nvidia") is None
    status, body = _err(await _call("nvidia", tier="pro"))
    assert (status, body["error_code"]) == (404, "TRILLION_CLUB_COMPANY_NOT_FOUND")
    assert db.total_reads == 0


# ══ 4. Staleness boundaries ══════════════════════════════════════════════════════════


def _stamp_all(db, stamp):
    for row in db.tables["trillion_club_companies"]:
        if row["cap_source"] != "manual":
            row["membership_checked_at"] = stamp


@pytest.mark.asyncio
async def test_membership_exactly_seven_days_old_is_still_shown(db):
    _stamp_all(db, (NOW - timedelta(days=7)).isoformat())
    assert _slugs(await _svc().get_group())[0] == "nvidia"
    assert await _svc().get_detail("nvidia") is not None


@pytest.mark.asyncio
async def test_membership_one_microsecond_past_seven_days_hides_everything(db):
    _stamp_all(db, (NOW - timedelta(days=7, microseconds=1)).isoformat())
    assert await _svc().get_group() == TrillionClubGroupResponse()
    status, body = _err(await _call("nvidia", tier="pro"))
    assert (status, body["error_code"]) == (404, "TRILLION_CLUB_COMPANY_NOT_FOUND")


@pytest.mark.asyncio
async def test_stake_staleness_boundary_through_the_group_uses_the_et_date(db):
    for s in db.tables["trillion_club_stakes"]:
        if s["investee_name"] == "OpenAI Group PBC":
            s["verified_on"] = (TODAY - timedelta(days=120)).isoformat()
        if s["investee_name"] == "G42":
            s["verified_on"] = (TODAY - timedelta(days=121)).isoformat()
    stakes = _card(await _svc().get_group(), "microsoft").stakes
    assert [(s.investee_name, s.is_stale) for s in stakes] == [
        ("OpenAI Group PBC", False), ("G42", True)]


@pytest.mark.asyncio
async def test_manual_only_club_hides_the_section_and_404s_after_one_read(db):
    db.tables["trillion_club_companies"] = [
        r for r in db.tables["trillion_club_companies"] if r["cap_source"] == "manual"
    ]
    # Even a freshly stamped hand-sized row cannot vouch: the job never re-evaluates it.
    _row(db, "samsung")["membership_checked_at"] = CHECKED
    assert await _svc().get_group() == TrillionClubGroupResponse()
    status, body = _err(await _call("samsung", tier="pro"))
    assert (status, body["error_code"]) == (404, "TRILLION_CLUB_COMPANY_NOT_FOUND")
    # Only the registry is read — nothing is fetched for a section that will not show.
    assert set(db.reads) == {"trillion_club_companies"}


# ══ 5. What earns a card ═════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_a_member_whose_only_material_stake_is_refused_is_listed_not_carded(db):
    for s in db.tables["trillion_club_stakes"]:
        if s["company_slug"] == "tesla":
            s["source_url"] = "http://insecure.example.com/x"      # refused by the validator
    db.tables["trillion_club_stakes"].append(_st("tesla", "Some Private Co", material=False))
    group = await _svc().get_group()
    assert "tesla" not in _slugs(group) and "tesla" in _also(group)
    detail = await _svc().get_detail("tesla")                  # still has a detail page
    assert [s.investee_name for s in detail.stakes] == ["Some Private Co"]


@pytest.mark.asyncio
async def test_whale_link_with_a_whale_row_but_no_material_stake_keeps_its_card(db):
    for s in db.tables["trillion_club_stakes"]:
        if s["company_slug"] == "berkshire":
            s["material"] = False
    card = _card(await _svc().get_group(), "berkshire")
    assert card.whale_id == "whale-brk" and card.stakes == []


# ══ 6. Ordering: ties + missing / bad caps ═══════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_cap", [None, 0, -1.0, float("nan"), float("inf"), "abc", True])
async def test_ties_break_by_slug_and_bad_caps_sink_everywhere(db, bad_cap):
    rows = db.tables["trillion_club_companies"]
    db.tables["trillion_club_stakes"].append(_st("spacex", "xAI Holdings", pct=10.0))  # a card
    for slug in ("tesla", "spacex"):                      # an exact tie
        _row(db, slug)["last_market_cap"] = 2.0e12
    _row(db, "microsoft")["last_market_cap"] = bad_cap    # sinks
    _row(db, "micron")["last_market_cap"] = None          # sinks too (no card: also_in_club)
    rows.reverse()                                        # row order must not matter
    group = await _svc().get_group()
    slugs = _slugs(group)
    assert slugs.index("spacex") < slugs.index("tesla")   # tie → slug
    assert slugs[-1] == "microsoft"                       # only carded member with no cap
    assert _card(group, "microsoft").market_cap is None
    detail = await _svc().get_detail("nvidia")
    others = [m.slug for m in detail.other_members]
    assert others[-2:] == ["micron", "microsoft"]         # both capless, slug order
    assert others.index("spacex") < others.index("tesla")


# ══ 7. Notices around the due date + 5 business days ═════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("today, notice, next_due", [
    # next_due is the deadline of the quarter right after 2026-Q2 while it is still ahead;
    # never a past date (it used to stay "2026-11-16" for months) and never a LATER
    # quarter's deadline (that said "nothing due before Feb" while Q3 was overdue).
    (date(2026, 11, 16), None, "2026-11-16"),               # Q3 due date itself
    (date(2026, 11, 23), None, None),                       # 5th business day after — grace
    (date(2026, 11, 24), "latest_not_in", None),            # the day after the grace
    (date(2027, 2, 23), "latest_not_in", None),             # Q4 grace end (Presidents' Day)
    (date(2027, 2, 24), "no_newer_filing", None),           # both due dates missed
])
async def test_staleness_notice_boundaries(db, monkeypatch, today, notice, next_due):
    monkeypatch.setattr(tcs, "_today_et", lambda: today)
    card = _card(await _svc().get_group(), "nvidia")
    assert card.notice == notice
    assert card.next_due == next_due
    assert card.next_due is None or date.fromisoformat(card.next_due) >= today


@pytest.mark.asyncio
async def test_amended_beats_first_filing_and_staleness_beats_both(db, monkeypatch):
    f = db.tables["trillion_club_filings"][0]
    f["changes"] = {"comparison": "first_filing", "prev_period": None,
                    "counts": {"newly_reported": 0}, "rows": []}
    assert _card(await _svc().get_group(), "nvidia").notice == "first_filing"
    _reset()
    f["accessions"] = ["x1", "x2"]
    assert _card(await _svc().get_group(), "nvidia").notice == "amended"
    _reset()
    monkeypatch.setattr(tcs, "_today_et", lambda: date(2026, 12, 1))
    assert _card(await _svc().get_group(), "nvidia").notice == "latest_not_in"


@pytest.mark.asyncio
async def test_amended_on_equal_to_filed_on_with_one_accession_is_not_amended(db):
    db.tables["trillion_club_filings"][0]["amended_on"] = "2026-08-14"
    assert _card(await _svc().get_group(), "nvidia").notice is None


# ══ 8. Read-time club tags: aliases, detail_symbol, collisions ═══════════════════════


@pytest.mark.asyncio
async def test_detail_symbol_and_aliases_tag_with_separator_and_case_folding(db):
    # ZETA's cap symbol is ZETA; its U.S. listing (detail_symbol) is ZETB; alias BRK-style.
    db.tables["trillion_club_companies"].append(
        _co("zeta", "Zeta Corp", cap=1.1e12, symbol="ZETA", detail_symbol="ZETB",
            aliases=["ZET-C"]))
    f = db.tables["trillion_club_filings"][0]
    f["holdings"] += [_h("ZZZB00001", "zetb", "Zeta B listing", 0.1e9, 0.001),
                      _h("ZZZC00001", "ZET.C", "Zeta C class", 0.1e9, 0.001),
                      _h("ZZZD00001", "BRK/A", "Berkshire A", 0.1e9, 0.001)]
    detail = await _svc().get_detail("nvidia")
    tags = {h.name: h.club_member_slug for h in detail.holdings}
    assert tags["Zeta B listing"] == "zeta"
    assert tags["Zeta C class"] == "zeta"
    assert tags["Berkshire A"] == "berkshire"


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_a_symbol_claimed_twice_goes_to_the_first_slug_whatever_the_row_order(db, caplog, reverse):
    # "aaa-dup" and "zzz-dup" both claim ZDUP (one as detail_symbol, one as an alias).
    db.tables["trillion_club_companies"] += [
        _co("zzz-dup", "Zzz Dup", cap=1.1e12, symbol="ZZZ", aliases=["ZDUP"]),
        _co("aaa-dup", "Aaa Dup", cap=1.1e12, symbol="AAA", detail_symbol="ZDUP"),
    ]
    if reverse:
        db.tables["trillion_club_companies"].reverse()
    db.tables["trillion_club_filings"][0]["holdings"].append(
        _h("ZDUP00001", "ZDUP", "Dup Co", 0.1e9, 0.001))
    with caplog.at_level(logging.WARNING, logger=tcs.__name__):
        detail = await _svc().get_detail("nvidia")
    assert next(h for h in detail.holdings if h.name == "Dup Co").club_member_slug == "aaa-dup"
    assert "claimed by both aaa-dup and zzz-dup" in caplog.text


@pytest.mark.asyncio
async def test_non_member_force_out_and_unpublished_symbols_never_tag(db):
    db.tables["trillion_club_companies"] += [
        _co("gone", "Gone Co", cap=1.2e12, symbol="ZZUA", mode="force_out", is_member=True),
        _co("hidden-row", "Hidden Row", cap=1.2e12, symbol="ZZUB", published=False),
    ]
    detail = await _svc().get_detail("nvidia")
    tags = {h.symbol: h.club_member_slug for h in detail.holdings if h.symbol}
    assert tags["ZZUA"] is None and tags["ZZUB"] is None
    assert tags["AMD"] is None                    # auto, not a member
    assert tags["SPCX"] == "spacex"               # non-vacuity: tagging does work here


# ══ 9. The stake validator on malformed rows ═════════════════════════════════════════


def _valid(**over):
    row = _st("microsoft", "OpenAI Group PBC", pct=25.0)
    row.update(over)
    return row


@pytest.mark.parametrize("over, fragment", [
    ({"ownership_pct": "nan"}, "ownership_pct"),
    ({"ownership_pct": "inf"}, "ownership_pct"),
    ({"ownership_pct": "-inf"}, "ownership_pct"),
    ({"ownership_pct": -0.0}, "ownership_pct"),
    ({"ownership_pct": 100.0000001}, "ownership_pct"),
    ({"ownership_pct": "25%"}, "ownership_pct"),
    ({"ownership_pct": [25]}, "ownership_pct"),
    ({"disclosed_value_usd": "1e999", "value_basis": "invested"}, "disclosed_value_usd"),
    ({"disclosed_value_usd": 0, "value_basis": "invested"}, "disclosed_value_usd"),
    ({"as_of": ""}, "as_of"),
    ({"as_of": "   "}, "as_of"),
    ({"as_of": "2026-02-30"}, "as_of"),
    ({"verified_on": None}, "verified_on"),
    ({"source_url": "javascript:alert(1)"}, "source_url"),
    ({"source_url": "HTTPS://www.sec.gov/x"}, "source_url"),
    ({"source_url": "ftp://www.sec.gov/x"}, "source_url"),
    ({"source_url": "https://www.sec.gov/a\tb"}, "source_url"),
    ({"source_url": "https://www.sec.gov/a b"}, "source_url"),
    ({"source_url": 42}, "source_url"),
    ({"source_title": "x" * 121}, "source_title"),
    ({"kind": None}, "kind"),
    ({"published": "true"}, "published"),
    ({"source_confidence": "PRIMARY"}, "primary"),
    ({"local_listing": "A hot listing in Taipei"}, "banned"),
    ({"source_title": "Secret filing"}, "banned"),
    ({"background": "BETS on chips"}, "banned"),
    ({"ownership_basis": "follow their lead"}, "banned"),
    ({"investee_name": "Copy what they hold LLC"}, "banned"),
    ({"background": 7}, "background"),
    ({"kind": "commitment", "value_basis": "invested", "disclosed_value_usd": 1e9}, "commitment"),
])
def test_validator_refuses_malformed_rows(over, fragment):
    problem = stake_problem(_valid(**over), TODAY)
    assert problem is not None and fragment in problem, problem


@pytest.mark.parametrize("over", [
    {"ownership_pct": 100}, {"ownership_pct": "25"}, {"ownership_pct": 1e-9},
    {"background": "x" * 90}, {"investee_name": "  " + "y" * 60 + "  "},
    {"as_of": "2026-06-30T00:00:00+00:00"}, {"source_url": "  https://www.sec.gov/x  "},
    {"investee_name": "Holdings Hotel Alphabet"},
])
def test_validator_accepts_boundary_valid_rows(over):
    assert stake_problem(_valid(**over), TODAY) is None


@pytest.mark.asyncio
async def test_a_stake_url_with_surrounding_whitespace_is_shipped_trimmed(db):
    for s in db.tables["trillion_club_stakes"]:
        if s["investee_name"] == "G42":
            s["source_url"] = "  https://news.example.com/g42  "
    card = _card(await _svc().get_group(), "microsoft")
    assert next(s for s in card.stakes if s.investee_name == "G42").source_url == \
        "https://news.example.com/g42"


# ══ 10. JSON-safety: NaN / inf never reach the wire ══════════════════════════════════

_POISON = [float("nan"), float("inf"), float("-inf"), "nan", "1e999", "-1e999", None, True,
           False, -1, -0.0, 0, "abc", [], {}, 1e308, "  "]


def _poison(tables, rng):
    for row in tables["trillion_club_companies"]:
        for k in ("last_market_cap", "manual_cap_usd"):
            if rng.random() < 0.5:
                row[k] = rng.choice(_POISON)
    for row in tables["trillion_club_stakes"]:
        for k in ("ownership_pct", "disclosed_value_usd"):
            if rng.random() < 0.3:
                row[k] = rng.choice(_POISON)
    for f in tables["trillion_club_filings"]:
        for k in ("total_value", "position_count"):
            if rng.random() < 0.5:
                f[k] = rng.choice(_POISON)
        for h in f["holdings"]:
            for k in ("shares", "value", "weight"):
                if rng.random() < 0.5:
                    h[k] = rng.choice(_POISON)
        ch = f["changes"]
        for r in ch.get("rows", []):
            for k in ("shares", "prev_shares", "share_change", "value", "weight"):
                if rng.random() < 0.5:
                    r[k] = rng.choice(_POISON)
        if isinstance(ch.get("counts"), dict):
            for k in list(ch["counts"]):
                if rng.random() < 0.5:
                    ch["counts"][k] = rng.choice(_POISON)


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(40))
async def test_poisoned_numbers_never_reach_the_wire(db, seed):
    _poison(db.tables, random.Random(seed))
    svc = _svc()
    group = await svc.get_group()
    _strict_json(group)
    TrillionClubGroupResponse.model_validate(json.loads(_strict_json(group)))
    for slug in ("nvidia", "alphabet", "microsoft"):
        detail = await svc.get_detail(slug)
        if detail is None:
            continue
        _strict_json(detail)
        _strict_json(redact_trillion_club_detail(detail, "pro"))
        for h in detail.holdings:
            assert h.weight is None or 0 <= h.weight <= 1
            assert h.value is None or (math.isfinite(h.value) and h.value >= 0)


# ══ 11. _inflight: cancellation, failure, independence ═══════════════════════════════


@pytest.mark.asyncio
async def test_a_cancelled_detail_joiner_does_not_break_the_leader_or_others(db):
    db.gate.enabled = True
    svc = _svc()
    leader = asyncio.create_task(svc.get_detail("nvidia"))
    await asyncio.wait_for(db.gate.entered.wait(), 2)
    quitter = asyncio.create_task(svc.get_detail("nvidia"))
    patient = asyncio.create_task(svc.get_detail("nvidia"))
    await asyncio.sleep(0.01)
    quitter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await quitter
    db.gate.open.set()
    detail = await asyncio.wait_for(leader, 2)
    assert await asyncio.wait_for(patient, 2) is detail
    assert db.reads["trillion_club_companies"] == 1
    assert TrillionClubService._inflight == {}


@pytest.mark.asyncio
async def test_a_failed_detail_leader_fails_its_joiners_then_the_next_call_rebuilds(db):
    db.gate.enabled = True
    db.fail["trillion_club_companies"] = RuntimeError("db down")
    svc = _svc()
    leader = asyncio.create_task(svc.get_detail("nvidia"))
    await asyncio.wait_for(db.gate.entered.wait(), 2)
    joiner = asyncio.create_task(_call("nvidia", tier="pro"))
    await asyncio.sleep(0.01)
    db.gate.open.set()
    with pytest.raises(RuntimeError, match="db down"):
        await asyncio.wait_for(leader, 2)
    status, body = _err(await asyncio.wait_for(joiner, 2))
    assert (status, body["error_code"], body["action"]) == (503, "TRILLION_CLUB_UNAVAILABLE", "retry")
    assert TrillionClubService._inflight == {} and TrillionClubService._detail_cache == {}
    db.fail.clear()
    db.gate.enabled = False
    assert (await svc.get_detail("nvidia")).company.slug == "nvidia"


@pytest.mark.asyncio
async def test_a_cancelled_detail_leader_gives_its_endpoint_joiner_a_503_retry(db):
    db.gate.enabled = True
    svc = _svc()
    leader = asyncio.create_task(svc.get_detail("nvidia"))
    await asyncio.wait_for(db.gate.entered.wait(), 2)
    joiner = asyncio.create_task(_call("nvidia", tier="free"))
    await asyncio.sleep(0.01)
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    status, body = _err(await asyncio.wait_for(joiner, 2))
    assert (status, body["error_code"], body["action"]) == (503, "TRILLION_CLUB_UNAVAILABLE", "retry")
    assert body["details"] == {"slug": "nvidia"}


@pytest.mark.asyncio
async def test_distinct_slugs_and_the_group_build_independently(db):
    db.gate.enabled = True
    svc = _svc()
    tasks = [asyncio.create_task(svc.get_detail("nvidia")),
             asyncio.create_task(svc.get_detail("microsoft")),
             asyncio.create_task(svc.get_group())]
    for _ in range(50):
        if db.gate.calls >= 3:
            break
        await asyncio.sleep(0.005)
    assert db.gate.calls == 3, "three different keys must be three builds, not one"
    db.gate.open.set()
    nv, ms, group = await asyncio.wait_for(asyncio.gather(*tasks), 2)
    assert (nv.company.slug, ms.company.slug) == ("nvidia", "microsoft")
    assert _slugs(group)[0] == "nvidia"


@pytest.mark.asyncio
async def test_regression_a_timed_out_guard_whose_build_later_fails_logs_task_exception_never_retrieved(db, monkeypatch):
    """REGRESSION (fixed 2026-09-24). Was: WHERE: TrillionClubService.get_group_guarded:
    `asyncio.wait_for(asyncio.shield(self.get_group()), 6)`.
    WHY: on the timeout, shield's outer future is cancelled and (CPython 3.11
    `_outer_done_callback`) REMOVES the callback that would have retrieved the inner task's
    exception. get_group() RAISES on a read error by design, so when a slow Supabase then
    fails (the usual shape of an outage) the orphaned Task is garbage-collected with its
    exception unretrieved and asyncio logs 'Task exception was never retrieved' + traceback at
    ERROR on the `asyncio` logger — an unhandled-looking error (Sentry event) per Home request
    during an outage. _dedup already calls fut.exception() for exactly this reason on its
    future; the shield's Task is the one it misses.
    FIXED: the guard builds an explicit Task whose done-callback (`_settle_group_build`)
    always retrieves the exception, and logs it at WARNING once the guard stopped waiting."""
    loop = asyncio.get_running_loop()
    seen = []
    old = loop.get_exception_handler()
    loop.set_exception_handler(lambda _l, ctx: seen.append(ctx.get("message")))
    try:
        monkeypatch.setattr(tcs, "_GROUP_TIMEOUT_SECONDS", 0.05)
        db.gate.enabled = True
        svc = _svc()
        assert await svc.get_group_guarded() == TrillionClubGroupResponse()
        db.fail["trillion_club_companies"] = RuntimeError("late failure")
        db.gate.open.set()
        for _ in range(100):
            if not TrillionClubService._inflight:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.01)
        gc.collect()
        await asyncio.sleep(0)
        assert not [m for m in seen if m and "never retrieved" in m], seen
        assert TrillionClubService._group_cache == {}
    finally:
        loop.set_exception_handler(old)


# ══ 12. The guard: the last good group on a TIMEOUT, and the 24 h edge ═══════════════


@pytest.mark.asyncio
async def test_guard_timeout_serves_the_invalidated_last_good_group(db, monkeypatch):
    svc = _svc()
    good = await svc.get_group()
    tcs.invalidate()                                   # a job wrote → rebuild needed
    monkeypatch.setattr(tcs, "_GROUP_TIMEOUT_SECONDS", 0.05)
    db.gate.enabled = True                             # the rebuild is slow
    served = await asyncio.wait_for(svc.get_group_guarded(), 2)
    assert served is good
    db.gate.open.set()
    for _ in range(100):
        if not TrillionClubService._inflight:
            break
        await asyncio.sleep(0.01)
    assert TrillionClubService._group_cache[tcs._GROUP_KEY][1] is not good   # refreshed


@pytest.mark.asyncio
@pytest.mark.parametrize("age, served", [(86_399.5, True), (86_400.0, False), (86_400.5, False)])
async def test_guard_fallback_age_boundary(db, monkeypatch, age, served):
    svc = _svc()
    good = await svc.get_group()
    t0 = 1_000_000.0
    TrillionClubService._group_cache[tcs._GROUP_KEY] = (t0, good)
    TrillionClubService._invalidated_at = t0 + 1
    monkeypatch.setattr(tcs, "time", SimpleNamespace(time=lambda: t0 + age))
    db.fail["trillion_club_companies"] = RuntimeError("db down")
    out = await svc.get_group_guarded()
    assert (out is good) is served
    if not served:
        assert out == TrillionClubGroupResponse()


# ══ 13. The Home gather never raises ═════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dashboard_branch_survives_the_service_module_failing_to_import(monkeypatch, caplog):
    from app.services.home_dashboard_service import HomeDashboardService

    monkeypatch.setitem(sys.modules, "app.services.trillion_club_service", None)
    svc = HomeDashboardService.__new__(HomeDashboardService)
    with caplog.at_level(logging.WARNING):
        assert await svc._get_trillion_club_guarded() == TrillionClubGroupResponse()
    assert "Trillion club section unavailable" in caplog.text


@pytest.mark.asyncio
async def test_full_dashboard_keeps_every_other_section_when_the_club_branch_breaks(monkeypatch):
    import app.services.signals_service as sig
    from app.schemas.home_dashboard import (
        HomeDashboardResponse,
        ScannerGroupsResponse,
        SignalsGroupResponse,
        ThemesGroupResponse,
    )
    from app.services.home_dashboard_service import HomeDashboardService

    class _Sig:
        async def get_signals_guarded(self):
            return SignalsGroupResponse()

    class _Boom:
        async def get_group_guarded(self):
            raise KeyError("guard broke its promise")

    async def _empty_list(*_a, **_k):
        return []

    async def _scanners(*_a, **_k):
        return ScannerGroupsResponse()

    async def _themes(*_a, **_k):
        return ThemesGroupResponse()

    async def _watch(*_a, **_k):
        return ("Tech", True, [])

    monkeypatch.setattr(sig, "get_signals_service", lambda: _Sig())
    monkeypatch.setattr(tcs, "get_trillion_club_service", lambda: _Boom())
    monkeypatch.setattr(HomeDashboardService, "_get_pulse_guarded", _empty_list)
    monkeypatch.setattr(HomeDashboardService, "_get_scanners_guarded", _scanners)
    monkeypatch.setattr(HomeDashboardService, "_get_themes_guarded", _themes)
    monkeypatch.setattr(HomeDashboardService, "_get_watchlist_guarded", _watch)
    svc = HomeDashboardService.__new__(HomeDashboardService)
    resp = await svc.get_dashboard(user_id="u-1", tier="pro")
    assert isinstance(resp, HomeDashboardResponse)
    assert resp.trillion_club == TrillionClubGroupResponse()
    assert (resp.watchlist_title, resp.watchlist_is_group) == ("Tech", True)
    json.dumps(resp.model_dump(mode="json"), allow_nan=False)


# ══ 14. Slug validation ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", [
    "a" * 41, "Nvidia", "nvidia\n", "\nnvidia", "nvidia\r", "nvıdia", "ｎvidia",
    "nvidia\x00", "nv_idia", "nvidia.", "nvidia ", " nvidia", "%6evidia", "nvidia/", "..",
    "٦٧",  # Arabic-Indic digits: \d would accept them, [0-9] must not
])
async def test_malformed_slugs_are_400_and_read_nothing(db, slug):
    status, body = _err(await _call(slug))
    assert (status, body["error_code"]) == (400, "INVALID_INPUT")
    assert len(body["details"]["slug"]) <= 60
    assert db.total_reads == 0
    assert await _svc().get_detail(slug) is None and db.total_reads == 0


@pytest.mark.asyncio
async def test_an_enormous_slug_is_truncated_in_both_message_and_details(db):
    status, body = _err(await _call("x" * 10_000 + "!"))
    assert status == 400 and len(body["details"]["slug"]) == 60 and len(body["message"]) <= 500


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["a", "-", "---", "a" * 40, "0", "nvidia-2", "9-9"])
async def test_well_formed_unknown_slugs_are_404_not_400(db, slug):
    status, body = _err(await _call(slug))
    assert (status, body["error_code"]) == (404, "TRILLION_CLUB_COMPANY_NOT_FOUND")
    assert body["details"] == {"slug": slug} and body["action"] is None


# ══ 15. 404 vs 503 ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("slug, table, cols", [
    ("nvidia", "trillion_club_companies", None),
    ("nvidia", "trillion_club_stakes", None),
    ("nvidia", "trillion_club_filings", "cik,period,period_end,filed_on,total_value,position_count"),
    ("nvidia", "trillion_club_filings", "*"),            # the index read works, the full row fails
    ("berkshire", "whales", None),
])
async def test_every_detail_read_failure_is_503_retry_never_404_or_500(db, slug, table, cols):
    boom = RuntimeError(f"{table} down: secret-internal-detail")
    db.fail_if = lambda t, c: boom if t == table and (cols is None or c == cols) else None
    resp = await _call(slug, tier="pro")
    status, body = _err(resp)
    assert (status, body["error_code"], body["action"]) == (503, "TRILLION_CLUB_UNAVAILABLE", "retry")
    assert "secret-internal-detail" not in json.dumps(body)
    assert TrillionClubService._detail_cache == {}
    db.fail_if = None
    assert isinstance(await _call(slug, tier="pro"), TrillionClubDetailResponse)


@pytest.mark.asyncio
async def test_stale_membership_is_404_while_a_read_error_is_503(db):
    _stamp_all(db, (NOW - timedelta(days=9)).isoformat())
    status, body = _err(await _call("nvidia"))
    assert (status, body["error_code"]) == (404, "TRILLION_CLUB_COMPANY_NOT_FOUND")
    db.fail["trillion_club_companies"] = RuntimeError("x")
    status, body = _err(await _call("nvidia"))
    assert (status, body["error_code"]) == (503, "TRILLION_CLUB_UNAVAILABLE")


def test_error_codes_are_registered_with_status_copy_and_action():
    from app.api.error_response import (
        _DEFAULT_ACTIONS,
        _DEFAULT_STATUS,
        _USER_MESSAGES,
        ErrorCode,
    )

    nf, un = ErrorCode.TRILLION_CLUB_COMPANY_NOT_FOUND, ErrorCode.TRILLION_CLUB_UNAVAILABLE
    assert (_DEFAULT_STATUS[nf], _DEFAULT_STATUS[un]) == (404, 503)
    assert _DEFAULT_ACTIONS.get(nf) is None and _DEFAULT_ACTIONS[un] == "retry"
    for code in (nf, un):
        copy_ = _USER_MESSAGES[code]
        assert copy_ and "report" not in copy_.lower() and "Something went wrong" not in copy_


# ══ Regressions — were test_BUG_* (left failing on purpose), fixed 2026-09-24 ═════════


def _gap_changes():
    """EXACTLY what the core builder writes for a `gap` quarter (builder.build_filing →
    diff_13f_positions(comparison='gap')): no rows, every count 0 — it never compares."""
    from app.services._whale_common import diff_13f_positions

    return diff_13f_positions([], None, split_ratios={}, unclassified=set(), comparison="gap",
                              prev_ipo_cutoff=None, prev_period="2025-Q4")


@pytest.mark.asyncio
async def test_regression_gap_quarter_labels_every_holding_unchanged_and_claims_zero_changes(db):
    """REGRESSION (fixed 2026-09-24). Was: WHERE: trillion_club_service._holdings and
    _change_counts.
    WHY: for comparison='gap' the builder does NOT diff (diff_13f_positions returns rows=[] and
    all-zero counts because a non-adjacent quarter would book months as one change). The
    service treated gap like 'quarter': every holding with no change row became 'unchanged',
    and the zero counts became ClubChangeCountsResponse(0,…). iOS then rendered
    "vs 2025-Q4, the latest earlier 13F: no share-count changes" and an Unchanged pill on every
    holding — a factual claim about share counts nobody compared.
    FIXED: only a 'quarter' comparison is compared (`_compared`): a gap's holdings carry no
    change, its counts are None and it has no change rows — like a first filing — while the
    card keeps comparison='gap' and its prev_period."""
    db.tables["trillion_club_filings"][0]["changes"] = _gap_changes()
    detail = await _svc().get_detail("nvidia")
    assert detail.company.comparison == "gap"                  # the comparison is still shown
    assert detail.company.prev_period == "2025-Q4"             # ...and what it is against
    assert [h.change for h in detail.holdings if h.change == "unchanged"] == [], \
        "a gap filing compared nothing, yet every holding is labelled 'unchanged'"
    assert all(h.change is None for h in detail.holdings)
    assert detail.company.change_counts is None, \
        "all-zero counts on a gap read as 'no share-count changes'"
    assert detail.changes == []
    card = _card(await _svc().get_group(), "nvidia")           # the Home card agrees
    assert card.change_counts is None and all(h.change is None for h in card.top_holdings)


@pytest.mark.asyncio
async def test_regression_no_longer_reported_row_ships_a_symbol_its_own_quarter_marked_non_routable(db):
    """REGRESSION (fixed 2026-09-24). Was: WHERE: trillion_club_service._changes: a row with no
    CURRENT holding kept `raw_symbol.upper()`.
    WHY: every other symbol on the wire is gated on routability (holdings via the builder's
    `routable`, ClubStakeResponse 'only when routable'); iOS makes every change row with a
    symbol a Button → TickerDetailView. 'No longer reported' is typically a merger /
    delisting, so the symbol is dead or recycled — and here the PREVIOUS quarter's own stored
    holdings say that very CUSIP was NOT routable. The builder resolves prev-quarter symbols
    but never profiles them, so nothing vouched for them.
    FIXED: such a row ships a symbol only when something vouches — a `routable` flag on the
    row, else the stored ADJACENT previous quarter's holding (read in the same round trip);
    nothing vouching → None."""
    detail = await _svc().get_detail("nvidia")
    gone = next(c for c in detail.changes if c.change == "no_longer_reported")
    prev = next(h for h in _nvda_q1()["holdings"] if h["cusip"] == "ZZGN00001")
    assert prev["routable"] is False                            # the stored evidence
    assert gone.symbol is None, f"non-routable symbol {gone.symbol!r} shipped as tappable"
    assert gone.name == "Gone Holdings Inc"                     # still listed, just not a link


@pytest.mark.asyncio
async def test_regression_a_huge_integer_in_holdings_json_hides_the_whole_section(db):
    """REGRESSION (fixed 2026-09-24). Was: WHERE: trillion_club_service._finite: `float(value)`
    caught only (TypeError, ValueError). `float(10**400)` raises OverflowError.
    WHY: JSONB numbers are arbitrary precision and PostgREST → json.loads yields a Python int.
    One oversized value in ONE company's holdings escaped the row-level degradation the module
    promises ('None for … non-numeric'), failed the whole group build, and the guard then hid
    the section for EVERY company (and the detail 503'd forever).
    FIXED: `except (TypeError, ValueError, OverflowError)` in _finite; `_count` also refuses a
    count iOS could not decode as Int."""
    db.tables["trillion_club_filings"][0]["holdings"][4]["value"] = 10 ** 400
    db.tables["trillion_club_filings"][0]["position_count"] = 10 ** 400
    group = await _svc().get_group_guarded()
    assert "microsoft" in _slugs(group), "one bad JSONB number hid the entire section"
    assert _card(group, "nvidia").position_count == 8          # falls back to len(holdings)
    detail = await _svc().get_detail("nvidia")
    assert next(h for h in detail.holdings if h.name == "Umbra Unchanged A").value is None
    _strict_json(group)


@pytest.mark.asyncio
async def test_regression_a_non_string_comparison_in_changes_json_hides_the_whole_section(db):
    """REGRESSION (fixed 2026-09-24). Was: WHERE: trillion_club_service._comparison:
    `value in _COMPARISONS` on a frozenset raised TypeError('unhashable type') when
    changes.comparison was a list/dict.
    WHY: _json() only checks that `changes` is a dict; its members are trusted. One malformed
    JSONB field failed the group build → the guard hid the section for every company.
    FIXED: `isinstance(value, str)` before every JSONB value meets a set/dict membership test
    (_comparison, _changes, _holdings, _parse_company, _read_stakes)."""
    db.tables["trillion_club_filings"][0]["changes"]["comparison"] = ["quarter"]
    group = await _svc().get_group_guarded()
    assert "microsoft" in _slugs(group), "one bad JSONB field hid the entire section"
    assert _card(group, "nvidia").comparison is None


@pytest.mark.asyncio
async def test_regression_a_non_string_change_kind_in_a_change_row_503s_the_detail(db):
    """REGRESSION (fixed 2026-09-24). Was: WHERE: trillion_club_service._changes:
    `change not in _CHANGE_ORDER` (a dict) raised TypeError for an unhashable `change`
    (list/dict) instead of dropping the row.
    WHY: the function's own contract is 'unknown change → dropped with a WARNING'; instead
    the detail build raised and the endpoint answered 503 'try again' on every retry.
    FIXED: `if not isinstance(change, str) or change not in _CHANGE_ORDER:` (log + skip)."""
    db.tables["trillion_club_filings"][0]["changes"]["rows"][1]["change"] = {"kind": "decreased"}
    resp = await _call("nvidia", tier="pro")
    assert isinstance(resp, TrillionClubDetailResponse), "one malformed change row 503s the page"
    assert [c.change for c in resp.changes] == ["newly_reported", "no_longer_reported"]


@pytest.mark.asyncio
async def test_regression_a_member_whose_own_check_is_a_month_old_keeps_its_card(db):
    """REGRESSION (fixed 2026-09-24). Was: WHERE: trillion_club_service._membership_problem /
    _members: only the NEWEST membership_checked_at across all FMP-sized companies was checked.
    WHY: jobs.py fails CLOSED per company ('the stored state is kept and membership_checked_at
    is NOT advanced'), e.g. a renamed/delisted cap_symbol returning no closes. That company's
    is_member was then frozen indefinitely while every other company kept the section fresh —
    after 30 days the 20-close leave rule could have fired unseen, yet it kept a '$1T club'
    card on Home (the job only logs STALE).
    FIXED: `_members` leaves out any member whose OWN evidence is stale (`_member_problem`,
    WARNING with the slug): FMP-sized by its checked_at, hand-sized by manual_cap_as_of."""
    _row(db, "tesla")["membership_checked_at"] = (NOW - timedelta(days=30)).isoformat()
    group = await _svc().get_group()
    assert "nvidia" in _slugs(group)                            # the section itself is fresh
    assert "tesla" not in _slugs(group) + _also(group), \
        "membership frozen for 30 days still shown as a club member"


@pytest.mark.asyncio
async def test_regression_a_future_membership_stamp_keeps_a_dead_job_fresh_forever(db):
    """REGRESSION (fixed 2026-09-24). Was: WHERE: trillion_club_service._membership_problem:
    `now - newest > 7 days` with a FUTURE newest is negative → always fresh.
    WHY: one typo'd stamp (a Studio edit, 2031 for 2026) vouched for the whole registry for
    years — exactly the failure the stake validator guards against for verified_on ('a typo'd
    year would otherwise make verified_on read as never stale'), but not here.
    FIXED: a stamp more than 5 minutes ahead of now is ignored for the section (WARNING), and
    its own company is left out."""
    _stamp_all(db, (NOW - timedelta(days=30)).isoformat())      # the job is dead
    _row(db, "micron")["membership_checked_at"] = "2031-09-24T11:00:00+00:00"
    assert await _svc().get_group() == TrillionClubGroupResponse(), \
        "a far-future stamp kept a 30-day-dead membership job looking fresh"


@pytest.mark.parametrize("url", ["https://", "https:///", "https:///www.sec.gov/x", "https://?q=1"])
def test_regression_a_hostless_https_source_url_passes_the_validator(url):
    """REGRESSION (fixed 2026-09-24). Was: WHERE: trillion_club_service.stake_problem: only
    `startswith('https://')` and no whitespace were checked (the migration CHECK is the same
    regex).
    WHY: a stake is shown as a SOURCED fact; a link with no host opens nothing, so the row is
    effectively unsourced — the case the validator exists to drop.
    FIXED: `_source_url_problem` — urlsplit; scheme https, a host containing a dot, and no
    userinfo ('@' in the netloc)."""
    assert stake_problem(_valid(source_url=url), TODAY) is not None, f"{url!r} accepted as a source"


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["N/A", "BRK B", "—", "https://www.sec.gov/x"])
async def test_regression_a_stake_symbol_is_never_shape_checked_before_becoming_tappable(db, symbol):
    """REGRESSION (fixed 2026-09-24). Was: WHERE: trillion_club_service._parse_stake:
    `symbol=raw_symbol.upper()` for any non-blank `investee_us_symbol`; stake_problem never
    looked at the column and migration 175 has no CHECK on it ('only when routable to the
    stock detail screen' is a comment).
    WHY: a hand-kept Studio value like 'N/A', 'BRK B' or a pasted URL shipped as a symbol, and
    iOS renders it as a tappable chip that opens TickerDetailView for garbage (a '/' or space
    also breaks the /stocks/{symbol} path).
    FIXED: the stake is kept but its symbol nulled (WARNING) unless it is ASCII and
    fullmatches `[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z0-9]{1,2})?` after upper()."""
    for s in db.tables["trillion_club_stakes"]:
        if s["investee_name"] == "SpaceX":
            s["investee_us_symbol"] = symbol
    card = _card(await _svc().get_group(), "tesla")
    shipped = card.stakes[0].symbol
    assert shipped is None or re.fullmatch(r"[A-Z][A-Z0-9]{0,5}([.-][A-Z0-9]{1,2})?", shipped), \
        f"malformed stake symbol {shipped!r} shipped as tappable"
    assert card.stakes[0].investee_name == "SpaceX"             # the stake itself survives
    assert card.stakes[0].club_member_slug is None              # no tag off a malformed symbol


# ══ The route imports the service lazily (contract-eager-import-defeats-home-guard) ═══


@pytest.mark.asyncio
async def test_home_router_imports_and_the_route_503s_when_the_service_module_cannot_import(db, monkeypatch):
    """The dashboard branch's lazy import only contains a defect if NOTHING on the Home path
    imports the service eagerly. `endpoints/home.py` did (module top), so an import-time
    defect in trillion_club_service stopped the /home router — and the app — from importing.
    Now the route imports it inside the handler: a fresh import of the endpoint module must
    succeed with the service unimportable, and the route must answer the typed 503."""
    import app.api.v1.endpoints as endpoints_pkg

    monkeypatch.setattr(endpoints_pkg, "home", endpoints_pkg.home)          # restored after
    monkeypatch.delitem(sys.modules, "app.api.v1.endpoints.home")
    monkeypatch.setitem(sys.modules, "app.services.trillion_club_service", None)  # → ImportError
    import importlib

    fresh = importlib.import_module("app.api.v1.endpoints.home")            # must not raise
    resp = await fresh.get_trillion_club_detail(slug="nvidia", user={"id": "u-1", "tier": "pro"})
    status, body = _err(resp)
    assert (status, body["error_code"], body["action"]) == (503, "TRILLION_CLUB_UNAVAILABLE", "retry")
    assert db.total_reads == 0
