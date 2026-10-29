"""Trillion Club stake finder — the write orders, reconcile, lifecycle and taps (plan v3, "Write
orders, taps and reconcile").

``stake_finder.run_stakes`` and ``stake_finder.apply_owner_decision`` against the REAL
``StakeStore`` over the in-memory database (``tests/_trillion_club_stake_fakes.py``: migrations
175 + 205 enforced, PostgREST's answers, failure injection) and a fake SEC serving synthetic and
recorded filings (``test_trillion_club_stake_backtest.FakeSEC``). Each scenario asserts the
ledger, the stake rows and the Telegram calls — and that the fake saw nothing it does not model.

§1 world · §2 the auto write order · §3 a crash or a lost answer at EVERY auto write, then the
next run · §4 races (a Withdraw between the flip's read and its UPDATE) · §5 reconcile (kill
switch, SEC throttle, Studio delete, seed adoption, publishing, errors) · §6 lifecycle (exit,
amendment → update proposal, open proposal + a second filing, commitment → closing) · §7 the
Monday re-check · §8 expiry · §9 caps (messages, deadline, SEC requests) and ``ok`` · §10 the
taps: every verb, replays, stale messages, races.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

import pytest

import _trillion_club_stake_fakes as F
import test_trillion_club_stake_backtest as B
from app.config import settings
from app.integrations.sec_edgar import (
    SECEdgarFullTextThrottled,
    SECRateLimitException,
    SECUnavailableException,
)
from app.services.trillion_club import jobs
from app.services.trillion_club import rules
from app.services.trillion_club import stake_contracts as C
from app.services.trillion_club import stake_finder as SF
from app.services.trillion_club import stake_store as SS
from app.services.trillion_club.stake_store import StakeStore
from app.services.trillion_club.store import TrillionClubStore
from app.utils.market_hours import ET

NOW = datetime(2026, 10, 28, 22, 30, tzinfo=timezone.utc)          # Wed 18:30 ET
LATER = NOW + timedelta(hours=1)
MONDAY = datetime(2026, 11, 2, 23, 30, tzinfo=timezone.utc)        # Mon 18:30 EST
NV_CIK = "0001045810"
ACME_CIK = "0009000001"
ACME_CUSIP = "00450J10" + str(rules.cusip_check_digit("00450J10"))
CONFIGS = B.CONFIGS
S1_ONLY = {"nvidia": dataclasses.replace(CONFIGS["nvidia"], s2_aliases=())}
S2_ONLY = {"nvidia": dataclasses.replace(CONFIGS["nvidia"], s1_filers=(), intermediates=())}


def acc(n: int) -> str:
    return f"0001045810-26-{n:06d}"


# ═════════════════════════════════════════════════════════════════════════════════════════
# §1 The world
# ═════════════════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(SS, "READ_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(SF, "_monotonic", lambda: 0.0)
    monkeypatch.setattr(settings, "TRILLION_CLUB_JOBS_ENABLED", True)
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_FINDER_ENABLED", True)
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "Caydex test bot test@example.com")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_TIERS", "13dg")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_SINCE", "2026-01-01")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_LOOKBACK_DAYS", 45)
    invalidations: List[int] = []
    monkeypatch.setattr(jobs, "_invalidate_read_cache", lambda: invalidations.append(1))
    return invalidations


class World:
    """One database, one SEC, one Telegram, across several runs and taps."""

    def __init__(self, *, seed_stakes: bool = False, configs: Optional[Mapping[str, C.MemberConfig]] = None):
        self.fake = F.make_fake(include_stakes=seed_stakes)
        self.sec = B.FakeSEC()
        self.sec.set_submissions(NV_CIK, B.empty_submissions(NV_CIK, "NVIDIA CORP", ["NVDA"], ["Nasdaq"]))
        self.sec.set_submissions(ACME_CIK, B.empty_submissions(ACME_CIK, "Acme Robotics, Inc.", ["ACME"], ["Nasdaq"]))
        self.notifier = B.FakeNotifier()
        self.configs = dict(configs or S1_ONLY)

    def run(self, now: datetime = NOW) -> Dict[str, Any]:
        summary = asyncio.run(SF.run_stakes(now, sec=self.sec, store=StakeStore(self.fake), notifier=self.notifier,
                                            member_configs=self.configs, club_store=TrillionClubStore(self.fake)))
        assert self.fake.misuse == [], self.fake.misuse
        assert jobs.failure_text(summary) == (None if summary["ok"] else jobs.failure_text(summary))
        return summary

    def tap(self, verb: str, finding_id: str, message_id: Optional[int], now: datetime = LATER) -> C.TapResult:
        result = asyncio.run(SF.apply_owner_decision(verb, finding_id, message_id=message_id, now=now,
                                                     store=StakeStore(self.fake),
                                                     club_store=TrillionClubStore(self.fake)))
        assert self.fake.misuse == [], self.fake.misuse
        return result

    def file_13g(self, accession: str, *, filed: str = "2026-10-20", event: str = "10/15/2026", pct: str = "9.1",
                 shares: str = "5000000", form: str = "SCHEDULE 13G", five_or_less: Optional[str] = None,
                 comments: str = "", persons: Optional[List[Any]] = None, class_title: Optional[str] = None,
                 issuer_cik: str = ACME_CIK, issuer_name: str = "Acme Robotics, Inc.", cusip: Optional[str] = None) -> None:
        B.add_filing(self.sec.submissions_of(NV_CIK), accession, form, filed)
        xml = B.synthetic_13g(
            filer_cik=NV_CIK, issuer_cik=issuer_cik, issuer_name=issuer_name, cusip=cusip or ACME_CUSIP,
            class_title=class_title or "Common Stock, par value $0.001 per share", event=event,
            persons=persons or [("NVIDIA Corporation", "CO", shares, pct)], submission=form,
            five_or_less=five_or_less, comments=comments)
        self.sec.set_document(accession, "primary_doc.xml", xml)

    def findings(self) -> List[Dict[str, Any]]:
        return self.fake.rows(F.FINDINGS)

    def finding(self, accession: str, slug: str = "nvidia") -> Dict[str, Any]:
        rows = [f for f in self.findings() if f["accession"] == accession and f["company_slug"] == slug]
        assert len(rows) == 1, (accession, rows)
        return rows[0]

    def stakes(self, slug: str = "nvidia", *, machine: bool = True) -> List[Dict[str, Any]]:
        return [s for s in self.fake.rows(F.STAKES) if s["company_slug"] == slug
                and (not machine or s["origin"] in C.MACHINE_ORIGINS)]

    def stake(self) -> Dict[str, Any]:
        rows = self.stakes()
        assert len(rows) == 1, rows
        return rows[0]


def live_acme(world: World, accession: str = acc(901)) -> Dict[str, Any]:
    world.file_13g(accession)
    summary = world.run()
    assert summary["ok"], summary["failures"]
    f = world.finding(accession)
    assert f["status"] == C.STATUS_PUBLISHED
    return f


# ═════════════════════════════════════════════════════════════════════════════════════════
# §2 The auto write order
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_the_auto_write_order(_on):
    """Ledger new → stake INSERT unpublished → staged → the LOUD note → noted → the flip →
    published → the read cache invalidated. Nothing else is written."""
    w = World()
    w.file_13g(acc(901))
    summary = w.run()
    assert summary["ok"] is True and summary["failures"] == []
    writes = [(c.table, c.op, c.payload) for c in w.fake.writes()]
    shape = [(t, op, p.get("status") if t == F.FINDINGS and op == "update" else p.get("published")
              if t == F.STAKES else p.get("status")) for t, op, p in writes]
    assert shape == [
        (F.FINDINGS, "insert", C.STATUS_NEW),
        (F.STAKES, "insert", False),
        (F.FINDINGS, "update", C.STATUS_STAGED),
        (F.FINDINGS, "update", C.STATUS_NOTED),
        (F.STAKES, "update", True),
        (F.FINDINGS, "update", C.STATUS_PUBLISHED),
    ], shape
    f, s = w.finding(acc(901)), w.stake()
    assert (f["outcome"], f["tier"], f["reason"]) == (C.OUTCOME_AUTO, C.TIER_13DG, "plain_13dg")
    assert f["stake_id"] == s["id"] and s["finding_id"] == f["id"] and s["origin"] == "auto" and s["published"]
    assert s["investee_name"] == "Acme Robotics" and s["investee_cik"] == ACME_CIK and s["ownership_pct"] == 9.1
    assert s["kind"] == "us_listed_off_13f" and s["investee_us_symbol"] == "ACME" and s["investee_cusip"] == ACME_CUSIP
    assert len(w.notifier.of("auto")) == 1 and w.notifier.of("auto")[0]["finding"]["member_name"] == "NVIDIA"
    assert f["telegram_message_id"] == 5001
    assert _on, "the Home read cache was invalidated after the flip"


def test_the_note_goes_out_before_the_flip_and_the_gates_are_read_fresh_around_it(_on):
    w = World()
    w.file_13g(acc(901))
    w.run()
    seq = [(c.table, c.op, (c.payload or {}).get("status") if c.op == "update" else None) for c in w.fake.calls]
    noted = seq.index((F.FINDINGS, "update", C.STATUS_NOTED))
    staged = seq.index((F.FINDINGS, "update", C.STATUS_STAGED))
    flip = next(i for i, c in enumerate(w.fake.calls) if c.table == F.STAKES and c.op == "update")
    between_note = [c for c in w.fake.calls[staged:noted] if c.op == "select"]
    between_flip = [c for c in w.fake.calls[noted:flip] if c.op == "select"]
    for reads in (between_note, between_flip):
        assert {c.table for c in reads} >= {F.STAKES, F.FINDINGS}, "gates 3 and 8-10 re-read the tables"


def test_no_delivered_note_means_no_publish_and_ok_false(_on):
    w = World()
    w.file_13g(acc(901))
    w.notifier.fail.add("auto")
    summary = w.run()
    assert summary["ok"] is False and any("auto note was not delivered" in f for f in summary["failures"])
    assert w.finding(acc(901))["status"] == C.STATUS_STAGED and not w.stake()["published"]
    assert jobs.failure_text(summary)
    w.notifier.fail.clear()
    assert w.run(LATER)["ok"] is True
    assert w.finding(acc(901))["status"] == C.STATUS_PUBLISHED and w.stake()["published"]


def test_an_idempotent_rerun_writes_nothing_and_sends_nothing(_on):
    w = World()
    live_acme(w)
    writes, messages = len(w.fake.writes()), len(w.notifier.calls)
    summary = w.run(LATER)
    assert summary["ok"] is True and summary["items"] == 0
    assert len(w.fake.writes()) == writes and len(w.notifier.calls) == messages


# ═════════════════════════════════════════════════════════════════════════════════════════
# §3 A crash (refused before it ran) or a lost answer (applied, answer lost) at EVERY write
# ═════════════════════════════════════════════════════════════════════════════════════════

_WRITES = {
    "finding insert": (F.FINDINGS, "insert", lambda st: True),
    "stake insert": (F.STAKES, "insert", lambda st: True),
    "staged": (F.FINDINGS, "update", lambda st: st.payload.get("status") == C.STATUS_STAGED),
    "noted": (F.FINDINGS, "update", lambda st: st.payload.get("status") == C.STATUS_NOTED),
    "flip": (F.STAKES, "update", lambda st: st.payload.get("published") is True),
    "published": (F.FINDINGS, "update", lambda st: st.payload.get("status") == C.STATUS_PUBLISHED),
}
#: How many auto notes the owner has received once the next run finished: a refused `noted`
#: CAS means the note went out but the ledger never learned it — the next run sends it again
#: (at-least-once). Every other crash or lost answer ends with exactly one.
_NOTES_AFTER = {"noted": 2}


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("step", sorted(_WRITES))
def test_a_failure_at_each_write_then_the_next_run_ends_published_once(step, when, _on):
    w = World()
    w.file_13g(acc(901))
    table, op, match = _WRITES[step]
    w.fake.fail(table, op, when=when, match=match)
    first = w.run()
    assert w.fake.pending_injections() == [], "the failure fired"
    if when == "after":
        # A lost answer is decided by a re-read: the write landed, the run carries on.
        assert first["ok"] is True, first["failures"]
    else:
        assert first["ok"] is False and first["failures"]
    w.run(LATER)
    f = w.finding(acc(901))
    stakes = w.stakes()
    assert len(stakes) == 1, "never a second row"
    assert f["status"] == C.STATUS_PUBLISHED and stakes[0]["published"] is True
    assert stakes[0]["finding_id"] == f["id"] and f["stake_id"] == stakes[0]["id"]
    want = _NOTES_AFTER.get(step, 1) if when == "before" else 1
    assert len(w.notifier.of("auto")) == want, (step, when)
    assert f["telegram_message_id"] == 5000 + want


def test_a_lost_stake_insert_answer_is_never_a_second_row(_on):
    """The INSERT committed, its answer was lost, and the re-read by finding id ALSO failed: the
    run stops (StakeStoreError) and the next run finds the row by finding id — never re-INSERTs."""
    w = World()
    w.file_13g(acc(901))
    w.fake.fail(F.STAKES, "insert", when="after")
    w.fake.fail(F.STAKES, "select", times=3, match=lambda st: any(c == "finding_id" for _, c, _ in st.filters))
    assert w.run()["ok"] is False
    assert w.fake.pending_injections() == [], "the re-read by finding id was refused all three times"
    assert len(w.stakes()) == 1 and w.finding(acc(901))["status"] == C.STATUS_NEW
    w.run(LATER)
    assert len(w.stakes()) == 1 and w.finding(acc(901))["status"] == C.STATUS_PUBLISHED


# ═════════════════════════════════════════════════════════════════════════════════════════
# §4 Races
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_a_withdraw_between_the_flips_read_and_its_update_is_never_republished(_on):
    """The owner's Withdraw lands right before the flip's UPDATE: the flip's withdrawn_at fence
    refuses it, the finding records the owner's decision ONCE, and later runs never re-publish."""
    w = World()
    w.file_13g(acc(901))

    def withdraw(db: F.FakeSupabase) -> None:
        sid = [s for s in db.tables[F.STAKES] if s["origin"] == "auto"][0]["id"]
        db.admin_update(F.STAKES, {"id": sid}, {"published": False, "withdrawn_at": "2026-10-28T22:31:00Z"})

    w.fake.on_before(F.STAKES, "update", withdraw, match=lambda st: st.payload.get("published") is True)
    w.run()
    assert w.fake.pending_injections() == []
    s, f = w.stake(), w.finding(acc(901))
    assert s["published"] is False and s["withdrawn_at"] is not None
    assert (f["status"], f["owner_decision"]) == (C.STATUS_WITHDRAWN, C.DECISION_WITHDRAWN)
    for later in (LATER, LATER + timedelta(days=1)):
        w.run(later)
        assert w.stake()["published"] is False
    decisions = [x for x in w.findings() if x["owner_decision"] == C.DECISION_WITHDRAWN]
    assert len(decisions) == 1, "the breaker counts one Withdraw once"


def test_a_withdraw_tap_racing_the_flip_both_ways_ends_withdrawn(_on):
    """The run published; the owner's Withdraw from the note arrives right after: withdrawn."""
    w = World()
    f = live_acme(w)
    result = w.tap(C.VERB_WITHDRAW, f["id"], f["telegram_message_id"])
    assert result.ok and result.verdict.startswith("Withdrawn ") and result.buttons == ()
    assert w.stake()["published"] is False and w.finding(acc(901))["status"] == C.STATUS_WITHDRAWN
    w.run(LATER + timedelta(hours=1))
    assert w.stake()["published"] is False


# ═════════════════════════════════════════════════════════════════════════════════════════
# §5 Reconcile
# ═════════════════════════════════════════════════════════════════════════════════════════


def _staged_unpublished(w: World) -> Dict[str, Any]:
    """An auto finding left `noted` with its stake unpublished (the run died after the note)."""
    w.file_13g(acc(901))
    w.fake.fail(F.STAKES, "update", when="before", match=lambda st: st.payload.get("published") is True)
    w.run()
    f = w.finding(acc(901))
    assert f["status"] == C.STATUS_NOTED and w.stake()["published"] is False
    return f


