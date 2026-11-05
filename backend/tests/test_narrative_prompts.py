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
                         "z_score": 2.7, "event": {"tag": "earnings guidance cut"},
                         "_earnings": {"date": "2026-10-29", "position": "on_last_bar"},
                         "_market": {"change_pct": 0.9}},
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
            "dimensions": [{"name": p, "score": 6.5, "peer_score": 5.8,
                            "source": "deterministic", "peer_source": "industry"}
                           for p in pillars],
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


def test_the_moat_digest_ignores_the_projection():
    """The LLM digest stays on MEASURED growth (2026-10-22): the card's projected pair is a
    display figure, never a thesis input."""
    projected = dict(_AD_MEASURED, projection_basis="bls_output", projection_year=2026,
                     projection_tam=400.0, projection_target_year=2031, projection_target_tam=494.6,
                     projection_rate_pct=4.3, market_size_label="US industry revenue",
                     projection_source_label="Projection: BLS Employment Projections 2025–35, +4.3%/yr")
    assert _structure_line(**projected) == _structure_line(**_AD_MEASURED)
    for gone in ("2031", "400", "495", "4.3", "Projection", "BLS"):
        assert gone not in _structure_line(**projected), gone


# ── Moat Insight / digest provenance (owner decisions 2026-10-28) ────────
# JOBY's Insight claimed "some edge in potential network effects and brand power" and
# "trails significantly in switching costs" — the last against an AI 0.0 and a 5.0
# placeholder peer. Only a MEASURED pillar against a MEASURED industry peer may be an
# edge or a gap; an AI estimate is listed as such and never sets the moat's strength.

def _moat_provenance_shell(dims):
    return {"moat_competition": {"dimensions": dims, "market_dynamics": {}, "competitors": []}}


_MEASURED_EDGE = {"name": "Network Effects", "score": 6.3, "peer_score": 4.1,
                  "source": "deterministic", "peer_source": "industry"}
_MEASURED_BASELINE_PEER = {"name": "Brand Power", "score": 6.0, "peer_score": 4.0,
                           "source": "deterministic", "peer_source": "baseline"}
_AI_PILLAR = {"name": "Switching Costs", "score": 9.0, "peer_score": 5.0,
              "source": "ai_legacy", "peer_source": "baseline"}
_AI_GAP = {"name": "Intangible Assets", "score": 0.0, "peer_score": 5.0,
           "source": "ai_legacy", "peer_source": "ai_estimate"}


def test_durability_edges_and_gaps_use_measured_pillars_and_industry_peers_only():
    prompt = np_._moat_durability_note_prompt(PERSONA, "EVIDENCE", _moat_provenance_shell(
        [_AI_PILLAR, _MEASURED_EDGE, _MEASURED_BASELINE_PEER, _AI_GAP]))
    edges = prompt.split("WHERE IT OUT-DEFENDS PEERS", 1)[1].split("\n", 1)[0]
    gaps = prompt.split("WHERE IT TRAILS PEERS", 1)[1].split("\n", 1)[0]
    assert "Network Effects (6.3 vs peer 4.1)" in edges
    assert "Brand Power" not in edges            # a placeholder peer is no comparison
    assert "Switching Costs" not in edges        # an AI estimate is no edge
    assert "Intangible Assets" not in gaps and "no measured gaps" in gaps
    # The AI's 9.0 never sets the top source or the tone (it would read "WIDE moat").
    assert "TOP MOAT SOURCE: Brand Power (score 6.0/10)" not in prompt
    assert "TOP MOAT SOURCE: Network Effects (score 6.3/10)" in prompt
    assert "WIDE moat" not in prompt and "NO moat" in prompt
    assert "Switching Costs 9.0/10 (AI estimate, not measured)" in prompt
    assert "Network Effects 6.3/10," in prompt  # measured: no tag


def test_durability_with_no_measured_pillar_never_grades_the_moat():
    prompt = np_._moat_durability_note_prompt(
        PERSONA, "EVIDENCE", _moat_provenance_shell([_AI_PILLAR, _AI_GAP]))
    assert "TOP MOAT SOURCE: none measured" in prompt
    assert "NOT MEASURED" in prompt
    for tone in ("WIDE moat", "NARROW moat", "NO moat —"):
        assert tone not in prompt


