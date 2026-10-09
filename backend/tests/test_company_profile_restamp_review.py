"""The shared `company_profile_cache` row after a RE-STAMP — the 2026-10-09 review fixes.

1. A re-stamp kept whale_service's raw identity keys (``symbol``, ``fullTimeEmployees``,
   ``ipoDate``, city / state, exchange, currency, ``isAdr``) next to the Overview's fresh keys and
   gave the row a fresh ``cached_at``. Both readers' "is this a raw profile?" test then read the
   MIXED row as raw: `get_company_facts` and the chat's STOCK line served whale's week-old head
   count and name ("Apple Computer, Inc.", 164,000) as "cached within the last 24 hours", over the
   Overview's fresh 166,000, and the full read never refreshed it. Now:
     * `_restamped` drops those keys (`_RAW_IDENTITY_KEYS`), so a merged row never reads as raw;
     * a row that still mixes them reads as the Overview's row (`_partial`, refreshed once);
     * whale's ``companyName`` / ``image`` stay, and both re-stamping writers carry TODAY's values
       (`profile_display_fields`) instead of re-dating a week-old write's.
2. A FAILED read of the row was answered like "no row", so the facts write-back upserted its
   update alone over the row — dropping ``key_executives``, whale's logo and name, the flags.
   Now the write is skipped (logged), as the Overview's writer already did.

Hermetic: the in-memory Supabase double and fake FMP of `test_company_facts_modes.py`, the REAL
Overview writer and read helper, the REAL chat formatter.
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from app.services import company_facts_service as cfs
from app.services import stock_overview_service as sos
from app.services.stock_overview_service import StockOverviewService, profile_display_fields

from test_company_facts_modes import (
    _EXECS, _OVERVIEW_ROW, _FakeDB, _FakeFMP, _NoUpstream, _raw_profile, _settle,
)


#: What whale_service stores: the upstream profile WHOLE — a week-old read, with an old name
#: and an old head count, plus the raw-only keys nothing else reads.
def _whale_row(**over):
    row = _raw_profile(
        companyName="Apple Computer, Inc.", fullTimeEmployees="164000",
        image="https://images.example/old-AAPL.png", city="Cupertino", state="CA",
        exchangeShortName="NASDAQ", exchangeFullName="NASDAQ Global Select", cik="0000320193",
        isin="US0378331005", cusip="037833100", zip="95014", address="One Apple Park Way",
        phone="408 996 1010", defaultImage=False, isActivelyTrading=True, marketCap=3.8e12,
        beta=1.2, range="169.21-260.1", dcf=150.0, dcfDiff=-105.0,
    )
    row.update(over)
    return row


#: The Overview's payload for the same ticker TODAY (166,000 people, today's name and logo).
_OVERVIEW_TODAY = dict(_OVERVIEW_ROW, employees=166000, companyName="Apple Inc.",
                       image="https://images.example/AAPL.png")

_FRESH_PROFILE = _raw_profile(fullTimeEmployees="166000", image="https://images.example/AAPL.png")


@pytest_asyncio.fixture
async def env(monkeypatch):
    cfs._pending_writes.clear()
    db = _FakeDB()
    fmp = _FakeFMP(profiles={"AAPL": _FRESH_PROFILE}, executives={"AAPL": _EXECS})
    service = object.__new__(StockOverviewService)
    service.supabase = db
    monkeypatch.setattr(cfs, "_overview_service", lambda: service)
    monkeypatch.setattr(cfs, "_db", lambda: db)
    monkeypatch.setattr(cfs, "_fmp", lambda: fmp)
    monkeypatch.setattr(sos, "get_stock_overview_service", lambda: service)
    cfs.clear_memory()
    cfs._inflight.clear()
    cfs._profile_inflight.clear()
    yield db, fmp, service
    await _settle()
    cfs.clear_memory()
    cfs._inflight.clear()
    cfs._profile_inflight.clear()
    cfs._pending_writes.clear()


def _chat():
    from app.services.chat_service import ChatService

    return object.__new__(ChatService)


# ── 1a. the merge itself ──────────────────────────────────────────────────────

def test_a_restamp_over_a_whale_row_drops_its_identity_and_price_keys():
    now = datetime.now(timezone.utc)
    base = {"profile_json": _whale_row(), "cached_at": (now - timedelta(days=3)).isoformat()}
    payload = {k: v for k, v in _OVERVIEW_ROW.items() if k not in ("isEtf", "isFund")}
    merged = cfs.merge_profile_row(base, dict(payload, employees=166000), now=now)
    for key in cfs._RAW_IDENTITY_KEYS + cfs._RAW_PRICE_KEYS:
        assert key not in merged, key
    # Whale's holdings keys and the fund flags it stored stay (the payload carried no flags).
    assert merged["companyName"] == "Apple Computer, Inc."
    assert merged["image"] == "https://images.example/old-AAPL.png"
    assert merged["isEtf"] is False and merged["isFund"] is False
    assert merged["country"] == "US" and merged["employees"] == 166000
    assert not cfs._looks_raw_profile(merged), "a merged row never reads as a raw profile"
    assert base["profile_json"]["fullTimeEmployees"] == "164000", "input not mutated"


def test_the_chats_raw_row_test_reads_a_merged_row_as_the_overviews():
    """The chat's STOCK line keeps its OWN copy of the raw-row test: the merged row must fail
    it too, or that line prints whale's head count (the review's second reader)."""
    from app.services.chat_service import ChatService

    merged = cfs.merge_profile_row(
        {"profile_json": _whale_row(), "cached_at": datetime.now(timezone.utc).isoformat()},
        dict(_OVERVIEW_TODAY))
    assert ChatService._cached_row_as_profile(merged) is merged
    line = ChatService._format_company_profile("AAPL", merged)
    assert "Employees: 166,000" in line and "164,000" not in line


def test_an_executives_only_update_keeps_a_raw_row_raw_and_undated(monkeypatch):
    """`restamp=False` never re-dates the row, so it keeps a raw row whole (its own stamp)."""
    db = _FakeDB()
    db.put("AAPL", _whale_row(), age=timedelta(days=2))
    stamp = db.rows["AAPL"]["cached_at"]
    monkeypatch.setattr(cfs, "_db", lambda: db)
    cfs._merge_write("AAPL", {cfs.EXECUTIVES_KEY: {"fetched_at": stamp, "rows": _EXECS}},
                     restamp=False)
    row = db.rows["AAPL"]
    assert row["cached_at"] == stamp and row["profile_json"]["fullTimeEmployees"] == "164000"
    assert row["profile_json"][cfs.EXECUTIVES_KEY]["rows"] == _EXECS


# ── 1b. end to end: whale row → detail view → Ask Cay AI ─────────────────────

@pytest.mark.asyncio
async def test_whale_row_then_overview_write_serves_the_overviews_fields(env, monkeypatch):
    """The review's scenario, step by step."""
    db, fmp, service = env
    db.put("AAPL", _whale_row(), age=timedelta(days=3))
    service._upsert_company_profile_db("AAPL", dict(_OVERVIEW_TODAY))
    blob = db.rows["AAPL"]["profile_json"]
    assert "fullTimeEmployees" not in blob and "symbol" not in blob and "ipoDate" not in blob
    assert blob["companyName"] == "Apple Inc.", "today's name, not whale's week-old one"
    assert blob["image"] == "https://images.example/AAPL.png"

    # Profile-only (the chat's STOCK line through the accessor): a hit on the row, the
    # Overview's head count, no name the row cannot vouch for.
    monkeypatch.setattr(cfs, "_fmp", lambda: _NoUpstream())
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert out["employees"] == 166000
    assert "name" not in out and "Apple Computer" not in json.dumps(out)

    # The chat's STOCK line reading the row itself.
    line = await _chat()._get_company_profile_summary("AAPL")
    assert "Employees: 166,000" in line and "164,000" not in line

    # The full read refreshes the row ONCE (it lacks a facts block), then is a hit.
    monkeypatch.setattr(cfs, "_fmp", lambda: fmp)
    cfs.clear_memory()
    out = await cfs.get_company_facts("AAPL")
    assert out["name"] == "Apple Inc." and out["employees"] == 166000
    assert fmp.profile_calls == ["AAPL"] and fmp.exec_calls == ["AAPL"]
    await _settle()
    blob = db.rows["AAPL"]["profile_json"]
    assert blob[cfs.FACTS_KEY]["company_name"] == "Apple Inc."
    for key in cfs._RAW_IDENTITY_KEYS:
        assert key not in blob, key
    cfs.clear_memory()
    again = await cfs.get_company_facts("AAPL")
    assert again["employees"] == 166000 and again["name"] == "Apple Inc."
    assert fmp.profile_calls == ["AAPL"], "exactly one profile call in all"


