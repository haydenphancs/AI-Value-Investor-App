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
        "corporate_action_cache", "chat_starters", "chat_starter_answers",
        "earnings_cache", "growth_cache", "health_check_cache", "hedge_fund_quarters",
        "holders_cache", "notification_job_state", "price_alerts",
        "profit_power_cache", "revenue_breakdown_cache", "signal_of_confidence_cache",
        "ticker_data_cache",
    ),
    # Studio-born tables 078 REVOKEd from the clients and gave a service_role POLICY — but
    # never a service_role GRANT, so service_role could not read them either (prod 42501 on
    # social_mentions_history, 2026-09-11). 169 adds the grant. This replay can only see the
    # REVOKE half; the PRESENCE of the service_role grant is what
    # tests/test_snapshot_grants_parity.py asserts from the privileged dump.
    "078→169": ("daily_briefings", "market_insights", "social_mentions_history"),
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
        "whale_alerts",
        # a VIEW (security_invoker), not a table — kept here because 164 revokes it and the
        # replay only reads names; the snapshot guard classifies it as a view.
        "vector_search_stats",
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
    # 169: the default-privilege-era read grant on FMP news (no migration ever touched it, so
    # this replay was blind to it — the snapshot guard found it); the two user tables 102
    # left client-writable; and seven service-role-only tables whose creating migration
    # wrote no explicit REVOKE (169's are no-ops live, written so they can be declared here).
    "169": (
        "ticker_news_cache", "user_settings", "device_tokens",
        "ai_insight_budget", "chat_usage_budget", "geopolitical_macro_audit",
        "guest_report_budget", "price_catalyst_audit", "updates_insight_state",
        "agent_personas",
    ),
    # 170: the marketing engine (SYSTEM_DESIGN_GUIDELINES §12). iOS never reads these; the
    # media worker reaches them only through the token-gated internal API.
    "170": ("marketing_runs", "marketing_assets", "marketing_posts", "podcast_episodes"),
    # 173: the writer's accepted package + round violations (web-side only; writer output must
    # never reach the public bucket) and the smart link's daily tap counters.
    "173": ("marketing_scripts", "marketing_link_hits"),
    # 174: the Emerging Frontiers monthly rotation (run record, decisions, AI fit verdicts)
    # and daily theme insights — FMP-derived and AI output, read only by the backend. It
    # also CLOSED trending_themes' 081 read grant: its tickers are now the output of a
    # pipeline scoring FMP data, and its pins/blocks are editorial controls.
    "174": ("theme_rotation_runs", "theme_rotation_decisions", "theme_relevance_cache",
            "theme_daily_insights", "trending_themes"),
    # 175: Trillion-Dollar Club Bets — the registry, the hand-kept stakes and the built 13F
    # snapshots (FMP-licensed holdings, auth.md §1a). iOS has no Supabase client; only the
    # backend reads them, as service_role.
    "175": ("trillion_club_companies", "trillion_club_stakes", "trillion_club_filings"),
}

