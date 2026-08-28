"""
Tests for Stage-B context caching in run_narrative_jobs.

The optimization hoists the shared `evidence` blob into one Gemini
CachedContent so the N parallel narrative calls each bill only their per-field
instruction. These tests pin the SAFE behavior — caching must never degrade a
report:
  1. No cache (create returns None) → inline path with the full prompt.
  2. Cache present → call uses the SLIM prompt (evidence stripped to a pointer).
  3. Cache call FAILS → falls back to the inline path (full quality), NOT the
     honest sentinel.

No live Gemini: a fake client records which path each job took.
"""

from __future__ import annotations

import pytest

from app.services.agents.narrative_prompts import (
    NarrativeJob,
    run_narrative_jobs,
    _EVIDENCE_POINTER,
)
from app.services.agents.persona_config import get_persona_config

_EVIDENCE = (
    "CARD VALUES (AS DISPLAYED TO USER): Revenue +20% YoY. "
    "Operating margin 31%. Altman Z-Score 4.2. Net cash $30B."
)


class _FakeGemini:
    """Records whether each job went through the cached or inline path."""

    def __init__(self, *, cache_handle=None, cached_raises=False,
                 inline_raises=False,
                 text="A durable, high-margin compounder with a wide moat."):
        self._cache_handle = cache_handle
        self._cached_raises = cached_raises
        # When True, the INLINE path also blows up. Combined with cached_raises
        # this drives BOTH paths to fail so the job lands on its sentinel — while
        # the runner's `finally` must STILL clean up the cache (delete_cache).
        self._inline_raises = inline_raises
        self._text = text
        self.created = 0
        self.deleted = 0
        self.cached_prompts: list[str] = []
        self.inline_prompts: list[str] = []
        # Thinking budgets, recorded per path. The parameters below are declared
        # EXPLICITLY rather than swallowed by **kwargs on purpose: a fake that
        # accepts anything cannot catch a call site that stopped passing the
        # budget, which is exactly the regression these lists exist to detect.
        self.cached_budgets: list = []
        self.inline_budgets: list = []

    async def create_narrative_cache(self, system_instruction, evidence,
                                     ttl_minutes=None):
        self.created += 1
        return self._cache_handle

    async def generate_text_cached(self, prompt, handle, thinking_budget=None):
        self.cached_prompts.append(prompt)
        self.cached_budgets.append(thinking_budget)
        if self._cached_raises:
            raise RuntimeError("simulated cache-path failure")
        return {"text": self._text}

    async def generate_text(self, prompt, system_instruction=None,
                            thinking_budget=None):
        self.inline_prompts.append(prompt)
        self.inline_budgets.append(thinking_budget)
        if self._inline_raises:
            raise RuntimeError("simulated inline-path failure")
        return {"text": self._text}

    async def delete_cache(self, handle):
        self.deleted += 1


def _job():
    captured: dict = {}
    prompt = (
        "Write the Executive Summary.\n\n"
        f"EVIDENCE:\n{_EVIDENCE}\n\n"
        "LENGTH: under 60 words. Cite a number from the CARD VALUES block."
    )
    job = NarrativeJob(
        label="executive_summary_text",
        prompt=prompt,
        word_cap=80,
        apply=lambda v: captured.__setitem__("v", v),
        fallback_value="SENTINEL — narrative unavailable.",
    )
    return job, captured


@pytest.mark.asyncio
async def test_no_cache_uses_inline_with_full_evidence():
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(cache_handle=None)  # cache creation declined
    job, captured = _job()

    await run_narrative_jobs([job], gemini, persona, evidence=_EVIDENCE)

    assert gemini.cached_prompts == []                 # never tried cache calls
    assert len(gemini.inline_prompts) == 1
    assert _EVIDENCE in gemini.inline_prompts[0]        # full evidence inline
    assert captured["v"] and captured["v"] != "SENTINEL — narrative unavailable."
    assert gemini.deleted == 1                          # delete_cache(None) still called


