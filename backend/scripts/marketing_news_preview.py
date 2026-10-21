#!/usr/bin/env python3
"""
marketing_news_preview.py — read a Company Weekly day before it goes live (drop 2a, contract D16).

Composes each news record as a template day stores it (`script_service._compose_candidate`'s steps: the
record's JSON round trip, its fact sheet, `news_templates.compose`, `revalidate`, the logos, the REAL
write-time freeze `MarketingScriptService._frozen_output` — the post formats, the video features and the
extra video posts — `revalidate` as stored, `worker_script`) and writes, per record, under
`backend/marketing/out/news-preview/<stamp>/<series>-<n>/` (gitignored):

    post.md        what a reviewer reads: headline, narration, cards, image text, every caption — the
                   extra VIDEO posts' captions (Facebook / LinkedIn / Threads) and the video features
                   included — and the switches it was frozen with
    script.json    the worker's script, exactly as the run would send it
    logos/         the logos the worker would draw, named by their bucket content address
    (--render)     preview.mp4 + post_image.jpg + report.json from the worker's own preview
                   (`python -m marketing.preview … --logos-dir … --phonemes`, venv_marketing)

The freeze reads its switches the way production does, at write time — but from THIS environment
(backend/.env, or a variable set on the command line, which wins): MARKETING_IMAGE_PLATFORMS,
MARKETING_X_IMAGES, MARKETING_VIDEO_FEATURES and MARKETING_VIDEO_EXTRA_PLATFORMS. To read a day as the
Railway web service would store it, set them as production has them, e.g.
`MARKETING_VIDEO_EXTRA_PLATFORMS=facebook,linkedin MARKETING_VIDEO_FEATURES=data_card,push_in
./venv/bin/python scripts/marketing_news_preview.py --fixture`. Two things are fixed, as production runs
them: image posts are on (MARKETING_IMAGE_POSTS, live since 2026-10-15), and the worker holding the run is
today's (`PreviewHolder`: every capability the current worker declares).

It writes NOTHING else (but the --record FILE you name): no marketing_runs / marketing_scripts row, no
bucket object, no post, no cache row — enforced, not just intended: every mode runs under
`supabase_read_only_guard` (below).

Usage (from backend/):
    ./venv/bin/python scripts/marketing_news_preview.py --series ceo_buys --fixture            # built-in samples
    ./venv/bin/python scripts/marketing_news_preview.py --candidates                          # next posting day
    ./venv/bin/python scripts/marketing_news_preview.py --candidates --date 2026-11-16 --series ceo_buys \\
        --record marketing/out/news-preview/day.json --logos --render
    ./venv/bin/python scripts/marketing_news_preview.py --fixture marketing/out/news-preview/day.json --render

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
is FMP and Supabase READS with the app's own keys (backend/.env). Already-posted items are NOT excluded (a
real run skips them). --logos fetches each company's FMP logo (`fetch_logo` + `logo_check`) into the
output folder — never the bucket. --record FILE saves the records to FILE (its folder created) for a
later --fixture FILE replay; the examples name one under marketing/out/, which is gitignored — any other
path is a file left wherever you named it (from backend/, `--record day.json` is backend/day.json).

The read-only guard: the services the adapter reuses refresh PRODUCTION cache rows as a side effect of a
read — company facts, revenue breakdown and profit power (company_profile_cache, revenue_breakdown_cache,
profit_power_cache) and, for the 13F series' split check, corporate actions (corporate_action_cache) —
so before anything else runs, `main` patches httpx's `Client.send` and `AsyncClient.send` (the HTTP layer
every Supabase sub-client and the FMP client send through) to refuse every request to the Supabase host
(SUPABASE_URL's, and any *.supabase.co / .in) that is not GET or HEAD: by HOST, never by table, so a
writer missing from that list is refused too. A refusal raises `SupabaseWriteRefused`, a RuntimeError
those services already treat as a failed cache write, and logs one WARNING naming the method and the
path (never a header, so never a key); the run ends with a count of what was refused. A Ctrl-C ends it
with the guard still INSTALLED until the process exits (one WARNING says so): a cache write a worker
thread still had pending is refused, never sent. No host in SUPABASE_URL → exit 2, never an unguarded
run. The --render child (venv_marketing) is another process, outside the guard: the worker's own
preview, which reads logos from the local folder and makes no Supabase call.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import logging
import subprocess
import sys
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.marketing import (  # noqa: E402
    EXTRA_FORMATS_KEY,
    EXTRA_VIDEO_PLATFORMS,
    IMAGE_FALLBACK_REASONS,
    RUN_WORKER_CAPABILITIES_KEY,
    VIDEO_FEATURES_KEY,
    VIDEO_POSTS_KEY,
    WORKER_CAPABILITIES,
)
from app.services.marketing import company_news_rules as R  # noqa: E402
from app.services.marketing import logo_check, news_templates, selection  # noqa: E402
from app.services.marketing.script_service import (  # noqa: E402
    IMAGE_FALLBACK_KEY,
    MarketingScriptService,
    worker_script,
)
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
#: The run id (and generation id) the preview freezes under: log context only — no run row exists.
PREVIEW_RUN_ID = "preview"
#: The write-time switches `MarketingScriptService._frozen_output` reads NOW (this environment's), named in
#: post.md so a reader knows which day it shows (B3-LIVE-3).
FREEZE_SWITCHES: Tuple[str, ...] = ("MARKETING_IMAGE_PLATFORMS", "MARKETING_X_IMAGES", "MARKETING_VIDEO_FEATURES",
                                    "MARKETING_VIDEO_EXTRA_PLATFORMS")
#: A fixed name, like the other scripts' loggers (`__name__` is "__main__" when this runs as a script).
logger = logging.getLogger("marketing_news_preview")


# ── the read-only guard ─────────────────────────────────────────────────────────

#: The only methods the guard lets through to Supabase: reads. PostgREST writes are POST (insert,
#: upsert), PATCH (update) and DELETE; Storage writes are POST / PUT. Everything that is not on this
#: list is refused, OPTIONS and junk included — and so are the two READS that travel as POST, an RPC
#: and a Storage listing: the adapter's path calls neither (checked 2026-10-15), and one added later
#: degrades that read loudly (a WARNING per refusal) rather than letting a write through.
READ_ONLY_METHODS = frozenset({"GET", "HEAD"})
#: A host under one of these is Supabase too (another project, a storage subdomain): its writes are
#: refused as well — a stricter net than SUPABASE_URL's host alone, never a looser one.
SUPABASE_HOST_SUFFIXES: Tuple[str, ...] = (".supabase.co", ".supabase.in")
#: A refused path is logged and counted at most this long (PostgREST's is /rest/v1/<table>).
GUARD_PATH_CHARS = 200


class SupabaseWriteRefused(RuntimeError):
    """A request the read-only guard refused: anything but GET/HEAD to the Supabase host.

    A RuntimeError on purpose, never an httpx error: every cache writer the adapter reaches
    (`company_facts_service._merge_write`, the revenue / profit `_upsert_supabase_cache_safe`,
    `corporate_actions_service._db_put`) wraps its write in ``except Exception`` and logs it as a failed
    best-effort write, the answer already in hand;
    an httpx transport type would instead read as a transient blip to
    `supabase_errors.is_transient_supabase_error` and be retried. The message names the method and the
    path only — never a header, so never a key."""


def _norm_host(host: Any) -> str:
    """Lower case, no surrounding space, no trailing root dot ("x.supabase.co." is the same host)."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    return str(host or "").strip().rstrip(".").lower()


