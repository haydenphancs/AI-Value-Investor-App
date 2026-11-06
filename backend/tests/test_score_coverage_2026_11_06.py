"""The line a report carries when its score rests on little of a style's weight.

`compute_quality_score` renormalizes over what was measured, so a company with no peer group
printed a confident headline with most of the style's weight unrated: BNY read 70 (Strong) under
the Buffett weights on 42% of them. Owner decision 2026-11-06, "Short line under 75%": the score
stays, and a code-owned line ends the Executive Summary. Rated here means exactly what the score
counted, so a malformed vital is unrated in both.
"""

import itertools

import pytest

from app.services import pdf_report_service as pdf
from app.services.agents import narrative_prompts as np_
from app.services.agents import persona_scoring as ps
from app.services.agents.persona_config import get_persona_config

BUFFETT, BURRY, ACKMAN, LYNCH, WOOD = (
    "warren_buffett", "michael_burry", "bill_ackman", "peter_lynch", "cathie_wood",
)
ALL_VITALS = tuple(ps.PERSONA_WEIGHTS[BUFFETT])


def _vitals(*unrated, score=6.0):
    """Every vital rated, minus `unrated`, which carry the builder's explicit None."""
    return {
        name: {"score": {"value": None if name in unrated else score}}
        for name in ALL_VITALS
    }


def _report(*unrated, score=6.0):
    return {"_scoring_inputs": _vitals(*unrated, score=score)}


NO_PEERS = ("moat", "financial_health", "valuation", "wall_street")


# ── the five measured companies ──────────────────────────────────────────────

@pytest.mark.parametrize("persona, share", [
    (BUFFETT, 42), (BURRY, 43), (ACKMAN, 54), (LYNCH, 65), (WOOD, 70),
])
def test_a_company_with_no_peers_reads_as_measured(persona, share):
    assert f"rests on {share}% of what this style weighs" in ps.score_coverage_note(
        persona, _report(*NO_PEERS),
    )


@pytest.mark.parametrize("persona, share", [
    (BUFFETT, 37), (BURRY, 34), (ACKMAN, 50), (LYNCH, 61), (WOOD, 66),
])
def test_a_foreign_bank_with_no_insider_reading_reads_as_measured(persona, share):
    note = ps.score_coverage_note(persona, _report(*NO_PEERS, "insider"))
    assert f"rests on {share}% of what this style weighs" in note


def test_the_owners_wording():
    assert ps.score_coverage_note(BUFFETT, _report(*NO_PEERS)) == (
        "This score rests on 42% of what this style weighs. "
        "Moat, Financial Health and Valuation are not rated."
    )


def test_each_style_names_what_it_weighs_most():
    assert "Valuation, Financial Health and Moat are" in ps.score_coverage_note(
        BURRY, _report(*NO_PEERS),
    )
    assert "Moat, Valuation and Financial Health are" in ps.score_coverage_note(
        WOOD, _report(*NO_PEERS),
    )


def test_the_names_follow_the_weight_not_the_order_of_the_table():
    """Lynch's table lists Moat (0.04) before Macro (0.05)."""
    assert ps.score_coverage_note(LYNCH, _report("forecast", "moat", "macro")).endswith(
        "Forecast, Macro and Moat are not rated."
    )


def test_the_score_itself_does_not_move():
    """BNY's measured vitals. The line is a disclosure: the headline stays 70.3."""
    vitals = _vitals(*NO_PEERS)
    for name, value in (
        ("profitability", 7.2), ("capital_allocation", 9.7), ("revenue", 8.1),
        ("insider", 3.0), ("forecast", 5.6), ("macro", 3.6),
    ):
        vitals[name] = {"score": {"value": value}}
    report = {"_scoring_inputs": vitals}
    assert ps.compute_quality_score(BUFFETT, report) == 70.3
    assert ps.score_coverage_note(BUFFETT, report)
    assert ps.compute_quality_score(BUFFETT, report) == 70.3


# ── where the line does not fire ─────────────────────────────────────────────

@pytest.mark.parametrize("persona", list(ps.PERSONA_WEIGHTS))
def test_every_vital_rated_is_silent(persona):
    assert ps.score_coverage_note(persona, _report()) == ""