def test_the_thesis_digest_never_carries_an_ai_pillar_number():
    lines = np_._digest_moat(_moat_provenance_shell(
        [_AI_PILLAR, _MEASURED_EDGE, _MEASURED_BASELINE_PEER]))
    text = "\n".join(lines)
    assert "MOAT (0-10, measured, focal vs industry peer): " in text
    assert "Network Effects 6.3 (peer 4.1)" in text
    assert "Brand Power 6.0," not in text and "Brand Power 6.0" in text
    assert "Brand Power 6.0 (peer" not in text     # placeholder peer dropped
    assert "Switching Costs 9.0" not in text
    assert "Moat pillars not measured (AI estimates, never cite): Switching Costs" in text


def test_a_pre_provenance_report_has_no_measured_moat_comparison():
    """A report stored before 2026-10-28 has no `source` / `peer_source`: nothing is
    narrated as measured (fail closed), every pillar is listed as unmeasured."""
    legacy = [{"name": "Switching Costs", "score": 8.5, "peer_score": 6.0}]
    lines = np_._digest_moat(_moat_provenance_shell(legacy))
    assert not any(ln.startswith("MOAT (0-10") for ln in lines)
    assert any("not measured" in ln for ln in lines)


def test_a_pillar_scored_with_patents_is_never_an_edge_against_patent_free_peers():
    """The industry peer averages never fetch patents / FDA approvals, so a company pillar
    that used one is not like-for-like with its peer and is not narrated as an edge."""
    with_ip = {"name": "Intangible Assets", "score": 7.5, "peer_score": 5.0,
               "source": "deterministic", "peer_source": "industry",
               "drivers": [{"metric": "rd_to_revenue", "sub_score": 7.5},
                           {"metric": "intangibles_to_assets", "sub_score": 6.5},
                           {"metric": "fda_active_approvals", "sub_score": 8.5}]}
    without_ip = dict(with_ip, drivers=with_ip["drivers"][:2])
    edges_of = lambda d: np_._moat_durability_note_prompt(  # noqa: E731
        PERSONA, "EVIDENCE", _moat_provenance_shell([d])
    ).split("WHERE IT OUT-DEFENDS PEERS", 1)[1].split("\n", 1)[0]
    assert "Intangible Assets" not in edges_of(with_ip)
    assert "Intangible Assets (7.5 vs peer 5.0)" in edges_of(without_ip)
    digest = "\n".join(np_._digest_moat(_moat_provenance_shell([with_ip])))
    assert "Intangible Assets 7.5" in digest and "(peer" not in digest


# ── Recent Price Movement: SIZE vs CAUSE prompt, the guard, the digest ───────
# Intel, 2026-10-30: "This is a normal-range move … routine trading inside the stock's
# normal range, not a fundamental change in the business" — after a 20.6% fall into its
# Oct 29 report. The old Typical branch ORDERED that last sentence.

_INTEL_TEXT = (
    "This is a normal-range move, well within the stock's typical monthly band. The drop "
    "came alongside recent news of a weak quarter for Intel. This move appears to be "
    "routine trading inside the stock's normal range, not a fundamental change in the "
    "business."
)


def _intel_pa(**over):
    pa = {
        "change_pct": -20.6, "direction": "down", "window_label": "Last 30 Days",
        "tier": "Notable", "tag": "Notable", "z_score": 0.87, "sigma_daily_pct": 5.03,
        "expected_band_pct": None, "event": None, "_news_headlines": [],
        "_floor": {"reason": "window_move", "session_move_pct": None, "session_date": None},
        "_earnings": {
            "date": "2026-10-29", "position": "on_last_bar", "reported": True,
            "eps_actual": 0.52, "eps_estimate": 0.4066, "eps_surprise_pct": 27.9,
            "eps_result": "beat", "revenue_surprise_pct": -0.3, "revenue_result": "in_line",
            "reaction_pct": None,
            "headlines": [{"date": "2026-10-30", "title": "Why Intel Stock Crashed", "site": "fool.com"}],
        },
        "_earnings_unavailable": False,
        "_market": {"change_pct": 0.9, "start": "2026-09-29", "end": "2026-10-29",
                    "relative_pts": -21.5, "read": "company_specific"},
        "_last_bar": "2026-10-29",
    }
    pa.update(over)
    return pa