def supabase_host(url: Optional[str] = None) -> str:
    """The host the guard protects: SUPABASE_URL's (this environment's settings unless `url` is given),
    normalized. ValueError when there is none — a guard that cannot name the host protects nothing, so
    the preview refuses to run rather than run unguarded. The URL itself is never echoed."""
    if url is None:
        from app.config import settings

        url = settings.SUPABASE_URL
    try:
        host = _norm_host(httpx.URL(str(url or "")).host)
    except Exception as e:  # noqa: BLE001 — any URL httpx cannot parse names no host
        raise ValueError(f"SUPABASE_URL does not parse as a URL ({type(e).__name__})") from e
    if not host:
        raise ValueError("SUPABASE_URL names no host (expected https://<project>.supabase.co)")
    return host


class ReadOnlyGuard:
    """The check `supabase_read_only_guard` puts in front of every httpx request, and its tally."""

    def __init__(self, host: str) -> None:
        self.host = _norm_host(host)
        if not self.host:
            raise ValueError("the read-only guard needs the Supabase host")
        #: "METHOD /path" → times refused. Locked: the sync writes arrive from worker threads
        #: (`asyncio.to_thread`), several at once.
        self.refused: Counter = Counter()
        self._lock = threading.Lock()

    def covers(self, host: Any) -> bool:
        """True for the Supabase host (any case, a trailing dot) and every *.supabase.co / .in host."""
        h = _norm_host(host)
        return bool(h) and (h == self.host or h.endswith(SUPABASE_HOST_SUFFIXES))

    def check(self, request: Any) -> None:
        """Return for a read, or for a request to another host; for a write to Supabase, log one WARNING
        and raise `SupabaseWriteRefused`. A method that cannot be read is not a read: refused."""
        method = getattr(request, "method", "")
        if isinstance(method, bytes):
            method = method.decode("ascii", "replace")
        method = str(method or "").strip().upper()
        if method in READ_ONLY_METHODS:
            return
        url = getattr(request, "url", None)
        if not self.covers(getattr(url, "host", "")):
            return
        path = " ".join(str(getattr(url, "path", "") or "/").split())[:GUARD_PATH_CHARS]
        what = f"{method or '?'} {path}"
        with self._lock:
            self.refused[what] += 1
        logger.warning("marketing preview is READ-ONLY: refused %s on Supabase — a write; the caller sees "
                       "a failed write (a cache refresh skipped), nothing reaches the database", what)
        raise SupabaseWriteRefused(f"marketing_news_preview is read-only: {what} to Supabase refused")

    def summary(self) -> str:
        if not self.refused:
            return "Read-only guard: no Supabase write was attempted."
        rows = "\n".join(f"  {what} ×{n}" for what, n in sorted(self.refused.items()))
        return (f"Read-only guard: refused {sum(self.refused.values())} Supabase write(s) — the cache "
                f"refreshes a live read would make; nothing was written:\n{rows}")