@pytest.mark.parametrize("persona", list(ps.PERSONA_WEIGHTS))
def test_a_normal_report_is_silent(persona):
    """No report has carried `wall_street` since 2026-09-02: that alone is every report."""
    assert ps.score_coverage_note(persona, _report("wall_street")) == ""


@pytest.mark.parametrize("persona", list(ps.PERSONA_WEIGHTS))
def test_a_large_bank_with_peers_is_silent(persona):
    """JPM and Berkshire, 77-88%: they already carry the financial line."""
    assert ps.score_coverage_note(persona, _report("financial_health", "wall_street")) == ""


@pytest.mark.parametrize("report", [
    None, {}, [], "text", 7,
    {"_scoring_inputs": None}, {"_scoring_inputs": {}},
    {"_scoring_inputs": []}, {"_scoring_inputs": "x"},
])
def test_a_report_with_no_scores_to_judge_is_silent(report):
    assert ps.score_coverage_note(BUFFETT, report) == ""


# ── the boundary ─────────────────────────────────────────────────────────────

def test_exactly_at_the_floor_is_silent():
    assert ps.PERSONA_WEIGHTS[WOOD]["revenue"] == 0.25
    assert ps.score_coverage_note(WOOD, _report("revenue")) == ""


def test_one_point_under_the_floor_fires():
    assert ps.score_coverage_note(BUFFETT, _report("moat", "wall_street")) == (
        "This score rests on 74% of what this style weighs. Moat is not rated."
    )


def test_two_names_read_as_a_pair():
    note = ps.score_coverage_note(BUFFETT, _report("financial_health", "insider", "wall_street"))
    assert note.endswith("Financial Health and Insider Activity are not rated.")


@pytest.mark.parametrize("persona", list(ps.PERSONA_WEIGHTS))
def test_every_combination_of_vitals_fires_exactly_below_the_floor(persona):
    """In whole hundredths, the weights' own grid: a float sum must never decide it."""
    weights = ps.PERSONA_WEIGHTS[persona]
    hundredths = {name: round(weight * 100) for name, weight in weights.items()}
    assert sum(hundredths.values()) == 100
    for size in range(len(ALL_VITALS) + 1):
        for unrated in itertools.combinations(ALL_VITALS, size):
            rated = 100 - sum(hundredths[name] for name in unrated)
            note = ps.score_coverage_note(persona, _report(*unrated))
            assert bool(note) == (rated < 75), (unrated, rated)
            if 0 < rated < 75:
                assert f"rests on {rated}% " in note, (unrated, rated)


# ── almost no vitals, and none ───────────────────────────────────────────────

def test_a_report_with_almost_no_vitals():
    only_macro = tuple(name for name in ALL_VITALS if name != "macro")
    assert ps.score_coverage_note(BUFFETT, _report(*only_macro)) == (
        "This score rests on 4% of what this style weighs. "
        "Moat, Financial Health and Valuation are not rated."
    )


def test_a_report_with_no_vital_at_all():
    note = ps.score_coverage_note(BUFFETT, _report(*ALL_VITALS))
    assert note == (
        "None of what this style weighs could be rated, so this score is not a rating of the "
        "company."
    )
    assert "0%" not in note
    assert ps.compute_quality_score(BUFFETT, _report(*ALL_VITALS)) == 0.0


def test_only_style_signals_counts_as_no_vital():
    report = {"_scoring_inputs": {"_style_signals": {"roe": 12.0}}}
    assert ps.score_coverage_note(BUFFETT, report).startswith("None of what this style weighs")


def test_a_legacy_report_is_read_through_its_old_key():
    report = {"key_vitals": _vitals(*NO_PEERS)}
    assert "42%" in ps.score_coverage_note(BUFFETT, report)


def test_an_unknown_style_reads_equal_weights():
    assert "rests on 60% of" in ps.score_coverage_note(
        "nobody", _report("moat", "valuation", "financial_health", "wall_street"),
    )


# ── a malformed vital ────────────────────────────────────────────────────────

BAD_VALUES = [
    float("nan"), float("inf"), float("-inf"), True, False, "7.5", "", [], {}, 10 ** 400,
]


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_a_malformed_vital_is_unrated(bad):
    assert ps._vital_score({"moat": {"score": {"value": bad}}}, "moat") is None