@pytest.mark.asyncio
async def test_a_legacy_mixed_row_reads_as_the_overviews_row(env, monkeypatch):
    """A row that STILL mixes raw keys with the Overview's (written before this fix) is read as
    the Overview's row: its fresh fields, `_partial`, refreshed once by the full read."""
    db, fmp, _ = env
    db.put("AAPL", dict(_whale_row(), **_OVERVIEW_TODAY), age=timedelta(hours=1))
    fields = cfs._fields_from_row(db.rows["AAPL"]["profile_json"])
    assert fields["_partial"] is True and fields["employees"] == 166000
    assert fields["name"] is None
    monkeypatch.setattr(cfs, "_fmp", lambda: _NoUpstream())
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert out["employees"] == 166000 and "Apple Computer" not in json.dumps(out)
    assert "24 hours" in out["as_of_note"]
    monkeypatch.setattr(cfs, "_fmp", lambda: fmp)
    cfs.clear_memory()
    out = await cfs.get_company_facts("AAPL")
    assert out["name"] == "Apple Inc." and fmp.profile_calls == ["AAPL"]


@pytest.mark.parametrize("overview_key,value", [
    ("founded", "1980-12-12"), ("employees", 166000), ("headquarters", "Cupertino, CA"),
])
def test_any_overview_only_key_beside_raw_keys_means_not_raw(overview_key, value):
    assert cfs._looks_raw_profile(_whale_row())
    assert not cfs._looks_raw_profile(dict(_whale_row(), **{overview_key: value}))


