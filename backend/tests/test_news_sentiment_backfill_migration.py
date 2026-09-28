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