@pytest.mark.parametrize("switch", ["TRILLION_CLUB_JOBS_ENABLED", "TRILLION_CLUB_STAKE_FINDER_ENABLED"])
def test_under_the_kill_switch_reconcile_repairs_but_never_publishes_or_sends(switch, monkeypatch):
    """Switched off, a run only RECORDS and HIDES: a Studio delete becomes a tombstone, while a
    noted row waiting for its flip stays unpublished — no send, no SEC call, ok=False."""
    w = World()
    beta_cusip = "00450K10" + str(rules.cusip_check_digit("00450K10"))
    w.sec.set_submissions("0009000003", B.empty_submissions("0009000003", "Beta Systems, Inc.", ["BETA"], ["Nasdaq"]))
    w.file_13g(acc(903), issuer_cik="0009000003", issuer_name="Beta Systems, Inc.", cusip=beta_cusip)
    assert w.run()["ok"]
    beta = w.finding(acc(903))
    assert beta["status"] == C.STATUS_PUBLISHED
    w.file_13g(acc(901))
    w.fake.fail(F.STAKES, "update", when="before", match=lambda st: st.payload.get("published") is True)
    w.run(LATER)
    assert w.finding(acc(901))["status"] == C.STATUS_NOTED
    w.fake.studio_delete_stake(beta["stake_id"])
    monkeypatch.setattr(settings, switch, False)
    calls_before, sec_before = len(w.notifier.calls), len(w.sec.calls)
    summary = w.run(LATER + timedelta(hours=1))
    assert summary["ok"] is False and any(switch in f for f in summary["failures"])
    assert len(w.notifier.calls) == calls_before and len(w.sec.calls) == sec_before, "no send, no SEC"
    deleted = w.finding(acc(903))
    assert (deleted["status"], deleted["owner_decision"]) == (C.STATUS_WITHDRAWN, C.DECISION_DELETED_IN_STUDIO)
    acme = [s for s in w.stakes() if s["investee_cik"] == ACME_CIK]
    assert w.finding(acc(901))["status"] == C.STATUS_NOTED and acme[0]["published"] is False, "never published"


def test_reconcile_runs_under_an_sec_throttle_and_the_digest_still_goes(_on):
    w = World()
    noted = _staged_unpublished(w)
    w.sec.fail["get_submissions"] = [SECRateLimitException("circuit open", retry_after=600.0)]
    w.sec.calls.clear()
    summary = w.run(LATER)
    assert w.finding(acc(901))["status"] == C.STATUS_PUBLISHED and w.stake()["published"], "reconcile flipped it"
    assert summary["ok"] is False and any("SEC throttled the run" in f for f in summary["failures"])
    assert summary["stages"]["reconcile"]["ok"] is True
    assert w.sec.count("get_submissions") == 1, "one refusal stops every SEC stage"
    assert noted["telegram_message_id"] == w.finding(acc(901))["telegram_message_id"], "no second note"


def test_an_efts_throttle_stops_s2_only(_on):
    w = World(configs={"nvidia": CONFIGS["nvidia"]})
    w.file_13g(acc(901))
    w.sec.fail["full_text_search"] = [SECEdgarFullTextThrottled("efts 429", status_code=429)]
    summary = w.run()
    assert w.finding(acc(901))["status"] == C.STATUS_PUBLISHED, "S1 ran"
    assert summary["stages"]["s2"]["ok"] is False and summary["stages"]["s1"]["ok"] is True
    assert any("full-text search is throttled" in f for f in summary["failures"])
    assert w.sec.count("full_text_search") == 1


def test_a_studio_delete_is_a_tombstone_and_one_digest_line(_on):
    w = World()
    f = live_acme(w)
    w.fake.studio_delete_stake(f["stake_id"])
    w.run(LATER)
    g = w.finding(acc(901))
    assert (g["status"], g["owner_decision"], g["stake_id"]) == (C.STATUS_WITHDRAWN, C.DECISION_DELETED_IN_STUDIO, None)
    assert g["decided_at"] is not None and g["notified_at"] is not None, "marked once the digest went out"
    lines = [line for line in w.notifier.digest_lines() if "Deleted in Studio" in line]
    assert len(lines) == 1 and "Acme Robotics" in lines[0]
    w.run(LATER + timedelta(days=1))
    assert len([line for line in w.notifier.digest_lines() if "Deleted in Studio" in line]) == 1
    # a new filing for the pair is never a new row: the tombstone holds
    w.file_13g(acc(902), filed="2026-10-28", event="10/27/2026", pct="10.5")
    w.run(LATER + timedelta(days=2))
    new = w.finding(acc(902))
    assert (new["outcome"], new["reason"]) == (C.OUTCOME_HEADS_UP, "tombstone") and w.stakes() == []


def test_a_seed_adoption_hands_the_finding_over(_on):
    w = World()
    f = live_acme(w)
    w.fake.admin_update(F.STAKES, {"id": f["stake_id"]}, {"origin": "seed", "finding_id": None,
                                                          "owner_kept_at": None, "withdrawn_at": None})
    w.run(LATER)
    assert w.finding(acc(901))["status"] == C.STATUS_SUPERSEDED
    assert sum("Handed over to the seed" in line for line in w.notifier.digest_lines()) == 1
    w.run(LATER + timedelta(hours=1))
    assert w.finding(acc(901))["status"] == C.STATUS_SUPERSEDED, "never retried"


def test_an_error_finding_is_read_once_per_et_day_then_becomes_a_heads_up_line(_on):
    """Review B lifecycle-F3: the scheduler retries a failed run HOURLY (≤ 3 a day), which used
    to spend all MAX_ATTEMPTS in one evening and turn the filing into a silent `skipped`. Now an
    attempt is spent at most once per ET day, and the last failed one makes the filing ONE digest
    line with its EDGAR link — never a skip, and never read again."""
    w = World()
    w.file_13g(acc(901))
    w.sec.fail["get_filing_document"] = [SECUnavailableException("502") for _ in range(3)]
    assert w.run(NOW)["ok"] is False
    f = w.finding(acc(901))
    assert (f["status"], f["attempts"]) == (C.STATUS_ERROR, 1) and "502" in f["last_error"]
    for hours in (1, 2):                                   # the same evening: no attempt spent
        reads = w.sec.count("get_filing_document")
        summary = w.run(NOW + timedelta(hours=hours))
        assert w.sec.count("get_filing_document") == reads, "not read again the same ET day"
        assert w.finding(acc(901))["attempts"] == 1 and summary["ok"] is True, summary["failures"]
    assert w.run(NOW + timedelta(days=1))["ok"] is False
    assert (w.finding(acc(901))["status"], w.finding(acc(901))["attempts"]) == (C.STATUS_ERROR, 2)
    w.run(NOW + timedelta(days=2))                        # the third ET day: the last attempt fails
    f = w.finding(acc(901))
    assert (f["status"], f["outcome"], f["reason"], f["attempts"]) == (
        C.STATUS_REPORTED, C.OUTCOME_HEADS_UP, "fetch_failed", 3), f
    [line] = [x for x in w.notifier.digest_lines() if "could not be read on 3 ET days" in x]
    assert "read it on EDGAR" in line and line.endswith(f["source_url"]), line
    assert not [x for x in w.findings() if x["status"] == C.STATUS_SKIPPED]
    reads = w.sec.count("get_filing_document")
    w.run(NOW + timedelta(days=3))
    assert w.sec.count("get_filing_document") == reads and w.finding(acc(901))["status"] == C.STATUS_REPORTED
    assert C.MAX_ATTEMPTS == 3


def test_an_exhausted_error_finding_found_by_reconcile_is_a_heads_up_never_a_skip(_on):
    """An `error` finding already at MAX_ATTEMPTS (e.g. written by an older build) is turned into
    the same fetch_failed heads-up by reconcile."""
    w = World()
    w.file_13g(acc(901))
    w.sec.fail["get_filing_document"] = [SECUnavailableException("502")]
    w.run(NOW)
    fid = w.finding(acc(901))["id"]
    w.fake.admin_update(F.FINDINGS, {"id": fid}, {"attempts": 3})
    w.run(NOW + timedelta(days=1))
    f = w.finding(acc(901))
    assert (f["status"], f["outcome"], f["reason"]) == (C.STATUS_REPORTED, C.OUTCOME_HEADS_UP, "fetch_failed")
    assert any("could not be read on 3 ET days" in x for x in w.notifier.digest_lines())


def test_an_exact_five_percent_row_still_waiting_is_held_back_by_the_exit(_on):
    """Final verify #1: a plain 13G stating EXACTLY 5.0% is a holding, not an exit (stake_schedule
    reads an exit only below 5 or with the box checked). Pending when the member's 3% 13G/A is read,
    it must be held back — never published after the exit, never verified on Mondays."""
    w = World()
    w.file_13g(acc(901), filed="2026-10-20", event="10/15/2026", pct="5.0")
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="3.0", form="SCHEDULE 13G/A")
    w.notifier.fail.add("auto")
    w.run(NOW)
    w.notifier.fail.clear()
    w.run(NOW + timedelta(days=1))
    assert w.finding(acc(901))["status"] == C.STATUS_CLOSED
    assert [s["published"] for s in w.stakes()] == [False]
    assert any("held back before it went live" in l for l in w.notifier.digest_lines())


def test_a_stale_withdraw_after_an_exit_decides_nothing_whatever_the_findings_status(_on):
    """Final verify #3: an exit-stopped row's old auto note in a state that is NOT `closed` — the
    exit took the live row down but its lifecycle note failed, so the row's own finding is still
    `published` over an unpublished stake. A Withdraw on the old note shows "Taken down", records no
    owner decision (the breaker counts those), and a genuine re-entry is proposed, never a tombstone."""
    w = World()
    a = live_acme(w)
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="4.2", form="SCHEDULE 13G/A")
    w.notifier.fail.add("lifecycle")
    w.run(LATER)
    w.notifier.fail.clear()
    assert [s["published"] for s in w.stakes()] == [False]
    r = w.tap(C.VERB_WITHDRAW, a["id"], a["telegram_message_id"], now=LATER + timedelta(hours=1))
    assert (r.ok, r.verdict, r.buttons) == (False, SF.VERDICT_DOWN_EXIT, ()), r
    assert w.finding(acc(901))["owner_decision"] is None
    assert [s["withdrawn_at"] for s in w.stakes()] == [None]
    w.run(LATER + timedelta(days=1))
    w.file_13g(acc(903), filed="2026-11-04", event="11/03/2026", pct="8.0", form="SCHEDULE 13G")
    w.run(NOW + timedelta(days=7))
    assert w.finding(acc(903))["reason"] != "tombstone"


def test_a_held_back_note_is_repaired_by_a_tap_and_a_stale_withdraw_decides_nothing(_on):
    """Verifier R6: the auto note went out ("It goes live now", Withdraw / Keep), then the member's
    exit held the row back and the "Held back" mark failed. Keep or Withdraw on that note re-shows
    the held-back state — and the stale Withdraw records NO owner decision (the breaker counts
    them) and never tombstones the pair against a genuine re-entry."""
    w = World()
    w.file_13g(acc(901), filed="2026-10-20", event="10/15/2026", pct="9.1")
    w.fake.fail(F.STAKES, "update", when="before", times=2, match=lambda st: st.payload.get("published") is True)
    w.run(NOW)
    a = w.finding(acc(901))
    assert a["status"] == C.STATUS_NOTED
    w.file_13g(acc(902), filed="2026-10-29", event="10/28/2026", pct="3.0", form="SCHEDULE 13G/A")
    w.notifier.fail.add("mark")
    w.run(NOW + timedelta(days=1))
    assert w.finding(acc(901))["status"] == C.STATUS_CLOSED
    w.notifier.fail.clear()
    for verb in (C.VERB_KEEP, C.VERB_WITHDRAW):
        r = w.tap(verb, a["id"], a["telegram_message_id"], now=NOW + timedelta(days=1, hours=2))
        assert (r.ok, r.verdict, r.buttons) == (False, SF.VERDICT_HELD_EXIT, ()), (verb, r)
    g = w.finding(acc(901))
    assert (g["status"], g["owner_decision"]) == (C.STATUS_CLOSED, None)
    assert [s["withdrawn_at"] for s in w.stakes()] == [None], "never a tombstone"


def test_a_held_proposal_is_never_sent_after_the_members_exit(_on, monkeypatch):
    """Verifier R5: an auto row downgraded to a held proposal (its message not sent yet) and the
    member's exit whose void write failed: the next run must not SEND a proposal for the pre-exit
    row — it becomes the exit heads-up, its stake left unpublished."""
    w = World()
    w.file_13g(acc(901), filed="2026-10-20", event="10/15/2026", pct="9.1")
    w.notifier.fail.add("auto")
    w.run(NOW)
    assert w.finding(acc(901))["status"] == C.STATUS_STAGED
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_TIERS", "")
    w.notifier.fail.clear()
    w.notifier.fail.add("proposal")
    w.file_13g(acc(902), filed="2026-10-29", event="10/28/2026", pct="3.0", form="SCHEDULE 13G/A")
    w.fake.fail(F.FINDINGS, "update", when="before", match=lambda st: st.payload.get("status") == C.STATUS_SUPERSEDED)
    w.run(NOW + timedelta(days=1))
    sent_before = len([p for p in w.notifier.of("proposal") if p["finding"]["accession"] == acc(901)])
    w.notifier.fail.clear()
    w.run(NOW + timedelta(days=2))
    sent = [p for p in w.notifier.of("proposal") if p["finding"]["accession"] == acc(901)]
    assert len(sent) == sent_before, "no proposal for the pre-exit row after the exit is on file"
    f = w.finding(acc(901))
    assert (f["outcome"], f["reason"]) == (C.OUTCOME_HEADS_UP, "exit") and f["status"] != C.STATUS_PROPOSED
    assert [s["published"] for s in w.stakes()] == [False]


def test_an_exit_never_voids_a_closing_proposal_it_does_not_contradict(_on, monkeypatch):
    """Verifier R2: the member's 13G/A reporting 5% or less supersedes a PERCENTAGE above 5% — not a
    completed purchase ("Intel · $5.0B invested as of Dec 26"), which stays a true, dated fact. The
    S2 closing proposal stays open (and publishable); the owner gets one line saying the member now
    reports 5% or less."""
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_LOOKBACK_DAYS", 120)
    w = World(configs={"nvidia": CONFIGS["nvidia"]})
    w.run(datetime(2025, 12, 30, 23, 30, tzinfo=timezone.utc))
    [intel] = [f for f in w.findings() if f["accession"] == "0000050863-25-000204"]
    assert (intel["outcome"], intel["status"], intel["reason"]) == (C.OUTCOME_PROPOSAL, C.STATUS_PROPOSED, "t1_closing")
    w.file_13g(acc(990), filed="2025-12-31", event="12/30/2025", pct="4.0", shares="190000000", form="SCHEDULE 13G/A",
               five_or_less="Y", issuer_cik="0000050863", issuer_name="Intel Corp", cusip="458140100")
    later = datetime(2025, 12, 31, 23, 30, tzinfo=timezone.utc)
    w.run(later)
    e = w.finding(acc(990))
    assert (e["outcome"], e["reason"]) == (C.OUTCOME_HEADS_UP, "exit")
    assert "stays open" in e["proposed_row"]["_detail"]
    assert w.finding("0000050863-25-000204")["status"] == C.STATUS_PROPOSED
    assert not [m for m in w.notifier.of("mark") if m["message_id"] == intel["telegram_message_id"]]
    assert w.tap(C.VERB_PUBLISH, intel["id"], intel["telegram_message_id"], now=later + timedelta(hours=1)).ok


def test_an_error_finding_that_left_the_window_becomes_a_line_not_a_silent_row(_on):
    """Verifier regression R1: attempts are spread over ET days, but S1/S2 only revisit accessions
    inside the lookback window — a filing near the window's start whose read failed would stay
    `error` forever, with ok=True and no line. Reconcile turns it into the fetch_failed line."""
    w = World()
    w.file_13g(acc(951), filed="2026-09-14", event="09/10/2026")
    w.sec.fail["get_filing_document"] = [SECUnavailableException("HTTP 503 (after one retry)")]
    first = w.run()
    f = w.finding(acc(951))
    assert (f["status"], int(f["attempts"])) == (C.STATUS_ERROR, 1) and first["ok"] is False
    later = w.run(NOW + timedelta(days=3))                 # 09-14 is now before today - 45 days
    g = w.finding(acc(951))
    assert (g["status"], g["outcome"], g["reason"]) == (C.STATUS_REPORTED, C.OUTCOME_HEADS_UP, "fetch_failed"), g
    assert later["ok"] is True
    [line] = [x for x in w.notifier.digest_lines() if acc(951) in x or "left the 45-day window" in x]
    assert "left the 45-day window" in line and "EDGAR" in line


def test_an_error_finding_that_reads_on_retry_is_processed(_on):
    w = World()
    w.file_13g(acc(901))
    w.sec.fail["get_filing_document"] = [SECUnavailableException("502")]
    assert w.run()["ok"] is False
    assert w.finding(acc(901))["status"] == C.STATUS_ERROR
    assert w.run(NOW + timedelta(days=1))["ok"] is True      # read again on the next ET day
    f = w.finding(acc(901))
    assert (f["status"], f["attempts"], f["last_error"]) == (C.STATUS_PUBLISHED, 2, None)


