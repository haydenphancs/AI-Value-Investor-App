#!/usr/bin/env python3
"""
marketing_news_preview.py — read a Company Weekly day before it goes live (drop 2a, contract D16).

Composes each news record EXACTLY as a template day stores it (`script_service._compose_candidate`'s
steps: the record's JSON round trip, its fact sheet, `news_templates.compose`, `revalidate`, the logos,
`freeze_post_formats` with image posts on, `revalidate` as stored, `worker_script`) and writes, per
record, under `backend/marketing/out/news-preview/<stamp>/<series>-<n>/` (gitignored):

    post.md        what a reviewer reads: headline, narration, cards, image text, every caption
    script.json    the worker's script, exactly as the run would send it
    logos/         the logos the worker would draw, named by their bucket content address
    (--render)     preview.mp4 + post_image.jpg + report.json from the worker's own preview
                   (`python -m marketing.preview … --logos-dir … --phonemes`, venv_marketing)

It writes NOTHING else: no marketing_runs / marketing_scripts row, no bucket object, no post.

Usage (from backend/):
    ./venv/bin/python scripts/marketing_news_preview.py --series ceo_buys --fixture            # built-in samples
    ./venv/bin/python scripts/marketing_news_preview.py --series money_map --fixture day.json --render
    ./venv/bin/python scripts/marketing_news_preview.py --candidates                          # next posting day
    ./venv/bin/python scripts/marketing_news_preview.py --candidates --date 2026-11-16 --series ceo_buys \\
        --record day.json --logos --render

--fixture without a file uses BUILT-IN SAMPLE records about FICTIONAL companies (Contoso, Fabrikam, …) on
symbols that are no listing (ZQCT, ZQFB, … — `SAMPLE_COMPANIES`), labelled "SAMPLE DATA" in post.md (the
rendered image and video carry no label) — every figure is invented. Their logos are wordmarks. Without --date
each series is previewed on the next day its calendar runs it (`sample_day`: a Congress Count needs its
Tuesday, 13F Season its season, earnings its season); --date pins one date for every series.

Every shipped series can be previewed — the drop-2b four (congress_count, company_stakes, earnings,
theme_explainer) included, although production runs them only once MARKETING_NEWS_SERIES lists them
(--candidates marks a series that is off there).

--candidates is LIVE and READ-ONLY: it asks the one adapter (`company_news_adapter.candidates`) for the
series the weekday calendar picks for --date (default: the next posting day, ET), or for --series. That
is FMP and Supabase READS with the app's own keys (backend/.env); the revenue / profit services it reuses
may refresh their own `*_cache` rows, exactly as the app does. Already-posted items are NOT excluded (a
real run skips them). --logos fetches each company's FMP logo (`fetch_logo` + `logo_check`) into the
output folder — never the bucket. --record FILE saves the records for a later --fixture FILE replay.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.marketing import company_news_rules as R  # noqa: E402
from app.services.marketing import logo_check, news_templates, selection  # noqa: E402
from app.services.marketing.script_service import freeze_post_formats, worker_script  # noqa: E402
from app.utils.market_hours import ET  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
OUT_ROOT = BACKEND / "marketing" / "out" / "news-preview"
WORKER_PYTHON = BACKEND / "venv_marketing" / "bin" / "python"
#: Mirrors marketing/preview.py PREVIEW_BUCKET_HOST (the worker never imports app.*; pinned equal by
#: tests/test_marketing_news_preview.py).
PREVIEW_BUCKET_HOST = "preview.local"
PREVIEW_PUBLIC_PREFIX = f"https://{PREVIEW_BUCKET_HOST}/storage/v1/object/public/marketing-media/logos/"
#: The series a preview can compose: every shipped series (`selection.SHIPPED_SERIES`, all eight since
#: drop 2b), in the calendar registry's order.
SHIPPED_SERIES: Tuple[str, ...] = tuple(s.id for s in selection.SERIES if s.id in selection.SHIPPED_SERIES)
#: How far ahead `sample_day` looks for a day whose calendar runs a series (> a year: every slot recurs).
SAMPLE_DAY_HORIZON_DAYS = 400
CANDIDATES_LIMIT = 5
CANDIDATES_BUDGET_SECONDS = 180.0
LOGO_TIMEOUT_SECONDS = 10.0


# ── built-in SAMPLE records: fictional companies, invented figures ──────────────

#: Every company a built-in sample draws: key → (symbol, name). FICTIONAL names on symbols chosen to be no
#: listing — "ZQ" + two letters. The rendered image and video carry no SAMPLE DATA label (only post.md
#: does), so a real ticker here would put an invented figure (a Congress count, a CEO buy) beside a real
#: company's chip. Each symbol is absent from every local universe, ticker list, the Trillion Club seed and
#: the whale registry (`backend/data/*.json`), pinned by tests/test_marketing_news_preview.py — an offline
#: check: re-check a new symbol against a live symbol search before adding it.
SAMPLE_COMPANIES: Dict[str, Tuple[str, str]] = {
    "contoso": ("ZQCT", "Contoso"),
    "fabrikam": ("ZQFB", "Fabrikam"),
    "northwind": ("ZQNW", "Northwind Traders"),
    "tailspin": ("ZQTT", "Tailspin Toys"),
    "woodgrove": ("ZQWB", "Woodgrove Bank"),
    "adventure": ("ZQAW", "Adventure Works"),
    "proseware": ("ZQPW", "Proseware"),
    "litware": ("ZQLW", "Litware"),
}


def _co(key: str) -> R.CompanyRef:
    sym, name = SAMPLE_COMPANIES[key]
    return R.CompanyRef(symbol=sym, name=name)


def _buy(key: str, role: str, person: Optional[str], amount: float, shares: float, k: int,
         filings: Sequence[date], holding: str = "direct", amended: bool = False) -> R.InsiderPurchase:
    return R.InsiderPurchase(company=_co(key), role=role, person_name=person, amount_usd=amount,
                             shares=shares, purchases=k, earliest_trade_date=filings[0],
                             latest_trade_date=filings[-1], filing_dates=tuple(filings), holding=holding,
                             amended=amended)


def _move(key: str, kind: str, **kw: Any) -> R.ThirteenFMove:
    base: Dict[str, Any] = dict(shares=None, prev_shares=None, value_usd=None, listed_on=None)
    base.update(kw)
    return R.ThirteenFMove(company=_co(key), move=kind, **base)


def _quarter_end_before(d: date) -> date:
    """The latest calendar quarter end strictly before `d`."""
    end_month = ((d.month - 1) // 3) * 3
    if end_month == 0:
        return date(d.year - 1, 12, 31)
    return date(d.year, end_month + 1, 1) - timedelta(days=1)


def sample_day(series: str, start: date) -> date:
    """The first day on or after `start` whose calendar runs `series` (`selection.plan_for`): the day a
    sample of it is previewed on when no --date is given."""
    for i in range(SAMPLE_DAY_HORIZON_DAYS):
        d = start + timedelta(days=i)
        if series in selection.plan_for(d).chain:
            return d
    raise ValueError(f"no day runs {series!r} within {SAMPLE_DAY_HORIZON_DAYS} days of {start}")


def sample_records(series: str, run_date: date) -> List[Any]:
    """Fictional records for `series`, dated for `run_date` (a Monday for the insider series, a
    Tuesday in 13F season for thirteen_f, the month's Congress Tuesday for congress_count — day 7 or
    later, any day for the others; `sample_day` finds one)."""
    start = run_date - timedelta(days=7)
    d = [start + timedelta(days=i) for i in range(7)]
    if series == "ceo_buys":
        rows = (_buy("contoso", "ceo", "Jordan Avery", 12_400_000.0, 310_000.0, 2, [d[1], d[2]]),
                _buy("fabrikam", "ceo", None, 1_850_000.0, 22_500.0, 1, [d[3]], holding="indirect"),
                _buy("northwind", "ceo", None, 420_000.0, 6_100.0, 1, [d[4]], amended=True))
        return [R.InsiderBuysWeek(series="ceo_buys", window_start=start, window_end=d[6], rows=rows),
                R.InsiderBuysWeek(series="ceo_buys", window_start=start, window_end=d[6], rows=rows[:1])]
    if series == "insider_buys":
        rows = (_buy("tailspin", "cfo", None, 1_250_000.0, 18_500.0, 1, [d[1]]),
                _buy("woodgrove", "director", "Casey Morgan", 640_000.0, 2_600.0, 3, [d[3], d[4]],
                     holding="mixed"),
                _buy("adventure", "director", None, 150_000.0, 812.0, 1, [d[5]]))
        return [R.InsiderBuysWeek(series="insider_buys", window_start=start, window_end=d[6], rows=rows)]
    if series == "thirteen_f":
        year = run_date.year if run_date.month > 3 else run_date.year - 1
        q = (run_date.month - 1) // 3 if run_date.month > 3 else 4
        period = f"{year}-Q{q}"
        period_end = R.period_end_of(period)
        listed = period_end - timedelta(days=40)
        return [R.ThirteenFFiling(
            series="thirteen_f", filer_name="Litware Capital Management", filer_cik="0009999901",
            filer_symbol=None, period=period, period_end=period_end, filed_on=period_end + timedelta(days=44),
            amended_on=None, total_value_usd=18_600_000_000.0, position_count=23,
            moves=(_move("proseware", "newly_reported", shares=1_900_000.0, prev_shares=0.0,
                         value_usd=410_000_000.0, listed_on=listed),
                   _move("contoso", "increased", shares=12_000_000.0, prev_shares=9_500_000.0,
                         value_usd=2_100_000_000.0),
                   _move("fabrikam", "decreased", shares=4_000_000.0, prev_shares=6_600_000.0,
                         value_usd=780_000_000.0),
                   _move("northwind", "no_longer_reported", shares=0.0, prev_shares=3_100_000.0)),
            counts=(("newly_reported", 2), ("increased", 3), ("decreased", 4), ("no_longer_reported", 1)))]
    if series == "money_map":
        fy = run_date.year - 1
        return [R.MoneyMap(series="money_map", company=_co("contoso"), fiscal_year=str(fy),
                           period_end=date(fy, 12, 31),
                           segments=(R.Segment("Cloud services", 61_000_000_000.0),
                                     R.Segment("Devices", 24_500_000_000.0),
                                     R.Segment("Advertising", 11_200_000_000.0)),
                           other_usd=None, eliminations_usd=None, revenue_usd=96_700_000_000.0,
                           gross_profit_usd=58_100_000_000.0, operating_profit_usd=29_400_000_000.0,
                           net_income_usd=22_800_000_000.0)]
    if series == "congress_count":
        # a count of distinct members of Congress — never a member's name; the month before the run
        month = selection.congress_disclosure_month(run_date)
        return [R.CongressCount(series="congress_count", company=_co("contoso"), month=month, members=4,
                                fetched_on=run_date),
                R.CongressCount(series="congress_count", company=_co("fabrikam"), month=month, members=2,
                                fetched_on=run_date)]
    if series == "company_stakes":
        return [R.CompanyStake(series="company_stakes", stake_id="0a1b2c3d-4e5f-4a6b-8c7d-0000000c0de1",
                               investor=_co("fabrikam"), investee_name="Relecloud", investee=None,
                               kind="private", value_usd=640_000_000.0, value_basis="invested", ownership_pct=None,
                               as_of=run_date - timedelta(days=75), verified_on=run_date - timedelta(days=12),
                               source_title="Relecloud Form S-1", background="For Series B shares.",
                               listed_since=None, local_listing=None, is_new=False),
                R.CompanyStake(series="company_stakes", stake_id="0a1b2c3d-4e5f-4a6b-8c7d-0000000c0de2",
                               investor=_co("contoso"), investee_name="Proseware",
                               investee=_co("proseware"), kind="us_listed_off_13f",
                               value_usd=1_250_000_000.0, value_basis="fair_value", ownership_pct=None,
                               as_of=_quarter_end_before(run_date), verified_on=run_date - timedelta(days=5),
                               source_title="Contoso 10-Q", background=None, listed_since=None,
                               local_listing=None, is_new=True)]
    if series == "earnings":
        report = run_date - timedelta(days=2)
        return [R.EarningsReport(series="earnings", company=_co("northwind"), report_date=report,
                                 period_end=_quarter_end_before(report), eps_actual=-0.05, eps_estimate=-0.12,
                                 revenue_actual=551_900_000.0, revenue_estimate=543_600_000.0),
                R.EarningsReport(series="earnings", company=_co("tailspin"),
                                 report_date=run_date - timedelta(days=4), period_end=None, eps_actual=1.42,
                                 eps_estimate=1.3, revenue_actual=None, revenue_estimate=None)]
    if series == "theme_explainer":
        fy = str(run_date.year - 1)
        rows = (("contoso", "Robotics systems", 0.62), ("fabrikam", "Automation software", 0.48),
                ("northwind", "Warehouse systems", 0.71), ("tailspin", "Drones", 0.39),
                ("adventure", "Field services", 0.55), ("proseware", "Sensors", None),
                ("woodgrove", None, None), ("litware", "Logistics software", 0.66))
        members = tuple(R.ThemeMember(company=_co(key), top_segment=seg, top_segment_share=share,
                                      fiscal_year=fy if seg else None) for key, seg, share in rows)
        return [R.ThemeExplainer(series="theme_explainer", slug="warehouse-robots", title="Warehouse robots",
                                 members=members, tickers_as_of=run_date - timedelta(days=10))]
    raise ValueError(f"no sample records for {series!r} (shipped: {', '.join(SHIPPED_SERIES)})")


# ── composing one record, as a template day stores it ──────────────────────────

class PreviewRefused(Exception):
    """The record does not compose (or fails its own re-check): what a real day would skip."""


def logo_entries(output: Dict[str, Any], logos_in: Optional[Path], logos_out: Path) -> List[Dict[str, Any]]:
    """`output.logos` as `_attach_logos` builds it, from local files `<logos_in>/<KEY>.png|jpg` instead
    of FMP + the bucket: checked by `logo_check.inspect_logo`, copied to `<logos_out>/<sha[:32]>.<ext>`,
    the URL a preview bucket URL. A missing or refused file → the all-None entry (a wordmark)."""
    out: List[Dict[str, Any]] = []
    for ref in news_templates.logo_refs(output):
        key, name = ref.get("key"), ref.get("name")
        entry: Dict[str, Any] = {"key": key, "name": name, "url": None, "sha256": None, "bytes": None,
                                 "width": None, "height": None}
        src = None
        if logos_in is not None:
            src = next((p for p in (logos_in / f"{key}.png", logos_in / f"{key}.jpg") if p.is_file()), None)
        if src is not None:
            data = src.read_bytes()
            try:
                info = logo_check.inspect_logo(data, "image/png" if src.suffix == ".png" else "image/jpeg")
            except logo_check.LogoRejected as e:
                print(f"  logo {key}: refused ({getattr(e, 'reason', e)}) — wordmark", file=sys.stderr)
            else:
                logos_out.mkdir(parents=True, exist_ok=True)
                name_on_disk = f"{info.sha256[:32]}.{info.ext}"
                (logos_out / name_on_disk).write_bytes(data)
                entry.update({"url": PREVIEW_PUBLIC_PREFIX + name_on_disk, "sha256": info.sha256,
                              "bytes": len(data), "width": info.width, "height": info.height})
        out.append(entry)
    return out


def compose_stored(rec: Any, run_date: date, *, logos_in: Optional[Path], logos_out: Path,
                   store_state: str = "live", allow_x_url: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(stored output, worker script) of one record — PreviewRefused when a real day would refuse it."""
    rec = R.record_from_dict(json.loads(json.dumps(R.record_to_dict(rec), allow_nan=False)))
    sheet = json.loads(json.dumps(R.fact_sheet(rec, rejections={}, selection={"preview": True}), allow_nan=False))
    try:
        out = news_templates.compose(rec, run_date=run_date, store_state=store_state, allow_x_url=allow_x_url)
    except news_templates.NewsTemplateRefused as e:
        raise PreviewRefused(f"compose refused: {e.code} ({getattr(e, 'detail', '')})") from e
    problems = news_templates.revalidate(out, fact_sheet=sheet, run_date=run_date)
    if problems:
        raise PreviewRefused(f"own re-check failed: {sorted({str(p.get('code')) for p in problems})}")
    out["logos"] = logo_entries(out, logos_in, logos_out)
    out = freeze_post_formats(out, run_date, image_posts=True, x_images=True, run_id="preview")
    stored = json.loads(json.dumps(out, allow_nan=False))
    problems = news_templates.revalidate(stored, fact_sheet=sheet, run_date=run_date)
    if problems:
        raise PreviewRefused(f"re-check as stored failed: {problems[:3]}")
    return stored, worker_script(stored)


