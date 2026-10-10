"""The Company Weekly preview (contract D16): `scripts/marketing_news_preview.py` composes a record
exactly as a template day stores it, and `marketing/preview.py` draws it with the worker's own
code, its logos read from a local folder instead of the bucket. Hermetic: no FMP, no Supabase,
no Kokoro (the render itself is exercised by hand — it needs venv_marketing and ffmpeg)."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.services.marketing import company_news_rules as R
from app.services.marketing import news_templates as T
from app.services.marketing import selection

_BACKEND = Path(__file__).resolve().parents[1]
MON, TUE, THU = date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 19)
RUN_DATES = {"ceo_buys": MON, "insider_buys": MON, "thirteen_f": TUE, "money_map": THU,
             # drop 2b: the December Congress Tuesday, the first stakes Tuesday, an earnings Thursday
             "congress_count": date(2026, 12, 8), "company_stakes": date(2026, 12, 29), "earnings": THU,
             "theme_explainer": MON}
#: The 2b samples' image layouts (pair / grid shipped with their series).
LAYOUTS_2B = {"congress_count": "spotlight", "company_stakes": "pair", "earnings": "rows", "theme_explainer": "grid"}


def _load_script():
    spec = importlib.util.spec_from_file_location("marketing_news_preview",
                                                  _BACKEND / "scripts" / "marketing_news_preview.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["marketing_news_preview"] = mod
    spec.loader.exec_module(mod)
    return mod


NP = _load_script()


def _png(size=(240, 120)) -> bytes:
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", size, (255, 255, 255, 0))
    ImageDraw.Draw(im).rectangle([20, 20, size[0] - 20, size[1] - 20], fill=(10, 60, 160, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_the_preview_bucket_host_mirrors_the_workers():
    from marketing import preview

    assert NP.PREVIEW_BUCKET_HOST == preview.PREVIEW_BUCKET_HOST


@pytest.mark.parametrize("series", NP.SHIPPED_SERIES)
def test_every_shipped_series_has_samples_that_compose_as_stored(series, tmp_path):
    recs = NP.sample_records(series, RUN_DATES[series])
    assert recs
    for rec in recs:
        stored, script = NP.compose_stored(rec, RUN_DATES[series], logos_in=None, logos_out=tmp_path / "logos")
        assert stored["authorship"] == T.AUTHORSHIP
        assert set(stored["post_formats"].values()) <= {"video", "image", "text"}
        assert "image" in stored["post_formats"].values()      # the preview shows the image day
        assert script["video_layout"] == T.VIDEO_LAYOUT and script["opening_card"]
        # No logo file given: every entry is a wordmark, never a URL.
        assert all(e["url"] is None for e in stored["logos"])


#: The local lists of REAL listings (backend/data): the peer universes, the short-interest and
#: persona-validation universes, the trending chips, the Trillion Club seed and the whale registry. Every
#: other data/*.json is scanned too — a stricter net, never a looser one.
_REAL_LISTING_FILES = ("industry_universe.json", "benchmark_universe.json", "short_interest_universe.json",
                       "persona_validation_universe.json", "search_trending_popular.json",
                       "trillion_club_seed.json", "whale_registry.json")
_WORD = re.compile(r"[A-Za-z0-9^][A-Za-z0-9.\-^]*")


def _listing_tokens(node) -> set:
    """Every symbol-shaped token in a JSON document, upper-cased: each dict key and each word of each
    string (so a symbol field, a `market_caps` key and a ticker written in prose all count)."""
    out, stack = set(), [node]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            stack.extend(x.keys())
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
        elif isinstance(x, str):
            out |= {w.strip(".-").upper() for w in _WORD.findall(x)}
    return out - {""}


def _sample_companies():
    """(symbols, names) the built-in samples carry, read off each record's STORED form (every
    {"symbol", "name"} company, the 13F filer, a stake's investee name) — not off `record_symbols`, so a
    company that helper misses is still checked."""
    symbols, names = set(), set()
    for series in NP.SHIPPED_SERIES:
        for rec in NP.sample_records(series, RUN_DATES[series]):
            symbols |= set(NP.record_symbols(rec))
            stack = [R.record_to_dict(rec)]
            while stack:
                x = stack.pop()
                if isinstance(x, list):
                    stack.extend(x)
                elif isinstance(x, dict):
                    stack.extend(x.values())
                    if isinstance(x.get("symbol"), str):
                        symbols.add(x["symbol"])
                        names.add(x.get("name"))
                    for k in ("filer_symbol", "investee_name", "filer_name"):
                        if isinstance(x.get(k), str):
                            (symbols if k == "filer_symbol" else names).add(x[k])
    return symbols, names


def test_the_samples_are_fictional_companies():
    """Built-in samples must never put invented figures beside a real company. The rendered image and
    video carry no SAMPLE DATA label (only post.md does), so a real ticker as a chip under "4 members of
    Congress disclosed purchases" is a fabricated record about a real listing (CTSO, the old Contoso
    symbol, is a real NASDAQ ticker in industry_universe.json)."""
    data = _BACKEND / "data"
    per_file = {p.name: _listing_tokens(json.loads(p.read_text(encoding="utf-8")))
                for p in sorted(data.glob("*.json"))}
    # Anti-vacuity: every named list is there and read, and real tickers ARE found — in a symbol list, a
    # symbol field and a whale's associated ticker.
    for name in _REAL_LISTING_FILES:
        assert per_file.get(name), f"{name}: missing or no symbols read"
    real = set().union(*per_file.values())
    assert "AAPL" in real and "AAPL" in per_file["search_trending_popular.json"]
    assert "NVDA" in per_file["trillion_club_seed.json"] and "BRK-A" in per_file["whale_registry.json"]

    symbols, names = _sample_companies()
    assert symbols == {sym for sym, _ in NP.SAMPLE_COMPANIES.values()}      # every chip from the vetted table
    for sym in symbols:
        assert re.fullmatch(r"[ZQ][A-Z]{3,4}", sym), sym
        hits = sorted(f for f, toks in per_file.items() if sym.upper() in toks)
        assert not hits, f"sample symbol {sym} is listed in {hits}"

    # The names are fictional too: none is a company in the known-companies lexicon, the Trillion Club
    # seed, the whale registry or the trending chips.
    known = {ln.strip().lstrip("~").casefold() for ln in (data / "known_companies_en.txt").read_text(
        encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")}
    seed = json.loads((data / "trillion_club_seed.json").read_text(encoding="utf-8"))
    known |= {str(c.get("display_name")).casefold() for c in seed["companies"]}
    known |= {str(s.get("investee_name")).casefold() for s in seed["stakes"]}
    for w in json.loads((data / "whale_registry.json").read_text(encoding="utf-8")):
        known |= {str(w.get(k)).casefold() for k in ("name", "firm_name", "fmp_name") if w.get(k)}
    trending = json.loads((data / "search_trending_popular.json").read_text(encoding="utf-8"))
    known |= {str(r.get("name")).casefold() for r in trending["all"] + trending["stocks"]}
    assert {"apple", "nvidia", "berkshire hathaway"} <= known                 # anti-vacuity
    assert names and None not in names
    assert not {n for n in names if n.casefold() in known}, names

    # every company a 2b record can draw is counted (the investor, a listed investee, every theme member)
    sym = {k: s for k, (s, _) in NP.SAMPLE_COMPANIES.items()}
    (stake, listed) = NP.sample_records("company_stakes", RUN_DATES["company_stakes"])
    assert stake.investee is None and stake.investee_name == "Relecloud"
    assert NP.record_symbols(stake) == [sym["fabrikam"]]
    assert NP.record_symbols(listed) == [sym["contoso"], sym["proseware"]]
    (theme,) = NP.sample_records("theme_explainer", RUN_DATES["theme_explainer"])
    assert NP.record_symbols(theme) == [m.company.symbol for m in theme.members] and len(theme.members) == 8
    assert {NP.record_symbols(r)[0] for r in NP.sample_records("congress_count", RUN_DATES["congress_count"])} == {
        sym["contoso"], sym["fabrikam"]}


def test_the_preview_offers_exactly_the_shipped_series_in_calendar_order():
    assert set(NP.SHIPPED_SERIES) == set(selection.SHIPPED_SERIES)
    assert list(NP.SHIPPED_SERIES) == [s.id for s in selection.SERIES if s.id in selection.SHIPPED_SERIES]
    assert set(RUN_DATES) == set(NP.SHIPPED_SERIES)
    for series, day in RUN_DATES.items():
        assert series in selection.plan_for(day).chain, (series, day)      # a day that runs it


@pytest.mark.parametrize("series", sorted(LAYOUTS_2B))
def test_a_2b_sample_composes_with_its_shipped_layout(series, tmp_path):
    for rec in NP.sample_records(series, RUN_DATES[series]):
        stored, script = NP.compose_stored(rec, RUN_DATES[series], logos_in=None, logos_out=tmp_path / "logos")
        assert stored["series"] == series and stored["image_spec"]["layout"] == LAYOUTS_2B[series]
        assert stored["post_formats"]["bluesky"] == "image"            # the image is drawable (not refused)
        assert script["image_spec"] == stored["image_spec"]


@pytest.mark.parametrize("series", NP.SHIPPED_SERIES)
def test_sample_day_is_the_first_day_whose_calendar_runs_the_series_and_its_samples_compose(series, tmp_path):
    """Without --date each series is previewed on its own next calendar day — and its samples compose
    there, whatever the starting day of the year (a Congress Count needs its Tuesday, day 8..14)."""
    for start in (date(2026, 10, 10), date(2026, 12, 1), date(2027, 2, 27), date(2027, 5, 3), date(2027, 8, 30)):
        day = NP.sample_day(series, start)
        assert series in selection.plan_for(day).chain and day >= start
        assert all(series not in selection.plan_for(start + timedelta(days=i)).chain
                   for i in range((day - start).days))
        for rec in NP.sample_records(series, day):
            NP.compose_stored(rec, day, logos_in=None, logos_out=tmp_path / "logos")


def test_a_congress_sample_on_a_day_its_template_refuses_is_reported_refused(tmp_path):
    """--date pins one date for every series: a Congress Count before the 7th of the month cannot be
    composed (its month has not settled) — the preview writes REFUSED and exits 1, never a post."""
    code = NP.main(["--fixture", "--series", "congress_count", "--date", "2026-12-03", "--out", str(tmp_path)])
    assert code == 1
    assert (tmp_path / "congress_count-1" / "REFUSED.txt").exists()
    assert not (tmp_path / "congress_count-1" / "script.json").exists()


@pytest.mark.parametrize("today", [date(2026, 10, 10), date(2026, 12, 2), date(2027, 3, 1)])
def test_the_built_in_samples_run_end_to_end_from_today(tmp_path, capsys, monkeypatch, today):
    """`--fixture` with no file and no --date: every shipped series, each composed for ITS OWN sample day
    from today (one date for all would refuse some — a 13F Season sample outside its season, a Congress
    Count before the 7th), writes its reviewer page and worker script (nothing rendered, nothing live)."""
    from datetime import datetime as real_datetime

    class _Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(today.year, today.month, today.day, 12, 0, tzinfo=tz)

    monkeypatch.setattr(NP, "datetime", _Clock)
    assert NP.main(["--fixture", "--out", str(tmp_path)]) == 0
    for series in NP.SHIPPED_SERIES:
        day = NP.sample_day(series, today)
        n = len(NP.sample_records(series, day))
        for i in range(1, n + 1):
            folder = tmp_path / f"{series}-{i}"
            assert (folder / "post.md").exists() and (folder / "script.json").exists(), folder
            assert "SAMPLE DATA" in (folder / "post.md").read_text(encoding="utf-8")
            stored = json.loads((folder / "stored_output.json").read_text(encoding="utf-8"))
            assert stored["run_date"] == day.isoformat(), (series, i)
    out = capsys.readouterr().out
    assert all(f"- {s}: sample day" in out for s in NP.SHIPPED_SERIES)


def test_candidates_mark_the_series_the_switch_keeps_off(monkeypatch):
    from app.config import Settings, settings

    monkeypatch.setattr(settings, "MARKETING_NEWS_SERIES", Settings.model_fields["MARKETING_NEWS_SERIES"].default)
    marked = NP.series_marked(["congress_count", "thirteen_f", "company_stakes"])
    assert marked == ["congress_count (off in MARKETING_NEWS_SERIES)", "thirteen_f",
                      "company_stakes (off in MARKETING_NEWS_SERIES)"]
    monkeypatch.setattr(settings, "MARKETING_NEWS_SERIES", "congress_count,company_stakes")
    assert NP.series_marked(["congress_count", "thirteen_f"]) == [
        "congress_count", "thirteen_f (off in MARKETING_NEWS_SERIES)"]


def test_a_local_logo_becomes_a_bucket_shaped_entry_the_worker_resolves(tmp_path):
    from marketing import cards, logos, preview

    logos_in = tmp_path / "in"
    logos_in.mkdir()
    rec = NP.sample_records("money_map", THU)[0]
    key = rec.company.symbol
    (logos_in / f"{key}.png").write_bytes(_png())
    stored, script = NP.compose_stored(rec, THU, logos_in=logos_in, logos_out=tmp_path / "out")
    entry = next(e for e in stored["logos"] if e["key"] == key)
    assert entry["url"].startswith(NP.PREVIEW_PUBLIC_PREFIX)
    m = logos.logo_url_pattern(preview.PREVIEW_BUCKET_HOST).match(entry["url"])
    assert m and m.group(1) == entry["sha256"][:32]
    assert (tmp_path / "out" / f"{entry['sha256'][:32]}.png").read_bytes() == (logos_in / f"{key}.png").read_bytes()
    # The worker's own resolve reads it back from the folder and draws the LOGO, not the wordmark.
    table = preview.template_logos(script, tmp_path / "out", tmp_path)
    assert isinstance(table[key], cards.LogoArt) and not table[key].wordmark
    # Without the folder the same script draws the wordmark.
    assert preview.template_logos(script, None, tmp_path)[key].wordmark


def test_a_refused_logo_file_is_a_wordmark(tmp_path):
    logos_in = tmp_path / "in"
    logos_in.mkdir()
    rec = NP.sample_records("money_map", THU)[0]
    (logos_in / f"{rec.company.symbol}.png").write_bytes(b"not a png")
    stored, _ = NP.compose_stored(rec, THU, logos_in=logos_in, logos_out=tmp_path / "out")
    assert all(e["url"] is None for e in stored["logos"])


def test_the_local_download_reads_only_a_file_name_in_its_folder(tmp_path):
    from marketing import preview

    (tmp_path / "abc.png").write_bytes(b"x" * 10)
    dl = preview.local_download(tmp_path)
    assert dl("https://preview.local/storage/v1/object/public/marketing-media/logos/abc.png",
              max_bytes=100, timeout=1) == b"x" * 10
    with pytest.raises(ValueError):
        dl("https://preview.local/x/abc.png", max_bytes=5, timeout=1)       # over the cap
    with pytest.raises((ValueError, OSError)):
        dl("https://preview.local/logos/.hidden", max_bytes=100, timeout=1)
    with pytest.raises(OSError):
        dl("https://preview.local/logos/missing.png", max_bytes=100, timeout=1)


def test_records_round_trip_through_a_record_file(tmp_path):
    by_series = {s: NP.sample_records(s, RUN_DATES[s]) for s in NP.SHIPPED_SERIES}
    path = tmp_path / "day.json"
    NP.save_records(path, MON, by_series)
    run_date, back = NP.load_records(path)
    assert run_date == MON
    assert {s: [R.record_to_dict(r) for r in v] for s, v in back.items()} == \
           {s: [R.record_to_dict(r) for r in v] for s, v in by_series.items()}


def test_the_reviewer_page_labels_samples_and_shows_every_caption(tmp_path):
    rec = NP.sample_records("ceo_buys", MON)[0]
    stored, _ = NP.compose_stored(rec, MON, logos_in=None, logos_out=tmp_path)
    page = NP.post_markdown(stored, series="ceo_buys", ref=R.ledger_key(rec), run_date=MON, sample=True)
    assert "SAMPLE DATA" in page
    for platform in stored["posts"]:
        assert f"### {platform} (" in page
    assert NP.post_markdown(stored, series="ceo_buys", ref="r", run_date=MON, sample=False).count("SAMPLE DATA") == 0


def test_a_refused_record_writes_refused_not_a_script(tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise T.NewsTemplateRefused("script_shape", "probe")

    monkeypatch.setattr(T, "compose", refuse)
    rec = NP.sample_records("money_map", THU)[0]
    assert NP.write_one(rec, THU, tmp_path / "d", logos_in=None, sample=True, do_render=False) is False
    assert (tmp_path / "d" / "REFUSED.txt").exists() and not (tmp_path / "d" / "script.json").exists()


def test_the_worker_preview_knows_a_template_script(tmp_path):
    from marketing import preview

    rec = NP.sample_records("money_map", THU)[0]
    _stored, script = NP.compose_stored(rec, THU, logos_in=None, logos_out=tmp_path)
    assert preview.is_template(script)
    assert not preview.is_template(preview.DEMO)
    assert json.loads(json.dumps(script)) == script