def test_a_publishing_finding_left_by_a_dead_tap_is_finished(_on):
    w = World()
    w.file_13g(acc(901), comments="Includes 1,000,000 shares issuable upon exercise of warrants.")
    w.run()
    f = w.finding(acc(901))
    assert (f["outcome"], f["status"]) == (C.OUTCOME_PROPOSAL, C.STATUS_PROPOSED)
    # the tap's flip failed after the INSERT: the row exists, so the run will finish it — the
    # owner is told so, never "something went wrong" (review B TAP-3)
    w.fake.fail(F.STAKES, "update", when="before", match=lambda st: st.payload.get("published") is True)
    result = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"])
    assert (result.ok, result.toast) == (False, SF.TOAST_UNFINISHED)
    assert w.finding(acc(901))["status"] == C.STATUS_PUBLISHING
    w.run(LATER + timedelta(minutes=5))
    assert w.finding(acc(901))["status"] == C.STATUS_PUBLISHING, "younger than 10 minutes: a tap may be in flight"
    w.run(LATER + timedelta(minutes=11))
    g = w.finding(acc(901))
    assert (g["status"], g["owner_decision"]) == (C.STATUS_PUBLISHED, C.DECISION_PUBLISHED)
    assert g["published_at"] is not None
    assert w.stake()["published"] is True and w.stake()["origin"] == "proposal"
    # the live row's message gets its verdict and a Withdraw button from the run
    [mark] = [m for m in w.notifier.of("mark") if m["message_id"] == f["telegram_message_id"]]
    assert mark["buttons"] == [C.VERB_WITHDRAW] and mark["verdict"].startswith("Published "), mark


def test_a_publishing_finding_with_no_row_rolls_back_to_proposed(_on):
    w = World()
    w.file_13g(acc(901), comments="Includes 1,000,000 shares issuable upon exercise of warrants.")
    w.run()
    f = w.finding(acc(901))
    w.fake.admin_update(F.FINDINGS, {"id": f["id"]}, {"status": "publishing", "updated_at": "2026-10-28T22:00:00Z"})
    w.run(LATER)
    assert w.finding(acc(901))["status"] == C.STATUS_PROPOSED
    assert any("did not finish" in line for line in w.notifier.digest_lines())


# ═════════════════════════════════════════════════════════════════════════════════════════
# §6 Lifecycle
# ═════════════════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("exit_kind", ["below_five", "flag"])
def test_an_exit_filing_unpublishes_the_auto_row_with_a_loud_note(exit_kind, _on):
    w = World()
    first = live_acme(w)
    if exit_kind == "below_five":
        w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="4.2", form="SCHEDULE 13G/A")
    else:
        w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="5.0", form="SCHEDULE 13G/A", five_or_less="Y")
    w.run(LATER)
    s = w.stake()
    assert s["published"] is False and s["withdrawn_at"] is None, "an exit is not the owner's no"
    assert w.finding(acc(901))["status"] == C.STATUS_CLOSED and w.finding(acc(902))["status"] == C.STATUS_CLOSED
    notes = w.notifier.of("lifecycle")
    assert len(notes) == 1 and notes[0]["offer_withdraw"] is False and "taken down automatically" in notes[0]["text"]
    assert first["id"] != w.finding(acc(902))["id"]


def test_an_exit_for_a_pair_with_no_row_is_skipped(_on):
    w = World()
    w.file_13g(acc(902), pct="3.1", form="SCHEDULE 13G/A")
    w.run()
    f = w.finding(acc(902))
    assert (f["outcome"], f["status"], f["reason"]) == (C.OUTCOME_SKIPPED, C.STATUS_SKIPPED, "exit_filing")


def test_an_amendment_is_an_update_proposal_and_the_old_row_stays_until_publish(_on):
    w = World()
    live_acme(w)
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="12.4", shares="7000000", form="SCHEDULE 13G/A")
    w.run(LATER)
    upd = w.finding(acc(902))
    assert (upd["outcome"], upd["tier"], upd["reason"], upd["status"]) == (
        C.OUTCOME_PROPOSAL, C.TIER_UPDATE, "update", C.STATUS_PROPOSED)
    s = w.stake()
    assert s["ownership_pct"] == 9.1 and s["published"] and s["finding_id"] == w.finding(acc(901))["id"]
    assert upd["stake_id"] == s["id"]
    proposal = w.notifier.of("proposal")[-1]
    assert proposal["row"]["ownership_pct"] == 12.4 and "Replaces: Acme Robotics · 9.1%" in proposal["finding"]["detail"]
    result = w.tap(C.VERB_PUBLISH, upd["id"], upd["telegram_message_id"], now=LATER + timedelta(hours=1))
    assert result.ok and result.buttons == (C.VERB_WITHDRAW,), result
    s = w.stake()
    assert (s["ownership_pct"], s["origin"], s["finding_id"], s["published"]) == (12.4, "proposal", upd["id"], True)
    assert s["as_of"] == "2026-10-26" and s["owner_kept_at"] is None
    assert w.finding(acc(901))["status"] == C.STATUS_SUPERSEDED
    assert w.finding(acc(902))["status"] == C.STATUS_PUBLISHED
    w.run(LATER + timedelta(hours=2))
    assert w.finding(acc(901))["status"] == C.STATUS_SUPERSEDED and len(w.stakes()) == 1


def test_a_restatement_with_the_same_facts_is_skipped(_on):
    w = World()
    live_acme(w)
    w.file_13g(acc(902), filed="2026-10-27", form="SCHEDULE 13G/A")   # same 9.1%, same event date
    w.run(LATER)
    f = w.finding(acc(902))
    assert (f["outcome"], f["reason"]) == (C.OUTCOME_SKIPPED, "existing_row")
    assert w.notifier.of("proposal") == []


def test_open_proposal_and_a_second_filing_edit_one_message(_on, monkeypatch):
    """The Intel signing 8-K (T2, a commitment proposal) is open when the closing 8-K arrives:
    its evidence is appended and the SAME message is edited — never a second proposal."""
    w = World(configs=S2_ONLY)
    signing_acc, closing_acc = "0000050863-25-000155", "0000050863-25-000204"
    signing = (B.FIX / "html" / "intel_signing_8k.htm").read_text(encoding="utf-8")
    w.sec.set_document(signing_acc, "intc-20250915.htm", signing)
    w.sec.add_hit({"adsh": signing_acc, "ciks": ["0000050863"], "display_names": ["INTEL CORP  (INTC)  (CIK 0000050863)"],
                   "file_date": "2025-09-18", "file_type": "8-K", "form": "8-K", "items": ["3.02", "7.01", "9.01"],
                   "root_forms": ["8-K"], "sequence": 1}, "intc-20250915.htm")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_LOOKBACK_DAYS", 400)
    w.run(datetime(2025, 9, 18, 22, 30, tzinfo=timezone.utc))
    t2 = w.finding(signing_acc)
    assert (t2["outcome"], t2["tier"], t2["status"]) == (C.OUTCOME_PROPOSAL, C.TIER_SEC_AGREEMENT, C.STATUS_PROPOSED)
    assert w.notifier.of("proposal")[0]["row"]["kind"] == "commitment"
    w.run(datetime(2025, 12, 29, 23, 30, tzinfo=timezone.utc))
    closing = w.finding(closing_acc)
    assert (closing["reason"], closing["status"]) == ("open_proposal", C.STATUS_REPORTED)
    intel = [p for p in w.notifier.of("proposal") if p["finding"].get("investee_cik") == "0000050863"]
    assert len(intel) == 1, "never resent"
    [edit] = w.notifier.of("edit")
    assert edit["message_id"] == t2["telegram_message_id"] and len(edit["evidence"]) > len(t2["verified_spans"])
    after = w.finding(signing_acc)
    assert after["fact_sha"] == t2["fact_sha"] and after["proposed_row"] == t2["proposed_row"], "facts never change"


def test_a_published_commitment_and_its_closing_make_an_update_proposal(_on, monkeypatch):
    """Closing evidence for a COMMITMENT is the one S2 update (plan v3, "Lifecycle")."""
    w = World(configs=S2_ONLY)
    signing_acc = "0000050863-25-000155"
    w.sec.set_document(signing_acc, "intc-20250915.htm",
                       (B.FIX / "html" / "intel_signing_8k.htm").read_text(encoding="utf-8"))
    w.sec.add_hit({"adsh": signing_acc, "ciks": ["0000050863"], "display_names": ["INTEL CORP  (INTC)  (CIK 0000050863)"],
                   "file_date": "2025-09-18", "file_type": "8-K", "form": "8-K", "items": ["3.02", "7.01", "9.01"],
                   "root_forms": ["8-K"], "sequence": 1}, "intc-20250915.htm")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_LOOKBACK_DAYS", 400)
    w.run(datetime(2025, 9, 18, 22, 30, tzinfo=timezone.utc))
    t2 = w.finding(signing_acc)
    assert w.tap(C.VERB_PUBLISH, t2["id"], t2["telegram_message_id"],
                 now=datetime(2025, 9, 19, 14, 0, tzinfo=timezone.utc)).ok
    w.run(datetime(2025, 12, 29, 23, 30, tzinfo=timezone.utc))
    closing = w.finding("0000050863-25-000204")
    assert (closing["tier"], closing["reason"], closing["status"]) == (C.TIER_UPDATE, "update", C.STATUS_PROPOSED)
    assert closing["proposed_row"]["disclosed_value_usd"] == 5e9 and closing["proposed_row"]["kind"] != "commitment"
    assert w.stake()["kind"] == "commitment", "the commitment row stays until Publish"


# ═════════════════════════════════════════════════════════════════════════════════════════
# §7 The Monday re-check
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_monday_a_definite_pass_bumps_verified_on(_on):
    w = World()
    live_acme(w)
    w.run(MONDAY)
    assert w.stake()["verified_on"] == "2026-11-02"


def test_not_monday_nothing_is_rechecked(_on):
    w = World()
    live_acme(w)
    w.run(NOW + timedelta(days=1))
    assert w.stake()["verified_on"] == "2026-10-28"


def test_monday_a_delisting_after_as_of_is_one_loud_note_with_withdraw(_on):
    w = World()
    live_acme(w)
    subs = w.sec.submissions_of(ACME_CIK)
    B.add_filing(subs, "0000876661-26-000999", "25-NSE", "2026-10-30", "xslF25X02/primary_doc.xml")
    w.run(MONDAY)
    s = w.stake()
    assert s["published"] and s["verified_on"] == "2026-10-28", "no bump, and the row stays live"
    [note] = w.notifier.of("lifecycle")
    assert note["offer_withdraw"] is True and "25-NSE" in note["text"]
    f = w.finding("0000876661-26-000999")
    assert (f["reason"], f["status"], f["stake_id"]) == ("recheck_negative", C.STATUS_REPORTED, s["id"])
    result = w.tap(C.VERB_WITHDRAW, f["id"], f["telegram_message_id"], now=MONDAY + timedelta(hours=1))
    assert result.ok and w.stake()["published"] is False
    w.run(MONDAY + timedelta(days=7))
    assert len(w.notifier.of("lifecycle")) == 1, "one note per delisting filing"


def test_monday_an_sec_error_changes_nothing(_on):
    w = World()
    live_acme(w)
    w.sec.fail["get_submissions"] = [None, SECUnavailableException("503")]   # S1's member read, then the re-check
    summary = w.run(MONDAY)
    assert w.stake()["verified_on"] == "2026-10-28" and summary["ok"] is False


def test_monday_a_changed_ticker_changes_nothing(_on):
    w = World()
    live_acme(w)
    w.sec.set_submissions(ACME_CIK, B.empty_submissions(ACME_CIK, "Acme Robotics, Inc.", ["ACMR"], ["Nasdaq"]))
    w.run(MONDAY)
    assert w.stake()["verified_on"] == "2026-10-28" and w.notifier.of("lifecycle") == []


def test_seed_rows_are_never_written_by_the_recheck(_on):
    w = World(seed_stakes=True)
    before = {s["id"]: s for s in w.fake.rows(F.STAKES)}
    w.run(MONDAY)
    after = {s["id"]: s for s in w.fake.rows(F.STAKES)}
    assert before == after


# ═════════════════════════════════════════════════════════════════════════════════════════
# §8 Expiry
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_an_unanswered_proposal_expires_after_14_days(_on):
    w = World()
    w.file_13g(acc(901), comments="Includes 1,000,000 shares issuable upon exercise of warrants.")
    w.run()
    f = w.finding(acc(901))
    assert f["status"] == C.STATUS_PROPOSED
    w.run(NOW + timedelta(days=13))
    assert w.finding(acc(901))["status"] == C.STATUS_PROPOSED
    w.run(NOW + timedelta(days=14, minutes=1))
    g = w.finding(acc(901))
    assert g["status"] == C.STATUS_EXPIRED and g["owner_decision"] is None
    [mark] = w.notifier.of("mark")
    assert (mark["message_id"], mark["verdict"], mark["buttons"]) == (f["telegram_message_id"],
                                                                      "Expired — not published", [])
    assert sum("Expired unpublished" in line for line in w.notifier.digest_lines()) == 1
    result = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"], now=NOW + timedelta(days=15))
    assert result.ok is False and result.toast == SF.TOAST_DECIDED


# ═════════════════════════════════════════════════════════════════════════════════════════
# §9 Caps, the deadline, and `ok`
# ═════════════════════════════════════════════════════════════════════════════════════════


def _cusip(base8: str) -> str:
    return base8 + str(rules.cusip_check_digit(base8))


def test_at_most_ten_loud_messages_a_run_and_the_rest_wait(_on):
    w = World()
    for i in range(12):
        cik, letter = f"00090001{i:02d}", chr(65 + i)
        w.sec.set_submissions(cik, B.empty_submissions(cik, f"Gamma {letter} Systems, Inc.", [f"GAM{letter}"],
                                                       ["Nasdaq"]))
        w.file_13g(acc(910 + i), issuer_cik=cik, issuer_name=f"Gamma {letter} Systems, Inc.",
                   cusip=_cusip(f"0045{i:02d}J1"),
                   comments="Includes 1,000,000 shares issuable upon exercise of warrants.")
    summary = w.run()
    assert w.notifier.loud == SF.MAX_LOUD_MESSAGES_PER_RUN == 10
    waiting = [f for f in w.findings() if f["status"] == C.STATUS_NEW and f["outcome"] == C.OUTCOME_PROPOSAL]
    assert len(waiting) == 2 and summary["ok"] is True, "waiting is not a failure"
    assert any("2 message(s) wait for the next run" in line for line in w.notifier.digest_lines())
    w.run(LATER)
    assert w.notifier.loud == 12 and not [f for f in w.findings() if f["status"] == C.STATUS_NEW
                                          and f["outcome"] == C.OUTCOME_PROPOSAL]


def test_the_run_deadline_stops_the_sec_stages_but_the_digest_goes(_on, monkeypatch):
    w = World()
    w.file_13g(acc(901))
    clock = iter([0.0] + [SF.RUN_DEADLINE_SECONDS + 1.0] * 100)
    monkeypatch.setattr(SF, "_monotonic", lambda: next(clock))
    summary = w.run()
    assert summary["ok"] is False and any("run deadline passed" in f for f in summary["failures"])
    assert w.sec.calls == []
    assert summary["stages"]["digest"]["ok"] is True


def test_the_sec_request_cap_ends_the_sec_stages(_on, monkeypatch):
    w = World()
    w.file_13g(acc(901))
    monkeypatch.setattr(SF, "MAX_SEC_REQUESTS_PER_RUN", 1)
    summary = w.run()
    assert summary["ok"] is False and any("request SEC cap" in f for f in summary["failures"])
    assert len(w.sec.calls) == 1
    monkeypatch.setattr(SF, "MAX_SEC_REQUESTS_PER_RUN", 600)
    assert w.run(LATER)["ok"] is True and w.finding(acc(901))["status"] == C.STATUS_PUBLISHED


def test_a_bad_filers_file_runs_no_sec_stage(_on, monkeypatch, tmp_path):
    w = World()
    w.file_13g(acc(901))
    bad = tmp_path / "filers.json"
    bad.write_text('{"version": 2}', encoding="utf-8")
    monkeypatch.setattr(SF, "FILERS_PATH", bad)
    summary = asyncio.run(SF.run_stakes(NOW, sec=w.sec, store=StakeStore(w.fake), notifier=w.notifier,
                                        club_store=TrillionClubStore(w.fake)))
    assert summary["ok"] is False and any("filers file is unusable" in f for f in summary["failures"])
    assert w.sec.calls == []
    assert summary["stages"]["reconcile"]["ok"] and summary["stages"]["digest"]["ok"]


def test_the_summary_shape_is_the_schedulers(_on):
    w = World()
    w.file_13g(acc(901))
    summary = w.run()
    assert set(summary) == {"job", "now", "ok", "items", "failures", "stages"}
    assert summary["job"] == "stakes" and summary["now"] == NOW.isoformat()
    assert summary["ok"] is True and jobs.failure_text(summary) is None
    # items = rows written: the finding, the stake and the flip (the ledger's CAS moves are not rows)
    assert summary["items"] == 3
    failed = dict(summary, ok=False, failures=["notes: x"])
    assert jobs.failure_text(failed) == "notes: x"


