"""The Company Weekly preview (contract D16): `scripts/marketing_news_preview.py` composes a record
exactly as a template day stores it, and `marketing/preview.py` draws it with the worker's own
code, its logos read from a local folder instead of the bucket. Hermetic: no FMP, no Supabase,
no Kokoro (the render itself is exercised by hand — it needs venv_marketing and ffmpeg).

The read-only guard (plan Batch 1 item 13) is driven through httpx MockTransports and the REAL
postgrest builder: a refused request never reaches the transport, so nothing here opens a socket."""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import logging
import re
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
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

_SB_HOST = "test-project.supabase.co"
_SB = f"https://{_SB_HOST}"


@pytest.fixture(autouse=True)
def _a_test_supabase_url(monkeypatch):
    """Every `main` in this file guards a TEST host, never the developer's SUPABASE_URL from
    backend/.env — and does not depend on what that variable holds (the guard refuses to run without
    a host, so a placeholder there would turn every fixture run into exit 2)."""
    from app.config import settings

    monkeypatch.setattr(settings, "SUPABASE_URL", _SB)


@pytest.fixture(autouse=True)
def _declared_post_weekdays(monkeypatch):
    """Batch 3: `next_posting_day` and `sample_day` follow the posting days production runs — `selection`, which reads
    MARKETING_POST_WEEKDAYS at call time. Pin it to its declared default (Mon/Tue/Thu/Sat), so a developer's .env can
    never move a sample day; a test that wants another cadence sets its own."""
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat")


@pytest.fixture(autouse=True)
def _declared_freeze_switches(monkeypatch):
    """B3-LIVE-3: `compose_stored` freezes through production's own `_frozen_output`, which reads its write-time
    switches from the settings object NOW — pin each to its DECLARED default (pictures on Bluesky / Facebook /
    LinkedIn / Threads, X text, no video feature, no extra video), so a developer's .env can never move a test; a
    test that wants production's planned switches sets its own."""
    from app.config import Settings, settings

    for name in NP.FREEZE_SWITCHES:
        monkeypatch.setattr(settings, name, Settings.model_fields[name].default)


@pytest.fixture(autouse=True)
def _httpx_send_restored(monkeypatch):
    """A guard its body left INSTALLED — on purpose after a Ctrl-C or any other BaseException that is not
    an Exception, when the process is ending (`supabase_read_only_guard`) — must not outlive the test that
    left it: httpx's two `send`s are put back at teardown, AFTER the test's own asserts (which still check
    the guard's own restore). Setting each to itself only records the original for monkeypatch's undo."""
    monkeypatch.setattr(httpx.Client, "send", httpx.Client.send)
    monkeypatch.setattr(httpx.AsyncClient, "send", httpx.AsyncClient.send)


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


def test_a_record_file_lands_in_a_folder_the_preview_creates_and_a_file_in_the_way_fails_loudly(
        tmp_path, monkeypatch):
    """PREVIEW-GUARD-3: the documented --record path sits under marketing/out/news-preview/ (gitignored),
    which a fresh checkout does not have until a preview ran — the dump is written only AFTER the live
    fetch (up to CANDIDATES_BUDGET_SECONDS per series), so a missing folder must not throw that work
    away. A FILE where the folder should be, or a folder named as the file, is a real error: it raises,
    never a silent skip."""
    by_series = {"money_map": NP.sample_records("money_map", THU)}
    path = tmp_path / "marketing" / "out" / "news-preview" / "day.json"
    NP.save_records(path, THU, by_series)
    assert NP.load_records(path)[0] == THU
    NP.save_records(path, MON, by_series)                                   # a second dump replaces it
    assert NP.load_records(path)[0] == MON
    monkeypatch.chdir(tmp_path)                                             # a bare name: its folder is "."
    NP.save_records(Path("bare.json"), THU, by_series)
    assert NP.load_records(tmp_path / "bare.json")[0] == THU
    (tmp_path / "blocked").write_text("a file, not a folder", encoding="utf-8")
    with pytest.raises(OSError):
        NP.save_records(tmp_path / "blocked" / "day.json", THU, by_series)
    assert (tmp_path / "blocked").read_text(encoding="utf-8") == "a file, not a folder"
    with pytest.raises(OSError):
        NP.save_records(tmp_path / "marketing", THU, by_series)              # a folder is not a file


def test_the_documented_record_and_fixture_files_live_under_the_gitignored_output_folder():
    """PREVIEW-GUARD-3: the usage lines run from backend/, so `--record day.json` left an untracked
    backend/day.json in the shared checkout (every session's `git status`). Every FILE the module
    docstring passes to --record or --fixture is under marketing/out/, which .gitignore covers."""
    usage = (NP.__doc__ or "").split("Usage (from backend/):", 1)[1].split("\n\n", 1)[0]
    # a FILE argument: not the next flag, not a `# comment`, not the line continuation
    files = re.findall(r"--(?:record|fixture)[ \t]+([^\s\\#-][^\s\\]*)", usage)
    assert len(files) >= 2, files                                           # anti-vacuity: both examples
    assert all(f.startswith("marketing/out/") and f.endswith(".json") for f in files), files
    ignored = (_BACKEND.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "backend/marketing/out/" in {ln.strip() for ln in ignored}


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


# ── the read-only guard (plan Batch 1 item 13): --candidates writes nothing ─────
#
# The services the adapter reuses refresh PRODUCTION cache rows as a side effect of a read
# (company_profile_cache, revenue_breakdown_cache, profit_power_cache — and, for the 13F series' split
# check, corporate_action_cache). The guard patches httpx at the HTTP layer so every one of them is
# refused — whatever binding the service reached its client by; it refuses by HOST, never by table, so a
# writer missing from this list is refused too.
# (`_SB_HOST` / `_SB` sit at the top, beside the autouse fixture that points SUPABASE_URL at them.)

#: Key-shaped, in every request's headers: it must never reach a log line or an exception message.
_PROBE_KEY = "sb_" + "secret_preview_guard_probe_0123456789"   # split: no key-shaped literal for scanners
_HEADERS = {"apikey": _PROBE_KEY, "Authorization": f"Bearer {_PROBE_KEY}"}
_CANDIDATES_ARGS = ["--candidates", "--series", "money_map", "--date", "2026-11-19"]


class _Wire:
    """A MockTransport handler that records what REACHED it — a refused request never does."""

    def __init__(self, rows=None):
        self.seen = []
        self.rows = rows if rows is not None else []

    def __call__(self, request):
        self.seen.append((request.method, request.url.host, request.url.path))
        return httpx.Response(200, json=self.rows)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self), headers=_HEADERS)

    def async_client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self), headers=_HEADERS)


