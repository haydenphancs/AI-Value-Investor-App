"""Trillion Club stake finder — the gates, one at a time (plan v3, "Gates").

Each gate of ``app/services/trillion_club/stake_finder.py`` is broken ONCE on an otherwise
passing candidate and must turn it into the outcome the plan names (a proposal, a heads-up or a
skip — never a silent drop, never a wrong row). Plus: the identity dedupe against EVERY row of the
real seed, tombstones, the volume caps and the breaker read from the ledger, AUTO_TIERS /
AUTO_SINCE parsing, the row builder against both validators, the full-text query and its echo
check, the digest builder, and the module's import isolation.

§1 gate 1 switches · §2 gate 2 members · §3 gate 3 identity · §4 gate 4 source rules · §5 gate 5
validators · §6 gate 6 people · §7 gate 7 dates · §8 gate 8 volume · §9 gate 9 breaker · §10 gate
10 tiers and AUTO_SINCE · §11 the row builder · §12 the full-text query · §13 the digest · §14
isolation.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import logging
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional

import pytest

import _trillion_club_stake_fakes as F
import test_trillion_club_stake_backtest as B
from app.config import settings
from app.integrations.sec_edgar import SECUnavailableException
from app.services.trillion_club import rules
from app.services.trillion_club import stake_contracts as C
from app.services.trillion_club import stake_finder as SF
from app.services.trillion_club import stake_rules
from app.services.trillion_club import stake_store as SS
from app.services.trillion_club.stake_store import StakeStore
from app.services.trillion_club.store import TrillionClubStore

BACKEND = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 28, 22, 30, tzinfo=timezone.utc)
TODAY = date(2026, 10, 28)
NV_CIK = "0001045810"
ACME_CIK = "0009000001"
ACME_CUSIP = "00450J10" + str(rules.cusip_check_digit("00450J10"))
#: Another issue of the same issuer: the same CUSIP-6, a different valid CUSIP.
ACME_CUSIP_OTHER = "00450J20" + str(rules.cusip_check_digit("00450J20"))
REGISTRY = SF._parse_registry(B.seed_companies())


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(SS, "READ_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(SF, "_monotonic", lambda: 0.0)
    monkeypatch.setattr(settings, "TRILLION_CLUB_JOBS_ENABLED", True)
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_FINDER_ENABLED", True)
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "Caydex test bot test@example.com")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_TIERS", "13dg")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_SINCE", "2026-01-01")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_LOOKBACK_DAYS", 45)
    from app.services.trillion_club import jobs

    monkeypatch.setattr(jobs, "_invalidate_read_cache", lambda: None)


def ctx(stakes=(), findings=(), today: date = TODAY) -> SF._Ctx:
    return SF._Ctx(today=today, companies=REGISTRY[0], company_rows=REGISTRY[1], configs=B.CONFIGS,
                   stakes=stakes, findings=findings)


def row(**over: Any) -> Dict[str, Any]:
    """A valid phase-1 auto row for NVIDIA → Acme Robotics."""
    base = SF._row(slug="nvidia", kind="us_listed_off_13f", name="Acme Robotics", cusip=ACME_CUSIP, symbol="ACME",
                   pct=9.1, basis="of common stock", value=None, value_basis=None, as_of=date(2026, 10, 15),
                   title="NVIDIA Schedule 13G (Oct 20, 2026)",
                   url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000901/primary_doc.xml",
                   today=TODAY, investee_cik=ACME_CIK)
    base.update(over)
    return base


def raw(**over: Any) -> SF._Raw:
    base = dict(slug="nvidia", source=C.SOURCE_SCH13, accession="0001045810-26-000901", filer_cik=NV_CIK,
                form="SCHEDULE 13G", filed_on=date(2026, 10, 20), document="primary_doc.xml",
                source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000901/primary_doc.xml",
                investee_cik=ACME_CIK, tier=C.TIER_13DG, outcome=C.OUTCOME_AUTO, reason="plain_13dg", row=row(),
                label="Acme Robotics", event_date=date(2026, 10, 15))
    base.update(over)
    return SF._Raw(**base)


def stake(**over: Any) -> Dict[str, Any]:
    base = {**row(), "id": "00000000-0000-4000-9000-000000000001", "origin": "auto", "published": True,
            "withdrawn_at": None, "owner_kept_at": None, "finding_id": "00000000-0000-4000-8000-000000000001",
            "updated_at": "2026-10-20T00:00:00+00:00"}
    base.update(over)
    return base


def finding(**over: Any) -> Dict[str, Any]:
    base = {"id": "00000000-0000-4000-8000-000000000077", "company_slug": "nvidia", "status": C.STATUS_PROPOSED,
            "outcome": C.OUTCOME_PROPOSAL, "investee_cik": ACME_CIK, "proposed_row": row(),
            "created_at": "2026-10-21T00:00:00+00:00"}
    base.update(over)
    return base


# ═════════════════════════════════════════════════════════════════════════════════════════
# §1 Gate 1 — the switches
# ═════════════════════════════════════════════════════════════════════════════════════════


def _world_run(*, notifier=None, configs=None, fake=None, sec=None, now: datetime = NOW):
    fake = fake or F.make_fake(include_stakes=False)
    sec = sec or B.FakeSEC()
    notifier = notifier or B.FakeNotifier()
    summary = asyncio.run(SF.run_stakes(now, sec=sec, store=StakeStore(fake), notifier=notifier,
                                        member_configs=configs or B.CONFIGS, club_store=TrillionClubStore(fake)))
    assert fake.misuse == []
    return summary, fake, sec, notifier


@pytest.mark.parametrize("off, says", [
    (("TRILLION_CLUB_JOBS_ENABLED", False), "TRILLION_CLUB_JOBS_ENABLED is off"),
    (("TRILLION_CLUB_STAKE_FINDER_ENABLED", False), "TRILLION_CLUB_STAKE_FINDER_ENABLED is off"),
    (("SEC_EDGAR_USER_AGENT", ""), "SEC_EDGAR_USER_AGENT is not set"),
    (("SEC_EDGAR_USER_AGENT", "Caydex — test@example.com"), "SEC_EDGAR_USER_AGENT is not set"),
    (None, "the review bot is not configured"),
])
def test_gate_1_any_switch_off_runs_only_the_database_repairs(off, says, monkeypatch):
    notifier = B.FakeNotifier(available=off is not None)
    if off is not None:
        monkeypatch.setattr(settings, off[0], off[1])
    summary, fake, sec, notifier = _world_run(notifier=notifier)
    assert summary["ok"] is False and any(says in f for f in summary["failures"]), summary["failures"]
    assert sec.calls == [] and notifier.calls == [] and fake.rows(F.FINDINGS) == []


def test_gate_1_all_on_runs_every_stage(monkeypatch):
    summary, *_ = _world_run(configs={"nvidia": dataclasses.replace(B.CONFIGS["nvidia"], s2_aliases=())})
    assert set(summary["stages"]) >= {"reconcile", "s1", "s2", "expiry", "digest"}


def test_gate_1_a_notifier_whose_available_raises_is_unavailable():
    class Broken(B.FakeNotifier):
        def available(self) -> bool:
            raise RuntimeError("settings bug")

    summary, _fake, sec, _n = _world_run(notifier=Broken())
    assert summary["ok"] is False and sec.calls == []


# ═════════════════════════════════════════════════════════════════════════════════════════
# §2 Gate 2 — which members are searched
# ═════════════════════════════════════════════════════════════════════════════════════════


def _members(companies: List[Dict[str, Any]], configs=B.CONFIGS):
    parsed, _raw = SF._parse_registry(companies)
    return SF._in_scope(parsed, configs)


def test_gate_2_the_real_registry_searches_every_listed_published_member():
    members, missing = _members(B.seed_companies())
    assert [m.slug for m in members] == sorted(B.CONFIGS)
    assert missing == [], "Berkshire is whale_link; JPMorgan, Walmart and SK hynix are unpublished"


@pytest.mark.parametrize("change, why", [
    ({"published": False}, "unpublished"),
    ({"membership_mode": "force_out"}, "not an effective member"),
    ({"is_member": False}, "not an effective member"),
    ({"card_kind": "whale_link"}, "a whale link"),
])
def test_gate_2_a_member_out_of_scope_is_never_searched(change, why):
    companies = [{**c, **change} if c["slug"] == "nvidia" else c for c in B.seed_companies()]
    members, missing = _members(companies)
    assert "nvidia" not in {m.slug for m in members} and "nvidia" not in missing, why


def test_gate_2_a_member_missing_from_the_filers_file_gets_a_monthly_line():
    configs = {k: v for k, v in B.CONFIGS.items() if k != "nvidia"}
    members, missing = _members(B.seed_companies(), configs)
    assert missing == ["nvidia"] and "nvidia" not in {m.slug for m in members}
    first = datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc)
    lines, _marks, _dedupe = SF._digest_lines([], [], ctx=ctx(), now=first, waiting=0, alerts=[], missing=missing)
    assert lines == ["• NVIDIA: stake finder not enabled (not in trillion_club_filers.json)"]
    lines, *_ = SF._digest_lines([], [], ctx=ctx(), now=NOW, waiting=0, alerts=[], missing=missing)
    assert lines == [], "only on the first day of a month"


def test_gate_2_a_member_out_of_scope_makes_no_sec_call(monkeypatch):
    fake = F.make_fake(include_stakes=False)
    fake.admin_update(F.COMPANIES, {"slug": "nvidia"}, {"published": False})
    summary, _f, sec, _n = _world_run(fake=fake, configs={"nvidia": B.CONFIGS["nvidia"]})
    assert summary["ok"] is True and sec.calls == []


def test_gate_2_a_malformed_registry_row_is_skipped_loudly_never_a_failed_run(caplog):
    companies = B.seed_companies() + [{"slug": "BAD SLUG", "cap_source": "fmp_us"}]
    with caplog.at_level(logging.ERROR):
        parsed, _raw = SF._parse_registry(companies)
    assert "BAD SLUG" not in parsed and len(parsed) == len(B.seed_companies())
    assert any("registry row skipped" in r.getMessage() for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §3 Gate 3 — identity dedupe, FIRST
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_gate_3_no_row_on_file_passes():
    d = SF._decide(raw(), ctx())
    assert (d.action, d.outcome, d.reason) == ("insert", C.OUTCOME_AUTO, "plain_13dg")


def _seed_matched_by(key: str, **facts: Any) -> Dict[str, Any]:
    seed = stake(origin="seed", finding_id=None, investee_cik=None, investee_cusip=None, investee_us_symbol=None,
                 investee_name="Something Else Entirely", **facts)
    if key == "cik":
        seed["investee_cik"] = ACME_CIK
    elif key == "cusip":
        seed["investee_cusip"] = ACME_CUSIP_OTHER   # the same CUSIP-6 issuer, another issue
    else:
        seed["investee_us_symbol"] = "acme"
    return seed


@pytest.mark.parametrize("key", ["cik", "cusip", "symbol"])
def test_gate_3_a_seed_row_matched_by_any_identifier_is_an_existing_row_heads_up(key):
    seed = _seed_matched_by(key, ownership_pct=5.5)          # the filing says 9.1%: news for the owner
    d = SF._decide(raw(), ctx(stakes=[seed]))
    assert (d.action, d.outcome, d.reason) == ("insert", C.OUTCOME_HEADS_UP, "existing_row"), key
    assert d.row is None and "→" in d.detail


@pytest.mark.parametrize("key", ["cik", "cusip", "symbol"])
@pytest.mark.parametrize("facts, says", [
    ({}, "nothing the hand-kept row does not already show"),                       # the same facts
    ({"disclosed_value_usd": 1.12e8, "value_basis": "invested"},
     "nothing the hand-kept row does not already show"),                           # the row knows MORE
    ({"as_of": "2026-10-16"}, "older than the hand-kept row on file"),             # a later figure on file
])
def test_gate_3_a_refiling_that_tells_the_owner_nothing_new_is_skipped(key, facts, says):
    """Review round 2: the go-live backfill re-reads the very filings the seed rows were built
    from; "SpaceX · 7.2% → SpaceX · 7.2%" lines are noise. Skipped (recorded, counted), never a row."""
    d = SF._decide(raw(), ctx(stakes=[_seed_matched_by(key, **facts)]))
    assert (d.action, d.outcome, d.reason, d.row) == ("insert", C.OUTCOME_SKIPPED, "existing_row", None), key
    assert d.detail.startswith(says), d.detail


@pytest.mark.parametrize("facts", [
    {"ownership_basis": "of Class A"},                     # another basis
    {"as_of": "2026-09-30"},                               # an older figure on file: the filing is news
    {"ownership_pct": None, "ownership_basis": None},      # the row shows no percent; the filing does
])
def test_gate_3_a_refiling_with_a_new_shown_fact_is_still_a_heads_up(facts):
    d = SF._decide(raw(), ctx(stakes=[_seed_matched_by("cik", **facts)]))
    assert (d.outcome, d.reason) == (C.OUTCOME_HEADS_UP, "existing_row"), facts


def test_adds_nothing_needs_the_same_as_of_date():
    new = {"ownership_pct": 9.1, "ownership_basis": "of common stock", "as_of": None}
    assert SF._adds_nothing(new, {**new, "as_of": "2026-10-15"}) is False
    assert SF._adds_nothing({**new, "as_of": "2026-10-15"}, {**new, "as_of": "2026-10-15"}) is True


def test_gate_3_a_symbol_alias_of_a_club_company_matches():
    """Alphabet's GOOG and GOOGL are one company (the registry's symbol_aliases)."""
    seed = stake(origin="seed", finding_id=None, investee_cik=None, investee_cusip=None, investee_us_symbol="GOOGL",
                 investee_name="Alphabet")
    candidate = raw(row=row(investee_cusip=None, investee_us_symbol="GOOG", investee_name="Alphabet Class C"),
                    investee_cik="0009999999")
    assert SF._decide(candidate, ctx(stakes=[seed])).reason == "existing_row"


def test_gate_3_a_name_only_match_is_a_possible_match():
    seed = stake(origin="seed", finding_id=None, investee_cik=None, investee_cusip=None, investee_us_symbol=None,
                 kind="private", investee_name="Acme Robotics")
    d = SF._decide(raw(), ctx(stakes=[seed]))
    assert (d.outcome, d.reason) == (C.OUTCOME_HEADS_UP, "possible_match")


def test_gate_3_an_open_proposal_absorbs_the_filings_evidence():
    open_ = finding()
    s2 = raw(source=C.SOURCE_EFTS, tier=C.TIER_SEC_CLOSING, outcome=C.OUTCOME_PROPOSAL, reason="t1_closing",
             spans=("On October 15, 2026, the Company completed the issuance and sale of shares to NVIDIA.",))
    d = SF._decide(s2, ctx(findings=[open_]))
    assert (d.action, d.reason) == ("append", "open_proposal") and d.target["id"] == open_["id"]


def test_gate_3_a_schedule_for_an_open_proposal_has_no_evidence_to_add():
    """A 13D/13G has no verified sentences: a heads-up naming both sets of facts, never an append."""
    d = SF._decide(raw(row=row(ownership_pct=12.0)), ctx(findings=[finding()]))
    assert (d.action, d.outcome, d.reason) == ("insert", C.OUTCOME_HEADS_UP, "existing_row")
    assert "an open proposal says Acme Robotics · 9.1%" in d.detail and "12%" in d.detail


def test_gate_3_a_tombstone_blocks_the_pair():
    for status in sorted(C.TOMBSTONE_STATUSES):
        d = SF._decide(raw(), ctx(findings=[finding(status=status)]))
        assert (d.outcome, d.reason) == (C.OUTCOME_HEADS_UP, "tombstone"), status


def test_gate_3_a_withdrawn_machine_row_is_a_tombstone():
    d = SF._decide(raw(), ctx(stakes=[stake(published=False, withdrawn_at="2026-10-22T00:00:00+00:00")]))
    assert (d.outcome, d.reason) == (C.OUTCOME_HEADS_UP, "tombstone")


def test_gate_3_a_live_row_wins_over_a_tombstone_of_the_same_investee():
    live = stake(id="00000000-0000-4000-9000-000000000009", origin="seed", finding_id=None)
    dead = stake(id="00000000-0000-4000-9000-000000000001", published=False,
                 withdrawn_at="2026-10-22T00:00:00+00:00")
    d = SF._decide(raw(), ctx(stakes=[dead, live]))
    assert d.reason == "existing_row", "the live row is what is on file"


def test_gate_3_the_members_own_auto_row_takes_the_update_path():
    d = SF._decide(raw(row=row(ownership_pct=12.4, as_of="2026-10-26")), ctx(stakes=[stake()]))
    assert (d.action, d.outcome, d.tier, d.reason) == ("update", C.OUTCOME_PROPOSAL, C.TIER_UPDATE, "update")
    assert d.target["id"] == stake()["id"] and d.detail.startswith("Replaces: Acme Robotics · 9.1%")


def test_gate_3_the_same_facts_or_an_older_filing_are_skipped():
    same = SF._decide(raw(), ctx(stakes=[stake()]))
    assert (same.outcome, same.reason) == (C.OUTCOME_SKIPPED, "existing_row")
    older = SF._decide(raw(row=row(ownership_pct=8.0, as_of="2026-09-01")), ctx(stakes=[stake()]))
    assert (older.outcome, older.reason) == (C.OUTCOME_SKIPPED, "existing_row")


def test_gate_3_another_companys_filing_about_the_members_own_row_is_a_heads_up():
    s2 = raw(source=C.SOURCE_EFTS, tier=C.TIER_SEC_CLOSING, outcome=C.OUTCOME_PROPOSAL, reason="t1_closing",
             row=row(ownership_pct=None, ownership_basis=None, disclosed_value_usd=5e8, value_basis="invested"))
    d = SF._decide(s2, ctx(stakes=[stake()]))
    assert (d.outcome, d.reason) == (C.OUTCOME_HEADS_UP, "existing_row")


def test_gate_3_closing_evidence_for_a_commitment_is_an_update():
    commitment = stake(origin="proposal", kind="commitment", investee_us_symbol=None, investee_cusip=None,
                       ownership_pct=None, ownership_basis=None)
    s2 = raw(source=C.SOURCE_EFTS, tier=C.TIER_SEC_CLOSING, outcome=C.OUTCOME_PROPOSAL, reason="t1_closing",
             row=row(ownership_pct=None, ownership_basis=None, disclosed_value_usd=5e8, value_basis="invested",
                     kind="us_listed_off_13f"), closed=True)
    d = SF._decide(s2, ctx(stakes=[commitment]))
    assert (d.action, d.tier, d.reason) == ("update", C.TIER_UPDATE, "update")


@pytest.mark.parametrize("tier", [C.TIER_SEC_CLOSING, C.TIER_DIRECTED, C.TIER_INSTRUMENT])
def test_gate_3_a_closing_tier_the_template_did_not_mark_closed_never_updates_a_commitment(tier):
    """``TemplateResult.closed`` is the template's own word that the deal COMPLETED; a closing
    tier without it (a contract drift) is only a newer filing for the row, never a replacement."""
    commitment = stake(origin="proposal", kind="commitment", investee_us_symbol=None, investee_cusip=None,
                       ownership_pct=None, ownership_basis=None)
    s2 = raw(source=C.SOURCE_EFTS, tier=tier, outcome=C.OUTCOME_PROPOSAL, reason="t1_closing",
             row=row(ownership_pct=None, ownership_basis=None, disclosed_value_usd=5e8, value_basis="invested",
                     kind="us_listed_off_13f"), closed=False)
    d = SF._decide(s2, ctx(stakes=[commitment]))
    assert (d.action, d.outcome, d.reason) == ("insert", C.OUTCOME_HEADS_UP, "existing_row")


def test_gate_3_runs_before_the_row_gates():
    """A duplicate is a duplicate even when its row would also fail a validator."""
    seed = stake(origin="seed", finding_id=None, investee_cik=None)
    bad = raw(row=row(investee_name="Acme Robotics Hot Picks"))
    assert SF._decide(bad, ctx(stakes=[seed])).reason == "existing_row"


@pytest.mark.parametrize("i", range(len(B.SEED["stakes"])))
def test_gate_3_every_real_seed_row_is_found_by_its_identifiers_or_possibly_by_its_name(i):
    """A candidate carrying a seed row's CUSIP or symbol is that row (existing_row); a seed row
    with neither (private, commitment) can only be matched by name — a POSSIBLE match; a
    placeholder name ("Private companies (not named)") matches nothing at all."""
    import app.services.trillion_club.stake_names as N

    seed_rows = B.seed_stakes()
    target = seed_rows[i]
    candidate_row = row(investee_name=target["investee_name"], investee_cusip=target["investee_cusip"],
                        investee_us_symbol=target["investee_us_symbol"], company_slug=target["company_slug"])
    d = SF._decide(raw(slug=target["company_slug"], row=candidate_row, investee_cik="0009999998"),
                   ctx(stakes=seed_rows))
    if target["investee_cusip"] or target["investee_us_symbol"]:
        assert (d.outcome, d.reason) == (C.OUTCOME_HEADS_UP, "existing_row"), target
    elif N.significant_tokens(target["investee_name"]):
        assert (d.outcome, d.reason) == (C.OUTCOME_HEADS_UP, "possible_match"), target
    else:
        assert d.reason not in ("existing_row", "possible_match"), target


# ═════════════════════════════════════════════════════════════════════════════════════════
# §4 Gate 4 — the source rules (S1 through the real parser, synthetic schedules)
# ═════════════════════════════════════════════════════════════════════════════════════════


def _s1(**xml: Any) -> Dict[str, Any]:
    sec = B.FakeSEC()
    sec.set_submissions(NV_CIK, B.empty_submissions(NV_CIK, "NVIDIA CORP"))
    sec.set_submissions(ACME_CIK, B.empty_submissions(ACME_CIK, "Acme Robotics, Inc.", ["ACME"], ["Nasdaq"]))
    tickers = xml.pop("_tickers", None)
    if tickers is not None:
        sec.set_submissions(ACME_CIK, B.empty_submissions(ACME_CIK, "Acme Robotics, Inc.", tickers[0], tickers[1]))
    form = xml.pop("submission", "SCHEDULE 13G")
    B.add_filing(sec.submissions_of(NV_CIK), "0001045810-26-000901", form, "2026-10-20",
                 xml.pop("_primary", "xslSCHEDULE_13G_X02/primary_doc.xml"))
    params = dict(filer_cik=NV_CIK, issuer_cik=ACME_CIK, issuer_name="Acme Robotics, Inc.", cusip=ACME_CUSIP,
                  event="10/15/2026", persons=[("NVIDIA Corporation", "CO", "5000000", "9.1")], submission=form)
    params.update(xml)
    sec.set_document("0001045810-26-000901", "primary_doc.xml", B.synthetic_13g(**params))
    configs = {"nvidia": dataclasses.replace(B.CONFIGS["nvidia"], s2_aliases=())}
    [found] = B.classify(sec, stakes=[], holdings=None, since=date(2026, 9, 1), configs=configs)
    return found


def test_gate_4_a_plain_own_13g_is_auto_eligible():
    f = _s1()
    assert (f["outcome"], f["reason"], f["row"]["kind"]) == (C.OUTCOME_AUTO, "plain_13dg", "us_listed_off_13f")


@pytest.mark.parametrize("change, outcome, reason", [
    (dict(persons=[("NVIDIA Corporation", "HC", "5000000", "9.1")]), C.OUTCOME_PROPOSAL, "reporting_type"),
    (dict(persons=[("NVIDIA Corporation", "CO", "60000000", "61.0")]), C.OUTCOME_PROPOSAL, "percent_above_50"),
    (dict(comments="Includes 1,000,000 shares issuable upon exercise of warrants."), C.OUTCOME_PROPOSAL,
     "derivative_position"),
    (dict(comments="Includes shares issuable upon the exercise of warrants and options."), C.OUTCOME_HEADS_UP,
     "derivative_position"),
    (dict(class_title="Series B Preferred Stock"), C.OUTCOME_HEADS_UP, "class_unmapped"),
    (dict(persons=[("Someone Else LLC", "CO", "5000000", "9.1")]), C.OUTCOME_HEADS_UP, "member_line_missing"),
    (dict(issuer_cik=NV_CIK, issuer_name="NVIDIA Corporation"), C.OUTCOME_SKIPPED, "about_member"),
    (dict(persons=[("NVIDIA Corporation", "CO", "1000000", "3.2")]), C.OUTCOME_SKIPPED, "exit_filing"),
    (dict(_tickers=([], [])), C.OUTCOME_PROPOSAL, "no_us_listing"),
    (dict(_tickers=(["ACME", "ACMB"], ["Nasdaq", "NYSE"])), C.OUTCOME_HEADS_UP, "ambiguous_listing"),
    (dict(_tickers=(["ACME"], ["OTC"])), C.OUTCOME_PROPOSAL, "no_us_listing"),
    (dict(issuer_name="ACME ROBOTICS INC"), C.OUTCOME_HEADS_UP, "name_unusable"),
    (dict(submission="SC 13G", _primary="sc13g.htm"), C.OUTCOME_HEADS_UP, "legacy_schedule"),
    (dict(_primary="weird/path/primary_doc.xml"), C.OUTCOME_HEADS_UP, "schedule_unparseable"),
])
def test_gate_4_each_source_rule_downgrades_or_skips(change, outcome, reason):
    f = _s1(**change)
    assert (f["outcome"], f["reason"]) == (outcome, reason), f
    assert (f["row"] is not None) == (outcome in (C.OUTCOME_AUTO, C.OUTCOME_PROPOSAL)), f


def test_gate_4_a_proposal_without_a_us_listing_is_private_and_never_auto():
    f = _s1(_tickers=([], []))
    assert f["row"]["kind"] == "private" and f["row"]["investee_us_symbol"] is None


def test_gate_4_the_derivative_quote_reaches_the_heads_up():
    f = _s1(comments="Includes shares issuable upon the exercise of warrants and options.")
    assert "warrants and options" in f["detail"]


def test_gate_4_an_unknown_position_reason_fails_closed(monkeypatch, caplog):
    """A reason the finder has no rule for (here an S2 code) is a heads-up, with a WARNING."""
    real = SF.select_member_position

    def odd(filing, member):
        r = real(filing, member)
        return dataclasses.replace(r, ok=False, reason="multi_cik")

    monkeypatch.setattr(SF, "select_member_position", odd)
    with caplog.at_level(logging.WARNING):
        f = _s1()
    assert (f["outcome"], f["reason"], f["row"]) == (C.OUTCOME_HEADS_UP, "multi_cik", None)
    assert any("unknown position reason" in r.getMessage() for r in caplog.records)


def test_gate_4_shared_power_with_an_outside_holder_is_a_proposal(monkeypatch):
    """Plan v3: "shared power with a non-affiliate reporter → proposal"."""
    real = SF.select_member_position

    def with_reason(filing, member):
        return dataclasses.replace(real(filing, member), ok=False, reason="shared_power")

    monkeypatch.setattr(SF, "select_member_position", with_reason)
    f = _s1()
    assert (f["outcome"], f["reason"]) == (C.OUTCOME_PROPOSAL, "shared_power")
    assert f["row"] is not None


def test_gate_4_no_power_to_sell_is_a_heads_up_naming_the_numbers(monkeypatch):
    """An acquirer's support-agreement 13D (voting power only) or a power to sell below the block:
    a pre-filled proposal would read "<member> holds X%" one tap from publishing — a holding the
    member may not own. It is a digest heads-up that states the reported numbers instead."""
    real = SF.select_member_position

    def voting_only(filing, member):
        r = real(filing, member)
        person = dataclasses.replace(r.person, sole_dispositive=Decimal(0), shared_dispositive=None)
        return dataclasses.replace(r, ok=False, reason="no_dispositive_power", person=person)

    monkeypatch.setattr(SF, "select_member_position", voting_only)
    f = _s1()
    assert (f["outcome"], f["reason"], f["row"]) == (C.OUTCOME_HEADS_UP, "no_dispositive_power", None)
    assert "power to sell: sole 0, shared not stated" in f["detail"], f["detail"]
    assert f["detail"].startswith("reports ") and "%" in f["detail"], f["detail"]


def test_gate_4_a_disclaimed_position_is_a_heads_up_quoting_the_filer(monkeypatch):
    """A member that disclaims beneficial ownership (or holds solely because of an agreement): never a
    pre-filled row; the digest line quotes the filer's own sentence (stake_schedule.disclaimer_quote)."""
    real = SF.select_member_position

    def disclaimed(filing, member):
        return dataclasses.replace(real(filing, member), ok=False, reason="ownership_disclaimed",
                                   disclaimer_quote="NVIDIA expressly disclaims beneficial ownership of these shares.")

    monkeypatch.setattr(SF, "select_member_position", disclaimed)
    f = _s1()
    assert (f["outcome"], f["reason"], f["row"]) == (C.OUTCOME_HEADS_UP, "ownership_disclaimed", None)
    assert "“NVIDIA expressly disclaims beneficial ownership of these shares.”" in f["detail"], f["detail"]


