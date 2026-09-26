"""
Migration 179 (search_pick_daily + the three search-trending functions), read statically.

The migration is applied by hand in Supabase Studio, so nothing else in the suite would
notice if a later edit dropped the privacy floor from SQL, added an identity column to the
"anonymous" counters, or opened a function to the shipped anon key.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.schemas.search_trending import PICK_TYPES

_SQL_PATH = Path(__file__).resolve().parents[1] / "database" / "migrations" / "179_search_trending.sql"


def _code() -> str:
    """The migration without `--` comments (the header discusses the very tokens asserted
    on, so an unstripped scan would pass on prose)."""
    lines = []
    for line in _SQL_PATH.read_text().splitlines():
        in_str = False
        out = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'":
                in_str = not in_str
            if not in_str and line.startswith("--", i):
                break
            out.append(ch)
            i += 1
        lines.append("".join(out))
    return "\n".join(lines)


CODE = _code()
FUNCTIONS = {
    "increment_search_pick": "DATE, TEXT, TEXT",
    "get_search_trending": "DATE, INTEGER, INTEGER",
    "get_most_added_tickers": "TIMESTAMPTZ, INTEGER, INTEGER, INTEGER",
}


def _table_body() -> str:
    m = re.search(r"CREATE TABLE IF NOT EXISTS public\.search_pick_daily \((.*?)\n\);", CODE, re.S)
    assert m, "search_pick_daily CREATE TABLE not found"
    return m.group(1)


def test_it_is_one_transaction_and_idempotent():
    assert CODE.strip().startswith("BEGIN;") and CODE.strip().endswith("COMMIT;")
    assert "CREATE TABLE IF NOT EXISTS public.search_pick_daily" in CODE
    assert "CREATE INDEX IF NOT EXISTS idx_watchlist_items_added_at" in CODE
    assert not re.search(r"CREATE TABLE (?!IF NOT EXISTS)", CODE)
    assert not re.search(r"CREATE FUNCTION", CODE), "functions must be CREATE OR REPLACE"
    assert 'DROP POLICY IF EXISTS "search_pick_daily_service_all"' in CODE


def test_the_counter_table_has_no_identity_column():
    """The whole privacy design: anonymous counters. A user/device/IP/session column here
    would make the data linked, contradicting App Privacy and the account-deletion story."""
    cols = {
        m.group(1)
        for m in re.finditer(r"^\s*([a-z_]+)\s+[A-Z]", _table_body(), re.M)
    } - {"PRIMARY"}
    assert cols == {"day", "ticker", "asset_type", "picks"}, cols
    assert not re.search(r"user|device|session|\bip\b|identity|install", _table_body(), re.I)
    # No timestamp beyond the day: a precise one matches a single request in the access logs
    # (which carry the client IP) and re-links a person to a ticker.
    assert not re.search(r"updated_at|created_at|timestamptz|timestamp", _table_body(), re.I)
    inc = re.search(r"FUNCTION public\.increment_search_pick\((.*?)\$\$;", CODE, re.S).group(0)
    assert "updated_at" not in inc and "now()," not in inc, "the increment writes no timestamp"


def test_the_type_check_equals_pick_types():
    m = re.search(r"asset_type\s+TEXT\s+NOT NULL CHECK \(asset_type IN \(([^)]*)\)\)", _table_body())
    assert m, "asset_type CHECK not found"
    values = tuple(v.strip().strip("'") for v in m.group(1).split(","))
    assert values == PICK_TYPES


def test_the_ticker_check_matches_the_service_regex():
    from app.services.search_pick_service import _SYMBOL_RE

    m = re.search(r"CHECK \(ticker ~ '([^']+)'\)", _table_body())
    assert m and m.group(1) == _SYMBOL_RE.pattern


@pytest.mark.parametrize("name,args", FUNCTIONS.items())
def test_every_function_is_invoker_pinned_and_service_role_only(name, args):
    m = re.search(
        rf"CREATE OR REPLACE FUNCTION public\.{name}\((.*?)\$\$;", CODE, re.S,
    )
    assert m, f"{name} not found"
    body = m.group(0)
    assert "SECURITY INVOKER" in body
    assert "SET search_path = public, pg_temp" in body
    assert f"REVOKE ALL ON FUNCTION public.{name}({args})\n    FROM PUBLIC, anon, authenticated;" in CODE
    assert f"GRANT EXECUTE ON FUNCTION public.{name}({args}) TO service_role;" in CODE.replace(
        f"({args})\n    TO service_role;", f"({args}) TO service_role;")


@pytest.mark.parametrize("name,param", [("get_search_trending", "p_min_picks"),
                                        ("get_most_added_tickers", "p_min_users")])
def test_the_privacy_floor_lives_in_sql(name, param):
    body = re.search(rf"FUNCTION public\.{name}\((.*?)\$\$;", CODE, re.S).group(0)
    assert f"GREATEST(COALESCE({param}, 3), 3)" in body


def test_most_added_returns_no_client_writable_name_and_merges_one_security():
    body = re.search(r"FUNCTION public\.get_most_added_tickers\((.*?)\$\$;", CODE, re.S).group(0)
    assert "RETURNS TABLE (ticker TEXT, asset_type TEXT, adders BIGINT)" in body
    assert "company_name" not in body, "watchlist_items.company_name is client-writable"
    assert "GROUP BY n.symbol, (n.kind = 'crypto')" in body
    assert "mode() WITHIN GROUP (ORDER BY n.kind)" in body


def test_most_added_counts_distinct_real_accounts_after_onboarding():
    body = re.search(r"FUNCTION public\.get_most_added_tickers\((.*?)\$\$;", CODE, re.S).group(0)
    assert "COUNT(DISTINCT n.user_id)" in body
    assert "JOIN public.users u ON u.id = wi.user_id" in body
    assert "NOT u.is_admin" in body
    flat = " ".join(body.split())
    assert ("wi.added_at >= u.created_at + make_interval(hours => "
            "GREATEST(COALESCE(p_min_account_age_hours, 24), 0))") in flat, (
        "onboarding's first 24 h must be excluded — its suggested chips would otherwise BE the list"
    )


def test_the_increment_refuses_a_day_far_from_today():
    body = re.search(r"FUNCTION public\.increment_search_pick\((.*?)\$\$;", CODE, re.S).group(0)
    assert "p_day < v_today - 1 OR p_day > v_today + 1" in body
    assert "ON CONFLICT (day, ticker, asset_type) DO UPDATE" in body


def test_rls_and_grants_are_service_role_only():
    assert "ALTER TABLE public.search_pick_daily ENABLE ROW LEVEL SECURITY;" in CODE
    assert "REVOKE ALL ON public.search_pick_daily FROM anon, authenticated;" in CODE
    assert "GRANT ALL ON public.search_pick_daily TO service_role;" in CODE
    assert "FOR ALL TO service_role USING (true) WITH CHECK (true);" in CODE


def test_the_table_carries_a_comment_for_the_atlas():
    assert "COMMENT ON TABLE public.search_pick_daily IS" in CODE