def _httpx_sends():
    return httpx.Client.send, httpx.AsyncClient.send


def _guard_warnings(caplog):
    return [r for r in caplog.records if r.name == "marketing_news_preview"]


def _postgrest(wire):
    """The REAL postgrest builder supabase-py's `.table()` returns, over the mock wire."""
    from postgrest import SyncPostgrestClient
    from postgrest.constants import DEFAULT_POSTGREST_CLIENT_HEADERS

    return SyncPostgrestClient(f"{_SB}/rest/v1", headers={**DEFAULT_POSTGREST_CLIENT_HEADERS, **_HEADERS},
                               http_client=httpx.Client(transport=httpx.MockTransport(wire)))


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE", "OPTIONS"])
@pytest.mark.parametrize("host", [_SB_HOST, "TEST-Project.SUPABASE.co.",        # any case, the root dot
                                  "other-ref.supabase.co", "ref.storage.supabase.co", "legacy.supabase.in"])
def test_the_guard_refuses_every_write_to_supabase(method, host, caplog):
    caplog.set_level(logging.WARNING)
    wire, before = _Wire(), _httpx_sends()
    with NP.supabase_read_only_guard(_SB_HOST) as guard, wire.client() as c:
        with pytest.raises(NP.SupabaseWriteRefused) as exc:
            c.request(method, f"https://{host}/rest/v1/company_profile_cache?on_conflict=ticker",
                      json={"ticker": "ZQCT"})
    assert wire.seen == []                                     # it never reached the wire
    assert guard.refused == {f"{method} /rest/v1/company_profile_cache": 1}
    (warning,) = _guard_warnings(caplog)                       # exactly one WARNING per refusal
    assert warning.levelno == logging.WARNING
    assert f"refused {method} /rest/v1/company_profile_cache" in warning.getMessage()
    for text in (caplog.text, str(exc.value)):                 # the path only: no key, no query string
        assert _PROBE_KEY not in text and "Bearer" not in text and "on_conflict" not in text
    assert _httpx_sends() == before                            # restored on exit


@pytest.mark.parametrize("method, url", [
    ("GET", f"{_SB}/rest/v1/company_profile_cache?ticker=eq.ZQCT"),
    ("HEAD", f"{_SB}/rest/v1/company_profile_cache"),
    ("get", f"{_SB}/rest/v1/profit_power_cache"),                                # httpx upper-cases it
    ("POST", "https://financialmodelingprep.com/stable/profile"),
    ("PATCH", "https://images.financialmodelingprep.com/symbol/ZQCT.png"),
    ("PUT", "https://evil-supabase.co/rest/v1/x"),                               # not a supabase.co host
    ("DELETE", "https://test-project.supabase.co.example.com/rest/v1/x"),        # a look-alike, another host
])
def test_the_guard_lets_reads_and_other_hosts_through(method, url, caplog):
    caplog.set_level(logging.WARNING)
    wire = _Wire()
    with NP.supabase_read_only_guard(_SB_HOST) as guard, wire.client() as c:
        assert c.request(method, url).status_code == 200
    assert [m for m, _h, _p in wire.seen] == [method.upper()]
    assert not guard.refused and not _guard_warnings(caplog)


@pytest.mark.asyncio
async def test_the_guard_covers_the_async_client_too():
    wire, before = _Wire(), _httpx_sends()
    with NP.supabase_read_only_guard(_SB_HOST) as guard:
        async with wire.async_client() as c:
            with pytest.raises(NP.SupabaseWriteRefused):
                await c.post(f"{_SB}/rest/v1/profit_power_cache", json={"ticker": "ZQCT"})
            with pytest.raises(NP.SupabaseWriteRefused):
                await c.delete(f"{_SB}/rest/v1/profit_power_cache")
            assert (await c.get(f"{_SB}/rest/v1/profit_power_cache")).status_code == 200
            assert (await c.post("https://financialmodelingprep.com/stable/x")).status_code == 200
    assert [m for m, _h, _p in wire.seen] == ["GET", "POST"]
    assert guard.refused == {"POST /rest/v1/profit_power_cache": 1, "DELETE /rest/v1/profit_power_cache": 1}
    assert _httpx_sends() == before


def test_the_guard_refuses_the_real_postgrest_writes_and_serves_its_reads():
    """The SDK's own builders (what `get_supabase().table(...)` returns): the binding is the HTTP layer,
    so every write shape is refused however the caller reached its client."""
    wire = _Wire(rows=[{"ticker": "ZQCT"}])
    pg = _postgrest(wire)
    writes = {"upsert": lambda t: t.upsert({"ticker": "ZQCT"}, on_conflict="ticker"),
              "insert": lambda t: t.insert({"ticker": "ZQCT"}),
              "update": lambda t: t.update({"cached_at": "2026-11-19"}).eq("ticker", "ZQCT"),
              "delete": lambda t: t.delete().eq("ticker", "ZQCT")}
    with NP.supabase_read_only_guard(_SB_HOST) as guard:
        res = pg.from_("company_profile_cache").select("*").eq("ticker", "ZQCT").execute()
        assert res.data == [{"ticker": "ZQCT"}]
        for name, build in writes.items():
            with pytest.raises(NP.SupabaseWriteRefused):
                build(pg.from_("company_profile_cache")).execute()
    assert wire.seen == [("GET", _SB_HOST, "/rest/v1/company_profile_cache")]
    assert guard.refused == {"POST /rest/v1/company_profile_cache": 2, "PATCH /rest/v1/company_profile_cache": 1,
                             "DELETE /rest/v1/company_profile_cache": 1}


