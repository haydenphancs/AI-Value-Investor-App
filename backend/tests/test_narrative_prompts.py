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

# Both spellings the prompts use: "total under N words" and the bare "under N words".
# The guard used to match only the first and silently `continue`d past 9 of 18 jobs
# (F12-9) — a cap lowered under one of those asks went unnoticed.
_ASK = re.compile(r"(?:total )?under (\d+) words")
# Jobs whose prompt states its length in sentences only. A numeric ask added to one of
# these is picked up automatically; a job NOT listed here with no parsable ask is a
# failure, never a skip.
_NO_ASK_ALLOWED = {"hidden_market_signals_insight"}


def test_every_word_cap_leaves_headroom_over_its_prompts_ask():
    """`_post_process` hard-cuts at `word_cap` and appends '…'. If the prompt asks for
    'under N words' and the cap is < N, a compliant answer is clipped mid-sentence."""
    short = []
    checked = 0
    for job in np_.build_narrative_jobs(PERSONA, "EVIDENCE", _report(3)):
        m = _ASK.search(job.prompt)
        assert m or job.label in _NO_ASK_ALLOWED, (
            f"{job.label} has no parsable length ask — add one, or list it in _NO_ASK_ALLOWED"
        )
        if not m:
            continue
        checked += 1
        ask = int(m.group(1))
        if job.word_cap < ask:
            short.append((job.label, job.word_cap, ask))
    assert not short, f"word_cap below the prompt's own ask: {short}"
    assert checked >= 15, f"only {checked} jobs carried a parsable ask — regex drift?"


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


# ── E1 (TestFlight 2026-09-16): an unmeasured guidance stance is not narrated ──
#
# "Earnings Call Transcripts" is off the FMP Order Form, so every report's
# `management_guidance` is "unknown" (nothing read a transcript). The card hides
# the badge; the Future Forecast insight must neither state a stance the user
# cannot see nor narrate its absence.

def _forecast_shell(guidance: str, quote=None) -> dict:
    return {
        "revenue_forecast": {
            "cagr": 12.0, "eps_growth": 15.0,
            "management_guidance": guidance,
            "guidance_quote": quote,
            "projections": [{"period": "2026", "revenue_label": "$5.1B",
                             "revenue_yoy_pct": 61.0, "eps_label": "$6.20",
                             "eps_yoy_pct": 40.0}],
            "earnings_track_record": [{"surprise_percent": 5.2}],
            "beat_summary": "Beat 10 of 10",
            "annual_timeline": [],
        },
    }


def test_unknown_guidance_is_not_a_stance_in_the_forecast_insight_prompt():
    prompt = np_._revenue_forecast_insight_prompt(PERSONA, "EVIDENCE", _forecast_shell("unknown"))
    assert "not available" in prompt
    assert "Do not mention guidance" in prompt
    # The old line "STANCE: maintained (raised / maintained / lowered)" and the
    # old trust bullet "read the guidance stance (maintained)" are both gone.
    assert "STANCE: maintained" not in prompt
    assert "guidance stance (" not in prompt
    assert "(raised / maintained / lowered)" not in prompt
    # The track record still anchors the trust read.
    assert "Beat 10 of 10" in prompt


@pytest.mark.parametrize("guidance", ["raised", "maintained", "lowered"])
def test_a_measured_stance_still_reaches_the_forecast_insight_prompt(guidance):
    prompt = np_._revenue_forecast_insight_prompt(PERSONA, "EVIDENCE", _forecast_shell(guidance))
    assert f"MANAGEMENT GUIDANCE STANCE: {guidance} (raised / maintained / lowered)" in prompt
    assert f"read the guidance stance ({guidance})" in prompt
    assert "not available" not in prompt


def test_a_quote_never_rides_an_unknown_stance():
    """Defensive: the overlay nulls the quote with the stance, but the prompt must
    not resurrect one from a hand-edited or legacy row."""
    prompt = np_._revenue_forecast_insight_prompt(
        PERSONA, "EVIDENCE", _forecast_shell("unknown", quote="We are raising guidance."),
    )
    assert "MANAGEMENT GUIDANCE QUOTE" not in prompt


