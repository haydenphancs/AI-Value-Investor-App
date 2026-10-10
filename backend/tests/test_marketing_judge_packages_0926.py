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
from app.services.marketing import post_copy
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
        elif lab == "image_post.title":
            obj.setdefault("image_post", {"title": "", "paragraphs": []})["title"] = text
        elif re.fullmatch(r"image_post\.paragraphs\[\d+\]", lab):
            obj.setdefault("image_post", {"title": "", "paragraphs": []})["paragraphs"].append(text)
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


#: The captions whose body budget the code-owned suffix sizes. Since 2026-10-05 that suffix carries
#: the VALUE LINE ("Caydex: AI research on public companies — …"), which took 60-71 characters (the
#: 59-69-character line plus its separator) from the budgets the 09-26 captions were written to — so
#: these, and only these, may now be dropped.
_VALUE_LINE_SIZED = ("x", "threads", "bluesky")


def _value_line_drops(pkg: Dict[str, Any], store_state: str) -> List[str]:
    """Validate a kept 09-26 package under `store_state`; return the platforms whose caption it
    lost. Everything else is the over-block gate, asserted here: the package still passes, every
    field it keeps is byte-identical and in order, and a lost caption was dropped ONLY as `too_long`,
    ONLY on an x/threads/bluesky caption, and ONLY by what the value line costs (the caption fit
    the run's own budget, which had no value line)."""
    item = content_pool.get_item(pkg["item"])
    vr = ws.validate_package(_rebuild(pkg["fields"]), item, _RUN_DATE, store_state=store_state)
    where = (pkg["id"], store_state)
    assert vr.regex_ok, (where, [(v.field, v.code, v.detail) for v in vr.violations])
    dropped = sorted(vr.outlets)
    captions = _rebuild(pkg["fields"])["captions"]
    for platform in dropped:
        assert platform in _VALUE_LINE_SIZED, (where, platform, vr.outlets[platform])
        assert [(v.field, v.code) for v in vr.outlets[platform]] == [(platform, "too_long")], (
            where, [(v.field, v.code, v.detail) for v in vr.outlets[platform]])
        # What the line takes from this platform's budget: itself plus its separator — on X it is
        # the whole CTA, a "\n\n"-separated part of its own (+2); elsewhere it opens "Learn more:"
        # after one space (+1). Every character of it weighs 1, on X too (the em dash included).
        line_cost = len(post_copy.value_line(store_state)) + (2 if platform == "x" else 1)
        n = post_copy.measured_length(platform, captions[platform])
        budget = post_copy.body_budget(platform, item.category, _RUN_DATE, store_state=store_state)
        assert budget < n <= budget + line_cost, (where, platform, n, budget, line_cost)
    assert sorted(vr.package["dropped_outlets"]) == dropped, where
    assert all([d["code"] for d in vr.package["dropped_outlets"][p]] == ["too_long"] for p in dropped)
    gone = {f"captions.{p}" for p in dropped}
    kept = [f for f in pkg["fields"] if f[0] not in gone]
    assert len(kept) == len(pkg["fields"]) - len(dropped), where   # each lost caption was there
    assert [list(x) for x in jd.package_fields(vr.package or {})] == kept, where
    return dropped


@pytest.mark.parametrize("store_state", post_copy.STORE_STATES)
@pytest.mark.parametrize("pkg", _accepted(), ids=lambda p: p["id"])
def test_an_accepted_package_still_passes_the_validators_unchanged(pkg, store_state):
    """The over-block gate for the 09-26 run: a validator change that starts rejecting what the
    writer really produced (or rewrites it in cleaning) fails here, not in production. Since the
    value line (2026-10-05) an x/threads/bluesky caption written to the OLD budget may be dropped
    as too_long — nothing else may change (`_value_line_drops`); how many is pinned below. In every
    store state: production writes `live` since the App Store release (2026-10-05), and a
    pre-order or a URL misconfiguration (prelaunch) must not reach a different over-block."""
    _value_line_drops(pkg, store_state)