# Tables that DO grant anon or authenticated on purpose. Each needs the reason; an entry
# without one is a grant nobody has defended.
_CLIENT_ACCESS_BY_DESIGN: dict[str, str] = {
    "credit_packs": "storefront catalogue — GET /billing/credit-packs is `.public` (design doc §9.1); no FMP data, no user data",
    "plan_credits": "storefront catalogue — GET /billing/plans is `.public`; same",
    # trending_themes was here ("editorial, no FMP data") until 174 — see _SERVICE_ROLE_ONLY.
    "lessons": "Learn content (Investor Journey); editorial, no FMP data",
    "money_move_articles": "Learn content (Money Moves); editorial, no FMP data",
    "credit_transactions": "SELECT only, own rows via RLS (credit_transactions_select_own); the ledger the user is entitled to read",
    "subscriptions": "SELECT only, own rows via RLS; FK-bound to public.users",
    # device_tokens / user_settings were here until 169: the iOS app has no Supabase client
    # and the backend writes both as service_role, so their client grants were an unused door.
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
# The privilege list may carry column lists (`SELECT(col)`), and the object may be
# double-quoted — both used to slip past the scan (F11-3).
_RE_GRANT = re.compile(
    r"GRANT\s+([A-Za-z0-9_ ,()]+?)\s+ON\s+(?:TABLE\s+)?"
    r'(?:"?public"?\.)?"?([a-z0-9_]+)"?\s+TO\s+([^;\']+)',
    re.I,
)
#: `GRANT ... TO PUBLIC` reaches every role and survives a per-role REVOKE. It has no
#: legitimate use in this project (every client-visible table is granted by role, on
#: purpose), so on a service-role-only table it is an outright failure rather than a
#: state to replay.
_RE_GRANT_TO_PUBLIC = re.compile(
    r"GRANT\s+[A-Z_ ,()]+?\s+ON\s+(?:TABLE\s+)?(?:\"?public\"?\.)?\"?([a-z0-9_]+)\"?\s+TO\s+"
    r"(?:[a-z_\", ]*,\s*)?public\b",
    re.I,
)
_RE_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?([a-z0-9_]+)", re.I
)
_NOT_A_TABLE = {"function", "sequence", "schema", "all"}