def test_a_naive_now_is_refused():
    with pytest.raises(ValueError):
        asyncio.run(SF.run_stakes(datetime(2026, 10, 28, 18, 30)))


def test_without_the_review_bot_only_the_database_repairs_run(_on):
    w = World()
    w.file_13g(acc(901))
    w.notifier = B.FakeNotifier(available=False)
    summary = w.run()
    assert summary["ok"] is False and any("review bot is not configured" in f for f in summary["failures"])
    assert w.sec.calls == [] and w.findings() == []


def test_null_notifier_matches_the_protocol():
    import inspect

    for name in ("available", "send_auto_note", "send_proposal", "edit_proposal", "send_lifecycle_note",
                 "mark_message", "send_digest"):
        want = inspect.signature(getattr(C.StakeNotifier, name))
        got = inspect.signature(getattr(SF.NullNotifier, name))
        assert list(want.parameters) == list(got.parameters), name
        assert [p.kind for p in want.parameters.values()] == [p.kind for p in got.parameters.values()], name
    n = SF.NullNotifier()
    assert n.available() is False
    assert asyncio.run(n.send_auto_note({}, {})) is None
    assert asyncio.run(n.send_digest(["x"])) is False


# ═════════════════════════════════════════════════════════════════════════════════════════
# §10 The taps
# ═════════════════════════════════════════════════════════════════════════════════════════


def _proposal(w: World, accession: str = acc(901)) -> Dict[str, Any]:
    w.file_13g(accession, comments="Includes 1,000,000 shares issuable upon exercise of warrants.")
    w.run()
    f = w.finding(accession)
    assert (f["outcome"], f["status"]) == (C.OUTCOME_PROPOSAL, C.STATUS_PROPOSED)
    return f


def test_publish_inserts_flips_and_offers_withdraw(_on):
    w = World()
    f = _proposal(w)
    result = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"])
    assert result.ok and result.verdict.startswith("Published ") and result.buttons == (C.VERB_WITHDRAW,)
    g, s = w.finding(acc(901)), w.stake()
    assert (g["status"], g["owner_decision"], g["stake_id"]) == (C.STATUS_PUBLISHED, C.DECISION_PUBLISHED, s["id"])
    assert (s["origin"], s["published"], s["finding_id"]) == ("proposal", True, f["id"])
    assert s["ownership_basis"] == "of common stock, incl. 1.0M warrant shares"
    replay = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"])
    assert (replay.ok, replay.toast) == (False, "Already published")
    assert len(w.stakes()) == 1


def test_a_replay_on_a_decided_finding_repairs_its_message(_on):
    """Review A TG-1: the verdict edit after a Publish can fail (flood control, an outage), leaving
    Publish / Dismiss on a LIVE row with no Withdraw anywhere. The owner's next tap on that stale
    message re-shows the current state — a live row regains Withdraw; a decided pair, its verdict."""
    w = World()
    f = _proposal(w)
    assert w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"]).ok
    for verb in (C.VERB_PUBLISH, C.VERB_DISMISS):
        replay = w.tap(verb, f["id"], f["telegram_message_id"])
        assert replay.ok is False and replay.verdict == "Live — published earlier", verb
        assert replay.buttons == (C.VERB_WITHDRAW,), verb
    writes = len(w.fake.writes())
    gone = w.tap(C.VERB_WITHDRAW, f["id"], f["telegram_message_id"])
    assert gone.ok and w.stake()["published"] is False
    assert len(w.fake.writes()) > writes
    for verb in (C.VERB_PUBLISH, C.VERB_DISMISS, C.VERB_KEEP, C.VERB_WITHDRAW):
        replay = w.tap(verb, f["id"], f["telegram_message_id"])
        assert replay.ok is False and replay.verdict == "Withdrawn earlier" and replay.buttons == (), verb


def test_a_replay_on_a_dismissed_or_expired_proposal_shows_its_verdict(_on):
    w = World()
    f = _proposal(w)
    assert w.tap(C.VERB_DISMISS, f["id"], f["telegram_message_id"]).ok
    replay = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"])
    assert (replay.ok, replay.toast, replay.verdict, replay.buttons) == (False, SF.TOAST_DECIDED, "Dismissed earlier", ())
    w2 = World()                       # (the dismissed pair is a tombstone: a new world for the expiry)
    g = _proposal(w2, acc(903))
    w2.fake.admin_update(F.FINDINGS, {"id": g["id"]}, {"status": "expired"})
    replay = w2.tap(C.VERB_DISMISS, g["id"], g["telegram_message_id"])
    assert (replay.verdict, replay.buttons) == ("Expired — not published", ())


def test_a_proposal_edit_that_lands_after_a_publish_tap_gets_the_withdraw_button_back(_on, monkeypatch):
    """Review A TG-1 (the race half): the run appends evidence, then edit_proposal waits on the pacer;
    a Publish tap lands meanwhile, and the paced edit puts Publish / Dismiss back on a LIVE row. The
    run re-reads the finding after its edit and restores the current state (Withdraw)."""
    w = World(configs=S2_ONLY)
    signing_acc, closing_acc = "0000050863-25-000155", "0000050863-25-000204"
    signing = (B.FIX / "html" / "intel_signing_8k.htm").read_text(encoding="utf-8")
    w.sec.set_document(signing_acc, "intc-20250915.htm", signing)
    w.sec.add_hit({"adsh": signing_acc, "ciks": ["0000050863"], "display_names": ["INTEL CORP  (INTC)  (CIK 0000050863)"],
                   "file_date": "2025-09-18", "file_type": "8-K", "form": "8-K", "items": ["3.02", "7.01", "9.01"],
                   "root_forms": ["8-K"], "sequence": 1}, "intc-20250915.htm")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_LOOKBACK_DAYS", 400)
    w.run(datetime(2025, 9, 18, 22, 30, tzinfo=timezone.utc))
    t2 = w.finding(signing_acc)
    real_edit = w.notifier.edit_proposal
    tapped: List[C.TapResult] = []

    async def edit_then_tap_lands(finding, row, *, message_id, trial, evidence):
        ok = await real_edit(finding, row, message_id=message_id, trial=trial, evidence=evidence)
        # the owner's Publish lands while the (paced) edit was in flight
        tapped.append(await SF.apply_owner_decision(C.VERB_PUBLISH, t2["id"], message_id=message_id,
                                                    now=datetime(2025, 12, 29, 23, 31, tzinfo=timezone.utc),
                                                    store=StakeStore(w.fake), club_store=TrillionClubStore(w.fake)))
        return ok

    monkeypatch.setattr(w.notifier, "edit_proposal", edit_then_tap_lands)
    w.run(datetime(2025, 12, 29, 23, 30, tzinfo=timezone.utc))
    assert tapped and tapped[0].ok, tapped
    assert w.finding(signing_acc)["status"] == C.STATUS_PUBLISHED
    marks = [m for m in w.notifier.of("mark") if m["message_id"] == t2["telegram_message_id"]]
    assert marks, w.notifier.calls
    assert list(marks[-1]["buttons"]) == [C.VERB_WITHDRAW] and marks[-1]["verdict"] == "Live — published earlier"


def test_publish_from_a_stale_message_is_refused(_on):
    w = World()
    f = _proposal(w)
    for mid in (None, f["telegram_message_id"] + 1):
        result = w.tap(C.VERB_PUBLISH, f["id"], mid)
        assert (result.ok, result.toast) == (False, SF.TOAST_STALE)
    assert w.finding(acc(901))["status"] == C.STATUS_PROPOSED and w.stakes() == []


def test_publish_re_runs_the_identity_gate(_on):
    """A row for the pair appeared since the proposal (a seed refresh): back to proposed."""
    w = World()
    f = _proposal(w)
    w.fake.admin_insert(F.STAKES, {
        "company_slug": "nvidia", "kind": "us_listed_off_13f", "investee_name": "Acme", "investee_cusip": ACME_CUSIP,
        "investee_us_symbol": "ACME", "as_of": "2026-10-01", "source_title": "Seed row",
        "source_url": "https://example.com/acme", "verified_on": "2026-10-02", "published": True, "sort_order": 1})
    result = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"])
    assert result.ok is False and "a row for this company is on file" in result.toast
    assert w.finding(acc(901))["status"] == C.STATUS_PROPOSED and w.stakes() == []


def test_dismiss_is_a_tombstone_and_a_replay_writes_nothing(_on):
    w = World()
    f = _proposal(w)
    result = w.tap(C.VERB_DISMISS, f["id"], f["telegram_message_id"])
    assert result.ok and (result.verdict, result.buttons) == ("Dismissed", ())
    g = w.finding(acc(901))
    assert (g["status"], g["owner_decision"]) == (C.STATUS_DISMISSED, C.DECISION_DISMISSED)
    writes = len(w.fake.writes())
    assert w.tap(C.VERB_DISMISS, f["id"], f["telegram_message_id"]).toast == SF.TOAST_DECIDED
    assert w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"]).toast == SF.TOAST_DECIDED
    assert len(w.fake.writes()) == writes, "a replay writes nothing"
    w.file_13g(acc(902), filed="2026-10-28", event="10/27/2026", pct="11.0",
               comments="Includes 1,000,000 shares issuable upon exercise of warrants.")
    w.run(LATER)
    assert w.finding(acc(902))["reason"] == "tombstone", "the owner said no to this pair"


def test_dismiss_is_refused_while_publishing(_on):
    w = World()
    f = _proposal(w)
    # a Publish in flight right now (fresh): never taken over
    w.fake.admin_update(F.FINDINGS, {"id": f["id"]}, {"status": "publishing", "updated_at": "2026-10-28T23:29:00Z"})
    result = w.tap(C.VERB_DISMISS, f["id"], f["telegram_message_id"])
    assert result.ok is False and "Publishing in progress" in result.toast
    assert w.finding(acc(901))["status"] == C.STATUS_PUBLISHING


@pytest.mark.parametrize("verb", [C.VERB_PUBLISH, C.VERB_DISMISS])
def test_a_tap_takes_over_a_stale_publish_that_wrote_no_row(_on, verb):
    """Final verify #4: a db_only run leaves a dead Publish (no row) to a full run, so meanwhile the
    owner's taps would all answer "still publishing". A tap on a STALE one with no row rolls it back
    and goes on; a fresh one, or one that owns a row, is still left alone."""
    w = World()
    f = _proposal(w)
    w.fake.admin_update(F.FINDINGS, {"id": f["id"]}, {"status": "publishing", "updated_at": "2026-10-28T22:40:00Z"})
    r = w.tap(verb, f["id"], f["telegram_message_id"])
    assert r.ok, r
    want = C.STATUS_PUBLISHED if verb == C.VERB_PUBLISH else C.STATUS_DISMISSED
    assert w.finding(acc(901))["status"] == want


def test_publish_and_dismiss_race_one_wins(_on):
    """A Dismiss lands right before Publish's compare-and-swap: Publish answers Already decided."""
    w = World()
    f = _proposal(w)

    def dismiss(db: F.FakeSupabase) -> None:
        db.admin_update(F.FINDINGS, {"id": f["id"]}, {"status": "dismissed", "owner_decision": "dismissed",
                                                     "decided_at": "2026-10-28T23:00:00Z"})

    w.fake.on_before(F.FINDINGS, "update", dismiss, match=lambda st: st.payload.get("status") == C.STATUS_PUBLISHING)
    result = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"])
    assert (result.ok, result.toast) == (False, SF.TOAST_DECIDED)
    assert w.stakes() == [] and w.finding(acc(901))["status"] == C.STATUS_DISMISSED


def test_keep_then_withdraw(_on):
    w = World()
    f = live_acme(w)
    kept = w.tap(C.VERB_KEEP, f["id"], f["telegram_message_id"])
    assert kept.ok and kept.buttons == (C.VERB_WITHDRAW,)
    s = w.stake()
    assert s["owner_kept_at"] is not None and w.finding(acc(901))["status"] == C.STATUS_KEPT
    assert w.tap(C.VERB_KEEP, f["id"], f["telegram_message_id"]).toast == SF.TOAST_DECIDED, "a replay"
    gone = w.tap(C.VERB_WITHDRAW, f["id"], f["telegram_message_id"])
    assert gone.ok and w.stake()["published"] is False and w.stake()["withdrawn_at"] is not None
    g = w.finding(acc(901))
    assert (g["status"], g["owner_decision"]) == (C.STATUS_WITHDRAWN, C.DECISION_WITHDRAWN)
    again = w.tap(C.VERB_WITHDRAW, f["id"], f["telegram_message_id"])
    assert (again.ok, again.toast) == (False, SF.TOAST_DECIDED)


def test_keep_is_fenced_on_its_own_note(_on):
    w = World()
    f = live_acme(w)
    assert w.tap(C.VERB_KEEP, f["id"], f["telegram_message_id"] + 7).toast == SF.TOAST_STALE
    assert w.tap(C.VERB_KEEP, f["id"], None).toast == SF.TOAST_STALE
    assert w.stake()["owner_kept_at"] is None


def test_keep_before_the_row_is_live_is_not_live_yet(_on):
    w = World()
    w.file_13g(acc(901))
    w.fake.fail(F.STAKES, "update", when="before", match=lambda st: st.payload.get("published") is True)
    w.run()
    f = w.finding(acc(901))
    assert f["status"] == C.STATUS_NOTED
    assert w.tap(C.VERB_KEEP, f["id"], f["telegram_message_id"]).toast == SF.TOAST_NOT_LIVE


def test_withdraw_works_from_any_linked_finding_and_counts_once(_on):
    w = World()
    first = live_acme(w)
    subs = w.sec.submissions_of(ACME_CIK)
    B.add_filing(subs, "0000876661-26-000999", "25-NSE", "2026-10-30", "xslF25X02/primary_doc.xml")
    w.run(MONDAY)
    note = w.finding("0000876661-26-000999")
    assert w.tap(C.VERB_WITHDRAW, note["id"], note["telegram_message_id"], now=MONDAY + timedelta(hours=1)).ok
    again = w.tap(C.VERB_WITHDRAW, first["id"], first["telegram_message_id"], now=MONDAY + timedelta(hours=2))
    assert again.ok is False
    decisions = [f for f in w.findings() if f["owner_decision"] == C.DECISION_WITHDRAWN]
    assert len(decisions) == 1, "one Withdraw, one decision (the breaker)"
    assert w.finding(acc(901))["status"] == C.STATUS_WITHDRAWN


def test_a_tap_on_an_unknown_or_malformed_finding(_on):
    w = World()
    assert w.tap(C.VERB_WITHDRAW, "00000000-0000-4000-8000-00000000abcd", 1).toast == SF.TOAST_GONE
    assert w.tap(C.VERB_WITHDRAW, "not-a-uuid", 1).toast == SF.TOAST_UNKNOWN
    assert w.tap("x", "00000000-0000-4000-8000-00000000abcd", 1).toast == SF.TOAST_UNKNOWN


def test_a_tap_that_crashes_answers_once_with_the_crash_toast(_on, monkeypatch):
    w = World()
    f = live_acme(w)

    async def boom(self, finding_id):  # noqa: ARG001
        raise RuntimeError("database on fire")

    monkeypatch.setattr(StakeStore, "read_finding", boom)
    result = w.tap(C.VERB_WITHDRAW, f["id"], f["telegram_message_id"])
    assert (result.ok, result.toast, result.verdict) == (False, SF.TOAST_CRASH, None)


def test_a_naive_tap_time_is_a_crash_toast_never_a_raise(_on):
    w = World()
    f = live_acme(w)
    result = asyncio.run(SF.apply_owner_decision(C.VERB_WITHDRAW, f["id"], message_id=f["telegram_message_id"],
                                                 now=datetime(2026, 10, 28, 19, 0), store=StakeStore(w.fake),
                                                 club_store=TrillionClubStore(w.fake)))
    assert result.toast == SF.TOAST_CRASH and w.stake()["published"] is True


def test_taps_work_with_the_finder_switched_off(_on, monkeypatch):
    w = World()
    f = live_acme(w)
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_FINDER_ENABLED", False)
    monkeypatch.setattr(settings, "TRILLION_CLUB_JOBS_ENABLED", False)
    assert w.tap(C.VERB_WITHDRAW, f["id"], f["telegram_message_id"]).ok
    assert w.stake()["published"] is False