#: How many of the 32 kept captions per platform the value line's budget no longer fits, per store
#: state (measured 2026-10-05; the line is 40 / 69 / 59 characters — prelaunch re-measured 2026-10-07,
#: when it lost "— coming soon to iPhone." and went from 64 characters and 18 X + 8 Bluesky drops to
#: 3 X). They were written to the OLD asks; the new prompt asks for the new budget. A further budget
#: change moves these numbers: make it a deliberate edit here, never a silent drift (the preview run
#: measures the new drafts).
_VALUE_LINE_DROPS = {
    "prelaunch": {"x": 3},
    "preorder": {"x": 21, "bluesky": 9},
    "live": {"x": 14, "bluesky": 4},
}


@pytest.mark.parametrize("store_state", sorted(_VALUE_LINE_DROPS))
def test_the_value_line_drops_exactly_the_pinned_09_26_captions(store_state):
    assert set(_VALUE_LINE_DROPS) == set(post_copy.STORE_STATES)
    got: Counter = Counter()
    for pkg in _accepted():
        got.update(_value_line_drops(pkg, store_state))
    assert dict(got) == _VALUE_LINE_DROPS[store_state]


def test_a_longer_value_line_never_keeps_a_caption_a_shorter_one_drops():
    """Budgets shrink as the line grows (prelaunch 40 < live 59 < pre-order 69 characters), so per
    package the drops nest: prelaunch ⊆ live ⊆ pre-order. A caption kept under a LONGER line but
    dropped under a shorter one would mean a budget not driven by the line alone."""
    lines = {s: len(post_copy.value_line(s)) for s in post_copy.STORE_STATES}
    assert lines[post_copy.STORE_PRELAUNCH] < lines[post_copy.STORE_LIVE] < lines[post_copy.STORE_PREORDER]
    for pkg in _accepted():
        pre, live, order = (set(_value_line_drops(pkg, s)) for s in (
            post_copy.STORE_PRELAUNCH, post_copy.STORE_LIVE, post_copy.STORE_PREORDER))
        assert pre <= live <= order, (pkg["id"], pre, live, order)


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


_MM_X_WORDS = ("Every day he names a new price for the same business. " * 20).split()


def _x_body_just_over(budget: int) -> str:
    """The shortest body of whole words from `_MM_X_WORDS` (closed with a full stop) that X measures
    OVER `budget`: it overshoots by at most one word (≤ 10 characters with its space)."""
    body = ""
    for word in _MM_X_WORDS:
        body = f"{body} {word}".strip()
        if post_copy.measured_length("x", body.rstrip(".") + ".") > budget:
            return body.rstrip(".") + "."
    raise AssertionError(f"the words never exceed {budget}")


def test_the_dump_validates_with_the_runs_own_x_budget(tmp_path):
    prev = _preview_module()
    raw = _mm_raw()
    item = content_pool.get_item(_MM)
    with_url = post_copy.body_budget("x", item.category, _RUN_DATE, allow_x_url=True)
    without = post_copy.body_budget("x", item.category, _RUN_DATE, allow_x_url=False)
    assert with_url < without
    # Built word by word, not sliced: the gap (24, the URL's weight + its space) is wider than any
    # word, so the body lands inside it whatever the budgets are (a slice landed ON with_url once
    # the value line moved both).
    body = _x_body_just_over(with_url)
    assert with_url < post_copy.measured_length("x", body) <= without, len(body)
    raw["captions"]["x"] = body
    out = tmp_path / "d.json"
    for allow, present in ((True, False), (False, True)):
        prev._dump_packages(out, [(_MM, "question_hook", _RUN_DATE)], [_Res([raw], raw)],
                            judge_mode="shadow", allow_x_url=allow)
        labels = [lab for lab, _t in json.loads(out.read_text(encoding="utf-8"))["packages"][0]["fields"]]
        assert ("captions.x" in labels) is present, (allow, labels)


