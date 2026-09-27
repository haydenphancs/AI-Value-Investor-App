"""`scripts/preview_insight_card.py` — the read-only replay of an Insights card.

Hermetic: only the pure helpers and the read-only guarantees are exercised; nothing
here reaches FMP, Gemini or Supabase.
"""

from datetime import date, datetime, timezone

import pytest

from scripts import preview_insight_card as preview


def test_the_tripwire_refuses_every_supabase_use(monkeypatch):
    import app.database as database

    monkeypatch.setattr(database, "_supabase_client", None)
    preview.install_supabase_tripwire()
    client = database._supabase_client
    for name in ("table", "rpc", "storage", "from_"):
        with pytest.raises(preview.SupabaseRefused):
            getattr(client, name)


def test_fmp_wall_clock_is_read_as_et_not_utc():
    # FMP stamps ET without an offset; 16:10 ET on 2026-09-10 is 20:10Z.
    rows = preview.map_fmp_rows(
        [{"title": "Oracle Announces Q1 Results", "publishedDate": "2026-09-10 16:10:00",
          "url": "https://x/1", "symbol": "ORCL", "text": "t"}],
        "ORCL",
    )
    assert rows[0]["published_at"].startswith("2026-09-10T20:10:00")
    assert rows[0]["related_tickers"] == ["ORCL"]
    assert rows[0]["external_id"] == "https://x/1"


def test_rows_are_deduplicated_and_non_dicts_skipped():
    raw = [
        {"title": "A", "url": "https://x/1", "publishedDate": "2026-09-10 10:00:00"},
        {"title": "A again", "url": "https://x/1", "publishedDate": "2026-09-10 10:05:00"},
        "garbage", None,
    ]
    assert len(preview.map_fmp_rows(raw, "ORCL")) == 1


def test_the_market_scope_records_no_synthetic_ticker():
    rows = preview.map_fmp_rows([{"title": "Fed", "url": "u", "symbol": None}], "__MARKET__")
    assert rows[0]["related_tickers"] == []


def test_rows_after_the_as_of_are_dropped_and_the_rest_sorted_newest_first():
    as_of = datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc)
    rows = [
        {"headline": "old", "published_at": "2026-09-10T14:00:00+00:00"},
        {"headline": "future", "published_at": "2026-09-10T22:00:00+00:00"},
        {"headline": "new", "published_at": "2026-09-10T20:10:00+00:00"},
        {"headline": "undated", "published_at": None},
    ]
    assert [r["headline"] for r in preview.rows_as_of(rows, as_of)] == ["new", "old"]


def test_masking_actuals_only_touches_the_as_of_day():
    rows = [
        {"symbol": "ORCL", "date": "2026-09-10", "epsActual": 1.92, "revenueActual": 1.0},
        {"symbol": "ADBE", "date": "2026-09-09", "epsActual": 5.0, "revenueActual": 2.0},
        "garbage",
    ]
    out = preview.mask_actuals(rows, date(2026, 9, 10))
    assert out[0]["epsActual"] is None and out[0]["revenueActual"] is None
    assert out[1]["epsActual"] == 5.0
    assert rows[0]["epsActual"] == 1.92, "the input is not mutated"
    assert out[2] == "garbage"


@pytest.mark.parametrize("text,expected", [
    ("2026-09-10T21:00:00Z", datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc)),
    ("2026-09-10T21:00:00", datetime(2026, 9, 10, 21, 0, tzinfo=timezone.utc)),
])
def test_parse_as_of(text, expected):
    assert preview.parse_as_of(text) == expected


def test_parse_as_of_defaults_to_now():
    assert preview.parse_as_of(None).tzinfo is not None


def test_safe_scrubs_keys():
    assert "SECRETKEY123" not in preview.safe("GET /x?apikey=SECRETKEY123", ["SECRETKEY123"])
    assert "***" in preview.safe("key SECRETKEY123 leaked", ["SECRETKEY123"])


def test_there_is_no_write_flag():
    args = preview.parse_args(["--scope", "ORCL"])
    assert not any("write" in k or "apply" in k for k in vars(args))
