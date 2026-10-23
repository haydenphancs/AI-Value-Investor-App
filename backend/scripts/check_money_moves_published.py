#!/usr/bin/env python
"""Does production actually serve every Money Moves article we authored — with its artwork?

WHY THIS EXISTS
---------------
Three articles were authored into money_moves.json and then sat UNPUBLISHED for six days.
The catalog said 16, `GET /api/v1/learn/money-moves` served 13, and the three newest cards
rendered a flat category gradient — because the iOS bundle is only the offline fallback and
deliberately carries no imageUrl. Every test stayed green throughout: the suite is offline by
design, so nothing compared the authored catalog to the served one. This is that comparison.

It is a MANUAL probe, not a pytest — it needs the network and it asserts about production.
Run it after `seed_money_moves.py`, after a Wiser weekly piece is published or unpublished, and
any time the Money Moves row looks wrong.

    ./venv/bin/python scripts/check_money_moves_published.py

HOW IT READS (2026-10-22)
-------------------------
The HTTP route is account-only now (auth.md §1a), so the old unauthenticated GET answers 401.
The probe instead runs the SERVICE's own read — `MoneyMovesContentService()._load()` — with the
service-role client from backend/.env (`app.database.get_supabase()`, the seeder's credentials).
That is the exact code the route serves from: the same overlays, the same featured rule, the same
archive cap. It only READS: one SELECT on `money_move_articles`, one on `learn_weekly_pieces`, a
Storage LIST per weekly clip, and HEAD requests on the public artwork. It never writes.

WHAT IT CHECKS
--------------
* Expected = every bundled article + every `learn_weekly_pieces` row with status `published`
  (plan "Wiser weekly content engine": weekly pieces live only in the database). Before
  migration 204 the ledger does not exist; the probe says so and checks the bundle alone.
* NOT PUBLISHED: an expected slug with no row. SERVED BUT NOT AUTHORED: a served slug in neither
  set (e.g. a row the ledger says was unpublished). A published weekly piece held back by the
  archive cap (`LEARN_SERVED_WEEKLY_MAX`) is reported, not failed — that is the rule working.
* Exactly ONE served article has `isFeatured: true` (iOS shows the first one; the service's
  featured rule guarantees at most one, so zero means no eligible flagged row).
* Artwork: hero + card URLs present and HEAD 200 (public bucket). A weekly piece may have no
  artwork until cover art ships (plan 1c) — noted, not failed; a URL it does carry must resolve.
* Narration: each weekly row's audio object exists in the PRIVATE `money-moves-media` bucket
  (a Storage LIST — a HEAD on the public-form URL cannot see a private object).

Exit 0 = everything above holds. Exit 1 = drift. The message names the slugs.

⚠️ The probe reads the TABLE, not the serving instance's cache. A publish/unpublish invalidates
the cache of the instance that ran it, and every other instance converges on its next Learn tick
(`MoneyMovesContentService.sync_with_stamp`); a seed run has no invalidation at all, so a seeded
row can still take up to the hour-long TTL (or a Railway restart) to appear in the app. A PASS
here therefore means "the database is right"; what a device shows can lag it.
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent

# Make `app` importable when run from backend/ (the seeder does the same).
sys.path.insert(0, str(ROOT))

from app.database import get_supabase  # noqa: E402
from app.services.learn_audio_urls import MONEY_MOVES_BUCKET, parse_storage_url  # noqa: E402
from app.services.money_moves_content_service import (  # noqa: E402
    SOURCE_WEEKLY,
    MoneyMovesContentService,
    _row_source,
    _served_weekly_max,
)

# The same precedence seed_money_moves.py uses: the iOS bundle is authoritative, the vendored
# backend copy is the fallback. Checking the one the seeder publishes is the point.
_FRONTEND = REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
_BACKEND = ROOT / "data/money_moves.json"
LEDGER_TABLE = "learn_weekly_pieces"


def _status(url: str):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as r:
            return r.status
    except Exception as exc:  # noqa: BLE001 - any failure is a failure to serve
        return getattr(exc, "code", None) or f"{type(exc).__name__}"


def _missing_ledger(exc: BaseException) -> bool:
    """True when `learn_weekly_pieces` does not exist yet (migration 204 not applied)."""
    text = " ".join(
        str(p) for p in (getattr(exc, "code", None), getattr(exc, "message", None), exc) if p
    ).lower()
    return LEDGER_TABLE in text and any(
        marker in text for marker in ("42p01", "pgrst205", "does not exist", "could not find")
    )


def _published_weekly_slugs(sb) -> Tuple[Set[str], Optional[str]]:
    """(slugs the ledger says are published, a problem line or None)."""
    try:
        rows = (
            sb.table(LEDGER_TABLE).select("slug, status").eq("status", "published").execute()
        ).data or []
    except Exception as exc:  # noqa: BLE001 - reported below, never swallowed
        if _missing_ledger(exc):
            print(f"  ledger: {LEDGER_TABLE} does not exist yet (migration 204 not applied) — "
                  "checking the bundle only")
            return set(), None
        return set(), f"ledger unreadable ({type(exc).__name__}: {exc})"
    slugs = {r["slug"] for r in rows if isinstance(r.get("slug"), str) and r["slug"].strip()}
    blank = len(rows) - len(slugs)
    print(f"  ledger: {len(slugs)} published weekly piece(s)")
    if blank:
        return slugs, f"{blank} published ledger row(s) carry no slug"
    return slugs, None


def _audio_object_problem(sb, audio_url: Any) -> Optional[str]:
    """None when the weekly clip exists in the private narration bucket; else why not."""
    if not audio_url:
        return "audio_url MISSING"
    parsed = parse_storage_url(str(audio_url), allowed={MONEY_MOVES_BUCKET})
    if parsed is None:
        return f"audio_url is not a {MONEY_MOVES_BUCKET} object URL: {audio_url}"
    bucket, object_path = parsed
    folder, _, name = object_path.rpartition("/")
    try:
        listed = sb.storage.from_(bucket).list(folder, {"search": name}) or []
    except Exception as exc:  # noqa: BLE001 - any failure is a failure to verify
        return f"storage list failed ({type(exc).__name__}: {exc})"
    if not any(isinstance(o, dict) and o.get("name") == name for o in listed):
        return f"object {bucket}/{object_path} NOT FOUND"
    return None


def main() -> int:
    catalog = _FRONTEND if _FRONTEND.exists() else _BACKEND
    bundle = [a["slug"] for a in json.loads(catalog.read_text(encoding="utf-8"))["articles"]]

    sb = get_supabase()
    # ONE read of the table, handed to the service's own `_load` (deep-copied: `_load` overlays
    # the content blobs in place) — so the raw rows and the served catalog describe one moment.
    svc = MoneyMovesContentService()
    rows: List[Dict[str, Any]] = svc._fetch_rows()
    svc._fetch_rows = lambda: copy.deepcopy(rows)  # type: ignore[assignment]
    served = asyncio.run(svc._load()).articles

    print(f"authored (bundle): {len(bundle)}   rows: {len(rows)}   served: {len(served)}   "
          f"({catalog.relative_to(REPO)})")
    weekly_published, ledger_problem = _published_weekly_slugs(sb)

    served_slugs = {a.get("slug") for a in served}
    row_slugs = {r.get("slug") for r in rows}
    weekly_rows = [r for r in rows if _row_source(r) == SOURCE_WEEKLY]

    missing = [s for s in bundle if s not in served_slugs]
    missing += sorted(s for s in weekly_published if s not in row_slugs)
    archived = sorted(s for s in weekly_published if s in row_slugs and s not in served_slugs)
    expected = set(bundle) | weekly_published
    extra = sorted(str(s) for s in served_slugs if s not in expected)

    if ledger_problem:
        print(f"  LEDGER: {ledger_problem}")
    if missing:
        print(f"  NOT PUBLISHED ({len(missing)}): {', '.join(missing)}")
        print("     -> bundle: run ./venv/bin/python scripts/seed_money_moves.py; "
              "weekly: check the piece's publish step in the ledger")
    if archived:
        print(f"  ARCHIVED (published, beyond LEARN_SERVED_WEEKLY_MAX={_served_weekly_max()}; "
              f"not a failure) ({len(archived)}): {', '.join(archived)}")
    if extra:
        print(f"  SERVED BUT NOT AUTHORED ({len(extra)}): {', '.join(extra)}")

    featured = [a.get("slug") for a in served if a.get("isFeatured") is True]
    featured_ok = len(featured) == 1
    print(f"  featured: {', '.join(map(str, featured)) or 'NONE'}"
          f"{'' if featured_ok else '   <- expected exactly one'}")

    weekly_slugs = {r.get("slug") for r in weekly_rows}
    broken, notes = [], []
    for a in served:
        hero, card = a.get("imageUrl"), a.get("imageCardUrl")
        is_weekly = a.get("slug") in weekly_slugs
        if not hero or not card:
            if is_weekly and not hero and not card:
                # Cover art for weekly pieces is plan 1c (LEARN_ART_ENABLED): until then the
                # card falls back to its gradient by design.
                notes.append(a.get("slug"))
                continue
            # The on-screen symptom of this is IDENTICAL to the row being absent: the client
            # falls back to the authored gradient. Naming it separately is the only way to
            # tell "never seeded" from "seeded without artwork".
            broken.append((a.get("slug"), f"hero_url={'set' if hero else 'MISSING'} card_url={'set' if card else 'MISSING'}"))
            continue
        sh, sc = _status(hero), _status(card)
        if sh != 200 or sc != 200:
            broken.append((a.get("slug"), f"hero={sh} card={sc}"))

    print(f"  artwork resolving: {len(served) - len(broken) - len(notes)}/{len(served)}")
    for slug, why in broken:
        print(f"     BROKEN {slug}: {why}")
    if notes:
        print(f"  weekly pieces without artwork (expected until cover art ships): {', '.join(map(str, notes))}")

    audio_broken = []
    for r in weekly_rows:
        problem = _audio_object_problem(sb, r.get("audio_url"))
        if problem:
            audio_broken.append((r.get("slug"), problem))
    print(f"  weekly narration present: {len(weekly_rows) - len(audio_broken)}/{len(weekly_rows)}")
    for slug, why in audio_broken:
        print(f"     NO AUDIO {slug}: {why}")

    ok = (not missing and not extra and not broken and not audio_broken and featured_ok
          and not ledger_problem)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    if not ok and not missing and not extra:
        print("(the catalog rows are right; the failure is artwork, narration, the featured "
              "flag or the ledger — NOT a cache issue)")
    elif not ok:
        print("(this probe reads the table directly, so a NOT PUBLISHED / SERVED BUT NOT AUTHORED "
              "here is real drift, not the server's cache)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