def _refresh_company_profile(sb, monkeypatch):
    """company_facts' read-merge-write: `_db()` imports `get_supabase` INSIDE the function, so the
    source module is the binding (testing.md)."""
    import app.database
    from app.services import company_facts_service as cf

    monkeypatch.setattr(app.database, "get_supabase", lambda: sb)
    cf._merge_write("ZQCT", {"companyName": "Contoso"}, restamp=True)


def _refresh_profit_power(sb, monkeypatch):
    from app.services import profit_power_service as pp

    svc = pp.ProfitPowerService.__new__(pp.ProfitPowerService)    # the client it built in __init__
    svc.supabase = sb
    svc._upsert_supabase_cache_safe("ZQCT", SimpleNamespace(degraded=[], model_dump=lambda: {"ticker": "ZQCT"}),
                                    "2026-11-20")


def _refresh_revenue_breakdown(sb, monkeypatch):
    from app.services import revenue_breakdown_service as rb

    monkeypatch.setattr(rb, "_is_cacheable", lambda *a, **k: True)
    svc = rb.RevenueBreakdownService.__new__(rb.RevenueBreakdownService)
    svc.supabase = sb
    svc._upsert_supabase_cache_safe("ZQCT", SimpleNamespace(degraded=[], model_dump=lambda: {"ticker": "ZQCT"}),
                                    None)


def _refresh_corporate_actions(sb, monkeypatch):
    """The 13F series (PREVIEW-GUARD-2): the preview's `NewsDeps()` injects no `actions`, so the builder's
    split check takes the production `corporate_actions_source(None)`, which caches a CLOSED window's events
    through `_db_put` (`asyncio.to_thread`; `get_supabase` imported INSIDE it, so the source module is the
    binding). An empty list is a real answer it stores."""
    import app.database
    from app.services import corporate_actions_service as ca

    monkeypatch.setattr(app.database, "get_supabase", lambda: sb)
    asyncio.run(ca.CorporateActionsService()._db_put("ZQCT", "split", "2026-06-30", "2026-09-30", []))


@pytest.mark.parametrize("table, refresh", [("company_profile_cache", _refresh_company_profile),
                                            ("profit_power_cache", _refresh_profit_power),
                                            ("revenue_breakdown_cache", _refresh_revenue_breakdown),
                                            ("corporate_action_cache", _refresh_corporate_actions)])
def test_each_cache_refresh_the_adapter_reaches_is_refused_and_the_service_swallows_it(
        table, refresh, monkeypatch, caplog):
    """The four production writes --candidates used to make (Money Map's company facts, revenue breakdown
    and profit power — the revenue breakdown also once per Theme Explainer member, `_theme_facts`; the 13F
    series' corporate-actions cache), through each service's REAL write path: the guard refuses the write,
    the service's own `except Exception` logs it as a failed cache write, and nothing raises — the preview
    reads on, exactly as on any failed refresh."""
    caplog.set_level(logging.WARNING)
    wire = _Wire(rows=[{"profile_json": {"companyName": "Contoso"}, "cached_at": "2026-11-18T00:00:00+00:00"}])
    with NP.supabase_read_only_guard(_SB_HOST) as guard:
        refresh(SimpleNamespace(table=_postgrest(wire).from_), monkeypatch)    # never raises
    assert guard.refused == {f"POST /rest/v1/{table}": 1}
    assert all(m in NP.READ_ONLY_METHODS for m, _h, _p in wire.seen)          # only reads reached the wire
    assert [f"refused POST /rest/v1/{table}" in r.getMessage() for r in _guard_warnings(caplog)] == [True]
    swallowed = [r for r in caplog.records if r.name.startswith("app.services.") and r.levelno == logging.WARNING
                 and f"read-only: POST /rest/v1/{table}" in r.getMessage()]
    assert len(swallowed) == 1, [r.getMessage() for r in caplog.records]       # the service logged it, once
    assert _PROBE_KEY not in caplog.text


def test_a_refusal_is_a_plain_runtime_error_that_no_supabase_retry_retries():
    """Not an httpx error: `is_transient_supabase_error` would retry a transport type (one refusal
    logged three times), and the services' `except Exception` already treats it as a failed write."""
    from app.utils.supabase_errors import is_transient_supabase_error

    wire = _Wire()
    with NP.supabase_read_only_guard(_SB_HOST), wire.client() as c:
        with pytest.raises(NP.SupabaseWriteRefused) as exc:
            c.post(f"{_SB}/rest/v1/company_profile_cache")
    assert isinstance(exc.value, RuntimeError) and not isinstance(exc.value, httpx.HTTPError)
    assert not is_transient_supabase_error(exc.value)