@contextmanager
def supabase_read_only_guard(host: Optional[str] = None) -> Iterator[ReadOnlyGuard]:
    """Run the body with every httpx request checked by a `ReadOnlyGuard` for `host` (default:
    `supabase_host()`, so ValueError before anything is patched when SUPABASE_URL names none).

    WHY the HTTP layer: the writes happen deep inside services, through supabase-py → postgrest →
    `httpx.Client`, behind bindings that differ per module (`company_facts_service._db` imports
    `get_supabase` inside the function, the revenue / profit services hold the client they built in
    `__init__`) — patching a service or one `get_supabase` binding would miss the next writer
    (testing.md's binding trap). Every Supabase sub-client (postgrest, storage, auth) and the FMP client
    send through these two methods, patched on the CLASS so clients built before the guard (the module
    singletons) are covered too.

    Restored when the body returns or raises an ordinary error (an `Exception`): `main` holds the guard
    around `asyncio.run`, which has drained its default executor before either, so no write the body
    started is still pending. Any BaseException that is NOT an Exception (Ctrl-C, SystemExit) leaves the
    guard INSTALLED, with one WARNING: the process is ending, and that is exactly when a write can still
    be pending (review PREVIEW-GUARD-1) — Python 3.11's `asyncio.run` puts the default SIGINT handler back
    BEFORE `close()` drains the executor, so a Ctrl-C during the drain escapes it, `loop.close()` shuts
    the executor down without waiting, and the interpreter's exit join lets the thread finish its upsert.
    Tests run `main` in-process; theirs put httpx back themselves."""
    guard = ReadOnlyGuard(supabase_host() if host is None else host)
    orig_sync, orig_async = httpx.Client.send, httpx.AsyncClient.send

    @functools.wraps(orig_sync)
    def send(self: httpx.Client, request: httpx.Request, *args: Any, **kwargs: Any) -> httpx.Response:
        guard.check(request)
        return orig_sync(self, request, *args, **kwargs)

    @functools.wraps(orig_async)
    async def send_async(self: httpx.AsyncClient, request: httpx.Request, *args: Any,
                         **kwargs: Any) -> httpx.Response:
        guard.check(request)
        return await orig_async(self, request, *args, **kwargs)

    httpx.Client.send = send  # type: ignore[method-assign]
    httpx.AsyncClient.send = send_async  # type: ignore[method-assign]
    # False until the body ended in a way that leaves no write pending behind it — set on a return or an
    # ordinary error, never by a Ctrl-C landing anywhere in between (a second press included).
    restore = False
    try:
        yield guard
        restore = True
    except Exception:
        restore = True
        raise
    except BaseException as e:
        logger.warning("marketing preview interrupted (%s): the read-only guard stays installed until this "
                       "process exits — a cache write still pending in a worker thread is refused (a WARNING "
                       "each), never sent", type(e).__name__)
        raise
    finally:
        if restore:
            httpx.Client.send = orig_sync  # type: ignore[method-assign]
            httpx.AsyncClient.send = orig_async  # type: ignore[method-assign]


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
        # Both carry a revenue pair: since Batch 1 a report without one is refused whole (the adapter's
        # `revenue_implausible`, the template's `implausible_figures`). Tailspin keeps no quarter end — the
        # "reported on <date>" shape the period contingency (`EARNINGS_ALLOW_SCALE_CHECK`) would publish.
        report = run_date - timedelta(days=2)
        return [R.EarningsReport(series="earnings", company=_co("northwind"), report_date=report,
                                 period_end=_quarter_end_before(report), eps_actual=-0.05, eps_estimate=-0.12,
                                 revenue_actual=551_900_000.0, revenue_estimate=543_600_000.0),
                R.EarningsReport(series="earnings", company=_co("tailspin"),
                                 report_date=run_date - timedelta(days=4), period_end=None, eps_actual=1.42,
                                 eps_estimate=1.3, revenue_actual=1_284_600_000.0, revenue_estimate=1_251_300_000.0)]
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


