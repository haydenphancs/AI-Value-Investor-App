#!/usr/bin/env python
"""Queue one hand-written Money Moves CATALOG article for the Railway learn-narrator.

⚠️ WRITES PRODUCTION (`learn_narration_jobs`) with the service-role key in backend/.env. Without `--yes`
it only reads and prints what it would do.

    cd backend && ./venv/bin/python scripts/learn_catalog_narrate.py --slug coca-cola-vs-pepsico
    cd backend && ./venv/bin/python scripts/learn_catalog_narrate.py --slug coca-cola-vs-pepsico \\
        [--article data/money_moves_drafts/coca-cola-vs-pepsico.json] [--force] --yes

WHY IT EXISTS
-------------
The owner decided on 2026-11-01 ("Yes, build it") that the narrator renders the two hand-written catalog
articles (Coca-Cola vs. PepsiCo, Kodak) so nothing heavy runs on the 16 GB Mac. Migration 207 lets a
narration job narrate an article with no weekly piece (`piece_id` NULL, `catalog_slug` set); the worker
is unchanged. The narrator's Chatterbox render IS the catalog voice (the same reference wav, exaggeration
0.65, cfg 0.40, 165 WPM, 0.6 s block pause, 96k AAC — `narration.CHATTERBOX_VOICE`), and the server
returns the catalog's SENTENCE readAlong / itemsReadAlong shape.

WHAT IT DOES
------------
1. Reads the article: `backend/data/money_moves_drafts/<slug>.json` by default, else the bundled
   `frontend/ios/ios/Resources/MoneyMoves/money_moves.json` (or `--article`). Its slug must be `--slug`.
2. Builds the narration exactly as a weekly piece's (`narration.narration_blocks`, AST-pinned equal to
   the catalog's `clone_learn_audio.narration_blocks`) and refuses a blank block or one over the
   Chatterbox block cap (`narration.check_narratable`, what a weekly enqueue refuses).
3. Refuses (pure, before any write):
   * while a job of this slug is still queued / in_progress;
   * when a READY job of this slug already narrates this exact text (fetch it instead) — unless --force;
   * while a weekly piece is building / drafted / media_pending, or between Thursday 18:00 ET and
     Saturday 00:00 ET (the Deep Dive window: the narrator renders ONE job at a time, oldest first, and a
     ~1 h catalog render must never delay the Friday Deep Dive) — unless --force.
4. With --yes: `ledger.enqueue_catalog_job_sync` — round = 1 + the slug's highest round, engine
   chatterbox, frozen voice / script / sha / PIPELINE_VERSION. The narrator claims it on its next cron
   tick; when it is READY the owner gets a Telegram line naming scripts/learn_catalog_fetch.py.

ORDER: migration 207 applied, THEN the web deploy that carries the catalog branch of
`learn_weekly/narration.py`, THEN this script. A web without that branch would claim the job, look for a
piece that does not exist, and leave it claimed until its lease re-claims run out (failed, no alert).
The narrator image needs no rebuild: the worker never learns which kind of job it renders.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DRAFTS_DIR = _BACKEND / "data" / "money_moves_drafts"
BUNDLE = _BACKEND.parent / "frontend" / "ios" / "ios" / "Resources" / "MoneyMoves" / "money_moves.json"
#: The Deep Dive window, ET: from Thursday at this hour until Saturday 00:00.
WINDOW_START_WEEKDAY = 3          # Thursday (Monday = 0)
WINDOW_START_HOUR = 18
WINDOW_END_WEEKDAY = 5            # Saturday 00:00 closes it


def load_article(slug: str, article: Optional[Path] = None) -> Tuple[Dict[str, Any], Path, bool]:
    """(article, where it was read, whether it came from the bundle). SystemExit when it is missing,
    unreadable or another article. `--article` may be one article's JSON or a bundle."""
    candidates = [article] if article is not None else [DRAFTS_DIR / f"{slug}.json", BUNDLE]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SystemExit(f"{path}: unreadable JSON ({type(e).__name__}: {e})")
        if isinstance(data, dict) and isinstance(data.get("articles"), list):
            found = [a for a in data["articles"] if isinstance(a, dict) and a.get("slug") == slug]
            if len(found) == 1:
                return found[0], path, True
            if article is not None:
                raise SystemExit(f"{path}: holds {len(found)} articles with slug {slug!r}")
            continue
        if not isinstance(data, dict):
            raise SystemExit(f"{path}: not an article object")
        if data.get("slug") != slug:
            raise SystemExit(f"{path} is the article {data.get('slug')!r}, not {slug!r}")
        return data, path, False
    raise SystemExit(f"no article {slug!r} in {', '.join(str(p) for p in candidates)}")


def in_deep_dive_window(now: datetime) -> bool:
    """Thursday 18:00 ET ≤ now < Saturday 00:00 ET (the build is Friday 08:00 ET)."""
    from app.utils.market_hours import ET

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    et = now.astimezone(ET)
    return (et.weekday() == WINDOW_START_WEEKDAY and et.hour >= WINDOW_START_HOUR) or \
        WINDOW_START_WEEKDAY < et.weekday() < WINDOW_END_WEEKDAY