def test_the_guard_reads_odd_requests_fail_closed(caplog):
    caplog.set_level(logging.WARNING)
    g = NP.ReadOnlyGuard(_SB_HOST)
    url = httpx.URL(f"{_SB}/rest/v1/x")
    g.check(SimpleNamespace(method=b"GET", url=url))                       # a bytes read
    g.check(SimpleNamespace(method=" head ", url=url))                     # a spaced, lower-case read
    for method in (b"POST", "post", None, "", 7):                           # unreadable is NOT a read
        with pytest.raises(NP.SupabaseWriteRefused):
            g.check(SimpleNamespace(method=method, url=url))
    g.check(SimpleNamespace(method="POST", url=None))                      # no host: httpx refuses it itself
    g.check(SimpleNamespace(method="POST", url=httpx.URL("/rest/v1/x")))   # a relative URL: no host
    # A forged log line is folded onto one line; a huge path is capped.
    with pytest.raises(NP.SupabaseWriteRefused):
        g.check(SimpleNamespace(method="POST", url=SimpleNamespace(host=_SB_HOST, path="/rest/v1/x\nFORGED line")))
    with pytest.raises(NP.SupabaseWriteRefused):
        g.check(SimpleNamespace(method="PUT", url=SimpleNamespace(host=_SB_HOST, path="/" + "a" * 5000)))
    assert g.refused == {"POST /rest/v1/x": 2, "? /rest/v1/x": 2, "7 /rest/v1/x": 1,
                         "POST /rest/v1/x FORGED line": 1, "PUT /" + "a" * (NP.GUARD_PATH_CHARS - 1): 1}
    assert all("\n" not in r.getMessage() for r in _guard_warnings(caplog))
    assert len(_guard_warnings(caplog)) == 7                               # one WARNING per refusal
    assert g.covers(b"TEST-PROJECT.supabase.co.") and not g.covers("") and not g.covers(None)
    assert "refused 7 Supabase write(s)" in g.summary()
    assert NP.ReadOnlyGuard(_SB_HOST).summary() == "Read-only guard: no Supabase write was attempted."


@pytest.mark.parametrize("url, host", [("https://Test-Project.Supabase.co/", _SB_HOST),
                                       ("https://test-project.supabase.co:443/rest/v1", _SB_HOST),
                                       ("https://test-project.supabase.co./", _SB_HOST),
                                       ("https://db.example.com", "db.example.com")])   # a custom domain, exactly
def test_supabase_host_reads_the_configured_host(url, host):
    assert NP.supabase_host(url) == host


@pytest.mark.parametrize("url", ["", "   ", "not a url", "test-project.supabase.co", "https://", "https://[::1"])
def test_a_supabase_url_that_names_no_host_is_refused(url):
    with pytest.raises(ValueError):
        NP.supabase_host(url)


@pytest.mark.parametrize("host", ["", "   ", ".", b""])
def test_no_host_means_no_guard_and_nothing_patched(host):
    before = _httpx_sends()
    with pytest.raises(ValueError):
        NP.ReadOnlyGuard(host)
    with pytest.raises(ValueError):
        with NP.supabase_read_only_guard(host):
            pytest.fail("the body must never run unguarded")
    assert _httpx_sends() == before
    with pytest.raises(ValueError):
        NP.ReadOnlyGuard(None)


def test_the_guard_restores_httpx_however_its_body_ends_and_when_nested():
    before = _httpx_sends()
    with pytest.raises(KeyError):
        with NP.supabase_read_only_guard(_SB_HOST):
            assert _httpx_sends() != before
            raise KeyError("boom")
    assert _httpx_sends() == before
    with NP.supabase_read_only_guard(_SB_HOST):
        outer = _httpx_sends()
        with NP.supabase_read_only_guard("db.example.com"):
            assert _httpx_sends() != outer
        assert _httpx_sends() == outer
    assert _httpx_sends() == before


def _stays_installed(caplog):
    return [r.getMessage() for r in _guard_warnings(caplog) if "stays installed" in r.getMessage()]


@pytest.mark.parametrize("ending", [KeyboardInterrupt(), SystemExit(130), asyncio.CancelledError(),
                                    BaseExceptionGroup("interrupted", [KeyboardInterrupt()])],
                         ids=["ctrl-c", "system-exit", "cancelled", "base-exception-group"])
def test_a_body_ending_in_a_base_exception_that_is_not_an_error_leaves_the_guard_installed(ending, caplog):
    """PREVIEW-GUARD-1, the rule: Ctrl-C, SystemExit — any BaseException that is not an Exception — means
    the process is ending, and a worker thread it does not wait for here may still write. So the guard
    stays in front of httpx (one WARNING saying so) and a write AFTER the `with` is still refused.
    MUTATION TARGET: restore in the guard's `finally` whatever ended the body. (The autouse
    `_httpx_send_restored` puts httpx back at teardown.)"""
    caplog.set_level(logging.WARNING)
    wire, before = _Wire(), _httpx_sends()
    with pytest.raises(type(ending)):
        with NP.supabase_read_only_guard(_SB_HOST) as guard:
            raise ending
    assert _httpx_sends() != before
    with wire.client() as c, pytest.raises(NP.SupabaseWriteRefused):
        c.post(f"{_SB}/rest/v1/company_profile_cache", json={"ticker": "ZQCT"})
    assert wire.seen == [] and guard.refused == {"POST /rest/v1/company_profile_cache": 1}
    (stays,) = _stays_installed(caplog)
    assert f"interrupted ({type(ending).__name__})" in stays


@pytest.mark.parametrize("ending", [KeyError("boom"), RuntimeError("x"), NP.SupabaseWriteRefused("x"),
                                    ExceptionGroup("errors", [ValueError("x")])],
                         ids=["key-error", "runtime-error", "a-refusal", "exception-group"])
def test_a_body_ending_in_an_ordinary_error_restores_httpx(ending, caplog):
    """The other half: an ordinary error does not end the process on its own, and `asyncio.run` drained
    its executor before raising one — so httpx is restored, with no 'stays installed' WARNING."""
    caplog.set_level(logging.WARNING)
    before = _httpx_sends()
    with pytest.raises(type(ending)):
        with NP.supabase_read_only_guard(_SB_HOST):
            raise ending
    assert _httpx_sends() == before
    assert _stays_installed(caplog) == []


