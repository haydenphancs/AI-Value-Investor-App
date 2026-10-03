"""No real investor's name, catchphrase or trading directive reaches the REPORT persona text.

The five persona LABELS were renamed to style names in migration 103, but the prompts kept the
person: every report's system prompt opened "…associated with Warren Buffett" / "…as
popularized by Peter Lynch", quoted their sayings ("Know what you own", "tenbaggers",
"diworsification"), and carried timing and sizing lines ("Buy when cheap, sell when 30-50% gain
reached", "a 10%+ portfolio weight") that contradict the ADVICE_BOUNDARY appended after them.
That prompt is the system instruction for the agentic loop, Stage A, the fallback path and
every Stage-B narrative job, so the person leaked into report prose.

This scans what the MODEL actually reads, rendered, for all five personas:
  * the full persona `system_prompt` and every structured field (display/agent labels, lens,
    metrics, bull/bear priorities, red flags, score rules, analysis focus, the method table);
  * the Stage-B helpers (`_style_block`, `_lens_directive` with every flag on);
  * every Stage-B narrative job built from a fully populated report (>= 12 jobs, so an empty
    shell cannot pass the scan vacuously);
  * the Stage-A, thesis-synthesis and critical-factors prompts.

Scope: report personas only. The Learn book voices name their author by design and are not
scanned (see `_persona_name_guard`). Names that appear in the DATA (executives, insiders,
holders) are allowed: the placeholders below carry none, so any hit is the template's own.
"""

from __future__ import annotations

import pytest

import _persona_name_guard as guard
from app.services.agents import narrative_prompts as np_
from app.services.agents.persona_config import (
    ADVICE_BOUNDARY,
    IDENTITY_RULE,
    METHOD_ATTRIBUTION_RULE,
    PERSONA_KEYS,
    PERSONA_METHODS,
    PersonaConfig,
    get_persona_config,
)
from test_narrative_prompts import _report

_KEYS = sorted(PERSONA_KEYS)
# Placeholders carry no name, no saying and no directive, so a hit can only be template text.
_EVIDENCE = "EVIDENCE PLACEHOLDER"
_COMPANY, _TICKER = "Example Corp", "EXMP"


def _structured_texts(cfg: PersonaConfig) -> list[tuple[str, str]]:
    out = [
        ("display_name", cfg.display_name),
        ("agent_label", cfg.agent_label),
        ("narrative_lens", cfg.narrative_lens),
        ("score_rules", cfg.score_rules),
    ]
    for field in ("key_metrics", "bull_priority", "bear_priority", "red_flags", "extra_data"):
        out += [(f"{field}[{i}]", v) for i, v in enumerate(getattr(cfg, field))]
    out += [(f"analysis_focus[{k}]", f"{k} {v}") for k, v in cfg.analysis_focus.items()]
    style, school = PERSONA_METHODS[cfg.key]
    out += [("PERSONA_METHODS.style", style), ("PERSONA_METHODS.school", school)]
    return out


def _stage_b_prompts(cfg: PersonaConfig) -> list[tuple[str, str]]:
    jobs = np_.build_narrative_jobs(cfg, _EVIDENCE, _report(3))
    # 12 fixed + 2 per critical factor: a shell that silently stopped emitting jobs would
    # make this scan pass on nothing.
    assert len(jobs) >= 12, [j.label for j in jobs]
    out = [(f"job:{j.label}", j.prompt) for j in jobs]
    out.append(("_style_block", np_._style_block(cfg)))
    out.append((
        "_lens_directive",
        np_._lens_directive(cfg, want_bull=True, want_bear=True, want_metrics=True, want_flags=True),
    ))
    out.append(("stage_a", np_.build_stage_a_prompt(cfg, _COMPANY, _TICKER, _EVIDENCE, "FINDINGS")))
    out.append((
        "thesis_synthesis",
        np_.build_thesis_synthesis_prompt(cfg, _COMPANY, _TICKER, _EVIDENCE, "DIGEST", 3, 3),
    ))
    out.append((
        "critical_factors",
        np_.build_critical_factors_prompt(
            cfg, _COMPANY, _TICKER, _EVIDENCE, "DIGEST", "BEAR BLOCK", "MACRO WATCH",
        ),
    ))
    return out