@pytest.mark.parametrize("person, says", [
    (None, "sole not stated, shared not stated"),
    (SimpleNamespace(sole_dispositive=Decimal("1500000"), shared_dispositive=Decimal("0")),
     "sole 1,500,000, shared 0"),
    (SimpleNamespace(sole_dispositive="junk", shared_dispositive=None), "sole not stated, shared not stated"),
])
def test_the_power_text_never_raises(person, says):
    position = SimpleNamespace(person=person, percent=Decimal("19.9"), shares=3_000_000)
    text = SF._power_text(position)
    assert text.startswith("reports 19.9% (3,000,000 shares); power to sell: ") and says in text, text
    assert SF._power_text(SimpleNamespace()) == "reports a block; power to sell: sole not stated, shared not stated"


# ═════════════════════════════════════════════════════════════════════════════════════════
# §5 Gate 5 — the validators
# ═════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("change, says", [
    (dict(investee_name="Acme Robotics Hot Picks"), "banned wording"),
    (dict(kind="on_13f_note", company_slug="apple"), "on_13f_note belongs only on a thirteen_f card"),
    (dict(verified_on="2026-10-30"), "in the future"),
    (dict(background="Acquired in a private placement."), "migration 205"),
    (dict(source_url="https://example.com/x"), "migration 205"),
    (dict(ownership_pct=0.0), "ownership_pct"),
    (dict(investee_name="x" * 61), "investee_name"),
])
def test_gate_5_a_row_the_validators_refuse_is_a_heads_up(change, says):
    bad = row(**change)
    problem = SF._validator_problem(bad, origin="auto", company_rows=REGISTRY[1], today=TODAY)
    assert problem and says in problem, problem
    d = SF._decide(raw(row=bad, slug=bad["company_slug"]), ctx())
    assert (d.outcome, d.reason) == (C.OUTCOME_HEADS_UP, "validator") and d.row is None