def test_withdraw_during_publishing_wins(_on):
    """An update proposal is publishing when the owner withdraws the live row from its auto note:
    the replace's withdrawn_at fence refuses it and the row stays down."""
    w = World()
    first = live_acme(w)
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="12.4", form="SCHEDULE 13G/A")
    w.run(LATER)
    upd = w.finding(acc(902))

    def withdraw(db: F.FakeSupabase) -> None:
        sid = [s for s in db.tables[F.STAKES] if s["origin"] == "auto"][0]["id"]
        db.admin_update(F.STAKES, {"id": sid}, {"published": False, "withdrawn_at": "2026-10-29T00:00:00Z"})

    w.fake.on_before(F.STAKES, "update", withdraw, match=lambda st: st.payload.get("origin") == "proposal")
    result = w.tap(C.VERB_PUBLISH, upd["id"], upd["telegram_message_id"], now=LATER + timedelta(hours=1))
    assert result.ok is False
    s = w.stake()
    assert s["published"] is False and s["ownership_pct"] == 9.1 and s["finding_id"] == first["id"]


# ═════════════════════════════════════════════════════════════════════════════════════════
# §11 Guards the mutation pass found untested
# ═════════════════════════════════════════════════════════════════════════════════════════


def test_an_s1_filing_for_an_open_s1_proposal_adds_no_evidence_and_is_a_digest_line(_on):
    """A 13D/13G has no verified spans: nothing new to show in the open proposal, so its message is
    NOT edited and its facts never change — the newer filing is a digest line naming its facts."""
    w = World()
    first = _proposal(w)
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="12.0", form="SCHEDULE 13G/A",
               comments="Includes 1,000,000 shares issuable upon exercise of warrants.")
    w.run(LATER)
    second = w.finding(acc(902))
    assert (second["outcome"], second["reason"], second["status"]) == (C.OUTCOME_HEADS_UP, "existing_row",
                                                                       C.STATUS_REPORTED)
    assert w.notifier.of("edit") == [] and len(w.notifier.of("proposal")) == 1
    still = w.finding(acc(901))
    assert (still["fact_sha"], still["proposed_row"]) == (first["fact_sha"], first["proposed_row"])
    [line] = [x for x in w.notifier.digest_lines() if "open proposal says" in x]
    assert "this newer filing says Acme Robotics · 12%" in line and "an open proposal says Acme Robotics · 9.1%" in line, \
        line


def test_an_exit_for_a_published_proposal_row_offers_withdraw_and_keeps_it_live(_on):
    w = World()
    f = _proposal(w)
    assert w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"]).ok
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="3.1", form="SCHEDULE 13G/A")
    w.run(LATER + timedelta(hours=1))
    s = w.stake()
    assert s["published"] is True and s["origin"] == "proposal", "the owner's own publish stays until they withdraw"
    [note] = w.notifier.of("lifecycle")
    assert note["offer_withdraw"] is True and "stays live until you withdraw it" in note["text"]
    exit_finding = w.finding(acc(902))
    assert (exit_finding["status"], exit_finding["stake_id"]) == (C.STATUS_REPORTED, s["id"])
    assert w.tap(C.VERB_WITHDRAW, exit_finding["id"], exit_finding["telegram_message_id"],
                 now=LATER + timedelta(hours=2)).ok
    assert w.stake()["published"] is False and w.stake()["withdrawn_at"] is not None


def test_publish_is_fenced_on_the_fact_sha(_on):
    """The proposal's facts move between the tap's read and its compare-and-swap: nothing is
    published from a message that showed other facts."""
    w = World()
    f = _proposal(w)

    def change(db: F.FakeSupabase) -> None:
        db.admin_update(F.FINDINGS, {"id": f["id"]}, {"fact_sha": "b" * 64})

    w.fake.on_before(F.FINDINGS, "update", change, match=lambda st: st.payload.get("status") == C.STATUS_PUBLISHING)
    result = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"])
    assert (result.ok, result.toast) == (False, SF.TOAST_CHANGED)
    assert w.stakes() == [] and w.finding(acc(901))["status"] == C.STATUS_PROPOSED


def test_a_dismissed_update_does_not_block_a_later_update(_on):
    """Dismissing an UPDATE leaves the live row; a later amendment of that row is still proposed
    and publishes — the dismissed update is no tombstone for its own target."""
    w = World()
    live_acme(w)
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="12.4", form="SCHEDULE 13G/A")
    w.run(LATER)
    upd = w.finding(acc(902))
    assert w.tap(C.VERB_DISMISS, upd["id"], upd["telegram_message_id"]).ok
    assert w.stake()["ownership_pct"] == 9.1 and w.stake()["published"] is True and w.stake()["withdrawn_at"] is None
    w.file_13g(acc(903), filed="2026-10-28", event="10/27/2026", pct="15.0", form="SCHEDULE 13G/A")
    w.run(LATER + timedelta(hours=2))
    again = w.finding(acc(903))
    assert (again["tier"], again["status"]) == (C.TIER_UPDATE, C.STATUS_PROPOSED)
    result = w.tap(C.VERB_PUBLISH, again["id"], again["telegram_message_id"], now=LATER + timedelta(hours=3))
    assert result.ok, result.toast
    assert w.stake()["ownership_pct"] == 15.0 and w.stake()["published"] is True


def test_a_members_own_filing_is_recognised_by_its_cik_under_any_name(_on):
    """EFTS names the filer as it likes ("FACEBOOK INC"); the member's own CIK decides, before
    any document is fetched."""
    w = World(configs={"meta": B.CONFIGS["meta"]})
    meta_text = (B.FIX / "text" / "meta_424b5_final.txt").read_text(encoding="utf-8")
    w.sec.set_document("0001326801-26-000777", "own.htm", meta_text)
    w.sec.add_hit({"adsh": "0001326801-26-000777", "ciks": ["0001326801"],
                   "display_names": ["FACEBOOK INC  (META)  (CIK 0001326801)"], "file_date": "2026-10-27",
                   "file_type": "424B5", "form": "424B5", "items": [], "root_forms": ["424B5"], "sequence": 1},
                  "own.htm")
    w.run()
    f = w.finding("0001326801-26-000777", slug="meta")
    assert (f["outcome"], f["reason"]) == (C.OUTCOME_HEADS_UP, "member_filed")
    fetched = [c for c in w.sec.calls if c[0] == "get_filing_document" and c[1][1] == "0001326801-26-000777"]
    assert fetched == [], "no document read for the member's own filing"


def _noted_unpublished(w: World, accession: str = acc(901)) -> Dict[str, Any]:
    w.file_13g(accession)
    w.fake.fail(F.STAKES, "update", when="before", match=lambda st: st.payload.get("published") is True)
    w.run()
    f = w.finding(accession)
    assert f["status"] == C.STATUS_NOTED and not w.stake()["published"]
    return f


def test_a_registry_outage_runs_only_the_database_repairs(_on):
    """Switches on and the bot configured, but the registry cannot be read: no gate can run, so
    nothing is published or sent — a noted row waits."""
    w = World()
    _noted_unpublished(w)

    class Down(TrillionClubStore):
        async def read_companies(self):
            raise RuntimeError("supabase down")

    calls = len(w.notifier.calls)
    summary = asyncio.run(SF.run_stakes(LATER, sec=w.sec, store=StakeStore(w.fake), notifier=w.notifier,
                                        member_configs=w.configs, club_store=Down(w.fake)))
    assert summary["ok"] is False and any("registry read failed" in f for f in summary["failures"])
    assert w.finding(acc(901))["status"] == C.STATUS_NOTED and w.stake()["published"] is False
    assert len(w.notifier.calls) == calls


def test_an_identity_change_before_the_flip_holds_the_row_back_for_good(_on):
    """A row for the same investee appears between the note and the flip (a seed refresh): the
    publish gate refuses, the hidden duplicate is withdrawn, the note is marked, a digest line."""
    w = World()
    f = _noted_unpublished(w)
    w.fake.admin_insert(F.STAKES, {
        "company_slug": "nvidia", "kind": "us_listed_off_13f", "investee_name": "Acme", "investee_cusip": ACME_CUSIP,
        "investee_us_symbol": "ACME", "as_of": "2026-10-01", "source_title": "Seed row",
        "source_url": "https://example.com/acme", "verified_on": "2026-10-02", "published": True, "sort_order": 1})
    w.run(LATER)
    auto = [s for s in w.stakes() if s["origin"] == "auto"]
    assert auto[0]["published"] is False and auto[0]["withdrawn_at"] is not None
    g = w.finding(acc(901))
    assert (g["outcome"], g["reason"], g["status"]) == (C.OUTCOME_HEADS_UP, "existing_row", C.STATUS_REPORTED)
    [mark] = w.notifier.of("mark")
    assert mark["message_id"] == f["telegram_message_id"] and mark["verdict"].startswith("Held back")


def test_a_downgraded_auto_row_becomes_a_proposal_that_publishes_and_expires_cleanly(_on, monkeypatch):
    """The tier is switched off between the note and the flip: the unpublished auto row is ADOPTED
    as the proposal's row (no second INSERT); Publish flips it; an expiry withdraws it."""
    w = World()
    f = _noted_unpublished(w)
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_TIERS", "")
    w.run(LATER)
    g = w.finding(acc(901))
    assert (g["outcome"], g["reason"], g["status"]) == (C.OUTCOME_PROPOSAL, "tier_off", C.STATUS_PROPOSED)
    s = w.stake()
    assert (s["origin"], s["published"], s["finding_id"]) == ("proposal", False, f["id"])
    assert w.notifier.of("mark")[0]["verdict"].startswith("Held back — sent as a proposal")
    # 15 days on, unanswered: expired, and the adopted row it owns is withdrawn in the same run
    w.run(LATER + timedelta(days=15))
    assert w.finding(acc(901))["status"] == C.STATUS_EXPIRED
    assert w.stake()["withdrawn_at"] is not None and w.stake()["published"] is False


def test_a_downgraded_auto_row_publishes_without_a_second_insert(_on, monkeypatch):
    w = World()
    _noted_unpublished(w)
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_TIERS", "")
    w.run(LATER)
    g = w.finding(acc(901))
    inserts = len(w.fake.writes(F.STAKES, "insert"))
    assert w.tap(C.VERB_PUBLISH, g["id"], g["telegram_message_id"], now=LATER + timedelta(hours=1)).ok
    assert len(w.fake.writes(F.STAKES, "insert")) == inserts and len(w.stakes()) == 1
    assert w.stake()["published"] is True and w.stake()["origin"] == "proposal"


def test_a_second_exit_for_a_row_already_down_is_skipped(_on):
    w = World()
    live_acme(w)
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="4.2", form="SCHEDULE 13G/A")
    w.run(LATER)
    w.file_13g(acc(903), filed="2026-10-28", event="10/27/2026", pct="3.0", form="SCHEDULE 13G/A")
    w.run(LATER + timedelta(days=1))
    third = w.finding(acc(903))
    assert (third["outcome"], third["reason"]) == (C.OUTCOME_SKIPPED, "exit_filing")
    assert len(w.notifier.of("lifecycle")) == 1


def test_one_withdrawn_stake_is_one_decision_whatever_the_order(_on):
    """The Studio valve takes the row down; the owner then taps Withdraw on the re-check note (one
    decision), and the run's catch-up of the auto finding records none — the breaker sees one."""
    w = World()
    first = live_acme(w)
    B.add_filing(w.sec.submissions_of(ACME_CIK), "0000876661-26-000999", "25-NSE", "2026-10-30",
                 "xslF25X02/primary_doc.xml")
    w.run(MONDAY)
    note = w.finding("0000876661-26-000999")
    w.fake.admin_update(F.STAKES, {"id": first["stake_id"]}, {"published": False,
                                                             "withdrawn_at": "2026-11-03T00:00:00Z"})
    # the tap's move of the auto finding is lost: reconcile must catch it up WITHOUT a second decision
    w.fake.fail(F.FINDINGS, "update", when="before",
                match=lambda st: st.payload.get("status") == C.STATUS_WITHDRAWN
                and st.filter_value("eq", "id") == first["id"])
    result = w.tap(C.VERB_WITHDRAW, note["id"], note["telegram_message_id"], now=MONDAY + timedelta(hours=1))
    assert result.ok is False and result.toast == "Already withdrawn", "the row is down: never a crash toast"
    assert w.fake.pending_injections() == []
    assert w.finding(acc(901))["status"] == C.STATUS_PUBLISHED, "the injected failure held"
    w.run(MONDAY + timedelta(days=1))
    assert w.finding(acc(901))["status"] == C.STATUS_WITHDRAWN
    decisions = [f for f in w.findings() if f["owner_decision"] == C.DECISION_WITHDRAWN]
    assert len(decisions) == 1 and decisions[0]["id"] == note["id"]


def test_an_auto_find_beyond_the_message_cap_waits_with_no_row_written(_on):
    w = World()
    for i in range(10):
        cik, letter = f"00090001{i:02d}", chr(65 + i)
        w.sec.set_submissions(cik, B.empty_submissions(cik, f"Gamma {letter} Systems, Inc.", [f"GAM{letter}"],
                                                       ["Nasdaq"]))
        w.file_13g(acc(910 + i), issuer_cik=cik, issuer_name=f"Gamma {letter} Systems, Inc.",
                   cusip=_cusip(f"0045{i:02d}J1"), comments="Includes 1,000,000 shares issuable upon exercise of warrants.")
    w.file_13g(acc(990), filed="2026-10-21")
    w.run()
    acme = w.finding(acc(990))
    assert (acme["outcome"], acme["status"]) == (C.OUTCOME_AUTO, C.STATUS_NEW)
    assert [s for s in w.stakes() if s["investee_cik"] == ACME_CIK] == [], "nothing written while it waits"
    w.run(LATER)
    assert w.finding(acc(990))["status"] == C.STATUS_PUBLISHED


def test_publish_answers_published_when_only_the_ledger_follow_up_fails(_on):
    """The flip landed and the final ledger write failed: the row IS live, so the answer says so;
    the next run's reconcile records the decision."""
    w = World()
    f = _proposal(w)
    w.fake.fail(F.FINDINGS, "update", when="before", match=lambda st: st.payload.get("status") == C.STATUS_PUBLISHED)
    result = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"])
    assert result.ok and result.verdict.startswith("Published ") and w.fake.pending_injections() == []
    assert w.stake()["published"] is True and w.finding(acc(901))["status"] == C.STATUS_PUBLISHING
    w.run(LATER + timedelta(minutes=11))
    g = w.finding(acc(901))
    assert (g["status"], g["owner_decision"]) == (C.STATUS_PUBLISHED, C.DECISION_PUBLISHED)


def test_a_second_withdraw_from_another_message_adds_no_second_decision(_on):
    """Withdraw from the re-check note (the decision), the auto finding's move lost; then the
    owner taps Withdraw on the original auto note too: still exactly one decision."""
    w = World()
    first = live_acme(w)
    B.add_filing(w.sec.submissions_of(ACME_CIK), "0000876661-26-000999", "25-NSE", "2026-10-30",
                 "xslF25X02/primary_doc.xml")
    w.run(MONDAY)
    note = w.finding("0000876661-26-000999")
    w.fake.fail(F.FINDINGS, "update", when="before",
                match=lambda st: st.payload.get("status") == C.STATUS_WITHDRAWN
                and st.filter_value("eq", "id") == first["id"])
    assert w.tap(C.VERB_WITHDRAW, note["id"], note["telegram_message_id"], now=MONDAY + timedelta(hours=1)).ok
    assert w.finding(acc(901))["status"] == C.STATUS_PUBLISHED
    second = w.tap(C.VERB_WITHDRAW, first["id"], first["telegram_message_id"], now=MONDAY + timedelta(hours=2))
    assert (second.ok, second.toast) == (False, "Already withdrawn")
    assert w.finding(acc(901))["status"] == C.STATUS_WITHDRAWN
    decisions = [f for f in w.findings() if f["owner_decision"] == C.DECISION_WITHDRAWN]
    assert [d["id"] for d in decisions] == [note["id"]]


def _intel_closing_world(monkeypatch) -> World:
    w = World(configs=S2_ONLY)
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_LOOKBACK_DAYS", 120)
    return w


def test_an_s2_document_that_fails_transiently_is_an_error_finding_then_read(_on, monkeypatch):
    w = _intel_closing_world(monkeypatch)
    when = datetime(2025, 12, 30, 23, 30, tzinfo=timezone.utc)
    w.sec.fail["get_filing_document"] = [SECUnavailableException("504")]
    assert w.run(when)["ok"] is False
    errored = [f for f in w.findings() if f["status"] == C.STATUS_ERROR]
    assert len(errored) == 1 and errored[0]["source"] == C.SOURCE_EFTS and errored[0]["attempts"] == 1
    assert w.run(when + timedelta(hours=1))["ok"] is True
    assert w.finding(errored[0]["accession"])["status"] == C.STATUS_ERROR, "one attempt per ET day"
    assert w.run(when + timedelta(days=1))["ok"] is True
    f = w.finding(errored[0]["accession"])
    assert (f["outcome"], f["status"], f["attempts"], f["last_error"]) == (C.OUTCOME_PROPOSAL, C.STATUS_PROPOSED, 2,
                                                                           None), f
    assert f["reason"] in ("t1_closing", "t4_directed") and f["proposed_row"]["kind"]