@pytest.mark.parametrize("guidance,expected", [
    ("raised", "guidance raised"),
    ("maintained", "guidance maintained"),
    ("unknown", None),
    ("", None),
    (None, None),
])
def test_the_forecast_digest_only_carries_a_measured_stance(guidance, expected):
    lines = np_._digest_forecast({"revenue_forecast": {
        "cagr": 12.0, "management_guidance": guidance, "beat_summary": "Beat 6 of 8",
    }})
    joined = " ".join(lines)
    if expected is None:
        assert "guidance" not in joined, joined
    else:
        assert expected in joined
    assert "beat 6 of 8" in joined  # the rest of the digest is untouched


def test_the_forecast_insight_summarises_surprises_by_the_median():
    """One quarter against a near-zero consensus (a true +1100%) used to turn nine ~+3%
    quarters into "avg EPS surprise +112.7%" in the model's context."""
    shell = _forecast_shell("unknown")
    shell["revenue_forecast"]["earnings_track_record"] = (
        [{"surprise_percent": 3.0}] * 9 + [{"surprise_percent": 1100.0}]
    )
    prompt = np_._revenue_forecast_insight_prompt(PERSONA, "EVIDENCE", shell)
    assert "median EPS surprise +3.0% over the last 10 quarters" in prompt
    assert "avg EPS surprise" not in prompt


# ── Competitors: the biggest threat is computed over the FULL list ────────────
#
# On a research ("direct") list the rows are most direct first, so the highest threat can
# sit at any index — AVGO: NVDA (9.0) is row 4. The prompts used to slice `[:4]` and the
# digest `[:3]` before anyone looked at the scores, and told the model "the real threat is
# the highest threat_level". "Closest rival" may be claimed only when the report's order
# marker is exactly "direct" (every report stored before 2026-10-01 has none).

def _comp(ticker, score, level, segment=None):
    row = {"name": f"{ticker} Inc", "ticker": ticker, "competitive_score": score,
           "market_share_percent": 0.0, "threat_level": level}
    if segment is not None:
        row["segment"] = segment
    return row


def _moat_shell(order="direct", rows=None):
    rows = rows if rows is not None else [
        _comp("MRVL", 7.1, "high", "Custom AI chips & networking"),
        _comp("QCOM", 5.0, "moderate"),
        _comp("INTC", 4.4, "moderate"),
        _comp("MSFT", 6.2, "moderate"),
        _comp("NVDA", 9.0, "high", "AI data-center networking"),
    ]
    moat = {"dimensions": [{"name": "Switching Costs", "score": 8.5, "peer_score": 6.0}],
            "market_dynamics": {}, "competitors": rows}
    if order is not None:
        moat["competitor_order"] = order
    return {"moat_competition": moat}


_MOAT_PROMPTS = [np_._moat_durability_note_prompt, np_._moat_competitive_insight_prompt]


@pytest.mark.parametrize("build", _MOAT_PROMPTS)
@pytest.mark.parametrize("order", ["direct", "threat", None])
def test_the_biggest_threat_past_the_slice_reaches_every_moat_prompt(build, order):
    prompt = build(PERSONA, "EVIDENCE", _moat_shell(order))
    assert "Biggest threat: NVDA Inc (9.0/10, high)" in prompt
    assert "score 7.1/10, competes in: Custom AI chips & networking" in prompt
    assert ("Closest rival: MRVL Inc" in prompt) == (order == "direct")
    if order == "direct":
        assert "KEY COMPETITORS (most direct first)" in prompt
    else:
        assert "most direct first" not in prompt
        assert "KEY COMPETITORS (highest threat score first)" in prompt
        assert "never call any of them the closest" in prompt
    assert "the real threat (the highest threat_level)" not in prompt


@pytest.mark.parametrize("order", ["direct", "threat", None])
def test_the_digest_names_the_biggest_threat_from_the_full_list(order):
    lines = np_._digest_moat(_moat_shell(order))
    text = "\n".join(lines)
    assert "Biggest threat: NVDA Inc (9.0/10, high)" in text       # row 4, past the [:3]
    assert ("Closest rival: MRVL Inc" in text) == (order == "direct")
    expected = "most direct first" if order == "direct" else "highest threat first"
    assert f"Competitors ({expected}): MRVL Inc 7.1/10 high threat" in text


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), 11.0, -1.0, "high", None])
def test_a_bad_score_never_becomes_the_biggest_threat(bad):
    rows = [_comp("AAA", bad, "high"), _comp("BBB", 4.0, "moderate")]
    for build in _MOAT_PROMPTS:
        prompt = build(PERSONA, "EVIDENCE", _moat_shell("direct", rows))
        assert "Biggest threat: BBB Inc (4.0/10, moderate)" in prompt
    assert "Biggest threat: BBB Inc (4.0/10, moderate)" in "\n".join(
        np_._digest_moat(_moat_shell("direct", rows)))