def _pa_prompt(pa, evidence="EVIDENCE-BLOCK"):
    return np_._price_action_narrative_prompt(PERSONA, evidence, {"price_action": pa})


def test_the_forced_typical_verdict_is_gone_from_every_branch():
    for tier in ("Typical", "Notable", "Unusual", "Extreme", "typical"):
        prompt = _pa_prompt({"change_pct": 3.0, "direction": "up", "tier": tier,
                             "sigma_daily_pct": 1.2})
        assert "routine trading inside" not in prompt
        assert "not a real change in the business" not in prompt
        assert "CONTEXT only" not in prompt
        assert "normal-range move" not in prompt


def test_the_intel_prompt_carries_size_market_earnings_and_the_timing_caveat():
    prompt = _pa_prompt(_intel_pa())
    assert "SIZE: Large: a move of 20% or more over this window." in prompt
    assert "MARKET: The S&P 500 was up 0.9% over the same days (the stock down 20.6%), so " \
           "the broad market does not explain this move." in prompt
    assert "EARNINGS: Quarterly results reported Oct 29, the date of the chart's last close." in prompt
    assert "EPS 0.52 vs 0.41 estimate (beat by 28%)." in prompt
    assert "Revenue in line with the estimate." in prompt
    assert "The market's reaction may not be in this chart yet." in prompt
    assert "may not be in this chart yet, and do not present the chart's move" in prompt
    assert "never describe it as normal" in prompt                 # the floor rule
    assert "HEADLINES SINCE THE REPORT" in prompt and "Why Intel Stock Crashed" in prompt
    size_line = next(ln for ln in prompt.splitlines() if ln.startswith("SIZE:"))
    assert "normal" not in size_line.lower()


def test_the_prompt_keeps_one_parsable_word_ask_and_embeds_evidence_once():
    ev = "UNIQUE-EVIDENCE-7731"
    prompt = _pa_prompt(_intel_pa(), evidence=ev)
    assert prompt.count(ev) == 1                                   # the cached-context replace
    m = re.search(r"(?:total )?under (\d+) words", prompt)
    assert m and int(m.group(1)) == 60
    assert len(re.findall(r"under \d+ words", prompt)) == 1


@pytest.mark.parametrize("tier,phrase", [
    ("unusual", "Unusually large"), ("EXTREME", "Extremely large"),
    ("Notable", "Larger than this stock usually moves"),
    ("Typical", "An ordinary size for this stock"),
])
def test_size_lines_match_the_tier_case_insensitively(tier, phrase):
    prompt = _pa_prompt({"change_pct": 4.0, "direction": "up", "tier": tier,
                         "sigma_daily_pct": 1.5})
    assert phrase in prompt


def test_size_line_for_a_single_session_floor_and_for_unknown_sigma():
    pa = _intel_pa(_floor={"reason": "single_session", "session_move_pct": -12.3,
                           "session_date": "2026-10-22"})
    assert "a single session down 12.3% on Oct 22" in _pa_prompt(pa)
    assert "Not enough price history" in _pa_prompt(
        {"change_pct": 4.0, "direction": "up", "tier": "Typical", "sigma_daily_pct": None})