def test_gate_5_an_auto_row_with_a_value_breaks_the_auto_scope_but_a_proposal_may_carry_it():
    valued = row(disclosed_value_usd=5e8, value_basis="invested")
    assert "auto_scope" in SF._validator_problem(valued, origin="auto", company_rows=REGISTRY[1], today=TODAY)
    assert SF._validator_problem(valued, origin="proposal", company_rows=REGISTRY[1], today=TODAY) is None


def test_gate_5_the_read_path_is_consulted(monkeypatch):
    import app.services.trillion_club_service as svc

    monkeypatch.setattr(svc, "stake_problem", lambda r, today=None: "read path says no")
    assert "read path says no" in SF._validator_problem(row(), origin="auto", company_rows=REGISTRY[1], today=TODAY)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §6 Gate 6 — people (fail closed)
# ═════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("patch, says", [
    (("filer_looks_like_person", lambda name: True), "reads as a person's"),
    (("stake_person_surname_hits", lambda text: ["Huang"]), "names 'Huang'"),
    (("person_names_allowed", lambda: False), "block-list is unavailable"),
    (("roster_fresh_for", lambda day: False), "sitting Congress"),
])
def test_gate_6_a_person_or_an_unusable_check_is_a_heads_up(patch, says, monkeypatch):
    from app.services.marketing import company_news_rules as R

    monkeypatch.setattr(R, patch[0], patch[1])
    d = SF._decide(raw(), ctx())
    assert (d.outcome, d.reason) == (C.OUTCOME_HEADS_UP, "person_name") and says in d.detail, d.detail


