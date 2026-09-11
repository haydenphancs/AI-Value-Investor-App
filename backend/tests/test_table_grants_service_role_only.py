"""Every table the product serves THROUGH the backend must be service-role-only in the DB.

WHY THIS FILE EXISTS
--------------------
The backend reaches Supabase with the service_role key and the iOS app has no Supabase REST
client, so for almost every table the ONLY legitimate PostgREST caller is the backend. Yet
until migrations 163-165 (2026-09-11):

  * `public.users` was UPDATE-able by any signed-in user through the anon key that ships in the
    binary — including `tier` (sizes the monthly credit grant) and, because a column-level
    REVOKE is a no-op while the role holds table-level UPDATE, `is_admin` as well (113's guard
    was inert).
  * 25 `*_cache` tables holding FMP-derived market data and the full generated AI report
    (`ticker_report_cache.ticker_report_data`) granted SELECT to `anon` — a second,
    unauthenticated door to licensed data the app went account-only to protect.
  * The guest-partitioned tables (`watchlist_items`, `portfolios`, `research_reports`,
    `chat_sessions`, ...) that `.claude/rules/database.md` DESCRIBED as service-role-only still
    carried their per-user INSERT/UPDATE/DELETE policies and grants.

None of that was visible: `dump_schema.sh` ran `--no-privileges`, so the snapshot showed the
policies but never the grants, and nothing in the suite read either. This is the table twin of
`test_security_definer_grants.py`.

WHAT THIS CAN AND CANNOT SEE
----------------------------
A SOURCE SCAN over `backend/database/migrations/` (per .claude/rules/testing.md — no live DB in
the suite). It replays every GRANT / REVOKE ALL on a table in migration order and asserts the
EFFECTIVE end state for `anon` and `authenticated`. It catches the two things that actually
happened: a migration that grants a role and never revokes it, and a later migration that
silently re-grants something an earlier one closed.

It CANNOT see a grant made by hand in the Supabase SQL editor, Supabase's own
`ALTER DEFAULT PRIVILEGES` (which is why a table with NO grant statement at all is NOT proof of
safety — every table here must have an explicit REVOKE), or a migration written but never
applied. For those, run the VERIFY query in each migration's header against the live catalog.
"""

from __future__ import annotations

import re
from pathlib import Path

_MIGRATIONS = Path(__file__).resolve().parents[1] / "database" / "migrations"

_ROLES = ("anon", "authenticated")

# Tables that MUST end up with no privilege for anon or authenticated. Grouped by the
# migration that made them so. Add every new table here unless it belongs in
# _CLIENT_ACCESS_BY_DESIGN below with a reason — a table in neither list is a test failure,
# not an oversight to be tidied later.
_SERVICE_ROLE_ONLY: dict[str, tuple[str, ...]] = {
    # identity + money
    "163": ("users",),
    "115": ("user_credits",),
    "117": ("credit_purchases",),
    "107": ("analytics_events",),
    "109": ("push_send_log",),
    "119": ("notification_events",),
    "131": ("user_investor_profile",),
    "132": ("user_memory_facts",),
    # caches that shipped service-role-only from the start
    "149-162": (
        "commodity_cache", "index_cache", "etf_snapshot_cache", "market_close_snapshot",
        "corporate_action_cache", "chat_starters", "chat_starter_answers", "daily_briefings",
        "earnings_cache", "growth_cache", "health_check_cache", "hedge_fund_quarters",
        "holders_cache", "market_insights", "notification_job_state", "price_alerts",
        "profit_power_cache", "revenue_breakdown_cache", "signal_of_confidence_cache",
        "social_mentions_history", "ticker_data_cache",
    ),
    # FMP-derived caches, AI output, audit trails, RAG corpus, smart money (164)
    "164": (
        "ai_insight_cache", "company_profile_cache", "competitor_intel_audit",
        "competitor_intel_cache", "crypto_coin_id_cache", "crypto_fundamentals_cache",
        "crypto_snapshots", "geopolitical_macro_cache", "index_macro_forecast_cache",
        "industry_dossier", "industry_moat_benchmarks", "industry_override_audit",
        "ip_intel_audit", "ip_intel_cache", "market_deep_dive_cache", "moat_intel_audit",
        "moat_intel_cache", "price_catalyst_cache", "short_interest_cache", "signals_cache",
        "snapshot_cache", "stock_fundamentals_cache", "ticker_report_cache",
        "ticker_volatility_cache", "sector_benchmarks", "sector_aggregates",
        "article_chunks", "book_chunks", "company_filing_chunks", "books", "book_chapters",
        "whales", "whale_trades", "whale_trade_groups", "whale_holdings",
        "whale_sector_allocations", "whale_profile_cache", "whale_filing_snapshots",
        "whale_alerts", "vector_search_stats",
        # not a grant problem but a TRIGGER one: update_whale_followers_count() is SECURITY
        # INVOKER and UPDATEs whales, so a client-side follow would 42501 once whales is
        # service-role-only. No migration CREATEs this table, so the replay could never have
        # discovered it — it is listed by hand.
        "whale_follows",
    ),
    # guest-partitioned user tables the rules already called service-role-only (165)
    "165": (
        "watchlist_items", "portfolios", "portfolio_items", "research_reports",
        "chat_sessions", "chat_messages", "user_learn_progress",
    ),
}