def test_the_dump_validates_with_the_runs_own_store_state(tmp_path):
    """`--store-state` reaches the dump's validation and is recorded in it: the value line sizes the
    X budget, so an X body that fits under the live line but not under the (longer) pre-order line
    is a must-pass line only in the state whose budget kept it — a dump validated with another
    state would hand calibration a caption the run dropped, or hide one it kept."""
    prev = _preview_module()
    raw = _mm_raw()
    item = content_pool.get_item(_MM)
    preorder = post_copy.body_budget("x", item.category, _RUN_DATE, store_state=post_copy.STORE_PREORDER)
    live = post_copy.body_budget("x", item.category, _RUN_DATE, store_state=post_copy.STORE_LIVE)
    assert preorder < live
    body = _x_body_just_over(preorder)
    assert preorder < post_copy.measured_length("x", body) <= live, (len(body), preorder, live)
    raw["captions"]["x"] = body
    out = tmp_path / "d.json"
    for state, present in ((post_copy.STORE_PREORDER, False), (post_copy.STORE_LIVE, True)):
        prev._dump_packages(out, [(_MM, "question_hook", _RUN_DATE)], [_Res([raw], raw)],
                            judge_mode="shadow", allow_x_url=False, store_state=state)
        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["store_state"] == state
        labels = [lab for lab, _t in doc["packages"][0]["fields"]]
        assert ("captions.x" in labels) is present, (state, labels)


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


# ── shape stats: what a prompt change is accepted on (2026-10-05, shorter videos) ──────────────


def test_shape_stats_on_the_0926_fixture():
    """The BASELINE the 2026-10-05 prompt is measured against, from the 09-26 run (asked: 7-9 lines
    of 10-16 words, hence line_floor=10). Every value was computed offline AND re-derived by an
    independent count before it was pinned; a counter or estimate that moves must be a deliberate
    edit. `number_hooks` counts a digit OR a number word (one…ten): the two hooks are "Risk and
    uncertainty: two kinds of unknown." and "How one app changed the game…" — no hook has a digit."""
    prev = _preview_module()
    assert prev.shape_stats(_packages(), line_floor=10) == {
        "accepted": 32,
        "script_words": {"median": 83.5, "min": 61, "max": 108},
        "lines": {8: 14, 9: 18},
        "exactly_6_share": 0.0,
        "mean_words_per_line": 9.81,
        "lines_under_floor": "118/274",
        "hook_words": {"median": 7, "max": 12},
        "est_video_s": {"median": 47.2, "min": 35.8, "max": 57.6, "over_40": 30},
        "mm_hooks_naming_title_company": "4/15",
        "study_openers": 6,
        "yes_no_hooks": 1,
        "who_wins_hooks": 2,
        "number_hooks": 2,
        "example_copies": 0,
        "mm_youtube_titles_naming_company": "11/15",
        "yes_no_youtube_titles": 0,
        "mm_investor_framed_hooks": 0,
        "mm_investor_framed_youtube_titles": 0,
        # The 09-26 run predates the post image (Drop 1): no package has an image title.
        "image_titles": "0/32",
        "yes_no_image_titles": 0,
        "mm_image_titles_naming_company": "0/15",
        "mm_investor_framed_image_titles": 0,
        "rounds_outside_enforced_window": 0,
    }
    assert not any(ch.isdigit() for p in _accepted() for ch in _fields(p)["hook"])


def test_shape_stats_counts_the_post_image_title_under_the_hook_rules():
    """Drop 1: the image title follows HOOK AND TITLES, so the acceptance counters read it — a counter
    that never saw it would report a clean "0" for a rule nobody measured."""
    prev = _preview_module()
    row = next(p for p in _packages() if p.get("accepted"))
    yes_no = dict(row, fields=list(row["fields"]) + [["image_post.title", "Is this the best stock to own?"],
                                                     ["image_post.paragraphs[0]", "A paragraph."]])
    plain = dict(row, fields=list(row["fields"]) + [["image_post.title", "How a balance sheet works"]])
    stats = prev.shape_stats([yes_no, plain])
    assert stats["image_titles"] == "2/2"
    assert stats["yes_no_image_titles"] == 1
    none = prev.shape_stats([row])
    assert none["image_titles"] == "0/1" and none["yes_no_image_titles"] == 0


def test_rebuild_reads_the_post_image_labels_back():
    obj = _rebuild([["hook", "h"], ["image_post.title", "T"], ["image_post.paragraphs[0]", "P1"],
                    ["image_post.paragraphs[1]", "P2"]])
    assert obj["image_post"] == {"title": "T", "paragraphs": ["P1", "P2"]}