def _fire_and_forget_in_executor(job):
    """profit_power / revenue_breakdown: `run_in_executor(None, …)`, never awaited — a plain future that
    `asyncio.run`'s task cancellation never reaches."""
    asyncio.get_running_loop().run_in_executor(None, job)


def _fire_and_forget_to_thread_task(job):
    """company facts' `_schedule_write`: `ensure_future(to_thread(…))` — a task `asyncio.run` cancels at
    close, though the thread it started runs on."""
    asyncio.ensure_future(asyncio.to_thread(job))


@pytest.mark.parametrize("schedule", [_fire_and_forget_in_executor, _fire_and_forget_to_thread_task],
                         ids=["run_in_executor", "ensure_future_to_thread"])
def test_a_ctrl_c_during_the_executor_drain_leaves_the_guard_on_for_the_pending_write(
        schedule, monkeypatch, tmp_path, caplog):
    """PREVIEW-GUARD-1, end to end through `main` and a REAL `asyncio.run`. Python 3.11's runner puts the
    default SIGINT handler back BEFORE `close()` drains the default executor, so a Ctrl-C while that drain
    waits on a cache write's thread (the owner's second press, or one landing as the run ends) escapes it
    as KeyboardInterrupt; `loop.close()` shuts the executor down WITHOUT waiting and the interpreter's exit
    join lets the thread finish — its upsert sent after `main` left the guard. The Ctrl-C is raised from
    the drain itself (`shutdown_default_executor`), never as a real SIGINT, which would hit pytest.
    MUTATION TARGET: restore httpx in the guard's `finally` whatever ended the body, and the write lands."""
    from app.services.marketing import company_news_adapter as adapter

    caplog.set_level(logging.WARNING)
    wire = _Wire(rows=[{"profile_json": {"companyName": "Contoso"}}])
    read_done, interrupted, landed = threading.Event(), threading.Event(), threading.Event()
    in_drain, outcomes, workers = [], [], []

    def merge_write():                       # company facts' `_merge_write`: the row read, then the upsert
        workers.append(threading.current_thread())
        try:
            with wire.client() as c:
                c.get(f"{_SB}/rest/v1/company_profile_cache")
                read_done.set()
                if not interrupted.wait(timeout=5):     # the Ctrl-C lands while the thread is here
                    outcomes.append("never interrupted")
                    return
                try:
                    c.post(f"{_SB}/rest/v1/company_profile_cache", json={"ticker": "ZQCT"})
                    outcomes.append("reached the wire")
                except NP.SupabaseWriteRefused:
                    outcomes.append("refused")
        finally:
            landed.set()

    async def fake_candidates(series, *, run_date, exclude, limit, deadline, deps):
        schedule(merge_write)                # fire and forget, as the services do …
        for _ in range(500):                 # … while the adapter reads on (until the thread has read)
            if read_done.is_set():
                break
            await asyncio.sleep(0.01)
        return SimpleNamespace(records=(), skip_reason="money_map_none_qualified", rejections={})

    async def ctrl_c_during_the_drain(self, *args, **kwargs):
        in_drain.append(read_done.is_set() and not landed.is_set())     # its read done, its write pending
        raise KeyboardInterrupt

    monkeypatch.setattr(adapter, "candidates", fake_candidates)
    monkeypatch.setattr(asyncio.BaseEventLoop, "shutdown_default_executor", ctrl_c_during_the_drain)
    before = _httpx_sends()
    with pytest.raises(KeyboardInterrupt):
        NP.main(_CANDIDATES_ARGS + ["--out", str(tmp_path)])
    left_on = _httpx_sends() != before       # `main` is gone and its guard exited …
    interrupted.set()                        # … and only now does the pending upsert send
    assert landed.wait(timeout=5)
    (worker,) = workers                      # the executor's thread outlived `main` — and then exits (no
    worker.join(timeout=5)                   # thread leaks into the next test; the exit join is this one)
    assert not worker.is_alive() and worker is not threading.main_thread()
    assert in_drain == [True]                # the Ctrl-C came from inside the drain, mid-write
    assert (outcomes, wire.seen) == (["refused"], [("GET", _SB_HOST, "/rest/v1/company_profile_cache")])
    assert left_on
    (stays,) = _stays_installed(caplog)
    assert "interrupted (KeyboardInterrupt)" in stays
    assert [m for m in (r.getMessage() for r in _guard_warnings(caplog))
            if "refused POST /rest/v1/company_profile_cache" in m] != []


def _install_fake_adapter(monkeypatch, wire, calls):
    """Replace the live adapter with one that tries a write and a read from INSIDE main's run — the
    guard either stops the write there or it does not."""
    from app.services.marketing import company_news_adapter as adapter

    async def fake_candidates(series, *, run_date, exclude, limit, deadline, deps):
        outcomes = {}
        with wire.client() as c:
            try:
                c.post(f"{_SB}/rest/v1/company_profile_cache", json={"ticker": "ZQCT"})
                outcomes["sync"] = "reached the wire"
            except NP.SupabaseWriteRefused:
                outcomes["sync"] = "refused"
            c.get(f"{_SB}/rest/v1/company_profile_cache")
        async with wire.async_client() as ac:
            try:
                await ac.patch(f"{_SB}/rest/v1/profit_power_cache", json={})
                outcomes["async"] = "reached the wire"
            except NP.SupabaseWriteRefused:
                outcomes["async"] = "refused"
        calls.append((series, run_date, outcomes))
        return SimpleNamespace(records=(), skip_reason="money_map_none_qualified", rejections={})

    monkeypatch.setattr(adapter, "candidates", fake_candidates)