# Tables that DO grant anon or authenticated on purpose. Each needs the reason; an entry
# without one is a grant nobody has defended.
_CLIENT_ACCESS_BY_DESIGN: dict[str, str] = {
    "credit_packs": "storefront catalogue — GET /billing/credit-packs is `.public` (design doc §9.1); no FMP data, no user data",
    "plan_credits": "storefront catalogue — GET /billing/plans is `.public`; same",
    "trending_themes": "editorial megatrend cards, server-editable; no FMP data",
    "lessons": "Learn content (Investor Journey); editorial, no FMP data",
    "money_move_articles": "Learn content (Money Moves); editorial, no FMP data",
    "credit_transactions": "SELECT only, own rows via RLS (credit_transactions_select_own); the ledger the user is entitled to read",
    "subscriptions": "SELECT only, own rows via RLS; FK-bound to public.users",
    "device_tokens": "own rows via RLS, FK-bound; user-scoped table template (database.md)",
    "user_settings": "own rows via RLS, FK-bound; user-scoped table template (database.md)",
}

# Tables a migration DROPS. Their grant state stops mattering once the drop is applied, and
# 164's guarded block only revokes them while they still exist.
_DROPPED: dict[str, str] = {
    "portfolio_holdings": "168", "user_lesson_progress": "168", "user_study_schedules": "168",
    "user_bookmarks": "168", "asset_snapshots": "168", "etf_detail_cache": "168",
    "index_detail_cache": "168", "news_articles": "168",
    # replaced long before the review; their GRANT lines survive only in old migrations
    "snapshot_health_cache": "032", "snapshot_ownership_cache": "032",
    "snapshot_valuation_cache": "032", "user_book_progress": "067",
}

_RE_REVOKE = re.compile(
    r"REVOKE\s+ALL(?:\s+PRIVILEGES)?\s+ON\s+(?:TABLE\s+)?(?:public\.)?([a-z0-9_]+)\s+FROM\s+([^;']+)",
    re.I,
)
_RE_GRANT = re.compile(
    r"GRANT\s+([A-Z ,]+?)\s+ON\s+(?:TABLE\s+)?(?:public\.)?([a-z0-9_]+)\s+TO\s+([^;']+)",
    re.I,
)
_RE_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?([a-z0-9_]+)", re.I
)
_NOT_A_TABLE = {"function", "sequence", "schema", "all"}