class PreviewHolder:
    """The `runs` the real write-time freeze (`MarketingScriptService._frozen_output`) reads its ONE row from —
    the run as its holder's claim left it — answered without a database: the holder is TODAY's worker, every
    capability it declares (`schemas.WORKER_CAPABILITIES`, pinned equal to marketing/main.py's), so the formats,
    video features and extra video posts freeze as on a run the current worker holds."""

    async def get_run(self, run_id: str) -> Dict[str, Any]:
        return {"id": run_id, "worker_version": "preview", "attempts": 1,
                "metadata": {RUN_WORKER_CAPABILITIES_KEY: list(WORKER_CAPABILITIES)}}


def freeze_switches() -> Dict[str, str]:
    """FREEZE_SWITCHES as this environment holds them — what `compose_stored`'s freeze just read."""
    from app.config import settings

    return {name: str(getattr(settings, name)) for name in FREEZE_SWITCHES}


def compose_stored(rec: Any, run_date: date, *, logos_in: Optional[Path], logos_out: Path,
                   store_state: str = "live", allow_x_url: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(stored output, worker script) of one record — PreviewRefused when a real day would refuse it.

    The freeze is production's own (`_compose_candidate` → `MarketingScriptService._frozen_output`, B3-LIVE-3):
    the post formats under MARKETING_IMAGE_PLATFORMS / MARKETING_X_IMAGES, the video features
    (MARKETING_VIDEO_FEATURES) and the extra video posts (MARKETING_VIDEO_EXTRA_PLATFORMS, re-checked against the
    record's fact sheet), every switch read NOW from this environment, image posts on, the holder today's worker
    (`PreviewHolder`)."""
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
    svc = MarketingScriptService(runs=PreviewHolder())   # type: ignore[arg-type] — the one method the freeze reads
    out = asyncio.run(svc._frozen_output(out, run_date, run_id=PREVIEW_RUN_ID, gen_id=PREVIEW_RUN_ID,
                                         image_posts=True, fact_sheet=sheet))
    stored = json.loads(json.dumps(out, allow_nan=False))
    problems = news_templates.revalidate(stored, fact_sheet=sheet, run_date=run_date)
    if problems:
        raise PreviewRefused(f"re-check as stored failed: {problems[:3]}")
    return stored, worker_script(stored, run_id=PREVIEW_RUN_ID)


# ── what a reviewer reads ──────────────────────────────────────────────────────

def post_markdown(stored: Dict[str, Any], *, series: str, ref: str, run_date: date, sample: bool,
                  switches: Optional[Dict[str, str]] = None) -> str:
    """The reviewer's page: every post the day would record — the PRIMARY posts (`posts`, each in its frozen
    format) and the EXTRA video posts (`video_posts`, decided with the video) — the frozen video features, a
    picture fallback, and (`switches`) the write-time switches the freeze read."""
    lines: List[str] = [f"# {series} · {run_date:%a %b %-d, %Y}", ""]
    if sample:
        lines += ["> **SAMPLE DATA** — fictional companies, invented figures. Never post this.", ""]
    lines += [f"`{ref}` · template {stored.get('prompt_version') or news_templates.TEMPLATE_VERSION}", ""]
    if switches:
        lines += ["_Frozen as a template day stores it, with this environment's switches: "
                  + ", ".join(f"{k}={v if v else '(empty)'}" for k, v in switches.items())
                  + " · image posts on · the worker holding the run is today's._", ""]
    features = stored.get(VIDEO_FEATURES_KEY) or []
    lines += ["## Video", "",
              f"**Video features:** {', '.join(map(str, features)) if features else 'none (the video as before)'}", "",
              f"**Hook (spoken over the opening card):** {stored.get('hook')}", ""]
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
    extras = stored.get(VIDEO_POSTS_KEY) or {}
    # A platform the switch lists that the freeze did not keep is named, never silently missing (`freeze_extra_formats`:
    # a Threads caption over its byte limit, a Form 4 roundup whose frozen video features lack the data card
    # (`picture_caption`, B3-LIVE-1), or a caption that does not re-derive or fails its re-check — WARNING).
    listed = {p.strip() for p in str((switches or {}).get("MARKETING_VIDEO_EXTRA_PLATFORMS") or "").split(",")}
    missing = [p for p in EXTRA_VIDEO_PLATFORMS if p in listed and p not in extras]
    if extras or missing:
        extra_formats = stored.get(EXTRA_FORMATS_KEY) or {}
        order = ([p for p in EXTRA_VIDEO_PLATFORMS if p in extras]
                 + sorted(p for p in extras if p not in EXTRA_VIDEO_PLATFORMS))
        lines += ["## Extra video posts", "",
                  "_The day's video, posted again on these platforms with their own caption "
                  "(MARKETING_VIDEO_EXTRA_PLATFORMS) — decided in the VIDEO approval._", ""]
        if missing:
            lines += [f"_Listed but NOT frozen today: {', '.join(missing)} — a Threads caption over its byte limit, "
                      "a Form 4 roundup without the data card in its video (picture_caption: its caption speaks of "
                      "the picture's rows), or a caption that fails its re-check (a WARNING on stderr names the "
                      "codes)._", ""]
        for platform in order:
            post = extras.get(platform) or {}
            lines += [f"### {platform} ({extra_formats.get(platform, 'video')}, extra)", "",
                      "```", str(post.get("caption") or "").strip(), "```", ""]
    fallback = stored.get(IMAGE_FALLBACK_KEY)
    if isinstance(fallback, dict) and fallback:
        reason = str(fallback.get("reason"))
        lines += ["## Picture not used", "",
                  f"{IMAGE_FALLBACK_REASONS.get(reason, reason)} ({reason}) — posted as text: "
                  f"{', '.join(map(str, fallback.get('platforms') or [])) or '?'}", ""]
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
                                                  sample=sample, switches=freeze_switches()), encoding="utf-8")
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
    """The --record dump, written to the file named, its folder created: the documented one sits under
    the gitignored marketing/out/news-preview/, which a fresh checkout lacks — and this runs only after
    the whole live fetch. Anything in the way (a FILE where the folder should be) raises."""
    path.parent.mkdir(parents=True, exist_ok=True)
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

    # The guard goes on BEFORE anything else runs — the adapter, its lazy import, a logo fetch — and
    # covers every mode; without a Supabase host to protect, nothing runs at all.
    try:
        host = supabase_host()
    except ValueError as e:
        print(f"refused: {e} — the preview never runs without its read-only guard", file=sys.stderr)
        return 2
    with supabase_read_only_guard(host) as guard:
        code = _run(args)
    if args.candidates or guard.refused:
        print("\n" + guard.summary())
    return code


def _run(args: argparse.Namespace) -> int:
    """`main`'s work, under the read-only guard."""
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
        # `asyncio.run`, never a bare loop: it drains the default executor before it returns, so a cache
        # write a service fires and forgets (profit / revenue: `run_in_executor(None, …)`; company facts:
        # `ensure_future(to_thread(…))`) is still attempted INSIDE the guard. `loop.close()` does not wait
        # for that executor — a write landing after `main` left the guard would reach production. A Ctrl-C
        # during that drain escapes it with the thread alive: the guard then stays installed until the
        # process exits (`supabase_read_only_guard`), so that write is refused all the same.
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