def test_shape_stats_measures_lines_under_the_asked_floor_by_default():
    prev = _preview_module()
    from app.services.marketing import writer_prompts as wp

    default = prev.shape_stats(_packages())["lines_under_floor"]
    assert default == prev.shape_stats(_packages(), line_floor=wp._ASK_SCRIPT_LINE_WORDS_MIN)["lines_under_floor"]
    per_line = [len(t.split()) for p in _accepted() for lab, t in p["fields"]
                if re.fullmatch(r"video_script\[\d+\]", lab)]
    assert default == f"{sum(1 for n in per_line if n < wp._ASK_SCRIPT_LINE_WORDS_MIN)}/{len(per_line)}"


def test_estimated_video_seconds_for_a_six_line_script():
    """An 8-word hook and 6 lines of 11 words: 74 words / 2.23 words/s = 33.18 s, + 6 line pauses
    × 0.28 s = 1.68 s, + the 4 s disclaimer card = 38.86 s."""
    prev = _preview_module()
    hook = " ".join(["word"] * 8)
    lines = [" ".join(["word"] * 11)] * 6
    assert round(prev.estimated_video_seconds(hook, lines), 1) == 38.9
    # The hook is narrated but adds no pause of its own; an empty hook is no words.
    assert prev.estimated_video_seconds("", []) == pytest.approx(4.0)
    assert prev.estimated_video_seconds("a b", ["c"]) == pytest.approx(3 / 2.23 + 0.28 + 4.0)


def test_the_previews_pause_and_card_copies_equal_the_workers():
    """The preview copies the worker's narration timings instead of importing the worker; the copies
    must not drift from what the video really does."""
    from marketing import timings, voice

    prev = _preview_module()
    assert prev._LINE_PAUSE_SECONDS == timings.LINE_PAUSE_SECONDS
    assert prev._DISCLAIMER_CARD_SECONDS == voice.DISCLAIMER_CARD_SECONDS


@pytest.mark.parametrize("counter, text, hit", [
    # A study verb OPENS the hook (leading punctuation allowed), as a whole word.
    ("_STUDY_OPENER_RE", "Understand a company's financial snapshot.", True),
    ("_STUDY_OPENER_RE", "“Find businesses with lasting advantages.", True),
    ("_STUDY_OPENER_RE", "Master key numbers for any company.", True),
    ("_STUDY_OPENER_RE", "Findings surprised the market.", False),
    ("_STUDY_OPENER_RE", "Why learn the balance sheet?", False),
    # A yes/no question opens on an auxiliary and ends on "?".
    ("_YES_NO_RE", "Can discipline help you become a better investor?", True),
    ("_YES_NO_RE", "Is a stock the same as a business?", True),
    ("_YES_NO_RE", "Island economies grew fast?", False),
    ("_YES_NO_RE", "Who is Mr. Market and what does he want?", False),
    ("_YES_NO_RE", "How does AI affect investing?", False),
    ("_YES_NO_RE", "Is this the end.", False),
    # "Who wins / will win" anywhere in the hook.
    ("_WHO_WINS_RE", "Who wins the streaming wars?", True),
    ("_WHO_WINS_RE", "Software or steel: who wins the car race?", True),
    ("_WHO_WINS_RE", "Who will win the chip race?", True),
    ("_WHO_WINS_RE", "Who is Mr. Market?", False),
    # A digit or a number word, as a word.
    ("_NUMBER_RE", "Risk and uncertainty: two kinds of unknown.", True),
    ("_NUMBER_RE", "3 lessons from a duopoly.", True),
    ("_NUMBER_RE", "Someone gave money to a tone-deaf idea.", False),
    ("_NUMBER_RE", "No one saw the shift coming.", False),       # nobody, not a number (2026-10-07)
    ("_NUMBER_RE", "No-one saw it coming.", False),
    ("_NUMBER_RE", "Anyone can read a balance sheet.", False),
    ("_NUMBER_RE", "No company sold more than one product.", True),
])
def test_each_hook_counter_reads_the_shape_it_names(counter, text, hit):
    """The acceptance gates ("0 study-verb / yes-no / who-wins / number hooks") are only as good as
    these counters: each has a must-count and a must-not-count twin."""
    prev = _preview_module()
    assert bool(getattr(prev, counter).search(text)) is hit, (counter, text)