def test_gate_6_a_check_that_raises_fails_closed(monkeypatch):
    from app.services.marketing import company_news_rules as R

    def boom(*_a):
        raise RuntimeError("roster unreadable")

    monkeypatch.setattr(R, "filer_looks_like_person", boom)
    d = SF._decide(raw(), ctx())
    assert (d.outcome, d.reason, d.detail) == (C.OUTCOME_HEADS_UP, "person_name", "the person-name check failed")


def test_gate_6_a_check_that_cannot_load_fails_closed(monkeypatch):
    import app.services.marketing as marketing_pkg

    monkeypatch.setitem(sys.modules, "app.services.marketing.company_news_rules", None)
    if hasattr(marketing_pkg, "company_news_rules"):     # a submodule imported earlier is a package attribute
        monkeypatch.delattr(marketing_pkg, "company_news_rules")
    d = SF._decide(raw(), ctx())
    assert (d.outcome, d.reason) == (C.OUTCOME_HEADS_UP, "person_name")


def test_gate_6_the_real_check_passes_the_recorded_investees():
    for name in ("Nebius Group", "SpaceX", "X-Energy", "ProQR Therapeutics", "TeraWulf", "Marvell Technology (warrant)"):
        assert SF._people_problem(name, "NVIDIA Schedule 13G (Jul 20, 2026)", TODAY) is None, name
    assert SF._people_problem("Jensen Huang", "x", TODAY) is not None


# ═════════════════════════════════════════════════════════════════════════════════════════
# §7 Gate 7 — dates (ET)
# ═════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("kw, reason", [
    (dict(event_date=date(2026, 10, 25), filed_on=date(2026, 10, 20), as_of=date(2026, 10, 15)), "dates_invalid"),
    (dict(event_date=None, filed_on=date(2026, 10, 29), as_of=date(2026, 10, 15)), "dates_invalid"),
    (dict(event_date=None, filed_on=date(2026, 10, 20), as_of=date(2026, 10, 30)), "dates_invalid"),
    (dict(event_date=None, filed_on=date(2026, 10, 20), as_of=None), "dates_invalid"),
    (dict(event_date=None, filed_on=date(2026, 10, 20), as_of=date(2025, 10, 27)), "too_old"),
])
def test_gate_7_inconsistent_or_stale_dates(kw, reason):
    assert SF._dates_problem(today=TODAY, **kw)[0] == reason


def test_gate_7_the_age_boundary_is_365_days():
    assert SF._dates_problem(event_date=None, filed_on=TODAY, as_of=TODAY - timedelta(days=365), today=TODAY) is None
    assert SF._dates_problem(event_date=None, filed_on=TODAY, as_of=TODAY - timedelta(days=366),
                             today=TODAY)[0] == "too_old"


def test_gate_7_turns_a_row_into_a_heads_up():
    d = SF._decide(raw(event_date=date(2026, 10, 25)), ctx())
    assert (d.outcome, d.reason) == (C.OUTCOME_HEADS_UP, "dates_invalid")


# ═════════════════════════════════════════════════════════════════════════════════════════
# §8-§10 Gates 8, 9, 10 — read from the ledger
# ═════════════════════════════════════════════════════════════════════════════════════════


def _ledger(entries: List[Dict[str, Any]]) -> F.FakeSupabase:
    fake = F.make_fake(include_stakes=False)
    for i, e in enumerate(entries):
        fake.admin_insert(F.FINDINGS, {
            "company_slug": "nvidia", "source": "sch13", "accession": f"0001045810-26-{800 + i:06d}",
            "filer_cik": NV_CIK, "form": "SCHEDULE 13G", "filed_on": "2026-10-20", "document": "primary_doc.xml",
            "source_url": "https://www.sec.gov/Archives/edgar/data/1045810/x", "tier": "13dg",
            "outcome": e.get("outcome", "auto"), "reason": "plain_13dg", "status": e.get("status", "published"),
            "created_at": e.get("created_at", EARLIER), "published_at": e.get("published_at"),
            "owner_decision": e.get("decision"), "decided_at": e.get("decided_at")})
    return fake


def _gate(fake: F.FakeSupabase, *, filed_on: date = date(2026, 10, 20), exclude: Optional[str] = None) -> Optional[str]:
    run = SF._Run(NOW, sec=B.FakeSEC(), store=StakeStore(fake), notifier=B.FakeNotifier(), configs=B.CONFIGS,
                  configs_error=None, club_store=TrillionClubStore(fake))
    return asyncio.run(run.auto_gate(exclude=exclude, filed_on=filed_on))


TODAY_TS = "2026-10-28T15:00:00+00:00"     # 11:00 ET today
EARLIER = "2026-10-25T15:00:00+00:00"
LONG_AGO = "2026-09-01T15:00:00+00:00"     # created weeks before it went live


def test_gate_8_passes_under_the_caps():
    assert _gate(_ledger([{"published_at": TODAY_TS}] * 2)) is None


def test_gate_8_three_today_is_the_daily_cap():
    assert _gate(_ledger([{"published_at": TODAY_TS}] * 3)) == "volume_cap"


def test_gate_8_ten_in_seven_days_is_the_weekly_cap():
    assert _gate(_ledger([{"published_at": EARLIER}] * 10)) == "volume_cap"
    assert _gate(_ledger([{"published_at": EARLIER}] * 9)) is None


def test_gate_8_counts_by_the_day_a_row_went_live_never_by_created_at():
    """Review B WO-1 / GI-1 / F4: rows found on earlier days and published TODAY count today; rows
    created today but published days ago count on their own day."""
    late = [{"created_at": LONG_AGO, "published_at": TODAY_TS}] * 3
    assert _gate(_ledger(late)) == "volume_cap", "a carry-over published today counts today"
    early = [{"created_at": TODAY_TS, "published_at": EARLIER}] * 3
    assert _gate(_ledger(early)) is None, "published three days ago: not today's"
    old = [{"created_at": LONG_AGO, "published_at": "2026-10-20T15:00:00+00:00"}] * 10
    assert _gate(_ledger(old)) is None, "published more than 7 days ago: neither cap"


def test_gate_8_counts_every_publish_in_waiting_whatever_its_age():
    """An auto finding still new / staged / noted is a publish in waiting — it goes live today at
    the earliest, so it counts against today, however old it is."""
    for status in ("new", "staged", "noted"):
        assert _gate(_ledger([{"created_at": LONG_AGO, "status": status}] * 3)) == "volume_cap", status
        assert _gate(_ledger([{"created_at": LONG_AGO, "status": status}] * 2)) is None, status
    mixed = [{"status": "staged"}, {"status": "new"}, {"published_at": TODAY_TS}]
    assert _gate(_ledger(mixed)) == "volume_cap", "pending + live today"


def test_gate_8_counts_across_statuses_but_not_downgrades():
    rows = [{"published_at": TODAY_TS, "status": s} for s in ("kept", "withdrawn", "closed")]
    assert _gate(_ledger(rows)) == "volume_cap", "every row that went live counts, whatever happened next"
    downgraded = [{"published_at": TODAY_TS, "outcome": "proposal", "status": "published"}] * 5
    assert _gate(_ledger(downgraded)) is None, "a proposal the owner published is no auto publish"
    held = [{"outcome": "proposal", "status": "proposed"}] * 5
    assert _gate(_ledger(held)) is None, "a downgrade is no auto publish"
    never = [{"status": "closed"}, {"status": "reported", "outcome": "heads_up"}, {"status": "skipped"}] * 2
    assert _gate(_ledger(never)) is None, "never live, not waiting: nothing to count"


def test_gate_8_the_candidate_itself_never_counts():
    fake = _ledger([{"published_at": TODAY_TS}] * 2 + [{"status": "staged"}])
    me = [r for r in fake.rows(F.FINDINGS) if r["status"] == "staged"][0]["id"]
    assert _gate(fake) == "volume_cap"
    assert _gate(fake, exclude=me) is None


def test_gate_8_yesterday_et_is_not_today():
    yesterday = "2026-10-28T03:00:00+00:00"   # 23:00 ET on the 27th
    assert _gate(_ledger([{"published_at": yesterday}] * 3)) is None
    assert _gate(_ledger([{"published_at": yesterday}] * 10)) == "volume_cap", "but inside the 7 days"


def test_gate_8_reads_the_volume_through_two_bounded_queries_never_by_created_at():
    fake = _ledger([{"published_at": TODAY_TS}])
    fake.clear_calls()
    _gate(fake)
    reads = [r for r in fake.reads(F.FINDINGS)]
    filters = [{(op, col) for op, col, _ in r.filters} for r in reads]
    assert any({("eq", "outcome"), ("in", "status")} <= f for f in filters), "the publishes in waiting"
    assert any({("eq", "outcome"), ("gte", "published_at")} <= f for f in filters), "the publishes since"
    assert not any(col == "created_at" for r in reads for op, col, _ in r.filters if op != "gt"), \
        "gate 8 never filters on created_at"


def test_gate_9_two_owner_withdrawals_since_auto_since_pause_auto():
    w = {"created_at": EARLIER, "status": "withdrawn", "decision": "withdrawn", "decided_at": "2026-10-26T00:00:00Z"}
    assert _gate(_ledger([w])) is None
    assert _gate(_ledger([w, w])) == "breaker"


def test_gate_9_withdrawals_before_auto_since_and_studio_deletes_do_not_count(monkeypatch):
    old = {"created_at": "2025-12-01T00:00:00Z", "status": "withdrawn", "decision": "withdrawn",
           "decided_at": "2025-12-02T00:00:00Z"}
    studio = {"created_at": EARLIER, "status": "withdrawn", "decision": "deleted_in_studio",
              "decided_at": "2026-10-26T00:00:00Z"}
    assert _gate(_ledger([old, old, studio, studio])) is None


def test_gate_9_never_lifts_until_auto_since_moves_past_them(monkeypatch):
    w = {"created_at": EARLIER, "status": "withdrawn", "decision": "withdrawn", "decided_at": "2026-10-26T00:00:00Z"}
    fake = _ledger([w, w])
    assert _gate(fake) == "breaker"
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_SINCE", "2026-10-27")
    assert _gate(fake, filed_on=date(2026, 10, 28)) is None


@pytest.mark.parametrize("tiers, since, filed, want", [
    ("13dg", "2026-01-01", date(2026, 10, 20), None),
    ("", "2026-01-01", date(2026, 10, 20), "tier_off"),
    ("sec_closing", "2026-01-01", date(2026, 10, 20), "tier_off"),
    ("13dg,sec_closing", "2026-01-01", date(2026, 10, 20), None),
    ("junk", "2026-01-01", date(2026, 10, 20), "tier_off"),
    ("13dg", "", date(2026, 10, 20), "before_auto_since"),
    ("13dg", "2026-13-40", date(2026, 10, 20), "before_auto_since"),
    ("13dg", "soon", date(2026, 10, 20), "before_auto_since"),
    ("13dg", "2026-10-21", date(2026, 10, 20), "before_auto_since"),
    ("13dg", "2026-10-20", date(2026, 10, 20), None),
])
def test_gate_10_tiers_and_auto_since(tiers, since, filed, want, monkeypatch):
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_TIERS", tiers)
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_SINCE", since)
    assert _gate(_ledger([]), filed_on=filed) == want


