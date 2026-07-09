#!/usr/bin/env python3
"""
Whale Registry Sync
====================
Upserts whales from data/whale_registry.json into the Supabase whales table.

- Additive only: never deletes existing whales
- Matches on name (upsert)
- Preserves followers_count, portfolio_value, and other computed fields

Usage:
    cd backend
    python -m scripts.sync_whale_registry
    python -m scripts.sync_whale_registry --dry-run
"""

import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import get_supabase  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("sync_whale_registry")

REGISTRY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "whale_registry.json"
)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Sync whale registry to DB")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    with open(REGISTRY_PATH) as f:
        registry = json.load(f)

    logger.info("Loaded %d whales from registry", len(registry))

    sb = get_supabase()

    # Fetch existing whales for dedup. Match on BOTH name and CIK: a whale RENAME
    # that keeps its CIK must UPDATE the existing row, not INSERT a new one that
    # collides on the uq_whales_cik unique index (migration 080). Without the CIK
    # match the collision raises, and (with per-row isolation below) that one row
    # is skipped instead of aborting the entire remaining sync.
    existing = sb.table("whales").select("id, name, cik").execute()
    existing_names = {w["name"]: w["id"] for w in (existing.data or [])}
    existing_ciks = {
        w["cik"]: w["id"] for w in (existing.data or []) if w.get("cik")
    }

    created = 0
    updated = 0
    errors = 0

    for whale in registry:
        name = whale["name"]
        try:
            row = {
                "name": name,
                "title": whale.get("title", ""),
                "description": whale.get("description", ""),
                "category": whale.get("category", "investors"),
                "data_source": whale.get("data_source", "manual"),
                # Unconditional (unlike cik/fmp_name below) so a corrected or
                # removed firm_name propagates on re-sync. Requires migration 080
                # (adds whales.firm_name) to be applied first.
                "firm_name": whale.get("firm_name"),
            }
            if whale.get("cik"):
                row["cik"] = whale["cik"]
            if whale.get("fmp_name"):
                row["fmp_name"] = whale["fmp_name"]
            if whale.get("associated_ticker"):
                row["associated_ticker"] = whale["associated_ticker"]

            # Resolve to an existing row by name first, then by CIK (rename case).
            row_id = existing_names.get(name)
            if row_id is None and whale.get("cik"):
                row_id = existing_ciks.get(whale["cik"])

            if row_id is not None:
                if args.dry_run:
                    logger.info("  [DRY RUN] Would update: %s", name)
                else:
                    sb.table("whales").update(row).eq("id", row_id).execute()
                    logger.info("  Updated: %s", name)
                updated += 1
            else:
                if args.dry_run:
                    logger.info("  [DRY RUN] Would create: %s", name)
                else:
                    sb.table("whales").insert(row).execute()
                    logger.info("  Created: %s", name)
                created += 1
        except Exception as e:
            # Isolate per-row failures so one bad entry (e.g. a duplicate-CIK
            # collision) doesn't abort the rest of the registry — which would
            # silently leave every later whale unsynced. Log loudly and keep
            # going; the non-zero exit below still flags the run as failed.
            errors += 1
            logger.error(
                "  FAILED to sync %s: %s: %s", name, type(e).__name__, e
            )

    logger.info(
        "Done. created=%d  updated=%d  errors=%d  total=%d",
        created, updated, errors, len(registry),
    )
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
