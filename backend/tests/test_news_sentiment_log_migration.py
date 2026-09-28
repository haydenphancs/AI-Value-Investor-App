"""Migration 180 (news_sentiment_log + news_sentiment_daily), read statically and bound to
the Python that uses it.

The migration is applied by hand in Supabase Studio, and every service test talks to an
in-memory fake — so a renamed RPC parameter, a renamed RETURNS TABLE column, or a seed that
hashes differently from `article_key` would leave the suite green while every production
read raised PGRST202 (a 503, and the chart silently hidden) or every row was dropped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import app.services.news_sentiment_trend_service as svc

_SQL_PATH = (
    Path(__file__).resolve().parents[1] / "database" / "migrations" / "180_news_sentiment_log.sql"
)


def _code() -> str:
    """The migration without `--` comments (the header discusses the very tokens asserted on)."""
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


def _table_body() -> str:
    m = re.search(rf"CREATE TABLE IF NOT EXISTS public\.{svc.TABLE} \((.*?)\n\);", CODE, re.S)
    assert m, f"{svc.TABLE} CREATE TABLE not found"
    return m.group(1)


def _function() -> re.Match:
    m = re.search(
        rf"CREATE OR REPLACE FUNCTION public\.{svc.DAILY_RPC}\((.*?)\)\s*RETURNS TABLE \((.*?)\)",
        CODE, re.S,
    )
    assert m, f"{svc.DAILY_RPC} not found"
    return m


def test_it_is_one_transaction_and_idempotent():
    assert CODE.strip().startswith("BEGIN;") and CODE.strip().endswith("COMMIT;")
    assert not re.search(r"CREATE TABLE (?!IF NOT EXISTS)", CODE)
    assert not re.search(r"CREATE INDEX (?!IF NOT EXISTS)", CODE)
    assert not re.search(r"CREATE FUNCTION", CODE), "functions must be CREATE OR REPLACE"
    assert "ON CONFLICT (scope, article_key) DO NOTHING" in CODE, "the seed must be re-runnable"


def test_the_primary_key_is_the_writers_conflict_target():
    body = _table_body()
    pk = re.search(r"PRIMARY KEY \(([^)]*)\)", body)
    assert pk, "no primary key"
    cols = [c.strip() for c in pk.group(1).split(",")]
    # Every writer (live labels and the backfill) goes through upsert_log_rows.
    import inspect

    src = inspect.getsource(svc.upsert_log_rows)
    assert src.count('on_conflict="scope,article_key", ignore_duplicates=True') == 2, (
        "both the first try and the no-model retry must keep first-label-wins"
    )
    assert "upsert_log_rows" in inspect.getsource(svc.record_labels)
    assert cols == ["scope", "article_key"]


def test_the_sentiment_check_is_the_python_vocabulary():
    m = re.search(r"sentiment\s+TEXT\s+NOT NULL CHECK \(sentiment IN \(([^)]*)\)\)", _table_body())
    assert m, "sentiment CHECK not found"
    allowed = {v.strip().strip("'") for v in m.group(1).split(",")}
    assert allowed == set(svc.SENTIMENTS)


def test_the_scope_bound_matches_the_writer():
    assert "CHECK (char_length(scope) BETWEEN 1 AND 32)" in _table_body()
    assert svc.build_log_rows("X" * 32, [{"external_id": "a", "sentiment": "bullish"}],
                              now=__import__("datetime").datetime(2026, 9, 27,
                              tzinfo=__import__("datetime").timezone.utc))
    assert svc.build_log_rows("X" * 33, [{"external_id": "a", "sentiment": "bullish"}],
                              now=__import__("datetime").datetime(2026, 9, 27,
                              tzinfo=__import__("datetime").timezone.utc)) == []


def test_the_rpc_signature_is_what_the_service_sends_and_reads():
    m = _function()
    params = [p.strip().split()[0] for p in m.group(1).split(",")]
    assert params == ["p_scope", "p_since"]
    import inspect

    fetch_src = inspect.getsource(svc.NewsSentimentTrendService._fetch)
    assert '{"p_scope": scope, "p_since": since.isoformat()}' in fetch_src
    returned = [c.strip().split()[0] for c in m.group(2).split(",")]
    # shape_series reads `day` and one column per sentiment.
    assert returned == ["day", *svc.SENTIMENTS]
    assert 'row.get("day")' in inspect.getsource(svc.shape_series)


def test_the_seed_hashes_and_skips_exactly_like_the_live_writer():
    assert "md5(c.external_id)::uuid" in CODE
    # article_key is md5(utf-8 bytes) → uuid, the same value as Postgres md5(text)::uuid.
    assert svc.article_key("abc") == "90015098-3cd2-4fb0-d696-3f7d28e17f72"
    assert r"NOT LIKE 'unknown\_%'" in CODE
    assert svc.article_key("unknown_3") is None
    # The same 96-hour bound as MAX_LABEL_AGE_HOURS, and the same future-date fallback.
    assert svc.MAX_LABEL_AGE_HOURS == 96
    assert "c.published_at >= now() - interval '96 hours'" in CODE
    assert "c.published_at > now() + interval '2 hours'" in CODE
    # Legacy spellings the cache still admits map to the log's vocabulary.
    assert "WHEN 'positive' THEN 'bullish'" in CODE and "WHEN 'negative' THEN 'bearish'" in CODE


def test_it_is_service_role_only():
    assert f"ALTER TABLE public.{svc.TABLE} ENABLE ROW LEVEL SECURITY;" in CODE
    assert f"REVOKE ALL ON public.{svc.TABLE} FROM anon, authenticated;" in CODE
    assert f"GRANT ALL ON public.{svc.TABLE} TO service_role;" in CODE
    assert re.search(
        rf"REVOKE ALL ON FUNCTION public\.{svc.DAILY_RPC}\(TEXT, DATE\)\s+FROM PUBLIC, anon, authenticated;",
        CODE,
    )
    assert f"GRANT EXECUTE ON FUNCTION public.{svc.DAILY_RPC}(TEXT, DATE) TO service_role;" in CODE
    assert "SECURITY DEFINER" not in CODE


@pytest.mark.parametrize("column", ["headline", "summary", "article_url", "url", "source_name", "publisher"])
def test_no_news_text_column_exists(column):
    """Migration 104's rule: no long-term copy of news text. Labels only."""
    assert not re.search(rf"^\s*{column}\s", _table_body(), re.M)