def test_gate_10_a_deferred_or_junk_tier_is_logged_as_an_error(monkeypatch, caplog):
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_TIERS", "sec_closing")
    with caplog.at_level(logging.ERROR):
        assert _gate(_ledger([])) == "tier_off"
    assert any("[tc-stakes-config]" in r.getMessage() and "sec_closing" in r.getMessage() for r in caplog.records)


def test_gates_8_to_10_never_run_for_a_proposal(monkeypatch):
    """A proposal is not an auto publish: the run's consume only asks auto rows."""
    calls: List[Any] = []

    async def spy(self, **kw):
        calls.append(kw)
        return None

    monkeypatch.setattr(SF._Run, "auto_gate", spy)
    fake = F.make_fake(include_stakes=False)
    run = SF._Run(NOW, sec=B.FakeSEC(), store=StakeStore(fake), notifier=B.FakeNotifier(), configs=B.CONFIGS,
                  configs_error=None, club_store=TrillionClubStore(fake))
    run.ctx = ctx()
    asyncio.run(run.consume(raw(outcome=C.OUTCOME_PROPOSAL, reason="reporting_type"), "s1"))
    assert calls == [], "a proposal never asks gates 8-10"
    beta_cusip = "00450K10" + str(rules.cusip_check_digit("00450K10"))
    beta = raw(accession="0001045810-26-000902", investee_cik="0009000003", label="Beta Systems",
               row=row(investee_name="Beta Systems", investee_cusip=beta_cusip, investee_us_symbol="BETA",
                       investee_cik="0009000003"))
    asyncio.run(run.consume(beta, "s1"))
    assert calls[0] == {"exclude": None, "filed_on": date(2026, 10, 20)}, "the auto row asks first, with its date"
    assert all(c["filed_on"] == date(2026, 10, 20) for c in calls), "and the publish gates ask again"


# ═════════════════════════════════════════════════════════════════════════════════════════
# §11 The row builder
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_the_row_builder_writes_exactly_the_machine_row_columns():
    r = row()
    assert set(r) == set(stake_rules.STAKE_COLUMNS) | {"investee_cik"}
    assert (r["sort_order"], r["source_confidence"], r["material"], r["tied_to_deal"], r["published"]) == (
        900, "primary", False, False, False)
    assert r["background"] is None and r["listed_since"] is None and r["verified_on"] == TODAY.isoformat()


def test_the_row_builder_drops_a_placeholder_cusip():
    assert row(investee_cusip=None)["investee_cusip"] is None
    built = SF._row(slug="nvidia", kind="private", name="X Co", cusip="000000000", symbol=None, pct=7.9,
                    basis="of common stock", value=None, value_basis=None, as_of=TODAY, title="t", url="https://x",
                    today=TODAY, investee_cik=None)
    assert built["investee_cusip"] is None, "the all-zero placeholder is never an identity key"


def test_a_built_row_passes_both_validators_and_the_store():
    from app.services.trillion_club_service import stake_problem

    r = row()
    cols = {c: r[c] for c in stake_rules.STAKE_COLUMNS}
    assert stake_rules.stake_problems({**cols, "published": True}, REGISTRY[1], today=TODAY) == []
    assert stake_problem({**cols, "published": True}, TODAY) is None
    assert SS.stake_check_violations({**cols, "origin": "auto", "finding_id": SF._PLACEHOLDER_FINDING,
                                      "investee_cik": ACME_CIK}) == []


def test_fact_sha_is_canonical_and_refuses_nan():
    a = {"b": 1, "a": [1, 2.5], "c": None}
    assert SF._fact_sha(a) == SF._fact_sha(dict(reversed(list(a.items()))))
    assert len(SF._fact_sha(a)) == 64 and SF._fact_sha(a) != SF._fact_sha({**a, "b": 2})
    with pytest.raises(ValueError):
        SF._fact_sha({"x": float("nan")})


def test_spans_are_capped_deduplicated_and_clean():
    spans = ["x" * 50, "x" * 50, "", None, 7] + [f"{i:03d}" + "y" * 600 for i in range(200)]
    out = SF._cap_spans(spans)
    assert out[0] == "x" * 50 and out.count("x" * 50) == 1
    assert len(out) <= SF.LEDGER_SPANS_MAX
    import json

    assert len(json.dumps(out, ensure_ascii=False).encode()) <= SF.LEDGER_SPANS_MAX_BYTES + 2


# ═════════════════════════════════════════════════════════════════════════════════════════
# §12 The full-text query, its echo, paging and the window split
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_the_query_is_the_alias_and_the_eight_phrases_with_no_parentheses():
    q = SF.efts_query("NVIDIA Corporation")
    assert q == ('"NVIDIA Corporation" "securities purchase agreement" OR "share purchase agreement" OR '
                 '"stock purchase agreement" OR "subscription agreement" OR "investment agreement" OR '
                 '"concurrent private placement" OR "indicated an interest" OR "directed share issuance"')
    assert "(" not in q and ")" not in q and q.count('"') % 2 == 0
    assert q == B.MANIFEST["efts"]["efts_nvidia_2026_10"]["q"], "the query the fixtures were recorded with"
    assert SF.EFTS_ROOT_FORMS == tuple(B.MANIFEST["efts"]["efts_nvidia_2026_10"]["forms"].split(","))


def _echo(start=date(2026, 10, 1), end=date(2026, 10, 28)) -> Dict[str, Any]:
    return B.load_json("efts_nvidia_2026_10.json")


def test_the_recorded_answer_passes_the_echo_check():
    assert SF.echo_problem(_echo(), alias="NVIDIA Corporation", start=date(2026, 10, 1), end=date(2026, 10, 28),
                           offset=0) is None


@pytest.mark.parametrize("mutate", [
    lambda e: e["query"].update(size=50),
    lambda e: e["query"].update({"from": 100}),
    lambda e: e["query"]["query"]["bool"].update(must=[{"match_phrase": {"doc_text": "NVIDIA"}}]),
    lambda e: e["query"]["query"]["bool"]["should"].pop(),
    lambda e: e["query"]["query"]["bool"].update(minimum_should_match=0),
    lambda e: e["query"]["query"]["bool"].update(must_not=[{"match_phrase": {"doc_text": "x"}}]),
    lambda e: e["query"]["query"]["bool"]["filter"][0]["terms"].update(root_forms=["8-K"]),
    lambda e: e["query"]["query"]["bool"]["filter"][1]["range"]["file_date"].update(gte="2026-09-01"),
    lambda e: e["query"]["query"]["bool"]["filter"].pop(),
    lambda e: e.pop("query"),
    lambda e: e["query"]["query"].pop("bool"),
])
def test_an_echo_that_is_not_our_query_fails_closed(mutate):
    e = _echo()
    mutate(e)
    assert SF.echo_problem(e, alias="NVIDIA Corporation", start=date(2026, 10, 1), end=date(2026, 10, 28),
                           offset=0) is not None


class _BigSEC(B.FakeSEC):
    """Answers every search with ``total`` hits (generated), to drive paging and splits."""

    def __init__(self, total_for):
        super().__init__()
        self.total_for = total_for

    async def full_text_search(self, query, *, start, end, forms=(), ciks=(), offset=0):
        resp = await super().full_text_search(query, start=start, end=end, forms=forms, ciks=ciks, offset=offset)
        total, relation = self.total_for(start, end)
        hits = []
        for i in range(offset, min(offset + 100, total)):
            hits.append({"_id": f"0009{i:06d}-26-{i:06d}:doc{i}.htm", "_source": {
                "adsh": f"0009{i:06d}-26-{i:06d}", "ciks": ["0009000077"], "display_names": ["Noise Co  (CIK 0009000077)"],
                "file_date": start.isoformat(), "file_type": "8-K", "form": "8-K", "items": [], "root_forms": ["8-K"],
                "sequence": 1}})
        resp["hits"] = {"hits": hits, "total": {"relation": relation, "value": total}}
        return resp


def _search(sec, start=date(2026, 10, 1), end=date(2026, 10, 3)):
    fails: List[str] = []
    disc = SF._Discovery(ctx=ctx(), sec=SF._SecGate(sec, deadline=1e18), start=start, end=end,
                         known=None, holdings=None, fail=lambda stage, m: fails.append(m))
    hits = asyncio.run(disc._search("NVIDIA Corporation", start, end))
    return hits, fails


def test_paging_reads_every_page_up_to_the_total():
    sec = _BigSEC(lambda s, e: (250, "eq"))
    hits, fails = _search(sec, start=date(2026, 10, 1), end=date(2026, 10, 1))
    assert len(hits) == 250 and fails == [] and sec.count("full_text_search") == 3


def test_a_window_with_more_than_the_page_cap_is_split_by_day():
    sec = _BigSEC(lambda s, e: (900, "eq") if s != e else (40, "eq"))
    hits, fails = _search(sec)
    assert len(hits) == 120 and fails == [], "three days of 40"
    days = {call[1][1] for call in sec.calls if call[0] == "full_text_search"}
    assert days == {date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)}


@pytest.mark.parametrize("relation, total", [("gte", 10_000), ("eq", 10_001)])
def test_a_gte_or_over_window_total_is_split_by_day(relation, total):
    sec = _BigSEC(lambda s, e: (total, relation) if s != e else (3, "eq"))
    hits, fails = _search(sec)
    assert len(hits) == 9 and fails == []


def test_a_single_day_over_the_page_cap_reads_five_pages_and_fails_loudly():
    sec = _BigSEC(lambda s, e: (700, "eq"))
    hits, fails = _search(sec, start=date(2026, 10, 1), end=date(2026, 10, 1))
    assert len(hits) == 500 and len(fails) == 1 and "page cap" in fails[0]


def test_a_malformed_hit_is_skipped_and_a_malformed_answer_fails_the_query():
    assert SF._parse_hit({"_id": "nonsense", "_source": {}}) is None
    assert SF._parse_hit({"_id": "0001-26-1:../x", "_source": {"adsh": "0001-26-1"}}) is None
    good = B.load_json("efts_nvidia_2026_10.json")["hits"]["hits"][1]
    h = SF._parse_hit(good)
    assert (h.accession, h.document, h.root_form, h.ciks) == ("0001193125-26-436410", "d94886d424b5.htm", "424B5",
                                                              ("0001326801",))
    with pytest.raises(SF._QueryFailed):
        SF._hits_page({"hits": {"total": {"value": True}, "hits": []}})
    with pytest.raises(SF._QueryFailed):
        SF._hits_page({"hits": {"total": {"value": 3}, "hits": "x"}})


