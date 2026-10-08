#!/usr/bin/env python3
"""
marketing_preview.py — dry-run the class-A marketing writer for a human read
(SYSTEM_DESIGN_GUIDELINES §12.5; approved plan, Phase 2 "Verify").

Runs selection → writer → validators for sample dates or for every eligible item, with the REAL
Gemini key from backend/.env, and prints Markdown. It writes NOTHING: no marketing_runs row, no
marketing_scripts row, no bucket object. That is the point — sampling future dates through the
worker instead would CLAIM those days (`marketing_runs.run_date` is UNIQUE) and the real day would
later be skipped as "already done".

Usage (from backend/):
    ./venv/bin/python scripts/marketing_preview.py --next 5          # next 5 calendar days (ET)
    ./venv/bin/python scripts/marketing_preview.py --dates 2026-09-24,2026-09-26
    ./venv/bin/python scripts/marketing_preview.py --all-items       # every eligible item once
    ./venv/bin/python scripts/marketing_preview.py --item journey:mr_market --template checklist
    ./venv/bin/python scripts/marketing_preview.py --all-items --judge shadow \
        --dump-packages /tmp/packages.json   # vendor real packages for the judge calibration
    ./venv/bin/python scripts/marketing_preview.py --stats-from /tmp/packages.json   # no model call

Cost: one or two `gemini-2.5-flash` writer calls per item plus up to two judge calls
(`--judge`, default = MARKETING_JUDGE_MODE) — well under a cent each; a 34-item `--all-items` run is
about 0.36-0.44M tokens.

Acceptance of a prompt change (rules/marketing.md §7): run `--all-items --judge shadow --raw
--store-state live --dump-packages` on the OLD prompt and on the new one, then `--stats-from` each dump
(production's store state has been `live` since the 2026-10-05 release). The SHAPE block
(`shape_stats`) estimates each accepted package's video length at the measured narration pace and
counts what the hook rules ask for: Money Moves hooks naming their title's company
(`content_pool.title_companies`), study-verb openers, yes/no and "who wins" hooks, number hooks,
copies of the prompt's example hook, YouTube titles, and investor-framed case-study hooks and titles.
`--store-state` picks the captions' value line (default: what THIS process's MARKETING_APP_STORE_URL and
MARKETING_APP_STORE_PREORDER give, `smart_link.store_state()` — usually `prelaunch` on a laptop).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.marketing import compliance, content_pool, post_copy, selection, smart_link  # noqa: E402
from app.services.marketing import judge as jd  # noqa: E402
from app.services.marketing import writer_prompts as wp  # noqa: E402
from app.services.marketing.writer_service import WriterResult, generate_package  # noqa: E402
from app.utils.market_hours import ET  # noqa: E402


def _md_raw_rounds(res: WriterResult) -> List[str]:
    """Each round's raw model output, so a rejection can be judged from the words it rejected."""
    out: List[str] = []
    for r, raw in zip(res.rounds, res.raw_outputs or []):
        text = raw if isinstance(raw, str) else json.dumps(raw, indent=1, ensure_ascii=False)
        out += ["", f"<details><summary>{r.kind} raw output</summary>", "", "```json", text, "```",
                "</details>"]
    return out


