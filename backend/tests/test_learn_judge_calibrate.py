"""The Learn judge calibrator (`scripts/learn_judge_calibrate.py`) and its fixture.

Pinned: the scoring (a verdict hits a label only in the label's own field and with overlapping
words; a verdict that hits only a BORDERLINE label counts on neither side; other rules never count
toward the unsupported-claim recall), the database is blocked before the first judge call, the
fixture is well formed (every label is found verbatim in its own draft, both splits exist), and
the rubric's examples are never calibration text (marketing.md §7a: a rubric tuned on its own
calibration lines measures nothing).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.services.learn_weekly import contracts as C
from app.services.learn_weekly import judge as J

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "learn_judge_calibrate.py"
FIXTURE = BACKEND / "tests" / "data" / "learn_judge_calibration_2026_10_24.json"


def _load_script():
    spec = importlib.util.spec_from_file_location("learn_judge_calibrate", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["learn_judge_calibrate"] = mod
    spec.loader.exec_module(mod)
    return mod


cal = _load_script()

SHEET = {"kind": C.KIND_DEEP_DIVE, "topic_title": "Widgets", "theme_slug": "widgets",
         "subjects": [{"name": "ACME CORP"}],
         "facts": [{"id": "S1", "text": "ACME CORP reported revenue of $1.0 billion for the fiscal year "
                                        "ended June 30, 2025 (Form 10-K).",
                    "origin": C.ORIGIN_SEC_XBRL, "url": "https://www.sec.gov/a", "accession": "0000000000-25-000001"}]}

DRAFT: Dict[str, Any] = {
    "title": "How Widgets Work", "subtitle": "A plain subtitle.",
    "sections": [
        {"title": "Origins", "content": [
            {"type": "paragraph", "text": "ACME signed a supply deal, a move meant to lock in buyers for a decade."},
            {"type": "paragraph", "text": "Widget makers often sign long contracts with large buyers."},
        ]},
        {"title": "How it earns", "content": [
            {"type": "paragraph", "text": "ACME reported revenue of $1.0 billion, which shows its pricing power."},
            {"type": "bulletList", "text": "Costs:", "items": ["Steel is the main input.",
                                                                "ACME closed a plant because demand fell."]},
        ]},
    ],
}

ITEM = {"id": "x", "split": "train", "draft": DRAFT, "sheet": SHEET, "labels": {
    "unsupported": [
        {"section": 0, "quote": "a move meant to lock in buyers for a decade", "category": "a", "why": "purpose"},
        {"section": 1, "quote": "ACME closed a plant because demand fell", "category": "b", "why": "cause"},
    ],
    "borderline": [{"section": 1, "quote": "which shows its pricing power", "why": "vague"}],
}}


def _v(field: str, quote: str, rule: str = cal.RULE) -> Dict[str, Any]:
    return {"label": field, "rule": rule, "quote": quote, "reason": "r", "located": True}   # Verdict.as_dict()


# ── matching ──────────────────────────────────────────────────────────────────────────────────


def test_spans_are_word_positions_in_the_field():
    field = "ACME signed a supply deal, a move meant to lock in buyers for a decade."
    assert cal.span_of("a move meant to lock in buyers", field) == (5, 12)
    assert cal.span_of("ACME’s “deal” — meant", "acme's deal meant") == (0, 4)   # curly quotes fold
    assert cal.span_of("a move meant to lock in buy", field) == (5, 12)     # a quote capped mid-word
    # "sell" is no prefix of "buyers": never the cut path, only the fuzzy place of its words.
    assert cal.span_of("a move meant to lock in sell", field) == (6, 10)
    assert cal.span_of("", field) is None and cal.span_of("for the a", field) is None
    two = "Revenue rose for the fiscal year. ACME closed a plant because demand fell."
    assert cal.span_of("ACME shut the plant because of falling demand", two) == (6, 12)   # fuzzy → its words
    assert cal.spans_meet((0, 4), (3, 9)) and not cal.spans_meet((0, 4), (4, 9))
    assert not cal.spans_meet(None, (0, 1))


def test_shared_stopwords_never_make_a_hit():
    """Review 2026-10-24: a verdict quoting "For the fiscal year" from the SUPPORTED sentence of a
    field scored as a hit on an unsupported label in the same field, on "for" and "the" alone."""
    draft = {"title": "T", "sections": [{"title": "S", "content": [{"type": "paragraph", "text":
             "ACME reported revenue for the fiscal year ended June 30, 2025. The demand for the "
             "hardware has driven the results."}]}]}
    item = {"id": "y", "split": "train", "draft": draft, "sheet": SHEET, "labels": {
        "unsupported": [{"section": 0, "quote": "The demand for the hardware has driven the results.",
                         "category": "b", "why": "cause"}], "borderline": []}}
    sc = cal.score_item(item, [_v("sections[0].content[0]", "For the fiscal year")])
    assert sc["hit_rule"] == [] and len(sc["fp"]) == 1
    sc = cal.score_item(item, [_v("sections[0].content[0]", "has driven the results")])
    assert sc["hit_rule"] == [0] and not sc["fp"]


def test_locate_finds_a_label_in_its_own_field_and_uses_the_section_to_break_ties():
    fields = J.article_fields(DRAFT)
    assert cal.locate("a move meant to lock in buyers for a decade", fields, 0) == ["sections[0].content[0]"]
    assert cal.locate("ACME closed a plant because demand fell", fields, 1) == ["sections[1].content[1].items[1]"]
    assert cal.locate("a sentence that is nowhere in the article at all", fields, 0) == []
    dup = [("sections[0].content[0]", "Same words here."), ("sections[2].content[0]", "Same words here.")]
    assert cal.locate("same words here", dup, 2) == ["sections[2].content[0]"]


def test_section_of_reads_the_labellers_convention():
    assert cal.section_of("sections[3].content[2].items[1]") == 3
    assert cal.section_of("title") == -1 and cal.section_of("keyHighlights[0].title") == -1


# ── scoring ───────────────────────────────────────────────────────────────────────────────────


def test_a_verdict_in_the_labels_field_with_overlapping_words_is_a_hit():
    sc = cal.score_item(ITEM, [_v("sections[0].content[0]", "a move meant to lock in buyers")])
    assert sc["hit_rule"] == [0] and len(sc["tp"]) == 1 and not sc["fp"]
    assert sc["missed"] == ["ACME closed a plant because demand fell"]


def test_the_right_words_in_the_wrong_field_are_not_a_hit():
    sc = cal.score_item(ITEM, [_v("sections[0].content[1]", "a move meant to lock in buyers")])
    assert sc["hit_rule"] == [] and len(sc["fp"]) == 1


def test_a_verdict_on_a_general_explanation_is_a_false_positive():
    sc = cal.score_item(ITEM, [_v("sections[0].content[1]", "Widget makers often sign long contracts")])
    assert len(sc["fp"]) == 1 and not sc["tp"]


def test_a_verdict_on_a_borderline_label_counts_on_neither_side():
    sc = cal.score_item(ITEM, [_v("sections[1].content[0]", "which shows its pricing power")])
    assert not sc["tp"] and not sc["fp"] and len(sc["borderline_only"]) == 1


def test_another_rule_never_counts_toward_the_unsupported_recall():
    sc = cal.score_item(ITEM, [_v("sections[0].content[0]", "a move meant to lock in buyers", "learn_forecast")])
    assert sc["hit_rule"] == [] and sc["hit_any"] == [0] and not sc["fp"]
    assert sc["other_rules"][0]["hits_unsupported"] is True


def test_the_raw_answers_field_key_reads_like_the_stored_label():
    raw = {"field": "sections[0].content[0]", "rule": cal.RULE, "quote": "a move meant to lock in buyers"}
    assert cal.score_item(ITEM, [raw])["hit_rule"] == [0]


def test_a_verdict_on_an_unknown_field_hits_nothing():
    sc = cal.score_item(ITEM, [_v("judge", "a move meant to lock in buyers")])
    assert sc["hit_rule"] == [] and len(sc["fp"]) == 1


def test_a_quote_found_twice_is_scored_on_neither_side_and_counted_ambiguous():
    """Re-review 2026-10-24: the first occurrence alone was used, so a correct catch on a later
    sentence that repeats an earlier one's words scored as a miss plus a false positive; scoring it
    on ANY place (round 2) let a short claim from a supported sentence land on another's label
    (round 3). Its place cannot be told, so it counts on neither side."""
    text = "ACME expanded into Asia in 2020. In 2022 ACME expanded into Asia to lock in buyers."
    draft = {"title": "T", "sections": [{"title": "S", "content": [{"type": "paragraph", "text": text}]}]}
    item = {"id": "z", "split": "train", "draft": draft, "sheet": SHEET, "labels": {
        "unsupported": [{"section": 0, "quote": "In 2022 ACME expanded into Asia to lock in buyers.",
                         "category": "a", "why": "purpose"}], "borderline": []}}
    sc = cal.score_item(item, [_v("sections[0].content[0]", "ACME expanded into Asia")])
    assert sc["hit_rule"] == [] and not sc["fp"] and not sc["tp"] and len(sc["ambiguous"]) == 1
    s = cal.summarize({"items": [item]}, {"z": [{"verdicts": [_v("sections[0].content[0]",
                                                                  "ACME expanded into Asia")]}]}, "train")
    assert s["ambiguous"] == 1 and s["precision"] is None


def test_a_quote_found_twice_that_misses_everywhere_is_still_a_false_positive():
    """Round 4: every repeated quote was dropped, so a definite false positive — one that misses
    every label wherever it sits — went uncounted and lifted precision over the bar."""
    text = "ACME builds data centers. Widget makers sign long contracts. ACME builds data centers in Ohio."
    draft = {"title": "T", "sections": [{"title": "S", "content": [{"type": "paragraph", "text": text}]}]}
    item = {"id": "q", "split": "holdout", "draft": draft, "sheet": SHEET, "labels": {
        "unsupported": [{"section": 0, "quote": "Widget makers sign long contracts", "category": "e",
                         "why": "x"}], "borderline": []}}
    tp_v = _v("sections[0].content[0]", "Widget makers sign long contracts")
    fp_v = _v("sections[0].content[0]", "ACME builds data centers")
    sc = cal.score_item(item, [tp_v, fp_v])
    assert len(sc["tp"]) == 1 and len(sc["fp"]) == 1 and sc["ambiguous"] == []
    s = cal.summarize({"items": [item]}, {"q": [{"verdicts": [tp_v, fp_v]}]}, "holdout")
    assert s["precision"] == 0.5 and s["gate"] is False


def test_an_ambiguous_other_rule_verdict_stays_on_the_human_list():
    text = "ACME expanded into Asia in 2020. In 2022 ACME expanded into Asia to lock in buyers."
    draft = {"title": "T", "sections": [{"title": "S", "content": [{"type": "paragraph", "text": text}]}]}
    item = {"id": "z", "split": "train", "draft": draft, "sheet": SHEET, "labels": {
        "unsupported": [{"section": 0, "quote": "In 2022 ACME expanded into Asia to lock in buyers.",
                         "category": "a", "why": "purpose"}], "borderline": []}}
    sc = cal.score_item(item, [_v("sections[0].content[0]", "ACME expanded into Asia", "learn_forecast")])
    assert len(sc["ambiguous"]) == 1 and len(sc["other_rules"]) == 1 and sc["hit_any"] == []


def _one_field_item(text: str, unsupported, borderline=()) -> Dict[str, Any]:
    draft = {"title": "T", "sections": [{"title": "S", "content": [{"type": "paragraph", "text": text}]}]}
    return {"id": "c", "split": "holdout", "draft": draft, "sheet": SHEET, "labels": {
        "unsupported": [{"section": 0, "quote": q, "category": "e", "why": "x"} for q in unsupported],
        "borderline": [{"section": 0, "quote": q, "why": "x"} for q in borderline]}}


def test_borderline_in_one_place_and_nothing_in_another_is_a_false_positive():
    """Round 5: such a verdict can never be a hit; dropping it as ambiguous always flattered the
    judge (here it flipped the bar to MET)."""
    item = _one_field_item(
        "ACME builds data centers, which shows its pricing power. Widget makers sign long contracts. "
        "ACME builds data centers in Ohio.",
        ["Widget makers sign long contracts"], ["ACME builds data centers, which shows its pricing power"])
    verdicts = [_v("sections[0].content[0]", "Widget makers sign long contracts"),
                _v("sections[0].content[0]", "ACME builds data centers")]
    sc = cal.score_item(item, verdicts)
    assert len(sc["tp"]) == 1 and len(sc["fp"]) == 1 and sc["ambiguous"] == []
    s = cal.summarize({"items": [item]}, {"c": [{"verdicts": verdicts}]}, "holdout")
    assert s["precision"] == 0.5 and s["gate"] is False


def test_a_hit_in_every_place_is_a_true_positive_credited_only_where_certain():
    # (a) both places meet the same unsupported label; one also meets a borderline one.
    item = _one_field_item(
        "ACME expanded into Asia, which shows its reach, and ACME expanded into Asia again to lock in buyers.",
        ["ACME expanded into Asia, which shows its reach, and ACME expanded into Asia again to lock in buyers."],
        ["ACME expanded into Asia, which shows its reach"])
    sc = cal.score_item(item, [_v("sections[0].content[0]", "ACME expanded into Asia")])
    assert len(sc["tp"]) == 1 and sc["hit_rule"] == [0] and sc["ambiguous"] == []
    # (b) each place meets a DIFFERENT label: a correct flag, but which label it found is unknown.
    item = _one_field_item(
        "ACME expanded into Asia to lock in buyers. ACME expanded into Asia because demand fell.",
        ["ACME expanded into Asia to lock in buyers.", "ACME expanded into Asia because demand fell."])
    sc = cal.score_item(item, [_v("sections[0].content[0]", "ACME expanded into Asia")])
    assert len(sc["tp"]) == 1 and sc["hit_rule"] == [] and sc["hit_any"] == []


@pytest.mark.parametrize("quote", ["", "   ", "\u2014", "..."])
def test_a_quote_with_no_words_could_be_any_sentence_of_its_field(quote):
    """Round 5: "" covered the whole field and credited EVERY label in it (100% recall from a
    verdict naming no words), while "—" scored FP."""
    both = _one_field_item("ACME expanded to lock in buyers. ACME closed a plant because demand fell.",
                           ["ACME expanded to lock in buyers.", "ACME closed a plant because demand fell."])
    sc = cal.score_item(both, [_v("sections[0].content[0]", quote)])
    assert len(sc["tp"]) == 1 and sc["hit_rule"] == []        # a hit, but on no certain label
    mixed = _one_field_item("Revenue was reported for the year. ACME closed a plant because demand fell.",
                            ["ACME closed a plant because demand fell."])
    sc = cal.score_item(mixed, [_v("sections[0].content[0]", quote)])
    assert len(sc["ambiguous"]) == 1 and not sc["tp"] and not sc["fp"]


def test_a_paraphrase_of_a_supported_clause_never_takes_its_neighbours_label():
    """Round 6: a paraphrase was placed on its WHOLE sentence, so a quote of the supported clause
    met the unsupported clause beside it — a false positive scored as a hit."""
    item = _one_field_item("ACME reported revenue of $1.0 billion for fiscal 2025, and it closed a "
                           "plant because demand fell.", ["it closed a plant because demand fell"])
    sc = cal.score_item(item, [_v("sections[0].content[0]", "ACME reported revenues of $1.0bn in fiscal 2025")])
    assert not sc["tp"] and len(sc["fp"]) == 1 and sc["hit_rule"] == []
    sc = cal.score_item(item, [_v("sections[0].content[0]", "it shut a plant because demand fell")])
    assert len(sc["tp"]) == 1 and sc["hit_rule"] == [0]          # a paraphrase OF the label still hits


def test_a_hit_beside_a_borderline_place_is_ambiguous_on_purpose():
    """Round 6: {TP, BORDER} can never be a false positive; leaving it out is the PESSIMISTIC
    choice (it can only lower the measured numbers) — pinned so an edit cannot flip it unseen."""
    item = _one_field_item("ACME expanded into Asia, which shows its reach. ACME expanded into Asia "
                           "to lock in buyers.", ["ACME expanded into Asia to lock in buyers."],
                           ["ACME expanded into Asia, which shows its reach."])
    sc = cal.score_item(item, [_v("sections[0].content[0]", "ACME expanded into Asia")])
    assert len(sc["ambiguous"]) == 1 and not sc["tp"] and not sc["fp"] and not sc["borderline_only"]
    assert cal.classify([(frozenset({0}), frozenset()), (frozenset(), frozenset({0}))])[0] == cal.AMBIGUOUS


def test_the_other_rule_flags_say_what_is_certain():
    item = _one_field_item("ACME closed a plant because demand fell, which shows its discipline.",
                           ["ACME closed a plant because demand fell"], ["which shows its discipline"])
    whole = "ACME closed a plant because demand fell, which shows its discipline."
    (o,) = cal.score_item(item, [_v("sections[0].content[0]", whole, "learn_forecast")])["other_rules"]
    assert o["hits_unsupported"] is True and o["hits_borderline"] is True     # one place meets both
    assert cal.classify([(frozenset(), frozenset({1})), (frozenset(), frozenset())]) == \
        (cal.FP, frozenset(), frozenset())                                     # a maybe is not certain
    assert cal.classify([(frozenset({0}), frozenset({2})), (frozenset({0}), frozenset())]) == \
        (cal.TP, frozenset({0}), frozenset())


def test_the_report_lists_each_ambiguous_verdict(capsys):
    item = _one_field_item("Revenue was reported for the year. ACME closed a plant because demand fell.",
                           ["ACME closed a plant because demand fell."])
    s = cal.summarize({"items": [item]}, {"c": [{"verdicts": [_v("sections[0].content[0]", "")]}]}, "holdout")
    cal.print_report(s)
    out = capsys.readouterr().out
    assert "1 verdict(s) whose words sit in places that score differently" in out
    assert "? c sample 0 learn_unsupported_claim sections[0].content[0]: ''" in out


def test_a_fuzzy_tie_is_ambiguous_never_the_first_sentence():
    text = "ACME sold widgets in 2020. ACME sold widgets to lock in buyers."
    assert cal.spans_of("ACME sells widgets", text) == [(0, 3), (5, 8)]


def test_a_label_with_no_position_is_reported_unlocated_not_silently_missed():
    draft = {"title": "T", "sections": [{"title": "S", "content": [{"type": "paragraph", "text":
             "ACME closed plants. Demand fell sharply in Asia. Rivals followed later."}]}]}
    item = {"id": "u", "split": "train", "draft": draft, "sheet": SHEET, "labels": {
        "unsupported": [{"section": 0, "quote": "ACME closed plants and demand fell later",
                         "category": "b", "why": "x"}], "borderline": []}}
    sc = cal.score_item(item, [])
    assert sc["unlocated_labels"] == ["ACME closed plants and demand fell later"]


def test_an_item_with_fewer_runs_is_left_out_and_named():
    other = dict(ITEM, id="w")
    fixture = {"items": [ITEM, other]}
    hit0 = _v("sections[0].content[0]", "a move meant to lock in buyers")
    s = cal.summarize(fixture, {"x": [{"verdicts": [hit0]}]}, "train")
    assert s["incomplete_items"] == ["w"] and s["labels"] == 2 and s["recall_majority"] == 0.5
    # A split measured without some of its items gets no bar verdict (round 3: it printed MET).
    both = _v("sections[1].content[1].items[1]", "ACME closed a plant because demand fell")
    s = cal.summarize(fixture, {"x": [{"verdicts": [hit0, both]}], "w": []}, "train")
    assert s["recall_majority"] == 1.0 and s["gate"] is None


def test_a_silent_judge_fails_the_bar_on_recall_alone():
    s = cal.summarize({"items": [ITEM]}, {"x": [{"verdicts": []}]}, "train")
    assert s["recall_majority"] == 0.0 and s["precision"] is None and s["gate"] is False


def test_the_thinking_refusal_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "JUDGE_THINKING_BUDGET", 0)
    fixture = tmp_path / "f.json"
    fixture.write_text(json.dumps({"items": [ITEM]}))
    saved = tmp_path / "v.json"
    saved.write_text(json.dumps({"runs": {}}))
    with pytest.raises(SystemExit):
        asyncio.run(cal.main(["--fixture", str(fixture), "--verdicts-in", str(saved), "--thinking", "7"]))
    assert J.JUDGE_THINKING_BUDGET == 0


def test_a_split_with_no_runs_is_no_measurement():
    s = cal.summarize({"items": [ITEM]}, {}, "train")
    assert s["samples"] == 0 and s["recall_majority"] is None and s["recall_ever"] is None
    assert s["gate"] is None


def test_the_report_shows_an_item_whose_first_sample_errored(capsys):
    fp = _v("sections[0].content[1]", "Widget makers often sign long contracts")
    s = cal.summarize({"items": [ITEM]}, {"x": [{"error": "E: boom"}, {"verdicts": [fp]}]}, "train")
    cal.print_report(s)
    out = capsys.readouterr().out
    assert "! x sample 0 errored: E: boom" in out
    assert "- x [train] sample 1:" in out and "fp  sections[0].content[1]" in out


def test_saved_runs_print_their_own_model_and_refuse_a_thinking_override(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(J, "JUDGE_THINKING_BUDGET", J.JUDGE_THINKING_BUDGET)    # restored after
    fixture = tmp_path / "f.json"
    fixture.write_text(json.dumps({"items": [ITEM]}))
    saved = tmp_path / "v.json"
    saved.write_text(json.dumps({"rubric_version": "r (support)", "model": "m-1", "thinking": 1024,
                                 "runs": {"x": [{"verdicts": []}]}}))
    asyncio.run(cal.main(["--fixture", str(fixture), "--verdicts-in", str(saved), "--split", "train"]))
    assert "rubric r (support) · model m-1 · thinking 1024" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        asyncio.run(cal.main(["--fixture", str(fixture), "--verdicts-in", str(saved), "--thinking", "0"]))


def test_summary_recall_is_by_majority_of_samples_and_errors_never_count_as_passes():
    fixture = {"items": [ITEM]}
    hit0 = _v("sections[0].content[0]", "a move meant to lock in buyers")
    runs = {"x": [{"verdicts": [hit0]}, {"verdicts": [hit0]}, {"verdicts": []}]}
    s = cal.summarize(fixture, runs, "train")
    assert s["labels"] == 2 and s["samples"] == 3
    assert s["recall_majority"] == 0.5 and s["recall_ever"] == 0.5 and s["precision"] == 1.0
    assert s["gate"] is False                          # 50% recall is under the bar
    runs = {"x": [{"error": "LearnJudgeUnavailable: x"}, {"verdicts": [hit0]}, {"verdicts": []}]}
    s = cal.summarize(fixture, runs, "train")
    assert s["errors"] == 1 and s["recall_majority"] == 0.0     # 1 of 3 samples is no majority
    assert cal.summarize(fixture, runs, "holdout")["labels"] == 0


def test_the_bar_was_registered_before_any_run_and_is_not_trivial():
    assert cal.GATE_RECALL == 0.80 and cal.GATE_PRECISION == 0.90
    assert cal.RULE == J.SUPPORT_RULE and cal.RULE in J.EMITTED_CODES


# ── never the database ────────────────────────────────────────────────────────────────────────


def test_run_judge_blocks_the_database_before_the_first_call(monkeypatch):
    import app.database as database

    for name, module in list(sys.modules.items()):     # every binding is restored at teardown
        if name.startswith("app.") and getattr(module, "get_supabase", None) is not None:
            monkeypatch.setattr(module, "get_supabase", module.get_supabase)
    seen: List[str] = []

    class FakeClient:
        async def generate_json(self, *_a, **_k):
            try:
                database.get_supabase()
                seen.append("REACHED THE DATABASE")
            except cal.CalibrationDatabaseBlocked:
                seen.append("blocked")
            return {"text": json.dumps({"verdicts": []}), "finish_reason": "STOP", "tokens_used": 3}

    runs = asyncio.run(cal.run_judge({"items": [ITEM]}, 2, 2, client=FakeClient()))
    assert seen == ["blocked", "blocked"]
    assert [r["verdicts"] for r in runs["x"]] == [[], []]


# ── the fixture ───────────────────────────────────────────────────────────────────────────────


def _fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_fixture_is_well_formed_and_has_both_splits():
    data = _fixture()
    splits = {it["split"] for it in data["items"]}
    assert splits == {"train", "holdout"}
    ids = [it["id"] for it in data["items"]]
    assert len(ids) == len(set(ids))
    for it in data["items"]:
        C.FactSheet.from_json(it["sheet"])
        assert J.article_fields(it["draft"]), it["id"]
        labels = it["labels"]
        assert isinstance(labels["unsupported"], list) and isinstance(labels["borderline"], list)
        for row in labels["unsupported"]:
            assert row["category"] in {"a", "b", "c", "d", "e"}, row


def test_every_label_is_found_verbatim_in_its_own_section():
    """Strict (review 2026-10-24): `locate` also accepts a field holding 80% of the words, so an
    edited or mistyped label would have passed — and could sit in the wrong field."""
    for it in _fixture()["items"]:
        fields = J.article_fields(it["draft"])
        for row in it["labels"]["unsupported"] + it["labels"]["borderline"]:
            homes = cal.locate_exact(row["quote"], fields, row.get("section"))
            assert homes, (it["id"], row["quote"])
            assert all(cal.section_of(h) == row["section"] for h in homes), (it["id"], row["quote"])


def test_the_rubric_examples_are_never_calibration_text():
    examples = [q for body in (J.SYSTEM_BODY, J.SUPPORT_BODY)
                for q in re.findall(r'"([^"]*)"', body) if len(q) >= 6]
    assert len(examples) >= 10
    corpus = " ".join(" ".join(cal.words(text)) for it in _fixture()["items"]
                      for _, text in J.article_fields(it["draft"]))
    for ex in examples:
        folded = " ".join(cal.words(ex))
        if len(folded.split()) < 3:
            continue
        assert folded not in corpus, f"rubric example is calibration text: {ex!r}"