def _sql_files() -> list[Path]:
    files = sorted(_MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    assert len(files) > 100, f"the migration glob found only {len(files)} files — it has rotted"
    return files


def _strip_sql_comments(sql: str) -> str:
    """Drop `--` line comments and `/* */` blocks. Load-bearing in BOTH directions: the headers
    quote the very GRANT/REVOKE lines being asserted (163's header quotes 113's REVOKE), so an
    un-stripped scan would pass on prose."""
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _roles(clause: str) -> list[str]:
    return [r for r in re.split(r"[,\s]+", clause.strip().lower()) if r in _ROLES]


def _replay() -> tuple[dict[tuple[str, str], tuple[str, int]], dict[str, int], int, int]:
    """Replay every table GRANT / REVOKE ALL in migration order.

    Returns ({(table, role): (state, migration_no)}, {table: created_in}, n_revokes, n_grants)
    where state is 'revoked' or 'granted:<privs>'. The LAST statement wins, which is what
    makes a later re-grant visible."""
    state: dict[tuple[str, str], tuple[str, int]] = {}
    created: dict[str, int] = {}
    n_rev = n_grant = 0
    for path in _sql_files():
        num = int(path.name[:3])
        code = _strip_sql_comments(path.read_text(encoding="utf-8"))
        for m in _RE_CREATE_TABLE.finditer(code):
            created.setdefault(m.group(1).lower(), num)
        # A migration may GRANT then REVOKE (or the reverse) the same table; apply in file order.
        events: list[tuple[int, str, str, str]] = []
        for m in _RE_REVOKE.finditer(code):
            for r in _roles(m.group(2)):
                events.append((m.start(), m.group(1).lower(), r, "revoked"))
                n_rev += 1
        for m in _RE_GRANT.finditer(code):
            if m.group(2).lower() in _NOT_A_TABLE:
                continue
            for r in _roles(m.group(3)):
                events.append((m.start(), m.group(2).lower(), r, "granted:" + m.group(1).strip().upper()))
                n_grant += 1
        for _, table, role, st in sorted(events):
            state[(table, role)] = (st, num)
    return state, created, n_rev, n_grant


def _all_service_role_only() -> set[str]:
    return {t for group in _SERVICE_ROLE_ONLY.values() for t in group}


def test_every_service_role_only_table_is_revoked_from_both_roles():
    state, _, _, _ = _replay()
    open_ = {
        (t, r): state.get((t, r))
        for t in sorted(_all_service_role_only())
        for r in _ROLES
        if (state.get((t, r)) or ("", 0))[0] != "revoked"
    }
    assert not open_, (
        "table(s) declared service-role-only whose EFFECTIVE grant state is not a REVOKE — "
        "either no migration revokes them (Supabase's default privileges then grant ALL) or a "
        "later migration re-granted:\n"
        + "\n".join(f"  • public.{t} / {r}: {v}" for (t, r), v in open_.items())
        + "\n\nAdd to a migration:\n"
        "  REVOKE ALL ON public.<table> FROM anon, authenticated;\n"
        "  GRANT  ALL ON public.<table> TO service_role;\n"
        "and DROP any anon/authenticated policy the table carried (see 151/164)."
    )


def test_every_explicit_client_grant_is_defended_or_dropped():
    """A GRANT to anon/authenticated that is not revoked later must be in the allowlist WITH a
    reason, or on a table a migration drops. Anything else is an undefended door."""
    state, _, _, _ = _replay()
    granted = sorted({t for (t, r), (st, _) in state.items() if st.startswith("granted")
                      and all((state.get((t, rr)) or ("", 0))[0] != "revoked" or rr != r
                              for rr in (r,))})
    undefended = [t for t in granted
                  if t not in _CLIENT_ACCESS_BY_DESIGN and t not in _DROPPED]
    assert not undefended, (
        "table(s) granted to anon/authenticated with no reason recorded in "
        "_CLIENT_ACCESS_BY_DESIGN and no migration dropping them:\n"
        + "\n".join(f"  • public.{t}: " + ", ".join(
            f"{r}={state[(t, r)][0]} (mig {state[(t, r)][1]:03d})" for r in _ROLES if (t, r) in state)
            for t in undefended)
    )
    for t, why in _CLIENT_ACCESS_BY_DESIGN.items():
        assert len(why) > 20, f"_CLIENT_ACCESS_BY_DESIGN[{t!r}] needs a real reason"


def test_no_table_is_in_two_lists():
    both = _all_service_role_only() & set(_CLIENT_ACCESS_BY_DESIGN)
    assert not both, f"contradictory classification: {sorted(both)}"
    both = _all_service_role_only() & set(_DROPPED)
    assert not both, f"dropped tables must not also be declared service-role-only: {sorted(both)}"


def test_new_cache_tables_never_reuse_the_public_read_template():
    """Migration 049's template gave every `*_cache` table `GRANT SELECT TO anon`. Any cache
    table created after 164 must ship service-role-only, whatever the rules file says."""
    state, created, _, _ = _replay()
    offenders = sorted(
        t for t, num in created.items()
        if num >= 164 and (t.endswith("_cache") or t.endswith("_snapshots"))
        and any((state.get((t, r)) or ("", 0))[0] != "revoked" for r in _ROLES)
    )
    assert not offenders, (
        "new cache table(s) created after 164 without `REVOKE ALL … FROM anon, authenticated` — "
        f"the public-read cache template is retired: {offenders}"
    )


def test_the_three_migrations_that_closed_the_doors_are_load_bearing():
    """Mutation targets. Delete a REVOKE from 163, 164 or 165 and this must go red."""
    state, _, _, _ = _replay()
    for table, mig in (
        ("users", 163),
        ("ticker_report_cache", 164), ("sector_benchmarks", 164), ("whale_trades", 164),
        ("book_chunks", 164), ("market_deep_dive_cache", 164),
        ("watchlist_items", 165), ("research_reports", 165), ("chat_sessions", 165),
        ("portfolios", 165), ("user_learn_progress", 165),
    ):
        for r in _ROLES:
            st, num = state.get((table, r), ("", 0))
            assert st == "revoked" and num == mig, (
                f"public.{table} / {r}: expected REVOKE in {mig:03d}, effective state is "
                f"{st or 'none'} from {num:03d}"
            )


def test_users_table_has_no_per_user_policy_left_behind():
    """163 must DROP the three `users_*_own` policies, not just revoke the grant — a dead
    permissive policy re-opens the moment someone GRANTs again (the 150/151 lesson)."""
    code = _strip_sql_comments((_MIGRATIONS / "163_users_service_role_only.sql").read_text())
    for pol in ("users_select_own", "users_insert_own", "users_update_own"):
        assert re.search(rf'DROP\s+POLICY\s+IF\s+EXISTS\s+"?{pol}"?\s+ON\s+public\.users', code, re.I), \
            f"163 no longer drops policy {pol}"
    assert not re.search(r"DROP\s+POLICY[^;]*users_service_all", code, re.I), \
        "163 must keep users_service_all"


def test_the_detectors_are_not_vacuous():
    """Both halves must actually match something, and comments must not count."""
    state, created, n_rev, n_grant = _replay()
    assert n_rev >= 120, f"the REVOKE detector matched only {n_rev} role-revokes"
    assert n_grant >= 30, f"the GRANT detector matched only {n_grant} role-grants"
    # 69 today: migrations 001-014 never existed in the repo and a number of early tables
    # were created in Supabase Studio, so the migration set is NOT the full table list.
    assert len(created) >= 60, f"the CREATE TABLE detector found only {len(created)} tables"

    # Synthetic discrimination, independent of the live tree.
    assert _RE_REVOKE.search("REVOKE ALL ON public.x FROM anon, authenticated;")
    assert _RE_REVOKE.search("REVOKE ALL PRIVILEGES ON TABLE x FROM authenticated;")
    assert not _RE_REVOKE.search("REVOKE UPDATE (is_admin) ON public.users FROM anon;"), \
        "a column-level REVOKE must NOT count as closing the table (Postgres ignores it under a table-level grant)"
    g = _RE_GRANT.search("GRANT SELECT, INSERT ON public.y TO anon, authenticated;")
    assert g and _roles(g.group(3)) == ["anon", "authenticated"]
    # A FUNCTION/SEQUENCE grant must not read as a table grant (the object token has a
    # signature/space after it, so the table regex cannot match it).
    assert _RE_GRANT.search("GRANT EXECUTE ON FUNCTION public.f() TO service_role;") is None
    assert _RE_GRANT.search("GRANT USAGE, SELECT ON SEQUENCE x_id_seq TO service_role;") is None

    commented = "-- REVOKE ALL ON public.ghost FROM anon, authenticated;\n/* GRANT ALL ON public.ghost TO anon; */"
    stripped = _strip_sql_comments(commented)
    assert "REVOKE" not in stripped and "GRANT" not in stripped, "comment stripping is broken"