def test_main_runs_the_live_adapter_under_the_guard(monkeypatch, tmp_path, capsys):
    """MUTATION TARGET: drop the guard from `main` and both writes reach the wire. (SUPABASE_URL is the
    test host: the autouse fixture.)"""
    wire, calls, before = _Wire(), [], _httpx_sends()
    _install_fake_adapter(monkeypatch, wire, calls)
    assert NP.main(_CANDIDATES_ARGS + ["--out", str(tmp_path)]) == 0
    assert calls == [("money_map", date(2026, 11, 19), {"sync": "refused", "async": "refused"})]
    assert wire.seen == [("GET", _SB_HOST, "/rest/v1/company_profile_cache")]    # the read went through
    assert _httpx_sends() == before                                            # removed when main returns
    out = capsys.readouterr().out
    assert "Read-only guard: refused 2 Supabase write(s)" in out
    assert "POST /rest/v1/company_profile_cache ×1" in out and "PATCH /rest/v1/profit_power_cache ×1" in out


def test_main_runs_the_fixture_mode_under_the_guard_too(monkeypatch, tmp_path):
    wire, outcomes = _Wire(), []

    def fake_write_one(rec, run_date, folder, **kw):
        with wire.client() as c:
            try:
                c.delete(f"{_SB}/rest/v1/marketing_runs")
                outcomes.append("reached the wire")
            except NP.SupabaseWriteRefused:
                outcomes.append("refused")
        return True

    monkeypatch.setattr(NP, "write_one", fake_write_one)
    assert NP.main(["--fixture", "--series", "money_map", "--date", "2026-11-19", "--out", str(tmp_path)]) == 0
    assert outcomes == ["refused"] and wire.seen == []


def test_a_fire_and_forget_cache_write_landing_after_the_adapter_returned_is_still_refused(monkeypatch, tmp_path):
    """The REAL shape of two of the three writers: profit_power / revenue_breakdown schedule their upsert
    with `run_in_executor(None, …)` and never await it (company facts: `ensure_future(to_thread(…))`), so
    the write can land AFTER the adapter — and `live_candidates` — returned. It is still refused because
    `asyncio.run` drains the default executor before it returns and `main` holds the guard until then.
    MUTATION TARGET: drive the loop without draining it (`new_event_loop` + `close()`, which shuts the
    executor down with wait=False) and `main` returns, the guard off, while this write is still pending."""
    from app.services.marketing import company_news_adapter as adapter

    wire, returned, landed, outcomes = _Wire(), threading.Event(), threading.Event(), []

    def late_upsert():
        try:
            returned.wait(timeout=5)             # only once live_candidates has returned …
            time.sleep(0.2)                      # … and later still: a write still pending at shutdown
            with wire.client() as c:
                try:
                    c.post(f"{_SB}/rest/v1/revenue_breakdown_cache", json={"ticker": "ZQCT"})
                    outcomes.append("reached the wire")
                except NP.SupabaseWriteRefused:
                    outcomes.append("refused")
        finally:
            landed.set()

    async def fake_candidates(series, *, run_date, exclude, limit, deadline, deps):
        asyncio.get_running_loop().run_in_executor(None, late_upsert)    # never awaited, as the services do
        return SimpleNamespace(records=(), skip_reason="money_map_none_qualified", rejections={})

    real_live = NP.live_candidates

    async def live_then_mark(*a, **k):
        try:
            return await real_live(*a, **k)
        finally:
            returned.set()

    monkeypatch.setattr(adapter, "candidates", fake_candidates)
    monkeypatch.setattr(NP, "live_candidates", live_then_mark)
    before = _httpx_sends()
    code = NP.main(_CANDIDATES_ARGS + ["--out", str(tmp_path)])
    drained = landed.is_set()                    # read BEFORE any wait: main never returns with it pending
    assert landed.wait(timeout=5)                # the thread is done either way before the asserts
    assert code == 0 and drained
    assert outcomes == ["refused"] and wire.seen == []
    assert _httpx_sends() == before


@pytest.mark.parametrize("bad", ["", "not a url"])
def test_main_refuses_to_run_without_a_supabase_host(monkeypatch, tmp_path, capsys, bad):
    from app.config import settings

    monkeypatch.setattr(settings, "SUPABASE_URL", bad)
    wire, calls, before = _Wire(), [], _httpx_sends()
    _install_fake_adapter(monkeypatch, wire, calls)
    out_root = tmp_path / "out"
    assert NP.main(_CANDIDATES_ARGS + ["--out", str(out_root)]) == 2
    assert calls == [] and wire.seen == [] and not out_root.exists()           # nothing ran, nothing written
    assert _httpx_sends() == before
    assert "never runs without its read-only guard" in capsys.readouterr().err


# ── Batch 3 (2026-10-20): the preview follows the posting-day switch ──────────


def test_batch3_next_posting_day_and_sample_day_follow_the_switch(monkeypatch, tmp_path):
    """The preview reads the calendar production runs (`selection`, call time). At seven days the next posting day
    of a Wednesday is that Wednesday, an earnings sample lands on the season's first Wednesday — and composes there —
    and a Sunday previews Money Map, never earnings (it leads Wed/Thu/Fri only). At the default nothing moves: the
    earnings sample is Thursday's, and switching back restores it."""
    mon, wed, thu, sat, sun = (date(2026, 11, 2), date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 7),
                               date(2026, 11, 8))
    assert selection.in_earnings_season(wed) and selection.in_earnings_season(sun)
    default = (NP.next_posting_day(wed), NP.sample_day("earnings", mon), NP.sample_day("money_map", sun),
               NP.sample_day("earnings", sat))
    assert default == (thu, thu, date(2026, 11, 9), date(2026, 11, 12))
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,wed,thu,fri,sat,sun")
    assert NP.next_posting_day(wed) == wed and NP.next_posting_day(sun) == sun
    day = NP.sample_day("earnings", mon)
    assert day == wed
    for rec in NP.sample_records("earnings", day):
        stored, script = NP.compose_stored(rec, day, logos_in=None, logos_out=tmp_path / "logos")
        assert stored["series"] == "earnings" and script["image_spec"] == stored["image_spec"]
    assert NP.sample_day("money_map", sun) == sun
    assert NP.sample_day("earnings", sat) == date(2026, 11, 11)          # past Sunday, Monday and Tuesday
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat")
    assert (NP.next_posting_day(wed), NP.sample_day("earnings", mon), NP.sample_day("money_map", sun),
            NP.sample_day("earnings", sat)) == default