@pytest.mark.parametrize("rows", [[], "garbage", None, [None, "x", 5]])
def test_malformed_competitor_lists_degrade_to_no_focus_lines(rows):
    shell = _moat_shell("direct", rows)
    shell["moat_competition"]["competitors"] = rows
    for build in _MOAT_PROMPTS:
        prompt = build(PERSONA, "EVIDENCE", shell)
        assert "KEY COMPETITORS (most direct first): none listed" in prompt
        assert "Biggest threat:" not in prompt and "Closest rival:" not in prompt
        assert "Biggest threat line above" not in prompt     # no dangling reference
    assert not any("Competitors" in ln or "Biggest" in ln
                   for ln in np_._digest_moat(shell))


def test_a_tie_for_the_biggest_threat_names_the_first_row():
    rows = [_comp("AAA", 6.0, "moderate"), _comp("BBB", 6.0, "moderate")]
    assert "Biggest threat: AAA Inc (6.0/10, moderate)" in "\n".join(
        np_._digest_moat(_moat_shell("threat", rows)))


# ── Market structure: a measured growth rate, never a "CAGR" forecast (2026-10-22) ──
#
# RKLB's stored bear case read the A&D "industry CAGR -0.2%" + "declining" as a shrinking
# market: the rate was a backward 2017→2024 Census change (a peak-to-trough window), labelled
# as a 5-year CAGR. Owner decision "Measured only": the digest names the rate for what it is —
# a nominal US industry rate over its measured window — and the lifecycle as a reading OF it.

def _structure_line(**md) -> str:
    lines = np_._digest_moat({"moat_competition": {"market_dynamics": md}})
    return next((ln for ln in lines if "Market structure" in ln), "")


_AD_MEASURED = dict(
    concentration="fragmented", concentration_basis="revenue_share", cagr_5yr=0.9,
    cagr_start_year=2017, cagr_end_year=2024, lifecycle_phase="mature",
    current_tam=342.8, future_tam=359.3, current_year="2024", future_year="2029",
    tam_basis="measured", tam_year=2024, tam_scope="us",
)


def test_the_moat_digest_line_is_the_owner_approved_wording():
    assert _structure_line(**_AD_MEASURED) == (
        "  Market structure: fragmented by revenue share of listed companies; "
        "US industry growth +0.9%/yr nominal 2017-24; lifecycle by that rate: mature"
    )


def test_the_moat_digest_scopes_the_growth_rate():
    line = _structure_line(**{**_AD_MEASURED, "cagr_5yr": -0.2})
    for want in ("%/yr", "2017", "24", "nominal", "US", "-0.2"):
        assert want in line, want
    assert "CAGR" not in line and "declining" not in line
    # Never a TAM figure: neither the measured size nor the legacy projection.
    for figure in ("342", "343", "359", "2029"):
        assert figure not in line, figure


@pytest.mark.parametrize("conc", ["unknown", "Unknown", " UNKNOWN ", "", None, 3, ["x"]])
def test_the_moat_digest_skips_an_unknown_concentration(conc):
    line = _structure_line(**{**_AD_MEASURED, "concentration": conc})
    assert line.startswith("  Market structure: US industry growth +0.9%/yr"), line
    assert "unknown" not in line.lower() and "listed companies" not in line


@pytest.mark.parametrize("basis,want", [
    ("revenue_share", "oligopoly by revenue share of listed companies"),
    ("market_cap", "oligopoly by market cap of listed companies"),
    (None, "Market structure: oligopoly; "),          # a report stored before the key
    ("other", "Market structure: oligopoly; "),
])
def test_the_concentration_names_what_it_was_measured_on(basis, want):
    assert want in _structure_line(**{**_AD_MEASURED, "concentration": "oligopoly",
                                      "concentration_basis": basis})