def test_a_pure_whale_row_is_still_read_as_raw():
    """Whale's own write (fresh from upstream) is unchanged: raw, not partial."""
    fields = cfs._fields_from_row(_whale_row())
    assert fields["_partial"] is False and fields["employees"] == 164000
    assert fields["name"] == "Apple Computer, Inc." and fields["exchange"] == "NASDAQ"


@pytest.mark.asyncio
async def test_a_facts_write_over_an_older_whale_row_refreshes_name_and_logo(env):
    """The facts writer re-stamps too: whale's raw identity keys go, and the name and logo whale
    reads come from TODAY's profile, never re-dated from the week-old write."""
    db, fmp, _ = env
    db.put("AAPL", _whale_row(), age=timedelta(days=2))       # past the 24 h helper: a miss
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert out["employees"] == 166000 and fmp.profile_calls == ["AAPL"]
    await _settle()
    blob = db.rows["AAPL"]["profile_json"]
    for key in cfs._RAW_IDENTITY_KEYS + cfs._RAW_PRICE_KEYS:
        assert key not in blob, key
    assert blob["companyName"] == "Apple Inc."
    assert blob["image"] == "https://images.example/AAPL.png"
    assert blob[cfs.FACTS_KEY]["company_name"] == "Apple Inc."
    assert not cfs._looks_raw_profile(blob)


# ── profile_display_fields ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ({"companyName": " Apple Inc. ", "image": " https://images.example/AAPL.png "},
     {"companyName": "Apple Inc.", "image": "https://images.example/AAPL.png"}),
    ({"companyName": "Apple Inc.", "image": "HTTP://x.example/a.png"},
     {"companyName": "Apple Inc.", "image": "HTTP://x.example/a.png"}),
    ({"companyName": "", "image": ""}, {}),
    ({"companyName": "   ", "image": "   "}, {}),
    ({"companyName": None, "image": None}, {}),
    ({"companyName": 7, "image": ["https://x"]}, {}),
    ({"image": "javascript:alert(1)"}, {}),
    ({"image": "ftp://x.example/a.png"}, {}),
    ({"image": "https://x.example/" + "a" * 600}, {}),
    ({"companyName": "N" * 400}, {"companyName": "N" * 160}),
    ({}, {}), (None, {}), ("raw", {}), ([], {}),
])
def test_profile_display_fields(raw, expected):
    assert profile_display_fields(raw) == expected


