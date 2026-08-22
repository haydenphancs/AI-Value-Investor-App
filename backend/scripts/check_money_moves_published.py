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
Run it after `seed_money_moves.py`, and any time the Money Moves row looks wrong.

    ./venv/bin/python scripts/check_money_moves_published.py

Exit 0 = every authored article is served with a hero AND card URL that both resolve.
Exit 1 = drift. The message names the slugs.

⚠️ A PASS can take up to an hour to appear after seeding. `MoneyMovesContentService._cache` is
a process-wide 3600s in-memory entry with no bust route, header or query param anywhere in the
codebase, so a fresh row is invisible until it expires or Railway restarts. A FAIL immediately
after seeding is therefore expected; re-run it, and only investigate if it persists past the
TTL. Check the database directly to tell the two apart.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent

# The same precedence seed_money_moves.py uses: the iOS bundle is authoritative, the vendored
# backend copy is the fallback. Checking the one the seeder publishes is the point.
_FRONTEND = REPO / "frontend/ios/ios/Resources/MoneyMoves/money_moves.json"
_BACKEND = ROOT / "data/money_moves.json"
BASE = "https://ai-value-investor-app-production.up.railway.app"


def _get(url: str):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def _status(url: str):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as r:
            return r.status
    except Exception as exc:  # noqa: BLE001 - any failure is a failure to serve
        return getattr(exc, "code", None) or f"{type(exc).__name__}"


def main() -> int:
    catalog = _FRONTEND if _FRONTEND.exists() else _BACKEND
    authored = [a["slug"] for a in json.loads(catalog.read_text(encoding="utf-8"))["articles"]]
    served = _get(f"{BASE}/api/v1/learn/money-moves")["articles"]

    print(f"authored: {len(authored)}   served: {len(served)}   ({catalog.relative_to(REPO)})")

    missing = [s for s in authored if s not in {a["slug"] for a in served}]
    extra = [a["slug"] for a in served if a["slug"] not in set(authored)]
    if missing:
        print(f"  NOT PUBLISHED ({len(missing)}): {', '.join(missing)}")
        print("     -> run: ./venv/bin/python scripts/seed_money_moves.py")
    if extra:
        print(f"  SERVED BUT NOT AUTHORED ({len(extra)}): {', '.join(extra)}")

    broken = []
    for a in served:
        hero, card = a.get("imageUrl"), a.get("imageCardUrl")
        if not hero or not card:
            # The on-screen symptom of this is IDENTICAL to the row being absent: the client
            # falls back to the authored gradient. Naming it separately is the only way to
            # tell "never seeded" from "seeded without artwork".
            broken.append((a["slug"], f"hero_url={'set' if hero else 'MISSING'} card_url={'set' if card else 'MISSING'}"))
            continue
        sh, sc = _status(hero), _status(card)
        if sh != 200 or sc != 200:
            broken.append((a["slug"], f"hero={sh} card={sc}"))

    print(f"  artwork resolving: {len(served) - len(broken)}/{len(served)}")
    for slug, why in broken:
        print(f"     BROKEN {slug}: {why}")

    ok = not missing and not extra and not broken
    print("\nRESULT:", "PASS" if ok else "FAIL")
    if not ok and not missing and not extra:
        print("(rows are published but artwork is not resolving — this is NOT a cache issue)")
    elif not ok:
        print("(if you just seeded, the 1-hour endpoint cache may still be serving the old set)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