# ── B3-LIVE-3 (2026-10-20): the preview freezes a day through production's own write-time freeze ───────────
#
# The page said it composed "EXACTLY as a template day stores it" but froze with its own
# `freeze_post_formats(image_posts=True, x_images=True)`: every picture platform plus X as a picture (production posts
# X as text), and no video features or extra video posts — so post.md never showed the Facebook / LinkedIn VIDEO
# captions. It now calls `MarketingScriptService._frozen_output` itself: the switches read NOW from this environment.

#: The switches the plan has production run with once Batch 2 / 3 are on (OWNER_TASKS 18-19).
_PLANNED_SWITCHES = {"MARKETING_VIDEO_EXTRA_PLATFORMS": "facebook,linkedin",
                     "MARKETING_VIDEO_FEATURES": "data_card,push_in"}


def _switches(monkeypatch, **values):
    from app.config import settings

    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)


@pytest.mark.parametrize("series, day", [("ceo_buys", MON), ("money_map", THU)])
def test_b3live3_the_preview_freezes_through_productions_own_write_time_freeze(monkeypatch, tmp_path, series, day):
    """The stored output is EXACTLY what `_frozen_output` returned — whatever production's rules decide — called with
    image posts on, the record's fact sheet and a holder that declares today's worker capabilities: X text
    (MARKETING_X_IMAGES off), every extra video post with its re-derived caption, and the video features the worker
    is sent. A Money Map (no caption rule speaks of its picture) gets both extras."""
    from app.schemas import marketing as sch
    from app.services.marketing import post_copy
    from app.services.marketing.script_service import MarketingScriptService

    _switches(monkeypatch, **_PLANNED_SWITCHES)
    calls = []
    real = MarketingScriptService._frozen_output

    async def spy(self, package, run_date, **kw):
        holder = await self.runs.get_run(kw["run_id"])
        got = await real(self, package, run_date, **kw)
        calls.append((run_date, kw, holder, got))
        return got

    monkeypatch.setattr(MarketingScriptService, "_frozen_output", spy)
    rec = NP.sample_records(series, day)[0]
    stored, script = NP.compose_stored(rec, day, logos_in=None, logos_out=tmp_path)
    ((run_date, kw, holder, got),) = calls
    rec_rt = R.record_from_dict(json.loads(json.dumps(R.record_to_dict(rec))))
    assert run_date == day and kw["image_posts"] is True
    assert kw["fact_sheet"] == json.loads(json.dumps(R.fact_sheet(rec_rt, rejections={}, selection={"preview": True})))
    assert holder["metadata"][sch.RUN_WORKER_CAPABILITIES_KEY] == list(sch.WORKER_CAPABILITIES)
    assert stored == json.loads(json.dumps(got))
    assert stored["post_formats"]["x"] == "text"
    assert {p for p, f in stored["post_formats"].items() if f == "image"} == set(sch.IMAGE_PLATFORMS)
    extras = stored.get(sch.EXTRA_FORMATS_KEY) or {}
    assert set(extras) == set(stored.get(sch.VIDEO_POSTS_KEY) or {}) <= {"facebook", "linkedin"}
    for platform in extras:
        assert stored[sch.VIDEO_POSTS_KEY][platform]["caption"] == post_copy.extra_video_caption(
            platform, stored["posts"][platform]["caption"], day, T.AUTHORSHIP)
    if series == "money_map":
        assert extras == {"facebook": "video", "linkedin": "video"}
    assert "push_in" in script["video_features"]
    assert set(script["video_features"]) <= set(stored[sch.VIDEO_FEATURES_KEY])


def test_b3live3_the_picture_switches_are_this_environments(monkeypatch, tmp_path):
    """MARKETING_X_IMAGES on → X posts the picture; MARKETING_IMAGE_PLATFORMS=bluesky → Facebook, LinkedIn and Threads
    post text, by design (no `image_fallback`) — as production would freeze them."""
    _switches(monkeypatch, MARKETING_X_IMAGES=True, MARKETING_IMAGE_PLATFORMS="bluesky")
    rec = NP.sample_records("ceo_buys", MON)[0]
    stored, _ = NP.compose_stored(rec, MON, logos_in=None, logos_out=tmp_path)
    formats = stored["post_formats"]
    assert formats["x"] == "image" and formats["bluesky"] == "image"
    assert formats["facebook"] == formats["linkedin"] == formats["threads"] == "text"
    assert "image_fallback" not in stored


def test_b3live3_at_the_declared_defaults_there_is_no_extra_post_and_no_video_feature(tmp_path):
    from app.schemas import marketing as sch

    rec = NP.sample_records("ceo_buys", MON)[0]
    stored, script = NP.compose_stored(rec, MON, logos_in=None, logos_out=tmp_path)
    assert not {sch.EXTRA_FORMATS_KEY, sch.VIDEO_POSTS_KEY, sch.VIDEO_FEATURES_KEY} & set(stored)
    assert stored["post_formats"]["x"] == "text" and script["video_features"] == []
    page = NP.post_markdown(stored, series="ceo_buys", ref="r", run_date=MON, sample=True)
    assert "## Extra video posts" not in page and "**Video features:** none" in page and "this environment" not in page


