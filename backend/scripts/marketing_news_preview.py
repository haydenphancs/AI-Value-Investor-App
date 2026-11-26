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
    ./venv/bin/python scripts/marketing_news_preview.py --fixture --lane spotlight                # spotlight calendar
    ./venv/bin/python scripts/marketing_news_preview.py --candidates --lane spotlight --date 2026-11-19
    ./venv/bin/python scripts/marketing_news_preview.py --candidates --lane spotlight --series insider_spotlight \\
        --logos --art --render                                                  # SPENDS MONEY: per-post art

--art (workstream C, 2026-11-16) SPENDS MONEY — about $0.15 to $0.45 per record (one or two drawings at the image
model's price, $0.134 each at 2K, plus the brief and the picture check on this environment's Claude key) — and does so
ONLY with this explicit flag; without it the preview makes no model call at all and its output is the one of before.
Owner decision 2026-11-20 ("Drop it from insider posts (Recommended) :: Keep the code but use it nowhere for now"): NO
shipped series carries art (`post_art.ART_SERIES` is empty), so today --art finds no art subject and spends nothing; it
is kept for the day the owner turns art back on for a series. Then: for each composed record that pictures ONE company
(`post_art.art_subject_for`: the series put back into the art set — never an insider post, a 13F list, a Congress
Count, an earnings day, the stakes list or a theme) on a day that has a video post for it, it runs the build's own art step
(`post_art.make_post_art`: brief → draw → check → at most one redraw) with a LOCAL store: the checked picture is written
to `<series>-<n>/art.jpg` and the art record (subject, treatment, prompt hash, checks, usage — the server-side record,
which names its models) to `art.json`; post.md gets an "Art" block (no model named), and --render passes the picture
to the worker's preview as `--art-file` (drawn on the video's opening card; a `feature` picture would draw it on its
band, but no series composes one any more). It never
writes the bucket or the ledger (still under the read-only guard below), and it is not gated by MARKETING_ART_ENABLED,
the monthly cap (MARKETING_ART_MONTHLY_BUDGET_USD) or a worker capability: the flag is the owner's own spend. Records
with no art subject, or on a text-only day, spend nothing (art.json says why). Built-in SAMPLE records (fictional
companies) get no sector or industry facts; live or --fixture FILE records read them from FMP (`company_facts`).

--lane spotlight (migration 210, 2026-11-15) previews the SPOTLIGHT lane — the second, morning run on
MARKETING_SPOTLIGHT_WEEKDAYS — instead of the main lane: its calendar is `selection.spotlight_plan_for` (single-company
series only, never a weekly roundup, never the lesson: a day where nothing qualifies SKIPS), the default --date is the
next spotlight day, and only the shipped series a spotlight chain can hold are offered (a --series the lane never runs is
refused, exit 2). Its chain is the build's (`selection.spotlight_chain`, C0 2026-11-16): a series the MAIN lane's chain
holds that day — Money Map on every main posting day but Saturday — never enters it (the per-series switch is not
consulted: every shipped series counts, what COULD run). It also simulates the CROSS-LANE EXCLUSION a real build
applies (`script_service._cross_lane`, run here as is): a record the other lane posted that day is excluded, a series
the other lane used that day falls back before the adapter is asked, and a record whose LEAD company (its issuer root
or its display name, `script_service.lead_keys`) a lane drew within CROSS_LANE_COMPANY_DAYS ET days is excluded
(EXCLUDED.txt, never a post). The ledger it is judged against is --ledger FILE (a JSON list of
`run_service.lane_scripts_between` rows — `{run_id, run_date, lane, source_ref, template_id, output}` — or
`{"rows": [...]}`), else, with --candidates, the LIVE ledger, read-only (the same read the build makes, a GET under
the guard below); a fixture run without --ledger simulates nothing and says so. The previewed day's own row of its lane
is left out of the ledger (a build runs before it exists). The switch itself (MARKETING_SPOTLIGHT_ENABLED) is not
consulted: a preview shows what the lane WOULD post. --lane main is today's preview, byte for byte, unless --ledger is
given (then the main lane's side of the exclusion is simulated against it).

--fixture without a file uses BUILT-IN SAMPLE records about FICTIONAL companies (Contoso, Fabrikam, …) on
symbols that are no listing (ZQCT, ZQFB, … — `SAMPLE_COMPANIES`), labelled "SAMPLE DATA" in post.md (the
rendered image and video carry no label) — every figure is invented. Their logos are wordmarks. Without --date
each series is previewed on the next day its calendar runs it (`sample_day`: a Congress Count needs its
Tuesday, 13F Season its season, earnings its season, the weekly theme post its Wednesday — the main lane's preview
calendar is MARKETING_POST_WEEKDAYS plus Wednesday for it, `theme_preview_weekdays`); --date pins one date for every
series.
The theme's built-in samples are its five angles (news-v23 phase 3): "how they make money" (the grid), "who's biggest"
(the ranked list, by an invented market value), "growth + profit" (the figures table), the insider scorecard (the
ledger) and Congress (the tally).

Every shipped series can be previewed — the drop-2b four (congress_count, company_stakes, earnings,
theme_explainer) included, although production runs them only once MARKETING_NEWS_SERIES lists them
(--candidates marks a series that is off there). The spotlight lane's two single-company series (news-v22,
insider_spotlight and thirteen_f_spotlight) are previewed on --lane spotlight only — the main lane never runs them
(asking for one there is refused, exit 2). Their --candidates read starts the insider spotlight's window at
run_date − 7 and does not apply its 14-day cooldown (a real build reads both from the ledger; the preview says so).

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
import hashlib
import json
import logging
import subprocess
import sys
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterator, List, NamedTuple, Optional, Sequence, Tuple

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.marketing import (  # noqa: E402
    EXTRA_FORMATS_KEY,
    EXTRA_VIDEO_PLATFORMS,
    IMAGE_FALLBACK_REASONS,
    RUN_LANE_MAIN,
    RUN_LANE_SPOTLIGHT,
    RUN_LANES,
    RUN_WORKER_CAPABILITIES_KEY,
    VIDEO_FEATURES_KEY,
    VIDEO_POSTS_KEY,
    WORKER_CAPABILITIES,
)
from app.services.marketing import company_news_rules as R  # noqa: E402
from app.services.marketing import logo_check, news_templates, post_art, selection  # noqa: E402
from app.services.marketing.script_service import (  # noqa: E402
    CROSS_LANE_COMPANY_DAYS,
    IMAGE_FALLBACK_KEY,
    MarketingScriptService,
    _art_day_surfaces,
    image_platforms_setting,
    lead_company,
    lead_keys,
    narrow_other_lane_lead,
    template_picture_outlets,
    worker_script,
)
from app.services.trillion_club.rules import is_federal_business_day  # noqa: E402
from app.utils.market_hours import ET  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
OUT_ROOT = BACKEND / "marketing" / "out" / "news-preview"
WORKER_PYTHON = BACKEND / "venv_marketing" / "bin" / "python"
#: Mirrors marketing/preview.py PREVIEW_BUCKET_HOST (the worker never imports app.*; pinned equal by
#: tests/test_marketing_news_preview.py).
PREVIEW_BUCKET_HOST = "preview.local"
PREVIEW_PUBLIC_PREFIX = f"https://{PREVIEW_BUCKET_HOST}/storage/v1/object/public/marketing-media/logos/"
#: The series a preview can compose: every shipped series (`selection.SHIPPED_SERIES`: the eight of drop 2b and,
#: since news-v22, the spotlight lane's two), in the calendar registry's order.
SHIPPED_SERIES: Tuple[str, ...] = tuple(s.id for s in selection.SERIES if s.id in selection.SHIPPED_SERIES)
#: The shipped series the MAIN lane's calendar runs (`selection.plan_for`): every one but the spotlight pair.
MAIN_SERIES: Tuple[str, ...] = tuple(s for s in SHIPPED_SERIES if s not in selection.SPOTLIGHT_SERIES)
#: How far ahead `sample_day` looks for a day whose calendar runs a series (> a year: every slot recurs).
SAMPLE_DAY_HORIZON_DAYS = 400
CANDIDATES_LIMIT = 5
CANDIDATES_BUDGET_SECONDS = 180.0
LOGO_TIMEOUT_SECONDS = 10.0
#: The run id (and generation id) the preview freezes under: log context only — no run row exists.
PREVIEW_RUN_ID = "preview"
#: The lanes a preview shows (`schemas.RUN_LANES`, migration 210): the main lane's calendar (`selection.plan_for`) or
#: the spotlight lane's (`selection.spotlight_plan_for`).
LANES: Tuple[str, ...] = RUN_LANES
#: How far ahead the next spotlight day is looked for (a week holds every listed weekday).
NEXT_SPOTLIGHT_DAY_HORIZON_DAYS = 7
#: The write-time switches `MarketingScriptService._frozen_output` reads NOW (this environment's) — and the one the
#: build reads at compose, MARKETING_FIRST_LINE_PREFIX (news-v22, `compose_stored`) — named in post.md so a reader
#: knows which day it shows (B3-LIVE-3).
FREEZE_SWITCHES: Tuple[str, ...] = ("MARKETING_IMAGE_PLATFORMS", "MARKETING_X_IMAGES", "MARKETING_VIDEO_FEATURES",
                                    "MARKETING_VIDEO_EXTRA_PLATFORMS",
                                    # news-v22: the first-line prefixes, read once per build like production's
                                    "MARKETING_FIRST_LINE_PREFIX")
