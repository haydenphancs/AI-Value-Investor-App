"""Shape and length contracts of the Stage-B narrative pipeline (`narrative_prompts.py`).

The 2,655-line module had no dedicated test; eight files each pinned one helper. This
covers the contracts a prompt rewrite is most likely to break silently:

  * the job LIST: 12 fixed jobs + 2 per critical factor (10-22), never the "14" / "15"
    the comments used to claim;
  * every job's word cap is >= the length its own prompt asks for — a cap below the ask
    hard-cuts a compliant answer with an ellipsis (revenue_engine sat at 22 under a
    25-word ask; wall_street at 45 under a 45-word ask);
  * the Stage-A prompt names every top-level key the assembler reads;
  * the two post-assembly syntheses only OVERWRITE when the model returned a complete
    result (both thesis sides; >= 2 valid factors) — a half answer keeps Stage A's;
  * the executive summary reads the assembled report's verdicts (it took `shell` and never
    read it).

No network.
"""

from __future__ import annotations

import json
import re

import pytest

from app.services.agents import narrative_prompts as np_
from app.services.agents.persona_config import get_persona_config

PERSONA = get_persona_config("warren_buffett")


def _report(n_factors: int = 3) -> dict:
    """A minimal assembled report with every module a job is conditioned on."""
    return {
        "symbol": "AAPL", "company_name": "Apple Inc.",
        "overall_assessment": {"label": "Solid", "score": 7.2},
        "moat_competition": {"dimensions": [], "market_dynamics": "", "competitors": []},
        "macro_data": {"overall_threat_level": "moderate", "risk_factors": []},
        "price_action": {"change_pct": 4.2, "window_label": "5 days"},
        "revenue_engine": {"segments": [{"name": "iPhone", "share": 52.0}]},
        "revenue_forecast": {"management_guidance": ""},
        "hidden_market_signals": {"signals": []},
        "key_management": {"ownership_insight": ""},
        "wall_street_consensus": {"consensus": "buy"},
        "_scoring_inputs": {"insider": {"net_buys": 3}},
        "critical_factors": [
            {"title": f"Factor {i}", "severity": "high", "description": "", "watch": ""}
            for i in range(n_factors)
        ],
    }


# ── job list shape ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("n_factors,expected", [(0, 12), (1, 14), (3, 18), (5, 22)])
def test_job_count_is_twelve_fixed_plus_two_per_critical_factor(n_factors, expected):
    jobs = np_.build_narrative_jobs(PERSONA, "EVIDENCE", _report(n_factors))
    assert len(jobs) == expected, [j.label for j in jobs]


def test_the_fixed_jobs_are_the_documented_twelve():
    labels = {j.label for j in np_.build_narrative_jobs(PERSONA, "EVIDENCE", _report(0))}
    assert labels == {
        "executive_summary_text", "overall_assessment_text", "moat_durability_note",
        "moat_competitive_insight", "macro_intelligence_brief", "price_action_narrative",
        "revenue_engine_analysis_note", "revenue_forecast_insight",
        "hidden_market_signals_insight", "key_management_insight", "insider_key_insight",
        "wall_street_insight",
    }


def test_optional_modules_skip_their_job_rather_than_prompting_on_nothing():
    r = _report(0)
    r["hidden_market_signals"] = None
    r["revenue_engine"] = {"segments": []}
    labels = {j.label for j in np_.build_narrative_jobs(PERSONA, "EVIDENCE", r)}
    assert "hidden_market_signals_insight" not in labels
    assert "revenue_engine_analysis_note" not in labels


# ── caps vs asks ──────────────────────────────────────────────────────────────

_ASK = re.compile(r"total under (\d+) words")


def test_every_word_cap_leaves_headroom_over_its_prompts_ask():
    """`_post_process` hard-cuts at `word_cap` and appends '…'. If the prompt asks for
    'under N words' and the cap is < N, a compliant answer is clipped mid-sentence."""
    short = []
    for job in np_.build_narrative_jobs(PERSONA, "EVIDENCE", _report(3)):
        m = _ASK.search(job.prompt)
        if not m:
            continue
        ask = int(m.group(1))
        if job.word_cap < ask:
            short.append((job.label, job.word_cap, ask))
    assert not short, f"word_cap below the prompt's own ask: {short}"


def test_post_process_caps_words_and_marks_the_cut():
    assert np_._post_process("one two three four five", word_cap=3) == "one two three…"
    assert np_._post_process("one two three", word_cap=3) == "one two three"


# ── Stage A prompt names every key the assembler reads ────────────────────────

def test_stage_a_prompt_carries_the_eleven_top_level_keys():
    prompt = np_.build_stage_a_prompt(PERSONA, "Apple Inc.", "AAPL", "EVIDENCE", deep_findings="")
    for key in ("quality_score", "core_thesis", "revenue_forecast", "insider_analysis",
                "key_management", "price_action", "revenue_engine", "moat_competition",
                "macro_data", "wall_street", "critical_factors"):
        assert f'"{key}"' in prompt, key
    fallback = np_.stage_a_fallback()
    for key in ("quality_score", "core_thesis", "moat_competition", "macro_data", "critical_factors"):
        assert key in fallback, f"fallback shell lacks {key}"


def test_parse_stage_a_response_tolerates_fenced_and_broken_json():
    good = json.dumps({"quality_score": 71, "core_thesis": {"bull_case": [], "bear_case": []}})
    assert np_.parse_stage_a_response(good)["quality_score"] == 71
    assert np_.parse_stage_a_response("```json\n" + good + "\n```")["quality_score"] == 71
    assert np_.parse_stage_a_response("not json at all") is None
    assert np_.parse_stage_a_response("") is None