def _hits(pairs: list[tuple[str, str]], check) -> list[tuple[str, list[str]]]:
    return [(where, found) for where, text in pairs if (found := check(text))]


# ── The persona prompt and its structured fields ─────────────────────────────

@pytest.mark.parametrize("key", _KEYS)
def test_the_persona_prompt_names_no_real_investor_or_saying(key: str):
    cfg = get_persona_config(key)
    hits = _hits([("system_prompt", cfg.system_prompt)] + _structured_texts(cfg), guard.violations)
    assert not hits, f"{key}: real-investor name / catchphrase in persona text: {hits}"


@pytest.mark.parametrize("key", _KEYS)
def test_the_persona_prompt_gives_no_trading_directive(key: str):
    """Timing and sizing lines contradict ADVICE_BOUNDARY, which the model reads AFTER them."""
    cfg = get_persona_config(key)
    hits = _hits([("system_prompt", cfg.system_prompt)] + _structured_texts(cfg),
                 guard.directive_violations)
    assert not hits, f"{key}: trading directive / holder framing in persona text: {hits}"


@pytest.mark.parametrize("key", _KEYS)
def test_every_persona_prompt_carries_the_attribution_rule_before_the_bias_block(key: str):
    prompt = get_persona_config(key).system_prompt
    assert prompt.count(METHOD_ATTRIBUTION_RULE) == 1
    assert prompt.startswith(IDENTITY_RULE)
    assert prompt.endswith(ADVICE_BOUNDARY), "ADVICE_BOUNDARY must stay the last thing said"
    i = prompt.index(METHOD_ATTRIBUTION_RULE)
    assert i < prompt.index("HOW TO BIAS YOUR VERDICT") < prompt.index(ADVICE_BOUNDARY)


def test_the_attribution_rule_says_what_it_must():
    low = METHOD_ATTRIBUTION_RULE.lower()
    assert "in your own words" in low
    assert "do not attribute it to" in low and "any named investor" in low
    assert "catchphrases" in low
    # People in the DATA stay nameable — the rule must not blank out insiders or holders.
    assert "may be named as data" in low
    assert not guard.violations(METHOD_ATTRIBUTION_RULE)


def test_the_attribution_rule_applies_to_a_bare_persona_too():
    """Applied in __post_init__, not by hand per prompt — a sixth persona gets it for free."""
    bare = PersonaConfig(key="bare", agent_tag="bare", display_name="Bare", system_prompt="Body.")
    assert bare.system_prompt == IDENTITY_RULE + "Body." + METHOD_ATTRIBUTION_RULE + ADVICE_BOUNDARY


# ── Everything built from the persona (Stage A, Stage B, syntheses) ──────────

@pytest.mark.parametrize("key", _KEYS)
def test_no_report_prompt_names_a_real_investor_or_saying(key: str):
    hits = _hits(_stage_b_prompts(get_persona_config(key)), guard.violations)
    assert not hits, f"{key}: real-investor name / catchphrase in a report prompt: {hits}"


def test_the_insider_rule_no_longer_quotes_the_one_reason_saying():
    """The insider-flow job told every persona 'Insiders buy for one reason only … a strong
    endorsement' under a 'GARP method' label — a famous investor's line, as advocacy."""
    jobs = {j.label: j.prompt for j in
            np_.build_narrative_jobs(get_persona_config("warren_buffett"), _EVIDENCE, _report(3))}
    prompt = jobs["key_management_insight"]
    assert "SIGNAL ASYMMETRY" in prompt, "anti-vacuity: the rule moved; re-point this test"
    assert "GARP method" not in prompt
    assert "endorsement" not in prompt.lower()
    assert not guard.violations(prompt)


def test_the_executive_summary_names_the_method_not_a_reader_style():
    """'what a The Growth Hunter investor concludes about how well this fits your style' read
    as suitability ('fits your style') and rendered 'a The …'."""
    cfg = get_persona_config("peter_lynch")
    jobs = {j.label: j.prompt for j in np_.build_narrative_jobs(cfg, _EVIDENCE, _report(3))}
    prompt = jobs["executive_summary_text"]
    assert "Growth Hunter method's criteria" in prompt
    assert "fits your style" not in prompt
    assert "a The " not in prompt