#: --art (workstream C): what one record TYPICALLY costs, in dollars (one or two drawings + the brief and check calls)
#: — the docstring and the console's warning say it. The worst case one record can reach (every retry billed,
#: `post_art.run_worst_micros`, about $1.91 at the defaults) is printed beside it; the build's monthly cap reserves
#: that, the preview has no cap (its flag is the owner's own spend).
ART_COST_PER_RECORD_USD: Tuple[float, float] = (0.15, 0.45)
#: --art writes these two files into the record's folder, and nothing anywhere else.
ART_FILE = "art.jpg"
ART_RECORD_FILE = "art.json"
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


def _business_day_before(d: date) -> date:
    """The latest federal business day strictly before `d` (the SEC's calendar)."""
    d -= timedelta(days=1)
    while not is_federal_business_day(d):
        d -= timedelta(days=1)
    return d


def _quarter_end_before(d: date) -> date:
    """The latest calendar quarter end strictly before `d`."""
    end_month = ((d.month - 1) // 3) * 3
    if end_month == 0:
        return date(d.year - 1, 12, 31)
    return date(d.year, end_month + 1, 1) - timedelta(days=1)


#: What the preview narrows a chain with (C0, 2026-11-16): every class and every shipped series — the switches are
#: not consulted, a preview shows what COULD run.
_ALL_CLASSES = frozenset({"A", "C", "F"})


def theme_preview_weekdays() -> Tuple[int, ...]:
    """The main lane's calendar a THEME preview walks: MARKETING_POST_WEEKDAYS (read now) plus WEDNESDAY — the weekly
    theme post's only day (news-v23 phase 3, `selection.THEME_WEEKDAY`), which production posts once its calendar lists
    "wed" (documents/OWNER_TASKS.md). A preview shows what COULD run, as it does for the per-series switch; every
    other series' preview walks the calendar as it is."""
    return tuple(sorted(set(selection.post_weekdays()) | {selection.THEME_WEEKDAY}))


def plan_of(d: date, lane: str = RUN_LANE_MAIN, *, series: Optional[str] = None) -> "selection.DayPlan":
    """The day's plan on `lane`'s calendar: the main lane's weekday calendar (`selection.plan_for`; for the theme
    `series`, on `theme_preview_weekdays`) or the spotlight lane's (`selection.spotlight_chain`, on
    MARKETING_SPOTLIGHT_WEEKDAYS — its switch is not consulted: a preview shows what the lane WOULD post — WITHOUT the
    series the main lane's chain holds that day, as the build walks it: C0, 2026-11-16). ValueError for an unknown
    lane."""
    if lane == RUN_LANE_MAIN:
        return selection.plan_for(d, theme_preview_weekdays() if series == "theme_explainer" else None)
    if lane == RUN_LANE_SPOTLIGHT:
        got = selection.spotlight_chain(d, classes=_ALL_CLASSES, news_series=selection.SHIPPED_SERIES)
        if got.plan.rest_day:
            return got.plan
        return selection.DayPlan(False, got.chain, got.plan.reason)
    raise ValueError(f"unknown lane {lane!r} (lanes: {', '.join(LANES)})")


def main_lane_holds(series: str, d: date) -> bool:
    """C0 (2026-11-16): does the MAIN lane's chain on `d` hold `series` (every class and shipped series — the switches
    not consulted)? Such a series never enters that day's spotlight chain (`selection.spotlight_chain`): a spotlight
    preview leaves it out, as the build would."""
    return series in selection.main_lane_series(d, _ALL_CLASSES, selection.SHIPPED_SERIES)


def spotlight_chain_series(start: date) -> Tuple[str, ...]:
    """The shipped series a spotlight chain can hold (any weekday, any season, within SAMPLE_DAY_HORIZON_DAYS of
    `start`), in the calendar registry's order — what a spotlight preview offers: Money Map until the single-company
    spotlight series ship. Never a weekly roundup, never the lesson (`selection.spotlight_plan_for`)."""
    seen: set = set()
    for i in range(SAMPLE_DAY_HORIZON_DAYS):
        seen.update(selection.spotlight_plan_for(start + timedelta(days=i), weekdays=range(7)).chain)
    return tuple(s for s in SHIPPED_SERIES if s in seen)


def sample_day(series: str, start: date, *, lane: str = RUN_LANE_MAIN) -> date:
    """The first day on or after `start` whose calendar (`lane`'s: `plan_of`) runs `series`: the day a sample of it
    is previewed on when no --date is given."""
    for i in range(SAMPLE_DAY_HORIZON_DAYS):
        d = start + timedelta(days=i)
        if series in plan_of(d, lane, series=series).chain:
            return d
    raise ValueError(f"no day runs {series!r} within {SAMPLE_DAY_HORIZON_DAYS} days of {start}"
                     + (" on the spotlight lane (is MARKETING_SPOTLIGHT_WEEKDAYS empty?)"
                        if lane == RUN_LANE_SPOTLIGHT else ""))


def sample_records(series: str, run_date: date) -> List[Any]:
    """Fictional records for `series`, dated for `run_date` (a Monday for the insider series, a
    Tuesday in 13F season for thirteen_f, the month's Congress Tuesday for congress_count — day 7 or
    later, a spotlight day for the spotlight pair (the 13F one in its season), any day for the others;
    `sample_day` finds one)."""
    start = run_date - timedelta(days=7)
    d = [start + timedelta(days=i) for i in range(7)]
    if series == "insider_spotlight":
        # owner decisions 2026-11-20 ("3 to 5 :: at least 3 companies, at most 5 (the 5 largest purchases when more
        # qualify)"): the insider LIST — three companies (Contoso's CEO with two more of its insiders, Fabrikam's CFO,
        # Northwind Traders' director held indirectly), and a busy window of seven qualifying companies cut to its FIVE
        # largest, as the adapter cuts it (every one drawn, no "+N more"); each row's own filed cost per share;
        # role-only (a fictional company's insiders are never named, whatever the date)
        # federal business days only: EDGAR accepts no weekend or holiday filing
        filed = _business_day_before(run_date)
        before = _business_day_before(filed)

        def spot(key: str, role: str, amount: float, shares: float, k: int, filing: date, cost: Tuple[float, float],
                 holding: str = "direct") -> R.InsiderPurchase:
            trade = _business_day_before(filing)
            return R.InsiderPurchase(company=_co(key), role=role, person_name=None, amount_usd=amount,
                                     shares=shares, purchases=k, earliest_trade_date=trade, latest_trade_date=trade,
                                     filing_dates=(filing,), holding=holding, amended=False,
                                     cost_per_share_min_usd=cost[0], cost_per_share_max_usd=cost[1])

        def row(lead: R.InsiderPurchase, *others: R.InsiderPurchase) -> R.InsiderSpotlightCompany:
            return R.InsiderSpotlightCompany(lead=lead, others=tuple(others))

        rows = [row(spot("contoso", "ceo", 2_001_650.0, 14_000.0, 2, filed, (142.10, 143.85)),
                    spot("contoso", "director", 410_002.0, 2_900.0, 1, before, (141.38, 141.38)),
                    spot("contoso", "cfo", 250_008.0, 1_760.0, 1, filed, (142.05, 142.05))),
                row(spot("fabrikam", "cfo", 612_500.0, 25_000.0, 2, filed, (24.10, 24.90))),
                row(spot("northwind", "director", 450_000.0, 18_000.0, 1, before, (25.0, 25.0), holding="indirect")),
                row(spot("tailspin", "ceo", 300_000.0, 10_000.0, 1, filed, (30.0, 30.0))),
                row(spot("woodgrove", "director", 240_000.0, 6_000.0, 1, filed, (40.0, 40.0))),
                row(spot("adventure", "cfo", 180_000.0, 4_500.0, 1, before, (40.0, 40.0))),
                row(spot("proseware", "director", 120_000.0, 4_800.0, 1, filed, (25.0, 25.0)))]

        def listed(chosen: Sequence[R.InsiderSpotlightCompany]) -> R.InsiderSpotlight:
            return R.InsiderSpotlight(series="insider_spotlight", window_start=before, window_end=filed,
                                      companies=tuple(sorted(chosen, key=R.spotlight_company_order)))

        return [listed(rows[:3]), listed(sorted(rows, key=R.spotlight_company_order)[:R.INSIDER_MAX_ROWS])]
    if series == "thirteen_f_spotlight":
        # news-v23 (owner 2026-11-19): ONE filer's latest 13F as the ONE 13F list — share counts only, two to eight
        # holdings (never one), the largest dollar change first; seven holdings draw six rows and "+1 more change in
        # the caption"
        year = run_date.year if run_date.month > 3 else run_date.year - 1
        q = (run_date.month - 1) // 3 if run_date.month > 3 else 4
        period = f"{year}-Q{q}"
        period_end = R.period_end_of(period)

        def f13(*moves: R.ThirteenFMove, changes: Optional[int] = 23) -> R.ThirteenFSpotlight:
            ordered = tuple(sorted(moves, key=R.thirteen_f_spotlight_order))
            return R.ThirteenFSpotlight(series="thirteen_f_spotlight", filer_name="Litware Capital Management",
                                        filer_cik="0009999901", filer_symbol=None, period=period,
                                        period_end=period_end, filed_on=period_end + timedelta(days=44),
                                        amended_on=None, moves=ordered, eligible_moves=len(ordered),
                                        filing_moves=changes)

        fewer = _move("fabrikam", "decreased", shares=41_900_000.0, prev_shares=60_000_000.0, value_usd=780_000_000.0)
        new = _move("proseware", "newly_reported", shares=1_900_000.0, prev_shares=0.0, value_usd=410_000_000.0)
        more = _move("contoso", "increased", shares=12_000_000.0, prev_shares=8_500_000.0, value_usd=2_100_000_000.0)
        gone = _move("woodgrove", "no_longer_reported", shares=0.0, prev_shares=3_100_000.0,
                     prev_value_usd=290_000_000.0)
        small = _move("tailspin", "decreased", shares=2_000_000.0, prev_shares=3_000_000.0, value_usd=150_000_000.0)
        nw = _move("northwind", "increased", shares=900_000.0, prev_shares=600_000.0, value_usd=60_000_000.0)
        aw = _move("adventure", "newly_reported", shares=400_000.0, prev_shares=0.0, value_usd=30_000_000.0)
        return [f13(fewer, new), f13(fewer, new, more, gone), f13(fewer, new, more, gone, small, nw, aw)]
    if series == "ceo_buys":
        rows = (_buy("contoso", "ceo", "Jordan Avery", 12_400_000.0, 310_000.0, 2, [d[1], d[2]]),
                _buy("fabrikam", "ceo", None, 1_850_000.0, 22_500.0, 1, [d[3]], holding="indirect"),
                _buy("northwind", "ceo", None, 420_000.0, 6_100.0, 1, [d[4]], amended=True))
        # owner decisions 2026-11-20 ("3 to 5 :: at least 3 companies, at most 5 (the 5 largest purchases when more
        # qualify)"): three CEOs, and a six-CEO week cut to its FIVE largest, as the adapter cuts it (every one drawn;
        # the five-row picture moves its note to the caption) — never one company, never more than five
        more = (rows + (_buy("tailspin", "ceo", None, 300_000.0, 10_000.0, 1, [d[4]]),
                        _buy("woodgrove", "ceo", None, 240_000.0, 6_000.0, 1, [d[5]]),
                        _buy("adventure", "ceo", None, 180_000.0, 4_500.0, 1, [d[5]])))[:R.INSIDER_MAX_ROWS]
        return [R.InsiderBuysWeek(series="ceo_buys", window_start=start, window_end=d[6], rows=rows),
                R.InsiderBuysWeek(series="ceo_buys", window_start=start, window_end=d[6], rows=more)]
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
        # a count of distinct members of Congress — never a member's name; the month before the run. news-v23 (the
        # restyle, owner 2026-11-19): ONE post of the month's 2-3 companies, the most members first
        month = selection.congress_disclosure_month(run_date)
        counts = (R.CongressCount(series="congress_count", company=_co("contoso"), month=month, members=4,
                                  fetched_on=run_date),
                  R.CongressCount(series="congress_count", company=_co("fabrikam"), month=month, members=2,
                                  fetched_on=run_date))
        return [R.CongressMonth(series="congress_count", month=month, fetched_on=run_date, counts=counts)]
    if series == "company_stakes":
        # news-v23 (the restyle, owner 2026-11-19: "public only" + "add 1-3 more companies"): ONE post of one investor's
        # stakes in PUBLIC companies (each a listed company the adapter checked is actively trading), the largest
        # figure first; a private investee is never a row (the adapter drops it)
        investor = _co("contoso")
        rows = (R.CompanyStake(series="company_stakes", stake_id="0a1b2c3d-4e5f-4a6b-8c7d-0000000c0de2",
                               investor=investor, investee_name="Proseware", investee=_co("proseware"),
                               kind="us_listed_off_13f", value_usd=1_250_000_000.0, value_basis="fair_value",
                               ownership_pct=None, as_of=_quarter_end_before(run_date),
                               verified_on=run_date - timedelta(days=5), source_title="Contoso 10-Q", background=None,
                               listed_since=None, local_listing=None, is_new=True),
                R.CompanyStake(series="company_stakes", stake_id="0a1b2c3d-4e5f-4a6b-8c7d-0000000c0de1",
                               investor=investor, investee_name="Litware", investee=_co("litware"),
                               kind="on_13f_note", value_usd=640_000_000.0, value_basis="invested",
                               ownership_pct=None, as_of=run_date - timedelta(days=75),
                               verified_on=run_date - timedelta(days=12), source_title="Litware 8-K",
                               background="For Series B shares.", listed_since=None, local_listing=None,
                               is_new=False))
        return [R.CompanyStakes(series="company_stakes", investor=investor,
                                stakes=tuple(sorted(rows, key=R.stake_rank_key)))]
    if series == "earnings":
        # Both carry a revenue pair: since Batch 1 a report without one is refused whole (the adapter's
        # `revenue_implausible`, the template's `implausible_figures`). Tailspin keeps no quarter end — the
        # "reported on <date>" shape the period contingency (`EARNINGS_ALLOW_SCALE_CHECK`) would publish.
        # news-v23 (the restyle, owner 2026-11-19): ONE table of the window's 2-4 companies, the largest first.
        report = run_date - timedelta(days=2)
        reports = (R.EarningsReport(series="earnings", company=_co("tailspin"),
                                    report_date=run_date - timedelta(days=4), period_end=None, eps_actual=1.42,
                                    eps_estimate=1.3, revenue_actual=1_284_600_000.0,
                                    revenue_estimate=1_251_300_000.0),
                   R.EarningsReport(series="earnings", company=_co("northwind"), report_date=report,
                                    period_end=_quarter_end_before(report), eps_actual=-0.05, eps_estimate=-0.12,
                                    revenue_actual=551_900_000.0, revenue_estimate=543_600_000.0))
        return [R.EarningsDay(series="earnings", reports=reports)]
    if series == "theme_explainer":
        # news-v23 phase 3 (the weekly theme post, Wednesdays): angle 1 "how they make money" (the grid), angle 2
        # "who's biggest" (the ranked list, by a dated market value) and — 2026-11-20 — angles 3-5, under their new keys
        fy = str(run_date.year - 1)
        rows = (("contoso", "Robotics systems", 0.62), ("fabrikam", "Automation software", 0.48),
                ("northwind", "Warehouse systems", 0.71), ("tailspin", "Drones", 0.39),
                ("adventure", "Field services", 0.55), ("proseware", "Sensors", None),
                ("woodgrove", None, None), ("litware", "Logistics software", 0.66))
        members = tuple(R.ThemeMember(company=_co(key), top_segment=seg, top_segment_share=share,
                                      fiscal_year=fy if seg else None) for key, seg, share in rows)
        values = (("northwind", 182e9), ("contoso", 96.4e9), ("litware", 41e9), ("fabrikam", 12.7e9),
                  ("adventure", 3.9e9), ("tailspin", 820e6))
        ranked = tuple(R.ThemeMember(company=_co(key), top_segment=None, top_segment_share=None, fiscal_year=None,
                                     market_value_usd=v) for key, v in values)
        return [R.ThemeExplainer(series="theme_explainer", slug="warehouse-robots", title="Warehouse robots",
                                 members=members, tickers_as_of=run_date - timedelta(days=10), angle="make_money",
                                 posted_on=run_date),
                R.ThemeExplainer(series="theme_explainer", slug="grid-builders", title="Grid builders",
                                 members=ranked, tickers_as_of=run_date - timedelta(days=10), theme_size=7,
                                 angle="biggest", posted_on=run_date, values_as_of=run_date - timedelta(days=1)),
                # 2026-11-20, the last three angles: "growth + profit" (figures), the insider scorecard (ledger) and
                # Congress (tally) — invented figures, fictional companies
                R.ThemeExplainer(series="theme_explainer", slug="warehouse-robots", title="Warehouse robots",
                                 members=tuple(R.ThemeMember(company=_co(key), top_segment=None, top_segment_share=None,
                                                             fiscal_year=fy, revenue_usd=r, prior_revenue_usd=p,
                                                             net_income_usd=n)
                                               for key, r, p, n in (("contoso", 4.2e9, 3.1e9, 0.61e9),
                                                                    ("fabrikam", 1.9e9, 1.8e9, -0.24e9),
                                                                    ("northwind", 9.6e9, 9.9e9, 1.4e9),
                                                                    ("tailspin", 0.82e9, 0.55e9, 0.04e9),
                                                                    ("litware", 2.7e9, 2.2e9, 0.45e9))),
                                 tickers_as_of=run_date - timedelta(days=10), theme_size=8, angle="growth_profit",
                                 posted_on=run_date),
                R.ThemeExplainer(series="theme_explainer", slug="grid-builders", title="Grid builders",
                                 members=tuple(sorted((R.ThemeMember(company=_co(key), top_segment=None,
                                                                     top_segment_share=None, fiscal_year=None,
                                                                     purchases_usd=b, sales_usd=sl)
                                                       for key, b, sl in (("northwind", 0.0, 41.2e6),
                                                                          ("contoso", 1.3e6, 6.4e6),
                                                                          ("litware", 0.0, 12.9e6),
                                                                          ("adventure", 0.42e6, 0.0))),
                                                      key=R.theme_insider_order_key)),
                                 tickers_as_of=run_date - timedelta(days=10), theme_size=7, angle="insiders",
                                 posted_on=run_date, window_start=run_date - timedelta(days=90),
                                 window_end=run_date - timedelta(days=1), companies_checked=6),
                R.ThemeExplainer(series="theme_explainer", slug="warehouse-robots", title="Warehouse robots",
                                 members=tuple(sorted((R.ThemeMember(company=_co(key), top_segment=None,
                                                                     top_segment_share=None, fiscal_year=None,
                                                                     congress_members=c)
                                                       for key, c in (("contoso", 4), ("northwind", 3),
                                                                      ("litware", 2), ("tailspin", 2))),
                                                      key=R.theme_congress_order_key)),
                                 tickers_as_of=run_date - timedelta(days=10), theme_size=8, angle="congress",
                                 posted_on=run_date, month=selection.congress_disclosure_month(run_date))]
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
    capability it declares (`schemas.WORKER_CAPABILITIES`, pinned equal to marketing/main.py's — `post_art`
    included, workstream C), so the formats, video features and extra video posts freeze as on a run the current
    worker holds."""

    async def get_run(self, run_id: str) -> Dict[str, Any]:
        return {"id": run_id, "worker_version": "preview", "attempts": 1,
                "metadata": {RUN_WORKER_CAPABILITIES_KEY: list(WORKER_CAPABILITIES)}}


def freeze_switches() -> Dict[str, str]:
    """FREEZE_SWITCHES as this environment holds them — what `compose_stored`'s freeze just read."""
    from app.config import settings

    return {name: str(getattr(settings, name)) for name in FREEZE_SWITCHES}


def narrowed_record(rec: Any, run_date: date) -> Any:
    """The record as the build composes it (`_compose_candidate`): its JSON round trip, then — a several-company post
    (earnings, Congress Count, company stakes) — without the companies its template refuses one by one
    (`news_templates.drop_refused_rows`), so its fact sheet, its ledger key and its post name exactly the companies a
    real day would draw. Review 2026-11-20: the preview used to compose the record whole and refuse the post a real day
    narrows and posts. PreviewRefused when too few are left (the refusal production makes)."""
    rec = R.record_from_dict(json.loads(json.dumps(R.record_to_dict(rec), allow_nan=False)))
    try:
        return news_templates.drop_refused_rows(rec, run_date)
    except news_templates.NewsTemplateRefused as e:
        raise PreviewRefused(f"compose refused: {e.code} ({getattr(e, 'detail', '')})") from e


def compose_stored(rec: Any, run_date: date, *, logos_in: Optional[Path], logos_out: Path,
                   store_state: str = "live", allow_x_url: bool = False) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(stored output, worker script) of one record — PreviewRefused when a real day would refuse it.

    The freeze is production's own (`_compose_candidate` → `MarketingScriptService._frozen_output`, B3-LIVE-3):
    the post formats under MARKETING_IMAGE_PLATFORMS / MARKETING_X_IMAGES, the video features
    (MARKETING_VIDEO_FEATURES) and the extra video posts (MARKETING_VIDEO_EXTRA_PLATFORMS, re-checked against the
    record's fact sheet), every switch read NOW from this environment, image posts on, the holder today's worker
    (`PreviewHolder`); the first-line prefixes (MARKETING_FIRST_LINE_PREFIX, news-v22) are read at compose, as the
    build reads them. Review 2026-11-20: the record is NARROWED first, as the build narrows it (`narrowed_record`:
    the companies its template refuses dropped, never the post), and the picture platforms are read once and frozen
    into the composition (`picture_outlets`, `template_picture_outlets`), as `_build_template_day` reads them."""
    from app.config import settings

    rec = narrowed_record(rec, run_date)
    sheet = json.loads(json.dumps(R.fact_sheet(rec, rejections={}, selection={"preview": True}), allow_nan=False))
    # news-v22: MARKETING_FIRST_LINE_PREFIX read NOW, once, as `_build_template_day` reads it (strictly True)
    first_line_prefix = settings.MARKETING_FIRST_LINE_PREFIX is True
    picture_outlets = template_picture_outlets(True, bool(settings.MARKETING_X_IMAGES), image_platforms_setting())
    try:
        out = news_templates.compose(rec, run_date=run_date, store_state=store_state, allow_x_url=allow_x_url,
                                     first_line_prefix=first_line_prefix, picture_outlets=picture_outlets)
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


# ── the cross-lane exclusion (migration 210), simulated against a ledger ───────

class LedgerHolder:
    """The `runs` the real cross-lane read (`MarketingScriptService._cross_lane`) asks — `lane_scripts_between` —
    answered from ledger rows (a --ledger file, or the live read-only read), filtered to the window as that read's
    SQL filters it. Never a database."""

    def __init__(self, rows: Sequence[Any]) -> None:
        self.rows = list(rows)

    async def lane_scripts_between(self, start: date, end: date) -> List[Dict[str, Any]]:
        return [r for r in self.rows if isinstance(r, dict)
                and start.isoformat() <= str(r.get("run_date") or "")[:10] <= end.isoformat()]


class CrossLane(NamedTuple):
    """What a build of `lane` on the day would keep away from (`_cross_lane`'s three answers) and what it was judged
    against (`source`: "" when nothing was simulated)."""
    same_day_refs: FrozenSet[str]
    taken_today: FrozenSet[str]
    avoid_leads: FrozenSet[str]
    source: str


NO_CROSS_LANE = CrossLane(frozenset(), frozenset(), frozenset(), "")


def load_ledger(path: Path) -> List[Dict[str, Any]]:
    """A --ledger file: a JSON list of `lane_scripts_between` rows, or {"rows": [...]}. ValueError for anything else
    (never a partly read ledger: an exclusion judged on half of it would look like none)."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("rows") if isinstance(raw, dict) else raw
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError(f"{path}: not a list of ledger rows (or {{\"rows\": [...]}})")
    return rows


def load_live_ledger(run_date: date, lane: str = RUN_LANE_SPOTLIGHT) -> List[Dict[str, Any]]:
    """The live ledger the build reads — the accepted scripts of both lanes dated [run_date − CROSS_LANE_COMPANY_DAYS,
    run_date] (`run_service.lane_scripts_between`), through run_date + 1 for a MAIN build (C0: a resumed main run sees
    the next day's spotlight pick) — READ-ONLY: a GET, under the guard `main` installs first."""
    from app.services.marketing.run_service import get_marketing_run_service

    through = run_date + timedelta(days=1) if lane == RUN_LANE_MAIN else run_date
    return asyncio.run(get_marketing_run_service().lane_scripts_between(
        run_date - timedelta(days=CROSS_LANE_COMPANY_DAYS), through))


def cross_lane(rows: Sequence[Any], run_date: date, lane: str, source: str) -> CrossLane:
    """`_cross_lane` itself, for a build of `lane` on `run_date`, against `rows`. The day's own row of `lane` is left
    out first: a real build runs before that row exists, so a preview of a day that already ran must not read its own
    post as the other lane's."""
    other = [r for r in rows if not (isinstance(r, dict) and str(r.get("run_date") or "")[:10] == run_date.isoformat()
                                     and (r.get("lane") or RUN_LANE_MAIN) == lane)]
    svc = MarketingScriptService(runs=LedgerHolder(other))   # type: ignore[arg-type] — the one method it reads
    refs, taken, leads = asyncio.run(svc._cross_lane({"id": PREVIEW_RUN_ID}, run_date, lane))
    return CrossLane(refs, taken, leads, source)


# ── --art: the per-post art, made locally (SPENDS MONEY — only with the flag) ──────────

def art_clients() -> Tuple[Any, Any]:
    """(the image client, the Claude CLIENT) --art hands `post_art.make_post_art`: (None, None) — the shared clients it
    builds itself, on THIS environment's keys (real calls, real money). The tests stand fakes in here."""
    return None, None


def art_facts_fn(sample: bool) -> Any:
    """The company-facts reader the art step calls INSIDE its budget (`company_news_adapter.company_facts`: an FMP
    profile read, under the guard): None for built-in SAMPLE records — fictional companies have no profile, so their
    brief reads the display name and the top revenue line only."""
    if sample:
        return None
    from app.services.marketing import company_news_adapter as adapter  # the one FMP door, lazily

    return functools.partial(adapter.company_facts, timeout=post_art.STEP_SECONDS["facts"][1])


class LocalArtStore:
    """`run_service.store_art`'s protocol over a LOCAL folder: the checked picture is written to `<folder>/art.jpg`
    (never the bucket) and answered with the entry the record validates — its content-addressed `path` and the URL the
    bucket WOULD serve it at (`post_art.expected_art_url`: built from SUPABASE_URL, never requested)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.written: List[Path] = []

    async def __call__(self, data: bytes, *, width: int, height: int) -> Dict[str, Any]:
        sha = hashlib.sha256(data).hexdigest()
        target = self.folder / ART_FILE
        target.write_bytes(data)
        self.written.append(target)
        return {"path": post_art.art_path(sha), "url": post_art.expected_art_url(sha), "sha256": sha,
                "bytes": len(data), "width": width, "height": height}


def make_art(rec: Any, stored: Dict[str, Any], folder: Path, *, ref: str, sample: bool) -> Dict[str, Any]:
    """The art record of one composed record — what the build's art step stores in `output.art` — made with the BUILD's
    own gates before any spend (a record that does not picture one company: `not_a_company_series`; a day with no
    picture or video post for it: `text_only_day` — `post_art.gate_record`, nothing spent), then
    `post_art.make_post_art` against a `LocalArtStore` in `folder`. Never raises but for a cancellation (as
    make_post_art); the record is written to `<folder>/art.json`."""
    subject = post_art.art_subject_for(rec.series, stored, rec)
    if subject is None:
        record = post_art.gate_record("not_a_company_series", run_id=PREVIEW_RUN_ID, ref=ref, series=rec.series,
                                      detail="preview: the post does not picture exactly one company")
    elif not _art_day_surfaces(stored, subject):
        record = post_art.gate_record("text_only_day", run_id=PREVIEW_RUN_ID, ref=ref, series=rec.series,
                                      subject=subject, detail="preview: no picture or video post carries it")
    else:
        gemini, claude = art_clients()
        record = asyncio.run(post_art.make_post_art(
            run_id=PREVIEW_RUN_ID, ref=ref, series=rec.series, output=stored, store=LocalArtStore(folder),
            facts_fn=art_facts_fn(sample), record=rec, gemini=gemini, claude=claude))
    (folder / ART_RECORD_FILE).write_text(json.dumps(record, indent=1, ensure_ascii=False, default=str),
                                          encoding="utf-8")
    return record


def art_drawable(stored: Any, folder: Path) -> Optional[Path]:
    """`<folder>/art.jpg` when the stored output (its `art` record attached as the build attaches it) carries a
    checked picture the read-back would send (`post_art.drawable_art`: a template output's ok record that validates)
    and the file is there with the recorded sha256 — what --render passes as --art-file; None otherwise."""
    entry = post_art.drawable_art(stored) if isinstance(stored, dict) else None
    path = folder / ART_FILE
    if entry is None or not path.is_file():
        return None
    return path if hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"] else None


def art_markdown(record: Any) -> List[str]:
    """post.md's "Art" block: what was drawn (subject, treatment, the checks in the owner's words, where it may be
    drawn, the estimated spend) or why there is none — never a model or provider name (the record's `models` stay in
    art.json)."""
    lines = ["## Art (--art: per-post art, made locally)", ""]
    if not isinstance(record, dict):
        return lines + ["The art record could not be read.", ""]
    usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
    spent = usage.get("estimated_micros")
    spent_txt = (f"≈ ${spent / 1_000_000:.3f}" if isinstance(spent, int) and not isinstance(spent, bool)
                 else "unknown (see art.json)")
    images = usage.get("images") if isinstance(usage.get("images"), int) else "?"
    if record.get("status") == post_art.STATUS_OK:
        checks = record.get("checks") if isinstance(record.get("checks"), list) else []
        verdicts = []
        for i, check in enumerate(checks, 1):
            flags = [post_art.FLAG_WORDS[f][1] for f in (check.get("flags") or []) if f in post_art.FLAG_WORDS] \
                if isinstance(check, dict) else []
            verdicts.append(f"check {i}: " + ("passed" if isinstance(check, dict) and check.get("pass") is True
                                              else f"flagged {', '.join(flags) or '(unreadable)'}"))
        surfaces = record.get("surfaces") if isinstance(record.get("surfaces"), list) else []
        lines += [f"**Subject:** {record.get('subject')}", "",
                  f"**Treatment:** {post_art.treatment_words(record.get('treatment')) or record.get('treatment')}",
                  "", f"**Checks:** {' · '.join(verdicts) or 'none recorded'}", "",
                  f"**May be drawn on:** {', '.join(surfaces) or 'nothing'} · `{ART_FILE}` "
                  f"({record.get('width')}×{record.get('height')}, {record.get('bytes')} bytes)", ""]
    else:
        reason = record.get("reason")
        lines += [f"**No art:** {post_art.REASON_WORDS.get(reason, 'unknown')} (`{reason}`)", ""]
    return lines + [f"**Spent:** {spent_txt} ({images} image(s); the brief and check calls included)", ""]


# ── what a reviewer reads ──────────────────────────────────────────────────────

def post_markdown(stored: Dict[str, Any], *, series: str, ref: str, run_date: date, sample: bool,
                  switches: Optional[Dict[str, str]] = None, lane: str = RUN_LANE_MAIN,
                  art: Optional[Dict[str, Any]] = None) -> str:
    """The reviewer's page: every post the day would record — the PRIMARY posts (`posts`, each in its frozen
    format) and the EXTRA video posts (`video_posts`, decided with the video) — the frozen video features, a
    picture fallback, and (`switches`) the write-time switches the freeze read. A spotlight preview's title names
    its lane; a main one's is the page of before. `art` (--art only): the record's "Art" block at the end
    (`art_markdown`); without it the page is the one of before."""
    lane_tag = " · spotlight lane" if lane == RUN_LANE_SPOTLIGHT else ""
    lines: List[str] = [f"# {series} · {run_date:%a %b %-d, %Y}{lane_tag}", ""]
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
    if art is not None:
        lines += art_markdown(art)
    return "\n".join(lines)


def render(script_path: Path, logos_dir: Path, out_dir: Path, art_file: Optional[Path] = None) -> int:
    """The worker's own preview (venv_marketing: Kokoro, Pillow, ffmpeg) into `out_dir` — with `art_file` (--art: the
    record's checked art.jpg) passed as `--art-file`, which the worker's preview checks and draws as a verified
    read-back art (the `feature` picture's band, the opening card)."""
    if not WORKER_PYTHON.exists():
        print(f"  --render: {WORKER_PYTHON} is missing (see marketing/README.md)", file=sys.stderr)
        return 1
    cmd = [str(WORKER_PYTHON), "-m", "marketing.preview", str(script_path), "--logos-dir", str(logos_dir),
           "--phonemes", "--out", str(out_dir)] + (["--art-file", str(art_file)] if art_file is not None else [])
    done = subprocess.run(cmd, cwd=str(BACKEND), capture_output=True, text=True)
    (out_dir / "render.log").write_text(done.stdout + "\n--- stderr ---\n" + done.stderr, encoding="utf-8")
    return done.returncode


def _excluded(folder: Path, ref: str, why: str) -> bool:
    """A record the cross-lane exclusion keeps out: EXCLUDED.txt, never a post — an expected outcome, not a failure."""
    (folder / "EXCLUDED.txt").write_text(f"{ref}\n{why}\n", encoding="utf-8")
    print(f"- {ref}: EXCLUDED — {why}")
    return True


def write_one(rec: Any, run_date: date, folder: Path, *, logos_in: Optional[Path], sample: bool,
              do_render: bool, lane: str = RUN_LANE_MAIN, cross: CrossLane = NO_CROSS_LANE,
              do_art: bool = False) -> bool:
    """One record into `folder`: composed as stored (or REFUSED / EXCLUDED), its script and stored output, post.md,
    and — `do_art` (--art, SPENDS MONEY) — its per-post art (`make_art`: art.jpg + art.json, the record stored in the
    output as the build stores it), then — `do_render` — the worker's preview, with the checked art as --art-file."""
    folder.mkdir(parents=True, exist_ok=True)
    try:
        # review 2026-11-20: the record as the build narrows it (`narrowed_record`) — its ledger key, art and post.md
        # name exactly the companies the post draws, as the stored output's `source_ref` does
        rec = narrowed_record(rec, run_date)
    except PreviewRefused as e:
        ref = R.ledger_key(rec)
        (folder / "REFUSED.txt").write_text(f"{ref}\n{e}\n", encoding="utf-8")
        print(f"- {ref}: REFUSED — {e}")
        return False
    ref = R.ledger_key(rec)
    if ref in cross.same_day_refs:
        # rule (a): the other lane posted this very record today (the build adds it to its exclude set)
        return _excluded(folder, ref, f"the other lane posted this record that day ({cross.source})")
    # rule (b) on a several-company post (review 2026-11-20, `narrow_other_lane_lead`): a lead a lane drew is dropped
    # from the post — the next one checked the same way — before the fact sheet and the key, as the build does
    lead_free, lead_dropped = narrow_other_lane_lead(rec, cross.avoid_leads)
    if lead_dropped:
        if lead_free is None:
            return _excluded(folder, ref, f"a lane drew its lead(s) {', '.join(lead_dropped)} within "
                                          f"{CROSS_LANE_COMPANY_DAYS} ET days and too few companies are left "
                                          f"({cross.source})")
        print(f"  rule (b): {', '.join(lead_dropped)} dropped from the post — a lane drew it within "
              f"{CROSS_LANE_COMPANY_DAYS} ET days ({cross.source})")
        rec = lead_free
        ref = R.ledger_key(rec)
    try:
        stored, script = compose_stored(rec, run_date, logos_in=logos_in, logos_out=folder / "logos")
    except PreviewRefused as e:
        (folder / "REFUSED.txt").write_text(f"{ref}\n{e}\n", encoding="utf-8")
        print(f"- {ref}: REFUSED — {e}")
        return False
    hit = lead_keys(stored) & cross.avoid_leads
    if hit:
        # rule (b): a lane drew this record's LEAD company within CROSS_LANE_COMPANY_DAYS ET days — by its issuer root
        # or its display name (`lead_keys`: GOOGL and GOOG are one company)
        return _excluded(folder, ref, f"a lane drew its lead company {lead_company(stored)} "
                                      f"({', '.join(sorted(hit))}) within {CROSS_LANE_COMPANY_DAYS} ET days "
                                      f"({cross.source})")
    art: Optional[Dict[str, Any]] = None
    art_file: Optional[Path] = None
    if do_art:
        art = make_art(rec, stored, folder, ref=ref, sample=sample)
        # the record rides in the stored output as the build stores it (`_with_art`: outside COMPARED_FIELDS)
        stored = MarketingScriptService._with_art(stored, art, PREVIEW_RUN_ID)
        art_file = art_drawable(stored, folder)
        usage = art.get("usage") if isinstance(art.get("usage"), dict) else {}
        spent = usage.get("estimated_micros")
        print(f"  art: {art.get('status')}" + (f" ({art.get('reason')})" if art.get("reason") else "")
              + (f" · spent ≈ ${spent / 1_000_000:.3f}" if isinstance(spent, int) and not isinstance(spent, bool)
                 else " · spent unknown") + (f" → {art_file}" if art_file is not None else ""))
    (folder / "script.json").write_text(json.dumps(script, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "stored_output.json").write_text(json.dumps(stored, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "post.md").write_text(post_markdown(stored, series=rec.series, ref=ref, run_date=run_date,
                                                  sample=sample, switches=freeze_switches(), lane=lane, art=art),
                                    encoding="utf-8")
    print(f"- {ref}: composed → {folder}")
    if do_render:
        (folder / "logos").mkdir(exist_ok=True)
        code = render(folder / "script.json", folder / "logos", folder, art_file=art_file)
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


def next_lane_day(today: date, lane: str) -> Optional[date]:
    """The next day `lane` runs on or after `today`: the next posting day (main), or the next day the spotlight
    calendar does not rest — None when MARKETING_SPOTLIGHT_WEEKDAYS names no day (the lane closed)."""
    if lane == RUN_LANE_MAIN:
        return next_posting_day(today)
    for i in range(NEXT_SPOTLIGHT_DAY_HORIZON_DAYS):
        d = today + timedelta(days=i)
        if not selection.spotlight_plan_for(d).rest_day:
            return d
    return None


async def live_candidates(series_list: Sequence[str], run_date: date, *, logos_dir: Optional[Path],
                          exclude: FrozenSet[str] = frozenset()) -> Dict[str, Dict[str, Any]]:
    """{series: {"records": [...], "skip_reason", "rejections", "error"}} from the live adapter. `exclude`: the
    ledger refs a build keeps out — only the other lane's same-day refs here (`CrossLane.same_day_refs`; already
    posted items are NOT excluded, as before)."""
    from app.services.marketing import company_news_adapter as adapter  # the one FMP door, lazily

    deps = adapter.NewsDeps()
    found: Dict[str, Dict[str, Any]] = {}
    for series in series_list:
        try:
            got = await adapter.candidates(series, run_date=run_date, exclude=exclude,
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
    if isinstance(rec, R.InsiderSpotlight):                  # owner decisions 2026-11-20: the insider LIST
        return [c.company.symbol for c in rec.companies]
    if isinstance(rec, R.ThirteenFSpotlight):                # news-v22: the HOLDINGS, and a list's filer plate
        return ([rec.filer_symbol] if rec.filer_symbol and len(rec.moves) > 1 else []) + [
            m.company.symbol for m in rec.moves]
    if isinstance(rec, R.ThirteenFFiling):
        return ([rec.filer_symbol] if rec.filer_symbol else []) + [m.company.symbol for m in rec.moves]
    if isinstance(rec, R.CompanyStakes):                     # news-v23: one investor's stakes in public companies
        return [rec.investor.symbol] + [s.investee.symbol for s in rec.stakes if s.investee is not None]
    if isinstance(rec, R.ThemeExplainer):
        return [m.company.symbol for m in rec.members]
    if isinstance(rec, R.CongressMonth):                     # news-v23: one post of the month's 2-3 companies
        return [c.company.symbol for c in rec.counts]
    if isinstance(rec, R.EarningsDay):                       # news-v23: one table of 2-4 companies
        return [r.company.symbol for r in rec.reports]
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
    ap.add_argument("--lane", choices=LANES, default=RUN_LANE_MAIN,
                    help="the lane whose calendar to preview (default: main; spotlight = the morning lane)")
    ap.add_argument("--ledger", type=Path, metavar="FILE",
                    help="simulate the cross-lane exclusion against this ledger (lane_scripts_between rows); "
                         "default with --lane spotlight --candidates: the live ledger, read-only")
    ap.add_argument("--art", action="store_true",
                    help="SPENDS MONEY (about $%.2f-$%.2f a record): make each single-company record's per-post art "
                         "(brief, drawing, picture check) into <series>-<n>/art.jpg + art.json"
                         % ART_COST_PER_RECORD_USD)
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


def _cross_lane_for(args: argparse.Namespace, run_date: date, *, live: bool) -> CrossLane:
    """The cross-lane exclusion a build of `args.lane` on `run_date` would apply: against --ledger FILE when given;
    else, for a LIVE spotlight preview (`live`), against the live ledger (read-only); else nothing (NO_CROSS_LANE)."""
    if args.ledger is not None:
        return cross_lane(load_ledger(args.ledger), run_date, args.lane, f"--ledger {args.ledger}")
    if live and args.lane == RUN_LANE_SPOTLIGHT:
        return cross_lane(load_live_ledger(run_date, args.lane), run_date, args.lane, "the live ledger")
    return NO_CROSS_LANE


def _cross_lane_lines(cross: CrossLane, lane: str) -> List[str]:
    """What the simulation found, for the console: nothing for a main preview with no ledger (the output of before)."""
    if not cross.source:
        return (["(cross-lane exclusion not simulated: no --ledger)"] if lane == RUN_LANE_SPOTLIGHT else [])
    return [f"Cross-lane exclusion (against {cross.source}): the other lane's records today "
            f"{sorted(cross.same_day_refs) or 'none'} · its series today {sorted(cross.taken_today) or 'none'} · lead "
            f"companies drawn within {CROSS_LANE_COMPANY_DAYS} ET days {sorted(cross.avoid_leads) or 'none'}"]


def _lane_series_refused(args: argparse.Namespace, today: date) -> Optional[str]:
    """Why the --series asked cannot be previewed on its --lane (a series that lane's chains never hold: on the
    spotlight lane the weekly roundups, the main lane's 13F roundup, …; on the main lane the spotlight pair), or
    None."""
    if not args.series:
        return None
    if args.lane == RUN_LANE_MAIN:
        never = [s for s in args.series if s in selection.SPOTLIGHT_SERIES]
        return (f"the main lane never runs {', '.join(never)} (the spotlight lane's series: --lane spotlight)"
                if never else None)
    offered = spotlight_chain_series(today)
    never = [s for s in args.series if s not in offered]
    return (f"the spotlight lane never runs {', '.join(never)} (its series: {', '.join(offered) or 'none shipped'})"
            if never else None)


def _run(args: argparse.Namespace) -> int:
    """`main`'s work, under the read-only guard."""
    today = datetime.now(ET).date()
    out_root = args.out or (OUT_ROOT / datetime.now(ET).strftime("%Y%m%d-%H%M%S"))
    logos_in: Optional[Path] = args.logos_dir
    sample = False
    lane = args.lane
    #: the run date each series is composed for (one date unless built-in samples pick their own days)
    dates: Dict[str, date] = {}
    #: the cross-lane exclusion each run date is judged with (NO_CROSS_LANE: nothing simulated)
    crosses: Dict[date, CrossLane] = {}
    refused = _lane_series_refused(args, today)
    if refused:
        print(f"refused: {refused}", file=sys.stderr)
        return 2
    if args.art:
        worst = post_art.run_worst_micros()
        print(f"--art SPENDS MONEY: about ${ART_COST_PER_RECORD_USD[0]:.2f}-${ART_COST_PER_RECORD_USD[1]:.2f} per "
              f"single-company record (at worst {'unpriced' if worst is None else f'${worst / 1_000_000:.2f}'}), on "
              f"this environment's keys; pictures are written under {out_root} only\n")
    try:
        if args.ledger is not None:
            load_ledger(args.ledger)       # a ledger that does not read is refused before anything runs
    except (OSError, ValueError) as e:
        print(f"refused: --ledger {args.ledger}: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if args.candidates:
        found_day = args.date or next_lane_day(today, lane)
        if found_day is None:
            print("refused: MARKETING_SPOTLIGHT_WEEKDAYS names no day — the spotlight lane has no day to preview",
                  file=sys.stderr)
            return 2
        run_date = found_day
        plan = plan_of(run_date, lane)
        series_list = args.series or [s for s in plan.chain if s in SHIPPED_SERIES]
        if lane == RUN_LANE_SPOTLIGHT:
            for s in [s for s in series_list if main_lane_holds(s, run_date)]:
                print(f"## {s}: left out — the main lane's chain holds it that day (C0: never the spotlight's)")
                series_list = [x for x in series_list if x != s]
        if args.logos:
            logos_in = out_root / "fetched-logos"
        lane_txt = ", spotlight lane" if lane == RUN_LANE_SPOTLIGHT else ""
        print(f"# Live candidates for {run_date:%a %Y-%m-%d} ({plan.reason}{lane_txt}): "
              f"{', '.join(series_marked(series_list)) or 'none'}\n")
        try:
            cross = crosses[run_date] = _cross_lane_for(args, run_date, live=True)
        except Exception as e:  # noqa: BLE001 — a ledger that cannot be read is never "no other lane"
            print(f"refused: the ledger for the cross-lane exclusion could not be read ({type(e).__name__}: {e}) — "
                  f"a preview never guesses the other lane", file=sys.stderr)
            return 1
        for line in _cross_lane_lines(cross, lane):
            print(line)
        taken = [s for s in series_list if s in cross.taken_today]
        for s in taken:
            print(f"## {s}: falls back — the other lane posted this series that day (other_lane)")
        if "insider_spotlight" in series_list and "insider_spotlight" not in taken:
            print("(insider_spotlight: the window starts at run_date − 7 and the 14-day issuer cooldown is not "
                  "applied — a real build reads the last window a spotlight CHECKED and the cooldown from the ledger)")
        # `asyncio.run`, never a bare loop: it drains the default executor before it returns, so a cache
        # write a service fires and forgets (profit / revenue: `run_in_executor(None, …)`; company facts:
        # `ensure_future(to_thread(…))`) is still attempted INSIDE the guard. `loop.close()` does not wait
        # for that executor — a write landing after `main` left the guard would reach production. A Ctrl-C
        # during that drain escapes it with the thread alive: the guard then stays installed until the
        # process exits (`supabase_read_only_guard`), so that write is refused all the same.
        found = asyncio.run(live_candidates([s for s in series_list if s not in taken], run_date,
                                            logos_dir=logos_in, exclude=cross.same_day_refs))
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
            if lane == RUN_LANE_SPOTLIGHT:
                offered = spotlight_chain_series(today)
                for s in [s for s in by_series if s not in offered]:
                    print(f"## {s}: left out — the spotlight lane never runs it")
                    del by_series[s]
        else:
            sample = True
            series_list = args.series or (list(spotlight_chain_series(today)) if lane == RUN_LANE_SPOTLIGHT
                                          else list(MAIN_SERIES))
            by_series = {}
            for s in series_list:
                try:
                    dates[s] = args.date or sample_day(s, today, lane=lane)
                except ValueError as e:
                    print(f"refused: {e}", file=sys.stderr)
                    return 2
                by_series[s] = sample_records(s, dates[s])
                print(f"- {s}: sample day {dates[s]:%a %Y-%m-%d}" + (" (spotlight lane)"
                                                                     if lane == RUN_LANE_SPOTLIGHT else ""))
            run_date = args.date or next_lane_day(today, lane) or today
        if lane == RUN_LANE_SPOTLIGHT:
            for s in [s for s in by_series if main_lane_holds(s, dates.get(s, run_date))]:
                print(f"## {s}: left out — the main lane's chain holds it on "
                      f"{dates.get(s, run_date):%a %Y-%m-%d} (C0: never the spotlight's)")
                del by_series[s]
        for d in sorted({dates.get(s, run_date) for s in by_series}):
            crosses[d] = _cross_lane_for(args, d, live=False)
            for line in _cross_lane_lines(crosses[d], lane):
                print(f"{d:%a %Y-%m-%d}: {line}")
        for s in list(by_series):
            if s in crosses[dates.get(s, run_date)].taken_today:
                print(f"## {s}: falls back — the other lane posted this series that day (other_lane)")
                del by_series[s]
    print(f"\nOutput → {out_root}\n")
    ok = True
    for s, recs in by_series.items():
        day = dates.get(s, run_date)
        for i, rec in enumerate(recs, 1):
            ok &= write_one(rec, day, out_root / f"{s}-{i}", logos_in=logos_in, sample=sample,
                            do_render=args.render, lane=lane, cross=crosses.get(day, NO_CROSS_LANE),
                            do_art=args.art)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
