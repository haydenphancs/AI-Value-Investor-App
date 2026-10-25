"""`scripts/learn_weekly_preview.py` — the owner's offline look at a real draft. What it must
guarantee: it can never reach the database (every `get_supabase` binding is replaced before any
step runs), it writes only into its output directory, and it stops honestly when there is no
peg fact. Every network collaborator is stubbed."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.services.learn_weekly import build, contracts as C, ledger, sec_facts, signals, topic, writer

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "learn_weekly_preview.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("learn_weekly_preview_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def restore_db_bindings():
    """`block_database` rebinds `get_supabase` process-wide; put every binding back afterwards so
    the rest of the suite keeps its own stubs."""
    saved = {name: m.get_supabase for name, m in list(sys.modules.items())
             if name.startswith("app") and getattr(m, "get_supabase", None) is not None}
    yield
    for name, fn in saved.items():
        module = sys.modules.get(name)
        if module is not None:
            module.get_supabase = fn


SUMMARY = sec_facts.CompanySummary(
    name="CoreWeave, Inc.", cik="0001769628", tickers=("CRWV",), exchanges=("Nasdaq",), sic="7374",
    sic_description="Services-Computer Processing", foreign_filer=False, recent_8k=())

PEG = C.Fact(id="P1", text="CoreWeave filed a report describing a new master services agreement with a customer.",
             origin=C.ORIGIN_SEC_PEG, url="https://www.sec.gov/Archives/edgar/data/1769628/x-index.htm",
             accession="0001769628-26-000001")

SHEET = C.FactSheet(kind=C.KIND_DEEP_DIVE, topic_title="AI infrastructure", theme_slug="silicon-rush",
                    subjects=(C.Subject("CoreWeave, Inc.", "0001769628", "CRWV"),), facts=(PEG,))


@pytest.fixture
def stubs(monkeypatch):
    calls = {"write": []}

    async def cik_map(*, sec=None):
        return {"CRWV": "0001769628"}

    async def summary(cik, *, client=None):
        return SUMMARY

    async def pegs(run, subjects, today, *, cand_key):
        return [PEG]

    async def sheet(kind, candidate, *, library=None):
        assert candidate["peg_facts"][0]["origin"] == C.ORIGIN_SEC_PEG
        return SHEET

    async def write(**kw):
        calls["write"].append(kw)
        assert await kw["before_call"]() is True
        return writer.WriterOutcome(status="accepted", content={
            "title": "How the AI cloud is built", "subtitle": "s", "keyHighlights": [],
            "statistics": [], "sections": [{"title": "Why now", "content": [
                {"type": "paragraph", "text": "A paragraph."},
                {"type": "bulletList", "items": ["one", "two"]}]}]},
            tokens_used=1234, calls=9)

    monkeypatch.setattr(signals, "ticker_cik_map", cik_map)
    monkeypatch.setattr(sec_facts, "company_summary", summary)
    monkeypatch.setattr(topic, "_peg_facts", pegs)
    monkeypatch.setattr(build, "assemble_fact_sheet", sheet)
    monkeypatch.setattr(writer, "write_article", write)
    return calls


def _args(script, *extra):
    return script.parse_args(["--tickers", "CRWV", "--theme", "silicon-rush", "--label",
                              "AI infrastructure", "--as-of", "2026-10-22", *extra])


def test_the_preview_blocks_the_database_and_writes_only_its_out_dir(tmp_path, stubs,
                                                                    restore_db_bindings):
    script = _load_script()
    report = asyncio.run(script.preview(_args(script), gemini=object(), sec=object(), out_dir=tmp_path))
    assert report["status"] == "accepted" and report["tokens_used"] == 1234
    assert sorted(p.name for p in tmp_path.iterdir()) == ["draft.json", "draft.txt", "report.json",
                                                         "sheet.json"]
    assert "## Why now" in (tmp_path / "draft.txt").read_text() and "  - two" in (tmp_path / "draft.txt").read_text()
    assert json.loads((tmp_path / "sheet.json").read_text())["facts"][0]["id"] == "P1"
    # The ledger's own module-level binding is blocked, not just app.database's.
    with pytest.raises(script.PreviewDatabaseBlocked):
        ledger.get_supabase()
    kw = stubs["write"][0]
    assert kw["in_flight_titles"] == [] and len(kw["served_titles"]) == 23


def test_no_peg_fact_stops_the_preview_honestly(tmp_path, stubs, monkeypatch, restore_db_bindings):
    async def none(run, subjects, today, *, cand_key):
        run.drops[cand_key] = "no_8k_this_week"
        return []

    monkeypatch.setattr(topic, "_peg_facts", none)
    script = _load_script()
    with pytest.raises(SystemExit) as e:
        asyncio.run(script.preview(_args(script), gemini=object(), sec=object(), out_dir=tmp_path))
    assert "no verified peg fact" in str(e.value) and "no_8k_this_week" in str(e.value)
    assert list(tmp_path.iterdir()) == [], "nothing written when there is nothing to write"
    assert stubs["write"] == []


def test_a_foreign_filer_or_unknown_ticker_is_never_a_main_subject(tmp_path, stubs, monkeypatch,
                                                                  restore_db_bindings, capsys):
    async def foreign(cik, *, client=None):
        return SUMMARY._replace(foreign_filer=True) if hasattr(SUMMARY, "_replace") else \
            sec_facts.CompanySummary(**{**SUMMARY.__dict__, "foreign_filer": True})

    monkeypatch.setattr(sec_facts, "company_summary", foreign)
    script = _load_script()
    with pytest.raises(SystemExit) as e:
        asyncio.run(script.preview(_args(script), gemini=object(), sec=object(), out_dir=tmp_path))
    assert "no eligible main subject" in str(e.value)
    assert "foreign filer" in capsys.readouterr().out


def test_a_lone_company_the_build_would_refuse_stops_with_a_plain_reason(tmp_path, stubs, monkeypatch,
                                                                          restore_db_bindings):
    """Owner rule 2026-10-24: a lone company is written only with its verified company primer. The
    build passes such a candidate over (FactSheetShortfall); the preview says so, never a traceback,
    and writes nothing."""
    script = _load_script()

    async def refused(kind, candidate, *, library=None):
        raise build.FactSheetShortfall("single-company topic 'CRWV': CoreWeave, Inc. has no verified "
                                       "company entry in the case library", usable=0)

    monkeypatch.setattr(build, "assemble_fact_sheet", refused)
    args = script.parse_args(["--tickers", "CRWV", "--label", "CoreWeave", "--as-of", "2026-10-22"])
    with pytest.raises(SystemExit, match="would pass this candidate over: single-company topic"):
        asyncio.run(script.preview(args, gemini=object(), sec=object(), out_dir=tmp_path))
    assert list(tmp_path.iterdir()) == [] and stubs["write"] == []


def test_several_tickers_without_a_theme_is_refused_before_any_read(tmp_path, stubs, restore_db_bindings):
    """The engine has no multi-company topic; previewing one would test a shape production never
    builds (review 2026-10-24)."""
    script = _load_script()
    args = script.parse_args(["--tickers", "MMM,NVDA", "--label", "x", "--as-of", "2026-10-22"])
    with pytest.raises(SystemExit, match="without --theme a topic is one company"):
        asyncio.run(script.preview(args, gemini=object(), sec=object(), out_dir=tmp_path))
    assert list(tmp_path.iterdir()) == [] and stubs["write"] == []


def test_one_unprimed_ticker_is_refused_before_any_8k_read_or_model_call(tmp_path, stubs, monkeypatch,
                                                                         restore_db_bindings):
    """Review round 2: the refusal is certain once the subject is known, so no 8-K read, peg-screen
    or label call is spent on it (the build would refuse the same candidate)."""
    from app.services.learn_weekly import case_library

    spent = []

    async def pegs(run, subjects, today, *, cand_key):
        spent.append("pegs")
        return [PEG]

    monkeypatch.setattr(topic, "_peg_facts", pegs)
    monkeypatch.setattr(case_library, "get_case_library", lambda path=None: case_library.CaseLibrary(1, ()))
    script = _load_script()
    args = script.parse_args(["--tickers", "CRWV", "--as-of", "2026-10-22"])     # no --label: no model call
    with pytest.raises(SystemExit, match="would pass this candidate over: single-company topic"):
        asyncio.run(script.preview(args, gemini=object(), sec=object(), out_dir=tmp_path))
    assert spent == [] and stubs["write"] == [] and list(tmp_path.iterdir()) == []
