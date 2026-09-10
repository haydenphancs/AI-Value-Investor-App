"""Seed the Ask Cay AI starter-question pool into Supabase.

Reads the SAME JSON the iOS app bundles (frontend path locally, vendored
`backend/data/chat_starters.json` on Railway — the Money Moves pattern) and upserts one
row per question into `public.chat_starters`.

Ids are uuid5 of the slug, so re-running UPDATES in place rather than duplicating, and a
reworded question keeps its row. Questions that have been removed from the JSON are
DEACTIVATED (`is_active = false`) rather than deleted, so a bad edit is one flag away from
being undone and nothing is ever lost.

Prerequisites:
  - Migration 161_chat_starters.sql applied.
  - backend/.env with SUPABASE service-role credentials (uses app.database.get_supabase()).

Usage (from backend/):
    ./venv/bin/python scripts/seed_chat_starters.py
    ./venv/bin/python scripts/seed_chat_starters.py --dry-run    # build + print, no writes
"""

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.services.chat_starters_service import _ALL_SCOPES, _DETAIL_SCOPES, _GLOBAL_SCOPE  # noqa: E402

#: Same resolution order as seed_money_moves: prefer the authored copy in the iOS
#: resource bundle when running from a checkout, fall back to the vendored one that
#: ships to Railway.
_CANDIDATES = (
    BACKEND.parent / "frontend" / "ios" / "ios" / "Resources" / "ChatStarters" / "chat_starters.json",
    BACKEND / "data" / "chat_starters.json",
)

#: Stable namespace for the uuid5 slugs. Changing it re-mints every id and orphans
#: every existing row — don't.
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "caydex.chat_starters")

_TABLE = "chat_starters"
_MAX_TEXT = 140          # mirrors the CHECK in migration 161


def _catalogue_path() -> Path:
    for path in _CANDIDATES:
        if path.is_file():
            return path
    raise SystemExit(
        "No chat_starters.json found. Looked in:\n  " + "\n  ".join(str(p) for p in _CANDIDATES)
    )


def _slugify(scope: str, text: str) -> str:
    body = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:70]
    return f"{scope}-{body}"


def _build_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    detail = payload.get("detail") or {}

    for scope in _ALL_SCOPES:
        items = (payload.get("global") if scope == _GLOBAL_SCOPE else detail.get(scope)) or []
        for order, raw in enumerate(items):
            text = " ".join(str(raw).split())
            if not text:
                continue
            # Fail loudly rather than letting Postgres reject the batch with a CHECK
            # violation that names a constraint instead of the offending question.
            if len(text) > _MAX_TEXT:
                raise SystemExit(f"[{scope}] question exceeds {_MAX_TEXT} chars: {text!r}")
            placeholders = text.count("{symbol}")
            if scope in _DETAIL_SCOPES and placeholders != 1:
                raise SystemExit(
                    f"[{scope}] detail templates need exactly one {{symbol}}, found "
                    f"{placeholders}: {text!r}"
                )
            if scope == _GLOBAL_SCOPE and "{" in text:
                raise SystemExit(f"[global] questions must carry no placeholder: {text!r}")

            slug = _slugify(scope, text)
            if slug in seen:
                raise SystemExit(f"[{scope}] duplicate slug {slug!r} from: {text!r}")
            seen.add(slug)
            rows.append({
                "id": str(uuid.uuid5(_NAMESPACE, slug)),
                "slug": slug,
                "text": text,
                "scope": scope,
                "is_active": True,
                "sort_order": order,
            })
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="build and print, write nothing")
    args = parser.parse_args()

    path = _catalogue_path()
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = _build_rows(payload)

    by_scope: Dict[str, int] = {}
    for row in rows:
        by_scope[row["scope"]] = by_scope.get(row["scope"], 0) + 1
    print(f"Source: {path}")
    print(f"Built {len(rows)} rows: " + ", ".join(f"{k}={v}" for k, v in sorted(by_scope.items())))

    if args.dry_run:
        for row in rows[:5]:
            print(f"  {row['scope']:9} {row['slug'][:60]:62} {row['text']}")
        print("  … (dry run — nothing written)")
        return

    from app.database import get_supabase

    supabase = get_supabase()
    supabase.table(_TABLE).upsert(rows, on_conflict="slug").execute()
    print(f"Upserted {len(rows)} rows.")

    # Retire anything no longer in the JSON. Deactivate rather than delete: a question
    # removed by mistake comes back with one flag, and nothing that was ever live is lost.
    live_slugs = {row["slug"] for row in rows}
    existing = supabase.table(_TABLE).select("slug, is_active").execute().data or []
    stale = [r["slug"] for r in existing if r["slug"] not in live_slugs and r.get("is_active")]
    if stale:
        supabase.table(_TABLE).update({"is_active": False}).in_("slug", stale).execute()
        print(f"Deactivated {len(stale)} question(s) no longer in the catalogue:")
        for slug in stale[:10]:
            print(f"  - {slug}")
    else:
        print("No stale questions to retire.")


if __name__ == "__main__":
    main()
