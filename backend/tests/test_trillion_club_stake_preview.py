"""The stake finder's read-only preview (`scripts/preview_trillion_club_stakes.py`).

Hermetic: the classification is INJECTED (`classify=`) or a fake `stake_finder` module in `sys.modules`; the
SEC client is a fake object nobody calls a socket on (conftest blocks sockets anyway).

What must never regress:
  * the Supabase tripwire is installed FIRST — before the arguments are even read — and the Telegram stubs
    right after; both are in force while the finder runs; the stubs are called 0 times on a clean run, a call
    (even a swallowed one) fails the preview, and the real functions are back afterwards;
  * there is no write flag at all, and the source makes no table write and no `.rpc()` (the 13F preview's
    `_db_write_calls` scan) and imports no store;
  * the inputs: the seed's companies (published `auto` ones marked members), its stakes as origin 'seed'
    with stable unique ids, the reviewed member file (filtered by `--slug`); `--since` defaults to 30 days
    before today ET and is bounded;
  * the output: one block per finding grouped auto → proposal → heads-up → skipped (→ other), then the
    counts; the SEC User-Agent is never printed.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

import app.database as database
import scripts.preview_trillion_club as OLD
import scripts.preview_trillion_club_stakes as P
from app.config import settings
from app.integrations import sec_edgar, telegram
from app.services.trillion_club.stake_contracts import REASONS

BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND / "scripts" / "preview_trillion_club_stakes.py"
UTC = timezone.utc
NOW = datetime(2026, 10, 28, 22, 0, tzinfo=UTC)            # 18:00 ET
TODAY_ET = date(2026, 10, 28)
FINDER_MODULE = "app.services.trillion_club.stake_finder"
URL = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000123/xslSCHEDULE_13G_X01/primary_doc.xml"
UA = "Caydex Research ops-desk@example.com"


@pytest.fixture(autouse=True)
def _restored(monkeypatch):
    """`main` installs process-wide guards; every test leaves the process exactly as it found it."""
    monkeypatch.setattr(database, "_supabase_client", database._supabase_client)
    for name in P.TELEGRAM_FUNCTIONS:
        monkeypatch.setattr(telegram, name, getattr(telegram, name))
    monkeypatch.setattr(P, "TELEGRAM_STUB_CALLS", [])


class FakeSEC:
    """The SEC client stand-in: any attribute use is recorded and refused (the injected classify never
    needs one)."""

    def __init__(self) -> None:
        self.used: List[str] = []

    def __getattr__(self, name: str) -> Any:
        self.used.append(name)
        raise AssertionError(f"the preview itself called sec.{name}")


ROW = {"company_slug": "nvidia", "kind": "us_listed_off_13f", "investee_name": "Nebius Group N.V.",
       "investee_cusip": "N97284108", "investee_us_symbol": "NBIS", "local_listing": None, "ownership_pct": 9.9,
       "ownership_basis": "of Class A", "disclosed_value_usd": None, "value_basis": None, "as_of": "2026-09-30",
       "source_title": "NVIDIA Schedule 13G (Oct 1, 2026)", "source_url": URL, "source_confidence": "primary",
       "material": False, "tied_to_deal": False, "listed_since": None, "background": None,
       "verified_on": "2026-10-28", "published": False, "sort_order": 900}


def fnd(outcome: str, **kw: Any) -> Dict[str, Any]:
    out = {"slug": "nvidia", "source": "sch13", "accession": "0001045810-26-000123", "form": "SCHEDULE 13G",
           "filed_on": "2026-10-01", "document": "primary_doc.xml", "source_url": URL,
           "investee_cik": "0001513845", "tier": "13dg", "outcome": outcome, "reason": "plain_13dg",
           "row": dict(ROW), "spans": ["NVIDIA Corporation beneficially owns 9.9% of the Class A shares."],
           "detail": None, "trial": False}
    out.update(kw)
    return out


_EMPTY = object()


class Recorder:
    """A `classify` that records what it was given and checks the guards are in force while it runs."""

    def __init__(self, findings: Any = _EMPTY, *, also: Any = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.findings = [] if findings is _EMPTY else findings
        self.also = also
        self.guards: Dict[str, bool] = {}

    async def __call__(self, **kw: Any) -> Any:
        self.calls.append(kw)
        self.guards["tripwire"] = type(database._supabase_client).__name__ == "_SupabaseTripwire"
        try:
            database.get_supabase().table("trillion_club_stakes")
            self.guards["supabase_refused"] = False
        except OLD.SupabaseRefused:
            self.guards["supabase_refused"] = True
        self.guards["telegram_stubbed"] = all(getattr(getattr(telegram, n), "stake_preview_stub", False)
                                              for n in P.TELEGRAM_FUNCTIONS)
        if self.also is not None:
            await self.also()
        if isinstance(self.findings, BaseException):
            raise self.findings
        return self.findings


def run_main(argv: List[str], **kw: Any) -> int:
    kw.setdefault("sec", FakeSEC())
    kw.setdefault("now", NOW)
    return P.main(argv, **kw)


# ── the read-only guarantees ──────────────────────────────────────────────────────────────────


def test_the_tripwire_goes_in_first_then_the_telegram_stubs_and_both_hold_while_the_finder_runs(monkeypatch,
                                                                                               capsys):
    order: List[str] = []
    real_tripwire, real_stubs, real_parse = P.install_supabase_tripwire, P.install_telegram_stubs, P.parse_args
    monkeypatch.setattr(P, "install_supabase_tripwire", lambda: (order.append("tripwire"), real_tripwire())[1])
    monkeypatch.setattr(P, "install_telegram_stubs", lambda: (order.append("telegram"), real_stubs())[1])
    monkeypatch.setattr(P, "parse_args", lambda argv=None: (order.append("parse"), real_parse(argv))[1])
    classify = Recorder([fnd("auto")])
    assert run_main([], classify=classify) == 0
    assert order == ["tripwire", "telegram", "parse"]
    assert classify.guards == {"tripwire": True, "supabase_refused": True, "telegram_stubbed": True}
    assert P.TELEGRAM_STUB_CALLS == []                          # called 0 times
    for name in P.TELEGRAM_FUNCTIONS:                           # and the real functions are back
        assert not getattr(getattr(telegram, name), "stake_preview_stub", False), name


def test_a_telegram_call_even_a_swallowed_one_fails_the_preview(capsys):
    async def sneaky_send() -> None:
        try:
            await telegram.send_message(424242, "hello")
        except Exception:  # noqa: BLE001 — a caller that swallows the refusal
            pass
    assert run_main([], classify=Recorder([], also=sneaky_send)) == 1
    out = capsys.readouterr().out
    assert P.TELEGRAM_STUB_CALLS == ["send_message"]
    assert "Telegram was called 1 time(s) (send_message) and refused" in out


def test_the_request_function_itself_is_refused_so_a_bound_send_cannot_leak(monkeypatch):
    bound_early = telegram.send_message                         # a `from … import send_message` made before
    saved = P.install_telegram_stubs()
    try:
        with pytest.raises(P.TelegramRefused):
            asyncio.run(bound_early(424242, "x"))               # the real function → the stubbed `_call`
    finally:
        P.restore_telegram(saved)
    assert P.TELEGRAM_STUB_CALLS == ["_call"]
    assert set(saved) == set(P.TELEGRAM_FUNCTIONS)              # every name exists on the integration


def test_bad_arguments_still_leave_telegram_restored():
    with pytest.raises(SystemExit):
        run_main(["--since", "yesterday"])
    for name in P.TELEGRAM_FUNCTIONS:
        assert not getattr(getattr(telegram, name), "stake_preview_stub", False), name


@pytest.mark.parametrize("flag", ["--apply", "--write", "--update", "--json", "--send"])
def test_there_is_no_write_flag(flag, capsys):
    with pytest.raises(SystemExit):
        P.parse_args([flag])


@pytest.mark.parametrize("argv", [["--sh"], ["--show"], ["--sl", "nvidia"], ["--sin", "2026-09-24"]])
def test_an_abbreviated_flag_is_an_error_never_the_option_it_prefixes(argv, capsys):
    with pytest.raises(SystemExit) as info:
        P.parse_args(argv)
    assert info.value.code == 2


def test_the_source_never_writes_and_never_imports_a_store():
    from test_trillion_club_jobs import _db_write_calls   # the 13F preview's scan, reused

    src = SRC.read_text()
    assert _db_write_calls(src) == []
    assert "--apply" not in src and "--write" not in src
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in ("app.services.trillion_club.stake_store", "app.services.trillion_club.store")
            assert not (node.module == "app.database" and any(a.name == "get_supabase" for a in node.names))
            if node.module == "app.services.trillion_club.stake_finder":
                assert {a.name for a in node.names} == {"classify_only"}
            if node.module == "app.services.trillion_club.stake_review":
                assert {a.name for a in node.names} <= {"auto_note_text", "proposal_text"}   # pure text only


def test_the_sec_client_is_passed_through_and_never_built_when_injected(monkeypatch, capsys):
    def refuse() -> Any:
        raise AssertionError("the default SEC client was built")
    monkeypatch.setattr(sec_edgar, "get_sec_edgar_client", refuse)
    sec = FakeSEC()
    classify = Recorder([])
    assert run_main([], classify=classify, sec=sec) == 0
    assert classify.calls[0]["sec"] is sec and sec.used == []


def test_without_a_user_agent_the_default_client_is_never_built(monkeypatch, capsys):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "")

    def refuse() -> Any:
        raise AssertionError("the default SEC client was built")
    monkeypatch.setattr(sec_edgar, "get_sec_edgar_client", refuse)
    classify = Recorder([])
    assert P.main([], classify=classify, now=NOW) == 2
    assert classify.calls == [] and "SEC_EDGAR_USER_AGENT is not set" in capsys.readouterr().out


def test_the_default_client_is_closed_after_the_run(monkeypatch, capsys):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", UA)
    closed: List[bool] = []

    async def close() -> None:
        closed.append(True)
    monkeypatch.setattr(sec_edgar, "close_sec_edgar_client", close)
    classify = Recorder(RuntimeError("SEC went away"))
    assert P.main([], classify=classify, now=NOW) == 1
    assert closed == [True] and isinstance(classify.calls[0]["sec"], sec_edgar.SECEdgarClient)


def test_the_user_agent_is_never_printed(monkeypatch, capsys):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", UA)
    classify = Recorder(RuntimeError(f"403 for User-Agent {UA}"))
    assert run_main([], classify=classify) == 1
    out = capsys.readouterr().out
    assert "classification FAILED — RuntimeError" in out and UA not in out and "ops-desk@example.com" not in out


def test_a_traceback_on_stderr_never_carries_the_user_agent(monkeypatch, capsys):
    """Review A PV-1: the CLI's stderr handler scrubs the UA (its _KeyScrubFilter). The classification
    error is logged with exc_info, so a chained raw cause that echoes the header reaches stderr."""
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", UA)
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    try:
        P._configure_logging()
        try:
            raise ValueError(f"raw transport error: User-Agent: {UA}")
        except ValueError as cause:
            boom = RuntimeError("SEC EDGAR: request failed")
            boom.__cause__ = cause
        assert run_main([], classify=Recorder(boom)) == 1
        err = capsys.readouterr().err
        assert "Traceback" in err and "raw transport error" in err, err[-500:]
        assert UA not in err and "ops-desk@example.com" not in err
    finally:
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])


@pytest.mark.parametrize("tiers, since, why", [
    ("", "", "tier_off"),                       # the shipped switches: nothing goes live on its own
    ("13dg", "", "before_auto_since"),
    ("13dg", "2026-10-15", "before_auto_since"),  # the filing (2026-10-01) predates AUTO_SINCE
    ("sec_closing", "2026-01-01", "tier_off"),   # a deferred tier is ignored
])
def test_gate_10_turns_an_auto_eligible_find_into_the_proposal_the_run_would_send(monkeypatch, capsys,
                                                                                 tiers, since, why):
    """Review A PV-3: classify_only stops at gate 7; the preview applies gate 10 with today's switches
    (stake_names.auto_tier_gate, the run's own function) and shows them in the header."""
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_TIERS", tiers)
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_SINCE", since)
    assert run_main(["--show-messages"], classify=Recorder([fnd("auto")])) == 0
    out = capsys.readouterr().out
    assert f"━━ {P.OUTCOME_TITLES['auto']} (0)" in out and f"━━ {P.OUTCOME_TITLES['proposal']} (1)" in out
    assert f"{why} — {REASONS[why]}" in out and "the daily run sends a proposal" in out
    assert "auto-publish switches: TRILLION_CLUB_STAKE_AUTO_TIERS=" in out
    assert "going live automatically" not in out and "Publish" in out


def test_with_both_switches_on_an_auto_eligible_find_stays_auto(monkeypatch, capsys):
    _switches_on(monkeypatch)
    assert run_main([], classify=Recorder([fnd("auto")])) == 0
    out = capsys.readouterr().out
    assert f"━━ {P.OUTCOME_TITLES['auto']} (1)" in out
    assert "TRILLION_CLUB_STAKE_AUTO_TIERS=13dg · TRILLION_CLUB_STAKE_AUTO_SINCE=2026-01-01" in out
    assert "nothing goes live on its own" not in out


# ── the inputs ────────────────────────────────────────────────────────────────────────────────


def test_the_finder_gets_the_seed_members_its_stakes_and_the_reviewed_member_file(capsys):
    classify = Recorder([])
    assert run_main(["--since", "2026-09-24"], classify=classify) == 0
    (call,) = classify.calls
    assert call["now"] == NOW and call["since"] == date(2026, 9, 24) and call["holdings"] is None
    seed = json.loads((BACKEND / "data" / "trillion_club_seed.json").read_text())
    companies = {c["slug"]: c for c in call["companies"]}
    assert set(companies) == {c["slug"] for c in seed["companies"]}
    assert companies["nvidia"]["is_member"] is True                     # published, auto
    assert companies["samsung"].get("is_member") is None                # force_in decides by itself
    assert companies["sk-hynix"].get("is_member") is None                # unpublished force_out stays out
    assert companies["walmart"].get("is_member") is None                 # unpublished auto stays out
    stakes = call["stakes"]
    assert len(stakes) == len(seed["stakes"]) and {s["origin"] for s in stakes} == {"seed"}
    assert len({s["id"] for s in stakes}) == len(stakes)                # stable, unique synthetic ids
    assert all(s["finding_id"] is None and s["withdrawn_at"] is None for s in stakes)
    filers = json.loads((BACKEND / "data" / "trillion_club_filers.json").read_text())
    assert set(call["member_configs"]) == {m["slug"] for m in filers["members"]}
    assert call["member_configs"]["alphabet"].filer_ciks == ("0001652044", "0001824723")


def test_the_seed_stake_ids_are_stable_across_runs():
    rows = [{"company_slug": "nvidia", "investee_name": "Nebius", "kind": "private"}]
    assert P.preview_stakes(rows)[0]["id"] == P.preview_stakes(rows)[0]["id"]
    assert P.preview_stakes([{**rows[0], "id": "keep-me"}])[0]["id"] == "keep-me"


def test_slug_limits_the_members_and_an_unknown_slug_stops(capsys):
    classify = Recorder([])
    assert run_main(["--slug", "nvidia", "--slug", "amd"], classify=classify) == 0
    assert set(classify.calls[0]["member_configs"]) == {"nvidia", "amd"}
    assert run_main(["--slug", "nope"], classify=classify) == 2
    assert len(classify.calls) == 1 and "not in trillion_club_filers.json: nope" in capsys.readouterr().out


def test_since_defaults_to_30_days_before_today_et_and_is_bounded(capsys):
    classify = Recorder([])
    late_evening = datetime(2026, 10, 29, 3, 30, tzinfo=UTC)        # still Oct 28 in New York
    assert run_main([], classify=classify, now=late_evening) == 0
    assert classify.calls[0]["since"] == TODAY_ET - timedelta(days=30)
    assert run_main(["--since", "2026-10-29"], classify=classify) == 2                # after today ET
    assert run_main(["--since", (TODAY_ET - timedelta(days=367)).isoformat()], classify=classify) == 2
    assert run_main(["--since", (TODAY_ET - timedelta(days=366)).isoformat()], classify=classify) == 0
    assert run_main(["--since", TODAY_ET.isoformat()], classify=classify) == 0
    assert len(classify.calls) == 3


@pytest.mark.parametrize("junk", ["2026-9-24", "2026-02-30", "20260924", "2026-09-24\n", "", "x"])
def test_a_junk_since_is_refused_by_the_parser(junk, capsys):
    with pytest.raises(SystemExit) as info:
        P.parse_args(["--since", junk])
    assert info.value.code == 2


def test_a_naive_now_is_refused(capsys):
    assert run_main([], classify=Recorder([]), now=datetime(2026, 10, 28, 18, 0)) == 2


def test_a_bad_member_file_stops_before_any_classification(monkeypatch, tmp_path, capsys):
    bad = tmp_path / "trillion_club_filers.json"
    bad.write_text(json.dumps({"version": 1, "members": [{"slug": "nvidia", "source_name": "NVIDIA",
                                                         "names": ["NVIDIA"], "s1_filers": [],
                                                         "s2_aliases": ["NVIDIA"]}]}))
    monkeypatch.setattr(P, "FILERS_PATH", bad)
    classify = Recorder([])
    assert run_main([], classify=classify) == 2
    assert classify.calls == [] and "could not be used — ValueError" in capsys.readouterr().out
    monkeypatch.setattr(P, "FILERS_PATH", tmp_path / "missing.json")
    assert run_main([], classify=classify) == 2 and classify.calls == []


def test_a_seed_with_no_companies_stops(monkeypatch, tmp_path, capsys):
    bad = tmp_path / "seed.json"
    bad.write_text(json.dumps({"companies": [], "stakes": []}))
    monkeypatch.setattr(P, "SEED_PATH", bad)
    classify = Recorder([])
    assert run_main([], classify=classify) == 2 and classify.calls == []


def test_the_default_classification_is_the_finders_classify_only(monkeypatch, capsys):
    classify = Recorder([fnd("proposal", reason="derivative_position", tier="instrument")])
    monkeypatch.setitem(sys.modules, FINDER_MODULE, types.SimpleNamespace(classify_only=classify))
    assert run_main([], classify=None) == 0
    assert len(classify.calls) == 1 and "PROPOSAL" in capsys.readouterr().out


def test_without_the_finder_the_preview_says_so_and_stops(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, FINDER_MODULE, None)
    assert run_main([], classify=None) == 2
    assert "the stake finder could not be loaded" in capsys.readouterr().out


# ── the output ────────────────────────────────────────────────────────────────────────────────


def _switches_on(monkeypatch, *, since: str = "2026-01-01") -> None:
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_TIERS", "13dg")
    monkeypatch.setattr(settings, "TRILLION_CLUB_STAKE_AUTO_SINCE", since)


def test_findings_are_printed_grouped_by_outcome_then_counted(capsys, monkeypatch):
    _switches_on(monkeypatch)
    findings = [
        fnd("skipped", reason="no_template", source="efts", form="8-K", accession="0000000001-26-000001",
            row=None),
        fnd("heads_up", reason="t3_indication", tier="ioi", row=None, form="424B5"),
        fnd("proposal", reason="t1_closing", tier="sec_closing", trial=True, form="8-K"),
        fnd("auto"),
        fnd("skipped", reason="about_member", row=None),
        fnd("weird"),
        "not a finding",
    ]
    assert run_main([], classify=Recorder(findings)) == 0
    out = capsys.readouterr().out
    # A LITERAL order: the module's own OUTCOME_ORDER would make this check agree with any reordering of it.
    heads = [out.index(f"━━ {P.OUTCOME_TITLES[o]}") for o in ("auto", "proposal", "heads_up", "skipped")]
    assert heads == sorted(heads) and out.index("━━ OTHER") > heads[-1]  # auto → proposal → heads-up → skipped
    assert f"━━ {P.OUTCOME_TITLES['auto']} (1)" in out and f"━━ {P.OUTCOME_TITLES['skipped']} (2)" in out
    assert "━━ OTHER — not a known outcome (2)" in out and "(malformed finding: str)" in out
    assert out.rstrip().endswith("Counts: auto 1 · proposal 1 · heads-up 1 · skipped 2 · other 2 — 7 finding(s)")
    assert f"plain_13dg — {REASONS['plain_13dg']}" in out
    assert "TRIAL (would auto-publish)" in out
    assert "row: Nebius Group N.V. · us_listed_off_13f · NBIS · CUSIP N97284108 · 9.9% of Class A · as of " \
           "2026-09-30" in out
    assert URL in out and "evidence: “NVIDIA Corporation beneficially owns 9.9% of the Class A shares.”" in out
    skipped_line = next(line for line in out.split("\n") if "0000000001-26-000001" in line)
    assert skipped_line.strip().startswith("• nvidia · efts · 8-K") and "no_template" in skipped_line


def test_show_messages_prints_the_telegram_text_of_auto_notes_and_proposals_only(capsys, monkeypatch):
    _switches_on(monkeypatch)
    findings = [fnd("auto"), fnd("proposal", reason="t1_closing", trial=True),
                fnd("heads_up", reason="t3_indication", row=None)]
    assert run_main(["--show-messages"], classify=Recorder(findings)) == 0
    out = capsys.readouterr().out
    assert out.count("┌ Telegram message:") == 2
    assert "│ 🟢 Trillion Club stake — going live automatically" in out
    assert "│ 🟡 Trillion Club stake — proposal (Publish or Dismiss)" in out
    assert "│ NVIDIA → Nebius Group N.V." in out                     # the member's display name from the seed
    assert P.TELEGRAM_STUB_CALLS == []                              # rendering never sends


def test_hostile_finding_values_print_as_one_clean_line(capsys):
    hostile = fnd("proposal", accession="0001045810-26-000123\n━━ AUTO (99)", detail="a\x00b‮c",
                  row={**ROW, "investee_name": "Acme\nCounts: auto 99"})
    assert run_main([], classify=Recorder([hostile])) == 0
    out = capsys.readouterr().out
    lines = out.split("\n")
    assert not [line for line in lines if line.startswith(("━━ AUTO (99)", "Counts: auto 99"))]
    assert "0001045810-26-000123 ━━ AUTO (99)" in out                  # folded onto its own line, shown
    assert "\x00" not in out and "‮" not in out


@pytest.mark.parametrize("returned", [None, {"outcome": "auto"}, "findings", 7])
def test_a_finder_answer_that_is_not_a_list_is_a_failure(returned, capsys):
    assert run_main([], classify=Recorder(returned)) == 1
    assert "not a list of findings" in capsys.readouterr().out


def test_an_empty_preview_prints_every_group_and_zero_counts(capsys):
    assert run_main([], classify=Recorder([])) == 0
    out = capsys.readouterr().out
    assert all(f"━━ {P.OUTCOME_TITLES[o]} (0)" in out for o in P.OUTCOME_ORDER)
    assert "Counts: auto 0 · proposal 0 · heads-up 0 · skipped 0 · other 0 — 0 finding(s)" in out
    assert "READ-ONLY" in out


class _Partial(list):
    """What classify_only answers when part of it did not finish (`stake_finder.ClassifyResult`)."""

    def __init__(self, items, problems):
        super().__init__(items)
        self.problems = list(problems)


def test_an_incomplete_classification_prints_what_it_has_then_says_incomplete_and_exits_1(capsys):
    """Review B S2-SECEXCEPTION (B2): a failed query no longer loses everything already classified —
    the preview prints those findings, then an INCOMPLETE line naming what did not finish, exit 1."""
    partial = _Partial([fnd("auto")], ["s2: nvidia: full-text search for 'NVIDIA Corporation' failed "
                                       "(SECUnavailableException: HTTP 503) — the next run retries"])
    assert run_main([], classify=Recorder(partial)) == 1
    out = capsys.readouterr().out
    assert "— 1 finding(s)" in out, "what was classified is printed"
    tail = out[out.index("Counts:"):]
    assert "preview: INCOMPLETE — 1 part(s) did not finish; the findings above are partial:" in tail
    assert "! s2: nvidia: full-text search for 'NVIDIA Corporation' failed" in tail
    assert P.TELEGRAM_STUB_CALLS == []


def test_a_complete_classification_result_exits_0(capsys):
    assert run_main([], classify=Recorder(_Partial([fnd("auto")], []))) == 0
    assert "INCOMPLETE" not in capsys.readouterr().out


def test_the_real_classify_only_answer_carries_its_problems_to_the_preview(capsys):
    """End to end with the REAL classify_only over the recorded fixtures: one EFTS 503 → INCOMPLETE, exit 1,
    and the members' own 13D/13G findings are still printed."""
    import test_trillion_club_stake_backtest as B
    from app.integrations.sec_edgar import SECUnavailableException

    sec = B.FakeSEC()
    sec.fail["full_text_search"] = [SECUnavailableException("HTTP 503 (after one retry)")]
    code = run_main(["--since", "2025-11-01"], sec=sec, now=datetime(2026, 10, 28, 22, 30, tzinfo=UTC))
    out = capsys.readouterr().out
    assert code == 1 and "preview: INCOMPLETE" in out and "full-text search for" in out
    assert "sch13 · SCHEDULE 13" in out