def test_conformed_names_come_from_the_display_name():
    assert SF._conformed_name("INTEL CORP  (INTC)  (CIK 0000050863)") == "INTEL CORP"
    assert SF._conformed_name("Churchill Capital Corp X/Cayman  (CCCX, CCCXU, CCCXW)  (CIK 0002007825)") == \
        "Churchill Capital Corp X/Cayman"
    assert SF._conformed_name("Anthropic, PBC  (CIK 0002130357)") == "Anthropic, PBC"
    assert SF._conformed_name(None) is None


# ═════════════════════════════════════════════════════════════════════════════════════════
# §13 The digest
# ═════════════════════════════════════════════════════════════════════════════════════════


def _heads(i: int, **over: Any) -> Dict[str, Any]:
    base = {"id": f"00000000-0000-4000-8000-{i:012d}", "company_slug": "nvidia", "status": "new",
            "outcome": "heads_up", "reason": "existing_row", "investee_cik": f"00090000{i:02d}",
            "accession": f"0001045810-26-{i:06d}", "form": "8-K", "filed_on": "2026-10-20",
            "source_url": f"https://www.sec.gov/Archives/edgar/data/1/{i}", "created_at": f"2026-10-28T22:30:00.{i:06d}Z",
            "notified_at": None, "stake_id": None, "proposed_row": {"_investee": f"Co {i}"}}
    base.update(over)
    return base


def test_the_digest_dedupes_per_member_investee_and_reason_for_30_days():
    sent = _heads(1, status="reported", notified_at="2026-10-10T00:00:00Z")
    stale = _heads(2, status="reported", notified_at="2026-09-20T00:00:00Z")
    pending = [_heads(1, id="00000000-0000-4000-8000-000000000101", accession="0001045810-26-000101"),
               _heads(2, id="00000000-0000-4000-8000-000000000102", accession="0001045810-26-000102")]
    lines, marks, deduped = SF._digest_lines(pending, [sent, stale], ctx=ctx(), now=NOW, waiting=0, alerts=[],
                                             missing=[])
    assert [d["accession"] for d in deduped] == ["0001045810-26-000101"]
    assert [m["accession"] for m in marks] == ["0001045810-26-000102"] and len(lines) == 1


def test_a_deduped_line_never_extends_the_window():
    """A line deduped away is marked reported WITHOUT notified_at, so it cannot keep later ones
    quiet for another 30 days."""
    quiet = _heads(1, status="reported", notified_at=None)
    lines, marks, deduped = SF._digest_lines([_heads(1, id="00000000-0000-4000-8000-000000000201")], [quiet],
                                             ctx=ctx(), now=NOW, waiting=0, alerts=[], missing=[])
    assert len(marks) == 1 and deduped == []


def test_the_line_cap_shows_plus_n_more_and_keeps_the_rest_for_next_time(caplog):
    pending = [_heads(i) for i in range(SF.DIGEST_MAX_LINES + 7)]
    with caplog.at_level(logging.WARNING):
        lines, marks, _ = SF._digest_lines(pending, [], ctx=ctx(), now=NOW, waiting=0, alerts=[], missing=[])
    assert lines[-1] == "• +7 more — they come in the next digest" and len(marks) == SF.DIGEST_MAX_LINES
    assert any("over the" in r.getMessage() for r in caplog.records)


def test_the_digest_lists_markers_once_and_skips_lifecycle_findings():
    expired = _heads(1, status="expired", proposed_row=B.seed_stakes()[0] | {"investee_name": "Old Co"})
    studio = _heads(2, status="withdrawn", owner_decision="deleted_in_studio")
    handed = _heads(3, status="superseded")
    owner = _heads(4, status="withdrawn", owner_decision="withdrawn")
    # an exit FILING with a live row to take down (its _event date marks it): a LOUD note, no line
    exit_note = _heads(5, reason="exit", stake_id="00000000-0000-4000-9000-000000000005",
                       proposed_row={"_investee": "Co 5", "_event": "2026-10-26"})
    # a filing REFUSED because of an exit (reason exit too, no _event): it IS a line, whatever its stake_id
    refused = _heads(6, reason="exit", stake_id="00000000-0000-4000-9000-000000000006",
                     proposed_row={"_investee": "Co 6", "_detail": "a later filing reports 5% or less"})
    lines, marks, _ = SF._digest_lines([expired, studio, handed, owner, exit_note, refused], [], ctx=ctx(), now=NOW,
                                       waiting=2, alerts=["volume_cap", "breaker"], missing=[])
    text = "\n".join(lines)
    assert "Expired unpublished" in text and "Deleted in Studio" in text and "Handed over" in text
    assert "Co 4" not in text and "Co 5" not in text, "an owner's own Withdraw and an exit note are no digest lines"
    assert "Co 6" in text, "a refusal because of an exit is a digest line"
    assert "2 message(s) wait" in text and "cap reached" in text and "paused" in text
    assert {m["id"] for m in marks} == {expired["id"], studio["id"], handed["id"], refused["id"]}


def test_the_runs_new_skips_and_the_monday_weekly_summary():
    """The count line is of THIS run's new skips (created at the run's now); the Monday summary
    lists the week's skips per investee, but only alongside a digest that has news."""
    monday = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)
    stamp = monday.isoformat()
    skipped = [_heads(i, status="skipped", reason="about_member" if i % 2 else "no_template", created_at=stamp,
                      proposed_row={"_investee": "Noise Co"}) for i in range(5)]
    lines, *_ = SF._digest_lines([], skipped, ctx=ctx(), now=monday, waiting=0, alerts=[], missing=[])
    assert "• Skipped (recorded, not shown): 5 new (no_template 3, about_member 2)" in lines
    assert "• This week's skips — Noise Co: 5" in lines
    # a retry an hour later finds them all on file: no count line, and the summary never goes alone
    lines, *_ = SF._digest_lines([], skipped, ctx=ctx(), now=monday + timedelta(hours=1), waiting=0, alerts=[],
                                 missing=[])
    assert lines == []
    # but with news, the Monday summary rides along
    lines, *_ = SF._digest_lines([_heads(9)], skipped, ctx=ctx(), now=monday + timedelta(hours=1), waiting=0,
                                 alerts=[], missing=[])
    assert "• This week's skips — Noise Co: 5" in lines and len(lines) == 2
    tuesday = monday + timedelta(days=1)
    lines, *_ = SF._digest_lines([_heads(9)], skipped, ctx=ctx(), now=tuesday, waiting=0, alerts=[], missing=[])
    assert not any("This week's skips" in line or "Skipped (recorded" in line for line in lines)


def test_a_digest_that_is_not_delivered_marks_nothing():
    fake = F.make_fake(include_stakes=False)
    sec = B.FakeSEC()
    sec.set_submissions(NV_CIK, B.empty_submissions(NV_CIK))
    B.add_filing(sec.submissions_of(NV_CIK), "0001045810-26-000901", "SC 13G", "2026-10-20", "sc13g.htm")
    notifier = B.FakeNotifier()
    notifier.fail.add("digest")
    configs = {"nvidia": dataclasses.replace(B.CONFIGS["nvidia"], s2_aliases=())}
    summary, fake, *_ = _world_run(fake=fake, sec=sec, notifier=notifier, configs=configs)
    [f] = fake.rows(F.FINDINGS)
    assert (f["reason"], f["status"]) == ("legacy_schedule", "new") and summary["ok"] is False
    notifier.fail.clear()
    summary, fake, *_ = _world_run(fake=fake, sec=sec, notifier=notifier, configs=configs, now=NOW + timedelta(hours=1))
    [f] = fake.rows(F.FINDINGS)
    assert f["status"] == "reported" and f["notified_at"] is not None and summary["ok"] is True