def _md_result(title: str, item, template, run_date: date, res: WriterResult, show_facts: bool,
               show_raw: bool = False) -> str:
    out: List[str] = [f"## {title}", ""]
    out.append(f"- **source**: `{item.key}` — {item.title} ({item.category})")
    out.append(f"- **template**: `{template.id}` — {template.name}")
    out.append(f"- **status**: **{res.status}** · tokens {res.tokens_used} · rounds "
               + ", ".join(f"{r.kind}({len(r.violations)} violations)" for r in res.rounds))
    if show_facts:
        out += ["", "<details><summary>fact sheet</summary>", "", "```", item.fact_text, "```",
                "</details>"]
    for r in res.rounds:
        if r.violations:
            out += ["", f"**{r.kind} violations** ({len(r.violations)}):"]
            out += [f"- `{v['field']}` {v['code']}: {v['detail']}" for v in r.violations[:30]]
        if r.judge is not None:
            j = r.judge
            head = f"**{r.kind} judge** ({j.get('mode')}, {len(j.get('verdicts') or [])} verdicts"
            head += f", ERROR {j['error']}" if j.get("error") else ""
            out += ["", head + "):"]
            out += [f"- `{v['label']}` {v['rule']}: \"{v['quote']}\" — {v['reason']}"
                    for v in (j.get("verdicts") or [])[:30]]
    if show_raw or res.status != "accepted":
        out += _md_raw_rounds(res)
    pkg = res.package
    if pkg:
        out += ["", f"**Hook:** {pkg['hook']}", "", "**Video script:**", ""]
        out += [f"> {line}" for line in pkg["video_script"]]
        out += ["", "**Cards:**"] + [f"- **{c['title']}** — {c['body']}" for c in pkg["cards"]]
        out += ["", "**Carousel:**"] + [f"- **{c['title']}** — {c['body']}" for c in pkg["carousel_slides"]]
        for platform, post in pkg["posts"].items():
            title = f" — title: “{post['title']}”" if post.get("title") else ""
            out += ["", f"**{platform}**{title}", "", "```", post["caption"], "```"]
        if pkg.get("dropped_outlets"):
            out += ["", "**Dropped outlets:** " + ", ".join(
                f"{p} ({', '.join(sorted({v['code'] for v in vs}))})"
                for p, vs in pkg["dropped_outlets"].items())]
    return "\n".join(out)


# ── shape stats (offline: a dump in, numbers out) ──────────────────────────────

#: The narration pace measured in production (2026-10-03: 108 words in 50.6 s, minus 8 × 0.28 s of
#: line pauses) — what `estimated_video_seconds` divides by.
_SPEECH_WORDS_PER_SECOND = 2.23
#: Copies of the worker's `marketing.timings.LINE_PAUSE_SECONDS` and `marketing.voice.
#: DISCLAIMER_CARD_SECONDS` (a test pins them equal; the preview never imports the worker).
_LINE_PAUSE_SECONDS = 0.28
_DISCLAIMER_CARD_SECONDS = 4.0
_STUDY_OPENER_RE = re.compile(r"^\W{0,5}(?:understand|learn|discover|master|explore|find)\b", re.IGNORECASE)
_YES_NO_RE = re.compile(r"^\W{0,5}(?:(?:is|are|was|were|can|could|do|does|did|will|would|should|has|have|had)"
                        r"(?:n['\u2019]t)?|won['\u2019]t)\b[^?]{0,200}\?", re.IGNORECASE)
#: One leading label ("Apple's Services: …", "Myth: …") — the counters read what follows it too
#: (review 2026-10-07: "Apple's Services: a hidden problem for investors?" was counted as no yes/no).
_LABEL_RE = re.compile(r"^[^?:]{1,60}:\s*")
#: A question holding a wh-word asks how, why or what (or who, which, when, where) — not yes/no.
_WH_WORD_RE = re.compile(r"\b(?:how|why|what|who|whom|whose|which|when|where)\b", re.IGNORECASE)
#: A label that opens on a wh-word is the question's own stem ("What matters more: subscribers or
#: profit?" asks what), never a label to read past.
_WH_OPENER_RE = re.compile(r"^\W{0,5}(?:how|why|what|who|whom|whose|which|when|where)\b", re.IGNORECASE)
#: A yes/no question after a subordinate clause: a comma clause that opens on an auxiliary and runs to
#: the "?" ("When Netflix raised prices, did subscribers leave?") — never after a wh-word that a comma
#: follows, which opens a wh-question with an insert ("Why, after years of growth, did Netflix stall?").
_WH_INSERT_RE = re.compile(r"^\W{0,5}(?:how|why|what|who|whom|whose|which|when|where)\s*,", re.IGNORECASE)
_AUX_CLAUSE_RE = re.compile(r",\s+(?:and\s+|but\s+|so\s+)?(?:is|are|was|were|can|could|do|does|did|will|would|"
                            r"should|has|have|had)\b[^,?]*\?", re.IGNORECASE)