# ── what a reviewer reads ──────────────────────────────────────────────────────

def post_markdown(stored: Dict[str, Any], *, series: str, ref: str, run_date: date, sample: bool) -> str:
    lines: List[str] = [f"# {series} · {run_date:%a %b %-d, %Y}", ""]
    if sample:
        lines += ["> **SAMPLE DATA** — fictional companies, invented figures. Never post this.", ""]
    lines += [f"`{ref}` · template {stored.get('prompt_version') or news_templates.TEMPLATE_VERSION}", "",
              "## Video", "", f"**Hook (spoken over the opening card):** {stored.get('hook')}", ""]
    opening = stored.get("opening_card") or {}
    if opening:
        lines += ["**Opening card:** " + " · ".join(f"{k}: {v}" for k, v in opening.items()
                                                  if isinstance(v, (str, int, float))), ""]
    cards = stored.get("cards") or []
    for i, text in enumerate(stored.get("video_script") or [], 1):
        card = cards[i - 1] if i - 1 < len(cards) else {}
        lines.append(f"{i}. 🔊 {text}")
        if card:
            lines.append(f"   🟦 **{card.get('title', '')}** — {card.get('body', '')}")
    lines += ["", f"_Last card:_ {stored.get('disclaimer_card')}", "", "## Post image", ""]
    spec = stored.get("image_spec")
    lines += ["```json", json.dumps(spec, indent=1, ensure_ascii=False)[:4000], "```",
              f"_Footer:_ {stored.get('image_footer')}", "", "## Captions", ""]
    formats = stored.get("post_formats") or {}
    for platform, post in sorted((stored.get("posts") or {}).items()):
        post = post or {}
        lines += [f"### {platform} ({formats.get(platform, '?')})", ""]
        if post.get("title"):
            lines += [f"**Title:** {post['title']}", ""]
        lines += ["```", str(post.get("caption") or "").strip(), "```", ""]
    dropped = stored.get("dropped_outlets") or {}
    if dropped:
        lines += ["## Dropped outlets", "", "```json", json.dumps(dropped, indent=1), "```", ""]
    return "\n".join(lines)


