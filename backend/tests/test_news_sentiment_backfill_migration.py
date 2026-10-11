"""Migration 181 (news-sentiment backfill queue + fenced RPCs), read statically and bound to
the Python that calls it.

Applied by hand in Supabase Studio, and every service test talks to an in-memory fake — so a
renamed RPC parameter or a CHECK that forgot 'backfill' would leave the suite green while every
production call failed.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

import app.services.news_sentiment_backfill_service as bf
import app.services.news_sentiment_trend_service as trend

_SQL = Path(__file__).resolve().parents[1] / "database" / "migrations" / "181_news_sentiment_backfill.sql"


def _code() -> str:
    lines = []
    for line in _SQL.read_text().splitlines():
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


def _params(fn: str) -> list:
    m = re.search(rf"CREATE OR REPLACE FUNCTION public\.{fn}\((.*?)\)\s*RETURNS", CODE, re.S)
    assert m, f"{fn} not found"
    return [p.strip().split()[0] for p in m.group(1).split(",") if p.strip()]


def _service_src() -> str:
    return inspect.getsource(bf)


def test_it_is_one_idempotent_transaction():
    assert CODE.strip().startswith("BEGIN;") and CODE.strip().endswith("COMMIT;")
    assert not re.search(r"CREATE TABLE (?!IF NOT EXISTS)", CODE)
    assert not re.search(r"CREATE INDEX (?!IF NOT EXISTS)", CODE)
    assert not re.search(r"CREATE FUNCTION", CODE)
    assert "ADD COLUMN IF NOT EXISTS model" in CODE


def test_the_source_check_is_the_python_vocabulary():
    m = re.search(r"CHECK \(source IN \(([^)]*)\)\)", CODE)
    assert m, "source CHECK not found"
    assert {v.strip().strip("'") for v in m.group(1).split(",")} == set(trend.SOURCES)
    # Re-added under an explicit name after dropping whatever unnamed CHECK 180 left.
    assert "ADD CONSTRAINT news_sentiment_log_source_check" in CODE
    assert "pg_get_constraintdef(con.oid) ILIKE '%source%'" in CODE


def test_every_rpc_the_service_calls_exists_with_its_parameters():
    src = _service_src()
    expected = {
        bf.CLAIM_RPC: ["p_token", "p_limit", "p_lease_seconds", "p_max_attempts"],
        bf.RENEW_RPC: ["p_scope", "p_token", "p_lease_seconds", "p_covered_from", "p_covered_to"],
        bf.FINISH_RPC: ["p_scope", "p_token", "p_status", "p_next_run_at", "p_covered_from",
                        "p_covered_to", "p_articles", "p_labels", "p_error"],
        bf.ENQUEUE_RPC: ["p_scopes"],
        bf.DISCOVER_RPC: [],
    }
    for fn, params in expected.items():
        assert _params(fn) == params, fn
        for p in params:
            assert f'"{p}"' in src, f"the service never sends {p} to {fn}"


def test_the_status_vocabulary_matches_what_the_service_writes():
    m = re.search(r"CHECK \(status IN \(([^)]*)\)\)", CODE)
    allowed = {v.strip().strip("'") for v in m.group(1).split(",")}
    assert allowed == {"queued", "running", "done", "failed", "unsupported"}
    for status in ("queued", "done", "failed", "unsupported"):
        assert f'"{status}"' in _service_src()
    finish = re.search(r"FUNCTION public\.finish_sentiment_backfill.*?\$\$;", CODE, re.S).group(0)
    assert "NOT IN ('queued', 'done', 'failed', 'unsupported')" in finish


def test_renew_and_finish_are_fenced_on_the_claim_token():
    for fn in ("renew_sentiment_backfill", "finish_sentiment_backfill"):
        body = re.search(rf"FUNCTION public\.{fn}.*?\$\$;", CODE, re.S).group(0)
        assert "claim_token = p_token" in body, fn
        assert "RETURN FOUND" in body, fn


def test_the_claim_uses_the_db_clock_skip_locked_and_only_watched_scopes():
    body = re.search(r"FUNCTION public\.claim_sentiment_backfill.*?\$\$;", CODE, re.S).group(0)
    assert "FOR UPDATE SKIP LOCKED" in body
    assert "lease_until = now() +" in body
    assert "EXISTS (SELECT 1 FROM public.watchlist_items w WHERE w.ticker = s.scope)" in body
    assert "attempts    = b.attempts + 1" in body
    assert "s.updated_at < now() - interval '24 hours'" in body


def test_a_deferral_gives_back_its_attempt_and_success_clears_them():
    body = re.search(r"FUNCTION public\.finish_sentiment_backfill.*?\$\$;", CODE, re.S).group(0)
    assert "WHEN p_status IN ('done', 'unsupported') THEN 0" in body
    assert "WHEN p_status = 'queued' THEN GREATEST(attempts - 1, 0)" in body


def test_the_market_scope_is_never_queued_and_covered_tickers_are_left_alone():
    body = re.search(r"FUNCTION public\.enqueue_sentiment_backfill.*?\$\$;", CODE, re.S).group(0)
    assert "<> '__MARKET__'" in body
    assert "b.covered_to < v_today - 1" in body


def test_it_is_service_role_only_and_invoker():
    assert "SECURITY DEFINER" not in CODE
    assert "ALTER TABLE public.news_sentiment_backfill ENABLE ROW LEVEL SECURITY;" in CODE
    assert "REVOKE ALL ON public.news_sentiment_backfill FROM anon, authenticated;" in CODE
    assert "GRANT ALL ON public.news_sentiment_backfill TO service_role;" in CODE
    for fn in (bf.CLAIM_RPC, bf.RENEW_RPC, bf.FINISH_RPC, bf.ENQUEUE_RPC, bf.DISCOVER_RPC):
        assert re.search(rf"REVOKE ALL ON FUNCTION public\.{fn}\(.*?\)\s+FROM PUBLIC, anon, authenticated;", CODE, re.S), fn
        assert re.search(rf"GRANT EXECUTE ON FUNCTION public\.{fn}\(.*?\) TO service_role;", CODE, re.S), fn


@pytest.mark.parametrize("column", ["headline", "summary", "article_url", "url", "user_id"])
def test_the_queue_holds_no_news_text_and_no_user(column):
    m = re.search(r"CREATE TABLE IF NOT EXISTS public\.news_sentiment_backfill \((.*?)\n\);", CODE, re.S)
    assert not re.search(rf"^\s*{column}\s", m.group(1), re.M)


# ── 182: the queue fixes from the 2026-09-27 deep-check ─────────────────────────

_SQL_182 = _SQL.parent / "182_news_sentiment_backfill_fixes.sql"


def _code_182() -> str:
    lines = []
    for line in _SQL_182.read_text().splitlines():
        in_str, out, i = False, [], 0
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


CODE_182 = _code_182()


def _fn_182(fn: str) -> str:
    m = re.search(rf"CREATE OR REPLACE FUNCTION public\.{fn}\((.*?)\$\$;", CODE_182, re.S)
    assert m, f"{fn} not in 182"
    return m.group(0)


def test_182_is_one_idempotent_transaction_of_same_signature_replacements():
    assert CODE_182.strip().startswith("BEGIN;") and CODE_182.strip().endswith("COMMIT;")
    assert not re.search(r"CREATE FUNCTION", CODE_182)
    assert not re.search(r"CREATE INDEX (?!IF NOT EXISTS)", CODE_182)
    assert "ADD COLUMN IF NOT EXISTS last_failed_at TIMESTAMPTZ" in CODE_182
    assert "SECURITY DEFINER" not in CODE_182
    for fn, params in ((bf.CLAIM_RPC, ["p_token", "p_limit", "p_lease_seconds", "p_max_attempts"]),
                       (bf.FINISH_RPC, ["p_scope", "p_token", "p_status", "p_next_run_at", "p_covered_from",
                                        "p_covered_to", "p_articles", "p_labels", "p_error"]),
                       (bf.ENQUEUE_RPC, ["p_scopes"])):
        m = re.search(rf"CREATE OR REPLACE FUNCTION public\.{fn}\((.*?)\)\s*RETURNS", CODE_182, re.S)
        assert m, fn
        assert [p.strip().split()[0] for p in m.group(1).split(",") if p.strip()] == params, fn
        assert re.search(rf"REVOKE ALL ON FUNCTION public\.{fn}\(.*?\)\s+FROM PUBLIC, anon, authenticated;", CODE_182, re.S), fn
        assert re.search(rf"GRANT EXECUTE ON FUNCTION public\.{fn}\(.*?\) TO service_role;", CODE_182, re.S), fn


def test_182_makes_unsupported_scopes_claimable_on_their_recheck():
    body = _fn_182(bf.CLAIM_RPC)
    assert "s.status IN ('queued', 'done', 'failed', 'running', 'unsupported')" in body
    for keep in ("FOR UPDATE SKIP LOCKED", "s.next_run_at <= now()", "attempts       = b.attempts + 1"):
        assert keep in body, keep
    assert bf.UNSUPPORTED_RECHECK_DAYS == 30


def test_182_the_cap_clock_is_last_failed_at_not_updated_at():
    claim = _fn_182(bf.CLAIM_RPC)
    predicate = claim[claim.index("WHERE s.next_run_at"):claim.index("ORDER BY")]
    assert "updated_at" not in predicate, "a deferral writes updated_at; it must not lock a scope out"
    assert "s.last_failed_at < now() - interval '24 hours'" in predicate
    assert "last_failed_at = CASE WHEN b.status = 'running' THEN now() ELSE b.last_failed_at END" in claim, \
        "a lapsed-lease takeover counts as a failure (crash loops stay once a day)"
    finish = _fn_182(bf.FINISH_RPC)
    for arm in ("WHEN p_status = 'failed' THEN now()",
                "WHEN p_status IN ('done', 'unsupported') THEN NULL",
                "ELSE last_failed_at"):
        assert arm in finish, arm


def test_182_enqueue_never_touches_the_cap_clock():
    body = _fn_182(bf.ENQUEUE_RPC)
    assert "updated_at" not in body and "last_failed_at" not in body
    assert "next_run_at  = LEAST(b.next_run_at, now())" in body
    assert "<> '__MARKET__'" in body and "b.covered_to < v_today - 1" in body


def test_182_enqueue_turns_a_stale_done_back_to_queued():
    body = _fn_182(bf.ENQUEUE_RPC)
    assert "status       = CASE WHEN b.status = 'done' THEN 'queued' ELSE b.status END" in body


def test_182_the_claim_compares_tickers_normalized_like_discover():
    """A legacy 'nvda ' watchlist row was queued as 'NVDA' by discover but the claim compared
    the raw ticker, so the scope stayed 'queued' — and the app on "Building…" — forever."""
    claim = _fn_182(bf.CLAIM_RPC)
    assert "WHERE upper(btrim(w.ticker)) = s.scope" in claim
    assert "w.ticker = s.scope" not in claim
    assert "CREATE INDEX IF NOT EXISTS idx_watchlist_items_ticker_norm" in CODE_182
    assert "ON public.watchlist_items (upper(btrim(ticker)))" in CODE_182
    # discover (181) stores exactly that form.
    assert "SELECT DISTINCT upper(btrim(w.ticker))" in CODE
    # "Watched" stays the codebase-wide universe (guest rows included) on purpose.
    assert "JOIN public.users" not in CODE_182


def test_182_verify_queries_match_a_correct_install():
    """The owner runs VERIFY by hand, and pg_get_functiondef returns the body WITH its
    comments: a comment naming updated_at made a correct install read 't'."""
    raw = _SQL_182.read_text()
    body = raw[raw.index("CREATE OR REPLACE FUNCTION public.enqueue_sentiment_backfill"):]
    body = body[:body.index("$$;")]
    assert "updated_at" not in body.lower()


# ── 183: re-label the first pass the old labeller wrote (2026-09-28) ─────────────

_SQL_183 = _SQL.parent / "183_news_sentiment_backfill_relabel.sql"


def _code_183() -> str:
    return "\n".join(line.split("--", 1)[0] for line in _SQL_183.read_text().splitlines())


def test_183_deletes_only_old_labeller_backfill_rows_and_requeues_first():
    """Bounded by source AND the moment the live-request build went live; the re-queue runs
    BEFORE the delete (it finds its scopes by those rows), so a re-run is a no-op."""
    code = _code_183()
    assert code.strip().startswith("BEGIN;") and code.strip().endswith("COMMIT;")
    cutoff = "'2026-09-28 23:53:17+00'"          # commit 610021e2's deploy, 17:53:17 MDT
    delete = code[code.index("DELETE FROM public.news_sentiment_log"):]
    delete = delete[:delete.index(";")]
    assert "source = 'backfill'" in delete and f"labelled_at < {cutoff}" in delete
    update = code[code.index("UPDATE public.news_sentiment_backfill b"):code.index("DELETE FROM")]
    for piece in ("status         = 'queued'", "covered_from   = NULL", "covered_to     = NULL",
                  "claim_token    = NULL", "WHERE b.status <> 'unsupported'",
                  f"AND l.labelled_at < {cutoff}", "AND l.source = 'backfill'"):
        assert piece in update, piece
    assert "AND EXISTS (" in update and "NOT EXISTS" not in update, "re-queue exactly the scopes with old rows"
    assert code.index("UPDATE public.news_sentiment_backfill") < code.index("DELETE FROM public.news_sentiment_log")
    assert "DESTRUCTIVE (bounded)" in _SQL_183.read_text()
    for forbidden in ("DROP ", "TRUNCATE", "CREATE TABLE", "ALTER TABLE"):
        assert forbidden not in code, forbidden


# ── 191/192: the labeller column and the v2 re-label (2026-10-10) ───────────────

_SQL_191 = _SQL.parent / "191_news_sentiment_labeller_version.sql"
_SQL_192 = _SQL.parent / "192_news_sentiment_relabel_v2.sql"


def _code(path) -> str:
    """The SQL without its `--` comments (the header names every token the asserts look for)."""
    return "\n".join(line.split("--", 1)[0] for line in path.read_text().splitlines())


def test_191_adds_the_column_the_writer_sends_with_a_check_it_satisfies():
    import app.services.news_sentiment_trend_service as trend

    code = _code(_SQL_191)
    assert code.strip().startswith("BEGIN;") and code.strip().endswith("COMMIT;")
    assert "SET LOCAL lock_timeout" in code
    assert "ADD COLUMN IF NOT EXISTS labeller smallint" in code
    assert "DROP CONSTRAINT IF EXISTS news_sentiment_log_labeller_check" in code
    m = re.search(r"CHECK \(labeller IS NULL OR labeller BETWEEN (\d+) AND (\d+)\)", code)
    assert m and int(m.group(1)) <= trend.TONE_LABELLER_VERSION <= int(m.group(2))
    assert trend._OPTIONAL_COLUMNS["labeller"][1] == 191
    assert trend._OPTIONAL_COLUMNS["skip_reason"][1] == 191
    # skip_reason: the CHECK lists exactly the gate's reasons, and the RPC counts only NULL ones.
    m = re.search(r"skip_reason IN \(([^)]*)\)", code)
    assert m and sorted(x.strip(" '\n") for x in m.group(1).split(",")) == sorted(
        ["off_topic", "holdings_filing", "solicitation", "duplicate_title"])
    rpc = code[code.index("CREATE OR REPLACE FUNCTION public.news_sentiment_daily"):]
    rpc = rpc[:rpc.index("$$;")]
    assert "AND l.skip_reason IS NULL" in rpc and "SECURITY INVOKER" in rpc
    assert "RETURNS TABLE (day DATE, bullish BIGINT, bearish BIGINT, neutral BIGINT)" in rpc
    assert "GRANT EXECUTE ON FUNCTION public.news_sentiment_daily(TEXT, DATE) TO service_role;" in code
    assert "FROM PUBLIC, anon, authenticated;" in code
    for forbidden in ("DROP TABLE", "DROP COLUMN", "DELETE ", "TRUNCATE", "SECURITY DEFINER",
                      "TO anon", "TO authenticated", "GRANT ALL"):
        assert forbidden not in code, forbidden


def test_192_requeues_first_then_deletes_only_old_rule_rows_of_rebuildable_scopes():
    import app.services.news_sentiment_trend_service as trend

    code = _code(_SQL_192)
    assert code.strip().startswith("BEGIN;") and code.strip().endswith("COMMIT;")
    # Step 0: refuse while an old-rule writer is still live (a deploy's overlap).
    assert "labeller IS NULL AND labelled_at > now() - interval '15 minutes'" in code
    assert code.index("RAISE EXCEPTION") < code.index("UPDATE public.news_sentiment_backfill")
    assert "status = 'running' AND lease_until > now()" in code, "a live old-code run blocks it too"
    assert "watchlist_items" not in code, "unwatched scopes are re-labelled too (rebuilt on re-watch)"
    update = code[code.index("UPDATE public.news_sentiment_backfill b"):code.index("DELETE FROM")]
    for piece in ("status         = 'queued'", "covered_from   = NULL", "covered_to     = NULL",
                  "claim_token    = NULL", "lease_until    = NULL", "WHERE b.status <> 'unsupported'",
                  "AND b.scope <> '__MARKET__'", "AND l.labeller IS NULL"):
        assert piece in update, piece
    first = code[code.index("DELETE FROM public.news_sentiment_log l"):]
    first = first[:first.index(";")]
    for piece in ("l.labeller IS NULL", "l.scope <> '__MARKET__'", "b.status <> 'unsupported'"):
        assert piece in first, piece
    assert r"WHERE scope LIKE 'COMMODITY\_%'" in code
    assert code.count("DELETE FROM") == 2
    text = _SQL_192.read_text()
    assert "DESTRUCTIVE (bounded)" in text and "3. DESTRUCTIVE" in text
    for forbidden in ("DROP ", "TRUNCATE", "CREATE TABLE", "ALTER TABLE"):
        assert forbidden not in code, forbidden
    assert not trend.is_trend_scope("COMMODITY_GC"), "the code must no longer log what 192 deletes"


def test_193_adds_the_title_hash_the_copy_check_reads():
    sql = _SQL.parent / "193_news_sentiment_title_hash.sql"
    code = _code(sql)
    assert code.strip().startswith("BEGIN;") and code.strip().endswith("COMMIT;")
    assert "ADD COLUMN IF NOT EXISTS title_hash uuid" in code
    assert "CREATE INDEX IF NOT EXISTS idx_news_sentiment_log_scope_title" in code
    assert "(scope, title_hash)" in code and "WHERE title_hash IS NOT NULL" in code
    assert trend._OPTIONAL_COLUMNS["title_hash"][1] == 193
    for forbidden in ("DROP ", "DELETE ", "TRUNCATE", "GRANT "):
        assert forbidden not in code, forbidden
    # Migration 104: a hash column only — never a column that could hold the headline itself.
    assert not re.search(r"ADD COLUMN[^;]*\b(?:text|varchar|jsonb)\b", code, re.I)
