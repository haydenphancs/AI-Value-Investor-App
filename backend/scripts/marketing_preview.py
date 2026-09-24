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

Cost: one or two `gemini-2.5-flash` calls per item, no thinking — well under a cent each.
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

from app.services.marketing import content_pool, selection  # noqa: E402
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
                   allow_x_url: bool, show_raw: bool = False) -> Optional[WriterResult]:
    item = content_pool.get_item(item_key)
    template = selection.TEMPLATES_BY_ID[template_id]
    try:
        res = await generate_package(item, template, run_date,
                                     generation_id=f"preview-{uuid.uuid4().hex[:8]}",
                                     allow_x_url=allow_x_url)
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
    args = ap.parse_args()

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

    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def run(job):
        async with sem:
            return await _run_one(*job, show_facts=args.facts, allow_x_url=args.allow_x_url,
                                  show_raw=args.raw)

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
    return 0 if done and len(accepted) == len(done) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