@pytest.mark.asyncio
async def test_cache_path_strips_evidence():
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(cache_handle={"cache": object(), "model": object()})
    job, captured = _job()

    await run_narrative_jobs([job], gemini, persona, evidence=_EVIDENCE)

    assert gemini.inline_prompts == []                 # cached path only
    assert len(gemini.cached_prompts) == 1
    slim = gemini.cached_prompts[0]
    assert _EVIDENCE not in slim                        # evidence hoisted to cache
    assert _EVIDENCE_POINTER in slim                    # replaced by a pointer
    assert "CARD VALUES" in slim                        # the instruction label remains
    assert captured["v"] and "SENTINEL" not in captured["v"]
    assert gemini.deleted == 1                          # cache cleaned up


@pytest.mark.asyncio
async def test_cache_failure_falls_back_to_inline_not_sentinel():
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(
        cache_handle={"cache": object(), "model": object()},
        cached_raises=True,                            # cache path blows up
    )
    job, captured = _job()

    await run_narrative_jobs([job], gemini, persona, evidence=_EVIDENCE)

    assert len(gemini.cached_prompts) == 1             # tried cache first
    assert len(gemini.inline_prompts) == 1             # then recovered inline
    assert _EVIDENCE in gemini.inline_prompts[0]       # inline retry has full evidence
    # Full-quality recovery — NOT the honest sentinel.
    assert captured["v"] and "SENTINEL" not in captured["v"]


# ── Appended coverage ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_evidence_skips_cache_and_keeps_prompt_intact():
    """EMPTY-EVIDENCE GUARD: evidence="" must short-circuit BEFORE any cache work.

    `use_cache = bool(evidence) and ...` is False, so create_narrative_cache is
    never called and the cache_handle stays None. The per-job inline path then
    fires with the ORIGINAL prompt — crucially, the runner never executes
    `job.prompt.replace("", _EVIDENCE_POINTER)`, which would splatter the pointer
    between every character of the prompt and corrupt it.
    """
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(cache_handle={"cache": object(), "model": object()})
    job, captured = _job()
    original_prompt = job.prompt

    await run_narrative_jobs([job], gemini, persona, evidence="")

    assert gemini.created == 0                          # never tried to build a cache
    assert gemini.cached_prompts == []                 # cached path never taken
    assert len(gemini.inline_prompts) == 1
    # The prompt is byte-for-byte intact — no empty-string replace ran.
    assert gemini.inline_prompts[0] == original_prompt
    assert _EVIDENCE_POINTER not in gemini.inline_prompts[0]
    assert captured["v"] and "SENTINEL" not in captured["v"]
    assert gemini.deleted == 1                          # delete_cache(None) still called


@pytest.mark.asyncio
async def test_multiple_jobs_all_use_cached_slim_path():
    """MULTIPLE JOBS: a 3-job mix all route through the cached path, and each
    gets its OWN slim prompt (evidence stripped) when a cache handle exists."""
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(cache_handle={"cache": object(), "model": object()})

    jobs = []
    captures = []
    for label in ("executive_summary_text", "moat_durability_note",
                  "macro_intelligence_brief"):
        captured: dict = {}
        prompt = (
            f"Write the {label}.\n\n"
            f"EVIDENCE:\n{_EVIDENCE}\n\n"
            "LENGTH: under 60 words. Cite a number from the CARD VALUES block."
        )
        jobs.append(NarrativeJob(
            label=label,
            prompt=prompt,
            word_cap=80,
            apply=lambda v, c=captured: c.__setitem__("v", v),
            fallback_value=f"SENTINEL {label}",
        ))
        captures.append(captured)

    await run_narrative_jobs(jobs, gemini, persona, evidence=_EVIDENCE)

    assert gemini.created == 1                          # ONE shared cache for all 3
    assert gemini.inline_prompts == []                 # nobody fell back to inline
    assert len(gemini.cached_prompts) == 3             # every job hit the cache path
    for slim in gemini.cached_prompts:
        assert _EVIDENCE not in slim                   # evidence hoisted out of each
        assert _EVIDENCE_POINTER in slim               # replaced by the pointer
    for captured in captures:
        assert captured["v"] and "SENTINEL" not in captured["v"]
    assert gemini.deleted == 1                          # cleaned up exactly once


