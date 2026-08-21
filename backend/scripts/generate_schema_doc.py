#!/usr/bin/env python3
"""
generate_schema_doc.py — build the Caydex Database Atlas.

Reads   backend/database/schema_snapshot.sql   (the live pg_dump)
      + backend/scripts/schema_curation.py     (domains, purposes, key columns)
      + backend/app/**, backend/scripts/**     (which code touches which table)
Writes  documents/System Design/caydex-database-schema.html

Run it after every `dump_schema.sh`. The output is deterministic — same input,
byte-identical file — so the git diff shows what actually changed in the schema.

    ./venv/bin/python scripts/generate_schema_doc.py
    ./venv/bin/python scripts/generate_schema_doc.py --check

Exit codes:
    0  wrote the page (or --check passed)
    1  a table in the dump has no entry in schema_curation.py
    2  --check found a structural count that moved
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import schema_doc_data as data  # noqa: E402
import schema_doc_svg as svgmod  # noqa: E402
import schema_doc_template as tpl  # noqa: E402
from schema_parser import parse_dump  # noqa: E402

BACKEND = SCRIPT_DIR.parent
REPO = BACKEND.parent
DEFAULT_SOURCE = BACKEND / "database" / "schema_snapshot.sql"
DEFAULT_OUT = REPO / "documents" / "System Design" / "caydex-database-schema.html"

TITLE = "Caydex · Database Atlas"

MAINTENANCE = """  Caydex — Database Atlas (standalone, offline, no external assets).
  Every table in the Supabase database, grouped by what it is for, with its keys,
  indexes, RLS policies, relationships and the backend code that touches it.

  DO NOT HAND-EDIT THIS FILE. It is generated:

      cd backend && ./venv/bin/python scripts/generate_schema_doc.py

  Structure comes from backend/database/schema_snapshot.sql (refresh it with
  backend/scripts/dump_schema.sh). Descriptions come from COMMENT ON TABLE in the
  migrations first, then backend/scripts/schema_curation.py — edit one of those.

  Sibling docs: SYSTEM_DESIGN_GUIDELINES.md · caydex-system-design.html ·
  caydex-system-design-structure.html · caydex-ask-cay-ai-system-design.html"""

# Structural truths of the current snapshot. --check re-asserts them so a schema
# change that moves one of these is announced rather than silently reshaping the
# page. Update deliberately, in the same change as the migration.
EXPECTED: dict[str, int] = {
    # Refreshed 2026-08-21 after re-dumping a snapshot that was three migrations stale.
    # Every delta is accounted for, which is the point of asserting these at all:
    #   +2 tables / +2 policies / +2 rls  -> commodity_cache (149), index_cache (150)
    #   +2 functions / +2 secdef          -> claim_scheduled_job + finish_scheduled_job,
    #                                        from migration 147 — applied to Supabase before
    #                                        the previous dump but never captured in it.
    # Then 151 took policies 197 -> 195: it DROPped etf_snapshot_cache_public_read and
    # etf_detail_cache_public_read, so those two caches are service_role-only like 149/150.
    "tables": 132,
    "public": 97,
    "fk": 28,
    "policies": 195,
    "rls": 97,
    "functions": 40,
    "enums": 14,
    "views": 1,
}


def build(source: Path, *, allow_uncurated: bool, date: str | None) -> tuple[str, dict, list[str]]:
    sql = source.read_text(encoding="utf-8")
    schema = parse_dump(sql)

    table_names = {t.name for t in schema.tables.values() if t.schema == "public"}
    usage = data.scan_code(
        [BACKEND / "app", BACKEND / "scripts"], REPO, table_names
    )

    if date is None:
        # The snapshot's own mtime, not "now" — the page describes the dump, and
        # keying off the clock would make the output non-deterministic.
        #
        # LOCAL time, not UTC: an evening dump on this machine is 6-7 hours
        # behind UTC, so utcfromtimestamp() printed TOMORROW's date for a file
        # the user had just created today. It also emits a DeprecationWarning
        # on 3.12+.
        date = dt.datetime.fromtimestamp(source.stat().st_mtime).strftime("%Y-%m-%d")

    rel_source = source.relative_to(REPO).as_posix() if source.is_relative_to(REPO) else str(source)
    payload, uncurated = data.build_payload(
        schema, usage, generated=date, source=rel_source, allow_uncurated=allow_uncurated
    )
    if uncurated:
        return "", payload, uncurated

    hard, soft = data.build_edges(schema)
    payload["svg"] = svgmod.build_svg(payload["tables"], hard, soft)

    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Never let payload text terminate the <script> element or open a comment.
    blob = blob.replace("</", "<\\/").replace("<!--", "<\\!--")

    counts = payload["meta"]["counts"]
    subtitle = (
        f"{counts['tables']} tables · {counts['public']} in public · "
        f"Supabase Postgres {schema.pg_version}"
    )
    body = (
        tpl.BODY.replace("{{SUBTITLE}}", subtitle)
        .replace("{{SOURCE}}", rel_source)
        .replace("{{PGV}}", schema.pg_version)
        .replace("{{DUMPV}}", schema.dump_version)
        .replace("{{DATE}}", f"snapshot dated {date}")
    )
    html = tpl.page(tpl.CSS, body, tpl.JS, blob, TITLE, MAINTENANCE)
    return html, payload, []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the Caydex Database Atlas.")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--allow-uncurated", action="store_true",
                    help="emit anyway when a table has no curation entry")
    ap.add_argument("--check", action="store_true",
                    help="parse and assert the expected structural counts; write nothing")
    ap.add_argument("--date", default=None, help="override the snapshot date (tests)")
    args = ap.parse_args(argv)

    if not args.source.exists():
        print(f"ERROR: no such dump: {args.source}", file=sys.stderr)
        return 2

    html, payload, uncurated = build(
        args.source, allow_uncurated=args.allow_uncurated or args.check, date=args.date
    )

    if uncurated:
        print(
            "ERROR: these tables are in the dump but have no entry in "
            "backend/scripts/schema_curation.py:\n",
            file=sys.stderr,
        )
        for q in uncurated:
            print(f"    {q}", file=sys.stderr)
        print(
            "\nAdd them (domain + purpose + key columns) in the same change as the "
            "migration, or re-run with --allow-uncurated.",
            file=sys.stderr,
        )
        return 1

    counts = payload["meta"]["counts"]
    if args.check:
        drift = {k: (v, counts.get(k)) for k, v in EXPECTED.items() if counts.get(k) != v}
        for k, v in sorted(counts.items()):
            flag = "  <-- moved" if k in drift else ""
            print(f"  {k:>12}: {v}{flag}")
        if drift:
            print("\nStructural counts moved since EXPECTED was last set:", file=sys.stderr)
            for k, (want, got) in sorted(drift.items()):
                print(f"    {k}: expected {want}, got {got}", file=sys.stderr)
            print(
                "\nIf the schema really changed, update EXPECTED in this file in the same "
                "change as the migration.",
                file=sys.stderr,
            )
            return 2
        print("\nOK — all expected structural counts hold.")
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(
        f"wrote {args.out.relative_to(REPO) if args.out.is_relative_to(REPO) else args.out} "
        f"({len(html) / 1024:.0f} KB) — {counts['tables']} tables, {counts['fk']} FKs, "
        f"{counts['soft']} unenforced joins"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
