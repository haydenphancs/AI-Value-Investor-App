"""Stage A's one re-ask, the early stop, and the Stage B outage line, on BOTH report doors.

The incident (GOOGL / warren_buffett, 2026-11-05): one Stage A answer with broken JSON ended a
20-credit report. Nothing asked twice, because `async_retry` only sees exceptions and a 200 with
bad JSON is not one. The run then spent 30 s and ~15 Gemini calls on a report already certain to
be refunded, and one incident opened three Sentry issues.

Behavioural on purpose: `test_degraded_never_cached.py` pins these paths by source text, which
said nothing about what a real bad answer does. Fakes are STRICT (no **kwargs): a swallowed
TypeError inside Stage A's `except` would otherwise read as "the model failed". No network.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import research_service as rs
from app.services import ticker_report_cache as trc
from app.services import ticker_report_service as trs
from app.services.agents import narrative_prompts as np_
from app.services.agents import research_agent as ra
from app.services.agents.persona_config import get_persona_config
from app.services.agents.research_agent import ResearchAgent
from app.services.agents.ticker_report_data_collector import (
    DEGRADED_SECTIONS_KEY,
    _sanitize_stage_a_factors,
    _sanitize_thesis,
    _stage_a_text,
    record_degraded_section,
    report_degraded_sections,
)
from app.services.report_degradation import (
    REPORT_DEGRADED_KEY,
    DegradedReportError,
    _degraded_reason,
    report_degraded_reason,
)
from app.services.ticker_report_service import TickerReportService

PERSONA = get_persona_config("warren_buffett")
DOORS = ["direct", "deep"]

# The incident's shape: a stray quote inside a string, on a clean STOP.
BROKEN = '{"macro_data": {"risk_factors": [{"description": "the "Other Bets" unit"}]}}'
GOOD = '{"quality_score": 71, "core_thesis": {"bull_case": ["a"], "bear_case": ["b"]}}'


class _Gemini:
    """Answers from a script. Strict: only what Stage A may send."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list = []

    async def generate_json(self, prompt=None, system_instruction=None, thinking_budget=None,
                            usage_tag=None, cache=True):
        self.calls.append({
            "prompt": prompt, "system_instruction": system_instruction,
            "thinking_budget": thinking_budget, "usage_tag": usage_tag, "cache": cache,
        })
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return {"text": answer, "finish_reason": "STOP"}


class _Out:
    ticker = "GOOGL"
    profile = {"companyName": "Alphabet Inc."}


async def _stage_a(door: str, gemini) -> dict:
    if door == "direct":
        svc = TickerReportService.__new__(TickerReportService)
        svc.gemini = gemini
        return await svc._generate_stage_a(_Out(), PERSONA, "EVIDENCE")
    agent = ResearchAgent.__new__(ResearchAgent)
    agent.gemini = gemini
    agent.persona = PERSONA
    return await agent._generate_stage_a(_Out(), "EVIDENCE", "FINDINGS")


def _errors(caplog) -> list:
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


# ── The re-ask ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_healthy_answer_is_asked_once(door, caplog):
    gem = _Gemini(GOOD)
    with caplog.at_level(logging.WARNING):
        shell = await _stage_a(door, gem)
    assert shell["quality_score"] == 71 and _degraded_reason(shell) is None
    assert len(gem.calls) == 1
    assert gem.calls[0]["cache"] is False, "a cached wrong-shape answer would waste this ask"
    assert gem.calls[0]["usage_tag"] == "report_stage_a:GOOGL:warren_buffett"
    assert "[stage-a-retry]" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_one_bad_answer_is_recovered_by_a_fresh_second_ask(door, caplog):
    """What the incident should have done."""
    gem = _Gemini(BROKEN, GOOD)
    with caplog.at_level(logging.WARNING):
        shell = await _stage_a(door, gem)
    assert shell["quality_score"] == 71 and _degraded_reason(shell) is None
    assert len(gem.calls) == 2
    first, second = gem.calls
    assert first["cache"] is False and second["cache"] is False
    assert first["prompt"] == second["prompt"]
    assert first["system_instruction"] == second["system_instruction"]
    assert first["thinking_budget"] == second["thinking_budget"]
    assert second["usage_tag"] == "report_stage_a_retry:GOOGL:warren_buffett"
    assert "[stage-a-retry]" in caplog.text and "Other Bets" in caplog.text
    assert not _errors(caplog), "a recovered answer must not page"


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_two_bad_answers_degrade_with_one_constant_error(door, caplog):
    gem = _Gemini(BROKEN, BROKEN)
    with caplog.at_level(logging.WARNING):
        shell = await _stage_a(door, gem)
    assert _degraded_reason(shell) == "stage_a_unparseable"
    assert len(gem.calls) == 2, "exactly one re-ask, never a loop"
    errors = _errors(caplog)
    assert len(errors) == 1
    assert errors[0].msg.startswith("[stage-a-unparseable]")
    assert "GOOGL" not in errors[0].msg, "a constant template: Sentry groups every ticker as one"
    assert "GOOGL" in errors[0].getMessage(), "and still greppable by ticker"
    assert "problem=unparseable" in errors[0].getMessage()