def plan_enqueue(article: Mapping[str, Any], slug: str, *, jobs: Iterable[Mapping[str, Any]],
                 busy_pieces: Iterable[Mapping[str, Any]], now: datetime, force: bool = False) -> Dict[str, Any]:
    """Everything the enqueue writes, or SystemExit naming why it must not run. Pure."""
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import ledger, narration

    try:
        ledger.validate_catalog_slug(slug)
    except ValueError as e:
        raise SystemExit(str(e))
    if article.get("slug") != slug:
        raise SystemExit(f"the article's slug is {article.get('slug')!r}, not {slug!r}")
    try:
        blocks = narration.narration_blocks(article)
        narration.check_narratable(blocks, subject=f"catalog article {slug}")
    except ValueError as e:
        raise SystemExit(f"{slug} cannot be narrated: {e}")
    sha = narration.script_sha256(blocks)
    jobs = list(jobs)
    open_jobs = [j for j in jobs if j.get("status") in (C.JOB_QUEUED, C.JOB_IN_PROGRESS)]
    if open_jobs:
        j = open_jobs[0]
        raise SystemExit(f"{slug}: job {j.get('id')} (round {j.get('round')}) is still {j.get('status')} — wait "
                         "for it (the narrator renders one job at a time)")
    ready_same = [j for j in jobs if j.get("status") == C.JOB_READY and j.get("script_sha256") == sha]
    if ready_same and not force:
        j = ready_same[-1]
        raise SystemExit(f"{slug}: round {j.get('round')} is already READY for this exact text — run "
                         f"scripts/learn_catalog_fetch.py --slug {slug} (or --force to render it again)")
    busy = [p for p in busy_pieces if p.get("status") in (C.BUILDING, C.DRAFTED, C.MEDIA_PENDING)]
    if busy and not force:
        names = ", ".join(f"{p.get('period_key')} ({p.get('status')})" for p in busy[:3])
        raise SystemExit(f"a weekly piece is being built or narrated: {names} — the narrator renders one job at a "
                         "time; enqueue after it reaches review (or --force)")
    if in_deep_dive_window(now) and not force:
        raise SystemExit("it is the Deep Dive window (Thursday 18:00 ET to Saturday 00:00 ET): a catalog render "
                         "must not delay the Friday Deep Dive — enqueue on Saturday (or --force)")
    rounds = [r for r in (j.get("round") for j in jobs) if isinstance(r, int) and not isinstance(r, bool)]
    engine = C.ENGINE_CHATTERBOX         # the catalog voice, whatever LEARN_NARRATOR_ENGINE says
    words = sum(len(b.text.split()) for b in blocks)
    audio_seconds = words / C.NARRATION_WPM * 60
    return {
        "slug": slug, "round": max(rounds, default=0) + 1, "script": narration.script_json(blocks),
        "script_sha256": sha, "engine": engine, "voice": narration.voice_key(narration.voice_spec(engine)),
        "pipeline_version": narration.PIPELINE_VERSION, "blocks": len(blocks), "words": words,
        "audio_minutes": round(audio_seconds / 60, 1),
        "render_minutes": round(audio_seconds * C.RENDER_FACTOR[engine] / 60),
    }


# ── I/O seams (tests replace these) ──────────────────────────────────────────────────────────


def _jobs(slug: str) -> List[Dict[str, Any]]:
    from app.services.learn_weekly import ledger

    return ledger.list_catalog_jobs_sync(slug)


def _busy_pieces() -> List[Dict[str, Any]]:
    from app.services.learn_weekly import contracts as C
    from app.services.learn_weekly import ledger

    return ledger.list_pieces_sync(statuses=[C.BUILDING, C.DRAFTED, C.MEDIA_PENDING], limit=50)


def _enqueue(plan: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    from app.services.learn_weekly import ledger

    return ledger.enqueue_catalog_job_sync(plan["slug"], round=plan["round"], script=plan["script"],
                                           script_sha256=plan["script_sha256"], engine=plan["engine"],
                                           voice=plan["voice"], pipeline_version=plan["pipeline_version"])


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Queue a catalog article for the Railway narrator (WRITES PRODUCTION).")
    p.add_argument("--slug", required=True, help="e.g. coca-cola-vs-pepsico")
    p.add_argument("--article", type=Path, default=None,
                   help="the article JSON (default: data/money_moves_drafts/<slug>.json, else the bundle)")
    p.add_argument("--force", action="store_true",
                   help="enqueue during the Deep Dive window, beside a weekly build, or over a READY round")
    p.add_argument("--yes", action="store_true", help="actually write (default: print only)")
    args = p.parse_args(argv)

    from app.services.learn_weekly.ledger import LearnLedgerError

    article, source, from_bundle = load_article(args.slug, args.article)
    try:
        jobs, busy = _jobs(args.slug), _busy_pieces()
    except LearnLedgerError as e:
        raise SystemExit(f"could not read the narration ledger (is migration 207 applied?): {e}")
    plan = plan_enqueue(article, args.slug, jobs=jobs, busy_pieces=busy, now=datetime.now(timezone.utc),
                        force=args.force)
    print(f"{args.slug}: round {plan['round']} from {source}{' (bundle)' if from_bundle else ''} — "
          f"{plan['blocks']} blocks, {plan['words']} words, ≈{plan['audio_minutes']} min of audio, "
          f"render ≈{plan['render_minutes']} min on the narrator")
    print(f"  engine {plan['engine']} · voice {plan['voice']} · {plan['pipeline_version']} · "
          f"script {plan['script_sha256'][:12]}")
    if not args.yes:
        print("dry run — nothing written (add --yes)")
        return 0
    try:
        job = _enqueue(plan)
    except LearnLedgerError as e:
        raise SystemExit(f"the enqueue failed (is migration 207 applied?): {e}")
    if job is None:
        print(f"round {plan['round']} of {args.slug} already holds a job for ANOTHER script (see the ERROR log) — "
              "nothing queued; re-run to take the next round")
        return 1
    print(f"queued narration job {job.get('id')} ({job.get('status')}). The narrator claims it on its next tick; "
          f"when it is READY: scripts/learn_catalog_fetch.py --slug {args.slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
