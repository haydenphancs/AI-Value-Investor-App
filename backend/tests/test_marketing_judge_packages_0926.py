"""
The 2026-09-26 JUDGE CALIBRATION FIXTURE: real writer packages, pre-registered by hand.

`tests/data/marketing_judge_packages_2026_09_26.json` holds every parsed round (draft AND repair)
of one `scripts/marketing_preview.py --all-items --raw --judge shadow` run (prompt 2026-09-26.6)
exactly as the judge reads it (`judge.package_fields(validate_package(raw))`), plus:

* `judge_true_positives` — the fields that break a policy rule only the semantic judge
  enforces (portfolio_gardening's trim / prune / let-winners-grow directives — user decision
  2026-09-26, trade directives no), each with its rule and why;
* `boundary_notes` — honest lines near a boundary, with the call that keeps them honest. These
  are the ASSISTANT's calls, not user decisions, and are disclosed as such in the fixture.

Both were classified BEFORE any judge verdict of the run was read — the calibration is only
evidence if its expected answers were fixed first. `scripts/marketing_judge_calibrate.py
--packages` treats every accepted package as must-pass except the true positives, which it
requires the judge to flag. Never edit a field to make a test pass: narrow a rule that rejects an
honest line, or move a policy break into `judge_true_positives` with a why that says WHEN it was
classified.

Category 1 (pure): one JSON fixture, the Learn bundle, the pure validators.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from app.services.marketing import content_pool
from app.services.marketing import judge as jd
from app.services.marketing import writer_service as ws

FIXTURE = Path(__file__).resolve().parent / "data" / "marketing_judge_packages_2026_09_26.json"
_DOC = json.loads(FIXTURE.read_text(encoding="utf-8"))
_RUN_DATE = date(2026, 9, 26)

#: Items the run's writer REJECTED after its repair (regex over-blocks the prompt now steers
#: around — see test_marketing_residuals_compliance.py's steering pairs).
_REJECTED_IN_THE_RUN = {"journey:inflation_thief", "journey:risk_reward"}


def _packages() -> List[Dict[str, Any]]:
    return _DOC["packages"]


def _accepted() -> List[Dict[str, Any]]:
    return [p for p in _packages() if p["accepted"]]


def _fields(pkg: Dict[str, Any]) -> Dict[str, str]:
    return {lab: text for lab, text in pkg["fields"]}


def _by_id() -> Dict[str, Dict[str, Any]]:
    return {p["id"]: p for p in _packages()}


def _rebuild(fields: List[List[str]]) -> Dict[str, Any]:
    """The raw writer object back from judge labels (the inverse of `jd.package_fields`)."""
    obj: Dict[str, Any] = {"hook": "", "video_script": [], "cards": [], "carousel_slides": [],
                           "captions": {}}
    for lab, text in fields:
        if lab == "hook":
            obj["hook"] = text
        elif re.fullmatch(r"video_script\[\d+\]", lab):
            obj["video_script"].append(text)
        elif m := re.fullmatch(r"(cards|carousel_slides)\[(\d+)\]\.(title|body)", lab):
            rows = obj[m.group(1)]
            while len(rows) <= int(m.group(2)):
                rows.append({})
            rows[int(m.group(2))][m.group(3)] = text
        elif lab.startswith("captions."):
            obj["captions"][lab[len("captions."):]] = text
        else:
            raise AssertionError(f"unknown label {lab!r}")
    return obj


def test_the_fixture_is_the_whole_run():
    pkgs = _packages()
    items = {p["item"] for p in pkgs}
    assert len(items) == 34, len(items)
    assert len(pkgs) == len({p["id"] for p in pkgs}), "duplicate package ids"
    accepted = Counter(p["item"] for p in _accepted())
    # Exactly one kept round per accepted item — a second would double-count in calibration.
    assert all(n == 1 for n in accepted.values()), [k for k, n in accepted.items() if n > 1]
    assert len(accepted) == 32 and set(accepted) == items - _REJECTED_IN_THE_RUN
    for p in pkgs:
        assert p["id"] == f"{p['item']}#{p['template']}#r{p['round']}", p["id"]
        assert p["fields"] and all(isinstance(t, str) and t for _lab, t in p["fields"]), p["id"]
    assert "2026-09-26.6" in _DOC["_about"]


def test_every_item_in_the_fixture_is_still_in_the_pool():
    missing = sorted(k for k in {p["item"] for p in _packages()} if content_pool.get_item(k) is None)
    assert missing == [], missing


@pytest.mark.parametrize("pkg", _accepted(), ids=lambda p: p["id"])
def test_an_accepted_package_still_passes_the_validators_unchanged(pkg):
    """The over-block gate for the 09-26 run: a validator change that starts rejecting what the
    writer really produced (or rewrites it in cleaning) fails here, not in production."""
    item = content_pool.get_item(pkg["item"])
    vr = ws.validate_package(_rebuild(pkg["fields"]), item, _RUN_DATE)
    assert vr.regex_ok, [(v.field, v.code, v.detail) for v in vr.violations]
    assert [list(x) for x in jd.package_fields(vr.package or {})] == pkg["fields"]


def test_the_judge_true_positives_point_at_real_fields_of_kept_packages():
    rows = _DOC["judge_true_positives"]
    assert len(rows) >= 5, len(rows)
    by_id = _by_id()
    seen = set()
    for r in rows:
        assert r["rule"] in jd.RULE_CODES, r
        # Pre-registered before any verdict, or a disclosed post-run correction TOWARD fail
        # (the 09-24 fixture's precedent: 'Nurture Your Winners').
        assert "2026-09-26" in r["why"], r["why"]
        assert ("BEFORE any judge verdict" in r["why"]
                or (r["why"].startswith("RECLASSIFIED AFTER") and "toward FAIL" in r["why"])), r["why"]
        pkg = by_id[r["package_id"]]
        assert pkg["accepted"], f"{r['package_id']} was not kept: calibration never sees it"
        assert _fields(pkg).get(r["label"]) == r["text"], (r["package_id"], r["label"])
        key = (r["package_id"], r["label"])
        assert key not in seen, key
        seen.add(key)
    assert {r["rule"] for r in rows} == {"judge_directive", "judge_return_claim"}
    post_run = [r for r in rows if r["why"].startswith("RECLASSIFIED AFTER")]
    assert len(post_run) == 1, "a new post-run reclassification: disclose it in the report too"


def test_the_judge_true_positives_pass_the_regex():
    """By construction these are lines only the JUDGE enforces: the writer kept them. If the
    regex starts rejecting one, it moved to the lexical layer — record that, do not drop it."""
    by_id = _by_id()
    for r in _DOC["judge_true_positives"]:
        pkg = by_id[r["package_id"]]
        assert pkg["regex_ok"], r["package_id"]


def test_the_boundary_notes_are_honest_kept_lines_and_disclosed_as_assistant_calls():
    by_id = _by_id()
    tps = {(r["package_id"], r["label"]) for r in _DOC["judge_true_positives"]}
    notes = _DOC["boundary_notes"]
    assert notes
    for n in notes:
        assert n["why"].startswith("ASSISTANT's pre-registered call"), n["why"][:60]
        assert (n["package_id"], n["label"]) not in tps, n
        pkg = by_id[n["package_id"]]
        assert pkg["accepted"], n["package_id"]
        assert _fields(pkg).get(n["label"]) == n["text"], (n["package_id"], n["label"])


def test_the_precedent_corrections_are_disclosed_real_and_out_of_the_true_positives():
    """Ten lines were pre-registered as directives, then found (after the calibration run) to
    have the SAME shape as honest lines of the 09-24 fixture, classified before this run. The
    correction runs toward honest, so it must be loud: disclosed as post-run, tied to that
    precedent, and scored on neither side (calibration test below)."""
    by_id = _by_id()
    tps = {(r["package_id"], r["label"]) for r in _DOC["judge_true_positives"]}
    rows = _DOC["reclassified_by_precedent"]
    assert len(rows) == 10, len(rows)
    for r in rows:
        assert r["why"].startswith("DISCLOSED CORRECTION made AFTER"), r["why"][:60]
        assert "09-24 fixture's honest set" in r["why"] and "excluded from BOTH sides" in r["why"]
        assert r["pre_registered_rule"] in jd.RULE_CODES
        assert (r["package_id"], r["label"]) not in tps
        assert _fields(by_id[r["package_id"]]).get(r["label"]) == r["text"]
    # Every quoted precedent really is an honest row of the 09-24 fixture.
    drafts = json.loads((FIXTURE.parent / "marketing_real_drafts_2026_09_24.json")
                        .read_text(encoding="utf-8"))
    honest = {h[2] for h in drafts["honest"]}
    for r in rows:
        quoted = re.search(r'\("(.+?)"\)', r["why"]).group(1)
        assert any(quoted in h for h in honest), quoted


def _calibrate_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "marketing_judge_calibrate.py"
    spec = importlib.util.spec_from_file_location("_marketing_judge_calibrate", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # A module that defines dataclasses must be in sys.modules while it executes.
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return mod


def test_calibration_expects_exactly_the_true_positives_and_passes_everything_else():
    cal = _calibrate_module()
    calls = [c for c in cal.build_calls(FIXTURE) if c.kind == "honest_package"
             and (c.package_id or "").count("#") == 2]
    assert {c.package_id for c in calls} == {p["id"] for p in _accepted()}
    expected: Dict[Tuple[str, str], str] = {}
    for c in calls:
        for case in c.cases:
            if case.expect:
                expected[(c.package_id, case.label)] = case.expect
    want = {(r["package_id"], r["label"]): r["rule"] for r in _DOC["judge_true_positives"]}
    assert expected == want
    unscored = {(c.package_id, case.label) for c in calls for case in c.cases
                if case.source == cal.UNSCORED}
    assert unscored == {(r["package_id"], r["label"]) for r in _DOC["reclassified_by_precedent"]}
    # Captions are scored as captions, everything else as shared lines.
    for c in calls:
        for case in c.cases:
            assert case.shared == (not case.label.startswith("captions.")), case.label


def test_evaluate_scores_an_unscored_line_on_neither_side():
    """A flagged unscored line is not a false positive and does not taint its package; an
    unflagged one is not a miss. A scored twin in the same call still counts."""
    cal = _calibrate_module()
    cases = [cal.Case("journey:portfolio_gardening", "cards[0].title", "Let Winners Grow", None,
                      cal.UNSCORED),
             cal.Case("journey:portfolio_gardening", "cards[1].title", "Prune When Needed",
                      "judge_directive", "preview_package"),
             cal.Case("journey:portfolio_gardening", "hook", "Tend it with patience.", None,
                      "preview_package")]
    call = cal.Call("journey:portfolio_gardening", cases, "honest_package", package_id="p#t#r1")
    v = jd.Verdict("cards[0].title", "judge_directive", "Let Winners Grow", "hold", True)
    ev = cal.evaluate([cal.Outcome(call, 0, "x", verdicts=[v])], 1)
    assert ev["false_positives"] == [] and ev["shared_line_fp"] == {
        "lines": 1, "flagged": 0, "rate": 0.0}
    assert ev["honest_packages"]["zero_shared_verdicts"] == 1
    assert ev["must_fail"]["total"] == 1 and ev["must_fail"]["flagged"] == 0
    assert ev["must_fail"]["by_source"] == {"preview_package": {"flagged": 0, "total": 1}}


# ── pins found by the 2026-09-26 fix-diff review ─────────────────────────────

#: The true positives, literally. Adding or removing one (the direction that turns a failing
#: recall gate into a pass) must be a visible, disclosed edit here — the precedent corrections
#: are pinned by count for the same reason.
_EXPECTED_TPS = {
    ("journey:portfolio_gardening#checklist#r1", "cards[1].title", "judge_directive"),
    ("journey:portfolio_gardening#checklist#r1", "carousel_slides[2].title", "judge_directive"),
    ("journey:portfolio_gardening#checklist#r1", "carousel_slides[2].body", "judge_directive"),
    ("journey:portfolio_gardening#checklist#r1", "captions.youtube_description", "judge_directive"),
    ("journey:portfolio_gardening#checklist#r1", "captions.x", "judge_directive"),
    ("journey:portfolio_gardening#checklist#r1", "captions.threads", "judge_directive"),
    ("journey:portfolio_gardening#checklist#r1", "captions.bluesky", "judge_directive"),
    ("journey:power_of_discipline#question_hook#r1", "carousel_slides[0].body", "judge_return_claim"),
}

#: The round the writer KEPT, per item that needed a repair (every other accepted item kept r1).
_KEPT_REPAIRS = {
    "journey:etfs_101", "journey:fomo_cycle", "money_moves:how-amazon-built-its-moat",
    "money_moves:nvidias-ai-dominance",
}


def test_the_true_positive_set_is_exactly_the_registered_one():
    got = {(r["package_id"], r["label"], r["rule"]) for r in _DOC["judge_true_positives"]}
    assert got == _EXPECTED_TPS


def test_the_accepted_round_per_item_is_pinned():
    for p in _accepted():
        want = 2 if p["item"] in _KEPT_REPAIRS else 1
        assert p["round"] == want, (p["id"], want)


def test_the_fixture_records_a_non_enforcing_run():
    assert _DOC["judge_mode"] == "shadow" and _DOC["allow_x_url"] is False


def _write(tmp_path, doc) -> Path:
    path = tmp_path / "pkgs.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize("mode", ["enforce", None, "ENFORCE"])
def test_calibration_refuses_a_dump_from_an_enforcing_or_unknown_run(tmp_path, mode):
    cal = _calibrate_module()
    doc = dict(_DOC)
    if mode is None:
        doc.pop("judge_mode")
    else:
        doc["judge_mode"] = mode
    with pytest.raises(ValueError, match="judge_mode"):
        cal.build_calls(_write(tmp_path, doc))


def test_calibration_refuses_two_accepted_rounds_of_one_item(tmp_path):
    cal = _calibrate_module()
    doc = json.loads(json.dumps(_DOC))
    for p in doc["packages"]:
        if p["id"] == "journey:fomo_cycle#myth_vs_fact#r1":
            p["accepted"] = True
    with pytest.raises(ValueError, match="more than one accepted round"):
        cal.build_calls(_write(tmp_path, doc))


def test_rule_agreement_counts_package_true_positives():
    """A judge that flags every expected line with its expected rule agrees 100% — the package
    true positives used to count in the denominator only."""
    cal = _calibrate_module()
    calls = cal.build_calls(FIXTURE, holdout=True)
    outcomes = []
    for c in calls:
        vs = [jd.Verdict(case.label, case.expect, case.text[:20], "x", True)
              for case in c.cases if case.expect]
        outcomes.append(cal.Outcome(c, 0, "perfect", verdicts=vs))
    ev = cal.evaluate(outcomes, 1)
    assert ev["must_fail"]["recall"] == 1.0
    assert ev["must_fail"]["rule_agreement"] == 1.0


def _preview_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "marketing_preview.py"
    spec = importlib.util.spec_from_file_location("_marketing_preview", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return mod


class _Res:
    def __init__(self, raws, kept):
        self.raw_outputs = raws
        self.status = "accepted"
        self.package = ws.validate_package(kept, content_pool.get_item(_MM), _RUN_DATE).package


_MM = "journey:mr_market"


def _mm_raw() -> Dict[str, Any]:
    pkg = next(p for p in _accepted() if p["item"] == _MM)
    return _rebuild(pkg["fields"])


def test_a_byte_identical_repair_marks_only_the_first_round_accepted(tmp_path):
    prev = _preview_module()
    raw = _mm_raw()
    out = tmp_path / "d.json"
    prev._dump_packages(out, [(_MM, "question_hook", _RUN_DATE)], [_Res([raw, dict(raw)], raw)],
                        judge_mode="shadow", allow_x_url=False)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert [p["accepted"] for p in doc["packages"]] == [True, False]
    assert doc["judge_mode"] == "shadow" and doc["allow_x_url"] is False


def test_the_dump_validates_with_the_runs_own_x_budget(tmp_path):
    from app.services.marketing import post_copy

    prev = _preview_module()
    raw = _mm_raw()
    item = content_pool.get_item(_MM)
    with_url = post_copy.body_budget("x", item.category, _RUN_DATE, allow_x_url=True)
    without = post_copy.body_budget("x", item.category, _RUN_DATE, allow_x_url=False)
    assert with_url < without
    words = "Every day he names a new price for the same business. "
    body = (words * 20)[: with_url + 3].rsplit(" ", 1)[0].rstrip(".") + "."
    assert with_url < post_copy.measured_length("x", body) <= without, len(body)
    raw["captions"]["x"] = body
    out = tmp_path / "d.json"
    for allow, present in ((True, False), (False, True)):
        prev._dump_packages(out, [(_MM, "question_hook", _RUN_DATE)], [_Res([raw], raw)],
                            judge_mode="shadow", allow_x_url=allow)
        labels = [lab for lab, _t in json.loads(out.read_text(encoding="utf-8"))["packages"][0]["fields"]]
        assert ("captions.x" in labels) is present, (allow, labels)


def test_the_preview_refuses_to_dump_under_an_enforcing_judge(monkeypatch, tmp_path):
    import asyncio

    prev = _preview_module()
    monkeypatch.setattr(sys, "argv", ["marketing_preview.py", "--all-items", "--judge", "enforce",
                                      "--dump-packages", str(tmp_path / "x.json")])
    with pytest.raises(SystemExit) as e:
        asyncio.run(prev.main())
    assert e.value.code == 2


def test_a_truncated_not_ok_first_round_is_not_marked_accepted(tmp_path):
    """W2 2026-09-26: round 1 carried a 5th card (cleaning truncates it), so its keys equal the
    repair that dropped it — but round 1 was not ok and the writer kept round 2."""
    prev = _preview_module()
    kept = _mm_raw()
    r1 = json.loads(json.dumps(kept))
    r1["cards"] = r1["cards"] + [dict(r1["cards"][0])] * (6 - len(r1["cards"]))
    assert not ws.validate_package(r1, content_pool.get_item(_MM), _RUN_DATE).regex_ok
    out = tmp_path / "d.json"
    prev._dump_packages(out, [(_MM, "question_hook", _RUN_DATE)], [_Res([r1, kept], kept)],
                        judge_mode="shadow", allow_x_url=False)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert [(p["regex_ok"], p["accepted"]) for p in doc["packages"]] == [(False, False), (True, True)]


def test_the_preview_refuses_to_dump_an_item_twice(monkeypatch, tmp_path):
    import asyncio

    prev = _preview_module()
    monkeypatch.setattr(prev.selection, "choose",
                        lambda pool, d, recent: type("Sel", (), {"rest_day": False,
                                                                 "source_ref": _MM,
                                                                 "template_id": "question_hook"})())
    monkeypatch.setattr(sys, "argv", ["marketing_preview.py", "--next", "2", "--judge", "shadow",
                                      "--dump-packages", str(tmp_path / "x.json")])
    with pytest.raises(SystemExit) as e:
        asyncio.run(prev.main())
    assert e.value.code == 2