@pytest.mark.parametrize("bad", BAD_VALUES, ids=repr)
def test_a_malformed_vital_moves_the_score_and_the_line_together(bad):
    """NaN used to survive `min(10.0, nan)` as 10.0: top marks, and counted as rated."""
    broken = _report(*NO_PEERS[1:], score=4.0)
    broken["_scoring_inputs"]["moat"] = {"score": {"value": bad}}
    assert ps.compute_quality_score(BUFFETT, broken) == 40.0
    assert ps.compute_quality_score(BUFFETT, _report(*NO_PEERS, score=4.0)) == 40.0
    assert ps.score_coverage_note(BUFFETT, broken) == ps.score_coverage_note(
        BUFFETT, _report(*NO_PEERS),
    )


@pytest.mark.parametrize("vital", ["x", 7, 7.5, [], True, None], ids=repr)
def test_a_vital_that_is_not_a_dict_is_unrated(vital):
    report = _report(*NO_PEERS[1:])
    report["_scoring_inputs"]["moat"] = vital
    assert "42%" in ps.score_coverage_note(BUFFETT, report)


@pytest.mark.parametrize("name, key", [
    ("valuation", "status"), ("moat", "overall_rating"), ("financial_health", "level"),
])
@pytest.mark.parametrize("bad", [5, True, ["wide"], {"a": 1}], ids=repr)
def test_a_malformed_legacy_label_is_unrated_and_does_not_raise(name, key, bad):
    assert ps._vital_score({name: {key: bad}}, name) is None


def test_a_good_legacy_label_still_reads():
    assert ps._vital_score({"valuation": {"status": "Overpriced"}}, "valuation") == 3.0
    assert ps._vital_score({"moat": {"overall_rating": "wide"}}, "moat") == 8.5
    assert ps._vital_score({"financial_health": {"level": "weak"}}, "financial_health") == 3.5


def test_a_scale_value_out_of_band_stays_rated_and_clipped():
    report = _report()
    report["_scoring_inputs"]["moat"] = {"score": {"value": 400.0}}
    assert ps.score_coverage_note(BUFFETT, report) == ""
    assert ps.compute_quality_score(BUFFETT, report) > ps.compute_quality_score(BUFFETT, _report())


@pytest.mark.parametrize("inputs", [[], "x", 7])
def test_scoring_inputs_of_the_wrong_type_score_nothing(inputs):
    assert ps.compute_quality_score(BUFFETT, {"_scoring_inputs": inputs}) == 0.0


# ── the names ────────────────────────────────────────────────────────────────

def test_every_vital_but_the_one_on_no_report_has_a_reader_name():
    assert set(ALL_VITALS) - set(ps._VITAL_READER_NAMES) == {"wall_street"}
    assert set(ps._VITAL_READER_NAMES) <= set(ALL_VITALS)


@pytest.mark.parametrize("persona", list(ps.PERSONA_WEIGHTS))
def test_the_vital_on_no_report_is_never_named(persona):
    only_it_named = ps.score_coverage_note(
        persona, _report(*tuple(name for name in ALL_VITALS if name != "macro")),
    )
    for word in ("wall", "street", "analyst"):
        assert word not in only_it_named.lower()


@pytest.mark.parametrize("persona", list(ps.PERSONA_WEIGHTS))
def test_the_list_stays_short_and_plain(persona):
    note = ps.score_coverage_note(persona, _report(*tuple(n for n in ALL_VITALS if n != "macro")))
    assert note.count(",") == 1 and " and " in note and "—" not in note


# ── the report text ──────────────────────────────────────────────────────────

def _jobs(shell, persona=BUFFETT):
    base = {
        "fundamental_metrics": [], "overall_assessment": {"text": ""},
        "moat_competition": {}, "symbol": "BNY", "company_name": "BNY",
    }
    jobs = np_.build_narrative_jobs(get_persona_config(persona), "E", {**base, **shell})
    return {job.label: job for job in jobs}


LINE = "This score rests on 42% of what this style weighs."


def test_the_line_ends_the_summary():
    job = _jobs(_report(*NO_PEERS))["executive_summary_text"]
    assert job.postprocess("A bank.").startswith(f"A bank. {LINE}")