@pytest.mark.asyncio
async def test_the_final_error_names_the_last_answers_defect(caplog):
    gem = _Gemini('{"report": {}}', '{"quality_score": 71, "moat_competition": "strong"}')
    with caplog.at_level(logging.WARNING):
        shell = await np_.generate_stage_a_shell(gem, PERSONA, "GOOGL", "PROMPT")
    assert _degraded_reason(shell) == "stage_a_malformed"
    assert "problem=moat_competition_not_object" in _errors(caplog)[0].getMessage()


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("bad", ["", "   ", "null", "[1, 2]", "5", '"text"'])
async def test_blank_and_non_object_answers_take_the_same_path(door, bad):
    gem = _Gemini(bad, bad)
    assert _degraded_reason(await _stage_a(door, gem)) == "stage_a_unparseable"
    assert len(gem.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_fenced_good_answer_needs_no_re_ask(door):
    gem = _Gemini("```json\n" + GOOD + "\n```")
    assert (await _stage_a(door, gem))["quality_score"] == 71
    assert len(gem.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("bad", ["{}", '{"report": {"quality_score": 71}}'])
async def test_valid_json_with_no_stage_a_keys_is_malformed(door, bad):
    """`{}` carries no more than the fallback shell, which is refunded. It used to be billed
    and cached as a real report."""
    gem = _Gemini(bad, bad)
    assert _degraded_reason(await _stage_a(door, gem)) == "stage_a_malformed"
    assert len(gem.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_wrong_typed_section_is_malformed_not_a_crash(door):
    bad = '{"quality_score": 71, "macro_data": "high risk"}'
    assert _degraded_reason(await _stage_a(door, _Gemini(bad, bad))) == "stage_a_malformed"


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_the_reason_is_the_last_answers(door):
    gem = _Gemini("{}", BROKEN)
    assert _degraded_reason(await _stage_a(door, gem)) == "stage_a_unparseable"


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_wrong_shape_then_a_good_answer_recovers(door):
    gem = _Gemini("{}", GOOD)
    assert (await _stage_a(door, gem))["quality_score"] == 71


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_sparse_real_shell_is_accepted_and_counted(door, caplog):
    gem = _Gemini('{"quality_score": 71}')
    with caplog.at_level(logging.WARNING):
        shell = await _stage_a(door, gem)
    assert _degraded_reason(shell) is None and len(gem.calls) == 1
    assert "[stage-a-sparse]" in caplog.text and "moat_competition" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_raised_error_is_not_re_asked(door, caplog):
    """`async_retry` already spent its budget on it. A second call here would add load to a
    provider already known to be failing."""
    class GeminiQuotaError(Exception):
        pass

    gem = _Gemini(GeminiQuotaError("quota"))
    with caplog.at_level(logging.WARNING):
        shell = await _stage_a(door, gem)
    assert _degraded_reason(shell) == "stage_a_GeminiQuotaError"
    assert len(gem.calls) == 1
    assert len(_errors(caplog)) == 1 and _errors(caplog)[0].msg.startswith("[stage-a-failed]")


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_an_error_on_the_re_ask_takes_the_error_reason(door):
    class GeminiTimeoutError(Exception):
        pass

    gem = _Gemini(BROKEN, GeminiTimeoutError("slow"))
    assert _degraded_reason(await _stage_a(door, gem)) == "stage_a_GeminiTimeoutError"
    assert len(gem.calls) == 2


@pytest.mark.asyncio
async def test_a_real_client_re_ask_reaches_the_model(monkeypatch):
    """End to end through the real `generate_json`: the cached-broken-answer replay is what
    would have made the re-ask a no-op."""
    from app.integrations import gemini as gem

    class _Models:
        def __init__(self):
            self.calls = 0

        async def generate_content(self, *, model, contents, config):
            self.calls += 1
            return SimpleNamespace(
                text=BROKEN if self.calls == 1 else GOOD,
                usage_metadata=None,
                candidates=[SimpleNamespace(finish_reason="STOP")],
            )

    models = _Models()
    client = gem.GeminiClient.__new__(gem.GeminiClient)
    client.model_name = "gemini-2.5-flash"
    client._temperature, client._max_tokens = 0.7, 128
    client._client = SimpleNamespace(aio=SimpleNamespace(models=models))
    client._response_cache = gem._TTLCache(max_size=8, ttl_seconds=3600)
    gem._quota_circuit.record_success()

    shell = await np_.generate_stage_a_shell(client, PERSONA, "GOOGL", "PROMPT")
    assert shell["quality_score"] == 71
    assert models.calls == 2


# ── The doors, end to end ────────────────────────────────────────────────────

class _Collector:
    def __init__(self, thesis=None):
        self.assembled = 0
        self.thesis = thesis or {"bull_case": ["a"], "bear_case": ["b"]}

    async def collect(self, ticker, persona_key):
        return SimpleNamespace(ticker=ticker, profile={"companyName": "Alphabet Inc."})

    def assemble_report(self, out, shell):
        self.assembled += 1
        return {"symbol": out.ticker, "core_thesis": self.thesis}


def _wire(monkeypatch, door, *, shell=None, stage_b=None, thesis=None, schema_problem=None):
    """A door with every collaborator recorded. Returns (run, spy).

    `spy.upsert` exists on the direct door only: that door writes the cache itself. The deep
    door's write is in `generate_report`, guarded by the `_deep_service` tests below, so a
    stray assertion on it here raises rather than passing for nothing.
    """
    spy = SimpleNamespace(
        collector=_Collector(thesis), jobs=0,
        stage_b=AsyncMock(return_value=stage_b), thesis=AsyncMock(), factors=AsyncMock(),
        upsert=AsyncMock() if door == "direct" else None,
    )

    def _jobs(persona, evidence, report):
        spy.jobs += 1
        return []

    module = trs if door == "direct" else ra
    monkeypatch.setattr(module, "build_financial_context", lambda out: "EVIDENCE")
    monkeypatch.setattr(module, "build_narrative_jobs", _jobs)
    monkeypatch.setattr(module, "run_narrative_jobs", spy.stage_b)
    monkeypatch.setattr(module, "synthesize_core_thesis", spy.thesis)
    monkeypatch.setattr(module, "synthesize_critical_factors", spy.factors)
    if door == "direct":
        monkeypatch.setattr(trs, "upsert_cached_report", spy.upsert)
        monkeypatch.setattr(trs, "report_schema_problem", lambda report: schema_problem)
        svc = TickerReportService.__new__(TickerReportService)
        svc.collector = spy.collector
        svc.gemini = None
        monkeypatch.setattr(svc, "_generate_stage_a", AsyncMock(return_value=shell))

        async def run():
            return await svc._generate_uncontended("GOOGL", "warren_buffett")
    else:
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.persona = PERSONA
        agent.collector = spy.collector
        agent.gemini = agent.fmp = None
        agent.research_findings = ""
        monkeypatch.setattr(agent, "_agentic_research", AsyncMock(return_value="findings"))
        monkeypatch.setattr(agent, "_generate_stage_a", AsyncMock(return_value=shell))

        async def run():
            return await agent.run("GOOGL")
    return run, spy


def _nothing_after_stage_a_ran(spy, door):
    assert spy.collector.assembled == 0
    assert spy.jobs == 0
    spy.stage_b.assert_not_awaited()
    spy.thesis.assert_not_awaited()
    spy.factors.assert_not_awaited()
    if door == "direct":
        spy.upsert.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("reason", ["stage_a_unparseable", "stage_a_malformed", "stage_a_X"])
async def test_a_degraded_stage_a_stops_the_run_and_nothing_is_cached(monkeypatch, door, reason):
    """Spent 30 s and ~15 Gemini calls on a report that could only be refunded."""
    shell = np_._mark_degraded(np_.stage_a_fallback(), reason)
    run, spy = _wire(monkeypatch, door, shell=shell)
    report = await run()
    assert report == {REPORT_DEGRADED_KEY: reason}
    assert report_degraded_reason(report) == reason, "the key both settlement gates read"
    _nothing_after_stage_a_ran(spy, door)


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
async def test_a_real_shell_still_runs_the_whole_pipeline(monkeypatch, door):
    run, spy = _wire(
        monkeypatch, door, shell={"quality_score": 71},
        stage_b=np_.NarrativeRunOutcome(required=12, fell_back=0),
    )
    report = await run()
    assert report_degraded_reason(report) is None
    assert spy.collector.assembled == 1 and spy.jobs == 1
    spy.stage_b.assert_awaited_once()
    spy.thesis.assert_awaited_once()
    spy.factors.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("fell_back, outage", [(12, True), (6, True), (5, False), (1, False), (0, False)])
async def test_the_refund_line_on_both_doors(monkeypatch, door, fell_back, outage):
    run, spy = _wire(
        monkeypatch, door, shell={"quality_score": 71},
        stage_b=np_.NarrativeRunOutcome(required=12, fell_back=fell_back),
    )
    report = await run()
    if outage:
        assert report == {REPORT_DEGRADED_KEY: "stage_b_outage"}
        spy.factors.assert_not_awaited()
    else:
        assert report_degraded_reason(report) is None
        spy.factors.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_direct_door_caches_a_healthy_report_and_not_an_outage(monkeypatch):
    run, spy = _wire(
        monkeypatch, "direct", shell={"quality_score": 71},
        stage_b=np_.NarrativeRunOutcome(required=12, fell_back=1),
    )
    await run()
    spy.upsert.assert_awaited_once()

    run, spy = _wire(
        monkeypatch, "direct", shell={"quality_score": 71},
        stage_b=np_.NarrativeRunOutcome(required=12, fell_back=12),
    )
    await run()
    spy.upsert.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("fell_back, shared", [(0, True), (1, True), (2, False), (5, False)])
async def test_a_partial_run_is_delivered_but_shared_with_nobody(monkeypatch, door, fell_back, shared):
    """Below the refund line the buyer keeps what they paid for, but a cache would serve those
    "Data unavailable" sections free, and re-sell them, until the next close."""
    run, spy = _wire(
        monkeypatch, door, shell={"quality_score": 71},
        stage_b=np_.NarrativeRunOutcome(required=12, fell_back=fell_back),
    )
    report = await run()
    assert report_degraded_reason(report) is None, "still delivered"
    lost = report_degraded_sections(report)
    assert (np_.STAGE_B_PARTIAL_SECTION not in lost) is shared
    if door == "direct":
        assert spy.upsert.await_count == (1 if shared else 0)


def test_a_partial_keeps_what_assemble_report_already_recorded():
    report = {DEGRADED_SECTIONS_KEY: ["snap_health:health_check:ratios"]}
    record_degraded_section(report, np_.STAGE_B_PARTIAL_SECTION)
    record_degraded_section(report, np_.STAGE_B_PARTIAL_SECTION)
    assert report_degraded_sections(report) == [
        "snap_health:health_check:ratios", np_.STAGE_B_PARTIAL_SECTION,
    ]


def test_the_shared_cache_readers_refuse_a_partial_report():
    """The literal in `ticker_report_cache` must stay the collector's key, or the marker is
    silently ignored on the read side."""
    assert trc.PARTIAL_REPORT_KEY == DEGRADED_SECTIONS_KEY


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOORS)
@pytest.mark.parametrize("thesis", [
    {"bull_case": [], "bear_case": []},
    {"bull_case": ["a"], "bear_case": []},
    {"bull_case": [], "bear_case": ["b"]},
])
async def test_a_report_with_no_thesis_is_refunded_not_delivered(monkeypatch, door, thesis, caplog):
    """An unusable Stage A thesis plus a failed synthesis validates, bills and caches."""
    run, spy = _wire(
        monkeypatch, door, shell={"quality_score": 71},
        stage_b=np_.NarrativeRunOutcome(required=12, fell_back=0), thesis=thesis,
    )
    with caplog.at_level(logging.WARNING):
        report = await run()
    assert report == {REPORT_DEGRADED_KEY: "thesis_empty"}
    spy.factors.assert_not_awaited()
    if door == "direct":
        spy.upsert.assert_not_awaited()
    errors = _errors(caplog)
    assert len(errors) == 1 and errors[0].msg.startswith("[report-thesis-empty]")


@pytest.mark.parametrize("report", [{"symbol": "AAPL"}, None, "text", {"core_thesis": "x"}])
def test_a_stub_without_a_thesis_block_is_not_an_empty_thesis(report):
    assert np_.thesis_empty_reason(report, ticker="T", persona="p") is None


@pytest.mark.asyncio
async def test_a_direct_report_failing_the_schema_is_typed_not_data_incomplete(monkeypatch, caplog):
    run, spy = _wire(
        monkeypatch, "direct", shell={"quality_score": 71},
        stage_b=np_.NarrativeRunOutcome(required=12, fell_back=0),
        schema_problem="1 field(s): x (missing)",
    )
    with caplog.at_level(logging.WARNING):
        report = await run()
    assert report == {REPORT_DEGRADED_KEY: "report_schema_invalid"}
    spy.upsert.assert_not_awaited()
    errors = _errors(caplog)
    assert len(errors) == 1 and errors[0].msg.startswith("[report-schema-invalid]")


# ── Stage B: the counts and the line ─────────────────────────────────────────

class _StageBGemini:
    def __init__(self, *, fail_labels=(), blank_labels=()):
        self.fail, self.blank = set(fail_labels), set(blank_labels)

    async def create_narrative_cache(self, *a, **k):
        return None

    async def delete_cache(self, handle):
        return None

    async def generate_text(self, prompt=None, system_instruction=None, thinking_budget=None):
        if prompt in self.fail:
            raise RuntimeError("quota")
        return {"text": "" if prompt in self.blank else "Real prose."}


def _jobs(*labels, nullable=()):
    out = {}

    def _job(label):
        return np_.NarrativeJob(
            label=label, prompt=label, word_cap=20,
            apply=lambda value, key=label: out.__setitem__(key, value),
            fallback_value="FALLBACK", nullable=label in nullable,
        )

    return [_job(label) for label in labels], out


@pytest.mark.asyncio
async def test_a_clean_run_counts_no_fallbacks():
    jobs, out = _jobs("a", "b", "c")
    outcome = await np_.run_narrative_jobs(jobs, _StageBGemini(), PERSONA)
    assert outcome == np_.NarrativeRunOutcome(required=3, fell_back=0)
    assert out == {"a": "Real prose.", "b": "Real prose.", "c": "Real prose."}


@pytest.mark.asyncio
async def test_no_jobs_is_an_empty_outcome():
    assert await np_.run_narrative_jobs([], _StageBGemini(), PERSONA) == np_.NarrativeRunOutcome()


@pytest.mark.asyncio
async def test_both_routes_to_the_fallback_count():
    jobs, out = _jobs("raised", "blank", "fine")
    gem = _StageBGemini(fail_labels=["raised"], blank_labels=["blank"])
    outcome = await np_.run_narrative_jobs(jobs, gem, PERSONA)
    assert outcome == np_.NarrativeRunOutcome(required=3, fell_back=2)
    assert out["raised"] == "FALLBACK" and out["blank"] == "FALLBACK"


@pytest.mark.asyncio
async def test_a_nullable_opt_out_is_an_answer_not_a_failure():
    jobs, out = _jobs("a", "optional", nullable=("optional",))
    outcome = await np_.run_narrative_jobs(jobs, _StageBGemini(blank_labels=["optional"]), PERSONA)
    assert outcome == np_.NarrativeRunOutcome(required=1, fell_back=0)
    assert out["optional"] is None


class _CachedPathGemini:
    """The context-cache path answers; the inline path says whether it was reached."""

    def __init__(self, cached_text):
        self.cached_text = cached_text
        self.inline_calls = 0

    async def create_narrative_cache(self, *a, **k):
        return {"cache": object()}

    async def delete_cache(self, handle):
        return None

    async def generate_text_cached(self, prompt, handle, thinking_budget=None):
        return {"text": self.cached_text, "finish_reason": "OTHER"}

    async def generate_text(self, prompt=None, system_instruction=None, thinking_budget=None):
        self.inline_calls += 1
        return {"text": "Real prose."}


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", ["", "   \n"])
async def test_a_blank_cached_answer_tries_inline_before_it_counts(blank, caplog):
    """The runner promises only a failure of BOTH paths lands on the sentinel, and these jobs
    now count toward a refund."""
    jobs, out = _jobs("a", "b")
    gem = _CachedPathGemini(blank)
    with caplog.at_level(logging.WARNING, logger=np_.logger.name):
        outcome = await np_.run_narrative_jobs(jobs, gem, PERSONA, evidence="EVIDENCE")
    assert outcome == np_.NarrativeRunOutcome(required=2, fell_back=0)
    assert gem.inline_calls == 2 and out == {"a": "Real prose.", "b": "Real prose."}
    assert "came back blank" in caplog.text and "finish=OTHER" in caplog.text


@pytest.mark.parametrize("required, fell_back, expected", [
    (12, 12, "stage_b_outage"),
    (12, 6, "stage_b_outage"),                      # exactly on the owner's line
    (12, 5, None),
    (12, 1, None),
    (2, 2, "stage_b_outage"),
    (3, 2, "stage_b_outage"),
    (2, 1, None),                                   # half, but a single miss still ships
    (1, 1, None),
    (0, 0, None),
])
def test_stage_b_outage_reason(required, fell_back, expected):
    outcome = np_.NarrativeRunOutcome(required=required, fell_back=fell_back)
    assert np_.stage_b_outage_reason(outcome, ticker="T", persona="p") == expected


@pytest.mark.parametrize("stub", [None, 5, "x", MagicMock()])
def test_anything_but_an_outcome_is_no_outage(stub):
    assert np_.stage_b_outage_reason(stub, ticker="T", persona="p") is None


def test_an_outage_logs_the_one_error_and_a_partial_run_does_not(caplog):
    with caplog.at_level(logging.WARNING, logger=np_.logger.name):
        np_.stage_b_outage_reason(np_.NarrativeRunOutcome(12, 1), ticker="T", persona="p")
    assert not _errors(caplog) and "[report-stage-b-partial]" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=np_.logger.name):
        np_.stage_b_outage_reason(np_.NarrativeRunOutcome(12, 12), ticker="T", persona="p")
    errors = _errors(caplog)
    assert len(errors) == 1 and errors[0].msg.startswith("[report-stage-b-outage]")


# ── The schema gate ──────────────────────────────────────────────────────────



def test_a_report_missing_its_required_fields_is_a_problem():
    problem = trc.report_schema_problem({"symbol": "AAPL"})
    assert problem and "field(s)" in problem


def test_a_report_the_schema_accepts_is_no_problem(monkeypatch):
    """Anti-vacuity: the gate must be able to say yes."""
    monkeypatch.setattr(trc, "TickerReportResponse", lambda **report: None)
    assert trc.report_schema_problem({"symbol": "AAPL"}) is None


@pytest.mark.parametrize("bad", [None, 5, "text", ["a"]])
def test_a_non_mapping_is_a_problem_not_a_raise(bad):
    assert trc.report_schema_problem(bad)


def test_the_problem_never_quotes_the_report():
    secret_prose = "the model wrote this sentence"
    problem = trc.report_schema_problem({"symbol": 5, "company_name": {"x": secret_prose}})
    assert problem and secret_prose not in problem


@pytest.mark.asyncio
async def test_upsert_refuses_a_schema_invalid_report(monkeypatch, caplog):
    monkeypatch.setattr(trc, "report_is_grounding_free", lambda report: True)
    monkeypatch.setattr(trc, "report_schema_problem", lambda report: "1 field(s): x (missing)")
    written = MagicMock()
    monkeypatch.setattr(trc, "get_supabase", lambda: written)
    with caplog.at_level(logging.ERROR, logger=trc.__name__):
        await trc.upsert_cached_report("AAPL", "warren_buffett", {"symbol": "AAPL"})
    written.table.assert_not_called()
    assert "[report-schema-invalid]" in caplog.text


@pytest.mark.asyncio
async def test_the_schema_check_runs_off_the_loop(monkeypatch):
    import threading

    loop_thread = threading.get_ident()
    seen = []

    def _check(report):
        seen.append(threading.get_ident())
        return "bad"

    monkeypatch.setattr(trc, "report_is_grounding_free", lambda report: True)
    monkeypatch.setattr(trc, "report_schema_problem", _check)
    await trc.upsert_cached_report("AAPL", "warren_buffett", {"symbol": "AAPL"})
    assert seen and seen[0] != loop_thread, "a ~200 KB validation ran on the event loop"


def _shared_row_service(blob):
    q = MagicMock()
    for method in ("table", "select", "eq", "gte", "not_", "is_", "order", "limit"):
        getattr(q, method).return_value = q
    q.not_ = q
    q.execute.return_value = MagicMock(
        data=[{"ticker_report_data": blob, "completed_at": "2026-09-30T10:00:00+00:00"}]
    )
    svc = object.__new__(rs.ResearchService)
    svc.supabase = q
    return svc


@pytest.mark.asyncio
@pytest.mark.parametrize("problem, reused", [("1 field(s): x (missing)", False), (None, True)])
async def test_the_shared_lookup_never_re_sells_an_invalid_row(monkeypatch, problem, reused):
    from app.services.report_degradation import GROUNDING_FREE_KEY

    blob = {"symbol": "AAPL", GROUNDING_FREE_KEY: True}
    svc = _shared_row_service(blob)
    monkeypatch.setattr(rs, "report_dcf_source_matches", lambda b: True)
    monkeypatch.setattr(rs, "report_dcf_model_is_current", lambda b: True)
    monkeypatch.setattr(rs, "report_moat_model_is_current", lambda b: True)
    monkeypatch.setattr(rs, "report_schema_problem", lambda b: problem)
    got = await svc._lookup_shared_cache("AAPL", "warren_buffett")
    assert (got is not None) is reused


def _deep_service(monkeypatch, *, report):
    class _Agent:
        research_findings = ""

        async def run(self, ticker, progress_cb):
            if isinstance(report, BaseException):
                raise report
            return report

    svc = object.__new__(rs.ResearchService)
    svc.supabase = MagicMock()
    svc.fmp = svc.gemini = None
    notify = AsyncMock()
    upsert = AsyncMock()
    monkeypatch.setattr(svc, "_lookup_shared_cache", AsyncMock(return_value=None))
    monkeypatch.setattr(svc, "_any_still_active", lambda ids: True)
    monkeypatch.setattr(svc, "_update_status_async", AsyncMock())
    monkeypatch.setattr(svc, "_mark_processing_started", lambda report_id: None)
    monkeypatch.setattr(svc, "_notify_report_ready", notify)
    monkeypatch.setattr(rs, "ResearchAgent", lambda **kwargs: _Agent())
    monkeypatch.setattr(rs, "compute_quality_score", lambda key, data: 70)
    monkeypatch.setattr(rs, "upsert_cached_report", upsert)
    monkeypatch.setattr(rs, "_AGENT_SEMAPHORE", asyncio.Semaphore(1))
    monkeypatch.setattr(rs, "_AGENT_INFLIGHT", {})
    monkeypatch.setattr(rs, "_AGENT_RUNS", {})
    return svc, notify, upsert


@pytest.mark.asyncio
async def test_an_invalid_fresh_deep_report_is_refunded_not_completed(monkeypatch, caplog):
    """The deep door never validated, so an invalid report was kept at 20 credits and failed
    to decode every time it was opened. Raising is what makes the refund fire."""
    svc, notify, upsert = _deep_service(monkeypatch, report={"symbol": "AAPL"})
    monkeypatch.setattr(rs, "report_schema_problem", lambda report: "1 field(s): x (missing)")
    with caplog.at_level(logging.WARNING):
        with pytest.raises(DegradedReportError) as raised:
            await svc.generate_report("r1", "AAPL", "warren_buffett", "u1")
    assert raised.value.reason == "report_schema_invalid"
    svc.supabase.table.assert_not_called()      # no completion write
    upsert.assert_not_awaited()
    notify.assert_not_awaited()
    errors = _errors(caplog)
    assert len(errors) == 1 and errors[0].msg.startswith("[report-schema-invalid]")


@pytest.mark.asyncio
async def test_a_degraded_deep_report_logs_no_error_of_its_own(monkeypatch, caplog):
    svc, notify, upsert = _deep_service(
        monkeypatch, report={REPORT_DEGRADED_KEY: "stage_a_unparseable"},
    )
    with caplog.at_level(logging.WARNING):
        with pytest.raises(DegradedReportError):
            await svc.generate_report("r1", "AAPL", "warren_buffett", "u1")
    assert not _errors(caplog), "Stage A's helper already logged the one ERROR"
    upsert.assert_not_awaited()
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unexpected_deep_failure_still_pages_with_its_stack(monkeypatch, caplog):
    svc, _notify, _upsert = _deep_service(monkeypatch, report=RuntimeError("boom"))
    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError):
            await svc.generate_report("r1", "AAPL", "warren_buffett", "u1")
    errors = _errors(caplog)
    assert len(errors) == 1 and errors[0].exc_info


