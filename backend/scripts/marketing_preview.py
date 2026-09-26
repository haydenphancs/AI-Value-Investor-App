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

Cost: one or two `gemini-2.5-flash` writer calls per item plus up to two judge calls
(`--judge`, default = MARKETING_JUDGE_MODE) — well under a cent each.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.marketing import content_pool, selection  # noqa: E402
from app.services.marketing import judge as jd  # noqa: E402
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


async def _run_one(item_key: str, template_id: str, run_date: date, show_facts: bool,
                   allow_x_url: bool, show_raw: bool = False,
                   judge_mode: str = jd.MODE_ENFORCE) -> Optional[WriterResult]:
    item = content_pool.get_item(item_key)
    template = selection.TEMPLATES_BY_ID[template_id]
    try:
        res = await generate_package(item, template, run_date,
                                     generation_id=f"preview-{uuid.uuid4().hex[:8]}",
                                     allow_x_url=allow_x_url, judge_mode=judge_mode)
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
    args = ap.parse_args()
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
                                  show_raw=args.raw, judge_mode=args.judge)

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
                       allow_x_url=args.allow_x_url)
    return 0 if done and len(accepted) == len(done) else 1


def _field_kind(name: str) -> str:
    """`video_script[3]` → `video_script`, `cards[1].body` → `cards.body`, `x` → `x`."""
    import re as _re

    return _re.sub(r"\[\d+\]", "", name)


def _dump_packages(path: Path, jobs, results, *, judge_mode: str, allow_x_url: bool) -> None:
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
            vr = validate_package(raw, item, run_date, allow_x_url=allow_x_url)
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
        "packages": out,
        "judge_true_positives": [],
    }
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n{len(out)} packages written to {path}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