def test_earnings_and_market_lines_degrade_honestly():
    none = _pa_prompt({"change_pct": 2.0, "direction": "up", "tier": "Typical",
                       "sigma_daily_pct": 1.0})
    assert "EARNINGS: No quarterly report in this window." in none
    assert "MARKET: Not available. Do not guess" in none
    failed = _pa_prompt({"change_pct": 2.0, "direction": "up", "tier": "Typical",
                         "sigma_daily_pct": 1.0, "_earnings_unavailable": True})
    assert "EARNINGS: Could not be checked" in failed
    pending = _pa_prompt(_intel_pa(_earnings={
        "date": "2026-10-30", "position": "after_last_bar", "reported": False}))
    assert "Quarterly results scheduled for Oct 30, after the chart's last close (Oct 29)" in pending
    assert "not in the data yet" in pending and "reported Oct 30" not in pending
    inside = _pa_prompt(_intel_pa(_earnings=dict(_intel_pa()["_earnings"],
                                                 position="inside_window", reaction_pct=-31.16)))
    assert "inside this window" in inside and "were down 31.2% across the report" in inside
    assert "may not be in this chart yet" not in inside


def test_the_guard_replaces_the_intel_verdict_with_the_facts():
    out = np_.guard_price_action_narrative(_INTEL_TEXT, _intel_pa(), ticker="INTC")
    assert out == (
        "The stock fell 20.6% over the last 30 days while the S&P 500 rose 0.9%, so the move "
        "was specific to the company. It reported quarterly results on Oct 29: EPS beat "
        "estimates by 28% and revenue was in line with the estimate. The market's reaction "
        "to those results may not be in this chart yet."
    )


@pytest.mark.parametrize("bad", [
    "Shares slipped, but this is noise.",
    "The decline sits within its normal trading range.",
    "Nothing statistically unusual here; the business has not changed.",
    "It is not a fundamental shift in the company.",
    "This was routine selling.",
    "Shares stayed inside the usual band.",
    "An unremarkable stretch for the stock.",
    "There was no real change at the company.",
    # review round (2026-10-31): rewordings that used to pass
    "A typical move for Intel, with no single cause confirmed.",
    "It is a normal move for a stock this volatile.",
    "Nothing out of the ordinary drove the decline.",
    "The slide is not a change in fundamentals but a shift in sentiment.",
    "Sentiment rather than a fundamental change in the business drove it.",
    "There was no fundamental shift in the business.",
    "The decline stayed within the stock’s normal 30-day range.",
    "The underlying business is intact.",
])
def test_the_guard_catches_rewordings_of_the_forbidden_verdict(bad):
    assert np_.guard_price_action_narrative(bad, _intel_pa()) != bad


def test_the_guard_leaves_honest_text_and_ordinary_cards_alone():
    honest = ("Intel fell after its Oct 29 results: EPS beat estimates, but the shares had "
              "already slid 20.6% while the S&P 500 rose. The move is company-specific.")
    assert np_.guard_price_action_narrative(honest, _intel_pa()) == honest
    ordinary = {"change_pct": 2.1, "direction": "up", "tier": "Typical", "sigma_daily_pct": 1.4}
    typical_text = "This is a normal-range move for the stock."
    assert np_.guard_price_action_narrative(typical_text, ordinary) == typical_text
    assert np_.guard_price_action_narrative("", _intel_pa()) == ""
    assert np_.guard_price_action_narrative(_INTEL_TEXT, None) == _INTEL_TEXT


@pytest.mark.parametrize("pa", [
    _intel_pa(),
    _intel_pa(change_pct=0.4, direction="flat", _market=None, _earnings=None),
    _intel_pa(_earnings={"date": "2026-10-30", "position": "after_last_bar", "reported": False}),
    _intel_pa(window_label="Since Oct 29", _earnings=dict(_intel_pa()["_earnings"],
              position="inside_window", reaction_pct=-31.16)),
    _intel_pa(_earnings=None, _market={"change_pct": -12.0, "read": "market_wide"}),
    _intel_pa(_earnings=None, _floor=None, _market=None),
])
def test_the_fact_text_is_short_dashless_and_model_free(pa):
    text = np_.price_action_fact_text(pa)
    assert text and len(text.split()) <= 90
    assert not re.search(r"[‒–—―]| - ", text)
    assert not re.search(r"(?i)gemini|google|openai|\bmodel\b|\bAI\b", text)
    assert "routine" not in text.lower() and "normal" not in text.lower()


