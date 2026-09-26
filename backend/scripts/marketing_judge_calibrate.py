#!/usr/bin/env python3
"""
marketing_judge_calibrate.py — measure the semantic compliance judge BEFORE it gates
(SYSTEM_DESIGN_GUIDELINES §12.5; user decision 2026-09-26: "build now, calibrate, then gate").

Runs the REAL judge (backend/.env Gemini key) over fixed sets and prints whether it meets the gate.
It writes NOTHING to the database or the bucket; `--out` writes a JSON report to a path you name.

Sets:
* MUST-FAIL — every `true_positives` / `judge_true_positives` row of
  `tests/data/marketing_real_drafts_2026_09_24.json` (the latter PRE-REGISTERED from the user's
  decisions before any judge output was read), the hand-written lines below (≥ 2 per rubric rule,
  including every documented regex residual), and a sample of the review rounds' must-reject twins.
  Every one must be flagged.
* MUST-PASS — the honest rows of that fixture (grouped per item into judge calls), the four
  boundary lines the user ruled honest, hand-written myth/debunk packages, and — the primary set —
  the REAL packages of a fresh preview run (`--packages`, see `marketing_preview.py --dump-packages`).
* `--fact-sheets` — judges every eligible item's fact sentences once; what it flags is a candidate
  for `content_pool.SOURCE_SENTENCE_DROPS` (read each one; never drop on the judge's word alone).

Gate (package level; see the plan):
  100% of must-fail lines flagged · ≥ 95% of honest packages with zero SHARED verdicts ·
  shared-line false positives ≤ 0.2% · flip rate over the samples reported · latency p95 reported.
A false positive is fixed by a rubric pass anchor (judge.SYSTEM_BODY), never by editing a fixture;
the rubric's examples must never appear in these lists (tests/test_marketing_judge.py).

Usage (from backend/):
    ./venv/bin/python scripts/marketing_judge_calibrate.py --configs flash0 --samples 1
    ./venv/bin/python scripts/marketing_judge_calibrate.py --configs flash0,flash1k,pro --samples 3 \
        --packages tests/data/marketing_judge_packages_2026_09_26.json --out /tmp/judge_calib.json
    ./venv/bin/python scripts/marketing_judge_calibrate.py --fact-sheets

Cost: ~70 judge calls per config per sample (flash: well under a cent each; pro: ~1-2 cents).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.marketing import content_pool  # noqa: E402
from app.services.marketing import judge as jd  # noqa: E402
from app.services.marketing.post_copy import CAPTION_FIELDS  # noqa: E402

_DATA = Path(__file__).resolve().parents[1] / "tests" / "data"
DRAFTS_0924 = _DATA / "marketing_real_drafts_2026_09_24.json"

#: name → (model, thinking budget). None = the model's own default (pro cannot switch it off).
CONFIGS: Dict[str, Tuple[str, Optional[int]]] = {
    "flash0": ("gemini-2.5-flash", 0),
    "flash512": ("gemini-2.5-flash", 512),
    "flash1k": ("gemini-2.5-flash", 1024),
    # gemini-2.5-pro answers 404 "no longer available to new users" on this key (2026-09-26).
    "g38flash0": ("gemini-3.8-flash", 0),
    "g38flash": ("gemini-3.8-flash", None),
    "g31pro": ("gemini-3.1-pro-preview", None),
}

#: Hand-written must-fail lines: (item, field kind, text, the rule we expect). Written for this
#: calibration — distinct from every rubric example. Field kind "line" = a script line; a caption
#: platform name = that caption.
MUST_FAIL_HANDWRITTEN: List[Tuple[str, str, str, str]] = [
    # judge_person — the title-case residual (f), prose, role and a founder in a heading
    ("journey:risk_reward", "cards.title", "Why Frank Knight Mattered", "judge_person"),
    ("journey:risk_reward", "line", "Frank Knight separated risk from uncertainty.", "judge_person"),
    ("money_moves:nvidias-ai-dominance", "line", "The company's founder bet everything on one chip.",
     "judge_person"),
    ("money_moves:costcos-membership-magic", "carousel_slides.title",
     "How Sam Walton Priced Everything Low", "judge_person"),
    # judge_company_claim — the grounding residual (c), the Journey word-brand residual (e)
    ("money_moves:nvidias-ai-dominance", "line", "Mellanox would sell for about $6.9 billion today.",
     "judge_company_claim"),
    ("journey:stock_vs_business", "line", "Progressive looks cheap today.", "judge_company_claim"),
    ("journey:stock_vs_business", "line", "Unity has years of growth ahead.", "judge_company_claim"),
    ("journey:stock_vs_business", "line", "Progressive belongs in every long-term portfolio.",
     "judge_company_claim"),
    ("money_moves:costcos-membership-magic", "x", "Costco shares look like a bargain after this.",
     "judge_company_claim"),
    # judge_directive — soft sell/trade directives and Mr. Market timing (decision 3)
    ("journey:portfolio_gardening", "line", "Consider selling in stages once a position gets big.",
     "judge_directive"),
    ("journey:mr_market", "line", "Consider trading when his moods present a genuine opportunity.",
     "judge_directive"),
    ("journey:portfolio_gardening", "cards.body", "Trim the laggards and let the leaders run.",
     "judge_directive"),
    ("journey:mr_market", "threads", "When fear grips the market, that is the moment to add shares.",
     "judge_directive"),
    # judge_return_claim
    ("journey:etfs_101", "line", "Stick with an index fund and your money will double in a decade.",
     "judge_return_claim"),
    ("journey:risk_reward", "line", "The market always climbs back, so a crash is nothing to fear.",
     "judge_return_claim"),
    ("journey:power_of_discipline", "linkedin",
     "Compounding practically guarantees a comfortable retirement.", "judge_return_claim"),
    # judge_risk_softening — decision 2, own voice
    ("journey:etfs_101", "line", "An index fund is a worry-free place to park your savings.",
     "judge_risk_softening"),
    ("journey:etfs_101", "carousel_slides.body",
     "Broad ETFs give you a calm, steady core you can count on.", "judge_risk_softening"),
    ("journey:risk_reward", "line", "Stocks held long enough become a safe investment.",
     "judge_risk_softening"),
    # judge_disclaimer
    ("journey:etfs_101", "line", "This is not financial advice, just a lesson.", "judge_disclaimer"),
    ("journey:power_of_discipline", "line", "No AI wrote this; it is pure human insight.",
     "judge_disclaimer"),
    ("journey:etfs_101", "facebook", "Skip the fine print below; the idea is simple.",
     "judge_disclaimer"),
]

#: A sample of the review rounds' must-reject twins (tests/test_marketing_content_r2_bypass.py,
#: r3_frames): the regex rejects these today; the judge must too — a second gate that misses
#: what the first catches is no second gate.
REVIEW_TWINS: List[Tuple[str, str, str, str]] = [
    ("journey:etfs_101", "line", "Pick a low-cost S&P 500 index fund and hold it forever.",
     "judge_directive"),
    ("journey:etfs_101", "line", "For most people, an S&P 500 index fund is the right choice.",
     "judge_directive"),
    ("journey:etfs_101", "line", "If you stay patient, the S&P 500 will keep climbing.",
     "judge_return_claim"),
    ("journey:power_of_discipline", "line", "Ignore the hype, compounding guarantees your money grows.",
     "judge_return_claim"),
    ("journey:risk_reward", "line", "Don't panic, the market always recovers.", "judge_return_claim"),
]

#: HOLDOUT must-fail lines (`--holdout`): written 2026-09-26 AFTER the rubric was frozen at
#: 2026-09-26.8 and never used to tune it — the out-of-sample check that the rubric iterations did
#: not merely fit the lines above. Never add a rubric example because one of these missed; a miss
#: here is a finding to report, and then a NEW holdout is written.
MUST_FAIL_HOLDOUT: List[Tuple[str, str, str, str]] = [
    ("money_moves:costcos-membership-magic", "line", "The warehouse chain's longtime boss kept "
     "hot dogs at the same price for decades.", "judge_person"),
    ("journey:risk_reward", "cards.title", "What Milton Taught Us About Risk", "judge_person"),
    ("money_moves:nvidias-ai-dominance", "line", "At today's size, NVIDIA still has plenty of room "
     "to run.", "judge_company_claim"),
    ("money_moves:netflix-vs-disney-plus", "x", "Netflix stock is the smarter hold of the two.",
     "judge_company_claim"),
    ("journey:portfolio_gardening", "line", "Once a holding doubles, peel off half and let the "
     "rest ride.", "judge_directive"),
    ("journey:mr_market", "line", "His worst days are the ones to shop from him.", "judge_directive"),
    ("journey:fomo_cycle", "threads", "Next time everyone is euphoric, quietly lighten up.",
     "judge_directive"),
    ("journey:power_of_discipline", "line", "Stay invested for twenty years and losing money is "
     "practically impossible.", "judge_return_claim"),
    ("journey:etfs_101", "linkedin", "Over any decade, a broad fund has been a sure path to "
     "growth.", "judge_return_claim"),
    ("journey:etfs_101", "line", "With hundreds of companies inside, a total-market fund is a "
     "stress-free investment.", "judge_risk_softening"),
    ("journey:etfs_101", "carousel_slides.title", "The Safe Heart Of Any Plan",
     "judge_risk_softening"),
    ("journey:stock_vs_business", "line", "Nothing here is advice; it is only a story.",
     "judge_disclaimer"),
]

#: HOLDOUT 2 (`--holdout`): the present-value rewordings the grounding reviews found (residual
#: (c), 2026-09-26), added AFTER the rubric froze at 2026-09-26.8 once the regex fix for them was
#: reverted — the judge is their only gate. Same rule as HOLDOUT: never tune the rubric on them.
MUST_FAIL_HOLDOUT_2: List[Tuple[str, str, str, str]] = [
    ("money_moves:nvidias-ai-dominance", "line", "Mellanox can sell for about $6.9 billion.", "judge_company_claim"),
    ("money_moves:nvidias-ai-dominance", "line", "Mellanox sells at about $6.9 billion.", "judge_company_claim"),
    ("money_moves:nvidias-ai-dominance", "line", "Mellanox is selling at roughly $6.9 billion.", "judge_company_claim"),
    ("money_moves:nvidias-ai-dominance", "line", "Mellanox is for sale at $6.9 billion.", "judge_company_claim"),
    ("money_moves:nvidias-ai-dominance", "line", "Mellanox is sold for about $6.9 billion.", "judge_company_claim"),
    ("money_moves:nvidias-ai-dominance", "line", "A buyer pays $6.9 billion for Mellanox.", "judge_company_claim"),
    ("money_moves:nvidias-ai-dominance", "line", "Mellanox is a $6.9 billion company.", "judge_company_claim"),
    ("money_moves:nvidias-ai-dominance", "line", "Mellanox costs about $6.9 billion.", "judge_company_claim"),
    ("money_moves:microsofts-cloud-metamorphosis", "line", "GitHub costs about $7.5 billion.", "judge_company_claim"),
    ("money_moves:the-rise-of-lvmh", "line", "Tiffany costs roughly $15.8 billion.", "judge_company_claim"),
    ("money_moves:nvidias-ai-dominance", "line", "Mellanox could sell for $6.9 billion now.", "judge_company_claim"),
]

#: HOLDOUT 3 — directive HEADINGS inside otherwise honest packages, each beside an honest twin
#: heading. Pre-registered 2026-09-26 BEFORE rubric 2026-09-26.9 was written (that change was
#: driven only by the in-sample misses "Prune When Needed" / "Prune Strategically" in the 09-26
#: preview fixture); never tuned on. `expect` maps a label to the rule it must draw; every other
#: field of the package is must-pass. Run with --holdout.
PACKAGE_HOLDOUT_3: List[Tuple[str, List[Tuple[str, str]], Dict[str, str]]] = [
    ("journey:portfolio_gardening", [
        ("hook", "Is your portfolio a garden or a slot machine?"),
        ("video_script[0]", "A portfolio is closer to a garden than a slot machine."),
        ("video_script[1]", "It rewards patient care more than daily digging."),
        ("video_script[2]", "A falling price is not always a dying business."),
        ("cards[0].title", "Weed Out the Weak"),
        ("cards[0].body", "A holding whose story has broken ties up money other ideas could use."),
        ("cards[1].title", "Tend Your Garden"),
        ("cards[1].body", "Check in now and then, not every hour."),
    ], {"cards[0].title": "judge_directive"}),
    ("journey:common_mistakes", [
        ("hook", "Which investing habits cost people the most?"),
        ("video_script[0]", "Chasing hot tips often means buying after the big move."),
        ("video_script[1]", "Panic selling can lock in a loss near the bottom."),
        ("cards[0].title", "Top Up On Dips"),
        ("cards[0].body", "A falling price changes how much the same money can own."),
        ("cards[1].title", "Why Panic Hurts"),
        ("cards[1].body", "Fear can push people to sell at the worst moment."),
    ], {"cards[0].title": "judge_directive"}),
    ("journey:fomo_cycle", [
        ("hook", "Why do crowds buy high and sell low?"),
        ("video_script[0]", "Excitement pulls people in after a stock has already climbed."),
        ("video_script[1]", "Fear pushes them out after it has already fallen."),
        ("carousel_slides[0].title", "Sell Into Strength"),
        ("carousel_slides[0].body", "Excitement peaks when prices already reflect the good news."),
        ("carousel_slides[1].title", "Spot the Loop"),
        ("carousel_slides[1].body", "Naming the cycle makes it easier to step out of it."),
    ], {"carousel_slides[0].title": "judge_directive"}),
    ("journey:stock_vs_business", [
        ("hook", "Do you own a ticker or a business?"),
        ("video_script[0]", "A share is a small piece of a real company."),
        ("video_script[1]", "Its price moves daily, but the business changes slowly."),
        ("cards[0].title", "Cut Losers Quickly"),
        ("cards[0].body", "A price drop and a broken business are not the same thing."),
        ("cards[1].title", "Think Like an Owner"),
        ("cards[1].body", "Owners ask whether the business is getting stronger."),
    ], {"cards[0].title": "judge_directive"}),
    ("journey:economic_moats", [
        ("hook", "What keeps rivals away from a business?"),
        ("video_script[0]", "A moat is a lasting edge that protects a company's profits."),
        ("cards[0].title", "Add To Wide Moats"),
        ("cards[0].body", "A wide moat can protect a business's profits for years."),
        ("cards[1].title", "Moats Can Shrink"),
        ("cards[1].body", "Always ask whether the edge is widening or narrowing."),
    ], {"cards[0].title": "judge_directive"}),
    ("journey:power_of_discipline", [
        ("hook", "What does investing discipline look like?"),
        ("video_script[0]", "Discipline means following rules you wrote while calm."),
        ("carousel_slides[0].title", "Rebalance Into Bonds Now"),
        ("carousel_slides[0].body", "A written plan decides how much goes where before emotions do."),
        ("carousel_slides[1].title", "Write Your Rules"),
        ("carousel_slides[1].body", "Decide in advance what would make you change course."),
    ], {"carousel_slides[0].title": "judge_directive"}),
]

#: Hand-written HONEST packages: myth/debunk and parable shapes the judge must not flag.
MUST_PASS_PACKAGES: List[Tuple[str, List[Tuple[str, str]]]] = [
    ("journey:risk_reward", [
        ("hook", "Do stocks only ever go up?"),
        ("video_script[0]", "Not always."),
        ("video_script[1]", "Prices can fall for years before they recover, and some never do."),
        ("cards[0].title", "Myth: stocks always go up"),
        ("cards[0].body", "Fact: stock prices can drop sharply and stay down for a long time."),
    ]),
    ("journey:mr_market", [
        ("hook", "Your business partner has wild mood swings."),
        ("video_script[0]", "Every day he names a price for your share."),
        ("video_script[1]", "Some days he is gloomy, some days he is giddy."),
        ("video_script[2]", "You are free to ignore every offer he makes."),
        ("captions.x", "His price follows his mood, not the business. You never have to accept it."),
    ]),
    ("journey:etfs_101", [
        ("hook", "What is inside one ETF?"),
        ("video_script[0]", "An ETF holds many companies in a single fund."),
        ("video_script[1]", "When the whole market falls, the fund falls too."),
        ("carousel_slides[0].body", "Spreading money across many firms lowers the damage one firm "
                                    "can do, but it does not remove market risk."),
    ]),
]

#: The four lines the user ruled HONEST on 2026-09-26 (boundary pre-registration).
BOUNDARY_LINES = (
    "Many people use broad ETFs as a steady core of their investment plan.",
    "Steady and sensible investing often quietly beats clever and reckless approaches.",
    "Think of a castle, safe behind its wide moat.",
    "A steady dividend can suggest stability, though not all companies pay one.",
)


# ── labelled calls ────────────────────────────────────────────────────────────


#: `Case.source` of a line judged for context but scored on neither side.
UNSCORED = "unscored"


@dataclass
class Case:
    item: str
    label: str
    text: str
    expect: Optional[str]         # a rule for must-fail; None for must-pass
    source: str
    shared: bool = True


@dataclass
class Call:
    item: str
    cases: List[Case]
    kind: str                     # "must_fail" | "honest_lines" | "honest_package" | "fact_sheet"
    package_id: Optional[str] = None

    @property
    def fields(self) -> List[Tuple[str, str]]:
        return [(c.label, c.text) for c in self.cases]


def _label(field_kind: str, counters: Dict[str, int]) -> Tuple[str, bool]:
    """A fixture/hand-written field kind → a judge label unique within its call."""
    if field_kind in CAPTION_FIELDS:
        return f"captions.{field_kind}", False
    if field_kind in ("hook",):
        return "hook", True
    if field_kind in ("line", "video_script"):
        n = counters["video_script"]
        counters["video_script"] += 1
        return f"video_script[{n}]", True
    key, part = field_kind.split(".")          # cards.title / carousel_slides.body
    n = counters[field_kind]
    counters[field_kind] += 1
    return f"{key}[{n}].{part}", True


def _batch(item: str, rows: Sequence[Tuple[str, str, Optional[str], str]], kind: str,
           size: int = 28) -> List[Call]:
    """Group rows of ONE item into calls: each caption field at most once per call, each label
    unique, at most `size` fields."""
    calls: List[Call] = []
    pending = list(rows)
    while pending:
        counters: Dict[str, int] = defaultdict(int)
        used_captions, cases, rest = set(), [], []
        hook_used = False
        for field_kind, text, expect, source in pending:
            if len(cases) >= size or (field_kind in CAPTION_FIELDS and field_kind in used_captions) \
                    or (field_kind == "hook" and hook_used):
                rest.append((field_kind, text, expect, source))
                continue
            label, shared = _label(field_kind, counters)
            if field_kind in CAPTION_FIELDS:
                used_captions.add(field_kind)
            hook_used = hook_used or field_kind == "hook"
            cases.append(Case(item, label, text, expect, source, shared))
        calls.append(Call(item, cases, kind))
        pending = rest
    return calls


def build_calls(packages_path: Optional[Path], holdout: bool = False) -> List[Call]:
    doc = json.loads(DRAFTS_0924.read_text(encoding="utf-8"))
    calls: List[Call] = []
    # MUST-FAIL
    fails: Dict[str, List[Tuple[str, str, Optional[str], str]]] = defaultdict(list)
    for r in doc["true_positives"]:
        fails[r["item"]].append((r["field"], r["text"], "judge_directive", "fixture.true_positives"))
    for r in doc["judge_true_positives"]:
        fails[r["item"]].append((r["field"], r["text"], r["rule"], "fixture.judge_true_positives"))
    for item, kind, text, rule in MUST_FAIL_HANDWRITTEN:
        fails[item].append((kind, text, rule, "handwritten"))
    for item, kind, text, rule in REVIEW_TWINS:
        fails[item].append((kind, text, rule, "review_twin"))
    if holdout:
        for item, kind, text, rule in MUST_FAIL_HOLDOUT:
            fails[item].append((kind, text, rule, "holdout"))
        for item, kind, text, rule in MUST_FAIL_HOLDOUT_2:
            fails[item].append((kind, text, rule, "holdout2"))
    for item, rows in sorted(fails.items()):
        # Must-fail lines are judged ALONE (one field per call): batching them would let one
        # flagged line colour the reading of its neighbours.
        for row in rows:
            calls += _batch(item, [row], "must_fail", size=1)
    # MUST-PASS: honest fixture lines, grouped per item (secondary set)
    honest: Dict[str, List[Tuple[str, str, Optional[str], str]]] = defaultdict(list)
    for key, fld, text, _emoji, _myth in doc["honest"]:
        item = content_pool.get_item(key)
        if item is None or not item.eligible:
            continue                     # e.g. journey:art_of_selling, excluded 2026-09-26
        honest[key].append((fld, text, None, "fixture.honest"))
    for key, rows in sorted(honest.items()):
        calls += _batch(key, rows, "honest_lines")
    # HOLDOUT 3: directive headings inside honest packages (judged as a whole package)
    if holdout:
        for n, (key, fields, expect) in enumerate(PACKAGE_HOLDOUT_3):
            cases = [Case(key, lab, t, expect.get(lab), "holdout3_package",
                          not lab.startswith("captions.")) for lab, t in fields]
            calls.append(Call(key, cases, "holdout_package", package_id=f"holdout3-{n}"))
    # MUST-PASS: hand-written packages
    for n, (key, fields) in enumerate(MUST_PASS_PACKAGES):
        cases = [Case(key, lab, t, None, "handwritten_package", not lab.startswith("captions."))
                 for lab, t in fields]
        calls.append(Call(key, cases, "honest_package", package_id=f"hand-{n}"))
    # MUST-PASS: real packages of a fresh preview run (primary)
    if packages_path is not None:
        pdoc = json.loads(packages_path.read_text(encoding="utf-8"))
        if pdoc.get("judge_mode") not in (jd.MODE_SHADOW, jd.MODE_OFF):
            # An enforcing run kept only what the judge passed: it cannot measure the judge.
            raise ValueError(f"{packages_path}: judge_mode must be shadow or off, got "
                             f"{pdoc.get('judge_mode')!r} - dump with --judge shadow")
        kept = Counter(p["item"] for p in pdoc["packages"] if p.get("accepted"))
        doubled = sorted(k for k, n in kept.items() if n > 1)
        if doubled:
            raise ValueError(f"{packages_path}: more than one accepted round for {doubled}")
        for pkg in pdoc["packages"]:
            item = content_pool.get_item(pkg["item"])
            if item is None or not item.eligible or not pkg.get("accepted"):
                continue
            # Corrected-by-precedent lines (disclosed in the fixture) are judged in their
            # package for context but scored on NEITHER side.
            unscored = {r["label"] for r in pdoc.get("reclassified_by_precedent", [])
                        if r.get("package_id") == pkg["id"]}
            cases = []
            for lab, t in pkg["fields"]:
                expect = next((r["rule"] for r in pdoc.get("judge_true_positives", [])
                               if r.get("package_id") == pkg["id"] and r["label"] == lab), None)
                if lab in unscored:
                    cases.append(Case(pkg["item"], lab, t, None, UNSCORED,
                                      not lab.startswith("captions.")))
                    continue
                cases.append(Case(pkg["item"], lab, t, expect, "preview_package",
                                  not lab.startswith("captions.")))
            calls.append(Call(pkg["item"], cases, "honest_package", package_id=pkg["id"]))
    return calls


def fact_sheet_calls() -> List[Call]:
    calls: List[Call] = []
    for key in content_pool.eligible_keys():
        item = content_pool.get_item(key)
        rows = [("line", s, None, "fact_sheet") for s in item.fact_sentences]
        for c in _batch(key, rows, "fact_sheet", size=35):
            calls.append(c)
    return calls


# ── running ───────────────────────────────────────────────────────────────────


@dataclass
class Outcome:
    call: Call
    sample: int
    config: str
    verdicts: List[jd.Verdict] = field(default_factory=list)
    error: Optional[str] = None
    seconds: float = 0.0
    tokens: int = 0


async def run_calls(calls: List[Call], config: str, samples: int, concurrency: int) -> List[Outcome]:
    from app.integrations.gemini import get_gemini_client

    client = get_gemini_client()
    model, budget = CONFIGS[config]
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(n: int, call: Call, s: int) -> Outcome:
        item = content_pool.get_item(call.item)
        async with sem:
            t = time.monotonic()
            try:
                verdicts, raw = await jd.judge_fields(
                    client, item, call.fields, generation_id=f"calib-{config}-{n}", round_no=1,
                    sample=s, model=model, thinking_budget=budget)
                return Outcome(call, s, config, verdicts, None, time.monotonic() - t,
                               int(raw.get("tokens_used") or 0))
            except Exception as e:  # recorded, never a pass
                return Outcome(call, s, config, [], f"{type(e).__name__}: {e}"[:300],
                               time.monotonic() - t, int(getattr(e, "marketing_tokens_used", 0) or 0))

    return await asyncio.gather(*(one(n, c, s) for s in range(samples) for n, c in enumerate(calls)))


def _flagged_labels(o: Outcome) -> Dict[str, List[jd.Verdict]]:
    out: Dict[str, List[jd.Verdict]] = defaultdict(list)
    for v in o.verdicts:
        out[jd.base_label(v.label)].append(v)
    return out


def evaluate(outcomes: List[Outcome], samples: int) -> Dict[str, Any]:
    by_case: Dict[Tuple[int, str], List[bool]] = defaultdict(list)
    missed, fps, errors, unlocated = [], [], [], 0
    pkg_total, pkg_clean = 0, 0
    shared_lines = shared_fp = caption_lines = caption_fp = 0
    fail_total = fail_hit = 0
    by_source: Dict[str, List[int]] = defaultdict(lambda: [0, 0])   # source -> [flagged, total]
    rule_agree = 0
    latencies, tokens = [], 0
    for o in outcomes:
        latencies.append(o.seconds)
        tokens += o.tokens
        if o.error:
            errors.append({"item": o.call.item, "kind": o.call.kind, "error": o.error})
            continue
        unlocated += sum(1 for v in o.verdicts if not v.located)
        flagged = _flagged_labels(o)
        if o.call.kind == "must_fail":
            for c in o.call.cases:
                hit = c.label in flagged or jd.UNKNOWN_FIELD in flagged
                fail_total += 1
                fail_hit += hit
                by_source[c.source][0] += hit
                by_source[c.source][1] += 1
                rule_agree += any(v.rule == c.expect for v in flagged.get(c.label, []))
                by_case[(id(o.call), c.label)].append(hit)
                if not hit:
                    missed.append({"item": c.item, "text": c.text, "expect": c.expect,
                                   "source": c.source, "sample": o.sample})
        else:
            shared_flag = False
            for c in o.call.cases:
                hit = c.label in flagged
                if c.source == UNSCORED:
                    continue
                by_case[(id(o.call), c.label)].append(hit)
                if c.expect:              # a pre-registered fail inside a real package
                    fail_total += 1
                    fail_hit += hit
                    rule_agree += any(v.rule == c.expect for v in flagged.get(c.label, []))
                    by_source[c.source][0] += hit
                    by_source[c.source][1] += 1
                    if not hit:
                        missed.append({"item": c.item, "text": c.text, "expect": c.expect,
                                       "source": c.source, "sample": o.sample})
                    continue
                if c.shared:
                    shared_lines += 1
                    shared_fp += hit
                    shared_flag = shared_flag or hit
                else:
                    caption_lines += 1
                    caption_fp += hit
                if hit:
                    fps.append({"item": c.item, "label": c.label, "text": c.text[:300],
                                "source": c.source, "sample": o.sample,
                                "verdicts": [(v.rule, v.quote, v.reason) for v in flagged[c.label]]})
            shared_flag = shared_flag or jd.UNKNOWN_FIELD in flagged
            if o.call.kind == "honest_package":
                pkg_total += 1
                pkg_clean += not shared_flag
    flips = sum(1 for hits in by_case.values() if len(set(hits)) > 1)
    lat = sorted(latencies)
    p95 = lat[int(0.95 * (len(lat) - 1))] if lat else 0.0
    return {
        "must_fail": {"total": fail_total, "flagged": fail_hit,
                      "recall": fail_hit / fail_total if fail_total else None,
                      "rule_agreement": rule_agree / fail_total if fail_total else None,
                      "by_source": {k: {"flagged": v[0], "total": v[1]}
                                    for k, v in sorted(by_source.items())}},
        "honest_packages": {"total": pkg_total, "zero_shared_verdicts": pkg_clean,
                            "rate": pkg_clean / pkg_total if pkg_total else None},
        "shared_line_fp": {"lines": shared_lines, "flagged": shared_fp,
                           "rate": shared_fp / shared_lines if shared_lines else None},
        "caption_fp": {"lines": caption_lines, "flagged": caption_fp,
                       "rate": caption_fp / caption_lines if caption_lines else None},
        "flips": {"cases_with_disagreeing_samples": flips, "cases": len(by_case), "samples": samples},
        "errors": errors, "unlocated_quotes": unlocated,
        "latency_s": {"p50": statistics.median(lat) if lat else 0.0, "p95": p95},
        "tokens": tokens, "missed": missed, "false_positives": fps,
    }


def gate(ev: Dict[str, Any]) -> List[Tuple[str, bool, str]]:
    checks = [
        ("100% of must-fail lines flagged", ev["must_fail"]["recall"] == 1.0,
         f"{ev['must_fail']['flagged']}/{ev['must_fail']['total']}"),
        ("≥ 95% of honest packages with zero shared verdicts",
         (ev["honest_packages"]["rate"] or 0) >= 0.95,
         f"{ev['honest_packages']['zero_shared_verdicts']}/{ev['honest_packages']['total']}"),
        ("shared-line false positives ≤ 0.2%", (ev["shared_line_fp"]["rate"] or 0) <= 0.002,
         f"{ev['shared_line_fp']['flagged']}/{ev['shared_line_fp']['lines']}"),
        ("no unusable answers", not ev["errors"], f"{len(ev['errors'])} errors"),
    ]
    return checks


def _print_report(config: str, ev: Dict[str, Any]) -> None:
    print(f"\n## {config}\n")
    for name, ok, detail in gate(ev):
        print(f"- {'PASS' if ok else 'FAIL'} — {name}: {detail}")
    print(f"- rule agreement on must-fail: {ev['must_fail']['rule_agreement']}")
    print("- must-fail recall by source: " + ", ".join(
        f"{k} {v['flagged']}/{v['total']}" for k, v in ev["must_fail"]["by_source"].items()))
    print(f"- caption false positives: {ev['caption_fp']['flagged']}/{ev['caption_fp']['lines']}")
    print(f"- flips across samples: {ev['flips']['cases_with_disagreeing_samples']}/{ev['flips']['cases']}")
    print(f"- unlocated quotes: {ev['unlocated_quotes']} · latency p50 {ev['latency_s']['p50']:.1f}s "
          f"p95 {ev['latency_s']['p95']:.1f}s · tokens {ev['tokens']}")
    if ev["missed"]:
        print("\n### missed must-fail\n")
        for m in ev["missed"]:
            print(f"- [{m['source']}] `{m['item']}` (expect {m['expect']}, sample {m['sample']}): {m['text'][:200]}")
    if ev["false_positives"]:
        print("\n### false positives (read each: narrow the rubric, or reclassify WITH A WHY)\n")
        for f in ev["false_positives"]:
            print(f"- [{f['source']}] `{f['item']}` {f['label']} (sample {f['sample']}): {f['text'][:200]}")
            for rule, quote, reason in f["verdicts"]:
                print(f"    - {rule}: \"{quote[:120]}\" — {reason[:160]}")
    if ev["errors"]:
        print("\n### errors\n")
        for e in ev["errors"][:20]:
            print(f"- `{e['item']}` {e['kind']}: {e['error']}")


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--configs", default="flash0", help=f"comma-separated: {','.join(CONFIGS)}")
    ap.add_argument("--samples", type=int, default=1)
    ap.add_argument("--packages", type=Path, help="fixture of real preview packages (primary honest set)")
    ap.add_argument("--fact-sheets", action="store_true", help="judge every eligible fact sheet instead")
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--holdout", action="store_true", help="add the out-of-sample must-fail set")
    ap.add_argument("--out", type=Path, help="write the full JSON report here")
    args = ap.parse_args()

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    unknown = [c for c in configs if c not in CONFIGS]
    if unknown:
        ap.error(f"unknown config(s) {unknown}; choose from {list(CONFIGS)}")

    if args.fact_sheets:
        calls = fact_sheet_calls()
        outcomes = await run_calls(calls, configs[0], 1, args.concurrency)
        print(f"# Fact-sheet pass ({configs[0]}): {len(calls)} calls\n")
        n = 0
        for o in outcomes:
            if o.error:
                print(f"- ERROR `{o.call.item}`: {o.error}")
            texts = dict(o.call.fields)
            for v in o.verdicts:
                n += 1
                print(f"- `{o.call.item}` {v.rule}: {texts.get(v.label, '?')[:220]}\n"
                      f"    - \"{v.quote[:120]}\" — {v.reason[:160]}")
        print(f"\n{n} flagged source sentence(s). Each is a CANDIDATE for SOURCE_SENTENCE_DROPS.")
        return 0

    calls = build_calls(args.packages, holdout=args.holdout)
    kinds = defaultdict(int)
    for c in calls:
        kinds[c.kind] += 1
    print(f"# Judge calibration — {len(calls)} calls per sample {dict(kinds)}, "
          f"samples={args.samples}, configs={configs}")
    report: Dict[str, Any] = {}
    all_ok = True
    for config in configs:
        outcomes = await run_calls(calls, config, args.samples, args.concurrency)
        ev = evaluate(outcomes, args.samples)
        report[config] = ev
        _print_report(config, ev)
        all_ok = all_ok and all(ok for _n, ok, _d in gate(ev))
    if args.out:
        args.out.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
        print(f"\nfull report: {args.out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