# ── the syntheses overwrite only on a COMPLETE result ────────────────────────

class _JsonGem:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def generate_json(self, **kwargs):
        self.calls += 1
        return {"text": json.dumps(self.payload) if not isinstance(self.payload, str) else self.payload}


@pytest.mark.asyncio
async def test_thesis_synthesis_keeps_stage_a_when_one_side_is_empty():
    report = _report(1)
    report["core_thesis"] = {"bull_case": ["stage-a bull"], "bear_case": ["stage-a bear"]}
    gem = _JsonGem({"bull_case": ["new bull"], "bear_case": []})
    await np_.synthesize_core_thesis(report, PERSONA, gem, "EVIDENCE")
    assert gem.calls == 1, "the digest must be non-empty for the call to happen (anti-vacuity)"
    assert report["core_thesis"] == {"bull_case": ["stage-a bull"], "bear_case": ["stage-a bear"]}


@pytest.mark.asyncio
async def test_thesis_synthesis_overwrites_on_a_complete_result():
    report = _report(1)
    report["core_thesis"] = {"bull_case": ["stage-a bull"], "bear_case": ["stage-a bear"]}
    gem = _JsonGem({"bull_case": ["new bull 1", "new bull 2"], "bear_case": ["new bear 1", "new bear 2"]})
    await np_.synthesize_core_thesis(report, PERSONA, gem, "EVIDENCE")
    assert report["core_thesis"]["bull_case"][0] == "new bull 1"
    assert report["core_thesis"]["bear_case"][0] == "new bear 1"


@pytest.mark.asyncio
async def test_thesis_synthesis_keeps_stage_a_on_unparseable_json():
    report = _report(1)
    report["core_thesis"] = {"bull_case": ["b"], "bear_case": ["r"]}
    await np_.synthesize_core_thesis(report, PERSONA, _JsonGem("{{not json"), "EVIDENCE")
    assert report["core_thesis"] == {"bull_case": ["b"], "bear_case": ["r"]}


@pytest.mark.asyncio
async def test_critical_factor_synthesis_needs_at_least_two_valid_factors():
    report = _report(2)
    before = [dict(f) for f in report["critical_factors"]]
    gem = _JsonGem({"critical_factors": [{"title": "Only one", "severity": "high",
                                          "description": "d", "watch": "w"}]})
    await np_.synthesize_critical_factors(report, PERSONA, gem, "EVIDENCE")
    assert report["critical_factors"] == before, "one factor must not replace Stage A's set"


# ── executive summary reads the report ───────────────────────────────────────

def test_executive_summary_prompt_carries_the_module_verdicts():
    report = _report(1)
    prompt = np_._executive_summary_text_prompt(PERSONA, "EVIDENCE", report)
    assert "REPORT VERDICTS" in prompt
    # The price-action digest line is the anti-vacuity anchor: change_pct=4.2 must surface.
    assert "4.2" in prompt


def test_executive_summary_prompt_survives_a_bare_shell():
    prompt = np_._executive_summary_text_prompt(PERSONA, "EVIDENCE", {})
    assert "Executive Summary" in prompt and "REPORT VERDICTS" not in prompt


def test_executive_summary_verdicts_are_cut_on_line_boundaries(monkeypatch):
    """The digest is ~9 fixed-order module lines; a character cut landed mid-number inside
    MOAT/MACRO and silently dropped every module after it. Whole lines only."""
    lines = [f"MODULE{i}: verdict {i} " + ("x" * 280) for i in range(12)]  # ~300 chars each
    monkeypatch.setattr(np_, "build_module_digest", lambda shell: "\n".join(lines))
    prompt = np_._executive_summary_text_prompt(PERSONA, "EVIDENCE", {"any": "shell"})
    block = prompt.split("REPORT VERDICTS", 1)[1]
    kept = [l for l in lines if l in block]
    assert 1 <= len(kept) < len(lines), "the cut must drop whole trailing lines"
    for line in lines[len(kept):]:
        assert line[:40] not in block, "a dropped line must not appear partially"
    assert sum(len(l) + 1 for l in kept) <= np_._VERDICTS_CHAR_CAP


def test_executive_summary_verdicts_skip_the_fundamentals_line(monkeypatch):
    """EVIDENCE already carries every fundamentals ratio; spending ~450 chars of the
    verdict budget on them is what pushed MACRO / WALL STREET / HIDDEN SIGNALS off the
    end on a fully populated report."""
    digest = "FUNDAMENTALS & GROWTH: ROE 22% | margin 30%\nMOAT: wide\nMACRO threat: high\nWALL STREET: buy 30/35\nHIDDEN SIGNALS: insiders selling"
    monkeypatch.setattr(np_, "build_module_digest", lambda shell: digest)
    prompt = np_._executive_summary_text_prompt(PERSONA, "EVIDENCE", {"any": "shell"})
    block = prompt.split("REPORT VERDICTS", 1)[1]
    assert "FUNDAMENTALS & GROWTH" not in block
    for line in ("MOAT: wide", "MACRO threat: high", "WALL STREET: buy 30/35", "HIDDEN SIGNALS"):
        assert line in block
    assert np_._VERDICTS_CHAR_CAP >= 2000, "sized to a fully populated digest (~1.8-2k chars)"