@pytest.mark.parametrize("rate,want", [
    (-0.04, "+0.0%/yr"),        # never "-0.0" (it reads as a decline)
    (-0.0, "+0.0%/yr"),
    (0, "+0.0%/yr"),
    (-1.0, "-1.0%/yr"),
    (15.04, "+15.0%/yr"),
])
def test_the_growth_rate_is_rounded_without_a_negative_zero(rate, want):
    line = _structure_line(**{**_AD_MEASURED, "cagr_5yr": rate})
    assert want in line and "-0.0" not in line


@pytest.mark.parametrize("rate", [None, float("nan"), float("inf"), -float("inf"), True, False,
                                  "0.9", 5000.0, {"v": 1}])
def test_no_usable_rate_says_neither_growth_nor_a_lifecycle(rate):
    """Without a rate the phase is the classifier's no-signal default: naming it would invent
    a lifecycle reading."""
    line = _structure_line(**{**_AD_MEASURED, "cagr_5yr": rate})
    assert "growth" not in line and "lifecycle" not in line
    assert "nan" not in line.lower() and "inf" not in line.lower()
    assert line == "  Market structure: fragmented by revenue share of listed companies"


@pytest.mark.parametrize("start,end,want", [
    (2017, 2024, " 2017-24;"),
    ("2017", "2024", " 2017-24;"),
    (2017.0, 2024.0, " 2017-24;"),
    (1998, 2003, " 1998-2003;"),              # across a century: the 4-digit end
    (2099, 2100, " 2099-2100;"),
    (2024, 2017, "nominal;"),                 # start after end: no window
    (2024, 2024, "nominal;"),
    (None, 2024, "nominal;"),
    (float("nan"), 2024, "nominal;"),
    (True, 2024, "nominal;"),
    ("FY17", "2024", "nominal;"),
    (2017.5, 2024, "nominal;"),
    (2568, 2569, "nominal;"),                 # a Buddhist-calendar year is not a window
])
def test_the_growth_window_is_stated_only_when_valid(start, end, want):
    line = _structure_line(**{**_AD_MEASURED, "cagr_start_year": start, "cagr_end_year": end})
    assert want in line, line


def test_a_legacy_global_row_is_not_called_a_us_rate():
    line = _structure_line(**{**_AD_MEASURED, "tam_scope": "global", "tam_basis": None})
    assert "; industry growth +0.9%/yr nominal" in line and "US industry" not in line


def test_the_lifecycle_rides_only_beside_the_rate():
    line = _structure_line(**{**_AD_MEASURED, "lifecycle_phase": "secular_growth",
                              "cagr_5yr": 18.0})
    assert line.endswith("; lifecycle by that rate: secular growth")
    for bad in (None, "", 5, "  "):
        assert "lifecycle" not in _structure_line(**{**_AD_MEASURED, "lifecycle_phase": bad})


@pytest.mark.parametrize("md", [None, "", "garbage", [], {}])
def test_a_missing_market_block_gives_no_structure_line(md):
    lines = np_._digest_moat({"moat_competition": {"market_dynamics": md}})
    assert not any("Market structure" in ln for ln in lines)