#: The two statements that re-open EVERY table at once, which `_RE_GRANT` cannot see.
#:
#: `_RE_GRANT` needs `ON <name> TO`, so against Supabase's own bootstrap line —
#: `GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role;` — it
#: captures `ALL` as the table name and then fails to find `TO`, matching NOTHING. The
#: replay records no event, so it does not even reach the `_NOT_A_TABLE` skip, and the
#: whole guard stays green while every table in the schema is granted to the shipped anon
#: key. `ALTER DEFAULT PRIVILEGES` is the same hole for tables not yet created — and the
#: 2026-09-12 snapshot audit turned on the fact that this project has NONE of them, which
#: is exactly why an ungranted table is owner-only here.
_RE_SCHEMA_WIDE_GRANT = re.compile(
    r"GRANT\s+[A-Z ,]+?\s+ON\s+ALL\s+(?:TABLES|SEQUENCES|FUNCTIONS|ROUTINES)\s+"
    r"IN\s+SCHEMA\s+([a-z0-9_]+)\s+TO\s+([^;]+)",
    re.I,
)
_RE_DEFAULT_PRIVILEGES = re.compile(
    r"ALTER\s+DEFAULT\s+PRIVILEGES\b[^;]*?\bGRANT\s+[A-Z ,]+?\s+ON\s+"
    r"(?:TABLES|SEQUENCES|FUNCTIONS|ROUTINES)\s+TO\s+([^;]+)",
    re.I | re.S,
)


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
    """Mutation targets. Delete a REVOKE from 163, 164, 165 or 169 and this must go red."""
    state, _, _, _ = _replay()
    for table, mig in (
        ("users", 163),
        ("ticker_report_cache", 164), ("sector_benchmarks", 164), ("whale_trades", 164),
        ("book_chunks", 164), ("market_deep_dive_cache", 164),
        ("watchlist_items", 165), ("research_reports", 165), ("chat_sessions", 165),
        ("portfolios", 165), ("user_learn_progress", 165),
        ("ticker_news_cache", 169), ("user_settings", 169), ("device_tokens", 169),
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


def test_169_drops_the_client_policies_it_revokes():
    """169 must DROP the seven `*_own` policies on user_settings / device_tokens, not just
    revoke the grant — the same 150/151 lesson 163 pins for `users`."""
    code = _strip_sql_comments(
        (_MIGRATIONS / "169_service_role_grants_for_ungranted_tables.sql").read_text()
    )
    for table, pols in (
        ("user_settings", ("user_settings_select_own", "user_settings_insert_own", "user_settings_update_own")),
        ("device_tokens", ("device_tokens_select_own", "device_tokens_insert_own",
                           "device_tokens_update_own", "device_tokens_delete_own")),
    ):
        for pol in pols:
            assert re.search(rf'DROP\s+POLICY\s+IF\s+EXISTS\s+"?{pol}"?\s+ON\s+public\.{table}', code, re.I), \
                f"169 no longer drops policy {pol}"
        assert not re.search(rf"DROP\s+POLICY[^;]*{table}_service_all", code, re.I), \
            f"169 must keep {table}_service_all"
    # The grant-less trio gets its service_role GRANT here and nowhere else.
    for t in ("social_mentions_history", "daily_briefings", "market_insights"):
        assert re.search(rf"GRANT\s+ALL\s+ON\s+public\.{t}\s+TO\s+service_role", code, re.I), \
            f"169 no longer grants public.{t} to service_role"


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
    # F11-3: column-level and quoted grants are seen; TO PUBLIC is flagged.
    c = _RE_GRANT.search('GRANT SELECT(id), UPDATE(name) ON "public"."z" TO anon;')
    assert c and c.group(2) == "z" and "anon" in _roles(c.group(3))
    assert _RE_GRANT_TO_PUBLIC.search("GRANT SELECT ON public.z TO PUBLIC;").group(1) == "z"
    assert _RE_GRANT_TO_PUBLIC.search("GRANT SELECT ON TABLE z TO anon, public;").group(1) == "z"
    assert _RE_GRANT_TO_PUBLIC.search("GRANT SELECT ON public.z TO authenticated;") is None
    g = _RE_GRANT.search("GRANT SELECT, INSERT ON public.y TO anon, authenticated;")
    assert g and _roles(g.group(3)) == ["anon", "authenticated"]
    # A FUNCTION/SEQUENCE grant must not read as a table grant (the object token has a
    # signature/space after it, so the table regex cannot match it).
    assert _RE_GRANT.search("GRANT EXECUTE ON FUNCTION public.f() TO service_role;") is None
    assert _RE_GRANT.search("GRANT USAGE, SELECT ON SEQUENCE x_id_seq TO service_role;") is None

    commented = "-- REVOKE ALL ON public.ghost FROM anon, authenticated;\n/* GRANT ALL ON public.ghost TO anon; */"
    stripped = _strip_sql_comments(commented)
    assert "REVOKE" not in stripped and "GRANT" not in stripped, "comment stripping is broken"


# ── the two statements that would re-open everything at once ────────────────────────


def test_no_migration_grants_a_service_role_only_table_to_public():
    """F11-3: a grant TO PUBLIC is invisible to the per-role replay and survives a per-role
    REVOKE. None is legitimate here."""
    only = _all_service_role_only()
    hits = []
    for path in _sql_files():
        code = _strip_sql_comments(path.read_text(encoding="utf-8"))
        for m in _RE_GRANT_TO_PUBLIC.finditer(code):
            if m.group(1).lower() in only:
                hits.append((path.name, m.group(1).lower()))
    assert hits == [], f"GRANT ... TO PUBLIC on a service-role-only table: {hits}"


def test_no_migration_grants_the_whole_schema_to_a_client_role():
    """One line can undo every REVOKE in this file, and the replay cannot see it.

    `GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role;` is
    Supabase's own bootstrap snippet — it appears in their docs and in a lot of
    copy-pasted setup SQL — and `_RE_GRANT` does not match it at all (it needs
    `ON <name> TO`, so it grabs `ALL` and then looks for `TO` where `TABLES` is). The
    replay therefore records NO event and every assertion above stays green while the
    shipped anon key can read every table in the database.
    """
    offenders = []
    for path in _sql_files():
        code = _strip_sql_comments(path.read_text(encoding="utf-8"))
        for m in _RE_SCHEMA_WIDE_GRANT.finditer(code):
            roles = _roles(m.group(2))
            client = [r for r in roles if r in _ROLES]
            if client:
                offenders.append(f"{path.name}: schema-wide GRANT to {', '.join(client)}")
    assert not offenders, (
        "a schema-wide GRANT re-opens every table at once, including all the ones this "
        "file asserts are service-role-only:\n  " + "\n  ".join(offenders)
    )


def test_no_migration_sets_default_privileges_for_a_client_role():
    """`ALTER DEFAULT PRIVILEGES` is the same hole for tables that do not exist YET.

    It also underpins a separate finding: this project has none, which is precisely why a
    table with no GRANT line is owner-only (three Studio-born tables were 42501-ing in
    production for months because of it). If one ever lands, that reasoning breaks too.
    """
    offenders = []
    for path in _sql_files():
        code = _strip_sql_comments(path.read_text(encoding="utf-8"))
        for m in _RE_DEFAULT_PRIVILEGES.finditer(code):
            client = [r for r in _roles(m.group(1)) if r in _ROLES]
            if client:
                offenders.append(f"{path.name}: default privileges to {', '.join(client)}")
    assert not offenders, (
        "ALTER DEFAULT PRIVILEGES grants every FUTURE table to a client role:\n  "
        + "\n  ".join(offenders)
    )


def test_the_schema_wide_detectors_are_not_vacuous():
    """Mutation-proof by construction: the exact statements must match, and an ordinary
    per-table grant must NOT (or the two tests above would fire on every migration)."""
    hit = _RE_SCHEMA_WIDE_GRANT.search(
        "GRANT ALL ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role;"
    )
    assert hit, "the schema-wide detector cannot see Supabase's own bootstrap line"
    assert "anon" in _roles(hit.group(2))

    assert _RE_SCHEMA_WIDE_GRANT.search(
        "GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO authenticated;"
    ), "sequences are not covered"

    assert _RE_DEFAULT_PRIVILEGES.search(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon;"
    ), "the default-privileges detector cannot see the statement"

    assert not _RE_SCHEMA_WIDE_GRANT.search(
        "GRANT ALL ON public.x_cache TO service_role;"
    ), "an ordinary per-table grant must not trip the schema-wide detector"
    assert not _RE_DEFAULT_PRIVILEGES.search(
        "GRANT ALL ON public.x_cache TO service_role;"
    )



# ── storage.objects: no policy a client role can use survives the replay ───────────────
#
# A PUBLIC bucket is served from /storage/v1/object/public/<bucket>/<path>, which bypasses
# RLS; a SELECT policy for anon/authenticated on storage.objects only ever adds the LIST api,
# i.e. lets any holder of the shipped publishable key ENUMERATE the bucket. 153 dropped four
# such policies; 170 then re-created the pattern for `marketing-media` and 173 dropped it
# again (a rejected or never-published run's media must not be discoverable). The iOS app has
# no Supabase client, so no storage policy has a legitimate client-role reader at all — this
# pins that as an ordered replay, the storage twin of `_replay()` above.

_RE_STORAGE_POLICY = re.compile(
    r"(?P<verb>CREATE|DROP|ALTER)\s+POLICY\s+(?:IF\s+EXISTS\s+)?\"?(?P<name>[A-Za-z0-9_]+)\"?"
    r"\s+ON\s+(?:\"?storage\"?\.)\"?objects\"?(?![A-Za-z0-9_])(?P<rest>[^;]*)",
    re.I,
)
_STORAGE_CLIENT_ROLES = frozenset({"anon", "authenticated", "public"})


def _storage_policy_roles(rest: str) -> frozenset[str]:
    """The roles a CREATE POLICY applies to. Only the text BEFORE `USING` / `WITH CHECK` is
    searched, so a `to` inside the predicate cannot be read as the role clause; no `TO` clause
    at all means PUBLIC — every role, anon included."""
    head = re.split(r"\bUSING\b|\bWITH\s+CHECK\b", rest, maxsplit=1, flags=re.I)[0]
    m = re.search(r"\bTO\s+(.+)$", head, re.I | re.S)
    if not m:
        return frozenset({"public"})
    return frozenset(r.strip().strip('"').lower() for r in m.group(1).split(",") if r.strip())


def _storage_policy_replay(files) -> tuple[dict[str, frozenset[str]], set[str], list[str]]:
    """Ordered replay of CREATE / DROP POLICY on storage.objects over (filename, sql) pairs.

    Returns ({live policy: its roles}, {every policy ever created for a client role},
    [ALTER POLICY statements]) — ALTER is reported, not modelled, so the caller fails closed
    on it instead of guessing what it changed."""
    live: dict[str, frozenset[str]] = {}
    ever_client: set[str] = set()
    alters: list[str] = []
    for fname, sql in files:
        for m in _RE_STORAGE_POLICY.finditer(_strip_sql_comments(sql)):
            name, verb = m.group("name").lower(), m.group("verb").upper()
            if verb == "DROP":
                live.pop(name, None)
            elif verb == "ALTER":
                alters.append(f"{fname}: ALTER POLICY {name}")
            else:
                roles = _storage_policy_roles(m.group("rest"))
                live[name] = roles
                if roles & _STORAGE_CLIENT_ROLES:
                    ever_client.add(name)
    return live, ever_client, alters


def test_no_storage_policy_reaches_a_client_role():
    live, _, alters = _storage_policy_replay(
        (p.name, p.read_text(encoding="utf-8")) for p in _sql_files()
    )
    assert not alters, (
        "ALTER POLICY on storage.objects is not modelled by this replay — assert its effect by "
        f"hand and extend `_storage_policy_replay`: {alters}"
    )
    open_ = {n: sorted(r) for n, r in live.items() if r & _STORAGE_CLIENT_ROLES}
    assert not open_, (
        "storage.objects policy/policies still usable by anon/authenticated/PUBLIC at the end of "
        "the migration replay. On a PUBLIC bucket that is the LIST api (anyone with the shipped "
        "publishable key enumerates the bucket; object URLs never needed it — 153 §B); on a "
        "private bucket it is a read of licensed media. The app has no Supabase client:\n"
        + "\n".join(f"  • {n}: TO {', '.join(r)}" for n, r in sorted(open_.items()))
        + '\n\nAdd to a migration:  DROP POLICY IF EXISTS "<name>" ON storage.objects;'
    )


def test_the_storage_policy_replay_is_not_vacuous():
    live, ever_client, alters = _storage_policy_replay(
        (p.name, p.read_text(encoding="utf-8")) for p in _sql_files()
    )
    # It sees the service-role write policies that must survive, and the client-read ones the
    # history really created (061 … 170) — so an empty `open_` above means "dropped", not
    # "never matched".
    assert {"marketing_media_service_write", "user_avatars_service_all"} <= set(live), sorted(live)
    assert live["marketing_media_service_write"] == frozenset({"service_role"})
    assert {"marketing_media_public_read", "book_covers_public_read",
            "journey_media_public_read"} <= ever_client, sorted(ever_client)
    assert len(live) >= 9 and len(ever_client) >= 8, (len(live), len(ever_client))

    # Synthetic discrimination, independent of the live tree.
    live, ever, alters = _storage_policy_replay([
        ("001.sql",
         'CREATE POLICY "a" ON storage.objects FOR SELECT TO anon, authenticated USING (true);\n'
         'CREATE POLICY b ON storage.objects FOR ALL TO service_role USING (true) WITH CHECK (true);\n'
         "CREATE POLICY c ON storage.objects FOR SELECT USING (note = 'to be');\n"
         'CREATE POLICY d ON "storage"."objects" FOR SELECT TO "authenticated" USING (true);\n'
         "CREATE POLICY e ON storage.objects_archive FOR SELECT TO anon USING (true);\n"
         "CREATE POLICY f ON public.objects FOR SELECT TO anon USING (true);\n"
         '-- CREATE POLICY "ghost" ON storage.objects FOR SELECT TO anon USING (true);\n'),
        ("002.sql",
         'DROP POLICY IF EXISTS "a" ON storage.objects;\n'
         "/* DROP POLICY IF EXISTS d ON storage.objects; */\n"
         "ALTER POLICY b ON storage.objects TO anon;\n"),
    ])
    assert "a" not in live, "a later DROP must remove the policy"
    assert live["b"] == frozenset({"service_role"})
    assert live["c"] == frozenset({"public"}), "no TO clause is PUBLIC; a `to` in USING is not a role"
    assert live["d"] == frozenset({"authenticated"}), "quoted schema/table/role must still match"
    assert "e" not in live and "f" not in live, "only storage.objects is in scope"
    assert "ghost" not in live and "ghost" not in ever, "a commented CREATE must not count"
    assert ever == {"a", "c", "d"}
    assert alters == ["002.sql: ALTER POLICY b"], alters


# ── the window BEFORE the snapshot can see a table ────────────────────────────
#
# `test_snapshot_grants_parity` owns "service_role holds its grant" — but it reads the dump of
# the LIVE database, so it sees a table only after the migration was applied and re-dumped. By
# then a dropped `GRANT … TO service_role` has already answered 42501 in production (169's
# incident: a service_role POLICY with no GRANT admits nobody — this project has no default
# privileges). The guard below covers exactly that window: every table a migration creates that
# the snapshot does not hold yet must be granted to service_role (at least the four DML verbs)
# and have RLS enabled, in the migrations themselves. Once the table is applied and dumped the
# snapshot guard takes over, so between them a table is covered for its whole life.

_SNAPSHOT = Path(__file__).resolve().parents[1] / "database" / "schema_snapshot.sql"
_RE_SNAPSHOT_TABLE = re.compile(r"CREATE\s+TABLE\s+public\.([a-z0-9_]+)\s*\(", re.I)
_RE_DROP_TABLE = re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:public\.)?([a-z0-9_]+)", re.I)
_RE_ENABLE_RLS = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:public\.)?([a-z0-9_]+)\s+"
    r"ENABLE\s+ROW\s+LEVEL\s+SECURITY", re.I,
)
_RE_REVOKE_SERVICE = re.compile(
    r"REVOKE\s+ALL(?:\s+PRIVILEGES)?\s+ON\s+(?:TABLE\s+)?(?:public\.)?([a-z0-9_]+)\s+FROM\s+([^;']+)", re.I,
)
_DML = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})