@pytest.mark.asyncio
async def test_the_overview_payload_carries_todays_name_and_logo(monkeypatch):
    from test_stock_overview_profile_merge import _RAW, _harness

    seen = {}
    svc, _ = _harness(monkeypatch, profile=dict(_RAW, image="https://images.example/AAPL.png"))
    monkeypatch.setattr(svc, "_upsert_company_profile_db",
                        lambda t, payload: seen.update(payload=payload))
    await svc.get_overview("AAPL", "3M", "1day", False)
    assert seen["payload"]["companyName"] == "Apple Inc."
    assert seen["payload"]["image"] == "https://images.example/AAPL.png"
    for key in ("symbol", "fullTimeEmployees", "ipoDate"):
        assert key not in seen["payload"], "the payload itself never looks raw"

    seen.clear()
    svc, _ = _harness(monkeypatch, profile={k: v for k, v in _RAW.items()
                                            if k != "companyName"})
    monkeypatch.setattr(svc, "_upsert_company_profile_db",
                        lambda t, payload: seen.update(payload=payload))
    await svc.get_overview("AAPL", "3M", "1day", False)
    assert "companyName" not in seen["payload"] and "image" not in seen["payload"], \
        "a profile without them writes nothing: the merge keeps the row's own"


# ── 2. a failed read skips the facts write-back ───────────────────────────────

@pytest.mark.parametrize("restamp", [True, False])
def test_a_failed_row_read_skips_the_merge_write(restamp, caplog, monkeypatch):
    db = _FakeDB()
    db.put("AAPL", {"key_executives": {"fetched_at": "2026-10-08T00:00:00+00:00",
                                       "rows": _EXECS},
                    "image": "https://logo", "companyName": "Apple Inc.", "isEtf": False})
    before = copy.deepcopy(db.rows["AAPL"])
    db.fail_reads = True
    monkeypatch.setattr(cfs, "_db", lambda: db)
    with caplog.at_level(logging.WARNING):
        cfs._merge_write("AAPL", {"ceo": "X", cfs.FACTS_KEY: {"v": 1}}, restamp=restamp)
    assert db.writes == [] and db.rows["AAPL"] == before
    msgs = [r.getMessage() for r in caplog.records]
    assert any("SKIPPED" in m and "AAPL" in m and "RuntimeError" in m for m in msgs), msgs
    assert not any("no row for AAPL" in m for m in msgs), "a failure is not 'no row'"


def test_no_row_still_writes_the_update_alone(monkeypatch):
    """The distinction the fix keeps: a READ that found nothing is not a failure."""
    db = _FakeDB()
    monkeypatch.setattr(cfs, "_db", lambda: db)
    cfs._merge_write("AAPL", {"ceo": "X"}, restamp=True)
    assert db.rows["AAPL"]["profile_json"] == {"ceo": "X"} and len(db.writes) == 1


@pytest.mark.asyncio
async def test_an_outage_of_the_row_read_never_overwrites_the_row(env):
    """End to end: the 24 h helper and the any-age read both fail (a miss), FMP answers, and the
    write-back's own read fails too — the answer stands and the row is untouched."""
    db, fmp, _ = env
    db.put("AAPL", {"key_executives": {"fetched_at": "2026-10-08T00:00:00+00:00",
                                       "rows": _EXECS}, "image": "https://logo"})
    before = copy.deepcopy(db.rows["AAPL"])
    db.fail_reads = True
    out = await cfs.get_company_facts("AAPL", need_executives=False)
    assert out["name"] == "Apple Inc." and fmp.profile_calls == ["AAPL"]
    await _settle()
    assert db.writes == [] and db.rows["AAPL"] == before


def test_the_read_path_still_treats_a_failed_read_as_a_miss(monkeypatch, caplog):
    db = _FakeDB()
    db.fail_reads = True
    monkeypatch.setattr(cfs, "_db", lambda: db)
    with caplog.at_level(logging.WARNING):
        assert cfs._read_row_any_age("AAPL") == (None, None)
    assert any("row read failed for AAPL" in r.getMessage() for r in caplog.records)
    with pytest.raises(RuntimeError):
        cfs._read_row("AAPL")


@pytest.mark.parametrize("stored", [None, "junk", ["a"], 7])
def test_read_row_with_no_usable_row_is_none_not_an_error(stored, monkeypatch):
    db = _FakeDB()
    if stored is not None:
        db.put("AAPL", stored)
    monkeypatch.setattr(cfs, "_db", lambda: db)
    blob, _ = cfs._read_row("AAPL")
    assert blob is None