# ── The thesis sanitizer ─────────────────────────────────────────────────────

@pytest.mark.parametrize("thesis, bull", [
    ({"bull_case": 5, "bear_case": []}, []),                     # used to raise TypeError
    ({"bull_case": "a point", "bear_case": []}, []),             # one bullet per character
    ({"bull_case": ["ok", "", "  ", None, 5], "bear_case": []}, ["ok"]),
    ({"bull_case": ["1", "2", "3", "4", "5", "6"], "bear_case": []}, ["1", "2", "3", "4", "5"]),
    (None, []),
    ("nope", []),
])
def test_sanitize_thesis_keeps_only_real_points(thesis, bull):
    assert _sanitize_thesis(thesis)["bull_case"] == bull


# ── Stage A free text the schema would otherwise refuse after Stage B was paid for ───────────

@pytest.mark.parametrize("value, expected", [
    ("A note.", "A note."), ("", None), ("   ", None), (None, None),
    (5, None), (["a"], None), ({"a": 1}, None), (True, None),
])
def test_stage_a_text(value, expected):
    assert _stage_a_text(value) == expected


def test_sanitize_stage_a_factors_keeps_a_shape_the_schema_accepts():
    from app.schemas.ticker_report import CriticalFactorResponse

    raw = [
        {"title": "Debt", "severity": "high", "description": "", "watch": ""},
        {"title": "No severity"},
        {"title": "Odd types", "severity": 5, "description": 7, "watch": ["x"]},
        {"title": "", "severity": "high"},
        {"severity": "high"},
        {"title": 5},
        "a string",
        None,
    ]
    factors = _sanitize_stage_a_factors(raw)
    assert [f["title"] for f in factors] == ["Debt", "No severity", "Odd types"]
    for factor in factors:
        CriticalFactorResponse(**factor)
    assert factors[1]["severity"] == "medium" and factors[1]["watch"] is None


def test_sanitize_stage_a_factors_caps_at_five_and_refuses_a_non_list():
    assert len(_sanitize_stage_a_factors([{"title": str(i)} for i in range(9)])) == 5
    assert _sanitize_stage_a_factors("text") == [] and _sanitize_stage_a_factors(None) == []


def test_a_non_string_industry_reads_as_no_archetype():
    from app.services.agents.ticker_report_data_collector import _classify_industry_archetype

    assert _classify_industry_archetype(123) is None
    assert _classify_industry_archetype(["Gold"]) is None
