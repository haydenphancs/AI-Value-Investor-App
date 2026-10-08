"""The paid report's model prompts name each peer median by its own group (2026-10-08).

Final verification found three prompt surfaces still describing peers the old way:
- the thesis / critical-factors DIGEST called an industry median "sector avg" (the
  EVIDENCE block of the same prompt already said "industry avg");
- the Fundamentals TRAJECTORY named every drill-down line with the card-wide word, though
  each line is ONE peer group with its own `sector_annual_level`;
- the overall-assessment prompt said "across four cards" when the average excludes the
  unrated (0-star) card a bank now has.
Hermetic: pure prompt builders over report dicts.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.schemas.ticker_report import DeepDiveMetricResponse
from app.services.agents import narrative_prompts as NP
from app.services.agents import ticker_report_data_collector as C


def _card(title, stars, metrics, level="industry"):
    return {"title": title, "star_rating": stars, "metrics": metrics, "quality_label": "x",
            "peer_group_level": level}


def test_the_digest_names_an_industry_median_as_the_industrys():
    report = {"fundamental_metrics": [_card("Valuation", 3, [
        {"label": "P/E (1.30x sector avg 22.4)", "value": "29.1", "peer_level": "industry"},
        {"label": "P/S (0.90x sector avg 4.04)", "value": "3.6", "peer_level": "sector"},
        {"label": "P/B", "value": "8.9"},
    ])]}
    text = "\n".join(NP._digest_fundamentals(report))
    assert "P/E (1.30x industry avg 22.4) 29.1" in text
    assert "P/S (0.90x sector avg 4.04) 3.6" in text
    assert "P/B 8.9" in text


def test_the_card_carries_the_metric_peer_level_and_it_survives_the_schema():
    snap = SimpleNamespace(rating=3, metrics=[SimpleNamespace(
        name="P/E (1.30x sector avg 22.4)", value="29.1", metric_key="pe", score=3,
        peer_level="industry")])
    md = C._snapshot_to_card("Valuation", snap)["metrics"][0]
    assert md["label"] == "P/E (1.30x sector avg 22.4)", "the wire label is unchanged"
    assert md["peer_level"] == "industry"
    assert DeepDiveMetricResponse.model_validate(md).peer_level == "industry"


def _traj_metric(level):
    m = {
        "label": "ROA", "history_unit": "percent",
        "annual_history": [{"period": "2023", "value": 8.0}, {"period": "2025", "value": 9.0}],
        "sector_annual_history": [{"period": "2023", "value": 10.0}, {"period": "2025", "value": 10.0}],
    }
    if level:
        m["sector_annual_level"] = level
    return m


def test_the_trajectory_names_each_line_by_its_own_group():
    shell = {"fundamental_metrics": [_card("Profitability", 3, [_traj_metric("sector")], level="industry")]}
    block = NP._fundamentals_trajectory_block(shell)
    assert "below sector" in block and "industry" not in block.split("ROA", 1)[1]


def test_a_report_frozen_before_the_levels_keeps_the_card_word():
    shell = {"fundamental_metrics": [_card("Profitability", 3, [_traj_metric(None)], level="industry")]}
    assert "below industry" in NP._fundamentals_trajectory_block(shell)


def test_the_overall_prompt_counts_rated_cards_only(monkeypatch):
    monkeypatch.setattr(NP, "_style_block", lambda _p: "")
    persona = SimpleNamespace()
    cards = [_card("Profitability", 4, []), _card("Growth", 3, []), _card("Valuation", 3, []),
             _card("Health", 0, [])]
    shell = {"fundamental_metrics": cards,
             "overall_assessment": {"average_rating": 3.3, "strong_count": 1, "weak_count": 0}}
    prompt = NP._overall_assessment_text_prompt(persona, "EVIDENCE", shell)
    assert "Average rating 3.3/5 across 3 rated cards (Health not rated)" in prompt
    assert "across four cards" not in prompt
    shell["fundamental_metrics"][3]["star_rating"] = 2
    prompt = NP._overall_assessment_text_prompt(persona, "EVIDENCE", shell)
    assert "across 4 rated cards (1 strong" in prompt