def _fully_populated_report() -> dict:
    """Every digest module at realistic full length: long segment names, five pillars, five
    competitors with segments, four insider rows, two material macro risks, a published
    estimate and analyst momentum, Congress and short selling."""
    pillars = ["Switching Costs", "Network Effects", "Brand Power", "Cost Advantage",
               "Intangible Assets"]
    return {
        "price_action": {"change_pct": -12.4, "window_label": "30 days", "tier": "unusual",
                         "z_score": 2.7, "event": {"tag": "earnings guidance cut"}},
        "revenue_engine": {"reporting_currency": "USD", "segments": [
            {"name": "Launch Services and Space Systems Integration", "current_revenue": 245_000.0,
             "previous_revenue": 180_000.0},
            {"name": "Space Systems Components and Spacecraft Buses", "current_revenue": 190_000.0,
             "previous_revenue": 150_000.0},
            {"name": "Government Mission Solutions and Engineering", "current_revenue": 120_000.0,
             "previous_revenue": 95_000.0},
        ]},
        "fundamental_metrics": [
            {"title": f"Card {c}", "star_rating": 3,
             "metrics": [{"label": f"Metric {c}{m} (1.20x sector avg 57.4%)", "value": "68.82%"}
                         for m in range(6)]}
            for c in range(4)
        ],
        "overall_assessment": {"average_rating": 3.4, "strong_count": 9, "weak_count": 6},
        "revenue_forecast": {"cagr": 31.2, "eps_growth": 44.8, "management_guidance": "raised",
                             "beat_summary": "Beat 6 of 8"},
        "insider_data": {
            "sentiment": "net selling", "timeframe": "last 12 months",
            "transactions": [{"type": t, "count": 14, "value": "$12.4M"}
                             for t in ("Informative Buy", "Informative Sell",
                                       "Planned Sell (10b5-1)", "Option Exercise")],
            "capital_allocation": {
                "buyback_status": "none", "dividend_yield": 0.0, "share_count_change": 9.8,
                "data_points": [{"period": "Q3 2024", "shares_outstanding": 480.0},
                                {"period": "Q2 2026", "shares_outstanding": 527.0}],
            },
        },
        "moat_competition": {
            "dimensions": [{"name": p, "score": 6.5, "peer_score": 5.8} for p in pillars],
            "market_dynamics": {**_AD_MEASURED, "cagr_5yr": -0.2},
            "competitor_order": "direct",
            "competitors": [
                _comp(t, s, lv, "Small-launch and medium-lift orbital launch services")
                for t, s, lv in (("FLY", 6.1, "moderate"), ("LUNR", 5.4, "moderate"),
                                 ("BA", 7.8, "high"), ("LMT", 8.2, "high"),
                                 ("NOC", 7.0, "high"))
            ],
        },
        "macro_data": {"overall_threat_level": "high", "risk_factors": [
            {"title": "US defense budget continuing resolution delays", "severity": "high",
             "impact": 0.8},
            {"title": "Launch-site range congestion and FAA licensing backlog",
             "severity": "elevated", "impact": 0.6},
        ]},
        "wall_street_consensus": {
            "rating": "strong_buy", "current_price": 58.25, "target_price": 72.0,
            "momentum_upgrades": 7, "momentum_downgrades": 3,
            "caydex_fair_value": {"status": "ok", "fair_value": 41.37, "range_low": 33.1,
                                  "range_high": 52.9},
        },
        "hidden_market_signals": {
            "congress": {"num_buyers": 4, "num_sellers": 2, "net_direction": "buying",
                         "period": "Last 12 Months"},
            "short_interest": {"percent_of_float": 14.6, "days_to_cover": 5.3, "change_3m": -8.0},
        },
    }


def test_a_fully_populated_digest_keeps_its_later_lines_within_the_verdicts_cap():
    """The longer market-structure line (owner wording, ~150 chars instead of ~40) must not
    push MACRO / WALL STREET / HIDDEN SIGNALS — the last three modules — out of the
    executive summary's 2,200-character verdicts block. Real formatters, no stubs."""
    report = _fully_populated_report()
    digest = np_.build_module_digest(report)
    # Anti-vacuity: every module produced its line(s).
    for head in ("PRICE MOVEMENT", "REVENUE ENGINE", "FUNDAMENTALS & GROWTH", "FUTURE FORECAST",
                 "INSIDER (", "MOAT (0-10", "  Market structure: fragmented by revenue share",
                 "MACRO threat: high", "WALL STREET:", "HIDDEN SIGNALS:"):
        assert head in digest, head
    budgeted = [ln for ln in digest.splitlines() if not ln.upper().startswith("FUNDAMENTALS")]
    assert sum(len(ln) + 1 for ln in budgeted) <= np_._VERDICTS_CHAR_CAP, \
        "a fully populated digest no longer fits: the trailing modules would fall off"
    prompt = np_._executive_summary_text_prompt(PERSONA, "EVIDENCE", report)
    block = prompt.split("REPORT VERDICTS", 1)[1]
    for later in ("MACRO threat: high", "WALL STREET:", "HIDDEN SIGNALS:"):
        assert later in block, later
    assert "US industry growth -0.2%/yr nominal 2017-24" in block
    assert "CAGR -0.2" not in block and "declining" not in block


def test_the_moat_digest_names_a_peer_set_concentration_as_peers():
    from app.services.agents.narrative_prompts import _market_structure_bits

    bits = _market_structure_bits({"concentration": "oligopoly", "concentration_basis": "peer_revenue_share"})
    assert bits[0] == "oligopoly by revenue share of a peer set"
