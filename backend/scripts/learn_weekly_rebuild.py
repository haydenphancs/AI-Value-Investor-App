#!/usr/bin/env python
"""Reopen one Wiser weekly piece for another build — the owner's manual retry.

⚠️ WRITES PRODUCTION (`learn_weekly_pieces`, `learn_narration_jobs`) with the service-role key in
backend/.env. Without `--yes` it only prints what it would do.

    cd backend && ./venv/bin/python scripts/learn_weekly_rebuild.py --period 2026-W43:deep_dive
    cd backend && ./venv/bin/python scripts/learn_weekly_rebuild.py --period 2026-W43:deep_dive \\
        [--next-topic] [--publish-at 2026-10-24T13:00:00-04:00] --yes

WHEN TO USE IT
--------------
A piece ended `failed`, `skipped`, `owner_rejected` or `expired` and the week still deserves its
Deep Dive. (While a piece is IN REVIEW, use the Telegram buttons instead — ♻️ Regenerate and
⏭ Next topic do the same thing with the owner's reject reason fed to the writer.)

WHAT IT DOES
------------
One compare-and-swap through the ledger (`ledger.transition`, from the REBUILDABLE statuses only):
status → `planned`, `review_round` + 1, the build lease and the writer's saved progress cleared,
`last_error` = "owner_rebuild", and the writer's two caps (`writer_failures`,
`content_rejections`) reset to 0 — this is the owner's explicit override, so a piece that ended at
a cap gets its full retry budget again (the Telegram ♻️ keeps the caps cumulative on purpose).
Open narration jobs are cancelled only AFTER the swap succeeded (a refused or lost swap changes
nothing). The next scheduler tick (≤ 5 min) builds it again under the usual lease.

* `--next-topic` moves `topic.chosen_index` to the next candidate (the runner-up), like ⏭.
* `--publish-at` sets a new `scheduled_publish_at` (hold = it + LEARN_REVIEW_HOLD_HOURS). An
  `expired` piece NEEDS one: its hold is over, so without a new one the build would be skipped at
  the catch-up cut-off and the piece would expire again on the next tick.
* A dry-run piece stays a dry run (`dry_run` is frozen at creation; the ledger never rewrites it).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _parse_when(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SystemExit("--publish-at needs a timezone offset (e.g. -04:00)")
    return parsed.astimezone(timezone.utc)


def plan_rebuild(piece: Dict[str, Any], *, next_topic: bool, publish_at: Optional[datetime],
                 now: datetime, hold_hours: int) -> Dict[str, Any]:
    """The fields the rebuild writes, or SystemExit with the reason it must not run. Pure."""
    from app.services.learn_weekly import contracts as C

    status = piece.get("status")
    if status not in C.REBUILDABLE_STATUSES:
        raise SystemExit(f"{piece.get('period_key')} is {status!r} — only "
                         f"{sorted(C.REBUILDABLE_STATUSES)} can be rebuilt "
                         "(use the Telegram buttons while it is in review)")
    rnd = piece.get("review_round")
    fields: Dict[str, Any] = {
        "review_round": (rnd if isinstance(rnd, int) and not isinstance(rnd, bool) and rnd >= 0 else 0) + 1,
        "lease_until": None,
        "progress": {},
        "last_error": "owner_rebuild",
        "writer_failures": 0,
        "content_rejections": 0,
    }
    if next_topic:
        topic = dict(piece.get("topic") or {})
        candidates = topic.get("candidates") or []
        current = topic.get("chosen_index")
        valid = isinstance(current, int) and not isinstance(current, bool) and current >= 0
        nxt = current + 1 if valid else 0      # junk / negative / none → the first candidate
        if nxt >= len(candidates):
            raise SystemExit(f"no candidate after #{(current or 0) + 1} — {len(candidates)} stored")
        topic["chosen_index"] = nxt
        topic["owner_pick"] = nxt
        topic["auto_picked"] = False
        fields["topic"] = topic
    if publish_at is not None:
        if publish_at <= now:
            raise SystemExit("--publish-at is in the past")
        # Datetimes, not strings: the ledger validates timestamp columns as aware datetimes.
        fields["scheduled_publish_at"] = publish_at
        fields["hold_until"] = publish_at + timedelta(hours=hold_hours)
    elif status == C.EXPIRED:
        raise SystemExit("an expired piece needs --publish-at: its hold is over, so the build would "
                         "be skipped at the catch-up cut-off and expire again")
    # The ledger's own field check, run HERE so the dry run shows a refusal before anything is
    # written (a mocked transition once hid a type error that crashed every real --publish-at run).
    from app.services.learn_weekly import ledger

    try:
        ledger._validate_piece_fields(fields)
    except ValueError as e:
        raise SystemExit(f"the ledger would refuse these fields: {e}")
    return fields


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Reopen one Wiser weekly piece (WRITES PRODUCTION).")
    p.add_argument("--period", required=True, help="e.g. 2026-W43:deep_dive")
    p.add_argument("--next-topic", action="store_true", help="move to the runner-up topic")
    p.add_argument("--publish-at", default=None, help="new publish instant with offset")
    p.add_argument("--yes", action="store_true", help="actually write (default: print only)")
    args = p.parse_args(argv)

    from app.config import settings
    from app.services.learn_weekly import contracts as C, ledger

    piece = ledger.get_piece_sync(args.period)
    if piece is None:
        print(f"no learn_weekly_pieces row for {args.period}")
        return 1
    now = datetime.now(timezone.utc)
    fields = plan_rebuild(piece, next_topic=args.next_topic,
                          publish_at=_parse_when(args.publish_at) if args.publish_at else None,
                          now=now, hold_hours=settings.LEARN_REVIEW_HOLD_HOURS)
    print(f"{args.period}: {piece.get('status')} → planned (round {fields['review_round']}); "
          f"last_error was {piece.get('last_error')!r}")
    for key in ("topic", "scheduled_publish_at", "hold_until"):
        if key in fields:
            value = fields[key]
            shown = value["chosen_index"] if key == "topic" else (
                value.isoformat() if isinstance(value, datetime) else value)
            print(f"  {key}: {shown}")
    if not args.yes:
        print("dry run — nothing written (add --yes)")
        return 0
    row = ledger.transition_sync(piece["id"], from_statuses=list(C.REBUILDABLE_STATUSES),
                                 to_status=C.PLANNED, fields=fields)
    if row is None:
        print("the piece moved on while this ran (lost compare-and-swap) — nothing written")
        return 1
    cancelled = ledger.cancel_open_jobs_sync(piece["id"], reason="owner_rebuild")
    print(f"reopened: {row.get('status')}; {len(cancelled or [])} open narration job(s) cancelled. "
          "The next scheduler tick (≤ 5 min) builds it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
