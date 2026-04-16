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

    # Fetch existing whales by name for dedup
    existing = sb.table("whales").select("id, name").execute()
    existing_names = {w["name"]: w["id"] for w in (existing.data or [])}

    created = 0
    updated = 0

    for whale in registry:
        name = whale["name"]
        row = {
            "name": name,
            "title": whale.get("title", ""),
            "description": whale.get("description", ""),
            "category": whale.get("category", "investors"),
            "data_source": whale.get("data_source", "manual"),
        }
        if whale.get("cik"):
            row["cik"] = whale["cik"]
        if whale.get("fmp_name"):
            row["fmp_name"] = whale["fmp_name"]

        if name in existing_names:
            if args.dry_run:
                logger.info("  [DRY RUN] Would update: %s", name)
            else:
                sb.table("whales").update(row).eq(
                    "id", existing_names[name]
                ).execute()
                logger.info("  Updated: %s", name)
            updated += 1
        else:
            if args.dry_run:
                logger.info("  [DRY RUN] Would create: %s", name)
            else:
                try:
                    sb.table("whales").insert(row).execute()
                    logger.info("  Created: %s", name)
                except Exception as e:
                    # CIK unique constraint conflict — retry without CIK
                    if "23505" in str(e) and "cik" in row:
                        logger.warning("  CIK conflict for %s, inserting without CIK", name)
                        row.pop("cik", None)
                        sb.table("whales").insert(row).execute()
                        logger.info("  Created (no CIK): %s", name)
                    else:
                        raise
            created += 1

    logger.info(
        "Done. created=%d  updated=%d  total=%d",
        created, updated, len(registry),
    )


if __name__ == "__main__":
    main()