def test_a_failed_13f_holdings_read_is_an_error_never_a_guessed_kind(_on, monkeypatch):
    """NVIDIA has use_13f: whether a row is a 13F note depends on its latest build. A read that
    fails is an error finding (retried), never "not on the 13F"."""
    w = World()
    w.file_13g(acc(901))

    async def down(self, cik):
        raise SS.StakeStoreError("read latest complete 13F", RuntimeError("supabase down"))

    monkeypatch.setattr(StakeStore, "read_latest_complete_holdings", down)
    summary = w.run()
    f = w.finding(acc(901))
    assert (f["status"], f["reason"]) == (C.STATUS_ERROR, "fetch_failed") and "13F holdings" in f["last_error"]
    assert summary["ok"] is False and w.stakes() == []


def test_classify_only_never_reaches_supabase(monkeypatch):
    import app.database as database

    def refused(*_a, **_k):
        raise AssertionError("classify_only reached Supabase")

    monkeypatch.setattr(database, "get_supabase", refused)
    found = B.classify(B.FakeSEC(), stakes=B.seed_stakes())
    assert found and {f["outcome"] for f in found} <= set(C.FINDING_OUTCOMES)
    assert all(set(f) == {"slug", "source", "accession", "form", "filed_on", "document", "source_url",
                          "investee_cik", "tier", "outcome", "reason", "row", "spans", "detail", "trial"}
               for f in found)


# ═════════════════════════════════════════════════════════════════════════════════════════
# §12 Review B (2026-10-29) — every confirmed scenario, replayed
# ═════════════════════════════════════════════════════════════════════════════════════════

WARRANTS = "Includes 1,000,000 shares issuable upon exercise of warrants."


def _gamma(w: World, i: int, accession: str, *, filed: str, event: str, comments: str = "") -> str:
    """A plain own 13G (or a warrant one) for a FRESH issuer "Gamma <letter> Systems" — a test that
    needs many pairs; the identity dedupe never joins two of them."""
    cik, letter = f"00090003{i:02d}", chr(65 + i)
    w.sec.set_submissions(cik, B.empty_submissions(cik, f"Gamma {letter} Systems, Inc.", [f"GAM{letter}"],
                                                   ["Nasdaq"]))
    w.file_13g(accession, filed=filed, event=event, issuer_cik=cik, issuer_name=f"Gamma {letter} Systems, Inc.",
               cusip=_cusip(f"0046{i:02d}J1"), comments=comments)
    return cik


def _published_on(w: World, day: date) -> List[str]:
    """The accessions whose row went live on ``day`` (ET), by the ledger's published_at."""
    out = []
    for f in w.findings():
        stamp = f.get("published_at")
        if stamp and datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(ET).date() == day:
            out.append(f["accession"])
    return sorted(out)


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ── WO-1 / GI-1 / F4 / facts-F2: gate 8 counts a publish on the day it goes live ─────────────


def test_gate_8_a_two_day_note_outage_then_new_filings_flips_at_most_three_a_day(_on):
    """Telegram refuses every LOUD message for two days, then comes back while new filings keep
    arriving: at most 3 rows go live per ET day, the rest are volume_cap proposals, and the day
    that hits the cap says so in ONE digest line (the cap used to count findings by created_at,
    so 9 rows went live on the third day)."""
    w = World()
    for i in range(3):
        _gamma(w, i, acc(920 + i), filed="2026-10-28", event="10/27/2026")
    w.notifier.fail |= {"auto", "proposal"}
    w.run(NOW)
    assert [w.finding(acc(920 + i))["status"] for i in range(3)] == [C.STATUS_STAGED] * 3
    for i in range(3, 6):
        _gamma(w, i, acc(920 + i), filed="2026-10-29", event="10/28/2026")
    w.run(NOW + timedelta(days=1))
    for i in range(3, 6):
        f = w.finding(acc(920 + i))
        assert (f["outcome"], f["reason"], f["status"]) == (C.OUTCOME_PROPOSAL, "volume_cap", C.STATUS_NEW), f
    w.notifier.fail.clear()
    for i in range(6, 9):
        _gamma(w, i, acc(920 + i), filed="2026-10-30", event="10/29/2026")
    digests = len(w.notifier.of("digest"))
    w.run(NOW + timedelta(days=2))
    assert _published_on(w, date(2026, 10, 30)) == [acc(920 + i) for i in range(3)]
    assert _published_on(w, date(2026, 10, 28)) == [] and _published_on(w, date(2026, 10, 29)) == []
    assert len([s for s in w.stakes() if s["published"]]) == 3
    for i in range(6, 9):
        f = w.finding(acc(920 + i))
        assert (f["outcome"], f["reason"], f["status"]) == (C.OUTCOME_PROPOSAL, "volume_cap", C.STATUS_PROPOSED), f
    lines = [x for p in w.notifier.of("digest")[digests:] for x in p["lines"] if "Auto-publish cap reached" in x]
    assert len(lines) == 1, lines


def test_gate_8_finds_waiting_on_the_message_cap_publish_at_most_three_the_next_day(_on):
    """Ten warrant proposals spend the run's ten LOUD messages; five plain 13Gs then wait as `new`.
    Each waiting auto find is a publish in waiting: the 4th and 5th become volume_cap proposals
    at once, and the next day exactly 3 go live (they all used to)."""
    w = World()
    for i in range(10):
        _gamma(w, i, acc(930 + i), filed="2026-10-20", event="10/19/2026", comments=WARRANTS)
    for i in range(10, 15):
        _gamma(w, i, acc(930 + i), filed="2026-10-21", event="10/20/2026")
    w.run(NOW)
    plain = [w.finding(acc(930 + i)) for i in range(10, 15)]
    assert [f["outcome"] for f in plain] == [C.OUTCOME_AUTO] * 3 + [C.OUTCOME_PROPOSAL] * 2
    assert all(f["status"] == C.STATUS_NEW for f in plain) and [f["reason"] for f in plain[3:]] == ["volume_cap"] * 2
    assert any("Auto-publish cap reached" in x for x in w.notifier.digest_lines())
    w.run(NOW + timedelta(days=1))
    assert _published_on(w, date(2026, 10, 29)) == [acc(940), acc(941), acc(942)]
    assert len([s for s in w.stakes() if s["published"]]) == 3
    assert [w.finding(acc(930 + i))["status"] for i in (13, 14)] == [C.STATUS_PROPOSED] * 2


def test_gate_8_an_error_retry_counts_on_the_day_it_publishes(_on):
    """A 13G whose XML failed yesterday is read again today and goes live today: it counts
    against TODAY (it used to count against the day its finding was created)."""
    w = World()
    _gamma(w, 0, acc(950), filed="2026-10-27", event="10/26/2026")
    w.sec.fail["get_filing_document"] = [SECUnavailableException("502")]
    assert w.run(NOW)["ok"] is False and w.finding(acc(950))["status"] == C.STATUS_ERROR
    for i in range(1, 4):
        _gamma(w, i, acc(950 + i), filed="2026-10-29", event="10/28/2026")
    w.run(NOW + timedelta(days=1))
    assert _published_on(w, date(2026, 10, 29)) == [acc(950), acc(951), acc(952)]
    last = w.finding(acc(953))
    assert (last["outcome"], last["reason"]) == (C.OUTCOME_PROPOSAL, "volume_cap")


def test_every_move_to_published_stamps_published_at(_on):
    """The auto flip, the proposal Publish and the update Publish all stamp published_at."""
    w = World()
    f = live_acme(w)
    assert w.finding(acc(901))["published_at"] is not None, "the auto flip stamps it"
    assert _ts(w.finding(acc(901))["published_at"]) == NOW
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="12.4", form="SCHEDULE 13G/A")
    w.run(LATER)
    upd = w.finding(acc(902))
    assert w.tap(C.VERB_PUBLISH, upd["id"], upd["telegram_message_id"], now=LATER + timedelta(hours=1)).ok
    assert _ts(w.finding(acc(902))["published_at"]) == LATER + timedelta(hours=1)
    assert _ts(w.finding(acc(901))["published_at"]) == NOW, "a superseded row keeps the day it went live"
    w2 = World()
    p = _proposal(w2)
    assert w2.tap(C.VERB_PUBLISH, p["id"], p["telegram_message_id"]).ok
    assert _ts(w2.finding(acc(901))["published_at"]) == LATER
    assert f["id"] != p["id"]


# ── TAP-1 / GI-3 / lifecycle-F2: an exit reaches the live row past an open update ───────────


def test_an_exit_while_an_update_is_open_takes_the_live_row_down_and_voids_the_update(_on):
    from app.services.trillion_club import stake_review as R

    w = World()
    live_acme(w)                                                     # 9.1% as of Oct 15, live
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="12.4", form="SCHEDULE 13G/A")
    w.run(LATER)
    upd = w.finding(acc(902))
    assert (upd["tier"], upd["status"]) == (C.TIER_UPDATE, C.STATUS_PROPOSED)
    w.file_13g(acc(903), filed="2026-10-28", event="10/27/2026", pct="3.1", form="SCHEDULE 13G/A")
    w.run(LATER + timedelta(hours=1))
    s = w.stake()
    assert (s["published"], s["withdrawn_at"], s["ownership_pct"]) == (False, None, 9.1), "taken down, dated"
    [note] = w.notifier.of("lifecycle")
    assert note["offer_withdraw"] is False and "taken down automatically" in note["text"]
    # the note links the exit filing (facts-F3), and still names the row's own source
    lines = R.lifecycle_text(note["finding"], note["row"], text=note["text"], offer_withdraw=False).split("\n")
    exit_f = w.finding(acc(903))
    assert exit_f["source_url"] in lines and note["row"]["source_url"] not in lines
    assert f"Source: {note['row']['source_title']}" in lines
    assert exit_f["status"] == C.STATUS_CLOSED and w.finding(acc(901))["status"] == C.STATUS_CLOSED
    u = w.finding(acc(902))
    assert (u["status"], u["reason"]) == (C.STATUS_SUPERSEDED, "exit"), "the open update is void"
    [void] = [m for m in w.notifier.of("mark") if m["message_id"] == upd["telegram_message_id"]]
    assert (void["verdict"], void["buttons"]) == (SF.VERDICT_VOID_EXIT, [])
    tap = w.tap(C.VERB_PUBLISH, upd["id"], upd["telegram_message_id"], now=LATER + timedelta(hours=2))
    assert (tap.ok, tap.toast, tap.verdict, tap.buttons) == (False, SF.TOAST_DECIDED, SF.VERDICT_VOID_EXIT, ())
    assert w.stake()["published"] is False and w.stake()["ownership_pct"] == 9.1


def test_the_publish_tap_refuses_an_update_older_than_the_exit_even_when_its_void_failed(_on):
    """Defence in depth (d): the void write fails, so the update's message still has Publish —
    the tap re-checks the pair's exits and refuses; the next run's reconcile repeats the void."""
    w = World()
    live_acme(w)
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="12.4", form="SCHEDULE 13G/A")
    w.run(LATER)
    upd = w.finding(acc(902))
    w.file_13g(acc(903), filed="2026-10-28", event="10/27/2026", pct="3.1", form="SCHEDULE 13G/A")
    w.fake.fail(F.FINDINGS, "update", when="before", match=lambda st: st.payload.get("status") == C.STATUS_SUPERSEDED)
    w.run(LATER + timedelta(hours=1))
    assert w.stake()["published"] is False, "the exit took the row down"
    assert w.fake.pending_injections() == [], "the void was attempted and failed"
    assert w.finding(acc(902))["status"] == C.STATUS_PROPOSED
    tap = w.tap(C.VERB_PUBLISH, upd["id"], upd["telegram_message_id"], now=LATER + timedelta(hours=2))
    assert tap.ok is False and "reports 5% or less" in tap.toast, tap
    assert w.stake()["published"] is False and w.stake()["ownership_pct"] == 9.1
    back = w.finding(acc(902))
    assert back["status"] == C.STATUS_PROPOSED and _ts(back["notified_at"]) == LATER + timedelta(hours=2)
    w.run(LATER + timedelta(days=1))
    assert w.finding(acc(902))["status"] == C.STATUS_SUPERSEDED and w.finding(acc(903))["status"] == C.STATUS_CLOSED
    assert len(w.notifier.of("lifecycle")) == 1


# ── GI-2 / lifecycle-F1 / facts-F1: an exit stops a publish still in waiting ────────────────


@pytest.mark.parametrize("left", ["staged", "noted", "same_run"])
def test_an_exit_for_a_row_waiting_for_its_note_or_flip_holds_it_back_for_good(left, _on):
    w = World()
    w.file_13g(acc(901))                                             # 9.1% as of Oct 15
    exit_kw = dict(filed="2026-10-28", event="10/27/2026", pct="4.2", form="SCHEDULE 13G/A")
    if left == "noted":
        # the note went out, the flip failed — in this run and in the next one's reconcile too
        w.fake.fail(F.STAKES, "update", when="before", times=2, match=lambda st: st.payload.get("published") is True)
    else:
        w.notifier.fail.add("auto")
    if left == "same_run":
        w.file_13g(acc(902), **exit_kw)
    w.run()
    if left != "same_run":
        assert w.finding(acc(901))["status"] == left and w.stake()["published"] is False
        w.file_13g(acc(902), **exit_kw)
        w.run(LATER)
    own, s = w.finding(acc(901)), w.stake()
    assert own["status"] == C.STATUS_CLOSED, own
    assert (s["published"], s["withdrawn_at"]) == (False, None), "held back — an exit is no tombstone"
    ex = w.finding(acc(902))
    assert (ex["outcome"], ex["reason"], ex["status"], ex["stake_id"]) == (C.OUTCOME_HEADS_UP, "exit",
                                                                           C.STATUS_REPORTED, None)
    [line] = [x for x in w.notifier.digest_lines() if "held back before it went live" in x]
    assert "Acme Robotics · 9.1%" in line and ex["source_url"] in line
    marks = [m for m in w.notifier.of("mark") if m["finding_id"] == own["id"]]
    if left == "noted":
        assert [(m["verdict"], m["buttons"]) for m in marks] == [(SF.VERDICT_HELD_EXIT, [])]
    else:
        assert marks == []
    w.notifier.fail.clear()
    notes = len(w.notifier.of("auto"))
    for later in (LATER + timedelta(days=1), LATER + timedelta(days=2)):
        w.run(later)
    assert w.stake()["published"] is False and w.notifier.of("lifecycle") == [], "never goes live"
    assert len(w.notifier.of("auto")) == notes, "no auto note after the hold"


def test_an_exit_for_a_find_waiting_on_the_message_cap_holds_it_and_says_so(_on):
    w = World()
    for i in range(10):
        _gamma(w, i, acc(930 + i), filed="2026-10-20", event="10/19/2026", comments=WARRANTS)
    w.file_13g(acc(990), filed="2026-10-21")                         # Acme: auto, waits `new` (no message left)
    w.file_13g(acc(991), filed="2026-10-27", event="10/26/2026", pct="2.0", form="SCHEDULE 13G/A")
    w.run()
    acme = w.finding(acc(990))
    assert (acme["outcome"], acme["status"]) == (C.OUTCOME_AUTO, C.STATUS_CLOSED)
    ex = w.finding(acc(991))
    assert (ex["outcome"], ex["reason"], ex["status"]) == (C.OUTCOME_HEADS_UP, "exit", C.STATUS_REPORTED)
    assert any("held back before it went live" in x and "Acme Robotics" in x for x in w.notifier.digest_lines())
    w.run(LATER + timedelta(days=1))
    assert [s for s in w.stakes() if s["investee_cik"] == ACME_CIK] == [], "nothing written, nothing live"


def test_an_exit_read_before_its_older_13g_binds_the_error_retry(_on):
    """The older 13G's XML fails once; its exit (read the same run) finds no row and is recorded
    with its event date. The next day's retry of the 13G is refused against that exit — never an
    auto row (it used to auto-publish the pre-exit 9.1%)."""
    w = World()
    w.file_13g(acc(901))                                             # 9.1% as of Oct 15, filed Oct 20
    w.file_13g(acc(902), filed="2026-10-27", event="10/24/2026", pct="4.0", form="SCHEDULE 13G/A", five_or_less="Y")
    w.sec.fail["get_filing_document"] = [SECUnavailableException("503")]
    w.run()
    assert w.finding(acc(901))["status"] == C.STATUS_ERROR
    ex = w.finding(acc(902))
    assert (ex["outcome"], ex["reason"]) == (C.OUTCOME_SKIPPED, "exit_filing")
    w.run(NOW + timedelta(days=1))
    f = w.finding(acc(901))
    assert (f["outcome"], f["reason"], f["status"]) == (C.OUTCOME_HEADS_UP, "exit", C.STATUS_REPORTED), f
    assert w.stakes() == [] and w.notifier.of("auto") == []
    assert any("later filing reports 5% or less" in x for x in w.notifier.digest_lines())
    assert w.finding(acc(902))["proposed_row"]["_event"] == "2026-10-24", "the exit keeps its date on the ledger"


