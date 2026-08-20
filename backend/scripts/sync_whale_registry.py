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
from app.services._whale_common import compute_activity  # noqa: E402
from app.services.entitlements import FREE_TIER_WHALE_NAME  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("sync_whale_registry")

REGISTRY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "whale_registry.json"
)


# Columns added by migration 145. Dropped automatically when that migration has not been
# applied yet, so the deploy order of code-vs-migration does not matter in EITHER
# direction. Without this, shipping the code first would fail all 56 row updates with
# PGRST204 — the exact hazard migration 127's header documents.
_MIGRATION_145_COLUMNS = ("lifecycle_status", "lifecycle_note")

_missing_145 = False


def _write_row(sb, row: dict, *, row_id) -> None:
    """UPDATE or INSERT a whale row, degrading if migration 145 is not applied."""
    global _missing_145
    payload = dict(row)
    if _missing_145:
        for col in _MIGRATION_145_COLUMNS:
            payload.pop(col, None)
    try:
        if row_id is not None:
            sb.table("whales").update(payload).eq("id", row_id).execute()
        else:
            sb.table("whales").insert(payload).execute()
    except Exception as e:
        msg = str(e)
        if not _missing_145 and (
            "PGRST204" in msg or any(c in msg for c in _MIGRATION_145_COLUMNS)
        ):
            _missing_145 = True
            logger.warning(
                "Migration 145 is not applied (%s) — syncing WITHOUT the curated "
                "lifecycle columns. Apply 145_whale_activity_disclosure.sql and re-run "
                "to write them.",
                type(e).__name__,
            )
            _write_row(sb, row, row_id=row_id)
            return
        raise


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
                # CURATED lifecycle (migration 145). Unconditional, like firm_name, so
                # clearing `status` in the registry propagates a whale back to active.
                # These carry facts the DATA CANNOT SHOW: "Nancy Pelosi retired" is
                # invisible in her filings — she still shows recent trades — so it can
                # only ever be written by a human.
                "lifecycle_status": (whale.get("status") or "").strip().lower(),
                "lifecycle_note": (whale.get("status_note") or "").strip() or None,
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
                    _write_row(sb, row, row_id=row_id)
                    logger.info("  Updated: %s", name)
                updated += 1
            else:
                if args.dry_run:
                    logger.info("  [DRY RUN] Would create: %s", name)
                else:
                    _write_row(sb, row, row_id=None)
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

    # ── DRIFT REPORT ────────────────────────────────────────────────────────
    #
    # The sync is ADDITIVE — it never deletes — so a row that was inserted by hand,
    # or whose registry entry was later removed, lives on in `whales` forever and is
    # served to users like any other. `tests/test_whale_registry_integrity.py` only
    # lints the JSON file, so it cannot see those rows at all.
    #
    # That is not hypothetical: Dan Crenshaw, Mark Kelly and Ted Cruz sat in production
    # outside the registry until an audit compared the two by hand. Deleting them
    # automatically would be wrong (a registry typo would silently destroy a whale and
    # cascade its follows), so this REPORTS and leaves the decision to a human.
    # ── Lifecycle lint, before anything is written ──────────────────────────
    # A bare "Inactive" badge invites exactly the question it fails to answer, so a
    # non-active status without a note is refused rather than shipped.
    _ALLOWED_STATUS = {"", "active", "inactive"}
    for whale in registry:
        st = (whale.get("status") or "").strip().lower()
        if st not in _ALLOWED_STATUS:
            logger.error(
                "  %s: unknown status %r (allowed: %s)",
                whale.get("name"), whale.get("status"), sorted(_ALLOWED_STATUS - {""}),
            )
            errors += 1
        elif st and st != "active" and not (whale.get("status_note") or "").strip():
            logger.error(
                "  %s: status=%r requires a status_note explaining why",
                whale.get("name"), st,
            )
            errors += 1

    registry_names = {w.get("name") for w in registry}
    orphans = sorted(
        n for n in existing_names if n not in registry_names
    )
    if orphans:
        logger.warning(
            "DRIFT: %d whale(s) in the database are NOT in the registry and will keep "
            "being served: %s. Add them to whale_registry.json, or delete the rows "
            "deliberately (follows cascade).",
            len(orphans), ", ".join(orphans),
        )
    else:
        logger.info("No drift: every database whale is present in the registry.")

    # ── ACTIVITY REPORT ─────────────────────────────────────────────────────
    #
    # Reads PERSISTED columns only. Every FMP method swallows its exception and returns
    # [], so probing upstream here would mark real whales dormant during an outage.
    try:
        rows = (
            sb.table("whales")
            .select("name,data_source,last_filing_period,last_activity_date,lifecycle_status")
            .limit(500)
            .execute()
        ).data or []
        activity_readable = True
    except Exception as e:
        logger.warning(
            "Activity report skipped (%s: %s) — migration 145 may not be applied yet",
            type(e).__name__, e,
        )
        rows = []
        # ⚠️ Load-bearing. Without this the free-whale check below sees an EMPTY `rows`
        # and reports "FREE WHALE is not in the whales table" — a false alarm that also
        # makes the script exit 1. "We could not read it" and "it is not there" are
        # different statements; only the second is an error.
        activity_readable = False

    quiet: list = []
    for r in rows:
        act = compute_activity(
            data_source=r.get("data_source"),
            last_filing_period=r.get("last_filing_period"),
            last_activity_date=r.get("last_activity_date"),
        )
        curated = (r.get("lifecycle_status") or "").strip().lower()
        if act.needs_disclosure and curated in ("", "active"):
            quiet.append(f"{r.get('name')} ({act.status}: {act.label or 'no label'})")

    if quiet:
        logger.warning(
            "ACTIVITY: %d whale(s) are no longer filing on cadence and have NO curated "
            "status_note explaining why: %s. Users see the derived label; add a "
            "status/status_note to whale_registry.json to say why.",
            len(quiet), "; ".join(sorted(quiet)),
        )

    # The designated free whale is the ONLY one a Free account can follow or see in full.
    # If it goes quiet, every Free user gets a permanently empty activity feed with no
    # explanation — and `free_tier_whale_id` has no freshness check at all. Swapping it is
    # a product decision, so this warns rather than acting.
    # Only meaningful when the report above could actually be read. "We could not read
    # it" and "it is not there" are different statements, and reporting the second for
    # the first is a false alarm that also exits non-zero.
    if activity_readable:
        free_row = next(
            (
                r for r in rows
                if str(r.get("name") or "").strip().casefold()
                == FREE_TIER_WHALE_NAME.casefold()
            ),
            None,
        )
        if free_row is None:
            logger.error(
                "FREE WHALE %r is not in the whales table — Free accounts can follow nobody",
                FREE_TIER_WHALE_NAME,
            )
            errors += 1
        else:
            free_act = compute_activity(
                data_source=free_row.get("data_source"),
                last_filing_period=free_row.get("last_filing_period"),
                last_activity_date=free_row.get("last_activity_date"),
            )
            if free_act.needs_disclosure:
                logger.error(
                    "FREE WHALE %r has gone quiet (%s: %s). Every Free account's activity "
                    "feed is filtered to this ONE whale, so it is now empty for all of "
                    "them. Designate a different FREE_TIER_WHALE_NAME in entitlements.py.",
                    FREE_TIER_WHALE_NAME, free_act.status, free_act.label,
                )
                errors += 1

    logger.info(
        "Done. created=%d  updated=%d  errors=%d  orphans=%d  quiet=%d  total=%d",
        created, updated, errors, len(orphans), len(quiet), len(registry),
    )
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