def test_b3live3_the_reviewer_page_shows_every_post_the_day_records_and_its_switches(monkeypatch, tmp_path):
    """Through `write_one`: post.md lists X as text, the video features, every EXTRA video post's own caption (where
    a caption written for the picture shows up beside the video) and the switches the freeze read."""
    _switches(monkeypatch, **_PLANNED_SWITCHES)
    rec = NP.sample_records("money_map", THU)[0]
    assert NP.write_one(rec, THU, tmp_path / "money_map-1", logos_in=None, sample=True, do_render=False) is True
    page = (tmp_path / "money_map-1" / "post.md").read_text(encoding="utf-8")
    stored = json.loads((tmp_path / "money_map-1" / "stored_output.json").read_text(encoding="utf-8"))
    assert "### x (text)" in page and "### x (image)" not in page
    assert "push_in" in stored["video_features"]
    assert f"**Video features:** {', '.join(stored['video_features'])}" in page
    for name, value in (("MARKETING_VIDEO_EXTRA_PLATFORMS", "facebook,linkedin"), ("MARKETING_X_IMAGES", "False"),
                        ("MARKETING_VIDEO_FEATURES", "data_card,push_in"),
                        ("MARKETING_IMAGE_PLATFORMS", "bluesky,facebook,linkedin,threads")):
        assert f"{name}={value}" in page
    extras = page.split("## Extra video posts", 1)[1]
    for platform in ("facebook", "linkedin"):
        assert f"### {platform} (video, extra)" in extras
        assert stored["video_posts"][platform]["caption"].strip() in extras
    assert "### threads (video, extra)" not in page


def test_b3live3_the_reviewer_page_names_a_picture_fallback_in_owner_words():
    stored = {"hook": "h", "posts": {"facebook": {"caption": "c"}}, "post_formats": {"facebook": "text"},
              "image_fallback": {"reason": "worker_capability", "platforms": ["facebook", "linkedin"]}}
    page = NP.post_markdown(stored, series="ceo_buys", ref="r", run_date=MON, sample=False)
    from app.schemas.marketing import IMAGE_FALLBACK_REASONS

    assert "## Picture not used" in page
    assert (f"{IMAGE_FALLBACK_REASONS['worker_capability']} (worker_capability) — posted as text: facebook, linkedin"
            in page)


def test_b3live3_a_listed_extra_the_freeze_did_not_keep_is_named_never_silently_missing(monkeypatch, tmp_path):
    """MARKETING_VIDEO_EXTRA_PLATFORMS lists Facebook and LinkedIn, but the freeze keeps neither (here stubbed: a
    caption that fails its re-check). post.md says so — never a page that simply lacks them."""
    from app.services.marketing import script_service as ss

    _switches(monkeypatch, **_PLANNED_SWITCHES)
    monkeypatch.setattr(ss, "freeze_extra_formats", lambda *_a, **_k: {})
    rec = NP.sample_records("money_map", THU)[0]
    assert NP.write_one(rec, THU, tmp_path / "money_map-1", logos_in=None, sample=True, do_render=False) is True
    page = (tmp_path / "money_map-1" / "post.md").read_text(encoding="utf-8")
    assert "## Extra video posts" in page and "Listed but NOT frozen today: facebook, linkedin" in page
    assert "(video, extra)" not in page
    # nothing listed, nothing frozen: no section at all
    _switches(monkeypatch, MARKETING_VIDEO_EXTRA_PLATFORMS="")
    assert NP.write_one(rec, THU, tmp_path / "money_map-2", logos_in=None, sample=True, do_render=False) is True
    assert "## Extra video posts" not in (tmp_path / "money_map-2" / "post.md").read_text(encoding="utf-8")


def test_r1_a_form4_roundup_without_the_data_card_shows_why_its_extras_are_not_frozen(monkeypatch, tmp_path):
    """Round-1 integration of B3-LIVE-1 (`picture_caption`) with B3-LIVE-3 (the preview freezes through production's
    own `_frozen_output`), with nothing stubbed: a Form 4 ROUNDUP's Facebook and LinkedIn captions speak of the
    picture's rows, so while the frozen video features lack the data card the freeze keeps neither extra — and
    post.md names that reason. With the data card frozen, the same record keeps both."""
    from app.schemas import marketing as sch
    from app.services.marketing import news_templates

    rec = NP.sample_records("ceo_buys", MON)[0]
    assert news_templates._picture_bound(rec, news_templates.SERIES_SPECS["ceo_buys"], MON)   # a roundup
    _switches(monkeypatch, MARKETING_VIDEO_EXTRA_PLATFORMS="facebook,linkedin", MARKETING_VIDEO_FEATURES="push_in")
    assert NP.write_one(rec, MON, tmp_path / "ceo-1", logos_in=None, sample=True, do_render=False) is True
    page = (tmp_path / "ceo-1" / "post.md").read_text(encoding="utf-8")
    stored = json.loads((tmp_path / "ceo-1" / "stored_output.json").read_text(encoding="utf-8"))
    assert not {sch.EXTRA_FORMATS_KEY, sch.VIDEO_POSTS_KEY} & set(stored)
    assert "data_card" not in (stored.get(sch.VIDEO_FEATURES_KEY) or [])
    assert "Listed but NOT frozen today: facebook, linkedin" in page and "(picture_caption: " in page
    assert "(video, extra)" not in page

    _switches(monkeypatch, MARKETING_VIDEO_FEATURES="data_card,push_in")
    assert NP.write_one(rec, MON, tmp_path / "ceo-2", logos_in=None, sample=True, do_render=False) is True
    page = (tmp_path / "ceo-2" / "post.md").read_text(encoding="utf-8")
    stored = json.loads((tmp_path / "ceo-2" / "stored_output.json").read_text(encoding="utf-8"))
    assert "data_card" in stored[sch.VIDEO_FEATURES_KEY]
    assert stored[sch.EXTRA_FORMATS_KEY] == {"facebook": "video", "linkedin": "video"}
    assert "NOT frozen today" not in page
    for platform in ("facebook", "linkedin"):
        assert f"### {platform} (video, extra)" in page