# ── The guard itself must not be vacuous ─────────────────────────────────────

@pytest.mark.parametrize("planted,expected", [
    ("This applies GARP as Peter Lynch taught it.", "Peter Lynch"),
    ("Look for a TENBAGGER here.", "TENBAGGER"),
    ("A wonderful company at a fair price.", "wonderful company"),
    ("Buffett would like it.", "Buffett"),
    ("like Berkshire does", "Berkshire"),
    ("Insiders buy for one reason only.", "buy for one reason"),
    ("Know what you own.", "Know what you own"),
    ("Joel Greenblatt's magic formula.", "Joel Greenblatt"),
    ("A Howard Marks memo on cycles.", "Howard Marks"),
    ("As Warren Buffet says.", "Warren Buffet"),
    ("Buffet's rule.", "Buffet"),
])
def test_the_name_guard_catches_a_planted_name_or_saying(planted: str, expected: str):
    assert expected in guard.violations(planted)


@pytest.mark.parametrize("planted", [
    "Buy when cheap, sell when 30-50% gain reached.",
    "Is conviction high enough for a 10%+ portfolio weight?",
    "Be practical about sell signals too.",
    "What makes NOW the right time?",
    "Avoid if growth stalls completely.",
    "stocks that can grow 10x from your purchase price",
    "You take large, concentrated positions in 8-12 businesses.",
    # Investor framing (fix pass 2026-10-02): the model given tastes of its own.
    "You would rather miss an expensive winner than overpay.",
    "- You distrust narratives, hype, and momentum.",
    "- You respect cash and hard assets.",
    "YOUR INVESTMENT PHILOSOPHY:",
])
def test_the_directive_guard_catches_a_planted_directive(planted: str):
    assert guard.directive_violations(planted)


def test_a_planted_name_in_a_real_prompt_is_caught():
    """End-to-end anti-vacuity: the scan above runs on real rendered prompts, so prove a name
    planted into one of them is found."""
    cfg = get_persona_config("peter_lynch")
    planted = PersonaConfig(
        key=cfg.key, agent_tag=cfg.agent_tag, display_name=cfg.display_name,
        system_prompt="GARP as popularized by Peter Lynch. Find a tenbagger.",
        narrative_lens=cfg.narrative_lens,
    )
    assert guard.violations(planted.system_prompt)


@pytest.mark.parametrize("clean", [
    "Insiders own 0.1% of the shares.",
    "Wood here is a commodity input.",
    "The company is buying back shares.",
    "Could the business hold up through a severe recession?",
    "Never tell the user to buy, sell, or hold.",
    # Method voice, the replacement for the planted investor framing above.
    "The method would rather miss an expensive winner than overpay.",
    "THE METHOD'S INVESTMENT PHILOSOPHY:",
    # Wright's Law is method substance, not a person's saying (see _persona_name_guard).
    "Wright's Law: costs fall by a consistent percentage per cumulative doubling.",
])
def test_the_guards_leave_ordinary_language_alone(clean: str):
    assert not guard.violations(clean)
    assert not guard.directive_violations(clean)


def test_the_misspelling_entries_do_not_double_count_the_real_spelling():
    """'buffet' sits beside 'buffett' in the list; the word boundary must keep it from also
    matching inside the correct spelling (one name, one hit — and the hit is the full name)."""
    assert guard.violations("Warren Buffett") == ["Warren Buffett"]
    assert guard.violations("Buffett's letter") == ["Buffett"]


def test_directives_are_checked_only_before_the_advice_boundary():
    """ADVICE_BOUNDARY legitimately says 'buy, sell, or hold'; the persona's own text above it
    is what must stay clean."""
    text = "Persona body.\n\nADVICE BOUNDARY (never violate):\nNever say buy when cheap."
    assert guard.directive_violations(text) == []
    assert guard.directive_violations("Buy when cheap.\n\nADVICE BOUNDARY") == ["Buy when"]