def render(script_path: Path, logos_dir: Path, out_dir: Path) -> int:
    """The worker's own preview (venv_marketing: Kokoro, Pillow, ffmpeg) into `out_dir`."""
    if not WORKER_PYTHON.exists():
        print(f"  --render: {WORKER_PYTHON} is missing (see marketing/README.md)", file=sys.stderr)
        return 1
    cmd = [str(WORKER_PYTHON), "-m", "marketing.preview", str(script_path), "--logos-dir", str(logos_dir),
           "--phonemes", "--out", str(out_dir)]
    done = subprocess.run(cmd, cwd=str(BACKEND), capture_output=True, text=True)
    (out_dir / "render.log").write_text(done.stdout + "\n--- stderr ---\n" + done.stderr, encoding="utf-8")
    return done.returncode


def write_one(rec: Any, run_date: date, folder: Path, *, logos_in: Optional[Path], sample: bool,
              do_render: bool) -> bool:
    folder.mkdir(parents=True, exist_ok=True)
    ref = R.ledger_key(rec)
    try:
        stored, script = compose_stored(rec, run_date, logos_in=logos_in, logos_out=folder / "logos")
    except PreviewRefused as e:
        (folder / "REFUSED.txt").write_text(f"{ref}\n{e}\n", encoding="utf-8")
        print(f"- {ref}: REFUSED — {e}")
        return False
    (folder / "script.json").write_text(json.dumps(script, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "stored_output.json").write_text(json.dumps(stored, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "post.md").write_text(post_markdown(stored, series=rec.series, ref=ref, run_date=run_date,
                                                  sample=sample), encoding="utf-8")
    print(f"- {ref}: composed → {folder}")
    if do_render:
        (folder / "logos").mkdir(exist_ok=True)
        code = render(folder / "script.json", folder / "logos", folder)
        print(f"  render {'OK' if code == 0 else f'FAILED (exit {code}, see render.log)'}")
        return code == 0
    return True


# ── live candidates (read-only) ────────────────────────────────────────────────

def series_marked(series_list: Sequence[str]) -> List[str]:
    """Each series id, marked when the per-series switch (MARKETING_NEWS_SERIES, this environment's) keeps
    it off in production — a preview shows what COULD run, not what will."""
    from app.config import settings

    on = selection.parse_news_series(settings.MARKETING_NEWS_SERIES)
    return [s if s in on else f"{s} (off in MARKETING_NEWS_SERIES)" for s in series_list]


def next_posting_day(today: date) -> date:
    d = today
    while not selection.is_posting_day(d):
        d += timedelta(days=1)
    return d


async def live_candidates(series_list: Sequence[str], run_date: date, *, logos_dir: Optional[Path]
                          ) -> Dict[str, Dict[str, Any]]:
    """{series: {"records": [...], "skip_reason", "rejections", "error"}} from the live adapter."""
    from app.services.marketing import company_news_adapter as adapter  # the one FMP door, lazily

    deps = adapter.NewsDeps()
    found: Dict[str, Dict[str, Any]] = {}
    for series in series_list:
        try:
            got = await adapter.candidates(series, run_date=run_date, exclude=frozenset(),
                                           limit=CANDIDATES_LIMIT,
                                           deadline=deps.monotonic() + CANDIDATES_BUDGET_SECONDS, deps=deps)
            found[series] = {"records": list(got.records), "skip_reason": got.skip_reason,
                             "rejections": dict(got.rejections), "error": None}
        except adapter.MarketingNewsUnavailable as e:
            found[series] = {"records": [], "skip_reason": None, "rejections": {},
                             "error": f"unavailable: {e.reason} {getattr(e, 'detail', '')}"[:300]}
        if logos_dir is not None:
            symbols = sorted({sym for rec in found[series]["records"] for sym in record_symbols(rec)})
            logos_dir.mkdir(parents=True, exist_ok=True)
            for sym in symbols:
                if (logos_dir / f"{sym}.png").exists() or (logos_dir / f"{sym}.jpg").exists():
                    continue
                got_logo = await adapter.fetch_logo(sym, max_bytes=logo_check.LOGO_MAX_BYTES,
                                                    timeout=LOGO_TIMEOUT_SECONDS, deps=deps)
                if got_logo is None:
                    continue
                data, content_type = got_logo
                try:
                    info = logo_check.inspect_logo(data, content_type)
                except logo_check.LogoRejected as e:
                    print(f"  logo {sym}: refused ({getattr(e, 'reason', e)}) — wordmark", file=sys.stderr)
                    continue
                (logos_dir / f"{sym}.{info.ext}").write_bytes(data)
    return found


def record_symbols(rec: Any) -> List[str]:
    """Every company symbol a record may draw a logo for."""
    if isinstance(rec, R.InsiderBuysWeek):
        return [r.company.symbol for r in rec.rows]
    if isinstance(rec, R.ThirteenFFiling):
        return ([rec.filer_symbol] if rec.filer_symbol else []) + [m.company.symbol for m in rec.moves]
    if isinstance(rec, R.CompanyStake):
        return [rec.investor.symbol] + ([rec.investee.symbol] if rec.investee is not None else [])
    if isinstance(rec, R.ThemeExplainer):
        return [m.company.symbol for m in rec.members]
    company = getattr(rec, "company", None)
    return [company.symbol] if isinstance(company, R.CompanyRef) else []


def save_records(path: Path, run_date: date, by_series: Dict[str, List[Any]]) -> None:
    path.write_text(json.dumps({"run_date": run_date.isoformat(),
                                "series": {s: [R.record_to_dict(r) for r in recs] for s, recs in by_series.items()}},
                               indent=1, allow_nan=False), encoding="utf-8")


def load_records(path: Path) -> Tuple[date, Dict[str, List[Any]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return (date.fromisoformat(raw["run_date"]),
            {s: [R.record_from_dict(d) for d in recs] for s, recs in (raw.get("series") or {}).items()})


# ── main ───────────────────────────────────────────────────────────────────────

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--series", action="append", choices=SHIPPED_SERIES,
                    help="a series to preview (repeatable; default with --candidates: the day's calendar)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fixture", nargs="?", const="", type=str, metavar="FILE",
                      help="compose records from FILE (a --record dump), or the built-in samples without FILE")
    mode.add_argument("--candidates", action="store_true", help="LIVE, read-only: ask the adapter")
    ap.add_argument("--date", type=date.fromisoformat, help="the run date (default: next posting day, ET)")
    ap.add_argument("--record", type=Path, help="with --candidates: save the records to this JSON file")
    ap.add_argument("--logos", action="store_true", help="with --candidates: fetch each company's logo (FMP)")
    ap.add_argument("--logos-dir", type=Path, help="local logos <SYMBOL>.png|jpg to draw (fixture mode)")
    ap.add_argument("--render", action="store_true", help="also render video + image (venv_marketing)")
    ap.add_argument("--out", type=Path, help="output root (default: marketing/out/news-preview/<stamp>)")
    args = ap.parse_args(argv)

    today = datetime.now(ET).date()
    out_root = args.out or (OUT_ROOT / datetime.now(ET).strftime("%Y%m%d-%H%M%S"))
    logos_in: Optional[Path] = args.logos_dir
    sample = False
    #: the run date each series is composed for (one date unless built-in samples pick their own days)
    dates: Dict[str, date] = {}
    if args.candidates:
        run_date = args.date or next_posting_day(today)
        plan = selection.plan_for(run_date)
        series_list = args.series or [s for s in plan.chain if s in SHIPPED_SERIES]
        if args.logos:
            logos_in = out_root / "fetched-logos"
        print(f"# Live candidates for {run_date:%a %Y-%m-%d} ({plan.reason}): "
              f"{', '.join(series_marked(series_list)) or 'none'}\n")
        found = asyncio.run(live_candidates(series_list, run_date, logos_dir=logos_in))
        by_series = {s: v["records"] for s, v in found.items()}
        for s, v in found.items():
            print(f"## {s}: {len(v['records'])} record(s) · skip={v['skip_reason']} · error={v['error']}\n"
                  f"   rejections={json.dumps(v['rejections'])}")
        if args.record:
            save_records(args.record, run_date, by_series)
            print(f"\nrecords saved → {args.record}")
    else:
        if args.fixture:
            run_date, by_series = load_records(Path(args.fixture))
            run_date = args.date or run_date
            if args.series:
                by_series = {s: v for s, v in by_series.items() if s in args.series}
        else:
            sample = True
            series_list = args.series or list(SHIPPED_SERIES)
            by_series = {}
            for s in series_list:
                dates[s] = args.date or sample_day(s, today)
                by_series[s] = sample_records(s, dates[s])
                print(f"- {s}: sample day {dates[s]:%a %Y-%m-%d}")
            run_date = args.date or next_posting_day(today)
    print(f"\nOutput → {out_root}\n")
    ok = True
    for s, recs in by_series.items():
        for i, rec in enumerate(recs, 1):
            ok &= write_one(rec, dates.get(s, run_date), out_root / f"{s}-{i}", logos_in=logos_in, sample=sample,
                            do_render=args.render)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