def _pending_table_state(files) -> dict[str, dict]:
    """{table: {"created": n, "privs": set, "rls": bool}} for tables whose LAST event in the
    migrations is a CREATE — replayed from (name, sql) pairs so the detector is testable on
    synthetic input. `privs` is service_role's effective table privileges (GRANT adds, REVOKE ALL
    FROM service_role resets); `ALL` expands to the DML verbs."""
    state: dict[str, dict] = {}
    for name, text in files:
        num = int(name[:3])
        code = _strip_sql_comments(text)
        events: list[tuple[int, str, str, object]] = []
        for m in _RE_CREATE_TABLE.finditer(code):
            events.append((m.start(), m.group(1).lower(), "create", None))
        for m in _RE_DROP_TABLE.finditer(code):
            events.append((m.start(), m.group(1).lower(), "drop", None))
        for m in _RE_ENABLE_RLS.finditer(code):
            events.append((m.start(), m.group(1).lower(), "rls", None))
        for m in _RE_GRANT.finditer(code):
            if "service_role" in re.split(r"[,\s]+", m.group(3).strip().lower()):
                privs = {p.strip().split("(")[0].upper() for p in m.group(1).split(",")}
                privs = set(_DML) if privs & {"ALL", "ALL PRIVILEGES"} else privs
                events.append((m.start(), m.group(2).lower(), "grant", privs))
        for m in _RE_REVOKE_SERVICE.finditer(code):
            if "service_role" in re.split(r"[,\s]+", m.group(2).strip().lower()):
                events.append((m.start(), m.group(1).lower(), "revoke", None))
        for _, table, kind, privs in sorted(events, key=lambda e: e[0]):
            if kind == "create":
                state.setdefault(table, {"created": num, "privs": set(), "rls": False, "live": True})
                state[table]["live"] = True
            elif kind == "drop":
                if table in state:
                    state[table]["live"] = False
            elif table in state:
                if kind == "rls":
                    state[table]["rls"] = True
                elif kind == "grant":
                    state[table]["privs"] |= privs  # type: ignore[operator]
                elif kind == "revoke":
                    state[table]["privs"] = set()
    return {t: v for t, v in state.items() if v["live"]}