@pytest.mark.asyncio
async def test_delete_cache_runs_even_when_both_paths_raise():
    """delete_cache MUST run even when a job's generation fails on BOTH paths.

    Cached gen raises → inline retry → inline ALSO raises → outer except applies
    the sentinel. The `finally` in run_narrative_jobs still has to delete the
    cache (no leaked CachedContent on the Gemini side).
    """
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(
        cache_handle={"cache": object(), "model": object()},
        cached_raises=True,
        inline_raises=True,
    )
    job, captured = _job()

    # Must not raise even though both generation paths blow up.
    await run_narrative_jobs([job], gemini, persona, evidence=_EVIDENCE)

    assert len(gemini.cached_prompts) == 1             # tried cache first
    assert len(gemini.inline_prompts) == 1             # then tried inline
    assert gemini.deleted == 1                          # cache STILL cleaned up
    # Total failure of both paths → the job's honest sentinel.
    assert captured["v"] == "SENTINEL — narrative unavailable."


@pytest.mark.asyncio
async def test_slim_prompt_strips_evidence_even_when_embedded_twice():
    """NO-DOUBLE-BILLING: if a builder embedded the evidence blob twice, the slim
    prompt sent to generate_text_cached must contain NEITHER copy (str.replace
    swaps all occurrences) — so the cached call never re-pays for the evidence."""
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(cache_handle={"cache": object(), "model": object()})

    captured: dict = {}
    # Two separate inline copies of the evidence (e.g. a header digest + a full
    # EVIDENCE block) — both must be hoisted to the cache.
    prompt = (
        f"HEADER DIGEST:\n{_EVIDENCE}\n\n"
        "Write the Executive Summary.\n\n"
        f"EVIDENCE:\n{_EVIDENCE}\n\n"
        "LENGTH: under 60 words."
    )
    job = NarrativeJob(
        label="executive_summary_text",
        prompt=prompt,
        word_cap=80,
        apply=lambda v: captured.__setitem__("v", v),
        fallback_value="SENTINEL — narrative unavailable.",
    )

    assert prompt.count(_EVIDENCE) == 2                 # precondition: embedded twice

    await run_narrative_jobs([job], gemini, persona, evidence=_EVIDENCE)

    assert len(gemini.cached_prompts) == 1
    slim = gemini.cached_prompts[0]
    assert _EVIDENCE not in slim                        # BOTH copies stripped
    assert slim.count(_EVIDENCE_POINTER) == 2          # each replaced by a pointer
    assert gemini.deleted == 1


@pytest.mark.asyncio
async def test_nullable_job_empty_text_applies_none_not_fallback():
    """nullable=True + empty cleaned text → applied value is None, NOT the
    fallback string. (Whitespace-only model output post-processes to "".)"""
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(cache_handle=None, text="   \n  ")  # → cleans to ""
    captured: dict = {"v": "untouched"}
    prompt = (
        "Write the guidance quote.\n\n"
        f"EVIDENCE:\n{_EVIDENCE}\n\n"
        "If there's no signal, write NULL."
    )
    job = NarrativeJob(
        label="guidance_quote",
        prompt=prompt,
        word_cap=30,
        apply=lambda v: captured.__setitem__("v", v),
        fallback_value="SENTINEL — should NOT be used for a nullable field.",
        nullable=True,
    )

    await run_narrative_jobs([job], gemini, persona, evidence=_EVIDENCE)

    assert len(gemini.inline_prompts) == 1             # no cache → inline path
    assert captured["v"] is None                       # nullable empty → None



# ─────────────────────────────────────────────────────────────────────────────
# Call-site regression: BOTH report paths must FORWARD `evidence`.
#
# Every test above passes `evidence=` explicitly, so they structurally cannot
# catch a CALLER that omits it — which is exactly the bug this pins. Dropping the
# 4th argument leaves `use_cache` False and every narrative call silently
# re-sends the full evidence blob inline at full token price: no error, no log,
# just a bigger bill on the primary paid surface.
#
# Identity is asserted, not truthiness. `run_narrative_jobs` does
# `job.prompt.replace(evidence, _EVIDENCE_POINTER)`, so a different-but-truthy
# string would create a cache and then fail every substitution — producing
# cached-path calls that still carry the full inline evidence.
# ─────────────────────────────────────────────────────────────────────────────

_SENTINEL_EVIDENCE = "SENTINEL EVIDENCE — must reach run_narrative_jobs verbatim."


def _evidence_spy():
    """Returns (spy, calls). `evidence` defaults to "" exactly as the real
    signature does, so a 3-argument caller records "" and fails the assertion."""
    calls: list[dict] = []

    async def _spy(jobs, gemini, persona, evidence="", *a, **kw):
        calls.append({"evidence": evidence, "persona": persona})

    return _spy, calls