def test_the_fact_text_variants():
    flat = np_.price_action_fact_text(_intel_pa(change_pct=0.4, direction="flat",
                                                _market=None, _earnings=None, _floor=None))
    assert flat == "The stock was roughly flat over the last 30 days. No single cause is confirmed in the data."
    pending = np_.price_action_fact_text(_intel_pa(_earnings={
        "date": "2026-10-30", "position": "after_last_bar", "reported": False}))
    assert "results are scheduled for Oct 30, and the figures are not in the data yet." in pending
    assert "may not be in this chart" not in pending
    since = np_.price_action_fact_text(_intel_pa(window_label="Since Oct 29", _earnings=dict(
        _intel_pa()["_earnings"], position="inside_window", reaction_pct=-31.16)))
    assert since.startswith("The stock fell 20.6% since Oct 29") and "fell 31.2% across" in since
    floored = np_.price_action_fact_text(_intel_pa(_earnings=None))
    assert floored.endswith("A move this large is significant even for a stock that swings a lot.")


def test_the_digest_carries_the_report_and_the_market():
    line = np_._digest_price_action({"price_action": _intel_pa()})[0]
    assert line == ("PRICE MOVEMENT: -20.6% over Last 30 Days; Notable (z=0.9); "
                    "results Oct 29, after this move; S&P up 0.9%")
    inside = _intel_pa(_earnings=dict(_intel_pa()["_earnings"], position="inside_window"))
    assert "; results Oct 29; S&P" in np_._digest_price_action({"price_action": inside})[0]
    pending = _intel_pa(_earnings={"date": "2026-10-30", "position": "after_last_bar",
                                   "reported": False})
    assert "; results due Oct 30, not out yet;" in np_._digest_price_action(
        {"price_action": pending})[0]
    with_event = _intel_pa(event={"tag": "Earnings Reaction", "date": "Oct 29", "index": 3})
    assert "; catalyst: Earnings Reaction;" in np_._digest_price_action({"price_action": with_event})[0]


def test_the_price_action_job_is_wired_to_the_guard():
    shell = {"price_action": _intel_pa(), "symbol": "INTC"}
    job = next(j for j in np_.build_narrative_jobs(PERSONA, "EVIDENCE", shell)
               if j.label == "price_action_narrative")
    assert job.postprocess is not None
    assert job.postprocess(_INTEL_TEXT) == np_.price_action_fact_text(shell["price_action"])


@pytest.mark.parametrize("text", [
    "The 20.6% fall is far outside its normal range, and it is not routine.",
    "A drop well beyond its usual band; this is not noise.",
    "The move is larger than a typical month for the stock.",
    "Intel fell sharply into its report; the noise-cancelling unit was not a factor.",
])
def test_the_guard_honours_negation_and_contrast(text):
    assert np_.guard_price_action_narrative(text, _intel_pa()) == text


def test_on_a_typical_card_with_a_report_the_size_words_are_true_and_allowed():
    pa = {"change_pct": 3.1, "direction": "up", "window_label": "Last 30 Days",
          "tier": "Typical", "sigma_daily_pct": 2.0, "_floor": None,
          "_earnings": {"date": "2026-10-22", "position": "inside_window", "reported": True,
                        "eps_result": "beat", "eps_surprise_pct": 12.0}}
    honest = ("Shares rose 3.1% after the Oct 22 report, where EPS beat estimates by 12%. "
              "The gain sits within the stock's normal range in size, but it came from the results.")
    assert np_.guard_price_action_narrative(honest, pa) == honest
    verdict = "Shares rose 3.1%, but this is routine trading, not a fundamental change."
    assert np_.guard_price_action_narrative(verdict, pa) != verdict


def test_a_last_bar_report_is_context_not_the_cause_in_the_prompt():
    late = _pa_prompt(_intel_pa())
    sentence_1 = next(ln for ln in late.splitlines() if ln.startswith("Sentence 1:"))
    assert "results reported inside the window" not in sentence_1
    assert "it is not the" in late and "cause of the move shown" in late
    assert "never present the chart's move as its cause or its reaction" in late
    inside = _pa_prompt(_intel_pa(_earnings=dict(_intel_pa()["_earnings"],
                                                 position="inside_window", reaction_pct=-31.2)))
    assert "Sentence 1: the main cause, checked in this order: results reported inside the " \
           "window (EARNINGS)" in inside
    assert "Lead with them" in inside