@pytest.mark.parametrize("text, hit", [
    # opens on an auxiliary, with or without one leading label
    ("Can discipline help you become a better investor?", True),
    ("Netflix: can it keep growing?", True),
    # a question with no wh-word before its "?" — verbless or declarative (review 2026-10-07: the first
    # two were accepted hooks of the 10-05 preview that the opener-only counter missed)
    ("Apple's Services: a hidden problem for investors?", True),
    ("The Home Depot vs. Lowe's: the same paint?", True),
    ("Profitable yet broke?", True),
    ("Island economies grew fast?", True),
    ("Netflix lost subscribers in 2022. Can it recover?", True),
    # a wh-word anywhere before the "?" asks how, why, what, who, which, when or where
    ("Why can a profitable company still run out of cash?", False),
    ("Software or steel: who wins the car race?", False),
    ('Beyond the obvious: ask "and then what?"', False),
    ("Netflix vs. Disney+: The Streaming Wars", False),         # no question at all
    ("Is this the end.", False), ("", False), ("?", False),
    # re-review 2026-10-07: the label is read past (an auxiliary after it, a "when" later on)…
    ("Netflix: can it grow when TV shrinks?", True),
    ("Myth: does a buyback always help when prices fall?", True),
    # …but a label that opens on a wh-word is the question's own stem
    ("What matters more: subscribers or profit?", False),
    ("How did NVIDIA win: chips or software?", False),
    ("Who wins the car race: software or steel?", False),
    # every question is read with only its own sentence; a comma clause opening on an auxiliary counts
    ("When Netflix raised prices, did subscribers leave?", True),
    ("If Apple's margins fell, did its moat break?", True),
    ("Netflix lost subscribers when prices rose. Can it recover?", True),
    ("Who is Mr. Market and what does he want?", False),
    ("Why did Apple win? It sold services.", False),
    # final round (2026-10-08): a wh-word then a comma opens a wh-question with an insert, not a
    # subordinate clause; a contracted negative auxiliary opens a yes/no question too
    ("Why, after years of growth, did Netflix stall?", False),
    ("How, if at all, did Apple adapt?", False),
    ("Isn't profit the same as cash?", True),
    ("Don't stocks always recover?", True),
    ("Won't a moat last forever?", True),
    # …and only the contracted opener tells these from a wh-question (a later wh-word would veto them)
    ("Isn't that why Netflix stalled?", True),
    ("Don't investors know what they own?", True),
    # final round follow-ups (2026-10-08): an insert after the SUBJECT of a direct wh-question…
    ("Why does Costco, a warehouse club, have such loyal members?", False),
    ("What did Netflix, of all companies, do right?", False),
    # …a conjunction-led second question after one…
    ("What is a moat, and does Costco have one?", True),
    # …a contracted auxiliary in a comma clause or a tag…
    ("When Netflix raised prices, didn't subscribers leave?", True),
    ("Costco knows why it wins, doesn't it?", True),
    # …an auxiliary opener counts only in the sentence that asks…
    ("Don't panic. What drives prices?", False),
    ("Is it real: what makes a moat?", True),
    # …a labelled auxiliary question beats a wh-label; a conjunction lead-in; a curly contraction…
    ("What changed: can Netflix keep growing?", True),
    ("Netflix grew when prices rose, but can it last?", True),
    ("Isn’t that why Netflix stalled?", True),
    # …and every wh-word, in a question and as a label's opener
    ("When did Netflix change course?", False),
    ("Where does Costco's profit come from?", False),
    ("Which costs matter most?", False),
    ("Why it matters: subscribers or profit?", False),
    ("Whose moat is wider: Visa's or Mastercard's?", False),
])
def test_a_yes_no_question_is_read_through_a_label_and_without_an_auxiliary(text, hit):
    """The gate "0 yes/no hooks" counted only a hook OPENING on an auxiliary, so a labelled or
    verbless yes/no question passed it unseen (review 2026-10-07)."""
    prev = _preview_module()
    assert prev.is_yes_no_question(text) is hit, text