_WHO_WINS_RE = re.compile(r"\bwho\b[^?]{0,80}\b(?:win|wins|won|winning)\b", re.IGNORECASE)
#: A digit or a number word — "no one" / "no-one" is nobody, not a number (review 2026-10-07; the hook's
#: whitespace is collapsed first, `_package_shape`).
_NUMBER_RE = re.compile(r"[0-9]|\b(?:(?<!\bno[\s\-\u2010])one|two|three|four|five|six|seven|eight|nine|ten)\b",
                        re.IGNORECASE)
#: An investor-framed verdict on a business — whether it is good or bad, a problem or an opportunity,
#: for investors or shareholders (HOOK AND TITLES bans it; review 2026-10-07: "Apple's Services: a
#: hidden problem for investors?" was accepted). Up to three words may stand between "for"/"to" and the
#: audience ("good for its shareholders", "a problem for Apple investors"). Counted on case studies
#: only — a lesson's "why patience is good for investors" is no verdict on a business. Prompt only: the
#: judge's rubric has no rule for it yet (the planned judge round), so the preview counts it and a
#: person reads it.
_INVESTOR_FRAMED_RE = re.compile(
    r"\b(?:problems?|opportunit(?:y|ies)|threats?|good|bad)\b[^.?!]{0,40}?\b(?:for|to)\s+(?:[\w'\u2019-]+\s+){0,3}"
    r"(?:investors?|shareholders?|stockholders?|owners?)\b"
    r"|\b(?:good|bad|great|terrible|grim|welcome|big)\s+news\s+for\b|\bgood or bad\b", re.IGNORECASE)
#: The example hook the prompt quotes (HOOK AND TITLES): a copy is counted, never wanted.
_EXAMPLE_HOOK = "Why can a profitable company still run out of cash?"
_LINE_RE = re.compile(r"video_script\[(\d+)\]")


def _unlabelled(text: str) -> str:
    """`text` without one leading "Label:" (a label longer than 60 characters, or a "?" before the
    colon, is no label)."""
    return _LABEL_RE.sub("", text or "", count=1)


def is_yes_no_question(text: str) -> bool:
    """A yes/no question, as the prompt bans it from hooks and titles. One that opens on an auxiliary
    (`_YES_NO_RE`, with or without one leading label) is one. A label that opens on a wh-word is the
    question's own stem ("What matters more: subscribers or profit?" asks what). Otherwise EVERY
    question is read with only its own sentence (`compliance.sentences`, which keeps "Mr." and "vs."
    whole): it is yes/no when it opens on an auxiliary ("…when prices rose. Can it recover?"), when a
    comma clause opening on one runs to its "?" ("When Netflix raised prices, did subscribers
    leave?"), or when it holds no wh-word at all ("Profitable yet broke?", "Apple's Services: a hidden
    problem for investors?"). A question that asks how, why, what, who, which, when or where ("Software
    or steel: who wins the car race?", 'Beyond the obvious: ask "and then what?"') is not one; a
    verbless one holding a subordinate "when" is a known miss."""
    text = " ".join((text or "").split())
    rest = _unlabelled(text)
    label = text[: len(text) - len(rest)]
    if _YES_NO_RE.search(text) or _YES_NO_RE.search(rest):
        return True
    if label and _WH_OPENER_RE.search(label):
        return False
    for sentence in compliance.sentences(rest):
        if "?" not in sentence:
            continue
        asked = sentence[: sentence.rindex("?") + 1]
        if _YES_NO_RE.search(asked) or (_AUX_CLAUSE_RE.search(asked) and not _WH_INSERT_RE.search(asked)):
            return True
        if re.search(r"[A-Za-z]", asked) and _WH_WORD_RE.search(asked) is None:
            return True
    return False