def test_an_exit_voids_an_open_proposal_so_it_can_never_be_published(_on, monkeypatch):
    """The phase-1 default (AUTO_SINCE blank: every plain 13G is a proposal): an exit after the
    proposal voids it — superseded, its message marked with no buttons, one digest line."""
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_SINCE", "")
    w = World()
    w.file_13g(acc(901))
    w.run()
    f = w.finding(acc(901))
    assert (f["outcome"], f["status"]) == (C.OUTCOME_PROPOSAL, C.STATUS_PROPOSED)
    w.file_13g(acc(902), filed="2026-10-28", event="10/27/2026", pct="2.0", form="SCHEDULE 13G/A")
    w.run(LATER)
    g = w.finding(acc(901))
    assert (g["status"], g["reason"]) == (C.STATUS_SUPERSEDED, "exit")
    [mark] = [m for m in w.notifier.of("mark") if m["message_id"] == f["telegram_message_id"]]
    assert (mark["verdict"], mark["buttons"]) == (SF.VERDICT_VOID_EXIT, [])
    assert any("the open proposal is void" in x for x in w.notifier.digest_lines())
    assert not any("Handed over" in x for x in w.notifier.digest_lines()), "a void is no hand-over"
    tap = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"], now=LATER + timedelta(hours=1))
    assert tap.ok is False and w.stakes() == []


def test_a_proposal_dated_after_the_exit_is_never_voided_by_it(_on, monkeypatch):
    """Honest twin: an exit is about what was held BEFORE it — a newer open proposal (a re-entry)
    stays open."""
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_SINCE", "")
    w = World()
    w.file_13g(acc(901), filed="2026-10-27", event="10/26/2026")
    w.run()
    w.file_13g(acc(902), filed="2026-10-28", event="10/20/2026", pct="2.0", form="SCHEDULE 13G/A")
    w.run(LATER)
    assert w.finding(acc(901))["status"] == C.STATUS_PROPOSED
    ex = w.finding(acc(902))
    assert (ex["outcome"], ex["reason"]) == (C.OUTCOME_SKIPPED, "exit_filing")
    assert w.notifier.of("mark") == []


def test_publish_gates_refuse_an_auto_row_older_than_an_exit_on_file(_on):
    """Defence in depth (d): the exit's hold write fails, so the pending row reaches its publish
    gate: the pair's exit on file refuses it — a heads-up, its stake left unpublished (never a
    tombstone), no owner decision."""
    w = World()
    w.file_13g(acc(901))
    w.notifier.fail.add("auto")
    w.run()
    assert w.finding(acc(901))["status"] == C.STATUS_STAGED
    w.file_13g(acc(902), filed="2026-10-28", event="10/27/2026", pct="4.2", form="SCHEDULE 13G/A")
    w.fake.fail(F.FINDINGS, "update", when="before", match=lambda st: st.payload.get("status") == C.STATUS_CLOSED)
    w.run(LATER)
    assert w.finding(acc(901))["status"] == C.STATUS_STAGED
    w.notifier.fail.clear()
    notes = len(w.notifier.of("auto"))
    w.run(LATER + timedelta(days=1))
    f, s = w.finding(acc(901)), w.stake()
    assert (s["published"], s["withdrawn_at"]) == (False, None), "never live — and never a tombstone"
    assert (f["outcome"], f["reason"], f["status"]) == (C.OUTCOME_HEADS_UP, "exit", C.STATUS_REPORTED), f
    assert len(w.notifier.of("auto")) == notes, "no note: the row never goes live"
    assert [x for x in w.findings() if x["owner_decision"]] == []
    assert w.fake.pending_injections() == [], "the exit's hold was attempted and failed"
    assert w.finding(acc(902))["status"] == C.STATUS_REPORTED, "the exit's line went out"


# ── TAP-2: an update can never put a row an exit took down back up ──────────────────────────


def test_an_older_amendment_read_after_the_exit_is_never_an_update(_on):
    w = World()
    live_acme(w)
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="12.4", form="SCHEDULE 13G/A")
    w.file_13g(acc(903), filed="2026-10-28", event="10/27/2026", pct="3.1", form="SCHEDULE 13G/A")
    w.sec.fail["get_filing_document"] = [SECUnavailableException("503")]    # the amendment's XML, once
    w.run(LATER)
    assert w.stake()["published"] is False and w.finding(acc(902))["status"] == C.STATUS_ERROR
    w.run(NOW + timedelta(days=1))
    f = w.finding(acc(902))
    assert (f["outcome"], f["reason"]) == (C.OUTCOME_HEADS_UP, "exit"), f
    assert w.notifier.of("proposal") == [] and w.stake()["published"] is False


def test_a_reentry_after_an_exit_is_an_update_of_the_downed_row_that_says_so_and_publishes(_on):
    from app.services.trillion_club import stake_review as R

    w = World()
    live_acme(w)
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="4.2", form="SCHEDULE 13G/A")
    w.run(LATER)
    assert w.stake()["published"] is False
    tuesday = datetime(2026, 11, 3, 23, 30, tzinfo=timezone.utc)
    w.file_13g(acc(903), filed="2026-11-03", event="11/02/2026", pct="8.0", form="SCHEDULE 13G/A")
    w.run(tuesday)
    u = w.finding(acc(903))
    assert (u["tier"], u["status"]) == (C.TIER_UPDATE, C.STATUS_PROPOSED)
    [sent] = [p for p in w.notifier.of("proposal") if p["finding"]["id"] == u["id"]]
    text = R.proposal_text(sent["finding"], sent["row"], trial=False, evidence=[])
    assert "not live now" in text and "the live row" not in text, text
    assert sent["finding"].get("target_live") is False
    assert w.tap(C.VERB_PUBLISH, u["id"], u["telegram_message_id"], now=tuesday + timedelta(hours=1)).ok
    s = w.stake()
    assert (s["published"], s["ownership_pct"], s["as_of"], s["origin"]) == (True, 8.0, "2026-11-02", "proposal")


# ── lifecycle-F5: the Monday re-check reads the pair's findings in ANY status ───────────────


def test_monday_never_bumps_a_proposal_row_its_members_exit_contradicts(_on):
    w = World()
    f = _proposal(w)
    assert w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"]).ok
    w.file_13g(acc(902), filed="2026-10-28", event="10/27/2026", pct="3.1", form="SCHEDULE 13G/A")
    w.run(LATER + timedelta(hours=1))
    assert w.stake()["published"] is True and w.finding(acc(902))["status"] == C.STATUS_REPORTED
    summary = w.run(MONDAY)
    assert w.stake()["verified_on"] == "2026-10-28" and summary["stages"]["recheck"].get("unclear") == 1


def test_monday_never_bumps_a_row_whose_newer_update_expired_unanswered(_on):
    w = World()
    live_acme(w)
    w.file_13g(acc(902), filed="2026-10-27", event="10/26/2026", pct="15.0", form="SCHEDULE 13G/A")
    w.run(LATER)
    w.run(NOW + timedelta(days=15))                                  # Thu Nov 12: expired unanswered
    assert w.finding(acc(902))["status"] == C.STATUS_EXPIRED
    w.run(datetime(2026, 11, 16, 23, 30, tzinfo=timezone.utc))      # Monday
    assert w.stake()["verified_on"] == "2026-10-28" and w.stake()["ownership_pct"] == 9.1


def test_monday_still_bumps_a_row_whose_newer_filing_only_restated_it(_on):
    """Honest twin: a restatement with the same facts (skipped) supersedes nothing."""
    w = World()
    live_acme(w)
    w.file_13g(acc(902), filed="2026-10-27", form="SCHEDULE 13G/A")   # the same 9.1% as of Oct 15
    w.run(LATER)
    assert w.finding(acc(902))["outcome"] == C.OUTCOME_SKIPPED
    w.run(MONDAY)
    assert w.stake()["verified_on"] == "2026-11-02"


# ── facts-F4: a private row whose issuer now lists a U.S. ticker is not re-verified ─────────


@pytest.mark.parametrize("tickers, exchanges, bumped", [
    ([], [], True),                                  # still unlisted: a definite pass
    (["ACME"], ["Nasdaq"], False),                   # an IPO since: unclear
    (["ACME", "ACMB"], ["Nasdaq", "NYSE"], False),   # ambiguous: unclear
    (["ACMEF"], ["OTC"], True),                      # OTC never counts as a U.S. listing
])
def test_monday_a_private_row_is_re_verified_only_while_its_issuer_lists_no_us_ticker(tickers, exchanges, bumped,
                                                                                     _on):
    w = World()
    w.sec.set_submissions(ACME_CIK, B.empty_submissions(ACME_CIK, "Acme Robotics, Inc.", [], []))
    w.file_13g(acc(901))
    w.run()
    f = w.finding(acc(901))
    assert (f["outcome"], f["reason"], f["proposed_row"]["kind"]) == (C.OUTCOME_PROPOSAL, "no_us_listing", "private")
    assert w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"]).ok
    w.sec.set_submissions(ACME_CIK, B.empty_submissions(ACME_CIK, "Acme Robotics, Inc.", tickers, exchanges))
    w.run(MONDAY)
    assert w.stake()["verified_on"] == ("2026-11-02" if bumped else "2026-10-28")


# ── WO-2: a held-back duplicate is hidden AFTER the ledger moved — never an owner Withdraw ──

REASON_EXISTING = C.REASONS["existing_row"]
_HELD_BACK_WRITES = {
    "heads-up CAS": (F.FINDINGS, "update", lambda st: st.payload.get("outcome") == C.OUTCOME_HEADS_UP),
    "hide": (F.STAKES, "update", lambda st: st.payload.get("withdrawn_at") is not None),
}


def _seed_acme(w: World) -> None:
    w.fake.admin_insert(F.STAKES, {
        "company_slug": "nvidia", "kind": "us_listed_off_13f", "investee_name": "Acme", "investee_cusip": ACME_CUSIP,
        "investee_us_symbol": "ACME", "as_of": "2026-10-01", "source_title": "Seed row",
        "source_url": "https://example.com/acme", "verified_on": "2026-10-02", "published": True, "sort_order": 1})


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("step", sorted(_HELD_BACK_WRITES))
def test_a_failure_at_each_held_back_write_never_reads_as_an_owner_withdraw(step, when, _on):
    w = World()
    f = _noted_unpublished(w)
    _seed_acme(w)                                     # a seed refresh adds the hand-kept row
    table, op, match = _HELD_BACK_WRITES[step]
    w.fake.fail(table, op, when=when, match=match)
    w.run(LATER)
    assert w.fake.pending_injections() == [], "the failure fired"
    w.run(LATER + timedelta(hours=1))
    w.run(LATER + timedelta(days=1))
    [auto] = [s for s in w.stakes() if s["origin"] == "auto"]
    assert auto["published"] is False and auto["withdrawn_at"] is not None, "the duplicate is hidden"
    g = w.finding(acc(901))
    assert (g["outcome"], g["reason"], g["status"], g["stake_id"]) == (C.OUTCOME_HEADS_UP, "existing_row",
                                                                       C.STATUS_REPORTED, auto["id"])
    assert [x["id"] for x in w.findings() if x["owner_decision"]] == [], "no owner decision was invented"
    since = asyncio.run(StakeStore(w.fake).read_decisions_since(C.DECISION_WITHDRAWN, NOW - timedelta(days=1)))
    assert since == [], "the breaker counts nothing"
    assert any(m["finding_id"] == f["id"] and m["verdict"].startswith("Held back") for m in w.notifier.of("mark"))
    assert len([x for x in w.notifier.digest_lines() if REASON_EXISTING in x and "Acme Robotics" in x]) == 1


def test_a_heads_up_finding_is_never_caught_up_as_an_owner_withdraw(_on):
    """_catch_up_withdrawn's own guard: a finding the finder downgraded to a heads-up whose stake
    is withdrawn records no owner decision (the twin below: an auto finding still does)."""
    w = World()
    f = _noted_unpublished(w)
    w.fake.admin_update(F.FINDINGS, {"id": f["id"]}, {"outcome": "heads_up", "reason": "existing_row"})
    w.fake.admin_update(F.STAKES, {"id": f["stake_id"]}, {"withdrawn_at": "2026-10-28T22:40:00Z"})
    w.run(LATER)
    g = w.finding(acc(901))
    assert (g["status"], g["owner_decision"]) == (C.STATUS_WITHDRAWN, None)
    w2 = World()
    f2 = _noted_unpublished(w2)
    w2.fake.admin_update(F.STAKES, {"id": f2["stake_id"]}, {"withdrawn_at": "2026-10-28T22:40:00Z"})
    w2.run(LATER)
    assert w2.finding(acc(901))["owner_decision"] == C.DECISION_WITHDRAWN, "the owner's Withdraw is still recorded"


# ── WO-3 / TAP-3: a Publish tap that fails after its claim ──────────────────────────────────


def test_a_publish_tap_that_fails_before_writing_a_row_rolls_back_and_restarts_the_ttl(_on):
    w = World()
    f = _proposal(w)                                                 # sent at NOW
    late = NOW + timedelta(days=13, hours=23)
    w.fake.fail(F.STAKES, "insert", when="before")
    result = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"], now=late)
    assert (result.ok, result.toast) == (False, SF.TOAST_TRY_AGAIN)
    g = w.finding(acc(901))
    assert g["status"] == C.STATUS_PROPOSED and _ts(g["notified_at"]) == late and w.stakes() == []
    w.run(NOW + timedelta(days=14, hours=1))                         # past the ORIGINAL 14 days
    assert w.finding(acc(901))["status"] == C.STATUS_PROPOSED, "the owner answered: never expired"
    assert w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"], now=NOW + timedelta(days=14, hours=2)).ok
    assert w.stake()["published"] is True


def test_a_publish_tap_whose_registry_read_fails_rolls_back_and_says_try_again(_on, monkeypatch):
    w = World()
    f = _proposal(w)

    async def down(self):
        raise RuntimeError("registry down")

    monkeypatch.setattr(TrillionClubStore, "read_companies", down)
    result = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"])
    assert (result.ok, result.toast) == (False, SF.TOAST_TRY_AGAIN)
    assert w.finding(acc(901))["status"] == C.STATUS_PROPOSED


def test_a_publish_tap_whose_link_write_fails_is_finished_by_the_run_with_a_withdraw_button(_on):
    w = World()
    f = _proposal(w)
    w.fake.fail(F.FINDINGS, "update", when="before",
                match=lambda st: "stake_id" in st.payload and "status" not in st.payload)
    result = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"])
    assert (result.ok, result.toast) == (False, SF.TOAST_UNFINISHED)
    assert w.finding(acc(901))["status"] == C.STATUS_PUBLISHING and w.stake()["published"] is False
    again = w.tap(C.VERB_PUBLISH, f["id"], f["telegram_message_id"])
    assert again.toast == SF.TOAST_PUBLISHING
    w.run(LATER + timedelta(minutes=11))
    g = w.finding(acc(901))
    assert (g["status"], g["owner_decision"], g["stake_id"]) == (C.STATUS_PUBLISHED, C.DECISION_PUBLISHED,
                                                                 w.stake()["id"])
    assert w.stake()["published"] is True
    [mark] = [m for m in w.notifier.of("mark") if m["message_id"] == f["telegram_message_id"]]
    assert mark["buttons"] == [C.VERB_WITHDRAW] and "finished by the run" in mark["verdict"]


def test_the_runs_roll_back_of_a_dead_publish_restarts_the_ttl_so_its_expiry_never_tombstones_it(_on):
    w = World()
    f = _proposal(w)                                                 # sent at NOW
    w.fake.admin_update(F.FINDINGS, {"id": f["id"]},
                        {"status": "publishing", "updated_at": (NOW + timedelta(days=13, hours=23)).isoformat()})
    day14 = NOW + timedelta(days=14, hours=1)
    w.run(day14)
    g = w.finding(acc(901))
    assert g["status"] == C.STATUS_PROPOSED, "rolled back — and NOT expired by the same run"
    assert _ts(g["notified_at"]) == day14
    assert any("did not finish" in x for x in w.notifier.digest_lines())