def _pending_tables() -> dict[str, dict]:
    in_snapshot = {m.group(1).lower() for m in _RE_SNAPSHOT_TABLE.finditer(_SNAPSHOT.read_text(encoding="utf-8"))}
    assert len(in_snapshot) > 80, f"the snapshot scan found only {len(in_snapshot)} tables — it has rotted"
    state = _pending_table_state((p.name, p.read_text(encoding="utf-8")) for p in _sql_files())
    return {t: v for t, v in state.items() if t not in in_snapshot and t not in _DROPPED}


def test_every_not_yet_applied_table_is_granted_to_service_role_and_has_rls():
    pending = _pending_tables()
    problems = []
    for table, st in sorted(pending.items()):
        missing = sorted(_DML - st["privs"])
        if missing:
            problems.append(f"{table} (migration {st['created']:03d}): service_role lacks {missing}")
        if not st["rls"]:
            problems.append(f"{table} (migration {st['created']:03d}): no ENABLE ROW LEVEL SECURITY")
    assert not problems, (
        "a migration creates a table the snapshot does not hold yet, without the service_role "
        "GRANT / RLS it needs — applied as is, every backend read 42501s (169):\n  "
        + "\n  ".join(problems)
    )


def test_the_pending_table_guard_is_not_vacuous():
    # It sees the not-yet-applied tables of this change (remove a name once 173 is dumped).
    pending = _pending_tables()
    assert {"marketing_scripts", "marketing_link_hits"} <= set(pending) or not (
        _MIGRATIONS / "173_marketing_scripts_and_link_hits.sql").exists(), sorted(pending)

    # Synthetic discrimination: a dropped GRANT, a dropped RLS, a partial grant, a revoke after
    # a grant, a commented grant, and a table dropped again are each seen for what they are.
    state = _pending_table_state([
        ("001_a.sql",
         "CREATE TABLE IF NOT EXISTS public.ok_t (id int);\n"
         "ALTER TABLE public.ok_t ENABLE ROW LEVEL SECURITY;\n"
         "GRANT ALL ON public.ok_t TO service_role;\n"
         "CREATE TABLE IF NOT EXISTS public.no_grant (id int);\n"
         "ALTER TABLE public.no_grant ENABLE ROW LEVEL SECURITY;\n"
         "-- GRANT ALL ON public.no_grant TO service_role;\n"
         "CREATE TABLE IF NOT EXISTS public.no_rls (id int);\n"
         "GRANT ALL ON public.no_rls TO service_role;\n"
         "CREATE TABLE IF NOT EXISTS public.partial (id int);\n"
         "ALTER TABLE public.partial ENABLE ROW LEVEL SECURITY;\n"
         "GRANT SELECT, INSERT ON public.partial TO service_role;\n"
         "CREATE TABLE IF NOT EXISTS public.gone (id int);\n"),
        ("002_b.sql",
         "REVOKE ALL ON public.ok_t FROM anon, authenticated;\n"
         "REVOKE ALL ON TABLE public.no_rls FROM service_role;\n"
         "DROP TABLE IF EXISTS public.gone;\n"),
    ])
    assert "gone" not in state
    assert _DML <= state["ok_t"]["privs"] and state["ok_t"]["rls"]
    assert state["no_grant"]["privs"] == set() and state["no_grant"]["rls"]
    assert state["no_rls"]["privs"] == set() and not state["no_rls"]["rls"]
    assert state["partial"]["privs"] == {"SELECT", "INSERT"}
