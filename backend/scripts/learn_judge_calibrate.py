#!/usr/bin/env python3
"""
learn_judge_calibrate.py — measure the Learn weekly judge (`app/services/learn_weekly/judge.py`)
against PRE-REGISTERED labels before anyone turns `LEARN_JUDGE_MODE` to enforce (plan "Wiser
weekly content engine" §1.7; the method is marketing.md §7a's).

It runs the REAL judge (backend/.env Gemini key; gemini-3.8-flash, about a cent a call) over the
real drafts of a fixture and scores its verdicts. It writes NOTHING to the database (every
`get_supabase` binding is replaced by a raiser before the first call) and nothing anywhere unless
`--verdicts-out` names a file.

The fixture (`tests/data/learn_judge_calibration_*.json`) holds, per item: the draft (iOS shape),
its fact sheet, its split (train | holdout) and its labels, written from the POLICY before any
verdict was read:
  * `unsupported` — clauses that state, about a named company/deal/filing/event, a purpose,
    cause, effect, significance judgement or specific detail the fact sheet does not;
  * `borderline` — clauses a reasonable reader could go either way on: scored on NEITHER side.

Scoring (per sample, then over samples):
  * recall — the share of `unsupported` labels a `learn_unsupported_claim` verdict hits (same
    field, overlapping word POSITIONS — never a bag of shared words); `recall_any_rule` counts a
    hit by any rule;
  * precision — `learn_unsupported_claim` verdicts that hit an `unsupported` label, over those that
    hit any label or none (verdicts that hit only a `borderline` label are left out);
  * every other rule's verdicts are listed for a human read (no labels exist for them).
Proposed bar for enforcing (pre-registered 2026-10-24, before any run): on the HOLDOUT split,
majority-of-samples recall ≥ 80% and precision ≥ 90%. Tune on the train split only; a holdout miss
is a finding to report, never a reason to copy its line into the rubric (the rubric's examples must
never be calibration lines — tests/test_learn_judge_calibrate.py checks).

Usage (from backend/):
    ./venv/bin/python scripts/learn_judge_calibrate.py --fixture tests/data/learn_judge_calibration_2026_10_24.json \
        --samples 3 --verdicts-out /tmp/learn_judge_verdicts.json
    ./venv/bin/python scripts/learn_judge_calibrate.py --fixture … --verdicts-in /tmp/learn_judge_verdicts.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.services.learn_weekly import contracts as C  # noqa: E402
from app.services.learn_weekly import judge as J  # noqa: E402

RULE = "learn_unsupported_claim"
SPLITS = ("train", "holdout")
#: The pre-registered bar (see the module docstring). Never moved after a run.
GATE_RECALL = 0.80
GATE_PRECISION = 0.90
#: A quote that is not a verbatim run of its field's words (a truncated or reworded claim) is
#: placed on the field's sentence holding at least this share of its CONTENT words.
FUZZY_SHARE = 0.6
#: Words that never decide where a quote sits (review 2026-10-24: "For the fiscal year" matched a
#: different sentence of its field on "for" and "the" alone).
STOPWORDS = frozenset(
    "a an and are as at be been but by for from had has have in into is it its of on or so such "
    "than that the their then these this those to was were which who with".split())

_WORD_RE = re.compile(r"[a-z0-9]+")
_FOLD = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "—": " ",
                       "–": " ", "‑": "-", " ": " "})


class CalibrationDatabaseBlocked(RuntimeError):
    """A calibration code path tried to reach Supabase. The calibrator never touches the DB."""


def _blocked(*_a: Any, **_k: Any) -> Any:
    raise CalibrationDatabaseBlocked("learn_judge_calibrate never touches the database")


def block_database() -> None:
    """Replace `get_supabase` in `app.database` and every imported `app.*` binding of it."""
    import app.database as database

    database.get_supabase = _blocked
    for name, module in list(sys.modules.items()):
        if name.startswith("app.") and getattr(module, "get_supabase", None) is not None:
            setattr(module, "get_supabase", _blocked)


def words(text: Any) -> List[str]:
    return _WORD_RE.findall(str(text or "").translate(_FOLD).lower())


def _run_at(haystack: Sequence[str], needle: Sequence[str]) -> Optional[int]:
    n = len(needle)
    for i in range(len(haystack) - n + 1):
        if list(haystack[i:i + n]) == list(needle):
            return i
    return None


def spans_of(quote: str, field_text: str) -> List[Tuple[int, int]]:
    """Every place [a, b) (word positions) `quote` sits in its field: each verbatim run of the
    field's words (folded); else each run of the quote minus a last word that was cut mid-word
    (the next field word must start with it — quotes are capped mid-word); else the best word
    windows (`_fuzzy_places`). [] when nowhere. A quote found in places that SCORE differently is ambiguous: the
    stored verdict has no sentence index to pick one (re-review 2026-10-24), so the scorer counts
    it on neither side and lists it."""
    fw, qw = words(field_text), words(quote)
    if not qw:
        return []
    found = [(i, i + len(qw)) for i in range(len(fw) - len(qw) + 1) if fw[i:i + len(qw)] == qw]
    if found:
        return found
    if len(qw) > 3:
        cut = qw[:-1]
        found = [(i, i + len(qw)) for i in range(len(fw) - len(qw) + 1)
                 if fw[i:i + len(cut)] == cut and fw[i + len(cut)].startswith(qw[-1])]
        if found:
            return found
    return _fuzzy_places(qw, fw, field_text)


#: In a paraphrase, matched content words more than this many positions apart are separate places.
FUZZY_GAP = 3


def _fuzzy_places(qw: Sequence[str], fw: Sequence[str], field_text: str) -> List[Tuple[int, int]]:
    """A quote that is no verbatim run (a paraphrase): the field positions of the quote's CONTENT
    words, grouped into runs whose neighbours are at most `FUZZY_GAP` apart (inside one sentence,
    each content word once — a repeat starts a new place); the runs holding the
    most of those words (at least `FUZZY_SHARE`) are its places, each from its first to its last
    matched word. Never its whole sentence: a paraphrase of a supported clause would then meet
    the label of the clause beside it (round 6). Tied runs are several places."""
    content = set(qw) - STOPWORDS
    if not content:
        return []
    sentence_of = [k for k, (a, b) in enumerate(_sentence_spans(field_text)) for _ in range(a, b)]
    groups: List[List[int]] = []
    for i, w in enumerate(fw):
        if w not in content:
            continue
        g = groups[-1] if groups else None
        # A run never crosses a sentence end, and a content word met again starts a new place.
        if g and i - g[-1] <= FUZZY_GAP and sentence_of[i] == sentence_of[g[-1]] \
                and w not in {fw[j] for j in g}:
            g.append(i)
        else:
            groups.append([i])
    scored = [(len({fw[i] for i in g}), (g[0], g[-1] + 1)) for g in groups]
    top = max((c for c, _ in scored), default=0)
    if not top or top / len(content) < FUZZY_SHARE:
        return []
    return [span for c, span in scored if c == top]


def span_of(quote: str, field_text: str) -> Optional[Tuple[int, int]]:
    """The first of `spans_of` (None: nowhere)."""
    found = spans_of(quote, field_text)
    return found[0] if found else None


def spans_meet(a: Optional[Tuple[int, int]], b: Optional[Tuple[int, int]]) -> bool:
    return a is not None and b is not None and a[0] < b[1] and b[0] < a[1]


def any_meet(xs: Sequence[Tuple[int, int]], ys: Sequence[Tuple[int, int]]) -> bool:
    return any(spans_meet(x, y) for x in xs for y in ys)


def section_of(label: str) -> int:
    """`sections[3].content[2]` → 3; any header field → -1 (the labellers' convention)."""
    m = re.match(r"sections\[(\d+)\]", label)
    return int(m.group(1)) if m else -1


def locate_exact(quote: str, fields: Sequence[Tuple[str, str]],
                 section: Optional[int] = None) -> List[str]:
    """The field labels holding `quote` as a verbatim run of words (folded), in `section` when one
    there holds it. [] when no field does."""
    qw = words(quote)
    if not qw:
        return []
    exact = [lab for lab, text in fields if _run_at(words(text), qw) is not None]
    same = [lab for lab in exact if section is None or section_of(lab) == section]
    return same or exact


def locate(quote: str, fields: Sequence[Tuple[str, str]], section: Optional[int] = None) -> List[str]:
    """The field labels holding `quote`: verbatim (case- and punctuation-folded) first, then the
    field with most of its words. `section` breaks ties. [] when nothing holds most of it."""
    exact = locate_exact(quote, fields, section)
    if exact:
        return exact
    qs = set(words(quote))
    if not qs:
        return []
    scored = sorted(((len(qs & set(words(text))) / len(qs), lab) for lab, text in fields), reverse=True)
    if not scored or scored[0][0] < 0.8:
        return []
    best = [lab for score, lab in scored if score == scored[0][0]]
    same = [lab for lab in best if section is None or section_of(lab) == section]
    return same or best


def field_of(verdict: Mapping[str, Any]) -> Any:
    """The verdict's field: `label` in `Verdict.as_dict()` (the stored shape), `field` in the
    judge's raw answer."""
    return verdict.get("label", verdict.get("field"))


TP, BORDER, FP, AMBIGUOUS = "tp", "border", "fp", "ambiguous"


def _sentence_spans(field_text: str) -> List[Tuple[int, int]]:
    out, pos = [], 0
    for sentence in J._SENT_RE.split(str(field_text or "")):
        n = len(words(sentence))
        if n:
            out.append((pos, pos + n))
        pos += n
    return out


def classify(places: Sequence[Tuple[FrozenSet[int], FrozenSet[int]]]
             ) -> Tuple[str, FrozenSet[int], FrozenSet[int]]:
    """A verdict's places → (kind, the unsupported labels it is CERTAIN to meet, the borderline
    labels it is CERTAIN to meet). Certain = met in every place.

    Each place is a TP (meets an unsupported label), BORDER (only a borderline one) or FP (none):
    * every place TP → TP, crediting only the labels met in EVERY place (none when they differ:
      it is a correct flag, but which label it found is not known);
    * no place TP → FP when any place is FP (it can never be a hit, so dropping it would always
      flatter the judge — review rounds 4-5), else BORDER;
    * TP beside BORDER or FP → AMBIGUOUS: the stored verdict has no sentence index to pick one, so
      it counts on neither side and is listed. For TP beside BORDER only (never an FP) that is the
      deliberately PESSIMISTIC choice: it can lower the measured recall and precision, never raise
      them (round 6).
    No place at all (unknown field, words nowhere) is FP."""
    if not places:
        return FP, frozenset(), frozenset()
    kinds = {TP if u else BORDER if b else FP for u, b in places}
    if TP in kinds and kinds != {TP}:
        return AMBIGUOUS, frozenset(), frozenset()
    certain_u = frozenset.intersection(*(u for u, _ in places))
    certain_b = frozenset.intersection(*(b for _, b in places))
    if kinds == {TP}:
        return TP, certain_u, certain_b
    return (FP if FP in kinds else BORDER), frozenset(), certain_b


def score_item(item: Mapping[str, Any], verdicts: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """One item, one sample → hits, misses and false positives."""
    fields = J.article_fields(item["draft"])
    texts = dict(fields)
    labels = item.get("labels") or {}

    def placed(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for row in rows or []:
            spans = {lab: spans_of(row["quote"], texts[lab])
                     for lab in locate(row["quote"], fields, row.get("section"))}
            spans = {lab: sp for lab, sp in spans.items() if sp}   # a home with no position is none
            out.append({**row, "fields": list(spans), "spans": spans})
        return out

    unsupported, borderline = placed(labels.get("unsupported")), placed(labels.get("borderline"))
    ambiguous: List[Dict[str, Any]] = []

    def placements(v: Mapping[str, Any]) -> List[Tuple[FrozenSet[int], FrozenSet[int]]]:
        """For each place the verdict's words sit in its field: (unsupported labels it meets,
        borderline labels it meets). [] when its field is unknown or its words are nowhere."""
        quote, where = str(v.get("quote") or ""), field_of(v)
        if where not in texts:
            return []
        # A quote with no words ("", "—") could be any sentence of its field: one place per sentence.
        spans = spans_of(quote, texts[where]) if words(quote) else _sentence_spans(texts[where])

        def meets(rows: Sequence[Mapping[str, Any]], span: Tuple[int, int]) -> FrozenSet[int]:
            return frozenset(i for i, r in enumerate(rows)
                             if where in r["fields"] and any_meet([span], r["spans"][where]))

        return [(meets(unsupported, sp), meets(borderline, sp)) for sp in spans]

    hit_rule, hit_any = set(), set()
    tp, fp, border_only, other = [], [], [], []
    for v in verdicts:
        kind, credited, borders = classify(placements(v))
        if kind == AMBIGUOUS:
            ambiguous.append(dict(v))
            if v.get("rule") != RULE:
                other.append({**v, "hits_unsupported": None, "hits_borderline": None})
            continue
        hit_any.update(credited)
        if v.get("rule") == RULE:
            hit_rule.update(credited)
            {TP: tp, BORDER: border_only, FP: fp}[kind].append(dict(v))
        else:
            other.append({**v, "hits_unsupported": kind == TP, "hits_borderline": bool(borders)})
    return {
        "item": item["id"], "split": item["split"],
        "unsupported": len(unsupported), "borderline": len(borderline),
        "unlocated_labels": [r["quote"] for r in unsupported + borderline if not r["fields"]],
        "hit_rule": sorted(hit_rule), "hit_any": sorted(hit_any),
        "missed": [unsupported[i]["quote"] for i in range(len(unsupported)) if i not in hit_rule],
        "tp": tp, "fp": fp, "borderline_only": border_only, "other_rules": other,
        "ambiguous": ambiguous,
    }


def summarize(fixture: Mapping[str, Any], runs: Mapping[str, Sequence[Mapping[str, Any]]],
              split: str) -> Dict[str, Any]:
    """`runs`: item id → one entry per sample ({"verdicts": [...]} or {"error": ...})."""
    items = [it for it in fixture["items"] if split == "all" or it["split"] == split]
    samples = max((len(runs.get(it["id"], [])) for it in items), default=0)
    # An item with fewer runs than the others (none saved, or a fixture newer than the verdicts)
    # is LEFT OUT and named — never scored as all-miss (re-review 2026-10-24).
    incomplete = [it["id"] for it in items if len(runs.get(it["id"], [])) < samples]
    items = [it for it in items if it["id"] not in incomplete]
    n_labels = sum(len((it.get("labels") or {}).get("unsupported") or []) for it in items)
    hits_per_label: Counter = Counter()
    any_per_label: Counter = Counter()
    tp = fp = errors = 0
    rows: List[Dict[str, Any]] = []
    error_rows: List[Tuple[str, int, str]] = []
    for it in items:
        for s, run in enumerate(runs.get(it["id"], [])):
            if run.get("error"):
                errors += 1
                error_rows.append((it["id"], s, str(run["error"])[:120]))
                continue
            sc = score_item(it, run.get("verdicts") or [])
            sc["sample"] = s
            rows.append(sc)
            for i in sc["hit_rule"]:
                hits_per_label[(it["id"], i)] += 1
            for i in sc["hit_any"]:
                any_per_label[(it["id"], i)] += 1
            tp, fp = tp + len(sc["tp"]), fp + len(sc["fp"])
    majority = (samples // 2) + 1
    measured = bool(n_labels and samples)     # a split with no runs is no measurement, not 0%
    recall_majority = (sum(1 for c in hits_per_label.values() if c >= majority) / n_labels) if measured else None
    recall_ever = (len(hits_per_label) / n_labels) if measured else None
    recall_any_rule = (sum(1 for c in any_per_label.values() if c >= majority) / n_labels) if measured else None
    precision = (tp / (tp + fp)) if (tp + fp) else None
    return {
        "split": split, "items": len(items), "labels": n_labels, "samples": samples, "errors": errors,
        "recall_majority": recall_majority, "recall_ever": recall_ever,
        "recall_any_rule_majority": recall_any_rule, "precision": precision, "tp": tp, "fp": fp,
        # A recall under the bar fails it on its own, even when no verdict made a precision; a
        # split with items left out is not measured whole, so it gets no verdict at all.
        "gate": None if recall_majority is None or incomplete else (
            False if recall_majority < GATE_RECALL else
            None if precision is None else precision >= GATE_PRECISION),
        "rows": rows, "error_rows": error_rows, "incomplete_items": incomplete,
        "ambiguous": sum(len(r["ambiguous"]) for r in rows),
    }


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def print_report(summary: Mapping[str, Any]) -> None:
    s = summary
    print(f"\n=== {s['split']}: {s['items']} items, {s['labels']} unsupported labels, "
          f"{s['samples']} sample(s), {s['errors']} judge error(s)")
    print(f"  recall ({RULE}, majority of samples): {_pct(s['recall_majority'])}"
          f"   ever: {_pct(s['recall_ever'])}   any rule: {_pct(s['recall_any_rule_majority'])}")
    print(f"  precision ({RULE}): {_pct(s['precision'])}  (tp {s['tp']}, fp {s['fp']})")
    if s["gate"] is not None:
        print(f"  proposed enforce bar (recall ≥ {_pct(GATE_RECALL)}, precision ≥ {_pct(GATE_PRECISION)}): "
              f"{'MET' if s['gate'] else 'NOT met'}")
    if s.get("incomplete_items"):
        print(f"  ! NOT measured whole — left out (fewer runs than the others): "
              f"{', '.join(s['incomplete_items'])}")
    if s.get("ambiguous"):
        print(f"  ! {s['ambiguous']} verdict(s) whose words sit in places that score differently "
              "(counted on neither side):")
        for row in s["rows"]:
            for v in row["ambiguous"]:
                print(f"      ? {row['item']} sample {row['sample']} {v.get('rule')} {field_of(v)}: "
                      f"{str(v.get('quote'))[:100]!r}")
    for item_id, sample, error in s.get("error_rows", []):
        print(f"  ! {item_id} sample {sample} errored: {error}")
    first: Dict[str, Mapping[str, Any]] = {}
    for row in s["rows"]:          # each item's FIRST scored sample (sample 0 may have errored)
        first.setdefault(row["item"], row)
    for row in first.values():
        print(f"  - {row['item']} [{row['split']}] sample {row['sample']}: hit "
              f"{len(row['hit_rule'])}/{row['unsupported']} · fp {len(row['fp'])} · other rules "
              f"{len(row['other_rules'])}")
        for q in row["unlocated_labels"]:
            print(f"      ! label not found in the draft: {q[:90]!r}")
        for v in row["fp"]:
            print(f"      fp  {field_of(v)}: {str(v.get('quote'))[:110]!r}")
        for v in row["other_rules"]:
            print(f"      {v.get('rule')}  {field_of(v)}: {str(v.get('quote'))[:100]!r}")


def load_fixture(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for it in data.get("items") or []:
        if it.get("split") not in SPLITS:
            raise SystemExit(f"{it.get('id')}: split must be one of {SPLITS}")
    return data


CHECKS = ("judge", "support", "both")


async def run_judge(fixture: Mapping[str, Any], samples: int, concurrency: int,
                    client: Any = None, check: str = "judge") -> Dict[str, List[Dict[str, Any]]]:
    """`check`: "judge" = the rubric's one call (`judge_article`); "support" = the sentence-level
    support check (`check_support`); "both" = their verdicts together, as the writer records them."""
    block_database()   # before the first call, whoever the client is
    if client is None:
        from app.integrations.gemini import get_gemini_client
        client = get_gemini_client()
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(item: Mapping[str, Any], s: int) -> Tuple[str, int, Dict[str, Any]]:
        sheet = C.FactSheet.from_json(item["sheet"])
        async with sem:
            t = time.monotonic()
            try:
                verdicts, tokens = [], 0
                gen = f"calib-{item['id']}-{s}"
                if check in ("judge", "both"):
                    found, raw = await J.judge_article(client, sheet, item["draft"], generation_id=gen)
                    verdicts, tokens = verdicts + found, tokens + int(raw.get("tokens_used") or 0)
                if check in ("support", "both"):
                    found, meta = await J.check_support(client, sheet, item["draft"], generation_id=gen)
                    verdicts, tokens = verdicts + found, tokens + int(meta.get("tokens_used") or 0)
                return item["id"], s, {"verdicts": [v.as_dict() for v in verdicts], "tokens_used": tokens,
                                       "seconds": round(time.monotonic() - t, 2)}
            except Exception as e:  # recorded, never a pass
                return item["id"], s, {"error": f"{type(e).__name__}: {e}"[:300]}

    done = await asyncio.gather(*(one(it, s) for it in fixture["items"] for s in range(samples)))
    runs: Dict[str, List[Dict[str, Any]]] = {}
    for item_id, s, out in sorted(done, key=lambda t: (t[0], t[1])):
        runs.setdefault(item_id, []).append(out)
    return runs


async def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fixture", required=True, type=Path)
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--split", choices=SPLITS + ("all",), default="all")
    ap.add_argument("--check", choices=CHECKS, default="both")
    ap.add_argument("--thinking", type=int, default=None,
                    help="override JUDGE_THINKING_BUDGET for this run (-1 = the model's default)")
    ap.add_argument("--verdicts-in", type=Path, help="score saved verdicts; no model call")
    ap.add_argument("--verdicts-out", type=Path, help="save the raw verdicts (JSON)")
    args = ap.parse_args(argv)

    if args.verdicts_in and args.thinking is not None:   # before anything is changed
        raise SystemExit("--thinking changes a judge call; --verdicts-in makes none")
    fixture = load_fixture(args.fixture)
    if args.thinking is not None:   # an experiment knob: the shipped value stays in judge.py
        J.JUDGE_THINKING_BUDGET = None if args.thinking < 0 else args.thinking
    if args.verdicts_in:
        saved = json.loads(args.verdicts_in.read_text(encoding="utf-8"))
        runs, rubric = saved["runs"], saved.get("rubric_version")
        model, thinking = saved.get("model", "?"), saved.get("thinking", "?")
    else:
        # A run on one split calls the judge on that split only (tuning on train never reads the
        # holdout's verdicts).
        subset = fixture if args.split == "all" else {
            **fixture, "items": [it for it in fixture["items"] if it["split"] == args.split]}
        runs = await run_judge(subset, args.samples, args.concurrency, check=args.check)
        rubric = f"{J.RUBRIC_VERSION} ({args.check})"
        model, thinking = J.JUDGE_MODEL, J.JUDGE_THINKING_BUDGET
        if args.verdicts_out:
            args.verdicts_out.write_text(json.dumps(
                {"rubric_version": rubric, "model": J.JUDGE_MODEL, "thinking": J.JUDGE_THINKING_BUDGET,
                 "samples": args.samples, "runs": runs},
                indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"rubric {rubric} · model {model} · thinking {thinking}")
    splits = SPLITS if args.split == "all" else (args.split,)
    for split in splits:
        print_report(summarize(fixture, runs, split))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