async def _noop(*a, **kw):
    return None


@pytest.mark.asyncio
async def test_ticker_report_path_forwards_evidence(monkeypatch):
    """`/stocks/{ticker}/report` (the direct, paid path) must hoist evidence."""
    import app.services.ticker_report_service as trs

    spy, calls = _evidence_spy()
    monkeypatch.setattr(trs, "run_narrative_jobs", spy)
    monkeypatch.setattr(trs, "build_financial_context", lambda out: _SENTINEL_EVIDENCE)
    monkeypatch.setattr(trs, "build_narrative_jobs", lambda persona, ev, rep: [])
    monkeypatch.setattr(trs, "synthesize_core_thesis", _noop)
    monkeypatch.setattr(trs, "synthesize_critical_factors", _noop)
    monkeypatch.setattr(trs, "upsert_cached_report", _noop)

    # Build the service without __init__ so no FMP/Gemini/Supabase client is
    # constructed; the pipeline below never touches the real ones.
    svc = object.__new__(trs.TickerReportService)
    svc.gemini = object()

    class _FakeCollector:
        async def collect(self, ticker, persona_key):
            return object()

        def assemble_report(self, out, shell):
            return {}

    svc.collector = _FakeCollector()

    async def _fake_stage_a(out, persona, evidence):
        return {}

    monkeypatch.setattr(svc, "_generate_stage_a", _fake_stage_a)

    await svc.generate_fresh_report("AAPL", "warren_buffett")

    assert len(calls) == 1
    assert calls[0]["evidence"] == _SENTINEL_EVIDENCE, (
        "ticker_report_service dropped `evidence` — Stage-B context caching is "
        "silently disabled and all N narrative calls bill at full inline price."
    )


@pytest.mark.asyncio
async def test_research_agent_path_forwards_evidence(monkeypatch):
    """`/research/generate` must too — pinned in the same test so the two
    paths can never drift apart again."""
    import app.services.agents.research_agent as ra

    spy, calls = _evidence_spy()
    monkeypatch.setattr(ra, "run_narrative_jobs", spy)
    monkeypatch.setattr(ra, "build_financial_context", lambda out: _SENTINEL_EVIDENCE)
    monkeypatch.setattr(ra, "build_narrative_jobs", lambda persona, ev, rep: [])
    monkeypatch.setattr(ra, "synthesize_core_thesis", _noop)
    monkeypatch.setattr(ra, "synthesize_critical_factors", _noop)

    agent = object.__new__(ra.ResearchAgent)
    agent.persona = get_persona_config("warren_buffett")
    agent.gemini = object()
    agent.research_findings = ""

    class _FakeCollector:
        async def collect(self, ticker, persona_key):
            return object()

        def assemble_report(self, out, shell):
            return {}

    agent.collector = _FakeCollector()

    async def _fake_agentic(out, evidence):
        return ""

    async def _fake_stage_a(out, evidence, research_text):
        return {}

    monkeypatch.setattr(agent, "_agentic_research", _fake_agentic)
    monkeypatch.setattr(agent, "_generate_stage_a", _fake_stage_a)

    await agent.run("AAPL")

    assert len(calls) == 1
    assert calls[0]["evidence"] == _SENTINEL_EVIDENCE


# ── Thinking budget on the Stage-B path (SYSTEM_DESIGN_GUIDELINES 9b.7) ──────
#
# gemini-2.5-flash thinks by default and those tokens bill at the OUTPUT rate.
# Stage B is ~14-18 word-capped jobs per report, measured at ~391 thought tokens
# each for ~100 tokens of prose — so the cap is real money on the 20-credit
# product. What these tests actually defend is the SYMMETRY of the two paths:
# `run_narrative_jobs` is fail-open by design, and the inline path is not an
# exotic error branch. `create_narrative_cache` returns None for evidence below
# the model's ~1024-token floor, on quota, and on a hung SDK — on any of those
# EVERY job takes the inline path. Capping only the cached call is therefore not
# a partial fix on those tickers, it is a ZERO fix, silently, with normal logs.

from app.config import settings  # noqa: E402
from app.services.agents.narrative_prompts import (  # noqa: E402
    _resolve_budget,
    narrative_thinking_budget,
    stage_a_thinking_budget,
)