@pytest.mark.parametrize("text, hit", [
    ("Apple's Services: a hidden problem for investors?", True),     # the 10-05 preview's accepted hook
    ("Is Netflix's ad tier an opportunity for shareholders?", True),
    ("Good news for Costco?", True),
    ("Tesla's price cuts: good or bad?", True),
    ("A good sign for owners?", True),
    # re-review 2026-10-07: words between "for"/"to" and the audience, a singular, other news adjectives
    ("Great News for Shareholders?", True),
    ("Is the deal good for its shareholders?", True),
    ("An opportunity for long-term investors?", True),
    ("A Problem for Apple Investors?", True),
    ("A threat to shareholders?", True),
    ("Hidden problems for investors?", True),                       # plural verdict nouns (2026-10-08)
    ("Two opportunities for shareholders", True),
    ("Threats to owners?", True),
    ("What problem did Netflix solve?", False),
    ("A simple rule for investors", False),
    ("Why does bad news move prices more than good news?", False),
    ("Opportunity cost, explained", False),
    ("Why diversification lowers risk for investors", False),
])
def test_the_investor_framed_counter_reads_a_verdict_for_investors(text, hit):
    """HOOK AND TITLES bans "whether it is good or bad, a problem or an opportunity, for investors or
    shareholders"; no validator or judge rule enforces it yet (the planned judge round), so the preview
    counts it for the person reading the run."""
    prev = _preview_module()
    assert bool(prev._INVESTOR_FRAMED_RE.search(text)) is hit, text


def test_shape_stats_counts_through_the_new_readers():
    """The 09-26 pins cannot tell the new readers from the old ones (no labelled, verbless or
    investor-framed hook there): rows built to differ prove `shape_stats` itself uses them."""
    prev = _preview_module()
    line = " ".join(["word"] * 11)

    def row(hook: str, title: str, item: str = "money_moves:apples-services-revolution") -> Dict[str, Any]:
        return {"accepted": True, "item": item,
                "fields": [["hook", hook], ["captions.youtube_title", title]]
                + [[f"video_script[{i}]", line] for i in range(6)]}

    stats = prev.shape_stats([
        row("Apple's Services: a hidden problem for investors?", "Netflix: can it keep growing?"),
        row("Apple: Understand its moat.", "Good news for Costco?"),
        row("No  one saw the shift coming.", "Why the shift mattered", "journey:mr_market"),   # two spaces
        # an investor-framed line in a LESSON is no verdict on a business: not counted
        row("Why patience is good for investors", "A good sign for owners?", "journey:mr_market"),
    ])
    assert (stats["yes_no_hooks"], stats["yes_no_youtube_titles"]) == (1, 3)
    assert (stats["mm_investor_framed_hooks"], stats["mm_investor_framed_youtube_titles"]) == (1, 1)
    assert (stats["study_openers"], stats["number_hooks"]) == (1, 0)


@pytest.mark.parametrize("text, rest", [
    ("Apple: Understand its moat.", "Understand its moat."),
    ("Myth: stocks always go up.", "stocks always go up."),
    ("Why? Because: reasons", "Why? Because: reasons"),            # a "?" before the colon: no label
    ("A" * 61 + ": then", "A" * 61 + ": then"),                     # longer than 60: no label
    ("No label here.", "No label here."), ("", ""),
])
def test_one_leading_label_is_read_past(text, rest):
    prev = _preview_module()
    assert prev._unlabelled(text) == rest
    assert bool(prev._STUDY_OPENER_RE.search(prev._unlabelled("Apple: Understand its moat.")))


def _prompt_list(pattern: str) -> List[str]:
    """A comma list SYSTEM_BODY's HOOK AND TITLES paragraph spells out ("A, B, C or D")."""
    from app.services.marketing import writer_prompts as wp

    m = re.search(pattern, wp.SYSTEM_BODY)
    assert m, f"HOOK AND TITLES no longer reads {pattern!r}: re-point this parity test"
    return [w.strip() for w in m.group(1).split(",")] + [m.group(2)]