def test_the_line_survives_a_failed_summary():
    job = _jobs(_report(*NO_PEERS))["executive_summary_text"]
    assert job.fallback_value == (
        f'{np_.FALLBACK["executive_summary_text"]} {ps.score_coverage_note(BUFFETT, _report(*NO_PEERS))}'
    )


def test_the_moat_note_does_not_take_its_place():
    """The moat block further down the same function has its own note: the summary's closure
    must keep reading the score line."""
    shell = {**_report(*NO_PEERS), "moat_competition": {"_coverage_note": "Moat note."}}
    job = _jobs(shell)["executive_summary_text"]
    text = job.postprocess("A bank.")
    assert LINE in text and "Moat note." not in text


def test_a_well_covered_report_keeps_its_summary_job():
    job = _jobs(_report("wall_street"))["executive_summary_text"]
    assert job.postprocess is None
    assert job.fallback_value == np_.FALLBACK["executive_summary_text"]


def test_the_line_follows_the_style_of_the_report():
    text = _jobs(_report(*NO_PEERS), WOOD)["executive_summary_text"].postprocess("A bank.")
    assert "rests on 70% of" in text


def test_a_shell_with_no_scores_builds_as_before():
    job = _jobs({})["executive_summary_text"]
    assert job.postprocess is None


def test_the_summary_prompt_tells_the_model_what_was_not_rated():
    """BNY's first real run read "a significant margin of safety" right above the line."""
    prompt = _jobs(_report(*NO_PEERS, "insider"))["executive_summary_text"].prompt
    assert "NOT RATED for this company: Moat, Financial Health, Valuation, Insider Activity." in prompt
    assert prompt.rstrip().endswith("no moat or lasting advantage.")


def test_a_well_covered_prompt_is_unchanged():
    assert "NOT RATED" not in _jobs(_report("wall_street"))["executive_summary_text"].prompt
    assert "NOT RATED" not in _jobs({})["executive_summary_text"].prompt


class _Gemini:
    """Stage B's two calls, no context cache: `text` is what the model answers."""

    def __init__(self, text=None):
        self.text = text

    async def create_cached_content(self, *args, **kwargs):
        return None

    async def delete_cache(self, *args, **kwargs):
        return None

    async def generate_text(self, *args, **kwargs):
        if self.text is None:
            raise RuntimeError("model down")
        return {"text": self.text}

    async def generate_with_cache(self, *args, **kwargs):
        raise AssertionError("no cache in this test")


@pytest.mark.asyncio
async def test_the_line_reaches_the_stored_text_through_the_real_runner():
    report = {**_report(*NO_PEERS), "symbol": "BNY", "company_name": "BNY"}
    persona = get_persona_config(BUFFETT)
    jobs = [j for j in np_.build_narrative_jobs(persona, "E", report)
            if j.label == "executive_summary_text"]
    await np_.run_narrative_jobs(jobs, _Gemini("BNY is a custody bank."), persona)
    assert report["executive_summary_text"].startswith(f"BNY is a custody bank. {LINE}")


@pytest.mark.asyncio
async def test_a_failed_model_still_counts_as_a_fallback_and_keeps_the_line():
    report = {**_report(*NO_PEERS), "symbol": "BNY", "company_name": "BNY"}
    persona = get_persona_config(BUFFETT)
    jobs = [j for j in np_.build_narrative_jobs(persona, "E", report)
            if j.label == "executive_summary_text"]
    outcome = await np_.run_narrative_jobs(jobs, _Gemini(None), persona)
    assert outcome.fell_back == 1
    assert LINE in report["executive_summary_text"]


def test_the_pdf_prints_the_line_after_the_score():
    text = f"A bank. {ps.score_coverage_note(BUFFETT, _report(*NO_PEERS))}"
    context = pdf.build_context({
        "symbol": "BNY", "company_name": "BNY", "quality_score": 70.3, "agent": "buffett",
        "executive_summary_text": text, **_report(*NO_PEERS),
    })
    assert context["exec_summary"] == text
    html = pdf.render_html(context)
    assert html.index("Executive Summary") < html.index(LINE)
