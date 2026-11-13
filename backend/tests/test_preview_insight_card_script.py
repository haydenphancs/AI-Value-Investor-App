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


# ── jump mode (2026-10-28) ────────────────────────────────────────────────────

def test_the_preview_never_reaches_the_jump_search():
    """The preview compares model samples — an evaluation. Brave §3(b)(xiii): never built on
    web results, so the switch is OFF for the process (no search, web cards withheld)."""
    from app.config import settings

    assert settings.UPDATES_JUMP_WEB_SEARCH_ENABLED is False
    assert preview.parse_args(["--scope", "ORCL", "--jump"]).jump is True
    assert preview.parse_args(["--scope", "ORCL"]).jump is False


def test_a_stored_web_sourced_card_is_never_printed(monkeypatch):
    rows = [{"headline": "Oracle sets a $225B target", "bullets": ["a", "b"],
             "prompt_version": 8, "generated_at": "2026-10-28T18:50:00Z",
             "sources": [{"title": "t", "url": "https://www.cnbc.com/x", "origin": "web"}]}]

    class _Q:
        def __getattr__(self, name):
            return lambda *a, **k: self

        def execute(self):
            return type("R", (), {"data": rows})()

    monkeypatch.setattr("app.database.get_supabase",
                        lambda: type("C", (), {"table": lambda self, t: _Q()})())
    assert preview._read_stored("ORCL") == {"withheld": True}
    rows[0]["sources"] = [{"title": "t", "url": "https://fmp.example/1"}]
    assert preview._read_stored("ORCL")["headline"] == "Oracle sets a $225B target"


def test_a_hand_written_web_fixture_is_loaded_and_checked(tmp_path):
    """Review 2026-10-29: the web-fed jump prompt had never met the model. `--web-fixture`
    replays it with HAND-WRITTEN pages — never saved search results (Brave §3(b)(xiii))."""
    import json

    good = tmp_path / "pages.json"
    good.write_text(json.dumps([{"title": "Oracle soars on targets", "snippet": "s",
                                 "url": "https://news.example/a", "publisher": "Example",
                                 "published_at": "2026-10-28T18:30:00Z"}]))
    pages = preview.load_web_fixture(str(good))
    assert pages[0]["published_at"] == datetime(2026, 10, 28, 18, 30, tzinfo=timezone.utc)
    assert preview.parse_args(["--scope", "ORCL", "--web-fixture", str(good)]).web_fixture
    for bad in ({"title": "x"}, [{"title": "", "url": "https://a.example"}],
                [{"title": "x", "url": "http://a.example"}], ["x"]):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            preview.load_web_fixture(str(path))


# ── web only (2026-11-12) ─────────────────────────────────────────────────────

class _NoNewsFMP:
    """FMP with no story in the window and an empty earnings calendar."""

    def __init__(self):
        self.calls = []

    async def get_stock_news(self, *args, **kwargs):
        self.calls.append("news")
        return []

    async def get_earnings_calendar(self, **kwargs):
        self.calls.append("calendar")
        return []


class _CardGemini:
    def __init__(self, card):
        self.card = card
        self.calls = 0

    async def generate_json(self, **kwargs):
        import json

        self.calls += 1
        return {"text": json.dumps(self.card)}


_DUBAI_PAGE = {
    "title": "Joby delays its Dubai air taxi launch to 2027",
    "snippet": "Joby said certification work pushed its commercial launch in Dubai to 2027.",
    "url": "https://www.reuters.com/business/aerospace/joby-dubai-delay/",
    "publisher": "Reuters", "published_at": "2026-11-12T16:52:00Z",
}
_PRICE_PAGE = {
    "title": "Why Joby Aviation Stock Is Sinking Today",
    "snippet": "Joby Aviation (JOBY) shares are trading lower Wednesday.",
    "url": "https://www.cnbc.com/2026/11/12/why-joby-is-sinking.html",
    "publisher": "CNBC", "published_at": "2026-11-12T16:52:00Z",
}
_DUBAI_CARD = {
    "headline": "Joby delays its Dubai launch to 2027",
    "points": ["Joby said certification work pushed its Dubai launch to 2027."],
    "conclusion": "The Dubai launch now turns on certification work.",
    "sentiment": "bearish",
}


async def _preview_web_only(tmp_path, monkeypatch, pages):
    import json

    import app.database as database

    # Read-only: any Supabase use during the replay raises.
    monkeypatch.setattr(database, "_supabase_client", None)
    preview.install_supabase_tripwire()
    argv = ["--scope", "JOBY", "--as-of", "2026-11-12T18:45:00Z", "--jump",
            "--name", "Joby Aviation, Inc."]
    if pages is not None:
        path = tmp_path / "pages.json"
        path.write_text(json.dumps(pages))
        argv += ["--web-fixture", str(path)]
    gemini = _CardGemini(_DUBAI_CARD)
    code = await preview.run_preview(preview.parse_args(argv), fmp=_NoNewsFMP(), gemini=gemini,
                                     stored=None, secrets=())
    return code, gemini


@pytest.mark.asyncio
async def test_a_web_only_day_is_replayed_from_the_fixture(tmp_path, monkeypatch, capsys):
    """Review 2026-11-12: the replay stopped at "no corpus" before any generation, so a
    web-only day could not be previewed at all."""
    code, gemini = await _preview_web_only(tmp_path, monkeypatch, [_DUBAI_PAGE])
    out = capsys.readouterr().out
    assert code == 0 and gemini.calls == 1
    assert "web only: the card is written from 1 page(s) alone" in out
    assert "no corpus" not in out and "REJECTED" not in out
    assert "Joby delays its Dubai launch to 2027" in out


@pytest.mark.asyncio
async def test_a_web_only_day_with_only_price_talk_makes_no_model_call(
    tmp_path, monkeypatch, capsys,
):
    """The same no-model-call gate production runs (`_web_only_pages`)."""
    code, gemini = await _preview_web_only(tmp_path, monkeypatch, [_PRICE_PAGE])
    out = capsys.readouterr().out
    assert code == 0 and gemini.calls == 0
    assert ("REJECTED — no model call, nothing would be written: "
            "web_only_no_content: price talk only (pages=1)") in out


@pytest.mark.asyncio
async def test_no_story_and_no_fixture_is_still_no_corpus(tmp_path, monkeypatch, capsys):
    code, gemini = await _preview_web_only(tmp_path, monkeypatch, None)
    out = capsys.readouterr().out
    assert code == 0 and gemini.calls == 0
    assert "no corpus — the sweeper would skip this scope (no_corpus)" in out
