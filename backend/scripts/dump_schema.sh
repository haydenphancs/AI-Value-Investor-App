#!/usr/bin/env bash
#
# dump_schema.sh — regenerate backend/database/schema_snapshot.sql from the
# live Supabase database. Schema only — no data, no secrets.
#
# When to run:
#   - After every batch of 3-5 new migrations applied to Supabase.
#   - Monthly as a refresh.
#   - Any time you've made a schema change via Supabase Studio that wasn't a migration.
#
# Usage:
#   ./backend/scripts/dump_schema.sh
#
# Password resolution order:
#   1. $SUPABASE_DB_PASSWORD env var if already set
#   2. backend/.env file if it defines SUPABASE_DB_PASSWORD
#   3. Interactive prompt (hidden input)
#
# To skip the prompt every time, add this line to backend/.env:
#   SUPABASE_DB_PASSWORD=<your-postgres-password>
#




set -euo pipefail

# Resolve project root (this script lives in backend/scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_FILE="$PROJECT_ROOT/backend/database/schema_snapshot.sql"

# Find a pg_dump compatible with Postgres 17+
PG_DUMP=""
for candidate in \
  /usr/local/opt/libpq/bin/pg_dump \
  /opt/homebrew/opt/libpq/bin/pg_dump \
  "$(command -v pg_dump 2>/dev/null || true)"; do
  if [ -x "$candidate" ]; then
    version=$("$candidate" --version | awk '{print $3}' | cut -d. -f1)
    if [ "$version" -ge 17 ] 2>/dev/null; then
      PG_DUMP="$candidate"
      break
    fi
  fi
done

if [ -z "$PG_DUMP" ]; then
  echo "ERROR: no pg_dump >= 17 found." >&2
  echo "Install via: brew install libpq" >&2
  exit 1
fi

# Resolve password
if [ -z "${SUPABASE_DB_PASSWORD:-}" ] && [ -f "$PROJECT_ROOT/backend/.env" ]; then
  # shellcheck disable=SC1090,SC1091
  set +u
  source "$PROJECT_ROOT/backend/.env"
  set -u
fi

if [ -z "${SUPABASE_DB_PASSWORD:-}" ]; then
  echo "SUPABASE_DB_PASSWORD not set in env or backend/.env."
  printf "Enter Postgres password (hidden): "
  stty -echo
  read -r SUPABASE_DB_PASSWORD
  stty echo
  echo
fi

# Connection parameters (Supabase Supavisor pooler, session mode)
PG_HOST="aws-1-us-east-1.pooler.supabase.com"
PG_PORT="5432"
PG_USER="postgres.gutlnhsjxrkxvrbqbbqq"
PG_DB="postgres"

echo "Using $PG_DUMP ($("$PG_DUMP" --version))"
echo "Dumping schema from $PG_HOST → $OUTPUT_FILE"

PGPASSWORD="$SUPABASE_DB_PASSWORD" "$PG_DUMP" \
  -h "$PG_HOST" \
  -p "$PG_PORT" \
  -U "$PG_USER" \
  -d "$PG_DB" \
  --schema-only \
  --no-owner \
  --no-privileges \
  > "$OUTPUT_FILE"

line_count=$(wc -l < "$OUTPUT_FILE")
table_count=$(grep -c "^CREATE TABLE" "$OUTPUT_FILE")
policy_count=$(grep -c "^CREATE POLICY" "$OUTPUT_FILE")

echo "Done. $line_count lines, $table_count tables, $policy_count RLS policies."
echo "Review with: git diff $OUTPUT_FILE"