# ═════════════════════════════════════════════════════════════════════════════════════════
# §14 Isolation
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_the_finder_passes_the_packages_isolation_scan():
    import test_trillion_club_jobs as J

    src = Path(SF.__file__).read_text(encoding="utf-8")
    assert J._isolation_problems(src, "stake_finder.py") == []
    tree = ast.parse(src)
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert "table" not in calls and "rpc" not in calls, "every Supabase access goes through the stores"
    assert "print" not in {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def test_importing_the_finder_loads_no_fmp_telegram_or_marketing_code():
    code = ("import sys\nimport app.services.trillion_club.stake_finder\n"
            "bad = sorted(m for m in sys.modules if m.startswith(('app.integrations.fmp', 'app.integrations.telegram', "
            "'app.services.marketing', 'app.services.trillion_club.stake_review', 'app.services.trillion_club.jobs', "
            "'app.services.trillion_club_service', 'app.database')))\nprint('BAD=' + ','.join(bad))\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().endswith("BAD="), out.stdout


def test_the_public_api_has_the_contract_signatures():
    import inspect

    sig = inspect.signature(SF.run_stakes)
    assert list(sig.parameters) == ["now", "sec", "store", "notifier", "member_configs", "club_store"]
    assert all(sig.parameters[p].default is None for p in ("sec", "store", "notifier", "member_configs", "club_store"))
    sig = inspect.signature(SF.apply_owner_decision)
    assert list(sig.parameters)[:5] == ["verb", "finding_id", "message_id", "now", "store"]
    assert sig.parameters["message_id"].kind is inspect.Parameter.KEYWORD_ONLY
    sig = inspect.signature(SF.classify_only)
    assert list(sig.parameters) == ["now", "sec", "companies", "stakes", "member_configs", "since", "holdings"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in sig.parameters.values())
    assert asyncio.iscoroutinefunction(SF.run_stakes) and asyncio.iscoroutinefunction(SF.classify_only)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §15 Guards the mutation pass found untested
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_gate_3_a_live_row_wins_over_a_hidden_row_of_the_same_investee():
    """An unpublished own auto row (an exit, a held-back duplicate) and a LIVE seed row for one
    investee: the live row is what is on file — never an update of the hidden one."""
    hidden = stake(id="00000000-0000-4000-9000-000000000001", published=False)
    live = stake(id="00000000-0000-4000-9000-000000000009", origin="seed", finding_id=None, investee_cik=None)
    d = SF._decide(raw(row=row(ownership_pct=12.4, as_of="2026-10-26")), ctx(stakes=[hidden, live]))
    assert (d.action, d.reason) == ("insert", "existing_row"), (d.action, d.reason)


def test_gate_4_a_proposal_reason_with_uncountable_warrants_is_a_heads_up():
    """A non-CO filer line whose position mentions warrants it cannot count: the basis would hide
    them, so a heads-up quoting the filer — never a proposal."""
    f = _s1(persons=[("NVIDIA Corporation", "HC", "5000000", "9.1")],
            comments="Includes shares issuable upon the exercise of warrants and options.")
    assert (f["outcome"], f["reason"], f["row"]) == (C.OUTCOME_HEADS_UP, "derivative_position", None)
    assert C.REASONS["reporting_type"] in f["detail"]


def test_an_instrument_is_never_a_13f_note_even_when_the_13f_lists_the_common():
    holdings = {"nvidia": {"cusips": frozenset(), "symbols": frozenset({"MRVL", "LITE"})}}
    found = B.by_key(f for f in B.classify(B.FakeSEC(), stakes=[], holdings=holdings, configs=B.s2_only_configs())
                     if f["source"] == C.SOURCE_EFTS)
    for name in ("marvell_preferred_8k", "lumentum_preferred_8k"):
        row_ = found[("nvidia", B.acc_of(name))]["row"]
        assert row_["kind"] == "us_listed_off_13f", (name, row_["kind"])


def test_gate_8_a_downgraded_auto_whose_proposal_is_pending_does_not_count():
    """Downgraded at the publish gate but its proposal not sent yet: status still staged / noted,
    outcome already proposal — not an auto publish."""
    rows = [{"created_at": TODAY_TS, "outcome": "proposal", "status": s} for s in ("staged", "noted", "staged")]
    assert _gate(_ledger(rows)) is None


def test_dismiss_is_fenced_on_its_own_message():
    fake = F.make_fake(include_stakes=False)
    fake.admin_insert(F.FINDINGS, {
        "company_slug": "nvidia", "source": "sch13", "accession": "0001045810-26-000901", "filer_cik": NV_CIK,
        "form": "SCHEDULE 13G", "filed_on": "2026-10-20", "document": "primary_doc.xml",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1045810/x", "tier": "13dg", "outcome": "proposal",
        "reason": "reporting_type", "status": "proposed", "telegram_message_id": 77, "proposed_row": row(),
        "fact_sha": "a" * 64, "notified_at": "2026-10-20T00:00:00Z"})
    fid = fake.rows(F.FINDINGS)[0]["id"]
    for stale in (None, 78):
        result = asyncio.run(SF.apply_owner_decision(C.VERB_DISMISS, fid, message_id=stale, now=NOW,
                                                     store=StakeStore(fake), club_store=TrillionClubStore(fake)))
        assert (result.ok, result.toast) == (False, SF.TOAST_STALE)
    assert fake.rows(F.FINDINGS)[0]["status"] == "proposed"
    result = asyncio.run(SF.apply_owner_decision(C.VERB_DISMISS, fid, message_id=77, now=NOW,
                                                 store=StakeStore(fake), club_store=TrillionClubStore(fake)))
    assert result.ok and fake.rows(F.FINDINGS)[0]["status"] == "dismissed"


# ═════════════════════════════════════════════════════════════════════════════════════════
# §16 Review B (2026-10-29) — the pure halves of the fixes
# ═════════════════════════════════════════════════════════════════════════════════════════


class _OneRefusedHit(_BigSEC):
    """Page 1 carries one hit _parse_hit refuses (a CIK normalize_cik rejects)."""

    async def full_text_search(self, query, *, start, end, forms=(), ciks=(), offset=0):
        resp = await super().full_text_search(query, start=start, end=end, forms=forms, ciks=ciks, offset=offset)
        if offset == 0 and resp["hits"]["hits"]:
            resp["hits"]["hits"][0]["_source"]["ciks"] = ["not-a-cik"]
        return resp


def test_a_refused_hit_on_a_full_page_never_ends_the_paging():
    """Review B S2-PAGING-FILTERED-COUNT: the last page is decided on what EFTS SENT (100 items),
    never on the 99 _parse_hit kept — page 2 (the Intel closing, item 150 in the review's probe)
    is read."""
    sec = _OneRefusedHit(lambda s, e: (150, "eq"))
    hits, fails = _search(sec, start=date(2026, 10, 1), end=date(2026, 10, 1))
    assert sec.count("full_text_search") == 2 and len(hits) == 149 and fails == []
    assert "0009000149-26-000149" in {h.accession for h in hits}, "the hit on page 2"


def test_paging_still_stops_on_a_short_page_and_on_the_total():
    sec = _BigSEC(lambda s, e: (100, "eq"))
    hits, _ = _search(sec, start=date(2026, 10, 1), end=date(2026, 10, 1))
    assert sec.count("full_text_search") == 1 and len(hits) == 100, "offset + 100 >= total: the end"
    total, relation, page, sent = SF._hits_page({"hits": {"total": {"value": 3, "relation": "eq"},
                                                          "hits": [{"_id": "x"}, {"_id": "y"}]}})
    assert (total, relation, page, sent) == (3, "eq", [], 2), "two sent, none kept"


@pytest.mark.parametrize("tier, kind, trial", [
    (C.TIER_SEC_CLOSING, "on_13f_note", True),
    (C.TIER_SEC_CLOSING, "us_listed_off_13f", True),
    (C.TIER_SEC_CLOSING, "private", False),          # 205's auto scope would refuse it (facts-F5 side note)
    (C.TIER_SEC_CLOSING, "commitment", False),
    (C.TIER_DIRECTED, "on_13f_note", False),
    (C.TIER_INSTRUMENT, "us_listed_off_13f", False),
])
def test_only_a_closing_whose_row_could_be_an_auto_row_is_a_trial(tier, kind, trial):
    d = SF._Decision(raw=raw(tier=tier, outcome=C.OUTCOME_PROPOSAL), action="insert", outcome=C.OUTCOME_PROPOSAL,
                     tier=tier, reason="t1_closing", row=row(kind=kind))
    assert d.trial is trial
    assert SF._is_trial(tier, {"kind": kind}) is trial
    assert SF._is_trial(tier, None) is False


@pytest.mark.parametrize("status, attempts, last, due", [
    ("error", 1, "2026-10-27T22:30:00+00:00", True),   # yesterday ET
    ("error", 1, "2026-10-28T15:00:00+00:00", False),  # earlier today ET: the hourly retry spends nothing
    ("error", 2, "2026-10-28T03:59:00+00:00", True),   # 23:59 ET on the 27th
    ("error", 2, "2026-10-28T04:00:00+00:00", False),  # midnight ET on the 28th
    ("error", 3, "2026-10-20T00:00:00+00:00", False),  # out of attempts
    ("error", 1, None, True),                          # an unreadable stamp: due
    ("skipped", 0, "2026-10-01T00:00:00+00:00", False),
    ("reported", 0, None, False),
])
def test_an_error_finding_is_due_once_per_et_day(status, attempts, last, due):
    assert SF._retry_due({"status": status, "attempts": attempts, "updated_at": last}, TODAY) is due


def _pair(**over: Any) -> Dict[str, Any]:
    base = {"id": "00000000-0000-4000-8000-0000000000aa", "company_slug": "nvidia", "investee_cik": ACME_CIK,
            "source": "sch13", "form": "SCHEDULE 13G/A", "accession": "0001045810-26-000902",
            "filed_on": "2026-10-27", "status": "reported", "outcome": "heads_up", "reason": "existing_row",
            "proposed_row": {"_investee": "Acme Robotics"}}
    base.update(over)
    return base


@pytest.mark.parametrize("finding, why", [
    (_pair(reason="exit", proposed_row={"_event": "2026-10-26"}), "exit filing"),
    (_pair(reason="exit_filing", outcome="skipped", status="skipped", proposed_row={"_event": "2026-10-15"}),
     "exit filing"),                                                       # an exit ON the as-of day
    (_pair(status="expired", outcome="proposal", tier="update", reason="update",
           proposed_row=row(ownership_pct=15.0, as_of="2026-10-26")), "says"),
    (_pair(status="dismissed", outcome="proposal", reason="update", proposed_row=row(ownership_pct=12.4)), "says"),
    (_pair(status="reported", reason="derivative_position"), "not reflected"),
    (_pair(status="new", reason="existing_row"), "still being decided"),
    (_pair(status="error", reason="fetch_failed"), "still being decided"),
    (_pair(status="proposed", source="efts", outcome="proposal", reason="t1_closing"), "still being decided"),
])
def test_the_recheck_counts_a_superseding_filing_in_any_status(finding, why):
    got = SF._superseding_filing([finding], stake())
    assert got is not None and why in got, got


@pytest.mark.parametrize("finding", [
    _pair(outcome="skipped", status="skipped", proposed_row={"_investee": "Acme Robotics"}),   # the same facts
    _pair(reason="exit", proposed_row={"_event": "2026-10-01"}, filed_on="2026-10-02"),      # an exit BEFORE as_of
    _pair(filed_on="2026-10-14"),                                                             # filed before as_of
    _pair(source="efts", status="reported"),                                                 # not the member's own
    _pair(reason="recheck_negative", stake_id="x"),                                          # its own lane
    _pair(status="superseded", outcome="proposal", reason="update", proposed_row=row()),     # the same facts
    _pair(id="00000000-0000-4000-8000-000000000001", status="new"),                         # the row's own finding
    # verifier R3: an UPDATE's 13G/A dated before the original 13G's FILING — the replaced original
    # (older facts, filed later than the new as-of) is no news; nor is any finding the row replaced
    _pair(status="reported", outcome="proposal", reason="update", filed_on="2026-10-22",
          proposed_row=row(ownership_pct=12.4, as_of="2026-10-01")),
    _pair(status="superseded", outcome="auto", reason="plain_13dg", filed_on="2026-10-22",
          proposed_row=row(ownership_pct=7.7, as_of="2026-10-20")),
])
def test_the_recheck_ignores_what_supersedes_nothing(finding):
    assert SF._superseding_filing([finding], stake()) is None


@pytest.mark.parametrize("row_facts, binds", [
    ({"ownership_pct": 9.1, "as_of": "2026-10-15"}, True),           # a percentage the exit contradicts
    ({"ownership_pct": 9.1, "as_of": "2026-10-27"}, True),           # dated ON the exit day
    ({"ownership_pct": 9.1, "as_of": "2026-10-28"}, False),          # newer than the exit: a re-entry
    ({"ownership_pct": 4.0, "as_of": "2026-10-15"}, True),           # any stated percentage (S1 rows)
    ({"ownership_pct": 5.0, "as_of": "2026-10-15"}, True),           # final verify #1: an exact 5.0% 13G
    ({"ownership_pct": None, "disclosed_value_usd": 5e9, "as_of": "2026-10-15"}, False),   # "$5.0B invested"
    ({"ownership_pct": "junk", "as_of": "2026-10-15"}, False),
    ({"ownership_pct": 9.1, "as_of": None}, True),                   # no date: fail closed
])
def test_an_exit_binds_only_a_percentage_it_contradicts(row_facts, binds):
    """Verifier R2 + final verify #1: the member's 13G/A reporting 5% or less supersedes a row that
    STATES a percentage (every member-own 13D/13G row, an exact 5.0% included), dated on or before
    it — never an amount-only closing (the S2 "Intel · $5.0B invested" proposal)."""
    assert SF._exit_binds(row_facts, date(2026, 10, 27)) is binds
    assert SF._exit_binds(row_facts, None) is False


def test_the_recheck_ignores_an_exit_that_does_not_bind_an_amount_only_row():
    """Final verify #2: an S2 row ("Intel · $5.0B invested") the owner published after the member's
    exit is not contradicted by it — the Monday re-check still verifies it (else it reads stale
    after 120 days, forever). A row stating a percentage is still held by the same exit."""
    exit_finding = _pair(reason="exit", proposed_row={"_event": "2026-10-26"}, filed_on="2026-10-27")
    amount_only = stake(ownership_pct=None, ownership_basis=None, disclosed_value_usd=5e9, value_basis="invested")
    assert SF._superseding_filing([exit_finding], amount_only) is None
    assert "exit filing" in SF._superseding_filing([exit_finding], stake())


def test_two_unreadable_filings_of_one_pair_get_a_line_each():
    """Final verify #6: a fetch_failed line is the only pointer to ITS filing — never deduped by pair."""
    ctx = SF._Ctx(today=TODAY, companies=REGISTRY[0], company_rows=REGISTRY[1], configs=B.CONFIGS)
    def f(n: int) -> Dict[str, Any]:
        return {"id": f"00000000-0000-4000-8000-0000000009{n:02d}", "company_slug": "nvidia", "status": "new",
                "outcome": "heads_up", "reason": "fetch_failed", "investee_cik": ACME_CIK, "form": "SCHEDULE 13G",
                "accession": f"0001045810-26-0009{n:02d}", "filed_on": "2026-09-14", "created_at": f"2026-10-28T22:0{n}:00+00:00",
                "source_url": f"https://www.sec.gov/Archives/edgar/data/1045810/x{n}", "notified_at": None,
                "stake_id": None, "proposed_row": {"_detail": "could not be read"}}
    lines, marks, deduped = SF._digest_lines([f(1), f(2)], [], ctx=ctx, now=NOW, waiting=0, alerts=[], missing=[])
    assert len([l for l in lines if "could not be read" in l]) == 2 and deduped == []


def _exit_raw(**over: Any) -> SF._Raw:
    base = dict(slug="nvidia", source=C.SOURCE_SCH13, accession="0001045810-26-000903", filer_cik=NV_CIK,
                form="SCHEDULE 13G/A", filed_on=date(2026, 10, 28), document="primary_doc.xml",
                source_url="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000903/primary_doc.xml",
                investee_cik=ACME_CIK, tier=C.TIER_13DG, outcome=C.OUTCOME_HEADS_UP, reason="exit_filing",
                exit=True, cusip=ACME_CUSIP, label="Acme Robotics", event_date=date(2026, 10, 27),
                detail="reports 3.1% as of 2026-10-27")
    base.update(over)
    return SF._Raw(**base)


def test_an_exit_finds_the_live_row_past_an_open_update_of_it():
    """Review B TAP-1: identity puts open proposals first; the exit looks among the stake rows."""
    upd = finding(id="00000000-0000-4000-8000-0000000000bb", tier=C.TIER_UPDATE, status=C.STATUS_PROPOSED,
                  proposed_row=row(ownership_pct=12.4, as_of="2026-10-26"))
    d = SF._decide_exit(_exit_raw(), ctx(stakes=[stake()], findings=[upd]))
    assert (d.action, d.reason, d.target["id"]) == ("exit", "exit", stake()["id"])


def test_an_exit_older_than_the_live_row_never_takes_it_down():
    d = SF._decide_exit(_exit_raw(event_date=date(2026, 10, 1)), ctx(stakes=[stake()]))
    assert (d.action, d.outcome, d.reason) == ("insert", C.OUTCOME_SKIPPED, "exit_filing")
    assert "older than the row on file" in d.detail


@pytest.mark.parametrize("status", [C.STATUS_NEW, C.STATUS_STAGED, C.STATUS_NOTED])
def test_an_exit_holds_back_an_auto_find_still_on_its_way(status):
    pending = finding(status=status, outcome=C.OUTCOME_AUTO)
    stakes = [stake(published=False, finding_id=pending["id"])] if status != C.STATUS_NEW else []
    d = SF._decide_exit(_exit_raw(), ctx(stakes=stakes, findings=[pending]))
    assert (d.action, d.outcome, d.reason, d.target) == ("exit", C.OUTCOME_HEADS_UP, "exit", None)
    assert "held back before it went live" in d.detail and "Acme Robotics · 9.1%" in d.detail


def test_an_exit_voids_an_open_proposal_dated_on_or_before_it_but_not_after():
    older = finding(proposed_row=row(as_of="2026-10-27"))
    d = SF._decide_exit(_exit_raw(), ctx(findings=[older]))
    assert d.action == "exit" and "the open proposal is void" in d.detail
    newer = finding(proposed_row=row(as_of="2026-10-28"))
    d = SF._decide_exit(_exit_raw(), ctx(findings=[newer]))
    assert (d.action, d.outcome) == ("insert", C.OUTCOME_SKIPPED), "a newer proposal is a re-entry"


def test_a_second_exit_for_a_row_an_earlier_exit_took_down_is_skipped():
    """Keyed on the pending finding's STATUS: a down row whose finding is closed holds nothing."""
    closed = finding(status=C.STATUS_CLOSED, outcome=C.OUTCOME_AUTO)
    d = SF._decide_exit(_exit_raw(), ctx(stakes=[stake(published=False, finding_id=closed["id"])],
                                         findings=[closed]))
    assert (d.outcome, d.reason) == (C.OUTCOME_SKIPPED, "exit_filing") and "not live" in d.detail


def test_an_exit_on_file_refuses_an_older_row_candidate_and_lets_a_newer_one_through():
    c = ctx()
    c.note_exit("nvidia", ACME_CIK, date(2026, 10, 26))
    d = SF._decide(raw(), c)                                     # as of Oct 15
    assert (d.action, d.outcome, d.reason) == ("insert", C.OUTCOME_HEADS_UP, "exit")
    d = SF._decide(raw(row=row(as_of="2026-10-26")), c)          # the exit's own day: still refused
    assert d.reason == "exit"
    d = SF._decide(raw(row=row(as_of="2026-10-27")), c)          # a re-entry after the exit
    assert (d.outcome, d.reason) == (C.OUTCOME_AUTO, "plain_13dg")
    d = SF._decide(raw(investee_cik="0009000077"), c)           # another pair
    assert d.outcome == C.OUTCOME_AUTO


def test_an_exit_on_file_refuses_an_update_older_than_it():
    """Review B TAP-2: the update branch is never reached for a candidate older than the exit."""
    c = ctx(stakes=[stake(published=False)])
    c.note_exit("nvidia", ACME_CIK, date(2026, 10, 27))
    d = SF._decide(raw(row=row(ownership_pct=12.4, as_of="2026-10-26")), c)
    assert (d.action, d.reason) == ("insert", "exit")
    d = SF._decide(raw(row=row(ownership_pct=8.0, as_of="2026-10-28")), c)      # a re-entry after it
    assert (d.action, d.reason, d.tier) == ("update", "update", C.TIER_UPDATE)


def test_only_an_exit_filing_is_an_exit_marker():
    """A finding refused BECAUSE of an exit (reason exit, no _event) is never one."""
    assert SF._exit_event({"proposed_row": {"_event": "2026-10-26"}}) == date(2026, 10, 26)
    assert SF._exit_event({"reason": "exit", "proposed_row": {"_detail": "x"}}) is None
    assert SF._exit_event({"proposed_row": None}) is None and SF._exit_event({}) is None
    assert SF._newest_exit([{"proposed_row": {"_event": d}} for d in ("2026-10-01", "2026-10-26", "junk")]) == \
        date(2026, 10, 26)
    c = ctx(findings=[{"id": "00000000-0000-4000-8000-0000000000cc", "company_slug": "nvidia",
                       "investee_cik": ACME_CIK, "reason": "exit", "proposed_row": {"_detail": "refused"}}])
    assert c.exit_on("nvidia", ACME_CIK) is None


def test_classify_only_keeps_what_it_classified_when_one_query_fails():
    """Review B S2-SECEXCEPTION (B2): an upstream failure of one query is a listed problem — the
    findings already classified are returned, and the other aliases are still searched."""
    sec = B.FakeSEC()
    sec.fail["full_text_search"] = [SECUnavailableException("HTTP 503 (after one retry)")]
    found = B.classify(sec, stakes=[])
    assert isinstance(found, SF.ClassifyResult) and isinstance(found, list) and not found.complete
    assert any("full-text search for" in p for p in found.problems), found.problems
    assert any(f["source"] == C.SOURCE_SCH13 for f in found) and any(f["source"] == C.SOURCE_EFTS for f in found)
    clean = B.classify(B.FakeSEC(), stakes=[])
    assert clean.complete and clean.problems == [] and len(clean) > len(found) - 1


@pytest.mark.parametrize("exc, says", [
    (RuntimeError("a template bug"), "the classification raised (RuntimeError"),
    (SECUnavailableException("HTTP 502"), "an SEC read failed (SECUnavailableException"),
])
def test_classify_only_records_a_member_whose_stage_raises_and_goes_on(exc, says, monkeypatch):
    real = SF._Discovery.s1

    async def boom(self, cfg):
        if cfg.slug == "alphabet":
            raise exc
        async for x in real(self, cfg):
            yield x

    monkeypatch.setattr(SF._Discovery, "s1", boom)
    found = B.classify(B.FakeSEC(), stakes=[])
    assert any(p.startswith("s1: alphabet: ") and says in p for p in found.problems), found.problems
    assert any(f["slug"] == "nvidia" and f["source"] == C.SOURCE_SCH13 for f in found), "the next member went on"


def test_gate_8_never_counts_a_publish_older_than_the_week_even_if_the_store_returns_it():
    """The finder's own window check (defence against a read that over-returns)."""
    class Store:
        async def read_auto_volume(self, since):
            return [{"id": f"x{i}", "outcome": "auto", "status": "published",
                     "published_at": "2026-10-01T15:00:00+00:00"} for i in range(10)]

        async def read_decisions_since(self, decision, since):
            return []

    run = SF._Run(NOW, sec=B.FakeSEC(), store=Store(), notifier=B.FakeNotifier(), configs=B.CONFIGS,
                  configs_error=None, club_store=None)
    assert asyncio.run(run.auto_gate(exclude=None, filed_on=date(2026, 10, 20))) is None


def test_an_exit_line_is_never_deduped_away():
    """Two exit lines for one pair within 30 days: each stops something the owner should see."""
    sent = _heads(1, status="reported", reason="exit", notified_at="2026-10-20T00:00:00Z")
    pending = _heads(1, id="00000000-0000-4000-8000-000000000301", accession="0001045810-26-000301", reason="exit",
                     proposed_row={"_investee": "Co 1", "_detail": "held back before it went live", "_event": "2026-10-27"})
    lines, marks, deduped = SF._digest_lines([pending], [sent], ctx=ctx(), now=NOW, waiting=0, alerts=[], missing=[])
    assert deduped == [] and [m["id"] for m in marks] == [pending["id"]] and len(lines) == 1
    plain = _heads(1, id="00000000-0000-4000-8000-000000000302", accession="0001045810-26-000302")
    _, _, deduped = SF._digest_lines([plain], [_heads(1, status="reported", notified_at="2026-10-20T00:00:00Z")],
                                     ctx=ctx(), now=NOW, waiting=0, alerts=[], missing=[])
    assert [d["id"] for d in deduped] == [plain["id"]], "other reasons are still deduped"