_HANDLE = {"cache": object(), "model": object()}


@pytest.mark.asyncio
async def test_the_cached_stage_b_call_carries_the_thinking_budget():
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(cache_handle=_HANDLE)
    job, _ = _job()

    await run_narrative_jobs([job], gemini, persona, evidence=_EVIDENCE)

    assert gemini.cached_budgets == [narrative_thinking_budget()]


@pytest.mark.asyncio
async def test_the_inline_fallback_carries_the_same_thinking_budget():
    """THE regression this pair exists for. A cache-path hiccup falls back
    inline; if that call is uncapped the saving evaporates precisely under quota
    pressure, and nothing says so."""
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(cache_handle=_HANDLE, cached_raises=True)
    job, _ = _job()

    await run_narrative_jobs([job], gemini, persona, evidence=_EVIDENCE)

    assert len(gemini.inline_prompts) == 1          # it really took the fallback
    assert gemini.inline_budgets == [narrative_thinking_budget()]
    assert gemini.cached_budgets == [narrative_thinking_budget()]


@pytest.mark.asyncio
async def test_the_no_cache_path_still_caps_thinking():
    """Evidence below the model's cache floor => cache_handle is None => EVERY
    job is inline. This is the common case that makes a cached-only cap a zero."""
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(cache_handle=None)
    job, _ = _job()

    await run_narrative_jobs([job], gemini, persona, evidence=_EVIDENCE)

    assert gemini.cached_prompts == []
    assert gemini.inline_budgets == [narrative_thinking_budget()]


@pytest.mark.asyncio
async def test_every_job_gets_the_budget_not_just_the_first():
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(cache_handle=_HANDLE)
    jobs = [_job()[0] for _ in range(3)]

    await run_narrative_jobs(jobs, gemini, persona, evidence=_EVIDENCE)

    assert gemini.cached_budgets == [narrative_thinking_budget()] * 3


@pytest.mark.asyncio
async def test_a_negative_setting_restores_the_model_default(monkeypatch):
    """The rollback path. A negative value maps to None — send NO thinking_config
    at all, byte-identical to a pre-cap request — rather than passing Gemini's
    own `-1` ("dynamic thinking") through, which is a different wire message and
    a different assumption."""
    monkeypatch.setattr(settings, "REPORT_NARRATIVE_THINKING_BUDGET", -1)
    persona = get_persona_config("warren_buffett")
    gemini = _FakeGemini(cache_handle=_HANDLE, cached_raises=True)
    job, _ = _job()

    await run_narrative_jobs([job], gemini, persona, evidence=_EVIDENCE)

    assert gemini.cached_budgets == [None]
    assert gemini.inline_budgets == [None]


def test_the_resolver_maps_negatives_to_none_and_keeps_zero():
    """0 and None are NOT interchangeable: 0 disables thinking, None leaves the
    model's default alone. Collapsing them would make the cap unrollbackable."""
    assert _resolve_budget(0) == 0
    assert _resolve_budget(512) == 512
    assert _resolve_budget(-1) is None
    assert _resolve_budget(-9999) is None


def test_the_stage_b_cap_is_actually_a_cap():
    """A later 'let's be safe' bump would silently restore the whole cost.
    Measured: 391 thought tokens at default, and outputs were substantively
    identical at 0 / 512 / 1024 / default."""
    budget = narrative_thinking_budget()
    assert budget is not None and 0 <= budget <= 1024


def test_stage_a_and_stage_b_budgets_are_independent_knobs(monkeypatch):
    """Stage A is the one with quality risk (it decides thesis/pros/cons/moat/
    valuation), so it must be revertable on its own WITHOUT giving up the
    risk-free Stage B saving."""
    monkeypatch.setattr(settings, "REPORT_STAGE_A_THINKING_BUDGET", 777)
    monkeypatch.setattr(settings, "REPORT_NARRATIVE_THINKING_BUDGET", 111)
    assert stage_a_thinking_budget() == 777
    assert narrative_thinking_budget() == 111

    # …and reverting ONE must not revert the other. That independence is the
    # whole point of two settings.
    monkeypatch.setattr(settings, "REPORT_STAGE_A_THINKING_BUDGET", -1)
    assert stage_a_thinking_budget() is None
    assert narrative_thinking_budget() == 111