def test_a_db_only_run_leaves_a_stale_publish_for_a_full_run_that_reports_it(_on, monkeypatch):
    """Verifier R4: a db_only run sends no digest, so a roll-back there would lose its "a Publish tap
    did not finish — back to a proposal" line. It waits; the next FULL run rolls back and says so."""
    w = World()
    f = _proposal(w)
    w.fake.admin_update(F.FINDINGS, {"id": f["id"]}, {"status": "publishing", "updated_at": "2026-10-28T22:40:00Z"})
    _gamma(w, 0, acc(903), filed="2026-10-21", event="10/20/2026", comments=WARRANTS)
    w.run(NOW + timedelta(minutes=5))
    g = w.finding(acc(903))
    assert g["status"] == C.STATUS_PROPOSED
    w.fake.fail(F.STAKES, "update", when="before", match=lambda st: st.payload.get("published") is True)
    w.tap(C.VERB_PUBLISH, g["id"], g["telegram_message_id"])
    assert w.finding(acc(903))["status"] == C.STATUS_PUBLISHING and len(w.stakes()) == 1   # it owns a row
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_FINDER_ENABLED", False)
    calls = len(w.notifier.calls)
    w.run(LATER + timedelta(hours=1))
    assert w.finding(acc(901))["status"] == C.STATUS_PUBLISHING, "no roll-back without a digest to report it"
    assert w.finding(acc(903))["status"] == C.STATUS_PUBLISHING, "a row exists: never published from db_only"
    assert len(w.notifier.calls) == calls and not [s for s in w.stakes() if s["published"]]
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_FINDER_ENABLED", True)
    w.run(LATER + timedelta(hours=2))
    assert w.finding(acc(901))["status"] == C.STATUS_PROPOSED
    assert any("did not finish" in line for line in w.notifier.digest_lines()), w.notifier.digest_lines()


# ── TAP-4: a Dismiss that landed is answered Dismissed ──────────────────────────────────────


def test_a_dismiss_whose_stake_withdrawal_fails_still_answers_dismissed(_on, monkeypatch):
    w = World()
    _noted_unpublished(w)
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_TIERS", "")
    w.run(LATER)                                                     # downgraded: its row adopted as the proposal's
    g = w.finding(acc(901))
    assert g["status"] == C.STATUS_PROPOSED and w.stake()["origin"] == "proposal"
    w.fake.fail(F.STAKES, "update", when="before", match=lambda st: st.payload.get("withdrawn_at") is not None)
    result = w.tap(C.VERB_DISMISS, g["id"], g["telegram_message_id"], now=LATER + timedelta(hours=1))
    assert (result.ok, result.toast, result.verdict, result.buttons) == (True, "Dismissed", "Dismissed", ())
    assert w.finding(acc(901))["status"] == C.STATUS_DISMISSED and w.stake()["withdrawn_at"] is None
    w.run(LATER + timedelta(hours=2))
    assert w.stake()["withdrawn_at"] is not None and w.stake()["published"] is False


# ── WO-4: an update of a not-yet-live auto row never hides it ───────────────────────────────


def test_a_staged_auto_row_and_its_amendment_put_exactly_one_row_live(_on):
    w = World()
    w.notifier.fail.add("auto")
    w.file_13g(acc(901))                                             # 9.1%: staged (its note refused)
    w.run()
    w.file_13g(acc(902), filed="2026-10-28", event="10/27/2026", pct="10.5", form="SCHEDULE 13G/A")
    w.run(LATER)                                                     # notes still refused; the update goes out
    upd = w.finding(acc(902))
    assert (upd["tier"], upd["status"], upd["stake_id"]) == (C.TIER_UPDATE, C.STATUS_PROPOSED, w.stake()["id"])
    [sent] = [p for p in w.notifier.of("proposal") if p["finding"]["id"] == upd["id"]]
    w.notifier.fail.clear()
    w.run(LATER + timedelta(hours=1))
    s = w.stake()
    assert (s["published"], s["withdrawn_at"], s["ownership_pct"]) == (True, None, 9.1), "the auto row went live"
    assert sent["finding"].get("target_live") is False, "its proposal said the row was not live"
    assert w.finding(acc(901))["status"] == C.STATUS_PUBLISHED and w.finding(acc(902))["status"] == C.STATUS_PROPOSED
    assert w.tap(C.VERB_PUBLISH, upd["id"], upd["telegram_message_id"], now=LATER + timedelta(hours=2)).ok
    assert [(x["published"], x["ownership_pct"]) for x in w.stakes()] == [(True, 10.5)], "exactly one row, updated"


# ── S2: one failing query never stops the stage ─────────────────────────────────────────────


def test_an_efts_failure_on_one_alias_never_stops_the_other_aliases_or_members(_on, caplog):
    configs = {"alphabet": dataclasses.replace(CONFIGS["alphabet"], s1_filers=(), intermediates=()),
               "nvidia": S2_ONLY["nvidia"]}
    w = World(configs=configs)
    w.sec.fail["full_text_search"] = [SECUnavailableException("HTTP 503 from efts (after one retry)")]
    with caplog.at_level(logging.WARNING):
        summary = w.run()
    searched = {c[1][0].split('"')[1] for c in w.sec.calls if c[0] == "full_text_search"}
    assert searched == {"Alphabet Inc.", "Google LLC", "NVIDIA Corporation"}, searched
    assert summary["ok"] is False and summary["stages"]["s2"]["ok"] is False
    [failure] = [x for x in summary["failures"] if "full-text search for" in x]
    assert "alphabet" in failure and "'Alphabet Inc.'" in failure and "the next run retries" in failure
    assert not any("crashed" in x for x in summary["failures"])
    assert not any("stage crashed" in r.getMessage() for r in caplog.records)
    assert any(f["company_slug"] == "nvidia" and f["source"] == C.SOURCE_EFTS for f in w.findings()), \
        "the next member's search still recorded its finds"


# ── Tests the review-B mutation pass asked for ──────────────────────────────────────────────


def test_a_publish_time_volume_cap_is_announced_too(_on):
    """Two auto rows went live earlier today, so consume admits the find (it is staged: its note is
    refused); a third goes live before the next attempt — the staged row's publish gate now hits
    the cap: a volume_cap proposal, and the digest says the cap was reached."""
    w = World()

    def live_today(n: int) -> None:
        w.fake.admin_insert(F.FINDINGS, {
            "company_slug": "nvidia", "source": "sch13", "accession": acc(n), "filer_cik": NV_CIK,
            "form": "SCHEDULE 13G", "filed_on": "2026-10-27", "document": "primary_doc.xml",
            "source_url": "https://www.sec.gov/Archives/edgar/data/1045810/x", "tier": "13dg", "outcome": "auto",
            "reason": "plain_13dg", "status": "published", "published_at": "2026-10-28T20:00:00Z"})

    live_today(960)
    live_today(961)
    w.file_13g(acc(901))
    w.notifier.fail.add("auto")
    w.run(NOW - timedelta(minutes=30))
    assert w.finding(acc(901))["status"] == C.STATUS_STAGED, "consume admitted it: 2 live today"
    assert not any("cap reached" in x for x in w.notifier.digest_lines())
    live_today(962)
    w.notifier.fail.clear()
    digests = len(w.notifier.of("digest"))
    w.run(NOW)
    f = w.finding(acc(901))
    assert (f["outcome"], f["reason"], f["status"]) == (C.OUTCOME_PROPOSAL, "volume_cap", C.STATUS_PROPOSED)
    lines = [x for p in w.notifier.of("digest")[digests:] for x in p["lines"] if "Auto-publish cap reached" in x]
    assert len(lines) == 1


def test_an_older_exit_read_late_never_voids_a_newer_update(_on):
    """The exit (Oct 20) is read a day late, after a NEWER amendment (Oct 26) became an update
    proposal: the row comes down, but the newer update stays open and still publishes."""
    w = World()
    live_acme(w)                                                     # 9.1% as of Oct 15
    w.file_13g(acc(902), filed="2026-10-22", event="10/20/2026", pct="3.0", form="SCHEDULE 13G/A")
    w.file_13g(acc(903), filed="2026-10-27", event="10/26/2026", pct="12.4", form="SCHEDULE 13G/A")
    w.sec.fail["get_filing_document"] = [SECUnavailableException("503")]   # the exit's XML, once
    w.run(LATER)
    upd = w.finding(acc(903))
    assert (upd["tier"], upd["status"]) == (C.TIER_UPDATE, C.STATUS_PROPOSED)
    w.run(NOW + timedelta(days=1))
    assert w.stake()["published"] is False, "the exit took the older row down"
    assert w.finding(acc(903))["status"] == C.STATUS_PROPOSED, "the newer update is untouched"
    assert w.tap(C.VERB_PUBLISH, upd["id"], upd["telegram_message_id"], now=NOW + timedelta(days=1, hours=1)).ok
    assert (w.stake()["published"], w.stake()["ownership_pct"]) == (True, 12.4)


def test_a_waiting_proposal_whose_void_failed_is_never_sent_after_the_exit(_on):
    w = World()
    for i in range(10):
        _gamma(w, i, acc(930 + i), filed="2026-10-20", event="10/19/2026", comments=WARRANTS)
    w.file_13g(acc(990), filed="2026-10-21", comments=WARRANTS)       # Acme: a proposal waiting `new`
    w.file_13g(acc(991), filed="2026-10-27", event="10/26/2026", pct="2.0", form="SCHEDULE 13G/A")
    w.fake.fail(F.FINDINGS, "update", when="before", match=lambda st: st.payload.get("status") == C.STATUS_SUPERSEDED)
    w.run()
    assert w.fake.pending_injections() == [] and w.finding(acc(990))["status"] == C.STATUS_NEW
    # the exit's line was held back (its sweep failed this run: final verify #7)…
    assert w.finding(acc(991))["status"] == C.STATUS_NEW
    w.run(LATER + timedelta(days=1))
    # …so the next run either repeats the sweep (the waiting proposal is VOID) or, when reconcile reaches
    # the proposal first (same created_at: the order follows the random ids), refuses it against the
    # exit on file (a heads-up). Either way it is never sent and can never be published.
    f = w.finding(acc(990))
    assert f["reason"] == "exit" and (
        (f["status"], f["outcome"]) == (C.STATUS_SUPERSEDED, C.OUTCOME_PROPOSAL)
        or (f["outcome"] == C.OUTCOME_HEADS_UP and f["status"] in (C.STATUS_NEW, C.STATUS_REPORTED))), f
    assert w.finding(acc(991))["status"] == C.STATUS_REPORTED
    assert not [p for p in w.notifier.of("proposal") if p["finding"]["accession"] == acc(990)], "never sent"


def test_reconcile_repeats_an_exit_lines_sweep_until_it_is_done(_on, monkeypatch):
    """The exit's void failed AND its digest was not delivered (the line stays `new`): the next run's
    reconcile repeats the sweep — the proposal is void before the line goes out."""
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_SINCE", "")
    w = World()
    w.file_13g(acc(901))
    w.run()
    f = w.finding(acc(901))
    w.file_13g(acc(902), filed="2026-10-28", event="10/27/2026", pct="2.0", form="SCHEDULE 13G/A")
    w.fake.fail(F.FINDINGS, "update", when="before", match=lambda st: st.payload.get("status") == C.STATUS_SUPERSEDED)
    w.notifier.fail.add("digest")
    w.run(LATER)
    assert w.finding(acc(901))["status"] == C.STATUS_PROPOSED and w.finding(acc(902))["status"] == C.STATUS_NEW
    w.notifier.fail.clear()
    w.run(LATER + timedelta(hours=1))
    assert (w.finding(acc(901))["status"], w.finding(acc(901))["reason"]) == (C.STATUS_SUPERSEDED, "exit")
    assert w.finding(acc(902))["status"] == C.STATUS_REPORTED
    assert [m["verdict"] for m in w.notifier.of("mark") if m["message_id"] == f["telegram_message_id"]] == [
        SF.VERDICT_VOID_EXIT]


@pytest.mark.parametrize("left", ["staged", "noted"])
def test_publish_gates_refuse_a_row_older_than_an_exit_and_mark_its_delivered_note(left, _on):
    w = World()
    w.file_13g(acc(901))
    if left == "noted":
        w.fake.fail(F.STAKES, "update", when="before", times=2, match=lambda st: st.payload.get("published") is True)
    else:
        w.notifier.fail.add("auto")
    w.run()
    w.file_13g(acc(902), filed="2026-10-28", event="10/27/2026", pct="4.2", form="SCHEDULE 13G/A")
    w.fake.fail(F.FINDINGS, "update", when="before", match=lambda st: st.payload.get("status") == C.STATUS_CLOSED)
    w.run(LATER)
    w.notifier.fail.clear()
    w.run(LATER + timedelta(days=1))
    f, s = w.finding(acc(901)), w.stake()
    assert (f["outcome"], f["reason"], s["published"], s["withdrawn_at"]) == (C.OUTCOME_HEADS_UP, "exit", False, None)
    marks = [m for m in w.notifier.of("mark") if m["finding_id"] == f["id"]]
    if left == "noted":
        assert [(m["verdict"], m["buttons"]) for m in marks] == [(SF.VERDICT_HELD_EXIT, [])]
    else:
        assert marks == []


def test_a_duplicate_that_an_open_update_targets_is_never_hidden(_on):
    """WO-4's other half: the publish gate refuses the staged row (a seed row appeared), but an open
    update proposal targets that very row — it is NOT hidden (withdrawn), and its line goes out."""
    w = World()
    w.notifier.fail.add("auto")
    w.file_13g(acc(901))
    w.run()
    w.file_13g(acc(902), filed="2026-10-28", event="10/27/2026", pct="10.5", form="SCHEDULE 13G/A")
    w.run(LATER)
    upd = w.finding(acc(902))
    assert upd["status"] == C.STATUS_PROPOSED
    _seed_acme(w)
    w.notifier.fail.clear()
    w.run(LATER + timedelta(hours=1))
    a = w.finding(acc(901))
    [auto] = [s for s in w.stakes() if s["origin"] in C.MACHINE_ORIGINS]
    assert (a["outcome"], a["reason"], a["status"]) == (C.OUTCOME_HEADS_UP, "existing_row", C.STATUS_REPORTED)
    assert auto["withdrawn_at"] is None and auto["published"] is False, "an open update's target is never hidden"
    tap = w.tap(C.VERB_PUBLISH, upd["id"], upd["telegram_message_id"], now=LATER + timedelta(hours=2))
    assert tap.ok is False and "a row for this company is on file" in tap.toast, tap


def test_monday_still_bumps_a_commitment_whose_issuer_is_listed(_on, monkeypatch):
    """Honest twin of facts-F4: the "now listed" check is for PRIVATE rows only — a commitment row
    never carries a symbol, and most commitment investees are listed."""
    w = World(configs=S2_ONLY)
    signing_acc = "0000050863-25-000155"
    w.sec.set_document(signing_acc, "intc-20250915.htm",
                       (B.FIX / "html" / "intel_signing_8k.htm").read_text(encoding="utf-8"))
    w.sec.add_hit({"adsh": signing_acc, "ciks": ["0000050863"], "display_names": ["INTEL CORP  (INTC)  (CIK 0000050863)"],
                   "file_date": "2025-09-18", "file_type": "8-K", "form": "8-K", "items": ["3.02", "7.01", "9.01"],
                   "root_forms": ["8-K"], "sequence": 1}, "intc-20250915.htm")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_LOOKBACK_DAYS", 120)
    w.run(datetime(2025, 9, 18, 22, 30, tzinfo=timezone.utc))
    t2 = w.finding(signing_acc)
    assert w.tap(C.VERB_PUBLISH, t2["id"], t2["telegram_message_id"],
                 now=datetime(2025, 9, 19, 14, 0, tzinfo=timezone.utc)).ok
    assert (w.stake()["kind"], w.stake()["investee_us_symbol"]) == ("commitment", None)
    w.run(datetime(2025, 9, 22, 23, 30, tzinfo=timezone.utc))      # Monday
    assert w.stake()["verified_on"] == "2025-09-22"


def test_an_older_exit_read_late_never_stops_a_newer_row_still_on_its_way(_on):
    """Honest twin of the hold: the exit (Oct 20) is read late, while a NEWER own 13G (Oct 26) is
    still waiting for its note. The exit is about what was held before it: skipped (dated on the
    ledger), and the newer row still goes live."""
    w = World()
    w.file_13g(acc(902), filed="2026-10-22", event="10/20/2026", pct="3.0", form="SCHEDULE 13G/A")
    w.file_13g(acc(903), filed="2026-10-27", event="10/26/2026", pct="9.4")
    w.sec.fail["get_filing_document"] = [SECUnavailableException("503")]   # the exit's XML, once
    w.notifier.fail.add("auto")
    w.run()
    assert (w.finding(acc(902))["status"], w.finding(acc(903))["status"]) == (C.STATUS_ERROR, C.STATUS_STAGED)
    w.run(NOW + timedelta(days=1))                                   # notes still refused; the exit is read
    ex = w.finding(acc(902))
    assert (ex["outcome"], ex["reason"]) == (C.OUTCOME_SKIPPED, "exit_filing")
    assert "older than the row on file" in ex["proposed_row"]["_detail"]
    assert w.finding(acc(903))["status"] == C.STATUS_STAGED, "never held back by an older exit"
    w.notifier.fail.clear()
    w.run(NOW + timedelta(days=2))
    assert w.finding(acc(903))["status"] == C.STATUS_PUBLISHED and w.stake()["published"] is True
