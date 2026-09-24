"""Preview a month's Emerging Frontiers rotation — what WOULD change — without publishing.

Reads the live `trending_themes` rows, pulls the same licensed data the monthly job uses
(FMP screener, seed-ETF holdings, profiles, revenue segments, price history) and runs the
same relevance check and rules, then prints per theme: kept / added / returned / removed /
deferred with scores and the one-line reasons users would see.

By default it WRITES NOTHING (works even before migration 174 is applied). `--record`
stores a `preview` run and its decisions (needs 174) — never published, never history.

Cost: ~1,100-1,500 FMP calls (flat-fee plan; a few minutes at the rate limit) and a few
hundred relevance checks on first run (~$0.05); later runs hit the verdict cache.

Examples (from backend/):
    ./venv/bin/python -m scripts.preview_theme_rotation --month 2026-10
    ./venv/bin/python -m scripts.preview_theme_rotation --month 2026-10 --slug cyber-wars
    ./venv/bin/python -m scripts.preview_theme_rotation --month 2026-10 --json out.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / "backend" / ".env")

from app.services.theme_rotation.models import Action  # noqa: E402
from app.services.theme_rotation.reasons import user_reason  # noqa: E402
from app.services.theme_rotation.service import ThemeRotationService  # noqa: E402


def _month(value: str) -> date:
    y, m = value.split("-")[:2]
    return date(int(y), int(m), 1)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--month", required=True, help="YYYY-MM (the run month)")
    parser.add_argument("--slug", action="append", help="limit to one theme (repeatable)")
    parser.add_argument("--record", action="store_true", help="store a preview run (needs 174)")
    parser.add_argument("--json", help="also write the full decisions to this JSON file")
    args = parser.parse_args()

    service = ThemeRotationService()
    result = await service.run(_month(args.month), "preview", slugs=args.slug,
                               record=args.record)
    print(f"\nRotation preview for {args.month} — status {result.status}; "
          f"{result.fmp_calls} FMP calls, {result.llm_calls} relevance checks "
          f"({result.llm_failures} failed)")
    if result.skipped_themes:
        print(f"Copied unchanged (no definition / rotation off): {', '.join(result.skipped_themes)}")

    dump = {}
    for slug, plan in result.plans.items():
        print(f"\n━━ {slug}: {len(plan.before)} → {len(plan.after)} stocks · "
              f"{plan.change_count} change(s), ceiling {plan.change_cap}"
              f"{' · SHORT' if plan.shortfall else ''}")
        for d in plan.decisions:
            if d.action in (Action.BENCH, Action.REJECTED) and not (d.rank and d.rank <= 30):
                continue
            line = user_reason(d.action, d.reason, d.score_parts) or ""
            score = f"{d.score:6.2f}" if d.score is not None else "     —"
            rank = f"#{d.rank:<3}" if d.rank else "    "
            parts = d.score_parts or {}
            src = parts.get("exposure_source", "")
            print(f"  {d.action.value:8} {d.ticker:6} {rank} score {score}  "
                  f"[{d.reason.value}{', ' + str(src) if src else ''}]"
                  f"{'  strike' if d.strike else ''}{'  — ' + line if line else ''}")
        dump[slug] = {
            "before": plan.before, "after": plan.after, "added": plan.added,
            "returned": plan.returned, "removed": plan.removed, "deferred": plan.deferred,
            "decisions": [{"ticker": d.ticker, "action": d.action.value, "reason": d.reason.value,
                           "score": d.score, "rank": d.rank, "strike": d.strike,
                           "parts": d.score_parts} for d in plan.decisions],
        }
    if args.json:
        Path(args.json).write_text(json.dumps(dump, indent=2, default=str))
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