def test_every_opener_the_prompt_bans_is_one_the_preview_counts():
    """The acceptance gates read the preview's counters, the model reads the prompt: a verb the
    prompt adds to its ban but the counter does not know would pass the gate unseen. Each opener the
    prompt names, starting a hook, must be counted (and the lists are pinned whole, so a reworded
    sentence fails here instead of matching nothing)."""
    prev = _preview_module()
    study = _prompt_list(r"instruction to study - not ([A-Za-z, ]+?) or ([A-Za-z]+)\.")
    yes_no = _prompt_list(r"one that opens with ([A-Za-z, ]+?) or ([A-Za-z]+) \(")
    assert study == ["Understand", "Learn", "Discover", "Master", "Explore", "Find"]
    assert yes_no == ["Is", "Are", "Do", "Does", "Did", "Can", "Will", "Should"]
    for verb in study:
        assert prev._STUDY_OPENER_RE.search(f"{verb} the balance sheet."), verb
    for aux in yes_no:
        assert prev._YES_NO_RE.search(f"{aux} the market ever stay calm?"), aux
    assert prev._WHO_WINS_RE.search("Who will win the chip race?")   # "never who will win"


def test_the_previews_example_hook_is_the_one_the_prompt_quotes():
    """`example_copies` counts copies of the prompt's example hook by its own literal: if the prompt's
    example changed and the preview's did not, every copy would go uncounted (gate: ≤ 1)."""
    from app.services.marketing import writer_prompts as wp

    prev = _preview_module()
    assert wp.SYSTEM_BODY.count(f'"{prev._EXAMPLE_HOOK}"') == 1
    line = " ".join(["word"] * 11)
    row = {"accepted": True, "item": "journey:mr_market",
           "fields": [["hook", prev._EXAMPLE_HOOK]] + [[f"video_script[{i}]", line] for i in range(6)]}
    assert prev.shape_stats([row])["example_copies"] == 1


@pytest.mark.parametrize("text, name, hit", [
    ("Meta's metaverse bet", "Meta", True),           # a following apostrophe still names it
    ("The metaverse bet", "Meta", False),             # a longer word does not
    ("Myth: Home Depot and Lowe's are the same.", "Lowe", True),
    ("Netflix vs. Disney+: The Streaming Wars", "Disney", True),
    ("Nvidia's AI lead began with gaming chips.", "NVIDIA", True),   # any case
    ("Visas and passports", "Visa", False),
])
def test_a_title_company_is_matched_as_a_word(text, name, hit):
    prev = _preview_module()
    assert prev._names(text, name) is hit


def test_shape_stats_degrades_on_an_empty_or_odd_dump(tmp_path):
    """An empty dump has no shape (no medians of nothing); a non-object row is skipped; a round with
    no script counts as outside the enforced window, never as a pass."""
    prev = _preview_module()
    assert prev.shape_stats([]) == {"accepted": 0, "rounds_outside_enforced_window": 0}
    assert prev.shape_stats(["junk", None, 7, {"accepted": False, "fields": []}]) == {
        "accepted": 0, "rounds_outside_enforced_window": 1}
    for doc, rows in (([], []), ({"no": "packages"}, []), ({"packages": None}, []),
                      ({"packages": ["x", {"id": "a"}]}, [{"id": "a"}])):
        path = tmp_path / "d.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        assert prev._rows_of(path) == rows, doc


def test_stats_from_needs_no_model_call(monkeypatch, capsys):
    """`--stats-from` reads a dump and prints its shape — before any job is planned, so it never
    reaches the writer (which would spend Gemini tokens)."""
    import asyncio

    prev = _preview_module()

    async def no_model_call(*_a, **_k):
        raise AssertionError("--stats-from reached the writer")

    monkeypatch.setattr(prev, "generate_package", no_model_call)
    monkeypatch.setattr(sys, "argv", ["marketing_preview.py", "--stats-from", str(FIXTURE)])
    assert asyncio.run(prev.main()) == 0
    out = capsys.readouterr().out
    assert "## Shape (accepted packages)" in out
    assert '- mm_hooks_naming_title_company: "4/15"' in out
    assert "# Marketing writer preview" not in out   # returned before planning any job