def estimated_video_seconds(hook: str, lines: List[str]) -> float:
    """A package's video length estimate: its spoken words at the measured pace, a pause after every
    narrated line but the last (the hook is line 0) and the disclaimer card."""
    words = len((hook or "").split()) + sum(len((line or "").split()) for line in lines)
    return words / _SPEECH_WORDS_PER_SECOND + _LINE_PAUSE_SECONDS * len(lines) + _DISCLAIMER_CARD_SECONDS


def _names(text: str, name: str) -> bool:
    """Does `text` name `name` as a word ("Meta's" yes, "Metaverse" no)?"""
    return re.search(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", text or "", re.IGNORECASE) is not None


def _package_shape(row: Dict[str, Any]) -> Dict[str, Any]:
    raw_fields = row.get("fields")
    # A malformed entry (not a [label, text] pair of strings) is skipped, never a crash in the tool.
    fields = {entry[0]: entry[1] for entry in (raw_fields if isinstance(raw_fields, list) else [])
              if isinstance(entry, (list, tuple)) and len(entry) == 2
              and isinstance(entry[0], str) and isinstance(entry[1], str)}
    numbered = []
    for lab, text in fields.items():
        m = _LINE_RE.fullmatch(lab)
        if m:
            numbered.append((int(m.group(1)), text))
    lines = [text for _i, text in sorted(numbered)]
    # Whitespace collapsed: a double space must not hide "no one" from `_NUMBER_RE`, or split a label.
    return {"hook": " ".join((fields.get("hook") or "").split()), "lines": lines,
            "title": " ".join((fields.get("captions.youtube_title") or "").split())}


def shape_stats(rows: List[Dict[str, Any]], *, line_floor: int = wp._ASK_SCRIPT_LINE_WORDS_MIN) -> Dict[str, Any]:
    """The shape of the ACCEPTED packages of a dump (`--dump-packages` rows), plus how many rounds of
    any status fall outside the enforced script window — the numbers a prompt change is accepted on."""
    out: Dict[str, Any] = {"accepted": 0}
    outside = 0
    words: List[int] = []
    line_counts: Counter = Counter()
    per_line: List[int] = []
    hooks_words: List[int] = []
    videos: List[float] = []
    mm_total = mm_named = mm_titles_named = 0
    study = yes_no = who_wins = numbers = copies = yes_no_titles = investor_hooks = investor_titles = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        shape = _package_shape(row)
        n_words = sum(len(line.split()) for line in shape["lines"])
        hook_words = len(shape["hook"].split())
        if not (wp.SCRIPT_MIN_WORDS <= n_words <= wp.SCRIPT_MAX_WORDS
                and wp.SCRIPT_MIN_LINES <= len(shape["lines"]) <= wp.SCRIPT_MAX_LINES
                and hook_words <= wp.HOOK_MAX_WORDS):
            outside += 1
        if not row.get("accepted"):
            continue
        out["accepted"] += 1
        words.append(n_words)
        line_counts[len(shape["lines"])] += 1
        per_line += [len(line.split()) for line in shape["lines"]]
        hooks_words.append(hook_words)
        videos.append(estimated_video_seconds(shape["hook"], shape["lines"]))
        hook, title = shape["hook"], shape["title"]
        study += bool(_STUDY_OPENER_RE.search(hook) or _STUDY_OPENER_RE.search(_unlabelled(hook)))
        yes_no += is_yes_no_question(hook)
        who_wins += bool(_WHO_WINS_RE.search(hook))
        numbers += bool(_NUMBER_RE.search(hook))
        copies += hook.strip().lower() == _EXAMPLE_HOOK.lower()
        yes_no_titles += is_yes_no_question(title)
        item = content_pool.get_item(str(row.get("item") or ""))
        if item is not None and item.kind == content_pool.MONEY_MOVES:
            mm_total += 1
            investor_hooks += bool(_INVESTOR_FRAMED_RE.search(hook))
            investor_titles += bool(_INVESTOR_FRAMED_RE.search(title))
            names = content_pool.title_companies(item)
            mm_named += any(_names(hook, n) for n in names)
            mm_titles_named += any(_names(title, n) for n in names)
    if words:
        out.update({
            "script_words": {"median": statistics.median(words), "min": min(words), "max": max(words)},
            "lines": dict(sorted(line_counts.items())),
            "exactly_6_share": line_counts.get(6, 0) / len(words),
            "mean_words_per_line": round(statistics.mean(per_line), 2) if per_line else None,
            "lines_under_floor": f"{sum(1 for n in per_line if n < line_floor)}/{len(per_line)}",
            "hook_words": {"median": statistics.median(hooks_words), "max": max(hooks_words)},
            "est_video_s": {"median": round(statistics.median(videos), 1), "min": round(min(videos), 1),
                            "max": round(max(videos), 1), "over_40": sum(1 for v in videos if v > 40)},
            "mm_hooks_naming_title_company": f"{mm_named}/{mm_total}",
            "study_openers": study, "yes_no_hooks": yes_no, "who_wins_hooks": who_wins,
            "number_hooks": numbers, "example_copies": copies,
            "mm_youtube_titles_naming_company": f"{mm_titles_named}/{mm_total}",
            "yes_no_youtube_titles": yes_no_titles,
            "mm_investor_framed_hooks": investor_hooks, "mm_investor_framed_youtube_titles": investor_titles,
        })
    out["rounds_outside_enforced_window"] = outside
    return out


def _print_shape(stats: Dict[str, Any]) -> None:
    print("\n## Shape (accepted packages)\n")
    for key, value in stats.items():
        print(f"- {key}: {json.dumps(value)}")


def _rows_of(path: Path) -> List[Dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("packages") if isinstance(doc, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


async def _run_one(item_key: str, template_id: str, run_date: date, show_facts: bool,
                   allow_x_url: bool, show_raw: bool = False,
                   judge_mode: str = jd.MODE_ENFORCE,
                   store_state: str = post_copy.STORE_PRELAUNCH) -> Optional[WriterResult]:
    item = content_pool.get_item(item_key)
    template = selection.TEMPLATES_BY_ID[template_id]
    try:
        res = await generate_package(item, template, run_date,
                                     generation_id=f"preview-{uuid.uuid4().hex[:8]}",
                                     allow_x_url=allow_x_url, judge_mode=judge_mode,
                                     store_state=store_state)
    except Exception as e:  # a preview reports and moves on; it never retries or spends more
        print(f"## {run_date} — {item_key}\n\n**ERROR** {type(e).__name__}: {e}\n", flush=True)
        return None
    print(_md_result(f"{run_date} — {item.title}", item, template, run_date, res, show_facts,
                     show_raw),
          "\n", flush=True)
    return res


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dates", help="comma-separated YYYY-MM-DD")
    ap.add_argument("--next", type=int, help="the next N calendar days from today (ET)")
    ap.add_argument("--all-items", action="store_true", help="every eligible item once")
    ap.add_argument("--item", help="one item key, e.g. journey:mr_market")
    ap.add_argument("--template", help="template id (with --item)")
    ap.add_argument("--limit", type=int, default=0, help="cap the number of items (all-items)")
    ap.add_argument("--facts", action="store_true", help="print each fact sheet")
    ap.add_argument("--allow-x-url", action="store_true")
    ap.add_argument("--raw", action="store_true",
                    help="print every round's raw model output (always printed for a rejection)")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--judge", choices=jd.MODES, default=jd.normalize_mode(settings.MARKETING_JUDGE_MODE),
                    help="semantic judge mode for this preview (default: MARKETING_JUDGE_MODE)")
    ap.add_argument("--dump-packages", type=Path,
                    help="write every package the run produced (judge-visible fields, every "
                         "round) as a calibration fixture to this path")
    ap.add_argument("--stats-from", type=Path,
                    help="print the shape stats of an existing --dump-packages file and exit (no model call)")
    ap.add_argument("--store-state", choices=post_copy.STORE_STATES, default=smart_link.store_state(),
                    help="the captions' value line (default: what THIS process's MARKETING_APP_STORE_URL / "
                         "MARKETING_APP_STORE_PREORDER give; production has been 'live' since the app "
                         "launched 2026-10-05, so pass --store-state live to measure its budgets)")
    args = ap.parse_args()
    if args.stats_from:
        _print_shape(shape_stats(_rows_of(args.stats_from)))
        return 0
    if args.dump_packages and args.judge == jd.MODE_ENFORCE:
        # An enforcing judge picks WHICH round is kept and rejects items it flags twice, so the
        # dumped honest set would hold only packages the judge already passed — calibration
        # could never see its own false positives.
        ap.error("--dump-packages needs --judge shadow or --judge off")

    today = datetime.now(ET).date()
    pool = content_pool.eligible_keys()
    print(f"# Marketing writer preview — {today.isoformat()} (ET)\n\n"
          f"{len(pool)} eligible items. Nothing is written anywhere.\n", flush=True)

    jobs = []  # (item_key, template_id, run_date)
    if args.item:
        t = args.template or selection.choose_template(args.item.split(":", 1)[0], 0)
        jobs.append((args.item, t, today))
    elif args.all_items:
        keys = pool[: args.limit] if args.limit else pool
        for i, key in enumerate(keys):
            jobs.append((key, selection.choose_template(key.split(":", 1)[0], i), today))
    else:
        if args.dates:
            dates = [date.fromisoformat(d.strip()) for d in args.dates.split(",") if d.strip()]
        else:
            dates = [today + timedelta(days=i) for i in range(args.next or 5)]
        recent: List[str] = []
        for d in dates:
            sel = selection.choose(pool, d, recent)
            if sel.rest_day:
                print(f"## {d} ({d:%a}) — rest day\n", flush=True)
                continue
            recent.insert(0, sel.source_ref)
            jobs.append((sel.source_ref, sel.template_id, d))

    if args.dump_packages and len({j[0] for j in jobs}) != len(jobs):
        # Package ids are item#template#round and calibration allows one kept round per item;
        # a long --next repeats an item once the pool is exhausted. Refused before any spend.
        ap.error("--dump-packages needs each item at most once (use --all-items or a shorter --next)")

    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def run(job):
        async with sem:
            return await _run_one(*job, show_facts=args.facts, allow_x_url=args.allow_x_url,
                                  show_raw=args.raw, judge_mode=args.judge, store_state=args.store_state)

    results = await asyncio.gather(*(run(j) for j in jobs))
    done = [r for r in results if r is not None]
    accepted = [r for r in done if r.status == "accepted"]
    codes = Counter(v["code"] for r in done for rd in r.rounds for v in rd.violations)
    first_pass = sum(1 for r in done if r.rounds and not r.rounds[0].violations)
    print("\n# Summary\n")
    print(f"- jobs: {len(jobs)} · completed: {len(done)} · errors: {len(jobs) - len(done)}")
    if done:
        print(f"- accepted: {len(accepted)}/{len(done)} ({100 * len(accepted) // len(done)}%) · "
              f"clean on the first draft: {first_pass}")
        print(f"- tokens: {sum(r.tokens_used for r in done)}")
        print(f"- violation codes across all rounds: {json.dumps(codes.most_common(25))}")
        by_field = Counter(f"{_field_kind(v['field'])}:{v['code']}"
                           for r in done for rd in r.rounds for v in rd.violations)
        print(f"- by field kind: {json.dumps(by_field.most_common(30))}")
        verdicts = Counter(v["rule"] for r in done for rd in r.rounds if rd.judge
                           for v in rd.judge.get("verdicts") or [])
        errors = sum(1 for r in done for rd in r.rounds if rd.judge and rd.judge.get("error"))
        print(f"- judge ({args.judge}): verdicts {json.dumps(verdicts.most_common())} · errors {errors}")
        dropped = Counter(p for r in done if r.package for p in r.package.get("dropped_outlets") or {})
        print(f"- dropped outlets on accepted packages: {json.dumps(dropped.most_common())}")
    if args.dump_packages:
        _dump_packages(args.dump_packages, jobs, results, judge_mode=args.judge,
                       allow_x_url=args.allow_x_url, store_state=args.store_state)
        _print_shape(shape_stats(_rows_of(args.dump_packages)))
    return 0 if done and len(accepted) == len(done) else 1


def _field_kind(name: str) -> str:
    """`video_script[3]` → `video_script`, `cards[1].body` → `cards.body`, `x` → `x`."""
    import re as _re

    return _re.sub(r"\[\d+\]", "", name)


def _dump_packages(path: Path, jobs, results, *, judge_mode: str, allow_x_url: bool,
                   store_state: str = post_copy.STORE_PRELAUNCH) -> None:
    """The calibration fixture (scripts/marketing_judge_calibrate.py --packages): every parsed
    round of every job, as the judge sees it. `accepted` marks the package the writer kept."""
    out = []
    for (key, template_id, run_date), res in zip(jobs, results):
        if res is None:
            continue
        kept = False
        for n, raw in enumerate(res.raw_outputs or []):
            if not isinstance(raw, dict):
                continue
            item = content_pool.get_item(key)
            from app.services.marketing.writer_service import validate_package

            # The run's own X budget: without it a caption the run dropped (and the judge never
            # read) would be dumped as a must-pass line.
            vr = validate_package(raw, item, run_date, allow_x_url=allow_x_url, store_state=store_state)
            fields = jd.package_fields(vr.package or {})
            # Every model-written key, not just hook + script: a repair that keeps both but
            # rewrites a caption must not mark the draft round accepted too. Not `posts` — in
            # enforce mode the judge drops outlets from the accepted package's posts.
            # `regex_ok` too: a round-1 with an extra card is truncated by cleaning to the same
            # keys as the repair that dropped it, but it was not ok and the writer kept round 2.
            # (With the judge in shadow or off, ok == regex_ok, the writer's own test.)
            accepted = bool(res.package) and bool(vr.package) and vr.regex_ok and all(
                res.package.get(k) == vr.package.get(k)
                for k in ("hook", "video_script", "cards", "carousel_slides", "captions"))
            # A byte-identical repair matches too; the writer keeps the EARLIER round on a tie.
            accepted, kept = accepted and not kept, kept or accepted
            out.append({"id": f"{key}#{template_id}#r{n + 1}", "item": key, "template": template_id,
                        "round": n + 1, "status": res.status, "accepted": accepted,
                        "regex_ok": vr.regex_ok, "fields": [[lab, t] for lab, t in fields]})
    doc = {
        "_about": ("Real writer packages from one marketing_preview run "
                   f"({datetime.now(ET).isoformat(timespec='minutes')}, prompt "
                   f"{__import__('app.services.marketing.writer_prompts', fromlist=['x']).PROMPT_VERSION}). "
                   "Judge-visible fields of every parsed round. Classify policy breaks into "
                   "`judge_true_positives` (package_id, label, text, rule, why) BEFORE reading any "
                   "judge verdict; never edit a field."),
        "judge_mode": judge_mode,
        "allow_x_url": allow_x_url,
        "store_state": store_state,
        "packages": out,
        "judge_true_positives": [],
    }
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{len(out)} packages written to {path}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