def test_the_headlines_heading_says_when_results_are_not_out_yet():
    pending = _intel_pa(_earnings={"date": "2026-10-30", "position": "after_last_bar",
                                   "reported": False,
                                   "headlines": [{"date": "2026-10-30",
                                                  "title": "Intel to report after the bell",
                                                  "site": "x.com"}]})
    prompt = _pa_prompt(pending)
    assert "HEADLINES SINCE THE SCHEDULED REPORT DATE (results not out yet" in prompt
    assert "HEADLINES SINCE THE REPORT (" not in prompt


def test_an_unscanned_window_says_not_checked_rather_than_none():
    pa = {"change_pct": 2.0, "direction": "up", "tier": "Typical", "sigma_daily_pct": 1.0,
          "_news_headlines": [], "_news_scanned": False}
    prompt = _pa_prompt(pa)
    assert "RECENT MATCHED HEADLINES: not checked" in prompt
    assert "none in window" not in prompt
    assert "none in window" in _pa_prompt(dict(pa, _news_scanned=True))


def test_the_fact_text_names_a_single_session_floor_and_an_anchored_event():
    retraced = _intel_pa(change_pct=1.5, direction="up", _earnings=None, _market=None,
                         _floor={"reason": "single_session", "session_move_pct": 12.4,
                                 "session_date": "2026-10-23"})
    text = np_.price_action_fact_text(retraced)
    assert text == ("The stock rose 1.5% over the last 30 days. The window includes a "
                    "single session up 12.4% on Oct 23, large even for a stock that "
                    "swings a lot.")
    assert "A move this large" not in text
    anchored = _intel_pa(window_label="Since Oct 15", _earnings=None, _floor=None,
                         event={"tag": "M&A", "date": "Oct 15", "index": 4})
    assert "The move is measured from Oct 15 (M&A)." in np_.price_action_fact_text(anchored)


def test_the_market_line_and_fact_text_when_the_market_moved_far_more():
    pa = _intel_pa(change_pct=2.0, direction="up", _earnings=None, _floor=None,
                   _market={"change_pct": 10.0, "read": "market_larger"})
    assert "far more than the stock moved" in _pa_prompt(pa)
    text = np_.price_action_fact_text(pa)
    assert text.startswith("The stock rose 2.0% over the last 30 days while the S&P 500 rose "
                           "10.0%, far more than the stock moved.")
    assert "in step" not in text


def test_on_a_floored_card_the_bare_size_words_are_refused_too():
    """The floor rule forbids "normal / typical / ordinary" outright; a phrasing no SIZE
    pattern lists ("normal for Intel") is still caught on a floored card, and only there."""
    text = "The 20.6% decline is normal for Intel given how much it swings."
    assert np_.guard_price_action_narrative(text, _intel_pa()) != text
    notable_only = _intel_pa(_floor=None, _earnings=None)
    assert np_.guard_price_action_narrative(text, notable_only) == text


def test_price_lines_never_feed_the_model_a_signed_number():
    """STYLE asks for negatives in words; a "-20.6%" in the prompt came back as "down
    twenty point six percent". The SIZE / MARKET / EARNINGS lines use directional words."""
    pa = _intel_pa(_earnings=dict(_intel_pa()["_earnings"], position="inside_window",
                                  reaction_pct=-31.16),
                   _floor={"reason": "single_session", "session_move_pct": -12.3,
                           "session_date": "2026-10-22"})
    prompt = _pa_prompt(pa)
    for head in ("SIZE:", "MARKET:", "EARNINGS:"):
        line = next(ln for ln in prompt.splitlines() if ln.startswith(head))
        assert not re.search(r"[+\-]\d", line), line
    assert not re.search(r"[+\-]\d", np_.price_action_fact_text(pa))
