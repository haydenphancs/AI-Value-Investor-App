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

    async def create_narrative_cache(self, system_instruction, evidence,
                                     ttl_minutes=None):
        self.created += 1
        return self._cache_handle

    async def generate_text_cached(self, prompt, handle):
        self.cached_prompts.append(prompt)
        if self._cached_raises:
            raise RuntimeError("simulated cache-path failure")
        return {"text": self._text}

    async def generate_text(self, prompt, system_instruction=None):
        self.inline_prompts.append(prompt)
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

